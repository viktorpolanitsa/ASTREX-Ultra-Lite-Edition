#!/usr/bin/env python3
"""
ASTREX v3.0 — Scan Engine
Движок сканирования с индексацией и кешированием
"""

import os
import sys
import json
import threading
import queue
import traceback
from pathlib import Path
from datetime import datetime
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Set, Generator, Callable, Any
from dataclasses import dataclass, field

from .config import ENGINE_CONFIG, FILE_TYPES, NLP_CONFIG
from .index import file_index
from .nlp import extract_entities, calculate_relevance, calculate_relevance_batch, expand_query, _trigram_jaccard
from .graph import GraphBuilder, Graph
from extractors import registry, ExtractionResult


# ═══════════════════════════════════════════════════════════════════════════════
# DATA CLASSES
# ═══════════════════════════════════════════════════════════════════════════════

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
        # Truncate entity lists to avoid IPC MemoryError when pickling large results
        truncated_entities = {
            k: v[:20] if isinstance(v, list) else v
            for k, v in (self.entities or {}).items()
        }
        # Keep only lightweight metadata fields to reduce pickle size
        safe_meta = dict(list((self.metadata or {}).items())[:15])
        return {
            'path': self.path,
            'filename': self.filename,
            'matched': self.matched,
            'snippet': self.snippet[:500] if self.snippet else '',
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
    bytes_processed: int = 0
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    
    @property
    def duration_seconds(self) -> float:
        if self.start_time and self.end_time:
            return (self.end_time - self.start_time).total_seconds()
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
            'bytes_processed': self.bytes_processed,
            'duration_seconds': self.duration_seconds,
            'files_per_second': self.files_per_second
        }


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
# SCAN ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

def _process_file_worker(
    file_path_str: str,
    query_lower: str,
    query_forms: Set[str],
    use_nlp: bool,
    use_cache: bool,
    fuzzy_search: bool,
    max_ram_per_worker_mb: int = 0,
) -> Optional[str]:
    """
    Top-level function for ProcessPoolExecutor.
    Runs in a separate process — if it crashes, the main process survives.

    Returns a path to a temp JSON file containing ScanResult.to_dict(), or None.
    Using a file path (~60 bytes) instead of the full dict avoids
    IPC MemoryError: even a severely OOM worker can pickle a short string.
    The main process reads and deletes the temp file.
    """
    file_path = Path(file_path_str)

    result = ScanResult(
        path=str(file_path.absolute()),
        filename=file_path.name
    )

    try:
        stat = file_path.stat()
        size = stat.st_size
        mtime = stat.st_mtime

        result.metadata['size'] = size
        result.metadata['mtime'] = mtime

        # Check cache
        text = None
        entities = None
        from_cache = False

        if use_cache:
            try:
                cached = file_index.get_file(str(file_path.absolute()))
                if cached and not file_index.file_needs_update(
                    str(file_path.absolute()), mtime, size
                ):
                    text = cached.extracted_text
                    entities = cached.entities
                    from_cache = True
            except Exception:
                pass

        # Extract text if not cached
        if text is None:
            try:
                extraction = registry.extract(file_path)
            except Exception as e:
                result.error = f"Extraction failed: {type(e).__name__}: {e}"
                return _serialize_result(result.to_dict())

            if extraction.error:
                result.error = extraction.error
                return _serialize_result(result.to_dict())

            text = extraction.full_text
            result.metadata.update(extraction.metadata)

        if not text:
            return _serialize_result(result.to_dict())

        # Quick check: does text contain any query form?
        text_lower = text.lower()

        match_found = False
        match_pos = -1

        # Check all morphological forms (exact substring match — fastest)
        for form in query_forms:
            pos = text_lower.find(form)
            if pos != -1:
                match_found = True
                if match_pos == -1 or pos < match_pos:
                    match_pos = pos
                break

        # Morphology sub-string search (partial word matches via stems)
        if not match_found and fuzzy_search:
            try:
                from .nlp import morph_analyzer
                stem = morph_analyzer.normalize(query_lower)
                if stem and len(stem) >= 3 and stem != query_lower:
                    pos = text_lower.find(stem)
                    if pos != -1:
                        match_found = True
                        match_pos = pos
            except Exception:
                pass

        # Fuzzy check if no exact match — sliding window approach
        if not match_found and fuzzy_search:
            try:
                from .nlp import FuzzyMatcher
                if FuzzyMatcher.is_available():
                    # Sliding window fuzzy search — more reliable than split('.')
                    search_text = text[:20000]
                    window_size = 200
                    step_size = 100
                    best_ratio = 0
                    best_pos = -1

                    for win_start in range(0, len(search_text) - len(query_lower), step_size):
                        win_end = min(win_start + window_size, len(search_text))
                        window = search_text[win_start:win_end].lower()
                        ratio = FuzzyMatcher.wratio(query_lower, window)
                        if ratio > best_ratio:
                            best_ratio = ratio
                            best_pos = win_start
                        if best_ratio >= ENGINE_CONFIG.fuzzy_threshold:
                            break

                    if best_ratio >= ENGINE_CONFIG.fuzzy_threshold:
                        match_found = True
                        match_pos = best_pos
                else:
                    # Fallback: trigram Jaccard when rapidfuzz is unavailable
                    search_text = text[:20000].lower()
                    window_size = 200
                    step_size = 100
                    best_ratio = 0
                    best_pos = -1

                    for win_start in range(0, len(search_text) - len(query_lower), step_size):
                        win_end = min(win_start + window_size, len(search_text))
                        window = search_text[win_start:win_end]
                        ratio = _trigram_jaccard(query_lower, window) * 100
                        if ratio > best_ratio:
                            best_ratio = ratio
                            best_pos = win_start
                        if best_ratio >= ENGINE_CONFIG.fuzzy_threshold:
                            break

                    if best_ratio >= ENGINE_CONFIG.fuzzy_threshold:
                        match_found = True
                        match_pos = best_pos
            except Exception:
                pass

        if not match_found:
            # Cache the extracted text for future searches
            if use_cache and not from_cache:
                try:
                    _cache_file_standalone(file_path, size, mtime, text, None)
                except Exception:
                    pass
            return _serialize_result(result.to_dict())

        # Match found
        result.matched = True

        # Extract snippet
        window = ENGINE_CONFIG.context_window
        start = max(0, match_pos - window)
        end = min(len(text), match_pos + len(query_lower) + window)
        result.snippet = text[start:end].strip()

        # NLP analysis
        if use_nlp:
            try:
                result.score = calculate_relevance(result.snippet, query_lower)
            except Exception:
                result.score = 0.5

            try:
                if entities is None:
                    entities = extract_entities(text[:NLP_CONFIG.max_text_length])
                result.entities = entities
            except Exception:
                pass
        else:
            query_count = text_lower.count(query_lower)
            result.score = min(1.0, 0.5 + query_count * 0.1)

        # Cache result
        if use_cache and not from_cache:
            try:
                _cache_file_standalone(file_path, size, mtime, text, entities)
            except Exception:
                pass

        return _serialize_result(result.to_dict())

    except MemoryError:
        result.error = "MemoryError: file too large"
        return _serialize_result(result.to_dict())
    except RecursionError:
        result.error = "RecursionError: file structure too deep"
        return _serialize_result(result.to_dict())
    except Exception as e:
        result.error = f"{type(e).__name__}: {e}"
        return _serialize_result(result.to_dict())


