from __future__ import annotations

import json

from PyQt6.QtCore import QProcess, pyqtSignal

from . import aurelia


class EventStream(QProcess):
    """One long-lived `aurelia chat events --json` process: every incoming chat
    event on the shared daemon session arrives here, tagged with its source
    SteamID — replaces the old per-friend `chat open` watcher sessions."""

    chatEvent = pyqtSignal(dict)

    def __init__(self):
        super().__init__()
        self._buf = ""

    def start_stream(self):
        self._buf = ""
        self.setProgram(aurelia.BIN)
        self.setArguments(["chat", "events", "--json"])
        self.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        self.readyReadStandardOutput.connect(self._on_out)
        self.start()

    def _on_out(self):
        self._buf += self.readAllStandardOutput().data().decode(errors="replace")
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            line = line.strip()
            if not line:
                continue
            try:
                self.chatEvent.emit(json.loads(line))
            except json.JSONDecodeError:
                pass
