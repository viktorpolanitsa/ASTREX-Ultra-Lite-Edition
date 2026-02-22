#!/usr/bin/env python3
"""
ASTREX v3.0 — Archive Extractors
ZIP, RAR, 7z, TAR, GZ, BZ2, XZ
"""

import threading
import zipfile
import tarfile
import tempfile
import shutil
from pathlib import Path
from typing import Optional, List, Set

from .base import BaseExtractor, ExtractionResult, registry
from core.config import ENGINE_CONFIG


# ═══════════════════════════════════════════════════════════════════════════════
# NESTED ARCHIVE HELPER
# ═══════════════════════════════════════════════════════════════════════════════

# Thread-local счётчик глубины вложенности архивов — предотвращает
# бесконечную рекурсию при registry.extract() → ZIPExtractor → registry.extract()
_archive_depth = threading.local()


def _get_archive_depth() -> int:
    return getattr(_archive_depth, 'value', 0)


def _set_archive_depth(depth: int):
    _archive_depth.value = depth


def _extract_nested_archive(
    parent_archive,
    entry_name: str,
) -> Optional[ExtractionResult]:
    """Извлечь текст из вложенного архива с ограничением глубины."""
    current_depth = _get_archive_depth()
    if current_depth >= ENGINE_CONFIG.archive_max_depth:
        return ExtractionResult(
            text=None,
            metadata={'nested_archive': entry_name, 'skipped': 'max depth reached'}
        )

    try:
        # Check size before reading to avoid OOM
        try:
            info = parent_archive.getinfo(entry_name)
            if info.file_size > ENGINE_CONFIG.archive_max_size:
                return None
        except (KeyError, AttributeError):
            pass
        data = parent_archive.read(entry_name)
        if not data or len(data) > ENGINE_CONFIG.archive_max_size:
            return None

        # Сохраняем во временный файл и извлекаем через registry
        with tempfile.NamedTemporaryFile(
            suffix=Path(entry_name).suffix, delete=False
        ) as tmp:
            tmp.write(data)
            tmp_path = Path(tmp.name)

        try:
            # Увеличиваем глубину перед вызовом registry.extract(),
            # чтобы вложенные архивные экстракторы знали текущий уровень
            _set_archive_depth(current_depth + 1)
            result = registry.extract(tmp_path)
            return result
        finally:
            _set_archive_depth(current_depth)
            try:
                tmp_path.unlink()
            except Exception:
                pass

    except Exception:
        return None


def _extract_nested_rar(rar_archive, entry_name: str) -> Optional[ExtractionResult]:
    """Extract text from a nested file inside a RAR archive."""
    current_depth = _get_archive_depth()
    if current_depth >= ENGINE_CONFIG.archive_max_depth:
        return None
    try:
        data = rar_archive.read(entry_name)
        if not data or len(data) > ENGINE_CONFIG.archive_max_size:
            return None
        with tempfile.NamedTemporaryFile(suffix=Path(entry_name).suffix, delete=False) as tmp:
            tmp.write(data)
            tmp_path = Path(tmp.name)
        try:
            _set_archive_depth(current_depth + 1)
            return registry.extract(tmp_path)
        finally:
            _set_archive_depth(current_depth)
            try:
                tmp_path.unlink()
            except Exception:
                pass
    except Exception:
        return None


def _extract_nested_bytes(data: bytes, suffix: str) -> Optional[ExtractionResult]:
    """Extract text from raw bytes via a temp file and registry."""
    current_depth = _get_archive_depth()
    if current_depth >= ENGINE_CONFIG.archive_max_depth:
        return None
    if not data or len(data) > ENGINE_CONFIG.archive_max_size:
        return None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(data)
            tmp_path = Path(tmp.name)
        try:
            _set_archive_depth(current_depth + 1)
            return registry.extract(tmp_path)
        finally:
            _set_archive_depth(current_depth)
            try:
                tmp_path.unlink()
            except Exception:
                pass
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# ZIP EXTRACTOR
# ═══════════════════════════════════════════════════════════════════════════════

