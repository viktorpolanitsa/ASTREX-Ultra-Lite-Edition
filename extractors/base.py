#!/usr/bin/env python3
"""
ASTREX v3.0 — Extractors Base
Базовый класс, реестр экстракторов и общие помощники
"""

import os
import re
import threading
from abc import ABC, abstractmethod
from contextlib import contextmanager
from pathlib import Path
from typing import Optional, Dict, Any, List, Type
from dataclasses import dataclass, field


@dataclass
class ExtractionResult:
    """Результат извлечения текста"""
    text: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    attachments: List['ExtractionResult'] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def success(self) -> bool:
        return self.text is not None and self.error is None

    @property
    def full_text(self) -> str:
        """Текст включая вложения (рекурсивно, с заголовками вложений)."""
        parts: List[str] = []
        self._collect(parts, depth=0)
        return '\n\n'.join(parts)

    def _collect(self, parts: List[str], depth: int) -> None:
        if self.text:
            parts.append(self.text)
        if depth >= 10:
            return
        for att in self.attachments:
            name = (att.metadata or {}).get('filename')
            before = len(parts)
            att._collect(parts, depth + 1)
            if name and len(parts) > before:
                parts[before] = f"--- [вложение] {name} ---\n{parts[before]}"


class TextCollector:
    """Накопитель текста с ограничением общего объёма (в символах)."""

    def __init__(self, limit: Optional[int] = None):
        if limit is None:
            from core.config import ENGINE_CONFIG
            limit = ENGINE_CONFIG.max_extracted_chars
        self.limit = limit
        self.parts: List[str] = []
        self.size = 0
        self.truncated = False

    @property
    def full(self) -> bool:
        return self.size >= self.limit

    def add(self, text: Optional[str]) -> bool:
        """Добавить фрагмент. False — лимит исчерпан (дальше читать не нужно)."""
        if not text:
            return not self.full
        if self.full:
            self.truncated = True
            return False
        remaining = self.limit - self.size
        if len(text) > remaining:
            text = text[:remaining]
            self.truncated = True
        self.parts.append(text)
        self.size += len(text) + 1
        return not self.full

    def text(self, sep: str = '\n') -> str:
        return sep.join(self.parts)


class BaseExtractor(ABC):
    """Базовый класс экстрактора"""

    # Расширения, которые обрабатывает экстрактор
    extensions: List[str] = []

    # Приоритет (меньше = выше приоритет)
    priority: int = 100

    @classmethod
    @abstractmethod
    def is_available(cls) -> bool:
        """Проверка доступности экстрактора"""
        pass

    @classmethod
    @abstractmethod
    def extract(cls, path: Path) -> ExtractionResult:
        """Извлечение текста из файла"""
        pass

    @classmethod
    def can_handle(cls, path: Path) -> bool:
        """Проверка, может ли экстрактор обработать файл"""
        return path.suffix.lower() in cls.extensions


class ExtractorRegistry:
    """Реестр экстракторов"""

    _extractors: List[Type[BaseExtractor]] = []
    _lock = threading.Lock()

    @classmethod
    def register(cls, extractor: Type[BaseExtractor]) -> Type[BaseExtractor]:
        """Декоратор для регистрации экстрактора"""
        with cls._lock:
            if extractor not in cls._extractors:
                cls._extractors.append(extractor)
                cls._extractors.sort(key=lambda x: x.priority)
        return extractor

    # Совместимость с примером плагина из старой документации
    register_class = register

    @classmethod
    def unregister(cls, extractor: Type[BaseExtractor]) -> None:
        """Удалить экстрактор из реестра"""
        with cls._lock:
            if extractor in cls._extractors:
                cls._extractors.remove(extractor)

    @classmethod
    def get_extractor(cls, path: Path) -> Optional[Type[BaseExtractor]]:
        """Получить подходящий экстрактор для файла"""
        for extractor in list(cls._extractors):
            try:
                if extractor.can_handle(path) and extractor.is_available():
                    return extractor
            except Exception:
                continue
        return None

    @classmethod
    def extract(cls, path: Path) -> ExtractionResult:
        """Извлечь текст из файла"""
        path = Path(path)
        extractor = cls.get_extractor(path)
        if extractor:
            try:
                result = extractor.extract(path)
                if result is None:
                    return ExtractionResult(error=f"{extractor.__name__} returned no result")
                return result
            except MemoryError:
                return ExtractionResult(error=f"MemoryError: file too large ({path.name})")
            except RecursionError:
                return ExtractionResult(error=f"RecursionError: file structure too deep ({path.name})")
            except Exception as e:
                return ExtractionResult(error=f"{type(e).__name__}: {e}")
        return ExtractionResult(error=f"No extractor for {path.suffix or path.name}")

    @classmethod
    def list_extractors(cls) -> List[Dict[str, Any]]:
        """Список зарегистрированных экстракторов"""
        result = []
        for ext in list(cls._extractors):
            try:
                available = bool(ext.is_available())
            except Exception:
                available = False
            result.append({
                "name": ext.__name__,
                "extensions": list(ext.extensions),
                "priority": ext.priority,
                "available": available,
            })
        return result

    @classmethod
    def supported_extensions(cls) -> List[str]:
        """Расширения, для которых есть доступный экстрактор."""
        exts = set()
        for item in cls.list_extractors():
            if item['available']:
                exts.update(item['extensions'])
        return sorted(exts)


