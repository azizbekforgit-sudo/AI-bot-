"""GPU telemetry backends: NVIDIA NVML first, then Windows performance counters (AMD/Intel/any WDDM GPU)."""

from __future__ import annotations

import logging
import re
import sys
import time
from collections import defaultdict

from claude_assistant.monitor.metrics import GpuInfo, Metric

log = logging.getLogger(__name__)
_GB = 1024**3


class NvmlBackend:
    name = "NVIDIA NVML"

    def __init__(self) -> None:
        import pynvml  # provided by nvidia-ml-py

        self._nv = pynvml
        pynvml.nvmlInit()
        self._handles = [pynvml.nvmlDeviceGetHandleByIndex(i) for i in range(pynvml.nvmlDeviceGetCount())]
        if not self._handles:
            raise RuntimeError("NVML: нет устройств")

    def read(self) -> list[GpuInfo]:
        nv = self._nv
        result = []
        for handle in self._handles:
            name = nv.nvmlDeviceGetName(handle)
            name = name.decode() if isinstance(name, bytes) else name
            now = time.time()
            try:
                util = Metric(float(nv.nvmlDeviceGetUtilizationRates(handle).gpu), "%", self.name, timestamp=now)
            except nv.NVMLError as exc:
                util = Metric.missing(f"NVML не отдаёт загрузку: {exc}", "%")
            try:
                mem = nv.nvmlDeviceGetMemoryInfo(handle)
                used = Metric(mem.used / _GB, "ГБ", self.name, timestamp=now)
                total = Metric(mem.total / _GB, "ГБ", self.name, timestamp=now)
            except nv.NVMLError as exc:
                used = Metric.missing(f"NVML не отдаёт память: {exc}", "ГБ")
                total = Metric.missing(str(exc), "ГБ")
            try:
                temp = Metric(float(nv.nvmlDeviceGetTemperature(handle, nv.NVML_TEMPERATURE_GPU)), "°C", self.name,
                              timestamp=now)
            except nv.NVMLError as exc:
                temp = Metric.missing(f"Датчик температуры недоступен: {exc}", "°C")
            result.append(GpuInfo(name, "NVIDIA", util, used, total, temp))
        return result

    def close(self) -> None:
        try:
            self._nv.nvmlShutdown()
        except Exception:
            pass


