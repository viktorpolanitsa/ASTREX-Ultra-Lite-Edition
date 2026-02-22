#!/usr/bin/env python3
"""
ASTREX v3.0 — Text Extractors
Plaintext, code, config files with encoding detection
"""

import csv
import mmap
import os
from pathlib import Path
from typing import Optional, Tuple

from .base import BaseExtractor, ExtractionResult, registry
from core.config import ENGINE_CONFIG, FILE_TYPES

# Устанавливаем лимит один раз при загрузке модуля, а не при каждом вызове extract()
csv.field_size_limit(1024 * 1024)  # 1MB per field max


# ═══════════════════════════════════════════════════════════════════════════════
# PLAINTEXT EXTRACTOR
# ═══════════════════════════════════════════════════════════════════════════════

@registry.register
class PlainTextExtractor(BaseExtractor):
    """Извлечение из текстовых файлов с определением кодировки"""
    
    extensions = list(FILE_TYPES.TEXT | FILE_TYPES.CODE | FILE_TYPES.DATA)
    priority = 100  # Lowest priority - fallback for text files
    
    @classmethod
    def is_available(cls) -> bool:
        return True
    
    @classmethod
    def extract(cls, path: Path) -> ExtractionResult:
        try:
            file_size = path.stat().st_size
            
            if file_size == 0:
                return ExtractionResult(text="", metadata={'empty': True})
            
            # For large files, use mmap
            if file_size > ENGINE_CONFIG.chunk_size:
                return cls._extract_large_file(path, file_size)
            
            # Normal extraction with encoding detection
            text, encoding = cls._read_with_encoding(path)
            
            if text is not None:
                return ExtractionResult(
                    text=text,
                    metadata={
                        'encoding': encoding,
                        'size': file_size,
                        'lines': text.count('\n') + 1
                    }
                )
            
            return ExtractionResult(error="Could not decode file with any supported encoding")
            
        except Exception as e:
            return ExtractionResult(error=f"Text extraction failed: {e}")
    
    @classmethod
    def _read_with_encoding(cls, path: Path, max_bytes: int = 0) -> Tuple[Optional[str], Optional[str]]:
        """Чтение файла с автоопределением кодировки.

        Args:
            max_bytes: максимум байт для чтения (0 = без лимита, но не более chunk_size)
        """
        if max_bytes <= 0:
            max_bytes = ENGINE_CONFIG.chunk_size  # 64MB safety cap

        file_size = path.stat().st_size
        read_limit = min(file_size, max_bytes)

        # Try chardet first if available
        try:
            import chardet

            with open(path, 'rb') as f:
                raw = f.read(65536)  # First 64KB for detection

            detected = chardet.detect(raw)
            if detected['encoding'] and detected['confidence'] > 0.7:
                try:
                    with open(path, 'r', encoding=detected['encoding']) as f:
                        return f.read(read_limit), detected['encoding']
                except (UnicodeDecodeError, LookupError):
                    pass
        except ImportError:
            pass

        # Fallback to manual detection
        for encoding in ENGINE_CONFIG.encodings:
            try:
                with open(path, 'r', encoding=encoding) as f:
                    text = f.read(read_limit)
                return text, encoding
            except (UnicodeDecodeError, LookupError):
                continue

        # Last resort: binary read with ignore
        try:
            with open(path, 'rb') as f:
                raw = f.read(read_limit)
            return raw.decode('utf-8', errors='ignore'), 'utf-8 (lossy)'
        except Exception:
            return None, None
    
    @classmethod
    def _extract_large_file(cls, path: Path, file_size: int) -> ExtractionResult:
        """Извлечение из больших файлов через mmap"""
        try:
            with open(path, 'rb') as f:
                with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                    # Read first and last chunks
                    chunk_size = ENGINE_CONFIG.chunk_size
                    
                    head = mm[:chunk_size]
                    # Only read tail if file is large enough that head and tail don't overlap
                    if file_size > 2 * chunk_size:
                        tail = mm[-chunk_size:]
                    else:
                        tail = b''

                    # Try to decode
                    for encoding in ENGINE_CONFIG.encodings:
                        try:
                            head_text = head.decode(encoding)
                            tail_text = tail.decode(encoding) if tail else ''

                            if tail_text:
                                skipped = file_size - 2 * chunk_size
                                text = f"{head_text}\n\n[...пропущено {skipped} байт...]\n\n{tail_text}"
                            else:
                                text = head_text
                            
                            return ExtractionResult(
                                text=text,
                                metadata={
                                    'encoding': encoding,
                                    'size': file_size,
                                    'truncated': True
                                }
                            )
                        except UnicodeDecodeError:
                            continue
                    
                    return ExtractionResult(error="Large file encoding detection failed")
                    
        except Exception as e:
            return ExtractionResult(error=f"Large file extraction failed: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# JSON EXTRACTOR (with pretty parsing)
# ═══════════════════════════════════════════════════════════════════════════════

@registry.register
class JSONExtractor(BaseExtractor):
    """Извлечение и форматирование JSON"""
    
    extensions = ['.json']
    priority = 90
    
    @classmethod
    def is_available(cls) -> bool:
        return True
    
    @classmethod
    def extract(cls, path: Path) -> ExtractionResult:
        try:
            import json
            
            text, encoding = PlainTextExtractor._read_with_encoding(path)
            
            if text is None:
                return ExtractionResult(error="Could not read JSON file")
            
            # Parse JSON
            try:
                data = json.loads(text)
                
                # Extract text content from common patterns
                text_parts = []
                cls._extract_text_from_json(data, text_parts)
                
                metadata = {
                    'encoding': encoding,
                    'type': type(data).__name__,
                }
                
                if isinstance(data, dict):
                    metadata['keys'] = list(data.keys())[:20]
                elif isinstance(data, list):
                    metadata['length'] = len(data)
                
                extracted_text = '\n'.join(text_parts) if text_parts else text[:50000]
                
                return ExtractionResult(text=extracted_text, metadata=metadata)
                
            except json.JSONDecodeError as e:
                # Return as plain text
                return ExtractionResult(
                    text=text,
                    metadata={'encoding': encoding, 'json_error': str(e)}
                )
                
        except Exception as e:
            return ExtractionResult(error=f"JSON extraction failed: {e}")
    
    @classmethod
    def _extract_text_from_json(cls, obj, parts: list, depth: int = 0, max_depth: int = 10):
        """Итеративное извлечение текстовых значений из JSON (без рекурсии)"""
        priority_keys = {'text', 'content', 'message', 'body', 'description',
                        'title', 'name', 'comment', 'note', 'value'}

        # Итеративный обход стеком вместо рекурсии — защита от stack overflow
        stack = [(obj, 0)]
        while stack:
            current, current_depth = stack.pop()
            if current_depth > max_depth:
                continue

            if isinstance(current, str):
                if len(current) > 20:
                    parts.append(current)
            elif isinstance(current, dict):
                # Добавляем в стек в обратном порядке (приоритетные первыми)
                non_priority = [(v, current_depth + 1) for k, v in current.items()
                                if k not in priority_keys]
                priority = [(current[k], current_depth + 1) for k in priority_keys
                            if k in current]
                stack.extend(reversed(non_priority))
                stack.extend(reversed(priority))
            elif isinstance(current, list):
                for item in reversed(current[:100]):
                    stack.append((item, current_depth + 1))


# ═══════════════════════════════════════════════════════════════════════════════
# XML EXTRACTOR
# ═══════════════════════════════════════════════════════════════════════════════

@registry.register
class XMLExtractor(BaseExtractor):
    """Извлечение текста из XML"""
    
    extensions = ['.xml', '.xhtml']
    priority = 90
    
    @classmethod
    def is_available(cls) -> bool:
        return True
    
    @classmethod
    def extract(cls, path: Path) -> ExtractionResult:
        try:
            text, encoding = PlainTextExtractor._read_with_encoding(path)
            
            if text is None:
                return ExtractionResult(error="Could not read XML file")
            
            # Try to parse and extract text
            try:
                import xml.etree.ElementTree as ET
                
                root = ET.fromstring(text)
                text_parts = []
                cls._extract_text_from_xml(root, text_parts)
                
                extracted_text = ' '.join(text_parts) if text_parts else text[:50000]
                
                return ExtractionResult(
                    text=extracted_text,
                    metadata={
                        'encoding': encoding,
                        'root_tag': root.tag
                    }
                )
                
            except ET.ParseError:
                # Return as is
                return ExtractionResult(text=text, metadata={'encoding': encoding})
                
        except Exception as e:
            return ExtractionResult(error=f"XML extraction failed: {e}")
    
    @classmethod
    def _extract_text_from_xml(cls, element, parts: list):
        """Итеративное извлечение текста из XML (без рекурсии)"""
        stack = [element]
        while stack:
            el = stack.pop()
            if el.text and el.text.strip():
                parts.append(el.text.strip())
            # Добавляем детей в обратном порядке для сохранения порядка обхода
            children = list(el)
            for child in reversed(children):
                stack.append(child)
            for child in children:
                if child.tail and child.tail.strip():
                    parts.append(child.tail.strip())


# ═══════════════════════════════════════════════════════════════════════════════
# HTML EXTRACTOR
# ═══════════════════════════════════════════════════════════════════════════════

@registry.register
class HTMLExtractor(BaseExtractor):
    """Извлечение текста из HTML"""
    
    extensions = ['.html', '.htm']
    priority = 85
    
    @classmethod
    def is_available(cls) -> bool:
        return True
    
    @classmethod
    def extract(cls, path: Path) -> ExtractionResult:
        try:
            text, encoding = PlainTextExtractor._read_with_encoding(path)
            
            if text is None:
                return ExtractionResult(error="Could not read HTML file")
            
            # Try BeautifulSoup first
            try:
                from bs4 import BeautifulSoup
                
                soup = BeautifulSoup(text, 'html.parser')
                
                # Remove scripts and styles
                for tag in soup(['script', 'style', 'meta', 'link']):
                    tag.decompose()
                
                # Get title
                title = soup.title.string if soup.title else None
                
                # Get text
                extracted_text = soup.get_text(separator='\n', strip=True)
                
                return ExtractionResult(
                    text=extracted_text,
                    metadata={
                        'encoding': encoding,
                        'title': title
                    }
                )
                
            except ImportError:
                # Fallback to regex
                import re
                
                # Remove scripts and styles
                text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL | re.IGNORECASE)
                text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
                
                # Remove tags
                text = re.sub(r'<[^>]+>', ' ', text)
                
                # Decode entities
                text = re.sub(r'&nbsp;', ' ', text)
                text = re.sub(r'&lt;', '<', text)
                text = re.sub(r'&gt;', '>', text)
                text = re.sub(r'&amp;', '&', text)
                
                # Cleanup
                text = ' '.join(text.split())
                
                return ExtractionResult(text=text, metadata={'encoding': encoding})
                
        except Exception as e:
            return ExtractionResult(error=f"HTML extraction failed: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# CSV EXTRACTOR
# ═══════════════════════════════════════════════════════════════════════════════

@registry.register
class CSVExtractor(BaseExtractor):
    """Извлечение из CSV/TSV — потоковое чтение, безопасно для больших файлов"""

    extensions = ['.csv', '.tsv']
    priority = 80

    MAX_ROWS = 1000
    MAX_CELL = 200
    MAX_READ_BYTES = 50 * 1024 * 1024  # 50MB max read

    @classmethod
    def is_available(cls) -> bool:
        return True

    @classmethod
    def _detect_encoding(cls, path: Path) -> str:
        """Определить кодировку по первым байтам"""
        try:
            import chardet
            with open(path, 'rb') as f:
                raw = f.read(65536)
            detected = chardet.detect(raw)
            if detected['encoding'] and detected['confidence'] > 0.7:
                return detected['encoding']
        except ImportError:
            pass

        for enc in ENGINE_CONFIG.encodings:
            try:
                with open(path, 'r', encoding=enc) as f:
                    f.read(4096)
                return enc
            except (UnicodeDecodeError, LookupError):
                continue
        return 'utf-8'

    @classmethod
    def extract(cls, path: Path) -> ExtractionResult:
        try:
            file_size = path.stat().st_size
            encoding = cls._detect_encoding(path)
            delimiter = '\t' if path.suffix.lower() == '.tsv' else ','

            try:
                # Stream read — never load entire file
                with open(path, 'r', encoding=encoding, errors='replace', newline='') as f:
                    reader = csv.reader(f, delimiter=delimiter)

                    headers = []
                    text_parts = []
                    row_count = 0

                    for row in reader:
                        if row_count == 0:
                            headers = [c[:cls.MAX_CELL] for c in row]
                            text_parts.append(' | '.join(headers))
                        else:
                            row_text = ' | '.join(str(c)[:cls.MAX_CELL] for c in row)
                            text_parts.append(row_text)

                        row_count += 1
                        if row_count >= cls.MAX_ROWS:
                            break

                    if text_parts:
                        return ExtractionResult(
                            text='\n'.join(text_parts),
                            metadata={
                                'encoding': encoding,
                                'headers': headers,
                                'rows_read': row_count,
                                'file_size': file_size,
                                'delimiter': delimiter,
                                'truncated': row_count >= cls.MAX_ROWS
                            }
                        )

            except csv.Error as e:
                # Fallback: read first chunk as plain text
                with open(path, 'r', encoding=encoding, errors='replace') as f:
                    text = f.read(cls.MAX_READ_BYTES)
                return ExtractionResult(
                    text=text,
                    metadata={'encoding': encoding, 'csv_error': str(e), 'file_size': file_size}
                )

            return ExtractionResult(error="Empty CSV file")

        except Exception as e:
            return ExtractionResult(error=f"CSV extraction failed: {e}")


__all__ = [
    'PlainTextExtractor', 'JSONExtractor', 'XMLExtractor', 
    'HTMLExtractor', 'CSVExtractor'
]
