#!/usr/bin/env python3
"""
ASTREX v3.0 — Image OCR Extractor
Tesseract OCR для извлечения текста из изображений
"""

import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, List

from .base import BaseExtractor, ExtractionResult, registry, worker_temp_dir
from core.config import ENGINE_CONFIG

logger = logging.getLogger("astrex.images")

_IMAGE_EXTS = ['.png', '.jpg', '.jpeg', '.tiff', '.tif', '.bmp', '.gif', '.webp']


class _Tesseract:
    """Поиск бинарника tesseract и установленных языков (однократно)."""
    path: Optional[str] = None
    languages: Optional[List[str]] = None
    checked = False

    @classmethod
    def detect(cls) -> bool:
        if cls.checked:
            return cls.path is not None
        cls.checked = True
        if not ENGINE_CONFIG.ocr_enabled:
            return False
        candidates = [shutil.which('tesseract'), '/usr/bin/tesseract', '/usr/local/bin/tesseract']
        for candidate in candidates:
            if not candidate or not os.path.exists(candidate):
                continue
            try:
                result = subprocess.run([candidate, '--version'], capture_output=True, text=True, timeout=10)
                if result.returncode == 0:
                    cls.path = candidate
                    break
            except (OSError, subprocess.SubprocessError):
                continue
        if cls.path:
            try:
                result = subprocess.run([cls.path, '--list-langs'], capture_output=True, text=True, timeout=10)
                out = (result.stdout or '') + (result.stderr or '')
                cls.languages = [line.strip() for line in out.splitlines()[1:] if line.strip()]
            except (OSError, subprocess.SubprocessError):
                cls.languages = []
        return cls.path is not None

    @classmethod
    def lang_arg(cls) -> str:
        """Запрошенные языки, которые реально установлены (иначе eng)."""
        wanted = [l for l in ENGINE_CONFIG.ocr_languages.split('+') if l]
        if cls.languages:
            present = [l for l in wanted if l in cls.languages]
            if not present:
                present = ['eng'] if 'eng' in cls.languages else cls.languages[:1]
            if len(present) < len(wanted):
                logger.debug(f"OCR languages missing: {set(wanted) - set(present)}")
            return '+'.join(present)
        return '+'.join(wanted) or 'eng'


def _run_tesseract(image_path: str, psm: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [_Tesseract.path, image_path, 'stdout', '-l', _Tesseract.lang_arg(), '--psm', psm, '--oem', '3'],
        capture_output=True, text=True, timeout=ENGINE_CONFIG.ocr_timeout,
    )


def _prepare_image(path: Path, grayscale: bool, enhance: bool) -> Optional[str]:
    """Подготовить изображение для OCR (RGB/L, уменьшение, контраст). None — без Pillow."""
    try:
        from PIL import Image, ImageEnhance, ImageFilter, ImageOps
    except ImportError:
        return None

    Image.MAX_IMAGE_PIXELS = 200_000_000  # защита от "декомпрессионных бомб"
    with Image.open(path) as src:
        src.seek(0)
        img = src.convert('L' if grayscale else 'RGB')
    try:
        max_dim = 4000
        if max(img.size) > max_dim:
            ratio = max_dim / max(img.size)
            img = img.resize((max(1, int(img.size[0] * ratio)), max(1, int(img.size[1] * ratio))),
                             Image.Resampling.LANCZOS)
        if grayscale:
            img = ImageOps.autocontrast(img)
        if enhance:
            img = ImageEnhance.Contrast(img).enhance(1.5).filter(ImageFilter.SHARPEN)
        fd, tmp_name = tempfile.mkstemp(prefix="astrex-ocr-", suffix=".png", dir=str(worker_temp_dir()))
        os.close(fd)
        img.save(tmp_name, 'PNG')
        return tmp_name
    finally:
        img.close()


def _ocr(path: Path, psm: str, grayscale: bool, enhance: bool) -> ExtractionResult:
    if not _Tesseract.detect():
        return ExtractionResult(error="Tesseract OCR not available")

    file_size = path.stat().st_size
    if file_size > 50 * 1024 * 1024:
        return ExtractionResult(error="Image too large for OCR")

    tmp_name = None
    try:
        try:
            tmp_name = _prepare_image(path, grayscale, enhance)
        except Exception as e:
            logger.debug(f"Image preprocessing failed for {path}: {e}")
            tmp_name = None
        try:
            result = _run_tesseract(tmp_name or str(path), psm)
        except subprocess.TimeoutExpired:
            return ExtractionResult(error=f"OCR timeout ({ENGINE_CONFIG.ocr_timeout}s)")
        if result.returncode != 0:
            return ExtractionResult(error=f"Tesseract error: {(result.stderr or '').strip()[:500]}")
        metadata = ImageOCRExtractor._get_image_metadata(path)
        metadata['ocr_languages'] = _Tesseract.lang_arg()
        text = (result.stdout or '').strip()
        if not text:
            metadata['ocr_empty'] = True
        # EXIF (камера, программа, GPS) тоже доступен для поиска
        exif_text = ' '.join(f"{k[5:]}: {v}" for k, v in metadata.items() if k.startswith('exif_'))
        if exif_text:
            text = (text + '\n\n' + exif_text).strip()
        return ExtractionResult(text=text, metadata=metadata)
    except Exception as e:
        return ExtractionResult(error=f"OCR failed: {e}")
    finally:
        if tmp_name:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass


