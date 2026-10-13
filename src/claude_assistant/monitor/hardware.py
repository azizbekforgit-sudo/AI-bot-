"""System telemetry with honest handling of unavailable sensors."""

from __future__ import annotations

import logging
import platform
import sys
import threading
import time

import psutil

from claude_assistant.monitor.gpu import open_gpu_backend
from claude_assistant.monitor.metrics import DiskInfo, Metric, ProcessInfo, Snapshot

log = logging.getLogger(__name__)
_GB = 1024**3


def _cpu_name() -> str:
    if sys.platform == "win32":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
                return str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
        except OSError:
            pass
    else:
        try:
            with open("/proc/cpuinfo", encoding="utf-8") as fh:
                for line in fh:
                    if line.lower().startswith("model name"):
                        return line.split(":", 1)[1].strip()
        except OSError:
            pass
    return platform.processor() or "Процессор"


class _CpuTemperature:
    """CPU temperature sources, best first. Windows has no standard user-level CPU sensor API."""

    CACHE_S = 10.0

    def __init__(self) -> None:
        self._cached: Metric | None = None
        self._at = 0.0

    def read(self) -> Metric:
        if self._cached and time.monotonic() - self._at < self.CACHE_S:
            return self._cached
        self._cached = self._read()
        self._at = time.monotonic()
        return self._cached

    def _read(self) -> Metric:
        if hasattr(psutil, "sensors_temperatures"):
            try:
                temps = psutil.sensors_temperatures() or {}
            except Exception:
                temps = {}
            for key in ("coretemp", "k10temp", "zenpower", "cpu_thermal", "acpitz"):
                entries = temps.get(key)
                if entries:
                    return Metric(max(e.current for e in entries), "°C", f"psutil/{key}")
        if sys.platform == "win32":
            return self._read_windows()
        return Metric.missing("Датчик температуры процессора не найден.", "°C")

    def _read_windows(self) -> Metric:
        try:
            import pythoncom
            import win32com.client
        except ImportError:
            return Metric.missing("pywin32 не установлен.", "°C")
        pythoncom.CoInitialize()
        try:
            for namespace, label in (("root\\LibreHardwareMonitor", "LibreHardwareMonitor"),
                                     ("root\\OpenHardwareMonitor", "OpenHardwareMonitor")):
                try:
                    wmi = win32com.client.GetObject(f"winmgmts:{namespace}")
                    sensors = wmi.ExecQuery("SELECT Name, Value, Parent FROM Sensor WHERE SensorType='Temperature'")
                    values = [float(s.Value) for s in sensors
                              if "cpu" in str(s.Parent).lower() or "package" in str(s.Name).lower()]
                    if values:
                        return Metric(max(values), "°C", label)
                except Exception:
                    continue
            try:
                wmi = win32com.client.GetObject("winmgmts:root\\WMI")
                zones = wmi.ExecQuery("SELECT CurrentTemperature FROM MSAcpi_ThermalZoneTemperature")
                values = [float(z.CurrentTemperature) / 10.0 - 273.15 for z in zones]
                values = [v for v in values if 0 < v < 125]
                if values:
                    return Metric(round(max(values), 1), "°C",
                                  "ACPI thermal zone (температура платы, может отличаться от ядер)")
            except Exception:
                pass
        finally:
            pythoncom.CoUninitialize()
        return Metric.missing(
            "Windows не даёт температуру процессора без прав администратора или утилиты мониторинга. "
            "Запустите LibreHardwareMonitor — ассистент подхватит его датчики.", "°C")


