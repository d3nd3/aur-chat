import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import vdf

from aur_chat import steam_shortcuts


class SteamShortcutTests(unittest.TestCase):
    def test_shortcut_uses_saved_proton_tool_without_steam(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shortcut_path = root / "userdata" / "1" / "config" / "shortcuts.vdf"
            shortcut_path.parent.mkdir(parents=True)
            start = root / "game dir"
            start.mkdir()
            exe = start / "game.exe"
            exe.touch()
            tool = root / "compatibilitytools.d" / "GE-Proton"
            tool.mkdir(parents=True)
            proton = tool / "proton"
            proton.touch()
            proton.chmod(0o755)
            (root / "config").mkdir()
            with (root / "config" / "config.vdf").open("w", encoding="utf-8") as f:
                vdf.dump(
                    {
                        "InstallConfigStore": {
                            "Software": {
                                "Valve": {
                                    "Steam": {
                                        "CompatToolMapping": {
                                            "123": {"name": "GE-Proton"}
                                        }
                                    }
                                }
                            }
                        }
                    },
                    f,
                )
            manifest = tool / "compatibilitytool.vdf"
            with manifest.open("w", encoding="utf-8") as f:
                vdf.dump(
                    {
                        "compatibilitytools": {
                            "compat_tools": {
                                "GE-Proton": {"install_path": "."}
                            }
                        }
                    },
                    f,
                )
            with shortcut_path.open("wb") as f:
                vdf.binary_dump(
                    {
                        "shortcuts": {
                            "0": {
                                "appid": 123,
                                "AppName": "Test game",
                                "Exe": str(exe),
                                "StartDir": str(start),
                                "LaunchOptions": "TEST_FLAG=1 gamemoderun %command% --fullscreen",
                            }
                        }
                    },
                    f,
                )

            with patch.object(steam_shortcuts, "_shortcut_paths", return_value=[shortcut_path]):
                rows = steam_shortcuts.load_shortcuts()
                with patch.object(steam_shortcuts.subprocess, "Popen") as spawn:
                    steam_shortcuts.launch_shortcut("123")

            self.assertEqual(rows[0]["compatTool"], "GE-Proton")
            command = spawn.call_args.args[0]
            self.assertEqual(
                command,
                ["gamemoderun", str(proton), "waitforexitandrun", str(exe), "--fullscreen"],
            )
            self.assertNotIn("-applaunch", command)
            self.assertEqual(spawn.call_args.kwargs["cwd"], str(start))
            env = spawn.call_args.kwargs["env"]
            self.assertEqual(env["TEST_FLAG"], "1")
            self.assertEqual(env["STEAM_COMPAT_DATA_PATH"], str(root / "steamapps" / "compatdata" / "123"))

    def test_native_shortcut_keeps_launch_options(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shortcut_path = root / "userdata" / "1" / "config" / "shortcuts.vdf"
            shortcut_path.parent.mkdir(parents=True)
            exe = root / "game"
            with shortcut_path.open("wb") as f:
                vdf.binary_dump(
                    {
                        "shortcuts": {
                            "0": {
                                "appid": 456,
                                "AppName": "Native game",
                                "Exe": str(exe),
                                "LaunchOptions": "--safe",
                            }
                        }
                    },
                    f,
                )
            with patch.object(steam_shortcuts, "_shortcut_paths", return_value=[shortcut_path]):
                with patch.object(steam_shortcuts.subprocess, "Popen") as spawn:
                    steam_shortcuts.launch_shortcut("456")
            self.assertEqual(spawn.call_args.args[0], [str(exe), "--safe"])


if __name__ == "__main__":
    unittest.main()
