#!/usr/bin/env python3
"""
ASTREX v3.0 — Vector Store
Векторное хранилище для семантического поиска
"""

import hashlib
import threading
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass

from core.config import VECTOR_CONFIG, VECTOR_DB_PATH, NLP_CONFIG


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
    """Разбиение текста на чанки для эмбеддингов"""
    
    def __init__(
        self, 
        chunk_size: int = VECTOR_CONFIG.chunk_size,
        overlap: int = VECTOR_CONFIG.chunk_overlap
    ):
        self.chunk_size = chunk_size
        self.overlap = overlap
    
    def chunk_text(self, text: str) -> List[str]:
        """Разбить текст на чанки"""
        if not text:
            return []
        
        # Simple sentence-aware chunking
        sentences = self._split_sentences(text)
        
        chunks = []
        current_chunk = []
        current_length = 0
        
        for sentence in sentences:
            sentence_len = len(sentence.split())
            
            if current_length + sentence_len > self.chunk_size:
                if current_chunk:
                    chunks.append(' '.join(current_chunk))

                # Start new chunk with word-based overlap (not sentence-count-based)
                overlap_sentences: List[str] = []
                overlap_words = 0
                for s in reversed(current_chunk):
                    w = len(s.split())
                    if overlap_words + w > self.overlap:
                        break
                    overlap_sentences.insert(0, s)
                    overlap_words += w
                current_chunk = overlap_sentences + [sentence]
                current_length = overlap_words + sentence_len
            else:
                current_chunk.append(sentence)
                current_length += sentence_len
        
        if current_chunk:
            chunks.append(' '.join(current_chunk))
        
        return chunks
    
    def _split_sentences(self, text: str) -> List[str]:
        """Разбить текст на предложения"""
        import re
        
        # Simple sentence splitting
        sentences = re.split(r'(?<=[.!?])\s+', text)
        return [s.strip() for s in sentences if s.strip()]


# ═══════════════════════════════════════════════════════════════════════════════
# EMBEDDING MODEL
# ═══════════════════════════════════════════════════════════════════════════════

class EmbeddingModel:
    """Модель для создания эмбеддингов"""
    
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
        
        self.model = None
        self.available = False
        self._init_model()
        self._initialized = True
    
    def _init_model(self) -> None:
        try:
            from sentence_transformers import SentenceTransformer
            from core.gpu import get_device
            device = get_device()
            self.model = SentenceTransformer(
                VECTOR_CONFIG.embedding_model,
                device=device.torch_device,
            )
            self.device_info = device
            self.available = True
        except ImportError:
            pass
        except Exception:
            pass
    
    def encode(self, texts: List[str]) -> List[List[float]]:
        """Создать эмбеддинги для текстов"""
        if not self.available or not texts:
            return []
        
        try:
            embeddings = self.model.encode(
                texts,
                convert_to_numpy=True,
                show_progress_bar=False,
                batch_size=32
            )
            return embeddings.tolist()
        except Exception:
            return []
    
    def encode_single(self, text: str) -> Optional[List[float]]:
        """Создать эмбеддинг для одного текста"""
        results = self.encode([text])
        return results[0] if results else None


embedding_model = EmbeddingModel()


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
        """Инициализация ChromaDB"""
        try:
            import chromadb
            from chromadb.config import Settings
            
            self.client = chromadb.PersistentClient(
                path=str(VECTOR_DB_PATH),
                settings=Settings(
                    anonymized_telemetry=False,
                    allow_reset=True
                )
            )
            
            self.collection = self.client.get_or_create_collection(
                name=VECTOR_CONFIG.collection_name,
                metadata={"hnsw:space": "cosine"}
            )
            
            self.available = True
            
        except ImportError:
            pass
        except Exception:
            pass
    
    def add_document(
        self,
        doc_id: str,
        text: str,
        metadata: Dict[str, Any] = None
    ) -> int:
        """Добавить документ в хранилище"""
        if not self.available or not text:
            return 0
        
        try:
            # Chunk text
            chunks = self.chunker.chunk_text(text)
            if not chunks:
                return 0
            
            # Generate embeddings
            embeddings = embedding_model.encode(chunks)
            if not embeddings:
                return 0
            
            # Prepare data
            ids = [f"{doc_id}_chunk_{i}" for i in range(len(chunks))]
            metadatas = []
            
            for i, chunk in enumerate(chunks):
                chunk_meta = {
                    "doc_id": doc_id,
                    "chunk_index": i,
                    "chunk_count": len(chunks),
                }
                if metadata:
                    # Only include simple types
                    for k, v in metadata.items():
                        if isinstance(v, (str, int, float, bool)):
                            chunk_meta[k] = v
                metadatas.append(chunk_meta)
            
            # Add to collection
            self.collection.add(
                ids=ids,
                embeddings=embeddings,
                documents=chunks,
                metadatas=metadatas
            )
            
            return len(chunks)
            
        except Exception:
            return 0
    
    def search(
        self,
        query: str,
        top_k: int = VECTOR_CONFIG.top_k,
        where: Dict = None
    ) -> List[VectorSearchResult]:
        """Семантический поиск"""
        if not self.available or not query:
            return []
        
        try:
            # Generate query embedding
            query_embedding = embedding_model.encode_single(query)
            if not query_embedding:
                return []
            
            # Search
            results = self.collection.query(
                query_embeddings=[query_embedding],
                n_results=top_k,
                where=where
            )
            
            # Parse results
            search_results = []
            
            if results and results.get('ids') and results['ids'][0]:
                ids = results['ids'][0]
                distances = results['distances'][0] if results.get('distances') and results['distances'] else []
                documents = results['documents'][0] if results.get('documents') and results['documents'] else []
                metadatas = results['metadatas'][0] if results.get('metadatas') and results['metadatas'] else []

                for i, doc_id in enumerate(ids):
                    raw_score = 1.0 - (distances[i] if i < len(distances) else 0)
                    score = max(0.0, min(1.0, raw_score))

                    if score >= VECTOR_CONFIG.min_similarity:
                        search_results.append(VectorSearchResult(
                            id=doc_id,
                            text=documents[i] if i < len(documents) else '',
                            score=score,
                            metadata=metadatas[i] if i < len(metadatas) else {}
                        ))
            
            return search_results
            
        except Exception:
            return []
    
    def search_similar(
        self,
        text: str,
        top_k: int = VECTOR_CONFIG.top_k,
        exclude_self: bool = True
    ) -> List[VectorSearchResult]:
        """Найти похожие документы"""
        results = self.search(text, top_k + (1 if exclude_self else 0))
        
        if exclude_self and results:
            # Remove exact match if present
            results = [r for r in results if r.score < 0.99]
        
        return results[:top_k]
    
    def delete_document(self, doc_id: str) -> bool:
        """Удалить документ из хранилища"""
        if not self.available:
            return False
        
        try:
            # Find all chunks for this document
            results = self.collection.get(
                where={"doc_id": doc_id}
            )
            
            if results and results['ids']:
                self.collection.delete(ids=results['ids'])
                return True
            
            return False
            
        except Exception:
            return False
    
    def get_stats(self) -> Dict[str, Any]:
        """Получить статистику хранилища"""
        if not self.available:
            return {"available": False}
        
        try:
            count = self.collection.count()
            
            return {
                "available": True,
                "collection": VECTOR_CONFIG.collection_name,
                "document_count": count,
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
                name=VECTOR_CONFIG.collection_name,
                metadata={"hnsw:space": "cosine"}
            )
            return True
        except Exception:
            return False


