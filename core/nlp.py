#!/usr/bin/env python3
"""
ASTREX v3.0 — NLP Engine (Enhanced)
Расширенный NLP с морфологией, fuzzy search и нейросетями

Улучшения v3.1:
- LRU-кэширование морфологии (50k слов)
- N-грамм расширение запросов (биграммы)
- Русский суффиксный стеммер (fallback)
- FuzzyMatcher: WRatio, скользящее окно, триграмм-Jaccard fallback
- NER: URL, IP, СНИЛС, авто-номера, латинские имена
- Нормализация сущностей (кавычки, пробелы)
- RelevanceCalculator: TF-IDF, proximity score, chunked SBERT
"""

import re
import math
import sys
import threading
from typing import Dict, List, Optional, Set, Tuple, Any
from dataclasses import dataclass, field
from functools import lru_cache
from collections import Counter

from .config import NLP_CONFIG, ENGINE_CONFIG


# ═══════════════════════════════════════════════════════════════════════════════
# RUSSIAN STEMMER (fallback when pymorphy2 unavailable)
# ═══════════════════════════════════════════════════════════════════════════════

_RU_VOWELS = set('аеёиоуыэюя')
_RU_PERFECTIVE_GERUND = re.compile(r'(ив|ивши|ившись|ыв|ывши|ывшись)$')
_RU_ADJECTIVE = re.compile(r'(ее|ие|ые|ое|ими|ыми|ей|ий|ый|ой|ем|им|ым|ом|его|ого|ему|ому|их|ых|ую|юю|ая|яя|ою|ею)$')
_RU_PARTICIPLE = re.compile(r'(ивш|ывш|ующ)$')
_RU_VERB = re.compile(r'(ила|ыла|ена|ейте|уйте|ите|или|ыли|ей|уй|ил|ыл|им|ым|ен|ило|ыло|ено|нно|ет|ует|ит|ыт|ят|ны|ть|ешь|ишь)$')
_RU_NOUN = re.compile(r'(а|ев|ов|ие|ье|е|иями|ями|ами|еи|ии|и|ией|ей|ой|ий|й|иям|ям|ием|ем|ам|ом|о|у|ах|иях|ях|ы|ь|ию|ью|ю|ия|ья|я)$')
_RU_DERIVATIONAL = re.compile(r'(ост|ость)$')
_RU_SUPERLATIVE = re.compile(r'(ейше|ейш)$')


def _stem_russian(word: str) -> str:
    """Простой суффиксный стеммер для русского (алгоритм Портера)"""
    word = word.lower().strip()
    if len(word) < 4:
        return word

    # Find RV region (after first vowel)
    rv_pos = 0
    for i, ch in enumerate(word):
        if ch in _RU_VOWELS:
            rv_pos = i + 1
            break
    if rv_pos == 0:
        return word

    rv = word[rv_pos:]

    # Step 1: Remove perfective gerund, reflexive, adjective, verb, noun
    result = rv
    for pat in [_RU_PERFECTIVE_GERUND, _RU_ADJECTIVE, _RU_PARTICIPLE,
                _RU_VERB, _RU_NOUN]:
        m = pat.search(result)
        if m:
            result = result[:m.start()]
            break

    # Step 2: Remove и at the end
    if result.endswith('и'):
        result = result[:-1]

    # Step 3: Remove derivational suffix
    m = _RU_DERIVATIONAL.search(result)
    if m:
        result = result[:m.start()]

    # Step 4: Remove superlative, double н
    m = _RU_SUPERLATIVE.search(result)
    if m:
        result = result[:m.start()]
    if result.endswith('нн'):
        result = result[:-1]

    return word[:rv_pos] + result


# ═══════════════════════════════════════════════════════════════════════════════
# MORPHOLOGY (pymorphy2 with caching)
# ═══════════════════════════════════════════════════════════════════════════════

@lru_cache(maxsize=50000)
def _cached_normalize(word: str, morph, available: bool) -> str:
    """Нормализация слова (лемматизация) — кэшируется"""
    if not word:
        return word.lower()

    if available:
        try:
            parsed = morph.parse(word)
            if parsed:
                return parsed[0].normal_form
        except Exception:
            pass

    # Fallback: русский стеммер
    if any(c in word.lower() for c in 'абвгдежзиклмнопрстуфхцчшщэюя'):
        return _stem_russian(word)

    return word.lower()


