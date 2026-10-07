#!/usr/bin/env python3
"""
ASTREX v3.0 — Logging Setup
Централизованная настройка логирования с ротацией файлов.

Обработчики (stderr + файл) висят только на корневом логгере "astrex".
Все модули используют дочерние логгеры "astrex.<module>", которые лишь
передают записи наверх — поэтому каждая строка выводится ровно один раз.
"""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler

from .config import LOGS_PATH, CONFIG_WARNINGS

ROOT_LOGGER = "astrex"

_FORMATTER = logging.Formatter(
    fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)


def _in_worker_process() -> bool:
    """True, если код выполняется в дочернем процессе пула воркеров."""
    try:
        import multiprocessing
        return multiprocessing.parent_process() is not None
    except Exception:
        return False


def _level_from_env(default: int) -> int:
    value = os.environ.get("ASTREX_LOG_LEVEL")
    if not value:
        return default
    level = logging.getLevelName(value.strip().upper())
    return level if isinstance(level, int) else default


def setup_logger(
    name: str = ROOT_LOGGER,
    level: int = logging.INFO,
    log_file: bool = True,
    console: bool = True,
    max_bytes: int = 10 * 1024 * 1024,  # 10MB
    backup_count: int = 5
) -> logging.Logger:
    """Создать и настроить логгер с собственными обработчиками.

    Args:
        name: имя логгера
        level: уровень вывода в консоль (stderr)
        log_file: писать ли в файл ~/.astrex/logs/<name>.log
        console: выводить ли в stderr
        max_bytes: максимальный размер файла лога
        backup_count: количество ротированных файлов
    """
    logger = logging.getLogger(name)

    if logger.handlers:
        return logger

    worker = _in_worker_process()
    console_level = _level_from_env(logging.WARNING if worker else level)

    # Логгер пропускает всё (файл пишет DEBUG), уровни фильтруются обработчиками
    logger.setLevel(logging.DEBUG)
    # Не дублировать записи в корневой логгер Python (uvicorn и т.п.)
    logger.propagate = False

    if console:
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setLevel(console_level)
        console_handler.setFormatter(_FORMATTER)
        logger.addHandler(console_handler)

    # В воркерах файл не пишем: несколько процессов, ротирующих один файл,
    # портят его.
    if log_file and not worker:
        try:
            file_handler = RotatingFileHandler(
                LOGS_PATH / f"{name}.log",
                maxBytes=max_bytes,
                backupCount=backup_count,
                encoding="utf-8",
                delay=True,
            )
            file_handler.setLevel(logging.DEBUG)
            file_handler.setFormatter(_FORMATTER)
            logger.addHandler(file_handler)
        except Exception:
            pass  # не падаем если не можем создать файл лога

    if not logger.handlers:
        logger.addHandler(logging.NullHandler())

    return logger


def get_logger(name: str = ROOT_LOGGER) -> logging.Logger:
    """Получить логгер.

    Имена "astrex" и "astrex.*" используют общие обработчики корневого
    логгера "astrex" (без дублирования). Для прочих имён создаётся
    отдельный логгер со своими обработчиками.
    """
    if name == ROOT_LOGGER or name.startswith(ROOT_LOGGER + "."):
        setup_logger(ROOT_LOGGER)
        return logging.getLogger(name)

    logger = logging.getLogger(name)
    if not logger.handlers:
        return setup_logger(name)
    return logger


# Главный логгер приложения
log = setup_logger(ROOT_LOGGER)

# Предупреждения конфигурации, накопленные до настройки логирования
while CONFIG_WARNINGS:
    log.warning(CONFIG_WARNINGS.pop(0))


__all__ = ['setup_logger', 'get_logger', 'log', 'ROOT_LOGGER']
