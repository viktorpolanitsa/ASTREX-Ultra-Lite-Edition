#!/usr/bin/env python3
"""
ASTREX v3.0 — Extractors Package
Модули извлечения текста из различных форматов
"""

import logging

from .base import (BaseExtractor, ExtractionResult, ExtractorRegistry, registry,
                   TextCollector, extract_nested_bytes)

# Import all extractors to register them
from .documents import (
    PDFExtractor, DOCXExtractor, XLSXExtractor, PPTXExtractor,
    ODTExtractor, LegacyOfficeExtractor, RTFExtractor, EPUBExtractor,
    FB2Extractor, MOBIExtractor,
)
from .email import (
    EMLExtractor, MSGExtractor, MBOXExtractor, PSTExtractor,
)
from .archives import (
    ZIPExtractor, TARExtractor, CompressedFileExtractor, GZIPExtractor,
    RARExtractor, SevenZipExtractor,
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
    from .media import AudioExtractor, VideoExtractor
except Exception as e:  # pragma: no cover
    logging.getLogger("astrex.extractors").warning("Media extractors unavailable: %s", e)

try:
    from .network import PcapExtractor, NetflowExtractor
except Exception as e:  # pragma: no cover
    logging.getLogger("astrex.extractors").warning("Network extractors unavailable: %s", e)


def extract_text(path):
    """Convenience function for text extraction"""
    from pathlib import Path
    return registry.extract(Path(path))


def list_supported_formats():
    """List all supported file formats (extension → best available extractor)"""
    formats = {}
    for ext in registry.list_extractors():
        for extension in ext['extensions']:
            current = formats.get(extension)
            better = current is None or (ext['available'] and not current['available']) or (
                ext['available'] == current['available'] and ext['priority'] < current['priority'])
            if better:
                formats[extension] = {
                    'extractor': ext['name'],
                    'priority': ext['priority'],
                    'available': ext['available']
                }
    return formats


__all__ = [
    'BaseExtractor', 'ExtractionResult', 'ExtractorRegistry', 'registry',
    'TextCollector', 'extract_nested_bytes',
    'extract_text', 'list_supported_formats'
]
