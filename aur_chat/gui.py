"""aur-chat: Kirigami UI + system tray."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from PyQt6.QtCore import QLockFile, QRectF, QSize, Qt, QUrl, pyqtSlot
from PyQt6.QtGui import QAction, QBrush, QColor, QFont, QIcon, QPainter, QPixmap
from PyQt6.QtQml import QQmlApplicationEngine
from PyQt6.QtQuick import QQuickWindow
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from .backend import AureliaBackend
from .session_env import ensure_aurelia_session_env

_PKG = Path(__file__).resolve().parent
_ICON = _PKG.parent / "share" / "icons" / "hicolor" / "scalable" / "apps" / "aur-chat.svg"


def _icon() -> QIcon:
    if _ICON.is_file():
        return QIcon(str(_ICON))
    return QIcon.fromTheme("internet-chat", QIcon.fromTheme("user-available"))


def _badge(p: QPainter, size: float, corner_x: float, corner_y: float, color: str, text: str):
    r = QRectF(corner_x, corner_y, size, size)
    p.setPen(QColor("white"))
    p.setBrush(QBrush(QColor(color)))
    p.drawEllipse(r)
    font = QFont()
    font.setBold(True)
    font.setPixelSize(int(size * 0.6))
    p.setFont(font)
    p.drawText(r, Qt.AlignmentFlag.AlignCenter, text)


def _status_icon(base: QIcon, unread: int, downloading: int) -> QIcon:
    if not unread and not downloading:
        return base
    pm = base.pixmap(QSize(64, 64))
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    d = pm.width() * 0.5
    if downloading:  # blue down-arrow badge, bottom-left
        _badge(p, d, 0, pm.height() - d, "#1c71d8", "\u2193")
    if unread:  # red unread count, bottom-right
        _badge(p, d, pm.width() - d, pm.height() - d, "#e01b24", "9+" if unread > 9 else str(unread))
    p.end()
    return QIcon(pm)


def _acquire_instance_lock() -> QLockFile | None:
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir())
    path = runtime / f"aur-chat-{getattr(os, 'getuid', lambda: 0)()}.lock"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        lock = QLockFile(str(path))
        lock.setStaleLockTime(1000)
        return lock if lock.tryLock(0) else None
    except OSError:
        return None


class _TrayHost(QApplication):
    def __init__(self, argv):
        super().__init__(argv)
        self._backend = AureliaBackend()
        self._root: QQuickWindow | None = None
        self._tray = QSystemTrayIcon(_icon())
        self._tray.setToolTip("aur-chat")
        self._tray_games_requested = False
        self._tray_menu = QMenu()
        self._tray_menu.addAction("Show", self.show_window)
        self._tray_status = self._tray_menu.addAction("Status: checking presence…")
        self._tray_status.setEnabled(False)
        self._tray_menu.addSeparator()
        self._tray_game_end = self._tray_menu.addSeparator()
        self._tray_game_actions: list[QAction] = []
        self._tray_menu.aboutToShow.connect(self._ensure_tray_games)
        self._backend.gamesListChanged.connect(self._update_tray_games)
        self._backend.presenceChanged.connect(self._update_presence)
        self._backend.unreadChanged.connect(self._update_unread)
        self._backend.updatingChanged.connect(self._update_unread)
        self._update_presence()
        self._update_unread()
        self._update_tray_games()
        self._tray_menu.addAction("Refresh games", self._refresh_tray_games)
        self._tray_menu.addAction("Refresh", self._backend.refreshFriends)
        self._tray_menu.addSeparator()
        self._tray_menu.addAction("Quit", self.quit)
        self._tray.setContextMenu(self._tray_menu)
        self._tray.activated.connect(self._on_tray)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self._tray.show()

    def set_root(self, root):
        self._root = root

    def _update_presence(self):
        text = self._backend.presenceText
        self._tray_status.setText(f"Status: {text}")
        self._update_unread()

    def _update_unread(self):
        n, d = self._backend.unreadTotal, self._backend.updatingCount
        self._tray.setIcon(_status_icon(_icon(), n, d))
        parts = [f"{n} unread"] if n else []
        if d:
            parts.append(f"{d} downloading")
        tip = " · ".join(["aur-chat", *parts, self._backend.presenceText])
        self._tray.setToolTip(tip)

    def _ensure_tray_games(self):
        if self._tray_games_requested or self._backend.gameItems:
            return
        self._tray_games_requested = True
        self._backend.refreshGames("")

    def _refresh_tray_games(self):
        self._tray_games_requested = True
        self._backend.refreshGames("")

    def _update_tray_games(self):
        for action in self._tray_game_actions:
            self._tray_menu.removeAction(action)
            action.deleteLater()
        self._tray_game_actions.clear()
        games = [game for game in (self._backend.gameItems or []) if game.get("installed")]
        if not games:
            empty = QAction(
                "No installed games" if self._tray_games_requested else "Loading games…",
                self._tray_menu,
            )
            empty.setEnabled(False)
            self._tray_menu.insertAction(self._tray_game_end, empty)
            self._tray_game_actions.append(empty)
        else:
            for game in games:
                app_id = str(game.get("appId", ""))
                action = QAction(game.get("name") or app_id, self._tray_menu)
                action.triggered.connect(
                    lambda _checked=False, app_id=app_id: self._backend.playGame(app_id)
                )
                self._tray_menu.insertAction(self._tray_game_end, action)
                self._tray_game_actions.append(action)

    @pyqtSlot()
    def show_window(self):
        if not self._root:
            return
        self._root.show()
        self._root.raise_()
        self._root.requestActivate()
        self._backend.setWindowVisible(True)

    def _on_tray(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.show_window()


def main():
    ensure_aurelia_session_env()
    lock = _acquire_instance_lock()
    if lock is None:
        print("aur-chat is already running", file=sys.stderr)
        raise SystemExit(1)
    os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "org.kde.desktop")
    app = _TrayHost(sys.argv)
    app._instance_lock = lock
    app.aboutToQuit.connect(lock.unlock)
    backend = app._backend
    app.setApplicationName("aur-chat")
    app.setDesktopFileName("aur-chat")
    app.setWindowIcon(_icon())
    app.setQuitOnLastWindowClosed(False)

    engine = QQmlApplicationEngine()
    engine.addImportPath(str(_PKG / "qml"))
    engine.rootContext().setContextProperty("backend", backend)
    engine.rootContext().setContextProperty("appShell", app)
    engine.rootContext().setContextProperty(
        "trayOk", QSystemTrayIcon.isSystemTrayAvailable()
    )
    engine.load(QUrl.fromLocalFile(str(_PKG / "qml" / "Main.qml")))
    if not engine.rootObjects():
        print("Failed to load QML", file=sys.stderr)
        sys.exit(1)

    root = engine.rootObjects()[0]
    app.set_root(root)
    app.aboutToQuit.connect(backend.shutdown)
    sys.exit(app.exec())
