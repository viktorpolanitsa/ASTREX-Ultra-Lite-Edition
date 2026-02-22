#!/usr/bin/env python3
"""
ASTREX v3.0 — Database Extractors
SQLite, Access (MDB/ACCDB)
"""

import re
import sqlite3
from pathlib import Path
from typing import Optional, List, Dict, Any

from .base import BaseExtractor, ExtractionResult, registry


def _safe_identifier(name: str) -> str:
    """Sanitize SQL identifier: allow only alphanumeric and underscore."""
    clean = re.sub(r'[^\w]', '', name)
    if not clean:
        raise ValueError(f"Invalid identifier: {name}")
    return f'"{clean}"'


# ═══════════════════════════════════════════════════════════════════════════════
# SQLITE EXTRACTOR
# ═══════════════════════════════════════════════════════════════════════════════

@registry.register
class SQLiteExtractor(BaseExtractor):
    """Извлечение текстовых данных из SQLite баз"""
    
    extensions = ['.db', '.sqlite', '.sqlite3']
    priority = 40
    
    MAX_ROWS_PER_TABLE = 1000
    MAX_CELL_LENGTH = 5000
    
    @classmethod
    def is_available(cls) -> bool:
        return True  # sqlite3 is built-in
    
    @classmethod
    def extract(cls, path: Path) -> ExtractionResult:
        conn = None
        try:
            text_parts = []
            metadata = {
                'tables': [],
                'total_rows': 0
            }

            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # Get all tables
            cursor.execute("""
                SELECT name FROM sqlite_master
                WHERE type='table'
                AND name NOT LIKE 'sqlite_%'
            """)
            tables = [row[0] for row in cursor.fetchall()]

            for table in tables:
                try:
                    safe_table = _safe_identifier(table)

                    # Get table info
                    cursor.execute(f'PRAGMA table_info({safe_table})')
                    columns = [col[1] for col in cursor.fetchall()]

                    # Get row count
                    cursor.execute(f'SELECT COUNT(*) FROM {safe_table}')
                    row_count = cursor.fetchone()[0]

                    metadata['tables'].append({
                        'name': table,
                        'columns': columns,
                        'rows': row_count
                    })

                    # Extract text columns
                    text_columns = cls._identify_text_columns(cursor, table, columns)

                    if text_columns:
                        # Get sample data
                        cols_str = ', '.join(_safe_identifier(c) for c in text_columns)
                        cursor.execute(f"""
                            SELECT {cols_str} FROM {safe_table}
                            LIMIT {cls.MAX_ROWS_PER_TABLE}
                        """)

                        rows_text = []
                        for row in cursor.fetchall():
                            row_parts = []
                            for i, val in enumerate(row):
                                if val is not None:
                                    val_str = str(val)[:cls.MAX_CELL_LENGTH]
                                    if val_str.strip():
                                        row_parts.append(f"{text_columns[i]}: {val_str}")
                            if row_parts:
                                rows_text.append(' | '.join(row_parts))

                        if rows_text:
                            table_text = f"--- Таблица: {table} ({row_count} записей) ---\n"
                            table_text += '\n'.join(rows_text[:100])  # First 100 rows
                            if len(rows_text) > 100:
                                table_text += f"\n[...и ещё {len(rows_text) - 100} записей]"
                            text_parts.append(table_text)
                            metadata['total_rows'] += min(row_count, cls.MAX_ROWS_PER_TABLE)

                except sqlite3.Error:
                    continue

            text = '\n\n'.join(text_parts) if text_parts else f"[SQLite база: {len(tables)} таблиц]"

            return ExtractionResult(text=text, metadata=metadata)

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
    
    @classmethod
    def _identify_text_columns(
        cls, 
        cursor: sqlite3.Cursor, 
        table: str, 
        columns: List[str]
    ) -> List[str]:
        """Определить колонки с текстовыми данными"""
        text_columns = []
        
        safe_table = _safe_identifier(table)
        for col in columns:
            try:
                safe_col = _safe_identifier(col)
                # Sample first non-null value
                cursor.execute(f"""
                    SELECT {safe_col} FROM {safe_table}
                    WHERE {safe_col} IS NOT NULL
                    LIMIT 1
                """)
                row = cursor.fetchone()
                
                if row:
                    val = row[0]
                    # Check if it's text-like
                    if isinstance(val, str):
                        text_columns.append(col)
                    elif isinstance(val, (bytes,)):
                        # Try to decode
                        try:
                            val.decode('utf-8')
                            text_columns.append(col)
                        except Exception:
                            pass
            except Exception:
                continue
        
        return text_columns


