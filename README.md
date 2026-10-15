# aur-chat

Lightweight Steam chat UI for Aurelia.

## Build

Build the sibling Aurelia fork first:

```sh
cd /home/dinda/git-projects/d3nd3/Aurelia
cargo build --release --features keyring/vendored
cd /home/dinda/git-projects/d3nd3/public/utility/aur-chat
./install-desktop.sh
```

`aur-chat` uses `AURELIA_BIN` when set, then a release/debug binary under
`AURELIA_ROOT`, then `aurelia` on `PATH`, and finally the sibling development
checkout at `../../../Aurelia`.

Log in once with `aurelia login`. The Aurelia daemon owns the Steam session;
the GUI does not read or store Steam credentials.

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

The friend roster supports name/game filtering and colored presence indicators.
Incoming messages produce a desktop notification and play the system message
sound.
