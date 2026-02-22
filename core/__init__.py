#!/usr/bin/env python3
"""
ASTREX v3.0 — Core Package
Ядро системы: конфигурация, индекс, NLP, граф, сканер
"""

from .logging_setup import setup_logger, get_logger, log

from .config import (
    ASTREX_HOME, INDEX_DB_PATH, VECTOR_DB_PATH, CACHE_PATH, LOGS_PATH,
    FILE_TYPES, ENGINE_CONFIG, NLP_CONFIG, VECTOR_CONFIG, LLM_CONFIG, WEB_CONFIG
)

from .index import (
    FileIndex, IndexedFile, SearchResult, file_index, compute_file_hash
)

from .nlp import (
    entity_extractor, relevance_calculator, morph_analyzer,
    extract_entities, calculate_relevance, calculate_relevance_batch,
    expand_query, fuzzy_search,
    EntityExtractor, RelevanceCalculator, MorphologyAnalyzer, FuzzyMatcher
)

from .graph import (
    Node, Edge, Graph, GraphBuilder, GraphAnalyzer,
    export_to_graphml, export_to_gexf
)

from .engine import (
    ScanEngine, ScanResult, ScanStats, MessageType,
    IncrementalIndexer
)


__all__ = [
    # Config
    'ASTREX_HOME', 'INDEX_DB_PATH', 'VECTOR_DB_PATH', 'CACHE_PATH', 'LOGS_PATH',
    'FILE_TYPES', 'ENGINE_CONFIG', 'NLP_CONFIG', 'VECTOR_CONFIG', 'LLM_CONFIG', 'WEB_CONFIG',
    
    # Index
    'FileIndex', 'IndexedFile', 'SearchResult', 'file_index', 'compute_file_hash',
    
    # NLP
    'entity_extractor', 'relevance_calculator', 'morph_analyzer',
    'extract_entities', 'calculate_relevance', 'calculate_relevance_batch',
    'expand_query', 'fuzzy_search',
    'EntityExtractor', 'RelevanceCalculator', 'MorphologyAnalyzer', 'FuzzyMatcher',
    
    # Graph
    'Node', 'Edge', 'Graph', 'GraphBuilder', 'GraphAnalyzer',
    'export_to_graphml', 'export_to_gexf',
    
    # Scanner
    'ScanEngine', 'ScanResult', 'ScanStats', 'MessageType',
    'IncrementalIndexer'
]
