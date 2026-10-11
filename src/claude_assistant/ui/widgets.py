from __future__ import annotations

import math

from PySide6.QtCore import QElapsedTimer, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap, QRadialGradient
from PySide6.QtWidgets import QFrame, QLabel, QProgressBar, QSizePolicy, QVBoxLayout, QWidget

_ANIMATED = {"listening", "working", "speaking"}


class StatusOrb(QWidget):
    """Assistant state indicator. Animates only while active, so it costs nothing when idle."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(QSize(64, 64))
        self._color = QColor("#888888")
        self._state = "idle"
        self._clock = QElapsedTimer()
        self._clock.start()
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self.update)
        self.setAccessibleName("Индикатор состояния ассистента")

    def set_state(self, state: str, color: str) -> None:
        self._state = state
        self._color = QColor(color)
        if state in _ANIMATED and self.isVisible():
            self._timer.start()
        else:
            self._timer.stop()
        self.update()

    def hideEvent(self, event) -> None:  # stop animating when minimized to tray
        self._timer.stop()
        super().hideEvent(event)

    def showEvent(self, event) -> None:
        if self._state in _ANIMATED:
            self._timer.start()
        super().showEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect())
        center = rect.center()
        base = min(rect.width(), rect.height()) / 2 - 4
        phase = 0.0
        if self._state in _ANIMATED:
            speed = {"listening": 2.2, "working": 1.2, "speaking": 3.0}[self._state]
            phase = (math.sin(self._clock.elapsed() / 1000 * speed * math.pi) + 1) / 2
        halo = QColor(self._color)
        halo.setAlphaF(0.12 + 0.18 * phase)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(halo)
        painter.drawEllipse(center, base, base)
        core = base * (0.55 + 0.08 * phase)
        gradient = QRadialGradient(center, core)
        gradient.setColorAt(0, self._color.lighter(130))
        gradient.setColorAt(1, self._color)
        painter.setBrush(gradient)
        painter.drawEllipse(center, core, core)


class MetricCard(QFrame):
    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(6)
        self.title = QLabel(title, objectName="CardTitle")
        self.value = QLabel("—", objectName="CardValue")
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setFixedHeight(8)
        self.bar.setTextVisible(False)
        self.sub = QLabel("", objectName="CardSub")
        self.sub.setWordWrap(True)
        for w in (self.title, self.value, self.bar, self.sub):
            layout.addWidget(w)

    def set(self, value: str, percent: float | None, sub: str, tooltip: str = "") -> None:
        self.value.setText(value)
        self.bar.setVisible(percent is not None)
        if percent is not None:
            self.bar.setValue(int(max(0, min(100, percent))))
        self.sub.setText(sub)
        self.setToolTip(tooltip)


def app_icon(color: str = "#D97757", size: int = 64) -> QIcon:
    """Generated icon (no binary assets in the repo): a rounded square with a soft ring."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#1A1D24"))
    painter.drawRoundedRect(QRectF(2, 2, size - 4, size - 4), size * 0.22, size * 0.22)
    ring = QColor(color)
    painter.setBrush(ring)
    painter.drawEllipse(QRectF(size * 0.25, size * 0.25, size * 0.5, size * 0.5))
    painter.setBrush(QColor("#1A1D24"))
    painter.drawEllipse(QRectF(size * 0.37, size * 0.37, size * 0.26, size * 0.26))
    painter.end()
    return QIcon(pixmap)
