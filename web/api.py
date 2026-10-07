#!/usr/bin/env python3
"""
ASTREX v3.0 — Web API
FastAPI REST API для веб-интерфейса.

Безопасность:
- все эндпоинты (кроме /api/health) требуют токен: заголовок
  "Authorization: Bearer <token>" или "X-API-Key: <token>"
  (для WebSocket — параметр ?token=). Токен: переменная ASTREX_API_TOKEN
  или файл ~/.astrex/.secret_key (создаётся автоматически, права 0600);
- при работе на 127.0.0.1 принимаются только запросы с Host: localhost/127.0.0.1
  (защита от DNS-rebinding);
- системные и чувствительные каталоги исключаются из сканирования на всех
  уровнях обхода, а не только для корневой папки запроса.
"""

import asyncio
import hmac
import json
import logging
import os
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

try:
    from fastapi import (FastAPI, HTTPException, BackgroundTasks, WebSocket, WebSocketDisconnect,
                         Depends, Request, Query, APIRouter)
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import JSONResponse
    from pydantic import BaseModel, Field
    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import VERSION, WEB_CONFIG, ASTREX_HOME, FILE_TYPES, get_secret_key
from core.index import file_index
from core.engine import ScanEngine, MessageType

_logger = logging.getLogger("astrex.web")

_LOOPBACK = {'127.0.0.1', 'localhost', '::1', '[::1]'}

# Разрешённые значения заголовка Host (None — любые). Для сервера на loopback
# ограничиваем, чтобы сайт с DNS-rebinding не мог обращаться к API.
_ALLOWED_HOSTS: Optional[set] = (
    set(WEB_CONFIG.allowed_hosts) | {'localhost', '127.0.0.1'}
    if WEB_CONFIG.host in _LOOPBACK else None
)


def _host_only(host_header: str) -> str:
    host_header = (host_header or '').strip().lower()
    if host_header.startswith('['):
        end = host_header.find(']')
        return host_header[:end + 1] if end != -1 else host_header
    return host_header.split(':', 1)[0]


def _blocked_roots() -> List[str]:
    # Каталог ASTREX (индекс с текстом всех файлов, API-токен) закрыт всегда,
    # даже если ASTREX_HOME перенесён из ~/.astrex
    roots = [os.path.realpath(str(ASTREX_HOME))]
    for p in WEB_CONFIG.blocked_paths:
        try:
            roots.append(os.path.realpath(os.path.expanduser(p)))
        except OSError:
            continue
    return roots


def _is_blocked(path: str, roots: List[str]) -> bool:
    for root in roots:
        if path == root or path.startswith(root.rstrip('/') + '/'):
            return True
    return False


# ═══════════════════════════════════════════════════════════════════════════════
# MODELS
# ═══════════════════════════════════════════════════════════════════════════════

if FASTAPI_AVAILABLE:
    class ScanRequest(BaseModel):
        query: str = Field(..., min_length=1, description="Search query")
        folder: str = Field(..., description="Folder to scan")
        use_cache: bool = Field(True, description="Use index cache")
        use_nlp: bool = Field(True, description="Enable NLP analysis")
        fuzzy_search: bool = Field(True, description="Enable fuzzy matching")
        use_morphology: bool = Field(True, description="Enable morphological expansion")
        min_score: float = Field(0.1, ge=0, le=1, description="Minimum relevance score")
        limit: int = Field(100, ge=1, le=10000, description="Max results")
        workers: Optional[int] = Field(None, ge=1, le=128, description="Worker processes")

    class SearchRequest(BaseModel):
        query: str = Field(..., min_length=1)
        limit: int = Field(50, ge=1, le=1000)
        min_score: float = Field(0.1, ge=0, le=1)

    class EntityRequest(BaseModel):
        text: str = Field(..., min_length=1)

    class ScanResponse(BaseModel):
        task_id: str
        status: str
        message: str

    class RelevanceRequest(BaseModel):
        text: str = Field(..., min_length=1)
        query: str = Field(..., min_length=1)

    class SummarizeRequest(BaseModel):
        text: str = Field(..., min_length=1)
        max_length: int = Field(500, ge=50, le=5000)

    class AnalyzeRequest(BaseModel):
        text: str = Field(..., min_length=1)