def _gps_to_decimal(values, ref) -> Optional[float]:
    try:
        d, m, s = (float(v) for v in values)
        result = d + m / 60 + s / 3600
        if str(ref).upper() in ('S', 'W'):
            result = -result
        return round(result, 6)
    except Exception:
        return None


@registry.register
class ImageOCRExtractor(BaseExtractor):
    """Извлечение текста из изображений через Tesseract OCR"""

    extensions = list(_IMAGE_EXTS)
    priority = 60

    @classmethod
    def is_available(cls) -> bool:
        return _Tesseract.detect()

    @classmethod
    def extract(cls, path: Path) -> ExtractionResult:
        return _ocr(path, psm='3', grayscale=False, enhance=True)

    @classmethod
    def _get_image_metadata(cls, path: Path) -> dict:
        """Получить метаданные изображения (размер, формат, EXIF, GPS)"""
        metadata = {'filename': path.name, 'size_bytes': path.stat().st_size}
        try:
            from PIL import Image
            from PIL.ExifTags import TAGS, GPSTAGS

            with Image.open(path) as img:
                metadata['format'] = img.format
                metadata['mode'] = img.mode
                metadata['width'] = img.size[0]
                metadata['height'] = img.size[1]

                exif = img.getexif()
                if exif:
                    for tag_id, value in exif.items():
                        tag = TAGS.get(tag_id, tag_id)
                        if tag in ('Make', 'Model', 'DateTime', 'Software', 'Artist', 'Copyright'):
                            if isinstance(value, bytes):
                                value = value.decode('utf-8', errors='ignore')
                            metadata[f'exif_{tag}'] = str(value).strip('\x00 ')[:200]
                    try:
                        gps_ifd = exif.get_ifd(0x8825)
                    except Exception:
                        gps_ifd = {}
                    if gps_ifd:
                        gps = {GPSTAGS.get(k, k): v for k, v in gps_ifd.items()}
                        lat = _gps_to_decimal(gps.get('GPSLatitude', ()), gps.get('GPSLatitudeRef', 'N'))
                        lon = _gps_to_decimal(gps.get('GPSLongitude', ()), gps.get('GPSLongitudeRef', 'E'))
                        if lat is not None and lon is not None:
                            metadata['exif_GPS'] = f"{lat}, {lon}"
        except ImportError:
            pass
        except Exception as e:
            logger.debug(f"Image metadata failed for {path}: {e}")
        return metadata


# ═══════════════════════════════════════════════════════════════════════════════
# SCREENSHOT TEXT EXTRACTOR (optimized for screenshots)
# ═══════════════════════════════════════════════════════════════════════════════

@registry.register
class ScreenshotExtractor(BaseExtractor):
    """OCR, оптимизированный для скриншотов (только изображения с "screenshot" и т.п. в имени)"""

    extensions = list(_IMAGE_EXTS)
    priority = 59  # Higher priority than general OCR for screenshots

    _PATTERNS = ('screenshot', 'screen shot', 'screen_shot', 'screen-shot', 'снимок экрана',
                 'скриншот', 'снимок', 'capture', 'screencap')

    @classmethod
    def is_available(cls) -> bool:
        return ImageOCRExtractor.is_available()

    @classmethod
    def can_handle(cls, path: Path) -> bool:
        # Только изображения: раньше сюда попадали capture.log, screen.json и т.п.
        if path.suffix.lower() not in _IMAGE_EXTS:
            return False
        name = path.name.lower()
        return any(p in name for p in cls._PATTERNS)

    @classmethod
    def extract(cls, path: Path) -> ExtractionResult:
        result = _ocr(path, psm='6', grayscale=True, enhance=False)
        if result.success:
            result.metadata['type'] = 'screenshot'
        return result


__all__ = ['ImageOCRExtractor', 'ScreenshotExtractor']
