#!/usr/bin/env python3
"""
ASTREX v3.0 — Email Extractors
EML, MSG, PST, MBOX
"""

import email
import email.policy
import re
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime

from .base import BaseExtractor, ExtractionResult, registry


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
            
            return cls._extract_message(msg)
            
        except Exception as e:
            return ExtractionResult(error=f"EML extraction failed: {e}")
    
    @classmethod
    def _extract_message(cls, msg: email.message.EmailMessage) -> ExtractionResult:
        """Извлечение из email.message.Message"""
        text_parts = []
        attachments = []
        
        # Headers
        metadata = {
            'from': msg.get('From', ''),
            'to': msg.get('To', ''),
            'cc': msg.get('Cc', ''),
            'subject': msg.get('Subject', ''),
            'date': msg.get('Date', ''),
            'message_id': msg.get('Message-ID', ''),
        }
        
        # Clean metadata
        metadata = {k: str(v).strip() for k, v in metadata.items() if v}
        
        # Header summary
        header_text = f"""
От: {metadata.get('from', 'N/A')}
Кому: {metadata.get('to', 'N/A')}
Копия: {metadata.get('cc', '')}
Тема: {metadata.get('subject', 'N/A')}
Дата: {metadata.get('date', 'N/A')}
""".strip()
        text_parts.append(header_text)
        
        # Body
        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                content_disposition = str(part.get('Content-Disposition', ''))
                
                # Skip attachments for main text
                if 'attachment' in content_disposition:
                    # Extract attachment info
                    filename = part.get_filename() or 'attachment'
                    att_result = ExtractionResult(
                        text=None,
                        metadata={'filename': filename, 'content_type': content_type}
                    )
                    
                    # Try to extract text from attachment
                    if content_type == 'text/plain':
                        payload = part.get_payload(decode=True)
                        if payload:
                            charset = part.get_content_charset() or 'utf-8'
                            att_result.text = payload.decode(charset, errors='ignore')
                    
                    attachments.append(att_result)
                    continue
                
                # Get text content
                if content_type == 'text/plain':
                    payload = part.get_payload(decode=True)
                    if payload:
                        charset = part.get_content_charset() or 'utf-8'
                        text_parts.append(payload.decode(charset, errors='ignore'))
                
                elif content_type == 'text/html':
                    payload = part.get_payload(decode=True)
                    if payload:
                        charset = part.get_content_charset() or 'utf-8'
                        html = payload.decode(charset, errors='ignore')
                        # Basic HTML to text
                        text = cls._html_to_text(html)
                        text_parts.append(text)
        else:
            # Single part message
            payload = msg.get_payload(decode=True)
            if payload:
                charset = msg.get_content_charset() or 'utf-8'
                text_parts.append(payload.decode(charset, errors='ignore'))
        
        text = '\n\n'.join(text_parts) if text_parts else None
        
        return ExtractionResult(
            text=text,
            metadata=metadata,
            attachments=attachments
        )
    
    @staticmethod
    def _html_to_text(html: str) -> str:
        """Basic HTML to text conversion"""
        import html as _html_mod
        # Remove script and style
        text = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
        # Replace br and p with newlines
        text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
        text = re.sub(r'</p>', '\n\n', text, flags=re.IGNORECASE)
        # Remove all tags
        text = re.sub(r'<[^>]+>', ' ', text)
        # Decode all HTML entities (handles &nbsp; &lt; &gt; &amp; &quot; and numeric entities)
        text = _html_mod.unescape(text)
        # Cleanup whitespace
        text = re.sub(r'\n\s*\n', '\n\n', text)
        return text.strip()


