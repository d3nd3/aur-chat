"""Read and launch Steam's non-Steam shortcuts without starting Steam."""

from __future__ import annotations

import glob
import os
import re
import shlex
import subprocess
from pathlib import Path
from typing import Any, Iterator

try:
    import vdf
except ImportError:
    vdf = None  # type: ignore


def _unsigned_app_id(raw: int) -> str:
    return str(raw & 0xFFFFFFFF)


def _steam_roots() -> list[Path]:
    return [
        Path.home() / ".steam" / "root",
        Path.home() / ".steam" / "steam",
        Path.home() / ".steam" / "debian-installation",
        Path.home() / ".local" / "share" / "Steam",
        Path.home() / ".var" / "app" / "com.valvesoftware.Steam" / ".local" / "share" / "Steam",
    ]


def _shortcut_paths() -> list[Path]:
    paths = [
        Path(p)
        for root in _steam_roots()
        for p in glob.glob(str(root / "userdata" / "*" / "config" / "shortcuts.vdf"))
    ]
    seen: set[str] = set()
    return [p for p in paths if not (str(p.resolve()) in seen or seen.add(str(p.resolve())))]


def _steam_root(shortcuts: Path) -> Path:
    return shortcuts.parents[3]


def _text(entry: dict[str, Any], key: str) -> str:
    value = entry.get(key, "")
    return value if isinstance(value, str) else str(value)


def _app_id(entry: dict[str, Any]) -> str:
    try:
        return _unsigned_app_id(int(entry.get("appid", 0)))
    except (TypeError, ValueError):
        return "0"


def _read_vdf(path: Path, *, binary: bool = False) -> dict[str, Any]:
    if vdf is None:
        return {}
    try:
        with path.open("rb" if binary else "r", encoding=None if binary else "utf-8") as f:
            return vdf.binary_load(f) if binary else vdf.load(f)
    except (OSError, ValueError, TypeError):
        return {}


def _entries() -> Iterator[tuple[Path, dict[str, Any]]]:
    for path in _shortcut_paths():
        data = _read_vdf(path, binary=True)
        for entry in (data.get("shortcuts") or {}).values():
            if isinstance(entry, dict) and _text(entry, "AppName").strip():
                yield path, entry


def _compat_mapping(root: Path) -> dict[str, Any]:
    data = _read_vdf(root / "config" / "config.vdf")
    steam = (
        (data.get("InstallConfigStore") or {})
        .get("Software", {})
        .get("Valve", {})
        .get("Steam", {})
    )
    return steam.get("CompatToolMapping") or data.get("CompatToolMapping") or {}


def _compat_tool(root: Path, app_id: str) -> str:
    entry = _compat_mapping(root).get(str(app_id))
    return _text(entry, "name").strip() if isinstance(entry, dict) else ""


def _library_roots(root: Path) -> list[Path]:
    data = {}
    for path in (root / "config" / "libraryfolders.vdf", root / "steamapps" / "libraryfolders.vdf"):
        data = _read_vdf(path)
        if data:
            break
    folders = data.get("libraryfolders") or {}
    roots = [root]
    for folder in folders.values():
        path = folder.get("path") if isinstance(folder, dict) else folder
        if path:
            roots.append(Path(str(path)).expanduser())
    seen: set[str] = set()
    return [p for p in roots if not (str(p.resolve()) in seen or seen.add(str(p.resolve())))]


def _tool_bases(root: Path) -> list[Path]:
    bases = [
        base
        for library in _library_roots(root)
        for base in (library / "compatibilitytools.d", library / "steamapps" / "common")
    ]
    bases += [Path("/usr/share/steam/compatibilitytools.d"), Path("/usr/local/share/steam/compatibilitytools.d")]
    bases += [Path(p) for p in os.environ.get("STEAM_EXTRA_COMPAT_TOOLS_PATHS", "").split(":") if p]
    seen: set[str] = set()
    return [p for p in bases if not (str(p.resolve()) in seen or seen.add(str(p.resolve())))]


def _manifest_tool(manifest: Path, name: str) -> Path | None:
    data = _read_vdf(manifest)
    tools = (data.get("compatibilitytools") or {}).get("compat_tools") or {}
    for key, meta in tools.items():
        if str(key).lower() != name.lower() or not isinstance(meta, dict):
            continue
        install = Path(str(meta.get("install_path") or ".")).expanduser()
        return install if install.is_absolute() else manifest.parent / install
    return None


def _find_compat_tool(root: Path, name: str) -> Path | None:
    for base in _tool_bases(root):
        direct = base / name
        if (direct / "proton").is_file():
            return direct / "proton"
        if not base.is_dir():
            continue
        manifests = list(base.glob("*/compatibilitytool.vdf")) + list(base.glob("*.vdf"))
        for manifest in manifests:
            path = _manifest_tool(manifest, name)
            if path and (path / "proton").is_file():
                return path / "proton"
    return None


