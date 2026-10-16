from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from PyQt6.QtCore import (
    QAbstractListModel,
    QModelIndex,
    QObject,
    QProcess,
    Qt,
    QThread,
    QTimer,
    pyqtProperty,
    pyqtSignal,
    pyqtSlot,
)
from PyQt6.QtGui import QGuiApplication, QImage

from . import aurelia
from .chat_stream import ChatStream
from .notify import desktop_notify
from . import launch_options
from . import steam_shortcuts


def _clipboard_image_path() -> str | None:
    mime = QGuiApplication.clipboard().mimeData()
    if not mime or not mime.hasImage():
        return None
    image = QGuiApplication.clipboard().image()
    if image.isNull():
        return None
    fd, path = tempfile.mkstemp(prefix="aur-chat-", suffix=".png")
    os.close(fd)
    if image.save(path, "PNG"):
        return path
    os.unlink(path)
    return None


def _upload_status(data: Any) -> str:
    if isinstance(data, dict) and data.get("url") and not data.get("error"):
        return "Image sent"
    if isinstance(data, dict):
        return str(data.get("error") or "image upload failed")[:160]
    return "image upload failed"


def _social_snapshot() -> dict[str, Any]:
    friends = aurelia.friends()
    profile = None if aurelia.auth_required(friends) else aurelia.own_profile()
    return {"friends": friends, "profile": profile}


def _persona_state(friend: dict) -> int:
    try:
        return int(friend.get("persona_state") or 0)
    except (TypeError, ValueError):
        return 0


def _friend_name(friend: dict, names: dict[str, str]) -> str:
    steam_id = str(friend.get("steam_id", ""))
    return str(friend.get("persona_name") or names.get(steam_id) or steam_id)


_STATUS_COLORS = {
    0: "#8b949e",
    1: "#3fb950",
    2: "#f85149",
    3: "#d29922",
    4: "#a371f7",
    5: "#58a6ff",
    6: "#2dd4bf",
    7: "#8b949e",
}

_PRESENCE_MODES = (
    ("online", "Online", 1),
    ("busy", "Busy", 2),
    ("away", "Away", 3),
    ("snooze", "Snooze", 4),
    ("looking-to-trade", "Looking to trade", 5),
    ("looking-to-play", "Looking to play", 6),
    ("offline", "Invisible", 7),
)
_PRESENCE_STATES = {key: state for key, _, state in _PRESENCE_MODES}
_PRESENCE_LABELS = {key: label for key, label, _ in _PRESENCE_MODES}
_PRESENCE_STATE_MODES = {state: key for key, _, state in _PRESENCE_MODES}

_DEVICE_TYPES = (
    (512, "Mobile", "📱"),
    (2048, "VR", "🥽"),
    (1024, "TV", "▣"),
    (256, "Web", "🌐"),
)


def _persona_flags(friend: dict) -> int | None:
    value = friend.get("persona_state_flags")
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _device_info(friend: dict) -> tuple[str, str]:
    flags = _persona_flags(friend)
    if flags is None or not 0 < _persona_state(friend) < 7:
        return "", ""
    for bit, label, icon in _DEVICE_TYPES:
        if flags & bit:
            return label, icon
    return "PC", "🖥"


def _lookup_friend_names(steam_ids: list[str]) -> dict[str, str]:
    def lookup(steam_id: str) -> tuple[str, str]:
        try:
            profile = aurelia.user_profile(steam_id)
            name = profile.get("persona_name") if isinstance(profile, dict) else ""
            return steam_id, str(name or "").strip()
        except Exception:
            return steam_id, ""

    with ThreadPoolExecutor(max_workers=6) as pool:
        return {steam_id: name for steam_id, name in pool.map(lookup, steam_ids) if name}


def _friend_sort_key(friend: dict):
    return (-_persona_state(friend), (friend.get("persona_name") or "").lower())


def _format_timestamp(value: Any) -> str:
    try:
        return datetime.fromtimestamp(int(value)).strftime("%H:%M")
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


def _image_url(text: Any) -> str:
    value = str(text or "").strip()
    if value.startswith("[img]") and value.endswith("[/img]"):
        value = value[5:-6].strip()
    try:
        parsed = urlparse(value)
        host = parsed.hostname
    except ValueError:
        return ""
    if (
        parsed.scheme != "https"
        or host not in {"images.steamusercontent.com", "steamuserimages-a.akamaihd.net"}
        or not parsed.path.startswith("/ugc/")
    ):
        return ""
    return value


def _download_image(url: str) -> QImage:
    request = Request(url, headers={"User-Agent": "aur-chat"})
    with urlopen(request, timeout=30) as response:
        image = QImage()
        if not image.loadFromData(response.read()):
            raise ValueError("downloaded file is not an image")
        return image


_STEAM_CONTENT_LOG = Path.home() / ".steam/debian-installation/logs/content_log.txt"
_STEAM_STATE = re.compile(r"\[([\d-]+ [\d:]+)\] AppID (\d+) state changed : (.*)")