@lru_cache(maxsize=20000)
def _cached_get_all_forms(word: str, morph, available: bool) -> frozenset:
    """Получить все формы слова — кэшируется (frozenset для hashability)"""
    forms = {word.lower()}

    if available:
        try:
            parsed = morph.parse(word)
            if parsed:
                lexeme = parsed[0].lexeme
                for form in lexeme:
                    forms.add(form.word.lower())
        except Exception:
            pass
    else:
        # Fallback: stem и несколько суффиксных вариаций
        stem = _stem_russian(word)
        forms.add(stem)

    return frozenset(forms)


class MorphologyAnalyzer:
    """Морфологический анализатор на базе pymorphy2 с LRU-кэшем"""

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

        self.morph = None
        self.available = False

        if NLP_CONFIG.use_pymorphy:
            self._init_pymorphy()

        self._initialized = True

    def _init_pymorphy(self) -> None:
        try:
            import pymorphy2
            self.morph = pymorphy2.MorphAnalyzer()
            self.available = True
        except ImportError:
            pass

    def normalize(self, word: str) -> str:
        """Нормализация слова (лемматизация) — кэшируется"""
        return _cached_normalize(word, self.morph, self.available)

    def get_all_forms(self, word: str) -> frozenset:
        """Получить все формы слова — кэшируется (frozenset для hashability)"""
        return _cached_get_all_forms(word, self.morph, self.available)

    def expand_query(self, query: str) -> Set[str]:
        """Расширить поисковый запрос всеми формами слов + биграммы"""
        words = re.findall(r'\b\w+\b', query.lower())
        expanded = set()

        # Морфологические формы каждого слова
        for word in words:
            if len(word) >= 3:
                forms = self.get_all_forms(word)
                expanded.update(forms)
            else:
                expanded.add(word)

        # Биграммы из оригинальных слов (фразовый поиск)
        if len(words) >= 2:
            for i in range(len(words) - 1):
                bigram = f"{words[i]} {words[i+1]}"
                expanded.add(bigram)
                # Также нормализованная биграмма
                norm_bigram = f"{self.normalize(words[i])} {self.normalize(words[i+1])}"
                if norm_bigram != bigram:
                    expanded.add(norm_bigram)

        # Оригинальный запрос как фраза
        expanded.add(query.lower())

        return expanded

    def is_name(self, word: str) -> bool:
        """Проверить, является ли слово именем собственным"""
        if not self.available:
            return word[0].isupper() if word else False

        try:
            parsed = self.morph.parse(word)
            if parsed:
                return 'Name' in parsed[0].tag or 'Surn' in parsed[0].tag
        except Exception:
            pass

        return False


morph_analyzer = MorphologyAnalyzer()


# ═══════════════════════════════════════════════════════════════════════════════
# FUZZY SEARCH (Enhanced)
# ═══════════════════════════════════════════════════════════════════════════════

def _trigram_set(s: str) -> Set[str]:
    """Построить множество символьных триграмм"""
    s = f"  {s.lower()}  "
    return {s[i:i+3] for i in range(len(s) - 2)}


def _trigram_jaccard(s1: str, s2: str) -> float:
    """Jaccard similarity на символьных триграммах (0-100)"""
    t1 = _trigram_set(s1)
    t2 = _trigram_set(s2)
    if not t1 or not t2:
        return 0.0
    intersection = len(t1 & t2)
    union = len(t1 | t2)
    return (intersection / union) * 100 if union > 0 else 0.0


