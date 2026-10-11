import json
import time

import pytest

from claude_assistant import logging_setup
from claude_assistant.memory import MAX_ITEMS, MemoryStore
from claude_assistant.monitor import hardware
from claude_assistant.monitor.metrics import GpuInfo, Metric, Snapshot


# ---- memory -------------------------------------------------------------------------

def test_memory_crud_and_persistence(tmp_path):
    path = tmp_path / "memory.json"
    store = MemoryStore(path)
    item = store.add("Я люблю короткие ответы")
    store.add("Мой проект: D:\\code\\bot")
    store.update(item.id, "Я люблю очень короткие ответы")
    assert [i.text for i in MemoryStore(path).items()] == ["Я люблю очень короткие ответы", "Мой проект: D:\\code\\bot"]
    assert store.forget_matching("проект") == 1
    assert store.delete(item.id) and not store.items()
    assert not store.delete("missing")


def test_memory_export_and_clear(tmp_path):
    store = MemoryStore(tmp_path / "m.json")
    store.add("заметка")
    target = tmp_path / "export.json"
    store.export(target)
    assert json.loads(target.read_text(encoding="utf-8"))["items"][0]["text"] == "заметка"
    store.clear()
    assert store.items() == [] and store.as_prompt_context() == ""


def test_memory_prompt_context_only_has_saved_items(tmp_path):
    store = MemoryStore(tmp_path / "m.json")
    store.add("зовут Азиз")
    assert store.as_prompt_context().endswith("- зовут Азиз")


def test_memory_recovers_from_corruption(tmp_path):
    path = tmp_path / "m.json"
    path.write_text("{broken", encoding="utf-8")
    store = MemoryStore(path)
    assert store.load_error and store.items() == []
    assert (tmp_path / "m.broken.json").exists()
    store.add("новая")  # still usable
    assert len(MemoryStore(path).items()) == 1


def test_memory_limits(tmp_path):
    store = MemoryStore(tmp_path / "m.json")
    with pytest.raises(ValueError):
        store.add("   ")
    assert len(store.add("x" * 2000).text) == 500
    for i in range(MAX_ITEMS - 1):
        store.add(f"{i}")
    with pytest.raises(ValueError):
        store.add("переполнение")


# ---- logging redaction ----------------------------------------------------------------

@pytest.mark.parametrize("secret", [
    "sk-ant-api03-AbCdEfGhIjKlMnOpQrStUv", "Bearer eyJhbGciOiJIUzI1NiJ9.abc.def", "api_key=supersecret123",
    "token: ghp_abcdefghijklmnopqrstuvwxyz012345", "password=hunter2",
])
def test_redaction(secret):
    cleaned = logging_setup.redact(f"request with {secret} done")
    assert "***" in cleaned
    for fragment in ("AbCdEfGh", "eyJhbGci", "supersecret", "ghp_abcdef", "hunter2"):
        assert fragment not in cleaned


# ---- hardware -------------------------------------------------------------------------

def test_snapshot_real_machine():
    monitor = hardware.HardwareMonitor()
    snap = monitor.snapshot()
    assert 0 <= snap.cpu_percent.value <= 100
    assert snap.ram_total_gb.value > 0 and snap.ram_used_gb.value <= snap.ram_total_gb.value
    assert snap.cpu_percent.source == "psutil"
    # Each optional sensor is either a real reading or carries an explanation.
    for metric in (snap.cpu_temperature, snap.battery_percent):
        assert metric.available or metric.reason
    if not snap.gpus:
        assert snap.gpu_note
    assert isinstance(monitor.top_processes(), list)
    monitor.close()


def test_missing_metric_is_not_zero():
    m = Metric.missing("нет датчика", "°C")
    assert not m.available and m.value is None
    assert Metric(0.0, "%", "x").available  # zero utilisation is a real value


def _snapshot(gpus, note=""):
    now = time.time()
    return Snapshot(
        timestamp=now, cpu_percent=Metric(12, "%", "psutil"), cpu_temperature=Metric.missing("нет датчика", "°C"),
        cpu_name="CPU", ram_used_gb=Metric(6.2, "ГБ", "psutil"), ram_total_gb=Metric(16, "ГБ", "psutil"),
        ram_percent=Metric(39, "%", "psutil"), gpus=gpus, gpu_note=note, disks=[],
        battery_percent=Metric(81, "%", "psutil"), battery_plugged=False, battery_minutes_left=Metric(95, "мин", "psutil"),
    )


def test_describe_gpu_with_live_values():
    gpu = GpuInfo("NVIDIA GeForce RTX 3060 Laptop GPU", "NVIDIA", Metric(42, "%", "NVML"), Metric(3.2, "ГБ", "NVML"),
                  Metric(8, "ГБ", "NVML"), Metric(58, "°C", "NVML"))
    text = hardware.describe_gpu(_snapshot([gpu]))
    assert "загрузка 42%" in text and "3,2 из 8 ГБ" in text and "58 градусов" in text


def test_describe_gpu_unavailable_temperature_is_explicit():
    gpu = GpuInfo("Intel Iris Xe", "Intel", Metric(5, "%", "PDH"), Metric(0.4, "ГБ", "PDH"),
                  Metric.missing("нет", "ГБ"), Metric.missing("нет датчика", "°C"))
    text = hardware.describe_gpu(_snapshot([gpu]))
    assert "Температура недоступна" in text
    assert "0 градусов" not in text


def test_describe_without_gpu_uses_note():
    assert hardware.describe_gpu(_snapshot([], "Телеметрия видеокарты недоступна (NVML)")).startswith("Телеметрия")


def test_describe_cpu_and_battery():
    snap = _snapshot([])
    assert "Температура недоступна: нет датчика" in hardware.describe_cpu(snap)
    assert "81%" in hardware.describe_battery(snap) and "1 ч 35 мин" in hardware.describe_battery(snap)


def test_gpu_backend_failure_is_reported(monkeypatch):
    class Broken:
        def read(self):
            raise RuntimeError("driver crashed")

        def close(self):
            pass

    monkeypatch.setattr(hardware, "open_gpu_backend", lambda: (Broken(), ""))
    snap = hardware.HardwareMonitor().snapshot()
    assert snap.gpus == [] and "driver crashed" in snap.gpu_note
