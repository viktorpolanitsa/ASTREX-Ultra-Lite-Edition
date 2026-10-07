#!/usr/bin/env python3
"""
ASTREX v3.0 — Configuration
Центральная конфигурация системы
"""

import os
import secrets
import tempfile
from pathlib import Path
from dataclasses import dataclass, field, fields
from typing import Set, Tuple, Dict, Any, List, FrozenSet, Optional

VERSION = "3.0.1"

# Предупреждения, возникшие до настройки логирования (выводятся logging_setup)
CONFIG_WARNINGS: List[str] = []

# ═══════════════════════════════════════════════════════════════════════════════
# PATHS
# ═══════════════════════════════════════════════════════════════════════════════


def _init_home() -> Path:
    home = Path(os.environ.get("ASTREX_HOME", Path.home() / ".astrex")).expanduser()
    try:
        home.mkdir(parents=True, exist_ok=True)
        probe = home / ".write_test"
        probe.touch()
        probe.unlink()
        return home
    except OSError as e:
        fallback = Path(tempfile.gettempdir()) / f"astrex-{os.getuid() if hasattr(os, 'getuid') else 'user'}"
        fallback.mkdir(parents=True, exist_ok=True)
        CONFIG_WARNINGS.append(
            f"ASTREX_HOME {home} is not writable ({e}); using {fallback} instead"
        )
        return fallback


ASTREX_HOME = _init_home()

INDEX_DB_PATH = ASTREX_HOME / "index.db"
VECTOR_DB_PATH = ASTREX_HOME / "vectors"
CACHE_PATH = ASTREX_HOME / "cache"
LOGS_PATH = ASTREX_HOME / "logs"
TEMP_PATH = CACHE_PATH / "tmp"

for p in [VECTOR_DB_PATH, CACHE_PATH, LOGS_PATH, TEMP_PATH]:
    try:
        p.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        CONFIG_WARNINGS.append(f"Cannot create {p}: {e}")


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
        '.json', '.jsonl', '.ndjson',
        '.xml', '.yaml', '.yml',     # Structured
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
    # Многосоставные расширения (.tar.gz) сканер распознаёт по последнему
    # суффиксу (.gz), а экстрактор — по полному имени.
    ARCHIVE: Set[str] = field(default_factory=lambda: {
        '.zip', '.rar', '.7z', '.tar',
        '.gz', '.bz2', '.xz', '.lz4', '.zst',
        '.tgz', '.tbz2', '.txz',
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
        '.epub', '.fb2', '.mobi', '.azw', '.azw3',
    })

    # TIER 10: Data files (generic)
    DATA: Set[str] = field(default_factory=lambda: {
        '.dat', '.nfo',
    })

    # TIER 11: Audio / Video (metadata + optional Whisper transcription)
    AUDIO: Set[str] = field(default_factory=lambda: {
        '.mp3', '.wav', '.ogg', '.flac', '.m4a', '.aac', '.wma',
    })
    VIDEO: Set[str] = field(default_factory=lambda: {
        '.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv', '.webm',
    })

    # TIER 12: Network captures
    NETWORK: Set[str] = field(default_factory=lambda: {
        '.pcap', '.pcapng', '.cap', '.nfcapd', '.flow',
    })

    @property
    def all_supported(self) -> FrozenSet[str]:
        """Все поддерживаемые расширения (вычисляйте один раз перед циклом по файлам)."""
        return frozenset(
            self.PDF | self.OFFICE | self.EMAIL |
            self.TEXT | self.CODE | self.ARCHIVE |
            self.IMAGE | self.DATABASE | self.EBOOK | self.DATA |
            self.AUDIO | self.VIDEO | self.NETWORK
        )


FILE_TYPES = FileTypes()


