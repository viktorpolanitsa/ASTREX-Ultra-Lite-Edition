#!/usr/bin/env python3
"""
ASTREX v3.0 — GPU Detection & Management
Обнаружение и управление GPU/iGPU ускорением
"""

import logging
import threading
from dataclasses import dataclass
from typing import Optional, List

logger = logging.getLogger("astrex.gpu")


@dataclass
class GPUDevice:
    """Информация об ускорителе"""
    name: str
    backend: str          # "cuda", "rocm", "xpu", "mps", "openvino", "cpu"
    device_id: int = 0
    memory_total: int = 0  # MB
    memory_free: int = 0   # MB

    @property
    def torch_device(self) -> str:
        if self.backend == "cuda":
            return f"cuda:{self.device_id}"
        if self.backend == "rocm":
            return f"cuda:{self.device_id}"  # ROCm uses cuda API in PyTorch
        if self.backend == "xpu":
            return f"xpu:{self.device_id}"
        if self.backend == "mps":
            return "mps"
        return "cpu"


def _detect_cuda() -> List[GPUDevice]:
    """Обнаружение NVIDIA CUDA GPU"""
    devices = []
    try:
        import torch
        if torch.cuda.is_available():
            for i in range(torch.cuda.device_count()):
                props = torch.cuda.get_device_properties(i)
                free, total = torch.cuda.mem_get_info(i)
                devices.append(GPUDevice(
                    name=props.name,
                    backend="cuda",
                    device_id=i,
                    memory_total=total // (1024 * 1024),
                    memory_free=free // (1024 * 1024),
                ))
    except (ImportError, Exception):
        pass
    return devices


def _detect_rocm() -> List[GPUDevice]:
    """Обнаружение AMD ROCm GPU"""
    devices = []
    try:
        import torch
        # ROCm builds report as cuda in PyTorch
        if hasattr(torch.version, 'hip') and torch.version.hip and torch.cuda.is_available():
            for i in range(torch.cuda.device_count()):
                props = torch.cuda.get_device_properties(i)
                free, total = torch.cuda.mem_get_info(i)
                devices.append(GPUDevice(
                    name=props.name,
                    backend="rocm",
                    device_id=i,
                    memory_total=total // (1024 * 1024),
                    memory_free=free // (1024 * 1024),
                ))
    except (ImportError, Exception):
        pass
    return devices


def _detect_xpu() -> List[GPUDevice]:
    """Обнаружение Intel Arc/iGPU (XPU) через intel-extension-for-pytorch"""
    devices = []
    try:
        import torch
        import intel_extension_for_pytorch as ipex
        if hasattr(torch, 'xpu') and torch.xpu.is_available():
            for i in range(torch.xpu.device_count()):
                name = torch.xpu.get_device_name(i)
                try:
                    props = torch.xpu.get_device_properties(i)
                    total = getattr(props, 'total_memory', 0) // (1024 * 1024)
                except Exception:
                    total = 0
                devices.append(GPUDevice(
                    name=name,
                    backend="xpu",
                    device_id=i,
                    memory_total=total,
                    memory_free=0,
                ))
    except (ImportError, Exception):
        pass
    return devices


def _detect_openvino() -> List[GPUDevice]:
    """Обнаружение Intel GPU через OpenVINO"""
    devices = []
    try:
        # openvino >= 2025: импорт напрямую из openvino (openvino.runtime deprecated)
        try:
            from openvino import Core
        except ImportError:
            from openvino.runtime import Core

        core = Core()
        available = core.available_devices
        for dev in available:
            if dev.startswith("GPU"):
                name = core.get_property(dev, "FULL_DEVICE_NAME")
                devices.append(GPUDevice(
                    name=name,
                    backend="openvino",
                    device_id=0,
                ))
    except (ImportError, Exception):
        pass
    return devices


