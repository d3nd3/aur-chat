from __future__ import annotations

import json

from PyQt6.QtCore import QProcess, pyqtSignal

from . import aurelia


class ChatStream(QProcess):
    chatEvent = pyqtSignal(dict)

    def __init__(self, steam_id: str):
        super().__init__()
        self.steam_id = steam_id
        self._buf = ""

    def start_session(self):
        self._buf = ""
        self.setProgram(aurelia.BIN)
        self.setArguments(["chat", "open", self.steam_id, "--json"])
        self.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        self.readyReadStandardOutput.connect(self._on_out)
        self.start()

    def send_line(self, text: str):
        if self.state() == QProcess.ProcessState.Running:
            self.write((text + "\n").encode())

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
