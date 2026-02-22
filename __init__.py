#!/usr/bin/env python3
"""
ASTREX v3.0 — Intelligence System
Форензическая система глубокого анализа данных

Modules:
    core    - Core functionality (scanner, index, NLP, graph)
    extractors - File format extractors
    ml      - Machine learning (vectors, LLM)
    web     - REST API
"""

from pathlib import Path
from core.config import VERSION as __version__

__author__ = "ASTREX Team"

# Package root
PACKAGE_ROOT = Path(__file__).parent

# Lazy imports
def get_scanner():
    from .core import ScanEngine
    return ScanEngine

def get_index():
    from .core import file_index
    return file_index

def get_extractor():
    from .extractors import registry
    return registry

__all__ = [
    '__version__',
    'PACKAGE_ROOT',
    'get_scanner',
    'get_index',
    'get_extractor'
]
