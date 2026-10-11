"""Live hardware dashboard. Polls only while visible to keep idle CPU usage near zero."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView, QGridLayout, QHeaderView, QLabel, QScrollArea, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from claude_assistant.core.controller import Controller
from claude_assistant.monitor.metrics import Metric, Snapshot
from claude_assistant.ui.widgets import MetricCard


def _fmt(metric: Metric, digits: int = 0) -> str:
    if not metric.available:
        return "н/д"
    return f"{metric.value:.{digits}f}".replace(".", ",")


def _source(metric: Metric) -> str:
    if not metric.available:
        return metric.reason
    return f"Источник: {metric.source} · {datetime.fromtimestamp(metric.timestamp):%H:%M:%S}"


class HardwarePage(QWidget):
    def __init__(self, controller: Controller) -> None:
        super().__init__()
        self.c = controller
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll)
        body = QWidget()
        scroll.setWidget(body)
        root = QVBoxLayout(body)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(14)

        self.note = QLabel("Значения обновляются, пока открыта эта страница.", objectName="Muted")
        self.note.setWordWrap(True)
        root.addWidget(self.note)

        self.grid = QGridLayout()
        self.grid.setSpacing(12)
        self.cpu = MetricCard("ПРОЦЕССОР")
        self.cpu_temp = MetricCard("ТЕМПЕРАТУРА ПРОЦЕССОРА")
        self.ram = MetricCard("ОПЕРАТИВНАЯ ПАМЯТЬ")
        self.battery = MetricCard("БАТАРЕЯ")
        self.disks = MetricCard("ДИСКИ")
        self.gpu_cards: list[MetricCard] = []
        self._layout_cards()
        root.addLayout(self.grid)

        root.addWidget(QLabel("ПРОЦЕССЫ (по загрузке процессора)", objectName="CardTitle"))
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Процесс", "PID", "CPU, %", "Память, МБ"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setMinimumHeight(260)
        root.addWidget(self.table)
        root.addStretch(1)

        self.timer = QTimer(self)
        self.timer.timeout.connect(lambda: self.c.request_snapshot(with_processes=True))
        self.c.snapshot_ready.connect(self._on_snapshot)

    def _layout_cards(self) -> None:
        cards = [self.cpu, self.cpu_temp, self.ram, *self.gpu_cards, self.battery, self.disks]
        for i, card in enumerate(cards):
            self.grid.addWidget(card, i // 2, i % 2, Qt.AlignmentFlag.AlignTop)

    def showEvent(self, event) -> None:
        self.timer.start(int(self.c.config.system.monitor_interval_s * 1000))
        self.c.request_snapshot(with_processes=True)
        super().showEvent(event)

    def hideEvent(self, event) -> None:
        self.timer.stop()
        super().hideEvent(event)

    def _on_snapshot(self, payload) -> None:
        snap, procs = payload
        self._show(snap)
        if procs is not None:
            self.table.setRowCount(len(procs))
            for row, p in enumerate(procs):
                for col, value in enumerate([p.name, str(p.pid), f"{p.cpu_percent:.1f}", f"{p.memory_mb:.0f}"]):
                    self.table.setItem(row, col, QTableWidgetItem(value))

    def _show(self, s: Snapshot) -> None:
        self.cpu.set(f"{_fmt(s.cpu_percent)}%", s.cpu_percent.value, s.cpu_name)
        t = s.cpu_temperature
        self.cpu_temp.set(f"{_fmt(t)} °C" if t.available else "н/д", None, _source(t), t.reason)
        self.ram.set(f"{_fmt(s.ram_used_gb, 1)} / {_fmt(s.ram_total_gb, 1)} ГБ", s.ram_percent.value,
                     f"Занято {_fmt(s.ram_percent)}%")

        if len(self.gpu_cards) != max(1, len(s.gpus)):
            for card in self.gpu_cards:
                self.grid.removeWidget(card)
                card.deleteLater()
            self.gpu_cards = [MetricCard("ВИДЕОКАРТА") for _ in range(max(1, len(s.gpus)))]
            for card in (self.battery, self.disks):
                self.grid.removeWidget(card)
            self._layout_cards()
        if not s.gpus:
            self.gpu_cards[0].set("н/д", None, s.gpu_note, s.gpu_note)
        for card, gpu in zip(self.gpu_cards, s.gpus):
            mem = (f"VRAM {_fmt(gpu.memory_used_gb, 1)} / {_fmt(gpu.memory_total_gb, 1)} ГБ"
                   if gpu.memory_total_gb.available else
                   (f"VRAM занято {_fmt(gpu.memory_used_gb, 1)} ГБ" if gpu.memory_used_gb.available else "VRAM н/д"))
            temp = f"{_fmt(gpu.temperature)} °C" if gpu.temperature.available else "температура н/д"
            card.title.setText(f"ВИДЕОКАРТА · {gpu.name}")
            card.set(f"{_fmt(gpu.utilization)}%" if gpu.utilization.available else "н/д", gpu.utilization.value,
                     f"{mem} · {temp}\n{_source(gpu.utilization)}",
                     "\n".join(m.reason for m in (gpu.utilization, gpu.memory_used_gb, gpu.memory_total_gb,
                                                  gpu.temperature) if not m.available))

        b = s.battery_percent
        if b.available:
            state = "заряжается" if s.battery_plugged else "от батареи"
            left = (f" · ~{int(s.battery_minutes_left.value or 0) // 60} ч {int(s.battery_minutes_left.value or 0) % 60} мин"
                    if s.battery_minutes_left.available and not s.battery_plugged else "")
            self.battery.set(f"{_fmt(b)}%", b.value, f"{state}{left}")
        else:
            self.battery.set("н/д", None, b.reason)

        if s.disks:
            main = max(s.disks, key=lambda d: d.total_gb)
            lines = [f"{d.mount}  свободно {d.free_gb:.0f} из {d.total_gb:.0f} ГБ" for d in s.disks[:5]]
            self.disks.set(f"{main.used_percent:.0f}% занято", main.used_percent, "\n".join(lines))
        else:
            self.disks.set("н/д", None, "Нет данных о дисках.")