def _index_file_worker(file_path_str: str, extract_ents: bool) -> tuple:
    """Top-level indexing worker for ProcessPoolExecutor."""
    try:
        file_path = Path(file_path_str)
        stat = file_path.stat()
        path_str = str(file_path.absolute())

        # Check if needs update
        if not file_index.file_needs_update(path_str, stat.st_mtime, stat.st_size):
            return (True, False)

        # Extract text
        extraction = registry.extract(file_path)
        if extraction.error:
            return (False, True)

        text = extraction.full_text

        # Extract entities
        entities = None
        if extract_ents and text:
            try:
                entities = extract_entities(text[:NLP_CONFIG.max_text_length])
            except Exception:
                pass

        # Cache
        file_index.upsert_file(
            path=path_str,
            filename=file_path.name,
            extension=file_path.suffix.lower(),
            size=stat.st_size,
            mtime=stat.st_mtime,
            extracted_text=text[:500000] if text else None,
            entities=entities
        )

        return (False, False)

    except Exception:
        return (False, True)


def _cache_file_standalone(file_path, size, mtime, text, entities):
    """Standalone cache function for use in worker processes."""
    try:
        file_index.upsert_file(
            path=str(file_path.absolute()),
            filename=file_path.name,
            extension=file_path.suffix.lower(),
            size=size,
            mtime=mtime,
            extracted_text=text[:500000] if text else None,
            entities=entities
        )
    except Exception:
        pass


def _serialize_result(result_dict: Optional[Dict]) -> Optional[str]:
    """
    Serialize result dict to a JSON string for IPC.

    Workers return a JSON string through IPC instead of the full dict.
    JSON strings are lightweight and always picklable.

    Returns None on complete failure (main process treats it as no result).
    """
    if result_dict is None:
        return None
    try:
        return json.dumps(result_dict, ensure_ascii=False, default=str)
    except Exception:
        return None


