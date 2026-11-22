import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PyQt6.QtCore import QCoreApplication

from aur_chat import backend as backend_mod
from aur_chat.backend import AureliaBackend
from aur_chat import chat_format
from aur_chat.chat_format import (_CODE_BOX, escape_bbcode, steam_cache_names, to_bbcode,
                                  to_html, sticker_name, sticker_only, uni_emoticon_names, youtube_card)

_APP = QCoreApplication.instance() or QCoreApplication([])  # QTimers in the backend need an event loop owner
_REAL_LOAD_STATE = backend_mod._load_state
_REAL_VALID = backend_mod._valid_steam_id


class ChatFormatTests(unittest.TestCase):
    def test_shortcuts_expand_to_steam_bbcode(self):
        self.assertEqual(to_bbcode("/code x = 1"), "[code]x = 1[/code]")
        self.assertEqual(to_bbcode("/pre a  b"), "[code]a  b[/code]")
        self.assertEqual(to_bbcode("/quote hi"), "[quote]hi[/quote]")
        self.assertEqual(to_bbcode("/spoiler secret"), "[spoiler]secret[/spoiler]")

    def test_other_text_is_untouched(self):
        for text in ("/code", "/flip", "/me waves", "hi /code x", "/codex y"):
            self.assertEqual(to_bbcode(text), text)

    def test_typed_brackets_and_backslashes_are_escaped_like_steam(self):
        self.assertEqual(escape_bbcode("[b]hi\\"), "\\[b]hi\\\\")
        self.assertEqual(to_bbcode("/code a[1]"), "[code]a\\[1][/code]")

    def test_emoticon_shorthand_becomes_wire_tag(self):
        self.assertEqual(to_bbcode("hi :kappa: there"), "hi [emoticon=kappa] there")
        self.assertEqual(to_bbcode("12:34:56"), "12:34:56")  # clock-like colons stay text
        self.assertEqual(to_bbcode(":baphomet: :steamthumbsup:"), "[emoticon=baphomet] [emoticon=steamthumbsup]")
        self.assertIn('<img src="', to_html(to_bbcode("hi :kappa:")))  # renders locally too

    def test_stickers_render(self):
        raw = '[sticker type="Lunar2020PunchingRat" limit="0"][/sticker]'
        self.assertEqual(sticker_name(raw), "Lunar2020PunchingRat")
        self.assertEqual(sticker_name("plain text"), "")
        with tempfile.TemporaryDirectory() as tmp:  # isolate from the user's real icon cache
            with patch("aur_chat.chat_format._ICON_DIR", Path(tmp)):
                h = to_html(raw)
        self.assertIn("economy/sticker/Lunar2020PunchingRat", h)
        self.assertIn('width="96"', h)

    def test_unicode_colon_emoticons_render(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("aur_chat.chat_format._ICON_DIR", Path(tmp)):
                h = to_html("hi ːlunar2019piginablanketː!")
        self.assertIn("economy/emoticon/lunar2019piginablanket", h)
        self.assertNotIn("ː", h)
        self.assertIn("hi", h)

    def test_unicode_emoticon_names_collected(self):
        self.assertEqual(uni_emoticon_names("ːaː mid ːb_cː ːː"), ["a", "b_c"])
        self.assertEqual(uni_emoticon_names("plain 12:34:56"), [])

    def test_sticker_name_tolerates_attr_order_and_spaces(self):
        for raw, want in [('[sticker type="Nippy Walking" limit="0"][/sticker]', "Nippy Walking"),
                          ('[sticker limit="0" type="Nippy Walking"]', "Nippy Walking"),
                          ('[sticker type="mshm2"]', "mshm2"),
                          ("no sticker", "")]:
            self.assertEqual(sticker_name(raw), want)

    def test_sticker_only(self):
        raw = '[sticker type="Nippy Walking" limit="0"][/sticker]'
        self.assertTrue(sticker_only(raw))
        self.assertFalse(sticker_only(f"hi {raw}"))

    def test_steam_cache_mining(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "a_0").write_bytes(b"xx economy/emoticon/steamhappy yy economy/sticker/Nippy%20Walking zz")
            (base / "b_0").write_bytes(b"no urls here")
            names = steam_cache_names(base)
        self.assertEqual(names["emoticons"], ["steamhappy"])
        self.assertEqual(names["stickers"], ["Nippy Walking"])

    def test_apng_becomes_animated_gif(self):
        import importlib.util
        if not importlib.util.find_spec("PIL"):  # the runtime python has Pillow; the venv may not
            self.skipTest("Pillow unavailable")
        import io
        from PIL import Image
        seq = [Image.new("RGBA", (8, 8), c) for c in ((255, 0, 0, 255), (0, 255, 0, 255))]
        buf = io.BytesIO()
        seq[0].save(buf, "PNG", save_all=True, append_images=seq[1:], duration=[100, 100])
        gif = chat_format._as_animated_gif(buf.getvalue())
        self.assertIsNotNone(gif)
        out = Image.open(io.BytesIO(gif))
        self.assertEqual(out.n_frames, 2)
        self.assertTrue(out.is_animated)
        self.assertEqual(out.info.get("transparency"), 255)  # background stays transparent
        out.seek(1)
        self.assertEqual(out.disposal_method, 2)  # restore-to-bg: frames clear cleanly

    def test_gif_frames_clear_previous(self):
        import importlib.util
        if not importlib.util.find_spec("PIL"):
            self.skipTest("Pillow unavailable")
        import io
        from PIL import Image
        gone = Image.new("RGBA", (8, 8), (0, 0, 0, 0))  # frame 1: the art vanished
        seq = [Image.new("RGBA", (8, 8), (255, 0, 0, 255)), gone]
        buf = io.BytesIO()
        seq[0].save(buf, "PNG", save_all=True, append_images=seq[1:], duration=[100, 100])
        out = Image.open(io.BytesIO(chat_format._as_animated_gif(buf.getvalue())))
        out.seek(1)
        self.assertLess(out.convert("RGBA").getpixel((0, 0))[3], 128)  # no trail of frame 0

    def test_static_png_passthrough(self):
        import importlib.util
        if not importlib.util.find_spec("PIL"):
            self.skipTest("Pillow unavailable")
        import io
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGBA", (4, 4), (1, 2, 3, 255)).save(buf, "PNG")
        gif = chat_format._as_animated_gif(buf.getvalue())
        self.assertTrue(gif.startswith(b"GIF8"))  # single frames become static GIFs (idempotent cache)
        self.assertEqual(Image.open(io.BytesIO(gif)).n_frames, 1)

    def test_stale_apng_cache_migrates(self):
        import importlib.util
        if not importlib.util.find_spec("PIL"):
            self.skipTest("Pillow unavailable")
        import io
        from PIL import Image
        seq = [Image.new("RGBA", (8, 8), c) for c in ((255, 0, 0, 255), (0, 255, 0, 255))]
        buf = io.BytesIO()
        seq[0].save(buf, "PNG", save_all=True, append_images=seq[1:], duration=[100, 100])
        with tempfile.TemporaryDirectory() as tmp:
            with patch("aur_chat.chat_format._ICON_DIR", Path(tmp)):
                stale = Path(tmp) / "stickers" / "x"
                stale.parent.mkdir(parents=True)
                stale.write_bytes(buf.getvalue())  # pre-conversion era: raw APNG on disk
                chat_format.icon_src("stickers", "x")
                self.assertTrue(stale.read_bytes().startswith(b"GIF8"))  # converted in place

    def test_youtube_card_detection(self):
        self.assertEqual(youtube_card("https://youtu.be/dQw4w9WgXcQ"), "dQw4w9WgXcQ")
        self.assertEqual(youtube_card("https://www.youtube.com/watch?v=dQw4w9WgXcQ"), "dQw4w9WgXcQ")
        self.assertEqual(youtube_card("https://www.youtube.com/watch?t=42&v=dQw4w9WgXcQ"), "dQw4w9WgXcQ")
        self.assertEqual(youtube_card("https://www.youtube.com/shorts/dQw4w9WgXcQ"), "dQw4w9WgXcQ")
        self.assertEqual(youtube_card('[url="https://youtu.be/dQw4w9WgXcQ"]https://youtu.be/dQw4w9WgXcQ[/url]'), "dQw4w9WgXcQ")
        self.assertEqual(youtube_card("see https://youtu.be/dQw4w9WgXcQ now"), "")
        self.assertEqual(youtube_card("https://example.com/watch?v=dQw4w9WgXcQ"), "")

    def test_rich_text_for_formatting_tags(self):
        self.assertEqual(to_html("[code]x<y[/code]"),
                         '<table width="100%" cellspacing="0" cellpadding="4"><tr><td bgcolor="#2b2b33">'
                         '<pre style="margin-top:0;margin-bottom:0;color:#d8dee9">x&lt;y</pre></td></tr></table>')
        self.assertEqual(to_html("[quote=bob]q[/quote]"),
                         '<table width="100%" cellspacing="0" cellpadding="4"><tr><td bgcolor="#37474f">'
                         '<span style="color:#d8dee9"><b style="color:#82b1ff">bob</b><br>q</span></td></tr></table>')
        self.assertEqual(to_html("[quote]q[/quote]").count("<b style="), 0)  # no name, no heading
        self.assertEqual(to_html("[b]a[/b] [i]b[/i] [spoiler]s[/spoiler]"),
                         '<b>a</b> <i>b</i> <span style="background-color:#8b8b8b; color:#8b8b8b">s</span>')
        self.assertEqual(to_html("line1\nline2"), "line1<br>line2")
        self.assertEqual(to_html("[img]https://images.steamusercontent.com/ugc/1/2/[/img]"),
                         "https://images.steamusercontent.com/ugc/1/2/")

    def test_escaped_brackets_show_literally(self):  # escapes become entities, so no tag matches them
        self.assertEqual(to_html("\\[b]x\\[/b] a\\\\b"), "&#91;b]x&#91;/b] a&#92;b")

    def test_unknown_tags_and_html_are_shown_as_text(self):
        self.assertEqual(to_html("see [1] and [x]"), "see [1] and [x]")
        self.assertEqual(to_html("<script>&"), "&lt;script&gt;&amp;")

    def test_slash_command_results_from_steam_tags(self):
        self.assertEqual(to_html("[random min=1 max=100 result=42]/random 100[/random]"), "/random 1-100 : 42")
        self.assertEqual(to_html("[flip result=tails]/flip[/flip]"), "/flip: Tails")
        self.assertEqual(to_html("[img giphy_search=cats src=x]/giphy cats[/img]"), "/giphy cats")
        for raw in ("[emoticon=steamhappy]", "[emoticon]steamhappy[/emoticon]"):
            h = to_html(raw)
            self.assertIn('<img src="', h)
            self.assertIn('alt=":steamhappy:"', h)  # shown until the image lands
            self.assertTrue("emoticon/steamhappy" in h or h.startswith('<img src="file://'))
        self.assertEqual(to_html("[emoticon][/emoticon]"), "")
        self.assertEqual(to_html("/me waves"), "<i>* waves</i>")


class _BackendCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.state_file = Path(tmp.name) / "state.json"
        for target, name, value in [
            (backend_mod, "_STATE_FILE", self.state_file),
            (backend_mod, "_FRIENDS_LOG", Path(tmp.name) / "friends-debug.log"),
            (backend_mod, "_load_state", Mock(return_value={})),
            (backend_mod, "_valid_steam_id", lambda s: bool(s)),  # tests use short stand-in ids
            (backend_mod, "_Fetch", Mock()),
            (backend_mod, "EventStream", Mock()),
            (backend_mod, "desktop_notify", Mock()),
        ]:
            p = patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.backend = AureliaBackend()
        self.addCleanup(self.backend.shutdown)
        self.backend.pickFriend("1")  # "1" is the open chat, the window is on screen

    def receive(self, steam_id, at, active=False, **event):
        with patch.object(backend_mod.time, "time", return_value=at):
            self.backend._on_chat_event(steam_id, {"event": "message", "text": "hi", **event}, active=active)

    def show(self, rows):  # push a roster through the standard sync path
        self.backend.friends.set_rows(rows)
        self.backend._sync_friend_items(rows)


class UnreadRestoreTests(_BackendCase):
    def test_restore_opens_chat_with_most_recent_unread(self):
        b = self.backend
        b.setWindowVisible(False)
        self.receive("2", 100.0)
        self.receive("3", 200.0)
        self.receive("1", 150.0, active=True)  # the open chat also counts while minimized
        b.setWindowVisible(True)
        self.assertEqual(b.activeFriendId, "3")
        self.assertIn("3", b._tabs)
        self.assertEqual(b._unread, {"1": 1, "2": 1})  # the others stay unread

    def test_restore_keeps_open_chat_when_it_is_newest(self):
        b = self.backend
        b.setWindowVisible(False)
        self.receive("2", 100.0)
        self.receive("1", 300.0, active=True)
        b.setWindowVisible(True)
        self.assertEqual(b.activeFriendId, "1")
        self.assertEqual(b._unread, {"2": 1})

    def test_plain_show_without_minimize_does_not_switch(self):
        b = self.backend
        self.receive("2", 100.0)
        b.setWindowVisible(True)
        self.assertEqual(b.activeFriendId, "1")
        self.assertEqual(b._unread, {"2": 1})

    def test_alert_signal_only_for_messages_not_on_screen(self):
        b = self.backend
        arrived = Mock()
        b.messageArrived.connect(arrived)
        self.receive("1", 100.0, active=True)  # visible and open: nothing to flag
        arrived.assert_not_called()
        b.setWindowVisible(False)
        self.receive("1", 110.0, active=True)
        arrived.assert_called_once()


class ChatEventTests(_BackendCase):
    def test_send_expands_shortcut_and_escapes(self):
        b = self.backend
        b.sendMessage("/code x = [1]")
        self.assertEqual(backend_mod._Fetch.call_args.args[1:],
                         ("1", "[code]x = \\[1][/code]"))
        self.assertEqual(b.messages._rows[-1]["html"], _CODE_BOX.format("x = &#91;1]"))

    def test_dialog_groups_consecutive_messages_under_one_name(self):
        b = self.backend
        b._active_name = "BRABUS"
        self.receive("1", 100.0, active=True, text="one", timestamp=1000)
        self.receive("1", 100.0, active=True, text="two", timestamp=1030)
        self.receive("1", 100.0, active=True, text="later", timestamp=5000)
        b.sendMessage("my reply")
        self.receive("1", 100.0, active=True, text="after", timestamp=5100)
        self.assertEqual([(i["sender"], i["showName"]) for i in b.messages._rows], [
            ("BRABUS", True), ("BRABUS", False), ("BRABUS", True),
            ("You", True), ("BRABUS", True)])

    def test_incoming_bbcode_keeps_formatting(self):
        self.receive("1", 100.0, active=True, text="x = 1", bbcode="[code]x = 1[/code]")
        self.assertEqual(self.backend.messages._rows[-1]["html"], _CODE_BOX.format("x = 1"))

    def test_youtube_link_becomes_card_row(self):
        b = self.backend
        self.receive("1", 100.0, active=True, text="https://youtu.be/dQw4w9WgXcQ", timestamp=1000)
        row = b.messages._rows[-1]
        self.assertEqual(row["videoId"], "dQw4w9WgXcQ")
        self.assertEqual(row["html"], "")
        self.receive("1", 100.0, active=True,
                     text="see https://www.youtube.com/watch?v=dQw4w9WgXcQ now", timestamp=1100)
        row = b.messages._rows[-1]
        self.assertEqual(row["videoId"], "")  # mixed text: stays plain text
        self.assertNotEqual(row["html"], "")

    def test_sent_ack_swaps_in_the_text_steam_stored(self):
        b = self.backend
        b.sendMessage("/flip")
        self.assertEqual(b.messages._rows[-1]["html"], "/flip")
        b._on_sent("1", "/flip", {"modified_message": "[flip result=heads]/flip[/flip]"})
        self.assertEqual(b.messages._rows[-1]["html"], "/flip: Heads")

    def test_sent_ack_never_clobbers_our_bbcode_styling(self):
        b = self.backend
        b.sendMessage("/code x = 1")
        b._on_sent("1", "[code]x = 1[/code]", {"modified_message": "x = 1"})
        self.assertEqual(b.messages._rows[-1]["html"], _CODE_BOX.format("x = 1"))


class SmoothRefreshTests(_BackendCase):
    def test_friend_poll_never_toggles_busy(self):
        b = self.backend
        b.refreshFriends()
        self.assertFalse(b.busy)
        b._on_friends([{"steam_id": "2", "persona_name": "On", "persona_state": 1}])
        self.assertFalse(b.busy)  # no greyed-out controls during background polls

    def test_unchanged_poll_does_not_reemit_friend_list(self):
        b = self.backend
        rows = [{"steam_id": "2", "persona_name": "On", "persona_state": 1}]
        b._on_friends(rows)
        hits = []
        b.friendsListChanged.connect(lambda: hits.append(1))
        b._on_friends([dict(r) for r in rows])  # identical content: silence
        self.assertEqual(hits, [])
        b._on_friends([dict(r, persona_state=0) for r in rows])  # real change: one emit
        self.assertEqual(hits, [1])

    def test_intervals_settable_and_persisted(self):
        b = self.backend
        b.setFriendsInterval(30)
        self.assertEqual((b.friendsInterval, b._poll.interval()), (30, 30000))
        b.setFriendsInterval(999)  # clamped to the max
        self.assertEqual(b.friendsInterval, 120)
        b.setGamesInterval(5)
        self.assertEqual(b._game_poll.interval(), 5000)
        state = json.loads(self.state_file.read_text())
        self.assertEqual(state["friends_interval"], 120)
        self.assertEqual(state["games_interval"], 5)


class LastSeenTests(_BackendCase):
    NOW = 1_800_000_000

    def text(self, row):
        return backend_mod._last_seen_text(row, now=self.NOW)

    def test_format_buckets(self):
        f = lambda **kw: {"persona_state": 0, **kw}
        self.assertEqual(self.text(f(last_seen_online=self.NOW - 30)), "last seen just now")
        self.assertEqual(self.text(f(last_seen_online=self.NOW - 300)), "last seen 5 mins ago")
        self.assertEqual(self.text(f(last_seen_online=self.NOW - 7 * 3600)), "last seen 7 hours ago")
        self.assertEqual(self.text(f(last_seen_online=self.NOW - 86400 - 3600)), "last seen 1 day ago")
        self.assertEqual(self.text(f(last_seen_online=self.NOW - 3 * 86400)), "last seen 3 days ago")
        self.assertEqual(self.text(f(last_seen_online=self.NOW - 16 * 86400)), "last seen 2 weeks ago")

    def test_hidden_while_online_or_unknown(self):
        self.assertEqual(self.text({"persona_state": 1, "last_seen_online": 5}), "")
        self.assertEqual(self.text({"persona_state": 0, "last_seen_online": None}), "")

    def test_items_carry_last_seen(self):
        b = self.backend
        now = backend_mod.time.time()
        rows = [{"steam_id": "1", "persona_name": "Off", "persona_state": 0,
                 "last_seen_online": int(now) - 5 * 3600},
                {"steam_id": "2", "persona_name": "On", "persona_state": 1,
                 "last_seen_online": int(now) - 60}]
        b.friends.set_rows(rows)
        b._sync_friend_items(rows)
        items = {i["steamId"]: i["lastSeen"] for i in b.friendItems}
        self.assertEqual(items["1"], "last seen 5 hours ago")
        self.assertEqual(items["2"], "")
        b._tabs = ["1"]
        b._sync_tab_items()
        self.assertEqual(b.chatTabs[0]["lastSeen"], "last seen 5 hours ago")


class FriendFilterTests(_BackendCase):
    def show(self, rows):
        self.backend.friends.set_rows(rows)
        self.backend._sync_friend_items(rows)

    def ids(self):
        return {item["steamId"] for item in self.backend.friendItems}

    def test_hide_offline_keeps_online_unread_and_open_chat(self):
        b = self.backend
        self.show([
            {"steam_id": "1", "persona_name": "Open", "persona_state": 0},
            {"steam_id": "2", "persona_name": "On", "persona_state": 1},
            {"steam_id": "3", "persona_name": "Off", "persona_state": 0},
            {"steam_id": "4", "persona_name": "Unread", "persona_state": 0},
            {"steam_id": "5", "persona_name": "Invisible", "persona_state": 7},
        ])
        b._unread["4"] = 1
        self.assertEqual(self.ids(), {"1", "2", "3", "4", "5"})
        b.setHideOffline(True)
        self.assertTrue(b.hideOffline)
        self.assertEqual(self.ids(), {"1", "2", "4"})
        b.setHideOffline(False)
        self.assertEqual(self.ids(), {"1", "2", "3", "4", "5"})

    def test_game_falls_back_to_app_id_without_name(self):
        self.show([
            {"steam_id": "1", "persona_name": "Named", "persona_state": 1, "game_app_id": 440, "game_name": "TF2"},
            {"steam_id": "2", "persona_name": "Unnamed", "persona_state": 1, "game_app_id": 570, "game_name": None},
            {"steam_id": "3", "persona_name": "Idle", "persona_state": 1, "game_app_id": None},
        ])
        self.assertEqual([i["game"] for i in self.backend.friendItems], ["TF2", "app 570", ""])

    def test_presence_tiers_sort_online_then_away_then_snooze(self):
        # Online first, then busy/away/snooze; offline/unknown/invisible last.
        # Goes through _on_friends — the only path that sorts a real poll.
        b = self.backend
        b._on_friends([
            {"steam_id": "1", "persona_name": "Gone", "persona_state": 0},
            {"steam_id": "2", "persona_name": "Snoozing", "persona_state": 4},
            {"steam_id": "3", "persona_name": "Away", "persona_state": 3},
            {"steam_id": "4", "persona_name": "Online", "persona_state": 1},
            {"steam_id": "5", "persona_name": "Unknown", "persona_state": None},
            {"steam_id": "6", "persona_name": "Invisible", "persona_state": 7},
            {"steam_id": "7", "persona_name": "Busy", "persona_state": 2},
            {"steam_id": "8", "persona_name": "Trading", "persona_state": 5},
        ])
        tiers = [i["name"] for i in b.friendItems]
        self.assertEqual(
            tiers,
            ["Online", "Busy", "Away", "Snoozing", "Trading",
             "Gone", "Invisible", "Unknown"],
        )

    def test_hide_offline_survives_restart(self):
        self.backend.setHideOffline(True)
        with patch.object(backend_mod, "_load_state", _REAL_LOAD_STATE):
            again = AureliaBackend()
        self.addCleanup(again.shutdown)
        self.assertTrue(again.hideOffline)

    def test_friend_font_size_persists_and_clamps(self):
        self.backend.setFriendFontSize(14)
        self.assertEqual(self.backend.friendFontSize, 14)
        self.backend.setFriendFontSize(99)
        self.assertEqual(self.backend.friendFontSize, 24)  # clamped to the UI range
        self.backend.setFriendFontSize(0)
        self.assertEqual(self.backend.friendFontSize, 0)  # 0 = theme default
        with patch.object(backend_mod, "_load_state", _REAL_LOAD_STATE):
            again = AureliaBackend()
        self.addCleanup(again.shutdown)
        self.assertEqual(again.friendFontSize, 0)

    def test_friends_width_persists_and_clamps(self):
        self.backend.setFriendsWidth(400)
        self.assertEqual(self.backend.friendsWidth, 400)
        self.backend.setFriendsWidth(100)  # below minimum: keep the last valid value
        self.assertEqual(self.backend.friendsWidth, 400)
        with patch.object(backend_mod, "_load_state", _REAL_LOAD_STATE):
            again = AureliaBackend()
        self.addCleanup(again.shutdown)
        self.assertEqual(again.friendsWidth, 400)

    def test_chat_font_size_persists_and_clamps(self):
        self.backend.setChatFontSize(16)
        self.assertEqual(self.backend.chatFontSize, 16)
        self.backend.setChatFontSize(99)
        self.assertEqual(self.backend.chatFontSize, 24)  # clamped to the UI range
        self.backend.setChatFontSize(16)  # leave 16 behind so the reload checks it
        with patch.object(backend_mod, "_load_state", _REAL_LOAD_STATE):
            again = AureliaBackend()
        self.addCleanup(again.shutdown)
        self.assertEqual(again.chatFontSize, 16)


class ProfileNameTests(_BackendCase):
    def test_restored_tab_name_updates_when_roster_arrives(self):
        b = self.backend
        b.friends.set_rows([])  # roster not loaded yet, like right after startup
        b._show_chat("7")
        self.assertEqual(b.activeFriendName, "7")  # the SteamID shows until the name lands
        b._on_friends([{"steam_id": "7", "persona_name": "BRABUS", "persona_state": 1}])
        self.assertEqual(b.activeFriendName, "BRABUS")

    def test_active_subtitle_shows_status_and_game(self):
        b = self.backend
        b.friends.set_rows([])
        b._show_chat("7")
        self.assertEqual(b.activeFriendSubtitle, "")  # status unknown until the roster lands
        b._on_friends([{"steam_id": "7", "persona_name": "BRABUS", "persona_state": 1,
                        "game_app_id": 570, "game_name": "Dota 2"}])
        self.assertEqual(b.activeFriendSubtitle, "online · Dota 2")

    def test_chat_tab_carries_status_subtitle(self):
        b = self.backend
        b.friends.set_rows([{"steam_id": "7", "persona_name": "BRABUS", "persona_state": 1,
                             "game_app_id": 570, "game_name": "Dota 2",
                             "persona_state_flags": 512}])
        b.pickFriend("7")
        tabs = {t["steamId"]: t for t in b.chatTabs}
        self.assertEqual(tabs["7"]["subtitle"], "online · Dota 2")
        self.assertEqual(tabs["7"]["statusColor"], backend_mod._STATUS_COLORS[1])
        self.assertEqual(tabs["7"]["device"], "Mobile")
        self.assertEqual(tabs["7"]["deviceIcon"], "smartphone")

    def test_tab_subtitle_stays_quiet_before_the_roster_lands(self):
        b = self.backend
        b._tabs.append("9")  # restored tab, no roster row yet
        b._sync_tab_items()
        tabs = {t["steamId"]: t for t in b.chatTabs}
        self.assertEqual(tabs["9"]["subtitle"], "")

    def test_device_badges_use_theme_icons(self):
        online = {"steam_id": "7", "persona_state": 1}
        self.assertEqual(backend_mod._device_info({**online, "persona_state_flags": 512})[1], "smartphone")
        self.assertEqual(backend_mod._device_info({**online, "persona_state_flags": 2048})[1], "audio-headset")
        self.assertEqual(backend_mod._device_info({**online, "persona_state_flags": 0})[1], "computer")
        self.assertEqual(backend_mod._device_info({**online, "persona_state": 0}), ("", ""))

    def test_opening_a_tab_resolves_its_name_at_once(self):
        b = self.backend
        b.friends.set_rows([])
        b._name_worker = None
        backend_mod._Fetch.reset_mock()
        b._show_chat("7")
        calls = [c.args for c in backend_mod._Fetch.call_args_list]
        self.assertIn((backend_mod._lookup_friend_names, ["7"]), calls)

    def test_hide_offline_keeps_friends_with_an_open_tab(self):
        b = self.backend
        b._tabs = ["3"]
        rows = [
            {"steam_id": "3", "persona_name": "Tabbed", "persona_state": 0},
            {"steam_id": "5", "persona_name": "Gone", "persona_state": 0},
        ]
        b.friends.set_rows(rows)
        b._sync_friend_items(rows)
        b.setHideOffline(True)
        items = {i["steamId"] for i in b.friendItems}
        self.assertEqual(items, {"3"})  # an open chat is not a typical offline friend

    def test_own_name_comes_from_the_profile_snapshot(self):
        b = self.backend
        b._on_friends({"profile": {"persona_name": "Me", "persona_state": 1}, "friends": []})
        self.assertEqual(b.ownName, "Me")

    def test_own_name_change_calls_cli_and_applies_result(self):
        b = self.backend
        with patch.object(backend_mod.aurelia, "set_persona_name", Mock()) as send:
            backend_mod._Fetch.reset_mock()  # setUp already fetched chat history
            b.setOwnName("New")
            backend_mod._Fetch.assert_called_once_with(send, "New")
            b._on_own_name({"persona_name": "New"})  # _Fetch is mocked, deliver the result by hand
        self.assertEqual(b.ownName, "New")

    def test_own_name_change_failure_keeps_name(self):
        b = self.backend
        b._on_friends({"profile": {"persona_name": "Me"}, "friends": []})
        b.setOwnName("Bad")
        b._on_own_name({"error": "EResult 16"})
        self.assertEqual(b.ownName, "Me")
        self.assertIn("EResult 16", b.statusText)


class FriendRichInfoTests(_BackendCase):
    def test_dota_hero_names_translate_from_internal_slugs(self):
        self.assertEqual(backend_mod._dota_hero_name("npc_dota_hero_antimage"), "Anti-Mage")
        self.assertEqual(backend_mod._dota_hero_name("npc_dota_hero_obsidian_destroyer"), "Outworld Destroyer")
        self.assertEqual(backend_mod._dota_hero_name("npc_dota_hero_furion"), "Nature's Prophet")
        self.assertEqual(backend_mod._dota_hero_name("npc_dota_hero_axe"), "Axe")  # generic: plain titlecase

    def test_game_detail_shows_hero_and_level_for_dota(self):
        playing = {
            "steam_id": "7", "persona_name": "BRABUS", "persona_state": 1,
            "game_app_id": 570, "game_name": "Dota 2",
            "rich_presence": {"CurrentHero": "npc_dota_hero_axe", "CurrentHeroLevel": "25",
                              "CurrentPhaseTimeNonce": "123456"},  # noise keys don't surface
        }
        self.assertEqual(backend_mod._game_detail(playing), "Axe Lv 25")
        self.assertEqual(backend_mod._friend_subtitle(playing), "online · Dota 2 · Axe Lv 25")

    def test_subtitle_stays_quiet_without_rich_presence(self):  # regression: plain rows render as before
        self.assertEqual(
            backend_mod._friend_subtitle({"persona_state": 1, "game_app_id": 570, "game_name": "Dota 2"}),
            "online · Dota 2",
        )
        self.assertEqual(
            backend_mod._friend_subtitle({"persona_state": 1, "game_app_id": 440, "game_name": None}),
            "online · app 440",
        )

    def test_joinable_flag_digests_from_persona_flags(self):
        self.assertTrue(backend_mod._friend_joinable({"persona_state": 1, "persona_state_flags": 2}))
        self.assertFalse(backend_mod._friend_joinable({"persona_state": 1, "persona_state_flags": 512}))
        self.assertFalse(backend_mod._friend_joinable({"persona_state": 1}))
        non_dota = {"persona_state": 1, "game_app_id": 739890, "persona_state_flags": 2}
        self.assertEqual(backend_mod._game_detail(non_dota), "joinable")
        self.assertIn("in a joinable game", backend_mod._rich_text(non_dota))

    def test_rich_text_carries_lobby_and_custom_games(self):
        playing = {
            "steam_id": "7", "persona_state": 1, "game_app_id": 570, "game_name": "Dota 2",
            "rich_presence": {"CurrentHero": "npc_dota_hero_windrunner", "CurrentHeroLevel": "8",
                              "CustomGameName": "overwrite my sunset", "state": "In progress",
                              "CurrentGameMinutesDelta": "35"},
        }
        text = backend_mod._rich_text(playing)
        self.assertEqual(text, "Windranger Lv 8 · overwrite my sunset")

    def test_friend_items_expose_game_detail_joinable_and_tooltip(self):
        self.show([{
            "steam_id": "7", "persona_name": "BRABUS", "persona_state": 1,
            "game_app_id": 570, "game_name": "Dota 2", "persona_state_flags": 3,
            "rich_presence": {"CurrentHero": "npc_dota_hero_rattletrap", "CurrentHeroLevel": "12"},
        }])
        item = self.backend.friendItems[0]
        self.assertEqual(item["gameDetail"], "Clockwerk Lv 12")
        self.assertEqual(item["richText"], "Clockwerk Lv 12")
        self.assertTrue(item["joinable"])
        row = next(r for r in self.backend.friends.rows() if r["steam_id"] == "7")
        self.assertEqual(backend_mod._friend_subtitle(row), "online · Dota 2 · Clockwerk Lv 12")

    def test_friend_search_matches_hero_names(self):
        self.show([{
            "steam_id": "7", "persona_name": "BRABUS", "persona_state": 1,
            "game_app_id": 570, "game_name": "Dota 2",
            "rich_presence": {"CurrentHero": "npc_dota_hero_windrunner"},
        }])
        self.backend.setFriendFilter("windranger")
        self.assertEqual([i["name"] for i in self.backend.friendItems], ["BRABUS"])
        self.backend.setFriendFilter("dota")  # game name still matches too
        self.assertEqual(len(self.backend.friendItems), 1)


class FriendSyncReliabilityTests(_BackendCase):
    def test_carry_forward_splices_missing_persona_data_from_previous_poll(self):
        b = self.backend
        full = {"steam_id": "7", "persona_name": "BRABUS", "persona_state": 1,
                "game_app_id": 570, "game_name": "Dota 2", "last_seen_online": 42,
                "rich_presence": {"CurrentHero": "npc_dota_hero_axe"},
                "persona_state_flags": 3, "game_lobby_id": 99}
        partial = {"steam_id": "7", "persona_name": "BRABUS",
                   "persona_state": None, "game_app_id": None, "game_name": None,
                   "last_seen_online": None, "rich_presence": None,
                   "persona_state_flags": None, "game_lobby_id": None}
        b._on_friends([dict(full)])
        b._on_friends([dict(partial)])
        kept = next(r for r in b.friends.rows() if r["steam_id"] == "7")
        for key, value in full.items():
            self.assertEqual(kept[key], value, f"partial sync dropped {key}")

    def test_carry_forward_never_uses_stale_value_over_fresh_data(self):
        b = self.backend
        b._on_friends([{"steam_id": "7", "persona_name": "BRABUS", "persona_state": 1,
                        "game_app_id": 570, "game_name": "Dota 2"}])
        b._on_friends([{"steam_id": "7", "persona_name": "BRABUS", "persona_state": 0,
                        "game_app_id": None, "game_name": None}])  # real offline beats carried online
        row = next(r for r in b.friends.rows() if r["steam_id"] == "7")
        self.assertEqual(row["persona_state"], 0)
        self.assertEqual(row["game_name"], None)  # cleared by the daemon, not carried back

    def test_absent_friends_without_a_previous_row_stay_unknown(self):
        b = self.backend
        b._on_friends([{"steam_id": "8", "persona_name": "Newcomer", "persona_state": None}])
        row = next(r for r in b.friends.rows() if r["steam_id"] == "8")
        self.assertIsNone(row["persona_state"])  # nothing to carry: unknown, not offline
        self.assertEqual(backend_mod._friend_subtitle(row), "")

    def test_mass_offline_becomes_one_summary_notification(self):
        b = self.backend
        online = [{"steam_id": str(n), "persona_name": f"F{n}", "persona_state": 1} for n in range(1, 11)]
        b._on_friends(online)
        backend_mod.desktop_notify.reset_mock()
        hit = [{"steam_id": str(n), "persona_name": f"F{n}",
                "persona_state": 1 if n > 6 else 0} for n in range(1, 11)]
        b._on_friends(hit)  # 6 of 10 reported offline at once: a landslide, not log-offs
        messages = [c.args[1] for c in backend_mod.desktop_notify.call_args_list]
        self.assertIn("6 friends went offline", messages)
        self.assertEqual(len([m for m in messages if "went offline" in m]), 1)

    def test_small_offline_batch_keeps_individual_notifications(self):
        b = self.backend
        online = [{"steam_id": str(n), "persona_name": f"F{n}", "persona_state": 1} for n in range(1, 11)]
        b._on_friends(online)
        backend_mod.desktop_notify.reset_mock()
        going = [{"steam_id": str(n), "persona_name": f"F{n}",
                  "persona_state": 1 if n > 2 else 0} for n in range(1, 11)]
        b._on_friends(going)  # 2 of 10 = trickle
        messages = [c.args[1] for c in backend_mod.desktop_notify.call_args_list]
        self.assertIn("F1 went offline", messages)
        self.assertIn("F2 went offline", messages)
        self.assertNotIn("2 friends went offline", messages)

    def test_mass_offline_threshold_tracks_fraction_of_online_friends(self):
        b = self.backend
        online = [{"steam_id": str(n), "persona_name": f"F{n}", "persona_state": 1} for n in range(1, 21)]
        b._on_friends(online)
        backend_mod.desktop_notify.reset_mock()
        mixed = [{"steam_id": str(n), "persona_name": f"F{n}",
                  "persona_state": 1 if n > 10 else 0} for n in range(1, 21)]
        b._on_friends(mixed)  # 10 of 20: exactly the 50% boundary
        messages = [c.args[1] for c in backend_mod.desktop_notify.call_args_list]
        self.assertIn("10 friends went offline", messages)

    def test_empty_roster_polls_reclaimed_only_after_a_full_cycle(self):
        b = self.backend
        online = [{"steam_id": str(n), "persona_name": f"F{n}", "persona_state": 1} for n in range(1, 11)]
        b._on_friends(online)
        for _ in range(backend_mod._FRIENDS_EMPTY_RETRIES):
            b._on_friends([])
            self.assertTrue(b.friends.rows())  # empty retries keep the last roster
        b._on_friends([])  # budget exhausted: the emptiness becomes authoritative
        self.assertEqual(b.friends.rows(), [])
        backend_mod.desktop_notify.reset_mock()
        b._on_friends(online)  # roster comes back right away, like before a hiccup heals
        self.assertEqual(len(b.friends.rows()), 10)
        flips = [c.args[1] for c in backend_mod.desktop_notify.call_args_list
                 if "came online" in c.args[1] or "went offline" in c.args[1]]
        self.assertEqual(flips, [])  # presence memory spans the wipe: no tray storm


class FriendsDebugLogTests(_BackendCase):
    def test_sync_writes_a_timeline_of_partials_and_carries(self):
        b = self.backend
        full = [{"steam_id": "7", "persona_name": "BRABUS", "persona_state": 1}]
        b._on_friends([dict(r) for r in full])
        b._on_friends([{"steam_id": "7", "persona_name": "BRABUS", "persona_state": None}])
        log = backend_mod._FRIENDS_LOG.read_text()
        self.assertIn("] data rows=1 unnamed=0", log)
        self.assertIn("] carry rows=1 missing=1 spliced=1 fields=1", log)

    def test_log_can_be_disabled_by_environment(self):
        b = self.backend
        backend_mod._FRIENDS_LOG.unlink(missing_ok=True)
        with patch.dict("os.environ", {"AUR_CHAT_FRIENDS_DEBUG": "0"}):
            b._on_friends([{"steam_id": "7", "persona_name": "BRABUS", "persona_state": 1}])
        self.assertFalse(backend_mod._FRIENDS_LOG.exists())


class PlayingTitleTests(_BackendCase):
    def test_title_shows_local_game_with_launch_options(self):
        b = self.backend
        self.assertEqual(b.playingTitle, "No Game")
        b._track_local_game("Dota 2", Mock(), "570", "gamemoderun %command%")
        self.assertEqual(b.playingTitle, "Dota 2 · gamemoderun %command%")
        b._poll_local_game()  # the mocked process reports itself finished
        self.assertEqual(b.playingTitle, "No Game")

    def test_title_follows_the_steam_reported_game(self):
        b = self.backend
        b._sync_presence({"persona_state": 1, "game_name": "Dota 2", "game_app_id": 570})
        self.assertEqual(b.playingTitle, "Dota 2")
        b._sync_presence({"persona_state": 1})  # game closed
        self.assertEqual(b.playingTitle, "No Game")


class ImageTests(_BackendCase):
    def test_upload_appends_own_image(self):
        b = self.backend
        b._on_image_uploaded("1", "/tmp/gone.png", Mock(), {"url": "https://images.steamusercontent.com/ugc/9/x/"})
        self.assertTrue(b.messages._rows[-1]["from_self"])
        self.assertEqual(b.messages._rows[-1]["imageUrl"], "https://images.steamusercontent.com/ugc/9/x/")
        self.assertEqual(b.messages._rows[-1]["html"], "")  # preview only, no URL text
        self.assertEqual(b._status, "Image sent")

    def test_upload_failure_shows_no_row(self):
        self.backend._on_image_uploaded("1", "/tmp/gone.png", Mock(), {"error": "nope"})
        self.assertEqual(self.backend.messages._rows, [])

    def test_unrenderable_commit_waits_for_echo(self):
        b = self.backend
        b._on_image_uploaded("1", "/tmp/gone.png", Mock(), {"url": "https://cdn.example/img.png"})
        self.assertEqual(b.messages._rows, [])
        url = "https://images.steamusercontent.com/ugc/9/x/"
        b._on_chat_event("1", {"event": "echo", "text": url, "bbcode": url}, active=True)
        self.assertEqual(len(b.messages._rows), 1)
        self.assertEqual(b.messages._rows[-1]["imageUrl"], url)

    def test_echo_does_not_duplicate_own_image(self):
        b = self.backend
        url = "https://images.steamusercontent.com/ugc/9/x/"
        b._on_image_uploaded("1", "/tmp/gone.png", Mock(), {"url": url})
        b._on_chat_event("1", {"event": "echo", "text": url, "bbcode": url}, active=True)
        self.assertEqual(len(b.messages._rows), 1)

    def test_text_echo_does_not_duplicate_own_line(self):
        b = self.backend
        b._append_message("hello", True, 1760000000)
        b._on_chat_event("1", {"event": "echo", "from": "1", "text": "hello",
                               "bbcode": "hello"}, active=True)
        self.assertEqual(len(b.messages._rows), 1)

    def test_echo_on_other_cdn_host_does_not_duplicate(self):
        # upload commit URL and Steam's stored echo can use different CDN hosts
        b = self.backend
        b._on_image_uploaded("1", "/tmp/gone.png", Mock(), {
            "url": "https://images.steamusercontent.com/ugc/9/x/"})
        b._on_chat_event("1", {"event": "echo", "from": "1", "text": "",
                               "bbcode": "https://steamuserimages-a.akamaihd.net/ugc/9/x/"},
                         active=True)
        self.assertEqual(len(b.messages._rows), 1)

    def test_upload_does_not_duplicate_a_landed_echo(self):
        # Steam's echo can arrive before the upload response
        b = self.backend
        b._on_chat_event("1", {"event": "echo", "from": "1", "text": "",
                               "bbcode": "https://steamuserimages-a.akamaihd.net/ugc/9/x/"},
                         active=True)
        b._on_image_uploaded("1", "/tmp/gone.png", Mock(), {
            "url": "https://images.steamusercontent.com/ugc/9/x/"})
        self.assertEqual(len(b.messages._rows), 1)

    def test_img_tag_with_attributes(self):
        url = "https://images.steamusercontent.com/ugc/9/x/"
        self.assertEqual(backend_mod._image_url(f"[img width=300]{url}[/img]"), url)

    def test_wrapped_image_urls(self):
        url = "https://images.steamusercontent.com/ugc/9/x/"
        self.assertEqual(backend_mod._image_url(f"[url]{url}[/url]"), url)
        self.assertEqual(backend_mod._image_url(f"[url={url}]{url}[/url]"), url)
        self.assertEqual(backend_mod._image_url(f"[sharedfile]{url}[/sharedfile]"), url)
        self.assertEqual(backend_mod._image_url(f"look [img]{url}[/img] now"), url)


class EmoticonTests(_BackendCase):
    def setUp(self):
        super().setUp()
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        for target, name, value in [
            (backend_mod.chat_format, "_ICON_DIR", Path(d)),
            (backend_mod.chat_format, "_download_icon", Mock()),
        ]:
            p = patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)

    def test_property_and_icon_cache_cycle(self):
        b = self.backend
        self.assertIn("steamhappy", b.emoticons)
        self.assertTrue(b.emoticonIcon("kappa").startswith("https://"))  # uncached: remote src
        f = Path(backend_mod.chat_format._ICON_DIR) / "emoticons" / "kappa"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"x")
        self.assertTrue(b.emoticonIcon("kappa").startswith("file://"))  # cached: local src

    def test_sticker_row_renders_and_collects(self):
        b = self.backend
        raw = '[sticker type="mshm2" limit="0"][/sticker]'
        self.receive("1", 100.0, active=True, text=raw, bbcode=raw)
        self.assertEqual(b.messages._rows[-1]["html"], "")  # sticker bubble hides the bbcode
        self.assertIn("sticker/mshm2", b.messages._rows[-1]["stickerUrl"])
        self.assertIn("mshm2", b.stickers)  # seen stickers persist for the picker

    def test_unicode_emoticon_message_renders_and_enriches_picker(self):
        b = self.backend
        b._append_message("ːlunar2019piginablanketː", False, 1760000000)
        row = b.messages._rows[-1]
        self.assertIn("emoticon/lunar2019piginablanket", row["html"])
        self.assertIn("lunar2019piginablanket", b.emoticons)

    def test_steam_cache_merge_enriches_pickers(self):
        b = self.backend
        hits = []
        b.emoticonsChanged.connect(lambda: hits.append("emo"))
        b.stickersChanged.connect(lambda: hits.append("stk"))
        b._on_steam_cache({"emoticons": ["steamhappy", "lunar2019piginablanket"],
                           "stickers": ["Nippy Walking"]})
        self.assertIn("lunar2019piginablanket", b.emoticons)
        self.assertIn("Nippy Walking", b.stickers)
        self.assertEqual(hits, ["emo", "stk"])

    def test_owned_media_merge_strips_colons(self):
        b = self.backend
        hits = []
        b.emoticonsChanged.connect(lambda: hits.append("emo"))
        b.stickersChanged.connect(lambda: hits.append("stk"))
        b._on_owned_media({"emoticons": [":steamhappy:", ":d2axe:"], "stickers": ["happy"]})
        self.assertIn("d2axe", b.emoticons)  # proto colons stripped to our ːnameː spelling
        self.assertNotIn(":d2axe:", b.emoticons)
        self.assertIn("happy", b.stickers)
        self.assertEqual(hits, ["emo", "stk"])

    def test_picker_offers_only_owned_stickers_once_the_list_lands(self):
        # A received sticker must not become sendable: Steam silently drops
        # sticker payloads the account doesn't own (maximilian sees nothing).
        b = self.backend
        raw = '[sticker type="Lunar2020PunchingRat" limit="0"][/sticker]'
        self.receive("1", 100.0, active=True, text=raw, bbcode=raw)
        self.assertIn("Lunar2020PunchingRat", b.stickers)  # remembered while the owned list is unknown
        b._on_owned_media({"stickers": ["Nippy Walking"]})  # the account's real inventory: only the cat
        self.assertEqual(b.stickers, ["Nippy Walking"])
        self.assertNotIn("Lunar2020PunchingRat", b.stickers)

    def test_send_sticker_sends_raw_wire(self):
        b = self.backend
        b.sendSticker("Nippy Walking")
        wire = '[sticker type="Nippy Walking" limit="0"][/sticker]'
        backend_mod._Fetch.assert_called_with(backend_mod.aurelia.chat_send, "1", wire)
        self.assertEqual(b.messages._rows[-1]["text"], wire)  # displayed unescaped

    def test_history_restores_sent_media(self):
        b = self.backend
        b._sent_media = [["1", 1760000000, '[emoticon=steamhappy]'],
                         ["1", 1760000010, '[sticker type="Nippy Walking" limit="0"][/sticker]']]
        b._on_history("1", [
            {"message": "", "from_self": True, "timestamp": 1760000005},   # 5s skew, emoticon
            {"message": "hi", "from_self": True, "timestamp": 1760000006},  # text untouched
            {"message": "", "from_self": True, "timestamp": 1760000012},   # sticker
        ])
        rows = b.messages._rows
        self.assertIn("emoticon/steamhappy", rows[0]["html"])
        self.assertEqual(rows[1]["text"], "hi")
        self.assertIn("sticker/Nippy%20Walking", b.messages._rows[2]["stickerUrl"])
        self.assertIn("Nippy Walking", b.messages._rows[2]["text"])

    def test_sent_media_journal_persists(self):
        with tempfile.TemporaryDirectory() as tmp:  # isolate state file
            with patch.object(backend_mod, "_STATE_FILE", Path(tmp) / "state.json"):
                b = AureliaBackend()
                b.pickFriend("1")
                b.sendSticker("Nippy Walking")
                saved = json.loads((Path(tmp) / "state.json").read_text())
                self.assertEqual(saved["sent_media"][-1][2],
                                 '[sticker type="Nippy Walking" limit="0"][/sticker]')
                with patch.object(backend_mod, "_load_state", Mock(return_value=saved)):
                    again = AureliaBackend()
                    self.assertEqual(again._sent_media[-1][2],
                                     '[sticker type="Nippy Walking" limit="0"][/sticker]')

    def test_transcript_and_copy(self):
        b = self.backend
        b._append_message("hello", True, 1760000000)
        self.assertIn("me: hello", b.transcript())
        with patch.object(backend_mod, "QGuiApplication"):
            b.copyText("abc")
        self.assertIn("Copied", b._status)


