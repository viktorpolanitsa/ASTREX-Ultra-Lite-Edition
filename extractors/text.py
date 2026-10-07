#!/usr/bin/env python3
"""
ASTREX v3.0 — Text Extractors
Plaintext, code, config files with encoding detection
"""

import csv
import html as html_module
import json
import re
import sys
from pathlib import Path
from typing import Optional, Tuple

from .base import BaseExtractor, ExtractionResult, TextCollector, registry
from core.config import ENGINE_CONFIG, FILE_TYPES
from core.encoding import decode_bytes, detect_encoding, is_probably_binary

# Поля CSV бывают большими (текст писем в выгрузках); лимит задаётся один раз
csv.field_size_limit(min(sys.maxsize, 64 * 1024 * 1024))


def _byte_budget() -> int:
    """Сколько байт читать, чтобы получить ~max_extracted_chars символов."""
    return min(ENGINE_CONFIG.chunk_size, ENGINE_CONFIG.max_extracted_chars * 4)


def read_text_file(path: Path, max_bytes: Optional[int] = None) -> Tuple[str, str, bool]:
    """Прочитать текстовый файл с определением кодировки.

    Для файлов больше лимита читается начало (~80%) и конец (~20%).
    Разрез посередине многобайтового символа UTF-8 не ломает
    определение кодировки.

    Returns:
        (текст, кодировка, был ли текст обрезан)
    """
    if max_bytes is None:
        max_bytes = _byte_budget()
    size = path.stat().st_size

    with open(path, 'rb') as f:
        if size <= max_bytes:
            raw = f.read()
            text, encoding = decode_bytes(raw)
            return text, encoding, False

        head_size = int(max_bytes * 0.8)
        tail_size = max_bytes - head_size
        head_size -= head_size % 4  # выравнивание для UTF-16/32
        tail_size -= tail_size % 4
        head = f.read(head_size)
        f.seek(size - tail_size)
        tail = f.read(tail_size)

    encoding, _ = detect_encoding(head, final=False)
    if encoding == 'utf-8-sig':
        encoding = 'utf-8'
        head = head[3:]
    head_text, _ = decode_bytes(head, declared=encoding, final=False)
    tail_text = tail.decode(encoding, errors='replace') if encoding else ''
    # Отбрасываем начало хвоста до первого перевода строки (возможен обрезанный символ)
    nl = tail_text.find('\n')
    if 0 <= nl < 4096:
        tail_text = tail_text[nl + 1:]
    skipped = size - head_size - tail_size
    text = f"{head_text}\n\n[...пропущено {skipped} байт...]\n\n{tail_text}"
    return text, encoding, True


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

            with open(path, 'rb') as f:
                sample = f.read(8192)
            if is_probably_binary(sample):
                # .dat/.ts и т.п. часто бывают двоичными (видео MPEG-TS, дампы):
                # это не ошибка, текста просто нет
                return ExtractionResult(text='', metadata={'binary': True, 'size': file_size,
                                                           'skipped': 'binary content'})

            text, encoding, truncated = read_text_file(path)
            return ExtractionResult(
                text=text,
                metadata={
                    'encoding': encoding,
                    'size': file_size,
                    'lines': text.count('\n') + 1,
                    'truncated': truncated,
                }
            )

        except Exception as e:
            return ExtractionResult(error=f"Text extraction failed: {e}")

    @classmethod
    def _read_with_encoding(cls, path: Path, max_bytes: int = 0) -> Tuple[Optional[str], Optional[str]]:
        """Совместимость: чтение файла с автоопределением кодировки."""
        try:
            text, encoding, _ = read_text_file(path, max_bytes or None)
            return text, encoding
        except OSError:
            return None, None


# ═══════════════════════════════════════════════════════════════════════════════
# JSON EXTRACTOR
# ═══════════════════════════════════════════════════════════════════════════════

