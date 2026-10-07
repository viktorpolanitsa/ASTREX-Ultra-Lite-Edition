#!/usr/bin/env python3
"""
ASTREX v3.0 — ML Package
Машинное обучение: векторы, LLM, RAG.

Модели и подключения создаются лениво (при первом использовании).
"""

from .vectors import (
    VectorStore, SimpleVectorStore, VectorSearchResult,
    TextChunker, EmbeddingModel, embedding_model, vector_store,
    get_vector_store
)

from .llm import (
    OllamaClient, LLMResponse, ollama_client,
    summarize_text, analyze_connections, answer_question,
    generate_dossier, classify_document, batch_summarize,
    get_llm_status
)

from .rag import (
    RAGPipeline, RAGResponse
)


__all__ = [
    # Vectors
    'VectorStore', 'SimpleVectorStore', 'VectorSearchResult',
    'TextChunker', 'EmbeddingModel', 'embedding_model', 'vector_store',
    'get_vector_store',

    # LLM
    'OllamaClient', 'LLMResponse', 'ollama_client',
    'summarize_text', 'analyze_connections', 'answer_question',
    'generate_dossier', 'classify_document', 'batch_summarize',
    'get_llm_status',

    # RAG
    'RAGPipeline', 'RAGResponse'
]
