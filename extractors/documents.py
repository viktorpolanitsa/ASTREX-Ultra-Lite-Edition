#!/usr/bin/env python3
"""
ASTREX v3.0 — Document Extractors
PDF, Office (DOCX, XLSX, PPTX, ODT/ODS/ODP), legacy Office (DOC, XLS, PPT),
RTF, EPUB, FB2, MOBI
"""

import codecs
import posixpath
import re
import struct
import zipfile
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

from .base import BaseExtractor, ExtractionResult, TextCollector, registry
from core.config import ENGINE_CONFIG
from core.encoding import decode_bytes


def _local(tag) -> str:
    """Локальное имя XML-тега без пространства имён."""
    if not isinstance(tag, str):
        return ''
    return tag.rsplit('}', 1)[-1]


def _member_too_large(info: zipfile.ZipInfo) -> bool:
    return info.file_size > ENGINE_CONFIG.archive_member_max_size


def _open_member(zf: zipfile.ZipFile, name: str):
    """Открыть элемент ZIP-контейнера с проверкой размера (защита от zip-бомб)."""
    info = zf.getinfo(name)
    if _member_too_large(info):
        raise ValueError(f"{name}: member too large ({info.file_size} bytes)")
    return zf.open(info)


def _read_member(zf: zipfile.ZipFile, name: str) -> bytes:
    with _open_member(zf, name) as f:
        data = f.read(ENGINE_CONFIG.archive_member_max_size + 1)
    if len(data) > ENGINE_CONFIG.archive_member_max_size:
        raise ValueError(f"{name}: member too large")
    return data