class WindowsPdhBackend:
    """Uses the same 'GPU Engine' / 'GPU Adapter Memory' counters as Task Manager (Windows 10 1709+)."""

    name = "Счётчики производительности Windows"
    _CLASS_KEY = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"

    def __init__(self) -> None:
        import win32pdh  # pywin32

        self._pdh = win32pdh
        self._query = win32pdh.OpenQuery()
        self._engine = self._add_wildcard(r"\GPU Engine(*)\Utilization Percentage")
        self._memory = self._add_wildcard(r"\GPU Adapter Memory(*)\Dedicated Usage")
        if not self._engine:
            raise RuntimeError("Счётчики GPU Engine недоступны")
        win32pdh.CollectQueryData(self._query)
        self._adapters = self._read_adapters()

    def _add_wildcard(self, path: str) -> dict[str, int]:
        pdh = self._pdh
        counters = {}
        try:
            for expanded in pdh.ExpandCounterPath(path):
                counters[expanded] = pdh.AddCounter(self._query, expanded)
        except pdh.error as exc:
            log.debug("PDH path %s unavailable: %s", path, exc)
        return counters

    def _read_adapters(self) -> list[tuple[str, float | None]]:
        """(name, dedicated VRAM bytes) from the display-adapter class key; readable without admin rights."""
        import winreg

        adapters = []
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, self._CLASS_KEY) as root:
                for i in range(64):
                    try:
                        sub = winreg.EnumKey(root, i)
                    except OSError:
                        break
                    if not re.fullmatch(r"\d{4}", sub):
                        continue
                    try:
                        with winreg.OpenKey(root, sub) as key:
                            name = winreg.QueryValueEx(key, "DriverDesc")[0]
                            try:
                                size = winreg.QueryValueEx(key, "HardwareInformation.qwMemorySize")[0]
                                size = float(int.from_bytes(size, "little")) if isinstance(size, bytes) else float(size)
                            except OSError:
                                size = None
                            if "basic display" not in name.lower() and "remote" not in name.lower():
                                adapters.append((name, size))
                    except OSError:
                        continue
        except OSError as exc:
            log.debug("Display adapter registry unavailable: %s", exc)
        return adapters

    def _values(self, counters: dict[str, int]) -> dict[str, float]:
        pdh = self._pdh
        values = {}
        for path, handle in counters.items():
            try:
                values[path] = float(pdh.GetFormattedCounterValue(handle, pdh.PDH_FMT_DOUBLE)[1])
            except pdh.error:
                continue  # engine instances come and go with processes
        return values

    def read(self) -> list[GpuInfo]:
        self._pdh.CollectQueryData(self._query)
        now = time.time()
        # Task Manager semantics: per adapter, take the busiest engine type (sum over processes).
        per_adapter: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        for path, value in self._values(self._engine).items():
            luid = re.search(r"luid_(0x[0-9a-f]+_0x[0-9a-f]+)", path, re.I)
            engtype = re.search(r"engtype_([^)]+)", path, re.I)
            if luid and engtype:
                per_adapter[luid.group(1).lower()][engtype.group(1)] += value
        mem_per_adapter: dict[str, float] = {}
        for path, value in self._values(self._memory).items():
            luid = re.search(r"luid_(0x[0-9a-f]+_0x[0-9a-f]+)", path, re.I)
            if luid:
                mem_per_adapter[luid.group(1).lower()] = mem_per_adapter.get(luid.group(1).lower(), 0.0) + value

        luids = sorted(per_adapter, key=lambda k: -mem_per_adapter.get(k, 0.0))
        # Counters identify adapters by LUID only. A name/VRAM pairing is reported only
        # when it is unambiguous (one adapter), never guessed.
        single = len(luids) == 1 and len(self._adapters) == 1
        result = []
        for index, luid in enumerate(luids):
            name, vram = self._adapters[0] if single else (f"Видеокарта {index + 1}", None)
            busiest = min(100.0, max(per_adapter[luid].values(), default=0.0))
            used_bytes = mem_per_adapter.get(luid)
            vendor = next((v for v in ("NVIDIA", "AMD", "Intel") if v.lower() in name.lower()), "")
            result.append(GpuInfo(
                name=name,
                vendor=vendor,
                utilization=Metric(busiest, "%", self.name, timestamp=now),
                memory_used_gb=Metric(used_bytes / _GB, "ГБ", self.name, timestamp=now) if used_bytes is not None
                else Metric.missing("Счётчик видеопамяти недоступен", "ГБ"),
                memory_total_gb=Metric(vram / _GB, "ГБ", "Реестр драйвера", timestamp=now) if vram
                else Metric.missing("Объём видеопамяти не удалось определить однозначно", "ГБ"),
                temperature=Metric.missing(
                    "Windows не отдаёт температуру этой видеокарты без утилиты производителя", "°C"),
            ))
        return result

    def close(self) -> None:
        try:
            self._pdh.CloseQuery(self._query)
        except Exception:
            pass


def open_gpu_backend() -> tuple[object | None, str]:
    """Best available backend and a human-readable note about what was tried."""
    notes = []
    try:
        return NvmlBackend(), ""
    except Exception as exc:  # no NVIDIA driver, no device, or library missing
        notes.append(f"NVIDIA NVML: {exc.__class__.__name__}")
    if sys.platform == "win32":
        try:
            return WindowsPdhBackend(), ""
        except Exception as exc:
            notes.append(f"счётчики Windows: {exc}")
    return None, "Телеметрия видеокарты недоступна (" + "; ".join(notes) + ")."