@registry.register
class JSONExtractor(BaseExtractor):
    """Извлечение всех значений из JSON (строки любой длины, числа, ключи)"""

    extensions = ['.json']
    priority = 90

    MAX_PARSE_BYTES = 64 * 1024 * 1024

    @classmethod
    def is_available(cls) -> bool:
        return True

    @classmethod
    def extract(cls, path: Path) -> ExtractionResult:
        try:
            size = path.stat().st_size
            text, encoding, truncated = read_text_file(path)

            if size > cls.MAX_PARSE_BYTES or truncated:
                return ExtractionResult(text=text, metadata={
                    'encoding': encoding, 'truncated': truncated, 'parsed': False})

            try:
                data = json.loads(text)
            except json.JSONDecodeError as e:
                # JSON Lines / битый JSON — индексируем как текст
                return ExtractionResult(text=text, metadata={'encoding': encoding, 'json_error': str(e)})

            collector = TextCollector()
            cls._extract_text_from_json(data, collector)

            metadata = {'encoding': encoding, 'type': type(data).__name__,
                        'truncated': collector.truncated}
            if isinstance(data, dict):
                metadata['keys'] = list(data.keys())[:20]
            elif isinstance(data, list):
                metadata['length'] = len(data)

            return ExtractionResult(text=collector.text('\n'), metadata=metadata)

        except Exception as e:
            return ExtractionResult(error=f"JSON extraction failed: {e}")

    @classmethod
    def _extract_text_from_json(cls, obj, collector, depth: int = 0, max_depth: int = 64):
        """Итеративный обход JSON: строки "ключ: значение" для всех скаляров."""
        if not isinstance(collector, TextCollector):  # совместимость со старым API (list)
            target = collector
            collector = TextCollector()
            cls._extract_text_from_json(obj, collector, depth, max_depth)
            target.extend(collector.parts)
            return

        stack = [(obj, None, 0)]
        while stack:
            current, key, current_depth = stack.pop()
            if current_depth > max_depth:
                continue
            if isinstance(current, dict):
                items = list(current.items())
                for k, v in reversed(items):
                    stack.append((v, str(k), current_depth + 1))
            elif isinstance(current, list):
                for item in reversed(current):
                    stack.append((item, key, current_depth + 1))
            elif current is None:
                continue
            else:
                value = current if isinstance(current, str) else json.dumps(current, ensure_ascii=False)
                if not value.strip():
                    continue
                line = f"{key}: {value}" if key else value
                if not collector.add(line):
                    return


# ═══════════════════════════════════════════════════════════════════════════════
# XML EXTRACTOR
# ═══════════════════════════════════════════════════════════════════════════════

_TAG_RE = re.compile(r'<[^>]+>')


