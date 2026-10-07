#!/usr/bin/env python3
"""
ASTREX v3.0 — File Index
SQLite индекс для кеширования и быстрого поиска
"""

import os
import re
import sqlite3
import hashlib
import threading
import json
import logging
import time
import weakref
from collections import OrderedDict
from pathlib import Path
from typing import Optional, List, Dict, Any, Iterator, Tuple
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
    hash_md5: Optional[str]
    indexed_at: Any            # строка TIMESTAMP из SQLite
    extracted_text: Optional[str]
    entities_json: Optional[str]

    @property
    def entities(self) -> Dict[str, List[str]]:
        """Сущности ({} если не извлекались — см. entities_computed)."""
        if self.entities_json:
            try:
                return json.loads(self.entities_json)
            except (TypeError, ValueError):
                return {}
        return {}

    @property
    def entities_computed(self) -> bool:
        """Извлекались ли сущности для этого файла (NULL = нет, '{}' = пусто)."""
        return self.entities_json is not None


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


@dataclass
class FileMeta:
    """Лёгкая запись для проверки актуальности кеша (без текста)."""
    id: int
    mtime: float
    size: int
    has_text: bool
    has_entities: bool


# ═══════════════════════════════════════════════════════════════════════════════
# TTL LRU CACHE
# ═══════════════════════════════════════════════════════════════════════════════

