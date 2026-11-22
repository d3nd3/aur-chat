from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
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
    QUrl,
    pyqtProperty,
    pyqtSignal,
    pyqtSlot,
)
from PyQt6.QtGui import QDesktopServices, QGuiApplication, QImage

from . import aurelia
from . import chat_format
from .chat_stream import EventStream
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


def _display_name(name: str, alias: str) -> str:
    return f"{alias} ({name})" if alias else name


def _valid_steam_id(sid: str) -> bool:
    return sid.isdigit() and len(sid) == 17


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

# Device hints: freedesktop theme icon names rendered by Kirigami.Icon (no emoji glyphs).
_DEVICE_TYPES = (
    (512, "Mobile", "smartphone"),
    (2048, "VR", "audio-headset"),
    (1024, "TV", "video-display"),
    (256, "Web", "globe"),
)


def _persona_flags(friend: dict) -> int | None:
    value = friend.get("persona_state_flags")
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _friend_game(friend: dict) -> str:
    # Steam often sends the app id without a name; show the id, as the CLI does.
    return friend.get("game_name") or (f"app {friend['game_app_id']}" if friend.get("game_app_id") else "")


def _friend_subtitle(friend: dict) -> str:
    if friend.get("persona_state") is None:
        return ""  # no roster row yet: the status is unknown, not "offline"
    status = aurelia.persona_label(_persona_state(friend))
    game = _friend_game(friend)
    detail = _game_detail(friend)
    parts = [status]
    if game:
        parts.append(game)
    if detail:
        parts.append(detail)
    return " · ".join(parts)


def _last_seen_text(friend: dict, now: float | None = None) -> str:
    """'last seen 3 hours ago' for offline friends; empty while online or unknown."""
    if 0 < _persona_state(friend) < 7:
        return ""  # online: last-seen is noise
    ts = int(friend.get("last_seen_online") or 0)
    if not ts:
        return ""
    age = max(0, (now or time.time()) - ts)
    if age < 90:
        return "last seen just now"
    for span, unit in ((604800, "week"), (86400, "day"), (3600, "hour"), (60, "min")):
        if age >= span:
            n = round(age / span)
            return f"last seen {n} {unit}{'s' if n != 1 else ''} ago"
    return ""


def _device_info(friend: dict) -> tuple[str, str]:
    flags = _persona_flags(friend)
    if flags is None or not 0 < _persona_state(friend) < 7:
        return "", ""
    for bit, label, icon in _DEVICE_TYPES:
        if flags & bit:
            return label, icon
    return "PC", "computer"


_DOTA_APP_ID = 570
# Players see official names, not the internal AI slugs Steam rich presence sends.
_DOTA_HERO_OVERRIDES = {
    "antimage": "Anti-Mage",
    "nevermore": "Shadow Fiend",
    "shredder": "Timbersaw",
    "magnataur": "Magnus",
    "necrolyte": "Necrophos",
    "obsidian_destroyer": "Outworld Destroyer",
    "rattletrap": "Clockwerk",
    "skeleton_king": "Wraith King",
    "doom_bringer": "Doom",
    "furion": "Nature's Prophet",
    "centaur": "Centaur Warrunner",
    "life_stealer": "Lifestealer",
    "treant": "Treant Protector",
    "vengefulspirit": "Vengeful Spirit",
    "windrunner": "Windranger",
    "abyssal_underlord": "Underlord",
}

# Rich presence keys that are pure noise in a tooltip.
_RP_NOISE_KEYS = ("CurrentPhaseTimeNonce", "CurrentGameMinutesDelta",
                  "CurrentSplitScreenGameSession")


def _dota_hero_name(raw: str) -> str:
    """'npc_dota_hero_axe' → 'Axe'; official names for the slugs that differ."""
    slug = raw.removeprefix("npc_dota_hero_")
    if (special := _DOTA_HERO_OVERRIDES.get(slug)) is not None:
        return special
    return " ".join(w.capitalize() for w in re.split(r"[_ ]", slug) if w)


def _dota_hero_level(rp: dict) -> str:
    level = str(rp.get("CurrentHeroLevel") or "")
    return level if level.isdigit() else ""


def _game_detail(friend: dict) -> str:
    """Short game context for the friend list subtitle: hero + level in Dota."""
    if friend.get("game_app_id") != _DOTA_APP_ID:
        return "joinable" if _friend_joinable(friend) else ""
    rp = friend.get("rich_presence") or {}
    hero_raw = str(rp.get("CurrentHero") or "")
    if not hero_raw:
        return ""
    hero = _dota_hero_name(hero_raw)
    if not hero:
        return ""
    level = _dota_hero_level(rp)
    return f"{hero} Lv {level}" if level else hero


def _friend_joinable(friend: dict) -> bool:
    # EPersonaStateFlag::InJoinableGame (bit 2).
    return bool((_persona_flags(friend) or 0) & 2)


def _rich_text(friend: dict) -> str:
    """Full game context for tooltips: hero/level, lobby, custom game name…"""
    rp = friend.get("rich_presence") or {}
    parts = []
    if rp.get("CurrentHero"):
        hero = _dota_hero_name(str(rp["CurrentHero"]))
        level = _dota_hero_level(rp)
        parts.append(f"{hero} Lv {level}" if level else hero)
    if rp.get("CustomGameName"):
        parts.append(str(rp["CustomGameName"]))
    if rp.get("state") and "CurrentHero" not in rp:
        parts.append(str(rp["state"]))
    for key, value in sorted(rp.items()):
        if (key in _RP_NOISE_KEYS or key in ("state", "CurrentHero", "CurrentHeroLevel", "CustomGameName")
                or not value or value == "0"):
            continue
        parts.append(f"{key}: {value}")
    if not parts and _friend_joinable(friend):
        parts.append("in a joinable game")
    return " · ".join(parts)


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


def _friend_state_rank(friend: dict) -> int:
    # Presence tiers in semantic order: online (1), busy (2), away (3),
    # snooze (4), looking… (5, 6), then everything offline/unknown last.
    # Unknown (None) must not rank above online, and invisible (7) must not
    # rank above anyone online either.
    state = _persona_state(friend)
    return state if 0 < state < 7 else 7


def _friend_sort_key(friend: dict):
    return (_friend_state_rank(friend), (friend.get("persona_name") or "").lower())


def _format_timestamp(value: Any) -> str:
    try:
        return datetime.fromtimestamp(int(value)).strftime("%H:%M")
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


def _image_url(text: Any) -> str:
    value = str(text or "").strip()
    if m := re.fullmatch(r"\[(img|url)[^\]]*\]\s*(.*?)\s*\[/\1\]", value, re.S):
        value = m.group(2).strip()
    if "ugc" not in value:
        return ""
    if not value.startswith("https"):  # unknown Steam wrapper: pull the link out of it
        m = re.search(r"https?://[^\s\"'<>\\\[\]]+", value)
        if m:
            value = m.group(0)
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


