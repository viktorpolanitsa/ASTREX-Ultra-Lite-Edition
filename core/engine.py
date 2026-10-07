#!/usr/bin/env python3
"""
ASTREX v3.0 — Scan Engine
Движок сканирования с индексацией и кешированием
"""

import json
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Set, Tuple

from .config import ENGINE_CONFIG, FILE_TYPES, NLP_CONFIG
from .index import file_index
from .graph import GraphBuilder, Graph
from .logging_setup import get_logger

logger = get_logger("astrex.engine")


# ═══════════════════════════════════════════════════════════════════════════════
# DATA CLASSES
# ═══════════════════════════════════════════════════════════════════════════════

MAX_SNIPPET_CHARS = 5000


@dataclass
class ScanResult:
    """Результат сканирования одного файла"""
    path: str
    filename: str
    matched: bool = False
    snippet: str = ""
    score: float = 0.0
    entities: Dict[str, List[str]] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None

    def to_dict(self) -> Dict:
        # Ограничиваем размер: списки сущностей и "тяжёлые" значения метаданных
        truncated_entities = {
            k: v[:20] if isinstance(v, list) else v
            for k, v in (self.entities or {}).items()
        }
        safe_meta: Dict[str, Any] = {}
        for k, v in list((self.metadata or {}).items())[:20]:
            if isinstance(v, (list, tuple)) and len(v) > 50:
                v = list(v[:50]) + [f"... (+{len(v) - 50})"]
            elif isinstance(v, dict) and len(v) > 50:
                v = dict(list(v.items())[:50])
            elif isinstance(v, str) and len(v) > 1000:
                v = v[:1000] + "…"
            safe_meta[k] = v
        return {
            'path': self.path,
            'filename': self.filename,
            'matched': self.matched,
            'snippet': (self.snippet or '')[:MAX_SNIPPET_CHARS],
            'score': self.score,
            'entities': truncated_entities,
            'metadata': safe_meta,
            'error': self.error
        }


@dataclass
class ScanStats:
    """Статистика сканирования"""
    total_files: int = 0
    processed_files: int = 0
    matched_files: int = 0
    errors: int = 0
    skipped: int = 0
    cached: int = 0
    timeouts: int = 0
    bytes_processed: int = 0
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None

    @property
    def duration_seconds(self) -> float:
        if self.start_time:
            end = self.end_time or datetime.now()
            return (end - self.start_time).total_seconds()
        return 0.0

    @property
    def files_per_second(self) -> float:
        if self.duration_seconds > 0:
            return self.processed_files / self.duration_seconds
        return 0.0

    def to_dict(self) -> Dict:
        return {
            'total_files': self.total_files,
            'processed_files': self.processed_files,
            'matched_files': self.matched_files,
            'errors': self.errors,
            'skipped': self.skipped,
            'cached': self.cached,
            'timeouts': self.timeouts,
            'bytes_processed': self.bytes_processed,
            'duration_seconds': self.duration_seconds,
            'files_per_second': self.files_per_second
        }


@dataclass
class SearchOptions:
    """Параметры для ScanEngine.search() (Python API)."""
    use_index: bool = True
    use_fuzzy: bool = True
    use_morphology: bool = True
    extract_entities: bool = True
    build_graph: bool = True
    min_score: float = 0.1
    max_results: int = 500
    max_workers: Optional[int] = None
    extensions: Optional[Set[str]] = None


# ═══════════════════════════════════════════════════════════════════════════════
# MESSAGE TYPES
# ═══════════════════════════════════════════════════════════════════════════════

class MessageType:
    STATUS = "status"
    PROGRESS = "progress"
    MATCH = "match"
    ERROR = "error"
    STATS = "stats"
    GRAPH = "graph"
    DOSSIER = "dossier"
    GPU_STATUS = "gpu_status"


# ═══════════════════════════════════════════════════════════════════════════════
# WORKER-SIDE FUNCTIONS (run in child processes)
# ═══════════════════════════════════════════════════════════════════════════════

_PLUGINS_LOADED = False