# Shortcut
registry = ExtractorRegistry


# ═══════════════════════════════════════════════════════════════════════════════
# NESTED EXTRACTION (архивы, вложения писем)
# ═══════════════════════════════════════════════════════════════════════════════

_nesting = threading.local()


def nesting_depth() -> int:
    """Текущая глубина вложенности (архив в архиве, вложение в письме...)."""
    return getattr(_nesting, 'depth', 0)


@contextmanager
def nested_level():
    _nesting.depth = nesting_depth() + 1
    try:
        yield
    finally:
        _nesting.depth = nesting_depth() - 1


def worker_temp_dir() -> Path:
    """Каталог временных файлов текущего процесса (~/.astrex/cache/tmp/w<pid>).

    Если процесс убит (таймаут, OOM), каталог удаляется при следующем запуске
    сканирования, а не остаётся в /tmp навсегда.
    """
    from core.config import TEMP_PATH
    path = TEMP_PATH / f"w{os.getpid()}"
    path.mkdir(parents=True, exist_ok=True)
    return path


_SAFE_SUFFIX = re.compile(r'[^A-Za-z0-9.]')


def _temp_name_for(name: str) -> str:
    """Имя временного файла, сохраняющее расширение(я) исходного (tar.gz и т.п.)."""
    base = Path(name.replace('\\', '/')).name.lower()
    suffixes = Path(base).suffixes[-2:]
    suffix = _SAFE_SUFFIX.sub('', ''.join(suffixes))[:24]
    return f"member{suffix}"


def can_extract_name(name: str) -> bool:
    """Есть ли экстрактор для файла с таким именем."""
    return registry.get_extractor(Path(_temp_name_for(name))) is not None


def extract_nested_bytes(data: bytes, name: str) -> ExtractionResult:
    """Извлечь текст из байтов вложенного файла через реестр экстракторов.

    Учитывает лимиты глубины вложенности и размера; временный файл создаётся
    с безопасным именем (только расширение берётся из исходного имени).
    """
    import tempfile
    from core.config import ENGINE_CONFIG

    if nesting_depth() >= ENGINE_CONFIG.archive_max_depth:
        return ExtractionResult(error="max nesting depth reached",
                                metadata={'filename': name, 'skipped': 'max depth'})
    if not data:
        return ExtractionResult(text='', metadata={'filename': name})
    if len(data) > ENGINE_CONFIG.archive_member_max_size:
        return ExtractionResult(error=f"member too large ({len(data)} bytes)",
                                metadata={'filename': name, 'skipped': 'too large'})

    fd, tmp_name = tempfile.mkstemp(prefix="astrex-", suffix="-" + _temp_name_for(name),
                                    dir=str(worker_temp_dir()))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
        with nested_level():
            result = registry.extract(tmp_path)
        result.metadata = dict(result.metadata or {})
        result.metadata['filename'] = name
        return result
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass


__all__ = ['BaseExtractor', 'ExtractionResult', 'ExtractorRegistry', 'registry',
           'TextCollector', 'extract_nested_bytes', 'can_extract_name',
           'nesting_depth', 'nested_level', 'worker_temp_dir']