def _ugc_key(text: Any) -> str:
    """Canonical identity of a Steam uploaded image: its unique /ugc/<id> number.

    The upload response URL and Steam's stored/echoed URL can use different CDN
    hosts (steamuserimages-a.akamaihd.net vs images.steamusercontent.com) for the
    same file, so raw string comparison cannot dedupe them."""
    m = re.search(r"/ugc/(\d+)", _image_url(text))
    return m.group(1) if m else ""


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


_STATE_FILE = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "aur-chat" / "state.json"

_FRIENDS_LOG = (
    Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "aur-chat" / "friends-debug.log"
)
_FRIENDS_LOG_MAX = 1 << 20  # 1 MiB: partially-fetched rosters are a today-problem
_FRIENDS_EMPTY_RETRIES = 12  # ≈18 s of empty polls before the UI owns the emptiness
# Persona data a partial sync can drop; the previous poll's value is kept when
# the new row omits it (None), so friends don't flash offline mid-sync.
_CARRY_FIELDS = ("persona_state", "persona_state_flags", "last_seen_online",
                 "game_app_id", "game_name", "rich_presence", "game_lobby_id")


def friends_log(kind: str, detail: str = ""):
    """Timeline of friends sync for diagnosing partial/empty roster fetches.

    On by default into ~/.cache/aur-chat/friends-debug.log; set
    AUR_CHAT_FRIENDS_DEBUG=0 to silence. Lives beside the daemon's own
    ~/.config/Aurelia/logs/friends-debug.log (AURELIA_FRIENDS_DEBUG=0) so both
    sides of a bad sync can be compared. Failures are swallowed: logging must
    never break the UI.
    """
    if os.environ.get("AUR_CHAT_FRIENDS_DEBUG") == "0":
        return
    line = f"[{time.time():.3f}] {kind} {detail}".rstrip() + "\n"
    try:
        try:
            if _FRIENDS_LOG.stat().st_size > _FRIENDS_LOG_MAX:
                _FRIENDS_LOG.unlink()
        except FileNotFoundError:
            pass
        _FRIENDS_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _FRIENDS_LOG.open("a") as f:
            f.write(line)
    except OSError:
        pass


def _load_state() -> dict[str, Any]:
    try:
        state = json.loads(_STATE_FILE.read_text())
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


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
            return _friend_game(f)
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
    HtmlRole = Qt.ItemDataRole.UserRole + 4
    ImageRole = Qt.ItemDataRole.UserRole + 5
    StickerRole = Qt.ItemDataRole.UserRole + 6
    ShowNameRole = Qt.ItemDataRole.UserRole + 7
    SenderRole = Qt.ItemDataRole.UserRole + 8
    VideoRole = Qt.ItemDataRole.UserRole + 9
    StickerNameRole = Qt.ItemDataRole.UserRole + 10

    def __init__(self):
        super().__init__()
        self._rows: list[dict] = []

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._rows)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self._rows):
            return None
        m = self._rows[index.row()]
        if role in (Qt.ItemDataRole.DisplayRole, self.TextRole):
            return m.get("text", "")
        if role == self.FromSelfRole:
            return bool(m.get("from_self"))
        if role == self.TimeRole:
            return m.get("time", "")
        if role == self.HtmlRole:
            return m.get("html", "")
        if role == self.ImageRole:
            return m.get("imageUrl", "")
        if role == self.StickerRole:
            return m.get("stickerUrl", "")
        if role == self.ShowNameRole:
            return bool(m.get("showName"))
        if role == self.SenderRole:
            return m.get("sender", "")
        if role == self.VideoRole:
            return m.get("videoId", "")
        if role == self.StickerNameRole:
            return m.get("stickerName", "")
        return None

    def roleNames(self):
        return {
            Qt.ItemDataRole.DisplayRole: b"display",
            self.TextRole: b"text",
            self.FromSelfRole: b"fromSelf",
            self.TimeRole: b"time",
            self.HtmlRole: b"html",
            self.ImageRole: b"imageUrl",
            self.StickerRole: b"stickerUrl",
            self.ShowNameRole: b"showName",
            self.SenderRole: b"sender",
            self.VideoRole: b"videoId",
            self.StickerNameRole: b"stickerName",
        }

    def clear(self):
        if not self._rows:
            return
        self.beginResetModel()
        self._rows = []
        self.endResetModel()

    def set_rows(self, rows: list[dict]):
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def append_row(self, row: dict):
        self.beginInsertRows(QModelIndex(), len(self._rows), len(self._rows))
        self._rows.append(row)
        self.endInsertRows()

    def update_row(self, index: int, row: dict):
        self._rows[index] = row
        self.dataChanged.emit(self.index(index), self.index(index))


