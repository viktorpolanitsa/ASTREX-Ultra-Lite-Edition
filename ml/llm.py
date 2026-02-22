#!/usr/bin/env python3
"""
ASTREX v3.0 — LLM Integration
Интеграция с локальными LLM через Ollama
"""

import json
import threading
from typing import Optional, List, Dict, Any, Generator
from dataclasses import dataclass
import urllib.request
import urllib.error

from core.config import LLM_CONFIG


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


# ═══════════════════════════════════════════════════════════════════════════════
# OLLAMA CLIENT
# ═══════════════════════════════════════════════════════════════════════════════

class OllamaClient:
    """Клиент для Ollama API"""
    
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
        
        self.host = LLM_CONFIG.ollama_host
        self.default_model = LLM_CONFIG.default_model
        self.available = False
        self.models: List[str] = []
        
        self._check_availability()
        self._initialized = True
    
    def _check_availability(self) -> None:
        """Проверить доступность Ollama"""
        try:
            response = self._request("GET", "/api/tags")
            if response:
                data = json.loads(response)
                self.models = [m['name'] for m in data.get('models', [])]
                self.available = True
        except Exception:
            pass
    
    def _request(
        self,
        method: str,
        endpoint: str,
        data: Dict = None,
        timeout: int = None
    ) -> Optional[str]:
        """HTTP запрос к Ollama"""
        url = f"{self.host}{endpoint}"
        timeout = timeout or LLM_CONFIG.timeout
        
        try:
            if data:
                body = json.dumps(data).encode('utf-8')
                req = urllib.request.Request(
                    url,
                    data=body,
                    headers={'Content-Type': 'application/json'},
                    method=method
                )
            else:
                req = urllib.request.Request(url, method=method)
            
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode('utf-8')
                
        except urllib.error.URLError:
            return None
        except Exception:
            return None
    
    def generate(
        self,
        prompt: str,
        model: str = None,
        temperature: float = None,
        max_tokens: int = None,
        stream: bool = False
    ) -> LLMResponse:
        """Генерация текста"""
        if not self.available:
            return LLMResponse(
                text="",
                model="",
                error="Ollama not available"
            )
        
        model = model or self.default_model
        temperature = temperature if temperature is not None else LLM_CONFIG.temperature
        
        data = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": temperature
            }
        }
        
        if max_tokens:
            data["options"]["num_predict"] = max_tokens
        
        try:
            response = self._request("POST", "/api/generate", data)
            
            if response:
                result = json.loads(response)
                return LLMResponse(
                    text=result.get('response', ''),
                    model=model,
                    tokens_used=result.get('eval_count', 0)
                )
            
            return LLMResponse(text="", model=model, error="Empty response")
            
        except Exception as e:
            return LLMResponse(text="", model=model, error=str(e))
    
    def generate_stream(
        self,
        prompt: str,
        model: str = None,
        temperature: float = None
    ) -> Generator[str, None, None]:
        """Потоковая генерация текста"""
        if not self.available:
            return
        
        model = model or self.default_model
        temperature = temperature if temperature is not None else LLM_CONFIG.temperature
        
        data = {
            "model": model,
            "prompt": prompt,
            "stream": True,
            "options": {
                "temperature": temperature
            }
        }
        
        try:
            body = json.dumps(data).encode('utf-8')
            req = urllib.request.Request(
                f"{self.host}/api/generate",
                data=body,
                headers={'Content-Type': 'application/json'},
                method="POST"
            )
            
            with urllib.request.urlopen(req, timeout=LLM_CONFIG.timeout) as resp:
                for line in resp:
                    try:
                        chunk = json.loads(line.decode('utf-8'))
                        if 'response' in chunk:
                            yield chunk['response']
                        if chunk.get('done'):
                            break
                    except json.JSONDecodeError:
                        continue
                        
        except Exception:
            return
    
    def list_models(self) -> List[str]:
        """Список доступных моделей"""
        if not self.available:
            return []
        
        try:
            response = self._request("GET", "/api/tags")
            if response:
                data = json.loads(response)
                return [m['name'] for m in data.get('models', [])]
        except Exception:
            pass
        
        return self.models
    
    def pull_model(self, model: str) -> bool:
        """Скачать модель"""
        if not self.available:
            return False
        
        try:
            response = self._request(
                "POST",
                "/api/pull",
                {"name": model},
                timeout=600  # 10 minutes for download
            )
            return response is not None
        except Exception:
            return False


ollama_client = OllamaClient()


