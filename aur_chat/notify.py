import shutil
import subprocess
from pathlib import Path


def play_notification_sound():
    if shutil.which("canberra-gtk-play"):
        command = ["canberra-gtk-play", "-i", "message-new-instant"]
    else:
        sound = Path("/usr/share/sounds/freedesktop/stereo/message-new-instant.oga")
        player = next((p for p in ("pw-play", "paplay", "aplay") if shutil.which(p)), None)
        if not player or not sound.is_file():
            return
        command = [player, str(sound)]
    try:
        subprocess.Popen(
            command,
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        pass


def desktop_notify(title: str, body: str, *, urgency: str = "normal"):
    if shutil.which("notify-send"):
        subprocess.run(
            ["notify-send", "--app-name=aur-chat", f"--urgency={urgency}", title, body],
            check=False,
        )
    play_notification_sound()
