#!/usr/bin/env python3
"""
ASTREX v3.0 — RAG Pipeline
Retrieval-Augmented Generation: поиск + LLM
"""

from dataclasses import dataclass
from typing import List, Optional, Generator, Dict, Any

from core.config import LLM_CONFIG
from core.logging_setup import get_logger
from .vectors import get_vector_store, TextChunker
from .llm import ollama_client, LLMResponse


logger = get_logger(__name__)


@dataclass
class RAGResponse:
    """Ответ RAG-системы"""
    answer: str
    sources: List[dict]
    model: str
    tokens_used: int
    error: Optional[str] = None


class RAGPipeline:
    """RAG Pipeline: vector search + LLM generation"""

    PROMPT_TEMPLATE = """На основе следующих документов ответь на вопрос. Если информации недостаточно, скажи об этом.

Документы:
{context}

Вопрос: {question}

Ответ:"""

    def __init__(self, vector_store=None, llm_client=None, top_k=10, max_context_length=None):
        self.vector_store = vector_store or get_vector_store()
        self.llm_client = llm_client or ollama_client
        self.top_k = top_k
        self.max_context_length = max_context_length or LLM_CONFIG.max_context_length

    def ingest(self, doc_id: str, text: str, metadata: Optional[Dict[str, Any]] = None) -> int:
        """Добавить документ в векторное хранилище."""
        return self.vector_store.add_document(doc_id=doc_id, text=text, metadata=metadata)

    def get_context(self, question: str, top_k: Optional[int] = None) -> List[dict]:
        """Получить релевантные чанки без вызова LLM."""
        k = top_k if top_k is not None else self.top_k
        results = self.vector_store.search(question, top_k=k)

        # Дедупликация по doc_id
        seen_docs = {}
        for result in results:
            meta = result.metadata or {}
            doc_id = meta.get('doc_id', result.id)

            if doc_id not in seen_docs or result.score > seen_docs[doc_id]['score']:
                seen_docs[doc_id] = {
                    'text': result.text,
                    'score': result.score,
                    'metadata': meta
                }

        return sorted(seen_docs.values(), key=lambda x: x['score'], reverse=True)

    def _build_context(self, chunks: List[dict]) -> str:
        """Построить нумерованный контекст из чанков."""
        parts = []
        total = 0
        for i, chunk in enumerate(chunks, 1):
            part = f"[{i}] {chunk['text']}"
            if total + len(part) > self.max_context_length:
                break
            parts.append(part)
            total += len(part) + 2
        return "\n\n".join(parts)

    def query(self, question: str, top_k: Optional[int] = None) -> RAGResponse:
        """RAG-запрос: поиск + генерация ответа."""
        try:
            chunks = self.get_context(question, top_k=top_k)

            if not chunks:
                return RAGResponse(
                    answer="Не найдено релевантных документов для ответа на вопрос.",
                    sources=[], model=self.llm_client.default_model,
                    tokens_used=0, error="No documents found"
                )

            context = self._build_context(chunks)
            prompt = self.PROMPT_TEMPLATE.format(context=context, question=question)

            response = self.llm_client.generate(prompt=prompt)

            return RAGResponse(
                answer=response.text,
                sources=chunks,
                model=response.model,
                tokens_used=response.tokens_used,
                error=response.error
            )

        except Exception as e:
            logger.error(f"RAG query failed: {e}")
            return RAGResponse(
                answer="", sources=[],
                model=self.llm_client.default_model,
                tokens_used=0, error=str(e)
            )

    def query_stream(self, question: str, top_k: Optional[int] = None) -> Generator[str, None, None]:
        """Streaming RAG-запрос."""
        try:
            chunks = self.get_context(question, top_k=top_k)
            if not chunks:
                yield "Не найдено релевантных документов для ответа на вопрос."
                return

            context = self._build_context(chunks)
            prompt = self.PROMPT_TEMPLATE.format(context=context, question=question)

            for chunk in self.llm_client.generate_stream(prompt):
                yield chunk
        except Exception as e:
            yield f"Ошибка: {e}"


__all__ = ['RAGPipeline', 'RAGResponse']
