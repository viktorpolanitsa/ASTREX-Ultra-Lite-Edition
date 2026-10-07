#!/usr/bin/env python3
"""
ASTREX v3.0 — Timeline Builder
Построение хронологии событий из извлечённых дат и документов
"""

import re
from datetime import datetime, date
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field
from collections import defaultdict

from .logging_setup import get_logger

log = get_logger("astrex.timeline")


# ═══════════════════════════════════════════════════════════════════════════════
# DATA STRUCTURES
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class TimelineEvent:
    """Событие на временной шкале"""
    date: str                          # ISO date string
    date_parsed: Optional[date] = None
    text: str = ""                     # описание
    source: str = ""                   # файл-источник
    event_type: str = "mention"        # mention, created, modified, transaction, agreement
    entities: List[str] = field(default_factory=list)
    confidence: float = 1.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "date": self.date,
            "text": self.text,
            "source": self.source,
            "event_type": self.event_type,
            "entities": self.entities,
            "confidence": self.confidence,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# DATE PARSER
# ═══════════════════════════════════════════════════════════════════════════════

_MONTHS_RU = {
    'января': 1, 'февраля': 2, 'марта': 3, 'апреля': 4,
    'мая': 5, 'июня': 6, 'июля': 7, 'августа': 8,
    'сентября': 9, 'октября': 10, 'ноября': 11, 'декабря': 12,
    'январь': 1, 'февраль': 2, 'март': 3, 'апрель': 4,
    'май': 5, 'июнь': 6, 'июль': 7, 'август': 8,
    'сентябрь': 9, 'октябрь': 10, 'ноябрь': 11, 'декабрь': 12,
}

_DATE_PATTERNS = [
    # DD.MM.YYYY or DD/MM/YYYY
    (re.compile(r'\b(\d{1,2})[\.\/](\d{1,2})[\.\/](\d{4})\b'), 'dmy'),
    # YYYY-MM-DD (ISO)
    (re.compile(r'\b(\d{4})-(\d{1,2})-(\d{1,2})\b'), 'ymd'),
    # DD month YYYY (Russian)
    (re.compile(
        r'\b(\d{1,2})\s+(' + '|'.join(_MONTHS_RU.keys()) + r')\s+(\d{4})\b',
        re.IGNORECASE
    ), 'dMy'),
    # month YYYY
    (re.compile(
        r'\b(' + '|'.join(_MONTHS_RU.keys()) + r')\s+(\d{4})\b',
        re.IGNORECASE
    ), 'My'),
]


def parse_date(text: str) -> Optional[date]:
    """Попробовать распарсить дату из строки"""
    text = text.strip()
    for pattern, fmt in _DATE_PATTERNS:
        m = pattern.search(text)
        if not m:
            continue
        try:
            if fmt == 'dmy':
                return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
            elif fmt == 'ymd':
                return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            elif fmt == 'dMy':
                month = _MONTHS_RU.get(m.group(2).lower(), 0)
                if month:
                    return date(int(m.group(3)), month, int(m.group(1)))
            elif fmt == 'My':
                month = _MONTHS_RU.get(m.group(1).lower(), 0)
                if month:
                    return date(int(m.group(2)), month, 1)
        except (ValueError, OverflowError):
            continue
    return None


def extract_dates_from_text(text: str, max_dates: int = 200) -> List[Tuple[date, str, int]]:
    """Извлечь все даты из текста с контекстом.

    Совпадения разных шаблонов, вложенные друг в друга ("марта 2024" внутри
    "15 марта 2024"), отбрасываются — иначе появлялось ложное событие
    "1 марта". Одна и та же дата в разных местах документа даёт разные
    события, но повторы в пределах ±200 символов объединяются.

    Returns:
        Список (дата, контекст, позиция)
    """
    sample = text[:100_000]
    candidates: List[Tuple[int, int, date]] = []
    for pattern, _fmt in _DATE_PATTERNS:
        for m in pattern.finditer(sample):
            d = parse_date(m.group(0))
            if d and 1900 <= d.year <= 2100:
                candidates.append((m.start(), m.end(), d))

    # Предпочитаем более длинные (конкретные) совпадения, вложенные — убираем
    candidates.sort(key=lambda c: (-(c[1] - c[0]), c[0]))
    taken: List[Tuple[int, int]] = []
    accepted: List[Tuple[int, int, date]] = []
    for start, end, d in candidates:
        if any(start < t_end and end > t_start for t_start, t_end in taken):
            continue
        taken.append((start, end))
        accepted.append((start, end, d))
    accepted.sort(key=lambda c: c[0])

    results = []
    last_pos: Dict[date, int] = {}
    for start, end, d in accepted:
        if d in last_pos and start - last_pos[d] < 200:
            continue
        last_pos[d] = start
        ctx_start = max(0, start - 100)
        ctx_end = min(len(text), end + 100)
        ctx = text[ctx_start:ctx_end].strip().replace('\n', ' ')
        results.append((d, ctx, start))
        if len(results) >= max_dates:
            break

    results.sort(key=lambda x: (x[0], x[2]))
    return results