class AnimSpeedTests(_BackendCase):
    def test_set_and_clamp(self):
        b = self.backend
        hits = []
        b.animChanged.connect(lambda: hits.append(1))
        b.setAnimSpeed(2)
        self.assertEqual(b.animSpeed, 0.25)
        self.assertEqual(b.animIndex, 2)
        self.assertEqual(hits, [1])
        b.setAnimSpeed(99)  # out of range clamps to the last preset (Off)
        self.assertEqual(b.animSpeed, 0.0)
        self.assertEqual(hits, [1, 1])

    def test_persists_across_restart(self):
        with tempfile.TemporaryDirectory() as tmp:  # isolate state file
            with patch.object(backend_mod, "_STATE_FILE", Path(tmp) / "state.json"):
                b = AureliaBackend()
                b.setAnimSpeed(1)
                saved = json.loads((Path(tmp) / "state.json").read_text())
                self.assertEqual(saved["anim_index"], 1)
                with patch.object(backend_mod, "_load_state", Mock(return_value=saved)):
                    again = AureliaBackend()
                    self.assertEqual(again.animIndex, 1)
                    self.assertEqual(again.animSpeed, 0.5)


class FavouriteTests(_BackendCase):
    def show(self, rows):
        self.backend.friends.set_rows(rows)
        self.backend._sync_friend_items(rows)

    def test_favourite_sorts_first_and_toggles(self):
        b = self.backend
        self.show([
            {"steam_id": "1", "persona_name": "Alpha", "persona_state": 1},
            {"steam_id": "2", "persona_name": "Beta", "persona_state": 1},
            {"steam_id": "3", "persona_name": "Gamma", "persona_state": 0},
        ])
        b.toggleFavourite("3")
        items = b.friendItems
        self.assertEqual(items[0]["steamId"], "3")  # favourite on top, even offline
        self.assertTrue(items[0]["favourite"])
        self.assertIn("favourites", self.state_file.read_text())
        b.toggleFavourite("3")
        self.assertFalse(any(i["favourite"] for i in b.friendItems))
        self.assertNotEqual(b.friendItems[0]["steamId"], "3")

    def test_favourite_survives_hide_offline(self):
        b = self.backend
        b.setHideOffline(True)
        self.show([
            {"steam_id": "1", "persona_name": "Off", "persona_state": 0},
            {"steam_id": "2", "persona_name": "On", "persona_state": 1},
        ])
        b.toggleFavourite("1")
        self.assertEqual([i["steamId"] for i in b.friendItems], ["1", "2"])


