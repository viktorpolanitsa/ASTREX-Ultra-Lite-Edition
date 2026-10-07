"""Plugin system for dynamically loading extractor plugins.

Плагины из ~/.astrex/plugins загружаются автоматически при сканировании и
индексации (в главном процессе и в каждом процессе-воркере), если
ENGINE_CONFIG.plugins_autoload = True.

Example plugin structure:
    # ~/.astrex/plugins/my_extractor.py
    plugin_info = {
        "name": "my_extractor",
        "version": "1.0",
        "author": "User",
        "description": "Custom extractor for .xyz files"
    }

    from pathlib import Path
    from extractors.base import BaseExtractor, ExtractionResult

    class XyzExtractor(BaseExtractor):
        extensions = [".xyz"]
        priority = 5

        @classmethod
        def is_available(cls) -> bool:
            return True

        @classmethod
        def extract(cls, path: Path) -> ExtractionResult:
            return ExtractionResult(text=path.read_text(encoding="utf-8", errors="replace"))

    def register(registry):
        registry.register(XyzExtractor)

Расширения из `extensions` зарегистрированных экстракторов сканер собирает
автоматически (core.engine.default_extensions), отдельно добавлять их в
FILE_TYPES не нужно. Плагин выполняется как обычный Python-код с правами
пользователя — кладите в каталог плагинов только код, которому доверяете.
"""

import importlib.util
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass, field

from .logging_setup import get_logger
from .config import ASTREX_HOME

logger = get_logger("astrex.plugins")


@dataclass
class PluginInfo:
    """Information about a plugin."""
    name: str
    version: str
    author: str
    description: str
    module_path: str
    loaded: bool = False
    error: Optional[str] = None
    extractors: List[str] = field(default_factory=list)


class PluginManager:
    """Manages dynamic loading of extractor plugins."""

    PLUGIN_DIR = ASTREX_HOME / "plugins"

    def __init__(self):
        try:
            self.PLUGIN_DIR.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.debug(f"Cannot create plugin directory {self.PLUGIN_DIR}: {e}")
        self.plugins: Dict[str, PluginInfo] = {}
        self._registered: Dict[str, list] = {}

    @staticmethod
    def _module_name(module_path: Path) -> str:
        stem = module_path.stem if module_path.is_file() else module_path.name
        return "astrex_plugin_" + re.sub(r'\W', '_', stem)

    def discover(self) -> List[PluginInfo]:
        """Scan PLUGIN_DIR for plugins (.py files or packages with __init__.py)."""
        discovered = []

        if not self.PLUGIN_DIR.exists():
            return discovered

        for item in sorted(self.PLUGIN_DIR.iterdir()):
            plugin_info = None
            module_path = None

            if item.is_file() and item.suffix == ".py" and not item.name.startswith("__"):
                module_path = str(item)
                plugin_info = self._load_plugin_info(item)
            elif item.is_dir() and not item.name.startswith(("__", ".")):
                init_file = item / "__init__.py"
                if init_file.exists():
                    module_path = str(item)
                    plugin_info = self._load_plugin_info(init_file)

            if plugin_info is not None and module_path:
                name = str(plugin_info.get("name", item.stem))
                existing = self.plugins.get(name)
                if existing is not None and existing.module_path == module_path:
                    info = existing  # сохраняем состояние loaded/error
                else:
                    info = PluginInfo(
                        name=name,
                        version=str(plugin_info.get("version", "unknown")),
                        author=str(plugin_info.get("author", "unknown")),
                        description=str(plugin_info.get("description", "")),
                        module_path=module_path
                    )
                    self.plugins[name] = info
                discovered.append(info)

        logger.debug(f"Discovered {len(discovered)} plugin(s)")
        return discovered

    def _load_plugin_info(self, file_path: Path) -> Optional[Dict]:
        """Load plugin_info dict from a Python file using AST (safe, no exec)."""
        import ast

        try:
            content = file_path.read_text(encoding='utf-8')
            tree = ast.parse(content, str(file_path))

            for node in ast.iter_child_nodes(tree):
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name) and target.id == 'plugin_info':
                            try:
                                value = ast.literal_eval(node.value)
                            except (ValueError, TypeError, SyntaxError):
                                logger.warning(f"plugin_info in {file_path} contains non-literal values")
                                return None
                            if isinstance(value, dict):
                                return value
                            logger.warning(f"plugin_info in {file_path} is not a dict")
                            return None

            logger.warning(f"No plugin_info dict found in {file_path}")
            return None

        except Exception as e:
            logger.error(f"Error loading plugin info from {file_path}: {e}")
            return None

    def load(self, name: str) -> bool:
        """Load a specific plugin by name."""
        plugin = self.plugins.get(name)
        if plugin is None:
            logger.error(f"Plugin not found: {name}")
            return False
        if plugin.loaded:
            return True

        from extractors import registry as extractor_registry

        module_path = Path(plugin.module_path)
        module_name = self._module_name(module_path)
        before = list(extractor_registry._extractors)
        try:
            target = module_path if module_path.is_file() else module_path / "__init__.py"
            spec = importlib.util.spec_from_file_location(module_name, target)
            if spec is None or spec.loader is None:
                raise ImportError(f"Could not load spec for {plugin.module_path}")

            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)

            if hasattr(module, 'register'):
                module.register(extractor_registry)
            else:
                logger.warning(f"Plugin {name} has no register() function")

            added = [e for e in extractor_registry._extractors if e not in before]
            self._registered[name] = added
            plugin.extractors = [e.__name__ for e in added]
            plugin.loaded = True
            plugin.error = None
            logger.info(f"Loaded plugin: {name} v{plugin.version} "
                        f"({len(added)} extractor(s))")
            return True

        except Exception as e:
            # откатываем частичную регистрацию
            for ext in [e for e in extractor_registry._extractors if e not in before]:
                extractor_registry.unregister(ext)
            sys.modules.pop(module_name, None)
            plugin.error = f"{type(e).__name__}: {e}"
            logger.error(f"Error loading plugin {name}: {plugin.error}")
            return False

    def load_all(self, quiet: bool = False) -> int:
        """Discover and load all plugins. Returns number of loaded plugins."""
        discovered = self.discover()
        loaded_count = 0
        for plugin in discovered:
            if self.load(plugin.name):
                loaded_count += 1
        if discovered and not quiet:
            logger.info(f"Loaded {loaded_count} of {len(discovered)} plugin(s)")
        return loaded_count

    def unload(self, name: str) -> bool:
        """Unload a plugin: unregister its extractors and remove the module."""
        plugin = self.plugins.get(name)
        if plugin is None:
            logger.error(f"Plugin not found: {name}")
            return False
        if not plugin.loaded:
            return True

        from extractors import registry as extractor_registry
        for ext in self._registered.pop(name, []):
            extractor_registry.unregister(ext)
        sys.modules.pop(self._module_name(Path(plugin.module_path)), None)
        plugin.loaded = False
        plugin.extractors = []
        logger.info(f"Unloaded plugin: {name}")
        return True

    def list_plugins(self) -> List[PluginInfo]:
        """Return list of all known plugins."""
        return list(self.plugins.values())

    def get_plugin(self, name: str) -> Optional[PluginInfo]:
        """Get plugin information by name."""
        return self.plugins.get(name)


# Module-level convenience instance
plugin_manager = PluginManager()


__all__ = ["PluginManager", "PluginInfo", "plugin_manager"]
