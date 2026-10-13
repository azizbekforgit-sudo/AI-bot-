from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class Metric:
    """A single measurement. `value is None` means unavailable — never the same as zero."""

    value: float | None
    unit: str = ""
    source: str = ""
    reason: str = ""
    timestamp: float = field(default_factory=time.time)

    @property
    def available(self) -> bool:
        return self.value is not None

    @classmethod
    def missing(cls, reason: str, unit: str = "") -> "Metric":
        return cls(None, unit=unit, reason=reason)


@dataclass
class GpuInfo:
    name: str
    vendor: str
    utilization: Metric
    memory_used_gb: Metric
    memory_total_gb: Metric
    temperature: Metric


@dataclass
class DiskInfo:
    mount: str
    used_percent: float
    free_gb: float
    total_gb: float


@dataclass
class ProcessInfo:
    pid: int
    name: str
    cpu_percent: float
    memory_mb: float


@dataclass
class Snapshot:
    timestamp: float
    cpu_percent: Metric
    cpu_temperature: Metric
    cpu_name: str
    ram_used_gb: Metric
    ram_total_gb: Metric
    ram_percent: Metric
    gpus: list[GpuInfo]
    gpu_note: str
    disks: list[DiskInfo]
    battery_percent: Metric
    battery_plugged: bool | None
    battery_minutes_left: Metric
