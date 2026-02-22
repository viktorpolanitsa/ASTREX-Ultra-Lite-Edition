#!/usr/bin/env python3
"""
ASTREX v3.0 — Web API
FastAPI REST API для веб-интерфейса
"""

import os
import json
import asyncio
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any
from dataclasses import dataclass

try:
    from fastapi import FastAPI, HTTPException, BackgroundTasks, WebSocket, WebSocketDisconnect
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import JSONResponse, FileResponse
    from fastapi.staticfiles import StaticFiles
    from pydantic import BaseModel, Field
    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False

import sys
import logging
sys.path.insert(0, str(Path(__file__).parent.parent))

_logger = logging.getLogger("astrex.web")

from core.config import VERSION, WEB_CONFIG, ASTREX_HOME, FILE_TYPES
from core.index import file_index
from core.engine import ScanEngine, ScanStats, MessageType
from core.nlp import extract_entities, calculate_relevance
from core.graph import GraphAnalyzer


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
        min_score: float = Field(0.1, ge=0, le=1, description="Minimum relevance score")
        limit: int = Field(100, ge=1, le=10000, description="Max results")
        workers: Optional[int] = Field(None, ge=1, le=128, description="Worker threads")

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

    class SearchResult(BaseModel):
        path: str
        filename: str
        snippet: str
        score: float
        entities: Dict[str, List[str]]


# ═══════════════════════════════════════════════════════════════════════════════
# APP
# ═══════════════════════════════════════════════════════════════════════════════

