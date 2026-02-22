#!/usr/bin/env python3
"""
ASTREX v3.0 — Configuration
Центральная конфигурация системы
"""

import os
from pathlib import Path
from dataclasses import dataclass, field, fields
from typing import Set, Tuple, Dict, Any

VERSION = "3.0.0"

# ═══════════════════════════════════════════════════════════════════════════════
# PATHS
# ═══════════════════════════════════════════════════════════════════════════════

ASTREX_HOME = Path(os.environ.get("ASTREX_HOME", Path.home() / ".astrex"))
ASTREX_HOME.mkdir(parents=True, exist_ok=True)

INDEX_DB_PATH = ASTREX_HOME / "index.db"
VECTOR_DB_PATH = ASTREX_HOME / "vectors"
CACHE_PATH = ASTREX_HOME / "cache"
LOGS_PATH = ASTREX_HOME / "logs"

for p in [VECTOR_DB_PATH, CACHE_PATH, LOGS_PATH]:
    p.mkdir(parents=True, exist_ok=True)


# ═══════════════════════════════════════════════════════════════════════════════
# FILE TYPES
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class FileTypes:
    """Поддерживаемые типы файлов по категориям"""
    
    # TIER 1: PDF
    PDF: Set[str] = field(default_factory=lambda: {'.pdf'})
    
    # TIER 2: Office
    OFFICE: Set[str] = field(default_factory=lambda: {
        '.docx', '.xlsx', '.pptx',  # MS Office
        '.doc', '.xls', '.ppt',      # Legacy MS Office
        '.odt', '.ods', '.odp',      # OpenDocument
        '.rtf',                       # Rich Text
    })
    
    # TIER 3: Email
    EMAIL: Set[str] = field(default_factory=lambda: {
        '.eml', '.msg',              # Email messages
        '.pst', '.ost', '.mbox',     # Email archives
    })
    
    # TIER 4: Text
    TEXT: Set[str] = field(default_factory=lambda: {
        '.txt', '.md', '.rst',       # Plain text
        '.log', '.csv', '.tsv',      # Data files
        '.json', '.xml', '.yaml', '.yml',  # Structured
        '.html', '.htm', '.xhtml',   # Web
        '.ini', '.cfg', '.conf',     # Config
    })
    
    # TIER 5: Code
    CODE: Set[str] = field(default_factory=lambda: {
        '.py', '.js', '.ts', '.jsx', '.tsx',
        '.java', '.c', '.cpp', '.h', '.hpp',
        '.cs', '.go', '.rs', '.rb', '.php',
        '.swift', '.kt', '.scala', '.r',
        '.sh', '.bash', '.ps1',
        '.vue', '.svelte',
    })
    
    # TIER 6: Archives (recursive extraction)
    ARCHIVE: Set[str] = field(default_factory=lambda: {
        '.zip', '.rar', '.7z', '.tar',
        '.gz', '.bz2', '.xz', '.lz4', '.zst',
        '.tar.gz', '.tar.bz2', '.tar.xz',
    })
    
    # TIER 7: Images (OCR)
    IMAGE: Set[str] = field(default_factory=lambda: {
        '.png', '.jpg', '.jpeg', '.tiff', '.tif',
        '.bmp', '.gif', '.webp',
    })
    
    # TIER 8: Database
    DATABASE: Set[str] = field(default_factory=lambda: {
        '.db', '.sqlite', '.sqlite3',
        '.mdb', '.accdb',  # Access
        '.sql', '.dump', '.pgdump',  # SQL dumps
        '.myi', '.myd', '.frm',      # MySQL binary
    })

    # TIER 9: E-books
    EBOOK: Set[str] = field(default_factory=lambda: {
        '.epub', '.fb2', '.mobi',
    })

    # TIER 10: Data files (generic)
    DATA: Set[str] = field(default_factory=lambda: {
        '.dat', '.nfo',
    })
    
    @property
    def all_supported(self) -> Set[str]:
        return (
            self.PDF | self.OFFICE | self.EMAIL |
            self.TEXT | self.CODE | self.ARCHIVE |
            self.IMAGE | self.DATABASE | self.EBOOK | self.DATA
        )


FILE_TYPES = FileTypes()


# ═══════════════════════════════════════════════════════════════════════════════
# ENGINE CONFIG
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class EngineConfig:
    """Конфигурация движка сканирования"""

    # Threading
    max_workers_multiplier: int = 1   # было 2 — слишком агрессивно
    batch_size: int = 16
    batch_timeout: float = 0.5

    # Resource limits — защита от OOM и перегрева
    max_cpu_percent: int = 75          # макс. загрузка CPU (0 = без лимита)
    max_ram_percent: int = 70          # при превышении — пауза воркеров
    max_ram_per_worker_mb: int = 512   # лимит RSS на один воркер
    worker_spawn_delay: float = 0.05   # задержка между отправкой задач (сек)

    # File limits
    max_file_size: int = 2 * 1024 * 1024 * 1024  # 2GB
    chunk_size: int = 64 * 1024 * 1024           # 64MB
    context_window: int = 600                     # chars around match

    # Search
    min_score: float = 0.1
    max_results: int = 10000
    fuzzy_threshold: int = 80  # 0-100, for rapidfuzz

    # OCR
    ocr_enabled: bool = True
    ocr_languages: str = "rus+eng"
    ocr_timeout: int = 30  # seconds per image

    # Archive
    archive_max_depth: int = 3
    archive_max_size: int = 500 * 1024 * 1024  # 500MB uncompressed

    # Encoding detection
    encodings: Tuple[str, ...] = (
        'utf-8', 'cp1251', 'cp866', 'utf-16',
        'latin-1', 'koi8-r', 'iso-8859-5'
    )


