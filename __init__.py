#!/usr/bin/env python3
"""
ASTREX v3.0 — Intelligence System
Форензическая система глубокого анализа данных

Modules:
    core       - Core functionality (scanner, index, NLP, graph)
    extractors - File format extractors
    ml         - Machine learning (vectors, LLM)
    web        - REST API

Модули проекта используют абсолютные импорты (core, extractors, ...),
поэтому при импорте каталога как пакета его корень добавляется в sys.path.
"""

import sys
from pathlib import Path

# Package root
PACKAGE_ROOT = Path(__file__).resolve().parent
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from core.config import VERSION as __version__  # noqa: E402

__author__ = "ASTREX Team"


# Lazy imports
def get_scanner():
    from core import ScanEngine
    return ScanEngine


def get_index():
    from core import file_index
    return file_index


def get_extractor():
    from extractors import registry
    return registry


__all__ = [
    '__version__',
    'PACKAGE_ROOT',
    'get_scanner',
    'get_index',
    'get_extractor'
]
