# aur-chat

Lightweight Steam chat UI for Aurelia.

## Build

One step — `make` in the Aurelia checkout builds it and runs this repo's
`install-desktop.sh` (binary, GUI package and desktop entry into `~/.local`):

```sh
cd ../Aurelia
make
```

Build-only is `make build`; the desktop install alone stays
`./install-desktop.sh` from this repo.

`aur-chat` uses `AURELIA_BIN` when set, then a release/debug binary under
`AURELIA_ROOT`, then `aurelia` on `PATH`, and finally the sibling development
checkout at `../Aurelia`.

Log in once with `aurelia login`. The Aurelia daemon owns the Steam session;
the GUI does not read or store Steam credentials. The session is a PC
(Steam-client) login, not a mobile one — it matches how the desktop Steam client
appears on the network.

## Image chat

Select a friend, focus the message field, and press `Ctrl+V` with an image in
the clipboard. The image is written to a temporary PNG and passed to:

```sh
aurelia chat send-image <steamid64> <path> --json
```

Steam's `commitfileupload` posts the resulting URL/image chat message. The GUI
does not send the returned URL a second time, which avoids duplicate messages.
If the clipboard contains text, normal Qt text paste is unchanged.

Images fit the chat bubble width and open at full size when clicked. Right-click
an image to copy its URL or download the image into the clipboard.

Steam shortcuts are read from `shortcuts.vdf`. They launch directly from their
saved executable and `StartDir`; Windows shortcuts use the Proton tool selected
for their AppID in Steam's `config.vdf`, without starting the Steam client.

Library games launch with `steam -applaunch <app_id>`. A game with an Aurelia
launch script (`aurelia scripts list`) launches standalone with
`aurelia play <app_id>` instead, and the script wraps the launch. Dota 2 (570)
uses this to run inside Steam's sniper runtime. See
`docs/aurelia-standalone-launch.md`.

The friend roster supports name/game filtering and colored presence indicators.
**Hide offline** hides offline friends, except those with unread messages or an
open chat. The setting is saved with the open tabs. A friend's played game shows
under their name; when Steam sends only the app id, the name is looked up in
your library cache, otherwise it shows as `app <id>`. In Dota 2 the friend's
hero and level are shown from Steam rich presence (`online · Dota 2 · Axe
Lv 25`), and friends in a joinable game say so.

Sync hiccups are smoothed client-side: empty rosters are retried before the
list empties, persona data a partial sync dropped is carried forward from the
previous poll, and a landslide of simultaneous "offline" flips collapses into
one summary notification instead of a storm.

Your own profile name is shown next to the status selector. Click it (or run
`aurelia persona "New Name"`) to change the name friends see. The gear button
opens the settings, where the friends list font size and the chat font size can
be set (saved with the other settings).
Incoming messages produce a desktop notification and play the system message
sound.

Clicking a friend opens their chat as a tab. **Dual window** (toolbar) moves the
open chats into a second window, like Steam's pop-out. **Restore to one window**
in either window, or closing the second window, brings them back. Open tabs, the
window mode, the hide-offline setting and the font sizes are saved to
`~/.config/aur-chat/state.json` and restored on start.

A new message while the window is minimized flashes its taskbar entry. Restoring
the window opens the chat with the newest unread message.

Chat text is sent the way Steam's own client sends it: `[` and `\` are escaped,
and `/code`, `/pre`, `/quote` and `/spoiler` become Steam BBCode (`[code]`,
`[quote]`, `[spoiler]`). `/me`, `/random`, `/flip`, `/giphy`, `/store` and
`/roomeffect` are sent as typed. Steam's server turns them into their formatted
form, and the chat then shows what Steam stored. Received BBCode is shown
formatted: code, quotes, bold, italic, underline, strike and `/me`. Spoilers are
grey blocks that can be selected, not revealed on hover. Steam emoticons
(`[emoticon=steamhappy]`) render as their images from Steam's CDN, cached under
`~/.cache/aur-chat/emoticons` after the first fetch. The GIF for `/giphy`
is shown as its `/GIPHY` text.

Stickers are not supported: Steam picks them from your inventory, not from a
typed command.

## Troubleshooting friends sync

Both sides keep a timestamped timeline of every friends sync for diagnosing
partial rosters (only some friends fetched, statuses flipping, empty lists):

- GUI: `~/.cache/aur-chat/friends-debug.log` — polls (`data rows=… unnamed=…`),
  carried-forward fields (`carry …`), empty-retry rounds and mass-offline
  detections.
- Daemon: `~/.config/Aurelia/logs/friends-debug.log` — roster fetches
  (`friends-list …`), persona bursts (`persona-req …`), gap retries
  (`persona-gap …`), player-service presence backfill (`persona-service …`)
  and every `friends list` serve (`roster-served …`).

Both are on by default and capped at 1 MiB; the newest log wins the space.
Disable either with `AUR_CHAT_FRIENDS_DEBUG=0` (GUI) or
`AURELIA_FRIENDS_DEBUG=0` (daemon).
