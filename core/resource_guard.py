#!/usr/bin/env python3
"""
ASTREX v3.0 — Resource Guard
Контроль ресурсов: RAM, CPU, GPU — защита от OOM и перегрева
"""

import os
import time
import logging
import resource as _resource
from typing import Optional

logger = logging.getLogger("astrex.resources")


def get_ram_usage_percent() -> float:
    """Текущее использование RAM в процентах (без psutil)."""
    try:
        with open('/proc/meminfo', 'r') as f:
            lines = f.readlines()
        info = {}
        for line in lines:
            parts = line.split()
            if len(parts) >= 2:
                info[parts[0].rstrip(':')] = int(parts[1])
        total = info.get('MemTotal', 1)
        available = info.get('MemAvailable', info.get('MemFree', 0))
        return (1.0 - available / total) * 100.0
    except Exception:
        return 0.0


def get_ram_available_mb() -> int:
    """Доступная RAM в MB."""
    try:
        with open('/proc/meminfo', 'r') as f:
            for line in f:
                if line.startswith('MemAvailable'):
                    return int(line.split()[1]) // 1024
    except Exception:
        pass
    return 0


def wait_for_ram(max_ram_percent: int, poll_interval: float = 1.0, timeout: float = 60.0) -> bool:
    """Подождать пока RAM освободится ниже порога.

    Returns:
        True если RAM освободилась, False если timeout.
    """
    if max_ram_percent <= 0:
        return True

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        usage = get_ram_usage_percent()
        if usage < max_ram_percent:
            return True
        logger.warning(
            f"RAM usage {usage:.0f}% >= {max_ram_percent}%, "
            f"waiting for memory to free up..."
        )
        time.sleep(poll_interval)

    logger.error(f"RAM still above {max_ram_percent}% after {timeout}s timeout")
    return False


def set_worker_memory_limit(max_mb: int) -> None:
    """Установить rlimit на RSS для текущего процесса (Linux).

    Если воркер превысит лимит — получит MemoryError, а не убьёт систему.
    """
    if max_mb <= 0:
        return
    try:
        limit_bytes = max_mb * 1024 * 1024
        _resource.setrlimit(_resource.RLIMIT_AS, (limit_bytes, limit_bytes))
    except (ValueError, OSError) as e:
        # Может не сработать на некоторых системах
        logger.debug(f"Could not set memory limit: {e}")


def calculate_safe_workers(requested: Optional[int] = None) -> int:
    """Рассчитать безопасное количество воркеров исходя из доступной RAM."""
    from .config import ENGINE_CONFIG

    cpu_count = os.cpu_count() or 4

    if requested is not None and requested > 0:
        max_workers = requested
    else:
        max_workers = max(1, int(cpu_count * ENGINE_CONFIG.max_workers_multiplier))

    # Ограничиваем по доступной RAM
    available_mb = get_ram_available_mb()
    if available_mb > 0 and ENGINE_CONFIG.max_ram_per_worker_mb > 0:
        ram_based_max = max(1, available_mb // ENGINE_CONFIG.max_ram_per_worker_mb)
        if ram_based_max < max_workers:
            logger.info(
                f"Reducing workers {max_workers} -> {ram_based_max} "
                f"(available RAM: {available_mb}MB, "
                f"per worker limit: {ENGINE_CONFIG.max_ram_per_worker_mb}MB)"
            )
            max_workers = ram_based_max

    # Никогда больше cpu_count * 2 (жёсткий потолок)
    hard_cap = cpu_count * 2
    max_workers = min(max_workers, hard_cap)

    # Минимум 1
    return max(1, max_workers)


def limit_gpu_memory() -> None:
    """Ограничить использование GPU памяти по gpu_memory_fraction."""
    from .config import NLP_CONFIG
    fraction = NLP_CONFIG.gpu_memory_fraction

    if fraction <= 0 or fraction >= 1.0:
        return

    try:
        import torch
        if torch.cuda.is_available():
            for i in range(torch.cuda.device_count()):
                torch.cuda.set_per_process_memory_fraction(fraction, i)
                logger.info(f"CUDA device {i}: memory limited to {fraction*100:.0f}%")
    except (ImportError, Exception) as e:
        logger.debug(f"Could not limit GPU memory: {e}")


def limit_cpu_affinity(max_cpu_percent: int) -> None:
    """Ограничить количество CPU ядер через affinity (Linux).

    Например, max_cpu_percent=75 на 8-ядерном CPU оставит 6 ядер.
    """
    if max_cpu_percent <= 0 or max_cpu_percent >= 100:
        return

    try:
        cpu_count = os.cpu_count() or 4
        target_cpus = max(1, int(cpu_count * max_cpu_percent / 100))

        # Устанавливаем affinity для текущего процесса (наследуется дочерними)
        cpus = list(range(target_cpus))
        os.sched_setaffinity(0, cpus)
        logger.info(f"CPU affinity set to {target_cpus}/{cpu_count} cores ({max_cpu_percent}%)")
    except (AttributeError, OSError) as e:
        logger.debug(f"Could not set CPU affinity: {e}")


def apply_resource_limits() -> None:
    """Применить все ресурсные ограничения при старте движка."""
    from .config import ENGINE_CONFIG

    limit_cpu_affinity(ENGINE_CONFIG.max_cpu_percent)
    limit_gpu_memory()


__all__ = [
    'get_ram_usage_percent', 'get_ram_available_mb',
    'wait_for_ram', 'set_worker_memory_limit',
    'calculate_safe_workers', 'limit_gpu_memory',
    'limit_cpu_affinity', 'apply_resource_limits',
]
