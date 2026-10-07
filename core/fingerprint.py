"""
Document fingerprinting and deduplication module for ASTREX v3.

Provides cryptographic hashing (over the WHOLE file), locality-sensitive
hashing (simhash) and near-duplicate detection using Jaccard similarity
estimated from bottom-k sketches of word shingles.
"""

import hashlib
import heapq
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import List, Set, Tuple, Optional, Dict

from .encoding import decode_bytes, is_probably_binary
from .logging_setup import get_logger

logger = get_logger("astrex.fingerprint")


__all__ = [
    'DocumentFingerprint',
    'FingerprintEngine',
    'DedupReport',
    'deduplicate',
]

_WORD_RE = re.compile(r'\w+')
SKETCH_SIZE = 256              # размер bottom-k скетча (точность оценки Jaccard ~±6%)
MAX_TEXT_CHARS = 2_000_000     # текста на документ для шинглов
MIN_WORDS = 10                 # меньше — документ слишком короткий для near-dup


def _bit_count(x: int) -> int:
    try:
        return x.bit_count()
    except AttributeError:  # Python < 3.10
        return bin(x).count('1')


@dataclass
class DocumentFingerprint:
    """Fingerprint representation of a document."""
    file_path: str
    md5: str
    sha256: str
    size: int
    simhash: int  # locality-sensitive hash for near-duplicate detection
    shingles: Optional[Set[int]] = None  # bottom-k скетч хешей шинглов (None — нет текста)
    shingle_count: int = 0               # полное число уникальных шинглов


@dataclass
class DedupReport:
    """Deduplication analysis report."""
    total_files: int
    unique_files: int
    exact_duplicate_groups: List[List[str]]
    near_duplicate_pairs: List[Tuple[str, str, float]]
    wasted_bytes: int  # total size of duplicate files