# ═══════════════════════════════════════════════════════════════════════════════
# ACCESS DATABASE EXTRACTOR
# ═══════════════════════════════════════════════════════════════════════════════

@registry.register
class AccessExtractor(BaseExtractor):
    """Извлечение текстовых данных из Access баз (MDB/ACCDB)"""
    
    extensions = ['.mdb', '.accdb']
    priority = 40
    
    _available: Optional[bool] = None
    
    MAX_ROWS_PER_TABLE = 500
    
    @classmethod
    def is_available(cls) -> bool:
        if cls._available is None:
            try:
                import pyodbc
                # Check for Access driver
                drivers = pyodbc.drivers()
                cls._available = any('access' in d.lower() for d in drivers)
            except ImportError:
                # Try mdbtools
                try:
                    import subprocess
                    result = subprocess.run(
                        ['mdb-tables', '--version'],
                        capture_output=True,
                        timeout=5
                    )
                    cls._available = True
                except Exception:
                    cls._available = False
        return cls._available
    
    @classmethod
    def extract(cls, path: Path) -> ExtractionResult:
        # Try pyodbc first
        try:
            return cls._extract_pyodbc(path)
        except ImportError:
            pass
        except Exception:
            pass
        
        # Fallback to mdbtools
        try:
            return cls._extract_mdbtools(path)
        except Exception as e:
            return ExtractionResult(error=f"Access extraction failed: {e}")
    
    @classmethod
    def _extract_pyodbc(cls, path: Path) -> ExtractionResult:
        """Извлечение через pyodbc"""
        import pyodbc
        
        text_parts = []
        metadata = {'tables': []}
        
        # Connection string
        conn_str = (
            f"DRIVER={{Microsoft Access Driver (*.mdb, *.accdb)}};"
            f"DBQ={path};"
        )
        
        conn = pyodbc.connect(conn_str, readonly=True)
        cursor = conn.cursor()
        
        # Get tables
        tables = [row.table_name for row in cursor.tables(tableType='TABLE')]
        
        for table in tables:
            try:
                cursor.execute(f"SELECT TOP {cls.MAX_ROWS_PER_TABLE} * FROM [{table}]")
                columns = [col[0] for col in cursor.description]
                
                rows_text = []
                for row in cursor.fetchall():
                    row_parts = []
                    for i, val in enumerate(row):
                        if val is not None:
                            val_str = str(val)[:2000]
                            if val_str.strip() and isinstance(val, str):
                                row_parts.append(f"{columns[i]}: {val_str}")
                    if row_parts:
                        rows_text.append(' | '.join(row_parts))
                
                if rows_text:
                    text_parts.append(f"--- {table} ---\n" + '\n'.join(rows_text[:50]))
                    metadata['tables'].append(table)
                    
            except pyodbc.Error:
                continue
        
        conn.close()
        
        text = '\n\n'.join(text_parts) if text_parts else f"[Access база: {len(tables)} таблиц]"
        return ExtractionResult(text=text, metadata=metadata)
    
    @classmethod
    def _extract_mdbtools(cls, path: Path) -> ExtractionResult:
        """Извлечение через mdbtools (Linux)"""
        import subprocess
        
        text_parts = []
        metadata = {'tables': []}
        
        # Get tables
        result = subprocess.run(
            ['mdb-tables', '-1', str(path)],
            capture_output=True,
            text=True,
            timeout=30
        )
        
        if result.returncode != 0:
            return ExtractionResult(error=f"mdb-tables failed: {result.stderr}")
        
        tables = [t.strip() for t in result.stdout.strip().split('\n') if t.strip()]
        
        for table in tables[:20]:  # Limit tables
            try:
                result = subprocess.run(
                    ['mdb-export', str(path), table],
                    capture_output=True,
                    text=True,
                    timeout=60
                )
                
                if result.returncode == 0 and result.stdout.strip():
                    # CSV output - just take first N lines
                    lines = result.stdout.strip().split('\n')[:50]
                    text_parts.append(f"--- {table} ---\n" + '\n'.join(lines))
                    metadata['tables'].append(table)
                    
            except subprocess.TimeoutExpired:
                continue
        
        text = '\n\n'.join(text_parts) if text_parts else f"[Access база: {len(tables)} таблиц]"
        return ExtractionResult(text=text, metadata=metadata)