# ═══════════════════════════════════════════════════════════════════════════════
# ANALYSIS FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def summarize_text(text: str, max_length: int = 500) -> LLMResponse:
    """Суммаризация текста"""
    if not ollama_client.available:
        return LLMResponse(text="", model="", error="LLM not available")
    
    # Truncate if too long
    if len(text) > LLM_CONFIG.max_context_length:
        text = text[:LLM_CONFIG.max_context_length] + "..."
    
    prompt = LLM_CONFIG.summarize_prompt.format(text=text)
    
    return ollama_client.generate(
        prompt=prompt,
        max_tokens=max_length
    )


def analyze_connections(text: str) -> LLMResponse:
    """Анализ связей в тексте"""
    if not ollama_client.available:
        return LLMResponse(text="", model="", error="LLM not available")
    
    if len(text) > LLM_CONFIG.max_context_length:
        text = text[:LLM_CONFIG.max_context_length] + "..."
    
    prompt = LLM_CONFIG.analyze_connections_prompt.format(text=text)
    
    return ollama_client.generate(prompt=prompt)


def answer_question(question: str, context: str) -> LLMResponse:
    """Ответ на вопрос по контексту"""
    if not ollama_client.available:
        return LLMResponse(text="", model="", error="LLM not available")
    
    if len(context) > LLM_CONFIG.max_context_length:
        context = context[:LLM_CONFIG.max_context_length] + "..."
    
    prompt = f"""На основе следующего текста ответь на вопрос.

Текст:
{context}

Вопрос: {question}

Ответ:"""
    
    return ollama_client.generate(prompt=prompt)


def generate_dossier(
    entity: str,
    snippets: List[str],
    entities: Dict[str, List[str]]
) -> LLMResponse:
    """Генерация досье на сущность"""
    if not ollama_client.available:
        return LLMResponse(text="", model="", error="LLM not available")
    
    # Prepare context
    context_parts = [f"Информация о: {entity}\n"]
    
    # Add related entities
    if entities:
        context_parts.append("Связанные сущности:")
        for ent_type, ent_list in entities.items():
            if ent_list:
                context_parts.append(f"  {ent_type}: {', '.join(ent_list[:10])}")
    
    # Add snippets
    context_parts.append("\nФрагменты документов:")
    for i, snippet in enumerate(snippets[:10], 1):
        truncated = snippet[:500] if len(snippet) > 500 else snippet
        context_parts.append(f"\n[{i}] {truncated}")
    
    context = '\n'.join(context_parts)
    
    if len(context) > LLM_CONFIG.max_context_length:
        context = context[:LLM_CONFIG.max_context_length]
    
    prompt = f"""Составь подробное досье на основе собранной информации.
Включи: основные факты, связи с другими лицами/организациями, хронологию событий, финансовую информацию.

{context}

ДОСЬЕ:"""
    
    return ollama_client.generate(prompt=prompt, max_tokens=1000)


def classify_document(text: str, categories: List[str]) -> LLMResponse:
    """Классификация документа"""
    if not ollama_client.available:
        return LLMResponse(text="", model="", error="LLM not available")
    
    if len(text) > 2000:
        text = text[:2000] + "..."
    
    categories_str = ", ".join(categories)
    
    prompt = f"""Классифицируй документ по одной из категорий: {categories_str}

Документ:
{text}

Категория (только одно слово из списка):"""
    
    return ollama_client.generate(prompt=prompt, max_tokens=50)


# ═══════════════════════════════════════════════════════════════════════════════
# BATCH PROCESSING
# ═══════════════════════════════════════════════════════════════════════════════

def batch_summarize(texts: List[str], max_length: int = 200) -> List[LLMResponse]:
    """Батчевая суммаризация"""
    results = []
    
    for text in texts:
        result = summarize_text(text, max_length)
        results.append(result)
    
    return results


# ═══════════════════════════════════════════════════════════════════════════════
# STATUS
# ═══════════════════════════════════════════════════════════════════════════════

def get_llm_status() -> Dict[str, Any]:
    """Получить статус LLM"""
    return {
        "available": ollama_client.available,
        "host": ollama_client.host,
        "default_model": ollama_client.default_model,
        "models": ollama_client.models
    }


__all__ = [
    'OllamaClient', 'LLMResponse', 'ollama_client',
    'summarize_text', 'analyze_connections', 'answer_question',
    'generate_dossier', 'classify_document', 'batch_summarize',
    'get_llm_status'
]