class HardwareMonitor:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._gpu_backend, self._gpu_note = open_gpu_backend()
        self._cpu_temp = _CpuTemperature()
        self.cpu_name = _cpu_name()
        psutil.cpu_percent(None)  # prime the non-blocking measurement
        self._cpu_primed_at = time.monotonic()
        self._procs: dict[int, psutil.Process] = {}
        self._last: Snapshot | None = None

    @property
    def last(self) -> Snapshot | None:
        return self._last

    def snapshot(self) -> Snapshot:
        with self._lock:
            now = time.time()
            # A non-blocking reading right after priming covers ~0 s and is meaningless; sample briefly instead.
            if time.monotonic() - self._cpu_primed_at < 0.5:
                cpu_value = psutil.cpu_percent(interval=0.5)
            else:
                cpu_value = psutil.cpu_percent(None)
            cpu = Metric(cpu_value, "%", "psutil", timestamp=now)
            vm = psutil.virtual_memory()
            gpus = []
            note = self._gpu_note
            if self._gpu_backend is not None:
                try:
                    gpus = self._gpu_backend.read()  # type: ignore[attr-defined]
                    if not gpus:
                        note = "Видеокарта не сообщает данных."
                except Exception as exc:
                    log.warning("GPU telemetry failed: %s", exc)
                    note = f"Ошибка чтения телеметрии видеокарты: {exc}"
            disks = []
            for part in psutil.disk_partitions(all=False):
                if "cdrom" in part.opts or not part.fstype:
                    continue
                try:
                    usage = psutil.disk_usage(part.mountpoint)
                except (OSError, PermissionError):
                    continue
                if usage.total < _GB:  # pseudo/virtual mounts
                    continue
                disks.append(DiskInfo(part.mountpoint, usage.percent, usage.free / _GB, usage.total / _GB))
            try:
                battery = psutil.sensors_battery()
            except Exception:
                battery = None
            if battery is None:
                batt_pct = Metric.missing("Батарея не обнаружена (стационарный ПК или нет данных).", "%")
                plugged = None
                minutes = Metric.missing("Нет батареи.", "мин")
            else:
                batt_pct = Metric(float(battery.percent), "%", "psutil", timestamp=now)
                plugged = battery.power_plugged
                secs = battery.secsleft
                minutes = (Metric(secs / 60, "мин", "psutil", timestamp=now)
                           if isinstance(secs, (int, float)) and secs > 0 and secs != psutil.POWER_TIME_UNLIMITED
                           else Metric.missing("Время работы не оценивается.", "мин"))
            self._last = Snapshot(
                timestamp=now,
                cpu_percent=cpu,
                cpu_temperature=self._cpu_temp.read(),
                cpu_name=self.cpu_name,
                ram_used_gb=Metric((vm.total - vm.available) / _GB, "ГБ", "psutil", timestamp=now),
                ram_total_gb=Metric(vm.total / _GB, "ГБ", "psutil", timestamp=now),
                ram_percent=Metric(vm.percent, "%", "psutil", timestamp=now),
                gpus=gpus,
                gpu_note=note,
                disks=disks,
                battery_percent=batt_pct,
                battery_plugged=plugged,
                battery_minutes_left=minutes,
            )
            return self._last

    def top_processes(self, limit: int = 8) -> list[ProcessInfo]:
        """CPU% needs two samples; the first call after start reports 0 for new processes."""
        with self._lock:
            seen = set()
            rows = []
            cores = psutil.cpu_count() or 1
            for proc in psutil.process_iter(["pid", "name", "memory_info"]):
                pid = proc.info["pid"]
                seen.add(pid)
                cached = self._procs.setdefault(pid, proc)
                try:
                    cpu = cached.cpu_percent(None) / cores
                    mem = (proc.info["memory_info"].rss if proc.info["memory_info"] else 0) / 1024**2
                except psutil.Error:
                    continue
                if pid == 0:
                    continue  # System Idle Process
                rows.append(ProcessInfo(pid, proc.info["name"] or "?", cpu, mem))
            for pid in list(self._procs):
                if pid not in seen:
                    del self._procs[pid]
            rows.sort(key=lambda r: (r.cpu_percent, r.memory_mb), reverse=True)
            return rows[:limit]

    def close(self) -> None:
        if self._gpu_backend is not None:
            self._gpu_backend.close()  # type: ignore[attr-defined]


# ---- spoken summaries -------------------------------------------------------

def _num(value: float, digits: int = 1) -> str:
    text = f"{value:.{digits}f}".rstrip("0").rstrip(".") if digits else f"{value:.0f}"
    return text.replace(".", ",")


def describe_cpu(s: Snapshot) -> str:
    parts = [f"Процессор загружен на {_num(s.cpu_percent.value or 0, 0)}%."]
    if s.cpu_temperature.available:
        parts.append(f"Температура {_num(s.cpu_temperature.value or 0, 0)} градусов по данным {s.cpu_temperature.source}.")
    else:
        parts.append(f"Температура недоступна: {s.cpu_temperature.reason}")
    return " ".join(parts)


def describe_gpu(s: Snapshot) -> str:
    if not s.gpus:
        return s.gpu_note or "Телеметрия видеокарты недоступна."
    phrases = []
    for gpu in s.gpus:
        p = [f"{gpu.name}:"]
        p.append(f"загрузка {_num(gpu.utilization.value or 0, 0)}%." if gpu.utilization.available
                 else "загрузка недоступна.")
        if gpu.memory_used_gb.available and gpu.memory_total_gb.available:
            p.append(f"Видеопамять: {_num(gpu.memory_used_gb.value or 0)} из {_num(gpu.memory_total_gb.value or 0)} ГБ.")
        elif gpu.memory_used_gb.available:
            p.append(f"Занято видеопамяти: {_num(gpu.memory_used_gb.value or 0)} ГБ.")
        if gpu.temperature.available:
            p.append(f"Температура {_num(gpu.temperature.value or 0, 0)} градусов по данным датчика.")
        else:
            p.append("Температура недоступна.")
        phrases.append(" ".join(p))
    return " ".join(phrases)


def describe_ram(s: Snapshot) -> str:
    return (f"Оперативная память: занято {_num(s.ram_used_gb.value or 0)} из {_num(s.ram_total_gb.value or 0)} ГБ, "
            f"это {_num(s.ram_percent.value or 0, 0)}%.")


def describe_battery(s: Snapshot) -> str:
    if not s.battery_percent.available:
        return s.battery_percent.reason
    state = "заряжается" if s.battery_plugged else "работает от батареи"
    text = f"Батарея {_num(s.battery_percent.value or 0, 0)}%, {state}."
    if s.battery_minutes_left.available and not s.battery_plugged:
        minutes = int(s.battery_minutes_left.value or 0)
        text += f" Осталось примерно {minutes // 60} ч {minutes % 60} мин."
    return text


def describe_disks(s: Snapshot) -> str:
    if not s.disks:
        return "Не удалось получить данные о дисках."
    return " ".join(f"Диск {d.mount}: свободно {_num(d.free_gb, 0)} из {_num(d.total_gb, 0)} ГБ." for d in s.disks[:4])


def describe_all(s: Snapshot) -> str:
    return " ".join([describe_cpu(s), describe_ram(s), describe_gpu(s), describe_battery(s)])
