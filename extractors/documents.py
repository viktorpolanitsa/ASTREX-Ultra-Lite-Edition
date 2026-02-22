#!/usr/bin/env python3
"""
ASTREX v3.0 — Document Extractors
PDF, Office (DOCX, XLSX, PPTX, ODT), RTF
"""

import re
import zipfile
from pathlib import Path
from typing import Optional, List

from .base import BaseExtractor, ExtractionResult, registry


# ═══════════════════════════════════════════════════════════════════════════════
# PDF EXTRACTOR
# ═══════════════════════════════════════════════════════════════════════════════

@registry.register
class PDFExtractor(BaseExtractor):
    """Извлечение текста из PDF"""
    
    extensions = ['.pdf']
    priority = 10
    
    _available: Optional[bool] = None
    
    @classmethod
    def is_available(cls) -> bool:
        if cls._available is None:
            try:
                import pypdf
                cls._available = True
            except ImportError:
                cls._available = False
        return cls._available
    
    @classmethod
    def extract(cls, path: Path) -> ExtractionResult:
        try:
            import warnings
            import logging
            import pypdf

            text_parts = []
            metadata = {}

            # Подавляем предупреждения pypdf о битых PDF
            # (incorrect startxref, EOF marker not found, Object not defined)
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", module=r"pypdf")
                logging.getLogger("pypdf").setLevel(logging.ERROR)

                with open(path, 'rb') as f:
                    try:
                        reader = pypdf.PdfReader(f, strict=False)
                    except Exception as e:
                        return ExtractionResult(
                            error=f"PDF corrupted or unreadable: {e}"
                        )

                    # Metadata
                    try:
                        if reader.metadata:
                            metadata = {
                                'title': reader.metadata.get('/Title'),
                                'author': reader.metadata.get('/Author'),
                                'subject': reader.metadata.get('/Subject'),
                                'creator': reader.metadata.get('/Creator'),
                                'producer': reader.metadata.get('/Producer'),
                                'pages': len(reader.pages)
                            }
                            metadata = {k: v for k, v in metadata.items() if v}
                    except Exception:
                        pass

                    # Extract text from all pages
                    for i, page in enumerate(reader.pages):
                        try:
                            page_text = page.extract_text()
                            if page_text:
                                text_parts.append(page_text)
                        except Exception:
                            continue

            text = '\n\n'.join(text_parts) if text_parts else None

            return ExtractionResult(text=text, metadata=metadata)

        except Exception as e:
            return ExtractionResult(error=f"PDF extraction failed: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# OFFICE EXTRACTORS (OOXML)
# ═══════════════════════════════════════════════════════════════════════════════

class OOXMLExtractorBase(BaseExtractor):
    """Базовый экстрактор для Office Open XML"""
    
    XML_TAG_PATTERN = re.compile(r'<[^>]+>')
    
    @classmethod
    def is_available(cls) -> bool:
        return True  # zipfile is built-in
    
    @classmethod
    def _strip_xml_tags(cls, content: str) -> str:
        text = cls.XML_TAG_PATTERN.sub(' ', content)
        return ' '.join(text.split())
    
    @classmethod
    def _read_xml_from_zip(cls, zf: zipfile.ZipFile, path: str) -> Optional[str]:
        try:
            if path in zf.namelist():
                content = zf.read(path).decode('utf-8', errors='ignore')
                return cls._strip_xml_tags(content)
        except Exception:
            pass
        return None


@registry.register
class DOCXExtractor(OOXMLExtractorBase):
    """Извлечение текста из DOCX"""
    
    extensions = ['.docx']
    priority = 20
    
    @classmethod
    def extract(cls, path: Path) -> ExtractionResult:
        try:
            text_parts = []
            metadata = {}
            
            with zipfile.ZipFile(path, 'r') as zf:
                # Main document
                main_text = cls._read_xml_from_zip(zf, 'word/document.xml')
                if main_text:
                    text_parts.append(main_text)
                
                # Headers
                for name in zf.namelist():
                    if name.startswith('word/header') and name.endswith('.xml'):
                        header_text = cls._read_xml_from_zip(zf, name)
                        if header_text:
                            text_parts.append(header_text)
                
                # Footers
                for name in zf.namelist():
                    if name.startswith('word/footer') and name.endswith('.xml'):
                        footer_text = cls._read_xml_from_zip(zf, name)
                        if footer_text:
                            text_parts.append(footer_text)
                
                # Comments
                comments = cls._read_xml_from_zip(zf, 'word/comments.xml')
                if comments:
                    text_parts.append(f"[Комментарии: {comments}]")
                
                # Core properties (metadata)
                if 'docProps/core.xml' in zf.namelist():
                    core = zf.read('docProps/core.xml').decode('utf-8', errors='ignore')
                    # Simple extraction of common fields
                    for tag in ['dc:title', 'dc:creator', 'dc:subject', 'cp:lastModifiedBy']:
                        match = re.search(f'<{tag}>([^<]+)</{tag}>', core)
                        if match:
                            key = tag.split(':')[-1]
                            metadata[key] = match.group(1)
            
            text = '\n\n'.join(text_parts) if text_parts else None
            return ExtractionResult(text=text, metadata=metadata)
            
        except Exception as e:
            return ExtractionResult(error=f"DOCX extraction failed: {e}")


@registry.register
class XLSXExtractor(OOXMLExtractorBase):
    """Извлечение текста из XLSX"""
    
    extensions = ['.xlsx']
    priority = 20
    
    @classmethod
    def extract(cls, path: Path) -> ExtractionResult:
        try:
            text_parts = []
            metadata = {'sheets': []}
            
            with zipfile.ZipFile(path, 'r') as zf:
                # Shared strings
                shared_strings = cls._read_xml_from_zip(zf, 'xl/sharedStrings.xml')
                if shared_strings:
                    text_parts.append(shared_strings)
                
                # Worksheets
                for name in sorted(zf.namelist()):
                    if name.startswith('xl/worksheets/sheet') and name.endswith('.xml'):
                        sheet_num = re.search(r'sheet(\d+)', name)
                        if sheet_num:
                            metadata['sheets'].append(f"Sheet{sheet_num.group(1)}")
                        
                        sheet_text = cls._read_xml_from_zip(zf, name)
                        if sheet_text:
                            text_parts.append(sheet_text)
                
                # Comments
                for name in zf.namelist():
                    if 'comments' in name and name.endswith('.xml'):
                        comments = cls._read_xml_from_zip(zf, name)
                        if comments:
                            text_parts.append(f"[Комментарии: {comments}]")
            
            text = '\n\n'.join(text_parts) if text_parts else None
            return ExtractionResult(text=text, metadata=metadata)
            
        except Exception as e:
            return ExtractionResult(error=f"XLSX extraction failed: {e}")


@registry.register
class PPTXExtractor(OOXMLExtractorBase):
    """Извлечение текста из PPTX"""
    
    extensions = ['.pptx']
    priority = 20
    
    @classmethod
    def extract(cls, path: Path) -> ExtractionResult:
        try:
            text_parts = []
            metadata = {'slides': 0}
            
            with zipfile.ZipFile(path, 'r') as zf:
                # Slides
                slide_files = sorted([
                    n for n in zf.namelist() 
                    if n.startswith('ppt/slides/slide') and n.endswith('.xml')
                ], key=lambda x: int(re.search(r'slide(\d+)', x).group(1)) if re.search(r'slide(\d+)', x) else 0)
                
                metadata['slides'] = len(slide_files)
                
                for name in slide_files:
                    slide_text = cls._read_xml_from_zip(zf, name)
                    if slide_text:
                        text_parts.append(slide_text)
                
                # Notes
                for name in zf.namelist():
                    if 'notesSlides' in name and name.endswith('.xml'):
                        notes = cls._read_xml_from_zip(zf, name)
                        if notes:
                            text_parts.append(f"[Заметки: {notes}]")
            
            text = '\n\n'.join(text_parts) if text_parts else None
            return ExtractionResult(text=text, metadata=metadata)
            
        except Exception as e:
            return ExtractionResult(error=f"PPTX extraction failed: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# OPENDOCUMENT EXTRACTORS
# ═══════════════════════════════════════════════════════════════════════════════

@registry.register
class ODTExtractor(OOXMLExtractorBase):
    """Извлечение текста из ODT/ODS/ODP"""
    
    extensions = ['.odt', '.ods', '.odp']
    priority = 25
    
    @classmethod
    def extract(cls, path: Path) -> ExtractionResult:
        try:
            text_parts = []
            metadata = {}
            
            with zipfile.ZipFile(path, 'r') as zf:
                # Content
                content = cls._read_xml_from_zip(zf, 'content.xml')
                if content:
                    text_parts.append(content)
                
                # Styles (might contain text)
                styles = cls._read_xml_from_zip(zf, 'styles.xml')
                if styles:
                    text_parts.append(styles)
                
                # Meta
                if 'meta.xml' in zf.namelist():
                    meta = zf.read('meta.xml').decode('utf-8', errors='ignore')
                    for tag in ['dc:title', 'dc:creator', 'dc:subject', 'meta:creation-date']:
                        match = re.search(f'<{tag}>([^<]+)</{tag}>', meta)
                        if match:
                            key = tag.split(':')[-1]
                            metadata[key] = match.group(1)
            
            text = '\n\n'.join(text_parts) if text_parts else None
            return ExtractionResult(text=text, metadata=metadata)
            
        except Exception as e:
            return ExtractionResult(error=f"ODF extraction failed: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# LEGACY OFFICE (DOC, XLS, PPT)
# ═══════════════════════════════════════════════════════════════════════════════

@registry.register
class LegacyOfficeExtractor(BaseExtractor):
    """Извлечение текста из legacy Office (DOC, XLS, PPT)"""
    
    extensions = ['.doc', '.xls', '.ppt']
    priority = 30
    
    _available: Optional[bool] = None
    
    @classmethod
    def is_available(cls) -> bool:
        if cls._available is None:
            try:
                import olefile
                cls._available = True
            except ImportError:
                cls._available = False
        return cls._available
    
    @classmethod
    def extract(cls, path: Path) -> ExtractionResult:
        try:
            import olefile
            
            text_parts = []
            
            with olefile.OleFileIO(path) as ole:
                # Try to find text streams
                for stream in ole.listdir():
                    stream_path = '/'.join(stream)
                    try:
                        # WordDocument stream for DOC
                        if 'WordDocument' in stream_path or 'Document' in stream_path:
                            data = ole.openstream(stream).read()
                            # Basic text extraction (not perfect for complex docs)
                            text = data.decode('utf-16', errors='ignore')
                            text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', text)
                            if text.strip():
                                text_parts.append(text)
                    except Exception:
                        continue
            
            text = '\n\n'.join(text_parts) if text_parts else None
            return ExtractionResult(text=text)
            
        except Exception as e:
            return ExtractionResult(error=f"Legacy Office extraction failed: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# RTF
# ═══════════════════════════════════════════════════════════════════════════════

@registry.register
class RTFExtractor(BaseExtractor):
    """Извлечение текста из RTF"""
    
    extensions = ['.rtf']
    priority = 25
    
    _available: Optional[bool] = None
    
    @classmethod
    def is_available(cls) -> bool:
        if cls._available is None:
            try:
                import striprtf.striprtf
                cls._available = True
            except ImportError:
                # Fallback to regex
                cls._available = True
        return cls._available
    
    @classmethod
    def extract(cls, path: Path) -> ExtractionResult:
        try:
            with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
            
            # Try striprtf first
            try:
                from striprtf.striprtf import rtf_to_text
                text = rtf_to_text(content)
            except ImportError:
                # Fallback to basic regex
                text = cls._strip_rtf_basic(content)
            
            return ExtractionResult(text=text)
            
        except Exception as e:
            return ExtractionResult(error=f"RTF extraction failed: {e}")
    
    @classmethod
    def _strip_rtf_basic(cls, content: str) -> str:
        """Basic RTF stripping with regex"""
        # Remove RTF commands
        text = re.sub(r'\\[a-z]+\d*\s?', ' ', content)
        # Remove groups
        text = re.sub(r'\{[^{}]*\}', '', text)
        # Remove remaining braces
        text = re.sub(r'[{}]', '', text)
        # Cleanup
        text = ' '.join(text.split())
        return text


# ═══════════════════════════════════════════════════════════════════════════════
# EPUB EXTRACTOR
# ═══════════════════════════════════════════════════════════════════════════════

@registry.register
class EPUBExtractor(BaseExtractor):
    """Извлечение текста из EPUB"""

    extensions = ['.epub']
    priority = 30

    @classmethod
    def is_available(cls) -> bool:
        return True  # uses zipfile + xml (built-in)

    @classmethod
    def extract(cls, path: Path) -> ExtractionResult:
        try:
            import xml.etree.ElementTree as ET

            text_parts = []
            with zipfile.ZipFile(path, 'r') as zf:
                for name in zf.namelist():
                    if name.endswith(('.xhtml', '.html', '.htm', '.xml')) and 'META-INF' not in name:
                        try:
                            content = zf.read(name).decode('utf-8', errors='replace')
                            # Strip HTML tags
                            clean = re.sub(r'<[^>]+>', ' ', content)
                            clean = re.sub(r'\s+', ' ', clean).strip()
                            if len(clean) > 20:
                                text_parts.append(clean)
                        except Exception:
                            continue

            return ExtractionResult(
                text='\n\n'.join(text_parts) if text_parts else "[EPUB: no text found]",
                metadata={'chapters': len(text_parts)}
            )
        except Exception as e:
            return ExtractionResult(error=f"EPUB extraction failed: {e}")


__all__ = [
    'PDFExtractor', 'DOCXExtractor', 'XLSXExtractor', 'PPTXExtractor',
    'ODTExtractor', 'LegacyOfficeExtractor', 'RTFExtractor', 'EPUBExtractor'
]