# ═══════════════════════════════════════════════════════════════════════════════
# TIMELINE BUILDER
# ═══════════════════════════════════════════════════════════════════════════════

class TimelineBuilder:
    """Построение хронологии из множества документов"""

    def __init__(self):
        self.events: List[TimelineEvent] = []

    def add_document(
        self,
        text: str,
        source: str = "",
        entities: Dict[str, List[str]] = None,
        file_mtime: float = None,
    ) -> int:
        """Добавить документ для анализа.

        Returns:
            Количество найденных событий
        """
        count = 0

        # File modification time
        if file_mtime:
            try:
                dt = datetime.fromtimestamp(file_mtime)
                self.events.append(TimelineEvent(
                    date=dt.date().isoformat(),
                    date_parsed=dt.date(),
                    text=f"Файл изменён: {source}",
                    source=source,
                    event_type="modified",
                    confidence=1.0,
                ))
                count += 1
            except Exception as e:
                log.debug("Failed to parse file mtime for %s: %s", source, e)

        # Dates from text
        dates = extract_dates_from_text(text)
        related_entities = []
        if entities:
            for vals in entities.values():
                related_entities.extend(vals[:5])

        for d, ctx, _ in dates:
            event_type = _classify_event(ctx)
            self.events.append(TimelineEvent(
                date=d.isoformat(),
                date_parsed=d,
                text=ctx[:300],
                source=source,
                event_type=event_type,
                entities=related_entities[:10],
                confidence=0.8,
            ))
            count += 1

        return count

    def build(self) -> List[TimelineEvent]:
        """Построить отсортированную хронологию"""
        self.events.sort(key=lambda e: e.date)
        return self.events

    def get_summary(self) -> Dict[str, Any]:
        """Получить сводку хронологии"""
        events = self.build()
        if not events:
            return {"total_events": 0}

        by_type = defaultdict(int)
        by_year = defaultdict(int)
        for e in events:
            by_type[e.event_type] += 1
            if e.date_parsed:
                by_year[e.date_parsed.year] += 1

        return {
            "total_events": len(events),
            "date_range": f"{events[0].date} — {events[-1].date}",
            "by_type": dict(by_type),
            "by_year": dict(sorted(by_year.items())),
            "unique_sources": len(set(e.source for e in events)),
        }

    def to_list(self) -> List[Dict]:
        """Сериализация для отчётов"""
        return [e.to_dict() for e in self.build()]

    def clear(self):
        self.events.clear()


def _classify_event(context: str) -> str:
    """Классифицировать событие по контексту"""
    ctx = context.lower().replace('ё', 'е')
    if any(w in ctx for w in ('договор', 'контракт', 'соглашени', 'подписан')):
        return "agreement"
    if any(w in ctx for w in ('оплат', 'перевод', 'сумм', 'рубл', 'доллар', 'платеж')):
        return "transaction"
    if any(w in ctx for w in ('встреч', 'совещани', 'заседани', 'собрани')):
        return "meeting"
    if any(w in ctx for w in ('создан', 'зарегистрирован', 'основан', 'учрежден')):
        return "created"
    if any(w in ctx for w in ('родил', 'дата рождения')):
        return "birth"
    return "mention"


__all__ = [
    'TimelineBuilder', 'TimelineEvent',
    'parse_date', 'extract_dates_from_text'
]
