#!/usr/bin/env python3
"""
ASTREX v3.0 — Image OCR Extractor
Tesseract OCR для извлечения текста из изображений
"""

import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from .base import BaseExtractor, ExtractionResult, registry
from core.config import ENGINE_CONFIG


@registry.register
class ImageOCRExtractor(BaseExtractor):
    """Извлечение текста из изображений через Tesseract OCR"""
    
    extensions = ['.png', '.jpg', '.jpeg', '.tiff', '.tif', '.bmp', '.gif', '.webp']
    priority = 60
    
    _available: Optional[bool] = None
    _tesseract_path: Optional[str] = None
    
    @classmethod
    def is_available(cls) -> bool:
        if cls._available is None:
            if not ENGINE_CONFIG.ocr_enabled:
                cls._available = False
                return False
            
            # Check for tesseract binary
            try:
                result = subprocess.run(
                    ['tesseract', '--version'],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                cls._available = result.returncode == 0
                cls._tesseract_path = 'tesseract'
            except (subprocess.SubprocessError, FileNotFoundError):
                # Try common paths
                for path in ['/usr/bin/tesseract', '/usr/local/bin/tesseract']:
                    try:
                        result = subprocess.run(
                            [path, '--version'],
                            capture_output=True,
                            text=True,
                            timeout=5
                        )
                        if result.returncode == 0:
                            cls._available = True
                            cls._tesseract_path = path
                            break
                    except (subprocess.SubprocessError, FileNotFoundError):
                        continue
                else:
                    cls._available = False
        
        return cls._available
    
    @classmethod
    def extract(cls, path: Path) -> ExtractionResult:
        if not cls.is_available():
            return ExtractionResult(error="Tesseract OCR not available")
        
        try:
            # Check file size
            file_size = path.stat().st_size
            if file_size > 50 * 1024 * 1024:  # 50MB limit for images
                return ExtractionResult(error="Image too large for OCR")
            
            # Preprocess image if PIL available
            image_path = str(path)
            preprocessed = False
            temp_path = None
            
            try:
                from PIL import Image, ImageFilter, ImageEnhance
                
                with Image.open(path) as img:
                    # Convert to RGB if necessary
                    if img.mode in ('RGBA', 'P'):
                        img = img.convert('RGB')
                    
                    # Resize if too large
                    max_dim = 4000
                    if max(img.size) > max_dim:
                        ratio = max_dim / max(img.size)
                        new_size = (int(img.size[0] * ratio), int(img.size[1] * ratio))
                        img = img.resize(new_size, Image.Resampling.LANCZOS)
                    
                    # Enhance for better OCR
                    # Increase contrast
                    enhancer = ImageEnhance.Contrast(img)
                    img = enhancer.enhance(1.5)
                    
                    # Sharpen
                    img = img.filter(ImageFilter.SHARPEN)
                    
                    # Save preprocessed
                    temp_path = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
                    img.save(temp_path.name, 'PNG')
                    temp_path.close()
                    image_path = temp_path.name
                    preprocessed = True
                    
            except ImportError:
                pass  # PIL not available, use original image
            except Exception:
                pass  # Preprocessing failed, use original
            
            # Run Tesseract
            try:
                result = subprocess.run(
                    [
                        cls._tesseract_path,
                        image_path,
                        'stdout',
                        '-l', ENGINE_CONFIG.ocr_languages,
                        '--psm', '3',  # Fully automatic page segmentation
                        '--oem', '3',  # Default OCR Engine Mode
                    ],
                    capture_output=True,
                    text=True,
                    timeout=ENGINE_CONFIG.ocr_timeout
                )
                
                if result.returncode == 0:
                    text = result.stdout.strip()
                    
                    # Get metadata
                    metadata = cls._get_image_metadata(path)
                    
                    if text:
                        return ExtractionResult(text=text, metadata=metadata)
                    else:
                        return ExtractionResult(
                            text="[Текст не обнаружен]",
                            metadata=metadata
                        )
                else:
                    return ExtractionResult(error=f"Tesseract error: {result.stderr}")
                    
            finally:
                # Cleanup temp file
                if temp_path:
                    try:
                        Path(temp_path.name).unlink()
                    except Exception:
                        pass
                        
        except subprocess.TimeoutExpired:
            return ExtractionResult(error=f"OCR timeout ({ENGINE_CONFIG.ocr_timeout}s)")
        except Exception as e:
            return ExtractionResult(error=f"OCR failed: {e}")
    
    @classmethod
    def _get_image_metadata(cls, path: Path) -> dict:
        """Получить метаданные изображения"""
        metadata = {
            'filename': path.name,
            'size_bytes': path.stat().st_size
        }
        
        try:
            from PIL import Image
            from PIL.ExifTags import TAGS
            
            with Image.open(path) as img:
                metadata['format'] = img.format
                metadata['mode'] = img.mode
                metadata['width'] = img.size[0]
                metadata['height'] = img.size[1]
                
                # EXIF data
                exif = img.getexif()
                if exif:
                    for tag_id, value in exif.items():
                        tag = TAGS.get(tag_id, tag_id)
                        if tag in ('Make', 'Model', 'DateTime', 'Software', 'GPSInfo'):
                            if isinstance(value, bytes):
                                try:
                                    value = value.decode('utf-8', errors='ignore')
                                except Exception:
                                    value = str(value)
                            metadata[f'exif_{tag}'] = str(value)[:200]
                            
        except ImportError:
            pass
        except Exception:
            pass
        
        return metadata


# ═══════════════════════════════════════════════════════════════════════════════
# SCREENSHOT TEXT EXTRACTOR (optimized for screenshots)
# ═══════════════════════════════════════════════════════════════════════════════

@registry.register
class ScreenshotExtractor(BaseExtractor):
    """Оптимизированный экстрактор для скриншотов"""
    
    extensions = ['.png', '.jpg', '.jpeg']
    priority = 59  # Higher priority than general OCR for screenshots
    
    @classmethod
    def is_available(cls) -> bool:
        # Reuse ImageOCRExtractor availability
        return ImageOCRExtractor.is_available()
    
    @classmethod
    def can_handle(cls, path: Path) -> bool:
        # Handle files that look like screenshots
        name = path.name.lower()
        screenshot_patterns = [
            'screenshot', 'screen', 'снимок', 'скриншот',
            'capture', 'grab', 'снимок экрана'
        ]
        return any(p in name for p in screenshot_patterns)
    
    @classmethod
    def extract(cls, path: Path) -> ExtractionResult:
        # Use ImageOCRExtractor with screenshot-optimized settings
        try:
            from PIL import Image, ImageOps

            tmp_path = None
            try:
                with Image.open(path) as img:
                    # Screenshots often have white/light backgrounds
                    # Convert to grayscale and apply threshold for better OCR
                    if img.mode != 'L':
                        img = img.convert('L')

                    # Auto-contrast
                    img = ImageOps.autocontrast(img)

                    # Save to temp
                    tmp = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
                    tmp_path = tmp.name
                    tmp.close()
                    img.save(tmp_path, 'PNG')

                # Run tesseract with screenshot-optimized PSM
                result = subprocess.run(
                    [
                        ImageOCRExtractor._tesseract_path,
                        tmp_path,
                        'stdout',
                        '-l', ENGINE_CONFIG.ocr_languages,
                        '--psm', '6',  # Uniform block of text
                        '--oem', '3',
                    ],
                    capture_output=True,
                    text=True,
                    timeout=ENGINE_CONFIG.ocr_timeout
                )

                if result.returncode == 0:
                    return ExtractionResult(
                        text=result.stdout.strip(),
                        metadata={'type': 'screenshot'}
                    )
                else:
                    return ExtractionResult(error=result.stderr)
            finally:
                if tmp_path:
                    try:
                        Path(tmp_path).unlink()
                    except Exception:
                        pass
                        
        except ImportError:
            # Fallback to regular OCR
            return ImageOCRExtractor.extract(path)
        except Exception as e:
            return ExtractionResult(error=f"Screenshot OCR failed: {e}")


__all__ = ['ImageOCRExtractor', 'ScreenshotExtractor']
