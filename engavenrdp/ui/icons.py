"""Programmatically painted icons (no image assets to ship)."""
from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF

from ..core.session import State

STATE_COLORS = {
    State.CONNECTED: "#22a55a",
    State.CONNECTING: "#e0a100",
    State.ERROR: "#d64545",
    State.DISCONNECTED: None,
}

ACCENT = "#3b82f6"


def _canvas(size: int = 32) -> tuple[QPixmap, QPainter]:
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    return pm, p


def _dot(p: QPainter, color: str, size: int = 32) -> None:
    r = size * 0.21
    c = QPointF(size - r - 1, size - r - 1)
    p.setPen(QPen(QColor("white"), 2))
    p.setBrush(QColor(color))
    p.drawEllipse(c, r, r)


@lru_cache(maxsize=64)
def server_icon(state: State = State.DISCONNECTED, dark: bool = False, favorite: bool = False) -> QIcon:
    pm, p = _canvas()
    fg = QColor("#cbd5e1" if dark else "#475569")
    screen = QColor("#1e293b" if dark else "#e2e8f0")
    if state == State.CONNECTED:
        screen = QColor("#14532d" if dark else "#bbf7d0")
    p.setPen(QPen(fg, 2))
    p.setBrush(screen)
    p.drawRoundedRect(QRectF(3, 5, 24, 16), 2.5, 2.5)
    p.drawLine(QPointF(15, 21), QPointF(15, 25))
    p.drawLine(QPointF(9, 26), QPointF(21, 26))
    col = STATE_COLORS.get(state)
    if col:
        _dot(p, col)
    if favorite:
        _star(p, QColor("#f5b301"), QPointF(7, 7), 6)
    p.end()
    return QIcon(pm)


def _star(p: QPainter, color: QColor, c: QPointF, r: float) -> None:
    import math

    pts = []
    for i in range(10):
        ang = -math.pi / 2 + i * math.pi / 5
        rr = r if i % 2 == 0 else r * 0.45
        pts.append(QPointF(c.x() + rr * math.cos(ang), c.y() + rr * math.sin(ang)))
    p.setPen(QPen(QColor("#a16207"), 1))
    p.setBrush(color)
    p.drawPolygon(QPolygonF(pts))


@lru_cache(maxsize=16)
def folder_icon(dark: bool = False, smart: bool = False, open_: bool = False) -> QIcon:
    pm, p = _canvas()
    body = QColor("#eab308" if not smart else "#8b5cf6")
    tab = body.darker(115)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(tab)
    p.drawRoundedRect(QRectF(3, 6, 11, 6), 2, 2)
    p.setBrush(body)
    p.drawRoundedRect(QRectF(3, 9, 26, 18), 2.5, 2.5)
    if smart:
        p.setPen(QPen(QColor("white"), 2))
        path = QPainterPath()
        path.moveTo(17, 12)
        path.lineTo(13, 19)
        path.lineTo(17, 19)
        path.lineTo(14, 25)
        p.drawPath(path)
    p.end()
    return QIcon(pm)


@lru_cache(maxsize=8)
def file_icon(dark: bool = False, locked: bool = False, dirty: bool = False) -> QIcon:
    pm, p = _canvas()
    p.setPen(QPen(QColor("#94a3b8" if dark else "#64748b"), 1.6))
    p.setBrush(QColor("#334155" if dark else "#f8fafc"))
    path = QPainterPath()
    path.moveTo(7, 3)
    path.lineTo(20, 3)
    path.lineTo(26, 9)
    path.lineTo(26, 29)
    path.lineTo(7, 29)
    path.closeSubpath()
    p.drawPath(path)
    p.setPen(QPen(QColor(ACCENT), 2))
    for y in (13, 18, 23):
        p.drawLine(QPointF(11, y), QPointF(22, y))
    if locked:
        p.setPen(QPen(QColor("#b45309"), 2))
        p.setBrush(QColor("#f59e0b"))
        p.drawRoundedRect(QRectF(17, 19, 12, 10), 2, 2)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(QRectF(19, 14, 8, 9), 0, 180 * 16)
    elif dirty:
        _dot(p, ACCENT)
    p.end()
    return QIcon(pm)


@lru_cache(maxsize=16)
def virtual_icon(kind: str, dark: bool = False) -> QIcon:
    pm, p = _canvas()
    fg = QColor("#cbd5e1" if dark else "#475569")
    if kind == "favorites":
        _star(p, QColor("#f5b301"), QPointF(16, 16), 13)
    elif kind == "connected":
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(STATE_COLORS[State.CONNECTED]))
        p.drawEllipse(QPointF(16, 16), 11, 11)
        p.setPen(QPen(QColor("white"), 2.5))
        p.drawLine(QPointF(10, 16), QPointF(22, 16))
        p.drawLine(QPointF(18, 12), QPointF(22, 16))
        p.drawLine(QPointF(18, 20), QPointF(22, 16))
    elif kind == "recent":
        p.setPen(QPen(fg, 2.2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QPointF(16, 16), 11, 11)
        p.drawLine(QPointF(16, 16), QPointF(16, 9))
        p.drawLine(QPointF(16, 16), QPointF(21, 19))
    elif kind == "reconnect":
        p.setPen(QPen(QColor("#e0a100"), 2.6))
        p.drawArc(QRectF(6, 6, 20, 20), 40 * 16, 280 * 16)
        p.setBrush(QColor("#e0a100"))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawPolygon(QPolygonF([QPointF(20, 3), QPointF(27, 9), QPointF(19, 11)]))
    p.end()
    return QIcon(pm)


def dot_pixmap(state: State, size: int = 10) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(STATE_COLORS.get(state) or "#94a3b8"))
    p.drawEllipse(QRectF(0.5, 0.5, size - 1, size - 1))
    p.end()
    return pm


@lru_cache(maxsize=1)
def app_icon() -> QIcon:
    icon = QIcon()
    for size in (16, 32, 48, 64, 128, 256):
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        s = size / 64
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#1d4ed8"))
        p.drawRoundedRect(QRectF(2 * s, 2 * s, 60 * s, 60 * s), 14 * s, 14 * s)
        for i, (x, y) in enumerate(((12, 14), (34, 14), (12, 34), (34, 34))):
            p.setBrush(QColor("#dbeafe" if i != 3 else "#4ade80"))
            p.drawRoundedRect(QRectF(x * s, y * s, 18 * s, 14 * s), 2.5 * s, 2.5 * s)
        p.end()
        icon.addPixmap(pm)
    return icon