class FuzzyMatcher:
    """Нечёткий поиск с использованием rapidfuzz + fallback на триграммы"""

    _available: Optional[bool] = None

    @classmethod
    def is_available(cls) -> bool:
        if cls._available is None:
            try:
                import rapidfuzz
                cls._available = True
            except ImportError:
                cls._available = False
        return cls._available

    @classmethod
    def ratio(cls, s1: str, s2: str) -> float:
        """Коэффициент схожести двух строк (0-100)"""
        if cls.is_available():
            from rapidfuzz import fuzz
            return fuzz.ratio(s1.lower(), s2.lower())
        return _trigram_jaccard(s1, s2)

    @classmethod
    def partial_ratio(cls, s1: str, s2: str) -> float:
        """Частичное совпадение"""
        if cls.is_available():
            from rapidfuzz import fuzz
            return fuzz.partial_ratio(s1.lower(), s2.lower())
        # Fallback: substring check + trigram
        s1l, s2l = s1.lower(), s2.lower()
        if s1l in s2l or s2l in s1l:
            return 100.0
        return _trigram_jaccard(s1, s2)

    @classmethod
    def wratio(cls, s1: str, s2: str) -> float:
        """Weighted ratio — лучший универсальный scorer"""
        if cls.is_available():
            from rapidfuzz import fuzz
            return fuzz.WRatio(s1.lower(), s2.lower())
        return _trigram_jaccard(s1, s2)

    @classmethod
    def token_set_ratio(cls, s1: str, s2: str) -> float:
        """Сравнение по токенам (игнорируя порядок)"""
        if cls.is_available():
            from rapidfuzz import fuzz
            return fuzz.token_set_ratio(s1.lower(), s2.lower())
        else:
            tokens1 = set(s1.lower().split())
            tokens2 = set(s2.lower().split())
            if not tokens1 or not tokens2:
                return 0.0
            intersection = len(tokens1 & tokens2)
            return (intersection / max(len(tokens1), len(tokens2))) * 100

    @classmethod
    def find_matches(
        cls,
        query: str,
        candidates: List[str],
        threshold: int = None,
        limit: int = 10
    ) -> List[Tuple[str, float]]:
        """Найти похожие строки (WRatio для лучшего качества)"""
        if threshold is None:
            threshold = ENGINE_CONFIG.fuzzy_threshold

        if cls.is_available():
            from rapidfuzz import process, fuzz

            results = process.extract(
                query,
                candidates,
                scorer=fuzz.WRatio,
                limit=limit
            )

            return [(r[0], r[1]) for r in results if r[1] >= threshold]
        else:
            # Trigram Jaccard fallback
            matches = []
            for candidate in candidates:
                score = _trigram_jaccard(query, candidate)
                if score >= threshold:
                    matches.append((candidate, score))
            return sorted(matches, key=lambda x: -x[1])[:limit]

    @classmethod
    def fuzzy_find_in_text(
        cls,
        query: str,
        text: str,
        window: int = 200,
        step: int = 100,
        max_scan: int = 20000,
        threshold: int = None,
    ) -> Tuple[bool, int, float]:
        """Скользящее окно по тексту для нечёткого поиска.

        Returns:
            (found, position, score)
        """
        if threshold is None:
            threshold = ENGINE_CONFIG.fuzzy_threshold

        text_lower = text[:max_scan].lower()
        query_lower = query.lower()
        best_score = 0.0
        best_pos = -1

        for start in range(0, len(text_lower) - window + 1, step):
            chunk = text_lower[start:start + window]
            if cls.is_available():
                from rapidfuzz import fuzz
                score = fuzz.partial_ratio(query_lower, chunk)
            else:
                score = _trigram_jaccard(query_lower, chunk)

            if score > best_score:
                best_score = score
                best_pos = start

            if score >= 95:  # early exit on near-perfect match
                break

        return (best_score >= threshold, best_pos, best_score)