class FingerprintEngine:
    """Engine for computing document fingerprints and detecting duplicates."""

    def __init__(self, shingle_size: int = 5, simhash_bits: int = 64, use_extractors: bool = True):
        """
        Args:
            shingle_size: Number of words per shingle (w-shingle)
            simhash_bits: Number of bits in simhash (default 64)
            use_extractors: извлекать текст документов (PDF, DOCX…) для сравнения
                            содержимого, а не сырых (сжатых) байтов
        """
        self.shingle_size = shingle_size
        self.simhash_bits = simhash_bits
        self.use_extractors = use_extractors
        logger.debug(f"FingerprintEngine initialized: shingle_size={shingle_size}, simhash_bits={simhash_bits}")

    # ── fingerprints ─────────────────────────────────────────────────────────

    def fingerprint_file(self, path: str) -> DocumentFingerprint:
        """Compute fingerprint from file content.

        Raises:
            OSError: If file cannot be read
        """
        md5 = hashlib.md5()
        sha256 = hashlib.sha256()
        size = 0
        head = bytearray()
        with open(path, 'rb') as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b''):
                md5.update(chunk)
                sha256.update(chunk)
                size += len(chunk)
                if len(head) < 4 * 1024 * 1024:
                    head.extend(chunk[:4 * 1024 * 1024 - len(head)])

        text = self._text_for_shingles(path, bytes(head))
        simhash, sketch, count = self._sketch(text) if text else (0, None, 0)

        return DocumentFingerprint(
            file_path=path,
            md5=md5.hexdigest(),
            sha256=sha256.hexdigest(),
            size=size,
            simhash=simhash,
            shingles=sketch,
            shingle_count=count,
        )

    def _text_for_shingles(self, path: str, head: bytes) -> Optional[str]:
        """Текст документа для near-duplicate сравнения."""
        if self.use_extractors:
            try:
                from extractors import registry
                extractor = registry.get_extractor(Path(path))
                if extractor is not None and extractor.__name__ not in ('PlainTextExtractor',):
                    result = registry.extract(Path(path))
                    if result.success and result.full_text:
                        return result.full_text[:MAX_TEXT_CHARS]
                    return None
            except Exception as e:
                logger.debug(f"Text extraction failed for {path}: {e}")
        if is_probably_binary(head[:8192]):
            return None
        text, _enc = decode_bytes(head, final=False)
        return text[:MAX_TEXT_CHARS]

    def fingerprint_text(self, text: str, path: str = "") -> DocumentFingerprint:
        """Compute fingerprint from text string."""
        content_bytes = text.encode('utf-8')
        simhash, sketch, count = self._sketch(text)
        return DocumentFingerprint(
            file_path=path,
            md5=hashlib.md5(content_bytes).hexdigest(),
            sha256=hashlib.sha256(content_bytes).hexdigest(),
            size=len(content_bytes),
            simhash=simhash,
            shingles=sketch,
            shingle_count=count,
        )

    def _shingle_hashes(self, text: str) -> Set[int]:
        words = _WORD_RE.findall(text.lower().replace('ё', 'е'))
        if len(words) < max(MIN_WORDS, self.shingle_size):
            return set()
        hashes = set()
        for i in range(len(words) - self.shingle_size + 1):
            shingle = ' '.join(words[i:i + self.shingle_size])
            h = hashlib.blake2b(shingle.encode('utf-8'), digest_size=8).digest()
            hashes.add(int.from_bytes(h, 'little'))
        return hashes

    def _sketch(self, text: str) -> Tuple[int, Optional[Set[int]], int]:
        """simhash + bottom-k скетч шинглов текста."""
        hashes = self._shingle_hashes(text)
        if not hashes:
            return 0, None, 0
        sketch = set(heapq.nsmallest(SKETCH_SIZE, hashes))
        return self._simhash_from_hashes(hashes), sketch, len(hashes)

    def _simhash_from_hashes(self, hashes: Set[int]) -> int:
        """Simhash из 64-битных хешей шинглов (numpy, если доступен)."""
        bits = self.simhash_bits
        values = list(hashes)
        if len(values) > 50000:  # для скорости — равномерная выборка
            step = len(values) / 50000
            values = [values[int(i * step)] for i in range(50000)]
        try:
            import numpy as np
            arr = np.array(values, dtype=np.uint64)
            counts = np.zeros(bits, dtype=np.int64)
            for j in range(bits):
                counts[j] = int(((arr >> np.uint64(j)) & np.uint64(1)).sum())
            total = len(values)
            simhash = 0
            for j in range(bits):
                if counts[j] * 2 > total:
                    simhash |= (1 << j)
            return simhash
        except ImportError:
            total = len(values)
            simhash = 0
            for j in range(bits):
                ones = sum((h >> j) & 1 for h in values)
                if ones * 2 > total:
                    simhash |= (1 << j)
            return simhash

    # Совместимость со старым API
    def _compute_simhash(self, text: str) -> int:
        return self._sketch(text)[0]

    def _compute_shingles(self, text: str) -> Set[int]:
        return self._shingle_hashes(text)

    def _hamming_distance(self, a: int, b: int) -> int:
        """Number of differing bits between two simhash values."""
        return _bit_count(a ^ b)

    # ── duplicate detection ──────────────────────────────────────────────────

    def find_exact_duplicates(self, fingerprints: List[DocumentFingerprint]) -> List[List[str]]:
        """Group documents by full-content SHA-256 to find exact duplicates."""
        groups: Dict[Tuple[str, int], List[str]] = defaultdict(list)
        for fp in fingerprints:
            groups[(fp.sha256, fp.size)].append(fp.file_path)
        duplicate_groups = [sorted(g) for g in groups.values() if len(g) > 1]
        logger.info(f"Found {len(duplicate_groups)} exact duplicate groups")
        return duplicate_groups

    def find_near_duplicates(
        self,
        fingerprints: List[DocumentFingerprint],
        threshold: float = 0.8
    ) -> List[Tuple[str, str, float]]:
        """Find pairs of (non-identical) documents with Jaccard similarity >= threshold."""
        near_duplicates = []
        valid = [fp for fp in fingerprints if fp.shingles]

        # simhash-префильтр. Для бинарных векторов косинус ≥ Jaccard, поэтому
        # ожидаемая доля различающихся бит не больше p = arccos(J)/π; запас в
        # 4σ биномиального разброса делает пропуск настоящей пары практически
        # невозможным (раньше запас был 8 бит ≈ 2.5σ — терялась ~1% пар).
        import math
        p = math.acos(max(-1.0, min(1.0, threshold))) / math.pi
        n = self.simhash_bits
        threshold_bits = int(n * p + 4 * math.sqrt(n * p * (1 - p))) + 1

        for i in range(len(valid)):
            fp1 = valid[i]
            for j in range(i + 1, len(valid)):
                fp2 = valid[j]
                if fp1.sha256 == fp2.sha256 and fp1.size == fp2.size:
                    continue
                if _bit_count(fp1.simhash ^ fp2.simhash) > threshold_bits:
                    continue
                similarity = self.jaccard_similarity(fp1.shingles, fp2.shingles)
                if similarity >= threshold:
                    near_duplicates.append((fp1.file_path, fp2.file_path, round(similarity, 4)))

        logger.info(f"Found {len(near_duplicates)} near-duplicate pairs (threshold={threshold})")
        return near_duplicates

    def jaccard_similarity(self, a: Optional[Set[int]], b: Optional[Set[int]]) -> float:
        """Jaccard similarity, оценённая по bottom-k скетчам (точная для малых множеств).

        Пустые множества несравнимы — схожесть 0 (раньше два пустых
        документа считались идентичными: 1.0).
        """
        if not a or not b:
            return 0.0
        if len(a) < SKETCH_SIZE and len(b) < SKETCH_SIZE:
            return len(a & b) / len(a | b)
        union_sketch = heapq.nsmallest(SKETCH_SIZE, a | b)
        both = sum(1 for h in union_sketch if h in a and h in b)
        return both / len(union_sketch)


def deduplicate(
    fingerprints: List[DocumentFingerprint],
    near_threshold: float = 0.8
) -> DedupReport:
    """Perform deduplication analysis on a collection of fingerprints."""
    engine = FingerprintEngine()

    exact_duplicate_groups = engine.find_exact_duplicates(fingerprints)
    near_duplicate_pairs = engine.find_near_duplicates(fingerprints, near_threshold)

    sizes: Dict[Tuple[str, int], int] = {}
    counts: Dict[Tuple[str, int], int] = defaultdict(int)
    for fp in fingerprints:
        key = (fp.sha256, fp.size)
        sizes[key] = fp.size
        counts[key] += 1

    wasted_bytes = sum((count - 1) * sizes[key] for key, count in counts.items() if count > 1)

    report = DedupReport(
        total_files=len(fingerprints),
        unique_files=len(sizes),
        exact_duplicate_groups=exact_duplicate_groups,
        near_duplicate_pairs=near_duplicate_pairs,
        wasted_bytes=wasted_bytes
    )

    logger.info(
        f"Deduplication complete: {report.total_files} total, "
        f"{report.unique_files} unique, "
        f"{len(report.exact_duplicate_groups)} duplicate groups, "
        f"{len(report.near_duplicate_pairs)} near-duplicate pairs, "
        f"{report.wasted_bytes} wasted bytes"
    )

    return report
