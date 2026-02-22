#!/usr/bin/env python3
"""
ASTREX v3.0 — File Index
SQLite индекс для кеширования и быстрого поиска
"""

import sqlite3
import hashlib
import threading
import json
import logging
import time
from collections import OrderedDict
from pathlib import Path
from datetime import datetime
from functools import lru_cache
from typing import Optional, List, Dict, Any, Generator, Tuple
from dataclasses import dataclass
from contextlib import contextmanager

from .config import INDEX_DB_PATH

logger = logging.getLogger("astrex.index")


# ═══════════════════════════════════════════════════════════════════════════════
# DATA CLASSES
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class IndexedFile:
    """Запись индексированного файла"""
    id: int
    path: str
    filename: str
    extension: str
    size: int
    mtime: float
    hash_md5: str
    indexed_at: datetime
    extracted_text: Optional[str]
    entities_json: Optional[str]
    
    @property
    def entities(self) -> Dict[str, List[str]]:
        if self.entities_json:
            return json.loads(self.entities_json)
        return {}


@dataclass
class SearchResult:
    """Результат поиска"""
    file_id: int
    path: str
    filename: str
    snippet: str
    score: float
    entities: Dict[str, List[str]]
    match_positions: List[int]


# ═══════════════════════════════════════════════════════════════════════════════
# TTL LRU CACHE
# ═══════════════════════════════════════════════════════════════════════════════

class TTLCache:
    """LRU-кэш с TTL для снижения нагрузки на SQLite.

    Использует OrderedDict: get() перемещает ключ в конец (O(1) LRU),
    put() выталкивает первый элемент (O(1) eviction) вместо O(n) min().
    """

    def __init__(self, maxsize: int = 10000, ttl: float = 60.0):
        self._maxsize = maxsize
        self._ttl = ttl
        self._cache: OrderedDict = OrderedDict()  # key -> (value, timestamp)
        self._lock = threading.Lock()

    def get(self, key: str):
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            value, ts = entry
            if time.monotonic() - ts > self._ttl:
                del self._cache[key]
                return None
            # Promote to most-recently-used position
            self._cache.move_to_end(key)
            return value

    def put(self, key: str, value):
        with self._lock:
            if key in self._cache:
                # Refresh existing entry — move to MRU end
                self._cache.move_to_end(key)
            elif len(self._cache) >= self._maxsize:
                # Evict least-recently-used (first) entry — O(1)
                self._cache.popitem(last=False)
            self._cache[key] = (value, time.monotonic())

    def invalidate(self, key: str):
        with self._lock:
            self._cache.pop(key, None)

    def invalidate_prefix(self, prefix: str):
        """Remove all entries whose key starts with prefix."""
        with self._lock:
            to_remove = [k for k in self._cache if k.startswith(prefix)]
            for k in to_remove:
                del self._cache[k]

    def clear(self):
        with self._lock:
            self._cache.clear()


# ═══════════════════════════════════════════════════════════════════════════════
# INDEX MANAGER
# ═══════════════════════════════════════════════════════════════════════════════

