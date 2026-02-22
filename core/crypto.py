"""
Module for detecting encrypted/encoded content and crypto artifacts in files.
"""

import re
import os
import math
import struct
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field
from pathlib import Path
from .logging_setup import get_logger

logger = get_logger(__name__)


@dataclass
class CryptoDetection:
    """Results from crypto/encryption detection."""
    file_path: str
    is_encrypted: bool
    encryption_type: str  # "none", "aes", "gpg", "zip_encrypted", "office_encrypted", "unknown"
    entropy: float  # Shannon entropy 0-8
    has_crypto_headers: bool
    crypto_artifacts: List[str] = field(default_factory=list)  # found crypto-related strings
    confidence: float = 0.0


class CryptoDetector:
    """Detector for encrypted files and crypto artifacts."""

    # Known magic bytes for encrypted formats
    MAGIC_BYTES = {
        'pgp_binary': b'\x85\x02',
        'pgp_ascii': b'-----BEGIN PGP',
        'openssl': b'Salted__',
        'truecrypt': b'TRUE',
        'veracrypt': b'VERA',
        '7z': b'7z\xbc\xaf\x27\x1c',
    }

    # Crypto-related patterns
    CRYPTO_PATTERNS = {
        'aes': re.compile(rb'AES[-_]?(128|192|256)', re.IGNORECASE),
        'rsa': re.compile(rb'RSA[-_]?(1024|2048|4096)', re.IGNORECASE),
        'encryption': re.compile(rb'encrypt(ed|ion)?', re.IGNORECASE),
        'cipher': re.compile(rb'cipher', re.IGNORECASE),
    }

    # Text-based crypto patterns
    BASE64_PATTERN = re.compile(r'[A-Za-z0-9+/]{40,}={0,2}')
    HEX_PATTERN = re.compile(r'\b[0-9a-fA-F]{64,}\b')

    # PEM blocks
    PEM_BEGIN = re.compile(r'-----BEGIN ([A-Z ]+)-----')
    PEM_END = re.compile(r'-----END ([A-Z ]+)-----')

    # Cryptocurrency wallet addresses
    BITCOIN_PATTERN = re.compile(r'\b[13][a-km-zA-HJ-NP-Z1-9]{25,34}\b')
    ETHEREUM_PATTERN = re.compile(r'\b0x[0-9a-fA-F]{40}\b')

    # Password hashes
    HASH_PATTERNS = {
        'bcrypt': re.compile(r'\$2[aby]\$\d{2}\$[./A-Za-z0-9]{53}'),
        'argon2': re.compile(r'\$argon2[id]{0,2}\$'),
        'sha256_crypt': re.compile(r'\$5\$'),
        'sha512_crypt': re.compile(r'\$6\$'),
        'md5_crypt': re.compile(r'\$1\$'),
    }

    # API key patterns
    # aws_secret_key requires a context keyword to avoid matching any 40-char base64 string
    API_KEY_PATTERNS = {
        'aws_access_key': re.compile(r'AKIA[0-9A-Z]{16}'),
        'aws_secret_key': re.compile(
            r'(?:aws_secret(?:_access)?_key|AWS_SECRET(?:_ACCESS)?_KEY|SecretAccessKey)'
            r'[\s"\'=:]+([A-Za-z0-9/+=]{40})\b', re.IGNORECASE),
        'github_token': re.compile(r'gh[ps]_[A-Za-z0-9]{36,}'),
        'generic_api_key': re.compile(r'api[_-]?key["\s:=]+[A-Za-z0-9_\-]{20,}', re.IGNORECASE),
    }

    def __init__(self):
        self.logger = logger

    def detect_file(self, path: str) -> CryptoDetection:
        """
        Detect encryption and crypto artifacts in a file.

        Args:
            path: Path to the file to analyze

        Returns:
            CryptoDetection object with results
        """
        path_obj = Path(path)

        if not path_obj.exists():
            self.logger.warning(f"File not found: {path}")
            return CryptoDetection(
                file_path=path,
                is_encrypted=False,
                encryption_type="none",
                entropy=0.0,
                has_crypto_headers=False,
                crypto_artifacts=[],
                confidence=0.0
            )

        if not path_obj.is_file():
            self.logger.warning(f"Not a file: {path}")
            return CryptoDetection(
                file_path=path,
                is_encrypted=False,
                encryption_type="none",
                entropy=0.0,
                has_crypto_headers=False,
                crypto_artifacts=[],
                confidence=0.0
            )

        try:
            # Read first 8KB for header analysis
            with open(path, 'rb') as f:
                header = f.read(8192)

                # Read up to 64KB for entropy calculation
                f.seek(0)
                entropy_data = f.read(65536)

            # Check magic bytes
            encryption_type, has_crypto_headers = self._check_magic_bytes(header)
            crypto_artifacts = []

            # Calculate entropy
            entropy = self.calculate_entropy(entropy_data)

            # Check for crypto patterns in header
            for name, pattern in self.CRYPTO_PATTERNS.items():
                if pattern.search(header):
                    crypto_artifacts.append(f"pattern_{name}")

            # Check for PDF encryption
            if header.startswith(b'%PDF') and b'/Encrypt' in header:
                encryption_type = "pdf_encrypted"
                has_crypto_headers = True
                crypto_artifacts.append("pdf_encrypt_dict")

            # Check for ZIP encryption
            zip_encrypted = self._check_zip_encryption(header)
            if zip_encrypted:
                encryption_type = "zip_encrypted"
                has_crypto_headers = True
                crypto_artifacts.append("zip_encryption_flag")

            # Check for Office encryption (OLE format)
            office_encrypted = self._check_office_encryption(header)
            if office_encrypted:
                encryption_type = "office_encrypted"
                has_crypto_headers = True
                crypto_artifacts.append("ole_encrypted_package")

            # Determine if encrypted based on entropy and artifacts
            is_encrypted = self._determine_encryption(
                entropy, has_crypto_headers, encryption_type, crypto_artifacts
            )

            # Calculate confidence
            confidence = self._calculate_confidence(
                entropy, has_crypto_headers, len(crypto_artifacts), encryption_type
            )

            return CryptoDetection(
                file_path=path,
                is_encrypted=is_encrypted,
                encryption_type=encryption_type,
                entropy=entropy,
                has_crypto_headers=has_crypto_headers,
                crypto_artifacts=crypto_artifacts,
                confidence=confidence
            )

        except Exception as e:
            self.logger.error(f"Error detecting crypto in {path}: {e}")
            return CryptoDetection(
                file_path=path,
                is_encrypted=False,
                encryption_type="none",
                entropy=0.0,
                has_crypto_headers=False,
                crypto_artifacts=[f"error: {str(e)}"],
                confidence=0.0
            )

    def detect_text(self, text: str, path: str = "") -> CryptoDetection:
        """
        Detect crypto artifacts in text content.

        Args:
            text: Text content to analyze
            path: Optional file path for reference

        Returns:
            CryptoDetection object with results
        """
        crypto_artifacts = []
        encryption_type = "none"
        has_crypto_headers = False

        # Look for Base64 encoded blocks
        base64_matches = self.BASE64_PATTERN.findall(text)
        if base64_matches:
            crypto_artifacts.append(f"base64_blocks:{len(base64_matches)}")

        # Look for hex encoded blocks
        hex_matches = self.HEX_PATTERN.findall(text)
        if hex_matches:
            crypto_artifacts.append(f"hex_blocks:{len(hex_matches)}")

        # Look for PEM blocks
        pem_types = self.PEM_BEGIN.findall(text)
        if pem_types:
            for pem_type in set(pem_types):
                crypto_artifacts.append(f"pem_{pem_type.lower().replace(' ', '_')}")
                has_crypto_headers = True
                if 'PRIVATE KEY' in pem_type:
                    encryption_type = "private_key"
                elif 'CERTIFICATE' in pem_type:
                    encryption_type = "certificate"
                elif 'PGP' in pem_type:
                    encryption_type = "gpg"

        # Look for cryptocurrency wallets
        wallets = self.scan_for_crypto_wallets(text)
        if wallets:
            for wallet in wallets:
                crypto_artifacts.append(f"wallet_{wallet['type']}")

        # Look for password hashes
        for hash_type, pattern in self.HASH_PATTERNS.items():
            if pattern.search(text):
                crypto_artifacts.append(f"hash_{hash_type}")

        # Look for API keys
        keys = self.scan_for_keys(text)
        if keys:
            for key in keys:
                crypto_artifacts.append(f"key_{key['type']}")

        # Calculate entropy from text (as bytes)
        text_bytes = text.encode('utf-8', errors='ignore')
        entropy = self.calculate_entropy(text_bytes)

        # Determine encryption status
        is_encrypted = (
            encryption_type in ["gpg", "private_key"] or
            (entropy > 7.5 and len(base64_matches) > 5) or
            (len(hex_matches) > 3 and entropy > 7.0)
        )

        # Calculate confidence
        confidence = self._calculate_confidence(
            entropy, has_crypto_headers, len(crypto_artifacts), encryption_type
        )

        return CryptoDetection(
            file_path=path,
            is_encrypted=is_encrypted,
            encryption_type=encryption_type if encryption_type != "none" else "unknown" if is_encrypted else "none",
            entropy=entropy,
            has_crypto_headers=has_crypto_headers,
            crypto_artifacts=crypto_artifacts,
            confidence=confidence
        )

    def calculate_entropy(self, data: bytes) -> float:
        """
        Calculate Shannon entropy of data.

        Args:
            data: Bytes to analyze

        Returns:
            Entropy value (0-8 bits)
        """
        if not data:
            return 0.0

        # Count byte frequencies
        byte_counts = [0] * 256
        for byte in data:
            byte_counts[byte] += 1

        # Calculate entropy
        entropy = 0.0
        data_len = len(data)

        for count in byte_counts:
            if count > 0:
                probability = count / data_len
                entropy -= probability * math.log2(probability)

        return entropy

    def scan_for_crypto_wallets(self, text: str) -> List[Dict[str, str]]:
        """
        Scan text for cryptocurrency wallet addresses.

        Args:
            text: Text to scan

        Returns:
            List of found wallets with type and address
        """
        wallets = []

        # Bitcoin addresses
        btc_matches = self.BITCOIN_PATTERN.findall(text)
        for address in btc_matches:
            wallets.append({
                "type": "bitcoin",
                "address": address
            })

        # Ethereum addresses
        eth_matches = self.ETHEREUM_PATTERN.findall(text)
        for address in eth_matches:
            wallets.append({
                "type": "ethereum",
                "address": address
            })

        return wallets

    def scan_for_keys(self, text: str) -> List[Dict[str, str]]:
        """
        Scan text for API keys, private keys, etc.

        Args:
            text: Text to scan

        Returns:
            List of found keys with type and truncated value
        """
        keys = []

        # Check for PEM blocks first
        pem_matches = self.PEM_BEGIN.finditer(text)
        for match in pem_matches:
            key_type = match.group(1)
            start_pos = match.start()
            end_pattern = f"-----END {key_type}-----"
            end_pos = text.find(end_pattern, start_pos)

            if end_pos != -1:
                key_value = text[start_pos:end_pos + len(end_pattern)]
                keys.append({
                    "type": f"pem_{key_type.lower().replace(' ', '_')}",
                    "value": key_value[:100] + "...(truncated)"
                })

        # Check for API keys
        for key_type, pattern in self.API_KEY_PATTERNS.items():
            matches = pattern.finditer(text)
            for match in matches:
                # Use last capturing group if present (e.g. aws_secret_key has prefix context)
                key_value = match.group(match.lastindex) if match.lastindex else match.group(0)
                keys.append({
                    "type": key_type,
                    "value": key_value[:20] + "...(truncated)" if len(key_value) > 20 else key_value
                })

        return keys

    def _check_magic_bytes(self, data: bytes) -> Tuple[str, bool]:
        """
        Check for known encryption magic bytes.

        Returns:
            Tuple of (encryption_type, has_crypto_headers)
        """
        if not data:
            return "none", False

        # Check PGP/GPG
        if data.startswith(self.MAGIC_BYTES['pgp_binary']):
            return "gpg", True
        if data.startswith(self.MAGIC_BYTES['pgp_ascii']):
            return "gpg", True

        # Check OpenSSL
        if b'Salted__' in data[:16]:
            return "openssl", True

        # Check TrueCrypt
        if data[:4] == self.MAGIC_BYTES['truecrypt']:
            return "truecrypt", True

        # Check VeraCrypt
        if data[:4] == self.MAGIC_BYTES['veracrypt']:
            return "veracrypt", True

        # Check 7z
        if data.startswith(self.MAGIC_BYTES['7z']):
            # 7z can be encrypted, need to check further
            return "7z", False

        return "none", False

    def _check_zip_encryption(self, data: bytes) -> bool:
        """
        Check if ZIP file is encrypted.

        Returns:
            True if encrypted ZIP detected
        """
        if not data.startswith(b'PK\x03\x04'):
            return False

        try:
            # Local file header starts at offset 6 (general purpose bit flag)
            if len(data) < 10:
                return False

            # Bit 0 of general purpose flag indicates encryption
            flag = struct.unpack('<H', data[6:8])[0]
            return (flag & 0x0001) != 0

        except Exception:
            return False

    def _check_office_encryption(self, data: bytes) -> bool:
        """
        Check if Office document is encrypted.

        Returns:
            True if encrypted Office document detected
        """
        # Check for OLE2 signature
        if data.startswith(b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'):
            # Look for EncryptedPackage or EncryptionInfo
            if b'EncryptedPackage' in data or b'EncryptionInfo' in data:
                return True

        # Check for newer Office formats (ZIP-based)
        if data.startswith(b'PK\x03\x04'):
            if b'EncryptedPackage' in data or b'EncryptionInfo' in data:
                return True

        return False

    def _determine_encryption(
        self,
        entropy: float,
        has_crypto_headers: bool,
        encryption_type: str,
        crypto_artifacts: List[str]
    ) -> bool:
        """
        Determine if file is likely encrypted.

        Returns:
            True if file appears to be encrypted
        """
        # Known encrypted format
        if encryption_type not in ["none", "unknown"]:
            return True

        # Has crypto headers
        if has_crypto_headers:
            return True

        # High entropy with no known format suggests encryption
        if entropy > 7.5:
            return True

        # Multiple crypto artifacts and high-ish entropy
        if len(crypto_artifacts) >= 3 and entropy > 7.0:
            return True

        return False

    def _calculate_confidence(
        self,
        entropy: float,
        has_crypto_headers: bool,
        artifact_count: int,
        encryption_type: str
    ) -> float:
        """
        Calculate confidence score for encryption detection.

        Returns:
            Confidence score (0.0 - 1.0)
        """
        confidence = 0.0

        # Known encryption type adds high confidence
        if encryption_type not in ["none", "unknown"]:
            confidence += 0.5

        # Crypto headers add confidence
        if has_crypto_headers:
            confidence += 0.3

        # Entropy contribution
        if entropy > 7.8:
            confidence += 0.3
        elif entropy > 7.5:
            confidence += 0.2
        elif entropy > 7.0:
            confidence += 0.1

        # Artifacts add confidence
        if artifact_count >= 5:
            confidence += 0.2
        elif artifact_count >= 3:
            confidence += 0.15
        elif artifact_count >= 1:
            confidence += 0.1

        return min(confidence, 1.0)


def scan_directory_crypto(folder: str, sample_size: int = 100) -> List[CryptoDetection]:
    """
    Scan a directory for encrypted files and crypto artifacts.

    Args:
        folder: Directory path to scan
        sample_size: Maximum number of files to scan

    Returns:
        List of CryptoDetection results
    """
    detector = CryptoDetector()
    results = []

    folder_path = Path(folder)
    if not folder_path.exists() or not folder_path.is_dir():
        logger.error(f"Invalid directory: {folder}")
        return results

    # Collect files
    files = []
    try:
        for root, _, filenames in os.walk(folder):
            for filename in filenames:
                file_path = os.path.join(root, filename)
                files.append(file_path)

                if len(files) >= sample_size:
                    break

            if len(files) >= sample_size:
                break
    except Exception as e:
        logger.error(f"Error walking directory {folder}: {e}")
        return results

    # Scan files
    for file_path in files[:sample_size]:
        try:
            detection = detector.detect_file(file_path)
            results.append(detection)
        except Exception as e:
            logger.error(f"Error scanning {file_path}: {e}")

    return results


__all__ = [
    'CryptoDetection',
    'CryptoDetector',
    'scan_directory_crypto',
]
