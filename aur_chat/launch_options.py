"""Read/write Steam launch options (library apps + shortcuts)."""

from __future__ import annotations

import glob
import os
from pathlib import Path
from typing import Any

try:
    import vdf
except ImportError:
    vdf = None  # type: ignore

from .steam_shortcuts import _shortcut_paths, _unsigned_app_id


def _localconfig_paths() -> list[Path]:
    out: list[Path] = []
    for sp in _shortcut_paths():
        out.append(sp.parent / "localconfig.vdf")
    seen: set[str] = set()
    unique: list[Path] = []
    for p in out:
        if p.is_file():
            key = str(p.resolve())
            if key not in seen:
                seen.add(key)
                unique.append(p)
    return unique


def _signed_app_id(unsigned: str) -> int:
    u = int(unsigned) & 0xFFFFFFFF
    return u if u < 2**31 else u - 2**32


def get_shortcut_launch_options(app_id: str) -> str:
    if vdf is None:
        return ""
    for path in _shortcut_paths():
        try:
            with path.open("rb") as f:
                data = vdf.binary_load(f)
        except OSError:
            continue
        for entry in (data.get("shortcuts") or {}).values():
            if not isinstance(entry, dict):
                continue
            if _unsigned_app_id(int(entry.get("appid", 0))) == app_id:
                return (entry.get("LaunchOptions") or "").strip()
    return ""


def set_shortcut_launch_options(app_id: str, text: str) -> bool:
    if vdf is None:
        return False
    for path in _shortcut_paths():
        try:
            with path.open("rb") as f:
                data = vdf.binary_load(f)
        except OSError:
            continue
        shortcuts = data.get("shortcuts") or {}
        for entry in shortcuts.values():
            if not isinstance(entry, dict):
                continue
            if _unsigned_app_id(int(entry.get("appid", 0))) == app_id:
                entry["LaunchOptions"] = text
                with path.open("wb") as f:
                    vdf.binary_dump(data, f)
                return True
    return False


def get_steam_launch_options(app_id: str) -> str:
    if vdf is None:
        return ""
    for path in _localconfig_paths():
        try:
            with path.open("r", encoding="utf-8", errors="replace") as f:
                data = vdf.load(f)
        except OSError:
            continue
        apps = (
            data.get("UserLocalConfigStore", {})
            .get("Software", {})
            .get("Valve", {})
            .get("Steam", {})
            .get("apps", {})
        )
        entry = apps.get(str(app_id)) or apps.get(app_id)
        if isinstance(entry, dict):
            return (entry.get("LaunchOptions") or "").strip()
    return ""


def set_steam_launch_options(app_id: str, text: str) -> bool:
    if vdf is None:
        return False
    for path in _localconfig_paths():
        try:
            with path.open("r", encoding="utf-8", errors="replace") as f:
                data = vdf.load(f)
        except OSError:
            continue
        root = data.setdefault("UserLocalConfigStore", {})
        sw = root.setdefault("Software", {}).setdefault("Valve", {}).setdefault("Steam", {})
        apps = sw.setdefault("apps", {})
        key = str(app_id)
        if key not in apps or not isinstance(apps[key], dict):
            apps[key] = {}
        apps[key]["LaunchOptions"] = text
        try:
            with path.open("w", encoding="utf-8") as f:
                vdf.dump(data, f, pretty=True)
        except OSError:
            return False
        return True
    return False


def get_launch_options(app_id: str, *, is_shortcut: bool) -> str:
    return get_shortcut_launch_options(app_id) if is_shortcut else get_steam_launch_options(app_id)


def set_launch_options(app_id: str, text: str, *, is_shortcut: bool) -> bool:
    if is_shortcut:
        return set_shortcut_launch_options(app_id, text)
    return set_steam_launch_options(app_id, text)


def enrich_library_launch_options(items: list[dict[str, Any]]) -> None:
    if vdf is None:
        return
    apps: dict[str, Any] = {}
    for path in _localconfig_paths():
        try:
            with path.open("r", encoding="utf-8", errors="replace") as f:
                data = vdf.load(f)
        except OSError:
            continue
        apps = (
            data.get("UserLocalConfigStore", {})
            .get("Software", {})
            .get("Valve", {})
            .get("Steam", {})
            .get("apps", {})
        )
        break
    for g in items:
        if g.get("isShortcut"):
            continue
        entry = apps.get(str(g.get("appId", "")))
        if isinstance(entry, dict):
            g["launchOptions"] = (entry.get("LaunchOptions") or "").strip()