def strip_markup(text: str) -> str:
    """Грубое удаление тегов с декодированием HTML-сущностей."""
    text = re.sub(r'<(script|style)[^>]*>.*?</\1>', ' ', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<br\s*/?>|</p>|</div>|</h\d>|</li>|</tr>', '\n', text, flags=re.IGNORECASE)
    text = _TAG_RE.sub(' ', text)
    text = html_module.unescape(text)
    text = re.sub(r'[ \t\r\f\v]+', ' ', text)
    text = re.sub(r'\n\s*\n+', '\n\n', text)
    return text.strip()


@registry.register
class XMLExtractor(BaseExtractor):
    """Извлечение текста из XML (в порядке документа)"""

    extensions = ['.xml', '.xhtml']
    priority = 90

    MAX_PARSE_BYTES = 64 * 1024 * 1024

    @classmethod
    def is_available(cls) -> bool:
        return True

    @classmethod
    def extract(cls, path: Path) -> ExtractionResult:
        try:
            size = path.stat().st_size
            if size <= cls.MAX_PARSE_BYTES:
                import xml.etree.ElementTree as ET
                with open(path, 'rb') as f:
                    raw = f.read()
                try:
                    # Разбор из байтов: учитывается encoding из XML-декларации
                    root = ET.fromstring(raw)
                    collector = TextCollector()
                    for chunk in root.itertext():
                        chunk = chunk.strip()
                        if chunk and not collector.add(chunk):
                            break
                    return ExtractionResult(
                        text=collector.text(' '),
                        metadata={'root_tag': root.tag, 'truncated': collector.truncated}
                    )
                except ET.ParseError:
                    pass

            text, encoding, truncated = read_text_file(path)
            return ExtractionResult(text=strip_markup(text),
                                    metadata={'encoding': encoding, 'parsed': False, 'truncated': truncated})

        except Exception as e:
            return ExtractionResult(error=f"XML extraction failed: {e}")

    @classmethod
    def _extract_text_from_xml(cls, element, parts: list):
        """Текст элемента в порядке документа (совместимость)."""
        for chunk in element.itertext():
            if chunk and chunk.strip():
                parts.append(chunk.strip())


# ═══════════════════════════════════════════════════════════════════════════════
# HTML EXTRACTOR
# ═══════════════════════════════════════════════════════════════════════════════

def html_to_text(markup: str) -> Tuple[str, Optional[str]]:
    """HTML → текст. Returns (text, title)."""
    try:
        from bs4 import BeautifulSoup
        try:
            soup = BeautifulSoup(markup, 'lxml')
        except Exception:
            soup = BeautifulSoup(markup, 'html.parser')
        for tag in soup(['script', 'style', 'noscript', 'template', 'meta', 'link']):
            tag.decompose()
        title = soup.title.get_text(strip=True) if soup.title else None
        return soup.get_text(separator='\n', strip=True), (title or None)
    except ImportError:
        m = re.search(r'<title[^>]*>(.*?)</title>', markup, re.IGNORECASE | re.DOTALL)
        title = html_module.unescape(m.group(1)).strip() if m else None
        return strip_markup(markup), title


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
            text, encoding, truncated = read_text_file(path)
            extracted_text, title = html_to_text(text)
            return ExtractionResult(
                text=extracted_text,
                metadata={'encoding': encoding, 'title': title, 'truncated': truncated}
            )
        except Exception as e:
            return ExtractionResult(error=f"HTML extraction failed: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# CSV EXTRACTOR
# ═══════════════════════════════════════════════════════════════════════════════

@registry.register
class CSVExtractor(BaseExtractor):
    """Извлечение из CSV/TSV — потоковое чтение всех строк (до лимита объёма текста)"""

    extensions = ['.csv', '.tsv']
    priority = 80

    MAX_CELL = 10000

    @classmethod
    def is_available(cls) -> bool:
        return True

    @classmethod
    def _detect_encoding(cls, path: Path) -> str:
        """Определить кодировку по первым байтам"""
        with open(path, 'rb') as f:
            raw = f.read(256 * 1024)
        encoding, _ = detect_encoding(raw, final=len(raw) < 256 * 1024)
        return encoding

    @classmethod
    def _detect_delimiter(cls, sample: str, default: str) -> str:
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=',;\t|')
            return dialect.delimiter
        except csv.Error:
            # Sniffer не уверен: выбираем самый частый разделитель первой строки
            first = sample.split('\n', 1)[0]
            counts = {d: first.count(d) for d in (',', ';', '\t', '|')}
            best = max(counts, key=counts.get)
            return best if counts[best] > 0 else default

    @classmethod
    def extract(cls, path: Path) -> ExtractionResult:
        try:
            file_size = path.stat().st_size
            encoding = cls._detect_encoding(path)
            default = '\t' if path.suffix.lower() == '.tsv' else ','

            with open(path, 'r', encoding=encoding, errors='replace', newline='') as f:
                sample = f.read(64 * 1024)
                delimiter = cls._detect_delimiter(sample, default)
                f.seek(0)
                reader = csv.reader(f, delimiter=delimiter)

                collector = TextCollector()
                headers = []
                row_count = 0
                try:
                    for row in reader:
                        cells = [c[:cls.MAX_CELL] for c in row]
                        if row_count == 0:
                            headers = cells
                        row_count += 1
                        if not collector.add(' | '.join(cells)):
                            break
                except csv.Error as e:
                    # Битая строка: дочитываем остаток как обычный текст
                    rest = f.read(max(0, collector.limit - collector.size))
                    collector.add(rest)
                    csv_error = str(e)
                else:
                    csv_error = None

            metadata = {
                'encoding': encoding,
                'headers': headers[:50],
                'rows_read': row_count,
                'file_size': file_size,
                'delimiter': delimiter,
                'truncated': collector.truncated,
            }
            if csv_error:
                metadata['csv_error'] = csv_error
            return ExtractionResult(text=collector.text('\n'), metadata=metadata)

        except Exception as e:
            return ExtractionResult(error=f"CSV extraction failed: {e}")


__all__ = [
    'PlainTextExtractor', 'JSONExtractor', 'XMLExtractor',
    'HTMLExtractor', 'CSVExtractor', 'read_text_file', 'html_to_text', 'strip_markup',
]
