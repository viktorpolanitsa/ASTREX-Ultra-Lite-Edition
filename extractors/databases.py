#!/usr/bin/env python3
"""
ASTREX v3.0 — Database Extractors
SQLite, Access (MDB/ACCDB), SQL дампы, MySQL MyISAM
"""

import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, List, Dict, Any

from .base import BaseExtractor, ExtractionResult, TextCollector, registry, worker_temp_dir
from core.config import ENGINE_CONFIG
from core.encoding import decode_bytes, is_probably_binary


def _quote_identifier(name: str) -> str:
    """Экранировать SQL-идентификатор (любые символы допустимы)."""
    return '"' + str(name).replace('"', '""') + '"'


def _safe_identifier(name: str) -> str:
    """Совместимость со старым API."""
    return _quote_identifier(name)


def _cell_to_text(value: Any, max_len: int = 10000) -> Optional[str]:
    """Значение ячейки как текст (числа включаются — по ним тоже ищут)."""
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray, memoryview)):
        data = bytes(value)
        if not data or is_probably_binary(data[:4096]):
            return None
        text, _enc = decode_bytes(data[:max_len * 4])
        value = text
    text = str(value).strip()
    return text[:max_len] if text else None


# ═══════════════════════════════════════════════════════════════════════════════
# SQLITE EXTRACTOR
# ═══════════════════════════════════════════════════════════════════════════════

