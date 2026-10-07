#!/usr/bin/env python3
"""
ASTREX v3.0 — Media Extractors
Audio and Video: метаданные (mutagen / ffprobe) и транскрипция (Whisper).
"""

import json
import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Dict, Optional, Any

from .base import BaseExtractor, ExtractionResult, registry, worker_temp_dir
from core.config import ENGINE_CONFIG
from core.logging_setup import get_logger

logger = get_logger("astrex.media")

_SUBPROCESS_TIMEOUT = 120


class _Whisper:
    """Модель Whisper загружается один раз на процесс (а не на каждый файл)."""
    _model = None
    _checked = False
    _available = False
    _lock = threading.Lock()

    @classmethod
    def available(cls) -> bool:
        if not cls._checked:
            try:
                import whisper  # noqa: F401
                cls._available = bool(ENGINE_CONFIG.transcribe_media)
            except ImportError:
                cls._available = False
            cls._checked = True
        return cls._available

    @classmethod
    def transcribe(cls, audio_path: str) -> Optional[str]:
        if not cls.available():
            return None
        try:
            import whisper
            with cls._lock:
                if cls._model is None:
                    logger.info(f"Loading Whisper model '{ENGINE_CONFIG.whisper_model}'...")
                    cls._model = whisper.load_model(ENGINE_CONFIG.whisper_model)
                result = cls._model.transcribe(audio_path)
            return (result.get('text') or '').strip()
        except Exception as e:
            logger.error(f"Whisper transcription failed for {audio_path}: {e}")
            return None


def _tool(name: str) -> Optional[str]:
    return shutil.which(name)


def _ffprobe(path: Path) -> Dict[str, Any]:
    """Метаданные медиафайла через ffprobe (с таймаутом)."""
    ffprobe = _tool('ffprobe')
    if not ffprobe:
        return {}
    metadata: Dict[str, Any] = {}
    try:
        result = subprocess.run(
            [ffprobe, '-v', 'quiet', '-print_format', 'json', '-show_format', '-show_streams', str(path)],
            capture_output=True, text=True, timeout=_SUBPROCESS_TIMEOUT, check=True,
        )
        data = json.loads(result.stdout or '{}')
        fmt = data.get('format', {})
        if 'duration' in fmt:
            metadata['duration'] = round(float(fmt['duration']), 2)
        if 'size' in fmt:
            metadata['size'] = int(fmt['size'])
        if 'bit_rate' in fmt:
            metadata['bitrate'] = int(fmt['bit_rate'])
        for key, value in (fmt.get('tags') or {}).items():
            if key.lower() in ('title', 'artist', 'album', 'date', 'comment', 'creation_time',
                               'location', 'encoder'):
                metadata[key.lower()] = str(value)[:300]
        for stream in data.get('streams', []):
            if stream.get('codec_type') == 'video':
                metadata['video_codec'] = stream.get('codec_name')
                if 'width' in stream and 'height' in stream:
                    metadata['resolution'] = f"{stream['width']}x{stream['height']}"
                if 'r_frame_rate' in stream:
                    metadata['fps'] = stream['r_frame_rate']
            elif stream.get('codec_type') == 'audio':
                metadata['audio_codec'] = stream.get('codec_name')
                if 'sample_rate' in stream:
                    metadata['sample_rate'] = stream['sample_rate']
    except Exception as e:
        logger.debug(f"ffprobe failed for {path}: {e}")
    return metadata


def _extract_audio(src: Path, max_duration: Optional[float]) -> Optional[str]:
    """Аудиодорожка в WAV 16 кГц моно (с обрезкой до max_duration). None — нет ffmpeg/ошибка."""
    ffmpeg = _tool('ffmpeg')
    if not ffmpeg:
        return None
    fd, out = tempfile.mkstemp(prefix="astrex-audio-", suffix=".wav", dir=str(worker_temp_dir()))
    os.close(fd)
    cmd = [ffmpeg, '-nostdin', '-y', '-i', str(src), '-vn', '-acodec', 'pcm_s16le', '-ar', '16000', '-ac', '1']
    if max_duration:
        cmd += ['-t', str(int(max_duration))]
    cmd.append(out)
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=max(_SUBPROCESS_TIMEOUT, int(max_duration or 0)), check=True)
        return out
    except Exception as e:
        logger.debug(f"ffmpeg failed for {src}: {e}")
        try:
            os.unlink(out)
        except OSError:
            pass
        return None


def _metadata_text(metadata: Dict[str, Any]) -> str:
    keys = ('title', 'artist', 'album', 'date', 'comment', 'creation_time', 'location')
    return '\n'.join(f"{k}: {metadata[k]}" for k in keys if metadata.get(k))


