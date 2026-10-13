"""Colour tokens and the application stylesheet (dark by default, optional light)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Palette:
    bg: str
    surface: str
    surface2: str
    border: str
    text: str
    muted: str
    accent: str
    accent_text: str
    success: str
    warning: str
    danger: str
    listening: str


DARK = Palette(
    bg="#111318", surface="#181B22", surface2="#20242D", border="#2B303B", text="#E7E9EE", muted="#9BA2AF",
    accent="#D97757", accent_text="#FFFFFF", success="#4DB884", warning="#E3A63F", danger="#E5534B",
    listening="#5B9BFF",
)
LIGHT = Palette(
    bg="#F5F4F1", surface="#FFFFFF", surface2="#EFEEEA", border="#DCDAD4", text="#1D1E22", muted="#666B75",
    accent="#C4623F", accent_text="#FFFFFF", success="#25875A", warning="#A86F0B", danger="#C93A33",
    listening="#2F6FDB",
)


def palette(name: str) -> Palette:
    return LIGHT if name == "light" else DARK


def state_color(p: Palette, state: str) -> str:
    return {
        "idle": p.success, "listening": p.listening, "working": p.accent, "speaking": p.accent,
        "offline": p.muted, "stopped": p.danger,
    }.get(state, p.muted)


def stylesheet(p: Palette) -> str:
    return f"""
    QWidget {{ background: {p.bg}; color: {p.text}; font-size: 10pt; }}
    QMainWindow, QDialog {{ background: {p.bg}; }}
    QToolTip {{ background: {p.surface2}; color: {p.text}; border: 1px solid {p.border}; padding: 6px; }}
    #Sidebar {{ background: {p.surface}; border: none; border-right: 1px solid {p.border}; outline: 0; padding-top: 8px; }}
    #Sidebar::item {{ padding: 10px 16px; margin: 2px 8px; border-radius: 8px; color: {p.muted}; }}
    #Sidebar::item:selected {{ background: {p.surface2}; color: {p.text}; }}
    #Sidebar::item:hover:!selected {{ color: {p.text}; }}
    #Card, QGroupBox {{ background: {p.surface}; border: 1px solid {p.border}; border-radius: 12px; }}
    QGroupBox {{ margin-top: 14px; padding: 16px 12px 12px 12px; font-weight: 600; }}
    QGroupBox::title {{ subcontrol-origin: margin; left: 14px; padding: 0 4px; color: {p.muted}; }}
    #Card QLabel, QGroupBox QLabel, QGroupBox QCheckBox {{ background: transparent; }}
    #StateTitle {{ font-size: 16pt; font-weight: 600; background: transparent; }}
    #Muted, #CardSub {{ color: {p.muted}; background: transparent; }}
    #CardTitle {{ color: {p.muted}; font-weight: 600; background: transparent; }}
    #CardValue {{ font-size: 18pt; font-weight: 600; background: transparent; }}
    #Chip {{ background: {p.surface2}; border: 1px solid {p.border}; border-radius: 11px; padding: 3px 10px; color: {p.muted}; }}
    #ChipLive {{ background: {p.surface2}; border: 1px solid {p.listening}; border-radius: 11px; padding: 3px 10px; color: {p.listening}; }}
    #ChipOff {{ background: {p.surface2}; border: 1px solid {p.danger}; border-radius: 11px; padding: 3px 10px; color: {p.danger}; }}
    QPushButton {{ background: {p.surface2}; border: 1px solid {p.border}; border-radius: 8px; padding: 7px 14px; }}
    QPushButton:hover {{ border-color: {p.muted}; }}
    QPushButton:pressed {{ background: {p.border}; }}
    QPushButton:disabled {{ color: {p.muted}; border-color: {p.surface2}; }}
    QPushButton:checked {{ border-color: {p.danger}; color: {p.danger}; }}
    #Primary {{ background: {p.accent}; color: {p.accent_text}; border: none; font-weight: 600; }}
    #Primary:hover {{ background: {p.accent}; border: 1px solid {p.text}; }}
    #Danger {{ background: {p.danger}; color: #FFFFFF; border: none; font-weight: 600; }}
    #Danger:hover {{ border: 1px solid {p.text}; }}
    QLineEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
        background: {p.surface2}; border: 1px solid {p.border}; border-radius: 8px; padding: 6px 8px;
        selection-background-color: {p.accent}; }}
    QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QComboBox:focus {{ border-color: {p.accent}; }}
    #CommandInput {{ padding: 10px 12px; font-size: 11pt; border-radius: 10px; }}
    QComboBox QAbstractItemView {{ background: {p.surface}; border: 1px solid {p.border}; selection-background-color: {p.surface2}; }}
    QTextBrowser, QListWidget, QTableWidget {{ background: {p.surface}; border: 1px solid {p.border}; border-radius: 12px; padding: 6px; }}
    QListWidget::item {{ padding: 4px 2px; }}
    QListWidget::item:selected {{ background: {p.surface2}; color: {p.text}; }}
    QHeaderView::section {{ background: {p.surface}; color: {p.muted}; border: none; border-bottom: 1px solid {p.border}; padding: 6px; }}
    QTableWidget {{ gridline-color: {p.border}; }}
    QProgressBar {{ background: {p.surface2}; border: none; border-radius: 4px; height: 8px; text-align: center; color: transparent; }}
    QProgressBar::chunk {{ background: {p.accent}; border-radius: 4px; }}
    #Banner {{ background: {p.surface}; border: 1px solid {p.warning}; border-radius: 12px; }}
    #Banner QLabel {{ background: transparent; }}
    QScrollArea {{ border: none; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: {p.border}; border-radius: 4px; min-height: 30px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
    QSlider::groove:horizontal {{ height: 4px; background: {p.border}; border-radius: 2px; }}
    QSlider::handle:horizontal {{ background: {p.accent}; width: 14px; margin: -6px 0; border-radius: 7px; }}
    QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 4px; border: 1px solid {p.border}; background: {p.surface2}; }}
    QCheckBox::indicator:checked {{ background: {p.accent}; border-color: {p.accent}; }}
    QSplitter::handle {{ background: transparent; }}
    QMenu {{ background: {p.surface}; border: 1px solid {p.border}; padding: 4px; }}
    QMenu::item {{ padding: 6px 18px; border-radius: 6px; }}
    QMenu::item:selected {{ background: {p.surface2}; }}
    """
