#!/usr/bin/env python3
"""
ASTREX v3.0 — Core Package
Ядро системы: конфигурация, индекс, NLP, граф, сканер

Тяжёлые модели (pymorphy, spaCy, SBERT) загружаются лениво — при первом
использовании, поэтому импорт пакета быстрый.
"""

from .config import (
    VERSION,
    ASTREX_HOME, INDEX_DB_PATH, VECTOR_DB_PATH, CACHE_PATH, LOGS_PATH,
    FILE_TYPES, ENGINE_CONFIG, NLP_CONFIG, VECTOR_CONFIG, LLM_CONFIG, WEB_CONFIG
)

from .logging_setup import setup_logger, get_logger, log

from .index import (
    FileIndex, IndexedFile, SearchResult, file_index, compute_file_hash
)

from .nlp import (
    entity_extractor, relevance_calculator, morph_analyzer,
    extract_entities, calculate_relevance, calculate_relevance_batch,
    expand_query, fuzzy_search, build_query_matcher,
    EntityExtractor, RelevanceCalculator, MorphologyAnalyzer, FuzzyMatcher, QueryMatcher
)

from .graph import (
    Node, Edge, Graph, GraphBuilder, GraphAnalyzer,
    export_to_graphml, export_to_gexf
)

from .engine import (
    ScanEngine, ScanResult, ScanStats, SearchOptions, MessageType,
    IncrementalIndexer
)


__all__ = [
    'VERSION',
    # Config
    'ASTREX_HOME', 'INDEX_DB_PATH', 'VECTOR_DB_PATH', 'CACHE_PATH', 'LOGS_PATH',
    'FILE_TYPES', 'ENGINE_CONFIG', 'NLP_CONFIG', 'VECTOR_CONFIG', 'LLM_CONFIG', 'WEB_CONFIG',

    # Logging
    'setup_logger', 'get_logger', 'log',

    # Index
    'FileIndex', 'IndexedFile', 'SearchResult', 'file_index', 'compute_file_hash',

    # NLP
    'entity_extractor', 'relevance_calculator', 'morph_analyzer',
    'extract_entities', 'calculate_relevance', 'calculate_relevance_batch',
    'expand_query', 'fuzzy_search', 'build_query_matcher',
    'EntityExtractor', 'RelevanceCalculator', 'MorphologyAnalyzer', 'FuzzyMatcher', 'QueryMatcher',

    # Graph
    'Node', 'Edge', 'Graph', 'GraphBuilder', 'GraphAnalyzer',
    'export_to_graphml', 'export_to_gexf',

    # Scanner
    'ScanEngine', 'ScanResult', 'ScanStats', 'SearchOptions', 'MessageType',
    'IncrementalIndexer'
]