class AliasTests(_BackendCase):
    def show(self, rows):
        self.backend.friends.set_rows(rows)
        self.backend._sync_friend_items(rows)

    def test_alias_wraps_name_everywhere(self):
        b = self.backend
        self.show([{"steam_id": "1", "persona_name": "Alpha", "persona_state": 1}])
        b.setAlias("1", "Bro")
        self.assertEqual(b.friendItems[0]["name"], "Bro (Alpha)")
        self.assertEqual(b._tab_items[0]["name"], "Bro (Alpha)")
        self.assertEqual(b.activeFriendName, "Bro (Alpha)")
        self.assertIn("aliases", self.state_file.read_text())
        b.setAlias("1", "")
        self.assertEqual(b.friendItems[0]["name"], "Alpha")

    def test_name_history_records_changes(self):
        b = self.backend
        self.show([{"steam_id": "1", "persona_name": "Alpha", "persona_state": 1}])
        self.show([{"steam_id": "1", "persona_name": "Beta", "persona_state": 1}])
        self.show([{"steam_id": "1", "persona_name": "Beta", "persona_state": 1}])  # dup ignored
        self.assertEqual(b.friendNameHistory("1"), ["Beta", "Alpha"])  # newest first


class EventRoutingTests(_BackendCase):
    def test_events_route_by_friend_and_drop_strangers(self):
        b = self.backend
        b.friends.set_rows([{"steam_id": "2", "persona_name": "Beta", "persona_state": 1}])
        b._route_event({"event": "message", "from": "2", "text": "hi", "bbcode": "hi"})
        self.assertEqual(b._unread, {"2": 1})  # routed to the friend's unread count
        b._route_event({"event": "message", "from": "1", "text": "group spam"})
        self.assertEqual(b._unread, {"2": 1})  # unknown id (e.g. group chat) ignored