class FileIndex:
    """
    SQLite-based индекс файлов.
    
    Функции:
    - Кеширование извлечённого текста
    - Хранение NER сущностей
    - Инкрементальное обновление (по mtime/hash)
    - FTS5 полнотекстовый поиск
    """
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return

        self.db_path = INDEX_DB_PATH
        self._local = threading.local()
        self._connections: list = []
        self._conn_lock = threading.Lock()
        # In-memory LRU caches
        self._file_cache = TTLCache(maxsize=10000, ttl=60.0)
        self._update_cache = TTLCache(maxsize=10000, ttl=60.0)
        self._init_db()
        self._initialized = True

    @property
    def _conn(self) -> sqlite3.Connection:
        """Thread-local connection with optimized PRAGMAs"""
        if not hasattr(self._local, 'conn') or self._local.conn is None:
            self._local.conn = sqlite3.connect(
                str(self.db_path),
                check_same_thread=False,
                timeout=30.0
            )
            self._local.conn.row_factory = sqlite3.Row
            # Performance PRAGMAs
            self._local.conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn.execute("PRAGMA synchronous=NORMAL")
            self._local.conn.execute("PRAGMA cache_size=-64000")  # 64MB cache
            self._local.conn.execute("PRAGMA mmap_size=268435456")  # 256MB mmap I/O
            self._local.conn.execute("PRAGMA temp_store=MEMORY")
            with self._conn_lock:
                self._connections.append(self._local.conn)
        return self._local.conn
    
    def close(self) -> None:
        """Закрыть соединение текущего потока."""
        if hasattr(self._local, 'conn') and self._local.conn is not None:
            conn = self._local.conn
            self._local.conn = None
            try:
                conn.close()
            except Exception:
                pass
            with self._conn_lock:
                if conn in self._connections:
                    self._connections.remove(conn)

    def close_all(self) -> None:
        """Закрыть все соединения из всех потоков."""
        with self._conn_lock:
            for conn in self._connections:
                try:
                    conn.close()
                except Exception:
                    pass
            self._connections.clear()
        self._local = threading.local()
        self._file_cache.clear()
        self._update_cache.clear()

    def __del__(self):
        try:
            self.close_all()
        except Exception:
            pass

    @contextmanager
    def _cursor(self):
        """Context manager for cursor with commit"""
        cursor = self._conn.cursor()
        try:
            yield cursor
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        finally:
            cursor.close()
    
    def _init_db(self) -> None:
        """Инициализация схемы БД"""
        with self._cursor() as cur:
            # Основная таблица файлов
            cur.execute("""
                CREATE TABLE IF NOT EXISTS files (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    path TEXT UNIQUE NOT NULL,
                    filename TEXT NOT NULL,
                    extension TEXT,
                    size INTEGER NOT NULL,
                    mtime REAL NOT NULL,
                    hash_md5 TEXT,
                    indexed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    extracted_text TEXT,
                    entities_json TEXT
                )
            """)
            
            # Индексы
            cur.execute("CREATE INDEX IF NOT EXISTS idx_files_path ON files(path)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_files_hash ON files(hash_md5)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_files_ext ON files(extension)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_files_mtime ON files(mtime)")
            
            # FTS5 для полнотекстового поиска с unicode61 tokenizer
            cur.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS files_fts USING fts5(
                    path,
                    filename,
                    extracted_text,
                    entities_json,
                    content='files',
                    content_rowid='id',
                    tokenize='unicode61 remove_diacritics 2'
                )
            """)
            
            # Триггеры для синхронизации FTS
            cur.execute("""
                CREATE TRIGGER IF NOT EXISTS files_ai AFTER INSERT ON files BEGIN
                    INSERT INTO files_fts(rowid, path, filename, extracted_text, entities_json)
                    VALUES (new.id, new.path, new.filename, new.extracted_text, new.entities_json);
                END
            """)
            
            cur.execute("""
                CREATE TRIGGER IF NOT EXISTS files_ad AFTER DELETE ON files BEGIN
                    INSERT INTO files_fts(files_fts, rowid, path, filename, extracted_text, entities_json)
                    VALUES ('delete', old.id, old.path, old.filename, old.extracted_text, old.entities_json);
                END
            """)
            
            cur.execute("""
                CREATE TRIGGER IF NOT EXISTS files_au AFTER UPDATE ON files BEGIN
                    INSERT INTO files_fts(files_fts, rowid, path, filename, extracted_text, entities_json)
                    VALUES ('delete', old.id, old.path, old.filename, old.extracted_text, old.entities_json);
                    INSERT INTO files_fts(rowid, path, filename, extracted_text, entities_json)
                    VALUES (new.id, new.path, new.filename, new.extracted_text, new.entities_json);
                END
            """)
            
            # Таблица сканирований
            cur.execute("""
                CREATE TABLE IF NOT EXISTS scans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    folder TEXT NOT NULL,
                    query TEXT,
                    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    finished_at TIMESTAMP,
                    files_total INTEGER,
                    files_processed INTEGER,
                    matches_found INTEGER
                )
            """)
            
            # Таблица связей между сущностями
            cur.execute("""
                CREATE TABLE IF NOT EXISTS entity_links (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    entity1 TEXT NOT NULL,
                    entity1_type TEXT NOT NULL,
                    entity2 TEXT NOT NULL,
                    entity2_type TEXT NOT NULL,
                    file_id INTEGER REFERENCES files(id),
                    context TEXT,
                    weight REAL DEFAULT 1.0
                )
            """)
            
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_links_e1 
                ON entity_links(entity1, entity1_type)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_links_e2 
                ON entity_links(entity2, entity2_type)
            """)
    
    # ═══════════════════════════════════════════════════════════════════════════
    # FILE OPERATIONS
    # ═══════════════════════════════════════════════════════════════════════════
    
    def get_file(self, path: str) -> Optional[IndexedFile]:
        """Получить файл по пути (с LRU-кэшем)"""
        cached = self._file_cache.get(path)
        if cached is not None:
            return cached

        with self._cursor() as cur:
            cur.execute("SELECT * FROM files WHERE path = ?", (path,))
            row = cur.fetchone()
            if row:
                result = IndexedFile(**dict(row))
                self._file_cache.put(path, result)
                return result
        return None
    
    def get_file_by_hash(self, hash_md5: str) -> Optional[IndexedFile]:
        """Получить файл по хешу"""
        with self._cursor() as cur:
            cur.execute("SELECT * FROM files WHERE hash_md5 = ?", (hash_md5,))
            row = cur.fetchone()
            if row:
                return IndexedFile(**dict(row))
        return None
    
    def file_needs_update(self, path: str, mtime: float, size: int) -> bool:
        """Проверить, нужно ли переиндексировать файл (с кэшем)"""
        cache_key = f"{path}:{mtime}:{size}"
        cached = self._update_cache.get(cache_key)
        if cached is not None:
            return cached

        existing = self.get_file(path)
        if not existing:
            result = True
        else:
            result = existing.mtime != mtime or existing.size != size
        self._update_cache.put(cache_key, result)
        return result
    
    def upsert_file(
        self,
        path: str,
        filename: str,
        extension: str,
        size: int,
        mtime: float,
        hash_md5: Optional[str] = None,
        extracted_text: Optional[str] = None,
        entities: Optional[Dict[str, List[str]]] = None
    ) -> int:
        """Вставить или обновить файл"""
        entities_json = json.dumps(entities, ensure_ascii=False) if entities else None

        # Invalidate caches for this path only
        self._file_cache.invalidate(path)
        # Invalidate old update cache entries for this path (any mtime/size combo)
        self._update_cache.invalidate_prefix(path + ":")

        with self._cursor() as cur:
            cur.execute("""
                INSERT INTO files (path, filename, extension, size, mtime, hash_md5,
                                   extracted_text, entities_json, indexed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(path) DO UPDATE SET
                    filename = excluded.filename,
                    extension = excluded.extension,
                    size = excluded.size,
                    mtime = excluded.mtime,
                    hash_md5 = excluded.hash_md5,
                    extracted_text = excluded.extracted_text,
                    entities_json = excluded.entities_json,
                    indexed_at = CURRENT_TIMESTAMP
            """, (path, filename, extension, size, mtime, hash_md5,
                  extracted_text, entities_json))

            # lastrowid может быть 0 после ON CONFLICT DO UPDATE,
            # в таком случае получаем id через SELECT
            row_id = cur.lastrowid
            if not row_id:
                cur.execute("SELECT id FROM files WHERE path = ?", (path,))
                row = cur.fetchone()
                row_id = row[0] if row else 0
            return row_id
    
    def delete_file(self, path: str) -> bool:
        """Удалить файл из индекса"""
        self._file_cache.invalidate(path)
        with self._cursor() as cur:
            cur.execute("DELETE FROM files WHERE path = ?", (path,))
            return cur.rowcount > 0
    
    def delete_missing_files(self, existing_paths: set) -> int:
        """Удалить из индекса файлы, которых больше нет.

        Читает пути из БД потоково (fetchmany), удаляет батчами по 500
        чтобы не грузить RAM и не превышать лимит параметров SQLite.
        """
        conn = self._conn
        cur = conn.cursor()
        deleted = 0
        try:
            cur.execute("SELECT path FROM files")
            to_delete: List[str] = []
            while True:
                rows = cur.fetchmany(1000)
                if not rows:
                    break
                for row in rows:
                    if row[0] not in existing_paths:
                        to_delete.append(row[0])

            batch_size = 500
            for i in range(0, len(to_delete), batch_size):
                batch = to_delete[i:i + batch_size]
                placeholders = ','.join('?' * len(batch))
                cur.execute(
                    f"DELETE FROM files WHERE path IN ({placeholders})", batch
                )
                deleted += cur.rowcount
                for p in batch:
                    self._file_cache.invalidate(p)

            if deleted:
                conn.commit()
                self._update_cache.clear()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
        return deleted
    
    # ═══════════════════════════════════════════════════════════════════════════
    # SEARCH
    # ═══════════════════════════════════════════════════════════════════════════
    
    def search_fts(
        self,
        query: str,
        limit: int = 100,
        use_prefix: bool = True,
        use_or: bool = False
    ) -> List[Dict[str, Any]]:
        """FTS5 полнотекстовый поиск с расширенными возможностями.

        Args:
            query: поисковый запрос
            limit: максимум результатов
            use_prefix: добавлять prefix-поиск (query*) для частичных совпадений
            use_or: использовать OR между словами (по умолчанию AND)
        """
        if not query or not query.strip():
            return []

        try:
            with self._cursor() as cur:
                # Build FTS5 query
                fts_query = self._build_fts_query(query, use_prefix, use_or)

                # BM25 weights: filename(10), path(5), text(1), entities(3)
                cur.execute("""
                    SELECT f.*,
                           bm25(files_fts, 5.0, 10.0, 1.0, 3.0) as rank,
                           highlight(files_fts, 2, '<mark>', '</mark>') as snippet
                    FROM files_fts fts
                    JOIN files f ON f.id = fts.rowid
                    WHERE files_fts MATCH ?
                    ORDER BY rank
                    LIMIT ?
                """, (fts_query, limit))

                results = []
                for row in cur.fetchall():
                    d = dict(row)
                    raw_rank = d.pop('rank', 0)
                    d['score'] = min(1.0, max(0.0, 1.0 / (1.0 + abs(raw_rank))))
                    results.append(d)

                # If no results with exact match, try prefix fallback
                if not results and use_prefix:
                    prefix_query = self._build_fts_query(query, use_prefix=True, use_or=True)
                    if prefix_query != fts_query:
                        cur.execute("""
                            SELECT f.*,
                                   bm25(files_fts, 5.0, 10.0, 1.0, 3.0) as rank,
                                   highlight(files_fts, 2, '<mark>', '</mark>') as snippet
                            FROM files_fts fts
                            JOIN files f ON f.id = fts.rowid
                            WHERE files_fts MATCH ?
                            ORDER BY rank
                            LIMIT ?
                        """, (prefix_query, limit))

                        for row in cur.fetchall():
                            d = dict(row)
                            raw_rank = d.pop('rank', 0)
                            d['score'] = min(1.0, max(0.0, 1.0 / (1.0 + abs(raw_rank)))) * 0.9
                            results.append(d)

                return results
        except Exception as e:
            logger.warning("FTS search failed for query %r: %s", query, e)
            return []

    @staticmethod
    def _build_fts_query(query: str, use_prefix: bool = False, use_or: bool = False) -> str:
        """Построить FTS5 запрос с поддержкой prefix и OR/AND"""
        # Escape special FTS5 characters
        safe_query = query.replace('"', '""')
        words = safe_query.split()

        if not words:
            return f'"{safe_query}"'

        if len(words) == 1:
            word = words[0]
            if use_prefix:
                return f'"{word}" OR {word}*'
            return f'"{word}"'

        # Multiple words
        operator = " OR " if use_or else " AND "
        parts = []
        for w in words:
            if use_prefix:
                parts.append(f'("{w}" OR {w}*)')
            else:
                parts.append(f'"{w}"')

        # Also try exact phrase match (highest priority via BM25)
        phrase = f'"{safe_query}"'
        word_query = operator.join(parts)

        return f'{phrase} OR ({word_query})'
    
    def search_by_entity(
        self, 
        entity: str, 
        entity_type: Optional[str] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Поиск файлов по сущности"""
        with self._cursor() as cur:
            if entity_type:
                cur.execute("""
                    SELECT * FROM files 
                    WHERE entities_json LIKE ? 
                    AND entities_json LIKE ?
                    LIMIT ?
                """, (f'%"{entity}"%', f'%"{entity_type}"%', limit))
            else:
                cur.execute("""
                    SELECT * FROM files 
                    WHERE entities_json LIKE ?
                    LIMIT ?
                """, (f'%"{entity}"%', limit))
            
            return [dict(row) for row in cur.fetchall()]
    
    def search_by_extension(
        self, 
        extensions: List[str], 
        limit: int = 1000
    ) -> List[Dict[str, Any]]:
        """Поиск файлов по расширению"""
        with self._cursor() as cur:
            placeholders = ','.join('?' * len(extensions))
            cur.execute(f"""
                SELECT * FROM files 
                WHERE extension IN ({placeholders})
                LIMIT ?
            """, (*extensions, limit))
            
            return [dict(row) for row in cur.fetchall()]
    
    # ═══════════════════════════════════════════════════════════════════════════
    # ENTITY LINKS
    # ═══════════════════════════════════════════════════════════════════════════
    
    def add_entity_link(
        self,
        entity1: str,
        entity1_type: str,
        entity2: str,
        entity2_type: str,
        file_id: int,
        context: Optional[str] = None,
        weight: float = 1.0
    ) -> int:
        """Добавить связь между сущностями"""
        with self._cursor() as cur:
            cur.execute("""
                INSERT INTO entity_links 
                (entity1, entity1_type, entity2, entity2_type, file_id, context, weight)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (entity1, entity1_type, entity2, entity2_type, file_id, context, weight))
            return cur.lastrowid
    
    def get_entity_connections(
        self, 
        entity: str, 
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Получить все связи сущности"""
        with self._cursor() as cur:
            cur.execute("""
                SELECT * FROM entity_links 
                WHERE entity1 = ? OR entity2 = ?
                ORDER BY weight DESC
                LIMIT ?
            """, (entity, entity, limit))
            
            return [dict(row) for row in cur.fetchall()]
    
    def get_graph_data(self, limit: int = 500) -> Dict[str, Any]:
        """Получить данные для построения графа связей"""
        with self._cursor() as cur:
            # Nodes
            cur.execute("""
                SELECT entity1 as entity, entity1_type as type, 
                       SUM(weight) as total_weight
                FROM entity_links
                GROUP BY entity1, entity1_type
                UNION
                SELECT entity2 as entity, entity2_type as type,
                       SUM(weight) as total_weight
                FROM entity_links
                GROUP BY entity2, entity2_type
                ORDER BY total_weight DESC
                LIMIT ?
            """, (limit,))
            nodes = [dict(row) for row in cur.fetchall()]
            
            # Edges
            cur.execute("""
                SELECT entity1, entity1_type, entity2, entity2_type,
                       SUM(weight) as weight, COUNT(*) as count
                FROM entity_links
                GROUP BY entity1, entity2
                ORDER BY weight DESC
                LIMIT ?
            """, (limit * 2,))
            edges = [dict(row) for row in cur.fetchall()]
            
            return {"nodes": nodes, "edges": edges}
    
    # ═══════════════════════════════════════════════════════════════════════════
    # STATISTICS
    # ═══════════════════════════════════════════════════════════════════════════
    
    def get_stats(self) -> Dict[str, Any]:
        """Получить статистику индекса"""
        with self._cursor() as cur:
            cur.execute("SELECT COUNT(*) as count FROM files")
            total_files = cur.fetchone()['count']
            
            cur.execute("SELECT SUM(size) as total FROM files")
            total_size = cur.fetchone()['total'] or 0
            
            cur.execute("""
                SELECT extension, COUNT(*) as count 
                FROM files 
                GROUP BY extension 
                ORDER BY count DESC 
                LIMIT 20
            """)
            by_extension = [dict(row) for row in cur.fetchall()]
            
            cur.execute("SELECT COUNT(*) as count FROM entity_links")
            total_links = cur.fetchone()['count']
            
            return {
                "total_files": total_files,
                "total_size_bytes": total_size,
                "total_size_human": self._human_size(total_size),
                "by_extension": by_extension,
                "total_entity_links": total_links,
                "db_path": str(self.db_path),
                "db_size": self.db_path.stat().st_size if self.db_path.exists() else 0
            }
    
    @staticmethod
    def _human_size(size: int) -> str:
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} PB"
    
    def checkpoint(self) -> None:
        """WAL checkpoint после bulk-операций — освобождает место на диске.

        TRUNCATE сбрасывает WAL и обнуляет его размер.
        Безопасно вызывать из любого потока.
        """
        try:
            self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self._conn.commit()
        except Exception as e:
            logger.debug("WAL checkpoint failed: %s", e)

    def vacuum(self) -> None:
        """Оптимизация БД"""
        self._conn.execute("VACUUM")
        self._conn.execute("ANALYZE")
        self._file_cache.clear()
        self._update_cache.clear()


# ═══════════════════════════════════════════════════════════════════════════════
# UTILITY
# ═══════════════════════════════════════════════════════════════════════════════

def compute_file_hash(path: Path, chunk_size: int = 8192) -> str:
    """Вычислить MD5 хеш файла"""
    hasher = hashlib.md5()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(chunk_size), b''):
            hasher.update(chunk)
    return hasher.hexdigest()


# Singleton instance
file_index = FileIndex()


__all__ = ['FileIndex', 'IndexedFile', 'SearchResult', 'file_index', 'compute_file_hash']