# ═══════════════════════════════════════════════════════════════════════════════
# ENTITY PATTERNS (Enhanced)
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class EntityPatterns:
    """Regex паттерны для NER (расширенные)"""

    PERSON: List[re.Pattern] = field(default_factory=lambda: [
        # ФИО: Иванов Иван Иванович
        re.compile(r'\b[А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+(?:\s+[А-ЯЁ][а-яё]+)?\b'),
        # Инициалы
        re.compile(r'\b[А-ЯЁ][а-яё]+\s+[А-ЯЁ]\.\s?[А-ЯЁ]\.\b'),
        re.compile(r'\b[А-ЯЁ]\.\s?[А-ЯЁ]\.\s+[А-ЯЁ][а-яё]+\b'),
        # Латинские имена: John Smith, Jean-Pierre Dupont
        re.compile(r'\b[A-Z][a-z]+(?:-[A-Z][a-z]+)?\s+[A-Z][a-z]+(?:-[A-Z][a-z]+)?\b'),
    ])

    ORG: List[re.Pattern] = field(default_factory=lambda: [
        re.compile(r'\b(?:ООО|ОАО|ЗАО|ПАО|АО|ИП|ФГУП|ГУП|МУП|НКО|ТОО|КФХ|ПБОЮЛ)\s*[«"\']?[А-ЯЁа-яё][А-ЯЁа-яё\s\-\.]+[»"\']?\b'),
        re.compile(r'\b(?:Банк|банк|БАНК)\s+[«"\']?[А-ЯЁ][А-ЯЁа-яё\s\-]+[»"\']?\b'),
        re.compile(r'\b(?:Газпром|Роснефть|Сбербанк|ВТБ|Лукойл|РЖД|Ростелеком|МТС|Билайн|Мегафон|Яндекс|Тинькофф|Альфа-Банк|Росатом|Ростех|Аэрофлот|Норникель|Магнит|X5|Wildberries|Ozon)\b', re.IGNORECASE),
        re.compile(r'\b(?:Министерство|министерство|ФСБ|МВД|ФНС|ФТС|ФАС|ФСО|СВР|ФСИН|Росреестр|Роспотребнадзор|Роскомнадзор|Центробанк|ЦБ РФ)\b'),
        re.compile(r'\b(?:Google|Microsoft|Apple|Amazon|Meta|Facebook|Twitter|Tesla|OpenAI|Anthropic|Netflix|Samsung|Intel|AMD|NVIDIA|IBM|Oracle|SAP|Salesforce)\b', re.IGNORECASE),
    ])

    LOCATION: List[re.Pattern] = field(default_factory=lambda: [
        re.compile(r'\b(?:г\.|город|гор\.)\s*[А-ЯЁ][а-яё\-]+\b'),
        re.compile(r'\b[А-ЯЁ][а-яё]+(?:ская|ский|ское|ная|ный|ное)\s+(?:область|край|республика|округ|район|улица|проспект|площадь)\b'),
        re.compile(r'\b(?:ул\.|улица|пр\.|проспект|пер\.|переулок|б-р|бульвар|наб\.|набережная|ш\.|шоссе|пл\.|площадь)\s*[А-ЯЁа-яё][А-ЯЁа-яё\s\-]+\b'),
        re.compile(r'\bд\.\s*\d+[а-яА-Я]?(?:\s*корп?\.\s*\d+)?(?:\s*кв\.\s*\d+)?\b'),
        # Major cities
        re.compile(r'\b(?:Москва|Санкт-Петербург|Новосибирск|Екатеринбург|Казань|Нижний\s*Новгород|Челябинск|Самара|Омск|Ростов-на-Дону|Уфа|Красноярск|Воронеж|Пермь|Волгоград|Краснодар|Саратов|Тюмень|Тольятти|Ижевск|Барнаул|Владивосток|Хабаровск|Калининград|Сочи|Иркутск|Ярославль|Махачкала|Томск|Оренбург|Кемерово|Новокузнецк|Рязань|Астрахань|Пенза|Липецк|Киров|Чебоксары|Тула|Калуга|Брянск|Курск|Белгород|Владимир|Сургут|Архангельск|Симферополь|Севастополь)\b'),
    ])

    DATE: List[re.Pattern] = field(default_factory=lambda: [
        re.compile(r'\b\d{1,2}[\.\/\-]\d{1,2}[\.\/\-]\d{2,4}\b'),
        re.compile(r'\b\d{4}[\.\/\-]\d{1,2}[\.\/\-]\d{1,2}\b'),
        re.compile(r'\b\d{1,2}\s+(?:января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)\s+\d{4}\b', re.IGNORECASE),
        re.compile(r'\b(?:январь|февраль|март|апрель|май|июнь|июль|август|сентябрь|октябрь|ноябрь|декабрь)\s+\d{4}\b', re.IGNORECASE),
    ])

    MONEY: List[re.Pattern] = field(default_factory=lambda: [
        re.compile(r'\b\d[\d\s,.]*(?:руб\.?|₽|рублей|рубля|рубль|р\.)\b', re.IGNORECASE),
        re.compile(r'(?:\$|€|£|¥|USD|EUR|GBP|CNY)\s*\d[\d\s,.]*\b'),
        re.compile(r'\b\d[\d\s,.]*\s*(?:\$|€|£|¥|USD|EUR|GBP|долларов|евро|фунтов|юаней)\b', re.IGNORECASE),
        re.compile(r'\b\d[\d\s,.]*\s*(?:млн|млрд|трлн|тыс\.?|миллион\w*|миллиард\w*|триллион\w*|тысяч\w*)\b', re.IGNORECASE),
    ])

    CONTACT: List[re.Pattern] = field(default_factory=lambda: [
        # Phone
        re.compile(r'(?:\+7|8)[\s\-]?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}\b'),
        re.compile(r'\b\d{3}[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}\b'),
        # Email
        re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'),
        # INN/OGRN/KPP/BIK
        re.compile(r'\bИНН[\s:]*(\d{10}|\d{12})\b'),
        re.compile(r'\bОГРН[\s:]*\d{13,15}\b'),
        re.compile(r'\bКПП[\s:]*\d{9}\b'),
        re.compile(r'\bБИК[\s:]*\d{9}\b'),
        # СНИЛС
        re.compile(r'\bСНИЛС[\s:]*\d{3}[\s\-]?\d{3}[\s\-]?\d{3}[\s\-]?\d{2}\b'),
        # Passport
        re.compile(r'\b\d{2}\s*\d{2}\s+\d{6}\b'),
    ])

    DOCUMENT: List[re.Pattern] = field(default_factory=lambda: [
        re.compile(r'\b(?:договор|контракт|соглашение|акт|счёт|счет|накладная|доверенность|заявление|приказ|распоряжение|протокол|решение)\s*(?:№|номер|N)?\s*[\d\-/А-Яа-я]+\b', re.IGNORECASE),
        re.compile(r'\b(?:от|дата)\s*\d{1,2}[\.\/]\d{1,2}[\.\/]\d{2,4}\b', re.IGNORECASE),
    ])

    # Новые паттерны
    TECHNICAL: List[re.Pattern] = field(default_factory=lambda: [
        # URL
        re.compile(r'https?://[^\s<>"\']+', re.IGNORECASE),
        # IP address
        re.compile(r'\b(?:(?:25[0-5]|2[0-4]\d|1\d{2}|[1-9]?\d)\.){3}(?:25[0-5]|2[0-4]\d|1\d{2}|[1-9]?\d)\b'),
        # Российские авто-номера
        re.compile(r'\b[АВЕКМНОРСТУХABEKMHOPCTYX]\d{3}[АВЕКМНОРСТУХABEKMHOPCTYX]{2}\s?\d{2,3}\b', re.IGNORECASE),
        # Номера уголовных/гражданских дел
        re.compile(r'\b(?:дело|д\.)\s*(?:№|N)?\s*\d[\d\-/]+\b', re.IGNORECASE),
    ])


