#!/usr/bin/env python3
"""
ASTREX v3.0 — NLP Engine
Морфология, сопоставление запросов, нечёткий поиск, NER и релевантность.

Ключевые принципы:
- запрос из нескольких слов совпадает, только если в тексте есть ВСЕ
  значимые слова (в любой словоформе) — служебные слова ("и", "в", "с")
  не учитываются;
- слова сравниваются целиком (по границам слов), а не как подстроки:
  "Иван" больше не находит "диван";
- е/ё взаимозаменяемы;
- тяжёлые модели (pymorphy, spaCy, SBERT) загружаются лениво — при первом
  использовании, а не при импорте модуля.
"""

import itertools
import logging
import math
import re
import threading
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Dict, List, Optional, Set, Tuple, Any

from .config import NLP_CONFIG, ENGINE_CONFIG

logger = logging.getLogger("astrex.nlp")


def _in_worker_process() -> bool:
    try:
        import multiprocessing
        return multiprocessing.parent_process() is not None
    except Exception:
        return False


# ═══════════════════════════════════════════════════════════════════════════════
# STOPWORDS
# ═══════════════════════════════════════════════════════════════════════════════

STOPWORDS: Set[str] = {
    # русские служебные слова
    'и', 'в', 'во', 'не', 'на', 'с', 'со', 'к', 'ко', 'у', 'о', 'об', 'обо',
    'от', 'ото', 'до', 'за', 'из', 'изо', 'по', 'под', 'подо', 'при', 'про',
    'для', 'без', 'через', 'над', 'а', 'но', 'да', 'или', 'ли', 'же', 'ж',
    'бы', 'б', 'то', 'как', 'что', 'это', 'также', 'тоже', 'либо', 'ни',
    # английские
    'the', 'a', 'an', 'and', 'or', 'of', 'in', 'on', 'at', 'to', 'for',
    'by', 'with', 'is', 'are', 'be',
}

# Слово: буквы/цифры, допускаются внутренние дефисы и апострофы
_WORD_RE = re.compile(r"[^\W_]+(?:[-'’][^\W_]+)*", re.UNICODE)
_CYRILLIC_RE = re.compile(r'[а-яё]', re.IGNORECASE)


def tokenize_words(text: str) -> List[str]:
    """Разбить текст на слова (в нижнем регистре)."""
    return [w.lower() for w in _WORD_RE.findall(text or '')]


# ═══════════════════════════════════════════════════════════════════════════════
# RUSSIAN STEMMER (Snowball) — fallback when pymorphy is unavailable
# ═══════════════════════════════════════════════════════════════════════════════

_RU_VOWELS = 'аеиоуыэюя'


def _among(word: str, limit: int, table: Tuple[Tuple[str, bool], ...]) -> Optional[int]:
    """Найти самое длинное окончание из table внутри области [limit:].

    table: (окончание, требуется ли перед ним 'а'/'я').
    Returns: позицию начала окончания или None.
    """
    best: Optional[Tuple[str, bool]] = None
    for suffix, cond in table:
        if word.endswith(suffix) and len(word) - len(suffix) >= limit:
            if best is None or len(suffix) > len(best[0]):
                best = (suffix, cond)
    if best is None:
        return None
    pos = len(word) - len(best[0])
    if best[1] and not (pos - 1 >= limit and word[pos - 1] in 'ая'):
        return None
    return pos


def _table(group1: Tuple[str, ...], group2: Tuple[str, ...] = ()) -> Tuple[Tuple[str, bool], ...]:
    return tuple((s, True) for s in group1) + tuple((s, False) for s in group2)


_PERFECTIVE_GERUND = _table(('в', 'вши', 'вшись'),
                            ('ив', 'ивши', 'ившись', 'ыв', 'ывши', 'ывшись'))
_ADJECTIVE = _table((), ('ее', 'ие', 'ые', 'ое', 'ими', 'ыми', 'ей', 'ий', 'ый', 'ой',
                         'ем', 'им', 'ым', 'ом', 'его', 'ого', 'ему', 'ому', 'их',
                         'ых', 'ую', 'юю', 'ая', 'яя', 'ою', 'ею'))
_PARTICIPLE = _table(('ем', 'нн', 'вш', 'ющ', 'щ'), ('ивш', 'ывш', 'ующ'))
_REFLEXIVE = _table((), ('ся', 'сь'))
_VERB = _table(('ла', 'на', 'ете', 'йте', 'ли', 'й', 'л', 'ем', 'н', 'ло', 'но',
                'ет', 'ют', 'ны', 'ть', 'ешь', 'нно'),
               ('ила', 'ыла', 'ена', 'ейте', 'уйте', 'ите', 'или', 'ыли', 'ей',
                'уй', 'ил', 'ыл', 'им', 'ым', 'ен', 'ило', 'ыло', 'ено', 'ят',
                'ует', 'уют', 'ит', 'ыт', 'ены', 'ить', 'ыть', 'ишь', 'ую', 'ю'))
_NOUN = _table((), ('а', 'ев', 'ов', 'ие', 'ье', 'е', 'иями', 'ями', 'ами', 'еи',
                    'ии', 'и', 'ией', 'ей', 'ой', 'ий', 'й', 'иям', 'ям', 'ием',
                    'ем', 'ам', 'ом', 'о', 'у', 'ах', 'иях', 'ях', 'ы', 'ь', 'ию',
                    'ью', 'ю', 'ия', 'ья', 'я'))
_DERIVATIONAL = _table((), ('ост', 'ость'))
_TIDY = _table((), ('ейш', 'ейше', 'н', 'ь'))


def _regions(word: str) -> Tuple[int, int, int]:
    n = len(word)
    rv = r1 = r2 = n
    for i, ch in enumerate(word):
        if ch in _RU_VOWELS:
            rv = i + 1
            break
    for i in range(1, n):
        if word[i] not in _RU_VOWELS and word[i - 1] in _RU_VOWELS:
            r1 = i + 1
            break
    for i in range(r1 + 1, n):
        if word[i] not in _RU_VOWELS and word[i - 1] in _RU_VOWELS:
            r2 = i + 1
            break
    return rv, r1, r2


def _stem_russian(word: str) -> str:
    """Стеммер Портера для русского языка (алгоритм Snowball)."""
    word = word.lower().strip().replace('ё', 'е')
    if len(word) < 3 or not _CYRILLIC_RE.search(word):
        return word

    rv, _r1, r2 = _regions(word)

    # Step 1
    pos = _among(word, rv, _PERFECTIVE_GERUND)
    if pos is not None:
        word = word[:pos]
    else:
        pos = _among(word, rv, _REFLEXIVE)
        if pos is not None:
            word = word[:pos]
        pos = _among(word, rv, _ADJECTIVE)
        if pos is not None:
            word = word[:pos]
            ppos = _among(word, rv, _PARTICIPLE)
            if ppos is not None:
                word = word[:ppos]
        else:
            pos = _among(word, rv, _VERB)
            if pos is None:
                pos = _among(word, rv, _NOUN)
            if pos is not None:
                word = word[:pos]

    # Step 2
    if word.endswith('и') and len(word) - 1 >= rv:
        word = word[:-1]

    # Step 3 (R2)
    pos = _among(word, r2, _DERIVATIONAL)
    if pos is not None:
        word = word[:pos]

    # Step 4
    pos = _among(word, rv, _TIDY)
    if pos is not None:
        suffix = word[pos:]
        if suffix in ('ейш', 'ейше'):
            word = word[:pos]
            if word.endswith('нн') and len(word) - 1 >= rv:
                word = word[:-1]
        elif suffix == 'н':
            if word.endswith('нн') and len(word) - 2 >= rv:
                word = word[:-1]
        elif suffix == 'ь':
            word = word[:pos]

    return word