class AureliaBackend(QObject):
    authRequiredChanged = pyqtSignal()
    statusTextChanged = pyqtSignal()
    activeFriendChanged = pyqtSignal()
    chatTabsChanged = pyqtSignal()
    dualWindowChanged = pyqtSignal()
    searchResultChanged = pyqtSignal()
    busyChanged = pyqtSignal()
    friendsListChanged = pyqtSignal()
    gamesListChanged = pyqtSignal()
    gamesStatusChanged = pyqtSignal()
    launchOptionsChanged = pyqtSignal()
    presenceChanged = pyqtSignal()
    playingChanged = pyqtSignal()
    presenceModeChanged = pyqtSignal()
    ownNameChanged = pyqtSignal()
    friendFontChanged = pyqtSignal()
    friendsWidthChanged = pyqtSignal()
    chatFontChanged = pyqtSignal()
    intervalsChanged = pyqtSignal()
    videoInfoChanged = pyqtSignal()
    emoticonsChanged = pyqtSignal()
    unreadChanged = pyqtSignal()
    stickersChanged = pyqtSignal()
    mediaRevChanged = pyqtSignal()
    animChanged = pyqtSignal()
    messageArrived = pyqtSignal()
    updatingChanged = pyqtSignal()
    _progressSig = pyqtSignal(str, float, float, str)

    def __init__(self):
        super().__init__()
        self._auth_required = False
        self._status = "Ready"
        self._presence = "Checking presence…"
        self._presence_mode = "offline"
        self._presence_pin_until = 0.0  # ignore stale profile reads right after a change
        self._own_name = ""
        self._steam_game = ""
        self._steam_app_id = ""
        self._local_app_id = ""
        self._stop_worker: _Fetch | None = None
        self._local_game = ""
        self._local_game_process: Any = None
        self._playing_options = ""
        self._handoff: Any = None
        state = _load_state()
        self._friend_font = int(state.get("friend_font") or 0)  # 0 = theme default
        _w = int(state.get("friends_width") or 0)
        self._friends_width = _w if 220 <= _w <= 1600 else 0  # 0 = responsive default
        self._chat_font = int(state.get("chat_font") or 0)  # 0 = theme default
        self._tabs: list[str] = [s for s in state.get("tabs") or []
                                 if isinstance(s, str) and _valid_steam_id(s)]
        saved_active = state.get("active")
        self._active_id = saved_active if saved_active in self._tabs else next(iter(self._tabs), "")
        self._active_name = ""
        self._active_subtitle = ""
        self._dual_window = bool(state.get("dual"))
        self._hide_offline = bool(state.get("hide_offline"))
        self._stickers: set[str] = {s for s in state.get("stickers") or [] if isinstance(s, str)}
        # Server-confirmed inventory: only `chat stickers` names land here. The
        # picker offers only these — Steam silently drops sticker payloads for
        # media the account doesn't own (the recipient then sees nothing).
        self._owned_stickers: set[str] = set()
        self._media_rev = 0
        self._media_warmed: set[str] = set()
        self._anim_index = max(0, min(3, int(state.get("anim_index") or 0)))
        self._emoticons: list[str] = list(chat_format.EMOTICON_NAMES)
        self._favourites: set[str] = {s for s in state.get("favourites") or [] if isinstance(s, str)}
        self._aliases: dict[str, str] = {str(k): str(v) for k, v in (state.get("aliases") or {}).items()}
        self._name_history: dict[str, list[str]] = {
            str(k): [str(n) for n in v] for k, v in (state.get("name_history") or {}).items()
        }
        # Steam's chat history drops our own emoticon/sticker payloads (empty text);
        # this journal restores them after a restart. [steam_id, timestamp, wire]
        self._sent_media: list[list] = [m for m in state.get("sent_media") or []
                                        if isinstance(m, list) and len(m) == 3]
        self._tab_items: list[dict] = []
        self._search_result: dict | None = None
        self._busy = False
        self._worker: _Fetch | None = None
        self._history_workers: list[_Fetch] = []
        self._video_workers: list[_Fetch] = []
        self._video_info: dict[str, dict] = {}
        self._video_fetching: set[str] = set()
        self._friends_worker: _Fetch | None = None
        self._upload_worker: _Fetch | None = None
        self._copy_worker: _Fetch | None = None
        self._events: EventStream | None = None  # the one message stream for all friends
        self._send_workers: list[_Fetch] = []
        self._window_visible = True
        self._minimize_to_tray = True
        self._friends_empty_retries = 0
        self._friend_items: list[dict] = []
        self._friend_names: dict[str, str] = {}
        self._unread: dict[str, int] = {}
        self._unread_at: dict[str, float] = {}  # arrival time of each friend's latest unread message
        self._last_online: dict[str, bool] | None = None
        self._friend_filter = ""
        self._name_worker: _Fetch | None = None
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
        # Poll intervals (seconds), user-tunable in Settings.
        self._friends_interval = max(2, min(120, int(state.get("friends_interval") or 10)))
        self._games_interval = max(1, min(60, int(state.get("games_interval") or 2)))
        self._games_include_shortcuts = True
        self._launch_open = False
        self._launch_name = ""
        self._launch_text = ""
        self._launch_app_id = ""
        self._launch_is_shortcut = False

        self.friends = FriendModel()
        self.messages = MessageModel()

        self._poll = QTimer(self)
        self._poll.setInterval(self._friends_interval * 1000)
        self._poll.timeout.connect(self.refreshFriends)
        self._poll.start()
        self._game_poll = QTimer(self)
        self._game_poll.setInterval(self._games_interval * 1000)
        self._game_poll.timeout.connect(self._poll_local_game)
        self._game_poll.start()
        QTimer.singleShot(0, self.refreshFriends)
        self._ensure_events()
        worker = _Fetch(chat_format.steam_cache_names)  # media names Steam's own client fetched
        worker.done.connect(self._on_steam_cache)
        worker.start()
        owned = _Fetch(aurelia.chat_stickers)  # the media this account actually owns
        owned.done.connect(self._on_owned_media)
        owned.start()
        self._cache_workers = [worker, owned]
        # Open tabs get their names resolved up front instead of waiting for a sync.
        self._load_missing_friend_names([{"steam_id": sid} for sid in self._tabs])
        if self._active_id:  # restore the chat that was open last session
            self._show_chat(self._active_id)

    @pyqtProperty(bool, notify=authRequiredChanged)
    def authRequired(self):
        return self._auth_required

    @pyqtProperty(str, notify=statusTextChanged)
    def statusText(self):
        return self._status

    @pyqtProperty(str, notify=presenceChanged)
    def presenceText(self):
        return self._presence

    @pyqtProperty(str, notify=ownNameChanged)
    def ownName(self):
        return self._own_name

    @pyqtProperty(int, notify=friendFontChanged)
    def friendFontSize(self):
        return self._friend_font

    @pyqtSlot(int)
    def setFriendFontSize(self, size: int):
        size = max(0, min(24, int(size or 0)))
        if size == self._friend_font:
            return
        self._friend_font = size
        self.friendFontChanged.emit()
        self._save_state()

    @pyqtProperty(int, notify=friendsWidthChanged)
    def friendsWidth(self):
        return self._friends_width

    @pyqtSlot(int)
    def setFriendsWidth(self, width: int):
        width = int(width or 0)
        if width == self._friends_width or not 220 <= width <= 1600:
            return
        self._friends_width = width
        self.friendsWidthChanged.emit()
        self._save_state()

    @pyqtProperty(int, notify=chatFontChanged)
    def chatFontSize(self):
        return self._chat_font

    @pyqtSlot(int)
    def setChatFontSize(self, size: int):
        size = max(0, min(24, int(size or 0)))
        if size == self._chat_font:
            return
        self._chat_font = size
        self.chatFontChanged.emit()
        self._save_state()

    @pyqtProperty(int, notify=intervalsChanged)
    def friendsInterval(self):
        return self._friends_interval

    @pyqtSlot(int)
    def setFriendsInterval(self, secs: int):
        secs = max(2, min(120, int(secs or 0)))
        if secs == self._friends_interval:
            return
        self._friends_interval = secs
        self._poll.setInterval(secs * 1000)
        self.intervalsChanged.emit()
        self._save_state()

    @pyqtProperty(int, notify=intervalsChanged)
    def gamesInterval(self):
        return self._games_interval

    @pyqtSlot(int)
    def setGamesInterval(self, secs: int):
        secs = max(1, min(60, int(secs or 0)))
        if secs == self._games_interval:
            return
        self._games_interval = secs
        self._game_poll.setInterval(secs * 1000)
        self.intervalsChanged.emit()
        self._save_state()

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

    @pyqtProperty(str, notify=activeFriendChanged)
    def activeFriendSubtitle(self):
        return self._active_subtitle

    @pyqtProperty(bool, notify=busyChanged)
    def busy(self):
        return self._busy

    @pyqtProperty("QVariant", notify=searchResultChanged)
    def searchResult(self):
        return self._search_result

    @pyqtProperty("QVariantList", notify=friendsListChanged)
    def friendItems(self):
        return self._friend_items

    @pyqtProperty(QObject, constant=True)
    def chatModel(self):
        return self.messages

    @pyqtProperty("QVariantMap", notify=videoInfoChanged)
    def videoInfo(self):
        return self._video_info

    @pyqtProperty("QVariantList", notify=chatTabsChanged)
    def chatTabs(self):
        return self._tab_items

    @pyqtProperty("QVariantList", notify=emoticonsChanged)
    def emoticons(self):
        return list(self._emoticons)

    @pyqtProperty("QVariantList", notify=stickersChanged)
    def stickers(self):
        # Picker feed: server-confirmed inventory only, when it has landed;
        # before that (daemon still starting) fall back to the remembered set.
        if self._owned_stickers:
            return sorted(self._owned_stickers)
        return sorted(self._stickers)

    @pyqtProperty(int, notify=mediaRevChanged)
    def mediaRev(self):
        return self._media_rev

    _ANIM_SPEEDS = (1.0, 0.5, 0.25, 0.0)  # combo index → AnimatedImage.speed

    @pyqtProperty(int, notify=animChanged)
    def animIndex(self):
        return self._anim_index

    @pyqtProperty(float, notify=animChanged)
    def animSpeed(self):
        return self._ANIM_SPEEDS[self._anim_index]

    @pyqtSlot(int)
    def setAnimSpeed(self, index: int):
        index = max(0, min(3, int(index or 0)))
        if index == self._anim_index:
            return
        self._anim_index = index
        self.animChanged.emit()
        self._save_state()

    @pyqtSlot(result=str)
    def transcript(self) -> str:
        return "\n".join(
            f"[{_format_timestamp(m.get('timestamp'))}] "
            f"{'me' if m['from_self'] else self._active_name}: {m['text']}"
            for m in self.messages._rows
        )

    @pyqtSlot(str)
    def copyText(self, text: str):
        QGuiApplication.clipboard().setText(text or "")
        self._set_status("Copied")

    @pyqtSlot(str)
    def toggleFavourite(self, steam_id: str):
        self._favourites.symmetric_difference_update({str(steam_id)})
        self._sync_friend_items(self.friends.rows())
        self._save_state()

    @pyqtSlot(str, str)
    def setAlias(self, steam_id: str, alias: str):
        alias = (alias or "").strip()
        self._aliases.pop(steam_id, None) if not alias else self._aliases.update({steam_id: alias})
        self._save_state()
        self._sync_friend_items(self.friends.rows())
        self._refresh_active_name(self.friends.rows())

    @pyqtSlot(str)
    def openProfile(self, steam_id: str):
        QDesktopServices.openUrl(QUrl(f"https://steamcommunity.com/profiles/{steam_id}"))

    @pyqtSlot(str, result="QStringList")
    def friendNameHistory(self, steam_id: str):
        return list(reversed(self._name_history.get(steam_id, [])))  # newest first

    def _record_name(self, steam_id: str, name: str):
        hist = self._name_history.setdefault(steam_id, [])
        if name and name != steam_id and (not hist or hist[-1] != name):  # skip unknown-id fallbacks
            self._name_history[steam_id] = (hist + [name])[-30:]

    @pyqtSlot(str, result=str)
    def emoticonIcon(self, name: str) -> str:
        return chat_format.emoticon_icon(name)

    @pyqtSlot(str, result=str)
    def stickerIcon(self, name: str) -> str:
        return chat_format.icon_src("stickers", name)

    @pyqtProperty(bool, notify=dualWindowChanged)
    def dualWindow(self):
        return self._dual_window

    @pyqtProperty(bool, notify=friendsListChanged)
    def hideOffline(self):
        return self._hide_offline

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
        if self._games_status == text:
            return
        self._games_status = text
        self.gamesStatusChanged.emit()

    def _sync_friend_items(self, rows: list[dict]):
        self.unreadChanged.emit()
        query = self._friend_filter.lower()
        items = []
        for f in rows:
            steam_id = str(f.get("steam_id", ""))
            name = _friend_name(f, self._friend_names)
            self._record_name(steam_id, name)
            name = _display_name(name, self._aliases.get(steam_id, ""))
            game = _friend_game(f)
            detail = _game_detail(f)  # Dota hero + level, or a joinable hint
            rich_text = _rich_text(f)
            device, device_icon = _device_info(f)
            if query and query not in f"{name} {steam_id} {game} {detail} {rich_text} {device}".lower():
                continue
            state = _persona_state(f)
            online = 0 < state < 7
            unread = self._unread.get(steam_id, 0)
            # Offline friends stay listed while they have unread messages, an open chat, or are favourited.
            if self._hide_offline and not (online or unread or steam_id in self._tabs
                                           or steam_id in self._favourites):
                continue
            items.append(
                {
                    "steamId": steam_id,
                    "name": name,
                    "status": aurelia.persona_label(state),
                    "statusColor": _STATUS_COLORS.get(state, _STATUS_COLORS[0]),
                    "game": game,
                    "gameDetail": detail,
                    "richText": rich_text,
                    "joinable": _friend_joinable(f),
                    "online": online,
                    "device": device,
                    "deviceIcon": device_icon,
                    "unread": unread,
                    "favourite": steam_id in self._favourites,
                    "lastSeen": _last_seen_text(f),
                }
            )
        items.sort(key=lambda i: (not i["favourite"], not i["unread"]))
        if items != self._friend_items:  # unchanged polls must not churn the UI
            self._friend_items = items
            self.friendsListChanged.emit()
        self._sync_tab_items()

    def _sync_tab_items(self):
        rows = {str(f.get("steam_id")): f for f in self.friends.rows()}
        items = []
        for sid in self._tabs:
            f = rows.get(sid, {"steam_id": sid})
            device, device_icon = _device_info(f)
            items.append({
                "steamId": sid,
                "name": _display_name(_friend_name(f, self._friend_names), self._aliases.get(sid, "")),
                "alias": self._aliases.get(sid, ""),
                "subtitle": _friend_subtitle(f),
                "statusColor": _STATUS_COLORS.get(_persona_state(f), _STATUS_COLORS[0]),
                "device": device,
                "deviceIcon": device_icon,
                "unread": self._unread.get(sid, 0),
                "lastSeen": _last_seen_text(f),
                "richText": _rich_text(f),
            })
        if items != self._tab_items:
            self._tab_items = items
            self.chatTabsChanged.emit()

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
                _friend_state_rank(row),
                (
                    row.get("persona_name")
                    or self._friend_names.get(str(row.get("steam_id")))
                    or str(row.get("steam_id", ""))
                ).lower(),
            )
        )
        self.friends.set_rows(rows)
        self._sync_friend_items(rows)
        self._refresh_active_name(rows)

    def _refresh_active_name(self, rows: list[dict]):
        # A tab opened before the roster arrived shows its SteamID until the name lands.
        if not self._active_id:
            return
        active = next(
            (row for row in rows if str(row.get("steam_id")) == self._active_id),
            None,
        )
        if active is None:  # roster not loaded: keep the known name, claim no status
            name = _display_name(self._friend_names.get(self._active_id) or self._active_id,
                                 self._aliases.get(self._active_id, ""))
            if name != self._active_name or self._active_subtitle:
                self._active_name = name
                self._active_subtitle = ""
                self.activeFriendChanged.emit()
            return
        name = _display_name(_friend_name(active, self._friend_names),
                             self._aliases.get(self._active_id, ""))
        subtitle = _friend_subtitle(active)
        if name != self._active_name or subtitle != self._active_subtitle:
            self._active_name = name
            self._active_subtitle = subtitle
            self.activeFriendChanged.emit()
            self._rebuild_messages()  # sender labels carry the resolved name

    @pyqtSlot(str)
    def setFriendFilter(self, text: str):
        self._friend_filter = (text or "").strip()
        self._sync_friend_items(self.friends.rows())

    @pyqtSlot(bool)
    def setHideOffline(self, on: bool):
        if self._hide_offline == on:
            return
        self._hide_offline = on
        self._sync_friend_items(self.friends.rows())
        self._save_state()

    def _make_row(self, m: dict, prev: dict | None) -> dict:
        # Dialog behaviour: the sender's name opens each speaking turn — ours too —
        # consecutive bubbles from the same sender stay anonymous, a long silence
        # re-opens the group.
        ts = int(m.get("timestamp") or 0)
        prev_ts = int(prev.get("timestamp") or 0) if prev else 0
        new_turn = prev is None or prev["from_self"] != m["from_self"] or (
            prev_ts and ts and ts - prev_ts > 900)
        img = _image_url(m["text"])
        sticker = chat_format.sticker_name(m["text"])
        only_sticker = chat_format.sticker_only(m["text"])
        vid = chat_format.youtube_card(m["text"])
        if vid:
            self._ensure_video_info(vid)
        if sticker and sticker not in self._stickers:
            self._stickers.add(sticker)
            self.stickersChanged.emit()
            self._save_state()
            self._warm_media_cache()  # fetch + convert so this row animates after mediaRev bumps
        fresh = [n for n in chat_format.uni_emoticon_names(m["text"]) if n not in self._emoticons]
        if fresh:
            self._emoticons.extend(fresh)
            self.emoticonsChanged.emit()
        return {
            "text": m["text"],
            "from_self": m["from_self"],
            "timestamp": m.get("timestamp"),
            "html": "" if (img or only_sticker or vid) else chat_format.to_html(m["text"]),
            "time": _format_timestamp(m.get("timestamp")),
            "imageUrl": img,
            "stickerUrl": chat_format.icon_src("stickers", sticker) if only_sticker else "",
            "stickerName": sticker if only_sticker else "",
            "videoId": vid,
            "sender": self._own_name or "You" if m["from_self"] else self._active_name,
            "showName": new_turn,
        }

    def _ensure_video_info(self, vid: str):
        # One background oEmbed fetch per video per session; failures stay cached
        # so history rebuilds don't re-spam the endpoint.
        if vid in self._video_info or vid in self._video_fetching:
            return
        self._video_fetching.add(vid)
        self._video_workers = [w for w in self._video_workers if w.isRunning()]
        worker = _Fetch(chat_format.youtube_info, vid)
        worker.done.connect(lambda d, v=vid: self._on_video_info(v, d))
        worker.start()
        self._video_workers.append(worker)

    def _on_video_info(self, vid: str, data: Any):
        self._video_fetching.discard(vid)
        self._video_info[vid] = data if isinstance(data, dict) and not data.get("error") else {}
        self.videoInfoChanged.emit()

    def _on_owned_media(self, data: Any):
        """Merge the account's owned media (`aurelia chat stickers`); proto names
        come colon-wrapped (`:steamhappy:`) while ours use the ːnameː spelling."""
        if not isinstance(data, dict):
            return
        owned_e = [n.strip(":") for n in data.get("emoticons", []) if isinstance(n, str)]
        fresh = [n for n in owned_e if n and n not in self._emoticons]
        if fresh:
            self._emoticons.extend(fresh)
            self.emoticonsChanged.emit()
        owned = [n for n in data.get("stickers", []) if isinstance(n, str) and n]
        self._owned_stickers.update(owned)
        fresh = [n for n in owned if n not in self._stickers]
        if fresh:
            self._stickers.update(fresh)
            self._save_state()
        self.stickersChanged.emit()
        self._warm_media_cache()

    def _warm_media_cache(self):
        """Background-prefetch any stickers not yet cached, so pickers and chat
        rows render animated local GIFs instead of frozen remote APNGs."""
        todo = sorted(n for n in self._stickers if n not in self._media_warmed)
        if not todo:
            return
        self._media_warmed.update(todo)
        worker = _Fetch(lambda: [chat_format.download_icon("stickers", n) for n in todo])
        worker.done.connect(self._on_media_warm)
        worker.start()
        self._cache_workers.append(worker)

    def _on_media_warm(self, _result: Any):
        self._media_rev += 1  # QML source bindings re-resolve now that files are local
        self.mediaRevChanged.emit()

    def _on_steam_cache(self, mined: dict):
        if not isinstance(mined, dict):
            return
        fresh = [n for n in mined.get("emoticons", []) if n not in self._emoticons]
        if fresh:
            self._emoticons.extend(fresh)
            self.emoticonsChanged.emit()
        fresh = [n for n in mined.get("stickers", []) if n not in self._stickers]
        if fresh:
            self._stickers.update(fresh)
            self.stickersChanged.emit()
            self._save_state()
        self._warm_media_cache()

    def _own_dup(self, body: str) -> bool:
        """True when this already-displayed own line would be appended again."""
        return any(
            r["from_self"] and (
                r["text"] == body
                or (_ugc_key(r["text"]) and _ugc_key(r["text"]) == _ugc_key(body))
            )
            for r in self.messages._rows[-5:]
        )

    def _append_message(self, text: str, from_self: bool, timestamp: Any):
        rows = self.messages._rows
        row = self._make_row(
            {"text": text, "from_self": from_self, "timestamp": timestamp},
            rows[-1] if rows else None,
        )
        self.messages.append_row(row)

    def _rebuild_messages(self, raw: list[dict] | None = None):
        if raw is None:
            raw = [{"text": m["text"], "from_self": m["from_self"], "timestamp": m.get("timestamp")}
                   for m in self.messages._rows]
        prev, rows = None, []
        for m in raw:
            rows.append(self._make_row(m, prev))
            prev = m
        self.messages.set_rows(rows)

    def _set_status(self, text: str):
        if self._status == text:
            return
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
        self.playingChanged.emit()

    def _sync_presence(self, profile: Any):
        if not isinstance(profile, dict) or profile.get("error"):
            return
        name = str(profile.get("persona_name") or "")
        if name and name != self._own_name:
            self._own_name = name
            self.ownNameChanged.emit()
        if time.monotonic() >= self._presence_pin_until:
            state = _persona_state(profile)
            self._set_presence_mode(_PRESENCE_STATE_MODES.get(state, "offline"))
        game = str(profile.get("game_name") or "").strip()
        if not game and profile.get("game_app_id"):
            game = f"app {profile['game_app_id']}"
        if self._steam_game and not game:
            desktop_notify("aur-chat", f"{self._steam_game} closed")
            self._playing_options = ""
        self._steam_game = game
        self._steam_app_id = str(profile.get("game_app_id") or "")
        self._render_presence()

    def _track_local_game(self, name: str, process: Any, app_id: str = "", options: str = ""):
        self._local_game = name
        self._local_app_id = app_id
        self._playing_options = options or ""
        self._local_game_process = process
        self._render_presence()

    def _poll_local_game(self):
        if self._handoff and self._handoff[1].poll() is not None:
            name, proc, log = self._handoff
            self._handoff = None
            if proc.returncode:  # Steam refused the launch request
                why = aurelia.last_error(log)
                self._set_games_status(f"Couldn't launch {name}: {why}")
                desktop_notify("aur-chat", f"Launch failed: {name}", urgency="critical")
        process = self._local_game_process
        if not process:
            return
        try:
            finished = process.poll() is not None
        except (OSError, AttributeError):
            finished = True
        if finished:
            if self._local_game:
                desktop_notify("aur-chat", f"{self._local_game} closed")
            self._local_game_process = None
            self._local_game = ""
            self._local_app_id = ""
            self._playing_options = ""
            self._render_presence()

    @pyqtProperty(bool, notify=presenceChanged)
    def gameRunning(self):
        return bool(self._steam_game or self._local_game)

    @pyqtProperty(str, notify=playingChanged)
    def playingTitle(self):
        game = self._local_game or self._steam_game
        if not game:
            return "No Game"
        return f"{game} · {self._playing_options}" if self._playing_options else game

    @pyqtSlot(bool)
    def endGame(self, force: bool):
        app_id = self._local_app_id or self._steam_app_id
        if self._stop_worker and self._stop_worker.isRunning():
            return
        name = self._local_game or self._steam_game
        if not app_id:
            self._on_game_stopped(name, {"error": "unknown app id"})
            return
        if self._local_game:
            self._stop_worker = _Fetch(aurelia.stop_game, app_id, force=force)
        elif force:  # launched through Steam: kill its processes directly
            self._stop_worker = _Fetch(aurelia.kill_steam_game, app_id)
        else:  # launched through Steam: ask Steam to close it
            self._stop_worker = _Fetch(aurelia.close_steam_game, app_id)
        self._stop_worker.done.connect(lambda d, n=name: self._on_game_stopped(n, d))
        self._set_status(f"{'Force ending' if force else 'Ending'} {name}…")
        self._stop_worker.start()

    def _on_game_stopped(self, name: str, data: Any):
        if isinstance(data, dict) and not data.get("error"):
            self._set_status(f"Ended {name}")
            desktop_notify("aur-chat", f"Ended {name}")
            QTimer.singleShot(2000, self.refreshFriends)
        else:
            process = self._local_game_process
            if process and process.poll() is None:
                process.terminate()
                self._set_status(f"Terminated the launcher for {name}")
            else:
                err = data.get("error", "stop failed") if isinstance(data, dict) else "stop failed"
                self._set_status(f"{str(err)[:140]} — try Force end")
        self._playing_options = ""
        self.presenceChanged.emit()
        self.playingChanged.emit()

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

    @pyqtSlot(str)
    def setOwnName(self, name: str):
        name = (name or "").strip()
        if not name or name == self._own_name or (self._worker and self._worker.isRunning()):
            return
        self._set_busy(True)
        self._worker = _Fetch(aurelia.set_persona_name, name)
        self._worker.done.connect(self._on_own_name)
        self._worker.start()

    def _on_own_name(self, data: Any):
        self._set_busy(False)
        if not isinstance(data, dict) or data.get("error") or not data.get("persona_name"):
            self._set_status(str(data.get("error", data) if isinstance(data, dict) else data)[:160])
            return
        name = str(data["persona_name"])
        if name != self._own_name:
            self._own_name = name
            self.ownNameChanged.emit()
        self.refreshFriends()  # presence and status line now show the new name

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
        restored = visible and not self._window_visible
        self._window_visible = visible
        if restored:
            self._focus_latest_unread()
        self._ensure_events()

    @pyqtSlot(str)
    def refreshGames(self, search: str = ""):
        self._games_search = (search or "").strip()
        if self._worker and self._worker.isRunning():
            return
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
        was_checked, prev = self._updates_checked, self._updates
        self._updates = {str(u.get("app_id")) for u in data["updates"]}
        self._updates |= _steam_update_pending(installed)
        self._updates_checked = True
        self._apply_update_states()
        if self._updates != prev or not was_checked:  # silent when nothing changed
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
                self._track_local_game(row["name"], process, app_id, row.get("launchOptions") or "")
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
            options = (row or {}).get("launchOptions", "")
            if aurelia.has_launch_script(app_id):  # e.g. Dota 2 inside Steam's sniper runtime, without Steam
                if app_id in self._updating:  # `aurelia play` would update too, colliding with the running download
                    self._set_games_status(f"{name} is updating, wait for it to finish")
                    return
                self._track_local_game(name, aurelia.launch_standalone(app_id, options), app_id, options)
                how = "launch script"
            else:
                self._handoff = (name, aurelia.launch_steam(app_id), aurelia.play_log(app_id))
                self._playing_options = options  # Steam applies these itself once it starts the game
                self.playingChanged.emit()
                how = "Steam"
            self._set_games_status(f"Launching {name} via {how}…")
            desktop_notify("aur-chat", f"Launching {name}")
        except OSError as e:
            self._set_games_status(str(e))

    @pyqtSlot()
    def refreshFriends(self):
        # Own worker slot: sharing `_worker` let chat/search/games fetches skip this poll
        # and orphan a running QThread when replaced.
        if self._friends_worker and self._friends_worker.isRunning():
            return
        self._friends_worker = _Fetch(_social_snapshot)
        self._friends_worker.done.connect(self._on_friends)
        self._friends_worker.start()

    def _on_friends(self, data: Any):
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
            friends_log("bad", str(data)[:80])
            self._set_status(str(data.get("error", data))[:120])
            return
        unnamed = sum(1 for r in data if not r.get("persona_name"))
        friends_log("data", f"rows={len(data)} unnamed={unnamed} retries={self._friends_empty_retries}")
        if not data:
            if self._friends_empty_retries < _FRIENDS_EMPTY_RETRIES:
                self._friends_empty_retries += 1
                self._set_status("Loading friends…")
                QTimer.singleShot(1500, self.refreshFriends)
                return
            # Steam fills the roster within a poll or two; a whole cycle (>15 s)
            # of nothing means a partial response. Past saving grace, show empty
            # rather than stale rows — but keep presence memory so the roster
            # coming back can't storm the tray with "came online".
            friends_log("empty-final", f"after={self._friends_empty_retries} retries, wiping list")
            self.friends.set_rows([])
            self._sync_friend_items([])
            self._set_status("0 friends")
            return
        self._friends_empty_retries = 0
        rows = sorted(
            self._carry_forward(data),
            key=_friend_sort_key,
        )
        for row in rows:
            name = row.get("persona_name")
            if name:
                self._friend_names[str(row.get("steam_id"))] = name
        self._notify_presence_changes(rows)
        self.friends.set_rows(rows)
        self._sync_friend_items(rows)
        self._refresh_active_name(rows)
        self._load_missing_friend_names(rows)
        self._set_status(f"{len(rows)} friends")
        self._ensure_events()

    @pyqtSlot(str)
    def pickFriend(self, steam_id: str):
        if not _valid_steam_id(steam_id) or steam_id == self._active_id:
            return
        if steam_id not in self._tabs:
            self._tabs.append(steam_id)
        self._show_chat(steam_id)
        self._save_state()

    @pyqtSlot(str)
    def closeChat(self, steam_id: str):
        if steam_id not in self._tabs:
            return
        index = self._tabs.index(steam_id)
        self._tabs.remove(steam_id)
        if steam_id == self._active_id:  # show the neighbouring tab instead
            self._show_chat(self._tabs[min(index, len(self._tabs) - 1)] if self._tabs else "")
        self._sync_tab_items()
        self._save_state()

    @pyqtSlot(bool)
    def setDualWindow(self, on: bool):
        if self._dual_window == on:
            return
        self._dual_window = on
        self.dualWindowChanged.emit()
        self._save_state()

    def _mark_read(self, steam_id: str):
        self._unread.pop(steam_id, None)
        self._unread_at.pop(steam_id, None)

    def _focus_latest_unread(self):  # on restore: open the chat with the newest unread message
        unread = [sid for sid, n in self._unread.items() if n > 0]
        if not unread:
            return
        self.pickFriend(max(unread, key=lambda sid: self._unread_at.get(sid, 0.0)))
        self._mark_read(self._active_id)  # pickFriend is a no-op when that chat is already open
        self._sync_friend_items(self.friends.rows())

    def _show_chat(self, steam_id: str):  # "" clears the chat view
        self._active_id = steam_id
        self._mark_read(steam_id)
        row = next(
            (r for r in self.friends.rows() if str(r.get("steam_id")) == steam_id),
            None,
        )
        friend = row or {"steam_id": steam_id}
        self._active_name = _friend_name(friend, self._friend_names) if steam_id else ""
        # No roster row yet: the status is unknown, so show none — and resolve the
        # name right away instead of waiting for the next friends sync.
        self._active_subtitle = _friend_subtitle(friend) if row else ""
        if steam_id and not row:
            self._load_missing_friend_names([friend])
        self._sync_friend_items(self.friends.rows())
        self.activeFriendChanged.emit()
        self.messages.clear()
        if steam_id:
            # Keep fetches still running referenced: dropping one mid-run aborts Qt.
            self._history_workers = [w for w in self._history_workers if w.isRunning()]
            worker = _Fetch(aurelia.chat_history, steam_id)
            worker.done.connect(lambda d, s=steam_id: self._on_history(s, d))
            worker.start()
            self._history_workers.append(worker)

    def _save_state(self):
        state = {
            "tabs": self._tabs,
            "active": self._active_id,
            "dual": self._dual_window,
            "hide_offline": self._hide_offline,
            "friend_font": self._friend_font,
            "friends_width": self._friends_width,
            "chat_font": self._chat_font,
            "stickers": sorted(self._stickers),
            "favourites": sorted(self._favourites),
            "sent_media": self._sent_media[-500:],
            "aliases": self._aliases,
            "name_history": self._name_history,
            "friends_interval": self._friends_interval,
            "games_interval": self._games_interval,
            "anim_index": self._anim_index,
        }
        try:
            _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            _STATE_FILE.write_text(json.dumps(state))
        except OSError as e:
            print(f"[aur-chat] could not save state: {e}", file=sys.stderr, flush=True)

    def _on_history(self, steam_id: str, data: Any):
        if steam_id != self._active_id or not isinstance(data, list):
            return
        ordered = sorted(data, key=lambda r: (r.get("timestamp") is None, r.get("timestamp") or 0))
        used: set[int] = set()
        for r in ordered:
            if r.get("from_self") and not r.get("message"):
                # Steam blanks our own emoticon/sticker sends in history; restore
                # the wire from the local send journal (30s clock skew tolerated).
                ts = r.get("timestamp")
                if not ts:
                    continue
                cands = [(abs(int(ts) - m[1]), i) for i, m in enumerate(self._sent_media)
                         if m[0] == steam_id and i not in used and abs(int(ts) - m[1]) <= 30]
                if cands:
                    _, i = min(cands)
                    used.add(i)
                    r["message"] = self._sent_media[i][2]
        self._rebuild_messages([
            {"text": r.get("message", ""), "from_self": bool(r.get("from_self")),
             "timestamp": r.get("timestamp")}
            for r in ordered
        ])

    def _ensure_events(self):
        # One `aurelia chat events` process streams every friend's messages;
        # restart it here if it exited (daemon restart, logout…).
        if self._events and self._events.state() == QProcess.ProcessState.Running:
            return
        self._events = EventStream()
        self._events.chatEvent.connect(self._route_event)
        self._events.start_stream()

    def _route_event(self, ev: Any):
        if not isinstance(ev, dict):
            return
        sid = str(ev.get("from") or "")
        if not sid or sid not in {str(f["steam_id"]) for f in self.friends.rows()}:
            return  # group chats and strangers are not ours to show
        self._on_chat_event(sid, ev, active=sid == self._active_id)

    def _on_chat_event(self, steam_id: str, ev: dict, *, active: bool):
        kind = ev.get("event")
        if kind == "message":
            text = ev.get("text", "")  # plain text for notifications; "bbcode" keeps the formatting
            shown = active and steam_id == self._active_id
            if shown:
                self._append_message(ev.get("bbcode", text), False, ev.get("timestamp"))
            if not (shown and (self._window_visible or self._dual_window)):  # not on screen: unread
                self._unread[steam_id] = self._unread.get(steam_id, 0) + 1
                self._unread_at[steam_id] = time.time()
                self._sync_friend_items(self.friends.rows())
                self.messageArrived.emit()
            self._maybe_notify(steam_id, text)
        elif kind == "echo" and active and steam_id == self._active_id:
            body = ev.get("bbcode", ev.get("text", ""))
            # Our send already appended this line locally (uploads may race the
            # echo and Steam can echo on a different CDN host, so match text
            # exactly or by the image's unique /ugc/<id>).
            if not self._own_dup(body):
                self._append_message(body, True, ev.get("timestamp"))
        elif kind == "typing" and active and steam_id == self._active_id:
            self._set_status(f"{self._active_name} is typing…")
        elif kind == "error":
            self._set_status(ev.get("message", "chat error"))

    def _on_sent(self, steam_id: str, wire: str, data: Any):
        if isinstance(data, dict) and data.get("error"):
            self._set_status(str(data.get("error")))
            return
        srv = data.get("server_timestamp") if isinstance(data, dict) else None
        if srv:  # journal timestamps drift from our local clock; Steam's is canonical
            for m in reversed(self._sent_media):
                if m[0] == steam_id and m[2] == wire:
                    m[1] = int(srv)
                    break
        stored = (data or {}).get("modified_message") if isinstance(data, dict) else None
        if steam_id != self._active_id or not stored:
            return
        rows = self.messages._rows
        for i, row in reversed(list(enumerate(rows))):
            # Steam's stored text for our line: raw commands (/flip, /random) expand
            # here; rows already carrying our [code]/[quote]/[spoiler] bbcode keep
            # aur-chat's styling.
            if row["from_self"] and row["text"] == wire and not row["text"].startswith("["):
                raw = {"text": stored, "from_self": True, "timestamp": row.get("timestamp")}
                self.messages.update_row(
                    i, self._make_row(raw, rows[i - 1] if i else None)
                )
                break

    def _carry_forward(self, rows: list[dict]) -> list[dict]:
        """Splice stale-but-truthful persona data into partially-synced rows.

        A daemon whose persona stream lags the roster sends rows with None
        persona_state; treating that as 0 would out dozens of friends as
        offline for a poll. Values the new row still provides (explicit
        offline 0, a fresh game) always win."""
        missing = [r for r in rows if r.get("persona_state") is None]
        if not missing:
            return rows
        previous = {str(f.get("steam_id")): f for f in self.friends.rows()}
        spliced = fields = 0
        for row in missing:
            prev = previous.get(str(row.get("steam_id")))
            if not prev:
                continue
            touched = False
            for key in _CARRY_FIELDS:
                if row.get(key) is None and prev.get(key) is not None:
                    row[key] = prev[key]
                    fields += 1
                    touched = True
            spliced += touched
        friends_log("carry", f"rows={len(rows)} missing={len(missing)} spliced={spliced} fields={fields}")
        return rows

    def _notify_presence_changes(self, rows: list[dict]):
        online = {str(f.get("steam_id")): 0 < _persona_state(f) < 7 for f in rows}
        previous, self._last_online = self._last_online, online
        if previous is None:
            return
        dropped: list[str] = []
        for row in rows:
            sid = str(row.get("steam_id"))
            if sid not in previous or previous[sid] == online[sid]:
                continue
            name = _friend_name(row, self._friend_names)
            if online[sid]:
                desktop_notify("aur-chat", f"{name} came online")
            else:
                dropped.append(name)
        was_online = sum(previous.values())
        # A broken sync outs dozens at once; real log-offs trickle. If a
        # landslide hit (>40% of everyone online), one summary notification
        # beats a storm of take-backs.
        if len(dropped) >= 5 and len(dropped) * 5 >= was_online * 2:
            friends_log("mass-offline", f"dropped={len(dropped)} was_online={was_online}")
            desktop_notify("aur-chat", f"{len(dropped)} friends went offline")
            return
        for name in dropped:
            desktop_notify("aur-chat", f"{name} went offline")
        if dropped:
            friends_log("presence", f"offline={len(dropped)} was_online={was_online}")

    def _maybe_notify(self, steam_id: str, text: str):
        name = self._friend_names.get(str(steam_id)) or self.friends.name_for(steam_id)
        preview = text if len(text) < 180 else text[:177] + "…"
        desktop_notify(f"aur-chat · {name}", preview)

    @pyqtSlot(str)
    def sendMessage(self, text: str):
        text = (text or "").strip()
        if text and self._active_id:
            self._send_raw(chat_format.to_bbcode(text))

    @pyqtSlot(str)
    def sendSticker(self, name: str):
        """Send a sticker as raw sticker bbcode — bypasses to_bbcode's [ escaping."""
        name = (name or "").strip()
        if name and self._active_id:
            self._send_raw(f'[sticker type="{name}" limit="0"][/sticker]')

    def _send_raw(self, wire: str):
        ts = int(datetime.now().timestamp())
        self._append_message(wire, True, ts)
        self._sent_media.append([self._active_id, ts, wire])
        if "[emoticon=" in wire or "[sticker " in wire:
            self._save_state()  # media payloads don't survive Steam's history
        # One-shot `chat send` per message; the echo/confirm flows back on the
        # shared event stream. Keep workers referenced: dropping one mid-run aborts Qt.
        self._send_workers = [w for w in self._send_workers if w.isRunning()]
        worker = _Fetch(aurelia.chat_send, self._active_id, wire)
        worker.done.connect(lambda d, s=self._active_id, w=wire: self._on_sent(s, w, d))
        worker.start()
        self._send_workers.append(worker)

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
        # Only show a local row when the commit URL renders here; Steam's own echo of
        # the stored message lands otherwise and carries the renderable bare URL.
        if isinstance(data, dict) and _image_url(data.get("url")) \
                and not self._own_dup(data["url"]):
            self._append_message(data["url"], True, int(datetime.now().timestamp()))

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

    @staticmethod
    def _stop_stream(stream: QProcess):
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
        if self._events:
            self._stop_stream(self._events)
