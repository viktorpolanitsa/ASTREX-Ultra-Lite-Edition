#!/usr/bin/env python3
"""
ASTREX v3.0 — Email Extractors
EML, MSG, PST/OST, MBOX — тело письма, заголовки и содержимое вложений.
"""

import email
import email.policy
import logging
import mimetypes
import os
import shutil
import subprocess
import tempfile
from email.message import Message
from pathlib import Path
from typing import Any, Dict, List, Optional

from .base import (BaseExtractor, ExtractionResult, TextCollector, registry,
                   extract_nested_bytes, worker_temp_dir)
from core.config import ENGINE_CONFIG
from core.encoding import decode_bytes

logger = logging.getLogger("astrex.email")

_HEADER_FIELDS = (('from', 'From'), ('to', 'To'), ('cc', 'Cc'), ('bcc', 'Bcc'),
                  ('subject', 'Subject'), ('date', 'Date'), ('message_id', 'Message-ID'),
                  ('reply_to', 'Reply-To'))


def _html_to_text(markup: str) -> str:
    from .text import html_to_text
    return html_to_text(markup)[0]


def _safe_header(msg: Message, name: str) -> str:
    """Значение заголовка (декодированное), без исключений на битых заголовках."""
    try:
        value = msg.get(name)
        if value is None:
            return ''
        return ' '.join(str(value).split())
    except Exception:
        try:
            raw = msg.get_all(name, failobj=[]) if hasattr(msg, 'get_all') else []
            return ' '.join(str(v) for v in raw)
        except Exception:
            return ''


def _decode_part(part: Message) -> str:
    """Текст части письма: заявленная кодировка → автоопределение (без исключений)."""
    payload = part.get_payload(decode=True)
    if not payload:
        return ''
    if not isinstance(payload, bytes):
        return str(payload)
    charset = None
    try:
        charset = part.get_content_charset()
    except Exception:
        pass
    text, _enc = decode_bytes(payload, declared=charset)
    return text


def _attachment_name(part: Message, index: int) -> str:
    name = None
    try:
        name = part.get_filename()
    except Exception:
        pass
    if name:
        return str(name)
    ext = mimetypes.guess_extension(part.get_content_type() or '') or '.bin'
    return f"attachment{index}{ext}"


def message_to_result(msg: Message) -> ExtractionResult:
    """Разобрать email.message.Message в ExtractionResult (тело + вложения)."""
    metadata: Dict[str, Any] = {}
    for key, header in _HEADER_FIELDS:
        value = _safe_header(msg, header)
        if value:
            metadata[key] = value

    header_lines = [
        f"От: {metadata.get('from', 'N/A')}",
        f"Кому: {metadata.get('to', 'N/A')}",
    ]
    if metadata.get('cc'):
        header_lines.append(f"Копия: {metadata['cc']}")
    header_lines.append(f"Тема: {metadata.get('subject', 'N/A')}")
    header_lines.append(f"Дата: {metadata.get('date', 'N/A')}")

    plain: List[str] = []
    html: List[str] = []
    attachments: List[ExtractionResult] = []
    att_index = 0

    for part in msg.walk():
        try:
            if part.is_multipart():
                continue
            ctype = (part.get_content_type() or 'application/octet-stream').lower()
            try:
                disposition = part.get_content_disposition()
            except Exception:
                disposition = None
            filename = None
            try:
                filename = part.get_filename()
            except Exception:
                pass

            is_attachment = disposition == 'attachment' or (
                filename and ctype not in ('text/plain', 'text/html'))

            if is_attachment:
                att_index += 1
                name = _attachment_name(part, att_index)
                att_meta = {'filename': name, 'content_type': ctype}
                payload = part.get_payload(decode=True) or b''
                att_meta['size'] = len(payload)
                if ctype.startswith('text/') and not os.path.splitext(name)[1]:
                    attachments.append(ExtractionResult(text=_decode_part(part), metadata=att_meta))
                else:
                    nested = extract_nested_bytes(payload, name)
                    nested.metadata.update(att_meta)
                    if nested.error:
                        nested.metadata['error'] = nested.error
                        nested.error = None
                        nested.text = None
                    attachments.append(nested)
                continue

            if ctype == 'text/plain':
                plain.append(_decode_part(part))
            elif ctype == 'text/html':
                html.append(_html_to_text(_decode_part(part)))
        except Exception as e:  # битая часть не должна терять всё письмо
            logger.debug(f"Email part skipped: {e}")
            continue

    body_parts = [p for p in plain if p.strip()] or [h for h in html if h.strip()]
    text = '\n'.join(header_lines)
    if body_parts:
        text += '\n\n' + '\n\n'.join(body_parts)

    if attachments:
        metadata['attachments'] = [a.metadata.get('filename') for a in attachments][:50]
    return ExtractionResult(text=text, metadata=metadata, attachments=attachments)