class SenderNameTests(_BackendCase):
    def test_history_sender_resolves_when_roster_arrives(self):
        b = self.backend
        b._on_history("1", [{"text": "hello", "from_self": False, "timestamp": 10}])
        self.assertEqual(b.messages._rows[-1]["sender"], "1")  # roster not here yet
        b.friends.set_rows([{"steam_id": "1", "persona_name": "Alpha", "persona_state": 1}])
        b._refresh_active_name(b.friends.rows())
        self.assertEqual(b.messages._rows[-1]["sender"], "Alpha")


class SteamIdGuardTests(_BackendCase):
    def test_bogus_tab_rejected_and_real_id_accepted(self):
        with patch.object(backend_mod, "_valid_steam_id", _REAL_VALID):
            b = AureliaBackend()
            self.addCleanup(b.shutdown)
            b.pickFriend("1")  # the stray id that once leaked into state.json
            self.assertNotIn("1", b._tabs)
            self.assertEqual(b.activeFriendId, "")
            b.pickFriend("76561198001317003")
            self.assertIn("76561198001317003", b._tabs)
            self.assertEqual(b.activeFriendId, "76561198001317003")

    def test_state_tabs_heal_on_load(self):
        with patch.object(backend_mod, "_valid_steam_id", _REAL_VALID), \
             patch.object(backend_mod, "_load_state", _REAL_LOAD_STATE):
            state_file = Path(tempfile.mkdtemp()) / "state.json"
            state_file.write_text('{"tabs": ["1", "76561198001317003"], "active": "1"}')
            with patch.object(backend_mod, "_STATE_FILE", state_file):
                b = AureliaBackend()
                self.addCleanup(b.shutdown)
                self.assertEqual(b._tabs, ["76561198001317003"])  # "1" dropped, state heals on save
                self.assertEqual(b._active_id, "76561198001317003")  # falls back to the first tab


if __name__ == "__main__":
    unittest.main()