@registry.register
class ZIPExtractor(BaseExtractor):
    """Рекурсивное извлечение из ZIP архивов"""
    
    extensions = ['.zip']
    priority = 50
    
    @classmethod
    def is_available(cls) -> bool:
        return True  # zipfile is built-in
    
    @classmethod
    def extract(cls, path: Path) -> ExtractionResult:
        # Проверяем глубину вложенности — предотвращение бесконечной рекурсии
        if _get_archive_depth() >= ENGINE_CONFIG.archive_max_depth:
            return ExtractionResult(
                text=f"[ZIP: max nesting depth reached]",
                metadata={'skipped': 'max depth reached'}
            )
        try:
            text_parts = []
            attachments = []
            metadata = {'files': [], 'total_size': 0}

            with zipfile.ZipFile(path, 'r') as zf:
                for info in zf.infolist():
                    if info.is_dir():
                        continue

                    # Skip entries with path traversal
                    if '..' in info.filename or info.filename.startswith('/'):
                        continue

                    metadata['files'].append(info.filename)
                    metadata['total_size'] += info.file_size
                    
                    # Check size limit
                    if metadata['total_size'] > ENGINE_CONFIG.archive_max_size:
                        text_parts.append(f"[Архив слишком большой, извлечение прервано]")
                        break
                    
                    # Try to extract text from file
                    ext = Path(info.filename).suffix.lower()
                    
                    # Plain text files
                    if ext in {'.txt', '.log', '.md', '.csv', '.json', '.xml', '.html'}:
                        try:
                            if info.file_size > 50 * 1024 * 1024:  # skip files > 50MB
                                continue
                            content = zf.read(info.filename)
                            for encoding in ('utf-8', 'cp1251', 'latin-1'):
                                try:
                                    text = content.decode(encoding)
                                    text_parts.append(f"--- {info.filename} ---\n{text[:50000]}")
                                    break
                                except UnicodeDecodeError:
                                    continue
                        except Exception:
                            pass
                    
                    # Nested archives - extract recursively
                    elif ext in {'.zip', '.rar', '.7z', '.tar', '.gz'}:
                        nested_result = _extract_nested_archive(
                            zf, info.filename,
                        )
                        if nested_result and nested_result.text:
                            text_parts.append(
                                f"--- [архив] {info.filename} ---\n{nested_result.text}"
                            )
                        attachments.append(nested_result or ExtractionResult(
                            text=None,
                            metadata={'nested_archive': info.filename}
                        ))
            
            metadata['file_count'] = len(metadata['files'])
            
            text = '\n\n'.join(text_parts) if text_parts else f"[Архив: {len(metadata['files'])} файлов]"
            
            return ExtractionResult(
                text=text,
                metadata=metadata,
                attachments=attachments
            )
            
        except Exception as e:
            return ExtractionResult(error=f"ZIP extraction failed: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# TAR EXTRACTOR
# ═══════════════════════════════════════════════════════════════════════════════

@registry.register
class TARExtractor(BaseExtractor):
    """Извлечение из TAR архивов (включая .tar.gz, .tar.bz2, .tar.xz)"""
    
    extensions = ['.tar', '.tar.gz', '.tgz', '.tar.bz2', '.tbz2', '.tar.xz', '.txz']
    priority = 50
    
    @classmethod
    def is_available(cls) -> bool:
        return True  # tarfile is built-in
    
    @classmethod
    def can_handle(cls, path: Path) -> bool:
        name = path.name.lower()
        return (
            name.endswith('.tar') or
            name.endswith('.tar.gz') or
            name.endswith('.tgz') or
            name.endswith('.tar.bz2') or
            name.endswith('.tbz2') or
            name.endswith('.tar.xz') or
            name.endswith('.txz')
        )
    
    @classmethod
    def extract(cls, path: Path) -> ExtractionResult:
        try:
            text_parts = []
            metadata = {'files': [], 'total_size': 0}
            
            # Determine compression
            name = path.name.lower()
            if name.endswith('.gz') or name.endswith('.tgz'):
                mode = 'r:gz'
            elif name.endswith('.bz2') or name.endswith('.tbz2'):
                mode = 'r:bz2'
            elif name.endswith('.xz') or name.endswith('.txz'):
                mode = 'r:xz'
            else:
                mode = 'r'
            
            with tarfile.open(path, mode) as tf:
                for member in tf.getmembers():
                    if not member.isfile():
                        continue

                    # Skip entries with path traversal
                    if '..' in member.name or member.name.startswith('/'):
                        continue

                    metadata['files'].append(member.name)
                    metadata['total_size'] += member.size

                    if metadata['total_size'] > ENGINE_CONFIG.archive_max_size:
                        text_parts.append(f"[Архив слишком большой]")
                        break

                    ext = Path(member.name).suffix.lower()

                    # Text files — direct decode
                    if ext in {'.txt', '.log', '.md', '.csv', '.json', '.xml'}:
                        try:
                            f = tf.extractfile(member)
                            if f:
                                try:
                                    content = f.read()
                                finally:
                                    f.close()
                                for encoding in ('utf-8', 'cp1251', 'latin-1'):
                                    try:
                                        text = content.decode(encoding)
                                        text_parts.append(f"--- {member.name} ---\n{text[:50000]}")
                                        break
                                    except UnicodeDecodeError:
                                        continue
                        except Exception:
                            pass

                    # Documents and nested archives — extract via registry
                    elif ext in {'.pdf', '.docx', '.xlsx', '.pptx', '.doc', '.xls',
                                 '.odt', '.rtf', '.epub', '.html', '.htm',
                                 '.zip', '.rar', '.7z', '.tar', '.gz'}:
                        try:
                            f = tf.extractfile(member)
                            if f:
                                try:
                                    content = f.read()
                                finally:
                                    f.close()
                                nested = _extract_nested_bytes(content, ext)
                                if nested and nested.text:
                                    text_parts.append(f"--- {member.name} ---\n{nested.text[:50000]}")
                        except Exception:
                            pass
            
            metadata['file_count'] = len(metadata['files'])
            text = '\n\n'.join(text_parts) if text_parts else f"[Архив: {len(metadata['files'])} файлов]"
            
            return ExtractionResult(text=text, metadata=metadata)
            
        except Exception as e:
            return ExtractionResult(error=f"TAR extraction failed: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# GZIP EXTRACTOR (single file)
# ═══════════════════════════════════════════════════════════════════════════════

@registry.register
class GZIPExtractor(BaseExtractor):
    """Извлечение из GZIP (одиночный файл)"""
    
    extensions = ['.gz']
    priority = 55
    
    @classmethod
    def is_available(cls) -> bool:
        return True
    
    @classmethod
    def can_handle(cls, path: Path) -> bool:
        # Don't handle .tar.gz - let TARExtractor do it
        name = path.name.lower()
        return name.endswith('.gz') and not name.endswith('.tar.gz')
    
    @classmethod
    def extract(cls, path: Path) -> ExtractionResult:
        try:
            import gzip
            
            with gzip.open(path, 'rb') as f:
                content = f.read(ENGINE_CONFIG.max_file_size)
            
            # Try to decode as text
            for encoding in ('utf-8', 'cp1251', 'latin-1'):
                try:
                    text = content.decode(encoding)
                    return ExtractionResult(text=text)
                except UnicodeDecodeError:
                    continue
            
            return ExtractionResult(error="Cannot decode GZIP content as text")
            
        except Exception as e:
            return ExtractionResult(error=f"GZIP extraction failed: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# RAR EXTRACTOR
# ═══════════════════════════════════════════════════════════════════════════════

@registry.register
class RARExtractor(BaseExtractor):
    """Извлечение из RAR архивов"""
    
    extensions = ['.rar']
    priority = 50
    
    _available: Optional[bool] = None
    
    @classmethod
    def is_available(cls) -> bool:
        if cls._available is None:
            try:
                import rarfile
                cls._available = True
            except ImportError:
                cls._available = False
        return cls._available
    
    @classmethod
    def extract(cls, path: Path) -> ExtractionResult:
        try:
            import rarfile
            
            text_parts = []
            metadata = {'files': [], 'total_size': 0}
            
            with rarfile.RarFile(path, 'r') as rf:
                for info in rf.infolist():
                    if info.is_dir():
                        continue

                    metadata['files'].append(info.filename)
                    metadata['total_size'] += info.file_size

                    if metadata['total_size'] > ENGINE_CONFIG.archive_max_size:
                        text_parts.append(f"[Архив слишком большой]")
                        break

                    ext = Path(info.filename).suffix.lower()

                    # Text files — direct decode
                    if ext in {'.txt', '.log', '.md', '.csv', '.json', '.xml'}:
                        try:
                            content = rf.read(info.filename)
                            for encoding in ('utf-8', 'cp1251', 'latin-1'):
                                try:
                                    text = content.decode(encoding)
                                    text_parts.append(f"--- {info.filename} ---\n{text[:50000]}")
                                    break
                                except UnicodeDecodeError:
                                    continue
                        except Exception:
                            pass

                    # Documents and nested archives — extract via registry
                    elif ext in {'.pdf', '.docx', '.xlsx', '.pptx', '.doc', '.xls',
                                 '.odt', '.rtf', '.epub', '.html', '.htm',
                                 '.zip', '.rar', '.7z', '.tar', '.gz'}:
                        nested = _extract_nested_rar(rf, info.filename)
                        if nested and nested.text:
                            text_parts.append(f"--- {info.filename} ---\n{nested.text[:50000]}")
            
            metadata['file_count'] = len(metadata['files'])
            text = '\n\n'.join(text_parts) if text_parts else f"[RAR архив: {len(metadata['files'])} файлов]"
            
            return ExtractionResult(text=text, metadata=metadata)
            
        except Exception as e:
            return ExtractionResult(error=f"RAR extraction failed: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# 7Z EXTRACTOR
# ═══════════════════════════════════════════════════════════════════════════════

@registry.register
class SevenZipExtractor(BaseExtractor):
    """Извлечение из 7z архивов"""
    
    extensions = ['.7z']
    priority = 50
    
    _available: Optional[bool] = None
    
    @classmethod
    def is_available(cls) -> bool:
        if cls._available is None:
            try:
                import py7zr
                cls._available = True
            except ImportError:
                cls._available = False
        return cls._available
    
    @classmethod
    def extract(cls, path: Path) -> ExtractionResult:
        try:
            import py7zr
            
            text_parts = []
            metadata = {'files': [], 'total_size': 0}
            
            with py7zr.SevenZipFile(path, 'r') as zf:
                # Check total uncompressed size before extracting
                total_uncompressed = sum(
                    (e.uncompressed or 0) for e in zf.list() if hasattr(e, 'uncompressed')
                )
                if total_uncompressed > ENGINE_CONFIG.archive_max_size:
                    return ExtractionResult(
                        text="[7z archive too large to extract]",
                        metadata={'total_size': total_uncompressed, 'skipped': True}
                    )

                for name, bio in zf.readall().items():
                    metadata['files'].append(name)

                    content = bio.read() if hasattr(bio, 'read') else bio
                    if isinstance(content, bytes):
                        metadata['total_size'] += len(content)

                        if metadata['total_size'] > ENGINE_CONFIG.archive_max_size:
                            text_parts.append(f"[Архив слишком большой]")
                            break

                        ext = Path(name).suffix.lower()

                        # Text files — direct decode
                        if ext in {'.txt', '.log', '.md', '.csv', '.json', '.xml'}:
                            for encoding in ('utf-8', 'cp1251', 'latin-1'):
                                try:
                                    text = content.decode(encoding)
                                    text_parts.append(f"--- {name} ---\n{text[:50000]}")
                                    break
                                except UnicodeDecodeError:
                                    continue

                        # Documents and nested archives — extract via registry
                        elif ext in {'.pdf', '.docx', '.xlsx', '.pptx', '.doc', '.xls',
                                     '.odt', '.rtf', '.epub', '.html', '.htm',
                                     '.zip', '.rar', '.7z', '.tar', '.gz'}:
                            nested = _extract_nested_bytes(content, ext)
                            if nested and nested.text:
                                text_parts.append(f"--- {name} ---\n{nested.text[:50000]}")
            
            metadata['file_count'] = len(metadata['files'])
            text = '\n\n'.join(text_parts) if text_parts else f"[7z архив: {len(metadata['files'])} файлов]"
            
            return ExtractionResult(text=text, metadata=metadata)
            
        except Exception as e:
            return ExtractionResult(error=f"7z extraction failed: {e}")


__all__ = ['ZIPExtractor', 'TARExtractor', 'GZIPExtractor', 'RARExtractor', 'SevenZipExtractor']
