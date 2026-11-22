"""Steam chat text: outgoing shortcuts and escaping, and rich text for incoming BBCode."""

from __future__ import annotations

import html
import json
import re
import threading
import urllib.parse
import urllib.request
from pathlib import Path

_SHORTCUT = re.compile(r"^/(code|pre|quote|spoiler) (.+)$")
_EMO_TYPED = re.compile(r":([A-Za-z][A-Za-z0-9_-]{1,31}):")  # typed shorthand, like Steam's input
_SPECIAL = re.compile(r"([\[\\])")
_ESCAPED = re.compile(r"\\(.)", re.DOTALL)
_PAIR = re.compile(
    r"\[(b|i|u|strike|code|pre|quote|spoiler|random|flip|img|url|emoticon|sticker)((?:=|\s)[^\]]*)?\](.*?)\[/\1\]",
    re.IGNORECASE | re.DOTALL,
)
_ARG = re.compile(r'(\w*)=(?:"([^"]*)"|([^\s"\]]*))')
# aur-chat's own look: dark boxes that read the same on light and dark bubbles.
_CODE_BOX = (
    '<table width="100%" cellspacing="0" cellpadding="4"><tr><td bgcolor="#2b2b33">'
    '<pre style="margin-top:0;margin-bottom:0;color:#d8dee9">{}</pre></td></tr></table>'
)
_QUOTE_BOX = (
    '<table width="100%" cellspacing="0" cellpadding="4"><tr><td bgcolor="#37474f">'
    '<span style="color:#d8dee9">{}</span></td></tr></table>'
)
_QUOTE_NAME = '<b style="color:#82b1ff">{}</b><br>'
_SIMPLE = {
    "b": "<b>{}</b>",
    "i": "<i>{}</i>",
    "u": "<u>{}</u>",
    "strike": "<s>{}</s>",
    "code": _CODE_BOX,
    "pre": _CODE_BOX,
    "spoiler": '<span style="background-color:#8b8b8b; color:#8b8b8b">{}</span>',
}
_FLIP = {"heads": "Heads", "tails": "Tails"}

# Steam icons: [emoticon=name]/[sticker type="x"] -> the CDN image, cached on disk so repeats render locally.
_ICON_URL = {
    "emoticons": "https://community.cloudflare.steamstatic.com/economy/emoticon/{}",
    "stickers": "https://community.cloudflare.steamstatic.com/economy/sticker/{}",
}
_ICON_DIR = Path.home() / ".cache" / "aur-chat"
_ICON_PENDING: set = set()
_ICON_LOCK = threading.Lock()
_ICON_UNSAFE = re.compile(r"[^A-Za-z0-9_:-]")
EMOTICON_NAMES = [  # curated; each verified to exist on the CDN
    "steamhappy", "steamfacepalm", "steamthumbsup", "steamthumbsdown", "steamsad",
    "steamdance", "baphomet", "kappa", "doge", "trollface", "pika", "lmao", "huh",
    "feelsbad", "happy", "sad", "love", "heart", "laugh", "cry", "mad", "wow",
    "cool", "oops", "blush", "yum", "gg", "ez", "salty", "shake", "think",
]


def icon_path(kind: str, name: str) -> Path:
    safe = _ICON_UNSAFE.sub("_", name) or "x"
    return _ICON_DIR / kind / safe


def download_icon(kind: str, name: str) -> str:
    """Blocking fetch for warm-up: cache the media (converted GIF) if missing,
    then return its src — local file when possible, remote as fallback."""
    p = icon_path(kind, name)
    if not p.is_file():
        _download_icon(kind, name, p.name)
    if p.is_file():
        if kind == "stickers":
            _regif(p)
        return p.as_uri()
    return _ICON_URL[kind].format(urllib.parse.quote(name))


def icon_src(kind: str, name: str) -> str:
    """Src for the picker/message: cached file, else kick a fetch and use the remote src."""
    p = icon_path(kind, name)
    if p.is_file():
        if kind == "stickers":
            _regif(p)
        return p.as_uri()
    with _ICON_LOCK:  # one background fetch per icon per session
        if (kind, name) not in _ICON_PENDING:
            _ICON_PENDING.add((kind, name))
            thread = threading.Thread(target=_download_icon,
                                      args=(kind, name, icon_path(kind, name).name), daemon=True)
            thread.start()
    return _ICON_URL[kind].format(urllib.parse.quote(name))


