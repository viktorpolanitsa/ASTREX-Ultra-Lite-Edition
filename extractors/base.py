#!/usr/bin/env python3
"""
ASTREX v3.0 — Extractors Base
Базовый класс и реестр экстракторов
"""

from abc import ABC, abstractmethod
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
        """Текст включая вложения"""
        parts = [self.text] if self.text else []
        for att in self.attachments:
            if att.text:
                parts.append(att.text)
        return '\n\n'.join(parts)


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
    
    @classmethod
    def register(cls, extractor: Type[BaseExtractor]) -> Type[BaseExtractor]:
        """Декоратор для регистрации экстрактора"""
        cls._extractors.append(extractor)
        cls._extractors.sort(key=lambda x: x.priority)
        return extractor
    
    @classmethod
    def get_extractor(cls, path: Path) -> Optional[Type[BaseExtractor]]:
        """Получить подходящий экстрактор для файла"""
        for extractor in cls._extractors:
            if extractor.can_handle(path) and extractor.is_available():
                return extractor
        return None
    
    @classmethod
    def extract(cls, path: Path) -> ExtractionResult:
        """Извлечь текст из файла"""
        extractor = cls.get_extractor(path)
        if extractor:
            try:
                return extractor.extract(path)
            except MemoryError:
                return ExtractionResult(error=f"MemoryError: file too large ({path.name})")
            except RecursionError:
                return ExtractionResult(error=f"RecursionError: file structure too deep ({path.name})")
            except Exception as e:
                return ExtractionResult(error=f"{type(e).__name__}: {e}")
        return ExtractionResult(error=f"No extractor for {path.suffix}")
    
    @classmethod
    def list_extractors(cls) -> List[Dict[str, Any]]:
        """Список зарегистрированных экстракторов"""
        return [
            {
                "name": ext.__name__,
                "extensions": ext.extensions,
                "priority": ext.priority,
                "available": ext.is_available()
            }
            for ext in cls._extractors
        ]


# Shortcut
registry = ExtractorRegistry


__all__ = ['BaseExtractor', 'ExtractionResult', 'ExtractorRegistry', 'registry']