@registry.register
class SQLiteExtractor(BaseExtractor):
    """Извлечение данных из SQLite баз (все таблицы, все строки — до лимита текста)"""

    extensions = ['.db', '.sqlite', '.sqlite3', '.db3']
    priority = 40

    MAX_CELL_LENGTH = 10000
    MAX_WAL_COPY = 512 * 1024 * 1024

    @classmethod
    def is_available(cls) -> bool:
        return True  # sqlite3 is built-in

    @classmethod
    def _connect(cls, path: Path):
        """Открыть базу строго на чтение, не изменяя файлы-улики.

        Путь кодируется в URI (as_uri): символы '#', '?', '%' в имени файла
        раньше обрезали URI, отключали mode=ro и SQLite создавал рядом новый
        пустой файл. Если рядом есть журнал WAL, база копируется во временный
        каталог (чтобы прочитать незафиксированные в основном файле данные,
        не создавая -shm рядом с уликой).
        """
        wal = Path(str(path) + '-wal')
        if wal.exists() and wal.stat().st_size > 0:
            total = path.stat().st_size + wal.stat().st_size
            if total <= cls.MAX_WAL_COPY:
                tmpdir = tempfile.mkdtemp(prefix="astrex-sqlite-", dir=str(worker_temp_dir()))
                copy = Path(tmpdir) / 'db.sqlite'
                shutil.copy2(path, copy)
                shutil.copy2(wal, Path(str(copy) + '-wal'))
                conn = sqlite3.connect(f"{copy.as_uri()}?mode=ro", uri=True)
                return conn, tmpdir
        uri = f"{path.absolute().as_uri()}?mode=ro&immutable=1"
        return sqlite3.connect(uri, uri=True), None

    @classmethod
    def extract(cls, path: Path) -> ExtractionResult:
        conn = None
        tmpdir = None
        try:
            with open(path, 'rb') as f:
                if f.read(16) != b'SQLite format 3\x00':
                    return ExtractionResult(error="Not an SQLite database")

            collector = TextCollector()
            metadata: Dict[str, Any] = {'tables': [], 'total_rows': 0}

            conn, tmpdir = cls._connect(path)
            conn.text_factory = lambda b: b.decode('utf-8', errors='replace')
            cursor = conn.cursor()

            cursor.execute("""
                SELECT name FROM sqlite_master
                WHERE type='table' AND name NOT LIKE 'sqlite_%'
                ORDER BY name
            """)
            tables = [row[0] for row in cursor.fetchall()]

            for table in tables:
                if collector.full:
                    break
                quoted = _quote_identifier(table)
                try:
                    cursor.execute(f'PRAGMA table_info({quoted})')
                    columns = [col[1] for col in cursor.fetchall()]
                    cursor.execute(f'SELECT COUNT(*) FROM {quoted}')
                    row_count = cursor.fetchone()[0]
                except sqlite3.Error as e:
                    metadata.setdefault('table_errors', []).append(f"{table}: {e}")
                    continue

                metadata['tables'].append({'name': table, 'columns': columns[:100], 'rows': row_count})
                if not collector.add(f"--- Таблица: {table} ({row_count} записей) ---"):
                    break

                try:
                    data_cursor = conn.execute(f'SELECT * FROM {quoted}')
                    names = [d[0] for d in data_cursor.description]
                    while not collector.full:
                        rows = data_cursor.fetchmany(500)
                        if not rows:
                            break
                        for row in rows:
                            parts = []
                            for name, value in zip(names, row):
                                text = _cell_to_text(value, cls.MAX_CELL_LENGTH)
                                if text:
                                    parts.append(f"{name}: {text}")
                            if parts:
                                metadata['total_rows'] += 1
                                if not collector.add(' | '.join(parts)):
                                    break
                except sqlite3.Error as e:
                    metadata.setdefault('table_errors', []).append(f"{table}: {e}")

            metadata['truncated'] = collector.truncated
            return ExtractionResult(text=collector.text('\n'), metadata=metadata)

        except sqlite3.Error as e:
            return ExtractionResult(error=f"SQLite error: {e}")
        except Exception as e:
            return ExtractionResult(error=f"Database extraction failed: {e}")
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass
            if tmpdir:
                shutil.rmtree(tmpdir, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════════════════
# ACCESS DATABASE EXTRACTOR
# ═══════════════════════════════════════════════════════════════════════════════

@registry.register
class AccessExtractor(BaseExtractor):
    """Извлечение данных из Access баз (MDB/ACCDB): pyodbc с драйвером Access или mdbtools"""

    extensions = ['.mdb', '.accdb']
    priority = 40

    _pyodbc_driver: Optional[str] = None
    _mdbtools: Optional[bool] = None

    @classmethod
    def _detect(cls) -> None:
        if cls._mdbtools is not None:
            return
        cls._pyodbc_driver = ''
        try:
            import pyodbc
            for driver in pyodbc.drivers():
                if 'access' in driver.lower():
                    cls._pyodbc_driver = driver
                    break
        except Exception:
            pass
        cls._mdbtools = bool(shutil.which('mdb-tables') and shutil.which('mdb-export'))

    @classmethod
    def is_available(cls) -> bool:
        cls._detect()
        return bool(cls._pyodbc_driver) or bool(cls._mdbtools)

    @classmethod
    def extract(cls, path: Path) -> ExtractionResult:
        cls._detect()
        errors = []
        if cls._pyodbc_driver:
            try:
                return cls._extract_pyodbc(path)
            except Exception as e:
                errors.append(f"pyodbc: {e}")
        if cls._mdbtools:
            try:
                return cls._extract_mdbtools(path)
            except Exception as e:
                errors.append(f"mdbtools: {e}")
        return ExtractionResult(error="Access extraction failed: " + ("; ".join(errors) or "no backend"))

    @classmethod
    def _extract_pyodbc(cls, path: Path) -> ExtractionResult:
        import pyodbc

        collector = TextCollector()
        metadata: Dict[str, Any] = {'tables': [], 'backend': 'pyodbc'}
        conn = pyodbc.connect(f"DRIVER={{{cls._pyodbc_driver}}};DBQ={path};", readonly=True)
        try:
            cursor = conn.cursor()
            tables = [row.table_name for row in cursor.tables(tableType='TABLE')]
            for table in tables:
                if collector.full:
                    break
                try:
                    cursor.execute(f"SELECT * FROM [{table.replace(']', ']]')}]")
                    columns = [col[0] for col in cursor.description]
                    collector.add(f"--- {table} ---")
                    metadata['tables'].append(table)
                    while not collector.full:
                        rows = cursor.fetchmany(500)
                        if not rows:
                            break
                        for row in rows:
                            parts = [f"{c}: {t}" for c, v in zip(columns, row)
                                     if (t := _cell_to_text(v))]
                            if parts and not collector.add(' | '.join(parts)):
                                break
                except pyodbc.Error:
                    continue
        finally:
            conn.close()
        metadata['truncated'] = collector.truncated
        return ExtractionResult(text=collector.text('\n'), metadata=metadata)

    @classmethod
    def _extract_mdbtools(cls, path: Path) -> ExtractionResult:
        """Извлечение через mdbtools (Linux): все таблицы, потоково."""
        env = dict(os.environ)
        # Базы Access 97 (Jet3) хранят текст в кодировке системы автора
        env.setdefault('MDB_JET3_CHARSET', 'cp1251')
        env.setdefault('MDB_ICONV', 'UTF-8')

        result = subprocess.run(['mdb-tables', '-1', str(path)], capture_output=True,
                                timeout=60, env=env)
        if result.returncode != 0:
            return ExtractionResult(error=f"mdb-tables failed: {result.stderr.decode('utf-8', 'replace')}")
        tables = [t.strip() for t in result.stdout.decode('utf-8', 'replace').splitlines() if t.strip()]

        collector = TextCollector()
        metadata: Dict[str, Any] = {'tables': [], 'backend': 'mdbtools'}
        for table in tables:
            if collector.full:
                break
            proc = subprocess.Popen(['mdb-export', str(path), table], stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL, env=env)
            try:
                collector.add(f"--- {table} ---")
                metadata['tables'].append(table)
                for raw_line in proc.stdout:
                    if not collector.add(raw_line.decode('utf-8', errors='replace').rstrip('\r\n')):
                        break
            finally:
                proc.stdout.close()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
        metadata['truncated'] = collector.truncated
        return ExtractionResult(text=collector.text('\n'), metadata=metadata)


# ═══════════════════════════════════════════════════════════════════════════════
# SQL DUMP EXTRACTOR
# ═══════════════════════════════════════════════════════════════════════════════

_CREATE_RE = re.compile(r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`"\[]?([\w.$]+)', re.IGNORECASE)
_COPY_RE = re.compile(r'^COPY\s+([\w."$]+)\s*(?:\(([^)]*)\))?\s+FROM\s+stdin', re.IGNORECASE)


def _sql_literals(statement: str) -> List[str]:
    """Строковые и числовые значения из оператора SQL (с учётом экранирования)."""
    values: List[str] = []
    i, n = 0, len(statement)
    in_values = False
    upper = statement.upper()
    start = upper.find(' VALUES')
    if start != -1:
        i = start
        in_values = True
    while i < n:
        ch = statement[i]
        if ch == "'":
            j = i + 1
            buf = []
            while j < n:
                c = statement[j]
                if c == '\\' and j + 1 < n:
                    nxt = statement[j + 1]
                    buf.append({'n': '\n', 't': '\t', 'r': '\r', '0': ''}.get(nxt, nxt))
                    j += 2
                    continue
                if c == "'":
                    if j + 1 < n and statement[j + 1] == "'":
                        buf.append("'")
                        j += 2
                        continue
                    break
                buf.append(c)
                j += 1
            value = ''.join(buf).strip()
            if value:
                values.append(value)
            i = j + 1
            continue
        if in_values and (ch.isdigit() or (ch == '-' and i + 1 < n and statement[i + 1].isdigit())):
            j = i + 1
            while j < n and (statement[j].isdigit() or statement[j] in '.eE+-'):
                j += 1
            prev = statement[i - 1] if i else ' '
            if not (prev.isalpha() or prev == '_'):
                values.append(statement[i:j])
            i = j
            continue
        i += 1
    return values


@registry.register
class SQLDumpExtractor(BaseExtractor):
    """Извлечение данных из SQL дампов (INSERT, COPY ... FROM stdin, pg_dump -Fc)"""

    extensions = ['.sql', '.dump', '.pgdump']
    priority = 50

    @classmethod
    def is_available(cls) -> bool:
        return True

    @classmethod
    def extract(cls, path: Path) -> ExtractionResult:
        try:
            file_size = path.stat().st_size
            with open(path, 'rb') as f:
                head = f.read(256 * 1024)

            if head.startswith(b'PGDMP'):
                return cls._extract_pg_custom(path, file_size, head)

            text_stream_encoding = 'utf-8'
            from core.encoding import detect_encoding
            encoding, _ = detect_encoding(head, final=len(head) < 256 * 1024)
            text_stream_encoding = encoding

            with open(path, 'r', encoding=text_stream_encoding, errors='replace') as f:
                return cls._parse_sql_lines(f, file_size, text_stream_encoding)

        except Exception as e:
            return ExtractionResult(error=f"SQL dump extraction failed: {e}")

    @classmethod
    def _parse_sql_lines(cls, lines, file_size: int, encoding: str) -> ExtractionResult:
        collector = TextCollector()
        tables: List[str] = []
        inserts = 0
        statement: List[str] = []
        in_insert = False
        copy_table = None

        for line in lines:
            if collector.full:
                break
            if copy_table is not None:
                if line.rstrip('\r\n') == '\\.':
                    copy_table = None
                    continue
                fields = [fld.replace('\\t', '\t').replace('\\n', ' ').replace('\\\\', '\\')
                          for fld in line.rstrip('\r\n').split('\t') if fld and fld != '\\N']
                if fields:
                    collector.add(' | '.join(fields))
                continue

            stripped = line.strip()
            if not in_insert:
                if not stripped or stripped.startswith(('--', '/*', '#')):
                    continue
                m = _CREATE_RE.match(stripped)
                if m:
                    tables.append(m.group(1).strip('`"[]'))
                    collector.add(stripped[:500])
                    continue
                m = _COPY_RE.match(stripped)
                if m:
                    copy_table = m.group(1)
                    tables.append(copy_table.strip('"'))
                    collector.add(f"--- COPY {copy_table} ({m.group(2) or ''}) ---")
                    continue
                if stripped.upper().startswith(('INSERT', 'REPLACE')):
                    in_insert = True
                    statement = []
            if in_insert:
                statement.append(line)
                # Конец оператора: ';' в конце строки вне строкового литерала
                joined_tail = stripped
                if joined_tail.endswith(';') and cls._quotes_balanced(''.join(statement)):
                    inserts += 1
                    values = _sql_literals(''.join(statement))
                    if values:
                        collector.add(' | '.join(values))
                    in_insert = False
                    statement = []
                elif sum(len(s) for s in statement) > 64 * 1024 * 1024:
                    collector.add(' | '.join(_sql_literals(''.join(statement))))
                    in_insert = False
                    statement = []

        if statement:
            collector.add(' | '.join(_sql_literals(''.join(statement))))

        return ExtractionResult(
            text=collector.text('\n'),
            metadata={'encoding': encoding, 'file_size': file_size, 'tables': tables[:200],
                      'inserts': inserts, 'truncated': collector.truncated}
        )

    @staticmethod
    def _quotes_balanced(text: str) -> bool:
        """Чётное число неэкранированных одинарных кавычек."""
        count = 0
        i, n = 0, len(text)
        while i < n:
            c = text[i]
            if c == '\\':
                i += 2
                continue
            if c == "'":
                count += 1
            i += 1
        return count % 2 == 0

    @classmethod
    def _extract_pg_custom(cls, path: Path, file_size: int, head: bytes) -> ExtractionResult:
        """pg_dump -Fc: через pg_restore (если установлен) или строки из файла."""
        pg_restore = shutil.which('pg_restore')
        if pg_restore:
            try:
                proc = subprocess.Popen([pg_restore, '-f', '-', str(path)], stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL)
                try:
                    import io
                    reader = io.TextIOWrapper(proc.stdout, encoding='utf-8', errors='replace')
                    result = cls._parse_sql_lines(reader, file_size, 'utf-8')
                finally:
                    proc.kill()
                    proc.wait()
                result.metadata['format'] = 'pg_custom'
                if result.text:
                    return result
            except OSError:
                pass
        # Без pg_restore: данные сжаты zlib, доступны только строки заголовков/схемы
        strings = re.findall(rb'[\x20-\x7e]{6,}', head)
        return ExtractionResult(
            text='\n'.join(s.decode('ascii') for s in strings[:2000]),
            metadata={'format': 'pg_custom', 'warning': 'pg_restore not installed — table data not extracted',
                      'file_size': file_size}
        )


# ═══════════════════════════════════════════════════════════════════════════════
# MYSQL BINARY FILES EXTRACTOR
# ═══════════════════════════════════════════════════════════════════════════════

# Последовательности печатных символов UTF-8 (включая кириллицу) длиной от 4
_UTF8_RUN = re.compile(rb'(?:[\x20-\x7e]|[\xc2-\xdf][\x80-\xbf]|[\xe0-\xef][\x80-\xbf]{2}|'
                       rb'[\xf0-\xf4][\x80-\xbf]{3}){4,}')
# 8-битные кириллические строки (cp1251), если таблица в latin1/cp1251
_CP1251_RUN = re.compile(rb'[\x20-\x7e\xa8\xb8\xc0-\xff]{4,}')


@registry.register
class MySQLBinaryExtractor(BaseExtractor):
    """Извлечение текста из MySQL MyISAM файлов (.MYD, .MYI, .frm)"""

    extensions = ['.myd', '.myi', '.frm']
    priority = 60

    @classmethod
    def is_available(cls) -> bool:
        return True

    @classmethod
    def extract(cls, path: Path) -> ExtractionResult:
        try:
            file_size = path.stat().st_size
            read_size = min(file_size, ENGINE_CONFIG.max_extracted_chars * 2)
            with open(path, 'rb') as f:
                raw = f.read(read_size)

            collector = TextCollector()
            utf8_count = 0
            for m in _UTF8_RUN.finditer(raw):
                s = m.group(0).decode('utf-8', errors='replace').strip()
                if len(s) >= 4 and sum(ch.isalnum() for ch in s) >= len(s) * 0.5:
                    utf8_count += 1
                    if not collector.add(s):
                        break

            cp_count = 0
            if not collector.full:
                for m in _CP1251_RUN.finditer(raw):
                    chunk = m.group(0)
                    if not any(b >= 0xC0 for b in chunk):
                        continue  # чистый ASCII уже учтён выше
                    try:
                        chunk.decode('utf-8')
                        continue      # валидный UTF-8 — уже учтён
                    except UnicodeDecodeError:
                        pass
                    s = chunk.decode('cp1251', errors='replace').strip()
                    if sum(ch.isalpha() for ch in s) >= len(s) * 0.5:
                        cp_count += 1
                        if not collector.add(s):
                            break

            suffix = path.suffix.lower()
            ftype = {'.myd': 'MySQL Data', '.myi': 'MySQL Index', '.frm': 'MySQL Schema'}.get(suffix, 'MySQL')

            return ExtractionResult(
                text=collector.text('\n'),
                metadata={'file_type': ftype, 'file_size': file_size,
                          'strings_utf8': utf8_count, 'strings_cp1251': cp_count,
                          'truncated': collector.truncated or read_size < file_size}
            )

        except Exception as e:
            return ExtractionResult(error=f"MySQL binary extraction failed: {e}")


__all__ = ['SQLiteExtractor', 'AccessExtractor', 'SQLDumpExtractor', 'MySQLBinaryExtractor']