def emoticon_icon(name: str) -> str:
    return icon_src("emoticons", name)


def _download_icon(kind: str, name: str, safe: str):
    try:
        data = urllib.request.urlopen(_ICON_URL[kind].format(urllib.parse.quote(name)),
                                      timeout=15).read()
        (_ICON_DIR / kind).mkdir(parents=True, exist_ok=True)
        (_ICON_DIR / kind / safe).write_bytes(_as_animated_gif(data) or data)
    except OSError:
        pass  # the html keeps the remote src as fallback


def _as_animated_gif(data: bytes) -> bytes | None:
    """Steam's chat media is animated PNG, which this Qt build can't play; GIF it can.
    Single-frame media also becomes a (static) GIF so cached stickers are always GIFs.
    Each composited frame keeps its transparency (reserved index + restore-to-bg
    disposal) — naive RGBA→GIF turns the background opaque black and smears trails."""
    try:
        import io
        from PIL import Image
        im = Image.open(io.BytesIO(data))
        durs, frames = [], []
        for i in range(getattr(im, "n_frames", 1)):
            im.seek(i)
            durs.append(im.info.get("duration", 100))
            rgba = im.convert("RGBA")
            alpha = rgba.getchannel("A")
            p = rgba.convert("RGB").quantize(colors=255)  # index 255 = transparency
            p.paste(255, alpha.point(lambda a: 255 if a < 128 else 0))
            frames.append(p)
        out = io.BytesIO()
        frames[0].save(out, "GIF", save_all=True, append_images=frames[1:],
                       duration=durs, loop=0, transparency=255, disposal=2)
        return out.getvalue()
    except Exception:
        return None  # static bytes still render the first frame


def _regif(p: Path):
    """One-time cache migration: files stored before APNG→GIF conversion exist are
    raw APNG, which AnimatedImage shows as a frozen frame — convert them in place."""
    try:
        with p.open("rb") as f:
            if f.read(6).startswith(b"GIF8"):
                return
        data = p.read_bytes()
        gif = _as_animated_gif(data)
        if gif:
            p.write_bytes(gif)
    except OSError:
        pass


def _emoticon_img(name: str) -> str:
    return f'<img src="{icon_src("emoticons", name)}" alt=":{html.escape(name)}:">'


def _sticker_img(name: str) -> str:
    src = icon_src("stickers", name)
    return f'<img src="{src}" width="96" height="96" alt=":sticker:{html.escape(name)}:">'


_STICKER = re.compile(r'\[sticker[^\]]*?\btype\s*=\s*"([^"]+)"')

# YouTube links: watch?v=ID (any param order), shorts, embed, live, youtu.be.
_YT = re.compile(
    r"(?:https?://)?(?:www\.|m\.)?"
    r"(?:youtube\.com/(?:watch\?(?:[^\s]*&)?v=|shorts/|embed/|live/)|youtu\.be/)"
    r"([\w-]{11})(?:[?&][^\s]*)?")


def youtube_card(text: str) -> str:
    """Video id when the message is essentially just a YouTube link."""
    value = str(text or "").strip()
    if m := re.fullmatch(r"\[url(?:=[^\]]*)?\]\s*(.*?)\s*\[/url\]", value, re.S | re.I):
        value = m.group(1).strip()
    return m.group(1) if (m := _YT.fullmatch(value)) else ""


def youtube_info(vid: str) -> dict:
    """Public oEmbed metadata (no API key); {} on failure — the card still renders."""
    try:
        q = urllib.parse.urlencode({"url": f"https://www.youtube.com/watch?v={vid}", "format": "json"})
        with urllib.request.urlopen(f"https://www.youtube.com/oembed?{q}", timeout=10) as r:
            data = json.loads(r.read().decode())
        return {"title": str(data.get("title") or vid), "author": str(data.get("author_name") or "")}
    except (OSError, ValueError):
        return {"error": "youtube metadata unavailable"}


def sticker_name(text: str) -> str:
    m = _STICKER.search(str(text or ""))
    return m.group(1) if m else ""


def sticker_only(text: str) -> bool:
    """True when the whole message is a single sticker tag (no surrounding text)."""
    name = sticker_name(text)
    if not name:
        return False
    s = str(text or "").strip()
    return s in (
        f'[sticker type="{name}" limit="0"][/sticker]',
        f'[sticker limit="0" type="{name}"][/sticker]',
        f'[sticker type="{name}"][/sticker]',
    )


