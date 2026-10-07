#!/usr/bin/env python3
"""
ASTREX v3.0 — Resource Guard
Контроль ресурсов: RAM, CPU, GPU — защита от OOM и перегрева
"""

import os
import time
import logging
from typing import List, Optional, Set

logger = logging.getLogger("astrex.resources")

# Исходный набор разрешённых ядер процесса (до наших ограничений).
# Сохраняем при импорте, чтобы повторные вызовы limit_cpu_affinity()
# были идемпотентны и никогда не расширяли affinity/cpuset пользователя.
try:
    _ORIGINAL_AFFINITY: Optional[Set[int]] = set(os.sched_getaffinity(0))
except (AttributeError, OSError):
    _ORIGINAL_AFFINITY = None


def available_cpu_count() -> int:
    """Число ядер, доступных процессу (учитывает taskset/cpuset)."""
    if _ORIGINAL_AFFINITY:
        return len(_ORIGINAL_AFFINITY)
    return os.cpu_count() or 4


def _read_meminfo() -> dict:
    info = {}
    with open('/proc/meminfo', 'r') as f:
        for line in f:
            parts = line.split()
            if len(parts) >= 2:
                info[parts[0].rstrip(':')] = int(parts[1])
    return info


def get_ram_usage_percent() -> float:
    """Текущее использование RAM в процентах (без psutil)."""
    try:
        info = _read_meminfo()
        total = info.get('MemTotal', 0)
        if total <= 0:
            return 0.0
        available = info.get('MemAvailable', info.get('MemFree', 0))
        return (1.0 - available / total) * 100.0
    except Exception:
        return 0.0


def get_ram_available_mb() -> int:
    """Доступная RAM в MB (0 — неизвестно)."""
    try:
        info = _read_meminfo()
        return info.get('MemAvailable', info.get('MemFree', 0)) // 1024
    except Exception:
        return 0


def get_process_rss_mb(pid: int) -> float:
    """RSS процесса в MB (Linux, /proc/<pid>/statm). 0 — неизвестно."""
    try:
        with open(f'/proc/{pid}/statm', 'r') as f:
            resident_pages = int(f.read().split()[1])
        return resident_pages * os.sysconf('SC_PAGE_SIZE') / (1024 * 1024)
    except Exception:
        return 0.0


def is_ram_high(max_ram_percent: int) -> bool:
    """RAM занята выше порога (0 — проверка отключена)."""
    return max_ram_percent > 0 and get_ram_usage_percent() >= max_ram_percent


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
    """Ограничить ВИРТУАЛЬНОЕ адресное пространство процесса (RLIMIT_AS).

    Внимание: это не RSS. Библиотеки вроде torch/spaCy резервируют много
    виртуальной памяти, поэтому движок ограничивает воркеры по реальному RSS
    (см. core.workerpool), а эта функция оставлена для ручного использования.
    """
    if max_mb <= 0:
        return
    try:
        import resource
        limit_bytes = max_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (limit_bytes, limit_bytes))
    except (ImportError, ValueError, OSError) as e:
        logger.debug(f"Could not set memory limit: {e}")


def calculate_safe_workers(requested: Optional[int] = None, per_worker_mb: Optional[int] = None) -> int:
    """Рассчитать безопасное количество воркеров исходя из ядер и RAM."""
    from .config import ENGINE_CONFIG

    cpu_count = available_cpu_count()
    if ENGINE_CONFIG.max_cpu_percent and 0 < ENGINE_CONFIG.max_cpu_percent < 100:
        cpu_budget = max(1, int(cpu_count * ENGINE_CONFIG.max_cpu_percent / 100))
    else:
        cpu_budget = cpu_count

    if requested is not None and requested > 0:
        max_workers = requested
    else:
        max_workers = max(1, int(cpu_budget * max(1, ENGINE_CONFIG.max_workers_multiplier)))

    # Ограничиваем по доступной RAM
    per_worker = per_worker_mb if per_worker_mb is not None else ENGINE_CONFIG.est_ram_per_worker_mb
    available_mb = get_ram_available_mb()
    if available_mb > 0 and per_worker > 0:
        ram_based_max = max(1, available_mb // per_worker)
        if ram_based_max < max_workers:
            logger.info(
                f"Reducing workers {max_workers} -> {ram_based_max} "
                f"(available RAM: {available_mb}MB, "
                f"per worker estimate: {per_worker}MB)"
            )
            max_workers = ram_based_max

    # Жёсткий потолок: не больше 2 воркеров на доступное ядро
    max_workers = min(max_workers, cpu_count * 2)

    return max(1, max_workers)


def limit_gpu_memory() -> None:
    """Ограничить использование GPU памяти по gpu_memory_fraction (однократно)."""
    try:
        from .gpu import get_device
        get_device()  # get_device() применяет лимит при первом определении
    except Exception as e:
        logger.debug(f"Could not limit GPU memory: {e}")


def affinity_target(max_cpu_percent: int) -> Optional[List[int]]:
    """Набор ядер для сканирования: подмножество ИСХОДНОГО набора ядер процесса.

    Например, max_cpu_percent=75 на 8 разрешённых ядрах даёт 6 ядер.
    None — affinity не поддерживается платформой.
    """
    if not _ORIGINAL_AFFINITY:
        return None
    allowed = sorted(_ORIGINAL_AFFINITY)
    if max_cpu_percent <= 0 or max_cpu_percent >= 100:
        return allowed
    count = max(1, int(len(allowed) * max_cpu_percent / 100))
    return allowed[:count]


def limit_cpu_affinity(max_cpu_percent: int) -> None:
    """Ограничить ядра текущего процесса (идемпотентно; не расширяет taskset/cgroup)."""
    target = affinity_target(max_cpu_percent)
    if not target:
        return
    allowed = sorted(_ORIGINAL_AFFINITY or [])

    try:
        if set(os.sched_getaffinity(0)) != set(target):
            os.sched_setaffinity(0, target)
            logger.info(f"CPU affinity set to {len(target)}/{len(allowed)} cores ({max_cpu_percent}%)")
    except (AttributeError, OSError) as e:
        logger.debug(f"Could not set CPU affinity: {e}")


def apply_resource_limits() -> None:
    """Применить все ресурсные ограничения при старте движка."""
    from .config import ENGINE_CONFIG

    limit_cpu_affinity(ENGINE_CONFIG.max_cpu_percent)


__all__ = [
    'available_cpu_count', 'get_ram_usage_percent', 'get_ram_available_mb',
    'get_process_rss_mb', 'is_ram_high',
    'wait_for_ram', 'set_worker_memory_limit',
    'calculate_safe_workers', 'limit_gpu_memory',
    'affinity_target', 'limit_cpu_affinity', 'apply_resource_limits',
]