# ═══════════════════════════════════════════════════════════════════════════════
# MORPHOLOGY (pymorphy3 / pymorphy2 with caching)
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

    if _CYRILLIC_RE.search(word):
        return _stem_russian(word)

    return word.lower()


@lru_cache(maxsize=20000)
def _cached_get_all_forms(word: str, morph, available: bool) -> frozenset:
    """Получить все формы слова — кэшируется (frozenset для hashability)"""
    forms = {word.lower()}

    if available:
        try:
            limit = NLP_CONFIG.max_forms_per_word
            parses = morph.parse(word)
            # Основной разбор + альтернативные, только если они достаточно вероятны
            # (иначе "Иванов" расширялся бы до "Иваново" через маловероятный разбор)
            chosen = parses[:1] + [p for p in parses[1:3] if getattr(p, 'score', 0) >= 0.1]
            for parse in chosen:
                for form in parse.lexeme:
                    forms.add(form.word.lower())
                    if len(forms) >= limit:
                        break
        except Exception:
            pass
    else:
        stem = _stem_russian(word)
        if stem:
            forms.add(stem)

    return frozenset(forms)


class MorphologyAnalyzer:
    """Морфологический анализатор (pymorphy3 или pymorphy2) с LRU-кэшем.

    Загружается лениво при первом обращении. Если библиотека недоступна
    или несовместима с версией Python, используется стеммер Snowball.
    """

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
        self._morph = None
        self._available = False
        self._loaded = False
        self._load_lock = threading.Lock()
        self._backend: Optional[str] = None
        self._initialized = True

    def _ensure(self) -> None:
        if self._loaded:
            return
        with self._load_lock:
            if self._loaded:
                return
            if NLP_CONFIG.use_pymorphy:
                self._init_pymorphy()
            self._loaded = True

    def _init_pymorphy(self) -> None:
        # pymorphy2 0.9.x не работает на Python ≥ 3.11 (inspect.getargspec),
        # поэтому сначала пробуем поддерживаемый форк pymorphy3.
        for module_name in ("pymorphy3", "pymorphy2"):
            try:
                module = __import__(module_name)
                self._morph = module.MorphAnalyzer()
                self._available = True
                self._backend = module_name
                return
            except ImportError:
                continue
            except Exception as e:
                logger.warning("%s is installed but failed to initialize (%s: %s); "
                               "using built-in stemmer", module_name, type(e).__name__, e)
                continue

    @property
    def morph(self):
        self._ensure()
        return self._morph

    @property
    def backend(self) -> Optional[str]:
        """Имя библиотеки морфологии (pymorphy3 / pymorphy2) или None."""
        self._ensure()
        return self._backend

    @property
    def available(self) -> bool:
        self._ensure()
        return self._available

    def normalize(self, word: str) -> str:
        """Нормализация слова (лемматизация) — кэшируется"""
        return _cached_normalize(word, self.morph, self.available)

    def get_all_forms(self, word: str) -> frozenset:
        """Получить все формы слова — кэшируется"""
        return _cached_get_all_forms(word, self.morph, self.available)

    def expand_query(self, query: str) -> Set[str]:
        """Расширить поисковый запрос всеми формами значимых слов + биграммы."""
        words = tokenize_words(query)
        terms = significant_terms(words)
        expanded: Set[str] = set()

        for word in terms:
            if len(word) >= 3 and not word.isdigit():
                expanded.update(self.get_all_forms(word))
            else:
                expanded.add(word)

        if len(words) >= 2:
            for i in range(len(words) - 1):
                bigram = f"{words[i]} {words[i + 1]}"
                expanded.add(bigram)
                norm_bigram = f"{self.normalize(words[i])} {self.normalize(words[i + 1])}"
                expanded.add(norm_bigram)

        if query.strip():
            expanded.add(query.lower().strip())

        return expanded

    def is_name(self, word: str) -> bool:
        """Проверить, является ли слово именем собственным"""
        if not word:
            return False
        if not self.available:
            return word[0].isupper()

        try:
            parsed = self.morph.parse(word)
            if parsed:
                tag = parsed[0].tag
                return 'Name' in tag or 'Surn' in tag or 'Patr' in tag
        except Exception:
            pass

        return False


morph_analyzer = MorphologyAnalyzer()


def significant_terms(words: List[str]) -> List[str]:
    """Значимые слова запроса (без служебных слов и повторов)."""
    result: List[str] = []
    for w in words:
        if w in STOPWORDS or (len(w) < 2 and not w.isdigit()):
            continue
        if w not in result:
            result.append(w)
    if not result:
        result = list(dict.fromkeys(words))
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# QUERY MATCHER
# ═══════════════════════════════════════════════════════════════════════════════

def _form_pattern(form: str) -> str:
    """Экранировать словоформу; е/ё взаимозаменяемы."""
    parts = []
    for ch in form:
        if ch in 'её':
            parts.append('[её]')
        else:
            parts.append(re.escape(ch))
    return ''.join(parts)


@dataclass
class QueryTerm:
    """Значимое слово запроса со всеми допустимыми формами."""
    word: str
    forms: List[str]
    stem: Optional[str] = None   # префикс для поиска без морфологии


@dataclass
class MatchInfo:
    """Где и как запрос совпал с текстом."""
    kind: str          # 'phrase' | 'terms' | 'fuzzy'
    pos: int
    length: int
    count: int = 1
    score: float = 100.0


def _min_cover_window(spans: List[List[Tuple[int, int]]]) -> Tuple[int, int]:
    """Минимальное окно текста, содержащее хотя бы по одному вхождению каждого слова."""
    events = sorted((s, e, idx) for idx, lst in enumerate(spans) for s, e in lst)
    need = len(spans)
    counts = [0] * need
    have = 0
    best = (events[0][0], events[0][1])
    best_len = math.inf
    left = 0
    for right in range(len(events)):
        _, _, idx = events[right]
        if counts[idx] == 0:
            have += 1
        counts[idx] += 1
        while have == need:
            start = events[left][0]
            end = max(e for _, e, _ in events[left:right + 1])
            if end - start < best_len:
                best_len = end - start
                best = (start, end)
            lidx = events[left][2]
            counts[lidx] -= 1
            if counts[lidx] == 0:
                have -= 1
            left += 1
    return best