# Steam encodes emoticons in plain message text as "ːnameː" (U+02D0 letter colons).
_UNI_EMO = re.compile("ː([^ː\\s]{1,64})ː")


def uni_emoticon_names(text: str) -> list[str]:
    return _UNI_EMO.findall(str(text or ""))


_STEAM_CACHE_DIRS = (  # Steam's own client caches economy media from these URLs
    Path.home() / ".steam/steam/config/htmlcache/Default/Cache/Cache_Data",
    Path.home() / ".local/share/Steam/config/htmlcache/Default/Cache/Cache_Data",
)


def steam_cache_names(base: Path | None = None) -> dict[str, list[str]]:
    """Chat emoticon/sticker names found in Steam's client media cache."""
    pat = re.compile(rb"economy/(emoticon|sticker)/([A-Za-z0-9_.%-]{1,64})")
    found: dict[str, set] = {"emoticons": set(), "stickers": set()}
    for d in ([base] if base else _STEAM_CACHE_DIRS):
        if not d.is_dir():
            continue
        for f in d.iterdir():
            if f.is_file():
                for kind, name in pat.findall(f.read_bytes()):
                    found["stickers" if kind == b"sticker" else "emoticons"].add(
                        urllib.parse.unquote(name.decode("ascii", "ignore")))
    return {k: sorted(v) for k, v in found.items()}


def escape_bbcode(text: str) -> str:
    """Escape `[` and `\\` so typed text is shown literally, as Steam's own client does."""
    return _SPECIAL.sub(r"\\\1", text)


def to_bbcode(text: str) -> str:
    """The wire text for a typed line: `/code x` -> `[code]x[/code]`, `:name:` -> `[emoticon=name]`;
    conversion happens after escaping so the produced tag stays raw, like Steam's own input."""
    m = _SHORTCUT.match(text)
    if not m:
        return _EMO_TYPED.sub(lambda e: f"[emoticon={e.group(1)}]", escape_bbcode(text))
    tag = "code" if m.group(1) == "pre" else m.group(1)
    return f"[{tag}]{escape_bbcode(m.group(2))}[/{tag}]"


def _convert(m: re.Match) -> str:
    tag, attrs, inner = m.group(1).lower(), m.group(2) or "", m.group(3)
    args = {key: quoted or bare for key, quoted, bare in _ARG.findall(attrs)}
    if tag == "quote":
        name = args.get("")
        head = _QUOTE_NAME.format(name) if name else ""
        return _QUOTE_BOX.format(head + inner)
    if tag == "random" and "result" in args:  # the server's roll, e.g. [random min=1 max=100 result=42]
        return f"/random {args.get('min', '')}-{args.get('max', '')} : {args['result']}"
    if tag == "flip" and "result" in args:
        return "/flip: " + _FLIP.get(args["result"], args["result"])
    if tag == "img" and "giphy_search" in args:
        return f"/giphy {args['giphy_search']}"
    if tag == "emoticon":
        name = (args.get("") or "").strip() or inner.strip()  # attribute form, or inner text
        return _emoticon_img(name) if name else ""
    if tag == "sticker":
        name = (args.get("type") or "").strip() or inner.strip()
        return _sticker_img(name) if name else ""
    return _SIMPLE.get(tag, "{}").format(inner)  # url and img show their text; the image is handled apart


_EMO_SELF = re.compile(r"\[emoticon=([^\]]+)\]")  # the common wire form has no closing tag


def _body(raw: str) -> str:
    # Escape HTML first, then turn `\x` into an entity so the tag pass cannot see it.
    text = _ESCAPED.sub(lambda m: f"&#{ord(m.group(1))};", html.escape(raw, quote=False))
    text = _UNI_EMO.sub(lambda m: f"[emoticon={m.group(1)}]", text)
    prev = None
    while prev != text:  # repeat so nested tags are converted from the inside out
        prev, text = text, _PAIR.sub(_convert, text)
    text = _EMO_SELF.sub(lambda m: _emoticon_img(m.group(1).strip()), text)
    return text.replace("\n", "<br>")


def to_html(raw: str) -> str:
    """Rich text (for a TextEdit) for a message as Steam stores it; unknown tags stay as typed."""
    if raw.startswith("/me "):
        return f"<i>* {_body(raw[4:])}</i>"
    return _body(raw)