def _transcribe_media(path: Path, duration: Optional[float]) -> Optional[str]:
    if not _Whisper.available():
        return None
    limit = ENGINE_CONFIG.media_max_duration
    needs_trim = duration is None or (limit and duration > limit)
    wav = _extract_audio(path, limit) if (needs_trim or path.suffix.lower() not in ('.wav', '.mp3')) else None
    if wav is None and needs_trim and duration is not None and limit and duration > limit:
        logger.warning(f"{path.name}: {duration:.0f}s > {limit}s limit and ffmpeg is not available — "
                       f"transcription skipped")
        return None
    try:
        return _Whisper.transcribe(wav or str(path))
    finally:
        if wav:
            try:
                os.unlink(wav)
            except OSError:
                pass


# ═══════════════════════════════════════════════════════════════════════════════
# AUDIO EXTRACTOR
# ═══════════════════════════════════════════════════════════════════════════════

@registry.register
class AudioExtractor(BaseExtractor):
    """Аудио: теги и параметры (mutagen/ffprobe) + транскрипция Whisper (если установлен)"""

    extensions = ['.mp3', '.wav', '.ogg', '.flac', '.m4a', '.aac', '.wma', '.opus']
    priority = 10

    MAX_DURATION = 1800  # совместимость; используется ENGINE_CONFIG.media_max_duration

    @classmethod
    def is_available(cls) -> bool:
        try:
            import mutagen  # noqa: F401
            return True
        except ImportError:
            return bool(_tool('ffprobe')) or _Whisper.available()

    @classmethod
    def _extract_metadata(cls, path: Path) -> dict:
        metadata: Dict[str, Any] = {}
        try:
            import mutagen
            audio = mutagen.File(str(path), easy=True)
            if audio is not None:
                info = getattr(audio, 'info', None)
                for attr, key in (('length', 'duration'), ('sample_rate', 'sample_rate'),
                                  ('bitrate', 'bitrate'), ('channels', 'channels')):
                    value = getattr(info, attr, None)
                    if value:
                        metadata[key] = round(value, 2) if isinstance(value, float) else value
                for key in ('title', 'artist', 'album', 'date', 'genre', 'comment'):
                    try:
                        value = audio.get(key)
                    except Exception:
                        value = None
                    if value:
                        metadata[key] = str(value[0] if isinstance(value, list) else value)[:300]
        except ImportError:
            pass
        except Exception as e:
            logger.debug(f"mutagen failed for {path}: {e}")
        if not metadata:
            metadata = _ffprobe(path)
        return metadata

    @classmethod
    def extract(cls, path: Path) -> ExtractionResult:
        try:
            metadata = cls._extract_metadata(path)
            transcript = _transcribe_media(path, metadata.get('duration'))
            parts = [p for p in (_metadata_text(metadata), transcript) if p]
            if transcript is not None:
                metadata['transcribed'] = True
            if not parts and not metadata:
                return ExtractionResult(error="No metadata or transcription available "
                                              "(install mutagen/ffmpeg/openai-whisper)")
            return ExtractionResult(text='\n\n'.join(parts), metadata=metadata)
        except Exception as e:
            return ExtractionResult(error=f"Audio extraction failed: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# VIDEO EXTRACTOR
# ═══════════════════════════════════════════════════════════════════════════════

@registry.register
class VideoExtractor(BaseExtractor):
    """Видео: метаданные (ffprobe) + транскрипция аудиодорожки (ffmpeg + Whisper)"""

    extensions = ['.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv', '.webm', '.m4v', '.3gp']
    priority = 10

    MAX_DURATION = 1800

    @classmethod
    def is_available(cls) -> bool:
        return bool(_tool('ffprobe')) or (bool(_tool('ffmpeg')) and _Whisper.available())

    @classmethod
    def extract(cls, path: Path) -> ExtractionResult:
        try:
            metadata = _ffprobe(path)
            transcript = None
            if _Whisper.available() and _tool('ffmpeg'):
                wav = _extract_audio(path, ENGINE_CONFIG.media_max_duration)
                if wav:
                    try:
                        transcript = _Whisper.transcribe(wav)
                    finally:
                        try:
                            os.unlink(wav)
                        except OSError:
                            pass
            parts = [p for p in (_metadata_text(metadata), transcript) if p]
            if transcript is not None:
                metadata['transcribed'] = True
            if not parts and not metadata:
                return ExtractionResult(error="No metadata or transcription available "
                                              "(install ffmpeg/openai-whisper)")
            return ExtractionResult(text='\n\n'.join(parts), metadata=metadata)
        except Exception as e:
            return ExtractionResult(error=f"Video extraction failed: {e}")


__all__ = ['AudioExtractor', 'VideoExtractor']