class QueryMatcher:
    """Сопоставление запроса с текстом.

    Совпадение = точная фраза ИЛИ присутствие всех значимых слов запроса
    (в любой словоформе, целыми словами, без учёта регистра и е/ё).
    Объект сериализуется в dict для передачи в процессы-воркеры.
    """

    MAX_POSITIONS = 200

    def __init__(self, query: str, terms: List[QueryTerm], phrase_words: List[str]):
        self.query = query
        self.terms = terms
        self.phrase_words = phrase_words
        self._compiled: Optional[Tuple[Optional[re.Pattern], List[re.Pattern]]] = None

    # ── construction ────────────────────────────────────────────────────────

    @classmethod
    def build(cls, query: str, use_morphology: bool = True) -> 'QueryMatcher':
        words = tokenize_words(query)
        terms: List[QueryTerm] = []
        for word in significant_terms(words):
            forms = {word}
            stem = None
            if use_morphology and not word.isdigit() and len(word) >= 3:
                if morph_analyzer.available:
                    forms.update(morph_analyzer.get_all_forms(word))
                elif _CYRILLIC_RE.search(word):
                    s = _stem_russian(word)
                    if len(s) >= 4 and s != word:
                        stem = s
            terms.append(QueryTerm(
                word=word,
                forms=sorted(forms, key=len, reverse=True),
                stem=stem,
            ))
        phrase_words = words if len(words) >= 2 else []
        return cls(query, terms, phrase_words)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'query': self.query,
            'phrase_words': self.phrase_words,
            'terms': [{'word': t.word, 'forms': t.forms, 'stem': t.stem} for t in self.terms],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'QueryMatcher':
        terms = [QueryTerm(word=t['word'], forms=list(t['forms']), stem=t.get('stem'))
                 for t in data.get('terms', [])]
        return cls(data.get('query', ''), terms, list(data.get('phrase_words', [])))

    @property
    def is_empty(self) -> bool:
        return not self.terms and not self.phrase_words

    def all_forms(self) -> Set[str]:
        forms: Set[str] = set()
        for t in self.terms:
            forms.update(t.forms)
        return forms

    # ── matching ────────────────────────────────────────────────────────────

    def _compile(self) -> Tuple[Optional[re.Pattern], List[re.Pattern]]:
        if self._compiled is not None:
            return self._compiled
        flags = re.IGNORECASE | re.UNICODE
        phrase_re = None
        if len(self.phrase_words) >= 2:
            body = r'[\W_]+'.join(_form_pattern(w) for w in self.phrase_words)
            phrase_re = re.compile(r'(?<![^\W_])' + body + r'(?![^\W_])', flags)

        term_res: List[re.Pattern] = []
        for t in self.terms:
            if t.word.isdigit():
                term_res.append(re.compile(r'(?<!\d)' + re.escape(t.word) + r'(?!\d)', flags))
                continue
            alts = [_form_pattern(f) for f in t.forms if f]
            if t.stem:
                alts.append(_form_pattern(t.stem) + r'[^\W_]*')
            pattern = r'(?<![^\W_])(?:' + '|'.join(alts) + r')(?![^\W_])'
            term_res.append(re.compile(pattern, flags))
        self._compiled = (phrase_re, term_res)
        return self._compiled

    def find(self, text: str) -> Optional[MatchInfo]:
        """Найти совпадение в тексте (None — нет совпадения)."""
        if not text or self.is_empty:
            return None
        phrase_re, term_res = self._compile()

        if phrase_re is not None:
            matches = list(itertools.islice(phrase_re.finditer(text), 50))
            if matches:
                m = matches[0]
                return MatchInfo('phrase', m.start(), m.end() - m.start(), len(matches))

        if not term_res:
            return None

        spans: List[List[Tuple[int, int]]] = []
        for rx in term_res:
            found = [(m.start(), m.end()) for m in itertools.islice(rx.finditer(text), self.MAX_POSITIONS)]
            if not found:
                return None  # все значимые слова обязательны
            spans.append(found)

        count = min(len(s) for s in spans)
        if len(spans) == 1:
            start, end = spans[0][0]
        else:
            start, end = _min_cover_window(spans)
        return MatchInfo('terms', start, end - start, count)

    def term_coverage(self, text: str) -> float:
        """Доля значимых слов запроса, присутствующих в тексте."""
        if not text or not self.terms:
            return 0.0
        _, term_res = self._compile()
        found = sum(1 for rx in term_res if rx.search(text))
        return found / len(term_res)

    def term_spans(self, text: str) -> List[List[Tuple[int, int]]]:
        _, term_res = self._compile()
        return [[(m.start(), m.end()) for m in itertools.islice(rx.finditer(text), self.MAX_POSITIONS)]
                for rx in term_res]

    def phrase_count(self, text: str, cap: int = 50) -> int:
        phrase_re, term_res = self._compile()
        if phrase_re is not None:
            return sum(1 for _ in itertools.islice(phrase_re.finditer(text), cap))
        if len(term_res) == 1:
            return sum(1 for _ in itertools.islice(term_res[0].finditer(text), cap))
        return 0


def build_query_matcher(query: str, use_morphology: bool = True) -> QueryMatcher:
    """Построить объект сопоставления для запроса."""
    return QueryMatcher.build(query, use_morphology=use_morphology)


# ═══════════════════════════════════════════════════════════════════════════════
# FUZZY SEARCH
# ═══════════════════════════════════════════════════════════════════════════════

def _trigram_set(s: str) -> Set[str]:
    """Построить множество символьных триграмм"""
    s = f"  {s.lower()}  "
    return {s[i:i + 3] for i in range(len(s) - 2)}


def _trigram_jaccard(s1: str, s2: str) -> float:
    """Jaccard similarity на символьных триграммах (0-100)"""
    t1 = _trigram_set(s1)
    t2 = _trigram_set(s2)
    if not t1 or not t2:
        return 0.0
    union = len(t1 | t2)
    return (len(t1 & t2) / union) * 100 if union > 0 else 0.0


def _trigram_containment(query: str, text: str) -> float:
    """Доля триграмм запроса, найденных в тексте (0-100).

    В отличие от Jaccard не штрафует за то, что окно текста длиннее запроса.
    """
    q = _trigram_set(query)
    if not q:
        return 0.0
    t = _trigram_set(text)
    return len(q & t) / len(q) * 100


