"""Restore env vars Aurelia needs when launched from a .desktop file."""

import os


def ensure_aurelia_session_env() -> None:
    uid = os.getuid()
    if not os.environ.get("XDG_RUNTIME_DIR"):
        cand = f"/run/user/{uid}"
        if os.path.isdir(cand):
            os.environ["XDG_RUNTIME_DIR"] = cand
    if not os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
        rt = os.environ.get("XDG_RUNTIME_DIR")
        if rt:
            bus = f"unix:path={rt}/bus"
            if os.path.exists(f"{rt}/bus"):
                os.environ["DBUS_SESSION_BUS_ADDRESS"] = bus