PATTERNS = EntityPatterns()


# ═══════════════════════════════════════════════════════════════════════════════
# ENTITY NORMALIZER
# ═══════════════════════════════════════════════════════════════════════════════

# Убрать кавычки и нормализовать пробелы
_QUOTE_STRIP = re.compile(r'[«»""\'\"]+')


def _normalize_entity(text: str) -> str:
    """Нормализовать текст сущности"""
    text = _QUOTE_STRIP.sub('', text)
    text = ' '.join(text.split())
    return text.strip()


# ═══════════════════════════════════════════════════════════════════════════════
# NER ENGINE (Enhanced)
# ═══════════════════════════════════════════════════════════════════════════════

class EntityExtractor:
    """Извлечение именованных сущностей (расширенное)"""

    _MAX_REGEX_TEXT_LEN = 100_000

    def __init__(self):
        self.nlp = None
        self._spacy_lock = threading.Lock()
        self._init_spacy()

    def _init_spacy(self) -> None:
        """Инициализация spaCy с поддержкой GPU"""
        if sys.version_info >= (3, 14):
            return

        try:
            import spacy

            if NLP_CONFIG.use_gpu:
                try:
                    from .gpu import get_device
                    device = get_device()
                    if device.backend in ("cuda", "rocm"):
                        spacy.prefer_gpu(device.device_id)
                except Exception:
                    pass

            for model in NLP_CONFIG.spacy_models:
                try:
                    self.nlp = spacy.load(model, disable=['parser'])
                    break
                except OSError:
                    continue
        except ImportError:
            pass

    def extract(self, text: str) -> Dict[str, List[str]]:
        """Извлечь все сущности из текста"""
        if not text or len(text) < 2:
            return {}

        text = text[:NLP_CONFIG.max_text_length]

        # Combine spaCy and regex results
        entities: Dict[str, List[str]] = {}

        # spaCy NER
        spacy_entities = {}
        if self.nlp:
            spacy_entities = self._extract_spacy(text)
            for key, values in spacy_entities.items():
                entities.setdefault(key, []).extend(values)

        # Regex NER
        regex_entities = self._extract_regex(text)
        for key, values in regex_entities.items():
            entities.setdefault(key, []).extend(values)

        # Deduplicate and filter with cross-validation scoring
        return self._deduplicate(entities, spacy_entities)

    def _extract_spacy(self, text: str) -> Dict[str, List[str]]:
        """spaCy NER (thread-safe)"""
        entities: Dict[str, List[str]] = {}

        try:
            with self._spacy_lock:
                doc = self.nlp(text)

            label_map = {
                'PER': 'PERSONS',
                'ORG': 'ORGANIZATIONS',
                'LOC': 'LOCATIONS',
                'GPE': 'LOCATIONS',
                'DATE': 'DATES',
                'MONEY': 'MONEY',
            }

            for ent in doc.ents:
                key = label_map.get(ent.label_)
                if key:
                    entities.setdefault(key, []).append(ent.text.strip())
        except Exception:
            pass

        return entities

    def _extract_regex(self, text: str) -> Dict[str, List[str]]:
        """Regex-based NER с ограничением длины текста"""
        text = text[:self._MAX_REGEX_TEXT_LEN]
        entities: Dict[str, List[str]] = {
            'PERSONS': [],
            'ORGANIZATIONS': [],
            'LOCATIONS': [],
            'DATES': [],
            'MONEY': [],
            'CONTACTS': [],
            'DOCUMENTS': [],
            'TECHNICAL': [],
        }

        pattern_map = [
            (PATTERNS.PERSON, 'PERSONS'),
            (PATTERNS.ORG, 'ORGANIZATIONS'),
            (PATTERNS.LOCATION, 'LOCATIONS'),
            (PATTERNS.DATE, 'DATES'),
            (PATTERNS.MONEY, 'MONEY'),
            (PATTERNS.CONTACT, 'CONTACTS'),
            (PATTERNS.DOCUMENT, 'DOCUMENTS'),
            (PATTERNS.TECHNICAL, 'TECHNICAL'),
        ]

        for patterns, key in pattern_map:
            for pattern in patterns:
                if len(entities[key]) >= NLP_CONFIG.max_entities_per_type:
                    break
                matches = pattern.findall(text)
                if matches:
                    for m in matches:
                        if len(entities[key]) >= NLP_CONFIG.max_entities_per_type:
                            break
                        if isinstance(m, tuple):
                            if len(m) == 0:
                                continue
                            m = next((x for x in m if x), '')
                        if m and str(m).strip():
                            entities[key].append(str(m).strip())

        return entities

    def _deduplicate(
        self,
        entities: Dict[str, List[str]],
        spacy_entities: Dict[str, List[str]] = None,
    ) -> Dict[str, List[str]]:
        """Дедупликация с нормализацией и кросс-валидацией"""
        if spacy_entities is None:
            spacy_entities = {}

        # Build spaCy set for cross-validation
        spacy_set: Set[str] = set()
        for vals in spacy_entities.values():
            for v in vals:
                spacy_set.add(_normalize_entity(v).lower())

        result: Dict[str, List[str]] = {}

        for key, values in entities.items():
            seen: Set[str] = set()
            scored: List[Tuple[str, float]] = []

            for v in values:
                v = _normalize_entity(v)
                v_norm = ' '.join(v.split())
                v_lower = v_norm.lower()

                if len(v_norm) < NLP_CONFIG.min_entity_length:
                    continue
                if v_lower in seen:
                    continue

                # Skip common false positives
                if v_lower in {'рф', 'ру', 'ru', 'рос'} and key == 'ORGANIZATIONS':
                    continue

                seen.add(v_lower)

                # Cross-validation: boost if both spaCy and regex found it
                score = 1.0
                if v_lower in spacy_set:
                    score = 2.0

                scored.append((v_norm, score))

            if scored:
                # Sort by score descending
                scored.sort(key=lambda x: -x[1])
                result[key] = [s[0] for s in scored[:NLP_CONFIG.max_entities_per_type]]

        return result


