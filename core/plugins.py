"""Plugin system for dynamically loading extractor plugins.

Example plugin structure:
    # ~/.astrex/plugins/my_extractor.py
    plugin_info = {
        "name": "my_extractor",
        "version": "1.0",
        "author": "User",
        "description": "Custom extractor for .xyz files"
    }

    from extractors.base import BaseExtractor, ExtractionResult

    class XyzExtractor(BaseExtractor):
        name = "xyz"
        extensions = [".xyz"]
        priority = 5
        available = True

        def extract(self, path):
            text = path.read_text()
            return ExtractionResult(success=True, text=text)

    def register(registry):
        registry.register_class(XyzExtractor)
"""

import importlib
import importlib.util
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass

from .logging_setup import get_logger
from .config import ASTREX_HOME

logger = get_logger(__name__)


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


class PluginManager:
    """Manages dynamic loading of extractor plugins."""

    PLUGIN_DIR = ASTREX_HOME / "plugins"

    def __init__(self):
        """Initialize the plugin manager."""
        # Create plugin directory if it doesn't exist
        self.PLUGIN_DIR.mkdir(parents=True, exist_ok=True)
        self.plugins: Dict[str, PluginInfo] = {}
        logger.info(f"Plugin manager initialized. Plugin directory: {self.PLUGIN_DIR}")

    def discover(self) -> List[PluginInfo]:
        """Scan PLUGIN_DIR for plugins and return list of discovered plugins.

        Plugins can be:
        - Single .py files with plugin_info dict
        - Directories with __init__.py containing plugin_info dict

        Returns:
            List of discovered PluginInfo objects
        """
        discovered = []

        if not self.PLUGIN_DIR.exists():
            logger.warning(f"Plugin directory does not exist: {self.PLUGIN_DIR}")
            return discovered

        # Scan for .py files
        for item in self.PLUGIN_DIR.iterdir():
            plugin_info = None
            module_path = None

            # Check if it's a Python file (but not __pycache__ or __init__.py)
            if item.is_file() and item.suffix == ".py" and not item.name.startswith("__"):
                module_path = str(item)
                plugin_info = self._load_plugin_info(item)

            # Check if it's a directory with __init__.py
            elif item.is_dir() and not item.name.startswith("__"):
                init_file = item / "__init__.py"
                if init_file.exists():
                    module_path = str(item)
                    plugin_info = self._load_plugin_info(init_file)

            if plugin_info and module_path:
                info = PluginInfo(
                    name=plugin_info.get("name", item.stem),
                    version=plugin_info.get("version", "unknown"),
                    author=plugin_info.get("author", "unknown"),
                    description=plugin_info.get("description", ""),
                    module_path=module_path
                )
                discovered.append(info)
                self.plugins[info.name] = info
                logger.debug(f"Discovered plugin: {info.name} v{info.version}")

        logger.info(f"Discovered {len(discovered)} plugin(s)")
        return discovered

    def _load_plugin_info(self, file_path: Path) -> Optional[Dict]:
        """Load plugin_info dict from a Python file using AST (safe, no exec).

        Args:
            file_path: Path to the Python file

        Returns:
            plugin_info dict if found, None otherwise
        """
        import ast

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()

            tree = ast.parse(content, str(file_path))

            for node in ast.iter_child_nodes(tree):
                # Look for: plugin_info = {...}
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name) and target.id == 'plugin_info':
                            if isinstance(node.value, ast.Dict):
                                try:
                                    return ast.literal_eval(node.value)
                                except (ValueError, TypeError):
                                    logger.warning(f"plugin_info in {file_path} contains non-literal values")
                                    return None

            logger.warning(f"No plugin_info dict found in {file_path}")
            return None

        except Exception as e:
            logger.error(f"Error loading plugin info from {file_path}: {e}")
            return None

    def load(self, name: str) -> bool:
        """Load a specific plugin by name.

        Args:
            name: Plugin name

        Returns:
            True if loaded successfully, False otherwise
        """
        if name not in self.plugins:
            logger.error(f"Plugin not found: {name}")
            return False

        plugin = self.plugins[name]

        if plugin.loaded:
            logger.info(f"Plugin already loaded: {name}")
            return True

        try:
            # Determine module name and spec
            module_path = Path(plugin.module_path)

            if module_path.is_file():
                # Single file plugin
                module_name = f"astrex_plugins.{module_path.stem}"
                spec = importlib.util.spec_from_file_location(module_name, module_path)
            else:
                # Directory plugin
                module_name = f"astrex_plugins.{module_path.name}"
                init_file = module_path / "__init__.py"
                spec = importlib.util.spec_from_file_location(module_name, init_file)

            if spec is None or spec.loader is None:
                raise ImportError(f"Could not load spec for {plugin.module_path}")

            # Import the module
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)

            # Call register function if it exists
            if hasattr(module, 'register'):
                # Import the extractor registry
                from extractors import registry as extractor_registry
                module.register(extractor_registry)
                logger.info(f"Registered plugin: {name}")
            else:
                logger.warning(f"Plugin {name} has no register() function")

            # Mark as loaded
            plugin.loaded = True
            plugin.error = None
            logger.info(f"Successfully loaded plugin: {name} v{plugin.version}")
            return True

        except Exception as e:
            error_msg = f"Error loading plugin {name}: {e}"
            logger.error(error_msg)
            plugin.error = str(e)
            return False

    def load_all(self) -> int:
        """Discover and load all plugins.

        Returns:
            Number of successfully loaded plugins
        """
        logger.info("Loading all plugins...")
        discovered = self.discover()

        loaded_count = 0
        for plugin in discovered:
            if self.load(plugin.name):
                loaded_count += 1

        logger.info(f"Loaded {loaded_count} of {len(discovered)} plugin(s)")
        return loaded_count

    def unload(self, name: str) -> bool:
        """Unload a plugin by removing it from sys.modules.

        Args:
            name: Plugin name

        Returns:
            True if unloaded successfully, False otherwise
        """
        if name not in self.plugins:
            logger.error(f"Plugin not found: {name}")
            return False

        plugin = self.plugins[name]

        if not plugin.loaded:
            logger.info(f"Plugin not loaded: {name}")
            return True

        try:
            # Find and remove the module from sys.modules
            module_path = Path(plugin.module_path)

            if module_path.is_file():
                module_name = f"astrex_plugins.{module_path.stem}"
            else:
                module_name = f"astrex_plugins.{module_path.name}"

            if module_name in sys.modules:
                del sys.modules[module_name]

            plugin.loaded = False
            logger.info(f"Unloaded plugin: {name}")
            return True

        except Exception as e:
            logger.error(f"Error unloading plugin {name}: {e}")
            return False

    def list_plugins(self) -> List[PluginInfo]:
        """Return list of all known plugins.

        Returns:
            List of PluginInfo objects
        """
        return list(self.plugins.values())

    def get_plugin(self, name: str) -> Optional[PluginInfo]:
        """Get plugin information by name.

        Args:
            name: Plugin name

        Returns:
            PluginInfo object if found, None otherwise
        """
        return self.plugins.get(name)


# Module-level convenience instance
plugin_manager = PluginManager()


__all__ = ["PluginManager", "PluginInfo", "plugin_manager"]
