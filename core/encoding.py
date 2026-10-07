#!/usr/bin/env python3
"""
ASTREX v3.0 — Encoding detection
Определение кодировки текста с учётом кириллицы.

Почему не просто перебор кодировок: cp1251 и latin-1 "успешно" декодируют
почти любые байты, поэтому KOI8-R / CP866 / ISO-8859-5 при простом переборе
никогда не выбираются и превращаются в кракозябры. Здесь кандидаты
оцениваются по частотам русских букв и доле строчных букв.
"""

import codecs
from collections import Counter
import re
from typing import Optional, Tuple

_BOMS = (
    (codecs.BOM_UTF32_LE, 'utf-32-le'),
    (codecs.BOM_UTF32_BE, 'utf-32-be'),
    (codecs.BOM_UTF8, 'utf-8-sig'),
    (codecs.BOM_UTF16_LE, 'utf-16-le'),
    (codecs.BOM_UTF16_BE, 'utf-16-be'),
)

_CYRILLIC_CANDIDATES = ('cp1251', 'koi8-r', 'cp866', 'iso-8859-5', 'mac_cyrillic')

_EAST_SLAVIC_LOWER = 'абвгдеёжзийклмнопрстуфхцчшщъыьэюяіїєґў'
_EAST_SLAVIC_LETTERS = frozenset(_EAST_SLAVIC_LOWER + _EAST_SLAVIC_LOWER.upper())
_EAST_SLAVIC_WORD_RE = re.compile('[' + _EAST_SLAVIC_LOWER + _EAST_SLAVIC_LOWER.upper() + ']+')

# Частые русские биграммы: в тексте, декодированном не той кодировкой, их мало
_RU_BIGRAMS = frozenset('''
    ст но то на ен ов ни ра во ко по ро ер ть ли ал пр ет ор ос ла не ол го ел ва он ка та от
    ле ий ом од ес ан ло те ил ре ат ти ит ем ой ие ьн ве де ин ны да об ис вы ед ме ак ей из
    ск ль ам ри ая ые ых ия им ми ег ут ую ся тр зн ча че жи ши ку ту мо му бо бы ру ду ди ма
    ог ез ев ок ик нт нн сл св сп вс вн ки ги ии ию ью ья ье ют ят уд ус ым ое ее ям ах ях ца
    це ци ще щи ша жа жн жд зд зо зв за ры лу лы ля ню ня ви ды ба бе би бу пе пи пу па пл сс
    см сн со су сы са се си сь сх тв тк тн тс ты фи фо фа хо ха хи чт чн чи чу ше шл шн шк
'''.split())

# Слова из одной буквы, возможные в русском/украинском/белорусском тексте
_SINGLE_LETTER_WORDS = frozenset('авикосуяжбзйіў')

# Априорная распространённость: при равных оценках выбирается более частая кодировка
_ENCODING_PRIOR = {'cp1251': 1.0, 'koi8-r': 0.98, 'cp866': 0.98, 'iso-8859-5': 0.92, 'mac_cyrillic': 0.92}

# Объём текста для оценки кодировки (статистики хватает с запасом)
_SCORE_SAMPLE_SIZE = 16 * 1024

# Частоты букв русского языка (%)
_RU_FREQ = {
    'о': 10.97, 'е': 8.45, 'а': 8.01, 'и': 7.35, 'н': 6.70, 'т': 6.26,
    'с': 5.47, 'р': 4.73, 'в': 4.54, 'л': 4.40, 'к': 3.49, 'м': 3.21,
    'д': 2.98, 'п': 2.81, 'у': 2.62, 'я': 2.01, 'ы': 1.90, 'ь': 1.74,
    'г': 1.70, 'з': 1.65, 'б': 1.59, 'ч': 1.44, 'й': 1.21, 'х': 0.97,
    'ж': 0.94, 'ш': 0.73, 'ю': 0.64, 'ц': 0.48, 'щ': 0.36, 'э': 0.32,
    'ф': 0.26, 'ъ': 0.04, 'ё': 0.04,
}

_SAMPLE_SIZE = 256 * 1024
_CONTROL_RE = re.compile(rb'[\x00-\x08\x0e-\x1a\x1c-\x1f]')


def _bom_encoding(data: bytes) -> Optional[str]:
    for bom, enc in _BOMS:
        if data.startswith(bom):
            return enc
    return None