# ═══════════════════════════════════════════════════════════════════════════════
# RELEVANCE CALCULATOR (Enhanced)
# ═══════════════════════════════════════════════════════════════════════════════

class RelevanceCalculator:
    """Вычисление релевантности: TF-IDF + proximity + morphology + SBERT"""

    def __init__(self):
        self._sbert_model = None
        self._sbert_initialized = False
        self._sbert_lock = threading.Lock()

    @property
    def sbert_model(self):
        """Ленивая инициализация SBERT модели"""
        if not self._sbert_initialized:
            with self._sbert_lock:
                if not self._sbert_initialized:
                    try:
                        from sentence_transformers import SentenceTransformer
                        from .gpu import get_device
                        device = get_device()
                        self._sbert_model = SentenceTransformer(
                            NLP_CONFIG.sbert_model,
                            device=device.torch_device,
                        )
                    except ImportError:
                        self._sbert_model = None
                    except Exception:
                        self._sbert_model = None
                    finally:
                        self._sbert_initialized = True
        return self._sbert_model

    def calculate(self, text: str, query: str) -> float:
        """Вычислить релевантность текста запросу"""
        if not text or not query:
            return 0.0

        text = text[:NLP_CONFIG.max_text_length]

        # Try SBERT first
        if self.sbert_model:
            try:
                return self._sbert_similarity(text, query)
            except Exception:
                pass

        # Enhanced keyword + morphology + TF-IDF + proximity
        return self._enhanced_similarity(text, query)

    def _sbert_similarity(self, text: str, query: str) -> float:
        """SBERT cosine similarity с chunked scoring для длинных текстов"""
        from sentence_transformers import util

        max_chunk = 4000
        text_chunks = []

        # Разбиваем на чанки если текст длинный
        if len(text) > max_chunk:
            for i in range(0, min(len(text), max_chunk * 3), max_chunk):
                chunk = text[i:i + max_chunk]
                if chunk.strip():
                    text_chunks.append(chunk)
        else:
            text_chunks = [text]

        with self._sbert_lock:
            query_emb = self.sbert_model.encode(query, convert_to_tensor=True)
            text_embs = self.sbert_model.encode(text_chunks, convert_to_tensor=True)

        similarities = util.cos_sim(query_emb, text_embs)[0]
        # Берём максимальный score из всех чанков
        best = max(s.item() for s in similarities)
        return max(0.0, min(1.0, (best + 1) / 2))

    def _enhanced_similarity(self, text: str, query: str) -> float:
        """Улучшенная keyword similarity: TF-IDF + proximity + fuzzy"""
        text_lower = text.lower()
        query_lower = query.lower()

        # 1. Exact phrase match — highest score
        if query_lower in text_lower:
            # Boost by frequency
            count = text_lower.count(query_lower)
            return min(1.0, 0.90 + min(count, 10) * 0.01)

        # 2. Morphological forms
        expanded = morph_analyzer.expand_query(query)
        query_words = re.findall(r'\b\w+\b', query_lower)

        # TF-IDF component
        text_words = re.findall(r'\b\w+\b', text_lower[:20000])
        total_words = len(text_words) if text_words else 1
        word_freq = Counter(text_words)

        morph_score = 0.0
        matched_forms = 0
        matched_positions: List[int] = []

        for form in expanded:
            if len(form) < 2:
                continue
            pos = text_lower.find(form)
            if pos != -1:
                matched_forms += 1
                matched_positions.append(pos)
                # TF component: frequency normalized by text length
                tf = word_freq.get(form, 0) / total_words
                # IDF-like: shorter forms are more common, less valuable
                idf = math.log(1 + len(form))
                morph_score += tf * idf

        if not expanded:
            return 0.0

        # Normalize morph coverage
        coverage = matched_forms / len(expanded)

        # 3. Proximity score — query words close together is better
        proximity_bonus = 0.0
        if len(matched_positions) >= 2 and len(query_words) >= 2:
            matched_positions.sort()
            # Average distance between consecutive matches
            distances = [matched_positions[i+1] - matched_positions[i]
                        for i in range(len(matched_positions) - 1)]
            avg_distance = sum(distances) / len(distances)
            # Closer = better (exponential decay)
            proximity_bonus = min(0.15, 0.15 * math.exp(-avg_distance / 500))

        # 4. Fuzzy bonus
        fuzzy_bonus = 0.0
        if FuzzyMatcher.is_available() and coverage < 0.5:
            try:
                found, _, score = FuzzyMatcher.fuzzy_find_in_text(
                    query, text, window=200, step=100, max_scan=10000
                )
                if found:
                    fuzzy_bonus = (score / 100) * 0.3
            except Exception:
                pass

        # Combine scores
        base = coverage * 0.55 + min(morph_score * 10, 0.3) + proximity_bonus + fuzzy_bonus

        return min(1.0, max(0.0, base))

    def calculate_batch(self, texts: List[str], query: str) -> List[float]:
        """Батчевое вычисление релевантности"""
        if not texts or not query:
            return [0.0] * len(texts)

        if self.sbert_model:
            try:
                from sentence_transformers import util

                with self._sbert_lock:
                    query_emb = self.sbert_model.encode(query, convert_to_tensor=True)
                    text_embs = self.sbert_model.encode(
                        [t[:4000] for t in texts],
                        convert_to_tensor=True,
                        batch_size=32
                    )

                similarities = util.cos_sim(query_emb, text_embs)[0]
                return [max(0.0, min(1.0, (s.item() + 1) / 2)) for s in similarities]
            except Exception:
                pass

        return [self._enhanced_similarity(t, query) for t in texts]