if FASTAPI_AVAILABLE:
    app = FastAPI(
        title=f"ASTREX v{VERSION} API",
        description="Intelligence System API",
        version=VERSION
    )

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(WEB_CONFIG.cors_origins),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # State
    scan_tasks: Dict[str, Dict] = {}
    scan_engines: Dict[str, ScanEngine] = {}
    active_websockets: List[WebSocket] = []


    # ═══════════════════════════════════════════════════════════════════════════
    # WEBSOCKET
    # ═══════════════════════════════════════════════════════════════════════════

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        await websocket.accept()
        active_websockets.append(websocket)
        
        try:
            while True:
                data = await websocket.receive_text()
                # Handle incoming messages if needed
        except WebSocketDisconnect:
            if websocket in active_websockets:
                active_websockets.remove(websocket)


    async def broadcast_message(message: Dict):
        """Broadcast message to all connected websockets, removing dead connections."""
        dead = []
        for ws in active_websockets:
            try:
                await ws.send_json(message)
            except Exception as e:
                _logger.debug(f"WebSocket send failed: {e}")
                dead.append(ws)
        for ws in dead:
            if ws in active_websockets:
                active_websockets.remove(ws)


    # ═══════════════════════════════════════════════════════════════════════════
    # SCAN ENDPOINTS
    # ═══════════════════════════════════════════════════════════════════════════

    @app.post("/api/scan", response_model=ScanResponse)
    async def start_scan(request: ScanRequest, background_tasks: BackgroundTasks):
        """Start a new scan task"""
        
        # Validate folder
        folder_path = Path(request.folder).resolve()
        if not folder_path.exists():
            raise HTTPException(status_code=400, detail="Folder not found")
        if not folder_path.is_dir():
            raise HTTPException(status_code=400, detail="Path is not a directory")
        # Block sensitive system directories
        blocked = (
            '/proc', '/sys', '/dev', '/boot', '/etc', '/root',
            '/run', '/var/log', '/var/run', '/usr/lib', '/usr/bin',
            '/usr/sbin', '/bin', '/sbin', '/lib', '/lib64',
        )
        folder_str = str(folder_path)
        if any(folder_str == b or folder_str.startswith(b + '/') for b in blocked):
            raise HTTPException(status_code=403, detail="Access to this path is restricted")
        
        # Generate task ID
        task_id = f"scan_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
        
        # Initialize task state
        scan_tasks[task_id] = {
            "status": "running",
            "query": request.query,
            "folder": request.folder,
            "started_at": datetime.now().isoformat(),
            "progress": {"current": 0, "total": 0},
            "results": [],
            "stats": None
        }
        
        # Start background task
        background_tasks.add_task(
            run_scan_task,
            task_id,
            request
        )
        
        return ScanResponse(
            task_id=task_id,
            status="running",
            message=f"Scan started for query: {request.query}"
        )


    async def run_scan_task(task_id: str, request: ScanRequest):
        """Background scan task"""
        loop = asyncio.get_running_loop()

        def callback(msg_type: str, data: Dict):
            # Update task state
            if msg_type == MessageType.PROGRESS:
                scan_tasks[task_id]["progress"] = data
            elif msg_type == MessageType.MATCH:
                scan_tasks[task_id]["results"].append(data)
            elif msg_type == MessageType.STATS:
                scan_tasks[task_id]["stats"] = data

            # Broadcast to websockets (thread-safe)
            try:
                asyncio.run_coroutine_threadsafe(broadcast_message({
                    "task_id": task_id,
                    "type": msg_type,
                    "data": data
                }), loop)
            except Exception:
                pass

        try:
            engine = ScanEngine(
                callback=callback,
                use_cache=request.use_cache,
                use_nlp=request.use_nlp,
                fuzzy_search=request.fuzzy_search
            )
            scan_engines[task_id] = engine

            # Run blocking scan in thread pool to avoid blocking the event loop
            results = await loop.run_in_executor(
                None,
                lambda: engine.scan(
                    folder=request.folder,
                    query=request.query,
                    max_workers=request.workers
                )
            )
            
            # Filter by score and limit
            filtered = [
                r for r in results 
                if r.score >= request.min_score
            ][:request.limit]
            
            scan_tasks[task_id]["status"] = "completed"
            scan_tasks[task_id]["results"] = [r.to_dict() for r in filtered]
            scan_tasks[task_id]["completed_at"] = datetime.now().isoformat()
            scan_engines.pop(task_id, None)

        except Exception as e:
            scan_tasks[task_id]["status"] = "failed"
            scan_tasks[task_id]["error"] = str(e)
            scan_engines.pop(task_id, None)

        finally:
            # Evict oldest completed/failed tasks beyond the cap to prevent unbounded growth
            _MAX_STORED_TASKS = 50
            if len(scan_tasks) > _MAX_STORED_TASKS:
                done = [
                    (k, v) for k, v in scan_tasks.items()
                    if v.get('status') in ('completed', 'failed', 'cancelled')
                ]
                done.sort(key=lambda kv: kv[1].get('completed_at', ''))
                for old_id, _ in done[:len(done) // 2]:
                    scan_tasks.pop(old_id, None)


    @app.get("/api/scan/{task_id}")
    async def get_scan_status(task_id: str):
        """Get scan task status"""
        if task_id not in scan_tasks:
            raise HTTPException(status_code=404, detail="Task not found")
        
        return scan_tasks[task_id]


    @app.delete("/api/scan/{task_id}")
    async def cancel_scan(task_id: str):
        """Cancel a running scan"""
        if task_id not in scan_tasks:
            raise HTTPException(status_code=404, detail="Task not found")
        
        scan_tasks[task_id]["status"] = "cancelled"
        engine = scan_engines.pop(task_id, None)
        if engine:
            engine.stop()
        return {"status": "cancelled"}


    @app.get("/api/scan/{task_id}/results")
    async def get_scan_results(
        task_id: str,
        offset: int = 0,
        limit: int = 50
    ):
        """Get paginated scan results"""
        if task_id not in scan_tasks:
            raise HTTPException(status_code=404, detail="Task not found")

        offset = max(0, offset)
        limit = max(1, min(limit, 1000))

        results = scan_tasks[task_id]["results"]

        return {
            "total": len(results),
            "offset": offset,
            "limit": limit,
            "results": results[offset:offset + limit]
        }


    # ═══════════════════════════════════════════════════════════════════════════
    # SEARCH ENDPOINTS
    # ═══════════════════════════════════════════════════════════════════════════

    @app.post("/api/search")
    async def search_index(request: SearchRequest):
        """Search in indexed files"""
        
        results = file_index.search_fts(request.query, request.limit * 2)
        
        # Calculate relevance for results
        scored_results = []
        for row in results:
            text = row.get('extracted_text', '') or ''
            score = calculate_relevance(text[:5000], request.query)
            
            if score >= request.min_score:
                entities = {}
                if row.get('entities_json'):
                    try:
                        entities = json.loads(row['entities_json'])
                    except Exception:
                        pass
                
                scored_results.append({
                    'path': row.get('path', ''),
                    'filename': row.get('filename', ''),
                    'snippet': row.get('snippet', text[:500]),
                    'score': score,
                    'entities': entities
                })
        
        # Sort by score
        scored_results.sort(key=lambda x: x['score'], reverse=True)
        
        return {
            "query": request.query,
            "total": len(scored_results),
            "results": scored_results[:request.limit]
        }


    @app.get("/api/search/entity/{entity}")
    async def search_by_entity(entity: str, entity_type: Optional[str] = None, limit: int = 50):
        """Search files by entity"""
        
        results = file_index.search_by_entity(entity, entity_type, limit)
        
        return {
            "entity": entity,
            "entity_type": entity_type,
            "total": len(results),
            "results": results
        }


    # ═══════════════════════════════════════════════════════════════════════════
    # NLP ENDPOINTS
    # ═══════════════════════════════════════════════════════════════════════════

    @app.post("/api/nlp/entities")
    async def extract_entities_endpoint(request: EntityRequest):
        """Extract entities from text"""
        
        entities = extract_entities(request.text[:50000])
        
        return {
            "text_length": len(request.text),
            "entities": entities
        }


    class RelevanceRequest(BaseModel):
        text: str = Field(..., min_length=1)
        query: str = Field(..., min_length=1)

    @app.post("/api/nlp/relevance")
    async def calculate_relevance_endpoint(request: RelevanceRequest):
        """Calculate relevance score"""

        score = calculate_relevance(request.text[:10000], request.query)

        return {"score": score}


    # ═══════════════════════════════════════════════════════════════════════════
    # GRAPH ENDPOINTS
    # ═══════════════════════════════════════════════════════════════════════════

    @app.get("/api/graph")
    async def get_graph():
        """Get entity relationship graph"""
        
        graph_data = file_index.get_graph_data()
        
        return graph_data


    @app.get("/api/graph/entity/{entity}")
    async def get_entity_connections(entity: str, limit: int = 50):
        """Get connections for a specific entity"""
        try:
            connections = file_index.get_entity_connections(entity, limit)
            return {
                "entity": entity,
                "connections": connections
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Graph query failed: {e}")


    # ═══════════════════════════════════════════════════════════════════════════
    # INDEX ENDPOINTS
    # ═══════════════════════════════════════════════════════════════════════════

    @app.get("/api/index/stats")
    async def get_index_stats():
        """Get index statistics"""
        return file_index.get_stats()


    @app.post("/api/index/vacuum")
    async def vacuum_index():
        """Optimize index database"""
        file_index.vacuum()
        return {"status": "ok", "message": "Index optimized"}


    # ═══════════════════════════════════════════════════════════════════════════
    # SYSTEM ENDPOINTS
    # ═══════════════════════════════════════════════════════════════════════════

    @app.get("/api/status")
    async def get_status():
        """Get system status"""
        
        # NLP status
        nlp_status = {}
        try:
            from core.nlp import morph_analyzer, entity_extractor, relevance_calculator
            nlp_status = {
                "morphology": morph_analyzer.available,
                "spacy": entity_extractor.nlp is not None,
                "sbert": relevance_calculator.sbert_model is not None
            }
        except Exception as e:
            _logger.debug(f"NLP status check failed: {e}")

        # Vector store status
        vector_status = {}
        try:
            from ml import vector_store
            vector_status = vector_store.get_stats()
        except Exception as e:
            _logger.debug(f"Vector store status check failed: {e}")

        # LLM status
        llm_status = {}
        try:
            from ml import get_llm_status
            llm_status = get_llm_status()
        except Exception as e:
            _logger.debug(f"LLM status check failed: {e}")
        
        return {
            "version": VERSION,
            "home": str(ASTREX_HOME),
            "index": file_index.get_stats(),
            "nlp": nlp_status,
            "vectors": vector_status,
            "llm": llm_status,
            "supported_formats": list(FILE_TYPES.all_supported)
        }


    @app.get("/api/health")
    async def health_check():
        """Health check endpoint"""
        return {"status": "healthy", "timestamp": datetime.now().isoformat()}


    # ═══════════════════════════════════════════════════════════════════════════
    # LLM ENDPOINTS
    # ═══════════════════════════════════════════════════════════════════════════

    class SummarizeRequest(BaseModel):
        text: str = Field(..., min_length=1)
        max_length: int = Field(500, ge=50, le=5000)

    @app.post("/api/llm/summarize")
    async def llm_summarize(request: SummarizeRequest):
        """Summarize text using local LLM"""
        try:
            from ml import summarize_text
            result = summarize_text(request.text, request.max_length)
            
            if result.error:
                raise HTTPException(status_code=503, detail=result.error)
            
            return {
                "summary": result.text,
                "model": result.model,
                "tokens": result.tokens_used
            }
        except ImportError:
            raise HTTPException(status_code=503, detail="LLM module not available")


    class AnalyzeRequest(BaseModel):
        text: str = Field(..., min_length=1)

    @app.post("/api/llm/analyze")
    async def llm_analyze(request: AnalyzeRequest):
        """Analyze connections using local LLM"""
        try:
            from ml import analyze_connections
            result = analyze_connections(request.text)
            
            if result.error:
                raise HTTPException(status_code=503, detail=result.error)
            
            return {
                "analysis": result.text,
                "model": result.model
            }
        except ImportError:
            raise HTTPException(status_code=503, detail="LLM module not available")


# ═══════════════════════════════════════════════════════════════════════════════
# RUN
# ═══════════════════════════════════════════════════════════════════════════════

def run_server(host: str = None, port: int = None, debug: bool = None):
    """Run the web server"""
    if not FASTAPI_AVAILABLE:
        print("FastAPI not installed. Run: pip install fastapi uvicorn")
        return
    
    import uvicorn
    
    uvicorn.run(
        "web.api:app",
        host=host or WEB_CONFIG.host,
        port=port or WEB_CONFIG.port,
        reload=debug if debug is not None else WEB_CONFIG.debug
    )


if __name__ == "__main__":
    run_server()