def _looks_utf16(sample: bytes) -> Optional[str]:
    """UTF-16 без BOM.

    Старший байт кодовой единицы почти всегда принимает 1–3 значения
    (0x00 — латиница и пробелы, 0x04 — кириллица, 0x20 — типографские знаки),
    тогда как младший разнообразен. Раньше проверялись только нулевые байты,
    и русский текст в UTF-16 без BOM (нулей ~15%) не распознавался.
    """
    n = len(sample) - (len(sample) % 2)
    if n < 16:
        return None
    sample = sample[:n]
    for enc, high, low in (('utf-16-le', sample[1::2], sample[0::2]),
                           ('utf-16-be', sample[0::2], sample[1::2])):
        top = Counter(high).most_common(3)
        if sum(c for _, c in top) < 0.9 * len(high) or len(set(low)) < 4:
            continue
        if low.count(0) > 0.05 * len(low):
            continue
        try:
            text = codecs.getincrementaldecoder(enc)('strict').decode(sample, final=False)
        except UnicodeDecodeError:
            continue
        if not text:
            continue
        printable = sum(1 for ch in text if ch.isprintable() or ch in '\r\n\t')
        wordlike = sum(1 for ch in text if ch.isalnum() or ch.isspace())
        if printable >= 0.95 * len(text) and wordlike >= 0.6 * len(text):
            return enc
    return None


def _utf8_ok(sample: bytes, final: bool) -> bool:
    decoder = codecs.getincrementaldecoder('utf-8')('strict')
    try:
        decoder.decode(sample, final=final)
        return True
    except UnicodeDecodeError:
        return False


def _mixed_script_ratio(sample: bytes) -> float:
    """Доля старших байтов, соседствующих с латинской буквой.

    В кириллическом тексте старшие байты идут целыми словами, а в
    западноевропейском (é, à, ü) — поодиночке внутри латинских слов.
    """
    high = 0
    mixed = 0
    n = len(sample)
    for i, b in enumerate(sample):
        if b < 0x80:
            continue
        high += 1
        prev_b = sample[i - 1] if i > 0 else 0x20
        next_b = sample[i + 1] if i + 1 < n else 0x20
        if (0x41 <= prev_b <= 0x5a or 0x61 <= prev_b <= 0x7a or
                0x41 <= next_b <= 0x5a or 0x61 <= next_b <= 0x7a):
            mixed += 1
    return mixed / high if high else 0.0


def _cyrillic_score(sample: bytes, encoding: str, mixed_ratio: float = 0.0) -> float:
    """Оценка "похожести на русский текст" для 8-битной кодировки (≈0..1.2).

    Учитываются: доля "своих" букв среди старших символов, частоты букв,
    доля частых русских биграмм, форма слов по регистру (ВСЕ ЗАГЛАВНЫЕ,
    Первая заглавная или все строчные — а не "пРИВЕТ") и невозможные слова
    (одиночная "ь", слово на "ы"). Раньше решала доля строчных букв, и
    текст ЗАГЛАВНЫМИ в cp1251 определялся как KOI8-R (и наоборот).
    """
    try:
        text = sample[:_SCORE_SAMPLE_SIZE].decode(encoding, errors='replace')
    except LookupError:
        return 0.0
    counts = Counter(text)
    high = sum(c for ch, c in counts.items() if ord(ch) >= 0x80)
    if not high:
        return 0.0
    # Только буквы русского/украинского/белорусского алфавитов: "чужие"
    # кириллические буквы (ѕ, ќ, ј, Џ…) получаются при неверной кодировке —
    # например, MacCyrillic превращает cp1251-"П" в "ѕ".
    letter_counts = {ch: c for ch, c in counts.items() if ch in _EAST_SLAVIC_LETTERS}
    letters = sum(letter_counts.values())
    if not letters:
        return 0.0
    letter_ratio = letters / high
    freq = sum(_RU_FREQ.get(ch.lower(), 0.0) * c for ch, c in letter_counts.items()) / letters

    words = _EAST_SLAVIC_WORD_RE.findall(text)
    pairs = sum(len(w) - 1 for w in words)
    if pairs:
        lowered = text.lower()
        bigram_ratio = min(1.0, sum(lowered.count(bg) for bg in _RU_BIGRAMS) / pairs)
    else:
        bigram_ratio = 0.5   # однобуквенные слова — нейтрально
    good_case = impossible = 0
    for w in words:
        n = len(w)
        if w.islower() or w.isupper() or (w[0].isupper() and w[1:].islower()):
            good_case += n
        lw = w.lower()
        if (n == 1 and lw not in _SINGLE_LETTER_WORDS) or lw[0] in 'ьъы':
            impossible += n
    word_chars = sum(len(w) for w in words) or 1
    box = sum(c for ch, c in counts.items() if '\u2500' <= ch <= '\u259f')   # псевдографика

    score = ((letter_ratio ** 2)
             * min(1.2, freq / 5.5)
             * (0.2 + 0.8 * min(1.0, bigram_ratio / 0.5))
             * (0.3 + 0.7 * good_case / word_chars)
             * (1.0 - impossible / word_chars)
             * (1.0 - 0.5 * box / high))
    return score * _ENCODING_PRIOR.get(encoding, 1.0) * (1.0 - mixed_ratio)