def _steam_update_pending(app_ids: set[str]) -> set[str]:
    """Apps whose latest Steam log state says Update Required and is newer than their app manifest."""
    try:
        last: dict[str, tuple[str, str]] = {}
        with _STEAM_CONTENT_LOG.open(errors="replace") as f:
            for line in f:
                if m := _STEAM_STATE.search(line):
                    last[m.group(2)] = (m.group(1), m.group(3))
    except OSError:
        return set()
    steamapps = _STEAM_CONTENT_LOG.parent.parent / "steamapps"

    def pending(app_id: str) -> bool:
        if app_id not in last or "Update Required" not in last[app_id][1]:
            return False
        try:  # Steam's log goes stale when Steam is closed; a newer manifest means it's been updated
            manifest = (steamapps / f"appmanifest_{app_id}.acf").stat().st_mtime
        except OSError:
            return True
        return datetime.strptime(last[app_id][0], "%Y-%m-%d %H:%M:%S").timestamp() > manifest

    return {a for a in app_ids if pending(a)}


class _Fetch(QThread):
    done = pyqtSignal(object)

    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self._fn, self._args, self._kwargs = fn, args, kwargs

    def run(self):
        try:
            result = self._fn(*self._args, **self._kwargs)
        except Exception as error:
            result = {"error": str(error)[:160]}
        self.done.emit(result)


