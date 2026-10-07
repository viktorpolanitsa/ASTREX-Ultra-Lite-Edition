#!/usr/bin/env python3
"""
ASTREX v3.0 — Archive Extractors
ZIP, RAR, 7z, TAR (+ .tar.gz/.tgz/.tar.bz2/.tar.xz), одиночные GZ/BZ2/XZ/LZ4/ZST.

Каждый элемент архива обрабатывается тем же реестром экстракторов, что и
обычный файл (PDF, DOCX, письма, вложенные архивы...), с ограничениями
глубины вложенности и размера.
"""

import io
import logging
import os
import shutil
import tarfile
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Dict, List, Optional

from .base import (BaseExtractor, ExtractionResult, TextCollector, registry,
                   extract_nested_bytes, can_extract_name, nesting_depth, worker_temp_dir)
from core.config import ENGINE_CONFIG
from core.encoding import decode_bytes, is_probably_binary

logger = logging.getLogger("astrex.archives")

# Расширения, которые проще декодировать напрямую, без временного файла
_PLAIN_TEXT_EXTS = {'.txt', '.log', '.md', '.rst', '.ini', '.cfg', '.conf', '.yaml', '.yml'}
# Элементы без расширения читаются как текст, только если не больше этого размера
_EXTENSIONLESS_MAX_SIZE = 10 * 1024 * 1024


def _is_suspicious(name: str) -> bool:
    """Абсолютный путь или переход на уровень выше ('..' как компонент пути)."""
    norm = name.replace('\\', '/')
    if norm.startswith('/') or (len(norm) > 1 and norm[1] == ':'):
        return True
    return '..' in PurePosixPath(norm).parts


class _ArchiveWalker:
    """Общая логика обхода элементов архива с лимитами."""

    def __init__(self, kind: str):
        self.kind = kind
        self.collector = TextCollector()
        self.metadata: Dict[str, object] = {'files': [], 'total_size': 0, 'archive_type': kind}
        self.suspicious: List[str] = []
        self.errors: List[str] = []
        self.skipped: List[str] = []
        self.count = 0

    def budget_exhausted(self) -> bool:
        return self.collector.full

    def register(self, name: str, size: int) -> bool:
        """Учесть элемент. False — превышены общие лимиты архива."""
        self.count += 1
        files = self.metadata['files']
        if len(files) < 1000:
            files.append(name)
        self.metadata['total_size'] = int(self.metadata['total_size']) + max(0, size)
        if _is_suspicious(name):
            self.suspicious.append(name)
        if self.count > ENGINE_CONFIG.archive_max_members:
            self.skipped.append(f"{name}: too many members")
            return False
        if int(self.metadata['total_size']) > ENGINE_CONFIG.archive_max_size:
            self.skipped.append(f"{name}: archive size limit reached")
            return False
        return True

    def wants(self, name: str, size: int) -> bool:
        """Нужно ли читать содержимое элемента."""
        if size > ENGINE_CONFIG.archive_member_max_size:
            self.skipped.append(f"{name}: too large ({size} bytes)")
            return False
        ext = os.path.splitext(name)[1].lower()
        if not ext:
            # passwd, hosts, README, .bashrc… — читаем как текст, если небольшие
            return size <= _EXTENSIONLESS_MAX_SIZE
        return ext in _PLAIN_TEXT_EXTS or can_extract_name(name)

    def add_member(self, name: str, data: bytes) -> None:
        """Извлечь текст элемента и добавить его в результат."""
        ext = os.path.splitext(name)[1].lower()
        label = f"--- {name} ---" if not _is_suspicious(name) else f"--- {name} [подозрительный путь] ---"
        if ext in _PLAIN_TEXT_EXTS or not ext:
            if is_probably_binary(data[:8192]):
                return
            text, _enc = decode_bytes(data)
        else:
            result = extract_nested_bytes(data, name)
            if result.error:
                if result.error not in ('max nesting depth reached',):
                    self.errors.append(f"{name}: {result.error}")
                return
            text = result.full_text
        if text and text.strip():
            self.collector.add(label + "\n" + text)

    def result(self, empty_note: str) -> ExtractionResult:
        self.metadata['file_count'] = self.count
        self.metadata['truncated'] = self.collector.truncated
        if self.suspicious:
            self.metadata['suspicious_paths'] = self.suspicious[:100]
        if self.errors:
            self.metadata['member_errors'] = self.errors[:50]
        if self.skipped:
            self.metadata['skipped'] = self.skipped[:50]
        # Имена файлов архива тоже доступны для поиска
        names = '\n'.join(f"{n}  [подозрительный путь]" if _is_suspicious(str(n)) else str(n)
                          for n in self.metadata['files'])
        text = self.collector.text('\n\n')
        if names:
            text = f"[{empty_note}: {self.count} файлов]\n{names}" + (f"\n\n{text}" if text else '')
        return ExtractionResult(text=text, metadata=self.metadata)