# ═══════════════════════════════════════════════════════════════════════════════
# EML EXTRACTOR
# ═══════════════════════════════════════════════════════════════════════════════

@registry.register
class EMLExtractor(BaseExtractor):
    """Извлечение текста из EML (стандартный email формат)"""

    extensions = ['.eml']
    priority = 15

    @classmethod
    def is_available(cls) -> bool:
        return True  # email is built-in

    @classmethod
    def extract(cls, path: Path) -> ExtractionResult:
        try:
            with open(path, 'rb') as f:
                msg = email.message_from_binary_file(f, policy=email.policy.default)
            return message_to_result(msg)
        except Exception as e:
            return ExtractionResult(error=f"EML extraction failed: {e}")

    @classmethod
    def _extract_message(cls, msg) -> ExtractionResult:
        """Совместимость со старым API"""
        return message_to_result(msg)

    @staticmethod
    def _html_to_text(html: str) -> str:
        return _html_to_text(html)


# ═══════════════════════════════════════════════════════════════════════════════
# MSG EXTRACTOR (Outlook)
# ═══════════════════════════════════════════════════════════════════════════════

def _msg_text_attr(msg, name: str) -> str:
    try:
        value = getattr(msg, name, None)
    except Exception:
        return ''
    if value is None:
        return ''
    if isinstance(value, bytes):
        return decode_bytes(value)[0]
    return str(value)


def _msg_object_to_result(msg, depth: int = 0) -> ExtractionResult:
    """Преобразовать объект extract_msg (письмо или вложенное письмо) в результат."""
    metadata: Dict[str, Any] = {}
    for key, attr in (('from', 'sender'), ('to', 'to'), ('cc', 'cc'), ('subject', 'subject'), ('date', 'date')):
        value = _msg_text_attr(msg, attr)
        if value:
            metadata[key] = value

    lines = [f"От: {metadata.get('from', 'N/A')}", f"Кому: {metadata.get('to', 'N/A')}"]
    if metadata.get('cc'):
        lines.append(f"Копия: {metadata['cc']}")
    lines += [f"Тема: {metadata.get('subject', 'N/A')}", f"Дата: {metadata.get('date', 'N/A')}"]

    body = _msg_text_attr(msg, 'body')
    if not body.strip():
        html_body = _msg_text_attr(msg, 'htmlBody')
        if html_body:
            body = _html_to_text(html_body)
    text = '\n'.join(lines) + ('\n\n' + body if body.strip() else '')

    attachments: List[ExtractionResult] = []
    try:
        msg_attachments = list(getattr(msg, 'attachments', []) or [])
    except Exception:
        msg_attachments = []

    for i, att in enumerate(msg_attachments, 1):
        try:
            name = (getattr(att, 'longFilename', None) or getattr(att, 'shortFilename', None)
                    or getattr(att, 'name', None) or f"attachment{i}")
            data = att.data
            if isinstance(data, (bytes, bytearray)):
                nested = extract_nested_bytes(bytes(data), str(name))
                nested.metadata.update({'filename': str(name), 'size': len(data)})
                if nested.error:
                    nested.metadata['error'] = nested.error
                    nested.error = None
                    nested.text = None
                attachments.append(nested)
            elif data is not None and hasattr(data, 'subject'):
                # Вложенное письмо (EmbeddedMsgAttachment.data → MSGFile)
                if depth < ENGINE_CONFIG.archive_max_depth:
                    inner = _msg_object_to_result(data, depth + 1)
                    inner.metadata['filename'] = str(name)
                    attachments.append(inner)
            else:
                attachments.append(ExtractionResult(metadata={'filename': str(name)}))
        except Exception as e:
            attachments.append(ExtractionResult(metadata={'filename': f"attachment{i}", 'error': str(e)}))

    if attachments:
        metadata['attachments'] = [a.metadata.get('filename') for a in attachments][:50]
    return ExtractionResult(text=text, metadata=metadata, attachments=attachments)


