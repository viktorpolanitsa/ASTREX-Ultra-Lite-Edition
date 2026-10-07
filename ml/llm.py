#!/usr/bin/env python3
"""
ASTREX v3.0 — LLM Integration
Интеграция с локальными LLM через Ollama
"""

import json
import logging
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Optional, List, Dict, Any, Generator

from core.config import LLM_CONFIG

logger = logging.getLogger("astrex.llm")


# ═══════════════════════════════════════════════════════════════════════════════
# DATA CLASSES
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class LLMResponse:
    """Ответ LLM"""
    text: str
    model: str
    tokens_used: int = 0
    error: Optional[str] = None


class LLMError(Exception):
    pass


# ═══════════════════════════════════════════════════════════════════════════════
# OLLAMA CLIENT
# ═══════════════════════════════════════════════════════════════════════════════

class OllamaClient:
    """Клиент для Ollama API.

    Доступность проверяется лениво (не при импорте) и перепроверяется,
    если сервер был недоступен: Ollama можно запустить после старта ASTREX.
    """

    _instance = None
    _lock = threading.Lock()

    RECHECK_INTERVAL = 30.0

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
        self.host = LLM_CONFIG.ollama_host.rstrip('/')
        self.default_model = LLM_CONFIG.default_model
        self.models: List[str] = []
        self._available: Optional[bool] = None
        self._checked_at = 0.0
        self._check_lock = threading.Lock()
        self._initialized = True

    # ── availability ─────────────────────────────────────────────────────────

    def _check_availability(self) -> None:
        try:
            data = json.loads(self._request("GET", "/api/tags", timeout=LLM_CONFIG.availability_timeout))
            self.models = [m.get('name', '') for m in data.get('models', []) if m.get('name')]
            self._available = True
        except Exception as e:
            logger.debug(f"Ollama unavailable at {self.host}: {e}")
            self._available = False
        self._checked_at = time.monotonic()

    @property
    def available(self) -> bool:
        with self._check_lock:
            if self._available is None or (
                    not self._available and time.monotonic() - self._checked_at > self.RECHECK_INTERVAL):
                self._check_availability()
            return bool(self._available)

    def resolve_model(self, model: Optional[str]) -> str:
        """Модель по умолчанию; если её нет в Ollama — первая установленная."""
        model = model or self.default_model
        if self.models:
            names = set(self.models) | {m.split(':')[0] for m in self.models}
            if model not in names and f"{model}:latest" not in self.models:
                fallback = self.models[0]
                logger.warning(f"Model '{model}' is not installed in Ollama; using '{fallback}'")
                return fallback
        return model

    # ── HTTP ─────────────────────────────────────────────────────────────────

    def _make_request(self, method: str, endpoint: str, data: Dict = None) -> urllib.request.Request:
        url = f"{self.host}{endpoint}"
        if data is not None:
            return urllib.request.Request(url, data=json.dumps(data).encode('utf-8'),
                                          headers={'Content-Type': 'application/json'}, method=method)
        return urllib.request.Request(url, method=method)

    @staticmethod
    def _http_error_message(e: urllib.error.HTTPError) -> str:
        try:
            body = e.read().decode('utf-8', errors='replace')
            message = json.loads(body).get('error') or body
        except Exception:
            message = str(e)
        return f"HTTP {e.code}: {message}"

    def _request(self, method: str, endpoint: str, data: Dict = None, timeout: float = None) -> str:
        """HTTP запрос к Ollama (исключение LLMError с понятным текстом при ошибке)."""
        timeout = timeout or LLM_CONFIG.timeout
        try:
            with urllib.request.urlopen(self._make_request(method, endpoint, data), timeout=timeout) as resp:
                return resp.read().decode('utf-8')
        except urllib.error.HTTPError as e:
            raise LLMError(self._http_error_message(e)) from e
        except urllib.error.URLError as e:
            raise LLMError(f"Ollama is not reachable at {self.host}: {e.reason}") from e
        except TimeoutError as e:
            raise LLMError(f"Ollama request timed out after {timeout}s") from e

    def _stream(self, data: Dict) -> Generator[Dict[str, Any], None, None]:
        """Потоковый вызов /api/generate: таймаут действует на каждую порцию, а не на весь ответ."""
        try:
            with urllib.request.urlopen(self._make_request("POST", "/api/generate", data),
                                        timeout=LLM_CONFIG.timeout) as resp:
                for line in resp:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        chunk = json.loads(line.decode('utf-8'))
                    except json.JSONDecodeError:
                        continue
                    if chunk.get('error'):
                        raise LLMError(str(chunk['error']))
                    yield chunk
                    if chunk.get('done'):
                        break
        except urllib.error.HTTPError as e:
            raise LLMError(self._http_error_message(e)) from e
        except urllib.error.URLError as e:
            raise LLMError(f"Ollama is not reachable at {self.host}: {e.reason}") from e
        except TimeoutError as e:
            raise LLMError(f"Ollama did not respond within {LLM_CONFIG.timeout}s") from e

    def _payload(self, prompt: str, model: str, temperature: Optional[float],
                 max_tokens: Optional[int]) -> Dict[str, Any]:
        options: Dict[str, Any] = {
            "temperature": temperature if temperature is not None else LLM_CONFIG.temperature,
        }
        if LLM_CONFIG.num_ctx:
            options["num_ctx"] = LLM_CONFIG.num_ctx
        if max_tokens:
            options["num_predict"] = max_tokens
        return {"model": model, "prompt": prompt, "stream": True, "options": options}

    # ── public API ───────────────────────────────────────────────────────────

    def generate(self, prompt: str, model: str = None, temperature: float = None,
                 max_tokens: int = None, stream: bool = False) -> LLMResponse:
        """Генерация текста (полный ответ)"""
        if not self.available:
            return LLMResponse(text="", model="", error=f"Ollama not available at {self.host}")

        model = self.resolve_model(model)
        parts: List[str] = []
        tokens = 0
        try:
            for chunk in self._stream(self._payload(prompt, model, temperature, max_tokens)):
                parts.append(chunk.get('response', ''))
                if chunk.get('done'):
                    tokens = chunk.get('eval_count', 0) or 0
            text = ''.join(parts)
            if not text.strip():
                return LLMResponse(text="", model=model, error="Model returned an empty response")
            return LLMResponse(text=text, model=model, tokens_used=tokens)
        except LLMError as e:
            return LLMResponse(text=''.join(parts), model=model, error=str(e))
        except Exception as e:
            return LLMResponse(text=''.join(parts), model=model, error=f"{type(e).__name__}: {e}")

    def generate_stream(self, prompt: str, model: str = None,
                        temperature: float = None) -> Generator[str, None, None]:
        """Потоковая генерация текста"""
        if not self.available:
            yield f"[Ошибка: Ollama недоступна по адресу {self.host}]"
            return
        model = self.resolve_model(model)
        try:
            for chunk in self._stream(self._payload(prompt, model, temperature, None)):
                if chunk.get('response'):
                    yield chunk['response']
        except LLMError as e:
            yield f"\n[Ошибка: {e}]"

    def list_models(self) -> List[str]:
        """Список доступных моделей"""
        if self.available:
            try:
                data = json.loads(self._request("GET", "/api/tags", timeout=LLM_CONFIG.availability_timeout))
                self.models = [m.get('name', '') for m in data.get('models', []) if m.get('name')]
            except Exception:
                pass
        return list(self.models)

    def pull_model(self, model: str) -> bool:
        """Скачать модель"""
        if not self.available:
            return False
        try:
            self._request("POST", "/api/pull", {"model": model, "name": model, "stream": False}, timeout=3600)
            self.list_models()
            return True
        except Exception as e:
            logger.error(f"Model pull failed: {e}")
            return False