def _detect_mps() -> List[GPUDevice]:
    """Обнаружение Apple Metal (macOS)"""
    devices = []
    try:
        import torch
        if hasattr(torch.backends, 'mps') and torch.backends.mps.is_available():
            devices.append(GPUDevice(
                name="Apple Metal GPU",
                backend="mps",
                device_id=0,
            ))
    except (ImportError, Exception):
        pass
    return devices


def detect_all_gpus() -> List[GPUDevice]:
    """Обнаружить все доступные GPU/iGPU ускорители"""
    all_devices: List[GPUDevice] = []

    # NVIDIA CUDA
    cuda = _detect_cuda()
    # Проверяем, что это именно CUDA, а не ROCm
    rocm = _detect_rocm()
    if rocm:
        all_devices.extend(rocm)
    elif cuda:
        all_devices.extend(cuda)

    # Intel XPU (Arc / iGPU)
    all_devices.extend(_detect_xpu())

    # Intel OpenVINO (fallback для iGPU без IPEX)
    if not any(d.backend == "xpu" for d in all_devices):
        all_devices.extend(_detect_openvino())

    # Apple Metal
    all_devices.extend(_detect_mps())

    return all_devices


def get_best_device() -> GPUDevice:
    """Выбрать лучший доступный ускоритель.

    Приоритет: CUDA/ROCm > XPU > MPS > OpenVINO > CPU
    """
    devices = detect_all_gpus()

    if not devices:
        return GPUDevice(name="CPU", backend="cpu")

    # Сортируем: дискретные GPU первые (больше памяти = лучше)
    priority = {"cuda": 0, "rocm": 0, "xpu": 1, "mps": 2, "openvino": 3}
    devices.sort(key=lambda d: (priority.get(d.backend, 99), -d.memory_total))

    best = devices[0]
    logger.info(f"GPU selected: {best.name} ({best.backend})")
    return best


def get_torch_device_string() -> str:
    """Получить строку устройства PyTorch для использования в моделях"""
    from .config import NLP_CONFIG
    if not NLP_CONFIG.use_gpu:
        return "cpu"
    return get_best_device().torch_device


# Кешированный результат определения GPU
_cached_device: Optional[GPUDevice] = None
_cached_device_lock = threading.Lock()


def get_device(force_refresh: bool = False) -> GPUDevice:
    """Получить текущее устройство (кешированное).

    При первом вызове также применяет лимит GPU памяти.
    """
    global _cached_device
    if _cached_device is not None and not force_refresh:
        return _cached_device
    with _cached_device_lock:
        # Double-check inside lock
        if _cached_device is not None and not force_refresh:
            return _cached_device
        from .config import NLP_CONFIG
        if not NLP_CONFIG.use_gpu:
            _cached_device = GPUDevice(name="CPU", backend="cpu")
        else:
            _cached_device = get_best_device()

            # Применяем лимит GPU памяти при первом определении
            if _cached_device.backend in ("cuda", "rocm"):
                try:
                    import torch
                    fraction = NLP_CONFIG.gpu_memory_fraction
                    if 0 < fraction < 1.0:
                        for i in range(torch.cuda.device_count()):
                            torch.cuda.set_per_process_memory_fraction(fraction, i)
                            logger.info(
                                f"GPU {i}: memory limited to {fraction*100:.0f}%"
                            )
                except Exception as e:
                    logger.debug(f"Could not limit GPU memory: {e}")

        return _cached_device


def gpu_info_dict() -> dict:
    """Информация о GPU для вывода в статус"""
    devices = detect_all_gpus()
    active = get_device()
    return {
        "active_device": active.name,
        "active_backend": active.backend,
        "active_torch_device": active.torch_device,
        "all_devices": [
            {"name": d.name, "backend": d.backend, "memory_mb": d.memory_total}
            for d in devices
        ],
    }


__all__ = [
    'GPUDevice', 'detect_all_gpus', 'get_best_device',
    'get_torch_device_string', 'get_device', 'gpu_info_dict',
]
