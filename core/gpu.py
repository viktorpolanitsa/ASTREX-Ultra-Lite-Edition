#!/usr/bin/env python3
"""
ASTREX v3.0 — GPU Detection & Management
Обнаружение и управление GPU/iGPU ускорением
"""

import importlib.util
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
    memory_free: int = 0   # MB (0 — неизвестно)

    @property
    def torch_device(self) -> str:
        """Строка устройства для PyTorch (OpenVINO для torch недоступен → cpu)."""
        if self.backend in ("cuda", "rocm"):
            return f"cuda:{self.device_id}"  # ROCm использует CUDA API в PyTorch
        if self.backend == "xpu":
            return f"xpu:{self.device_id}"
        if self.backend == "mps":
            return "mps"
        return "cpu"

    @property
    def usable_by_torch(self) -> bool:
        return self.backend in ("cuda", "rocm", "xpu", "mps")


def _torch_installed() -> bool:
    return importlib.util.find_spec("torch") is not None


def _detect_torch_cuda() -> List[GPUDevice]:
    """NVIDIA CUDA или AMD ROCm (ROCm-сборки PyTorch тоже отвечают через torch.cuda).

    Используются только свойства устройства — без mem_get_info(), который
    создаёт CUDA-контекст (~300 МБ видеопамяти) ради одной строки статуса.
    """
    devices: List[GPUDevice] = []
    try:
        import torch
        if not torch.cuda.is_available():
            return devices
        backend = "rocm" if getattr(torch.version, 'hip', None) else "cuda"
        for i in range(torch.cuda.device_count()):
            props = torch.cuda.get_device_properties(i)
            devices.append(GPUDevice(
                name=props.name,
                backend=backend,
                device_id=i,
                memory_total=int(props.total_memory) // (1024 * 1024),
            ))
    except Exception as e:
        logger.debug(f"CUDA/ROCm detection failed: {e}")
    return devices


def _detect_xpu() -> List[GPUDevice]:
    """Intel Arc/iGPU (XPU): нативно в PyTorch ≥ 2.4 или через IPEX."""
    devices: List[GPUDevice] = []
    try:
        import torch
        if not (hasattr(torch, 'xpu') and torch.xpu.is_available()):
            try:
                import intel_extension_for_pytorch  # noqa: F401  (регистрирует torch.xpu)
            except ImportError:
                return devices
            if not (hasattr(torch, 'xpu') and torch.xpu.is_available()):
                return devices
        for i in range(torch.xpu.device_count()):
            name = torch.xpu.get_device_name(i)
            try:
                props = torch.xpu.get_device_properties(i)
                total = int(getattr(props, 'total_memory', 0)) // (1024 * 1024)
            except Exception:
                total = 0
            devices.append(GPUDevice(name=name, backend="xpu", device_id=i, memory_total=total))
    except Exception as e:
        logger.debug(f"XPU detection failed: {e}")
    return devices


def _detect_openvino() -> List[GPUDevice]:
    """Intel GPU через OpenVINO (информационно: PyTorch его не использует)."""
    devices: List[GPUDevice] = []
    if importlib.util.find_spec("openvino") is None:
        return devices
    try:
        try:
            from openvino import Core
        except ImportError:
            from openvino.runtime import Core

        core = Core()
        for idx, dev in enumerate(core.available_devices):
            if dev.startswith("GPU"):
                name = core.get_property(dev, "FULL_DEVICE_NAME")
                devices.append(GPUDevice(name=name, backend="openvino", device_id=idx))
    except Exception as e:
        logger.debug(f"OpenVINO detection failed: {e}")
    return devices


def _detect_mps() -> List[GPUDevice]:
    """Обнаружение Apple Metal (macOS)"""
    devices: List[GPUDevice] = []
    try:
        import torch
        if hasattr(torch.backends, 'mps') and torch.backends.mps.is_available():
            devices.append(GPUDevice(name="Apple Metal GPU", backend="mps"))
    except Exception as e:
        logger.debug(f"MPS detection failed: {e}")
    return devices


_detected: Optional[List[GPUDevice]] = None
_detect_lock = threading.Lock()


def detect_all_gpus(force_refresh: bool = False) -> List[GPUDevice]:
    """Обнаружить все доступные GPU/iGPU ускорители (результат кешируется)."""
    global _detected
    with _detect_lock:
        if _detected is not None and not force_refresh:
            return list(_detected)

        devices: List[GPUDevice] = []
        if _torch_installed():
            devices.extend(_detect_torch_cuda())
            devices.extend(_detect_xpu())
            devices.extend(_detect_mps())
        devices.extend(_detect_openvino())
        _detected = devices
        return list(devices)


def get_best_device() -> GPUDevice:
    """Выбрать лучший ускоритель, который реально может использовать PyTorch.

    Приоритет: CUDA/ROCm > XPU > MPS > CPU. OpenVINO-устройства показываются
    в статусе, но вычисления моделей (sentence-transformers) идут на CPU.
    """
    from .config import NLP_CONFIG

    devices = [d for d in detect_all_gpus() if d.usable_by_torch]
    backend = (NLP_CONFIG.gpu_backend or "auto").lower()
    if backend not in ("auto", ""):
        if backend == "cpu":
            return GPUDevice(name="CPU", backend="cpu")
        devices = [d for d in devices if d.backend == backend]

    if not devices:
        return GPUDevice(name="CPU", backend="cpu")

    priority = {"cuda": 0, "rocm": 0, "xpu": 1, "mps": 2}
    devices.sort(key=lambda d: (priority.get(d.backend, 99), -d.memory_total))

    best = devices[0]
    logger.info(f"GPU selected: {best.name} ({best.backend})")
    return best


def get_torch_device_string() -> str:
    """Получить строку устройства PyTorch для использования в моделях"""
    return get_device().torch_device


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
        if _cached_device is not None and not force_refresh:
            return _cached_device
        from .config import NLP_CONFIG
        if not NLP_CONFIG.use_gpu or not _torch_installed():
            _cached_device = GPUDevice(name="CPU", backend="cpu")
        else:
            _cached_device = get_best_device()

            # Применяем лимит GPU памяти при первом определении
            if _cached_device.backend in ("cuda", "rocm"):
                try:
                    import torch
                    fraction = NLP_CONFIG.gpu_memory_fraction
                    if 0 < fraction < 1.0:
                        torch.cuda.set_per_process_memory_fraction(fraction, _cached_device.device_id)
                        logger.info(
                            f"GPU {_cached_device.device_id}: memory limited to {fraction*100:.0f}%"
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
            {"name": d.name, "backend": d.backend, "memory_mb": d.memory_total,
             "usable_by_torch": d.usable_by_torch}
            for d in devices
        ],
    }


__all__ = [
    'GPUDevice', 'detect_all_gpus', 'get_best_device',
    'get_torch_device_string', 'get_device', 'gpu_info_dict',
]