class FuzzyMatcher:
    """Нечёткий поиск с использованием rapidfuzz + fallback на триграммы"""

    _available: Optional[bool] = None

    @classmethod
    def is_available(cls) -> bool:
        if cls._available is None:
            try:
                import rapidfuzz  # noqa: F401
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
        s1l, s2l = s1.lower(), s2.lower()
        if s1l in s2l or s2l in s1l:
            return 100.0
        shorter, longer = (s1l, s2l) if len(s1l) <= len(s2l) else (s2l, s1l)
        return _trigram_containment(shorter, longer)

    @classmethod
    def wratio(cls, s1: str, s2: str) -> float:
        """Weighted ratio — для сравнения строк сопоставимой длины"""
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
        tokens1 = set(s1.lower().split())
        tokens2 = set(s2.lower().split())
        if not tokens1 or not tokens2:
            return 0.0
        return (len(tokens1 & tokens2) / min(len(tokens1), len(tokens2))) * 100

    @classmethod
    def find_matches(
        cls,
        query: str,
        candidates: List[str],
        threshold: Optional[int] = None,
        limit: int = 10
    ) -> List[Tuple[str, float]]:
        """Найти похожие строки среди кандидатов (WRatio, без учёта регистра)"""
        if threshold is None:
            threshold = ENGINE_CONFIG.fuzzy_threshold

        if cls.is_available():
            from rapidfuzz import process, fuzz, utils
            results = process.extract(
                query, candidates,
                scorer=fuzz.WRatio,
                processor=utils.default_process,
                limit=limit,
                score_cutoff=threshold,
            )
            return [(r[0], float(r[1])) for r in results]

        matches = []
        for candidate in candidates:
            score = _trigram_jaccard(query, candidate)
            if score >= threshold:
                matches.append((candidate, score))
        return sorted(matches, key=lambda x: -x[1])[:limit]

    @staticmethod
    def effective_threshold(query: str, threshold: Optional[int] = None) -> float:
        """Порог для запроса: для коротких запросов строже (меньше ложных срабатываний)."""
        thr = float(threshold if threshold is not None else ENGINE_CONFIG.fuzzy_threshold)
        if len(query.strip()) <= 5:
            thr = max(thr, 90.0)
        return thr

    @classmethod
    def fuzzy_find_in_text(
        cls,
        query: str,
        text: str,
        window: Optional[int] = None,
        step: Optional[int] = None,
        max_scan: Optional[int] = None,
        threshold: Optional[int] = None,
    ) -> Tuple[bool, int, float]:
        """Найти в тексте слово (или последовательность слов), похожее на запрос.

        Сравнение идёт с ЦЕЛЫМИ словами текста (окнами из стольких же слов,
        сколько в запросе), а не с произвольными подстроками: иначе
        partial_ratio давал 100 для "иван" внутри "диван". Охватывает весь
        текст до max_scan символов, включая короткие тексты и хвост.

        Returns:
            (found, position, score)
        """
        qwords = tokenize_words(query)
        q = ' '.join(qwords)
        if len(q) < 4 or not text:
            return (False, -1, 0.0)

        thr = cls.effective_threshold(q, threshold)
        if max_scan is None:
            max_scan = ENGINE_CONFIG.fuzzy_max_scan_chars
        tokens = [(m.start(), m.group(0).lower()) for m in _WORD_RE.finditer(text[:max_scan])]
        n = len(qwords)
        if len(tokens) < n:
            return (False, -1, 0.0)

        min_len, max_len = len(q) * 0.6, len(q) * 1.6
        positions: List[int] = []
        choices: List[str] = []
        for i in range(len(tokens) - n + 1):
            candidate = ' '.join(t[1] for t in tokens[i:i + n])
            if min_len <= len(candidate) <= max_len:
                positions.append(tokens[i][0])
                choices.append(candidate)
        if not choices:
            return (False, -1, 0.0)

        if cls.is_available():
            from rapidfuzz import process, fuzz
            best = process.extractOne(q, choices, scorer=fuzz.ratio)
            if best is None:
                return (False, -1, 0.0)
            score, idx = float(best[1]), best[2]
            return (score >= thr, positions[idx], score)

        # Без rapidfuzz: отбор кандидатов по триграммам, затем точная оценка difflib
        import difflib
        q_tri = _trigram_set(q)
        best_score, best_pos = 0.0, -1
        for pos, candidate in zip(positions, choices):
            c_tri = _trigram_set(candidate)
            if len(q_tri & c_tri) / max(1, len(q_tri | c_tri)) < 0.25:
                continue
            score = difflib.SequenceMatcher(None, q, candidate).ratio() * 100
            if score > best_score:
                best_score, best_pos = score, pos
                if best_score >= 99.9:
                    break
        return (best_score >= thr, best_pos, best_score)


# ═══════════════════════════════════════════════════════════════════════════════
# ENTITY PATTERNS
# ═══════════════════════════════════════════════════════════════════════════════

_CAP = r'[А-ЯЁ][а-яё]+'
_CAP_H = r'[А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)?'
_PATRONYMIC = r'[А-ЯЁ][а-яё]+(?:ович|евич|ич|овна|евна|ична|инична)(?:а|у|ем|е|ы|ой|ою)?'
_SURNAME = (r'[А-ЯЁ][а-яё]+(?:ов|ев|ёв|ин|ын|ский|цкий|ской|цкой|ова|ева|ёва|ина|ына|'
            r'ская|цкая|енко|ко|ук|юк|чук|ян|дзе|швили|их|ых)'
            r'(?:а|у|ым|ом|е|ой|ы|ых|им|ую)?')
_NUM = r'(?<![\d.,])(?:\d{1,3}(?:[ \u00a0\u202f]\d{3})+|\d+)(?:[.,]\d+)?'
_RUB = r'(?:руб(?:л(?:ей|я|ь|ями|ям)|\.)?|₽|р\.)'
_ORG_FORMS = r'(?:ООО|ОАО|ЗАО|ПАО|НАО|АО|ИП|ФГУП|ГУП|МУП|НКО|ТОО|КФХ|ПБОЮЛ|ФГБУ|ФГБОУ|ГБУ|МБУ|АНО)'
_PLATE_CHARS = 'АВЕКМНОРСТУХABEKMHOPCTYX'

