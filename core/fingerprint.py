"""
Document fingerprinting and deduplication module for ASTREX v3.

Provides cryptographic hashing, locality-sensitive hashing (simhash),
and near-duplicate detection using Jaccard similarity.
"""

import hashlib
import re
from typing import List, Set, Tuple, Optional, Dict
from dataclasses import dataclass, field
from collections import defaultdict

from .logging_setup import get_logger

logger = get_logger(__name__)


__all__ = [
    'DocumentFingerprint',
    'FingerprintEngine',
    'DedupReport',
    'deduplicate',
]


@dataclass
class DocumentFingerprint:
    """Fingerprint representation of a document."""
    file_path: str
    md5: str
    sha256: str
    size: int
    simhash: int  # locality-sensitive hash for near-duplicate detection
    shingles: Optional[Set[int]] = None  # for Jaccard similarity


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

    def __init__(self, shingle_size: int = 5, simhash_bits: int = 64):
        """
        Initialize the fingerprint engine.

        Args:
            shingle_size: Number of words per shingle (w-shingle)
            simhash_bits: Number of bits in simhash (default 64)
        """
        self.shingle_size = shingle_size
        self.simhash_bits = simhash_bits
        logger.info(f"FingerprintEngine initialized: shingle_size={shingle_size}, simhash_bits={simhash_bits}")

    def fingerprint_file(self, path: str) -> DocumentFingerprint:
        """
        Compute fingerprint from file content.

        Args:
            path: Path to the file

        Returns:
            DocumentFingerprint with all hash values computed

        Raises:
            IOError: If file cannot be read
        """
        try:
            with open(path, 'rb') as f:
                content_bytes = f.read(10 * 1024 * 1024)  # 10MB cap — sufficient for fingerprinting

            # Decode to text for simhash/shingles
            try:
                text = content_bytes.decode('utf-8', errors='ignore')
            except Exception as e:
                logger.warning(f"Error decoding {path}: {e}, using binary mode")
                text = content_bytes.decode('latin-1')

            # Compute cryptographic hashes
            md5 = hashlib.md5(content_bytes).hexdigest()
            sha256 = hashlib.sha256(content_bytes).hexdigest()
            size = len(content_bytes)

            # Compute locality-sensitive hashes
            simhash = self._compute_simhash(text)
            shingles = self._compute_shingles(text)

            return DocumentFingerprint(
                file_path=path,
                md5=md5,
                sha256=sha256,
                size=size,
                simhash=simhash,
                shingles=shingles
            )
        except Exception as e:
            logger.error(f"Error fingerprinting file {path}: {e}")
            raise

    def fingerprint_text(self, text: str, path: str = "") -> DocumentFingerprint:
        """
        Compute fingerprint from text string.

        Args:
            text: Text content
            path: Optional path identifier

        Returns:
            DocumentFingerprint with all hash values computed
        """
        content_bytes = text.encode('utf-8')

        # Compute cryptographic hashes
        md5 = hashlib.md5(content_bytes).hexdigest()
        sha256 = hashlib.sha256(content_bytes).hexdigest()
        size = len(content_bytes)

        # Compute locality-sensitive hashes
        simhash = self._compute_simhash(text)
        shingles = self._compute_shingles(text)

        return DocumentFingerprint(
            file_path=path,
            md5=md5,
            sha256=sha256,
            size=size,
            simhash=simhash,
            shingles=shingles
        )

    def _compute_simhash(self, text: str) -> int:
        """
        Compute simhash of text using shingles.

        Algorithm:
        1. Split text into w-shingles (sequences of w words)
        2. Hash each shingle
        3. For each bit position, sum +1 if bit is 1, -1 if bit is 0
        4. Final hash: bit i is 1 if sum[i] > 0

        Args:
            text: Input text

        Returns:
            Simhash value as integer
        """
        # Tokenize into words
        words = re.findall(r'\w+', text.lower())

        if not words:
            return 0

        # Initialize bit vector
        v = [0] * self.simhash_bits

        # Generate shingles and update bit vector
        for i in range(len(words) - self.shingle_size + 1):
            shingle = ' '.join(words[i:i + self.shingle_size])

            # Hash the shingle
            h = hashlib.md5(shingle.encode('utf-8')).digest()

            # Convert to integer
            hash_int = int.from_bytes(h, byteorder='big')

            # Update bit vector
            for j in range(self.simhash_bits):
                if (hash_int >> j) & 1:
                    v[j] += 1
                else:
                    v[j] -= 1

        # Compute final simhash
        simhash = 0
        for j in range(self.simhash_bits):
            if v[j] > 0:
                simhash |= (1 << j)

        return simhash

    def _compute_shingles(self, text: str) -> Set[int]:
        """
        Compute set of hashed w-shingles.

        Uses blake2b (8-byte digest) instead of Python's built-in hash() because
        hash() is randomized per-process via PYTHONHASHSEED — shingles computed in
        different processes or runs cannot be compared for near-duplicate detection.

        Args:
            text: Input text

        Returns:
            Set of shingle hashes (deterministic across runs)
        """
        words = re.findall(r'\w+', text.lower())

        if not words:
            return set()

        shingles = set()

        for i in range(len(words) - self.shingle_size + 1):
            shingle = ' '.join(words[i:i + self.shingle_size])
            # blake2b with digest_size=8 is fast and deterministic
            h = hashlib.blake2b(shingle.encode('utf-8'), digest_size=8).digest()
            shingles.add(int.from_bytes(h, 'little'))

        return shingles

    def _hamming_distance(self, a: int, b: int) -> int:
        """
        Calculate Hamming distance between two simhash values.

        Args:
            a: First simhash value
            b: Second simhash value

        Returns:
            Number of differing bits
        """
        return bin(a ^ b).count('1')

    def find_exact_duplicates(
        self,
        fingerprints: List[DocumentFingerprint]
    ) -> List[List[str]]:
        """
        Group documents by MD5 hash to find exact duplicates.

        Args:
            fingerprints: List of document fingerprints

        Returns:
            List of groups, where each group contains file paths of exact duplicates
        """
        md5_groups: Dict[str, List[str]] = defaultdict(list)

        for fp in fingerprints:
            md5_groups[fp.md5].append(fp.file_path)

        # Return only groups with more than one file
        duplicate_groups = [
            group for group in md5_groups.values() if len(group) > 1
        ]

        logger.info(f"Found {len(duplicate_groups)} exact duplicate groups")
        return duplicate_groups

    def find_near_duplicates(
        self,
        fingerprints: List[DocumentFingerprint],
        threshold: float = 0.8
    ) -> List[Tuple[str, str, float]]:
        """
        Find pairs of documents with Jaccard similarity above threshold.

        Args:
            fingerprints: List of document fingerprints
            threshold: Minimum Jaccard similarity (0.0 to 1.0)

        Returns:
            List of (path1, path2, similarity) tuples
        """
        near_duplicates = []

        # Filter out fingerprints without shingles
        valid_fps = [fp for fp in fingerprints if fp.shingles is not None]

        # Pre-filter with simhash hamming distance (much faster than full Jaccard)
        # Hamming distance <= threshold_bits means potential near-duplicate
        threshold_bits = int(self.simhash_bits * (1 - threshold))

        for i in range(len(valid_fps)):
            fp1 = valid_fps[i]
            for j in range(i + 1, len(valid_fps)):
                fp2 = valid_fps[j]

                # Skip if already exact duplicates
                if fp1.md5 == fp2.md5:
                    continue

                # Fast pre-filter: check simhash distance first
                if self._hamming_distance(fp1.simhash, fp2.simhash) > threshold_bits + 5:
                    continue

                similarity = self.jaccard_similarity(fp1.shingles, fp2.shingles)

                if similarity >= threshold:
                    near_duplicates.append((fp1.file_path, fp2.file_path, similarity))

        logger.info(f"Found {len(near_duplicates)} near-duplicate pairs (threshold={threshold})")
        return near_duplicates

    def jaccard_similarity(self, a: Set[int], b: Set[int]) -> float:
        """
        Calculate Jaccard similarity between two sets.

        Args:
            a: First set
            b: Second set

        Returns:
            Jaccard similarity (intersection / union)
        """
        if not a and not b:
            return 1.0

        if not a or not b:
            return 0.0

        intersection = len(a & b)
        union = len(a | b)

        return intersection / union if union > 0 else 0.0


def deduplicate(
    fingerprints: List[DocumentFingerprint],
    near_threshold: float = 0.8
) -> DedupReport:
    """
    Perform deduplication analysis on a collection of fingerprints.

    Args:
        fingerprints: List of document fingerprints
        near_threshold: Threshold for near-duplicate detection

    Returns:
        DedupReport with analysis results
    """
    engine = FingerprintEngine()

    # Find exact duplicates
    exact_duplicate_groups = engine.find_exact_duplicates(fingerprints)

    # Find near duplicates
    near_duplicate_pairs = engine.find_near_duplicates(fingerprints, near_threshold)

    # Calculate unique files and wasted bytes
    md5_to_size: Dict[str, int] = {}
    md5_counts: Dict[str, int] = defaultdict(int)

    for fp in fingerprints:
        md5_to_size[fp.md5] = fp.size
        md5_counts[fp.md5] += 1

    unique_files = len(md5_to_size)

    # Wasted bytes = sum of (count - 1) * size for each duplicate group
    wasted_bytes = sum(
        (count - 1) * md5_to_size[md5]
        for md5, count in md5_counts.items()
        if count > 1
    )

    report = DedupReport(
        total_files=len(fingerprints),
        unique_files=unique_files,
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