# ═══════════════════════════════════════════════════════════════════════════════
# AUTH
# ═══════════════════════════════════════════════════════════════════════════════

def _token_from(headers, query_params) -> Optional[str]:
    auth = headers.get('authorization', '')
    if auth.lower().startswith('bearer '):
        return auth[7:].strip()
    key = headers.get('x-api-key')
    if key:
        return key.strip()
    return query_params.get('token')


def _token_valid(token: Optional[str]) -> bool:
    if not WEB_CONFIG.enable_auth:
        return True
    if not token:
        return False
    return hmac.compare_digest(token.encode(), get_secret_key().encode())


# ═══════════════════════════════════════════════════════════════════════════════
# APP
# ═══════════════════════════════════════════════════════════════════════════════

if FASTAPI_AVAILABLE:
    def require_token(request: Request) -> None:
        if not _token_valid(_token_from(request.headers, request.query_params)):
            raise HTTPException(status_code=401, detail="Invalid or missing API token",
                                headers={"WWW-Authenticate": "Bearer"})

    app = FastAPI(
        title=f"ASTREX v{VERSION} API",
        description="Intelligence System API",
        version=VERSION
    )

    @app.middleware("http")
    async def host_guard(request: Request, call_next):
        if _ALLOWED_HOSTS is not None and _host_only(request.headers.get('host', '')) not in _ALLOWED_HOSTS:
            return JSONResponse({"detail": "Invalid host header"}, status_code=400)
        return await call_next(request)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(WEB_CONFIG.cors_origins),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    api = APIRouter(dependencies=[Depends(require_token)])

    # State
    scan_tasks: Dict[str, Dict] = {}
    scan_engines: Dict[str, ScanEngine] = {}
    active_websockets: List[WebSocket] = []
    _scan_slots = threading.BoundedSemaphore(max(1, WEB_CONFIG.max_concurrent_scans))
    _MAX_STORED_TASKS = 50
    _MAX_LIVE_RESULTS = 10000

    # ═══════════════════════════════════════════════════════════════════════════
    # WEBSOCKET
    # ═══════════════════════════════════════════════════════════════════════════

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        if not _token_valid(_token_from(websocket.headers, websocket.query_params)):
            await websocket.close(code=1008)
            return
        await websocket.accept()
        active_websockets.append(websocket)
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        except Exception as e:
            _logger.debug(f"WebSocket error: {e}")
        finally:
            if websocket in active_websockets:
                active_websockets.remove(websocket)

    async def broadcast_message(message: Dict):
        """Broadcast message to all connected websockets, removing dead connections."""
        for ws in list(active_websockets):
            try:
                await ws.send_json(message)
            except Exception as e:
                _logger.debug(f"WebSocket send failed: {e}")
                if ws in active_websockets:
                    active_websockets.remove(ws)

    # ═══════════════════════════════════════════════════════════════════════════
    # SCAN ENDPOINTS
    # ═══════════════════════════════════════════════════════════════════════════

    def _evict_old_tasks() -> None:
        if len(scan_tasks) <= _MAX_STORED_TASKS:
            return
        done = [(k, v) for k, v in scan_tasks.items()
                if v.get('status') in ('completed', 'failed', 'cancelled')]
        done.sort(key=lambda kv: kv[1].get('completed_at') or kv[1].get('started_at', ''))
        for old_id, _ in done[:len(scan_tasks) - _MAX_STORED_TASKS]:
            scan_tasks.pop(old_id, None)

    @api.post("/api/scan", response_model=ScanResponse)
    async def start_scan(request: ScanRequest, background_tasks: BackgroundTasks):
        """Start a new scan task"""
        folder_path = Path(request.folder).expanduser().resolve()
        if not folder_path.exists():
            raise HTTPException(status_code=400, detail="Folder not found")
        if not folder_path.is_dir():
            raise HTTPException(status_code=400, detail="Path is not a directory")
        if _is_blocked(str(folder_path), _blocked_roots()):
            raise HTTPException(status_code=403, detail="Access to this path is restricted")

        if not _scan_slots.acquire(blocking=False):
            raise HTTPException(status_code=429,
                                detail=f"Too many concurrent scans (max {WEB_CONFIG.max_concurrent_scans})")

        task_id = f"scan_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
        scan_tasks[task_id] = {
            "status": "running",
            "query": request.query,
            "folder": str(folder_path),
            "started_at": datetime.now().isoformat(),
            "progress": {"current": 0, "total": 0},
            "results": [],
            "stats": None,
        }
        _evict_old_tasks()
        background_tasks.add_task(run_scan_task, task_id, request, str(folder_path))

        return ScanResponse(task_id=task_id, status="running",
                            message=f"Scan started for query: {request.query}")

    async def run_scan_task(task_id: str, request: ScanRequest, folder: str):
        """Background scan task (сканирование в отдельном потоке)"""
        loop = asyncio.get_running_loop()
        task = scan_tasks[task_id]

        def callback(msg_type: str, data: Dict):
            if msg_type == MessageType.PROGRESS:
                task["progress"] = data
            elif msg_type == MessageType.MATCH:
                if len(task["results"]) < _MAX_LIVE_RESULTS:
                    task["results"].append(data)
            elif msg_type == MessageType.STATS:
                task["stats"] = data
            if msg_type == MessageType.GRAPH:
                return  # граф большой — не рассылаем по WebSocket
            try:
                asyncio.run_coroutine_threadsafe(
                    broadcast_message({"task_id": task_id, "type": msg_type, "data": data}), loop)
            except Exception:
                pass

        try:
            engine = ScanEngine(
                callback=callback,
                use_cache=request.use_cache,
                use_nlp=request.use_nlp,
                fuzzy_search=request.fuzzy_search,
                use_morphology=request.use_morphology,
                apply_limits=False,
                exclude_paths=_blocked_roots(),
            )
            scan_engines[task_id] = engine
            results = await loop.run_in_executor(
                None, lambda: engine.scan(folder=folder, query=request.query, max_workers=request.workers))

            filtered = [r for r in results if r.score >= request.min_score][:request.limit]
            task["results"] = [r.to_dict() for r in filtered]
            task["total_matches"] = len(results)
            if task.get("status") != "cancelled":
                task["status"] = "completed"
        except Exception as e:
            task["status"] = "failed"
            task["error"] = f"{type(e).__name__}: {e}"
        finally:
            task["completed_at"] = datetime.now().isoformat()
            scan_engines.pop(task_id, None)
            _scan_slots.release()

    @api.get("/api/scan/{task_id}")
    def get_scan_status(task_id: str):
        """Get scan task status (results — via /api/scan/{task_id}/results)"""
        task = scan_tasks.get(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found")
        summary = {k: v for k, v in task.items() if k != "results"}
        summary["result_count"] = len(task.get("results", []))
        return summary

    @api.delete("/api/scan/{task_id}")
    def cancel_scan(task_id: str):
        """Cancel a running scan"""
        task = scan_tasks.get(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found")
        if task.get("status") == "running":
            task["status"] = "cancelled"
            engine = scan_engines.get(task_id)
            if engine:
                engine.stop()
        return {"status": task["status"]}

    @api.get("/api/scan/{task_id}/results")
    def get_scan_results(task_id: str,
                         offset: int = Query(0, ge=0),
                         limit: int = Query(50, ge=1, le=1000)):
        """Get paginated scan results"""
        task = scan_tasks.get(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found")
        results = list(task.get("results", []))
        return {"total": len(results), "offset": offset, "limit": limit,
                "results": results[offset:offset + limit]}

    # ═══════════════════════════════════════════════════════════════════════════
    # SEARCH ENDPOINTS
    # ═══════════════════════════════════════════════════════════════════════════

    @api.post("/api/search")
    def search_index(request: SearchRequest):
        """Search in indexed files (FTS5 + оценка релевантности)"""
        from core.nlp import calculate_relevance_batch

        rows = file_index.search_fts(request.query, request.limit * 2)
        snippets = [(row.get('snippet') or '').replace('<mark>', '').replace('</mark>', '') for row in rows]
        scores = calculate_relevance_batch(snippets, request.query) if rows else []

        results = []
        for row, rel in zip(rows, scores):
            score = max(rel, row.get('score', 0) * 0.9)
            if score < request.min_score:
                continue
            entities = {}
            if row.get('entities_json'):
                try:
                    entities = json.loads(row['entities_json'])
                except ValueError:
                    pass
            results.append({
                'path': row.get('path', ''),
                'filename': row.get('filename', ''),
                'snippet': row.get('snippet', ''),
                'score': round(score, 4),
                'entities': entities,
            })
        results.sort(key=lambda x: x['score'], reverse=True)
        return {"query": request.query, "total": len(results), "results": results[:request.limit]}

    @api.get("/api/search/entity/{entity}")
    def search_by_entity(entity: str, entity_type: Optional[str] = None,
                         limit: int = Query(50, ge=1, le=1000)):
        """Search files by entity"""
        results = file_index.search_by_entity(entity, entity_type, limit)
        return {"entity": entity, "entity_type": entity_type, "total": len(results), "results": results}

    # ═══════════════════════════════════════════════════════════════════════════
    # NLP ENDPOINTS
    # ═══════════════════════════════════════════════════════════════════════════

    @api.post("/api/nlp/entities")
    def extract_entities_endpoint(request: EntityRequest):
        """Extract entities from text"""
        from core.nlp import extract_entities
        return {"text_length": len(request.text), "entities": extract_entities(request.text[:50000])}

    @api.post("/api/nlp/relevance")
    def calculate_relevance_endpoint(request: RelevanceRequest):
        """Calculate relevance score"""
        from core.nlp import calculate_relevance
        return {"score": calculate_relevance(request.text[:10000], request.query)}

    # ═══════════════════════════════════════════════════════════════════════════
    # GRAPH ENDPOINTS
    # ═══════════════════════════════════════════════════════════════════════════

    @api.get("/api/graph")
    def get_graph(limit: int = Query(500, ge=1, le=5000)):
        """Get entity relationship graph (co-occurrence of entities in indexed files)"""
        return file_index.get_graph_data(limit)

    @api.get("/api/graph/entity/{entity}")
    def get_entity_connections(entity: str, limit: int = Query(50, ge=1, le=1000)):
        """Get connections for a specific entity"""
        try:
            return {"entity": entity, "connections": file_index.get_entity_connections(entity, limit)}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Graph query failed: {e}")

    # ═══════════════════════════════════════════════════════════════════════════
    # INDEX ENDPOINTS
    # ═══════════════════════════════════════════════════════════════════════════

    @api.get("/api/index/stats")
    def get_index_stats():
        """Get index statistics"""
        return file_index.get_stats()

    @api.post("/api/index/vacuum")
    def vacuum_index():
        """Optimize index database"""
        file_index.vacuum()
        return {"status": "ok", "message": "Index optimized"}

    # ═══════════════════════════════════════════════════════════════════════════
    # SYSTEM ENDPOINTS
    # ═══════════════════════════════════════════════════════════════════════════

    @api.get("/api/status")
    def get_status(load_models: bool = False):
        """System status (load_models=true — загрузить модели для проверки SBERT/spaCy)"""
        nlp_status: Dict[str, Any] = {}
        try:
            from core.nlp import morph_analyzer, entity_extractor, relevance_calculator
            nlp_status = {"morphology": morph_analyzer.available,
                          "morphology_backend": morph_analyzer.backend}
            if load_models:
                nlp_status["spacy"] = entity_extractor.nlp is not None
                nlp_status["sbert"] = relevance_calculator.sbert_model is not None
        except Exception as e:
            _logger.debug(f"NLP status check failed: {e}")

        vector_status: Dict[str, Any] = {}
        llm_status: Dict[str, Any] = {}
        try:
            from ml import vector_store, get_llm_status
            vector_status = vector_store.get_stats()
            llm_status = get_llm_status()
        except Exception as e:
            _logger.debug(f"ML status check failed: {e}")

        return {
            "version": VERSION,
            "home": str(ASTREX_HOME),
            "index": file_index.get_stats(),
            "nlp": nlp_status,
            "vectors": vector_status,
            "llm": llm_status,
            "supported_formats": sorted(FILE_TYPES.all_supported),
        }

    @app.get("/api/health")
    async def health_check():
        """Health check endpoint (без аутентификации)"""
        return {"status": "healthy", "timestamp": datetime.now().isoformat()}

    # ═══════════════════════════════════════════════════════════════════════════
    # LLM ENDPOINTS
    # ═══════════════════════════════════════════════════════════════════════════

    @api.post("/api/llm/summarize")
    def llm_summarize(request: SummarizeRequest):
        """Summarize text using local LLM"""
        from ml import summarize_text
        result = summarize_text(request.text, request.max_length)
        if result.error:
            raise HTTPException(status_code=503, detail=result.error)
        return {"summary": result.text, "model": result.model, "tokens": result.tokens_used}

    @api.post("/api/llm/analyze")
    def llm_analyze(request: AnalyzeRequest):
        """Analyze connections using local LLM"""
        from ml import analyze_connections
        result = analyze_connections(request.text)
        if result.error:
            raise HTTPException(status_code=503, detail=result.error)
        return {"analysis": result.text, "model": result.model}

    app.include_router(api)


# ═══════════════════════════════════════════════════════════════════════════════
# RUN
# ═══════════════════════════════════════════════════════════════════════════════

def run_server(host: str = None, port: int = None, debug: bool = None) -> int:
    """Run the web server. Returns process exit code."""
    if not FASTAPI_AVAILABLE:
        print("FastAPI not installed. Run: pip install fastapi uvicorn", file=sys.stderr)
        return 1
    try:
        import uvicorn
    except ImportError:
        print("uvicorn not installed. Run: pip install uvicorn", file=sys.stderr)
        return 1

    global _ALLOWED_HOSTS
    host = host or WEB_CONFIG.host
    port = WEB_CONFIG.port if port is None else port
    reload = WEB_CONFIG.debug if debug is None else debug
    _ALLOWED_HOSTS = (set(WEB_CONFIG.allowed_hosts) | {'localhost', '127.0.0.1'}
                      if host in _LOOPBACK else None)

    if host not in _LOOPBACK and not WEB_CONFIG.enable_auth:
        print("Refusing to listen on a non-loopback address with authentication disabled "
              "(set web.enable_auth: true)", file=sys.stderr)
        return 1

    if WEB_CONFIG.enable_auth:
        token_source = ("ASTREX_API_TOKEN" if os.environ.get('ASTREX_API_TOKEN')
                        else str(ASTREX_HOME / '.secret_key'))
        get_secret_key()
        print(f"ASTREX API: http://{host}:{port}  (token: {token_source}; "
              f"send 'Authorization: Bearer <token>')", file=sys.stderr)

    if reload:
        uvicorn.run("web.api:app", host=host, port=port, reload=True)
    else:
        uvicorn.run(app, host=host, port=port)
    return 0


if __name__ == "__main__":
    sys.exit(run_server())