ENGINE_CONFIG = EngineConfig()


# ═══════════════════════════════════════════════════════════════════════════════
# NLP CONFIG
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class NLPConfig:
    """Конфигурация NLP модуля"""
    
    # spaCy
    spacy_models: Tuple[str, ...] = (
        "ru_core_news_lg", 
        "ru_core_news_md", 
        "ru_core_news_sm"
    )
    
    # Sentence transformers
    sbert_model: str = "paraphrase-multilingual-MiniLM-L12-v2"
    
    # Morphology
    use_pymorphy: bool = True
    
    # Limits
    max_text_length: int = 50000
    min_entity_length: int = 2
    max_entities_per_type: int = 100
    
    # Caching
    cache_size: int = 10000
    
    # GPU — автоопределение: CUDA → ROCm → Intel XPU → OpenVINO → MPS → CPU
    use_gpu: bool = True
    gpu_memory_fraction: float = 0.7
    gpu_backend: str = "auto"  # "auto", "cuda", "rocm", "xpu", "openvino", "mps", "cpu"


NLP_CONFIG = NLPConfig()


# ═══════════════════════════════════════════════════════════════════════════════
# VECTOR STORE CONFIG
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class VectorConfig:
    """Конфигурация векторного хранилища"""
    
    collection_name: str = "astrex_documents"
    embedding_model: str = "paraphrase-multilingual-MiniLM-L12-v2"
    chunk_size: int = 512  # tokens
    chunk_overlap: int = 50
    
    # Search
    top_k: int = 20
    min_similarity: float = 0.5


VECTOR_CONFIG = VectorConfig()


# ═══════════════════════════════════════════════════════════════════════════════
# LLM CONFIG
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class LLMConfig:
    """Конфигурация локальных LLM"""
    
    # Ollama
    ollama_host: str = "http://localhost:11434"
    default_model: str = "llama3"
    
    # Prompts
    summarize_prompt: str = """Кратко опиши ключевые факты из текста на русском языке.
Выдели: имена, организации, даты, суммы, ключевые события.
Текст:
{text}

Краткое резюме:"""

    analyze_connections_prompt: str = """Проанализируй связи между объектами в тексте.
Найди: кто с кем связан, как, когда.
Текст:
{text}

Связи:"""
    
    # Limits
    max_context_length: int = 4096
    temperature: float = 0.3
    timeout: int = 60


LLM_CONFIG = LLMConfig()


# ═══════════════════════════════════════════════════════════════════════════════
# WEB CONFIG
# ═══════════════════════════════════════════════════════════════════════════════

def _load_or_create_secret_key() -> str:
    """Load secret key from env or file, generate and persist if missing."""
    env_key = os.environ.get('ASTREX_SECRET_KEY')
    if env_key:
        return env_key
    key_file = ASTREX_HOME / ".secret_key"
    try:
        if key_file.exists():
            return key_file.read_text().strip()
    except Exception:
        pass
    import secrets
    key = secrets.token_hex(32)
    try:
        key_file.write_text(key)
        key_file.chmod(0o600)
    except Exception:
        pass
    return key


@dataclass
class WebConfig:
    """Конфигурация веб-интерфейса"""
    
    host: str = "127.0.0.1"
    port: int = 8080
    debug: bool = False
    
    # Auth
    enable_auth: bool = False
    secret_key: str = field(default_factory=lambda: _load_or_create_secret_key())
    
    # CORS
    cors_origins: Tuple[str, ...] = ("http://localhost:3000",)


WEB_CONFIG = WebConfig()


# ═══════════════════════════════════════════════════════════════════════════════
# EXPORT
# ═══════════════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════════════
# YAML CONFIG LOADER
# ═══════════════════════════════════════════════════════════════════════════════

CONFIG_FILE = ASTREX_HOME / "config.yaml"


def _apply_yaml_overrides(obj: Any, overrides: Dict[str, Any]) -> None:
    """Применить значения из YAML поверх дефолтов dataclass."""
    if not overrides or not isinstance(overrides, dict):
        return
    for f in fields(obj):
        if f.name in overrides:
            val = overrides[f.name]
            if val is not None:
                # Конвертация списков в tuple/set
                current = getattr(obj, f.name)
                if isinstance(current, tuple) and isinstance(val, list):
                    val = tuple(val)
                elif isinstance(current, set) and isinstance(val, list):
                    val = set(val)
                setattr(obj, f.name, val)


def load_config() -> None:
    """Загрузить конфигурацию из ~/.astrex/config.yaml если файл существует."""
    if not CONFIG_FILE.exists():
        return

    try:
        import yaml
    except ImportError:
        return

    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)
    except Exception:
        return

    if not isinstance(data, dict):
        return

    section_map = {
        'engine': ENGINE_CONFIG,
        'nlp': NLP_CONFIG,
        'vector': VECTOR_CONFIG,
        'llm': LLM_CONFIG,
        'web': WEB_CONFIG,
    }

    for section_name, config_obj in section_map.items():
        if section_name in data:
            _apply_yaml_overrides(config_obj, data[section_name])


# Загружаем при импорте
load_config()


# ═══════════════════════════════════════════════════════════════════════════════
# EXPORT
# ═══════════════════════════════════════════════════════════════════════════════

__all__ = [
    'VERSION',
    'ASTREX_HOME', 'INDEX_DB_PATH', 'VECTOR_DB_PATH', 'CACHE_PATH', 'LOGS_PATH',
    'FILE_TYPES', 'ENGINE_CONFIG', 'NLP_CONFIG', 'VECTOR_CONFIG', 'LLM_CONFIG', 'WEB_CONFIG',
    'load_config', 'CONFIG_FILE'
]