# Распространённые имена (англоязычные и транслитерированные русские) —
# пара "Имя Фамилия" латиницей распознаётся только для них, иначе любые два
# слова с заглавной буквы ("Screen Print", "Annual Report") становились людьми.
_LATIN_FIRST_NAMES = {
    'Aaron', 'Adam', 'Adrian', 'Alan', 'Albert', 'Alex', 'Alexander', 'Alexandra', 'Alexei',
    'Alexey', 'Alice', 'Alina', 'Alla', 'Amanda', 'Amy', 'Anastasia', 'Andrea', 'Andrei', 'Andrew',
    'Andrey', 'Angela', 'Anna', 'Anne', 'Anthony', 'Anton', 'Arthur', 'Artem', 'Barbara', 'Ben',
    'Benjamin', 'Bill', 'Bob', 'Boris', 'Brian', 'Carl', 'Carlos', 'Carol', 'Catherine', 'Charles',
    'Chris', 'Christian', 'Christina', 'Christopher', 'Claire', 'Daniel', 'Daria', 'Daniil', 'David',
    'Denis', 'Diana', 'Dmitri', 'Dmitriy', 'Dmitry', 'Donald', 'Ekaterina', 'Elena', 'Elizabeth',
    'Emily', 'Emma', 'Eric', 'Eugene', 'Evgeny', 'Fedor', 'Frank', 'Fyodor', 'Gary', 'George',
    'Grace', 'Gregory', 'Hannah', 'Harry', 'Helen', 'Henry', 'Igor', 'Ilya', 'Irina', 'Ivan',
    'Jack', 'Jacob', 'James', 'Jane', 'Jason', 'Jean', 'Jeff', 'Jennifer', 'Jessica', 'Jim', 'Joe',
    'John', 'Jonathan', 'Joseph', 'Julia', 'Karen', 'Kate', 'Kevin', 'Kirill', 'Konstantin',
    'Larry', 'Laura', 'Leonid', 'Linda', 'Lisa', 'Lucas', 'Lyudmila', 'Margaret', 'Maria', 'Marina',
    'Mark', 'Martin', 'Mary', 'Matthew', 'Maxim', 'Michael', 'Michelle', 'Mikhail', 'Nadezhda',
    'Natalia', 'Natalya', 'Nancy', 'Nicholas', 'Nick', 'Nikita', 'Nikolai', 'Nikolay', 'Oleg',
    'Olga', 'Oliver', 'Paul', 'Pavel', 'Peter', 'Petr', 'Philip', 'Rachel', 'Richard', 'Robert',
    'Roman', 'Ruslan', 'Ryan', 'Sam', 'Samuel', 'Sarah', 'Scott', 'Sergei', 'Sergey', 'Sophia',
    'Stanislav', 'Stephen', 'Steve', 'Steven', 'Susan', 'Svetlana', 'Tatiana', 'Tatyana', 'Thomas',
    'Tim', 'Timothy', 'Tom', 'Valentina', 'Valery', 'Vasily', 'Victor', 'Viktor', 'Vladimir',
    'Vladislav', 'Walter', 'William', 'Yana', 'Yuri', 'Yury', 'Zoe',
}