def ensure_plugins_loaded() -> None:
    """Загрузить плагины в текущем процессе (один раз), если включена автозагрузка.

    Нужно и в главном процессе: от зарегистрированных экстракторов зависит,
    какие расширения файлов собирает сканер (default_extensions).
    """
    global _PLUGINS_LOADED
    if _PLUGINS_LOADED or not ENGINE_CONFIG.plugins_autoload:
        return
    _PLUGINS_LOADED = True
    try:
        from .plugins import plugin_manager
        plugin_manager.load_all(quiet=True)
    except Exception as e:
        logger.debug(f"Plugin autoload failed: {e}")


def _worker_init(cpu_affinity: Optional[List[int]], load_plugins: bool) -> None:
    """Инициализация процесса-воркера."""
    # Не плодить потоки BLAS/OpenMP в каждом воркере
    for var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ.setdefault(var, "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    if cpu_affinity:
        try:
            os.sched_setaffinity(0, cpu_affinity)
        except (AttributeError, OSError):
            pass
    if load_plugins:
        ensure_plugins_loaded()


def _make_snippet(text: str, pos: int, length: int, window: int) -> str:
    """Фрагмент текста вокруг совпадения (совпадение всегда внутри)."""
    start = max(0, pos - window)
    end = min(len(text), pos + max(length, 1) + window)
    # Не резать слова по краям
    if start > 0:
        space = text.rfind(' ', max(0, start - 40), start)
        if space != -1:
            start = space + 1
    if end < len(text):
        space = text.find(' ', end, min(len(text), end + 40))
        if space != -1:
            end = space
    snippet = text[start:end]
    if len(snippet) > MAX_SNIPPET_CHARS:
        rel = pos - start
        half = MAX_SNIPPET_CHARS // 2
        snippet = snippet[max(0, rel - half): max(0, rel - half) + MAX_SNIPPET_CHARS]
    return snippet.strip()


def _process_file_worker(file_path_str: str, matcher_data: Dict[str, Any], opts: Dict[str, Any]) -> str:
    """Обработать один файл в процессе-воркере.

    Returns:
        JSON-строку с ScanResult.to_dict() + служебные поля
        (короткая строка безопасно передаётся через IPC).
    """
    from extractors import registry
    from .nlp import QueryMatcher, FuzzyMatcher, extract_entities

    file_path = Path(file_path_str)
    abs_path = str(file_path.absolute())
    result = ScanResult(path=abs_path, filename=file_path.name)
    info: Dict[str, Any] = {'from_cache': False, 'size': 0}

    def done() -> str:
        payload = result.to_dict()
        payload['_info'] = info
        return json.dumps(payload, ensure_ascii=False, default=str)

    try:
        stat = file_path.stat()
        size, mtime = stat.st_size, stat.st_mtime
        info['size'] = size
        result.metadata['size'] = size
        result.metadata['mtime'] = mtime

        use_cache = opts.get('use_cache', True)
        use_nlp = opts.get('use_nlp', True)
        max_chars = opts.get('max_extracted_chars', ENGINE_CONFIG.max_extracted_chars)

        text: Optional[str] = None
        entities: Optional[Dict[str, List[str]]] = None
        from_cache = False

        if use_cache:
            try:
                meta = file_index.get_file_meta(abs_path)
                if meta and meta.mtime == mtime and meta.size == size and meta.has_text:
                    cached = file_index.get_file(abs_path)
                    if cached is not None and cached.extracted_text is not None:
                        text = cached.extracted_text
                        # NULL = сущности не извлекались (не путать с "пусто")
                        entities = cached.entities if cached.entities_computed else None
                        from_cache = True
            except Exception:
                pass
        info['from_cache'] = from_cache

        if text is None:
            extraction = registry.extract(file_path)
            if extraction.error:
                result.error = extraction.error
                return done()
            text = extraction.full_text or ''
            if len(text) > max_chars:
                text = text[:max_chars]
                result.metadata['text_truncated'] = True
            for k, v in (extraction.metadata or {}).items():
                result.metadata.setdefault(k, v)

        if not text:
            return done()

        matcher = QueryMatcher.from_dict(matcher_data)
        match = matcher.find(text)
        match_kind = match.kind if match else None

        if match is None and opts.get('fuzzy_search', True):
            ok, pos, fscore = FuzzyMatcher.fuzzy_find_in_text(
                matcher.query, text, threshold=opts.get('fuzzy_threshold'))
            if ok:
                from .nlp import MatchInfo
                match = MatchInfo('fuzzy', pos, len(matcher.query), 1, fscore)
                match_kind = 'fuzzy'

        cache_entities = entities
        if match is not None:
            result.matched = True
            result.snippet = _make_snippet(text, match.pos, match.length,
                                           opts.get('context_window', ENGINE_CONFIG.context_window))
            result.metadata['match_type'] = match_kind
            result.metadata['match_count'] = match.count
            if match_kind == 'fuzzy':
                result.metadata['fuzzy_score'] = round(match.score, 1)

            if use_nlp:
                if entities is None:
                    try:
                        entities = extract_entities(text[:NLP_CONFIG.max_text_length])
                    except Exception:
                        entities = None
                result.entities = entities or {}

        # Кеширование
        if use_cache:
            try:
                if not from_cache:
                    file_index.upsert_file(
                        path=abs_path,
                        filename=file_path.name,
                        extension=file_path.suffix.lower(),
                        size=size,
                        mtime=mtime,
                        extracted_text=text,
                        entities=entities,
                    )
                elif entities is not None and cache_entities is None:
                    file_index.update_entities(abs_path, entities)
            except Exception:
                pass

        return done()

    except MemoryError:
        result.error = "MemoryError: file too large"
        return done()
    except RecursionError:
        result.error = "RecursionError: file structure too deep"
        return done()
    except Exception as e:
        result.error = f"{type(e).__name__}: {e}"
        return done()


def _index_file_worker(file_path_str: str, extract_ents: bool, max_chars: int) -> Tuple[str, str, Optional[str]]:
    """Проиндексировать один файл (в процессе-воркере).

    Returns:
        (path, status, error) где status: 'cached' | 'indexed' | 'empty' | 'error'
    """
    from extractors import registry
    from .nlp import extract_entities

    file_path = Path(file_path_str)
    path_str = str(file_path.absolute())
    try:
        stat = file_path.stat()
        if not file_index.file_needs_update(path_str, stat.st_mtime, stat.st_size):
            return (path_str, 'cached', None)

        extraction = registry.extract(file_path)
        if extraction.error:
            return (path_str, 'error', extraction.error)

        text = (extraction.full_text or '')[:max_chars]

        entities = None
        if extract_ents and text:
            try:
                entities = extract_entities(text[:NLP_CONFIG.max_text_length])
            except Exception:
                entities = None

        file_index.upsert_file(
            path=path_str,
            filename=file_path.name,
            extension=file_path.suffix.lower(),
            size=stat.st_size,
            mtime=stat.st_mtime,
            extracted_text=text if text else None,
            entities=entities
        )
        return (path_str, 'indexed' if text else 'empty', None)

    except Exception as e:
        return (path_str, 'error', f"{type(e).__name__}: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _cleanup_stale_temp_dirs() -> None:
    """Удалить временные каталоги воркеров, процессы которых уже не существуют."""
    import shutil
    from .config import TEMP_PATH
    try:
        for entry in TEMP_PATH.iterdir():
            if not entry.is_dir() or not entry.name.startswith('w'):
                continue
            try:
                pid = int(entry.name[1:])
            except ValueError:
                continue
            try:
                os.kill(pid, 0)
                alive = True
            except ProcessLookupError:
                alive = False
            except PermissionError:
                alive = True
            if not alive:
                shutil.rmtree(entry, ignore_errors=True)
    except OSError:
        pass


def _resolved_excludes(paths) -> List[str]:
    result = []
    for p in paths or ():
        if not p:
            continue
        try:
            result.append(os.path.realpath(os.path.expanduser(p)))
        except OSError:
            continue
    return result


def _is_under(path: str, roots: List[str]) -> bool:
    for root in roots:
        if path == root or path.startswith(root.rstrip('/') + '/'):
            return True
    return False


def default_extensions() -> Set[str]:
    """Расширения для сканирования: FILE_TYPES + всё, что умеют доступные
    экстракторы (включая подключённые плагины)."""
    exts = set(FILE_TYPES.all_supported)
    try:
        from extractors import registry
        exts.update(e for e in registry.supported_extensions()
                    if e.startswith('.') and e.count('.') == 1)
    except Exception:
        pass
    return exts


def collect_files(
    folder: Path,
    extensions,
    exclude_paths=None,
    skip_hidden_dirs: bool = True,
    max_file_size: int = 0,
    should_stop: Callable[[], bool] = lambda: False,
    stats: Optional[ScanStats] = None,
) -> List[Path]:
    """Собрать файлы для обработки."""
    files: List[Path] = []
    excludes = _resolved_excludes(exclude_paths)
    folder_real = os.path.realpath(str(folder))

    def on_error(err):
        logger.debug(f"Cannot read directory: {err}")

    for root, dirs, filenames in os.walk(folder_real, onerror=on_error):
        if should_stop():
            break
        kept = []
        for d in dirs:
            if skip_hidden_dirs and d.startswith('.'):
                continue
            full = os.path.join(root, d)
            if excludes and _is_under(full, excludes):
                continue
            kept.append(d)
        dirs[:] = kept

        for filename in filenames:
            ext = os.path.splitext(filename)[1].lower()
            if ext not in extensions:
                continue
            file_path = os.path.join(root, filename)
            try:
                size = os.stat(file_path).st_size
            except OSError:
                continue
            if size == 0:
                continue
            if max_file_size and size > max_file_size:
                if stats is not None:
                    stats.skipped += 1
                continue
            files.append(Path(file_path))
    return files


# ═══════════════════════════════════════════════════════════════════════════════
# SCAN ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

class ScanEngine:
    """
    Движок сканирования ASTREX.

    Features:
    - Параллельная обработка в процессах (с таймаутом и лимитом памяти на файл)
    - Кеширование извлечённого текста в SQLite
    - Инкрементальное обновление (только изменённые файлы)
    - Морфология, нечёткий поиск, NER
    - Построение графа связей
    - Real-time callbacks для GUI
    """

    def __init__(
        self,
        callback: Optional[Callable[[str, Dict], None]] = None,
        use_cache: bool = True,
        use_nlp: bool = True,
        build_graph: bool = True,
        fuzzy_search: bool = True,
        use_morphology: bool = True,
        apply_limits: bool = True,
        exclude_paths: Optional[List[str]] = None,
    ):
        self.callback = callback or self._default_callback
        self.use_cache = use_cache
        self.use_nlp = use_nlp
        self.build_graph = build_graph
        self.fuzzy_search = fuzzy_search
        self.use_morphology = use_morphology
        self.apply_limits = apply_limits
        self.exclude_paths = list(exclude_paths or [])

        self.stats = ScanStats()
        self.results: List[ScanResult] = []
        self.graph_builder = GraphBuilder() if build_graph else None

        self._stop_event = threading.Event()
        self._lock = threading.RLock()

    def _default_callback(self, msg_type: str, data: Dict) -> None:
        """Колбэк по умолчанию — журнал (а не JSON в stdout: при использовании
        ScanEngine как библиотеки вывод программы засорялся событиями)."""
        if msg_type == MessageType.ERROR:
            target = f" [{data['file']}]" if data.get('file') else ''
            logger.warning(f"{data.get('msg', '')}{target}")
        elif msg_type == MessageType.STATUS:
            logger.info(data.get('msg', ''))
        else:
            logger.debug(f"{msg_type}: {str(data)[:200]}")

    def emit(self, msg_type: str, data: Dict) -> None:
        """Emit message to callback"""
        try:
            self.callback(msg_type, data)
        except Exception as e:
            logger.debug(f"Callback failed: {e}")

    def stop(self) -> None:
        """Stop scanning"""
        self._stop_event.set()

    def is_stopped(self) -> bool:
        return self._stop_event.is_set()

    # ═══════════════════════════════════════════════════════════════════════════
    # MAIN SCAN
    # ═══════════════════════════════════════════════════════════════════════════

    def scan(
        self,
        folder: str,
        query: str,
        extensions: Optional[Set[str]] = None,
        max_workers: Optional[int] = None
    ) -> List[ScanResult]:
        """
        Выполнить сканирование.

        Args:
            folder: Директория для сканирования
            query: Поисковый запрос
            extensions: Фильтр по расширениям (None = все поддерживаемые)
            max_workers: Количество процессов-воркеров (None = авто)

        Returns:
            Список совпавших файлов, отсортированный по релевантности
        """
        from .nlp import build_query_matcher, relevance_calculator, calculate_relevance_batch
        from .resource_guard import (apply_resource_limits, calculate_safe_workers,
                                     is_ram_high, affinity_target)
        from .workerpool import WorkerPool

        self._stop_event.clear()
        self.stats = ScanStats()
        self.results = []

        if self.graph_builder:
            self.graph_builder.clear()

        folder_path = Path(folder).expanduser().resolve()
        if not folder_path.exists():
            self.emit(MessageType.ERROR, {"msg": f"Folder not found: {folder}"})
            return []
        if not folder_path.is_dir():
            self.emit(MessageType.ERROR, {"msg": f"Not a directory: {folder}"})
            return []

        matcher = build_query_matcher(query, use_morphology=self.use_morphology)
        if matcher.is_empty:
            self.emit(MessageType.ERROR, {"msg": "Query contains no searchable words"})
            return []
        forms = matcher.all_forms()
        self.emit(MessageType.STATUS, {
            "msg": f"Query: {len(matcher.terms)} term(s), {len(forms)} word forms"
        })

        # Воркеры получают ограниченный набор ядер в инициализаторе (forkserver
        # не наследует affinity главного процесса); сам главный процесс
        # ограничивается только при apply_limits (CLI), но не в веб-сервере.
        cpu_affinity = affinity_target(ENGINE_CONFIG.max_cpu_percent)
        if self.apply_limits:
            try:
                apply_resource_limits()
            except Exception:
                pass

        per_worker = ENGINE_CONFIG.est_ram_per_worker_mb if self.use_nlp else max(
            128, ENGINE_CONFIG.est_ram_per_worker_mb // 4)
        max_workers = calculate_safe_workers(max_workers, per_worker_mb=per_worker)

        # GPU status (информационно; модели в воркерах работают на CPU)
        try:
            from .gpu import gpu_info_dict
            gpu_info = gpu_info_dict()
            self.emit(MessageType.GPU_STATUS, gpu_info)
            self.emit(MessageType.STATUS, {
                "msg": f"GPU: {gpu_info['active_device']} ({gpu_info['active_backend']})"
            })
        except Exception:
            self.emit(MessageType.STATUS, {"msg": "GPU: not available, using CPU"})

        self.stats.start_time = datetime.now()

        if extensions is None:
            ensure_plugins_loaded()
            extensions = default_extensions()

        excludes = list(ENGINE_CONFIG.exclude_paths) + self.exclude_paths
        files = collect_files(
            folder_path, extensions, exclude_paths=excludes,
            skip_hidden_dirs=ENGINE_CONFIG.skip_hidden_dirs,
            max_file_size=ENGINE_CONFIG.max_file_size,
            should_stop=self.is_stopped, stats=self.stats,
        )
        self.stats.total_files = len(files)

        self.emit(MessageType.STATUS, {
            "msg": f"Files to analyze: {len(files)} (workers={min(max_workers, max(1, len(files)))})"
        })

        if not files or self.is_stopped():
            self.stats.end_time = datetime.now()
            self.emit(MessageType.STATS, self.stats.to_dict())
            return []

        _cleanup_stale_temp_dirs()

        opts = {
            'use_cache': self.use_cache,
            'use_nlp': self.use_nlp,
            'fuzzy_search': self.fuzzy_search,
            'fuzzy_threshold': ENGINE_CONFIG.fuzzy_threshold,
            'context_window': ENGINE_CONFIG.context_window,
            'max_extracted_chars': ENGINE_CONFIG.max_extracted_chars,
        }
        matcher_data = matcher.to_dict()
        tasks = ((fp, (str(fp), matcher_data, opts)) for fp in files)

        ram_limit = ENGINE_CONFIG.max_ram_percent
        ram_state = {'high': False, 'checked': 0.0}

        def can_submit(pool: WorkerPool) -> bool:
            # При нехватке RAM обрабатываем по одному файлу, но не прерываем скан
            if ram_limit <= 0:
                return True
            now = time.monotonic()
            if now - ram_state['checked'] > 0.5:
                ram_state['checked'] = now
                high = is_ram_high(ram_limit)
                if high and not ram_state['high']:
                    self.emit(MessageType.STATUS, {
                        "msg": f"RAM usage >= {ram_limit}%: throttling to one file at a time"})
                ram_state['high'] = high
            return not ram_state['high'] or pool.busy_count == 0

        last_progress = 0.0
        pool = WorkerPool(
            _process_file_worker,
            n_workers=min(max_workers, len(files)),
            initializer=_worker_init,
            initargs=(cpu_affinity, ENGINE_CONFIG.plugins_autoload),
            task_timeout=ENGINE_CONFIG.file_timeout,
            max_rss_mb=ENGINE_CONFIG.max_ram_per_worker_mb,
        )

        try:
            for res in pool.run(tasks, should_stop=self.is_stopped, can_submit=can_submit):
                self.stats.processed_files += 1

                if not res.ok:
                    self.stats.errors += 1
                    if res.kind == 'timeout':
                        self.stats.timeouts += 1
                    self.emit(MessageType.ERROR, {"msg": res.error, "file": str(res.key)})
                else:
                    self._handle_result(res.value, query, relevance_calculator)

                now = time.monotonic()
                if now - last_progress >= 0.5 or self.stats.processed_files == self.stats.total_files:
                    last_progress = now
                    self.emit(MessageType.PROGRESS, {
                        "current": self.stats.processed_files,
                        "total": self.stats.total_files,
                        "matched": self.stats.matched_files,
                        "cached": self.stats.cached,
                        "errors": self.stats.errors,
                    })
        except Exception as pool_err:
            self.emit(MessageType.ERROR, {
                "msg": f"Worker pool failure: {type(pool_err).__name__}: {pool_err}. "
                       f"Returning {len(self.results)} partial results."
            })
        finally:
            pool.terminate()

        self.stats.end_time = datetime.now()

        if self.is_stopped():
            self.emit(MessageType.STATUS, {"msg": "Scan stopped by user"})

        # Итоговая оценка релевантности (ключевые слова + SBERT при NLP)
        if self.results:
            try:
                snippets = [r.snippet for r in self.results]
                scores = calculate_relevance_batch(snippets, query, use_semantic=self.use_nlp)
                if len(scores) == len(self.results):
                    for r, score in zip(self.results, scores):
                        if r.metadata.get('match_type') == 'fuzzy':
                            score = max(score, 0.3)
                        r.score = round(max(score, 0.1), 4)
            except Exception as e:
                logger.debug(f"Batch scoring failed: {e}")

        self.results.sort(key=lambda x: x.score, reverse=True)

        self.emit(MessageType.STATS, self.stats.to_dict())

        if self.graph_builder and self.graph_builder.get_graph().nodes:
            graph = self.graph_builder.get_graph()
            self.emit(MessageType.GRAPH, graph.to_dict(max_edges=2000))

        return self.results

    def _handle_result(self, json_str: Optional[str], query: str, relevance) -> None:
        if not json_str:
            self.stats.errors += 1
            return
        try:
            result_dict = json.loads(json_str)
        except (TypeError, ValueError):
            self.stats.errors += 1
            return

        info = result_dict.pop('_info', {}) or {}
        if info.get('from_cache'):
            self.stats.cached += 1
        self.stats.bytes_processed += int(info.get('size') or 0)

        if result_dict.get('error'):
            self.stats.errors += 1

        if not result_dict.get('matched'):
            return

        sr = ScanResult(
            path=result_dict.get('path', ''),
            filename=result_dict.get('filename', ''),
            matched=True,
            snippet=result_dict.get('snippet', ''),
            entities=result_dict.get('entities') or {},
            metadata=result_dict.get('metadata') or {},
            error=result_dict.get('error'),
        )
        # Предварительная оценка (без нейросетей) — для потоковой выдачи
        try:
            sr.score = round(max(relevance.keyword_score(sr.snippet, query), 0.1), 4)
        except Exception:
            sr.score = 0.5
        with self._lock:
            self.results.append(sr)
            self.stats.matched_files += 1

        if self.graph_builder and sr.entities:
            try:
                self.graph_builder.build_from_entities(sr.entities, sr.snippet, sr.path)
            except Exception as e:
                logger.debug(f"Graph build failed for {sr.path}: {e}")

        self.emit(MessageType.MATCH, sr.to_dict())

    # ═══════════════════════════════════════════════════════════════════════════
    # PYTHON API
    # ═══════════════════════════════════════════════════════════════════════════

    def search(self, query: str, folder: str,
               options: Optional[SearchOptions] = None) -> Iterator[ScanResult]:
        """Найти файлы по запросу (удобная обёртка над scan()).

        Пример:
            for match in engine.search("договор", "/data", SearchOptions(min_score=0.2)):
                print(match.filename, match.score)
        """
        options = options or SearchOptions()
        self.use_cache = options.use_index
        self.fuzzy_search = options.use_fuzzy
        self.use_morphology = options.use_morphology
        self.use_nlp = options.extract_entities
        if options.build_graph and self.graph_builder is None:
            self.graph_builder = GraphBuilder()
        elif not options.build_graph:
            self.graph_builder = None

        results = self.scan(folder, query, extensions=options.extensions,
                            max_workers=options.max_workers)
        count = 0
        for r in results:
            if r.score < options.min_score:
                continue
            yield r
            count += 1
            if options.max_results and count >= options.max_results:
                break

    # ═══════════════════════════════════════════════════════════════════════════
    # UTILITIES
    # ═══════════════════════════════════════════════════════════════════════════

    def _collect_files(self, folder: Path, extensions: Set[str]) -> List[Path]:
        """Collect files for scanning (совместимость)"""
        return collect_files(folder, extensions,
                             exclude_paths=list(ENGINE_CONFIG.exclude_paths) + self.exclude_paths,
                             skip_hidden_dirs=ENGINE_CONFIG.skip_hidden_dirs,
                             max_file_size=ENGINE_CONFIG.max_file_size,
                             should_stop=self.is_stopped, stats=self.stats)

    def get_graph(self) -> Optional[Graph]:
        """Get built graph"""
        if self.graph_builder:
            return self.graph_builder.get_graph()
        return None

    def get_stats(self) -> ScanStats:
        return self.stats

    def get_results(self) -> List[ScanResult]:
        return self.results


# ═══════════════════════════════════════════════════════════════════════════════
# INCREMENTAL INDEXER
# ═══════════════════════════════════════════════════════════════════════════════

class IncrementalIndexer:
    """
    Инкрементальный индексатор.
    Обновляет индекс только для изменённых файлов (параллельно).
    """

    def __init__(self, callback: Optional[Callable] = None, apply_limits: bool = True):
        self.callback = callback
        self.apply_limits = apply_limits
        self.stats = ScanStats()
        self._stop_event = threading.Event()

    def emit(self, msg_type: str, data: Dict) -> None:
        try:
            if self.callback:
                self.callback(msg_type, data)
            elif msg_type == MessageType.ERROR:
                logger.warning(f"{data.get('msg', '')} [{data.get('file', '')}]")
            elif msg_type == MessageType.STATUS:
                logger.info(data.get('msg', ''))
            else:
                logger.debug(f"{msg_type}: {str(data)[:200]}")
        except Exception as e:
            logger.debug(f"Callback failed: {e}")

    def stop(self) -> None:
        self._stop_event.set()

    def is_stopped(self) -> bool:
        return self._stop_event.is_set()

    def index_folder(
        self,
        folder: str,
        extensions: Optional[Set[str]] = None,
        extract_entities: bool = True,
        max_workers: Optional[int] = None,
        cleanup: bool = False,
    ) -> ScanStats:
        """
        Индексировать папку (без поиска).

        Args:
            cleanup: удалить из индекса записи о файлах этой папки,
                     которых больше нет на диске
        """
        from .resource_guard import apply_resource_limits, calculate_safe_workers, affinity_target
        from .workerpool import WorkerPool

        self._stop_event.clear()
        folder_path = Path(folder).expanduser().resolve()

        if extensions is None:
            ensure_plugins_loaded()
            extensions = default_extensions()

        self.stats = ScanStats()
        self.stats.start_time = datetime.now()

        self.emit(MessageType.STATUS, {"msg": f"Collecting files in {folder_path}..."})

        files = collect_files(
            folder_path, extensions, exclude_paths=ENGINE_CONFIG.exclude_paths,
            skip_hidden_dirs=ENGINE_CONFIG.skip_hidden_dirs,
            max_file_size=ENGINE_CONFIG.max_file_size,
            should_stop=self.is_stopped, stats=self.stats,
        )
        self.stats.total_files = len(files)

        if cleanup and not self.is_stopped():
            existing = {str(fp.absolute()) for fp in files}
            deleted = file_index.delete_missing_files(existing, scope=str(folder_path))
            self.emit(MessageType.STATUS, {"msg": f"Removed {deleted} stale index entries"})

        cpu_affinity = affinity_target(ENGINE_CONFIG.max_cpu_percent)
        if self.apply_limits:
            try:
                apply_resource_limits()
            except Exception:
                pass
        per_worker = ENGINE_CONFIG.est_ram_per_worker_mb if extract_entities else max(
            128, ENGINE_CONFIG.est_ram_per_worker_mb // 4)
        max_workers = calculate_safe_workers(max_workers, per_worker_mb=per_worker)

        self.emit(MessageType.STATUS, {
            "msg": f"Files to index: {len(files)} (workers={min(max_workers, max(1, len(files)))})"})

        if not files:
            self.stats.end_time = datetime.now()
            self.emit(MessageType.STATS, self.stats.to_dict())
            return self.stats

        _cleanup_stale_temp_dirs()

        tasks = ((fp, (str(fp), extract_entities, ENGINE_CONFIG.max_extracted_chars)) for fp in files)
        pool = WorkerPool(
            _index_file_worker,
            n_workers=min(max_workers, len(files)),
            initializer=_worker_init,
            initargs=(cpu_affinity, ENGINE_CONFIG.plugins_autoload),
            task_timeout=ENGINE_CONFIG.file_timeout,
            max_rss_mb=ENGINE_CONFIG.max_ram_per_worker_mb,
        )
        last_progress = 0.0
        try:
            for res in pool.run(tasks, should_stop=self.is_stopped):
                self.stats.processed_files += 1
                if not res.ok:
                    self.stats.errors += 1
                    if res.kind == 'timeout':
                        self.stats.timeouts += 1
                    self.emit(MessageType.ERROR, {"msg": res.error, "file": str(res.key)})
                else:
                    _path, status, error = res.value
                    if status == 'cached':
                        self.stats.cached += 1
                    elif status == 'error':
                        self.stats.errors += 1
                        logger.debug(f"Index error for {_path}: {error}")
                    elif status == 'indexed':
                        self.stats.matched_files += 1  # здесь: число проиндексированных
                    elif status == 'empty':
                        self.stats.skipped += 1        # файл без текста (двоичные данные, пустой)

                now = time.monotonic()
                if now - last_progress >= 1.0 or self.stats.processed_files == self.stats.total_files:
                    last_progress = now
                    self.emit(MessageType.PROGRESS, {
                        "current": self.stats.processed_files,
                        "total": self.stats.total_files,
                        "indexed": self.stats.matched_files,
                        "cached": self.stats.cached,
                        "skipped": self.stats.skipped,
                        "errors": self.stats.errors,
                    })
        except Exception as pool_err:
            self.emit(MessageType.ERROR, {"msg": f"Index pool failure: {pool_err}"})
        finally:
            pool.terminate()

        self.stats.end_time = datetime.now()

        try:
            file_index.checkpoint()
        except Exception:
            pass

        self.emit(MessageType.STATS, self.stats.to_dict())
        return self.stats


# ═══════════════════════════════════════════════════════════════════════════════
# EXPORTS
# ═══════════════════════════════════════════════════════════════════════════════

__all__ = [
    'ScanEngine', 'ScanResult', 'ScanStats', 'SearchOptions', 'MessageType',
    'IncrementalIndexer', 'collect_files', 'default_extensions', 'ensure_plugins_loaded',
]