class ScanEngine:
    """
    Движок сканирования ASTREX.
    
    Features:
    - Многопоточное сканирование
    - Кеширование извлечённого текста в SQLite
    - Инкрементальное обновление (только изменённые файлы)
    - NLP анализ с морфологией и fuzzy search
    - Построение графа связей
    - Real-time callbacks для GUI
    """
    
    def __init__(
        self,
        callback: Optional[Callable[[str, Dict], None]] = None,
        use_cache: bool = True,
        use_nlp: bool = True,
        build_graph: bool = True,
        fuzzy_search: bool = True
    ):
        self.callback = callback or self._default_callback
        self.use_cache = use_cache
        self.use_nlp = use_nlp
        self.build_graph = build_graph
        self.fuzzy_search = fuzzy_search
        
        self.stats = ScanStats()
        self.results: List[ScanResult] = []
        self.graph_builder = GraphBuilder() if build_graph else None
        
        self._stop_event = threading.Event()
        self._lock = threading.RLock()
        self._batch_queue: queue.Queue = queue.Queue()
        
        # Expanded query forms (morphology)
        self._query_forms: Set[str] = set()
    
    def _default_callback(self, msg_type: str, data: Dict) -> None:
        """Default callback - print JSON to stdout"""
        print(json.dumps({"type": msg_type, **data}, ensure_ascii=False), flush=True)
    
    def emit(self, msg_type: str, data: Dict) -> None:
        """Emit message to callback"""
        try:
            self.callback(msg_type, data)
        except Exception:
            pass
    
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
            max_workers: Количество потоков (None = auto)
        
        Returns:
            Список результатов
        """
        self._stop_event.clear()
        self.stats = ScanStats()
        self.results = []
        
        if self.graph_builder:
            self.graph_builder.clear()
        
        folder_path = Path(folder).resolve()
        if not folder_path.exists():
            self.emit(MessageType.ERROR, {"msg": f"Folder not found: {folder}"})
            return []
        
        # Prepare query
        query_lower = query.lower()
        
        # Expand query with morphological forms
        if self.fuzzy_search:
            self._query_forms = expand_query(query)
            self.emit(MessageType.STATUS, {
                "msg": f"Query expanded: {len(self._query_forms)} forms"
            })
        else:
            self._query_forms = {query_lower}
        
        # Apply resource limits (CPU affinity, GPU memory cap)
        try:
            from .resource_guard import apply_resource_limits, calculate_safe_workers
            apply_resource_limits()
            max_workers = calculate_safe_workers(max_workers)
        except Exception:
            if max_workers is None:
                cpu_count = os.cpu_count() or 4
                max_workers = max(1, cpu_count)

        self.emit(MessageType.STATUS, {
            "msg": f"ASTREX: Initializing scan (workers={max_workers})"
        })

        # GPU status
        try:
            from .gpu import gpu_info_dict
            gpu_info = gpu_info_dict()
            self.emit(MessageType.GPU_STATUS, gpu_info)
            self.emit(MessageType.STATUS, {
                "msg": f"GPU: {gpu_info['active_device']} ({gpu_info['active_backend']})"
            })
        except Exception:
            self.emit(MessageType.STATUS, {"msg": "GPU: not available, using CPU"})
        
        # Collect files
        self.stats.start_time = datetime.now()
        
        if extensions is None:
            extensions = FILE_TYPES.all_supported
        
        files = self._collect_files(folder_path, extensions)
        self.stats.total_files = len(files)
        
        self.emit(MessageType.STATUS, {
            "msg": f"Files to analyze: {len(files)}"
        })
        
        if not files:
            self.stats.end_time = datetime.now()
            return []
        
        # Import resource guard helpers
        try:
            from .resource_guard import wait_for_ram, get_ram_usage_percent
            _has_guard = True
        except Exception:
            _has_guard = False

        ram_limit = ENGINE_CONFIG.max_ram_percent
        spawn_delay = ENGINE_CONFIG.worker_spawn_delay
        worker_mem_limit = ENGINE_CONFIG.max_ram_per_worker_mb

        # Scan with process pool — throttled submission to prevent OOM.
        # NOTE: we intentionally avoid `with ProcessPoolExecutor` because its
        # __exit__ calls shutdown(wait=True) which re-raises BrokenProcessPool
        # after a worker OOM-crashes during IPC pickling. Instead we manage
        # the executor explicitly and always shut down with wait=False.
        executor = ProcessPoolExecutor(max_workers=max_workers)
        futures = {}

        try:
            for fp in files:
                if self.is_stopped():
                    break

                # Throttle: wait if RAM is too high
                if _has_guard and ram_limit > 0:
                    ram_pct = get_ram_usage_percent()
                    if ram_pct >= ram_limit:
                        self.emit(MessageType.STATUS, {
                            "msg": f"RAM {ram_pct:.0f}% >= {ram_limit}%, pausing..."
                        })
                        if not wait_for_ram(ram_limit, poll_interval=2.0, timeout=120.0):
                            self.emit(MessageType.ERROR, {
                                "msg": "RAM limit exceeded too long, stopping scan"
                            })
                            break

                try:
                    future = executor.submit(
                        _process_file_worker,
                        str(fp),
                        query_lower,
                        self._query_forms,
                        self.use_nlp,
                        self.use_cache,
                        self.fuzzy_search,
                        worker_mem_limit,
                    )
                    futures[future] = fp
                except Exception:
                    # Pool already broken — stop submitting
                    self.stats.errors += 1
                    break

                # Small delay between submissions to avoid spike
                if spawn_delay > 0:
                    import time
                    time.sleep(spawn_delay)

            for i, future in enumerate(as_completed(futures)):
                if self.is_stopped():
                    for f in futures:
                        f.cancel()
                    break

                try:
                    # Worker returns a JSON string (or None).
                    json_str = future.result(timeout=120)
                    result_dict = None

                    if json_str:
                        try:
                            result_dict = json.loads(json_str)
                        except Exception:
                            result_dict = None

                    if result_dict:
                        self.stats.processed_files += 1

                        is_matched = result_dict.get('matched', False)
                        has_error = result_dict.get('error')

                        if is_matched:
                            sr = ScanResult(
                                path=result_dict.get('path', ''),
                                filename=result_dict.get('filename', ''),
                                matched=True,
                                snippet=result_dict.get('snippet', ''),
                                score=result_dict.get('score', 0.0),
                                entities=result_dict.get('entities', {}),
                                metadata=result_dict.get('metadata', {}),
                                error=has_error,
                            )
                            self.results.append(sr)
                            self.stats.matched_files += 1

                            # Build graph in main process (not picklable)
                            if self.graph_builder and sr.entities:
                                try:
                                    self.graph_builder.build_from_entities(
                                        sr.entities, sr.snippet, sr.path
                                    )
                                except Exception:
                                    pass

                            self.emit(MessageType.MATCH, result_dict)

                        if has_error:
                            self.stats.errors += 1

                except Exception as e:
                    self.stats.errors += 1
                    self.emit(MessageType.ERROR, {
                        "msg": f"Worker error: {type(e).__name__}: {e}",
                        "file": str(futures.get(future, ''))
                    })

                # Progress update
                if i % 50 == 0 or i == len(futures) - 1:
                    self.emit(MessageType.PROGRESS, {
                        "current": self.stats.processed_files,
                        "total": self.stats.total_files,
                        "matched": self.stats.matched_files,
                        "cached": self.stats.cached
                    })

        except Exception as pool_err:
            # BrokenProcessPool — a worker died (OOM during IPC pickling).
            # Return partial results gathered so far instead of failing.
            self.emit(MessageType.ERROR, {
                "msg": f"Worker pool broken (OOM in subprocess): {pool_err}. "
                       f"Returning {len(self.results)} partial results. "
                       f"Try --no-nlp or --workers 1 to reduce memory pressure."
            })
        finally:
            # Always shut down without waiting — avoids re-raising BrokenProcessPool
            try:
                executor.shutdown(wait=False, cancel_futures=True)
            except Exception:
                pass
        
        # Finalize
        self.stats.end_time = datetime.now()
        
        # Batch re-score with SBERT if available and NLP enabled
        if self.use_nlp and self.results:
            try:
                snippets = [r.snippet[:2000] for r in self.results]
                batch_scores = calculate_relevance_batch(snippets, query)
                if batch_scores and len(batch_scores) == len(self.results):
                    for r, score in zip(self.results, batch_scores):
                        r.score = score
            except Exception:
                pass  # fallback to per-file scores

        # Sort results by score
        self.results.sort(key=lambda x: x.score, reverse=True)
        
        # Emit final stats
        self.emit(MessageType.STATS, self.stats.to_dict())
        
        # Emit graph if built
        if self.graph_builder and self.graph_builder.get_graph().nodes:
            graph = self.graph_builder.get_graph()
            self.emit(MessageType.GRAPH, graph.to_dict())
        
        return self.results
    
    # ═══════════════════════════════════════════════════════════════════════════
    # FILE COLLECTION
    # ═══════════════════════════════════════════════════════════════════════════
    
    def _collect_files(
        self, 
        folder: Path, 
        extensions: Set[str]
    ) -> List[Path]:
        """Collect files for scanning"""
        files = []
        
        try:
            for root, dirs, filenames in os.walk(folder):
                # Skip hidden directories
                dirs[:] = [d for d in dirs if not d.startswith('.')]
                
                for filename in filenames:
                    if self.is_stopped():
                        break
                    
                    # Check extension
                    ext = Path(filename).suffix.lower()
                    if ext not in extensions:
                        continue
                    
                    file_path = Path(root) / filename
                    
                    # Check size
                    try:
                        size = file_path.stat().st_size
                        if size > ENGINE_CONFIG.max_file_size:
                            self.stats.skipped += 1
                            continue
                        if size == 0:
                            continue
                    except OSError:
                        continue
                    
                    files.append(file_path)
        
        except PermissionError:
            pass
        
        return files
    
    # _process_file and _cache_file removed — replaced by
    # top-level _process_file_worker() for ProcessPoolExecutor isolation
    
    # ═══════════════════════════════════════════════════════════════════════════
    # UTILITIES
    # ═══════════════════════════════════════════════════════════════════════════
    
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
    Обновляет индекс только для изменённых файлов.
    """
    
    def __init__(self, callback: Optional[Callable] = None):
        self.callback = callback
        self.stats = ScanStats()
        self._lock = threading.Lock()
    
    def emit(self, msg_type: str, data: Dict) -> None:
        if self.callback:
            self.callback(msg_type, data)
        else:
            print(json.dumps({"type": msg_type, **data}, ensure_ascii=False), flush=True)
    
    def index_folder(
        self,
        folder: str,
        extensions: Optional[Set[str]] = None,
        extract_entities: bool = True
    ) -> ScanStats:
        """
        Индексировать папку (без поиска).
        Только извлечение текста и сущностей для кеширования.
        """
        folder_path = Path(folder).resolve()
        
        if extensions is None:
            extensions = FILE_TYPES.all_supported
        
        self.stats = ScanStats()
        self.stats.start_time = datetime.now()
        
        self.emit(MessageType.STATUS, {"msg": "Building index..."})
        
        # Collect files
        files = []
        for root, dirs, filenames in os.walk(folder_path):
            dirs[:] = [d for d in dirs if not d.startswith('.')]
            
            for filename in filenames:
                ext = Path(filename).suffix.lower()
                if ext in extensions:
                    files.append(Path(root) / filename)
        
        self.stats.total_files = len(files)
        self.emit(MessageType.STATUS, {"msg": f"Files to index: {len(files)}"})
        
        # Process files (with resource limits)
        try:
            from .resource_guard import calculate_safe_workers, apply_resource_limits
            apply_resource_limits()
            max_workers = calculate_safe_workers()
        except Exception:
            cpu_count = os.cpu_count() or 4
            max_workers = max(1, cpu_count)

        self.emit(MessageType.STATUS, {"msg": f"Indexing with {max_workers} workers"})

        executor = ProcessPoolExecutor(max_workers=max_workers)
        futures = {}
        try:
            for fp in files:
                try:
                    futures[executor.submit(_index_file_worker, str(fp), extract_entities)] = fp
                except Exception:
                    self.stats.errors += 1

            for i, future in enumerate(as_completed(futures)):
                try:
                    cached, error = future.result(timeout=120)
                    self.stats.processed_files += 1
                    if cached:
                        self.stats.cached += 1
                    if error:
                        self.stats.errors += 1

                except Exception:
                    self.stats.errors += 1

                if i % 100 == 0:
                    self.emit(MessageType.PROGRESS, {
                        "current": self.stats.processed_files,
                        "total": self.stats.total_files
                    })
        except Exception as pool_err:
            self.emit(MessageType.ERROR, {
                "msg": f"Index pool broken: {pool_err}"
            })
        finally:
            try:
                executor.shutdown(wait=False, cancel_futures=True)
            except Exception:
                pass
        
        self.stats.end_time = datetime.now()

        # Checkpoint WAL to reclaim disk space after bulk inserts
        try:
            file_index.checkpoint()
        except Exception:
            pass

        self.emit(MessageType.STATS, self.stats.to_dict())

        return self.stats
    
    # _index_file removed — replaced by top-level _index_file_worker()


# ═══════════════════════════════════════════════════════════════════════════════
# EXPORTS
# ═══════════════════════════════════════════════════════════════════════════════

__all__ = [
    'ScanEngine', 'ScanResult', 'ScanStats', 'MessageType',
    'IncrementalIndexer'
]
