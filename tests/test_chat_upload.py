import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from aur_chat import aurelia
from aur_chat.chat_stream import ChatStream
from aur_chat.backend import (
    _clipboard_image_path,
    _device_info,
    _friend_name,
    _friend_sort_key,
    _format_timestamp,
    _image_url,
    _persona_state,
    _upload_status,
)
from aur_chat.gui import _acquire_instance_lock


class ChatUploadTests(unittest.TestCase):
    def test_binary_override_wins(self):
        with patch.dict(os.environ, {"AURELIA_BIN": "/tmp/custom-aurelia"}):
            self.assertEqual(aurelia._find_binary(), "/tmp/custom-aurelia")

    def test_binary_root_fallback(self):
        with tempfile.TemporaryDirectory() as root:
            binary = Path(root) / "target" / "release" / "aurelia"
            binary.parent.mkdir(parents=True)
            binary.touch()
            binary.chmod(0o755)
            with patch.dict(os.environ, {"AURELIA_ROOT": root}, clear=True):
                with patch("shutil.which", return_value=None):
                    self.assertEqual(aurelia._find_binary(), str(binary))

    def test_image_command_uses_json_and_long_timeout(self):
        with patch.object(aurelia, "run_json", return_value={"url": "https://example"}) as run:
            result = aurelia.chat_send_image("7656119", "/tmp/capture.png")
        self.assertEqual(result["url"], "https://example")
        run.assert_called_once_with(
            ["chat", "send-image", "7656119", "/tmp/capture.png"], timeout=300
        )

    def test_own_profile_command(self):
        with patch.object(aurelia, "run_json", return_value={"persona_state": 6}) as run:
            self.assertEqual(aurelia.own_profile()["persona_state"], 6)
        run.assert_called_once_with(["user", "me"])

    def test_presence_command(self):
        with patch.object(aurelia, "run_json", return_value={"chat_presence": "away"}) as run:
            self.assertEqual(aurelia.set_presence("away")["chat_presence"], "away")
        run.assert_called_once_with(["config", "presence", "away"])

    def test_single_instance_lock(self):
        with tempfile.TemporaryDirectory() as runtime:
            with patch.dict(os.environ, {"XDG_RUNTIME_DIR": runtime}):
                first = _acquire_instance_lock()
                self.assertIsNotNone(first)
                try:
                    self.assertIsNone(_acquire_instance_lock())
                finally:
                    first.unlock()

    def test_chat_stream_keeps_qobject_event_method(self):
        stream = ChatStream("0")
        self.assertTrue(callable(stream.event))
        stream.kill()

    def test_upload_status(self):
        self.assertEqual(_upload_status({"url": "https://example"}), "Image sent")
        self.assertEqual(_upload_status({"error": "failed"}), "failed")
        self.assertEqual(_upload_status(None), "image upload failed")

    def test_friend_state_handles_missing_and_malformed_values(self):
        self.assertEqual(_persona_state({"persona_state": None}), 0)
        self.assertEqual(_persona_state({"persona_state": "2"}), 2)
        self.assertEqual(_persona_state({"persona_state": "unknown"}), 0)
        rows = sorted(
            [
                {"steam_id": "offline", "persona_state": None, "persona_name": "B"},
                {"steam_id": "online", "persona_state": "2", "persona_name": "A"},
            ],
            key=_friend_sort_key,
        )
        self.assertEqual([row["steam_id"] for row in rows], ["online", "offline"])

    def test_friend_name_uses_fallback_for_right_pane(self):
        self.assertEqual(
            _friend_name({"steam_id": "123", "persona_name": None}, {"123": "Alice"}),
            "Alice",
        )

    def test_friend_device_info_uses_steam_persona_flags(self):
        self.assertEqual(
            _device_info({"persona_state": 1, "persona_state_flags": 512}),
            ("Mobile", "📱"),
        )
        self.assertEqual(
            _device_info({"persona_state": 1, "persona_state_flags": 0}),
            ("PC", "🖥"),
        )
        self.assertEqual(_device_info({"persona_state": 0, "persona_state_flags": 512}), ("", ""))

    def test_timestamp_formatting(self):
        self.assertRegex(_format_timestamp(0), r"^\d{2}:\d{2}$")
        self.assertEqual(_format_timestamp(None), "")

    def test_image_url_allows_steam_ugc_links_only(self):
        url = "https://images.steamusercontent.com/ugc/123/456/"
        self.assertEqual(_image_url(url), url)
        self.assertEqual(_image_url(f"[img]{url}[/img]"), url)
        self.assertEqual(_image_url("https://github.com/d3nd3/spotify-pc-audio"), "")
        self.assertEqual(_image_url("https://images.steamusercontent.com/file/123"), "")

    def test_clipboard_image_is_saved_as_png(self):
        image = Mock()

        def save(path, fmt):
            self.assertEqual(fmt, "PNG")
            Path(path).write_bytes(b"png")
            return True

        image.isNull.return_value = False
        image.save.side_effect = save
        clipboard = Mock()
        clipboard.mimeData.return_value.hasImage.return_value = True
        clipboard.image.return_value = image
        with patch("aur_chat.backend.QGuiApplication.clipboard", return_value=clipboard):
            path = _clipboard_image_path()
        try:
            self.assertTrue(path)
            self.assertEqual(Path(path).read_bytes(), b"png")
        finally:
            if path:
                Path(path).unlink(missing_ok=True)

    def test_text_clipboard_is_left_to_qt(self):
        clipboard = Mock()
        clipboard.mimeData.return_value.hasImage.return_value = False
        with patch("aur_chat.backend.QGuiApplication.clipboard", return_value=clipboard):
            self.assertIsNone(_clipboard_image_path())


if __name__ == "__main__":
    unittest.main()
