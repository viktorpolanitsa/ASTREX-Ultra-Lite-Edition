#!/usr/bin/env python3
"""
ASTREX v3.0 — Logging Setup
Централизованная настройка логирования с ротацией файлов
"""

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .config import LOGS_PATH


def setup_logger(
    name: str = "astrex",
    level: int = logging.INFO,
    log_file: bool = True,
    console: bool = True,
    max_bytes: int = 10 * 1024 * 1024,  # 10MB
    backup_count: int = 5
) -> logging.Logger:
    """Создать и настроить логгер.

    Args:
        name: имя логгера
        level: уровень логирования
        log_file: писать ли в файл
        console: выводить ли в stderr
        max_bytes: максимальный размер файла лога
        backup_count: количество ротированных файлов
    """
    logger = logging.getLogger(name)

    if logger.handlers:
        return logger

    logger.setLevel(level)

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    if console:
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setLevel(level)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    if log_file:
        try:
            file_handler = RotatingFileHandler(
                LOGS_PATH / f"{name}.log",
                maxBytes=max_bytes,
                backupCount=backup_count,
                encoding="utf-8"
            )
            file_handler.setLevel(logging.DEBUG)
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
        except Exception:
            pass  # не падаем если не можем создать файл лога

    return logger


def get_logger(name: str = "astrex") -> logging.Logger:
    """Получить существующий логгер или создать новый."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        return setup_logger(name)
    return logger


# Главный логгер приложения
log = setup_logger("astrex")


__all__ = ['setup_logger', 'get_logger', 'log']
