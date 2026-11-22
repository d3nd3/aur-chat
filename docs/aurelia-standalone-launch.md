# Can Aurelia launch Steam games without the Steam client?

Short answer: **partly**. Aurelia can do most of the work Steam normally does (login, library, downloads, updates, ownership checks) without the Steam client. Actually *starting* a game without the client only works for some games, and it's the hard part.

Status as of 2026-10-16, on this machine (Aurelia 0.1.37).

## What works without the Steam client

Aurelia talks to Steam's servers directly, so these run with Steam closed:

- **Login**: `aurelia login`, including QR and Steam Guard. The session is stored encrypted.
- **Library and metadata**: `list`, `info`, `search`, `depots`, `manifests`, `branches`.
- **Install and update**: we downloaded Dota 2's pending depots with `aurelia update 570` while Steam was closed, so downloads don't need the client.
- **Ownership and DRM checks**: `aurelia drm <app_id>` verifies a game's DRM tickets headlessly.
- **Cloud saves, achievements, friends, chat**: exposed as commands, though they depend on the account and on Steam's servers.

## What starting a game involves

`aurelia play <app_id>` runs in one of two modes:

1. **Standalone** (default). Aurelia runs the game executable itself, natively or through Proton/Wine. No Steam client is involved. Aurelia's own message for this case is: *"--steam was requested but neither a host Steam client nor the in-Wine Steam runtime is installed; launching standalone. DRM-protected games will not run."*
2. **Steam bridge** (`--steam`). Aurelia hands the game to a Steam client. The client can be:
   - the **host Steam client** (what's installed on the desktop), started with `steam -silent` if it isn't running, then sent `-applaunch <id>`; or
   - an **in-Wine Steam client** in Aurelia's own master prefix, which needs a configured `steam_runtime_runner` (a Proton/GE build) and `aurelia steam-runtime install`.

`--steam` is also implied for Family-Shared games, because they need Steam's ownership handshake.

The global policy (`steam_runtime_policy`) is `auto`: the host client is preferred, and the in-Wine runtime is only used under `--steam` when no host client is installed.

On this machine the in-Wine runtime is **not** set up: `steam-runtime status` reports `steam.exe present: no` and the runner is unset.

## Game types

| Game type | Standalone, no Steam client | Notes |
| --- | --- | --- |
| DRM-free, native Linux | Likely works | Not tested here. |
| DRM-free Windows (run through Proton) | Likely works | Needs a Proton runner and per-game fixes. Not tested here. |
| Native Linux games that expect Steam's runtime (Dota 2, CS2) | **Fails** without a wrapper; wrapped in Steam's sniper runtime (option E) it's untested | Observed: `FATAL: It appears dota.sh was not launched within the Steam for Linux sniper runtime environment.` |
| Games using the Steamworks API (achievements, online play, matchmaking) | Starts, but online features fail | General behaviour: the API talks to a running Steam client. |
| SteamStub or CEG-protected games | **Fails** | `--steam` or a host client is required. |
| Games with VAC or other anti-cheat | Usually fails | General behaviour: anti-cheat expects the Steam client. |

Dota 2 is the case we hit. It's a native Linux game that expects to be started through Steam's sniper runtime container. Standalone it fails on startup, so aur-chat runs it through a launch script that wraps it in that runtime and starts Steam silently first (option E). Other games still launch through Steam.

The first standalone run, with the Steam client closed, showed "Lost connection to Steam" in-game. Steamworks needs a logged-in client, and the standalone launch didn't apply the Steam launch options (`-novid` and the rest).

## Challenges and complexity

1. **The Steamworks API needs a running client.** Games call into `steam_api` to talk to a Steam client over a local pipe, and to get user auth tickets. Standalone, the API can't get a session, so features like matchmaking and achievements fail, or the game refuses to start. Replacing the client means reimplementing that IPC, which is a large job.
2. **The Linux Steam Runtime container.** Valve's native Linux games expect `SteamLinuxRuntime_sniper` and its `_v2-entry-point`, with specific environment variables. Standalone, Aurelia has to reproduce that container, or the game refuses to run (the Dota 2 failure above).
3. **DRM.** SteamStub and CEG check ownership through the client. Aurelia can verify the tickets, but nothing here removes DRM, and I wouldn't try. Games with that protection need a client.
4. **Terms of service and account risk.** Aurelia implements Steam's own protocol outside the official client. Valve's Steam Subscriber Agreement may restrict this, and it could put the account at risk. This is a risk to weigh, not legal advice.
5. **Two clients on one account.** If the official client and Aurelia both run, they can fight over state. We already saw this: Steam's `content_log.txt` kept saying "Update Required" after Aurelia had updated the game, so aur-chat had to check the app manifest's timestamp instead.
6. **Presence and "playing" state.** Friends see "In game" from the official client. A game started standalone won't show that unless Aurelia reports it, and the presence Aurelia announces is a separate setting (`aurelia config presence`).
7. **Process lifecycle.** `aurelia play` runs until the game exits, so tracking it is straightforward. But the Steam bridge can return early or fail silently. That's what happened here: `aurelia play --steam` exited without starting Dota 2, and its output was discarded.
8. **Runtime and Proton setup.** Windows games need a Proton/Wine build, a prefix, and often per-game fixes (`protonfixes`). Each game can behave differently, so standalone support is a per-game problem, not one switch.
9. **Scope of the in-Wine Steam runtime.** It's the most self-contained option, but it needs a Windows Steam install under Wine, a separate login, and it's the most fragile part (the `steam-runtime` commands cover install, repair, login and stop).

## Options for aur-chat

- **A. Host Steam via `steam -applaunch`** (default for library games). Steam starts the game with its own runtime and DRM handling. Needs the host client, which is the most reliable path for Steam games.
- **B. Aurelia `--steam` bridge.** Same idea through Aurelia. Its launch step failed here, and its errors were hidden until now.
- **C. Standalone via Aurelia.** Fine for DRM-free games with a Proton runner. Not usable for Dota 2.
- **D. In-Wine Steam runtime.** Works without the host client, but needs a configured runner, a Windows Steam install and a separate login. Untested here.
- **E. Reuse Steam's installed sniper runtime, with a Steam client running** (implemented for Dota 2, 570). Steam already installs `~/.steam/debian-installation/steamapps/common/SteamLinuxRuntime_sniper`. Its `run-in-sniper` script runs a command inside the sniper container. Checked here: inside it, `/etc/os-release` reports `VERSION_CODENAME=sniper`, which is the check `dota.sh` makes. The launch script `~/.config/Aurelia/scripts/570.sh` does three things:
  1. If Steam isn't running, starts it with `steam -silent` and waits for its `connection_log.txt` to show a new logon.
  2. Reads the launch options from `AUR_CHAT_LAUNCH_OPTIONS` (set by aur-chat from your Steam settings), and applies them around the game the way Steam does: prefixes such as `gamemoderun` go before, and the rest go after the game.
  3. Runs the game with `run-in-sniper -- "$@"`.

  This fixes the runtime check and the launch options. Steamworks now has a client to connect to. It's untested end to end: it hasn't been launched with Steam closed, and Dota 2 may still report a connection problem.

For Steam games, option A is the practical choice. Standalone (C) is worth adding only for games that don't need Steam at all.

## How aur-chat launches games

- A game with an Aurelia launch script (`aurelia scripts list`) launches standalone with `aurelia play <app_id>`, and the script wraps it. Today that's only Dota 2 (570).
- Every other game launches with `steam -applaunch <app_id>` (option A), tracked through Steam's presence.
- Standalone launches are tracked as local processes: "In game" shows in aur-chat. End game runs `aurelia stop <app_id>`, and Force end runs `aurelia stop --force <app_id>` (SIGKILL). Steam's own presence doesn't show them.
- Steam-launched games: End game asks Steam to close the game (`steam://close/<app_id>`). Force end sends SIGKILL to every process whose environment has `SteamAppId=<app_id>`.
- For Dota 2, the script starts Steam silently when it isn't running. Steam stays running after the game exits.
- `aurelia play` checks for updates before launching (its default), so pending updates install first. aur-chat refuses Play while that game is downloading in aur-chat, so the two don't collide.
- To add another game: `aurelia scripts new <app_id>`, then edit the script. aur-chat picks it up on the next Play.

## Open questions

- Does `steam -applaunch` return straight away, or block while Steam is closed? This still needs a live test.
- Does Dota 2 connect to the silently started Steam client, and does it start without "Lost connection to Steam"? Not tested yet.
- Are the Steam launch options applied under the wrapper? The wrapper code is tested with stubs; the real game hasn't been launched with the wrapper yet.
- Does `aurelia stop 570` end a game started with `aurelia play`? Not tested yet.
- Would the in-Wine runtime (D) run Dota 2 well enough to be worth the setup? Not tested.