@dataclass
class EntityPatterns:
    """Regex паттерны для NER"""

    PERSON: List[re.Pattern] = field(default_factory=lambda: [
        # Фамилия Имя Отчество (в любом падеже отчества)
        re.compile(rf'\b{_CAP_H}\s+{_CAP}\s+{_PATRONYMIC}\b'),
        # Имя Отчество Фамилия
        re.compile(rf'\b{_CAP}\s+{_PATRONYMIC}\s+{_CAP_H}\b'),
        # Фамилия И.О. / И.О. Фамилия
        re.compile(rf'\b{_CAP_H}\s+[А-ЯЁ]\.\s?[А-ЯЁ]\.'),
        re.compile(rf'\b[А-ЯЁ]\.\s?[А-ЯЁ]\.\s?{_CAP_H}\b'),
        # Имя Фамилия (фамилия с типичным суффиксом)
        re.compile(rf'\b{_CAP}\s+{_SURNAME}\b'),
        # Латинские имена: с обращением (Mr John Smith), инициалы (J. Smith),
        # либо распространённое имя + фамилия (John Smith, Ivan Petrov)
        re.compile(r'\b(?:Mr|Mrs|Ms|Miss|Dr|Prof|Sir)\.?\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?\b'),
        re.compile(r'\b[A-Z]\.\s?(?:[A-Z]\.\s?)?[A-Z][a-z]{2,}(?:-[A-Z][a-z]+)?\b'),
        re.compile(r'\b(?:' + '|'.join(sorted(_LATIN_FIRST_NAMES)) + r')\s+[A-Z][a-z]+(?:-[A-Z][a-z]+)?\b'),
    ])

    ORG: List[re.Pattern] = field(default_factory=lambda: [
        re.compile(rf'\b{_ORG_FORMS}\s*[«"„“\']([^«»"„“”\'\n]{{1,100}})[»"”“\']'),
        re.compile(rf'\b{_ORG_FORMS}\s+[А-ЯЁA-Z][\w\-]*(?:\s+[А-ЯЁA-Z][\w\-]*){{0,3}}'),
        re.compile(r'\b(?:Банк|БАНК)\s+[«"„“\']?[А-ЯЁA-Z][\w\-]*(?:\s+[А-ЯЁA-Z][\w\-]*){0,2}[»"”“\']?'),
        re.compile(r'\b(?:Газпром(?:банк|нефть)?|Роснефть|Сбербанк|Сбер|ВТБ|Лукойл|РЖД|Ростелеком|'
                   r'МТС|Билайн|Мегафон|МегаФон|Яндекс|Тинькофф|Т-Банк|Альфа-Банк|Росатом|Ростех|'
                   r'Аэрофлот|Норникель|Магнит|X5|Wildberries|Ozon|Озон|Банк России|Центробанк|ЦБ РФ)\b'),
        re.compile(r'\b(?:ФСБ|МВД|ФНС|ФТС|ФАС|ФСО|СВР|ФСИН|ФССП|МЧС|ГИБДД|ПФР|СФР|ФОМС|'
                   r'Следственный комитет|СК РФ|Генпрокуратура|Прокуратура|Росреестр|'
                   r'Роспотребнадзор|Роскомнадзор|Минфин|Минюст|Минобороны|Минэкономразвития)\b'),
        re.compile(r'\bМинистерств[оау]\s+[а-яё]+(?:\s+(?:и\s+)?[а-яё]+){0,2}'
                   r'(?:\s+Российской\s+Федерации|\s+РФ)?'),
        re.compile(r'\b(?:Google|Microsoft|Apple|Amazon|Meta|Facebook|Twitter|Tesla|OpenAI|'
                   r'Anthropic|Netflix|Samsung|Intel|AMD|NVIDIA|IBM|Oracle|SAP|Salesforce)\b'),
    ])

    LOCATION: List[re.Pattern] = field(default_factory=lambda: [
        re.compile(r'\b(?:г\.|город[аеу]?|гор\.)\s*[А-ЯЁ][а-яё]+(?:-[А-ЯЁа-яё]+)*'),
        re.compile(r'\b[А-ЯЁ][а-яё]+(?:ская|ский|ское|ской|ную|ная|ный|ное|ной)\s+'
                   r'(?:область|области|край|края|крае|республика|республики|округ|округа|район|района)\b'),
        re.compile(r'\b(?:ул\.|улица|улице|улицы|пр-т|просп\.|проспект|пер\.|переулок|б-р|бульвар|'
                   r'наб\.|набережная|ш\.|шоссе|пл\.|площадь|пр\.(?=\s*[А-ЯЁ]))\s*'
                   r'[А-ЯЁ0-9][А-ЯЁа-яё0-9\-]*(?:\s+[А-ЯЁ][А-ЯЁа-яё\-]*){0,2}'),
        re.compile(r'\bд\.\s*\d+[а-яА-Я]?(?:\s*,?\s*(?:корп\.|к\.)\s*\d+)?(?:\s*,?\s*(?:кв\.|оф\.|офис)\s*\d+)?'),
        re.compile(r'\b(?:Москва|Москвы|Москве|Санкт-Петербург|Новосибирск|Екатеринбург|Казань|'
                   r'Нижний\s+Новгород|Челябинск|Самара|Омск|Ростов-на-Дону|Уфа|Красноярск|Воронеж|'
                   r'Пермь|Волгоград|Краснодар|Саратов|Тюмень|Тольятти|Ижевск|Барнаул|Владивосток|'
                   r'Хабаровск|Калининград|Сочи|Иркутск|Ярославль|Махачкала|Томск|Оренбург|Кемерово|'
                   r'Новокузнецк|Рязань|Астрахань|Пенза|Липецк|Киров|Чебоксары|Тула|Калуга|Брянск|'
                   r'Курск|Белгород|Владимир|Сургут|Архангельск|Симферополь|Севастополь)\b'),
    ])

    DATE: List[re.Pattern] = field(default_factory=lambda: [
        re.compile(r'(?<![\d.])\d{1,2}[./\-]\d{1,2}[./\-](?:\d{4}|\d{2})(?![\d.]\d)'),
        re.compile(r'(?<!\d)\d{4}[./\-]\d{1,2}[./\-]\d{1,2}(?!\d)'),
        re.compile(r'\b\d{1,2}\s+(?:января|февраля|марта|апреля|мая|июня|июля|августа|сентября|'
                   r'октября|ноября|декабря)\s+\d{4}(?:\s*(?:г\.|года))?', re.IGNORECASE),
        re.compile(r'\b(?:январь|февраль|март|апрель|май|июнь|июль|август|сентябрь|октябрь|ноябрь|'
                   r'декабрь|января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|'
                   r'ноября|декабря)\s+\d{4}\b', re.IGNORECASE),
    ])

    MONEY: List[re.Pattern] = field(default_factory=lambda: [
        re.compile(rf'{_NUM}\s*(?:млн|млрд|трлн|тыс)\.?\s*(?:{_RUB}|долл(?:ар(?:ов|а|ы)?)?\.?|\$|евро|€)(?!\w)',
                   re.IGNORECASE),
        re.compile(rf'{_NUM}\s*{_RUB}(?!\w)', re.IGNORECASE),
        re.compile(rf'(?:\$|€|£|¥|USD|EUR|GBP|CNY|RUB)\s*{_NUM}'),
        re.compile(rf'{_NUM}\s*(?:\$|€|£|¥|USD|EUR|GBP|CNY|RUB|долл(?:ар(?:ов|а|ы)?)?\.?|евро|'
                   r'фунт(?:ов|а)?|юан(?:ей|я|ь)?)(?!\w)', re.IGNORECASE),
    ])

    CONTACT: List[re.Pattern] = field(default_factory=lambda: [
        # Телефоны
        re.compile(r'(?<![\d+])(?:\+7|8)[\s\-]?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}(?!\d)'),
        re.compile(r'(?<!\d)\(\d{3,5}\)\s?\d{1,3}[\s\-]?\d{2}[\s\-]?\d{2}(?!\d)'),
        re.compile(r'(?<![\d\-])\d{3}-\d{2}-\d{2}(?![\d\-])'),
        # Email
        re.compile(r'\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-zА-Яа-яЁё]{2,}\b'),
        # Реквизиты
        re.compile(r'\bИНН[\s:№]*(?:\d{12}|\d{10})(?!\d)'),
        re.compile(r'\bОГРН(?:ИП)?[\s:№]*\d{13,15}(?!\d)'),
        re.compile(r'\bКПП[\s:№]*\d{9}(?!\d)'),
        re.compile(r'\bБИК[\s:№]*\d{9}(?!\d)'),
        re.compile(r'(?<!\w)(?:р/с|к/с|[Сс]ч[её]т)[\s:№]*\d{20}(?!\d)'),
        re.compile(r'\bСНИЛС[\s:№]*\d{3}[\s\-]?\d{3}[\s\-]?\d{3}[\s\-]?\d{2}(?!\d)'),
        # Паспорт (только с контекстом)
        re.compile(r'(?i:паспорт\w*)[^\d\n]{0,40}\d{2}\s?\d{2}\s?№?\s?\d{6}(?!\d)'),
    ])

    DOCUMENT: List[re.Pattern] = field(default_factory=lambda: [
        re.compile(r'\b(?:[Дд]оговор\w*|[Кк]онтракт\w*|[Сс]оглашени\w*|[Аа]кт\w{0,2}|[Сс]ч[её]т\w{0,3}|'
                   r'[Нн]акладн\w*|[Дд]оверенност\w*|[Зз]аявлени\w*|[Пп]риказ\w*|[Рр]аспоряжени\w*|'
                   r'[Пп]ротокол\w*|[Рр]ешени\w*)\s*(?:№|N|No\.?|номер)\s*[\w\-/.]+'
                   r'(?:\s+от\s+\d{1,2}[./]\d{1,2}[./]\d{2,4})?'),
    ])

    TECHNICAL: List[re.Pattern] = field(default_factory=lambda: [
        re.compile(r'\bhttps?://[^\s<>"\'«»]+', re.IGNORECASE),
        re.compile(r'\b(?:(?:25[0-5]|2[0-4]\d|1\d{2}|[1-9]?\d)\.){3}(?:25[0-5]|2[0-4]\d|1\d{2}|[1-9]?\d)\b'),
        re.compile(rf'(?<![\wЁё])[{_PLATE_CHARS}]\s?\d{{3}}\s?[{_PLATE_CHARS}]{{2}}\s?\d{{2,3}}(?![\wЁё])'),
        re.compile(r'\b(?:(?:[Уу]головн|[Гг]ражданск|[Аа]рбитражн|[Аа]дминистративн)\w+\s+)?'
                   r'[Дд]ел[оау]\s*(?:№|N)\s*[\dА-ЯA-Z][\w\-/]*'),
    ])


PATTERNS = EntityPatterns()

_PATTERN_KEYS = [
    ('PERSON', 'PERSONS'),
    ('ORG', 'ORGANIZATIONS'),
    ('LOCATION', 'LOCATIONS'),
    ('DATE', 'DATES'),
    ('MONEY', 'MONEY'),
    ('CONTACT', 'CONTACTS'),
    ('DOCUMENT', 'DOCUMENTS'),
    ('TECHNICAL', 'TECHNICAL'),
]

ENTITY_TYPES = [key for _, key in _PATTERN_KEYS]

# Слова, которые часто стоят с заглавной буквы, но не являются частью имени
_PERSON_STOP = {
    'Российская', 'Российской', 'Федерация', 'Федерации', 'Республика', 'Республики',
    'Министерство', 'Министерства', 'Генеральный', 'Генерального', 'Директор', 'Директора',
    'Общество', 'Акционерное', 'Открытое', 'Закрытое', 'Публичное', 'Уважаемый', 'Уважаемая',
    'Уважаемые', 'Сегодня', 'Вчера', 'Завтра', 'Также', 'Однако', 'Если', 'Когда', 'Согласно',
    'Настоящий', 'Настоящим', 'Договор', 'Приложение', 'Статья', 'Глава', 'Пункт', 'Господин',
    'Госпожа', 'Гражданин', 'Гражданка', 'Между', 'После', 'Перед', 'Итого', 'Всего', 'Москва',
    'The', 'This', 'That', 'These', 'Those', 'Dear', 'New', 'Best', 'Kind', 'Regards', 'In',
    'On', 'At', 'For', 'From', 'To', 'With', 'And', 'Or', 'But', 'If', 'When', 'Of', 'By',
    'As', 'It', 'We', 'You', 'Our', 'Your', 'Please', 'Thank', 'Thanks', 'Microsoft', 'Google',
    'Apple', 'Amazon', 'United', 'States', 'Page', 'Total', 'Date', 'Subject', 'Re', 'Fwd',
}