@registry.register
class MSGExtractor(BaseExtractor):
    """Извлечение текста из MSG (Outlook message)"""

    extensions = ['.msg']
    priority = 15

    _available: Optional[bool] = None

    @classmethod
    def is_available(cls) -> bool:
        if cls._available is None:
            try:
                import extract_msg  # noqa: F401
                cls._available = True
            except ImportError:
                cls._available = False
        return cls._available

    @classmethod
    def extract(cls, path: Path) -> ExtractionResult:
        try:
            import extract_msg

            opener = getattr(extract_msg, 'openMsg', None) or extract_msg.Message
            msg = opener(str(path))
            try:
                return _msg_object_to_result(msg)
            finally:
                try:
                    msg.close()
                except Exception:
                    pass
        except Exception as e:
            return ExtractionResult(error=f"MSG extraction failed: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# MBOX EXTRACTOR
# ═══════════════════════════════════════════════════════════════════════════════

def _mbox_factory(fp):
    try:
        return email.message_from_binary_file(fp, policy=email.policy.default)
    except Exception:
        return None


@registry.register
class MBOXExtractor(BaseExtractor):
    """Извлечение текста из MBOX (все письма, до лимита объёма текста)"""

    extensions = ['.mbox']
    priority = 15

    @classmethod
    def is_available(cls) -> bool:
        return True  # mailbox is built-in

    @classmethod
    def extract(cls, path: Path) -> ExtractionResult:
        try:
            import mailbox

            collector = TextCollector()
            metadata: Dict[str, Any] = {'message_count': 0, 'failed_messages': 0}

            mbox = mailbox.mbox(str(path), factory=_mbox_factory, create=False)
            try:
                for i, key in enumerate(mbox.iterkeys()):
                    try:
                        message = mbox[key]  # через factory: policy.default (RFC 2047)
                    except Exception:
                        message = None
                    if message is None:
                        metadata['failed_messages'] += 1
                        continue
                    try:
                        result = message_to_result(message)
                        text = result.full_text
                    except Exception:
                        metadata['failed_messages'] += 1
                        continue
                    metadata['message_count'] += 1
                    if not collector.add(f"--- Сообщение {i + 1} ---\n{text}"):
                        break
            finally:
                mbox.close()

            metadata['truncated'] = collector.truncated
            return ExtractionResult(text=collector.text('\n\n'), metadata=metadata)

        except Exception as e:
            return ExtractionResult(error=f"MBOX extraction failed: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# PST EXTRACTOR (Outlook Archive)
# ═══════════════════════════════════════════════════════════════════════════════

def _pff_value(obj, *names) -> Any:
    for name in names:
        try:
            value = getattr(obj, name)
            if callable(value):
                value = value()
            if value:
                return value
        except Exception:
            continue
    return None


def _pff_text(value) -> str:
    if value is None:
        return ''
    if isinstance(value, (bytes, bytearray)):
        return decode_bytes(bytes(value).rstrip(b'\x00'))[0]
    return str(value)


@registry.register
class PSTExtractor(BaseExtractor):
    """Извлечение текста из PST/OST: pypff (libpff-python) или утилита readpst (pst-utils)."""

    extensions = ['.pst', '.ost']
    priority = 15

    _pypff: Optional[bool] = None
    _readpst: Optional[str] = None

    @classmethod
    def _has_pypff(cls) -> bool:
        if cls._pypff is None:
            try:
                import pypff  # noqa: F401
                cls._pypff = True
            except ImportError:
                cls._pypff = False
        return cls._pypff

    @classmethod
    def is_available(cls) -> bool:
        if cls._readpst is None:
            cls._readpst = shutil.which('readpst') or ''
        return cls._has_pypff() or bool(cls._readpst)

    @classmethod
    def extract(cls, path: Path) -> ExtractionResult:
        if cls._has_pypff():
            return cls._extract_pypff(path)
        if cls.is_available():
            return cls._extract_readpst(path)
        return ExtractionResult(error="PST extraction requires libpff-python (pypff) or readpst (pst-utils)")

    # ── pypff ────────────────────────────────────────────────────────────────

    @classmethod
    def _extract_pypff(cls, path: Path) -> ExtractionResult:
        try:
            import pypff

            pst = pypff.file()
            pst.open(str(path))
            try:
                collector = TextCollector()
                metadata = {'folder_count': 0, 'message_count': 0}
                cls._extract_folder(pst.get_root_folder(), collector, metadata, depth=0)
            finally:
                try:
                    pst.close()
                except Exception:
                    pass
            metadata['truncated'] = collector.truncated
            return ExtractionResult(text=collector.text('\n\n'), metadata=metadata)
        except Exception as e:
            return ExtractionResult(error=f"PST extraction failed: {e}")

    @classmethod
    def _extract_folder(cls, folder, collector: TextCollector, metadata: Dict, depth: int = 0,
                        path: str = '') -> None:
        """Рекурсивное извлечение из папок PST"""
        if collector.full or depth > 50:
            return
        metadata['folder_count'] += 1
        folder_name = _pff_text(_pff_value(folder, 'name')) or "Unnamed"
        folder_path = f"{path}/{folder_name}" if path else folder_name

        count = _pff_value(folder, 'number_of_sub_messages', 'get_number_of_sub_messages') or 0
        for i in range(int(count)):
            if collector.full:
                return
            try:
                message = folder.get_sub_message(i)
                subject = _pff_text(_pff_value(message, 'subject'))
                sender = _pff_text(_pff_value(message, 'sender_name'))
                headers = _pff_text(_pff_value(message, 'transport_headers'))
                body = _pff_text(_pff_value(message, 'plain_text_body', 'get_plain_text_body'))
                if not body.strip():
                    html_body = _pff_text(_pff_value(message, 'html_body', 'get_html_body'))
                    body = _html_to_text(html_body) if html_body else ''
                lines = [f"--- Сообщение ({folder_path}) ---", f"От: {sender}", f"Тема: {subject}"]
                for h in ('To:', 'Cc:', 'Date:'):
                    for line in headers.splitlines():
                        if line.startswith(h):
                            lines.append(line.strip())
                            break
                lines.append(body)
                metadata['message_count'] += 1
                if not collector.add('\n'.join(lines)):
                    return
            except Exception:
                continue

        sub_count = _pff_value(folder, 'number_of_sub_folders', 'get_number_of_sub_folders') or 0
        for i in range(int(sub_count)):
            try:
                cls._extract_folder(folder.get_sub_folder(i), collector, metadata, depth + 1, folder_path)
            except Exception:
                continue

    # ── readpst ──────────────────────────────────────────────────────────────

    @classmethod
    def _extract_readpst(cls, path: Path) -> ExtractionResult:
        outdir = tempfile.mkdtemp(prefix="astrex-pst-", dir=str(worker_temp_dir()))
        try:
            timeout = ENGINE_CONFIG.file_timeout or None
            proc = subprocess.run(
                [cls._readpst, '-e', '-q', '-8', '-t', 'e', '-o', outdir, str(path)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout,
            )
            if proc.returncode != 0:
                err = proc.stderr.decode('utf-8', errors='replace').strip()
                return ExtractionResult(error=f"readpst failed: {err or proc.returncode}")

            collector = TextCollector()
            metadata = {'message_count': 0, 'backend': 'readpst'}
            for root, _dirs, files in os.walk(outdir):
                for name in sorted(files, key=lambda n: (len(n), n)):
                    if collector.full:
                        break
                    file_path = Path(root) / name
                    try:
                        with open(file_path, 'rb') as f:
                            msg = email.message_from_binary_file(f, policy=email.policy.default)
                        result = message_to_result(msg)
                    except Exception:
                        continue
                    folder = os.path.relpath(root, outdir)
                    metadata['message_count'] += 1
                    collector.add(f"--- Сообщение ({folder}) ---\n{result.full_text}")
            metadata['truncated'] = collector.truncated
            return ExtractionResult(text=collector.text('\n\n'), metadata=metadata)
        except subprocess.TimeoutExpired:
            return ExtractionResult(error="readpst timeout")
        except Exception as e:
            return ExtractionResult(error=f"PST extraction failed: {e}")
        finally:
            shutil.rmtree(outdir, ignore_errors=True)


__all__ = ['EMLExtractor', 'MSGExtractor', 'MBOXExtractor', 'PSTExtractor', 'message_to_result']