# ═══════════════════════════════════════════════════════════════════════════════
# MSG EXTRACTOR (Outlook)
# ═══════════════════════════════════════════════════════════════════════════════

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
                import extract_msg
                cls._available = True
            except ImportError:
                cls._available = False
        return cls._available
    
    @classmethod
    def extract(cls, path: Path) -> ExtractionResult:
        try:
            import extract_msg

            msg = extract_msg.Message(str(path))
            try:
                text_parts = []
                attachments = []

                # Metadata
                metadata = {
                    'from': msg.sender or '',
                    'to': msg.to or '',
                    'cc': msg.cc or '',
                    'subject': msg.subject or '',
                    'date': str(msg.date) if msg.date else '',
                }
                metadata = {k: v for k, v in metadata.items() if v}

                # Header
                header_text = f"""
От: {metadata.get('from', 'N/A')}
Кому: {metadata.get('to', 'N/A')}
Тема: {metadata.get('subject', 'N/A')}
Дата: {metadata.get('date', 'N/A')}
""".strip()
                text_parts.append(header_text)

                # Body
                if msg.body:
                    text_parts.append(msg.body)
                elif msg.htmlBody:
                    html_body = msg.htmlBody
                    if isinstance(html_body, bytes):
                        html_body = html_body.decode('utf-8', errors='ignore')
                    text_parts.append(EMLExtractor._html_to_text(html_body))

                # Attachments info
                for att in msg.attachments:
                    att_result = ExtractionResult(
                        text=None,
                        metadata={
                            'filename': att.longFilename or att.shortFilename or 'attachment',
                            'size': len(att.data) if att.data else 0
                        }
                    )
                    attachments.append(att_result)

                text = '\n\n'.join(text_parts) if text_parts else None

                return ExtractionResult(
                    text=text,
                    metadata=metadata,
                    attachments=attachments
                )
            finally:
                msg.close()

        except Exception as e:
            return ExtractionResult(error=f"MSG extraction failed: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# MBOX EXTRACTOR
# ═══════════════════════════════════════════════════════════════════════════════

@registry.register
class MBOXExtractor(BaseExtractor):
    """Извлечение текста из MBOX (mailbox archive)"""
    
    extensions = ['.mbox']
    priority = 15
    
    @classmethod
    def is_available(cls) -> bool:
        return True  # mailbox is built-in
    
    @classmethod
    def extract(cls, path: Path) -> ExtractionResult:
        try:
            import mailbox
            
            text_parts = []
            metadata = {'message_count': 0}
            
            mbox = mailbox.mbox(str(path))
            
            for i, message in enumerate(mbox):
                if i >= 1000:  # Limit to prevent huge files
                    # Avoid len(mbox) which scans the entire mailbox into memory
                    text_parts.append("[...достигнут лимит 1000 сообщений]")
                    break
                
                msg_result = EMLExtractor._extract_message(message)
                if msg_result.text:
                    text_parts.append(f"--- Сообщение {i+1} ---\n{msg_result.text}")
                
                metadata['message_count'] = i + 1
            
            mbox.close()
            
            text = '\n\n'.join(text_parts) if text_parts else None
            
            return ExtractionResult(text=text, metadata=metadata)
            
        except Exception as e:
            return ExtractionResult(error=f"MBOX extraction failed: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# PST EXTRACTOR (Outlook Archive)
# ═══════════════════════════════════════════════════════════════════════════════

@registry.register
class PSTExtractor(BaseExtractor):
    """Извлечение текста из PST (Outlook archive)"""
    
    extensions = ['.pst', '.ost']
    priority = 15
    
    _available: Optional[bool] = None
    
    @classmethod
    def is_available(cls) -> bool:
        if cls._available is None:
            try:
                import pypff
                cls._available = True
            except ImportError:
                # Try alternative
                try:
                    import libpff
                    cls._available = True
                except ImportError:
                    cls._available = False
        return cls._available
    
    @classmethod
    def extract(cls, path: Path) -> ExtractionResult:
        try:
            import pypff
            
            pst = pypff.file()
            pst.open(str(path))
            
            text_parts = []
            metadata = {'folder_count': 0, 'message_count': 0}
            
            root = pst.get_root_folder()
            cls._extract_folder(root, text_parts, metadata, depth=0, max_messages=500)
            
            pst.close()
            
            text = '\n\n'.join(text_parts) if text_parts else None
            
            return ExtractionResult(text=text, metadata=metadata)
            
        except ImportError:
            return ExtractionResult(error="PST extraction requires pypff library")
        except Exception as e:
            return ExtractionResult(error=f"PST extraction failed: {e}")
    
    @classmethod
    def _extract_folder(
        cls,
        folder,
        text_parts: List[str],
        metadata: Dict,
        depth: int = 0,
        max_messages: int = 500
    ) -> None:
        """Рекурсивное извлечение из папок PST"""
        if metadata['message_count'] >= max_messages:
            return
        
        metadata['folder_count'] += 1
        
        # Folder name
        folder_name = folder.name if hasattr(folder, 'name') and folder.name else "Unnamed"
        indent = "  " * depth
        
        # Messages in folder
        if hasattr(folder, 'get_number_of_sub_messages'):
            for i in range(folder.get_number_of_sub_messages()):
                if metadata['message_count'] >= max_messages:
                    text_parts.append(f"{indent}[...достигнут лимит сообщений]")
                    return
                
                try:
                    message = folder.get_sub_message(i)
                    
                    subject = message.subject if hasattr(message, 'subject') else ''
                    sender = message.sender_name if hasattr(message, 'sender_name') else ''
                    body = message.plain_text_body if hasattr(message, 'plain_text_body') else ''
                    
                    if body or subject:
                        msg_text = f"""
{indent}--- Сообщение ---
{indent}Папка: {folder_name}
{indent}От: {sender}
{indent}Тема: {subject}
{indent}{body[:2000] if body else '[нет текста]'}
""".strip()
                        text_parts.append(msg_text)
                        metadata['message_count'] += 1
                        
                except Exception:
                    continue
        
        # Subfolders
        if hasattr(folder, 'get_number_of_sub_folders'):
            for i in range(folder.get_number_of_sub_folders()):
                try:
                    subfolder = folder.get_sub_folder(i)
                    cls._extract_folder(subfolder, text_parts, metadata, depth + 1, max_messages)
                except Exception:
                    continue


__all__ = ['EMLExtractor', 'MSGExtractor', 'MBOXExtractor', 'PSTExtractor']
