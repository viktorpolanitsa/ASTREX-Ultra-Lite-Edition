#!/usr/bin/env python3
"""
ASTREX v3.0 — Extractors Package
Модули извлечения текста из различных форматов
"""

from .base import BaseExtractor, ExtractionResult, ExtractorRegistry, registry

# Import all extractors to register them
from .documents import (
    PDFExtractor, DOCXExtractor, XLSXExtractor, PPTXExtractor,
    ODTExtractor, LegacyOfficeExtractor, RTFExtractor, EPUBExtractor,
)
from .email import (
    EMLExtractor, MSGExtractor, MBOXExtractor, PSTExtractor,
)
from .archives import (
    ZIPExtractor, TARExtractor, GZIPExtractor, RARExtractor, SevenZipExtractor,
)
from .images import (
    ImageOCRExtractor, ScreenshotExtractor,
)
from .databases import (
    SQLiteExtractor, AccessExtractor, SQLDumpExtractor, MySQLBinaryExtractor,
)
from .text import (
    PlainTextExtractor, JSONExtractor, XMLExtractor, HTMLExtractor, CSVExtractor,
)

try:
    from .media import *
except ImportError:
    pass

try:
    from .network import *
except ImportError:
    pass


def extract_text(path):
    """Convenience function for text extraction"""
    from pathlib import Path
    return registry.extract(Path(path))


def list_supported_formats():
    """List all supported file formats"""
    extractors = registry.list_extractors()
    
    formats = {}
    for ext in extractors:
        for extension in ext['extensions']:
            if extension not in formats or ext['priority'] < formats[extension]['priority']:
                formats[extension] = {
                    'extractor': ext['name'],
                    'priority': ext['priority'],
                    'available': ext['available']
                }
    
    return formats


__all__ = [
    'BaseExtractor', 'ExtractionResult', 'ExtractorRegistry', 'registry',
    'extract_text', 'list_supported_formats'
]