# ═══════════════════════════════════════════════════════════════════════════════
# ENGINE CONFIG
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class EngineConfig:
    """Конфигурация движка сканирования"""

    # Workers (процессы, не потоки)
    max_workers_multiplier: int = 1
    batch_size: int = 16
    batch_timeout: float = 0.5

    # Resource limits — защита от OOM и перегрева
    max_cpu_percent: int = 75          # доля ядер для сканирования (0 = без лимита)
    max_ram_percent: int = 90          # при превышении — новые задачи не отправляются
    max_ram_per_worker_mb: int = 2048  # жёсткий лимит RSS воркера (0 = без лимита)
    est_ram_per_worker_mb: int = 512   # оценка RSS воркера для выбора их числа
    worker_spawn_delay: float = 0.0    # устарело, не используется
    file_timeout: int = 600            # лимит времени на один файл, сек (0 = без лимита)

    # File limits
    max_file_size: int = 2 * 1024 * 1024 * 1024  # 2GB
    chunk_size: int = 64 * 1024 * 1024           # 64MB — порог "большого" текстового файла
    max_extracted_chars: int = 5_000_000          # максимум символов текста с одного файла
    context_window: int = 600                     # chars around match

    # Search
    min_score: float = 0.1
    max_results: int = 10000
    fuzzy_threshold: int = 80  # 0-100, for rapidfuzz
    fuzzy_max_scan_chars: int = 200_000

    # OCR
    ocr_enabled: bool = True
    ocr_languages: str = "rus+eng"
    ocr_timeout: int = 60  # seconds per image

    # Archive
    archive_max_depth: int = 3
    archive_max_size: int = 500 * 1024 * 1024         # 500MB uncompressed per archive
    archive_member_max_size: int = 200 * 1024 * 1024  # 200MB per member
    archive_max_members: int = 20000

    # Media / network
    transcribe_media: bool = True       # работает, только если установлен whisper
    whisper_model: str = "base"
    media_max_duration: int = 1800      # секунд аудио для транскрипции
    pcap_max_packets: int = 100_000

    # Scan scope
    skip_hidden_dirs: bool = True
    exclude_paths: Tuple[str, ...] = ('/proc', '/sys', '/dev', '/run')

    # Plugins (~/.astrex/plugins)
    plugins_autoload: bool = True

    # Encoding detection (кандидаты для 8-битных кодировок)
    encodings: Tuple[str, ...] = (
        'utf-8', 'cp1251', 'koi8-r', 'cp866',
        'iso-8859-5', 'mac_cyrillic', 'utf-16', 'cp1252'
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
    sbert_chunk_chars: int = 500       # ~128 токенов — лимит модели
    sbert_max_chunks: int = 8

    # Morphology
    use_pymorphy: bool = True
    max_forms_per_word: int = 120

    # Limits
    max_text_length: int = 50000
    min_entity_length: int = 2
    max_entities_per_type: int = 100

    # Caching
    cache_size: int = 10000

    # GPU — автоопределение: CUDA → ROCm → Intel XPU → MPS → CPU
    use_gpu: bool = True
    gpu_memory_fraction: float = 0.7
    gpu_backend: str = "auto"  # "auto", "cuda", "rocm", "xpu", "mps", "cpu"


NLP_CONFIG = NLPConfig()


# ═══════════════════════════════════════════════════════════════════════════════
# VECTOR STORE CONFIG
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class VectorConfig:
    """Конфигурация векторного хранилища"""

    collection_name: str = "astrex_documents"
    embedding_model: str = "paraphrase-multilingual-MiniLM-L12-v2"
    # Размер чанка в словах. Модель MiniLM обрезает вход на 128 токенах
    # (~80 русских слов), поэтому больший чанк модель просто не "увидит".
    chunk_size: int = 80
    chunk_overlap: int = 15

    # Search
    top_k: int = 20
    min_similarity: float = 0.3


VECTOR_CONFIG = VectorConfig()


# ═══════════════════════════════════════════════════════════════════════════════
# LLM CONFIG
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class LLMConfig:
    """Конфигурация локальных LLM"""

    # Ollama
    ollama_host: str = "http://localhost:11434"
    default_model: str = "llama3.2"

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
    max_context_length: int = 6000   # символов контекста в промпте
    num_ctx: int = 8192              # окно контекста модели (токены), передаётся Ollama
    temperature: float = 0.3
    timeout: int = 120               # таймаут ожидания очередной порции ответа, сек
    availability_timeout: float = 3.0


LLM_CONFIG = LLMConfig()


# ═══════════════════════════════════════════════════════════════════════════════
# WEB CONFIG
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class WebConfig:
    """Конфигурация веб-интерфейса"""

    host: str = "127.0.0.1"
    port: int = 8080
    debug: bool = False

    # Auth: токен передаётся в заголовке "Authorization: Bearer <token>",
    # "X-API-Key: <token>" или параметром ?token= (для WebSocket).
    enable_auth: bool = True
    secret_key: str = ""   # пусто = взять из ASTREX_API_TOKEN / ~/.astrex/.secret_key

    # Разрешённые значения заголовка Host (защита от DNS-rebinding)
    allowed_hosts: Tuple[str, ...] = ("127.0.0.1", "localhost", "::1", "[::1]")

    # CORS
    cors_origins: Tuple[str, ...] = ("http://localhost:3000",)

    # Ограничения сканирования через API
    max_concurrent_scans: int = 2
    blocked_paths: Tuple[str, ...] = (
        '/proc', '/sys', '/dev', '/boot', '/etc', '/root',
        '/run', '/var/log', '/var/run', '/usr/lib', '/usr/bin',
        '/usr/sbin', '/bin', '/sbin', '/lib', '/lib64',
        '~/.ssh', '~/.gnupg', '~/.astrex', '~/.password-store',
    )


WEB_CONFIG = WebConfig()


_SECRET_KEY_CACHE: Optional[str] = None


def _read_key_file(key_file: Path) -> Optional[str]:
    try:
        key = key_file.read_text(encoding='ascii').strip()
    except (OSError, UnicodeDecodeError):
        return None
    return key if len(key) >= 32 else None


def get_secret_key() -> str:
    """API-токен: конфиг → переменная окружения → файл (создаётся с правами 0600).

    Сгенерированный ключ запоминается в процессе: если файл записать нельзя,
    токен всё равно не меняется от запроса к запросу.
    """
    global _SECRET_KEY_CACHE
    if WEB_CONFIG.secret_key:
        return WEB_CONFIG.secret_key
    env_key = os.environ.get('ASTREX_API_TOKEN') or os.environ.get('ASTREX_SECRET_KEY')
    if env_key:
        return env_key
    if _SECRET_KEY_CACHE:
        return _SECRET_KEY_CACHE

    key_file = ASTREX_HOME / ".secret_key"
    key = _read_key_file(key_file)
    if key is None:
        key = secrets.token_hex(32)
        try:
            try:
                # O_EXCL: два процесса, стартовавшие одновременно, не получат разные ключи
                fd = os.open(str(key_file), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                existing = _read_key_file(key_file)
                if existing:
                    _SECRET_KEY_CACHE = existing
                    return existing
                fd = os.open(str(key_file), os.O_WRONLY | os.O_TRUNC, 0o600)   # повреждённый файл
            with os.fdopen(fd, 'w', encoding='ascii') as f:
                f.write(key)
            os.chmod(key_file, 0o600)
        except OSError as e:
            import logging
            logging.getLogger("astrex.config").warning(
                "Cannot persist API token to %s: %s (token is valid until restart)", key_file, e)
    _SECRET_KEY_CACHE = key
    return key


# ═══════════════════════════════════════════════════════════════════════════════
# YAML CONFIG LOADER
# ═══════════════════════════════════════════════════════════════════════════════

CONFIG_FILE = ASTREX_HOME / "config.yaml"


def _coerce(value: Any, current: Any) -> Any:
    """Привести значение из YAML к типу значения по умолчанию."""
    if isinstance(current, bool):
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() in ('1', 'true', 'yes', 'on'):
            return True
        if isinstance(value, str) and value.strip().lower() in ('0', 'false', 'no', 'off'):
            return False
        if isinstance(value, int):
            return bool(value)
        raise ValueError(f"expected bool, got {value!r}")
    if isinstance(current, int):
        if isinstance(value, bool):
            raise ValueError(f"expected int, got {value!r}")
        return int(value)
    if isinstance(current, float):
        return float(value)
    if isinstance(current, str):
        if not isinstance(value, (str, int, float)):
            raise ValueError(f"expected string, got {value!r}")
        return str(value)
    if isinstance(current, tuple):
        if isinstance(value, (list, tuple)):
            return tuple(value)
        if isinstance(value, str):
            return (value,)
        raise ValueError(f"expected list, got {value!r}")
    if isinstance(current, (set, frozenset)):
        if isinstance(value, (list, tuple, set)):
            return set(value)
        raise ValueError(f"expected list, got {value!r}")
    return value


def _apply_yaml_overrides(obj: Any, overrides: Dict[str, Any], section: str = "") -> None:
    """Применить значения из YAML поверх дефолтов dataclass (с проверкой типов)."""
    if not overrides or not isinstance(overrides, dict):
        return
    known = {f.name for f in fields(obj)}
    for key in overrides:
        if key not in known:
            CONFIG_WARNINGS.append(f"config.yaml: unknown option '{section}.{key}' ignored")
    for f in fields(obj):
        if f.name not in overrides:
            continue
        val = overrides[f.name]
        if val is None:
            continue
        try:
            setattr(obj, f.name, _coerce(val, getattr(obj, f.name)))
        except (TypeError, ValueError) as e:
            CONFIG_WARNINGS.append(f"config.yaml: invalid value for '{section}.{f.name}': {e}")


def load_config() -> None:
    """Загрузить конфигурацию из ~/.astrex/config.yaml если файл существует."""
    if not CONFIG_FILE.exists():
        return

    try:
        import yaml
    except ImportError:
        CONFIG_WARNINGS.append(
            f"{CONFIG_FILE} exists but PyYAML is not installed — file ignored "
            f"(pip install pyyaml)"
        )
        return

    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)
    except Exception as e:
        CONFIG_WARNINGS.append(f"Cannot read {CONFIG_FILE}: {e}")
        return

    if data is None:
        return
    if not isinstance(data, dict):
        CONFIG_WARNINGS.append(f"{CONFIG_FILE}: top level must be a mapping")
        return

    section_map = {
        'engine': ENGINE_CONFIG,
        'nlp': NLP_CONFIG,
        'vector': VECTOR_CONFIG,
        'llm': LLM_CONFIG,
        'web': WEB_CONFIG,
    }

    for section_name in data:
        if section_name not in section_map:
            CONFIG_WARNINGS.append(f"config.yaml: unknown section '{section_name}' ignored")

    for section_name, config_obj in section_map.items():
        if section_name in data:
            _apply_yaml_overrides(config_obj, data[section_name], section_name)


# Загружаем при импорте
load_config()


# ═══════════════════════════════════════════════════════════════════════════════
# EXPORT
# ═══════════════════════════════════════════════════════════════════════════════

__all__ = [
    'VERSION',
    'ASTREX_HOME', 'INDEX_DB_PATH', 'VECTOR_DB_PATH', 'CACHE_PATH', 'LOGS_PATH', 'TEMP_PATH',
    'FILE_TYPES', 'ENGINE_CONFIG', 'NLP_CONFIG', 'VECTOR_CONFIG', 'LLM_CONFIG', 'WEB_CONFIG',
    'load_config', 'CONFIG_FILE', 'CONFIG_WARNINGS', 'get_secret_key',
]