class FriendModel(QAbstractListModel):
    SteamIdRole = Qt.ItemDataRole.UserRole + 1
    NameRole = Qt.ItemDataRole.UserRole + 2
    StatusRole = Qt.ItemDataRole.UserRole + 3
    GameRole = Qt.ItemDataRole.UserRole + 4
    OnlineRole = Qt.ItemDataRole.UserRole + 5
    DeviceRole = Qt.ItemDataRole.UserRole + 6
    DeviceIconRole = Qt.ItemDataRole.UserRole + 7

    def __init__(self):
        super().__init__()
        self._rows: list[dict] = []

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._rows)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self._rows):
            return None
        f = self._rows[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return f.get("persona_name") or str(f.get("steam_id", ""))
        if role == self.SteamIdRole:
            return str(f.get("steam_id", ""))
        if role == self.NameRole:
            return f.get("persona_name") or ""
        if role == self.StatusRole:
            return aurelia.persona_label(_persona_state(f))
        if role == self.GameRole:
            return f.get("game_name") or ""
        if role == self.OnlineRole:
            return _persona_state(f) > 0
        if role in (self.DeviceRole, self.DeviceIconRole):
            device, icon = _device_info(f)
            return device if role == self.DeviceRole else icon
        return None

    def roleNames(self):
        return {
            Qt.ItemDataRole.DisplayRole: b"display",
            self.SteamIdRole: b"steamId",
            self.NameRole: b"name",
            self.StatusRole: b"status",
            self.GameRole: b"game",
            self.OnlineRole: b"online",
            self.DeviceRole: b"device",
            self.DeviceIconRole: b"deviceIcon",
        }

    def set_rows(self, rows: list[dict]):
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def name_for(self, steam_id: str) -> str:
        for f in self._rows:
            if str(f.get("steam_id")) == steam_id:
                return f.get("persona_name") or steam_id
        return steam_id

    def rows(self) -> list[dict]:
        return self._rows


class MessageModel(QAbstractListModel):
    TextRole = Qt.ItemDataRole.UserRole + 1
    FromSelfRole = Qt.ItemDataRole.UserRole + 2
    TimeRole = Qt.ItemDataRole.UserRole + 3

    def __init__(self):
        super().__init__()
        self._rows: list[dict] = []

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._rows)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self._rows):
            return None
        m = self._rows[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return m.get("text", "")
        if role == self.TextRole:
            return m.get("text", "")
        if role == self.FromSelfRole:
            return bool(m.get("from_self"))
        if role == self.TimeRole:
            return m.get("timestamp")
        return None

    def roleNames(self):
        return {
            Qt.ItemDataRole.DisplayRole: b"display",
            self.TextRole: b"text",
            self.FromSelfRole: b"fromSelf",
            self.TimeRole: b"timestamp",
        }

    def clear(self):
        if not self._rows:
            return
        self.beginResetModel()
        self._rows = []
        self.endResetModel()

    def append(self, text: str, from_self: bool, timestamp: Any = None):
        self.beginInsertRows(QModelIndex(), len(self._rows), len(self._rows))
        self._rows.append({"text": text, "from_self": from_self, "timestamp": timestamp})
        self.endInsertRows()

    def load_history(self, rows: list[dict]):
        self.beginResetModel()
        ordered = sorted(
            rows,
            key=lambda r: (r.get("timestamp") is None, r.get("timestamp") or 0),
        )
        self._rows = [
            {
                "text": r.get("message", ""),
                "from_self": bool(r.get("from_self")),
                "timestamp": r.get("timestamp"),
            }
            for r in ordered
        ]
        self.endResetModel()


class AureliaBackend(QObject):
    authRequiredChanged = pyqtSignal()
    statusTextChanged = pyqtSignal()
    activeFriendChanged = pyqtSignal()
    searchResultChanged = pyqtSignal()
    busyChanged = pyqtSignal()
    friendsListChanged = pyqtSignal()
    messagesChanged = pyqtSignal()
    gamesListChanged = pyqtSignal()
    gamesStatusChanged = pyqtSignal()
    launchOptionsChanged = pyqtSignal()
    presenceChanged = pyqtSignal()
    presenceModeChanged = pyqtSignal()
    unreadChanged = pyqtSignal()
    updatingChanged = pyqtSignal()
    _progressSig = pyqtSignal(str, float, float, str)

    def __init__(self):
        super().__init__()
        self._auth_required = False
        self._status = "Ready"
        self._presence = "Checking presence…"
        self._presence_mode = "offline"
        self._presence_pin_until = 0.0  # ignore stale profile reads right after a change
        self._steam_game = ""
        self._steam_app_id = ""
        self._local_app_id = ""
        self._stop_worker: _Fetch | None = None
        self._stop_force = False
        self._local_game = ""
        self._local_game_process: Any = None
        self._local_log: Any = None
        self._local_started = 0.0
        self._active_id = ""
        self._active_name = ""
        self._search_result: dict | None = None
        self._busy = False
        self._worker: _Fetch | None = None
        self._upload_worker: _Fetch | None = None
        self._copy_worker: _Fetch | None = None
        self._stream: ChatStream | None = None
        self._watch: dict[str, ChatStream] = {}
        self._window_visible = True
        self._minimize_to_tray = True
        self._friends_empty_retries = 0
        self._friend_items: list[dict] = []
        self._friend_names: dict[str, str] = {}
        self._unread: dict[str, int] = {}
        self._last_online: dict[str, bool] | None = None
        self._friend_filter = ""
        self._name_worker: _Fetch | None = None
        self._message_items: list[dict] = []
        self._game_items: list[dict] = []
        self._games_status = ""
        self._updates: set[str] = set()
        self._updating: set[str] = set()
        self._progress: dict[str, tuple] = {}
        self._progressSig.connect(self._set_progress)
        self._updates_checked = False
        self._check_worker: _Fetch | None = None
        self._update_worker: _Fetch | None = None
        self._games_search = ""
        self._games_installed_only = True
        self._games_include_shortcuts = True
        self._launch_open = False
        self._launch_name = ""
        self._launch_text = ""
        self._launch_app_id = ""
        self._launch_is_shortcut = False

        self.friends = FriendModel()
        self.messages = MessageModel()

        self._poll = QTimer(self)
        self._poll.setInterval(20_000)
        self._poll.timeout.connect(self.refreshFriends)
        self._poll.start()
        self._game_poll = QTimer(self)
        self._game_poll.setInterval(2_000)
        self._game_poll.timeout.connect(self._poll_local_game)
        self._game_poll.start()
        QTimer.singleShot(0, self.refreshFriends)

    @pyqtProperty(bool, notify=authRequiredChanged)
    def authRequired(self):
        return self._auth_required

    @pyqtProperty(str, notify=statusTextChanged)
    def statusText(self):
        return self._status

    @pyqtProperty(str, notify=presenceChanged)
    def presenceText(self):
        return self._presence

    @pyqtProperty(int, notify=updatingChanged)
    def updatingCount(self):
        return len(self._updating)

    @pyqtProperty(int, notify=unreadChanged)
    def unreadTotal(self):
        return sum(self._unread.values())

    @pyqtProperty(str, notify=presenceModeChanged)
    def presenceMode(self):
        return self._presence_mode

    @pyqtProperty("QStringList", constant=True)
    def presenceModes(self):
        return [key for key, _, _ in _PRESENCE_MODES]

    @pyqtProperty("QStringList", constant=True)
    def presenceOptions(self):
        return [label for _, label, _ in _PRESENCE_MODES]

    @pyqtProperty(str, notify=activeFriendChanged)
    def activeFriendId(self):
        return self._active_id

    @pyqtProperty(str, notify=activeFriendChanged)
    def activeFriendName(self):
        return self._active_name

    @pyqtProperty(bool, notify=busyChanged)
    def busy(self):
        return self._busy

    @pyqtProperty("QVariant", notify=searchResultChanged)
    def searchResult(self):
        return self._search_result

    @pyqtProperty("QVariantList", notify=friendsListChanged)
    def friendItems(self):
        return self._friend_items

    @pyqtProperty("QVariantList", notify=messagesChanged)
    def messageItems(self):
        return self._message_items

    @pyqtProperty("QVariantList", notify=gamesListChanged)
    def gameItems(self):
        return self._game_items

    @pyqtProperty(str, notify=gamesStatusChanged)
    def gamesStatus(self):
        return self._games_status

    @pyqtProperty(bool, notify=launchOptionsChanged)
    def launchOptionsOpen(self):
        return self._launch_open

    @pyqtProperty(str, notify=launchOptionsChanged)
    def launchOptionsName(self):
        return self._launch_name

    @pyqtProperty(str, notify=launchOptionsChanged)
    def launchOptionsText(self):
        return self._launch_text

    def _set_games_status(self, text: str):
        self._games_status = text
        self.gamesStatusChanged.emit()

    def _sync_friend_items(self, rows: list[dict]):
        self.unreadChanged.emit()
        query = self._friend_filter.lower()
        items = []
        for f in rows:
            steam_id = str(f.get("steam_id", ""))
            name = _friend_name(f, self._friend_names)
            game = f.get("game_name") or ""
            device, device_icon = _device_info(f)
            if query and query not in f"{name} {steam_id} {game} {device}".lower():
                continue
            state = _persona_state(f)
            items.append(
                {
                    "steamId": steam_id,
                    "name": name,
                    "status": aurelia.persona_label(state),
                    "statusColor": _STATUS_COLORS.get(state, _STATUS_COLORS[0]),
                    "game": game,
                    "online": 0 < state < 7,
                    "device": device,
                    "deviceIcon": device_icon,
                    "unread": self._unread.get(steam_id, 0),
                }
            )
        items.sort(key=lambda i: not i["unread"])  # unread first, stable otherwise
        self._friend_items = items
        self.friendsListChanged.emit()

    def _load_missing_friend_names(self, rows: list[dict]):
        if self._name_worker and self._name_worker.isRunning():
            return
        missing = [
            str(f.get("steam_id"))
            for f in rows
            if not f.get("persona_name") and str(f.get("steam_id")) not in self._friend_names
        ]
        if not missing:
            return
        self._name_worker = _Fetch(_lookup_friend_names, missing)
        self._name_worker.done.connect(self._on_friend_names)
        self._name_worker.start()

    def _on_friend_names(self, names: Any):
        if not isinstance(names, dict):
            return
        self._friend_names.update(names)
        rows = self.friends.rows()
        for row in rows:
            name = self._friend_names.get(str(row.get("steam_id")))
            if name and not row.get("persona_name"):
                row["persona_name"] = name
        rows.sort(
            key=lambda row: (
                -_persona_state(row),
                (
                    row.get("persona_name")
                    or self._friend_names.get(str(row.get("steam_id")))
                    or str(row.get("steam_id", ""))
                ).lower(),
            )
        )
        self.friends.set_rows(rows)
        self._sync_friend_items(rows)
        if self._active_id:
            active = next(
                (row for row in rows if str(row.get("steam_id")) == self._active_id),
                {"steam_id": self._active_id},
            )
            name = _friend_name(active, self._friend_names)
            if name != self._active_name:
                self._active_name = name
                self.activeFriendChanged.emit()

    @pyqtSlot(str)
    def setFriendFilter(self, text: str):
        self._friend_filter = (text or "").strip()
        self._sync_friend_items(self.friends.rows())

    def _sync_message_items(self):
        self._message_items = [
            {
                "text": m["text"],
                "fromSelf": m["from_self"],
                "timestamp": _format_timestamp(m.get("timestamp")),
                "imageUrl": _image_url(m["text"]),
            }
            for m in self.messages._rows
        ]
        self.messagesChanged.emit()

    def _set_status(self, text: str):
        self._status = text
        self.statusTextChanged.emit()

    def _set_presence(self, text: str):
        if self._presence == text:
            return
        self._presence = text
        self.presenceChanged.emit()

    def _set_presence_mode(self, mode: str):
        if mode not in _PRESENCE_STATES or self._presence_mode == mode:
            return
        self._presence_mode = mode
        self.presenceModeChanged.emit()

    def _render_presence(self):
        label = _PRESENCE_LABELS.get(self._presence_mode, "Unknown")
        game = self._local_game or self._steam_game
        text = f"In game · {game} · {label}" if game else label
        self._set_presence(text)

    def _sync_presence(self, profile: Any):
        if not isinstance(profile, dict) or profile.get("error"):
            return
        if time.monotonic() >= self._presence_pin_until:
            state = _persona_state(profile)
            self._set_presence_mode(_PRESENCE_STATE_MODES.get(state, "offline"))
        game = str(profile.get("game_name") or "").strip()
        if not game and profile.get("game_app_id"):
            game = f"app {profile['game_app_id']}"
        self._steam_game = game
        self._steam_app_id = str(profile.get("game_app_id") or "")
        self._render_presence()

    def _track_local_game(self, name: str, process: Any, app_id: str = "", log: Any = None):
        self._local_game = name
        self._local_app_id = app_id
        self._local_game_process = process
        self._local_log = log
        self._local_started = time.monotonic()
        self._render_presence()

    def _poll_local_game(self):
        process = self._local_game_process
        if not process:
            return
        try:
            finished = process.poll() is not None
        except (OSError, AttributeError):
            finished = True
        if finished:
            if self._local_game and self._local_log and (process.returncode or time.monotonic() - self._local_started < 20):
                why = aurelia.last_error(self._local_log)  # exited straight away: the launch failed
                self._set_games_status(f"Couldn't launch {self._local_game}: {why}")
                desktop_notify("aur-chat", f"Launch failed: {self._local_game}", urgency="critical")
            elif self._local_game:
                desktop_notify("aur-chat", f"{self._local_game} closed")
            self._local_game_process = None
            self._local_game = ""
            self._local_app_id = ""
            self._render_presence()

    @pyqtProperty(bool, notify=presenceChanged)
    def gameRunning(self):
        return bool(self._steam_game or self._local_game)

    @pyqtProperty(bool, notify=presenceChanged)
    def stopForce(self):
        return self._stop_force

    @pyqtSlot()
    def endGame(self):
        app_id = self._local_app_id or self._steam_app_id
        if self._stop_worker and self._stop_worker.isRunning():
            return
        name = self._local_game or self._steam_game
        if not app_id:
            self._on_game_stopped(name, {"error": "unknown app id"})
            return
        self._stop_worker = _Fetch(aurelia.stop_game, app_id, force=self._stop_force)
        self._stop_worker.done.connect(lambda d, n=name: self._on_game_stopped(n, d))
        self._set_status(f"Ending {name}…")
        self._stop_worker.start()

    def _on_game_stopped(self, name: str, data: Any):
        if isinstance(data, dict) and not data.get("error"):
            self._stop_force = False
            self._set_status(f"Ended {name}")
            desktop_notify("aur-chat", f"Ended {name}")
            QTimer.singleShot(2000, self.refreshFriends)
        else:
            process = self._local_game_process
            if process and process.poll() is None:
                process.terminate()
                self._stop_force = False
                self._set_status(f"Terminated the launcher for {name}")
            else:
                self._stop_force = True
                err = data.get("error", "stop failed") if isinstance(data, dict) else "stop failed"
                self._set_status(f"{str(err)[:140]} — press Force end to kill it")
        self.presenceChanged.emit()

    @pyqtSlot(str)
    def setPresence(self, mode: str):
        mode = (mode or "").strip().lower()
        if mode not in _PRESENCE_STATES or (self._worker and self._worker.isRunning()):
            return
        previous = self._presence_mode
        self._set_presence_mode(mode)
        self._render_presence()
        self._set_busy(True)
        self._set_status(f"Changing status to {_PRESENCE_LABELS[mode]}…")
        self._worker = _Fetch(aurelia.set_presence, mode)
        self._worker.done.connect(
            lambda data, previous=previous: self._on_presence_set(previous, data)
        )
        self._worker.start()

    def _on_presence_set(self, previous: str, data: Any):
        self._set_busy(False)
        if not isinstance(data, dict) or data.get("error"):
            self._set_presence_mode(previous)
            self._render_presence()
            self._set_status(
                str(data.get("error", data))[:160]
                if isinstance(data, dict)
                else "Could not change status"
            )
            return
        mode = data.get("chat_presence")
        if mode in _PRESENCE_STATES:
            self._set_presence_mode(mode)
            self._presence_pin_until = time.monotonic() + 15
            self._render_presence()
            self._set_status(f"Status: {_PRESENCE_LABELS[mode]}")
        QTimer.singleShot(500, self.refreshFriends)

    def _set_busy(self, on: bool):
        self._busy = on
        self.busyChanged.emit()

    @pyqtSlot(bool)
    def setWindowVisible(self, visible: bool):
        self._window_visible = visible
        self._start_watchers()

    @pyqtSlot(str)
    def refreshGames(self, search: str = ""):
        self._games_search = (search or "").strip()
        if self._worker and self._worker.isRunning():
            return
        self._set_busy(True)
        self._set_games_status("Loading library…")
        self._worker = _Fetch(
            aurelia.list_games,
            search=self._games_search,
            installed_only=self._games_installed_only,
        )
        self._worker.done.connect(self._on_games)
        self._worker.start()

    @pyqtSlot(bool)
    def setGamesInstalledOnly(self, on: bool):
        if self._games_installed_only == on:
            return
        self._games_installed_only = on
        self.refreshGames(self._games_search)

    @pyqtSlot(bool)
    def setGamesIncludeShortcuts(self, on: bool):
        if self._games_include_shortcuts == on:
            return
        self._games_include_shortcuts = on
        self.refreshGames(self._games_search)

    def _on_games(self, data: Any):
        self._set_busy(False)
        if aurelia.auth_required(data):
            self._game_items = []
            self.gamesListChanged.emit()
            self._set_games_status("Log in with: aurelia login")
            return
        if not isinstance(data, list):
            self._set_games_status(str(data.get("error", data))[:120])
            return
        rows = sorted(data, key=lambda g: (g.get("name") or "").lower())
        items = [
            {
                "appId": str(g.get("app_id", "")),
                "name": g.get("name") or str(g.get("app_id", "")),
                "installed": bool(g.get("is_installed")),
                "isShortcut": False,
                "launchOptions": "",
            }
            for g in rows
        ]
        if self._games_include_shortcuts:
            shortcuts = steam_shortcuts.load_shortcuts()
            if self._games_search:
                q = self._games_search.lower()
                shortcuts = [s for s in shortcuts if q in s["name"].lower()]
            items.extend(shortcuts)
        seen: set[str] = set()
        merged: list[dict] = []
        for g in sorted(items, key=lambda x: x["name"].lower()):
            if g["appId"] in seen:
                continue
            seen.add(g["appId"])
            merged.append(g)
        launch_options.enrich_library_launch_options(merged)
        self._game_items = merged
        self._apply_update_states()
        self.gamesListChanged.emit()
        n = len(self._game_items)
        inst = sum(1 for g in self._game_items if g["installed"])
        sc = sum(1 for g in self._game_items if g.get("isShortcut"))
        extra = f", {sc} shortcuts" if sc else ""
        self._set_games_status(f"{n} titles ({inst} installed{extra})")
        self.refreshUpdates()

    def _apply_update_states(self):
        for g in self._game_items:
            app_id = g["appId"]
            if g.get("isShortcut") or not g["installed"]:
                state = ""
            elif app_id in self._updating:
                state = "Updating…"
            elif app_id in self._updates:
                state = "Update available"
            else:
                state = "Up to date" if self._updates_checked else ""
            g["updateState"] = state
            g["updatable"] = state == "Update available"
            g["updating"] = app_id in self._updating
            g["updateText"] = self._progress.get(app_id, ("",))[-1] if g["updating"] else ""

    @pyqtSlot()
    def refreshUpdates(self):
        if self._check_worker and self._check_worker.isRunning():
            return
        self._check_worker = _Fetch(aurelia.updates)
        self._check_worker.done.connect(self._on_updates)
        self._check_worker.start()

    def _on_updates(self, data: Any):
        if not isinstance(data, dict) or "updates" not in data:
            err = data.get("error", "could not check updates") if isinstance(data, dict) else "could not check updates"
            self._set_status(str(err)[:160])
            return
        installed = {g["appId"] for g in self._game_items if g["installed"] and not g.get("isShortcut")}
        self._updates = {str(u.get("app_id")) for u in data["updates"]}
        self._updates |= _steam_update_pending(installed)
        self._updates_checked = True
        self._apply_update_states()
        self.gamesListChanged.emit()
        self._set_status(
            f"{len(self._updates)} game update(s) available" if self._updates else "All games up to date"
        )

    @pyqtSlot(str)
    def updateGame(self, app_id: str):
        app_id = (app_id or "").strip()
        if not app_id or app_id in self._updating or (self._update_worker and self._update_worker.isRunning()):
            return
        name = next((g["name"] for g in self._game_items if g["appId"] == app_id), app_id)
        self._updating.add(app_id)
        self._apply_update_states()
        self.gamesListChanged.emit()
        self.updatingChanged.emit()
        self._set_status(f"Updating {name}… (this can take a while)")
        desktop_notify("aur-chat", f"Download started: {name}")
        self._update_worker = _Fetch(
            aurelia.update_game_progress, app_id, lambda d, n, sp, a=app_id: self._progressSig.emit(a, d, n, sp)
        )
        self._update_worker.done.connect(lambda d, a=app_id, n=name: self._on_update_done(a, n, d))
        self._update_worker.start()

    def _set_progress(self, app_id: str, done: float, total: float, speed: str):
        old = self._progress.get(app_id)
        if old is not None and done - old[0] < 1e8:
            return
        text = f"{done / 1e9:.2f} of {total / 1e9:.2f} GB" + (f" · {speed}" if speed else "")
        self._progress[app_id] = (done, text)
        self._apply_update_states()
        self.gamesListChanged.emit()

    def _on_update_done(self, app_id: str, name: str, data: Any):
        self._updating.discard(app_id)
        self._progress.pop(app_id, None)
        self.updatingChanged.emit()
        ok = isinstance(data, dict) and not data.get("error")
        if ok:
            self._updates.discard(app_id)
            self._set_status(f"{name} updated")
            if data.get("attached"):
                desktop_notify("aur-chat", f"Download finished: {name}")
        else:
            err = data.get("error", "update failed") if isinstance(data, dict) else "update failed"
            self._set_status(str(err)[:160])
            if isinstance(data, dict) and data.get("attached"):
                desktop_notify("aur-chat", f"Download stopped: {name}", urgency="critical")
        self._apply_update_states()
        self.gamesListChanged.emit()
        self.refreshUpdates()

    @pyqtSlot(str)
    def openLaunchOptions(self, app_id: str):
        app_id = (app_id or "").strip()
        row = next((g for g in self._game_items if g["appId"] == app_id), None)
        if not row:
            return
        self._launch_app_id = app_id
        self._launch_is_shortcut = bool(row.get("isShortcut"))
        self._launch_name = row["name"]
        self._launch_text = launch_options.get_launch_options(
            app_id, is_shortcut=self._launch_is_shortcut
        )
        self._launch_open = True
        self.launchOptionsChanged.emit()

    @pyqtSlot(str)
    def saveLaunchOptions(self, text: str):
        if not self._launch_app_id:
            return
        ok = launch_options.set_launch_options(
            self._launch_app_id,
            text or "",
            is_shortcut=self._launch_is_shortcut,
        )
        if ok:
            for g in self._game_items:
                if g["appId"] == self._launch_app_id:
                    g["launchOptions"] = text or ""
            self.gamesListChanged.emit()
            self._set_games_status("Launch options saved")
        else:
            self._set_games_status("Could not save launch options (is vdf installed?)")
        self._launch_open = False
        self.launchOptionsChanged.emit()

    @pyqtSlot()
    def closeLaunchOptions(self):
        self._launch_open = False
        self.launchOptionsChanged.emit()

    @pyqtSlot(str)
    def playGame(self, app_id: str):
        app_id = (app_id or "").strip()
        if not app_id:
            return
        row = next((g for g in self._game_items if g["appId"] == app_id), None)
        if row and row.get("isShortcut"):
            try:
                process = steam_shortcuts.launch_shortcut(app_id)
                self._track_local_game(row["name"], process, app_id)
                tool = row.get("compatTool") or "native"
                self._set_games_status(f"Launching {row['name']} directly ({tool})…")
                desktop_notify("aur-chat", f"Launching {row['name']}")
            except OSError as e:
                print(f"[aur-chat] shortcut launch failed: {e}", file=sys.stderr, flush=True)
                self._set_games_status(str(e))
            return
        if row and not row["installed"]:
            self._set_games_status(f"{row['name']} is not installed (aurelia install {app_id})")
            return
        try:
            name = row["name"] if row else app_id
            process = aurelia.play_game(app_id)
            self._track_local_game(name, process, app_id, aurelia.play_log(app_id))
            self._set_games_status(f"Launching {name}…")
            desktop_notify("aur-chat", f"Launching {name}")
        except OSError as e:
            self._set_games_status(str(e))

    @pyqtSlot()
    def refreshFriends(self):
        if self._worker and self._worker.isRunning():
            return
        self._set_busy(True)
        self._worker = _Fetch(_social_snapshot)
        self._worker.done.connect(self._on_friends)
        self._worker.start()

    def _on_friends(self, data: Any):
        self._set_busy(False)
        if isinstance(data, dict) and "friends" in data:
            self._sync_presence(data.get("profile"))
            data = data["friends"]
        if aurelia.auth_required(data):
            self._set_presence("Not logged in")
            self._auth_required = True
            self.authRequiredChanged.emit()
            self.friends.set_rows([])
            self._sync_friend_items([])
            self._set_status("Log in with: aurelia login")
            return
        if self._auth_required:
            self._auth_required = False
            self.authRequiredChanged.emit()
        if not isinstance(data, list):
            self._set_status(str(data.get("error", data))[:120])
            return
        if not data and self._friends_empty_retries < 4:
            self._friends_empty_retries += 1
            self._set_status("Loading friends…")
            QTimer.singleShot(1500, self.refreshFriends)
            return
        self._friends_empty_retries = 0
        rows = sorted(
            data,
            key=_friend_sort_key,
        )
        for row in rows:
            name = row.get("persona_name")
            if name:
                self._friend_names[str(row.get("steam_id"))] = name
        self._notify_presence_changes(rows)
        self.friends.set_rows(rows)
        self._sync_friend_items(rows)
        self._load_missing_friend_names(rows)
        self._set_status(f"{len(rows)} friends")
        self._sync_watchers(rows)

    @pyqtSlot(str)
    def pickFriend(self, steam_id: str):
        if not steam_id or steam_id == self._active_id:
            return
        self._active_id = steam_id
        self._unread.pop(steam_id, None)
        self._sync_friend_items(self.friends.rows())
        friend = next(
            (row for row in self.friends.rows() if str(row.get("steam_id")) == steam_id),
            {"steam_id": steam_id},
        )
        self._active_name = _friend_name(friend, self._friend_names)
        self.activeFriendChanged.emit()
        self.messages.clear()
        self._sync_message_items()
        self._open_stream(steam_id)
        self._set_busy(True)
        self._worker = _Fetch(aurelia.chat_history, steam_id)
        self._worker.done.connect(lambda d, s=steam_id: self._on_history(s, d))
        self._worker.start()

    def _on_history(self, steam_id: str, data: Any):
        self._set_busy(False)
        if steam_id != self._active_id or not isinstance(data, list):
            return
        self.messages.load_history(data)
        self._sync_message_items()

    def _open_stream(self, steam_id: str):
        if self._stream:
            self._stop_stream(self._stream)
        self._stream = ChatStream(steam_id)
        self._stream.chatEvent.connect(lambda ev, s=steam_id: self._on_chat_event(s, ev, active=True))
        self._stream.start_session()

    def _on_chat_event(self, steam_id: str, ev: dict, *, active: bool):
        kind = ev.get("event")
        if kind == "message":
            text = ev.get("text", "")
            if active and steam_id == self._active_id:
                self.messages.append(text, False, ev.get("timestamp"))
                self._sync_message_items()
            else:
                self._unread[steam_id] = self._unread.get(steam_id, 0) + 1
                self._sync_friend_items(self.friends.rows())
            self._maybe_notify(steam_id, text)
        elif kind == "echo" and active and steam_id == self._active_id:
            self.messages.append(ev.get("text", ""), True, ev.get("timestamp"))
            self._sync_message_items()
        elif kind == "typing" and active and steam_id == self._active_id:
            self._set_status(f"{self._active_name} is typing…")
        elif kind == "error":
            self._set_status(ev.get("message", "chat error"))
        elif kind == "closed" and active:
            self._set_status("Chat session closed")

    def _notify_presence_changes(self, rows: list[dict]):
        online = {str(f.get("steam_id")): 0 < _persona_state(f) < 7 for f in rows}
        previous, self._last_online = self._last_online, online
        if previous is None:
            return
        for row in rows:
            sid = str(row.get("steam_id"))
            if sid not in previous or previous[sid] == online[sid]:
                continue
            name = _friend_name(row, self._friend_names)
            if online[sid]:
                desktop_notify("aur-chat", f"{name} came online")
            else:
                desktop_notify("aur-chat", f"{name} went offline")

    def _maybe_notify(self, steam_id: str, text: str):
        name = self._friend_names.get(str(steam_id)) or self.friends.name_for(steam_id)
        preview = text if len(text) < 180 else text[:177] + "…"
        desktop_notify(f"aur-chat · {name}", preview)

    @pyqtSlot(str)
    def sendMessage(self, text: str):
        text = (text or "").strip()
        if not text or not self._stream:
            return
        self.messages.append(text, True, int(datetime.now().timestamp()))
        self._sync_message_items()
        self._stream.send_line(text)

    @pyqtSlot(result=bool)
    def pasteImage(self):
        if not self._active_id:
            return False
        path = _clipboard_image_path()
        if not path:
            return False
        if self._upload_worker and self._upload_worker.isRunning():
            os.unlink(path)
            self._set_status("An image is already uploading…")
            return True
        target = self._active_id
        worker = _Fetch(aurelia.chat_send_image, target, path)
        self._upload_worker = worker
        self._set_busy(True)
        self._set_status("Uploading image…")
        worker.done.connect(
            lambda data, w=worker, p=path, s=target: self._on_image_uploaded(s, p, w, data)
        )
        worker.start()
        return True

    def _on_image_uploaded(self, steam_id: str, path: str, worker: _Fetch, data: Any):
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        if self._upload_worker is worker:
            self._set_busy(False)
        if steam_id != self._active_id:
            return
        self._set_status(_upload_status(data))

    @pyqtSlot(str)
    def copyImageUrl(self, url: str):
        url = _image_url(url)
        if not url:
            return
        QGuiApplication.clipboard().setText(url)
        self._set_status("Image URL copied")

    @pyqtSlot(str)
    def copyImage(self, url: str):
        url = _image_url(url)
        if not url:
            return
        if self._copy_worker:
            self._set_status("An image is already downloading…")
            return
        worker = _Fetch(_download_image, url)
        self._copy_worker = worker
        self._set_status("Downloading image…")
        worker.done.connect(self._on_image_copied)
        worker.start()

    @pyqtSlot(object)
    def _on_image_copied(self, image: Any):
        if not self._copy_worker:
            return
        self._copy_worker = None
        if not isinstance(image, QImage) or image.isNull():
            error = image.get("error") if isinstance(image, dict) else None
            self._set_status(str(error or "could not copy image")[:160])
            return
        QGuiApplication.clipboard().setImage(image)
        self._set_status("Image copied")

    @pyqtSlot(str)
    def searchFriend(self, query: str):
        query = (query or "").strip()
        if not query:
            return
        self._set_busy(True)
        self._worker = _Fetch(aurelia.friends_search, query)
        self._worker.done.connect(self._on_search)
        self._worker.start()

    def _on_search(self, data: Any):
        self._set_busy(False)
        if isinstance(data, dict) and data.get("error"):
            self._search_result = {"error": data.get("error")}
        elif isinstance(data, dict):
            self._search_result = data
        else:
            self._search_result = {"error": "unexpected response"}
        self.searchResultChanged.emit()

    @pyqtSlot(str)
    def addFriend(self, query: str):
        query = (query or "").strip()
        if not query:
            return
        if self._search_result and self._search_result.get("steam_id"):
            query = self._search_result["steam_id"]
        self._set_busy(True)
        self._worker = _Fetch(aurelia.friends_add, query)
        self._worker.done.connect(self._on_add)
        self._worker.start()

    def _on_add(self, data: Any):
        self._set_busy(False)
        if isinstance(data, dict) and data.get("error"):
            self._set_status(str(data.get("error")))
            return
        self._set_status("Friend request sent")
        self.refreshFriends()

    def _start_watchers(self):
        self._sync_watchers(self.friends.rows())

    def _sync_watchers(self, rows: list[dict]):
        online = {str(f["steam_id"]) for f in rows if _persona_state(f) > 0}
        online.discard(self._active_id)
        for sid in list(self._watch):
            if sid not in online:
                self._stop_stream(self._watch.pop(sid))
        for sid in online:
            if sid in self._watch:
                continue
            st = ChatStream(sid)
            st.chatEvent.connect(lambda ev, s=sid: self._on_chat_event(s, ev, active=False))
            st.start_session()
            self._watch[sid] = st

    def _stop_watchers(self):
        for st in self._watch.values():
            self._stop_stream(st)
        self._watch.clear()

    @staticmethod
    def _stop_stream(stream: ChatStream):
        if stream.state() != QProcess.ProcessState.NotRunning:
            stream.terminate()
            if not stream.waitForFinished(250):
                stream.kill()
                stream.waitForFinished(500)
        stream.deleteLater()

    @pyqtSlot()
    def shutdown(self):
        self._poll.stop()
        self._game_poll.stop()
        self._stop_watchers()
        if self._stream:
            self._stop_stream(self._stream)
