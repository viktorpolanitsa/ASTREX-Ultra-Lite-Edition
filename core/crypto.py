"""
Module for detecting encrypted/encoded content and crypto artifacts in files.
"""

import math
import os
import re
import struct
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Dict, Optional, Tuple

from .logging_setup import get_logger

logger = get_logger("astrex.crypto")


@dataclass
class CryptoDetection:
    """Results from crypto/encryption detection."""
    file_path: str
    is_encrypted: bool
    encryption_type: str  # none, gpg, openssl, zip_encrypted, office_encrypted, pdf_encrypted,
                          # rar_encrypted, 7z_encrypted, possible_container, private_key, unknown
    entropy: float  # Shannon entropy 0-8
    has_crypto_headers: bool
    crypto_artifacts: List[str] = field(default_factory=list)  # found crypto-related strings
    confidence: float = 0.0
    file_format: str = "unknown"


# Сигнатуры форматов, для которых высокая энтропия нормальна (сжатие)
_COMPRESSED_MAGIC: List[Tuple[bytes, int, str]] = [
    (b'PK\x03\x04', 0, 'zip'), (b'PK\x05\x06', 0, 'zip'),
    (b'\x1f\x8b', 0, 'gzip'), (b'BZh', 0, 'bzip2'), (b'\xfd7zXZ\x00', 0, 'xz'),
    (b'7z\xbc\xaf\x27\x1c', 0, '7z'), (b'Rar!\x1a\x07', 0, 'rar'),
    (b'\x28\xb5\x2f\xfd', 0, 'zstd'), (b'\x04\x22\x4d\x18', 0, 'lz4'),
    (b'MSCF', 0, 'cab'),
    (b'\x89PNG\r\n\x1a\n', 0, 'png'), (b'\xff\xd8\xff', 0, 'jpeg'),
    (b'GIF87a', 0, 'gif'), (b'GIF89a', 0, 'gif'), (b'RIFF', 0, 'riff'),
    (b'II*\x00', 0, 'tiff'), (b'MM\x00*', 0, 'tiff'), (b'BM', 0, 'bmp'),
    (b'ID3', 0, 'mp3'), (b'\xff\xfb', 0, 'mp3'), (b'\xff\xf3', 0, 'mp3'), (b'\xff\xf2', 0, 'mp3'),
    (b'OggS', 0, 'ogg'), (b'fLaC', 0, 'flac'), (b'ftyp', 4, 'mp4'),
    (b'\x1aE\xdf\xa3', 0, 'mkv'), (b'%PDF', 0, 'pdf'),
    (b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1', 0, 'ole'),
    (b'SQLite format 3\x00', 0, 'sqlite'),
    (b'\x00\x00\x01\xba', 0, 'mpeg'), (b'wOFF', 0, 'woff'), (b'wOF2', 0, 'woff2'),
]


def _identify_format(header: bytes) -> str:
    for magic, offset, name in _COMPRESSED_MAGIC:
        if header[offset:offset + len(magic)] == magic:
            return name
    return 'unknown'


_B58_ALPHABET = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
_BECH32_CHARSET = 'qpzry9x8gf2tvdw0s3jn54khce6mua7l'


def _valid_base58check(address: str) -> bool:
    """Проверка контрольной суммы Base58Check (адреса Bitcoin P2PKH/P2SH)."""
    import hashlib
    num = 0
    for ch in address:
        idx = _B58_ALPHABET.find(ch)
        if idx < 0:
            return False
        num = num * 58 + idx
    raw = num.to_bytes((num.bit_length() + 7) // 8, 'big') if num else b''
    raw = b'\x00' * (len(address) - len(address.lstrip('1'))) + raw
    if len(raw) != 25:
        return False
    payload, checksum = raw[:-4], raw[-4:]
    return hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4] == checksum


def _valid_bech32(address: str) -> bool:
    """Проверка контрольной суммы Bech32/Bech32m (адреса bc1...)."""
    address = address.lower()
    pos = address.rfind('1')
    if pos < 1 or pos + 7 > len(address):
        return False
    hrp, data = address[:pos], address[pos + 1:]
    try:
        values = [_BECH32_CHARSET.index(c) for c in data]
    except ValueError:
        return False
    generator = [0x3b6a57b2, 0x26508e6d, 0x1ea119fa, 0x3d4233dd, 0x2a1462b3]
    chk = 1
    for v in [ord(c) >> 5 for c in hrp] + [0] + [ord(c) & 31 for c in hrp] + values:
        top = chk >> 25
        chk = (chk & 0x1ffffff) << 5 ^ v
        for i in range(5):
            if (top >> i) & 1:
                chk ^= generator[i]
    return chk in (1, 0x2bc830a3)


class CryptoDetector:
    """Detector for encrypted files and crypto artifacts."""

    # Crypto-related patterns
    CRYPTO_PATTERNS = {
        'aes': re.compile(rb'AES[-_]?(128|192|256)', re.IGNORECASE),
        'rsa': re.compile(rb'RSA[-_]?(1024|2048|3072|4096)', re.IGNORECASE),
        'encryption': re.compile(rb'encrypt(ed|ion)?', re.IGNORECASE),
        'cipher': re.compile(rb'cipher', re.IGNORECASE),
    }

    # Text-based crypto patterns
    BASE64_PATTERN = re.compile(r'[A-Za-z0-9+/]{40,}={0,2}')
    HEX_PATTERN = re.compile(r'\b[0-9a-fA-F]{64,}\b')

    # PEM blocks
    PEM_BEGIN = re.compile(r'-----BEGIN ([A-Z0-9 ]+)-----')
    PEM_END = re.compile(r'-----END ([A-Z0-9 ]+)-----')

    # Cryptocurrency wallet addresses
    BITCOIN_PATTERN = re.compile(r'\b(?:[13][a-km-zA-HJ-NP-Z1-9]{25,34}|bc1[ac-hj-np-z02-9]{11,71})\b')
    ETHEREUM_PATTERN = re.compile(r'\b0x[0-9a-fA-F]{40}\b')

    # Password hashes (полный формат, а не просто "$1$")
    HASH_PATTERNS = {
        'bcrypt': re.compile(r'\$2[abxy]\$\d{2}\$[./A-Za-z0-9]{53}'),
        'argon2': re.compile(r'\$argon2(?:id|i|d)\$v=\d+\$m=\d+,t=\d+,p=\d+\$[A-Za-z0-9+/]+\$[A-Za-z0-9+/]+'),
        'sha256_crypt': re.compile(r'\$5\$(?:rounds=\d+\$)?[./A-Za-z0-9]{1,16}\$[./A-Za-z0-9]{43}'),
        'sha512_crypt': re.compile(r'\$6\$(?:rounds=\d+\$)?[./A-Za-z0-9]{1,16}\$[./A-Za-z0-9]{86}'),
        'md5_crypt': re.compile(r'\$1\$[./A-Za-z0-9]{1,8}\$[./A-Za-z0-9]{22}'),
    }

    # API key patterns
    API_KEY_PATTERNS = {
        'aws_access_key': re.compile(r'\bAKIA[0-9A-Z]{16}\b'),
        'aws_secret_key': re.compile(
            r'(?:aws_secret(?:_access)?_key|AWS_SECRET(?:_ACCESS)?_KEY|SecretAccessKey)'
            r'[\s"\'=:]+([A-Za-z0-9/+=]{40})\b', re.IGNORECASE),
        'github_token': re.compile(r'\bgh[pousr]_[A-Za-z0-9]{36,}\b'),
        'generic_api_key': re.compile(r'api[_-]?key["\s:=]+[A-Za-z0-9_\-]{20,}', re.IGNORECASE),
    }

    TEXT_EXTENSIONS = {
        '.txt', '.md', '.log', '.csv', '.json', '.xml', '.yaml', '.yml', '.ini',
        '.cfg', '.conf', '.env', '.pem', '.key', '.py', '.js', '.sh', '.ts', '.php',
        '.rb', '.go', '.java', '.cs', '.html', '.htm', '.sql',
    }

    def __init__(self):
        self.logger = logger

    # ── file detection ────────────────────────────────────────────────────────

    def detect_file(self, path: str, deep: bool = False) -> CryptoDetection:
        """
        Detect encryption and crypto artifacts in a file.

        Args:
            path: Path to the file to analyze
            deep: также искать в тексте ключи, PEM-блоки, кошельки, хеши

        Returns:
            CryptoDetection object with results
        """
        path_obj = Path(path)

        if not path_obj.is_file():
            self.logger.warning(f"Not a file: {path}")
            return CryptoDetection(file_path=path, is_encrypted=False, encryption_type="none",
                                   entropy=0.0, has_crypto_headers=False)

        try:
            size = path_obj.stat().st_size
            with open(path, 'rb') as f:
                header = f.read(65536)
                tail = b''
                if size > 65536:
                    f.seek(max(0, size - 65536))
                    tail = f.read(65536)

            file_format = _identify_format(header)
            entropy = self.calculate_entropy(header)
            artifacts: List[str] = []
            encryption_type = "none"
            has_headers = False

            magic_type = self._check_magic_bytes(header)
            if magic_type:
                encryption_type, has_headers = magic_type, True

            for name, pattern in self.CRYPTO_PATTERNS.items():
                if pattern.search(header[:8192]):
                    artifacts.append(f"pattern_{name}")

            fmt_type = self._check_format_encryption(path_obj, file_format, header, tail, artifacts)
            if fmt_type:
                encryption_type, has_headers = fmt_type, True

            if deep and (path_obj.suffix.lower() in self.TEXT_EXTENSIONS or file_format == 'unknown'):
                text = header.decode('utf-8', errors='ignore')
                text_det = self.detect_text(text, path)
                artifacts.extend(a for a in text_det.crypto_artifacts if a not in artifacts)
                if encryption_type == "none" and text_det.encryption_type in ("private_key", "gpg"):
                    encryption_type = text_det.encryption_type
                    has_headers = True

            # Высокая энтропия без известного формата → возможный зашифрованный контейнер
            if encryption_type == "none" and file_format == 'unknown' and size >= 64 * 1024:
                if entropy > 7.9 and self._uniform_entropy(path, size):
                    encryption_type = "possible_container"
                    artifacts.append("high_entropy_no_format")

            is_encrypted = self._determine_encryption(entropy, has_headers, encryption_type,
                                                      artifacts, file_format)
            confidence = self._calculate_confidence(entropy, has_headers, len(artifacts),
                                                    encryption_type, file_format)

            return CryptoDetection(
                file_path=path,
                is_encrypted=is_encrypted,
                encryption_type=encryption_type,
                entropy=entropy,
                has_crypto_headers=has_headers,
                crypto_artifacts=artifacts,
                confidence=confidence,
                file_format=file_format,
            )

        except Exception as e:
            self.logger.error(f"Error detecting crypto in {path}: {e}")
            return CryptoDetection(file_path=path, is_encrypted=False, encryption_type="none",
                                   entropy=0.0, has_crypto_headers=False,
                                   crypto_artifacts=[f"error: {e}"])

    def _uniform_entropy(self, path: str, size: int, samples: int = 4) -> bool:
        """Высокая энтропия во всех частях файла (признак шифрования, а не сжатия)."""
        try:
            with open(path, 'rb') as f:
                for i in range(samples):
                    f.seek(int(size * i / samples))
                    block = f.read(65536)
                    if block and self.calculate_entropy(block) < 7.9:
                        return False
            return True
        except OSError:
            return False

    def _check_magic_bytes(self, data: bytes) -> Optional[str]:
        """Сигнатуры заведомо зашифрованных форматов."""
        if not data:
            return None
        # OpenPGP: старый формат пакета (бит 7 = 1, бит 6 = 0) с тегами
        # 1 (PKESK) и 3 (SKESK), или новый формат 0xC1 / 0xC3
        if self._looks_openpgp_encrypted(data):
            return "gpg"
        if data.startswith(b'-----BEGIN PGP MESSAGE'):
            return "gpg"
        if data.startswith(b'Salted__'):
            return "openssl"
        if data.startswith(b'LUKS\xba\xbe'):
            return "luks"
        if data[:4] == b'\x00\x00\x00\x00' and b'BitLocker' in data[:512]:
            return "bitlocker"
        if data[3:11] == b'-FVE-FS-':
            return "bitlocker"
        return None

    @staticmethod
    def _looks_openpgp_encrypted(data: bytes) -> bool:
        """Первый пакет OpenPGP — зашифрованный сеансовый ключ (тег 1 или 3)
        с допустимым номером версии (проверка версии отсекает случайные байты)."""
        if len(data) < 8:
            return False
        first = data[0]
        if first & 0xC0 == 0x80:                      # старый формат пакета
            tag = (first >> 2) & 0x0F
            length_type = first & 0x03
            offset = 1 + {0: 1, 1: 2, 2: 4, 3: 0}[length_type]
        elif first & 0xC0 == 0xC0:                    # новый формат пакета
            tag = first & 0x3F
            l0 = data[1]
            if l0 < 192:
                offset = 2
            elif l0 < 224:
                offset = 3
            elif l0 == 255:
                offset = 6
            else:
                return False
        else:
            return False
        if offset >= len(data):
            return False
        version = data[offset]
        if tag == 1:
            return version in (3, 6)
        if tag == 3:
            return version in (4, 5, 6)
        return False

    def _check_format_encryption(self, path: Path, fmt: str, header: bytes, tail: bytes,
                                 artifacts: List[str]) -> Optional[str]:
        """Проверка флагов шифрования внутри известных форматов."""
        if fmt == 'zip':
            try:
                with zipfile.ZipFile(path) as zf:
                    names = zf.namelist()
                    if any(info.flag_bits & 0x1 for info in zf.infolist()):
                        artifacts.append("zip_encryption_flag")
                        return "zip_encrypted"
                    if 'EncryptedPackage' in names:
                        return "office_encrypted"
            except (zipfile.BadZipFile, OSError, RuntimeError):
                if len(header) >= 8 and struct.unpack('<H', header[6:8])[0] & 0x1:
                    artifacts.append("zip_encryption_flag")
                    return "zip_encrypted"
            return None

        if fmt == 'pdf':
            if b'/Encrypt' in header or b'/Encrypt' in tail:
                artifacts.append("pdf_encrypt_dict")
                return "pdf_encrypted"
            return None

        if fmt == 'ole':
            return self._check_ole_encryption(path, header, artifacts)

        if fmt == 'rar':
            try:
                import rarfile
                with rarfile.RarFile(str(path)) as rf:
                    if rf.needs_password():
                        artifacts.append("rar_password")
                        return "rar_encrypted"
            except Exception:
                pass
            return None

        if fmt == '7z':
            try:
                import py7zr
                with py7zr.SevenZipFile(str(path), 'r') as zf:
                    if zf.needs_password():
                        artifacts.append("7z_password")
                        return "7z_encrypted"
            except Exception as e:
                if 'password' in str(e).lower():
                    artifacts.append("7z_password")
                    return "7z_encrypted"
            return None

        return None

    def _check_office_encryption(self, data: bytes) -> bool:
        """Совместимость: грубая проверка по заголовку (имена потоков OLE в UTF-16LE)."""
        markers = ('EncryptedPackage'.encode('utf-16-le'), 'EncryptionInfo'.encode('utf-16-le'))
        return data.startswith(b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1') and any(m in data for m in markers)

    def _check_ole_encryption(self, path: Path, header: bytes, artifacts: List[str]) -> Optional[str]:
        try:
            import olefile
        except ImportError:
            if self._check_office_encryption(header):
                artifacts.append("ole_encrypted_package")
                return "office_encrypted"
            return None
        try:
            with olefile.OleFileIO(str(path)) as ole:
                if ole.exists('EncryptedPackage') or ole.exists('EncryptionInfo'):
                    artifacts.append("ole_encrypted_package")
                    return "office_encrypted"
                # Word 97-2003: бит fEncrypted (0x0100) в FIB
                if ole.exists('WordDocument'):
                    fib = ole.openstream('WordDocument').read(12)
                    if len(fib) >= 12 and struct.unpack('<H', fib[10:12])[0] & 0x0100:
                        artifacts.append("doc_fib_encrypted")
                        return "office_encrypted"
                # Excel 97-2003: запись FILEPASS (0x002F) в начале потока Workbook
                for stream in ('Workbook', 'Book'):
                    if ole.exists(stream):
                        data = ole.openstream(stream).read(4096)
                        pos = 0
                        while pos + 4 <= len(data):
                            rtype, rlen = struct.unpack('<HH', data[pos:pos + 4])
                            if rtype == 0x002F:
                                artifacts.append("xls_filepass")
                                return "office_encrypted"
                            if rtype == 0x000A:  # EOF первого BOF-блока
                                break
                            pos += 4 + rlen
                        break
        except Exception:
            pass
        return None

    # ── text detection ────────────────────────────────────────────────────────

    def detect_text(self, text: str, path: str = "") -> CryptoDetection:
        """Detect crypto artifacts in text content."""
        crypto_artifacts: List[str] = []
        encryption_type = "none"
        has_crypto_headers = False

        base64_matches = self.BASE64_PATTERN.findall(text)
        if base64_matches:
            crypto_artifacts.append(f"base64_blocks:{len(base64_matches)}")

        hex_matches = self.HEX_PATTERN.findall(text)
        if hex_matches:
            crypto_artifacts.append(f"hex_blocks:{len(hex_matches)}")

        for pem_type in sorted(set(self.PEM_BEGIN.findall(text))):
            crypto_artifacts.append(f"pem_{pem_type.lower().replace(' ', '_')}")
            has_crypto_headers = True
            if 'PRIVATE KEY' in pem_type:
                encryption_type = "private_key"
            elif 'PGP' in pem_type and encryption_type == "none":
                encryption_type = "gpg"
            elif 'CERTIFICATE' in pem_type and encryption_type == "none":
                encryption_type = "certificate"

        for wallet_type in sorted({w['type'] for w in self.scan_for_crypto_wallets(text)}):
            crypto_artifacts.append(f"wallet_{wallet_type}")

        for hash_type, pattern in self.HASH_PATTERNS.items():
            if pattern.search(text):
                crypto_artifacts.append(f"hash_{hash_type}")

        for key_type in sorted({k['type'] for k in self.scan_for_keys(text)}):
            if not key_type.startswith('pem_'):
                crypto_artifacts.append(f"key_{key_type}")

        entropy = self.calculate_entropy(text.encode('utf-8', errors='ignore'))

        is_encrypted = (
            encryption_type in ("gpg", "private_key") or
            (entropy > 7.5 and len(base64_matches) > 5) or
            (len(hex_matches) > 3 and entropy > 7.0)
        )

        confidence = self._calculate_confidence(entropy, has_crypto_headers,
                                                len(crypto_artifacts), encryption_type, 'text')

        return CryptoDetection(
            file_path=path,
            is_encrypted=is_encrypted,
            encryption_type=encryption_type if encryption_type != "none" else ("unknown" if is_encrypted else "none"),
            entropy=entropy,
            has_crypto_headers=has_crypto_headers,
            crypto_artifacts=crypto_artifacts,
            confidence=confidence,
            file_format='text',
        )

    def calculate_entropy(self, data: bytes) -> float:
        """Shannon entropy of data (0-8 bits)."""
        if not data:
            return 0.0
        data_len = len(data)
        entropy = 0.0
        for count in Counter(data).values():
            probability = count / data_len
            entropy -= probability * math.log2(probability)
        return entropy

    def scan_for_crypto_wallets(self, text: str) -> List[Dict[str, str]]:
        """Scan text for cryptocurrency wallet addresses (Bitcoin — с проверкой контрольной суммы)."""
        wallets = []
        for address in self.BITCOIN_PATTERN.findall(text):
            valid = _valid_bech32(address) if address.lower().startswith('bc1') else _valid_base58check(address)
            if valid:
                wallets.append({"type": "bitcoin", "address": address})
        wallets += [{"type": "ethereum", "address": a} for a in self.ETHEREUM_PATTERN.findall(text)]
        return wallets

    def scan_for_keys(self, text: str) -> List[Dict[str, str]]:
        """Scan text for API keys, private keys, etc."""
        keys = []

        for match in self.PEM_BEGIN.finditer(text):
            key_type = match.group(1)
            end_pattern = f"-----END {key_type}-----"
            end_pos = text.find(end_pattern, match.start())
            if end_pos != -1:
                key_value = text[match.start():end_pos + len(end_pattern)]
                keys.append({
                    "type": f"pem_{key_type.lower().replace(' ', '_')}",
                    "value": key_value[:100] + "...(truncated)"
                })

        for key_type, pattern in self.API_KEY_PATTERNS.items():
            for match in pattern.finditer(text):
                key_value = match.group(match.lastindex) if match.lastindex else match.group(0)
                keys.append({
                    "type": key_type,
                    "value": key_value[:20] + "...(truncated)" if len(key_value) > 20 else key_value
                })

        return keys

    # ── scoring ───────────────────────────────────────────────────────────────

    def _determine_encryption(self, entropy: float, has_crypto_headers: bool, encryption_type: str,
                              crypto_artifacts: List[str], file_format: str = 'unknown') -> bool:
        """Determine if file is likely encrypted."""
        if encryption_type not in ("none", "unknown", "certificate"):
            return True
        if has_crypto_headers:
            return True
        # Для сжатых/медиа форматов высокая энтропия — норма, не шифрование
        if file_format != 'unknown':
            return False
        if len(crypto_artifacts) >= 3 and entropy > 7.0:
            return True
        return False

    def _calculate_confidence(self, entropy: float, has_crypto_headers: bool, artifact_count: int,
                              encryption_type: str, file_format: str = 'unknown') -> float:
        """Confidence score for encryption detection (0.0 - 1.0)."""
        if encryption_type == "possible_container":
            return 0.5
        confidence = 0.0
        if encryption_type not in ("none", "unknown", "certificate"):
            confidence += 0.6
        if has_crypto_headers:
            confidence += 0.3
        if file_format in ('unknown', 'text'):
            if entropy > 7.8:
                confidence += 0.2
            elif entropy > 7.5:
                confidence += 0.1
        if artifact_count >= 5:
            confidence += 0.15
        elif artifact_count >= 1:
            confidence += 0.05
        return min(confidence, 1.0)


def scan_directory_crypto(folder: str, sample_size: int = 100, deep: bool = False) -> List[CryptoDetection]:
    """
    Scan a directory for encrypted files and crypto artifacts.

    Args:
        folder: Directory path to scan
        sample_size: Maximum number of files to scan (first N in sorted walk order)
        deep: also search text files for keys, wallets, password hashes

    Returns:
        List of CryptoDetection results
    """
    detector = CryptoDetector()
    results: List[CryptoDetection] = []

    folder_path = Path(folder)
    if not folder_path.is_dir():
        logger.error(f"Invalid directory: {folder}")
        return results

    files: List[str] = []
    for root, dirs, filenames in os.walk(folder):
        dirs.sort()
        for filename in sorted(filenames):
            files.append(os.path.join(root, filename))
            if len(files) >= sample_size:
                break
        if len(files) >= sample_size:
            break

    for file_path in files:
        try:
            results.append(detector.detect_file(file_path, deep=deep))
        except Exception as e:
            logger.error(f"Error scanning {file_path}: {e}")

    return results


__all__ = [
    'CryptoDetection',
    'CryptoDetector',
    'scan_directory_crypto',
]
