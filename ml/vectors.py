#!/usr/bin/env python3
"""
ASTREX v3.0 — Vector Store
Векторное хранилище для семантического поиска (ChromaDB или NumPy-fallback).

Модель эмбеддингов и хранилище создаются лениво — при первом обращении.
"""

import json
import logging
import re
import threading
from pathlib import Path
from typing import List, Dict, Any, Optional
from dataclasses import dataclass

from core.config import VECTOR_CONFIG, VECTOR_DB_PATH

logger = logging.getLogger("astrex.vectors")


# ═══════════════════════════════════════════════════════════════════════════════
# DATA CLASSES
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class VectorSearchResult:
    """Результат векторного поиска"""
    id: str
    text: str
    score: float
    metadata: Dict[str, Any]


# ═══════════════════════════════════════════════════════════════════════════════
# TEXT CHUNKER
# ═══════════════════════════════════════════════════════════════════════════════

class TextChunker:
    """Разбиение текста на чанки по предложениям (размер — в словах).

    Предложения длиннее chunk_size режутся по словам, поэтому чанк никогда
    не превышает окно модели (MiniLM видит только ~128 токенов).
    """

    def __init__(self, chunk_size: Optional[int] = None, overlap: Optional[int] = None):
        self.chunk_size = max(10, int(chunk_size or VECTOR_CONFIG.chunk_size))
        self.overlap = max(0, min(int(overlap if overlap is not None else VECTOR_CONFIG.chunk_overlap),
                                  self.chunk_size // 2))

    def _split_sentences(self, text: str) -> List[str]:
        """Разбить текст на предложения (длинные — на куски по chunk_size слов)."""
        sentences = re.split(r'(?<=[.!?…])\s+|\n{2,}', text)
        result: List[str] = []
        for s in sentences:
            words = s.split()
            if not words:
                continue
            if len(words) <= self.chunk_size:
                result.append(' '.join(words))
            else:
                step = self.chunk_size - self.overlap
                for i in range(0, len(words), step):
                    part = words[i:i + self.chunk_size]
                    if part:
                        result.append(' '.join(part))
                    if i + self.chunk_size >= len(words):
                        break
        return result

    def chunk_text(self, text: str) -> List[str]:
        """Разбить текст на чанки с перекрытием"""
        if not text or not text.strip():
            return []

        chunks: List[str] = []
        current: List[str] = []
        current_len = 0

        for sentence in self._split_sentences(text):
            n = len(sentence.split())
            if current and current_len + n > self.chunk_size:
                chunks.append(' '.join(current))
                overlap_sentences: List[str] = []
                overlap_words = 0
                for s in reversed(current):
                    w = len(s.split())
                    if overlap_words + w > self.overlap:
                        break
                    overlap_sentences.insert(0, s)
                    overlap_words += w
                current = overlap_sentences
                current_len = overlap_words
            current.append(sentence)
            current_len += n

        if current:
            chunks.append(' '.join(current))
        return chunks


# ═══════════════════════════════════════════════════════════════════════════════
# EMBEDDING MODEL
# ═══════════════════════════════════════════════════════════════════════════════

class EmbeddingModel:
    """Модель для создания эмбеддингов (ленивая загрузка)."""

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
        self._model = None
        self._loaded = False
        self._load_lock = threading.Lock()
        self.device_info = None
        self._initialized = True

    def _init_model(self) -> None:
        try:
            from sentence_transformers import SentenceTransformer
            from core.gpu import get_device
            device = get_device()
            self._model = SentenceTransformer(VECTOR_CONFIG.embedding_model, device=device.torch_device)
            self.device_info = device
        except ImportError:
            self._model = None
        except Exception as e:
            logger.warning(f"Embedding model unavailable: {e}")
            self._model = None

    @property
    def model(self):
        if not self._loaded:
            with self._load_lock:
                if not self._loaded:
                    self._init_model()
                    self._loaded = True
        return self._model

    @property
    def available(self) -> bool:
        return self.model is not None

    def encode(self, texts: List[str]) -> List[List[float]]:
        """Создать эмбеддинги для текстов"""
        if not texts or self.model is None:
            return []
        try:
            embeddings = self.model.encode(
                texts, convert_to_numpy=True, show_progress_bar=False, batch_size=32,
                normalize_embeddings=True,
            )
            return embeddings.tolist()
        except Exception as e:
            logger.warning(f"Embedding failed: {e}")
            return []

    def encode_single(self, text: str) -> Optional[List[float]]:
        """Создать эмбеддинг для одного текста"""
        results = self.encode([text])
        return results[0] if results else None


embedding_model = EmbeddingModel()


def _simple_meta(doc_id: str, index: int, count: int, metadata: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    meta: Dict[str, Any] = {"doc_id": doc_id, "chunk_index": index, "chunk_count": count}
    for k, v in (metadata or {}).items():
        if isinstance(v, (str, int, float, bool)) and k not in meta:
            meta[k] = v
    return meta


# ═══════════════════════════════════════════════════════════════════════════════
# VECTOR STORE (ChromaDB)
# ═══════════════════════════════════════════════════════════════════════════════

class VectorStore:
    """Векторное хранилище на базе ChromaDB"""

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
        self.client = None
        self.collection = None
        self.available = False
        self.chunker = TextChunker()
        self._init_store()
        self._initialized = True

    def _init_store(self) -> None:
        try:
            import chromadb
            from chromadb.config import Settings

            self.client = chromadb.PersistentClient(
                path=str(VECTOR_DB_PATH),
                settings=Settings(anonymized_telemetry=False, allow_reset=True)
            )
            self.collection = self.client.get_or_create_collection(
                name=VECTOR_CONFIG.collection_name,
                metadata={"hnsw:space": "cosine"}
            )
            self.available = True
        except ImportError:
            pass
        except Exception as e:
            logger.warning(f"ChromaDB unavailable: {e}")

    def add_document(self, doc_id: str, text: str, metadata: Dict[str, Any] = None) -> int:
        """Добавить или ЗАМЕНИТЬ документ (старые чанки удаляются)."""
        if not self.available or not text:
            return 0
        try:
            chunks = self.chunker.chunk_text(text)
            if not chunks:
                return 0
            embeddings = embedding_model.encode(chunks)
            if not embeddings or len(embeddings) != len(chunks):
                return 0

            self.delete_document(doc_id)
            ids = [f"{doc_id}_chunk_{i}" for i in range(len(chunks))]
            metadatas = [_simple_meta(doc_id, i, len(chunks), metadata) for i in range(len(chunks))]
            for start in range(0, len(ids), 1000):
                self.collection.upsert(
                    ids=ids[start:start + 1000],
                    embeddings=embeddings[start:start + 1000],
                    documents=chunks[start:start + 1000],
                    metadatas=metadatas[start:start + 1000],
                )
            return len(chunks)
        except Exception as e:
            logger.warning(f"add_document failed for {doc_id}: {e}")
            return 0

    def search(self, query: str, top_k: int = None, where: Dict = None) -> List[VectorSearchResult]:
        """Семантический поиск"""
        if not self.available or not query:
            return []
        top_k = top_k or VECTOR_CONFIG.top_k
        try:
            total = self.collection.count()
            if total == 0:
                return []
            query_embedding = embedding_model.encode_single(query)
            if not query_embedding:
                return []
            results = self.collection.query(
                query_embeddings=[query_embedding],
                n_results=min(top_k, total),
                where=where
            )

            search_results = []
            if results and results.get('ids') and results['ids'][0]:
                ids = results['ids'][0]
                distances = (results.get('distances') or [[]])[0]
                documents = (results.get('documents') or [[]])[0]
                metadatas = (results.get('metadatas') or [[]])[0]
                for i, chunk_id in enumerate(ids):
                    distance = distances[i] if i < len(distances) else 1.0
                    score = max(0.0, min(1.0, 1.0 - distance))
                    if score >= VECTOR_CONFIG.min_similarity:
                        search_results.append(VectorSearchResult(
                            id=chunk_id,
                            text=documents[i] if i < len(documents) else '',
                            score=score,
                            metadata=(metadatas[i] if i < len(metadatas) else None) or {}
                        ))
            return search_results
        except Exception as e:
            logger.warning(f"Vector search failed: {e}")
            return []

    def search_similar(self, text: str, top_k: int = None, exclude_doc: Optional[str] = None) -> List[VectorSearchResult]:
        """Найти похожие документы (exclude_doc — исключить чанки этого документа)"""
        top_k = top_k or VECTOR_CONFIG.top_k
        results = self.search(text, top_k + 10)
        if exclude_doc:
            results = [r for r in results if r.metadata.get('doc_id') != exclude_doc]
        return results[:top_k]

    def delete_document(self, doc_id: str) -> bool:
        """Удалить документ из хранилища"""
        if not self.available:
            return False
        try:
            results = self.collection.get(where={"doc_id": doc_id})
            if results and results.get('ids'):
                self.collection.delete(ids=results['ids'])
                return True
            return False
        except Exception:
            return False

    def _document_count(self) -> int:
        try:
            ids = set()
            offset = 0
            while True:
                batch = self.collection.get(include=['metadatas'], limit=5000, offset=offset)
                metas = batch.get('metadatas') or []
                if not metas:
                    break
                ids.update((m or {}).get('doc_id') for m in metas)
                offset += len(metas)
                if len(metas) < 5000:
                    break
            ids.discard(None)
            return len(ids)
        except Exception:
            return 0

    def get_stats(self) -> Dict[str, Any]:
        """Получить статистику хранилища"""
        if not self.available:
            return {"available": False}
        try:
            return {
                "available": True,
                "type": "chromadb",
                "collection": VECTOR_CONFIG.collection_name,
                "document_count": self._document_count(),
                "chunk_count": self.collection.count(),
                "embedding_model": VECTOR_CONFIG.embedding_model,
                "path": str(VECTOR_DB_PATH)
            }
        except Exception as e:
            return {"available": True, "error": str(e)}

    def clear(self) -> bool:
        """Очистить хранилище"""
        if not self.available:
            return False
        try:
            self.client.delete_collection(VECTOR_CONFIG.collection_name)
            self.collection = self.client.create_collection(
                name=VECTOR_CONFIG.collection_name, metadata={"hnsw:space": "cosine"})
            return True
        except Exception:
            return False


# ═══════════════════════════════════════════════════════════════════════════════
# FALLBACK: Simple NumPy Vector Store (persistent)
# ═══════════════════════════════════════════════════════════════════════════════

class SimpleVectorStore:
    """Простое векторное хранилище на NumPy (если ChromaDB не установлен).

    Данные сохраняются в ~/.astrex/vectors/simple_store.json, поэтому
    `astrex ingest` и `astrex ask` (разные процессы) видят одни и те же данные.
    """

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else VECTOR_DB_PATH / "simple_store.json"
        self.documents: List[str] = []
        self.embeddings: List[List[float]] = []
        self.metadatas: List[Dict] = []
        self.ids: List[str] = []
        self.chunker = TextChunker()
        self._lock = threading.Lock()
        self._load()

    @property
    def available(self) -> bool:
        return embedding_model.available

    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            self.ids = data.get('ids', [])
            self.documents = data.get('documents', [])
            self.embeddings = data.get('embeddings', [])
            self.metadatas = data.get('metadatas', [])
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.warning(f"Cannot load {self.path}: {e}")

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix('.tmp')
            tmp.write_text(json.dumps({
                'ids': self.ids, 'documents': self.documents,
                'embeddings': self.embeddings, 'metadatas': self.metadatas,
            }, ensure_ascii=False), encoding='utf-8')
            tmp.replace(self.path)
        except Exception as e:
            logger.warning(f"Cannot save {self.path}: {e}")

    def delete_document(self, doc_id: str, save: bool = True) -> bool:
        with self._lock:
            keep = [i for i, m in enumerate(self.metadatas) if m.get('doc_id') != doc_id]
            if len(keep) == len(self.ids):
                return False
            self.ids = [self.ids[i] for i in keep]
            self.documents = [self.documents[i] for i in keep]
            self.embeddings = [self.embeddings[i] for i in keep]
            self.metadatas = [self.metadatas[i] for i in keep]
        if save:
            self._save()
        return True

    def add_document(self, doc_id: str, text: str, metadata: Dict[str, Any] = None) -> int:
        if not text or not self.available:
            return 0
        chunks = self.chunker.chunk_text(text)
        if not chunks:
            return 0
        embeddings = embedding_model.encode(chunks)
        if not embeddings or len(embeddings) != len(chunks):
            return 0
        self.delete_document(doc_id, save=False)
        with self._lock:
            for i, (chunk, emb) in enumerate(zip(chunks, embeddings)):
                self.ids.append(f"{doc_id}_chunk_{i}")
                self.documents.append(chunk)
                self.embeddings.append(list(map(float, emb)))
                self.metadatas.append(_simple_meta(doc_id, i, len(chunks), metadata))
        self._save()
        return len(chunks)

    def search(self, query: str, top_k: int = None, where: Dict = None) -> List[VectorSearchResult]:
        top_k = top_k or VECTOR_CONFIG.top_k
        if not query or not self.embeddings or not self.available:
            return []
        try:
            import numpy as np

            query_emb = embedding_model.encode_single(query)
            if not query_emb:
                return []
            q = np.array(query_emb, dtype=float)
            docs = np.array(self.embeddings, dtype=float)
            norms = np.linalg.norm(docs, axis=1) * (np.linalg.norm(q) or 1.0)
            norms[norms == 0] = 1.0
            similarities = docs @ q / norms

            results = []
            for idx in np.argsort(similarities)[::-1]:
                meta = self.metadatas[idx]
                if where and any(meta.get(k) != v for k, v in where.items()):
                    continue
                score = float(similarities[idx])
                if score < VECTOR_CONFIG.min_similarity:
                    break
                results.append(VectorSearchResult(id=self.ids[idx], text=self.documents[idx],
                                                  score=score, metadata=meta))
                if len(results) >= top_k:
                    break
            return results
        except Exception as e:
            logger.warning(f"Vector search failed: {e}")
            return []

    def clear(self) -> bool:
        with self._lock:
            self.documents, self.embeddings, self.metadatas, self.ids = [], [], [], []
        self._save()
        return True

    def get_stats(self) -> Dict[str, Any]:
        return {
            "available": self.available,
            "type": "simple_numpy",
            "document_count": len({m.get('doc_id') for m in self.metadatas}),
            "chunk_count": len(self.documents),
            "path": str(self.path),
        }


# ═══════════════════════════════════════════════════════════════════════════════
# FACTORY
# ═══════════════════════════════════════════════════════════════════════════════

_store = None
_store_lock = threading.Lock()


def get_vector_store():
    """Получить (единственный) экземпляр векторного хранилища"""
    global _store
    with _store_lock:
        if _store is None:
            store = VectorStore()
            _store = store if store.available else SimpleVectorStore()
        return _store


class _LazyVectorStore:
    """Прокси: хранилище создаётся при первом обращении, а не при импорте модуля."""

    def __getattr__(self, name):
        return getattr(get_vector_store(), name)

    def __repr__(self) -> str:
        return f"<lazy {get_vector_store()!r}>"


vector_store = _LazyVectorStore()


__all__ = [
    'VectorStore', 'SimpleVectorStore', 'VectorSearchResult',
    'TextChunker', 'EmbeddingModel', 'embedding_model', 'vector_store',
    'get_vector_store'
]
