"""Light / dark theme on top of the Fusion style."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication

_dark = False


def is_dark() -> bool:
    return _dark


def system_prefers_dark() -> bool:
    try:
        return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except Exception:
        pal = QGuiApplication.palette()
        return pal.color(QPalette.ColorRole.Window).lightness() < 128


def apply(app: QApplication, theme: str = "system") -> None:
    global _dark
    _dark = theme == "dark" or (theme == "system" and system_prefers_dark())
    app.setStyle("Fusion")
    pal = QPalette()
    if _dark:
        c = {
            "window": "#1b1f24", "base": "#14171b", "alt": "#1f242a", "text": "#e6e8eb",
            "button": "#242a31", "highlight": "#2f6fde", "mid": "#30363d", "tooltip": "#2a3038",
            "disabled": "#6b7280", "link": "#6ea8fe",
        }
    else:
        c = {
            "window": "#f4f6f8", "base": "#ffffff", "alt": "#f7f9fb", "text": "#1f2328",
            "button": "#ffffff", "highlight": "#2563eb", "mid": "#d0d7de", "tooltip": "#ffffff",
            "disabled": "#9aa4af", "link": "#1d4ed8",
        }
    R = QPalette.ColorRole
    pal.setColor(R.Window, QColor(c["window"]))
    pal.setColor(R.WindowText, QColor(c["text"]))
    pal.setColor(R.Base, QColor(c["base"]))
    pal.setColor(R.AlternateBase, QColor(c["alt"]))
    pal.setColor(R.Text, QColor(c["text"]))
    pal.setColor(R.Button, QColor(c["button"]))
    pal.setColor(R.ButtonText, QColor(c["text"]))
    pal.setColor(R.Highlight, QColor(c["highlight"]))
    pal.setColor(R.HighlightedText, QColor("#ffffff"))
    pal.setColor(R.ToolTipBase, QColor(c["tooltip"]))
    pal.setColor(R.ToolTipText, QColor(c["text"]))
    pal.setColor(R.Mid, QColor(c["mid"]))
    pal.setColor(R.Midlight, QColor(c["mid"]))
    pal.setColor(R.Link, QColor(c["link"]))
    pal.setColor(R.PlaceholderText, QColor(c["disabled"]))
    for role in (R.Text, R.WindowText, R.ButtonText):
        pal.setColor(QPalette.ColorGroup.Disabled, role, QColor(c["disabled"]))
    app.setPalette(pal)
    app.setStyleSheet(_qss(c))


def _qss(c: dict[str, str]) -> str:
    return f"""
    QTreeView {{ border: none; padding: 4px; outline: 0; }}
    QTreeView::item {{ padding: 3px 2px; border-radius: 5px; }}
    QTreeView::item:hover {{ background: {c['alt']}; }}
    QTreeView::item:selected {{ background: {c['highlight']}; color: white; }}
    QLineEdit#search {{ border: 1px solid {c['mid']}; border-radius: 8px; padding: 6px 10px;
                        background: {c['base']}; margin: 8px 8px 4px 8px; }}
    QToolBar {{ border: none; border-bottom: 1px solid {c['mid']}; spacing: 2px; padding: 3px 6px; }}
    QToolBar QToolButton {{ padding: 4px 8px; border-radius: 6px; }}
    QToolBar QToolButton:hover {{ background: {c['alt']}; }}
    QSplitter::handle {{ background: {c['mid']}; }}
    QWidget#sessionHeader, QWidget#paneHeader {{ background: {c['window']}; border-bottom: 1px solid {c['mid']}; }}
    QWidget#sessionHeader QPushButton {{ padding: 2px 12px; }}
    QWidget#sessionHeader QToolButton {{ padding: 2px 6px; }}
    QLabel#title {{ font-size: 13px; font-weight: 600; }}
    QLabel#subtitle {{ color: {c['disabled']}; }}
    QFrame#card {{ background: {c['base']}; border: 1px solid {c['mid']}; border-radius: 12px; }}
    QFrame#tile {{ background: {c['base']}; border: 1px solid {c['mid']}; border-radius: 10px; }}
    QFrame#tile[selected="true"] {{ border: 2px solid {c['highlight']}; }}
    QFrame#banner {{ background: #fef3c7; color: #78350f; border-bottom: 1px solid #f59e0b; }}
    QFrame#banner QLabel {{ color: #78350f; }}
    QFrame#fsbar {{ background: #1e293b; border-bottom-left-radius: 8px; border-bottom-right-radius: 8px; }}
    QFrame#fsbar QLabel {{ color: white; font-weight: 600; }}
    QFrame#fsbar QToolButton {{ color: white; background: transparent; border: none; padding: 3px 8px; border-radius: 5px; }}
    QFrame#fsbar QToolButton:hover, QFrame#fsbar QToolButton:checked {{ background: #334155; }}
    QPushButton {{ padding: 5px 14px; border-radius: 6px; border: 1px solid {c['mid']}; background: {c['button']}; }}
    QPushButton:hover {{ border-color: {c['highlight']}; }}
    QPushButton#primary {{ background: {c['highlight']}; color: white; border: none; }}
    QPushButton#primary:hover {{ background: {QColor(c['highlight']).lighter(112).name()}; }}
    QStatusBar {{ border-top: 1px solid {c['mid']}; }}
    QTabWidget::pane {{ border: 1px solid {c['mid']}; border-radius: 6px; top: -1px; }}
    """