# ═══════════════════════════════════════════════════════════════════════════════
# SQL DUMP EXTRACTOR
# ═══════════════════════════════════════════════════════════════════════════════

@registry.register
class SQLDumpExtractor(BaseExtractor):
    """Извлечение данных из SQL дампов (.sql, .dump, .pgdump)"""

    extensions = ['.sql', '.dump', '.pgdump']
    priority = 50

    MAX_READ = 50 * 1024 * 1024  # 50MB

    @classmethod
    def is_available(cls) -> bool:
        return True

    @classmethod
    def extract(cls, path: Path) -> ExtractionResult:
        import re

        try:
            file_size = path.stat().st_size

            # Detect encoding
            for enc in ('utf-8', 'cp1251', 'latin-1'):
                try:
                    with open(path, 'r', encoding=enc, errors='strict') as f:
                        f.read(4096)
                    encoding = enc
                    break
                except UnicodeDecodeError:
                    continue
            else:
                encoding = 'utf-8'

            text_parts = []
            tables_found = []
            inserts_count = 0

            with open(path, 'r', encoding=encoding, errors='replace') as f:
                bytes_read = 0
                for line in f:
                    bytes_read += len(line.encode(encoding, errors='replace'))
                    if bytes_read > cls.MAX_READ:
                        text_parts.append(f"\n[...обрезано, файл {file_size} байт...]")
                        break

                    stripped = line.strip()
                    if not stripped or stripped.startswith('--') or stripped.startswith('/*'):
                        continue

                    # CREATE TABLE
                    m = re.match(r'CREATE\s+TABLE\s+[`"\[]?(\w+)', stripped, re.IGNORECASE)
                    if m:
                        tables_found.append(m.group(1))
                        text_parts.append(stripped[:500])
                        continue

                    # INSERT — extract values
                    if stripped.upper().startswith('INSERT'):
                        inserts_count += 1
                        if inserts_count <= 500:
                            # Extract string values from INSERT
                            values = re.findall(r"'([^']{2,500})'", stripped)
                            if values:
                                text_parts.append(' | '.join(values[:20]))
                        continue

            return ExtractionResult(
                text='\n'.join(text_parts) if text_parts else f"[SQL dump: {file_size} bytes]",
                metadata={
                    'encoding': encoding,
                    'file_size': file_size,
                    'tables': tables_found,
                    'inserts': inserts_count
                }
            )

        except Exception as e:
            return ExtractionResult(error=f"SQL dump extraction failed: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# MYSQL BINARY FILES EXTRACTOR
# ═══════════════════════════════════════════════════════════════════════════════

@registry.register
class MySQLBinaryExtractor(BaseExtractor):
    """Извлечение текста из MySQL binary files (.MYD, .MYI, .frm)"""

    extensions = ['.myd', '.myi', '.frm']
    priority = 60

    MAX_READ = 20 * 1024 * 1024  # 20MB

    @classmethod
    def is_available(cls) -> bool:
        return True

    @classmethod
    def extract(cls, path: Path) -> ExtractionResult:
        try:
            file_size = path.stat().st_size
            read_size = min(file_size, cls.MAX_READ)

            with open(path, 'rb') as f:
                raw = f.read(read_size)

            # Extract printable strings (min length 6)
            import re
            strings = re.findall(rb'[\x20-\x7e\xc0-\xff]{6,500}', raw)

            # Decode
            text_parts = []
            for s in strings[:5000]:
                try:
                    text_parts.append(s.decode('utf-8'))
                except UnicodeDecodeError:
                    try:
                        text_parts.append(s.decode('cp1251'))
                    except UnicodeDecodeError:
                        continue

            suffix = path.suffix.lower()
            ftype = {'.myd': 'MySQL Data', '.myi': 'MySQL Index', '.frm': 'MySQL Schema'}.get(suffix, 'MySQL')

            return ExtractionResult(
                text='\n'.join(text_parts) if text_parts else f"[{ftype}: {file_size} bytes, no text found]",
                metadata={
                    'file_type': ftype,
                    'file_size': file_size,
                    'strings_found': len(text_parts)
                }
            )

        except Exception as e:
            return ExtractionResult(error=f"MySQL binary extraction failed: {e}")


__all__ = ['SQLiteExtractor', 'AccessExtractor', 'SQLDumpExtractor', 'MySQLBinaryExtractor']