def _depth_exceeded(kind: str) -> Optional[ExtractionResult]:
    if nesting_depth() >= ENGINE_CONFIG.archive_max_depth:
        return ExtractionResult(text='', metadata={'archive_type': kind, 'skipped': 'max depth reached'})
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# ZIP EXTRACTOR
# ═══════════════════════════════════════════════════════════════════════════════

@registry.register
class ZIPExtractor(BaseExtractor):
    """Рекурсивное извлечение из ZIP архивов (включая документы внутри)"""

    extensions = ['.zip', '.jar', '.apk', '.cbz']
    priority = 50

    @classmethod
    def is_available(cls) -> bool:
        return True  # zipfile is built-in

    @staticmethod
    def _member_name(info: zipfile.ZipInfo) -> str:
        """Имя элемента: без флага UTF-8 русские архивы Windows используют cp866."""
        name = info.filename
        if not info.flag_bits & 0x800 and any(ord(ch) > 127 for ch in name):
            try:
                candidate = name.encode('cp437').decode('cp866')
            except (UnicodeEncodeError, UnicodeDecodeError):
                return name
            cyrillic = sum(1 for ch in candidate if '\u0400' <= ch <= '\u04ff')
            pseudo = sum(1 for ch in candidate if '\u2500' <= ch <= '\u25ff')
            if cyrillic and not pseudo:
                return candidate
        return name

    @classmethod
    def extract(cls, path: Path) -> ExtractionResult:
        skipped = _depth_exceeded('zip')
        if skipped:
            return skipped
        try:
            walker = _ArchiveWalker('zip')
            encrypted = 0
            with zipfile.ZipFile(path, 'r') as zf:
                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    name = cls._member_name(info)
                    if not walker.register(name, info.file_size):
                        break
                    if walker.budget_exhausted() or not walker.wants(name, info.file_size):
                        continue
                    if info.flag_bits & 0x1:
                        encrypted += 1
                        walker.skipped.append(f"{name}: encrypted")
                        continue
                    try:
                        with zf.open(info) as f:
                            data = f.read(ENGINE_CONFIG.archive_member_max_size + 1)
                        if len(data) > ENGINE_CONFIG.archive_member_max_size:
                            walker.skipped.append(f"{name}: too large")
                            continue
                    except (RuntimeError, zipfile.BadZipFile, OSError, NotImplementedError) as e:
                        walker.errors.append(f"{name}: {e}")
                        continue
                    walker.add_member(name, data)
            if encrypted:
                walker.metadata['encrypted_members'] = encrypted
            return walker.result("ZIP архив")

        except Exception as e:
            return ExtractionResult(error=f"ZIP extraction failed: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# TAR EXTRACTOR
# ═══════════════════════════════════════════════════════════════════════════════

_TAR_SUFFIXES = ('.tar', '.tar.gz', '.tgz', '.tar.bz2', '.tbz2', '.tbz', '.tar.xz', '.txz',
                 '.tar.zst', '.tar.lz4')


@registry.register
class TARExtractor(BaseExtractor):
    """Извлечение из TAR архивов (сжатие определяется по содержимому)"""

    extensions = list(_TAR_SUFFIXES)
    priority = 50

    @classmethod
    def is_available(cls) -> bool:
        return True  # tarfile is built-in

    @classmethod
    def can_handle(cls, path: Path) -> bool:
        return path.name.lower().endswith(_TAR_SUFFIXES)

    @classmethod
    def extract(cls, path: Path) -> ExtractionResult:
        skipped = _depth_exceeded('tar')
        if skipped:
            return skipped
        name = path.name.lower()
        if name.endswith(('.tar.zst', '.tar.lz4')):
            return CompressedFileExtractor.extract(path)
        try:
            with open(path, 'rb') as f:
                return cls.extract_stream(f)
        except Exception as e:
            return ExtractionResult(error=f"TAR extraction failed: {e}")

    @classmethod
    def extract_stream(cls, fileobj) -> ExtractionResult:
        """Обход TAR-потока (потоковый режим: каждый элемент читается один раз)."""
        walker = _ArchiveWalker('tar')
        with tarfile.open(fileobj=fileobj, mode='r|*') as tf:
            for member in tf:
                if not member.isfile():
                    continue
                if not walker.register(member.name, member.size):
                    break
                if walker.budget_exhausted() or not walker.wants(member.name, member.size):
                    continue
                f = tf.extractfile(member)
                if f is None:
                    continue
                try:
                    data = f.read(ENGINE_CONFIG.archive_member_max_size + 1)
                finally:
                    f.close()
                if len(data) > ENGINE_CONFIG.archive_member_max_size:
                    walker.skipped.append(f"{member.name}: too large")
                    continue
                walker.add_member(member.name, data)
        return walker.result("TAR архив")


# ═══════════════════════════════════════════════════════════════════════════════
# SINGLE-FILE COMPRESSION (GZ, BZ2, XZ, LZ4, ZST)
# ═══════════════════════════════════════════════════════════════════════════════

def _open_decompressor(path: Path):
    """Открыть поток распаковки по сигнатуре файла."""
    with open(path, 'rb') as f:
        magic = f.read(6)
    if magic.startswith(b'\x1f\x8b'):
        import gzip
        return gzip.open(path, 'rb'), 'gzip'
    if magic.startswith(b'BZh'):
        import bz2
        return bz2.open(path, 'rb'), 'bzip2'
    if magic.startswith(b'\xfd7zXZ\x00'):
        import lzma
        return lzma.open(path, 'rb'), 'xz'
    if magic.startswith(b'\x28\xb5\x2f\xfd'):
        try:
            import zstandard
        except ImportError:
            raise RuntimeError("zstd support requires 'zstandard' package (pip install zstandard)")
        fh = open(path, 'rb')
        return zstandard.ZstdDecompressor().stream_reader(fh, closefd=True), 'zstd'
    if magic.startswith(b'\x04\x22\x4d\x18'):
        try:
            import lz4.frame
        except ImportError:
            raise RuntimeError("lz4 support requires 'lz4' package (pip install lz4)")
        return lz4.frame.open(path, 'rb'), 'lz4'
    raise RuntimeError("unknown compression format")


@registry.register
class CompressedFileExtractor(BaseExtractor):
    """Одиночный сжатый файл: распаковка и извлечение содержимого по внутреннему имени."""

    extensions = ['.gz', '.bz2', '.xz', '.lz4', '.zst']
    priority = 55

    @classmethod
    def is_available(cls) -> bool:
        return True

    @classmethod
    def can_handle(cls, path: Path) -> bool:
        name = path.name.lower()
        return name.endswith(tuple(cls.extensions)) and not name.endswith(
            ('.tar.gz', '.tar.bz2', '.tar.xz'))

    @classmethod
    def extract(cls, path: Path) -> ExtractionResult:
        skipped = _depth_exceeded('compressed')
        if skipped:
            return skipped
        try:
            stream, kind = _open_decompressor(path)
            limit = ENGINE_CONFIG.archive_max_size
            with stream:
                data = stream.read(limit + 1)
            truncated = len(data) > limit
            data = data[:limit]

            inner_name = path.name
            for suffix in cls.extensions:
                if inner_name.lower().endswith(suffix):
                    inner_name = inner_name[:-len(suffix)]
                    break
            metadata = {'compression': kind, 'inner_name': inner_name,
                        'uncompressed_size': len(data), 'truncated': truncated}

            # Внутри tar-поток (например, .tar.zst или .tgz без суффикса)
            if len(data) > 262 and data[257:262] == b'ustar':
                result = TARExtractor.extract_stream(io.BytesIO(data))
                result.metadata.update(metadata)
                return result

            if inner_name and can_extract_name(inner_name) and \
                    os.path.splitext(inner_name)[1].lower() not in ('', '.txt', '.log'):
                result = extract_nested_bytes(data, inner_name)
                result.metadata.update(metadata)
                return result

            if is_probably_binary(data[:8192]):
                return ExtractionResult(error=f"{kind}: binary content without known format",
                                        metadata=metadata)
            text, encoding = decode_bytes(data, final=not truncated)
            metadata['encoding'] = encoding
            return ExtractionResult(text=text[:ENGINE_CONFIG.max_extracted_chars], metadata=metadata)

        except Exception as e:
            return ExtractionResult(error=f"Decompression failed: {e}")


# Совместимость со старым именем
GZIPExtractor = CompressedFileExtractor


# ═══════════════════════════════════════════════════════════════════════════════
# RAR EXTRACTOR
# ═══════════════════════════════════════════════════════════════════════════════

@registry.register
class RARExtractor(BaseExtractor):
    """Извлечение из RAR архивов (нужна утилита unrar, unar, 7z или bsdtar)"""

    extensions = ['.rar', '.cbr']
    priority = 50

    _available: Optional[bool] = None
    _tool_ok: Optional[bool] = None

    @classmethod
    def is_available(cls) -> bool:
        if cls._available is None:
            try:
                import rarfile  # noqa: F401
                cls._available = True
            except ImportError:
                cls._available = False
        return cls._available

    @classmethod
    def _backend_available(cls) -> bool:
        if cls._tool_ok is None:
            try:
                import rarfile
                rarfile.tool_setup()
                cls._tool_ok = True
            except Exception:
                cls._tool_ok = False
                logger.warning("RAR backend not found (install unrar, unar, 7z or bsdtar): "
                               "RAR archives will be listed but not extracted")
        return cls._tool_ok

    @classmethod
    def extract(cls, path: Path) -> ExtractionResult:
        skipped = _depth_exceeded('rar')
        if skipped:
            return skipped
        try:
            import rarfile

            walker = _ArchiveWalker('rar')
            backend = cls._backend_available()
            with rarfile.RarFile(str(path), 'r') as rf:
                if rf.needs_password():
                    walker.metadata['encrypted'] = True
                for info in rf.infolist():
                    if info.is_dir():
                        continue
                    if not walker.register(info.filename, info.file_size):
                        break
                    if not backend or walker.metadata.get('encrypted'):
                        continue
                    if walker.budget_exhausted() or not walker.wants(info.filename, info.file_size):
                        continue
                    try:
                        data = rf.read(info)
                    except Exception as e:
                        walker.errors.append(f"{info.filename}: {e}")
                        continue
                    walker.add_member(info.filename, data)
            if not backend:
                walker.metadata['warning'] = "RAR backend (unrar/unar/7z/bsdtar) not installed — content not extracted"
            return walker.result("RAR архив")

        except Exception as e:
            return ExtractionResult(error=f"RAR extraction failed: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# 7Z EXTRACTOR
# ═══════════════════════════════════════════════════════════════════════════════

@registry.register
class SevenZipExtractor(BaseExtractor):
    """Извлечение из 7z архивов (py7zr; совместимо с py7zr 0.x и 1.x)"""

    extensions = ['.7z']
    priority = 50

    _available: Optional[bool] = None

    @classmethod
    def is_available(cls) -> bool:
        if cls._available is None:
            try:
                import py7zr  # noqa: F401
                cls._available = True
            except ImportError:
                cls._available = False
        return cls._available

    @classmethod
    def extract(cls, path: Path) -> ExtractionResult:
        skipped = _depth_exceeded('7z')
        if skipped:
            return skipped
        tmpdir = None
        try:
            import py7zr

            walker = _ArchiveWalker('7z')
            with py7zr.SevenZipFile(str(path), 'r') as zf:
                if zf.needs_password():
                    walker.metadata['encrypted'] = True
                    for entry in zf.list():
                        if not entry.is_directory:
                            walker.register(entry.filename, entry.uncompressed or 0)
                    return walker.result("7z архив (зашифрован)")

                wanted: List[str] = []
                for entry in zf.list():
                    if entry.is_directory:
                        continue
                    size = entry.uncompressed or 0
                    if not walker.register(entry.filename, size):
                        break
                    if not _is_suspicious(entry.filename) and walker.wants(entry.filename, size):
                        wanted.append(entry.filename)

            if wanted:
                # Распаковка во временный каталог (readall() удалён в py7zr 1.0)
                tmpdir = tempfile.mkdtemp(prefix="astrex-7z-", dir=str(worker_temp_dir()))
                with py7zr.SevenZipFile(str(path), 'r') as zf:
                    zf.extract(path=tmpdir, targets=wanted)
                for name in wanted:
                    if walker.budget_exhausted():
                        break
                    member = Path(tmpdir) / name
                    try:
                        member.resolve().relative_to(Path(tmpdir).resolve())
                    except ValueError:
                        continue
                    if member.is_file():
                        walker.add_member(name, member.read_bytes())
            return walker.result("7z архив")

        except Exception as e:
            message = str(e)
            if 'password' in message.lower():
                return ExtractionResult(error="7z archive is encrypted (password required)",
                                        metadata={'encrypted': True})
            return ExtractionResult(error=f"7z extraction failed: {e}")
        finally:
            if tmpdir:
                shutil.rmtree(tmpdir, ignore_errors=True)


__all__ = ['ZIPExtractor', 'TARExtractor', 'CompressedFileExtractor', 'GZIPExtractor',
           'RARExtractor', 'SevenZipExtractor']