class TTLCache:
    """LRU-кэш с TTL для снижения нагрузки на SQLite.

    Использует OrderedDict: get() перемещает ключ в конец (O(1) LRU),
    put() выталкивает первый элемент (O(1) eviction).
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
            self._cache.move_to_end(key)
            return value

    def put(self, key: str, value):
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
            elif len(self._cache) >= self._maxsize:
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
# FORK SAFETY
# ═══════════════════════════════════════════════════════════════════════════════

# Соединения, унаследованные дочерним процессом после fork(). SQLite
# запрещает использовать соединение после fork, а закрывать его в потомке
# тоже нельзя (close может выполнить checkpoint/удалить WAL родителя).
# Поэтому соединения просто "откладываются" и больше не используются.
_ABANDONED_CONNECTIONS: List[sqlite3.Connection] = []
_ALL_INDEXES: "weakref.WeakSet[FileIndex]" = weakref.WeakSet()


def _after_fork_in_child() -> None:
    for idx in list(_ALL_INDEXES):
        try:
            idx._reset_after_fork()
        except Exception:
            pass


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_after_fork_in_child)


def _py_lower(value):
    return value.lower() if isinstance(value, str) else value


# Токены так, как их видит токенайзер FTS5 unicode61: буквы и цифры
_FTS_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
_MAX_FTS_TOKENS = 32


# ═══════════════════════════════════════════════════════════════════════════════
# INDEX MANAGER
# ═══════════════════════════════════════════════════════════════════════════════

class FileIndex:
    """
    SQLite-based индекс файлов.

    Функции:
    - Кеширование извлечённого текста
    - Хранение NER сущностей и связей между ними
    - Инкрементальное обновление (по mtime/size)
    - FTS5 полнотекстовый поиск

    FileIndex() возвращает общий экземпляр (~/.astrex/index.db);
    FileIndex(db_path) — отдельный индекс в указанном файле.
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls, db_path=None):
        if db_path is not None:
            inst = super().__new__(cls)
            inst._initialized = False
            return inst
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    inst = super().__new__(cls)
                    inst._initialized = False
                    cls._instance = inst
        return cls._instance

    def __init__(self, db_path=None):
        if self._initialized:
            return

        self.db_path = Path(db_path) if db_path is not None else INDEX_DB_PATH
        self._pid = os.getpid()
        self._local = threading.local()
        self._connections: Dict[int, sqlite3.Connection] = {}  # thread ident -> conn
        self._conn_lock = threading.Lock()
        self._meta_cache = TTLCache(maxsize=50000, ttl=60.0)
        self.fts_available = True
        self.json_available = True
        _ALL_INDEXES.add(self)
        self._init_db()
        self._initialized = True

    # ═══════════════════════════════════════════════════════════════════════════
    # CONNECTIONS
    # ═══════════════════════════════════════════════════════════════════════════

    def _reset_after_fork(self) -> None:
        """Вызывается в дочернем процессе: забыть соединения родителя."""
        with self._conn_lock:
            _ABANDONED_CONNECTIONS.extend(self._connections.values())
            self._connections = {}
        self._local = threading.local()
        self._meta_cache = TTLCache(maxsize=50000, ttl=60.0)
        self._pid = os.getpid()

    def _prune_dead_threads(self) -> None:
        alive = {t.ident for t in threading.enumerate()}
        with self._conn_lock:
            dead = [ident for ident in self._connections if ident not in alive]
            for ident in dead:
                conn = self._connections.pop(ident)
                try:
                    conn.close()
                except Exception:
                    pass

    @property
    def _conn(self) -> sqlite3.Connection:
        """Thread-local connection with optimized PRAGMAs"""
        if os.getpid() != self._pid:
            self._reset_after_fork()

        conn = getattr(self._local, 'conn', None)
        if conn is None:
            self._prune_dead_threads()
            conn = sqlite3.connect(
                str(self.db_path),
                check_same_thread=False,
                timeout=30.0
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA cache_size=-64000")  # 64MB cache
            conn.execute("PRAGMA mmap_size=268435456")  # 256MB mmap I/O
            conn.execute("PRAGMA temp_store=MEMORY")
            conn.create_function("py_lower", 1, _py_lower, deterministic=True)
            self._local.conn = conn
            with self._conn_lock:
                self._connections[threading.get_ident()] = conn
        return conn

    def close(self) -> None:
        """Закрыть соединение текущего потока."""
        conn = getattr(self._local, 'conn', None)
        if conn is None:
            return
        self._local.conn = None
        if os.getpid() == self._pid:
            try:
                conn.close()
            except Exception:
                pass
        with self._conn_lock:
            for ident, c in list(self._connections.items()):
                if c is conn:
                    del self._connections[ident]

    def close_all(self) -> None:
        """Закрыть все соединения из всех потоков."""
        same_process = os.getpid() == self._pid
        with self._conn_lock:
            for conn in self._connections.values():
                if same_process:
                    try:
                        conn.close()
                    except Exception:
                        pass
                else:
                    _ABANDONED_CONNECTIONS.append(conn)
            self._connections = {}
        self._local = threading.local()
        self._meta_cache.clear()

    def __del__(self):
        try:
            self.close_all()
        except Exception:
            pass

    @contextmanager
    def _cursor(self):
        """Context manager for cursor with commit"""
        conn = self._conn
        cursor = conn.cursor()
        try:
            yield cursor
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()

    # ═══════════════════════════════════════════════════════════════════════════
    # SCHEMA
    # ═══════════════════════════════════════════════════════════════════════════

    def _init_db(self) -> None:
        """Инициализация схемы БД"""
        with self._cursor() as cur:
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

            # path уже проиндексирован ограничением UNIQUE
            cur.execute("DROP INDEX IF EXISTS idx_files_path")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_files_hash ON files(hash_md5)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_files_ext ON files(extension)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_files_mtime ON files(mtime)")

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

            # Таблица связей между сущностями (совместная встречаемость в файле)
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
            cur.execute("CREATE INDEX IF NOT EXISTS idx_links_e1 ON entity_links(entity1, entity1_type)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_links_e2 ON entity_links(entity2, entity2_type)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_links_file ON entity_links(file_id)")
            cur.execute("""
                CREATE TRIGGER IF NOT EXISTS files_ad_links AFTER DELETE ON files BEGIN
                    DELETE FROM entity_links WHERE file_id = old.id;
                END
            """)

        # FTS5 — отдельно: на сборках SQLite без FTS5 индекс продолжает работать
        try:
            with self._cursor() as cur:
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
        except sqlite3.OperationalError as e:
            self.fts_available = False
            logger.warning("SQLite FTS5 is not available (%s); falling back to LIKE search", e)

        try:
            self._conn.execute("SELECT json_valid('{}')").fetchone()
        except sqlite3.OperationalError:
            self.json_available = False

    # ═══════════════════════════════════════════════════════════════════════════
    # FILE OPERATIONS
    # ═══════════════════════════════════════════════════════════════════════════

    def get_file(self, path: str) -> Optional[IndexedFile]:
        """Получить файл по пути (полная запись, включая текст)."""
        with self._cursor() as cur:
            cur.execute("SELECT * FROM files WHERE path = ?", (path,))
            row = cur.fetchone()
            if row:
                return IndexedFile(**dict(row))
        return None

    def get_file_meta(self, path: str) -> Optional[FileMeta]:
        """Лёгкие метаданные файла (кешируются, текст не загружается)."""
        cached = self._meta_cache.get(path)
        if cached is not None:
            return cached if cached is not False else None

        with self._cursor() as cur:
            cur.execute("""
                SELECT id, mtime, size,
                       extracted_text IS NOT NULL AS has_text,
                       entities_json IS NOT NULL AS has_entities
                FROM files WHERE path = ?
            """, (path,))
            row = cur.fetchone()
        if not row:
            self._meta_cache.put(path, False)
            return None
        meta = FileMeta(id=row['id'], mtime=row['mtime'], size=row['size'],
                        has_text=bool(row['has_text']), has_entities=bool(row['has_entities']))
        self._meta_cache.put(path, meta)
        return meta

    def get_file_by_hash(self, hash_md5: str) -> Optional[IndexedFile]:
        """Получить файл по хешу"""
        with self._cursor() as cur:
            cur.execute("SELECT * FROM files WHERE hash_md5 = ?", (hash_md5,))
            row = cur.fetchone()
            if row:
                return IndexedFile(**dict(row))
        return None

    def file_needs_update(self, path: str, mtime: float, size: int) -> bool:
        """Проверить, нужно ли переиндексировать файл"""
        meta = self.get_file_meta(path)
        if meta is None:
            return True
        return meta.mtime != mtime or meta.size != size

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
        """Вставить или обновить файл.

        entities=None означает "сущности не извлекались" (NULL в БД),
        {} — "извлекались, но ничего не найдено".

        Returns:
            id записи файла
        """
        entities_json = json.dumps(entities, ensure_ascii=False) if entities is not None else None
        self._meta_cache.invalidate(path)

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

            # cursor.lastrowid после ветки UPDATE содержит id ПРЕДЫДУЩЕЙ вставки,
            # поэтому id всегда берём запросом.
            cur.execute("SELECT id FROM files WHERE path = ?", (path,))
            row = cur.fetchone()
            file_id = row[0] if row else 0

            if file_id:
                cur.execute("DELETE FROM entity_links WHERE file_id = ?", (file_id,))
                if entities:
                    self._insert_entity_links(cur, file_id, entities)
        return file_id

    def update_entities(self, path: str, entities: Dict[str, List[str]]) -> bool:
        """Сохранить сущности для уже проиндексированного файла."""
        self._meta_cache.invalidate(path)
        with self._cursor() as cur:
            cur.execute("UPDATE files SET entities_json = ? WHERE path = ?",
                        (json.dumps(entities, ensure_ascii=False), path))
            if cur.rowcount <= 0:
                return False
            cur.execute("SELECT id FROM files WHERE path = ?", (path,))
            row = cur.fetchone()
            if row:
                cur.execute("DELETE FROM entity_links WHERE file_id = ?", (row[0],))
                if entities:
                    self._insert_entity_links(cur, row[0], entities)
            return True

    @staticmethod
    def _entity_pairs(entities: Dict[str, List[str]],
                      per_type: int = 10, max_pairs: int = 300) -> List[Tuple[str, str, str, str]]:
        """Пары совместно встречающихся сущностей одного документа."""
        items: List[Tuple[str, str]] = []
        seen = set()
        for etype in sorted(entities):
            values = entities.get(etype) or []
            if not isinstance(values, list):
                continue
            for value in values[:per_type]:
                value = str(value).strip()
                key = (value.lower(), etype)
                if value and key not in seen:
                    seen.add(key)
                    items.append((value, etype))
        pairs = []
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                a, b = items[i], items[j]
                if a[0].lower() == b[0].lower():
                    continue
                if (a[1], a[0]) > (b[1], b[0]):
                    a, b = b, a
                pairs.append((a[0], a[1], b[0], b[1]))
                if len(pairs) >= max_pairs:
                    return pairs
        return pairs

    def _insert_entity_links(self, cur, file_id: int, entities: Dict[str, List[str]]) -> None:
        pairs = self._entity_pairs(entities)
        if pairs:
            cur.executemany("""
                INSERT INTO entity_links (entity1, entity1_type, entity2, entity2_type, file_id, weight)
                VALUES (?, ?, ?, ?, ?, 1.0)
            """, [(e1, t1, e2, t2, file_id) for e1, t1, e2, t2 in pairs])

    def delete_file(self, path: str) -> bool:
        """Удалить файл из индекса"""
        self._meta_cache.invalidate(path)
        with self._cursor() as cur:
            cur.execute("DELETE FROM files WHERE path = ?", (path,))
            return cur.rowcount > 0

    @staticmethod
    def _like_escape(text: str) -> str:
        return text.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')

    def delete_missing_files(self, existing_paths: set, scope: Optional[str] = None) -> int:
        """Удалить из индекса файлы, которых больше нет.

        Args:
            existing_paths: пути, которые существуют на диске
            scope: если задан — рассматриваются только записи внутри этой папки
                   (иначе индекс других папок был бы удалён целиком)

        Returns:
            число удалённых записей
        """
        conn = self._conn
        cur = conn.cursor()
        deleted = 0
        try:
            if scope:
                scope = str(scope).rstrip('/') or '/'
                prefix = scope if scope.endswith('/') else scope + '/'
                cur.execute(
                    "SELECT path FROM files WHERE path = ? OR path LIKE ? ESCAPE '\\'",
                    (scope, self._like_escape(prefix) + '%')
                )
            else:
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
                cur.execute(f"DELETE FROM files WHERE path IN ({placeholders})", batch)
                deleted += cur.rowcount
                for p in batch:
                    self._meta_cache.invalidate(p)

            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
        return deleted

    def iter_files(
        self,
        include_text: bool = False,
        with_entities_only: bool = False,
        limit: Optional[int] = None,
        batch_size: int = 500,
    ) -> Iterator[Dict[str, Any]]:
        """Перебрать все проиндексированные файлы (для экспорта, графа, отчётов).

        Это замена прежнему search_fts('*'), которое было синтаксической
        ошибкой FTS5 и всегда возвращало пустой список.
        """
        columns = "id, path, filename, extension, size, mtime, hash_md5, indexed_at, entities_json"
        if include_text:
            columns += ", extracted_text"
        sql = f"SELECT {columns} FROM files"
        if with_entities_only:
            sql += " WHERE entities_json IS NOT NULL AND entities_json != '{}'"
        sql += " ORDER BY id"
        params: tuple = ()
        if limit is not None and limit > 0:
            sql += " LIMIT ?"
            params = (int(limit),)

        cur = self._conn.cursor()
        try:
            cur.execute(sql, params)
            while True:
                rows = cur.fetchmany(batch_size)
                if not rows:
                    break
                for row in rows:
                    yield dict(row)
        finally:
            cur.close()

    def count_files(self) -> int:
        with self._cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM files")
            return cur.fetchone()[0]

    # ═══════════════════════════════════════════════════════════════════════════
    # SEARCH
    # ═══════════════════════════════════════════════════════════════════════════

    @staticmethod
    def _normalize_scores(rows: List[Dict[str, Any]], scale: float = 1.0) -> None:
        """Нормализовать bm25 в score 0.2..1.0 (лучшее совпадение = 1.0).

        bm25() в FTS5 тем меньше (отрицательнее), чем лучше совпадение.
        Прежняя формула 1/(1+|bm25|) переворачивала порядок: лучшие документы
        получали минимальный score и отсекались --min-score.
        """
        if not rows:
            return
        strengths = [max(0.0, -float(r.pop('rank', 0.0) or 0.0)) for r in rows]
        best = max(strengths)
        for row, strength in zip(rows, strengths):
            if best <= 1e-9:
                score = 1.0
            else:
                score = 0.2 + 0.8 * (strength / best)
            row['score'] = round(min(1.0, max(0.0, score * scale)), 4)

    def search_fts(
        self,
        query: str,
        limit: int = 100,
        use_prefix: bool = True,
        use_or: bool = False,
        include_text: bool = False,
    ) -> List[Dict[str, Any]]:
        """FTS5 полнотекстовый поиск.

        Args:
            query: поисковый запрос (знаки препинания допустимы)
            limit: максимум результатов
            use_prefix: искать также по префиксу слов (слово*)
            use_or: OR между словами (по умолчанию AND — все слова обязательны)
            include_text: включать полный extracted_text в результаты

        Returns:
            список dict с полями таблицы files, 'snippet' и 'score' (0..1)
        """
        if not query or not query.strip():
            return []
        limit = max(1, min(int(limit), 10000))

        if not self.fts_available:
            return self._search_like(query, limit, include_text)

        fts_query = self._build_fts_query(query, use_prefix, use_or)
        if not fts_query:
            return []

        columns = "f.id, f.path, f.filename, f.extension, f.size, f.mtime, f.hash_md5, f.indexed_at, f.entities_json"
        if include_text:
            columns += ", f.extracted_text"
        sql = f"""
            SELECT {columns},
                   bm25(files_fts, 5.0, 10.0, 1.0, 3.0) AS rank,
                   snippet(files_fts, -1, '<mark>', '</mark>', '…', 40) AS snippet
            FROM files_fts
            JOIN files f ON f.id = files_fts.rowid
            WHERE files_fts MATCH ?
            ORDER BY rank
            LIMIT ?
        """

        try:
            with self._cursor() as cur:
                cur.execute(sql, (fts_query, limit))
                results = [dict(row) for row in cur.fetchall()]
                self._normalize_scores(results)
                return results
        except sqlite3.Error as e:
            logger.warning("FTS search failed for query %r (%r): %s", query, fts_query, e)
            return []

    def _search_like(self, query: str, limit: int, include_text: bool) -> List[Dict[str, Any]]:
        """Запасной поиск без FTS5 (медленный, по подстроке)."""
        tokens = _FTS_TOKEN_RE.findall(query.lower())[:_MAX_FTS_TOKENS]
        if not tokens:
            return []
        where = " AND ".join(
            "(py_lower(extracted_text) LIKE ? ESCAPE '\\' OR py_lower(filename) LIKE ? ESCAPE '\\')"
            for _ in tokens)
        params: List[Any] = []
        for t in tokens:
            pattern = '%' + self._like_escape(t) + '%'
            params.extend([pattern, pattern])
        params.append(limit)
        with self._cursor() as cur:
            cur.execute(f"SELECT * FROM files WHERE {where} LIMIT ?", params)
            results = []
            for row in cur.fetchall():
                d = dict(row)
                text = d.get('extracted_text') or ''
                pos = text.lower().find(tokens[0])
                d['snippet'] = text[max(0, pos - 100): pos + 200] if pos >= 0 else text[:300]
                d['score'] = 0.5
                if not include_text:
                    d.pop('extracted_text', None)
                results.append(d)
            return results

    @staticmethod
    def _build_fts_query(query: str, use_prefix: bool = False, use_or: bool = False) -> Optional[str]:
        """Построить безопасный FTS5 запрос.

        Каждое слово берётся в кавычки ("слово" или "слово"*), поэтому дефисы,
        точки, '@', '*' и слова AND/OR/NOT в запросе пользователя больше не
        превращаются в синтаксис FTS5 (раньше это давало ошибку и пустой
        результат для "ООО-Ромашка", "example.com", "15.03.2024").

        Returns:
            строка запроса или None, если в запросе нет ни одного слова
        """
        tokens = _FTS_TOKEN_RE.findall(query)[:_MAX_FTS_TOKENS]
        if not tokens:
            return None

        def term(tok: str) -> str:
            return f'"{tok}"*' if use_prefix else f'"{tok}"'

        if len(tokens) == 1:
            return term(tokens[0])

        operator = " OR " if use_or else " AND "
        word_query = operator.join(term(t) for t in tokens)
        phrase = '"' + ' '.join(tokens) + '"'
        return f'{phrase} OR ({word_query})'

    def search_by_entity(
        self,
        entity: str,
        entity_type: Optional[str] = None,
        limit: int = 100,
        include_text: bool = False,
    ) -> List[Dict[str, Any]]:
        """Поиск файлов по сущности (без учёта регистра)."""
        limit = max(1, min(int(limit), 10000))
        columns = "f.id, f.path, f.filename, f.extension, f.size, f.mtime, f.indexed_at, f.entities_json"
        if include_text:
            columns += ", f.extracted_text"

        with self._cursor() as cur:
            if self.json_available:
                sql = f"""
                    SELECT DISTINCT {columns}
                    FROM files f, json_each(f.entities_json) AS t, json_each(t.value) AS v
                    WHERE f.entities_json IS NOT NULL
                      AND json_valid(f.entities_json)
                      AND py_lower(v.value) = py_lower(?)
                """
                params: List[Any] = [entity]
                if entity_type:
                    sql += " AND t.key = ?"
                    params.append(entity_type)
                sql += " LIMIT ?"
                params.append(limit)
                cur.execute(sql, params)
                return [dict(row) for row in cur.fetchall()]

            # Без JSON1: грубый фильтр LIKE + точная проверка в Python
            cur.execute(
                f"SELECT {columns} FROM files f WHERE entities_json LIKE ? ESCAPE '\\'",
                ('%' + self._like_escape(json.dumps(entity, ensure_ascii=False)[1:-1]) + '%',))
            results = []
            needle = entity.lower()
            for row in cur.fetchall():
                d = dict(row)
                try:
                    ents = json.loads(d.get('entities_json') or '{}')
                except ValueError:
                    continue
                for etype, values in ents.items():
                    if entity_type and etype != entity_type:
                        continue
                    if any(str(v).lower() == needle for v in values or []):
                        results.append(d)
                        break
                if len(results) >= limit:
                    break
            return results

    def search_by_extension(
        self,
        extensions: List[str],
        limit: int = 1000
    ) -> List[Dict[str, Any]]:
        """Поиск файлов по расширению"""
        if not extensions:
            return []
        with self._cursor() as cur:
            placeholders = ','.join('?' * len(extensions))
            cur.execute(f"""
                SELECT * FROM files
                WHERE extension IN ({placeholders})
                LIMIT ?
            """, (*extensions, max(1, int(limit))))

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
        """Получить все связи сущности (агрегированные по паре)."""
        with self._cursor() as cur:
            cur.execute("""
                SELECT entity1, entity1_type, entity2, entity2_type,
                       SUM(weight) AS weight, COUNT(DISTINCT file_id) AS files
                FROM entity_links
                WHERE py_lower(entity1) = py_lower(?) OR py_lower(entity2) = py_lower(?)
                GROUP BY entity1, entity1_type, entity2, entity2_type
                ORDER BY weight DESC
                LIMIT ?
            """, (entity, entity, max(1, int(limit))))
            return [dict(row) for row in cur.fetchall()]

    def get_graph_data(self, limit: int = 500) -> Dict[str, Any]:
        """Получить данные для построения графа связей"""
        limit = max(1, int(limit))
        with self._cursor() as cur:
            cur.execute("""
                SELECT entity, type, SUM(w) AS total_weight
                FROM (
                    SELECT entity1 AS entity, entity1_type AS type, weight AS w FROM entity_links
                    UNION ALL
                    SELECT entity2, entity2_type, weight FROM entity_links
                )
                GROUP BY entity, type
                ORDER BY total_weight DESC
                LIMIT ?
            """, (limit,))
            nodes = [dict(row) for row in cur.fetchall()]

            cur.execute("""
                SELECT entity1, entity1_type, entity2, entity2_type,
                       SUM(weight) AS weight, COUNT(*) AS count
                FROM entity_links
                GROUP BY entity1, entity1_type, entity2, entity2_type
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
            cur.execute("SELECT COUNT(*) AS count, COALESCE(SUM(size), 0) AS total FROM files")
            row = cur.fetchone()
            total_files = row['count']
            total_size = row['total'] or 0

            cur.execute("""
                SELECT extension, COUNT(*) as count
                FROM files
                GROUP BY extension
                ORDER BY count DESC
                LIMIT 20
            """)
            by_extension = [dict(r) for r in cur.fetchall()]

            cur.execute("SELECT COUNT(*) as count FROM entity_links")
            total_links = cur.fetchone()['count']

        db_size = 0
        for suffix in ('', '-wal'):
            p = Path(str(self.db_path) + suffix)
            try:
                db_size += p.stat().st_size
            except OSError:
                pass

        return {
            "total_files": total_files,
            "total_size_bytes": total_size,
            "total_size_human": self._human_size(total_size),
            "by_extension": by_extension,
            "total_entity_links": total_links,
            "db_path": str(self.db_path),
            "db_size": db_size,
            "fts_available": self.fts_available,
        }

    @staticmethod
    def _human_size(size: float) -> str:
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} PB"

    def checkpoint(self) -> None:
        """WAL checkpoint после bulk-операций — освобождает место на диске."""
        try:
            self._conn.commit()
            self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception as e:
            logger.debug("WAL checkpoint failed: %s", e)

    def vacuum(self) -> None:
        """Оптимизация БД (требует свободного места ~ размера БД)."""
        conn = self._conn
        conn.commit()
        conn.execute("VACUUM")
        conn.execute("ANALYZE")
        if self.fts_available:
            try:
                conn.execute("INSERT INTO files_fts(files_fts) VALUES ('optimize')")
                conn.commit()
            except sqlite3.Error as e:
                logger.debug("FTS optimize failed: %s", e)
        self._meta_cache.clear()


# ═══════════════════════════════════════════════════════════════════════════════
# UTILITY
# ═══════════════════════════════════════════════════════════════════════════════

def compute_file_hash(path, chunk_size: int = 1024 * 1024, algorithm: str = "md5") -> str:
    """Вычислить хеш файла (по всему содержимому, потоково)."""
    hasher = hashlib.new(algorithm)
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(chunk_size), b''):
            hasher.update(chunk)
    return hasher.hexdigest()


# Singleton instance
file_index = FileIndex()


__all__ = ['FileIndex', 'IndexedFile', 'SearchResult', 'FileMeta', 'file_index', 'compute_file_hash']