def _numeric_key(name: str) -> Tuple[int, str]:
    m = re.search(r'(\d+)(?=\D*$)', name)
    return (int(m.group(1)) if m else 0, name)


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
                import pypdf  # noqa: F401
                cls._available = True
            except ImportError:
                cls._available = False
        return cls._available

    @classmethod
    def extract(cls, path: Path) -> ExtractionResult:
        try:
            import logging
            import warnings
            import pypdf

            collector = TextCollector()
            metadata: Dict[str, object] = {}

            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", module=r"pypdf")
                logging.getLogger("pypdf").setLevel(logging.ERROR)

                with open(path, 'rb') as f:
                    try:
                        reader = pypdf.PdfReader(f, strict=False)
                    except Exception as e:
                        return ExtractionResult(error=f"PDF corrupted or unreadable: {e}")

                    if reader.is_encrypted:
                        try:
                            if not reader.decrypt(''):
                                return ExtractionResult(error="PDF is encrypted (password required)",
                                                        metadata={'encrypted': True})
                        except Exception as e:
                            return ExtractionResult(error=f"PDF is encrypted and cannot be decrypted: {e}",
                                                    metadata={'encrypted': True})
                        metadata['encrypted'] = True

                    try:
                        info = reader.metadata
                        if info:
                            for key, name in (('/Title', 'title'), ('/Author', 'author'),
                                              ('/Subject', 'subject'), ('/Creator', 'creator'),
                                              ('/Producer', 'producer'), ('/Keywords', 'keywords')):
                                value = info.get(key)
                                if value:
                                    metadata[name] = str(value)
                    except Exception:
                        pass

                    try:
                        pages = reader.pages
                        metadata['pages'] = len(pages)
                    except Exception as e:
                        return ExtractionResult(error=f"PDF pages unreadable: {e}", metadata=metadata)

                    failed_pages = 0
                    for page in pages:
                        try:
                            page_text = page.extract_text()
                        except Exception:
                            failed_pages += 1
                            continue
                        if page_text and not collector.add(page_text):
                            break
                    if failed_pages:
                        metadata['failed_pages'] = failed_pages

            # Документ без текстового слоя (скан) — метаданные тоже ищем
            meta_text = ' '.join(str(metadata[k]) for k in ('title', 'author', 'subject', 'keywords')
                                 if metadata.get(k))
            if not collector.parts:
                metadata['no_text_layer'] = True
                return ExtractionResult(text=meta_text or '', metadata=metadata)

            metadata['truncated'] = collector.truncated
            text = collector.text('\n\n')
            if meta_text:
                text = meta_text + '\n\n' + text
            return ExtractionResult(text=text, metadata=metadata)

        except Exception as e:
            return ExtractionResult(error=f"PDF extraction failed: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# OFFICE OPEN XML (DOCX, XLSX, PPTX)
# ═══════════════════════════════════════════════════════════════════════════════

def _iter_paragraphs(stream, para_tag: str, text_tags=('t',), tab_tags=('tab',),
                     break_tags=('br', 'cr')) -> Iterator[str]:
    """Потоково перечислить абзацы OOXML-документа.

    Текст фрагментов (runs) внутри абзаца склеивается БЕЗ пробелов — Word часто
    разбивает одно слово на несколько runs, и прежняя замена тегов пробелами
    превращала "Иванов" в "Ива нов". XML-сущности (&amp;, &quot;) декодируются
    парсером.
    """
    import xml.etree.ElementTree as ET

    # Элементы, внутри которых теги tab/t не являются текстом документа
    # (позиции табуляции в свойствах абзаца, фонетические подсказки)
    skip_containers = ('tabs', 'tabLst', 'rPh', 'pPr')

    buf: List[str] = []
    depth = 0
    skip = 0
    for event, elem in ET.iterparse(stream, events=('start', 'end')):
        name = _local(elem.tag)
        if event == 'start':
            if name == para_tag:
                depth += 1
            elif name in skip_containers:
                skip += 1
            continue
        if name in skip_containers:
            skip = max(0, skip - 1)
            continue
        if skip:
            continue
        if name in text_tags:
            if elem.text:
                buf.append(elem.text)
        elif name in tab_tags:
            buf.append('\t')
        elif name in break_tags:
            buf.append('\n')
        elif name == para_tag:
            depth -= 1
            if depth <= 0:
                depth = 0
                text = ''.join(buf).strip()
                buf = []
                if text:
                    yield text
            else:
                buf.append('\n')
            elem.clear()
    tail = ''.join(buf).strip()
    if tail:
        yield tail


def _core_properties(zf: zipfile.ZipFile, name: str) -> Dict[str, str]:
    """Метаданные из docProps/core.xml или meta.xml (ODF)."""
    import xml.etree.ElementTree as ET
    metadata: Dict[str, str] = {}
    try:
        root = ET.fromstring(_read_member(zf, name))
    except (KeyError, ET.ParseError, ValueError):
        return metadata
    wanted = {'title': 'title', 'creator': 'creator', 'subject': 'subject', 'description': 'description',
              'lastModifiedBy': 'lastModifiedBy', 'created': 'created', 'modified': 'modified',
              'keywords': 'keywords', 'initial-creator': 'initial_creator',
              'creation-date': 'creation_date', 'date': 'date'}
    for elem in root.iter():
        key = wanted.get(_local(elem.tag))
        if key and elem.text and elem.text.strip():
            metadata.setdefault(key, elem.text.strip())
    return metadata


class OOXMLExtractorBase(BaseExtractor):
    """Базовый экстрактор для Office Open XML"""

    XML_TAG_PATTERN = re.compile(r'<[^>]+>')

    @classmethod
    def is_available(cls) -> bool:
        return True  # zipfile is built-in

    @classmethod
    def _strip_xml_tags(cls, content: str) -> str:
        """Совместимость: грубое удаление тегов."""
        import html
        text = cls.XML_TAG_PATTERN.sub(' ', content)
        return ' '.join(html.unescape(text).split())

    @classmethod
    def _read_xml_from_zip(cls, zf: zipfile.ZipFile, path: str) -> Optional[str]:
        """Совместимость: текст XML-элемента контейнера."""
        try:
            data = _read_member(zf, path)
        except (KeyError, ValueError):
            return None
        return cls._strip_xml_tags(data.decode('utf-8', errors='replace'))

    @classmethod
    def _paragraph_text(cls, zf: zipfile.ZipFile, name: str, para_tag: str,
                        collector: TextCollector, header: Optional[str] = None) -> None:
        try:
            with _open_member(zf, name) as stream:
                first = True
                for para in _iter_paragraphs(stream, para_tag):
                    if first and header:
                        collector.add(header)
                    first = False
                    if not collector.add(para):
                        return
        except KeyError:
            return
        except Exception as e:
            collector.add(f"[{name}: ошибка разбора: {e}]")


def _ooxml_error(path: Path, kind: str, e: Exception) -> ExtractionResult:
    if isinstance(e, zipfile.BadZipFile):
        # Зашифрованные документы Office хранятся в OLE-контейнере, а не в ZIP
        try:
            with open(path, 'rb') as f:
                if f.read(8) == b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1':
                    return ExtractionResult(error=f"{kind} is encrypted (password protected)",
                                            metadata={'encrypted': True})
        except OSError:
            pass
    return ExtractionResult(error=f"{kind} extraction failed: {e}")


@registry.register
class DOCXExtractor(OOXMLExtractorBase):
    """Извлечение текста из DOCX"""

    extensions = ['.docx', '.docm', '.dotx']
    priority = 20

    @classmethod
    def extract(cls, path: Path) -> ExtractionResult:
        try:
            collector = TextCollector()
            with zipfile.ZipFile(path, 'r') as zf:
                names = zf.namelist()
                cls._paragraph_text(zf, 'word/document.xml', 'p', collector)

                extra = [
                    ('word/header', 'Колонтитул'), ('word/footer', 'Колонтитул'),
                    ('word/footnotes', 'Сноски'), ('word/endnotes', 'Концевые сноски'),
                    ('word/comments', 'Комментарии'),
                ]
                for prefix, title in extra:
                    for name in sorted((n for n in names if n.startswith(prefix) and n.endswith('.xml')),
                                       key=_numeric_key):
                        cls._paragraph_text(zf, name, 'p', collector, header=f"[{title}]")

                metadata = _core_properties(zf, 'docProps/core.xml') if 'docProps/core.xml' in names else {}

            metadata['truncated'] = collector.truncated
            return ExtractionResult(text=collector.text('\n'), metadata=metadata)

        except Exception as e:
            return _ooxml_error(path, "DOCX", e)


@registry.register
class XLSXExtractor(OOXMLExtractorBase):
    """Извлечение текста из XLSX (значения ячеек по строкам, включая числа)"""

    extensions = ['.xlsx', '.xlsm']
    priority = 20

    @staticmethod
    def _shared_strings(zf: zipfile.ZipFile) -> List[str]:
        import xml.etree.ElementTree as ET
        strings: List[str] = []
        try:
            stream = _open_member(zf, 'xl/sharedStrings.xml')
        except KeyError:
            return strings
        with stream:
            buf: List[str] = []
            skip = 0
            for event, elem in ET.iterparse(stream, events=('start', 'end')):
                name = _local(elem.tag)
                if event == 'start':
                    if name in ('rPh', 'phoneticPr'):
                        skip += 1
                    continue
                if name in ('rPh', 'phoneticPr'):
                    skip -= 1
                elif name == 't' and not skip:
                    buf.append(elem.text or '')
                elif name == 'si':
                    strings.append(''.join(buf))
                    buf = []
                    elem.clear()
        return strings

    @staticmethod
    def _sheets(zf: zipfile.ZipFile, names: List[str]) -> List[Tuple[str, str]]:
        """(имя листа, путь к XML) в порядке книги."""
        import xml.etree.ElementTree as ET
        sheets: List[Tuple[str, str]] = []
        try:
            rels_root = ET.fromstring(_read_member(zf, 'xl/_rels/workbook.xml.rels'))
            targets = {}
            for rel in rels_root.iter():
                if _local(rel.tag) == 'Relationship':
                    target = rel.get('Target', '')
                    if target.startswith('/'):
                        target = target.lstrip('/')
                    else:
                        target = posixpath.normpath(posixpath.join('xl', target))
                    targets[rel.get('Id')] = target
            wb = ET.fromstring(_read_member(zf, 'xl/workbook.xml'))
            for elem in wb.iter():
                if _local(elem.tag) == 'sheet':
                    rid = next((v for k, v in elem.attrib.items() if _local(k) == 'id'), None)
                    target = targets.get(rid)
                    if target and target in names:
                        sheets.append((elem.get('name', target), target))
        except (KeyError, ET.ParseError, ValueError):
            pass
        if not sheets:
            for n in sorted((n for n in names if n.startswith('xl/worksheets/') and n.endswith('.xml')),
                            key=_numeric_key):
                sheets.append((Path(n).stem, n))
        return sheets

    @classmethod
    def extract(cls, path: Path) -> ExtractionResult:
        import xml.etree.ElementTree as ET
        try:
            collector = TextCollector()
            metadata: Dict[str, object] = {'sheets': []}

            with zipfile.ZipFile(path, 'r') as zf:
                names = zf.namelist()
                shared = cls._shared_strings(zf)

                for sheet_name, sheet_path in cls._sheets(zf, names):
                    metadata['sheets'].append(sheet_name)
                    if not collector.add(f"=== Лист: {sheet_name} ==="):
                        break
                    row: List[str] = []
                    cell_type = None
                    value: Optional[str] = None
                    inline: List[str] = []
                    with _open_member(zf, sheet_path) as stream:
                        for event, elem in ET.iterparse(stream, events=('start', 'end')):
                            name = _local(elem.tag)
                            if event == 'start':
                                if name == 'c':
                                    cell_type = elem.get('t')
                                    value = None
                                    inline = []
                                continue
                            if name == 'v':
                                value = elem.text
                            elif name == 't':
                                inline.append(elem.text or '')
                            elif name == 'c':
                                if cell_type == 's' and value is not None:
                                    try:
                                        cell = shared[int(value)]
                                    except (ValueError, IndexError):
                                        cell = ''
                                elif cell_type == 'inlineStr':
                                    cell = ''.join(inline)
                                elif cell_type == 'b':
                                    cell = 'TRUE' if value == '1' else 'FALSE'
                                else:
                                    cell = value or ''
                                    if cell.endswith('.0'):
                                        cell = cell[:-2]
                                if cell.strip():
                                    row.append(cell.strip())
                                elem.clear()
                            elif name == 'row':
                                if row and not collector.add('\t'.join(row)):
                                    break
                                row = []
                                elem.clear()

                for name in sorted(n for n in names if re.match(r'xl/comments\d*\.xml$', n)):
                    cls._paragraph_text(zf, name, 'comment', collector, header="[Комментарии]")

                if 'docProps/core.xml' in names:
                    metadata.update(_core_properties(zf, 'docProps/core.xml'))

            metadata['truncated'] = collector.truncated
            return ExtractionResult(text=collector.text('\n'), metadata=metadata)

        except Exception as e:
            return _ooxml_error(path, "XLSX", e)


@registry.register
class PPTXExtractor(OOXMLExtractorBase):
    """Извлечение текста из PPTX"""

    extensions = ['.pptx', '.ppsx', '.pptm']
    priority = 20

    @classmethod
    def extract(cls, path: Path) -> ExtractionResult:
        try:
            collector = TextCollector()
            metadata: Dict[str, object] = {}

            with zipfile.ZipFile(path, 'r') as zf:
                names = zf.namelist()
                slides = sorted((n for n in names if re.match(r'ppt/slides/slide\d+\.xml$', n)),
                                key=_numeric_key)
                metadata['slides'] = len(slides)
                for i, name in enumerate(slides, 1):
                    collector.add(f"--- Слайд {i} ---")
                    cls._paragraph_text(zf, name, 'p', collector)

                notes = sorted((n for n in names if re.match(r'ppt/notesSlides/notesSlide\d+\.xml$', n)),
                               key=_numeric_key)
                for name in notes:
                    cls._paragraph_text(zf, name, 'p', collector, header="[Заметки]")

                if 'docProps/core.xml' in names:
                    metadata.update(_core_properties(zf, 'docProps/core.xml'))

            metadata['truncated'] = collector.truncated
            return ExtractionResult(text=collector.text('\n'), metadata=metadata)

        except Exception as e:
            return _ooxml_error(path, "PPTX", e)


# ═══════════════════════════════════════════════════════════════════════════════
# OPENDOCUMENT (ODT, ODS, ODP)
# ═══════════════════════════════════════════════════════════════════════════════

_ODF_TEXT_NS = 'urn:oasis:names:tc:opendocument:xmlns:text:1.0'


def _odf_inline_text(elem) -> str:
    """Текст абзаца ODF с учётом <text:s>, <text:tab>, <text:line-break>."""
    parts = [elem.text or '']
    for child in elem:
        name = _local(child.tag)
        if name == 's':
            try:
                count = int(child.get(f'{{{_ODF_TEXT_NS}}}c', '1'))
            except ValueError:
                count = 1
            parts.append(' ' * max(1, min(count, 100)))
        elif name == 'tab':
            parts.append('\t')
        elif name == 'line-break':
            parts.append('\n')
        elif name in ('note-citation', 'bookmark', 'bookmark-start', 'bookmark-end'):
            pass
        else:
            parts.append(_odf_inline_text(child))
        parts.append(child.tail or '')
    return ''.join(parts)


def _odf_walk(elem, collector: TextCollector, depth: int = 0) -> bool:
    """Обход документа ODF: абзацы и строки таблиц в порядке документа."""
    if depth > 200:
        return True
    name = _local(elem.tag)
    if name in ('p', 'h'):
        text = _odf_inline_text(elem).strip()
        return collector.add(text) if text else True
    if name == 'table-row':
        cells = []
        for cell in elem:
            if _local(cell.tag) not in ('table-cell', 'covered-table-cell'):
                continue
            text = '\n'.join(_odf_inline_text(p).strip() for p in cell
                             if _local(p.tag) in ('p', 'h')).strip()
            if text:
                cells.append(text)
        return collector.add('\t'.join(cells)) if cells else True
    if name in ('binary-data', 'script', 'event-listeners'):
        return True
    for child in elem:
        if not _odf_walk(child, collector, depth + 1):
            return False
    return True


@registry.register
class ODTExtractor(OOXMLExtractorBase):
    """Извлечение текста из ODT/ODS/ODP"""

    extensions = ['.odt', '.ods', '.odp', '.odg', '.ott']
    priority = 25

    @classmethod
    def extract(cls, path: Path) -> ExtractionResult:
        import xml.etree.ElementTree as ET
        try:
            collector = TextCollector()
            metadata: Dict[str, object] = {}

            with zipfile.ZipFile(path, 'r') as zf:
                names = zf.namelist()
                if 'content.xml' not in names:
                    return ExtractionResult(error="ODF: content.xml not found")
                body = ET.fromstring(_read_member(zf, 'content.xml'))
                for elem in body.iter():
                    if _local(elem.tag) == 'body':
                        _odf_walk(elem, collector)
                        break

                # Колонтитулы хранятся в styles.xml
                if 'styles.xml' in names and not collector.full:
                    try:
                        styles = ET.fromstring(_read_member(zf, 'styles.xml'))
                        for elem in styles.iter():
                            if _local(elem.tag) in ('header', 'footer', 'header-left', 'footer-left',
                                                    'header-first', 'footer-first'):
                                _odf_walk(elem, collector)
                    except ET.ParseError:
                        pass

                if 'meta.xml' in names:
                    metadata.update(_core_properties(zf, 'meta.xml'))

            metadata['truncated'] = collector.truncated
            return ExtractionResult(text=collector.text('\n'), metadata=metadata)

        except Exception as e:
            return _ooxml_error(path, "ODF", e)


# ═══════════════════════════════════════════════════════════════════════════════
# LEGACY OFFICE (DOC, XLS, PPT)
# ═══════════════════════════════════════════════════════════════════════════════

def _u16(data: bytes, pos: int) -> int:
    return struct.unpack_from('<H', data, pos)[0]


def _u32(data: bytes, pos: int) -> int:
    return struct.unpack_from('<I', data, pos)[0]


def _clean_word_text(text: str) -> str:
    """Убрать служебные символы Word: коды полей, метки ячеек, объекты."""
    out: List[str] = []
    stack: List[str] = []
    for ch in text:
        if ch == '\x13':          # начало поля: инструкция до \x14
            stack.append('instr')
            continue
        if ch == '\x14':          # разделитель: дальше — результат поля
            if stack:
                stack[-1] = 'result'
            continue
        if ch == '\x15':          # конец поля
            if stack:
                stack.pop()
            continue
        if stack and stack[-1] == 'instr':
            continue
        out.append(ch)
    s = ''.join(out)
    s = (s.replace('\r', '\n').replace('\x07', '\t').replace('\x0b', '\n')
          .replace('\x0c', '\n').replace('\x1e', '-').replace('\x1f', '').replace('\xa0', ' '))
    s = re.sub(r'[\x00-\x08\x0e-\x1d]', '', s)
    s = re.sub(r'[ \t]+\n', '\n', s)
    return re.sub(r'\n{3,}', '\n\n', s).strip()


def _word97_text(ole) -> Optional[str]:
    """Текст документа Word 97-2003 по таблице фрагментов (piece table, CLX)."""
    word = ole.openstream('WordDocument').read()
    if len(word) < 0x200 or _u16(word, 0) != 0xA5EC:
        return None
    flags = _u16(word, 0x0A)
    if flags & 0x0100:
        raise PermissionError("document is encrypted")
    table_name = '1Table' if flags & 0x0200 else '0Table'
    if not ole.exists(table_name):
        return None

    pos = 32
    csw = _u16(word, pos)
    pos += 2 + csw * 2
    cslw = _u16(word, pos)
    pos += 2 + cslw * 4
    pos += 2  # cbRgFcLcb
    fc_clx = _u32(word, pos + 33 * 8)
    lcb_clx = _u32(word, pos + 33 * 8 + 4)
    table = ole.openstream(table_name).read()
    clx = table[fc_clx:fc_clx + lcb_clx]
    if not clx:
        return None

    i = 0
    while i < len(clx) and clx[i] == 0x01:          # Prc (свойства) — пропускаем
        i += 3 + _u16(clx, i + 1)
    if i >= len(clx) or clx[i] != 0x02:             # Pcdt
        return None
    lcb = _u32(clx, i + 1)
    plc = clx[i + 5:i + 5 + lcb]
    n = (len(plc) - 4) // 12
    if n <= 0:
        return None
    cps = [_u32(plc, 4 * k) for k in range(n + 1)]
    base = 4 * (n + 1)

    parts: List[str] = []
    for k in range(n):
        fc = _u32(plc, base + 8 * k + 2)
        compressed = bool(fc & 0x40000000)
        fc &= 0x3FFFFFFF
        cch = max(0, cps[k + 1] - cps[k])
        if compressed:
            start = fc // 2
            parts.append(word[start:start + cch].decode('cp1252', errors='replace'))
        else:
            parts.append(word[fc:fc + 2 * cch].decode('utf-16-le', errors='replace'))
    return _clean_word_text(''.join(parts))


_WORD_RUN_RE = re.compile(r'[^\x00-\x08\x0b\x0c\x0e-\x1f\ufffd]{4,}')


def _heuristic_strings(data: bytes) -> str:
    """Запасной вариант: читаемые фрагменты из двоичного потока."""
    candidates = []
    for enc in ('utf-16-le', 'cp1251'):
        text = data.decode(enc, errors='replace')
        runs = [r for r in _WORD_RUN_RE.findall(text)
                if sum(ch.isalpha() for ch in r) >= len(r) * 0.5]
        candidates.append('\n'.join(runs))
    return max(candidates, key=len)


def _xls_with_xlrd(path: Path, collector: TextCollector) -> Optional[List[str]]:
    try:
        import xlrd
    except ImportError:
        return None
    book = xlrd.open_workbook(str(path), on_demand=True)
    names = []
    try:
        for idx in range(book.nsheets):
            sheet = book.sheet_by_index(idx)
            names.append(sheet.name)
            collector.add(f"=== Лист: {sheet.name} ===")
            for r in range(sheet.nrows):
                cells = []
                for value in sheet.row_values(r):
                    if isinstance(value, float) and value.is_integer():
                        value = int(value)
                    value = str(value).strip()
                    if value:
                        cells.append(value)
                if cells and not collector.add('\t'.join(cells)):
                    return names
            book.unload_sheet(idx)
    finally:
        book.release_resources()
    return names


def _biff_rk(value: int) -> float:
    if value & 0x02:
        number = float(struct.unpack('<i', struct.pack('<I', value & 0xFFFFFFFC))[0] >> 2)
    else:
        number = struct.unpack('<d', struct.pack('<Q', (value & 0xFFFFFFFC) << 32))[0]
    return number / 100 if value & 0x01 else number


def _fmt_number(number: float) -> str:
    return str(int(number)) if float(number).is_integer() else repr(number)


def _xls_biff8(workbook: bytes, collector: TextCollector) -> List[str]:
    """Минимальный разбор BIFF8 (Excel 97-2003) без внешних библиотек."""
    records: List[Tuple[int, bytes]] = []
    pos = 0
    while pos + 4 <= len(workbook):
        rtype, rlen = struct.unpack_from('<HH', workbook, pos)
        records.append((rtype, workbook[pos + 4:pos + 4 + rlen]))
        pos += 4 + rlen

    # SST + CONTINUE
    sst: List[str] = []
    for idx, (rtype, data) in enumerate(records):
        if rtype != 0x00FC:
            continue
        blob = bytearray(data)
        boundaries = []
        j = idx + 1
        while j < len(records) and records[j][0] == 0x003C:
            boundaries.append(len(blob))
            blob.extend(records[j][1])
            j += 1
        unique = _u32(blob, 4)
        p = 8
        bset = sorted(boundaries)
        try:
            for _ in range(unique):
                cch = _u16(blob, p)
                grbit = blob[p + 2]
                p += 3
                high = grbit & 0x01
                rich = _u16(blob, p) if grbit & 0x08 else 0
                p += 2 if grbit & 0x08 else 0
                ext = _u32(blob, p) if grbit & 0x04 else 0
                p += 4 if grbit & 0x04 else 0
                chars: List[str] = []
                remaining = cch
                while remaining > 0:
                    nxt = next((b for b in bset if b > p), len(blob))
                    per = 2 if high else 1
                    take = min(remaining, (nxt - p) // per)
                    seg = bytes(blob[p:p + take * per])
                    chars.append(seg.decode('utf-16-le' if high else 'latin-1', errors='replace'))
                    p += take * per
                    remaining -= take
                    if remaining > 0:
                        if p in bset and p < len(blob):
                            high = blob[p] & 0x01
                            p += 1
                        else:
                            remaining = 0
                sst.append(''.join(chars))
                p += 4 * rich + ext
        except (struct.error, IndexError):
            pass
        break

    sheet_names: List[str] = []
    cells: Dict[int, Dict[int, Dict[int, str]]] = {}
    sheet_idx = -1
    for rtype, data in records:
        try:
            if rtype == 0x0085 and len(data) > 8:      # BOUNDSHEET
                cch = data[6]
                high = data[7] & 0x01
                raw = data[8:8 + cch * (2 if high else 1)]
                sheet_names.append(raw.decode('utf-16-le' if high else 'latin-1', errors='replace'))
            elif rtype == 0x0809 and len(data) >= 4:   # BOF
                if _u16(data, 2) == 0x0010:            # рабочий лист
                    sheet_idx += 1
            elif rtype == 0x00FD and len(data) >= 10:  # LABELSST
                row, col, _xf, isst = struct.unpack_from('<HHHI', data)
                if isst < len(sst):
                    cells.setdefault(sheet_idx, {}).setdefault(row, {})[col] = sst[isst]
            elif rtype == 0x0203 and len(data) >= 14:  # NUMBER
                row, col, _xf, num = struct.unpack_from('<HHHd', data)
                cells.setdefault(sheet_idx, {}).setdefault(row, {})[col] = _fmt_number(num)
            elif rtype == 0x027E and len(data) >= 10:  # RK
                row, col, _xf, rk = struct.unpack_from('<HHHI', data)
                cells.setdefault(sheet_idx, {}).setdefault(row, {})[col] = _fmt_number(_biff_rk(rk))
            elif rtype == 0x00BD and len(data) >= 6:   # MULRK
                row, first = struct.unpack_from('<HH', data)
                count = (len(data) - 6) // 6
                for k in range(count):
                    _xf, rk = struct.unpack_from('<HI', data, 4 + 6 * k)
                    cells.setdefault(sheet_idx, {}).setdefault(row, {})[first + k] = _fmt_number(_biff_rk(rk))
            elif rtype == 0x0204 and len(data) >= 9:   # LABEL
                row, col, _xf, cch = struct.unpack_from('<HHHH', data)
                high = data[8] & 0x01
                raw = data[9:9 + cch * (2 if high else 1)]
                cells.setdefault(sheet_idx, {}).setdefault(row, {})[col] = raw.decode(
                    'utf-16-le' if high else 'latin-1', errors='replace')
        except struct.error:
            continue

    for s_idx in sorted(cells):
        name = sheet_names[s_idx] if 0 <= s_idx < len(sheet_names) else f"Sheet{s_idx + 1}"
        if not collector.add(f"=== Лист: {name} ==="):
            break
        for row in sorted(cells[s_idx]):
            values = [v.strip() for _, v in sorted(cells[s_idx][row].items()) if v and v.strip()]
            if values and not collector.add('\t'.join(values)):
                return sheet_names
    return sheet_names


_PPT_PLACEHOLDERS = (
    'click to edit', 'образец заголовка', 'образец текста', 'второй уровень',
    'третий уровень', 'четвертый уровень', 'пятый уровень', 'second level',
    'third level', 'fourth level', 'fifth level',
)


def _ppt_text(data: bytes, collector: TextCollector) -> None:
    """Текст из записей TextCharsAtom/TextBytesAtom потока 'PowerPoint Document'."""
    stack = [(0, len(data), 0)]
    while stack:
        offset, end, depth = stack.pop()
        children = []
        while offset + 8 <= end:
            ver_inst, rtype, rlen = struct.unpack_from('<HHI', data, offset)
            body = offset + 8
            if rlen > end - body:
                break
            if (ver_inst & 0x0F) == 0x0F:
                if depth < 64:
                    children.append((body, body + rlen, depth + 1))
            elif rtype in (0x0FA0, 0x0FA8):
                raw = data[body:body + rlen]
                text = raw.decode('utf-16-le' if rtype == 0x0FA0 else 'cp1252', errors='replace')
                text = text.replace('\r', '\n').replace('\x0b', '\n').strip()
                if text and not text.lower().startswith(_PPT_PLACEHOLDERS):
                    if not collector.add(text):
                        return
            offset = body + rlen
        stack.extend(reversed(children))


def _ole_metadata(ole) -> Dict[str, str]:
    metadata: Dict[str, str] = {}
    try:
        meta = ole.get_metadata()
        for attr in ('title', 'subject', 'author', 'keywords', 'last_saved_by', 'company'):
            value = getattr(meta, attr, None)
            if isinstance(value, bytes):
                value = decode_bytes(value)[0]
            if value:
                metadata[attr] = str(value).strip('\x00 ')
        for attr in ('create_time', 'last_saved_time'):
            value = getattr(meta, attr, None)
            if value:
                metadata[attr] = str(value)
    except Exception:
        pass
    return metadata


@registry.register
class LegacyOfficeExtractor(BaseExtractor):
    """Извлечение текста из legacy Office (DOC, XLS, PPT)"""

    extensions = ['.doc', '.dot', '.xls', '.xlt', '.ppt', '.pps']
    priority = 30

    _available: Optional[bool] = None

    @classmethod
    def is_available(cls) -> bool:
        if cls._available is None:
            try:
                import olefile  # noqa: F401
                cls._available = True
            except ImportError:
                cls._available = False
        return cls._available

    @classmethod
    def extract(cls, path: Path) -> ExtractionResult:
        try:
            import olefile

            if not olefile.isOleFile(str(path)):
                return ExtractionResult(error="Not an OLE2 (Office 97-2003) file")

            collector = TextCollector()
            ext = path.suffix.lower()
            with olefile.OleFileIO(str(path)) as ole:
                metadata: Dict[str, object] = _ole_metadata(ole)

                if ole.exists('EncryptedPackage'):
                    return ExtractionResult(error="Office document is encrypted (password protected)",
                                            metadata={'encrypted': True})

                if ole.exists('WordDocument'):
                    metadata['format'] = 'doc'
                    try:
                        text = _word97_text(ole)
                    except PermissionError:
                        return ExtractionResult(error="DOC is encrypted (password protected)",
                                                metadata={'encrypted': True})
                    except (struct.error, IndexError, OSError):
                        text = None
                    if text is None:
                        text = _heuristic_strings(ole.openstream('WordDocument').read())
                        metadata['heuristic'] = True
                    collector.add(text)

                elif ole.exists('Workbook') or ole.exists('Book'):
                    metadata['format'] = 'xls'
                    sheets = None
                    try:
                        sheets = _xls_with_xlrd(path, collector)
                    except Exception as e:
                        if 'encrypt' in str(e).lower() or 'password' in str(e).lower():
                            return ExtractionResult(error="XLS is encrypted (password protected)",
                                                    metadata={'encrypted': True})
                        collector = TextCollector()
                        sheets = None
                    if sheets is None:
                        stream = 'Workbook' if ole.exists('Workbook') else 'Book'
                        sheets = _xls_biff8(ole.openstream(stream).read(), collector)
                    metadata['sheets'] = sheets

                elif ole.exists('PowerPoint Document'):
                    metadata['format'] = 'ppt'
                    _ppt_text(ole.openstream('PowerPoint Document').read(), collector)

                else:
                    return ExtractionResult(error=f"Unknown OLE document type ({ext})", metadata=metadata)

            meta_text = ' '.join(str(metadata[k]) for k in ('title', 'subject', 'author', 'keywords')
                                 if metadata.get(k))
            text = collector.text('\n')
            if meta_text:
                text = (meta_text + '\n\n' + text) if text else meta_text
            metadata['truncated'] = collector.truncated
            return ExtractionResult(text=text, metadata=metadata)

        except Exception as e:
            return ExtractionResult(error=f"Legacy Office extraction failed: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# RTF
# ═══════════════════════════════════════════════════════════════════════════════

_RTF_TOKEN = re.compile(
    r"\\([a-zA-Z]{1,32})(-?\d{1,10})? ?|\\'([0-9a-fA-F]{2})|\\([^a-zA-Z])|([{}])|[\r\n]+|(.)",
    re.DOTALL)

_RTF_DESTINATIONS = {
    'fonttbl', 'colortbl', 'stylesheet', 'info', 'pict', 'object', 'objdata', 'themedata',
    'colorschememapping', 'latentstyles', 'datastore', 'xmlnstbl', 'listtable',
    'listoverridetable', 'rsidtbl', 'generator', 'filetbl', 'revtbl', 'pgdsctbl', 'fldinst',
    'bkmkstart', 'bkmkend', 'nonshppict', 'shppict', 'blipuid', 'datafield', 'mmathPr',
    'wgrffmtfilter', 'xform', 'falt', 'panose', 'fontemb', 'fontfile', 'userprops',
    'docvar', 'template', 'operator', 'author', 'title', 'subject', 'company', 'manager',
    'keywords', 'comment', 'doccomm', 'creatim', 'revtim', 'printim', 'buptim',
}

_RTF_SPECIAL = {
    'par': '\n', 'line': '\n', 'sect': '\n\n', 'page': '\n\n', 'tab': '\t', 'cell': '\t',
    'row': '\n', 'emdash': '—', 'endash': '–', 'bullet': '•', 'lquote': '‘', 'rquote': '’',
    'ldblquote': '“', 'rdblquote': '”', 'emspace': ' ', 'enspace': ' ', 'qmspace': ' ',
}


def _rtf_codepage(rtf: str) -> str:
    m = re.search(r'\\ansicpg(\d+)', rtf[:8192])
    if m:
        enc = f'cp{m.group(1)}'
        try:
            codecs.lookup(enc)
            return enc
        except LookupError:
            pass
    if '\\fcharset204' in rtf[:65536]:
        return 'cp1251'
    return 'cp1252'


def rtf_to_text_basic(rtf: str) -> str:
    """Встроенный RTF → текст (кодовые страницы, \\uN, служебные группы)."""
    codepage = _rtf_codepage(rtf)
    stack: List[Tuple[int, bool]] = []
    ignorable = False
    ucskip = 1
    curskip = 0
    out: List[str] = []
    hex_buf = bytearray()

    def flush():
        if hex_buf:
            out.append(hex_buf.decode(codepage, errors='replace'))
            hex_buf.clear()

    for match in _RTF_TOKEN.finditer(rtf):
        word, arg, hexcode, char, brace, tchar = match.groups()
        if hexcode is None:
            flush()
        if brace:
            curskip = 0
            if brace == '{':
                stack.append((ucskip, ignorable))
            else:
                ucskip, ignorable = stack.pop() if stack else (1, False)
        elif char:
            curskip = 0
            if char == '*':
                ignorable = True
            elif ignorable:
                continue
            elif char in '{}\\':
                out.append(char)
            elif char == '~':
                out.append('\xa0')
            elif char == '_':
                out.append('-')
        elif word:
            curskip = 0
            if word in _RTF_DESTINATIONS:
                ignorable = True
            elif ignorable:
                continue
            elif word in _RTF_SPECIAL:
                out.append(_RTF_SPECIAL[word])
            elif word == 'uc':
                ucskip = int(arg or 1)
            elif word == 'u':
                code = int(arg or 0)
                if code < 0:
                    code += 0x10000
                out.append(chr(code))
                curskip = ucskip
        elif hexcode:
            if curskip > 0:
                curskip -= 1
            elif not ignorable:
                hex_buf.append(int(hexcode, 16))
        elif tchar:
            if curskip > 0:
                curskip -= 1
            elif not ignorable:
                out.append(tchar)
    flush()
    text = ''.join(out)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


@registry.register
class RTFExtractor(BaseExtractor):
    """Извлечение текста из RTF (с учётом кодовой страницы документа)"""

    extensions = ['.rtf']
    priority = 25

    @classmethod
    def is_available(cls) -> bool:
        return True  # striprtf или встроенный разборщик

    @classmethod
    def extract(cls, path: Path) -> ExtractionResult:
        try:
            raw = path.read_bytes()
            max_bytes = ENGINE_CONFIG.max_extracted_chars * 4
            raw = raw[:max_bytes]
            # RTF — 7-битный формат; "сырые" 8-битные байты (встречаются у
            # некоторых генераторов) декодируем кодовой страницей документа.
            codepage = _rtf_codepage(raw[:65536].decode('latin-1'))
            content = raw.decode(codepage, errors='replace')

            text = None
            try:
                from striprtf.striprtf import rtf_to_text
                text = rtf_to_text(content, encoding=codepage, errors='replace')
            except ImportError:
                pass
            except Exception:
                text = None
            if text is None:
                text = rtf_to_text_basic(content)

            return ExtractionResult(text=text, metadata={'codepage': codepage})

        except Exception as e:
            return ExtractionResult(error=f"RTF extraction failed: {e}")

    @classmethod
    def _strip_rtf_basic(cls, content: str) -> str:
        """Совместимость"""
        return rtf_to_text_basic(content)


# ═══════════════════════════════════════════════════════════════════════════════
# EPUB
# ═══════════════════════════════════════════════════════════════════════════════

@registry.register
class EPUBExtractor(BaseExtractor):
    """Извлечение текста из EPUB (главы в порядке spine)"""

    extensions = ['.epub']
    priority = 30

    @classmethod
    def is_available(cls) -> bool:
        return True  # zipfile + xml (built-in)

    @staticmethod
    def _spine(zf: zipfile.ZipFile, names: List[str]) -> Tuple[List[str], Dict[str, str]]:
        import xml.etree.ElementTree as ET
        metadata: Dict[str, str] = {}
        try:
            container = ET.fromstring(_read_member(zf, 'META-INF/container.xml'))
            opf_path = next(e.get('full-path') for e in container.iter() if _local(e.tag) == 'rootfile')
            opf = ET.fromstring(_read_member(zf, opf_path))
            base = posixpath.dirname(opf_path)
            manifest = {}
            for e in opf.iter():
                name = _local(e.tag)
                if name == 'item':
                    href = posixpath.normpath(posixpath.join(base, e.get('href', '')))
                    manifest[e.get('id')] = href
                elif name in ('title', 'creator', 'language', 'date', 'publisher') and e.text:
                    metadata.setdefault(name, e.text.strip())
            order = [manifest[e.get('idref')] for e in opf.iter()
                     if _local(e.tag) == 'itemref' and e.get('idref') in manifest]
            order = [o for o in order if o in names]
            if order:
                return order, metadata
        except (KeyError, StopIteration, ET.ParseError, ValueError):
            pass
        fallback = [n for n in names if n.lower().endswith(('.xhtml', '.html', '.htm'))
                    and 'META-INF' not in n]
        return fallback, metadata

    @classmethod
    def extract(cls, path: Path) -> ExtractionResult:
        from .text import html_to_text
        try:
            collector = TextCollector()
            with zipfile.ZipFile(path, 'r') as zf:
                names = zf.namelist()
                chapters, metadata = cls._spine(zf, names)
                if metadata.get('title'):
                    collector.add(metadata['title'])
                if metadata.get('creator'):
                    collector.add(metadata['creator'])
                count = 0
                for name in chapters:
                    try:
                        raw = _read_member(zf, name)
                    except (KeyError, ValueError):
                        continue
                    text, _title = html_to_text(decode_bytes(raw)[0])
                    if text.strip():
                        count += 1
                        if not collector.add(text):
                            break
            metadata['chapters'] = count
            metadata['truncated'] = collector.truncated
            return ExtractionResult(text=collector.text('\n\n'), metadata=metadata)
        except Exception as e:
            return ExtractionResult(error=f"EPUB extraction failed: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# FB2 (FictionBook)
# ═══════════════════════════════════════════════════════════════════════════════

@registry.register
class FB2Extractor(BaseExtractor):
    """Извлечение текста из FictionBook 2 (.fb2)"""

    extensions = ['.fb2']
    priority = 30

    _TEXT_TAGS = {'p', 'v', 'subtitle', 'text-author', 'td', 'th'}

    @classmethod
    def is_available(cls) -> bool:
        return True

    @classmethod
    def _parse(cls, raw: bytes):
        import xml.etree.ElementTree as ET
        try:
            return ET.fromstring(raw)
        except (ET.ParseError, ValueError, LookupError):
            # Неподдерживаемая expat кодировка или битая декларация
            text, _enc = decode_bytes(raw)
            text = re.sub(r'^\s*<\?xml[^>]*\?>', '', text)
            return ET.fromstring(text)

    @classmethod
    def extract(cls, path: Path) -> ExtractionResult:
        try:
            raw = path.read_bytes()
            root = cls._parse(raw)
            collector = TextCollector()
            metadata: Dict[str, object] = {}

            for elem in root.iter():
                name = _local(elem.tag)
                if name == 'book-title' and elem.text:
                    metadata.setdefault('title', elem.text.strip())
                elif name == 'author' and 'author' not in metadata:
                    parts = [(c.text or '').strip() for c in elem
                             if _local(c.tag) in ('first-name', 'middle-name', 'last-name')]
                    author = ' '.join(p for p in parts if p)
                    if author:
                        metadata['author'] = author
                elif name == 'lang' and elem.text:
                    metadata.setdefault('language', elem.text.strip())

            for key in ('title', 'author'):
                if metadata.get(key):
                    collector.add(str(metadata[key]))

            def walk(elem, depth=0) -> bool:
                name = _local(elem.tag)
                if name == 'binary' or depth > 200:   # base64-картинки
                    return True
                if name in cls._TEXT_TAGS:
                    text = ''.join(elem.itertext()).strip()
                    return collector.add(text) if text else True
                for child in elem:
                    if not walk(child, depth + 1):
                        return False
                return True

            for elem in root:
                if _local(elem.tag) == 'description':
                    for ann in elem.iter():
                        if _local(ann.tag) == 'annotation':
                            walk(ann)
                elif _local(elem.tag) == 'body':
                    if not walk(elem):
                        break

            metadata['truncated'] = collector.truncated
            return ExtractionResult(text=collector.text('\n'), metadata=metadata)
        except Exception as e:
            return ExtractionResult(error=f"FB2 extraction failed: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# MOBI / AZW (PalmDOC)
# ═══════════════════════════════════════════════════════════════════════════════

def _palmdoc_decompress(data: bytes) -> bytes:
    out = bytearray()
    i, n = 0, len(data)
    while i < n:
        c = data[i]
        i += 1
        if c == 0 or 0x09 <= c <= 0x7F:
            out.append(c)
        elif 0x01 <= c <= 0x08:
            out.extend(data[i:i + c])
            i += c
        elif 0x80 <= c <= 0xBF:
            if i >= n:
                break
            pair = (c << 8) | data[i]
            i += 1
            distance = (pair >> 3) & 0x07FF
            length = (pair & 0x07) + 3
            if 0 < distance <= len(out):
                for _ in range(length):
                    out.append(out[-distance])
        else:
            out.append(0x20)
            out.append(c ^ 0x80)
    return bytes(out)


def _trailing_entries_size(record: bytes, extra_flags: int) -> int:
    def entry_size(end: int) -> int:
        bitpos = result = 0
        while end > 0:
            v = record[end - 1]
            result |= (v & 0x7F) << bitpos
            bitpos += 7
            end -= 1
            if (v & 0x80) or bitpos >= 28:
                break
        return result

    size = 0
    flags = extra_flags >> 1
    while flags:
        if flags & 1:
            size += entry_size(len(record) - size)
        flags >>= 1
    if extra_flags & 1 and len(record) - size - 1 >= 0:
        size += (record[len(record) - size - 1] & 0x3) + 1
    return size


@registry.register
class MOBIExtractor(BaseExtractor):
    """Извлечение текста из MOBI / AZW (без DRM, сжатие PalmDOC или без сжатия)"""

    extensions = ['.mobi', '.azw', '.azw3', '.prc']
    priority = 30

    @classmethod
    def is_available(cls) -> bool:
        return True

    @classmethod
    def extract(cls, path: Path) -> ExtractionResult:
        from .text import html_to_text
        try:
            data = path.read_bytes()
            if len(data) < 86:
                return ExtractionResult(error="MOBI: file too small")
            count = struct.unpack_from('>H', data, 76)[0]
            offsets = [struct.unpack_from('>I', data, 78 + 8 * i)[0] for i in range(count)]
            offsets.append(len(data))

            def record(i: int) -> bytes:
                return data[offsets[i]:offsets[i + 1]]

            rec0 = record(0)
            compression, _unused, text_length, text_records, _rec_size, encryption = \
                struct.unpack_from('>HHIHHH', rec0, 0)
            if encryption:
                return ExtractionResult(error="MOBI is DRM-protected", metadata={'encrypted': True})

            encoding = 'cp1252'
            extra_flags = 0
            metadata: Dict[str, object] = {'name': data[:32].split(b'\x00')[0].decode('latin-1')}
            if rec0[16:20] == b'MOBI':
                header_len = struct.unpack_from('>I', rec0, 20)[0]
                enc_code = struct.unpack_from('>I', rec0, 28)[0]
                encoding = 'utf-8' if enc_code == 65001 else 'cp1252'
                if header_len >= 0xE4 and len(rec0) >= 16 + 0xF4:
                    extra_flags = struct.unpack_from('>H', rec0, 16 + 0xE2)[0]

            if compression == 17480:
                return ExtractionResult(error="MOBI: HUFF/CDIC compression is not supported")
            if compression not in (1, 2):
                return ExtractionResult(error=f"MOBI: unknown compression {compression}")

            budget = ENGINE_CONFIG.max_extracted_chars * 4
            out = bytearray()
            for i in range(1, min(text_records, count - 1) + 1):
                rec = record(i)
                trailing = _trailing_entries_size(rec, extra_flags) if extra_flags else 0
                if trailing:
                    rec = rec[:len(rec) - trailing]
                out.extend(_palmdoc_decompress(rec) if compression == 2 else rec)
                if len(out) > budget:
                    metadata['truncated'] = True
                    break

            markup = bytes(out[:text_length or len(out)]).decode(encoding, errors='replace')
            text, _title = html_to_text(markup)
            return ExtractionResult(text=text[:ENGINE_CONFIG.max_extracted_chars], metadata=metadata)
        except Exception as e:
            return ExtractionResult(error=f"MOBI extraction failed: {e}")


__all__ = [
    'PDFExtractor', 'DOCXExtractor', 'XLSXExtractor', 'PPTXExtractor',
    'ODTExtractor', 'LegacyOfficeExtractor', 'RTFExtractor', 'EPUBExtractor',
    'FB2Extractor', 'MOBIExtractor', 'rtf_to_text_basic',
]