def detect_encoding(data: bytes, final: bool = True) -> Tuple[str, float]:
    """Определить кодировку байтов.

    Args:
        data: байты (используются первые 256 КБ)
        final: False — данные обрезаны, незавершённая последовательность
               UTF-8 в конце не считается ошибкой

    Returns:
        (имя кодировки, уверенность 0..1)
    """
    if not data:
        return 'utf-8', 1.0

    bom = _bom_encoding(data)
    if bom:
        return bom, 1.0

    sample = data[:_SAMPLE_SIZE]
    is_final = final and len(data) <= _SAMPLE_SIZE

    # Корректный UTF-8 с не-ASCII байтами — почти наверняка UTF-8 (текст в
    # UTF-16 таким не бывает). Проверяется ДО эвристики UTF-16: короткая
    # кириллица в UTF-8 (байты D0/D1 строго через один) похожа на UTF-16BE.
    has_high = not sample.isascii()
    if has_high and _utf8_ok(sample, is_final):
        return 'utf-8', 0.99

    # UTF-16 без BOM — до проверки на ASCII: кириллица в UTF-16LE состоит
    # из 7-битных байтов (0x10–0x4F, 0x04)
    utf16 = _looks_utf16(sample)
    if utf16:
        return utf16, 0.8

    if not has_high:
        return 'utf-8', 1.0  # чистый ASCII

    mixed = _mixed_script_ratio(sample[:65536])
    scores = {enc: _cyrillic_score(sample, enc, mixed) for enc in _CYRILLIC_CANDIDATES}
    best = max(scores, key=scores.get)
    if scores[best] >= 0.35:
        return best, min(0.95, scores[best])

    # Не кириллица — спросим chardet (если установлен)
    try:
        import chardet
        guess = chardet.detect(sample)
        enc = guess.get('encoding')
        conf = guess.get('confidence') or 0.0
        if enc and conf >= 0.5:
            codecs.lookup(enc)
            return enc.lower(), conf
    except Exception:
        pass

    if scores[best] > 0.15:
        return best, scores[best]
    return 'cp1252', 0.3


def decode_bytes(data: bytes, declared: Optional[str] = None,
                 final: bool = True) -> Tuple[str, str]:
    """Декодировать байты в текст.

    Args:
        data: исходные байты
        declared: кодировка, заявленная источником (заголовок письма, XML и т.п.)
        final: False — данные обрезаны (хвост может содержать половину символа)

    Returns:
        (текст, использованная кодировка)
    """
    if not data:
        return '', declared or 'utf-8'

    if declared:
        try:
            codecs.lookup(declared)
            decoder = codecs.getincrementaldecoder(declared)('strict')
            text = decoder.decode(data, final=final)
            return text, declared
        except (LookupError, UnicodeDecodeError, ValueError):
            pass

    encoding, _ = detect_encoding(data, final=final)
    decoder = codecs.getincrementaldecoder(encoding)('replace')
    text = decoder.decode(data, final=final)
    return text, encoding


def is_probably_binary(sample: bytes) -> bool:
    """Похоже ли содержимое на двоичные данные (а не текст)."""
    if not sample:
        return False
    if _bom_encoding(sample) or _looks_utf16(sample[:4096]):
        return False
    head = sample[:8192]
    if b'\x00' in head:
        return True
    controls = len(_CONTROL_RE.findall(head))
    return controls / len(head) > 0.10


# Символы, недопустимые в XML 1.0
_XML_INVALID_RE = re.compile('[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]')


def sanitize_xml_text(text: str) -> str:
    """Удалить символы, которые нельзя записать в XML 1.0."""
    if text is None:
        return ''
    return _XML_INVALID_RE.sub('', str(text))


__all__ = ['detect_encoding', 'decode_bytes', 'is_probably_binary', 'sanitize_xml_text']
