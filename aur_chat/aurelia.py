"""Thin wrapper around the `aurelia` CLI (--json)."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

def _find_binary() -> str:
    if value := os.environ.get("AURELIA_BIN"):
        return value
    explicit_roots = []
    if value := os.environ.get("AURELIA_ROOT"):
        explicit_roots.append(Path(value).expanduser())
    for root in explicit_roots:
        for path in (root / "target/release/aurelia", root / "target/debug/aurelia"):
            if path.is_file() and os.access(path, os.X_OK):
                return str(path)
    if path := shutil.which("aurelia"):
        return path
    root = Path(__file__).resolve().parents[4] / "Aurelia"
    for path in (root / "target/release/aurelia", root / "target/debug/aurelia"):
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    return "aurelia"


BIN = _find_binary()

PERSONA = {
    0: "offline",
    1: "online",
    2: "busy",
    3: "away",
    4: "snooze",
    5: "trade",
    6: "play",
    7: "invisible",
}


def persona_label(state: int) -> str:
    return PERSONA.get(state, "?")


def run_json(argv: list[str], *, timeout: float = 120) -> Any:
    proc = subprocess.run(
        [BIN, *argv, "--json"],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    raw = (proc.stdout or "").strip() or (proc.stderr or "").strip()
    if not raw:
        return {"error": "empty response", "exit_code": proc.returncode}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"error": raw, "exit_code": proc.returncode}


def auth_required(data: Any) -> bool:
    return isinstance(data, dict) and data.get("type") == "auth_required"


def account() -> Any:
    return run_json(["account"])


def friends() -> Any:
    return run_json(["friends", "list"])


def own_profile() -> Any:
    return user_profile("me")


def set_presence(mode: str) -> Any:
    return run_json(["config", "presence", mode])


def user_profile(steam_id: str) -> Any:
    return run_json(["user", str(steam_id)])


def friends_search(query: str) -> Any:
    return run_json(["friends", "search", query])


def friends_add(query: str) -> Any:
    return run_json(["friends", "add", query])


def chat_history(steam_id: str, count: int = 40) -> Any:
    return run_json(["chat", "history", steam_id, "--count", str(count)])


def chat_send_image(steam_id: str, path: str) -> Any:
    return run_json(["chat", "send-image", steam_id, path], timeout=300)


def list_games(*, search: str = "", installed_only: bool = False) -> Any:
    argv = ["list"]
    if installed_only:
        argv.append("--installed")
    if search.strip():
        argv.extend(["--search", search.strip()])
    return run_json(argv, timeout=300)


def play_game(app_id: str) -> subprocess.Popen:
    return subprocess.Popen(
        [BIN, "play", str(app_id), "--steam"],
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def updates() -> Any:
    return run_json(["update"], timeout=300)


def update_game(app_id: str) -> Any:
    return run_json(["update", str(app_id)], timeout=3600)


# Aurelia's "% overall" uses a total that grows as depots are queued, so it jumps backwards.
# The byte counts (done/total) are reliable, so we report those instead.
_PROGRESS = re.compile(r"Downloading:\s+\S+% overall\s+(\d+)/\d+ bytes")


def _update_running(app_id: str) -> bool:
    return subprocess.run(["pgrep", "-f", f"aurelia update {app_id}( |$)"], stdout=subprocess.DEVNULL).returncode == 0


# Runs the update detached and notifies on outcome, so you hear back even if aur-chat is closed.
_UPDATE_SH = (
    '"$1" update "$2" -v >>"$3" 2>&1; rc=$?; '
    'if [ $rc -eq 0 ]; then m=finished; else m=stopped; fi; '
    'notify-send --app-name=aur-chat aur-chat "Download $m: $2" 2>/dev/null; exit $rc'
)


def update_game_progress(app_id: str, on_progress) -> Any:
    # Output goes to a log file, not a pipe, so the update survives aur-chat closing.
    log = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "aur-chat"
    log.mkdir(parents=True, exist_ok=True)
    path = log / f"update-{app_id}.log"
    path.touch(exist_ok=True)
    proc = None
    attached = _update_running(app_id)  # attach to an update started elsewhere (e.g. CLI)
    if not attached:
        proc = subprocess.Popen(
            ["sh", "-c", _UPDATE_SH, "sh", BIN, str(app_id), str(path)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    tail = ""
    with path.open("rb") as r:
        while True:
            text = r.read().decode("utf-8", "replace")
            if text:
                tail = (tail + text)[-400:]
                matches = _PROGRESS.findall(tail)
                if matches:
                    on_progress(float(matches[-1]))
                    tail = tail[-120:]
            elif (proc.poll() is not None) if proc else not _update_running(app_id):
                break
            else:
                time.sleep(0.3)
    lines = [l.strip() for l in path.read_text(errors="replace").splitlines() if l.strip()]
    failed = proc.returncode != 0 if proc else any(l.startswith("Error:") for l in lines[-3:])
    if not failed:
        return {"ok": True, "attached": attached}
    return {"error": (lines[-1] if lines else "update failed")[:160], "attached": attached}


def stop_game(app_id: str, *, force: bool = False) -> Any:
    return run_json(["stop", str(app_id), *(["--force"] if force else [])])