# ═══════════════════════════════════════════════════════════════════════════════
# SINGLETON INSTANCES
# ═══════════════════════════════════════════════════════════════════════════════

entity_extractor = EntityExtractor()
relevance_calculator = RelevanceCalculator()


# ═══════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════════

def extract_entities(text: str) -> Dict[str, List[str]]:
    """Извлечь сущности из текста"""
    return entity_extractor.extract(text)


def calculate_relevance(text: str, query: str) -> float:
    """Вычислить релевантность"""
    return relevance_calculator.calculate(text, query)


def calculate_relevance_batch(texts: List[str], query: str) -> List[float]:
    """Батчевое вычисление релевантности"""
    return relevance_calculator.calculate_batch(texts, query)


def expand_query(query: str) -> Set[str]:
    """Расширить запрос морфологическими формами"""
    return morph_analyzer.expand_query(query)


def fuzzy_search(query: str, candidates: List[str], threshold: int = None) -> List[Tuple[str, float]]:
    """Нечёткий поиск"""
    return FuzzyMatcher.find_matches(query, candidates, threshold)


__all__ = [
    'entity_extractor', 'relevance_calculator', 'morph_analyzer',
    'extract_entities', 'calculate_relevance', 'calculate_relevance_batch',
    'expand_query', 'fuzzy_search',
    'EntityExtractor', 'RelevanceCalculator', 'MorphologyAnalyzer', 'FuzzyMatcher'
]