# Кавычки (в т.ч. типографские) и пробелы
_QUOTE_STRIP = re.compile(r'[«»“”„‟"\'‘’]+')
_TRAILING_PUNCT = re.compile(r'[.,;:!?)\]}>]+$')


def _normalize_entity(text: str, key: str = '') -> str:
    """Нормализовать текст сущности"""
    if key == 'TECHNICAL' and text.lower().startswith(('http://', 'https://')):
        text = _TRAILING_PUNCT.sub('', text)
    else:
        text = _QUOTE_STRIP.sub('', text)
    text = ' '.join(text.split())
    return text.strip(' ,;:')


# ═══════════════════════════════════════════════════════════════════════════════
# NER ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

class EntityExtractor:
    """Извлечение именованных сущностей: spaCy (если есть) + регулярные выражения."""

    _MAX_REGEX_TEXT_LEN = 100_000

    def __init__(self):
        self._nlp = None
        self._nlp_loaded = False
        self._load_lock = threading.Lock()
        self._spacy_lock = threading.Lock()

    @property
    def nlp(self):
        """spaCy-модель (ленивая загрузка; None если недоступна)."""
        if not self._nlp_loaded:
            with self._load_lock:
                if not self._nlp_loaded:
                    self._init_spacy()
                    self._nlp_loaded = True
        return self._nlp

    def _init_spacy(self) -> None:
        """Инициализация spaCy (GPU — только в основном процессе)"""
        try:
            import spacy
        except ImportError:
            return
        except Exception as e:  # несовместимые версии pydantic/numpy и т.п.
            logger.warning("spaCy import failed: %s", e)
            return

        if NLP_CONFIG.use_gpu and not _in_worker_process():
            try:
                from .gpu import get_device
                device = get_device()
                if device.backend in ("cuda", "rocm"):
                    spacy.prefer_gpu(device.device_id)
            except Exception as e:
                logger.debug("spaCy GPU init failed: %s", e)

        for model in NLP_CONFIG.spacy_models:
            try:
                self._nlp = spacy.load(model, disable=['parser', 'lemmatizer'])
                logger.info("spaCy model loaded: %s", model)
                return
            except OSError:
                continue
            except Exception as e:
                logger.warning("Failed to load spaCy model %s: %s", model, e)
                continue

    def extract(self, text: str) -> Dict[str, List[str]]:
        """Извлечь все сущности из текста"""
        if not text or len(text) < 2:
            return {}

        text = text[:NLP_CONFIG.max_text_length]

        spacy_entities = self._extract_spacy(text) if self.nlp is not None else {}
        regex_entities = self._extract_regex(text)

        return self._deduplicate(regex_entities, spacy_entities)

    def _extract_spacy(self, text: str) -> Dict[str, List[str]]:
        """spaCy NER (thread-safe)"""
        entities: Dict[str, List[str]] = {}

        try:
            with self._spacy_lock:
                doc = self._nlp(text)

            label_map = {
                'PER': 'PERSONS',
                'PERSON': 'PERSONS',
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
        except Exception as e:
            logger.debug("spaCy NER failed: %s", e)

        return entities

    def _extract_regex(self, text: str) -> Dict[str, List[str]]:
        """Regex-based NER с ограничением длины текста"""
        text = text[:self._MAX_REGEX_TEXT_LEN]
        limit = NLP_CONFIG.max_entities_per_type
        entities: Dict[str, List[str]] = {key: [] for _, key in _PATTERN_KEYS}

        for attr, key in _PATTERN_KEYS:
            spans: List[Tuple[int, int, str]] = []
            for pattern in getattr(PATTERNS, attr):
                for i, m in enumerate(pattern.finditer(text)):
                    if i >= limit * 3:
                        break
                    value = m.group(0).strip()
                    if value:
                        spans.append((m.start(), m.end(), value))
            # Совпадения разных шаблонов, вложенные в более длинное совпадение
            # того же типа ("123-45-67" внутри "+7 (999) 123-45-67"), отбрасываются
            spans.sort(key=lambda s: (s[0], -(s[1] - s[0])))
            kept: List[Tuple[int, int, str]] = []
            for start, end, value in spans:
                if any(start >= k_start and end <= k_end for k_start, k_end, _ in kept):
                    continue
                kept.append((start, end, value))
            entities[key] = [value for _, _, value in kept[:limit * 3]]

        return entities

    @staticmethod
    def _is_noise(value: str, key: str) -> bool:
        low = value.lower()
        if key == 'ORGANIZATIONS' and low in {'рф', 'ру', 'ru', 'рос'}:
            return True
        if key == 'PERSONS':
            words = value.replace('.', ' ').split()
            if any(w in _PERSON_STOP for w in words):
                return True
        return False

    def _deduplicate(
        self,
        regex_entities: Dict[str, List[str]],
        spacy_entities: Optional[Dict[str, List[str]]] = None,
    ) -> Dict[str, List[str]]:
        """Дедупликация с нормализацией.

        Сущность, найденная и регулярным выражением, и spaCy, получает
        повышенный вес и попадает в начало списка.
        """
        spacy_entities = spacy_entities or {}
        regex_norm: Dict[str, Set[str]] = {}
        for key, values in regex_entities.items():
            regex_norm[key] = {_normalize_entity(v, key).lower() for v in values}

        result: Dict[str, List[str]] = {}
        keys = list(dict.fromkeys(list(regex_entities) + list(spacy_entities)))
        limit = NLP_CONFIG.max_entities_per_type

        for key in keys:
            seen: Set[str] = set()
            scored: List[Tuple[str, float, int]] = []
            order = 0
            spacy_lower = {_normalize_entity(v, key).lower() for v in spacy_entities.get(key, [])}

            for source, values in (('regex', regex_entities.get(key, [])),
                                   ('spacy', spacy_entities.get(key, []))):
                for raw in values:
                    v = _normalize_entity(raw, key)
                    v_lower = v.lower()
                    if len(v) < NLP_CONFIG.min_entity_length or v_lower in seen:
                        continue
                    if self._is_noise(v, key):
                        continue
                    seen.add(v_lower)
                    score = 1.0
                    if source == 'regex' and v_lower in spacy_lower:
                        score = 2.0
                    elif source == 'spacy' and v_lower in regex_norm.get(key, set()):
                        score = 2.0
                    scored.append((v, score, order))
                    order += 1

            if scored:
                scored.sort(key=lambda x: (-x[1], x[2]))
                result[key] = [s[0] for s in scored[:limit]]

        return result


# ═══════════════════════════════════════════════════════════════════════════════
# RELEVANCE CALCULATOR
# ═══════════════════════════════════════════════════════════════════════════════

class RelevanceCalculator:
    """Релевантность: совпадения слов/фразы + близость + fuzzy + (опционально) SBERT.

    Итоговая оценка = max(оценка по ключевым словам, семантическая оценка),
    поэтому текст с точным совпадением запроса не может проиграть
    "похожему по смыслу" тексту без совпадения.
    """

    def __init__(self):
        self._sbert_model = None
        self._sbert_initialized = False
        self._sbert_lock = threading.Lock()
        self._matcher_cache: Dict[str, QueryMatcher] = {}

    @property
    def sbert_model(self):
        """Ленивая инициализация SBERT модели (только в основном процессе)."""
        if not self._sbert_initialized:
            with self._sbert_lock:
                if not self._sbert_initialized:
                    try:
                        if _in_worker_process():
                            raise RuntimeError("SBERT is not used in worker processes")
                        from sentence_transformers import SentenceTransformer
                        from .gpu import get_device
                        device = get_device()
                        self._sbert_model = SentenceTransformer(
                            NLP_CONFIG.sbert_model,
                            device=device.torch_device,
                        )
                    except ImportError:
                        self._sbert_model = None
                    except Exception as e:
                        logger.debug("SBERT unavailable: %s", e)
                        self._sbert_model = None
                    finally:
                        self._sbert_initialized = True
        return self._sbert_model

    def _matcher(self, query: str) -> QueryMatcher:
        m = self._matcher_cache.get(query)
        if m is None:
            if len(self._matcher_cache) > 256:
                self._matcher_cache.clear()
            m = build_query_matcher(query)
            self._matcher_cache[query] = m
        return m

    # ── keyword score ───────────────────────────────────────────────────────

    def keyword_score(self, text: str, query: str, matcher: Optional[QueryMatcher] = None) -> float:
        """Оценка по ключевым словам (0..1), без нейросетей."""
        if not text or not query:
            return 0.0
        text = text[:NLP_CONFIG.max_text_length]
        matcher = matcher or self._matcher(query)
        if matcher.is_empty:
            return 0.0

        phrase_hits = matcher.phrase_count(text)
        if phrase_hits and (matcher.phrase_words or len(matcher.terms) == 1):
            return min(1.0, 0.9 + min(phrase_hits, 10) * 0.01)

        spans = matcher.term_spans(text)
        found = [s for s in spans if s]
        coverage = len(found) / len(spans) if spans else 0.0

        if coverage == 0.0:
            ok, _pos, fscore = FuzzyMatcher.fuzzy_find_in_text(query, text, max_scan=20000)
            if ok:
                return round(0.3 + 0.4 * (fscore / 100.0), 4)
            return 0.0

        score = 0.3 + 0.5 * coverage
        if coverage == 1.0 and len(spans) >= 2:
            start, end = _min_cover_window(spans)
            score += 0.08 * math.exp(-max(0, end - start) / 300.0)
        frequency = min(sum(len(s) for s in found), 20)
        score += 0.01 * frequency * coverage / 2
        return round(min(0.89, score), 4)

    # ── semantic score ──────────────────────────────────────────────────────

    def _chunks(self, text: str) -> List[str]:
        size = max(100, NLP_CONFIG.sbert_chunk_chars)
        chunks = [text[i:i + size] for i in range(0, min(len(text), size * NLP_CONFIG.sbert_max_chunks), size)]
        return [c for c in chunks if c.strip()] or [text[:size]]

    def semantic_scores(self, texts: List[str], query: str) -> Optional[List[float]]:
        """Косинусное сходство SBERT (лучший чанк каждого текста), None если SBERT нет."""
        model = self.sbert_model
        if model is None or not texts:
            return None
        try:
            from sentence_transformers import util
            owners: List[int] = []
            chunks: List[str] = []
            for idx, t in enumerate(texts):
                for c in self._chunks(t or ' '):
                    owners.append(idx)
                    chunks.append(c)
            with self._sbert_lock:
                query_emb = model.encode(query, convert_to_tensor=True)
                chunk_embs = model.encode(chunks, convert_to_tensor=True, batch_size=32)
            sims = util.cos_sim(query_emb, chunk_embs)[0]
            best = [0.0] * len(texts)
            for owner, sim in zip(owners, sims):
                value = float(sim.item())
                if value > best[owner]:
                    best[owner] = value
            return [max(0.0, min(1.0, v)) for v in best]
        except Exception as e:
            logger.debug("SBERT scoring failed: %s", e)
            return None

    # ── public ──────────────────────────────────────────────────────────────

    def calculate(self, text: str, query: str, use_semantic: bool = True) -> float:
        """Вычислить релевантность текста запросу (0..1)"""
        if not text or not query:
            return 0.0
        kw = self.keyword_score(text, query)
        if use_semantic:
            sem = self.semantic_scores([text[:NLP_CONFIG.max_text_length]], query)
            if sem:
                return max(kw, sem[0])
        return kw

    def calculate_batch(self, texts: List[str], query: str, use_semantic: bool = True) -> List[float]:
        """Батчевое вычисление релевантности"""
        if not texts or not query:
            return [0.0] * len(texts)
        matcher = self._matcher(query)
        kw = [self.keyword_score(t, query, matcher) for t in texts]
        if use_semantic:
            sem = self.semantic_scores([(t or '')[:NLP_CONFIG.max_text_length] for t in texts], query)
            if sem:
                return [max(a, b) for a, b in zip(kw, sem)]
        return kw

    # Совместимость со старым API
    def _enhanced_similarity(self, text: str, query: str) -> float:
        return self.keyword_score(text, query)


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


def calculate_relevance_batch(texts: List[str], query: str, use_semantic: bool = True) -> List[float]:
    """Батчевое вычисление релевантности"""
    return relevance_calculator.calculate_batch(texts, query, use_semantic=use_semantic)


def expand_query(query: str) -> Set[str]:
    """Расширить запрос морфологическими формами"""
    return morph_analyzer.expand_query(query)


def fuzzy_search(query: str, candidates: List[str], threshold: Optional[int] = None) -> List[Tuple[str, float]]:
    """Нечёткий поиск"""
    return FuzzyMatcher.find_matches(query, candidates, threshold)


__all__ = [
    'entity_extractor', 'relevance_calculator', 'morph_analyzer',
    'extract_entities', 'calculate_relevance', 'calculate_relevance_batch',
    'expand_query', 'fuzzy_search', 'build_query_matcher', 'tokenize_words',
    'EntityExtractor', 'RelevanceCalculator', 'MorphologyAnalyzer', 'FuzzyMatcher',
    'QueryMatcher', 'QueryTerm', 'MatchInfo', 'STOPWORDS', 'ENTITY_TYPES',
]