ollama_client = OllamaClient()


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "..."


# ═══════════════════════════════════════════════════════════════════════════════
# ANALYSIS FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def summarize_text(text: str, max_length: int = 500) -> LLMResponse:
    """Суммаризация текста"""
    prompt = LLM_CONFIG.summarize_prompt.format(text=_truncate(text, LLM_CONFIG.max_context_length))
    return ollama_client.generate(prompt=prompt, max_tokens=max_length)


def analyze_connections(text: str) -> LLMResponse:
    """Анализ связей в тексте"""
    prompt = LLM_CONFIG.analyze_connections_prompt.format(text=_truncate(text, LLM_CONFIG.max_context_length))
    return ollama_client.generate(prompt=prompt)


def answer_question(question: str, context: str) -> LLMResponse:
    """Ответ на вопрос по контексту"""
    prompt = f"""На основе следующего текста ответь на вопрос.

Текст:
{_truncate(context, LLM_CONFIG.max_context_length)}

Вопрос: {question}

Ответ:"""
    return ollama_client.generate(prompt=prompt)


def generate_dossier(entity: str, snippets: List[str], entities: Dict[str, List[str]]) -> LLMResponse:
    """Генерация досье на сущность"""
    context_parts = [f"Информация о: {entity}\n"]
    if entities:
        context_parts.append("Связанные сущности:")
        for ent_type, ent_list in entities.items():
            if ent_list:
                context_parts.append(f"  {ent_type}: {', '.join(map(str, ent_list[:10]))}")
    context_parts.append("\nФрагменты документов:")
    for i, snippet in enumerate(snippets[:10], 1):
        context_parts.append(f"\n[{i}] {snippet[:500]}")

    context = _truncate('\n'.join(context_parts), LLM_CONFIG.max_context_length)
    prompt = f"""Составь подробное досье на основе собранной информации.
Включи: основные факты, связи с другими лицами/организациями, хронологию событий, финансовую информацию.

{context}

ДОСЬЕ:"""
    return ollama_client.generate(prompt=prompt, max_tokens=1000)


def classify_document(text: str, categories: List[str]) -> LLMResponse:
    """Классификация документа"""
    prompt = f"""Классифицируй документ по одной из категорий: {", ".join(categories)}

Документ:
{_truncate(text, 2000)}

Категория (только одно слово из списка):"""
    return ollama_client.generate(prompt=prompt, max_tokens=50)


# ═══════════════════════════════════════════════════════════════════════════════
# BATCH PROCESSING
# ═══════════════════════════════════════════════════════════════════════════════

def batch_summarize(texts: List[str], max_length: int = 200) -> List[LLMResponse]:
    """Батчевая суммаризация"""
    return [summarize_text(text, max_length) for text in texts]


# ═══════════════════════════════════════════════════════════════════════════════
# STATUS
# ═══════════════════════════════════════════════════════════════════════════════

def get_llm_status() -> Dict[str, Any]:
    """Получить статус LLM"""
    available = ollama_client.available
    return {
        "available": available,
        "host": ollama_client.host,
        "default_model": ollama_client.default_model,
        "models": list(ollama_client.models),
    }


__all__ = [
    'OllamaClient', 'LLMResponse', 'ollama_client',
    'summarize_text', 'analyze_connections', 'answer_question',
    'generate_dossier', 'classify_document', 'batch_summarize',
    'get_llm_status'
]