def _compat_data_path(root: Path, app_id: str) -> Path:
    candidates = [library / "steamapps" / "compatdata" / app_id for library in _library_roots(root)]
    return next((path for path in candidates if path.is_dir()), candidates[0])


_ENV_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def _field_path(raw: str) -> str:
    value = raw.strip()
    if len(value) > 1 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    return os.path.expanduser(os.path.expandvars(value))


def _command(
    entry: dict[str, Any], cwd: str | None
) -> tuple[list[str], list[str], dict[str, str], set[str]]:
    raw_exe = _text(entry, "Exe").strip()
    try:
        executable = shlex.split(raw_exe)
        options = shlex.split(_text(entry, "LaunchOptions"))
    except ValueError as error:
        raise OSError(f"invalid Steam shortcut command: {error}") from error
    path_exe = _field_path(raw_exe)
    if path_exe and (
        Path(path_exe).is_file() or (cwd and (Path(cwd) / path_exe).is_file())
    ):
        executable = [path_exe]
    if not executable:
        raise OSError("Steam shortcut has no executable")
    env = os.environ.copy()
    assigned: set[str] = set()
    while options and _ENV_ASSIGNMENT.match(options[0]):
        key, value = options.pop(0).split("=", 1)
        env[key] = os.path.expanduser(os.path.expandvars(value))
        assigned.add(key)
    if "%command%" in options:
        marker = options.index("%command%")
        wrapper, command = options[:marker], executable + options[marker + 1 :]
    else:
        wrapper, command = [], executable + options
    return command, wrapper, env, assigned


def _start_dir(entry: dict[str, Any]) -> str | None:
    raw = _text(entry, "StartDir").strip()
    if not raw:
        return None
    path = _field_path(raw)
    if Path(path).is_dir():
        return path
    try:
        parts = shlex.split(raw)
    except ValueError as error:
        raise OSError(f"invalid Steam shortcut start directory: {error}") from error
    return _field_path(parts[0]) if parts else None


def load_shortcuts() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path, entry in _entries():
        app_id = _app_id(entry)
        rows.append(
            {
                "appId": app_id,
                "name": _text(entry, "AppName").strip(),
                "installed": True,
                "isShortcut": True,
                "launchOptions": _text(entry, "LaunchOptions").strip(),
                "exe": _text(entry, "Exe"),
                "startDir": _text(entry, "StartDir"),
                "compatTool": _compat_tool(_steam_root(path), app_id),
            }
        )
    return sorted(rows, key=lambda row: row["name"].lower())


def launch_shortcut(app_id: str) -> subprocess.Popen:
    for path, entry in _entries():
        if _app_id(entry) != str(app_id):
            continue
        root = _steam_root(path)
        cwd = _start_dir(entry)
        command, wrapper, env, assigned = _command(entry, cwd)
        tool_name = _compat_tool(root, str(app_id))
        print(
            f"[aur-chat] shortcut {app_id}: root={root}, tool={tool_name or 'native'}",
            flush=True,
        )
        if tool_name:
            proton = _find_compat_tool(root, tool_name)
            print(f"[aur-chat] compatibility executable: {proton or 'not found'}", flush=True)
            if proton is None:
                raise OSError(f"compatibility tool not found: {tool_name}")
            prefix = _compat_data_path(root, str(app_id))
            defaults = {
                "SteamAppId": str(app_id),
                "SteamGameId": str(app_id),
                "STEAM_COMPAT_APP_ID": str(app_id),
                "STEAM_COMPAT_CLIENT_INSTALL_PATH": str(root),
                "STEAM_COMPAT_DATA_PATH": str(prefix),
                "STEAM_COMPAT_INSTALL_PATH": cwd or str(Path(command[0]).parent),
            }
            for key, value in defaults.items():
                if key not in assigned:
                    env[key] = value
            command = [*wrapper, str(proton), "waitforexitandrun", *command]
        else:
            command = [*wrapper, *command]
        print(
            f"[aur-chat] launching shortcut {app_id} ({tool_name or 'native'}): "
            f"{shlex.join(command)}",
            flush=True,
        )
        print(f"[aur-chat] working directory: {cwd or os.getcwd()}", flush=True)
        for key in ("SteamAppId", "STEAM_COMPAT_CLIENT_INSTALL_PATH", "STEAM_COMPAT_DATA_PATH"):
            if key in env:
                print(f"[aur-chat] {key}={env[key]}", flush=True)
        return subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            start_new_session=True,
            stdin=subprocess.DEVNULL,
        )
    raise OSError(f"Steam shortcut not found: {app_id}")