# ═══════════════════════════════════════════════════════════════════════════════
# FALLBACK: Simple NumPy Vector Store
# ═══════════════════════════════════════════════════════════════════════════════

class SimpleVectorStore:
    """Простое векторное хранилище на NumPy (fallback)"""
    
    def __init__(self):
        self.documents: List[str] = []
        self.embeddings: List[List[float]] = []
        self.metadatas: List[Dict] = []
        self.ids: List[str] = []
        self.available = embedding_model.available
        self.chunker = TextChunker()
    
    def add_document(
        self,
        doc_id: str,
        text: str,
        metadata: Dict[str, Any] = None
    ) -> int:
        if not self.available or not text:
            return 0
        
        chunks = self.chunker.chunk_text(text)
        if not chunks:
            return 0
        
        embeddings = embedding_model.encode(chunks)
        if not embeddings:
            return 0
        
        for i, (chunk, emb) in enumerate(zip(chunks, embeddings)):
            self.ids.append(f"{doc_id}_chunk_{i}")
            self.documents.append(chunk)
            self.embeddings.append(emb)
            self.metadatas.append(metadata or {})
        
        return len(chunks)
    
    def search(
        self,
        query: str,
        top_k: int = VECTOR_CONFIG.top_k,
        where: Dict = None
    ) -> List[VectorSearchResult]:
        if not self.available or not query or not self.embeddings:
            return []
        
        try:
            import numpy as np
            
            query_emb = embedding_model.encode_single(query)
            if not query_emb:
                return []
            
            query_emb = np.array(query_emb)
            doc_embs = np.array(self.embeddings)
            
            # Cosine similarity
            similarities = np.dot(doc_embs, query_emb) / (
                np.linalg.norm(doc_embs, axis=1) * np.linalg.norm(query_emb)
            )
            
            # Get top-k
            top_indices = np.argsort(similarities)[::-1][:top_k]
            
            results = []
            for idx in top_indices:
                score = float(similarities[idx])
                if score >= VECTOR_CONFIG.min_similarity:
                    results.append(VectorSearchResult(
                        id=self.ids[idx],
                        text=self.documents[idx],
                        score=score,
                        metadata=self.metadatas[idx]
                    ))
            
            return results
            
        except ImportError:
            return []
        except Exception:
            return []
    
    def clear(self) -> bool:
        self.documents = []
        self.embeddings = []
        self.metadatas = []
        self.ids = []
        return True
    
    def get_stats(self) -> Dict[str, Any]:
        return {
            "available": self.available,
            "type": "simple_numpy",
            "document_count": len(self.documents)
        }


# ═══════════════════════════════════════════════════════════════════════════════
# FACTORY
# ═══════════════════════════════════════════════════════════════════════════════

def get_vector_store():
    """Получить экземпляр векторного хранилища"""
    store = VectorStore()
    if store.available:
        return store
    
    # Fallback to simple store
    return SimpleVectorStore()


vector_store = get_vector_store()


__all__ = [
    'VectorStore', 'SimpleVectorStore', 'VectorSearchResult',
    'TextChunker', 'EmbeddingModel', 'embedding_model', 'vector_store',
    'get_vector_store'
]
