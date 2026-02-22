#!/usr/bin/env python3
"""
ASTREX v3.0 — Media Extractors
Audio and Video file processing with transcription
"""

import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from .base import BaseExtractor, ExtractionResult, registry
from core.logging_setup import get_logger

logger = get_logger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# AUDIO EXTRACTOR
# ═══════════════════════════════════════════════════════════════════════════════

@registry.register
class AudioExtractor(BaseExtractor):
    """Извлечение текста из аудио файлов через Whisper"""

    extensions = ['.mp3', '.wav', '.ogg', '.flac', '.m4a', '.aac', '.wma']
    priority = 10

    _available: Optional[bool] = None
    _whisper_available: Optional[bool] = None
    _mutagen_available: Optional[bool] = None

    # Максимальная длительность для транскрипции (секунды)
    MAX_DURATION = 1800  # 30 minutes

    @classmethod
    def is_available(cls) -> bool:
        if cls._available is None:
            # Check whisper availability
            try:
                import whisper
                cls._whisper_available = True
            except ImportError:
                cls._whisper_available = False

            # Check mutagen availability
            try:
                import mutagen
                cls._mutagen_available = True
            except ImportError:
                cls._mutagen_available = False

            # Available if at least one is available
            cls._available = cls._whisper_available or cls._mutagen_available

        return cls._available

    @classmethod
    def _extract_metadata(cls, path: Path) -> dict:
        """Извлечение метаданных из аудио файла"""
        metadata = {}

        if not cls._mutagen_available:
            return metadata

        try:
            import mutagen

            audio = mutagen.File(path)
            if audio is None:
                return metadata

            # Duration
            if hasattr(audio.info, 'length'):
                metadata['duration'] = round(audio.info.length, 2)

            # Sample rate
            if hasattr(audio.info, 'sample_rate'):
                metadata['sample_rate'] = audio.info.sample_rate

            # Bitrate
            if hasattr(audio.info, 'bitrate'):
                metadata['bitrate'] = audio.info.bitrate

            # Channels
            if hasattr(audio.info, 'channels'):
                metadata['channels'] = audio.info.channels

            # Tags
            if audio.tags:
                # Try to get common tags
                for key in ['title', 'artist', 'album', 'date']:
                    if key in audio.tags:
                        value = audio.tags[key]
                        if isinstance(value, list) and value:
                            metadata[key] = str(value[0])
                        else:
                            metadata[key] = str(value)

        except Exception as e:
            logger.debug(f"Failed to extract metadata from {path}: {e}")

        return metadata

    @classmethod
    def _transcribe_audio(cls, path: Path, duration: Optional[float] = None) -> Optional[str]:
        """Транскрипция аудио через Whisper"""
        if not cls._whisper_available:
            return None

        try:
            import whisper

            # Check duration limit
            if duration and duration > cls.MAX_DURATION:
                logger.warning(
                    f"Audio file {path.name} is {duration}s long, "
                    f"limiting transcription to first {cls.MAX_DURATION}s"
                )
                # Note: Whisper will process the entire file, but we warn the user
                # A production system might use ffmpeg to trim first

            # Load model (using base model for speed/quality balance)
            logger.info(f"Loading Whisper model for {path.name}...")
            model = whisper.load_model("base")

            # Transcribe
            logger.info(f"Transcribing {path.name}...")
            result = model.transcribe(str(path))

            return result.get('text', '').strip()

        except Exception as e:
            logger.error(f"Whisper transcription failed for {path}: {e}")
            return None

    @classmethod
    def extract(cls, path: Path) -> ExtractionResult:
        try:
            # Extract metadata first
            metadata = cls._extract_metadata(path)

            # Attempt transcription
            text = None
            if cls._whisper_available:
                duration = metadata.get('duration')
                text = cls._transcribe_audio(path, duration)

            # If we got neither text nor metadata, it's a failure
            if text is None and not metadata:
                return ExtractionResult(
                    error="No whisper or mutagen available for audio extraction"
                )

            return ExtractionResult(text=text, metadata=metadata)

        except Exception as e:
            return ExtractionResult(error=f"Audio extraction failed: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# VIDEO EXTRACTOR
# ═══════════════════════════════════════════════════════════════════════════════

@registry.register
class VideoExtractor(BaseExtractor):
    """Извлечение текста из видео файлов через FFmpeg + Whisper"""

    extensions = ['.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv', '.webm']
    priority = 10

    _available: Optional[bool] = None
    _whisper_available: Optional[bool] = None
    _ffmpeg_available: Optional[bool] = None
    _ffprobe_available: Optional[bool] = None

    # Максимальная длительность для транскрипции (секунды)
    MAX_DURATION = 1800  # 30 minutes

    @classmethod
    def is_available(cls) -> bool:
        if cls._available is None:
            # Check whisper
            try:
                import whisper
                cls._whisper_available = True
            except ImportError:
                cls._whisper_available = False

            # Check ffmpeg
            try:
                subprocess.run(
                    ['ffmpeg', '-version'],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=True
                )
                cls._ffmpeg_available = True
            except (subprocess.CalledProcessError, FileNotFoundError):
                cls._ffmpeg_available = False

            # Check ffprobe
            try:
                subprocess.run(
                    ['ffprobe', '-version'],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=True
                )
                cls._ffprobe_available = True
            except (subprocess.CalledProcessError, FileNotFoundError):
                cls._ffprobe_available = False

            # Available if whisper is available (ffmpeg is required for extraction)
            cls._available = cls._whisper_available

        return cls._available

    @classmethod
    def _extract_metadata(cls, path: Path) -> dict:
        """Извлечение метаданных из видео через ffprobe"""
        metadata = {}

        if not cls._ffprobe_available:
            return metadata

        try:
            # Get video info via ffprobe
            result = subprocess.run(
                [
                    'ffprobe',
                    '-v', 'quiet',
                    '-print_format', 'json',
                    '-show_format',
                    '-show_streams',
                    str(path)
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=True
            )

            import json
            data = json.loads(result.stdout)

            # Format info
            if 'format' in data:
                fmt = data['format']
                if 'duration' in fmt:
                    metadata['duration'] = round(float(fmt['duration']), 2)
                if 'size' in fmt:
                    metadata['size'] = int(fmt['size'])
                if 'bit_rate' in fmt:
                    metadata['bitrate'] = int(fmt['bit_rate'])

            # Stream info
            if 'streams' in data:
                for stream in data['streams']:
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
            logger.debug(f"Failed to extract metadata from {path}: {e}")

        return metadata

    @classmethod
    def _extract_audio_track(cls, video_path: Path, output_path: Path, duration: Optional[float] = None) -> bool:
        """Извлечение аудио дорожки из видео в WAV"""
        if not cls._ffmpeg_available:
            return False

        try:
            cmd = [
                'ffmpeg',
                '-i', str(video_path),
                '-vn',  # No video
                '-acodec', 'pcm_s16le',  # PCM 16-bit
                '-ar', '16000',  # 16kHz (Whisper default)
                '-ac', '1',  # Mono
            ]

            # Limit duration if needed
            if duration and duration > cls.MAX_DURATION:
                cmd.extend(['-t', str(cls.MAX_DURATION)])

            cmd.extend([
                '-y',  # Overwrite
                str(output_path)
            ])

            subprocess.run(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True
            )

            return True

        except Exception as e:
            logger.error(f"Failed to extract audio from {video_path}: {e}")
            return False

    @classmethod
    def _transcribe_audio(cls, audio_path: Path) -> Optional[str]:
        """Транскрипция аудио через Whisper"""
        if not cls._whisper_available:
            return None

        try:
            import whisper

            logger.info(f"Loading Whisper model for transcription...")
            model = whisper.load_model("base")

            logger.info(f"Transcribing audio track...")
            result = model.transcribe(str(audio_path))

            return result.get('text', '').strip()

        except Exception as e:
            logger.error(f"Whisper transcription failed: {e}")
            return None

    @classmethod
    def extract(cls, path: Path) -> ExtractionResult:
        try:
            # Extract metadata first
            metadata = cls._extract_metadata(path)

            # Attempt transcription
            text = None
            if cls._whisper_available and cls._ffmpeg_available:
                duration = metadata.get('duration')

                # Create temp file for audio
                with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp:
                    tmp_path = Path(tmp.name)

                try:
                    logger.info(f"Extracting audio track from {path.name}...")
                    if cls._extract_audio_track(path, tmp_path, duration):
                        text = cls._transcribe_audio(tmp_path)
                finally:
                    # Cleanup temp file
                    if tmp_path.exists():
                        tmp_path.unlink()

            # If we got neither text nor metadata, it's a failure
            if text is None and not metadata:
                return ExtractionResult(
                    error="No whisper/ffmpeg available for video extraction"
                )

            return ExtractionResult(text=text, metadata=metadata)

        except Exception as e:
            return ExtractionResult(error=f"Video extraction failed: {e}")


__all__ = ['AudioExtractor', 'VideoExtractor']
