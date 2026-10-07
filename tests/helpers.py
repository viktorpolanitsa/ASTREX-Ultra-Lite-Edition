#!/usr/bin/env python3
"""Общие помощники тестов.

Импортируется ПЕРВЫМ в каждом тестовом модуле: переключает ASTREX_HOME на
временный каталог, чтобы тесты не писали в настоящий индекс ~/.astrex
пользователя (index.db, логи, секретный ключ).
"""

import atexit
import io
import os
import shutil
import sys
import tempfile
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if 'ASTREX_TEST_HOME' not in os.environ:
    _home = tempfile.mkdtemp(prefix='astrex-test-')
    os.environ['ASTREX_TEST_HOME'] = _home
    os.environ['ASTREX_HOME'] = _home
    os.environ.setdefault('ASTREX_LOG_LEVEL', 'ERROR')
    atexit.register(shutil.rmtree, _home, True)


def make_docx(path: Path, paragraphs_xml: str) -> Path:
    """Минимальный DOCX с заданным содержимым <w:body>."""
    doc = ('<?xml version="1.0" encoding="UTF-8"?><w:document '
           'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
           + paragraphs_xml + '</w:body></w:document>')
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml', '<?xml version="1.0"?><Types/>')
        z.writestr('word/document.xml', doc)
    return path


def docx_bytes(paragraphs_xml: str) -> bytes:
    buf = io.BytesIO()
    doc = ('<?xml version="1.0" encoding="UTF-8"?><w:document '
           'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
           + paragraphs_xml + '</w:body></w:document>')
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr('word/document.xml', doc)
    return buf.getvalue()


# ── функции для тестов пула процессов (должны импортироваться по имени) ──────

def worker_echo(x):
    return x * 2


def worker_sleep(seconds):
    time.sleep(seconds)
    return seconds


def worker_crash(code):
    os._exit(code)


def worker_raise(message):
    raise ValueError(message)
