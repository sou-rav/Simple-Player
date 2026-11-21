#!/usr/bin/env python3
"""
Simple Player - a minimal video player on VLC's engine (libVLC),
styled after the Windows 11 Media Player.

Only playback features: no network, streaming or conversion tools.
"""
import ctypes
import faulthandler
try:
    import ctypes.wintypes as wintypes
except Exception:      # not on Windows
    wintypes = None
import datetime
import math
import hashlib
import json
import os
import random
import re
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

APP_NAME = "SimplePlayer"
APP_TITLE = "Simple Player"


# --------------------------------------------------------------------------
# Locate libVLC (a "vlc" folder next to the program wins, else installed VLC)
# --------------------------------------------------------------------------
def _base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


def _pe_machine(path):
    """'x64', 'x86', ... for a Windows DLL (reads the file header only), None if unreadable."""
    try:
        with open(path, "rb") as f:
            head = f.read(64)
            if head[:2] != b"MZ":
                return None
            f.seek(int.from_bytes(head[60:64], "little"))
            sig = f.read(6)
        if sig[:4] != b"PE\0\0":
            return None
        m = int.from_bytes(sig[4:6], "little")
        return {0x8664: "x64", 0x14C: "x86", 0xAA64: "arm64"}.get(m, hex(m))
    except OSError:
        return None


def _vlc_problem(d):
    """Why the VLC folder d cannot be used, or None when it looks complete and the right bitness."""
    d = Path(d)
    if not d.is_dir():
        return "folder not found"
    missing = [n for n in ("libvlc.dll", "libvlccore.dll", "plugins") if not (d / n).exists()]
    if missing:
        return "missing " + ", ".join(missing)
    want = "x64" if sys.maxsize > 2 ** 32 else "x86"
    got = _pe_machine(d / "libvlc.dll")
    if got in ("x64", "x86") and got != want:
        return f"libvlc.dll is {got}, but this program is {want} (install the matching VLC)"
    return None


_dll_dir_handles = []       # keep the add_dll_directory handles alive


def _locate_vlc():
    """Windows: the first usable VLC folder, bundled one first. Loads libvlccore.dll and libvlc.dll
    from it and points python-vlc at it (inside this process only, nothing is changed on the PC).
    Returns (folder or None, source text, list of problems)."""
    if sys.platform != "win32":
        return None, "system", []
    cands = [_base_dir() / "vlc"]
    for env in ("ProgramW6432", "ProgramFiles"):
        if os.environ.get(env):
            cands.append(Path(os.environ[env]) / "VideoLAN" / "VLC")
    problems, seen = [], set()
    for d in cands:
        if str(d).lower() in seen:
            continue
        seen.add(str(d).lower())
        why = _vlc_problem(d)
        if why:
            problems.append(f"{d}: {why}")
            continue
        try:
            if hasattr(os, "add_dll_directory"):
                _dll_dir_handles.append(os.add_dll_directory(str(d)))
            ctypes.CDLL(str(d / "libvlccore.dll"))
            ctypes.CDLL(str(d / "libvlc.dll"))
        except OSError as exc:
            problems.append(f"{d}: Windows could not load it ({exc})")
            continue
        os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")
        os.environ["PYTHON_VLC_LIB_PATH"] = str(d / "libvlc.dll")
        os.environ["PYTHON_VLC_MODULE_PATH"] = str(d / "plugins")
        os.environ["VLC_PLUGIN_PATH"] = str(d / "plugins")      # never an old value from the PC
        kind = "bundled" if d == cands[0] else "installed"
        return d, f"{kind} {d}", problems
    return None, "none", problems


_vlc_found, VLC_SOURCE, _vlc_problems = _locate_vlc()
VLC_ERROR = None
vlc = None
if _vlc_found is not None or sys.platform != "win32":
    try:
        import vlc
    except BaseException as exc:      # python-vlc calls sys.exit(1) when its library will not load
        vlc = None
        VLC_ERROR = f"{type(exc).__name__}: {exc}"
if vlc is None:
    VLC_ERROR = "\n".join(_vlc_problems + ([VLC_ERROR] if VLC_ERROR else [])) or "libvlc.dll was not found"

from PySide6.QtCore import (QByteArray, QEasingCurve, QEvent, QObject, QPoint, QPointF, QPropertyAnimation, QRect, QRectF,
                            QSize, QStandardPaths, Qt, QTimer, QVariantAnimation, Signal)
from PySide6.QtGui import (QActionGroup, QBrush, QColor, QCursor, QFont,
                           QFontMetrics, QIcon, QImage, QLinearGradient,
                           QPainter, QPainterPath, QPalette, QPen, QPixmap, QPolygonF,
                           QConicalGradient, QRadialGradient, QRegion, QRasterWindow, QMouseEvent, QWheelEvent, QKeyEvent, QEnterEvent)
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (QAbstractButton, QProxyStyle, QStyle, QWidgetAction, QApplication, QLineEdit, QStackedWidget, QAbstractItemView, QStyledItemDelegate, QListWidgetItem, QCheckBox, QComboBox, QDialog,
                               QFileDialog, QFrame, QHBoxLayout, QListWidget, QSlider,
                               QGraphicsOpacityEffect, QGridLayout, QLabel, QMainWindow, QMenu, QMessageBox, QPushButton,
                               QScrollArea, QSizePolicy, QVBoxLayout, QWidget)

# --------------------------------------------------------------------------
# Look
# --------------------------------------------------------------------------
BG = "#151515"
TEXT = "#ffffff"
MUTED = "#a0a0a0"
ACCENT = "#ff8a2a"
TRACK = "#6b6b6b"
DISABLED = "#5a5a5a"

# ---- Flat colours measured on the ACTIVE Media Player (ref8, empty areas). Real Mica was tried and removed (it broke the native child windows).
FLAT_NAV = "#221E22"               # left bar 34,30,34
FLAT_TITLE = "#27262E"             # Home title bar 39,38,46
FLAT_HOME_TOP = "#29262C"          # Home page, top 41,38,44
FLAT_HOME_BOTTOM = "#2E2524"       # Home page, bottom 46,37,36
FLAT_HOME_BAR = "#2D2524"          # Home control bar 45,37,36
HOME_BAR_LINE = "#241A18"          # 1 physical px line on top of the Home bar 36,26,24


VIDEO_EXT = {".mkv", ".mp4", ".m4v", ".avi", ".mov", ".wmv", ".flv", ".webm", ".mpg",
             ".mpeg", ".ts", ".m2ts", ".mts", ".3gp", ".ogv", ".vob", ".divx", ".rmvb",
             ".asf", ".mxf"}
SUB_EXT = {".srt", ".ass", ".ssa", ".sub", ".vtt", ".smi"}

# The Windows 11 Media Player (WinUI 3) draws everything in "Segoe UI Variable", which Windows 11 installs as
# three optical sizes: Small (captions, 12 px and below), Text (body) and Display (large titles). Older Windows
# has only "Segoe UI", which stays as the fallback.
# Semibold (600) is what the Media Player uses for titles. Windows can show it as a separate family name, so the
# Semibold names come first; if a name is missing Qt moves on to the next one (same look as before).
DISPLAY_QSS = ("font-family:'Segoe UI Variable Display Semibold','Segoe UI Variable Display','Segoe UI Variable Text',"
               "'Segoe UI Semibold','Segoe UI';")
SEMI_QSS = ("font-family:'Segoe UI Variable Text Semibold','Segoe UI Variable Text','Segoe UI Semibold','Segoe UI';")
SMALL_QSS = "font-family:'Segoe UI Variable Small','Segoe UI Variable Text','Segoe UI';"


def ui_families(px, weight=QFont.Normal):
    size = "Display" if px >= 28 else ("Small" if px <= 12 else "Text")
    if weight == QFont.DemiBold:
        return [f"Segoe UI Variable {size} Semibold", f"Segoe UI Variable {size}", "Segoe UI Variable Text",
                "Segoe UI Semibold", "Segoe UI"]
    return [f"Segoe UI Variable {size}", "Segoe UI Variable Text", "Segoe UI"]


def ui_font(px, weight=QFont.Normal):
    font = QFont()
    font.setFamilies(ui_families(px, weight))
    font.setPixelSize(px)
    font.setWeight(weight)
    return font


def fmt(ms):
    s = max(0, int(ms // 1000))
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


# ---- Display names (Phase 5). ONE function for every label. It only changes what is SHOWN: the file, its path
# and the history key are never touched. If the name is not understood with confidence, the plain cleaned
# file name is shown instead of guessing.
KEEP_EPISODE_TITLE = True        # "Show S01E02 The Pilot" (False: "Show S01E02")
SHOW_YEAR = False                # True: "The Matrix (1999)", False: "The Matrix" (your request, October 2026)
# Website / uploader names that start a file name ("CineCloud - CINEFREAK TOP - Title"). A leading
# " - " part is dropped when it starts with one of these (letters only, any case). Add more here.
SITE_WORDS = ("cinecloud", "cinefreak", "yts", "rarbg", "eztv", "tamilmv", "tamilblasters", "moviesmod",
              "vegamovies", "filmyzilla", "bolly4u", "hdhub4u", "mkvcinemas", "psarips", "pahe", "ettv", "1337x")
_TAG_ANY = re.compile(                                     # technical tags, any letter case
    r"^(?:\d{3,4}[pi]|4k|8k|uhd|blu|bluray|bdrip|brrip|bdremux|webrip|webdl|hdrip|dvdrip|dvdscr|hdtv|pdtv|"
    r"remux|x26[45]|h26[45]|hevc|avc|av1|xvid|divx|vp9|aac\d*|ac3|eac3|ddp\d*|dd\d+|dts|truehd|atmos|flac|opus|"
    r"10bit|8bit|hdr\d*|hdr10plus|proper|repack|internal|readnfo)$", re.I)
_TAG_UPPER = re.compile(                                   # ambiguous as normal words: only when written in capitals
    r"^(?:WEB|NF|AMZN|DSNP|HMAX|ATVP|HULU|PCOK|PMTP|STAN|CAM|HDCAM|HD|DV|TS|MA)$")
_YEAR = re.compile(r"^\(?(?:19|20)\d{2}\)?$")
_DISPLAY_CACHE = {}
# Rename on Home cards. Display only: files, paths and history keys are never touched. Store fills these.
_CUSTOM_NAMES = {}        # path key -> {"name", "size"}: a name typed for one file
_SHOW_NAMES = {}          # show key -> new show name (renaming a stack of episodes)
_NAME_RULES = []          # [{"side": "start" | "end", "text": piece}] pieces the user chose to hide everywhere
_SIZE_CACHE = {}
_COMMON_PIECES = frozenset(
    "a an the and of in on at to for with from by part movie film video videos full new hd uhd episode ep "
    "season series show final official trailer clip clips home vol volume copy file final edit cut".split())
_PIECE_SEP = " -._"


def _name_key(path):
    return os.path.normcase(os.path.abspath(path))


def _file_size(path):
    k = _name_key(path)
    if k not in _SIZE_CACHE:
        try:
            _SIZE_CACHE[k] = os.path.getsize(path)
        except OSError:
            _SIZE_CACHE[k] = None
    return _SIZE_CACHE[k]


def set_name_overrides(custom, shows, rules):
    """Install the user's names (called by Store whenever they change) and forget every cached name."""
    global _CUSTOM_NAMES, _SHOW_NAMES, _NAME_RULES
    _CUSTOM_NAMES, _SHOW_NAMES, _NAME_RULES = custom, shows, rules
    _DISPLAY_CACHE.clear()
    _SIZE_CACHE.clear()


def _apply_rules(name):
    for r in _NAME_RULES:
        t = re.escape(r["text"])
        pat = rf"^{t}[{re.escape(_PIECE_SEP)}]+" if r["side"] == "start" else rf"[{re.escape(_PIECE_SEP)}]+{t}$"
        out = re.sub(pat, "", name, count=1, flags=re.I)
        if out.strip():
            name = out.strip()
    return name


def piece_is_safe(piece):
    """A hidden piece must not be short or common: it would cut names it should not touch."""
    words = re.findall(r"[^\W_]+", piece.lower())
    letters = sum(len(w) for w in words)
    if letters < 4 or not any(c.isalpha() for c in piece) or all(w in _COMMON_PIECES for w in words):
        return False
    return not any(pat.search(piece) for pat in _EP_PATTERNS)


def detect_cut(old, new):
    """When `new` is `old` with a piece cut from the start or the end, return (side, piece); else None."""
    o, n = " ".join(old.split()), " ".join(new.split())
    ol, nl = o.lower(), n.lower()
    if not n or len(n) >= len(o):
        return None
    if ol.endswith(nl) and o[len(o) - len(n) - 1] in _PIECE_SEP:            # a piece was cut from the start
        piece = o[:len(o) - len(n)].strip(_PIECE_SEP)
        return ("start", piece) if piece else None
    if ol.startswith(nl) and o[len(n)] in _PIECE_SEP:                       # a piece was cut from the end
        piece = o[len(n):].strip(_PIECE_SEP)
        return ("end", piece) if piece else None
    return None


def _is_tag(tok):
    first = tok.split("-")[0]                              # "x265-MeGusta" and "WEB-DL" start with a tag
    return bool(_TAG_ANY.match(first) or _TAG_UPPER.match(first) or _TAG_ANY.match(tok.replace("-", "")))


def _normalise(stem):
    """Separators to spaces, release brackets removed. Keeps dots in 'Mr. Robot' and 'S.W.A.T.'."""
    stem = re.sub(r"^(?:www\.)\S+\s+-\s+", "", stem)                       # "www.site.com - Title"
    stem = re.sub(r"[\[(]((?:19|20)\d{2})[\])]", r" (\1) ", stem)           # [2019] / (2019) -> (2019)
    stem = re.sub(r"\[[^\]]*\]", " ", stem)                                # [EZTVx.to] [YTS.MX] [1080p]
    def paren(m):                                                          # (1080p WEB-DL) goes, (No Way Home) stays
        words = re.split(r"[\s._]+", m.group(1).strip())
        return " " if any(_is_tag(w) or "." in w for w in words if w) else m.group(0)
    stem = re.sub(r"\(([^)]*)\)", paren, stem)
    stem = re.sub(r"(?<![A-Za-z0-9])(?:[A-Za-z]\.){2,}[A-Za-z]?", lambda m: m.group(0).replace(".", "\x00"), stem)
    stem = stem.replace("_", " ")
    stem = re.sub(r"(?<=\S)\.(?=\S)", " ", stem)                           # dots between words are separators
    stem = stem.replace("\x00", ".")
    stem = re.sub(r"(?<![A-Za-z0-9])[Hh] ?26([45])(?!\d)", r"h26\1", stem)     # "H 264" / "H.264"
    stem = re.sub(r"\s+", " ", stem).strip(" -.")
    while True:                                                           # "CineCloud - CINEFREAK TOP - Title"
        head, sep, rest = stem.partition(" - ")
        compact = re.sub(r"[^a-z0-9]", "", head.lower())
        if sep and rest.strip() and any(compact.startswith(w) for w in SITE_WORDS):
            stem = rest.strip()
        else:
            return stem


def _drop_year(text):
    return re.sub(r"\s*\((?:19|20)\d{2}\)", "", text).strip(" -.") if not SHOW_YEAR else text


def _cut_tags(tokens):
    """Tokens before the first technical tag (a name that starts with a tag is not cut)."""
    for i, t in enumerate(tokens):
        if i > 0 and _is_tag(t):
            return tokens[:i]
    return tokens


def clean_title(stem, show_override=None):
    """Clean display title for a file name WITHOUT extension (or a folder name).
    show_override replaces the show part of an episode name (renamed stack)."""
    text = _normalise(stem)
    if not text:
        return stem
    for pat in _EP_PATTERNS:
        m = pat.search(text)
        if m:
            show = show_override or " ".join(_cut_tags(text[:m.start()].split())).strip(" -.")
            code = f"S{int(m.group(1)):02d}E{int(m.group(2)):02d}"
            tail = ""
            if KEEP_EPISODE_TITLE:
                tail = " ".join(_cut_tags(["x"] + text[m.end():].split())[1:]).strip(" -.")
            if not show_override:
                show = _drop_year(show)
                if not SHOW_YEAR:
                    show = strip_show_year(show)               # "Hijack 2023 S02E03" -> "Hijack S02E03"
            return " ".join(p for p in (show, code, tail) if p)
    tokens = text.split()
    cut = _cut_tags(tokens)
    # A year counts only when it is in brackets or release tags follow it ("IMG_2034" is not a movie).
    years = [i for i, t in enumerate(cut) if i > 0 and _YEAR.match(t) and (t[0] == "(" or len(cut) < len(tokens))]
    if years:                                              # movie: title and year, the rest is release noise
        y = years[-1]
        title = " ".join(cut[:y]).strip(" -.")
        if title:
            return f"{title} ({cut[y].strip('()')})" if SHOW_YEAR else title
        return text
    out = _drop_year(" ".join(cut).strip(" -."))
    return out or text


def auto_name(path):
    """The automatic name: cleaned file name, renamed show and hidden pieces applied, no per-file name."""
    base = os.path.basename(path)
    hit = _DISPLAY_CACHE.get(base)
    if hit is None:
        stem, ext = os.path.splitext(base)
        if ext.lower() not in VIDEO_EXT and ext.lower() not in SUB_EXT:
            stem = base                                    # not a known video extension: keep the whole name
        show = None
        if _SHOW_NAMES:
            info = parse_episode(base)
            show = info and (_SHOW_NAMES.get(info["key"]) or _SHOW_NAMES.get(info["rawkey"]))
        hit = _DISPLAY_CACHE[base] = _apply_rules(clean_title(stem, show))
        if len(_DISPLAY_CACHE) > 4000:
            _DISPLAY_CACHE.clear()
    return hit


def display_name(path):
    """Name shown for a video in the UI. Display only: never used as an id or history key."""
    if _CUSTOM_NAMES:
        c = _CUSTOM_NAMES.get(_name_key(path))
        if c and _file_size(path) in (c["size"], None):    # a replaced file (other size) loses the name
            return c["name"]
    return auto_name(path)


def fmt_short(ms):
    s = max(0, int(ms // 1000))
    h, m, sec = s // 3600, s % 3600 // 60, s % 60
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


_EP_PATTERNS = (
    # S01E06, s1e1, S01 E06
    re.compile(r"(?<![A-Za-z0-9])[Ss](\d{1,2}) ?[Ee](\d{1,3})(?!\d)"),
    # Season 1 Episode 2
    re.compile(r"(?<![A-Za-z0-9])season ?(\d{1,2})[ ,-]*episode ?(\d{1,3})(?!\d)", re.I),
    # 1x03 (never 1920x1080 or 16x9: the episode needs 2-3 digits and no digit may come before)
    re.compile(r"(?<![A-Za-z0-9])(\d{1,2})x(\d{2,3})(?![A-Za-z0-9])"),
)


def strip_show_year(show):
    """'Hijack 2023' -> 'Hijack'. A show that is only a year keeps it. Used for grouping and the stack title."""
    base = re.sub(r"\s+(?:19|20)\d{2}$", "", show or "").strip()
    return base or (show or "")


def parse_episode(filename):
    """Season/episode info from a file name, or None when it does not look like a TV episode.
    Returns {"show", "key", "season", "episode"}. `key` is the show name without punctuation/case."""
    stem = re.sub(r"[._]+", " ", os.path.splitext(os.path.basename(filename))[0])
    for pat in _EP_PATTERNS:
        m = pat.search(stem)
        if m:
            show = re.sub(r"[\s\-\[\(]+$", "", stem[:m.start()])
            show = re.sub(r"\s+", " ", show).strip()
            base = strip_show_year(show)
            key = re.sub(r"[^a-z0-9]", "", base.lower())
            return {"show": show, "key": key, "rawkey": re.sub(r"[^a-z0-9]", "", show.lower()),
                    "season": int(m.group(1)), "episode": int(m.group(2)),
                    "gkey": f"{key}|s{int(m.group(1)):03d}"}
    return None


def folder_title(name):
    """Folder name for Home headings and subfolder cards: the same naming rule as videos (clean_title), but a season
    tag such as S02 or Season 2 is kept, so 'Hijack.2023.S02.1080p' shows 'Hijack S02'."""
    t = clean_title(name) or name
    if re.search(r"(?<![A-Za-z0-9])(S\d{1,2}|Season \d{1,2})(?![A-Za-z0-9])", t, re.I):
        return t
    m = re.search(r"(?<![A-Za-z0-9])S(\d{1,2})(?![A-Za-z0-9])", re.sub(r"[._]+", " ", name), re.I)
    if m:
        return f"{t} S{int(m.group(1)):02d}"
    return t


def slot_fill_crop(vw, vh, rect=(0.0, 0.0, 1.0, 1.0), aspect=16 / 9):
    """VLC crop geometry as pixel margins "LEFT+TOP+RIGHT+BOTTOM" that makes the picture fill the 16:9 Home slot with no bars
    on any side: start from `rect` (fractions l, t, r, b of the frame, bars already cut), then cut the long side from the middle.
    Real libVLC 3.0 (tested): this margin format fills the slot for 16:9, 2.39:1, 4:3, vertical and baked-bar files. The
    "WxH+X+Y" format left side bars on wide and 4:3 files and crashed libVLC (floating point exception) on a vertical file;
    forcing the display aspect on top of a crop made it worse (61 % of the slot width on a 2.39:1 file)."""
    l, t, r, b = rect
    x0, y0 = l * vw, t * vh
    w, h = max(2.0, (r - l) * vw), max(2.0, (b - t) * vh)
    if w / h > aspect:                       # too wide: cut left and right
        nw = h * aspect
        x0 += (w - nw) / 2.0
        w = nw
    else:                                    # too tall: cut top and bottom
        nh = w / aspect
        y0 += (h - nh) / 2.0
        h = nh
    cw, ch = int(round(w)) // 2 * 2, int(round(h)) // 2 * 2
    cx, cy = int(round(x0)) // 2 * 2, int(round(y0)) // 2 * 2
    cx, cy = max(0, min(cx, vw - cw)), max(0, min(cy, vh - ch))
    return f"{cx}+{cy}+{max(0, vw - cx - cw)}+{max(0, vh - cy - ch)}"


def natural_key(s):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def ring_gradient(cx, cy, r):
    """Play ring colours measured on the Media Player ring: a linear gradient along the axis that points
    35 degrees below the horizontal. Orange up to 0.5 r against that axis, purple (#6f2e84) at 1.0 r."""
    ax, ay = math.cos(math.radians(35)), math.sin(math.radians(35))
    grad = QLinearGradient(cx - ax * r * 0.5, cy - ay * r * 0.5, cx + ax * r, cy + ay * r)
    for pos, rgb in ((0.0, (237, 117, 37)), (0.16, (225, 90, 96)), (0.273, (216, 78, 112)),
                     (0.507, (191, 56, 134)), (0.713, (162, 46, 140)), (0.88, (133, 45, 137)),
                     (1.0, (111, 46, 132))):
        grad.setColorAt(pos, QColor(*rgb))
    return grad


def _inset_tri(pts, d):
    """Triangle pts moved inwards by d (so a round-joined pen of width 2 d gives rounded corners of radius d)."""
    n = len(pts)
    out = []
    area = sum(pts[i][0] * pts[(i + 1) % n][1] - pts[(i + 1) % n][0] * pts[i][1] for i in range(n)) / 2.0
    sgn = 1.0 if area > 0 else -1.0
    lines = []
    for i in range(n):
        (x1, y1), (x2, y2) = pts[i], pts[(i + 1) % n]
        ex, ey = x2 - x1, y2 - y1
        ln = math.hypot(ex, ey)
        nx, ny = -ey / ln * sgn, ex / ln * sgn            # inward normal
        lines.append((x1 + nx * d, y1 + ny * d, ex, ey))
    for i in range(n):
        x1, y1, ex1, ey1 = lines[i - 1]
        x2, y2, ex2, ey2 = lines[i]
        den = ex1 * ey2 - ey1 * ex2
        t = ((x2 - x1) * ey2 - (y2 - y1) * ex2) / den
        out.append((x1 + ex1 * t, y1 + ey1 * t))
    return out


def _render_icon(px, small):
    """Draw the app icon: a round glowing disc (no box), designed on a 256 grid."""
    pm = QPixmap(px, px)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    p.scale(px / 256.0, px / 256.0)
    disc = QPainterPath()
    disc.addEllipse(QRectF(5, 5, 246, 246))
    bg = QRadialGradient(QPointF(128, 96), 160)
    bg.setColorAt(0, QColor("#33333f"))
    bg.setColorAt(1, QColor("#09090c"))
    p.fillPath(disc, bg)
    p.setClipPath(disc)
    if not small:
        glow = QRadialGradient(QPointF(128, 128), 120)
        glow.setColorAt(0.45, QColor(138, 60, 255, 80))
        glow.setColorAt(1.0, QColor(138, 60, 255, 0))
        p.fillRect(QRectF(5, 5, 246, 246), glow)
    w = 30 if small else 22                      # ring thickness
    ring = QConicalGradient(128, 128, -70)
    ring.setColorAt(0.00, QColor("#ff9a1f"))
    ring.setColorAt(0.30, QColor("#ff3d9a"))
    ring.setColorAt(0.60, QColor("#8a3cff"))
    ring.setColorAt(0.82, QColor("#2ad4ff"))
    ring.setColorAt(1.00, QColor("#ff9a1f"))
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(QBrush(ring), w))
    p.drawEllipse(QRectF(5 + w / 2, 5 + w / 2, 246 - w, 246 - w))
    if small:
        tri = [QPointF(102, 82), QPointF(102, 174), QPointF(182, 128)]
        pen_w = 14
    else:
        tri = [QPointF(104, 88), QPointF(104, 168), QPointF(174, 128)]
        pen_w = 12
    p.setPen(QPen(QColor(255, 255, 255, 245), pen_w, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.setBrush(QColor(255, 255, 255, 245))
    p.drawPolygon(QPolygonF(tri))
    if not small:
        hl = QLinearGradient(0, 5, 0, 128)
        hl.setColorAt(0, QColor(255, 255, 255, 46))
        hl.setColorAt(1, QColor(255, 255, 255, 0))
        p.fillRect(QRectF(5, 5, 246, 123), hl)
    p.end()
    return pm


def draw_app_icon(size):
    """Render supersampled (4x) and scale down, so edges stay crisp at every size."""
    big = _render_icon(max(size * 4, 1024) if size >= 128 else size * 8, size <= 40)
    return big.scaled(size, size, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)


def make_app_icon():
    icon = QIcon()
    for s in (16, 20, 24, 32, 40, 48, 64, 96, 128, 256):
        icon.addPixmap(draw_app_icon(s))
    return icon


# --------------------------------------------------------------------------
# Windows helpers
# --------------------------------------------------------------------------
def style_titlebar(win):
    """Dark title bar that matches the app (Windows 10 1809+/11)."""
    if sys.platform != "win32":
        return
    try:
        hwnd = ctypes.c_void_p(int(win.winId()))
        dwm = ctypes.windll.dwmapi

        def attr(a, value):
            v = ctypes.c_int(value)
            dwm.DwmSetWindowAttribute(hwnd, a, ctypes.byref(v), ctypes.sizeof(v))

        attr(20, 1)            # immersive dark mode
        attr(35, 0x001C1C1C)   # caption colour (BGR)
        attr(36, 0x00FFFFFF)   # caption text colour
    except Exception:
        pass


def set_topmost(win, on):
    if sys.platform == "win32":
        try:
            ctypes.windll.user32.SetWindowPos(ctypes.c_void_p(int(win.winId())),
                                              ctypes.c_void_p(-1 if on else -2),
                                              0, 0, 0, 0, 0x0013)
            return
        except Exception:
            pass
    win.setWindowFlag(Qt.WindowStaysOnTopHint, on)
    win.show()


def keep_awake(on):
    if sys.platform == "win32":
        try:
            flags = 0x80000000 | (0x00000003 if on else 0)
            ctypes.windll.kernel32.SetThreadExecutionState(ctypes.c_uint(flags))
        except Exception:
            pass


# --------------------------------------------------------------------------
# Persistent state (settings + the last few videos)
# --------------------------------------------------------------------------
SUB_SIZES = (("Smaller", "20"), ("Small", "18"), ("Normal", "16"), ("Large", "12"), ("Larger", "6"))
AUDIO_OUTPUTS = (("Automatic", ""), ("Windows Audio (WASAPI)", "mmdevice"), ("DirectSound", "directsound"),
                 ("WaveOut", "waveout"))
STEP_OPTS = tuple((f"{s} seconds", s) for s in (5, 10, 15, 20, 30))
HIDE_OPTS = tuple((f"{s} seconds", s) for s in (2, 3, 5, 10))
BOOST_OPTS = (("100%", 100), ("200%", 200))
FOLDER_OPTS = (("Episodes only", "episodes"), ("All videos in the folder", "all"), ("Never", "never"))
SETTING_OPTIONS = {"rewind": STEP_OPTS, "ff": STEP_OPTS, "hide_s": HIDE_OPTS, "boost": BOOST_OPTS,
                   "subsize": SUB_SIZES, "aout": AUDIO_OUTPUTS, "folderq": FOLDER_OPTS}
APP_VERSION = "1.0.0"
SKIP_SECONDS = {"back": 10, "fwd": 30}          # Rewind / Fast forward steps (Settings), shown in the icons


HOME_SORT_LABELS = {"name": "Name", "date": "Date modified"}
HOME_SORT_TEXT = {("name", "asc"): "A to Z", ("name", "desc"): "Z to A",
                  ("date", "desc"): "Newest first", ("date", "asc"): "Oldest first"}


def next_home_sort(cur, key):
    """Pick an item of the sort menu. The same item again flips the direction; the other item starts at its
    first-click direction (Name: A to Z, Date modified: newest first)."""
    if cur.get("key") == key:
        return {"key": key, "dir": "desc" if cur.get("dir") == "asc" else "asc"}
    return {"key": key, "dir": "asc" if key == "name" else "desc"}


class Store:
    def __init__(self):
        base = os.environ.get("APPDATA") or os.path.join(str(Path.home()), ".config")
        self.dir = Path(base) / APP_NAME
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        self.thumb_dir = self.dir / "thumbs"
        try:
            self.thumb_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        self.file = self.dir / "state.json"
        self.data = {"volume": 100, "muted": False, "shuffle": False, "repeat": 0,
                     "geometry": "", "recent": []}
        try:
            with open(self.file, "r", encoding="utf-8") as f:
                self.data.update(json.load(f))
        except (OSError, ValueError):
            pass
        self._psz = {}                       # playlist file size cache (for thumbnail names)
        self._fix_playlists()
        self._fix_names()
        self._fix_folders()
        self._fix_watched()
        self._fix_picked()
        self.clean()
        self._purge_thumbs(startup=True)

    @property
    def recent(self):
        return self.data["recent"]

    @staticmethod
    def _key(path):
        return os.path.normcase(os.path.abspath(path))

    def clean(self):
        """Drop entries whose file was moved, deleted or replaced (path + size must match)."""
        keep = []
        for e in self.recent:
            try:
                if os.path.getsize(e["path"]) == e["size"] and not e.get("finished"):
                    keep.append(e)
            except (OSError, KeyError):
                pass
        self.data["recent"] = keep

    # ------------------------------------------------ saved playlists (phase D8)
    def _fix_playlists(self):
        """Accept only well-formed playlists from state.json; the key is optional (old files have none)."""
        out = []
        raw = self.data.get("playlists")
        for pl in raw if isinstance(raw, list) else []:
            try:
                if isinstance(pl, dict) and isinstance(pl.get("name"), str) and isinstance(pl.get("items"), list):
                    now = time.time()
                    out.append({"id": str(pl.get("id") or uuid.uuid4().hex), "name": pl["name"],
                                "items": [x for x in pl["items"] if isinstance(x, str)],
                                "created": float(pl.get("created") or now), "modified": float(pl.get("modified") or now)})
            except (TypeError, ValueError):
                pass
        self.data["playlists"] = out
        if self.data.get("playlist_sort") not in ("az", "date"):
            self.data["playlist_sort"] = "az"

    # ------------------------------------------------ folders on Home (FOLDER SECTIONS phase)
    def _fix_folders(self):
        """Folders shown as sections on Home, newest first. Only well-formed entries are kept (old files have none).
        A folder that is not there now stays (a drive that is not connected); it only shows when it can be read."""
        out, seen = [], set()
        raw = self.data.get("folders")
        for f in raw if isinstance(raw, list) else []:
            try:
                if isinstance(f, dict) and isinstance(f.get("path"), str) and f["path"].strip():
                    k = self._key(f["path"])
                    if k not in seen:
                        seen.add(k)
                        rec = {"path": f["path"], "added": float(f.get("added") or time.time())}
                        hid = f.get("hidden")
                        if isinstance(hid, list) and hid:
                            rec["hidden"] = [x for x in hid if isinstance(x, str)]
                        out.append(rec)
            except (TypeError, ValueError):
                pass
        self.data["folders"] = out[:30]
        hs = self.data.get("home_sort")
        if not (isinstance(hs, dict) and hs.get("key") in ("name", "date") and hs.get("dir") in ("asc", "desc")):
            hs = {"key": "name", "dir": "asc"}
        self.data["home_sort"] = {"key": hs["key"], "dir": hs["dir"]}

    def add_folder(self, path):
        """Put a folder at the top of Home (the same folder again moves it up). Files are never touched."""
        k = self._key(path)
        rest = [f for f in self.data["folders"] if self._key(f["path"]) != k]
        self.data["folders"] = ([{"path": os.path.abspath(path), "added": time.time()}] + rest)[:30]
        self.save()

    def remove_folder(self, path):
        k = self._key(path)
        self.data["folders"] = [f for f in self.data["folders"] if self._key(f["path"]) != k]
        self.save()

    # ---------------------------------------- watched videos of saved folders (hidden on Home)
    WATCHED_MAX = 3000

    def _fix_watched(self):
        """Videos watched to the end (about 95%). Home folder sections hide them. Path + size, like the history.
        An entry whose file is gone is dropped, but only when its folder can be read (an unplugged drive keeps them)."""
        out, seen = [], set()
        raw = self.data.get("watched")
        for w in raw if isinstance(raw, list) else []:
            try:
                if isinstance(w, dict) and isinstance(w.get("path"), str) and w["path"].strip():
                    k = self._key(w["path"])
                    if k in seen:
                        continue
                    if not os.path.exists(w["path"]) and os.path.isdir(os.path.dirname(os.path.abspath(w["path"]))):
                        continue
                    seen.add(k)
                    out.append({"path": w["path"], "size": int(w.get("size") or 0)})
            except (TypeError, ValueError, OSError):
                pass
        self.data["watched"] = out[:self.WATCHED_MAX]

    def watched_map(self):
        return {self._key(w["path"]): w.get("size", 0) for w in self.data.get("watched", [])}

    def in_saved_folder(self, path):
        k = self._key(path)
        for f in self.data.get("folders", []):
            fk = self._key(f["path"])
            if k.startswith(fk if fk.endswith(os.sep) else fk + os.sep):
                return True
        return False

    def mark_watched(self, path, size):
        k = self._key(path)
        rest = [w for w in self.data.get("watched", []) if self._key(w["path"]) != k]
        self.data["watched"] = ([{"path": path, "size": int(size or 0)}] + rest)[:self.WATCHED_MAX]
        self.save()

    def unmark_watched(self, path):
        k = self._key(path)
        cur = self.data.get("watched", [])
        rest = [w for w in cur if self._key(w["path"]) != k]
        if len(rest) != len(cur):
            self.data["watched"] = rest
            self.save()

    def watched_under(self, folder):
        """Copies of the watched entries inside a folder (path prefix)."""
        fk = self._key(folder)
        pre = fk if fk.endswith(os.sep) else fk + os.sep
        return [dict(w) for w in self.data.get("watched", []) if self._key(w["path"]).startswith(pre)]

    def restore_watched(self, entries):
        have = {self._key(w["path"]) for w in self.data.get("watched", [])}
        add = [dict(w) for w in entries if self._key(w["path"]) not in have]
        if add:
            self.data["watched"] = (add + self.data.get("watched", []))[:self.WATCHED_MAX]
            self.save()

    # ---------------------------------------- PHASE B: pictures picked by the user, last played video per subfolder stack
    PICKED_MAX = 3000
    NOPIC_MAX = 1000          # videos that gave no frame (path + size): not tried again until the file changes

    def _fix_picked(self):
        out, seen = [], set()
        raw = self.data.get("picked")
        for w in raw if isinstance(raw, list) else []:
            try:
                if isinstance(w, dict) and isinstance(w.get("path"), str) and w["path"].strip():
                    k = self._key(w["path"])
                    if k in seen:
                        continue
                    if not os.path.exists(w["path"]) and os.path.isdir(os.path.dirname(os.path.abspath(w["path"]))):
                        continue
                    seen.add(k)
                    out.append({"path": w["path"], "size": int(w.get("size") or 0)})
            except (TypeError, ValueError, OSError):
                pass
        self.data["picked"] = out[:self.PICKED_MAX]
        npc = self.data.get("nopic")
        self.data["nopic"] = [w for w in npc if isinstance(w, dict) and isinstance(w.get("path"), str)
                              and isinstance(w.get("size"), int)][:self.NOPIC_MAX] if isinstance(npc, list) else []
        lp = self.data.get("lastplayed")
        self.data["lastplayed"] = {k: v for k, v in lp.items() if isinstance(k, str) and isinstance(v, str)} \
            if isinstance(lp, dict) else {}

    def is_picked(self, path, size):
        k = self._key(path)
        return any(self._key(w["path"]) == k and w.get("size") == int(size or 0) for w in self.data.get("picked", []))

    def set_picked(self, path, size):
        k = self._key(path)
        rest = [w for w in self.data.get("picked", []) if self._key(w["path"]) != k]
        self.data["picked"] = ([{"path": path, "size": int(size or 0)}] + rest)[:self.PICKED_MAX]
        self.save()

    def clear_picked(self, path):
        k = self._key(path)
        cur = self.data.get("picked", [])
        rest = [w for w in cur if self._key(w["path"]) != k]
        if len(rest) != len(cur):
            self.data["picked"] = rest
            self.save()

    def nopic_set(self):
        return {(w["path"], w["size"]) for w in self.data.get("nopic", [])}

    def add_nopic(self, path, size):
        cur = self.data.setdefault("nopic", [])
        if not any(w["path"] == path and w["size"] == int(size) for w in cur):
            cur.insert(0, {"path": path, "size": int(size)})
            del cur[self.NOPIC_MAX:]
            self.save()

    def set_lastplayed(self, dirkey, path):
        lp = self.data.setdefault("lastplayed", {})
        if lp.get(dirkey) != path:
            lp.pop(dirkey, None)
            lp[dirkey] = path
            while len(lp) > 500:
                lp.pop(next(iter(lp)))
            self.save()

    def lastplayed(self, dirkey):
        return self.data.get("lastplayed", {}).get(dirkey)

    # hidden cards of a saved folder (Remove from Home on a card). Files are never touched.
    def _folder_rec(self, path):
        k = self._key(path)
        return next((f for f in self.data["folders"] if self._key(f["path"]) == k), None)

    def folder_hidden(self, path):
        f = self._folder_rec(path)
        return set(f.get("hidden") or []) if f else set()

    def hide_in_folder(self, path, keys):
        """Hide cards (video keys or "dir:<folder key>") in one saved folder. Returns the keys that were new."""
        f = self._folder_rec(path)
        if not f:
            return []
        hid = f.setdefault("hidden", [])
        added = [k for k in dict.fromkeys(keys) if k not in hid]
        hid.extend(added)
        if added:
            self.save()
        return added

    def unhide_in_folder(self, path, keys):
        f = self._folder_rec(path)
        if not f or not f.get("hidden"):
            return
        f["hidden"] = [k for k in f["hidden"] if k not in set(keys)]
        if not f["hidden"]:
            del f["hidden"]
        self.save()

    def folder_snapshot(self, path):
        """(index, copy) of a saved folder, so it can be put back (Ctrl+Z)."""
        k = self._key(path)
        for i, f in enumerate(self.data["folders"]):
            if self._key(f["path"]) == k:
                return i, {**f, **({"hidden": list(f["hidden"])} if f.get("hidden") else {})}
        return None

    def restore_folder(self, snap):
        i, f = snap
        if self._folder_rec(f["path"]):
            return
        self.data["folders"].insert(min(i, len(self.data["folders"])), f)
        self.data["folders"] = self.data["folders"][:30]
        self.save()

    def restore_recent(self, entries):
        """Put removed history entries back (progress included), newest first like touch() keeps them."""
        have = {self._key(e["path"]) for e in self.recent}
        add = [dict(e) for e in entries if self._key(e["path"]) not in have]
        if add:
            self.data["recent"] = sorted(self.recent + add, key=lambda e: -float(e.get("updated") or 0))
            self.save()

    keep_thumbs = frozenset()       # thumbnails of removed cards that Ctrl+Z can still bring back
    folder_thumbs = frozenset()     # PHASE B: pictures of the videos shown in the saved folders (set by the app)
    folders_ok = False              # PHASE B: True once Home was built and every saved folder could be read

    # ------------------------------------------------ names (Rename on Home cards)
    def _fix_names(self):
        """Accept only well-formed names and rules from state.json (old files have none). A file that now has
        another size is a different file: its name is dropped."""
        names = []
        raw = self.data.get("names")
        for n in raw if isinstance(raw, list) else []:
            try:
                if (isinstance(n, dict) and isinstance(n.get("path"), str) and isinstance(n.get("name"), str)
                        and n["name"].strip() and isinstance(n.get("size"), int)):
                    try:
                        if os.path.getsize(n["path"]) != n["size"]:
                            continue
                    except OSError:
                        pass                                  # file not there now (a removed drive): keep the name
                    names.append({"path": n["path"], "size": n["size"], "name": n["name"].strip()[:120]})
            except (TypeError, KeyError):
                pass
        self.data["names"] = names[-500:]
        sh = self.data.get("show_names")
        self.data["show_names"] = {k: v.strip()[:120] for k, v in (sh if isinstance(sh, dict) else {}).items()
                                   if isinstance(k, str) and isinstance(v, str) and v.strip()}
        rules, raw = [], self.data.get("name_rules")
        for r in raw if isinstance(raw, list) else []:
            if (isinstance(r, dict) and r.get("side") in ("start", "end") and isinstance(r.get("text"), str)
                    and r["text"].strip() and r not in rules):
                rules.append({"side": r["side"], "text": r["text"].strip()})
        self.data["name_rules"] = rules
        self._publish_names()

    def _publish_names(self):
        set_name_overrides({_name_key(n["path"]): n for n in self.data["names"]},
                           dict(self.data["show_names"]), [dict(r) for r in self.data["name_rules"]])

    def set_custom_name(self, path, name):
        """Name for one file (display only). Empty or equal to the automatic name: the file uses the automatic one."""
        k = _name_key(path)
        self.data["names"] = [n for n in self.data["names"] if _name_key(n["path"]) != k]
        name = " ".join(name.split())[:120]
        try:
            size = os.path.getsize(path)
        except OSError:
            size = None
        self._publish_names()
        if name and size is not None and name != auto_name(path):
            self.data["names"].append({"path": path, "size": size, "name": name})
            self.data["names"] = self.data["names"][-500:]
        self._publish_names()
        self.save()

    def set_show_name(self, key, name):
        """New show name for a whole show (renamed stack). Empty: back to the automatic show name."""
        name = " ".join(name.split())[:120]
        if name:
            self.data["show_names"][key] = name
        else:
            self.data["show_names"].pop(key, None)
        self._publish_names()
        self.save()

    def add_name_rule(self, side, text):
        r = {"side": side, "text": " ".join(text.split())}
        if side in ("start", "end") and piece_is_safe(r["text"]) and r not in self.data["name_rules"]:
            self.data["name_rules"].append(r)
            self._publish_names()
            self.save()
            return True
        return False

    def remove_name_rule(self, i):
        if 0 <= i < len(self.data["name_rules"]):
            del self.data["name_rules"][i]
            self._publish_names()
            self.save()

    @property
    def playlists(self):
        return self.data["playlists"]

    def playlist(self, pid):
        return next((p for p in self.playlists if p["id"] == pid), None)

    def playlists_sorted(self):
        if self.data.get("playlist_sort") == "date":
            return sorted(self.playlists, key=lambda p: -p["modified"])
        return sorted(self.playlists, key=lambda p: p["name"].casefold())

    def unique_playlist_name(self, base, skip_id=None):
        base = " ".join((base or "").split()) or "Untitled playlist"
        have = {p["name"].casefold() for p in self.playlists if p["id"] != skip_id}
        if base.casefold() not in have:
            return base
        n = 2
        while f"{base} ({n})".casefold() in have:
            n += 1
        return f"{base} ({n})"

    def new_playlist(self, name, paths=()):
        now = time.time()
        pl = {"id": uuid.uuid4().hex, "name": self.unique_playlist_name(name), "items": [],
              "created": now, "modified": now}
        self.playlists.append(pl)
        self.add_to_playlist(pl["id"], paths)
        return pl

    def rename_playlist(self, pid, name):
        pl = self.playlist(pid)
        if pl:
            pl["name"] = self.unique_playlist_name(name, skip_id=pid)
            pl["modified"] = time.time()
        return pl

    def delete_playlist(self, pid):
        self.data["playlists"] = [p for p in self.playlists if p["id"] != pid]

    def add_to_playlist(self, pid, paths):
        """Append videos that are not in the playlist yet. Returns how many were added."""
        pl = self.playlist(pid)
        if not pl:
            return 0
        have = {self._key(p) for p in pl["items"]}
        n = 0
        for p in paths:
            k = self._key(p)
            if k not in have:
                have.add(k)
                pl["items"].append(os.path.abspath(p))
                self._psz.pop(k, None)
                n += 1
        if n:
            pl["modified"] = time.time()
        return n

    def remove_from_playlist(self, pid, paths):
        pl = self.playlist(pid)
        if pl:
            drop = {self._key(p) for p in paths}
            before = len(pl["items"])
            pl["items"] = [p for p in pl["items"] if self._key(p) not in drop]
            if len(pl["items"]) != before:
                pl["modified"] = time.time()

    def set_playlist_order(self, pid, paths):
        pl = self.playlist(pid)
        if pl and {self._key(p) for p in paths} == {self._key(p) for p in pl["items"]}:
            pl["items"] = list(paths)
            pl["modified"] = time.time()

    @staticmethod
    def _gone(path):
        """True only when the folder can be reached but the file is no longer in it. A missing
        folder (drive unplugged, network share down) keeps the item."""
        if os.path.isfile(path):
            return False
        return os.path.isdir(os.path.dirname(os.path.abspath(path)))

    def prune_playlists(self, pid=None):
        """Remove playlist items whose file was deleted or moved. Returns how many were removed."""
        n = 0
        for pl in self.playlists:
            if pid and pl["id"] != pid:
                continue
            keep = [p for p in pl["items"] if not self._gone(p)]
            if len(keep) != len(pl["items"]):
                n += len(pl["items"]) - len(keep)
                pl["items"] = keep
        return n

    def _playlist_thumb_names(self):
        names = set()
        for pl in self.playlists:
            for p in pl["items"]:
                k = self._key(p)
                if k not in self._psz:
                    try:
                        self._psz[k] = os.path.getsize(p)
                    except OSError:
                        self._psz[k] = None
                if self._psz[k] is not None:
                    names.add(self.thumb_path(p, self._psz[k]).name)
        return names

    def playlist_thumbs(self, pl, n=4):
        """Cached thumbnail file (or None) for each of the first `n` videos."""
        out = []
        for p in pl["items"][:n]:
            try:
                f = self.thumb_path(p, os.path.getsize(p))
                out.append(f if f.exists() else None)
            except OSError:
                out.append(None)
        return out

    def thumb_path(self, path, size):
        h = hashlib.sha1(f"{self._key(path)}|{size}".encode("utf-8", "replace")).hexdigest()[:20]
        return self.thumb_dir / f"{h}.jpg"

    protected = frozenset()          # thumbnail files of the loaded playlist: never cleaned up

    def _purge_thumbs(self, startup=False):
        valid = ({self.thumb_path(e["path"], e["size"]).name for e in self.recent} | set(self.protected)
                 | set(self.keep_thumbs) | set(self.folder_thumbs) | self._playlist_thumb_names())
        try:
            for f in self.thumb_dir.glob("*.crop"):          # PHASE B: crop note of a picture that is gone
                if f.with_suffix(".jpg").name not in valid and self.folders_ok:
                    f.unlink()
            for f in self.thumb_dir.glob("*.jpg"):
                if f.name.endswith(".new.jpg"):
                    if startup:
                        f.unlink()
                elif f.name not in valid and self.folders_ok:    # PHASE B: not before Home is built, not with a drive missing
                    f.unlink()
        except OSError:
            pass

    def remove(self, path):
        key = self._key(path)
        self.data["recent"] = [e for e in self.recent if self._key(e["path"]) != key]
        self.save()

    def find(self, path, size):
        key = self._key(path)
        for e in self.recent:
            if self._key(e["path"]) == key and e.get("size") == size:
                return e
        return None

    def touch(self, path, size, **fields):
        key = self._key(path)
        entry, rest = None, []
        for e in self.recent:
            if self._key(e["path"]) == key:
                entry = e
            else:
                rest.append(e)
        if entry is None or entry.get("size") != size:
            entry = {"path": path, "size": size, "position": 0, "duration": 0,
                     "finished": False, "audio": None, "spu": None}
        entry.update(fields)
        entry["updated"] = time.time()
        self.data["recent"] = [entry] + rest
        return entry

    def save(self):
        self._purge_thumbs()
        try:
            tmp = self.file.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=1)
            os.replace(tmp, self.file)
        except OSError:
            pass


# --------------------------------------------------------------------------
# Small widgets
# --------------------------------------------------------------------------
def lerp(a, b, t):
    return a + (b - a) * t


def mix(c1, c2, t):
    t = max(0.0, min(1.0, t))
    return QColor(int(lerp(c1.red(), c2.red(), t)), int(lerp(c1.green(), c2.green(), t)),
                  int(lerp(c1.blue(), c2.blue(), t)), int(lerp(c1.alpha(), c2.alpha(), t)))


def with_alpha(c, a):
    c = QColor(c)
    c.setAlpha(int(max(0, min(255, c.alpha() * a))))
    return c


def _ease(kind=QEasingCurve.OutCubic, overshoot=None):
    c = QEasingCurve(kind)
    if overshoot is not None:
        c.setOvershoot(overshoot)
    return c


EASE_OUT = _ease()
EASE_SPRING = _ease(QEasingCurve.OutBack, 2.4)      # soft "pop" when a button is released
EASE_SOFT = _ease(QEasingCurve.OutBack, 1.1)
EASE_INOUT = QEasingCurve(QEasingCurve.InOutCubic)    # bars: slow start, smooth middle, soft landing
BAR_FADE_OUT, BAR_FADE_IN, BAR_SLIDE = 650, 280, 14   # ms, ms, px the bars drift while they fade


class Tween:
    """Animates a float toward a target; used for every hover/press/morph effect."""

    def __init__(self, owner, value=0.0, ms=160, on_change=None):
        self.value = float(value)
        self._cb = on_change
        self._target = float(value)
        self.anim = QVariantAnimation(owner)
        self.anim.setDuration(ms)
        self.anim.setEasingCurve(EASE_OUT)
        self.anim.valueChanged.connect(self._set)

    def _set(self, v):
        self.value = float(v)
        if self._cb:
            self._cb()

    def set(self, v):
        self.value = self._target = float(v)
        try:
            self.anim.stop()
            if self._cb:
                self._cb()
        except RuntimeError:             # the widget that owned this animation is already gone
            pass

    def to(self, target, ms=None, curve=None):
        try:
            self._to(float(target), ms, curve)
        except RuntimeError:             # the widget that owned this animation is already gone
            pass

    def _to(self, target, ms, curve):
        running = self.anim.state() == QVariantAnimation.Running
        if running and abs(target - self._target) < 1e-6:
            return
        if not running and abs(target - self.value) < 1e-3:
            self._target = target
            return
        self._target = target
        self.anim.blockSignals(True)       # changing start/end of a finished animation makes Qt emit a stray
        try:                               # value (the old end): that made bars blink at the start of a fade
            self.anim.stop()
            if ms is not None:
                self.anim.setDuration(ms)
            self.anim.setEasingCurve(curve or EASE_OUT)
            self.anim.setStartValue(self.value)
            self.anim.setEndValue(target)
        finally:
            self.anim.blockSignals(False)
        self.anim.start()


# --------------------------------------------------------------------------
# Icons: drawn as vectors in a 24x24 box, thin rounded strokes, no icon fonts
# --------------------------------------------------------------------------
ALT_ON = {"pause", "mute", "restore", "repeat_one"}


def _poly(pts):
    return QPolygonF([QPointF(x, y) for x, y in pts])


def _lerp_pts(a, b, t):
    return [(lerp(x1, x2, t), lerp(y1, y2, t)) for (x1, y1), (x2, y2) in zip(a, b)]


_PLAY_L = [(-5.7, -7.2), (-0.8, -4.3), (-0.8, 4.3), (-5.7, 7.2)]
_PLAY_R = [(-0.8, -4.3), (6.4, 0.0), (6.4, 0.0), (-0.8, 4.3)]
_PAUSE_L = [(-5.6, -7.2), (-1.9, -7.2), (-1.9, 7.2), (-5.6, 7.2)]
_PAUSE_R = [(1.9, -7.2), (5.6, -7.2), (5.6, 7.2), (1.9, 7.2)]


def _chevron(p, ex, ey, dx, dy, size=3.5):
    """Open arrow head ('>' shape) at (ex, ey) pointing along the unit vector (dx, dy)."""
    tx, ty = ex + dx * 1.2, ey + dy * 1.2
    bx, by = tx - dx * size, ty - dy * size
    nx, ny = -dy, dx
    p.drawPolyline(_poly([(bx + nx * size, by + ny * size), (tx, ty), (bx - nx * size, by - ny * size)]))


def paint_icon(p, kind, cx, cy, px, color, alt=0.0, waves=2, slash=False, bg=None, sw=1.0):
    """Draw icon `kind` centred on (cx, cy), `px` pixels tall. `alt` morphs paired icons."""
    p.save()
    p.translate(cx, cy)
    s = px / 24.0
    p.scale(s, s)
    # sw scales the stroke weight (control bar icons use 0.78: measured 1.55 px against 1.99 px at 125 %)
    stroke = QPen(color, 1.9 * sw, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    fill_pen = QPen(color, 1.5 * sw, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    p.setPen(stroke)
    p.setBrush(Qt.NoBrush)

    if kind in ("play", "pause"):
        p.setPen(fill_pen)
        p.setBrush(color)
        p.drawPolygon(_poly(_lerp_pts(_PLAY_L, _PAUSE_L, alt)))
        p.drawPolygon(_poly(_lerp_pts(_PLAY_R, _PAUSE_R, alt)))

    elif kind in ("prev", "next"):
        if kind == "next":
            p.scale(-1, 1)
        p.setPen(QPen(color, 2.2 * sw, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(color)
        p.drawPolygon(_poly([(8.2, -7.4), (-1.4, 0), (8.2, 7.4)]))
        p.drawLine(QPointF(-7.6, -7.7), QPointF(-7.6, 7.7))

    elif kind in ("back10", "fwd30"):
        # measured on the Media Player icon (125 %): circle radius 8.8 px, arc from 17 to 159 degrees,
        # right-angle arrow head with 5 px arms, number about 10 px tall, semibold
        fwd = kind == "fwd30"
        p.save()
        if fwd:
            p.scale(-1, 1)
        r, ccx, ccy = 7.04, 0.6, -0.64
        p.drawArc(QRectF(ccx - r, ccy - r, 2 * r, 2 * r), 17 * 16, 142 * 16)
        hx, hy = ccx + r * math.cos(math.radians(159)), ccy - r * math.sin(math.radians(159))
        p.setPen(QPen(color, 1.9 * sw, Qt.SolidLine, Qt.SquareCap, Qt.MiterJoin))
        p.drawPolyline(_poly([(hx + 4.0, hy), (hx, hy), (hx, hy - 4.0)]))
        p.restore()
        p.setPen(color)
        p.setFont(ui_font(10, QFont.DemiBold))
        p.drawText(QRectF(-8, 3.6 - 5.0, 16, 10), Qt.AlignCenter, str(SKIP_SECONDS["fwd" if fwd else "back"]))

    elif kind == "shuffle":
        path = QPainterPath()
        path.moveTo(-9, -5)
        path.lineTo(-5.5, -5)
        path.cubicTo(0, -5, 0, 5, 5.5, 5)
        path.lineTo(8, 5)
        p.drawPath(path)
        path = QPainterPath()
        path.moveTo(-9, 5)
        path.lineTo(-5.5, 5)
        path.cubicTo(-3.8, 5, -2.8, 4.1, -2.0, 3.1)
        p.drawPath(path)
        path = QPainterPath()
        path.moveTo(2.0, -3.1)
        path.cubicTo(2.8, -4.1, 3.8, -5, 5.5, -5)
        path.lineTo(8, -5)
        p.drawPath(path)
        for y in (5, -5):
            p.drawPolyline(_poly([(5.8, y - 2.6), (8.4, y), (5.8, y + 2.6)]))

    elif kind in ("repeat_all", "repeat_one"):
        r = 7.2
        ang = math.radians(30)
        for _ in range(2):
            p.drawArc(QRectF(-r, -r, 2 * r, 2 * r), 165 * 16, -135 * 16)
            _chevron(p, r * math.cos(ang), -r * math.sin(ang), math.sin(ang), math.cos(ang))
            p.rotate(180)
        if alt > 0.01:
            p.setFont(ui_font(9, QFont.Bold))
            p.setPen(with_alpha(color, alt))
            p.drawText(QRectF(-5, -5, 10, 10), Qt.AlignCenter, "1")

    elif kind in ("volume", "mute") and sw < 1.0:          # control bar: shape measured on the Media Player
        p.save()
        p.scale(0.96, 0.96)                      # coordinates below are Media Player pixels at 125 %
        p.translate(-4.0, 0)                     # the speaker sits 4 px left of the button centre (measured)
        sp = QPainterPath()
        sp.moveTo(-5.95, -3.0)
        sp.lineTo(-1.95, -3.0)
        sp.lineTo(1.45, -6.4)
        sp.lineTo(1.45, 6.4)
        sp.lineTo(-1.95, 3.0)
        sp.lineTo(-5.95, 3.0)
        sp.closeSubpath()
        p.drawPath(sp)
        wave = 1.0 - alt
        if wave > 0.01:
            p.setPen(QPen(with_alpha(color, wave), 1.9 * sw, Qt.SolidLine, Qt.RoundCap))
            a1 = QPainterPath()
            a1.moveTo(4.0, -3.4)
            a1.quadTo(7.6, 0, 4.0, 3.4)
            p.drawPath(a1)
            if waves >= 2:
                a2 = QPainterPath()
                a2.moveTo(6.4, -6.2)
                a2.quadTo(11.4, 0, 6.4, 6.2)
                p.drawPath(a2)
        if alt > 0.01:
            p.setPen(QPen(with_alpha(color, alt), 1.9 * sw, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(3.6, -3.4), QPointF(9.0, 3.4))
            p.drawLine(QPointF(3.6, 3.4), QPointF(9.0, -3.4))
        p.restore()

    elif kind in ("volume", "mute"):
        sp = QPainterPath()
        sp.moveTo(-8.6, -2.9)
        sp.lineTo(-5.2, -2.9)
        sp.lineTo(-0.2, -6.9)
        sp.lineTo(-0.2, 6.9)
        sp.lineTo(-5.2, 2.9)
        sp.lineTo(-8.6, 2.9)
        sp.closeSubpath()
        p.drawPath(sp)
        wave = 1.0 - alt
        if wave > 0.01:
            p.setPen(QPen(with_alpha(color, wave), 1.9, Qt.SolidLine, Qt.RoundCap))
            a1 = QPainterPath()
            a1.moveTo(3.6, -3.2)
            a1.quadTo(6.2, 0, 3.6, 3.2)
            p.drawPath(a1)
            if waves >= 2:
                a2 = QPainterPath()
                a2.moveTo(6.9, -6.2)
                a2.quadTo(11.4, 0, 6.9, 6.2)
                p.drawPath(a2)
        if alt > 0.01:
            p.setPen(QPen(with_alpha(color, alt), 1.9, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(3.8, -3.2), QPointF(9.6, 3.2))
            p.drawLine(QPointF(3.8, 3.2), QPointF(9.6, -3.2))

    elif kind in ("fullscreen", "restore"):
        p.drawLine(QPointF(-7.0, 7.0), QPointF(7.0, -7.0))
        head = _lerp_pts([(0.6, -7.8), (7.8, -7.8), (7.8, -0.6)],
                         [(1.8, -6.6), (1.8, -1.8), (6.6, -1.8)], alt)
        p.drawPolyline(_poly(head))
        p.rotate(180)
        p.drawPolyline(_poly(head))
        p.rotate(180)

    elif kind == "cc":
        p.drawRoundedRect(QRectF(-9.4, -6.8, 18.8, 13.6), 3.8, 3.8)
        p.setPen(QPen(color, 1.6, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(-5.6, 1.0), QPointF(-2.2, 1.0))
        p.drawLine(QPointF(0.0, 1.0), QPointF(5.6, 1.0))
        p.drawLine(QPointF(-5.6, 4.0), QPointF(-0.6, 4.0))
        p.drawLine(QPointF(2.0, 4.0), QPointF(5.6, 4.0))

    elif kind == "cc_bar":                      # subtitles icon of the control bar (measured: line rows are swapped)
        p.drawRoundedRect(QRectF(-9.0, -7.1, 18.0, 14.2), 3.6, 3.6)
        p.setPen(QPen(color, 1.5 * sw * 1.05, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(-6.4, 0.9), QPointF(1.6, 0.9))
        p.drawLine(QPointF(3.8, 0.9), QPointF(6.4, 0.9))
        p.drawLine(QPointF(-6.4, 3.7), QPointF(-2.2, 3.7))
        p.drawLine(QPointF(0.0, 3.7), QPointF(6.4, 3.7))

    elif kind == "mini_bar":                    # picture-in-picture icon (pixel dump of the Media Player icon)
        p.scale(0.96, 0.96)
        h, rr = 8.0, 2.4
        fr = QPainterPath()
        fr.moveTo(-h, -1.5)
        fr.lineTo(-h, -h + rr)
        fr.quadTo(-h, -h, -h + rr, -h)
        fr.lineTo(h - rr, -h)
        fr.quadTo(h, -h, h, -h + rr)
        fr.lineTo(h, h - rr)
        fr.quadTo(h, h, h - rr, h)
        fr.lineTo(2.0, h)
        p.drawPath(fr)
        p.drawPolyline(_poly([(0.7, -h), (0.7, -0.8), (h, -0.8)]))
        p.drawPolyline(_poly([(-h, 1.8), (0.5, 1.8), (0.5, h)]))
        p.drawLine(QPointF(0.5, 1.8), QPointF(-h, h))

    elif kind == "mini_expand":                 # back to the full window (Media Player mini view, zoomed pixel dump)
        h, rr = 8.2, 2.4
        fr = QPainterPath()
        fr.moveTo(0.9, h)
        fr.lineTo(h - rr, h)
        fr.quadTo(h, h, h, h - rr)
        fr.lineTo(h, -h + rr)
        fr.quadTo(h, -h, h - rr, -h)
        fr.lineTo(-h + rr, -h)
        fr.quadTo(-h, -h, -h, -h + rr)
        fr.lineTo(-h, h)
        fr.lineTo(-1.5, h)
        p.drawPath(fr)
        p.drawPolyline(_poly([(0.9, -h), (0.9, -2.8), (h, -2.8)]))
        p.drawLine(QPointF(-1.6, 1.8), QPointF(-h, h))

    elif kind == "more":
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        for x in (-6.5, 0, 6.5):
            p.drawEllipse(QPointF(x, 0), 1.65, 1.65)

    elif kind == "home":                        # one outline: roof, walls, doorway (like the Media Player icon)
        h = QPainterPath()
        h.moveTo(0, -8.8)
        h.lineTo(8.4, -1.6)
        h.lineTo(8.4, 6.0)
        h.quadTo(8.4, 8.2, 6.2, 8.2)
        h.lineTo(3.5, 8.2)
        h.lineTo(3.5, 2.8)
        h.quadTo(3.5, 1.2, 1.9, 1.2)
        h.lineTo(-1.9, 1.2)
        h.quadTo(-3.5, 1.2, -3.5, 2.8)
        h.lineTo(-3.5, 8.2)
        h.lineTo(-6.2, 8.2)
        h.quadTo(-8.4, 8.2, -8.4, 6.0)
        h.lineTo(-8.4, -1.6)
        h.closeSubpath()
        p.setPen(QPen(color, 1.7, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawPath(h)

    elif kind == "mini":
        tl = QPainterPath()
        tl.moveTo(-8.6, -1.0)
        tl.lineTo(-8.6, -5.6)
        tl.quadTo(-8.6, -8.4, -5.8, -8.4)
        tl.lineTo(-1.8, -8.4)
        p.drawPath(tl)
        p.drawRoundedRect(QRectF(1.8, -8.4, 6.8, 6.8), 1.8, 1.8)
        br = QPainterPath()
        br.moveTo(8.6, 2.2)
        br.lineTo(8.6, 5.6)
        br.quadTo(8.6, 8.4, 5.8, 8.4)
        br.lineTo(2.2, 8.4)
        p.drawPath(br)
        p.drawLine(QPointF(1.4, 0.0), QPointF(-6.0, 7.4))
        p.drawPolyline(_poly([(-6.6, 3.2), (-6.6, 7.4), (-2.4, 7.4)]))

    elif kind == "info":
        p.drawEllipse(QPointF(0, 0), 8.6, 8.6)
        p.drawLine(QPointF(0, -0.2), QPointF(0, 4.8))
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawEllipse(QPointF(0, -4.2), 1.2, 1.2)

    elif kind == "folder":
        path = QPainterPath()
        path.moveTo(-9, -5.6)
        path.lineTo(-3.6, -5.6)
        path.lineTo(-1.6, -3.2)
        path.lineTo(9, -3.2)
        path.lineTo(9, 6.6)
        path.lineTo(-9, 6.6)
        path.closeSubpath()
        p.drawPath(path)

    elif kind == "queue":
        p.drawLine(QPointF(-9, -5.5), QPointF(9, -5.5))
        p.drawLine(QPointF(-9, -0.2), QPointF(-1.5, -0.2))
        p.drawLine(QPointF(-9, 5.1), QPointF(-1.5, 5.1))
        p.setPen(fill_pen)
        p.setBrush(color)
        p.drawPolygon(_poly([(3, 0.8), (3, 6.4), (8.4, 3.6)]))

    elif kind == "eq":
        knob = QColor(bg) if bg is not None else QColor(BG)
        for y, x in ((-5.4, -3.5), (0, 4.2), (5.4, -0.8)):
            p.drawLine(QPointF(-9, y), QPointF(9, y))
            p.setBrush(knob)
            p.drawEllipse(QPointF(x, y), 2.4, 2.4)

    elif kind == "speed":
        p.drawArc(QRectF(-8.8, -7.4, 17.6, 17.6), -20 * 16, 220 * 16)
        p.drawLine(QPointF(0, 1.4), QPointF(4.6, -3.8))
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawEllipse(QPointF(0, 1.4), 1.4, 1.4)

    elif kind == "video":
        p.drawRoundedRect(QRectF(-9, -5, 13, 10), 2.8, 2.8)
        p.drawPolyline(_poly([(4.6, -1.8), (9, -4.8), (9, 4.8), (4.6, 1.8)]))

    elif kind == "shot":
        p.drawRoundedRect(QRectF(-9.2, -5.2, 18.4, 13.2), 3.2, 3.2)
        p.drawEllipse(QPointF(0, 1.4), 3.7, 3.7)
        p.drawPolyline(_poly([(-3.4, -5.2), (-2.2, -7.8), (2.2, -7.8), (3.4, -5.2)]))

    elif kind == "menu":
        for y in (-5.6, 0, 5.6):
            p.drawLine(QPointF(-8.4, y), QPointF(8.4, y))

    elif kind == "search":
        p.drawEllipse(QPointF(-2.2, -2.2), 6.0, 6.0)
        p.drawLine(QPointF(2.2, 2.2), QPointF(8.6, 8.6))

    elif kind == "playlists":
        p.drawRoundedRect(QRectF(-8.8, -7.8, 17.6, 15.6), 3.6, 3.6)
        p.drawEllipse(QPointF(0, 0.2), 3.4, 3.4)

    elif kind == "settings":                    # six-tooth cog with a small centre ring
        g = QPainterPath()
        pts = []
        for i in range(6):
            c = -90 + 60 * i
            for da, r in ((-23, 6.4), (-13, 9.0), (13, 9.0), (23, 6.4)):
                a = math.radians(c + da)
                pts.append((r * math.cos(a), r * math.sin(a)))
        g.moveTo(*pts[0])
        for q in pts[1:]:
            g.lineTo(*q)
        g.closeSubpath()
        p.setPen(QPen(color, 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawPath(g)
        p.drawEllipse(QPointF(0, 0), 2.5, 2.5)

    elif kind == "back":
        p.setPen(QPen(color, 1.55, Qt.SolidLine, Qt.FlatCap, Qt.MiterJoin))
        p.drawLine(QPointF(8.4, 0), QPointF(-8.4, 0))
        p.drawPolyline(_poly([(-2.3, -6.1), (-8.4, 0), (-2.3, 6.1)]))

    elif kind == "film":
        p.drawRoundedRect(QRectF(-7.4, -8.4, 14.8, 16.8), 2.6, 2.6)
        for y in (-5.2, -1.7, 1.7, 5.2):
            p.drawLine(QPointF(-7.4, y), QPointF(-4.6, y))
            p.drawLine(QPointF(4.6, y), QPointF(7.4, y))

    elif kind == "film_sq":                     # square film frame (Play queue rows)
        p.drawRoundedRect(QRectF(-7.4, -7.4, 14.8, 14.8), 2.6, 2.6)
        for y in (-4.4, -1.5, 1.5, 4.4):
            p.drawLine(QPointF(-7.4, y), QPointF(-4.8, y))
            p.drawLine(QPointF(4.8, y), QPointF(7.4, y))

    elif kind == "check":
        p.drawEllipse(QPointF(0, 0), 8.6, 8.6)
        p.drawPolyline(_poly([(-3.6, 0.2), (-1, 2.9), (4, -2.7)]))

    elif kind == "play_o":                      # outline triangle (menus, D4)
        p.drawPolygon(_poly([(-4.6, -7.0), (-4.6, 7.0), (7.0, 0.0)]))

    elif kind == "playnext":                    # outline triangle with a small plus
        p.drawPolygon(_poly([(-7.4, -7.6), (-7.4, 3.8), (2.0, -1.9)]))
        p.drawLine(QPointF(5.2, 3.0), QPointF(5.2, 9.4))
        p.drawLine(QPointF(2.0, 6.2), QPointF(8.4, 6.2))

    elif kind == "add":
        p.drawLine(QPointF(-7.5, 0), QPointF(7.5, 0))
        p.drawLine(QPointF(0, -7.5), QPointF(0, 7.5))

    elif kind == "remove":
        p.drawLine(QPointF(-6.5, -6.5), QPointF(6.5, 6.5))
        p.drawLine(QPointF(-6.5, 6.5), QPointF(6.5, -6.5))

    elif kind == "trash":                       # bin with a lid (Play queue page, Clear)
        p.drawLine(QPointF(-7.5, -5.2), QPointF(7.5, -5.2))
        p.drawPolyline(_poly([(-2.8, -5.2), (-2.8, -7.8), (2.8, -7.8), (2.8, -5.2)]))
        p.drawPolyline(_poly([(-5.6, -5.2), (-4.9, 7.6), (4.9, 7.6), (5.6, -5.2)]))
        p.drawLine(QPointF(-2.0, -1.6), QPointF(-2.0, 4.4))
        p.drawLine(QPointF(2.0, -1.6), QPointF(2.0, 4.4))

    elif kind == "rename":                      # a box with an A in it and a text cursor (Playlists page)
        p.drawRoundedRect(QRectF(-8.2, -6.6, 16.4, 13.2), 2.4, 2.4)
        p.drawPolyline(_poly([(-4.0, 3.8), (0.0, -4.0), (4.0, 3.8)]))
        p.drawLine(QPointF(-2.4, 1.2), QPointF(2.4, 1.2))
        p.drawLine(QPointF(-10.6, -8.4), QPointF(-10.6, 8.4))

    elif kind == "collapse":                    # two corner brackets pointing inward (folder stack, collapse)
        p.drawPolyline(_poly([(2.0, -8.0), (2.0, -2.0), (8.0, -2.0)]))
        p.drawPolyline(_poly([(-2.0, 8.0), (-2.0, 2.0), (-8.0, 2.0)]))

    elif kind == "select":                      # check mark with a line under it
        p.drawPolyline(_poly([(-7.5, -2.0), (-4.2, 1.2), (1.0, -4.6)]))
        p.drawLine(QPointF(-7.5, 5.8), QPointF(7.5, 5.8))

    elif kind == "photo":                       # picture frame with a sun and a hill (Change picture)
        p.drawRoundedRect(QRectF(-8.2, -6.6, 16.4, 13.2), 2.4, 2.4)
        p.drawEllipse(QPointF(-3.6, -2.2), 1.7, 1.7)
        p.drawPolyline(_poly([(-8.0, 4.4), (-2.8, 0.2), (1.2, 3.6), (4.0, 1.4), (8.0, 4.8)]))

    elif kind == "refresh":                     # open circle with an arrow head (Use automatic picture)
        p.drawArc(QRectF(-7.0, -7.0, 14.0, 14.0), 40 * 16, 280 * 16)
        p.drawPolyline(_poly([(2.0, 4.8), (5.6, 4.6), (5.2, 0.9)]))

    if slash:   # "off" state of shuffle / repeat
        cutc = QColor(bg) if bg is not None else QColor(BG)
        p.setPen(QPen(cutc, 3.8 + 0.8 * sw, Qt.SolidLine, Qt.RoundCap))
        if cutc.alpha() < 255:                        # PHASE 12c: a see-through bar colour must REPLACE the pixels, not blend over them
            p.setCompositionMode(QPainter.CompositionMode_Source)
        p.drawLine(QPointF(-9.0, -9.0), QPointF(9.0, 9.0))
        p.setCompositionMode(QPainter.CompositionMode_SourceOver)
        p.setPen(QPen(color, 1.9 * sw, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(-9.0, -9.0), QPointF(9.0, 9.0))

    p.restore()


class IconButton(QAbstractButton):
    def __init__(self, kind, size=40, glyph_px=20, parent=None):
        super().__init__(parent)
        self.kind = kind
        self.glyph_px = glyph_px
        self.active = False
        self.hover = Tween(self, 0, 180, self.update)
        self.press = Tween(self, 0, 90, self.update)
        self.alt = Tween(self, 1.0 if kind in ALT_ON else 0.0, 260, self.update)
        self.act = Tween(self, 0.0, 220, self.update)
        self.waves = 2
        self.off_slash = False
        self.shape = "round"
        self.sw = 1.0                       # stroke weight scale (the control bar uses 0.78)
        self.dis = DISABLED                 # colour when disabled
        self.cut = None                     # background colour for the slash cut-out
        self.fill = None                    # if set, the button clears its own background before painting
        self.setFixedSize(size, size)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)

    def set_kind(self, kind):
        if kind != self.kind:
            self.kind = kind
            self.alt.to(1.0 if kind in ALT_ON else 0.0, 260)
            self.update()

    def set_waves(self, n):
        if n != self.waves:
            self.waves = n
            self.update()

    def set_active(self, on):
        if on != self.active:
            self.active = on
            self.act.to(1.0 if on else 0.0, 220)

    def enterEvent(self, e):
        self.hover.to(1, 180)

    def leaveEvent(self, e):
        self.hover.to(0, 260)
        self.press.to(0, 260, EASE_SOFT)

    def mousePressEvent(self, e):
        super().mousePressEvent(e)
        self.press.to(1, 80)

    def mouseReleaseEvent(self, e):
        super().mouseReleaseEvent(e)
        self.press.to(0, 420, EASE_SPRING)     # springs back with a small overshoot

    def wheelEvent(self, e):
        e.ignore()  # the window turns the wheel into volume

    def paintEvent(self, e):
        p = QPainter(self)
        if self.fill is not None:                   # clear our own square first (no ghost of the old icon)
            p.fillRect(self.rect(), self.fill)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect())
        enabled = self.isEnabled()
        c = rect.center()
        hov = max(0.0, min(1.0, self.hover.value))
        scale = 1.0 - 0.14 * self.press.value + 0.03 * hov
        p.translate(c)
        p.scale(scale, scale)
        p.translate(-c)

        if self.kind in ("play", "pause"):
            self._paint_play(p, rect, hov)
            return
        if enabled and (hov > 0.01 or self.press.value > 0.01):
            p.setPen(Qt.NoPen)
            alpha = int(30 * hov + 26 * max(0.0, self.press.value))
            p.setBrush(QColor(255, 255, 255, alpha))
            if self.shape == "rect":
                p.drawRoundedRect(rect.adjusted(2, 2, -2, -2), 5, 5)
            else:
                p.drawEllipse(rect.adjusted(2, 2, -2, -2))
        base = QColor(TEXT if enabled else self.dis)
        color = mix(base, QColor(ACCENT), self.act.value)
        paint_icon(p, self.kind, c.x(), c.y(), self.glyph_px, color, self.alt.value,
                   self.waves, self.off_slash and not self.active, self.cut, self.sw)

    def _paint_play(self, p, rect, hov):
        c = rect.center()
        r = rect.width() / 2 - 3.8                 # ring centre line 23.2 px, stroke 4 px (measured: 29 and 5 px at 125 %)
        p.setPen(QPen(QBrush(ring_gradient(c.x(), c.y(), r)), 4.0))
        base = int(34 + 12 * hov)                  # disc fill #222222 at rest (measured)
        p.setBrush(QColor(base, base, base))
        p.drawEllipse(c, r, r)
        if self.kind == "play":                    # triangle 18 x 20 px with rounded corners, a little right of centre
            p.save()
            p.translate(c.x(), c.y())
            p.setPen(QPen(QColor(TEXT), 3.0, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.setBrush(QColor(TEXT))
            p.drawPolygon(_poly(_inset_tri([(-7.6, -10.0), (-7.6, 11.2), (11.2, 0.6)], 1.5)))
            p.restore()
            return
        paint_icon(p, self.kind, c.x(), c.y(), 27, QColor(TEXT), self.alt.value)

    def _paint_skip(self, p, rect, color):
        forward = self.kind == "fwd30"
        number = str(SKIP_SECONDS["fwd" if forward else "back"])
        c = rect.center()
        r = 9.8
        p.save()
        p.translate(c)
        if not forward:
            p.scale(-1, 1)
        p.setPen(QPen(color, 1.9, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        p.drawArc(QRectF(-r, -r, 2 * r, 2 * r), 60 * 16, -300 * 16)
        ex, ey = -0.5 * r, -0.866 * r
        dx, dy, nx, ny = 0.866, -0.5, 0.5, 0.866
        p.drawPolyline(_poly([(ex + dx * 1.2 + nx * 3.4, ey + dy * 1.2 + ny * 3.4),
                              (ex + dx * 4.6, ey + dy * 4.6),
                              (ex + dx * 1.2 - nx * 3.4, ey + dy * 1.2 - ny * 3.4)]))
        p.restore()
        p.setPen(color)
        p.setFont(ui_font(8, QFont.Bold))
        p.drawText(QRectF(rect.x(), rect.y() + 1, rect.width(), rect.height()), Qt.AlignCenter, number)


class FlatSlider(QWidget):
    """Thin Media-Player style slider (seek bar and volume), with smooth hover growth."""
    moved = Signal(int)            # while dragging
    released = Signal(int)         # user finished a drag / click
    hoverMoved = Signal(int, QPoint)   # value under the cursor, global point on the bar
    hoverLeft = Signal()
    wheelStep = Signal(int)        # whole wheel notches: +1 = scroll up, -1 = scroll down

    M = 11

    def __init__(self, maximum=1000, mark=None, parent=None):
        super().__init__(parent)
        self._max = max(1, int(maximum))
        self._val = 0
        self._mark = mark
        self._drag = False
        self._last_px = -1
        self.wheel_enabled = False     # the seek bar turns this on; the volume slider leaves it off
        self._wheel_acc = 0
        self._glide = QVariantAnimation(self)           # smooth move of the handle to a new value
        self._glide.setDuration(150)
        self._glide.setEasingCurve(EASE_OUT)
        self._glide.valueChanged.connect(self._on_glide)
        self.grow = Tween(self, 0, 130, self.update)
        self.th0 = 3.0                 # track thickness at rest (px)
        self.ring_r = 9.0              # grey ring round the handle
        self.dot_r = 5.5               # coloured handle
        self.ring_col = QColor(86, 86, 86)
        self.track_col = QColor(TRACK)
        self.fill_col = QColor(ACCENT)
        self.fill = None               # if set, the slider clears its own background before painting
        self.setFixedHeight(26)
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)

    def setMaximum(self, m):
        m = max(1, int(m))
        if m != self._max:
            self._max = m
            self.update()

    def _on_glide(self, v):
        self._val = int(v)
        px = int(self._val_to_x(self._val))
        if px != self._last_px:
            self._last_px = px
            self.update()

    def glide_to(self, v):
        if self._drag:
            return
        v = max(0, min(self._max, int(v)))
        self._glide.stop()
        self._glide.setStartValue(float(self._val))
        self._glide.setEndValue(float(v))
        self._glide.start()

    def setValue(self, v):
        if self._drag or self._glide.state() == QVariantAnimation.Running:
            return
        v = max(0, min(self._max, int(v)))
        self._val = v
        px = int(self._val_to_x(v))
        if px != self._last_px:
            self._last_px = px
            self.update()

    def dragging(self):
        return self._drag

    def _x_to_val(self, x):
        frac = (x - self.M) / max(1, self.width() - 2 * self.M)
        return int(min(1.0, max(0.0, frac)) * self._max)

    def _val_to_x(self, v):
        return self.M + (self.width() - 2 * self.M) * v / self._max

    def wheelEvent(self, e):
        if not self.wheel_enabled:
            e.ignore()                 # volume slider: the popup handles it
            return
        e.accept()                     # consumed here, so it never reaches the volume wheel code
        d = e.angleDelta().y()
        if d == 0:
            return
        if self._wheel_acc and (d > 0) != (self._wheel_acc > 0):
            self._wheel_acc = 0        # direction changed: start counting again
        self._wheel_acc += d           # touchpads send many small steps; 120 = one mouse notch
        steps = int(self._wheel_acc / 120)
        if steps:
            self._wheel_acc -= steps * 120
            self.wheelStep.emit(steps)

    def paintEvent(self, e):
        p = QPainter(self)
        if self.fill is not None:
            p.fillRect(self.rect(), self.fill)
        p.setRenderHint(QPainter.Antialiasing)
        g = self.grow.value
        cy = self.height() / 2
        th = self.th0 + 2 * g
        w = self.width()
        p.setPen(Qt.NoPen)
        p.setBrush(self.track_col)
        p.drawRoundedRect(QRectF(self.M, cy - th / 2, w - 2 * self.M, th), th / 2, th / 2)
        hx = self._val_to_x(self._val)
        p.setBrush(self.fill_col)
        p.drawRoundedRect(QRectF(self.M, cy - th / 2, max(0.0, hx - self.M), th), th / 2, th / 2)
        if self._mark is not None:
            mx = self._val_to_x(self._mark)
            p.setBrush(QColor(255, 255, 255, 120))
            p.drawRect(QRectF(mx - 0.5, cy - 6, 1.5, 12))
        p.setBrush(self.ring_col)
        p.drawEllipse(QPointF(hx, cy), self.ring_r + 1.5 * g, self.ring_r + 1.5 * g)
        p.setBrush(self.fill_col)
        p.drawEllipse(QPointF(hx, cy), self.dot_r + 0.5 * g, self.dot_r + 0.5 * g)

    def enterEvent(self, e):
        self.grow.to(1)

    def leaveEvent(self, e):
        if not self._drag:
            self.grow.to(0)
            self.hoverLeft.emit()

    def _emit_hover(self, ev, v):
        self.hoverMoved.emit(v, self.mapToGlobal(QPoint(int(ev.position().x()), 0)))

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            self._drag = True
            self.grow.to(1)
            self._val = self._x_to_val(ev.position().x())
            self.moved.emit(self._val)
            self._emit_hover(ev, self._val)
            self.update()

    def mouseMoveEvent(self, ev):
        v = self._x_to_val(ev.position().x())
        if self._drag:
            self._val = v
            self.moved.emit(v)
            self.update()
        self._emit_hover(ev, v)

    def mouseReleaseEvent(self, ev):
        if self._drag and ev.button() == Qt.LeftButton:
            self._drag = False
            self.released.emit(self._val)
            if not self.underMouse():
                self.grow.to(0)
                self.hoverLeft.emit()
            self.update()


class ElidedLabel(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.fill = None                   # if set, the label clears its own background before painting
        self.fill_fn = None                # if set, asked at paint time for the colour (never out of date)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)

    def minimumSizeHint(self):
        return QSize(0, super().minimumSizeHint().height())

    def paintEvent(self, e):
        p = QPainter(self)
        f = self.fill_fn() if self.fill_fn else self.fill
        if f is not None:
            p.fillRect(self.rect(), f)
        p.setFont(self.font())
        p.setPen(QColor(TEXT))
        text = self.fontMetrics().elidedText(self.text(), Qt.ElideRight, self.width())
        p.drawText(self.rect(), Qt.AlignVCenter | Qt.AlignLeft, text)


def _overlay_flags():
    return (Qt.Tool | Qt.FramelessWindowHint | Qt.WindowDoesNotAcceptFocus
            | Qt.WindowTransparentForInput)


class SlotX(QWidget):
    """PHASE B: the X on the top right of the live preview slot (Home only). A see-through window above VLC's own
    window; it never takes the mouse (the main window polls the mouse, see MainWindow._block_poll)."""
    SIZE, INSET, PAD = 28, 6, 5

    def __init__(self, owner):
        super().__init__(owner, _overlay_flags())
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFixedSize(self.SIZE + 2 * self.PAD, self.SIZE + 2 * self.PAD)
        self.appear = Tween(self, 0.0, 220, self.update)
        self.hov = Tween(self, 0.0, 140, self.update)
        self.prs = Tween(self, 0.0, 90, self.update)
        self.want = False
        self.appear.anim.finished.connect(self._maybe_hide)

    @classmethod
    def disc_rect(cls, slot_g):
        """Global rect of the disc: top right of the slot, 6 px in from the corner."""
        return QRect(slot_g.right() + 1 - cls.INSET - cls.SIZE, slot_g.top() + cls.INSET, cls.SIZE, cls.SIZE)

    def armed(self):
        return self.want and self.appear.value > 0.5

    def sync(self, slot_g, pos, down):
        """slot_g: global rect of the slot while the mouse is on it (or on the X); None = hide."""
        if slot_g is None:
            self._set_want(False)
            return
        d = self.disc_rect(slot_g)
        self.move(d.x() - self.PAD, d.y() - self.PAD)
        self._set_want(True)
        over = d.contains(pos)
        self.hov.to(1.0 if over else 0.0, 140)
        self.prs.to(1.0 if (over and down) else 0.0, 90)

    def _set_want(self, on):
        if on == self.want:
            return
        self.want = on
        if on:
            self.show()
            self.appear.to(1.0, 220, EASE_SPRING)             # spring: pops in with a small overshoot
        else:
            self.appear.to(0.0, 140)

    def _maybe_hide(self):
        if not self.want and self.appear.value < 0.02:
            self.hide()

    def paintEvent(self, ev):
        a = self.appear.value
        if a <= 0.01:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setOpacity(max(0.0, min(1.0, a * 1.6)))
        c = QPointF(self.width() / 2.0, self.height() / 2.0)
        sc = (0.6 + 0.4 * a) * (1.0 - 0.10 * self.prs.value)
        p.translate(c)
        p.scale(sc, sc)
        h, pr = self.hov.value, self.prs.value
        dark, acc = QColor("#1b1617"), QColor(ACCENT)
        disc = QColor(int(lerp(dark.red(), acc.red(), h)), int(lerp(dark.green(), acc.green(), h)),
                      int(lerp(dark.blue(), acc.blue(), h)))
        if pr > 0:
            disc = disc.darker(100 + int(35 * pr))
        p.setPen(QPen(QColor(255, 255, 255, int(40 * (1 - h))), 1))
        p.setBrush(disc)
        p.drawEllipse(QPointF(0, 0), self.SIZE / 2.0 - 0.5, self.SIZE / 2.0 - 0.5)
        glyph = QColor(int(lerp(255, dark.red(), h)), int(lerp(255, dark.green(), h)), int(lerp(255, dark.blue(), h)))
        pen = QPen(glyph, 1.9)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.drawLine(QPointF(-4.2, -4.2), QPointF(4.2, 4.2))
        p.drawLine(QPointF(4.2, -4.2), QPointF(-4.2, 4.2))


class NameTip(QWidget):
    """PHASE B: the only tooltip of the app. It shows the full name when a name is cut off. A small dark card with a
    thin light border and a soft shadow; it never takes the mouse or the focus."""
    MAXW, PADX, PADY, SH = 320, 12, 9, 10

    def __init__(self, owner):
        super().__init__(owner, _overlay_flags())
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.text = ""
        self.fade = QPropertyAnimation(self, b"windowOpacity", self)
        self.fade.setDuration(120)

    def popup(self, text, anchor, above):
        """anchor: global rect of the cut-off name. The card opens below it (or above it for the control bar)."""
        self.text = text
        fm = QFontMetrics(ui_font(13))
        tr = fm.boundingRect(QRect(0, 0, self.MAXW - 2 * self.PADX, 4000), Qt.TextWordWrap, text)
        cw, ch = tr.width() + 2 * self.PADX + 2, tr.height() + 2 * self.PADY
        self._card = QRect(self.SH, self.SH, cw, ch)
        self.setFixedSize(cw + 2 * self.SH, ch + 2 * self.SH)
        scr = QApplication.screenAt(anchor.center()) or QApplication.primaryScreen()
        g = scr.availableGeometry()
        x = max(g.left(), min(anchor.left() - self.SH, g.right() - self.width()))
        y_below = anchor.bottom() + 6 - self.SH
        y_above = anchor.top() - 6 - ch - self.SH
        y = y_above if (above or y_below + self.height() > g.bottom()) else y_below
        self.move(x, max(g.top(), y))
        self.setWindowOpacity(0.0)
        self.show()
        self.fade.stop()
        self.fade.setStartValue(0.0)
        self.fade.setEndValue(1.0)
        self.fade.start()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        for i in range(self.SH):                         # soft shadow
            p.setBrush(QColor(0, 0, 0, int(5 + 3 * (self.SH - i) / self.SH)))
            p.drawRoundedRect(QRectF(self._card).adjusted(-i, -i + 4, i, i + 4), 8 + i, 8 + i)
        p.setBrush(QColor("#2f2b2d"))
        p.setPen(QPen(QColor(255, 255, 255, 36), 1))
        p.drawRoundedRect(QRectF(self._card).adjusted(0.5, 0.5, -0.5, -0.5), 8, 8)
        p.setPen(QColor(TEXT))
        p.setFont(ui_font(13))
        p.drawText(self._card.adjusted(self.PADX, self.PADY, -self.PADX, -self.PADY),
                   Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap, self.text)


class TipLabel(ElidedLabel):
    """The title in the control bar: shows the name tooltip when the name is cut off."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)

    def _arm(self):
        host = self.window()
        if hasattr(host, "tip_request") and self.fontMetrics().horizontalAdvance(self.text()) > self.width():
            host.tip_request(self, self.text(), QRect(self.mapToGlobal(QPoint(0, 0)), self.size()), True)

    def enterEvent(self, ev):
        self._arm()

    def mouseMoveEvent(self, ev):
        self._arm()

    def mousePressEvent(self, ev):
        host = self.window()
        if hasattr(host, "tip_cancel"):
            host.tip_cancel(self)
        super().mousePressEvent(ev)

    def leaveEvent(self, ev):
        host = self.window()
        if hasattr(host, "tip_cancel"):
            host.tip_cancel(self)


class Osd(QWidget):
    """Short on-screen message that floats above the video and fades in/out."""

    def __init__(self, owner):
        super().__init__(owner, _overlay_flags())
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self._text = ""
        self._accent = False
        self._hiding = False
        self._timer = QTimer(self, singleShot=True, interval=1100)
        self._timer.timeout.connect(self._begin_hide)
        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setDuration(150)
        self._fade.finished.connect(self._after_fade)

    def popup(self, text, anchor, accent=False):
        self._text, self._accent = text, accent
        font = ui_font(16, QFont.DemiBold)
        w = QFontMetrics(font).horizontalAdvance(text) + 40
        self.resize(max(w, 100), 40)
        g = anchor.mapToGlobal(QPoint(anchor.width() // 2, 0))
        self.move(g.x() - self.width() // 2, g.y() + 24)
        self._hiding = False
        self._fade.stop()
        if not self.isVisible():
            self.setWindowOpacity(0.0)
            self.show()
        self.raise_()
        self.update()
        self._fade.setStartValue(self.windowOpacity())
        self._fade.setEndValue(1.0)
        self._fade.start()
        self._timer.start()

    def _begin_hide(self):
        self._hiding = True
        self._fade.stop()
        self._fade.setStartValue(self.windowOpacity())
        self._fade.setEndValue(0.0)
        self._fade.start()

    def _after_fade(self):
        if self._hiding:
            self.hide()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(28, 28, 28, 230))
        p.drawRoundedRect(QRectF(self.rect()), 9, 9)
        p.setPen(QColor(ACCENT if self._accent else TEXT))
        p.setFont(ui_font(16, QFont.DemiBold))
        p.drawText(self.rect(), Qt.AlignCenter, self._text)


class TimeBubble(QWidget):
    """Small, slim time label that follows the cursor over the seek bar."""

    def __init__(self, owner):
        super().__init__(owner, _overlay_flags())
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self._text = ""

    def show_time(self, text, global_pt):
        self._text = text
        w = QFontMetrics(ui_font(12, QFont.DemiBold)).horizontalAdvance(text) + 18
        self.resize(w, 24)
        self.move(global_pt.x() - w // 2, global_pt.y() - self.height() - 6)
        if not self.isVisible():
            self.show()
        self.raise_()
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor(255, 255, 255, 28), 1))
        p.setBrush(QColor(40, 40, 40, 238))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 6, 6)
        p.setPen(QColor(TEXT))
        p.setFont(ui_font(12, QFont.DemiBold))
        p.drawText(self.rect(), Qt.AlignCenter, self._text)


def mini_layout(w, h):
    """Mini player overlay geometry (logical px) from the Media Player mini view, measured at 125 %.
    (w, h) is the whole visible window (title bar included). Returns the hit rectangles and the centres."""
    cy = h / 2.0                                # centre of the whole window: 108 px at 216 (79 px below the title bar)
    cx = w / 2.0 + 0.8                          # measured: the cluster sits about 1 px right of the middle
    pitch = 44.0
    c = {"prev": QPointF(cx - pitch, cy), "play": QPointF(cx, cy), "next": QPointF(cx + pitch, cy),
         "expand": QPointF(w - 21.2, h - 32.5)}
    r = {}
    for k, pt in c.items():
        d = 40.0 if k != "expand" else 34.0
        r[k] = QRectF(pt.x() - d / 2, pt.y() - d / 2, d, d)
    return r, c


class MiniOverlay(QWidget):
    """The mini player's controls (title bar, controls and progress line all come and go with the mouse). The picture is VLC's native window, which a normal child widget cannot
    be see-through over, so this is a small frameless see-through window that follows the main window.
    It never takes focus or mouse input: the main window polls the mouse (see _mini_tick)."""
    DIM = 90                                    # picture dimming while the mouse is over the window (estimate)

    def __init__(self, owner):
        super().__init__(owner, _overlay_flags())
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.hov = Tween(self, 0.0, 160, self.update)
        self._hover = False
        self.frac = 0.0
        self.playing = False
        self.has_next = False
        self.tb = 1.0                           # how much of the title bar is showing (0..1)

    def set_hover(self, on):
        if on != self._hover:
            self._hover = on
            self.hov.to(1.0 if on else 0.0, 160 if on else 220)

    def amount(self):
        return max(0.0, min(1.0, self.hov.value))

    def set_state(self, frac, playing, has_next, tb=1.0):
        frac = max(0.0, min(1.0, frac))
        tb = max(0.0, min(1.0, tb))
        if (abs(frac - self.frac) > 0.0005 or playing != self.playing or has_next != self.has_next
                or abs(tb - self.tb) > 0.004):
            self.frac, self.playing, self.has_next, self.tb = frac, playing, has_next, tb
            self.update()

    def button_at(self, pt):
        if self.amount() < 0.5:
            return None
        rects, _ = mini_layout(self.width(), self.height())
        for k, r in rects.items():
            if r.contains(QPointF(pt)):
                return k
        return None

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        a = self.amount()
        if a > 0.01:
            p.setOpacity(a)
            p.fillRect(QRectF(0, MINI_TB_H, w, h - MINI_TB_H), QColor(0, 0, 0, self.DIM))
            if self.tb < 0.99:                           # title bar fading away: its strip gets the dim as well
                p.fillRect(QRectF(0, 0, w, MINI_TB_H), QColor(0, 0, 0, int(self.DIM * (1.0 - self.tb))))
            _, c = mini_layout(w, h)
            white = QColor(TEXT)
            dis = QColor("#6a6a6a")
            paint_icon(p, "prev", c["prev"].x(), c["prev"].y(), 20.0, white, 0.0, 2, False, None, 0.9)
            paint_icon(p, "next", c["next"].x(), c["next"].y(), 20.0, white if self.has_next else dis,
                       0.0, 2, False, None, 0.9)
            # play ring: outer 36 px, stroke 2.4 px, no disc (measured: 45 and 3 px at 125 %)
            pc = c["play"]
            r = 18.0 - 1.2
            p.setPen(QPen(QBrush(ring_gradient(pc.x(), pc.y(), r)), 2.4))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(pc, r, r)
            if self.playing:
                paint_icon(p, "pause", pc.x(), pc.y(), 18.0, white, 1.0)
            else:
                p.save()
                p.translate(pc.x(), pc.y())
                p.scale(0.68, 0.68)
                p.setPen(QPen(white, 3.0, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
                p.setBrush(white)
                p.drawPolygon(_poly(_inset_tri([(-7.6, -10.0), (-7.6, 11.2), (11.2, 0.6)], 1.5)))
                p.restore()
            ex = c["expand"]
            paint_icon(p, "mini_expand", ex.x(), ex.y(), 20.0, white, 0.0, 2, False, None, 0.9)
            p.setOpacity(1.0)
        # thin progress line: fades with the controls (orange 3 px, grey track 1.5 px; measured 4 and 1 px at 125 %)
        if a <= 0.01:
            return
        p.setOpacity(a)
        x0, x1 = 2.0, w - 2.0
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#949494"))
        p.drawRect(QRectF(x0, h - 2.6, x1 - x0, 1.6))
        if self.frac > 0:
            p.setBrush(QColor("#ff8232"))
            p.drawRoundedRect(QRectF(x0, h - 4.2, max(3.0, (x1 - x0) * self.frac), 3.2), 1.0, 1.0)


class VolumePopup(QWidget):
    changed = Signal(int)
    muteClicked = Signal()
    wheel = Signal(int)

    def __init__(self, owner):
        super().__init__(owner, Qt.Popup | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 8, 20, 8)       # icon sits about 9.5 px inside its 36 px button: 19.5 left, 20 right
        lay.setSpacing(6)
        self.btn = IconButton("volume", 36, 17)
        self.btn.clicked.connect(self.muteClicked)
        self.slider = FlatSlider(200)
        self.slider.setFixedWidth(170)
        self.slider.moved.connect(self.changed)
        self.label = QLabel("100")
        self.label.setFixedWidth(34)
        self.label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        lay.addWidget(self.btn)
        lay.addWidget(self.slider)
        lay.addWidget(self.label)
        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setDuration(130)

    def sync(self, volume, muted):
        self.slider.setValue(volume)
        self.btn.set_kind("mute" if muted or volume == 0 else "volume")
        self.label.setText(str(volume))
        color = ACCENT if volume > 100 else TEXT
        self.label.setStyleSheet(f"color:{color};font-size:14px;")

    def fade_in(self):
        self.setWindowOpacity(0.0)
        self.show()
        self._fade.stop()
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.start()

    def wheelEvent(self, e):
        d = e.angleDelta().y()
        if d:
            self.wheel.emit(1 if d > 0 else -1)
        e.accept()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor("#3a3a3a"), 1))
        p.setBrush(QColor("#2b2b2b"))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 8, 8)


# --------------------------------------------------------------------------
# Home: Plex-style grid of recent videos with thumbnails and progress bars
# --------------------------------------------------------------------------
def human_left(ms):
    minutes = int(ms // 60000)
    h, m = divmod(minutes, 60)
    return f"{h}h {m}m left" if h else f"{max(1, m)}m left"


def wrap_lines(text, fm, width, lines=2):
    """Wrap `text` into at most `lines` lines; the last line ends with an ellipsis if it is cut."""
    out, rest = [], text
    for i in range(lines):
        if not rest:
            break
        if i == lines - 1 or fm.horizontalAdvance(rest) <= width:
            out.append(fm.elidedText(rest, Qt.ElideRight, width))
            break
        n = 1
        while n < len(rest) and fm.horizontalAdvance(rest[:n + 1]) <= width:
            n += 1
        cut = n
        for sep in (" ", ".", "-", "_"):               # prefer to break at a natural place
            k = rest.rfind(sep, 0, n)
            if k > n * 0.4:
                cut = k + 1
                break
        out.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip()
    return out


# --------------------------------------------------------------------------
# PHASE B: card pictures. Black bars are cut, the picture is made 16:9 (480x270) when it is saved, so the cards,
# the mini view and the control bar all show the same bar-free file. Pure functions: easy to test offscreen.
# --------------------------------------------------------------------------
PIC_W, PIC_H = 480, 270                 # saved card picture (16:9)
PIC_CROP_MAX = 0.35                     # approved: a crop never takes more than 35 % of the picture
_BAR_T = 22                             # gray level (0..255) at or below which a pixel counts as bar black
_BAR_TBL = bytes([0] * (_BAR_T + 1) + [1] * (255 - _BAR_T))


def _gray_data(img):
    g = img.convertToFormat(QImage.Format_Grayscale8)
    w, h, stride = g.width(), g.height(), g.bytesPerLine()
    return bytes(g.constBits())[: stride * h], w, h, stride


def find_bars(img):
    """Black bars of a frame: (left, top, right, bottom) in pixels. A bar must exist on BOTH opposite sides
    (a dark sky is not a bar); the cut is 2 px deeper than the bar so no soft edge stays."""
    data, w, h, stride = _gray_data(img)
    if w < 16 or h < 16:
        return 0, 0, 0, 0

    def row_bar(y):
        row = data[y * stride: y * stride + w]
        return sum(row.translate(_BAR_TBL)) <= w * 0.01

    def col_bar(x):
        col = data[x: (h - 1) * stride + x + 1: stride]
        return sum(col.translate(_BAR_TBL)) <= h * 0.01

    def run(test, limit):
        n = 0
        while n < limit and test(n):
            n += 1
        return n

    top = run(row_bar, int(h * 0.45))
    bot = run(lambda i: row_bar(h - 1 - i), int(h * 0.45))
    left = run(col_bar, int(w * 0.45))
    right = run(lambda i: col_bar(w - 1 - i), int(w * 0.45))
    minv, minh = max(2, int(h * 0.01)), max(2, int(w * 0.01))
    if not (top >= minv and bot >= minv):
        top = bot = 0
    if not (left >= minh and right >= minh):
        left = right = 0
    ins = 2
    top, bot = (top + ins if top else 0), (bot + ins if bot else 0)
    left, right = (left + ins if left else 0), (right + ins if right else 0)
    if top + bot >= h * 0.9 or left + right >= w * 0.9:
        return 0, 0, 0, 0
    return left, top, right, bot


def _soft_bar_colour(img):
    c = QColor(img.scaled(1, 1, Qt.IgnoreAspectRatio, Qt.SmoothTransformation).pixel(0, 0))
    h, s, l, _ = c.getHsl()
    return QColor.fromHsl(max(h, 0), int(s * 0.55), max(18, min(52, int(l * 0.35))))


def make_card_picture(img):
    """Frame -> (QImage 480x270, info). Bars cut; a crop to 16:9 up to 35 %; beyond that the whole picture is
    placed smaller with two EXACTLY EQUAL soft bars. info: mode 'fill' or 'bars'; keep = (l, t, r, b) fractions of
    the source frame that the picture shows; bars = (first, second) bar sizes in output pixels (None for fill)."""
    sw, sh = img.width(), img.height()
    l, t, r, b = find_bars(img)
    cw, ch = sw - l - r, sh - t - b
    out = QImage(PIC_W, PIC_H, QImage.Format_RGB32)
    asp = cw / float(ch)
    tgt = PIC_W / float(PIC_H)
    info = {"mode": "fill", "bars": None, "bars_cut": (l, t, r, b)}
    kx0, ky0, kx1, ky1 = l, t, l + cw, t + ch                    # part of the source that stays (pixels)
    if abs(asp - tgt) < 0.02:
        pass
    elif asp > tgt and 1 - tgt / asp <= PIC_CROP_MAX:          # too wide: cut the sides evenly
        nw = int(round(ch * tgt))
        kx0 = l + (cw - nw) // 2
        kx1 = kx0 + nw
    elif asp < tgt and 1 - asp / tgt <= PIC_CROP_MAX:          # too tall: cut top and bottom evenly
        nh = int(round(cw / tgt))
        ky0 = t + (ch - nh) // 2
        ky1 = ky0 + nh
    else:
        info["mode"] = "bars"
    p = QPainter(out)
    p.setRenderHint(QPainter.SmoothPixmapTransform, True)
    if info["mode"] == "fill":
        p.drawImage(QRect(0, 0, PIC_W, PIC_H), img, QRect(kx0, ky0, kx1 - kx0, ky1 - ky0))
    else:
        content = img.copy(l, t, cw, ch)
        p.fillRect(0, 0, PIC_W, PIC_H, _soft_bar_colour(content))
        if asp < tgt:                                          # tall picture: equal bars left and right
            pw = int(round(PIC_H * asp)) // 2 * 2              # even width, so (480 - pw) / 2 is a whole number
            x0 = (PIC_W - pw) // 2
            p.drawImage(QRect(x0, 0, pw, PIC_H), content)
            info["bars"] = (x0, PIC_W - pw - x0)
        else:                                                  # very wide picture: equal bars top and bottom
            ph = int(round(PIC_W / asp)) // 2 * 2
            y0 = (PIC_H - ph) // 2
            p.drawImage(QRect(0, y0, PIC_W, ph), content)
            info["bars"] = (y0, PIC_H - ph - y0)
    p.end()
    info["keep"] = (kx0 / sw, ky0 / sh, kx1 / sw, ky1 / sh)
    return out, info


def frame_score(img):
    """Quality of a frame for a card picture: a score 0..1, or None when the frame must be dropped (too dark,
    too bright, flat or blurry). Measured on the part left after the black bars are cut."""
    l, t, r, b = find_bars(img)
    c = img.copy(l, t, img.width() - l - r, img.height() - t - b)
    sm = c.scaled(96, 54, Qt.IgnoreAspectRatio, Qt.SmoothTransformation).convertToFormat(QImage.Format_RGB32)
    g = sm.convertToFormat(QImage.Format_Grayscale8)
    w, h, stride = g.width(), g.height(), g.bytesPerLine()
    d = bytes(g.constBits())
    vals = [d[y * stride + x] for y in range(h) for x in range(w)]
    n = len(vals)
    mean = sum(vals) / n
    std = (sum((v - mean) ** 2 for v in vals) / n) ** 0.5
    diff = sum(abs(d[y * stride + x] - d[y * stride + x + 1]) for y in range(h) for x in range(w - 1)) / (h * (w - 1))
    cols = {(QColor(sm.pixel(x, y)).red() >> 4, QColor(sm.pixel(x, y)).green() >> 4, QColor(sm.pixel(x, y)).blue() >> 4)
            for y in range(h) for x in range(w)}
    if mean < 28 or mean > 225 or std < 14 or diff < 1.5:
        return None
    bright = max(0.0, 1 - abs(mean - 120) / 120)
    return 0.30 * bright + 0.25 * min(std / 60, 1) + 0.25 * min(diff / 8, 1) + 0.20 * min(len(cols) / 200, 1)


def save_card_picture(img, tmp, final):
    """Process a frame (bars cut, 16:9) and store it as the card picture, with a small crop note beside it."""
    out, info = make_card_picture(img)
    if not out.save(str(tmp), "JPG", 88):
        raise OSError("picture not saved")
    os.replace(tmp, final)
    try:
        with open(final.with_suffix(".crop"), "w", encoding="utf-8") as fh:
            json.dump({"mode": info["mode"], "keep": [round(v, 5) for v in info["keep"]]}, fh)
    except OSError:
        pass
    return info


def read_crop_note(final):
    """(mode, (l, t, r, b) fractions of the source frame that the card picture shows) or None."""
    try:
        with open(final.with_suffix(".crop"), "r", encoding="utf-8") as fh:
            d = json.load(fh)
        k = d.get("keep")
        if isinstance(k, list) and len(k) == 4:
            return d.get("mode", "fill"), tuple(float(v) for v in k)
    except (OSError, ValueError, TypeError):
        pass
    return None


SAMPLE_FRACTIONS = (0.10, 0.25, 0.40, 0.55, 0.70)      # approved: about 5 frames, never the first minute or the end


def sample_times(dur_ms):
    """Times (ms) of the frames to look at: 10, 25, 40, 55, 70 percent; the first minute and the last 30 s are
    skipped when the video is long enough."""
    out = []
    for f in SAMPLE_FRACTIONS:
        t = dur_ms * f
        if dur_ms > 300000:
            t = max(t, 60000)
        if dur_ms > 120000:
            t = min(t, dur_ms - 30000)
        t = int(max(0, min(t, dur_ms * 0.95)))
        if all(abs(t - o) > 1500 for o in out):
            out.append(t)
    return out


class VlcFrames:
    """Hidden second VLC engine: opens a video without a window and saves single frames. Only used off the UI thread."""

    def __init__(self, tmpdir):
        self.tmp = tmpdir / "frame.jpg"
        self.inst = self.pl = None

    def start(self):
        self.inst = vlc.Instance("--quiet", "--no-audio", "--vout=dummy", "--no-video-title-show", "--no-osd",
                                 "--no-spu", "--no-stats", "--no-sub-autodetect-file")
        self.pl = self.inst.media_player_new()

    def open(self, path):
        """Start the video paused-free; returns its length in ms (0 = could not open)."""
        m = self.inst.media_new_path(path)
        m.parse()
        dur = m.get_duration() or 0
        self.pl.set_media(m)
        self.pl.play()
        end = time.monotonic() + 10
        while time.monotonic() < end:
            st = self.pl.get_state()
            if st == vlc.State.Playing:
                return dur
            if st in (vlc.State.Error, vlc.State.Ended, vlc.State.Stopped):
                break
            time.sleep(0.05)
        return 0

    def frame_at(self, ms):
        self.pl.set_time(int(ms))
        end = time.monotonic() + 4
        while time.monotonic() < end and self.pl.get_time() < ms - 700:
            time.sleep(0.05)
        time.sleep(0.25)
        try:
            if self.tmp.exists():
                self.tmp.unlink()
        except OSError:
            pass
        if self.pl.video_take_snapshot(0, str(self.tmp), PIC_W, 0) != 0:
            return None
        end = time.monotonic() + 2.5
        while time.monotonic() < end:
            try:
                if self.tmp.exists() and self.tmp.stat().st_size > 0:
                    time.sleep(0.05)
                    img = QImage(str(self.tmp))
                    return None if img.isNull() else img
            except OSError:
                pass
            time.sleep(0.05)
        return None

    def close(self):
        try:
            self.pl.stop()
        except Exception:
            pass

    def release(self):
        try:
            self.pl.release()
            self.inst.release()
        except Exception:
            pass


class ThumbWorker(QObject):
    """PHASE B: makes the card pictures in the background, one video at a time, on its own thread. It waits while
    a video is opening or playing (busy_fn). Jobs: ("auto") first picture of a video, ("fix") re-cut an old picture
    that still has black bars, ("samples") the 5 frames shown by Change picture (runs even while a video plays,
    because the user asked for it)."""
    done = Signal(str)                    # path of a card picture that is new or changed
    progress = Signal(int, int)           # jobs finished, jobs in this batch
    samplesReady = Signal(str, object)    # path, [(ms, QImage 480x270)]
    switchedOff = Signal()
    noFrame = Signal(str, int)            # a video that gave no frame (the window remembers it between sessions)

    def __init__(self, store, busy_fn, log_fn, source_factory=None, parent=None):
        super().__init__(parent)
        self.store, self.busy, self.log = store, busy_fn, log_fn
        self.factory = source_factory or (lambda: VlcFrames(self.store.thumb_dir))
        self._lock = threading.Lock()
        self._jobs = []                   # (kind, path, size)
        self._wake = threading.Event()
        self._stop = False
        self._thread = None
        self._src = None
        self._total = self._done_n = 0
        self._fails = 0
        self._failed = set()                # (path, size) of videos that could not give a frame: not tried again
        self.off = False

    # ---- called from the UI thread
    def submit(self, jobs):
        """Replace the waiting auto/fix jobs by this list [(kind, path, size)]. A job that is running goes on."""
        if self.off:
            return
        with self._lock:
            keep = [j for j in self._jobs if j[0] == "samples"]
            jobs = [j for j in jobs if (j[1], j[2]) not in self._failed]
            self._jobs = keep + list(jobs)
            self._total, self._done_n = len(jobs), 0
        self._ensure_thread()
        self._wake.set()

    def submit_samples(self, path, size):
        if self.off:
            return
        with self._lock:
            self._jobs = [j for j in self._jobs if not (j[0] == "samples" and j[1] == path)]
            self._jobs.insert(0, ("samples", path, size))
        self._ensure_thread()
        self._wake.set()

    def pending(self):
        with self._lock:
            return len(self._jobs)

    def stop(self):
        self._stop = True
        self._wake.set()

    def _ensure_thread(self):
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()

    # ---- worker thread
    def _next(self):
        with self._lock:
            if not self._jobs:
                return None
            idle = not self.busy()
            for i, j in enumerate(self._jobs):
                if idle or j[0] == "samples":
                    return self._jobs.pop(i)
            return None

    def _run(self):
        try:
            while not self._stop:
                job = self._next()
                if job is None:
                    if self._src is not None:                    # nothing to do or a video is playing: free the engine
                        self._drop_source()
                    self._wake.wait(0.5)
                    self._wake.clear()
                    continue
                try:
                    self._do(job)
                except Exception as ex:                          # a broken video must never stop the worker
                    self.log(f"thumbnail worker: {os.path.basename(job[1])}: {type(ex).__name__}: {ex}")
                    if job[0] != "samples":
                        self._failed.add((job[1], job[2]))         # not tried again this session
                    self._drop_source()
                if job[0] != "samples":
                    self._done_n += 1
                    self.progress.emit(self._done_n, self._total)
        finally:
            self._drop_source()

    def _drop_source(self):
        s, self._src = self._src, None
        if s is not None:
            try:
                s.close()
                s.release()
            except Exception:
                pass

    def _source(self):
        if self._src is None:
            t = time.monotonic()
            self._src = self.factory()
            self._src.start()
            self.log(f"thumbnail worker: hidden VLC engine created, took {(time.monotonic() - t) * 1000:.0f} ms")
        return self._src

    def _final(self, path, size):
        return self.store.thumb_path(path, size)

    def _do(self, job):
        kind, path, size = job
        final = self._final(path, size)
        if kind == "fix":
            img = QImage(str(final))
            if not img.isNull():
                save_card_picture(img, final.with_name(final.stem + ".new.jpg"), final)
                self.done.emit(path)
            return
        if kind == "auto" and (final.exists() or self.store.is_picked(path, size)):
            return
        t0 = time.monotonic()
        src = self._source()
        dur = src.open(path)
        if dur <= 0:
            self.log(f"thumbnail worker: could not open {os.path.basename(path)}")
            self._failed.add((path, size))
            self.noFrame.emit(path, int(size))
            self._drop_source()
            return
        times = sample_times(dur)
        frames = []                                            # (score, ms, QImage)
        for t in times:
            if kind != "samples" and self.busy():              # a video started playing: give the disk back
                with self._lock:
                    self._jobs.insert(0, job)
                    self._done_n -= 1                          # it is not finished: not counted
                self._drop_source()
                return
            img = src.frame_at(t)
            if img is None:
                continue
            frames.append((frame_score(img), t, img))
        if kind == "samples":
            outs = [(t, make_card_picture(img)[0], img) for _s, t, img in frames]
            self.samplesReady.emit(path, outs)
            return
        if not frames:
            self._fails += 1
            self._failed.add((path, size))
            self.noFrame.emit(path, int(size))
            self.log(f"thumbnail worker: no frame came out for {os.path.basename(path)} "
                     f"(fail {self._fails} in a row)")
            if self._fails >= 3:
                self.off = True
                self.log("thumbnail worker: switched off for this session, snapshots do not work with the hidden engine")
                self._drop_source()
                self.switchedOff.emit()
            return
        self._fails = 0
        good = [f for f in frames if f[0] is not None]
        if good:
            best = max(good, key=lambda f: f[0])[2]
        else:                                                  # every frame was dark, flat or blurry: the 20 % frame
            best = src.frame_at(int(dur * 0.20)) or frames[0][2]
        if final.exists() or self.store.is_picked(path, size):
            return
        save_card_picture(best, final.with_name(final.stem + ".new.jpg"), final)
        self.log(f"thumbnail worker: {os.path.basename(path)} done in {(time.monotonic() - t0) * 1000:.0f} ms "
                 f"({len(frames)} frames, {len(good)} kept)")
        self.done.emit(path)


# --------------------------------------------------------------------------
# PHASE B: placeholder picture (a clapperboard) for a card or preview that has no picture yet.
# The tint comes from the show (or the file name), so a video always keeps its colour.
# --------------------------------------------------------------------------
_CLAP_PALETTES = (          # dark, mid, light, accent
    ("#1f1a33", "#4a3c7a", "#8d7bd0", "#ffb36b"),      # violet
    ("#10232d", "#2f6580", "#6fb3d2", "#ffd479"),      # blue
    ("#2a1812", "#7d4f3a", "#dd9a73", "#f6e3a1"),      # terracotta
    ("#14261c", "#3b6b4c", "#8cc29a", "#ffe08a"),      # green
    ("#2a1520", "#7a3f5c", "#d48fae", "#ffc8a0"),      # rose
    ("#15191f", "#3b4a5c", "#8da0b8", "#f1efe6"),      # slate
)


def clapper_info(path):
    """(palette index, tag text) of a video: the show key (or the file name) picks the tint; an episode shows
    S02E09, anything else the first letter of its shown name."""
    ep = parse_episode(path)
    key = ep["key"] if ep and ep["key"] else os.path.splitext(os.path.basename(path))[0].lower()
    idx = int(hashlib.sha1(key.encode("utf-8", "replace")).hexdigest()[:8], 16) % len(_CLAP_PALETTES)
    if ep:
        tag = f"S{ep['season']:02d}E{ep['episode']:02d}"
    else:
        name = display_name(path)
        tag = next((c.upper() for c in name if c.isalnum()), "")
    return idx, tag


def paint_clapper(p, rect, path):
    """Draw the clapperboard placeholder inside `rect` (any 16:9 size; drawn for 192 x 108 and scaled)."""
    idx, tag = clapper_info(path)
    dk, md, lt, ac = (QColor(c) for c in _CLAP_PALETTES[idx])
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    p.translate(rect.topLeft())
    sc = rect.width() / 192.0
    p.scale(sc, sc)
    bg = QRadialGradient(96, 49, 100)
    bg.setColorAt(0, md)
    bg.setColorAt(1, dk)
    p.fillRect(QRectF(0, 0, 192, 108), bg)
    p.translate(96, 56)
    p.rotate(-4)
    p.translate(-96, -56)
    for i, a in enumerate((60, 36, 18)):                 # soft shadow under the board
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, a))
        p.drawRoundedRect(QRectF(34 - i * 2, 56 + i * 2 + 2, 124 + i * 4, 48), 6, 6)
    board = QRectF(34, 52, 124, 46)
    p.setBrush(QColor("#15151a"))
    p.setPen(QPen(QColor(255, 255, 255, 46), 1))
    p.drawRoundedRect(board, 5, 5)
    p.setPen(QColor(255, 255, 255, 235))                 # only the episode number (or the first letter of a movie)
    p.setFont(ui_font(24 if len(tag) > 2 else 28, QFont.DemiBold))
    p.drawText(board, Qt.AlignCenter, tag)

    def bar(x, y, flip):
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#15151a"))
        p.drawRoundedRect(QRectF(x, y, 124, 14), 3, 3)
        p.save()
        p.setClipRect(QRectF(x, y, 124, 14))
        for i in range(7):
            q = QPolygonF([QPointF(x + 8 + i * 17, y), QPointF(x + 18 + i * 17, y),
                           QPointF(x + 10 + i * 17, y + 14), QPointF(x + i * 17, y + 14)])
            p.setBrush(lt if (i % 2) != flip else dk)
            p.drawPolygon(q)
        p.restore()
    bar(34, 38, 0)                                       # fixed bar on top of the board
    p.save()                                             # opened arm, hinged at the left end of the fixed bar
    p.translate(34, 38)
    p.rotate(-8)
    p.translate(-34, -38)
    bar(34, 24, 1)
    p.restore()
    p.restore()


class ThumbCard(QWidget):
    """One 'Recent media' tile: square thumbnail, two-line title, hover background and play button."""
    clicked = Signal(str)
    menuRequested = Signal(object, QPoint)     # (entry, global position): right-click or "..." button
    checkToggled = Signal(object)              # entry: the checkbox was used (starts selection mode)
    selectToggled = Signal(object)             # entry: card clicked while in selection mode

    TW, TH = 192, 108              # 16:9 thumbnail container
    PAD = 5
    CW = TW + 2 * PAD
    TITLE_H = 24                   # PHASE B: one line only (a name that does not fit is cut and shows a tooltip)

    STEP = 4                       # how far each stacked layer peeks out (px)
    selectable = True              # every card has the hover checkbox (stacks select all their videos)

    @staticmethod
    def _load(path):
        try:
            if path is not None and path.exists():
                pm = QPixmap(str(path))
                if not pm.isNull():
                    return pm
        except OSError:
            pass
        return None

    def __init__(self, entry, thumb_file, parent=None, stack_files=None):
        super().__init__(parent)
        self.e = entry
        self.pm = self._load(thumb_file)
        # a playlist shows up to 3 more thumbnails layered behind the front one (None = fallback look)
        self.layers = [self._load(f) for f in (stack_files or [])]
        self.hover = Tween(self, 0, 200, self.update)
        self.press = Tween(self, 0, 90, self.update)
        self.appear = Tween(self, 0, 420, self.update)
        self._rest = None                  # cached picture of the card at rest (see _rest_pixmap)
        self._rest_dpr = 0
        self._over = None                  # "check" | "more" | "play" while the mouse is on that button
        self.ctl = {k: Tween(self, 0, 160, self.update) for k in ("check", "play", "more")}   # per-button hover (0..1)
        self.sel_mode = False              # set by the Home page
        self.selected = False
        self.setFixedSize(self.CW, self.PAD + self.TH + 8 + self.TITLE_H + self.PAD)
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)
        # (no hover popup: the title and progress are already on the card)

    def appear_in(self, delay_ms):
        self.appear.set(0)
        t = QTimer(self)                 # child of the card: deleted together with it
        t.setSingleShot(True)
        t.timeout.connect(lambda: self.appear.to(1, 480))
        t.start(int(delay_ms))

    def _thumb_rect(self):
        fh = self.TH - self.STEP * len(self.layers)
        fw = fh * 16 / 9.0
        return QRectF(self.PAD + self.TW - fw, self.PAD + self.TH - fh, fw, fh)

    CTL = 22          # checkbox, play circle and "..." circle: one size
    CTL_GAP = 8       # gap from the picture edge, same on every side

    def _check_rect(self):
        t = self._thumb_rect()
        return QRectF(t.left() + self.CTL_GAP, t.top() + self.CTL_GAP, self.CTL, self.CTL)

    def _more_rect(self):
        t = self._thumb_rect()
        return QRectF(t.right() - self.CTL_GAP - self.CTL, t.bottom() - self.CTL_GAP - self.CTL, self.CTL, self.CTL)

    def _play_rect(self):
        t = self._thumb_rect()
        return QRectF(t.left() + self.CTL_GAP, t.bottom() - self.CTL_GAP - self.CTL, self.CTL, self.CTL)

    def _hit(self, pos):
        """Which small button is under `pos` (the checkbox also works in selection mode)."""
        shown = self.hover.value > 0.3
        if self.selectable and (shown or self.sel_mode) and self._check_rect().adjusted(-3, -3, 3, 3).contains(pos):
            return "check"
        if shown and self._more_rect().contains(pos):
            return "more"
        if shown and not self.e.get("group") and self._play_rect().contains(pos):      # a stack opens, it does not play
            return "play"
        return None

    def enterEvent(self, ev):
        self.hover.to(1, 200)

    def leaveEvent(self, ev):
        host = self.window()
        if hasattr(host, "tip_cancel"):
            host.tip_cancel(self)
        if getattr(self, "menu_open", False):
            return                                   # while its menu is open the card keeps its hover look
        self.hover.to(0, 260)
        self.press.to(0, 260, EASE_SOFT)
        self._set_over(None)

    def mousePressEvent(self, ev):
        host = self.window()
        if hasattr(host, "tip_cancel"):
            host.tip_cancel(self)
        if ev.button() == Qt.LeftButton:
            self.press.to(1, 90)

    def mouseMoveEvent(self, ev):
        self._set_over(self._hit(ev.position()))
        self._tip_check(ev.position())

    def _tip_check(self, pos):
        """A name that is cut off shows its full name in the tooltip while the mouse is on the name."""
        host = self.window()
        if not hasattr(host, "tip_request"):
            return
        y0 = self.PAD + self.TH + 8
        title = self.e.get("title") or display_name(self.e["path"])
        if pos.y() >= y0 and QFontMetrics(ui_font(14, QFont.DemiBold)).horizontalAdvance(title) > self.TW - 2:
            host.tip_request(self, title, QRect(self.mapToGlobal(QPoint(self.PAD, y0)), QSize(self.TW, 20)), False)
        else:
            host.tip_cancel(self)

    def _set_over(self, over):
        """The button under the mouse lights up and grows; the one it left fades back (each has its own tween)."""
        if over == self._over:
            return
        old, self._over = self._over, over
        if old in self.ctl:
            self.ctl[old].to(0, 160)
        if over in self.ctl:
            self.ctl[over].to(1, 160)
        self.update()

    def _open_menu(self, pos):
        self.menu_open = True
        self.hover.to(1, 120)
        try:
            self.menuRequested.emit(self.e, pos)      # returns when the menu has closed
        finally:
            try:
                self.menu_open = False
                if not self.underMouse():
                    self.hover.to(0, 260)
                    self._set_over(None)
                self.update()
            except RuntimeError:
                pass                                  # the card was rebuilt while the menu was open

    def contextMenuEvent(self, ev):
        self._open_menu(ev.globalPos())

    def mouseReleaseEvent(self, ev):
        self.press.to(0, 420, EASE_SPRING)
        if ev.button() != Qt.LeftButton or not self.rect().contains(ev.position().toPoint()):
            return
        hit = self._hit(ev.position())
        if hit == "check":
            self.checkToggled.emit(self.e)
        elif hit == "more":
            r = self._more_rect()
            self._open_menu(self.mapToGlobal(QPoint(int(r.right()), int(r.bottom()))))
        elif self.sel_mode and hit != "play":
            self.selectToggled.emit(self.e)
        else:
            self.clicked.emit(self.e["path"])

    @staticmethod
    def _contain(src, target):
        """Largest rectangle with the picture's own aspect ratio that fits inside `target`."""
        sr, tr = src.width() / src.height(), target.width() / target.height()
        if sr > tr:
            w, h = target.width(), target.width() / sr
        else:
            w, h = target.height() * sr, target.height()
        return QRectF(target.x() + (target.width() - w) / 2, target.y() + (target.height() - h) / 2, w, h)

    @staticmethod
    def _draw_fit(p, rect, pm):
        p.fillRect(rect, QColor(0, 0, 0))                # bars for pictures that are not 16:9
        p.drawPixmap(ThumbCard._contain(pm.size(), rect), pm, QRectF(pm.rect()))

    @staticmethod
    def _cover(src, target):
        pw, ph = src.width(), src.height()
        tr, pr = target.width() / target.height(), pw / ph
        if pr > tr:
            sw = ph * tr
            return QRectF((pw - sw) / 2, 0, sw, ph)
        sh = pw / tr
        return QRectF(0, (ph - sh) / 2, pw, sh)

    def _resting(self):
        return (self.hover.value <= 0.01 and self.press.value <= 0.01 and self.appear.value >= 0.999
                and not self.sel_mode and not self.selected and not getattr(self, "menu_open", False)
                and self._over is None and all(t.value <= 0.01 for t in self.ctl.values()))

    def _rest_pixmap(self):
        """The card at rest, drawn once. Resizing the window then only copies this ready picture
        instead of scaling the thumbnail, clipping and laying out text again for every card."""
        dpr = self.devicePixelRatioF()
        if self._rest is None or self._rest_dpr != dpr:
            pm = QPixmap(int(self.width() * dpr), int(self.height() * dpr))
            pm.setDevicePixelRatio(dpr)
            pm.fill(Qt.transparent)
            pp = QPainter(pm)
            self._paint_full(pp)
            pp.end()
            self._rest, self._rest_dpr = pm, dpr
        return self._rest

    def paintEvent(self, ev):
        p = QPainter(self)
        if self._resting():
            p.drawPixmap(0, 0, self._rest_pixmap())
            return
        self._paint_full(p)

    DIM = 0.85        # buttons while the mouse is on the card but not on them (was 0.4: too faded)
    GROW = 0.05       # a button grows 5 % when the mouse is on it

    def _ctl_draw(self, p, rect, opacity, c, draw):
        """Draws one button into its own small picture (so a faded button has no see-through overlaps), grown by
        GROW * c around its centre, then lays that picture on the card with `opacity`."""
        dpr = max(self.devicePixelRatioF(), p.device().devicePixelRatioF())      # as sharp as what is painted on
        org = rect.topLeft() - QPointF(4, 4)
        pm = QPixmap(int((rect.width() + 8) * dpr) + 1, int((rect.height() + 8) * dpr) + 1)
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.transparent)
        q = QPainter(pm)
        q.setRenderHint(QPainter.Antialiasing)
        q.translate(-org)
        ctr = rect.center()
        q.translate(ctr)
        q.scale(1 + self.GROW * c, 1 + self.GROW * c)
        q.translate(-ctr)
        draw(q)
        q.end()
        p.save()
        p.setOpacity(max(0.0, min(1.0, opacity)))
        p.drawPixmap(org, pm)
        p.restore()

    @staticmethod
    def _ctl_fill(rest, c):
        hot = (74, 74, 78, 242)
        return QColor(*[int(round(rest[i] + (hot[i] - rest[i]) * c)) for i in range(4)])

    def _paint_overlay(self, p, a, h):
        """Hover buttons (play, "...") and the checkbox. They show dim while the mouse is on the card and
        light up (brighter ring and fill, 5 % bigger) only while the mouse is on that button itself."""
        dim = lambda c: self.DIM + (1 - self.DIM) * c
        if h > 0.01:
            circle = self._play_rect()
            if not self.e.get("group"):
                c = self.ctl["play"].value

                def draw_play(q, circle=circle, c=c):
                    q.setPen(QPen(QColor(255, 255, 255, int(215 + 40 * c)), 1.2))      # same ring as the "..." circle and the checkbox
                    q.setBrush(self._ctl_fill((24, 24, 24, 215), c))
                    q.drawEllipse(circle.adjusted(0.6, 0.6, -0.6, -0.6))
                    paint_icon(q, "play", circle.center().x() + 0.4, circle.center().y(), 10, QColor(TEXT))
                self._ctl_draw(p, circle, a * h * dim(c), c, draw_play)
            m = self._more_rect()
            c = self.ctl["more"].value

            def draw_more(q, m=m, c=c):
                q.setPen(QPen(QColor(255, 255, 255, int(215 + 40 * c)), 1.2))
                q.setBrush(self._ctl_fill((0, 0, 0, 120), c))
                q.drawEllipse(m.adjusted(0.6, 0.6, -0.6, -0.6))
                q.setPen(Qt.NoPen)
                q.setBrush(QColor(255, 255, 255, 245))
                for dx in (-2.8, 0.0, 2.8):                                            # three small dots
                    q.drawEllipse(QPointF(m.center().x() + dx, m.center().y()), 1.1, 1.1)
            self._ctl_draw(p, m, a * h * dim(c), c, draw_more)
        if self.selectable and (h > 0.01 or self.sel_mode or self.selected):           # checkbox, top-left
            c = self.ctl["check"].value
            cb = self._check_rect()
            op = a if (self.sel_mode or self.selected) else a * h * dim(c)             # selection mode: always clearly visible

            def draw_check(q, cb=cb, c=c):
                if self.selected:
                    q.setPen(Qt.NoPen)
                    q.setBrush(QColor(ACCENT))
                    q.drawRoundedRect(cb, 4, 4)
                    q.setPen(QPen(QColor("#1b1b1b"), 2.0, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
                    q.setBrush(Qt.NoBrush)
                    q.drawPolyline(QPolygonF([QPointF(cb.left() + 5.5, cb.center().y() + 0.5),
                                              QPointF(cb.left() + 9.5, cb.center().y() + 4.2),
                                              QPointF(cb.right() - 5.0, cb.top() + 6.0)]))
                else:
                    q.setPen(QPen(QColor(255, 255, 255, int(215 + 40 * c)), 1.2))
                    q.setBrush(self._ctl_fill((20, 20, 20, 190), c))
                    q.drawRoundedRect(cb.adjusted(0.6, 0.6, -0.6, -0.6), 4, 4)
            self._ctl_draw(p, cb, op, c, draw_check)

    def _paint_full(self, p):
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        e = self.e
        h = max(0.0, min(1.0, self.hover.value))
        a = max(0.0, min(1.0, self.appear.value))
        p.setOpacity(a)
        p.translate(0, (1 - a) * 14)
        sc = 1.0 - 0.025 * self.press.value
        c = QRectF(self.rect()).center()
        p.translate(c)
        p.scale(sc, sc)
        p.translate(-c)
        if h > 0.01:                                         # lighter rounded tile behind the card
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, int(26 * h)))
            p.drawRoundedRect(QRectF(self.rect()), 8, 8)

        n = len(self.layers)
        fh = self.TH - self.STEP * n                         # front thumbnail keeps 16:9
        fw = fh * 16 / 9.0
        thumb = QRectF(self.PAD + self.TW - fw, self.PAD + self.TH - fh, fw, fh)
        for i in range(n, 0, -1):                            # layers behind it, farthest first
            lay = QRectF(thumb.x() - self.STEP * i, thumb.y() - self.STEP * i, thumb.width(), thumb.height())
            lp = QPainterPath()
            lp.addRoundedRect(lay, 6, 6)
            p.setClipPath(lp)
            pm = self.layers[i - 1]
            if pm:
                self._draw_fit(p, lay, pm)
            else:
                g = QLinearGradient(0, lay.top(), 0, lay.bottom())
                g.setColorAt(0, QColor("#34343a"))
                g.setColorAt(1, QColor("#1d1d21"))
                p.fillRect(lay, g)
            p.fillRect(lay, QColor(0, 0, 0, 45 + 28 * (i - 1)))           # deeper = darker
            p.setClipping(False)
            p.setPen(QPen(QColor(255, 255, 255, 46), 1))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(lay.adjusted(0.5, 0.5, -0.5, -0.5), 6, 6)
        clip = QPainterPath()
        clip.addRoundedRect(thumb, 6, 6)
        p.setClipPath(clip)
        if self.pm:
            self._draw_fit(p, thumb, self.pm)
        else:
            paint_clapper(p, thumb, e["path"])            # PHASE B: clapperboard placeholder (look I)
        dur, pos = e.get("duration", 0), e.get("position", 0)
        if dur > 0 and pos > 0 and not e.get("finished"):
            bar = QRectF(thumb.left(), thumb.bottom() - 4, thumb.width(), 4)
            p.fillRect(bar, QColor(255, 255, 255, 64))
            p.fillRect(QRectF(bar.left(), bar.top(), thumb.width() * min(1.0, pos / dur), 4), QColor(ACCENT))
        p.setClipping(False)

        self._paint_overlay(p, a, h)

        p.setPen(QColor(TEXT))
        p.setFont(ui_font(14, QFont.DemiBold))
        y0 = self.PAD + self.TH + 8
        for i, line in enumerate(wrap_lines(e.get("title") or display_name(e["path"]), p.fontMetrics(), self.TW - 2, 1)):
            p.drawText(QRectF(self.PAD + 1, y0 + i * 19, self.TW, 19), Qt.AlignVCenter | Qt.AlignLeft, line)


class DescRow(QWidget):
    """A menu row with an icon, a title and a grey description (like the Open file(s) dropdown)."""

    def __init__(self, title, desc, kind, action, parent=None):
        super().__init__(parent)
        self.title, self.desc, self.kind, self.action = title, desc, kind, action
        self._hover = False
        self.setMouseTracking(True)
        self.setMinimumSize(390, 56)
        self.setCursor(Qt.PointingHandCursor)

    def sizeHint(self):
        return QSize(390, 56)

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            menu = self.parentWidget()
            while menu is not None and not isinstance(menu, QMenu):
                menu = menu.parentWidget()
            if menu is not None:
                menu.close()
            self.action.trigger()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if self._hover:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 24))
            p.drawRoundedRect(QRectF(self.rect()).adjusted(2, 1, -2, -1), 6, 6)
        paint_icon(p, self.kind, 30, self.height() / 2, 18, QColor(TEXT), 0.0, 2, False, QColor("#2b2b2b"))
        p.setPen(QColor(TEXT))
        p.setFont(ui_font(15))
        p.drawText(QRectF(56, 8, self.width() - 64, 22), Qt.AlignVCenter | Qt.AlignLeft, self.title)
        p.setPen(QColor(MUTED))
        p.setFont(ui_font(12))
        p.drawText(QRectF(56, 30, self.width() - 64, 18), Qt.AlignVCenter | Qt.AlignLeft, self.desc)


class DescAction(QWidgetAction):
    def __init__(self, title, desc, kind, parent, slot):
        super().__init__(parent)
        self.title, self.desc, self.kind = title, desc, kind
        self.triggered.connect(lambda _=False: slot())

    def createWidget(self, parent):
        return DescRow(self.title, self.desc, self.kind, self, parent)


class SplitButton(QWidget):
    """'Open file(s)' with a drop-down arrow, like the Windows Media Player."""
    openRequested = Signal()
    openFolderRequested = Signal()

    W, H, ARROW_W = 150, 32, 34

    def __init__(self, parent=None, label="Open file(s)", items=None, accent=False):
        super().__init__(parent)
        self.accent = accent                          # orange fill with dark text (empty Home)
        self.label = label
        self.items = items or (("Open file(s)", "Browse for files to play"),
                               ("Open folder", "Choose a folder, and play all the media in that folder"))
        self.setFixedSize(self.W, self.H)
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)
        self.hov = {"main": Tween(self, 0, 140, self.update), "arrow": Tween(self, 0, 140, self.update)}

    def _zone(self, pos):
        return "arrow" if pos.x() >= self.W - self.ARROW_W else "main"

    def mouseMoveEvent(self, e):
        z = self._zone(e.position())
        for k, tw in self.hov.items():
            tw.to(1.0 if k == z else 0.0, 140)

    def leaveEvent(self, e):
        for tw in self.hov.values():
            tw.to(0.0, 200)

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        if self._zone(e.position()) == "main":
            self.openRequested.emit()
        else:
            self.show_menu()

    def show_menu(self):
        m = DarkMenu(parent=self.window())
        (t1, d1), (t2, d2) = self.items
        m.addAction(DescAction(t1, d1, "folder", m, self.openRequested.emit))
        m.addAction(DescAction(t2, d2, "folder", m, self.openFolderRequested.emit))
        m.ensurePolished()
        m.exec(self.mapToGlobal(QPoint(self.W - m.sizeHint().width(), self.H + 6)))

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(r, 6, 6)
        base = QColor("#ff8232") if self.accent else QColor("#33333a")      # measured on the Media Player button
        fg = QColor("#1a1a1a") if self.accent else QColor(TEXT)
        p.fillPath(path, base)
        p.save()
        p.setClipPath(path)
        for key, area in (("main", QRectF(0, 0, self.W - self.ARROW_W, self.H)),
                          ("arrow", QRectF(self.W - self.ARROW_W, 0, self.ARROW_W, self.H))):
            h = max(0.0, min(1.0, self.hov[key].value))
            if h > 0.01:
                p.fillRect(area, QColor(0, 0, 0, int(26 * h)) if self.accent else QColor(255, 255, 255, int(22 * h)))
        p.restore()
        if self.accent:
            p.setPen(QPen(QColor(0, 0, 0, 45), 1))
        else:
            p.setPen(QPen(QColor("#474750"), 1))
        p.setBrush(Qt.NoBrush)
        if not self.accent:
            p.drawRoundedRect(r, 6, 6)
        p.drawLine(QPointF(self.W - self.ARROW_W, 7), QPointF(self.W - self.ARROW_W, self.H - 7))
        cy = self.H / 2
        paint_icon(p, "folder", 20, cy, 15, fg, 0.0, 2, False, base)
        p.setPen(fg)
        p.setFont(ui_font(14))
        p.drawText(QRectF(36, 0, 100, self.H), Qt.AlignVCenter | Qt.AlignLeft, self.label)
        ax = self.W - self.ARROW_W / 2
        p.setPen(QPen(fg, 1.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawPolyline(_poly([(ax - 4, cy - 1.5), (ax, cy + 2.5), (ax + 4, cy - 1.5)]))


class SelBtn(QAbstractButton):
    """Button of the selection command bar: icon + text, optional accent fill.
    compact=True is the measured Play queue look (13 px side padding, 14 px icon, 10 px gap)."""
    ICON_PX = {"play_o": 22, "playnext": 19, "add": 17, "trash": 19, "more": 20, "rename": 20}

    def __init__(self, text, icon=None, accent=False, parent=None, compact=False, bh=34):
        super().__init__(parent)
        self.setText(text)
        self.icon_kind, self.accent, self.compact = icon, accent, compact
        self.setCursor(Qt.PointingHandCursor)
        fm = QFontMetrics(ui_font(14))
        tw = fm.horizontalAdvance(text) if text else 0
        if compact:
            w = 40 if (icon and not text) else 26 + tw + (24 if icon else 0)
            self.setFixedSize(w, bh)
        else:
            w = 24 + tw + (28 if icon and text else 0)
            self.setFixedSize(max(w, 40), 44)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        on = self.isEnabled()
        r = QRectF(self.rect()) if self.compact else QRectF(self.rect()).adjusted(0, 3, 0, -3)
        if self.accent:
            bg = QColor("#ff9a40") if (self.underMouse() and on) else QColor(ACCENT)
            if self.compact:
                bg = QColor(255, 130, 50)
        else:
            bg = QColor(255, 255, 255, (40 if self.underMouse() else 24) if on else 10)
            if self.compact:
                bg = QColor(255, 255, 255, (34 if self.underMouse() else 24) if on else 10)
        if self.isDown() and on:
            bg = bg.darker(115)
        p.setPen(Qt.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(r, 4, 4)
        col = QColor("#1b1b1b") if self.accent else QColor(TEXT)
        if not on:
            col.setAlpha(90)
        if self.compact:
            if self.icon_kind:
                cx = 20.0 if self.text() else r.center().x()
                paint_icon(p, self.icon_kind, cx, r.center().y(), self.ICON_PX.get(self.icon_kind, 20), col)
            if self.text():
                p.setPen(col)
                p.setFont(ui_font(14))
                x = 37.0 if self.icon_kind else 13.0
                p.drawText(QRectF(x, r.top(), self.width() - x - 6, r.height()), Qt.AlignVCenter | Qt.AlignLeft,
                           self.text())
            return
        x = 12.0
        if self.icon_kind:
            ic = 18 if self.text() else 20
            cx = x + ic / 2 if self.text() else r.center().x()
            paint_icon(p, self.icon_kind, cx, r.center().y(), ic, col)
            x += ic + 10
        if self.text():
            p.setPen(col)
            p.setFont(ui_font(14))
            p.drawText(QRectF(x, r.top(), self.width() - x - 8, r.height()), Qt.AlignVCenter | Qt.AlignLeft, self.text())


class SelectionBar(QWidget):
    """Command bar shown above the grid in selection mode (REF-22)."""
    H = 56
    COMPACT = False                      # True: the measured Play queue look (see QueueBar)
    NOUN = ("item", "items")
    clearRequested = Signal()
    allToggled = Signal()
    playRequested = Signal()
    playNextRequested = Signal()
    addRequested = Signal(QPoint)
    moreRequested = Signal(QPoint)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.n = 0
        self.all_on = False
        self.setFixedHeight(self.H)
        self.btn_play = SelBtn("Play", "play_o", True, self, self.COMPACT)
        self.btn_next = SelBtn("Play next", "playnext", False, self, self.COMPACT)
        self.btn_add = SelBtn("Add to", "add", False, self, self.COMPACT)
        self.btn_more = SelBtn("", "more", False, self, self.COMPACT)
        self.btn_play.clicked.connect(self.playRequested)
        self.btn_next.clicked.connect(self.playNextRequested)
        self.btn_add.clicked.connect(lambda: self.addRequested.emit(
            self.btn_add.mapToGlobal(QPoint(0, self.btn_add.height() - 2))))
        self.btn_more.clicked.connect(lambda: self.moreRequested.emit(
            self.btn_more.mapToGlobal(QPoint(self.btn_more.width(), self.btn_more.height() - 2))))
        self._clear_rect = QRectF()
        self._over_clear = False
        self.setMouseTracking(True)

    def set_count(self, n, all_on):
        self.n, self.all_on = n, all_on
        self.update()

    def _layout(self):
        pad, gap = (15, 16) if self.COMPACT else (12, 8)
        x = self.width() - pad
        for b in (self.btn_more, self.btn_add, self.btn_next, self.btn_play):
            x -= b.width()
            b.move(x, (self.height() - b.height()) // 2)
            x -= gap if b is not self.btn_play else 0

    def resizeEvent(self, e):
        self._layout()

    def _box(self):
        if self.COMPACT:
            return QRectF(16, (self.height() - 20) / 2, 20, 20)
        return QRectF(18, (self.height() - 24) / 2, 24, 24)

    def mouseMoveEvent(self, ev):
        over = self._clear_rect.contains(ev.position())
        if over != self._over_clear:
            self._over_clear = over
            self.update()
        self.setCursor(Qt.PointingHandCursor if over or self._box().contains(ev.position()) else Qt.ArrowCursor)

    def mouseReleaseEvent(self, ev):
        if ev.button() != Qt.LeftButton:
            return
        if self._clear_rect.contains(ev.position()):
            self.clearRequested.emit()
        elif self._box().adjusted(-4, -4, 4, 4).contains(ev.position()):
            self.allToggled.emit()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor(255, 255, 255, 22), 1))
        p.setBrush(QColor("#343137") if self.COMPACT else QColor("#323034"))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 8, 8)
        b = self._box()
        k = b.width() / 24.0
        if self.all_on:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(ACCENT))
            p.drawRoundedRect(b, 4, 4)
            p.setPen(QPen(QColor("#1b1b1b"), 2.0, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.setBrush(Qt.NoBrush)
            p.drawPolyline(QPolygonF([QPointF(b.left() + 6 * k, b.center().y() + 0.5),
                                      QPointF(b.left() + 10.5 * k, b.center().y() + 4.5 * k),
                                      QPointF(b.right() - 5.5 * k, b.top() + 6.5 * k)]))
        else:
            p.setPen(QPen(QColor(255, 255, 255, 170), 1.4))
            p.setBrush(QColor(20, 20, 20, 120))
            p.drawRoundedRect(b.adjusted(0.7, 0.7, -0.7, -0.7), 4, 4)
        one, many = self.NOUN
        txt = f"{self.n} {one} selected" if self.n == 1 else f"{self.n} {many} selected"
        p.setFont(ui_font(14 if self.COMPACT else 15))
        p.setPen(QColor(TEXT))
        fm = p.fontMetrics()
        g0, g1, g2 = (8, 8, 14) if self.COMPACT else (12, 12, 20)
        x = b.right() + g0
        tw = fm.horizontalAdvance(txt)
        p.drawText(QRectF(x, 0, tw + 4, self.height()), Qt.AlignVCenter | Qt.AlignLeft, txt)
        x += tw + g1
        p.setPen(QColor(MUTED))
        p.drawText(QRectF(x, 0, 12, self.height()), Qt.AlignVCenter | Qt.AlignLeft, "\u2022")
        x += g2
        cw = fm.horizontalAdvance("Clear")
        self._clear_rect = QRectF(x - 4, 10, cw + 8, self.height() - 20)
        p.setPen(QColor("#ffa65c") if self._over_clear else QColor(ACCENT))
        p.drawText(QRectF(x, 0, cw + 4, self.height()), Qt.AlignVCenter | Qt.AlignLeft, "Clear")


class CaptionRow(QWidget):
    """Caption of an opened stack: the stack name and a collapse icon button (white corner brackets)."""
    collapseRequested = Signal()
    H = 32
    BTN = 32

    def __init__(self, name, parent=None):
        super().__init__(parent)
        self.name = name
        self.op = 1.0                          # opacity, set by the board while the row fades in or out
        self._hover = False
        self._font = ui_font(14, QFont.DemiBold)
        tw = QFontMetrics(self._font).horizontalAdvance(name)
        self._bx = ThumbCard.PAD + 1 + tw + 6
        self.setFixedSize(int(self._bx + self.BTN), self.H)
        self.setMouseTracking(True)

    def _btn(self):
        return QRectF(self._bx, 0, self.BTN, self.H)

    def mouseMoveEvent(self, ev):
        on = self._btn().contains(ev.position())
        if on != self._hover:
            self._hover = on
            self.setCursor(Qt.PointingHandCursor if on else Qt.ArrowCursor)
            self.update()

    def leaveEvent(self, ev):
        self._hover = False
        self.update()

    def mouseReleaseEvent(self, ev):
        if ev.button() == Qt.LeftButton and self._btn().contains(ev.position()):
            self.collapseRequested.emit()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setOpacity(self.op)
        p.setPen(QColor(TEXT))
        p.setFont(self._font)
        p.drawText(QRectF(ThumbCard.PAD + 1, 0, self._bx, self.H), Qt.AlignVCenter | Qt.AlignLeft, self.name)
        b = self._btn()
        if self._hover:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 26))
            p.drawRoundedRect(b, 4, 4)
        paint_icon(p, "collapse", b.center().x(), b.center().y(), 18, QColor(TEXT), sw=2.2 / 1.9)


class FolderBoard(QWidget):
    """The folder sections at the top of Home: a heading with the real folder name, then stacks (shows with 2 or
    more episodes) and single cards. A click on a stack opens it in place: a caption row (name and collapse icon),
    the episodes under it, and everything after it moves along with an ease (nothing jumps). Collapse reverses it.
    The cards are placed by hand like in HomePage._reflow, so the animation only interpolates positions."""
    playRequested = Signal(str)
    cardMenuRequested = Signal(object, QPoint)
    headMenuRequested = Signal(str, QPoint)
    checkToggled = Signal(object)
    selectToggled = Signal(object)
    layoutChanged = Signal()                 # a stack opened or closed

    GAP, VGAP = 0, 9
    HEAD_H, HEAD_GAP, SEC_GAP = 28, 12, 28
    MS = 360

    def __init__(self, store, stack_files, parent=None):
        super().__init__(parent)
        self.store = store
        self._stack_files = stack_files
        self.sections = []
        self.cols = 1
        self.sel_fn = None                   # entry -> (selection mode, selected): set by the Home page
        self.open = set()                    # (section index, stack key) of the opened stacks
        self.w = {}                          # plan key -> widget
        self._moves = {}                     # widget -> (start QPoint, end QPoint, fade): while animating
        self._leaving = []
        self._h0 = self._h1 = 0
        self.setStyleSheet("background:transparent;")
        self.anim = QVariantAnimation(self)
        self.anim.setDuration(self.MS)
        self.anim.setEasingCurve(EASE_OUT)
        self.anim.setStartValue(0.0)
        self.anim.setEndValue(1.0)
        self.anim.valueChanged.connect(self._tick)
        self.anim.finished.connect(self._finish)
        self.setFixedSize(0, 0)

    # --- plan: where everything goes for the current sections, open stacks and column count
    def _plan(self, opened=None):
        opened = self.open if opened is None else opened
        cw, ch = ThumbCard.CW, ThumbCard.PAD + ThumbCard.TH + 8 + ThumbCard.TITLE_H + ThumbCard.PAD
        pos, items, y = {}, [], 0
        for si, sec in enumerate(self.sections):
            pos[("head", si)] = QPoint(0, y)
            y += self.HEAD_H + self.HEAD_GAP
            col = 0
            for it in sec["items"]:
                is_open = it["kind"] == "stack" and (si, it["key"]) in opened
                if is_open:
                    if col:
                        y, col = y + ch + self.VGAP, 0
                    pos[("cap", si, it["key"])] = QPoint(0, y)
                    y += CaptionRow.H + self.VGAP
                    cards = [(("card", si, Store._key(ep["path"])), ep) for ep in it["eps"]]
                else:
                    key = (("stack", si, it["key"]) if it["kind"] == "stack"
                           else ("card", si, Store._key(it["e"]["path"])))
                    cards = [(key, it["e"])]
                for key, e in cards:
                    pos[key] = QPoint(col * (cw + self.GAP), y)
                    items.append((key, e))
                    col += 1
                    if col >= self.cols:
                        y, col = y + ch + self.VGAP, 0
                if is_open and col:                  # an open stack owns its rows: what follows starts a new row
                    y, col = y + ch + self.VGAP, 0
            if col:
                y += ch + self.VGAP
            y -= self.VGAP
            y += self.SEC_GAP
        width = self.cols * cw + (self.cols - 1) * self.GAP if self.sections else 0
        return pos, items, (y if self.sections else 0), width

    def _make(self, key, e, si):
        kind = key[0]
        if kind == "head":
            w = HeadRow(self.sections[si]["folder"], self)
            w.menuRequested.connect(self.headMenuRequested)
        elif kind == "cap":
            sec = self._item(si, key[2])
            w = CaptionRow(sec["e"].get("title") or "", self)
            w.collapseRequested.connect(lambda s=si, k=key[2]: self.toggle(s, k))
        else:
            w = ThumbCard(e, self.store.thumb_path(e["path"], e["size"]), self,
                          stack_files=self._stack_files(e) if kind == "stack" else None)
            w.clicked.connect(lambda _p, s=si, k=key: self._clicked(s, k, e))
            w.menuRequested.connect(self.cardMenuRequested)
            w.checkToggled.connect(self.checkToggled)
            w.selectToggled.connect(self.selectToggled)
            if self.sel_fn:
                w.sel_mode, w.selected = self.sel_fn(e)
        w.show()
        return w

    def _item(self, si, skey):
        return next(i for i in self.sections[si]["items"] if i["kind"] == "stack" and i["key"] == skey)

    def _clicked(self, si, key, e):
        if key[0] == "stack":
            self.toggle(si, key[2])
        else:
            self.playRequested.emit(e["path"])

    def _fit_heading(self, w, si, width):
        w.set_name(self.sections[si]["name"], width)

    # --- building (no animation of positions: used after a refresh, a resize and at start)
    def set_sections(self, sections, cols, animate=False):
        self._finish()
        self.sections, self.cols = sections, max(1, cols)
        live = {(si, it["key"]) for si, s in enumerate(sections) for it in s["items"] if it["kind"] == "stack"}
        self.open = {k for k in self.open if k in live}
        for w in self.w.values():
            w.hide()
            w.deleteLater()
        self.w = {}
        pos, items, h, width = self._plan()
        for si in range(len(sections)):
            self.w[("head", si)] = self._make(("head", si), None, si)
        for key, e in items:
            self.w[key] = self._make(key, e, si=key[1])
        for si, sec in enumerate(sections):
            for it in sec["items"]:
                if it["kind"] == "stack" and (si, it["key"]) in self.open:
                    self.w[("cap", si, it["key"])] = self._make(("cap", si, it["key"]), None, si)
        self._place(pos, width, h)
        n = 0
        for key, w in self.w.items():
            if isinstance(w, ThumbCard):
                if animate and n < 24:
                    w.appear_in(60 + n * 60)
                else:
                    w.appear.set(1)
                n += 1

    def set_cols(self, cols):
        cols = max(1, cols)
        if cols != self.cols and self.sections:
            self._finish()
            self.cols = cols
            pos, _items, h, width = self._plan()
            self._place(pos, width, h)
        else:
            self.cols = cols

    def _place(self, pos, width, h):
        for key, w in self.w.items():
            if key[0] == "head":
                self._fit_heading(w, key[1], width)
            w.move(pos[key])
        self.setFixedSize(width, max(0, h))

    # --- opening and closing a stack, animated
    def toggle(self, si, skey):
        self._finish()
        k = (si, skey)
        opening = k not in self.open
        new_open = (self.open | {k}) if opening else (self.open - {k})
        anchor = self._plan(self.open - {k})[0][("stack", si, skey)]     # where the closed stack sits
        old_pos = {key: w.pos() for key, w in self.w.items()}
        self._h0 = self.height()
        self.open = new_open
        pos, items, h, width = self._plan()
        self._h1 = h
        evt = dict(items)
        self._moves, self._leaving = {}, []
        for key in list(self.w):                                          # widgets that go away
            if key not in pos:
                w = self.w.pop(key)
                end = old_pos[key] if key[0] == "stack" else anchor
                self._moves[w] = (old_pos[key], end, "out")
                self._leaving.append(w)
        for key in pos:                                                   # widgets that stay or arrive
            if key in self.w:
                self._moves[self.w[key]] = (old_pos[key], pos[key], None)
                if key[0] == "head":
                    self._fit_heading(self.w[key], key[1], width)
            else:
                w = self.w[key] = self._make(key, evt.get(key), key[1])
                start = pos[key] if key[0] == "stack" else anchor
                if isinstance(w, ThumbCard):
                    w.appear.set(0)
                else:
                    w.op = 0.0
                w.move(start)
                w.raise_()
                self._moves[w] = (start, pos[key], "in")
        self.setFixedWidth(width)
        self.anim.start()
        self.layoutChanged.emit()

    def _tick(self, t):
        t = float(t)
        for w, (a, b, fade) in list(self._moves.items()):
            w.move(int(round(a.x() + (b.x() - a.x()) * t)), int(round(a.y() + (b.y() - a.y()) * t)))
            if fade:
                v = t if fade == "in" else 1.0 - t
                if isinstance(w, ThumbCard):
                    w.appear.set(v)
                else:
                    w.op = v
                    w.update()
        self.setFixedHeight(int(round(self._h0 + (self._h1 - self._h0) * t)))

    def _finish(self):
        """Jump to the end of a running animation (also used before the next change)."""
        if not self._moves and self.anim.state() != QVariantAnimation.Running:
            return
        self.anim.blockSignals(True)
        self.anim.stop()
        self.anim.blockSignals(False)
        for w, (a, b, fade) in self._moves.items():
            w.move(b)
            if fade == "in":
                if isinstance(w, ThumbCard):
                    w.appear.set(1)
                else:
                    w.op = 1.0
                    w.update()
        for w in self._leaving:
            w.hide()
            w.deleteLater()
        self._moves, self._leaving = {}, []
        self.setFixedHeight(int(self._h1))


class HomeSortButton(QAbstractButton):
    """'Sort by: A to Z' with a small arrow: up = A to Z and oldest first, down = Z to A and newest first
    (the File Explorer way). The words say it too, so the arrow is never the only hint."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.cur = {"key": "name", "dir": "asc"}
        self.setCursor(Qt.PointingHandCursor)
        fm = QFontMetrics(ui_font(14))
        w = max(fm.horizontalAdvance(t) for t in HOME_SORT_TEXT.values())
        self.setFixedSize(10 + fm.horizontalAdvance("Sort by: ") + w + 8 + 9 + 12 + 9 + 10, 32)

    def set_sort(self, cur):
        self.cur = dict(cur)
        self.update()

    def value_text(self):
        return HOME_SORT_TEXT[(self.cur["key"], self.cur["dir"])]

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if self.underMouse():
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 16))
            p.drawRoundedRect(QRectF(self.rect()), 4, 4)
        p.setFont(ui_font(14))
        fm = p.fontMetrics()
        x = 10.0
        p.setPen(QColor(TEXT))
        t1 = "Sort by: "
        p.drawText(QRectF(x, 0, fm.horizontalAdvance(t1) + 2, self.height()), Qt.AlignVCenter | Qt.AlignLeft, t1)
        x += fm.horizontalAdvance(t1)
        p.setPen(QColor(ACCENT))
        t2 = self.value_text()
        p.drawText(QRectF(x, 0, fm.horizontalAdvance(t2) + 2, self.height()), Qt.AlignVCenter | Qt.AlignLeft, t2)
        x += fm.horizontalAdvance(t2) + 8
        cy = self.height() / 2.0
        up = self.cur["dir"] == "asc"
        tip, base, d = (cy - 5, cy + 5, 1) if up else (cy + 5, cy - 5, -1)
        p.setPen(QPen(QColor(TEXT), 1.3, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawLine(QPointF(x + 4.5, base), QPointF(x + 4.5, tip))
        p.drawPolyline(QPolygonF([QPointF(x + 1, tip + 3.5 * d), QPointF(x + 4.5, tip), QPointF(x + 8, tip + 3.5 * d)]))
        x += 9 + 12
        cy += 0.5
        p.drawPolyline(QPolygonF([QPointF(x, cy - 2.5), QPointF(x + 4.5, cy + 2.0), QPointF(x + 9, cy - 2.5)]))


class EmptyHome(QWidget):
    """Home with no saved folder and no Recent media, like the Media Player's first screen: a round picture, a
    heading, one line of text and the orange Open button. The picture is our own (play ring colours)."""
    openRequested = Signal()
    openFolderRequested = Signal()
    PIC, GAP = 262, 34

    def __init__(self, parent=None):
        super().__init__(parent)
        self.title = QLabel("Meet Simple Player", self)
        self.title.setStyleSheet(DISPLAY_QSS + "color:#fff;font-size:28px;font-weight:600;background:transparent;")
        self.body = QLabel("Use this app to play your video files.", self)
        self.body.setStyleSheet("color:#aba8a9;font-size:14px;background:transparent;")
        self.btn = SplitButton(self, accent=True)
        self.btn.openRequested.connect(self.openRequested)
        self.btn.openFolderRequested.connect(self.openFolderRequested)
        self._pic = QRectF()
        self._layout()

    def _layout(self):
        tw = max(self.title.sizeHint().width(), self.body.sizeHint().width(), SplitButton.W)
        x0 = max(0, (self.width() - (self.PIC + self.GAP + tw)) // 2)
        cy = self.height() // 2
        self._pic = QRectF(x0, cy - self.PIC / 2.0, self.PIC, self.PIC)
        th, bh = self.title.sizeHint().height(), self.body.sizeHint().height()
        y = cy - (th + 4 + bh + 12 + SplitButton.H) // 2
        x = x0 + self.PIC + self.GAP
        self.title.setGeometry(x, y, tw, th)
        self.body.setGeometry(x, y + th + 4, tw, bh)
        self.btn.move(x, y + th + 4 + bh + 12)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._layout()
        self.update()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self._pic
        g = QLinearGradient(r.left(), 0, r.right(), 0)            # purple to orange, sampled from the Media Player picture
        for pos, rgb in ((0.0, (127, 44, 135)), (0.35, (179, 49, 139)), (0.6, (216, 78, 112)),
                         (0.85, (232, 106, 59)), (1.0, (240, 130, 40))):
            g.setColorAt(pos, QColor(*rgb))
        path = QPainterPath()
        path.addEllipse(r)
        p.fillPath(path, g)
        p.setPen(QPen(QColor("#393538"), 1.5))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(r)
        c = r.center()                                            # white play triangle with rounded corners
        p.setPen(QPen(QColor(255, 255, 255), 10, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(QColor(255, 255, 255))
        p.drawPolygon(QPolygonF([QPointF(c.x() - 20, c.y() - 34), QPointF(c.x() - 20, c.y() + 34), QPointF(c.x() + 40, c.y())]))


class HeadRow(QWidget):
    """Heading of a folder section: the folder name and a three-dot button that opens its menu."""
    menuRequested = Signal(str, QPoint)              # (folder, global position)
    H, DOT = 28, 28

    def __init__(self, folder, parent=None):
        super().__init__(parent)
        self.folder = folder
        self._text = ""
        self._dots = QRectF()
        self._over = False
        self._menu = False
        self.setFixedHeight(self.H)
        self.setMouseTracking(True)

    def text(self):
        return self._text

    def set_name(self, name, width):
        self.setFixedWidth(max(60, int(width)))
        fm = QFontMetrics(ui_font(20, QFont.DemiBold))
        self._text = fm.elidedText(name, Qt.ElideRight, max(40, self.width() - ThumbCard.PAD - 6 - self.DOT))
        self._dots = QRectF(ThumbCard.PAD + fm.horizontalAdvance(self._text) + 6, 0, self.DOT, self.H)
        self.update()

    def _open(self, global_pos):
        self._menu = True
        self.update()
        try:
            self.menuRequested.emit(self.folder, global_pos)       # returns when the menu has closed
        finally:
            try:
                self._menu = False
                self.update()
            except RuntimeError:
                pass                                                # rebuilt while the menu was open

    def mouseMoveEvent(self, ev):
        over = self._dots.contains(ev.position())
        if over != self._over:
            self._over = over
            self.setCursor(Qt.PointingHandCursor if over else Qt.ArrowCursor)
            self.update()

    def leaveEvent(self, ev):
        self._over = False
        self.update()

    def mouseReleaseEvent(self, ev):
        if ev.button() == Qt.LeftButton and self._dots.contains(ev.position()):
            self._open(self.mapToGlobal(QPoint(int(self._dots.left()), self.H + 2)))

    def contextMenuEvent(self, ev):
        self._open(ev.globalPos())

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QColor("#ffffff"))
        p.setFont(ui_font(20, QFont.DemiBold))
        tw = p.fontMetrics().horizontalAdvance(self._text)
        p.drawText(QRectF(ThumbCard.PAD, 0, tw + 4, self.H), Qt.AlignVCenter | Qt.AlignLeft, self._text)
        on = self._over or self._menu
        d = self._dots
        if on:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 24))
            p.drawRoundedRect(d.adjusted(2, 2, -2, -2), 6, 6)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 255 if on else 150))
        for dx in (-5.0, 0.0, 5.0):
            p.drawEllipse(QPointF(d.center().x() + dx, d.center().y()), 1.5, 1.5)


class HomePage(QWidget):
    openRequested = Signal()
    openFolderRequested = Signal()
    playRequested = Signal(str)
    cardMenuRequested = Signal(object, QPoint)
    selectionChanged = Signal()
    folderMenuRequested = Signal(str, QPoint)
    sortChosen = Signal(str)

    GAP = 0
    SIDE = 56
    recent_off = False

    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store
        self.cards = []
        self._cols = 0
        self.sel_mode = False
        self.sel = {}                      # selection key -> entry, for the selected cards
        self._sec_on = self._sub_on = False    # "Recent media" heading and its line
        self.is_empty = False
        self.sort_cur = {"key": "name", "dir": "asc"}
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.viewport().setAutoFillBackground(False)
        self.scroll.setStyleSheet(
            "QScrollArea{background:transparent;border:none;}"
            "QScrollBar:vertical{background:transparent;width:10px;margin:2px;}"
            "QScrollBar::handle:vertical{background:#4a4448;border-radius:4px;min-height:30px;}"
            "QScrollBar::handle:vertical:hover{background:#625b60;}"
            "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;}"
            "QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical{background:transparent;}")
        outer.addWidget(self.scroll)
        self.body = QWidget()
        self.body.setStyleSheet("background:transparent;")
        self.scroll.setWidget(self.body)
        v = QVBoxLayout(self.body)
        v.setContentsMargins(self.SIDE - ThumbCard.PAD, 14, self.SIDE - ThumbCard.PAD, 40)
        v.setSpacing(0)

        head = QHBoxLayout()
        head.setContentsMargins(ThumbCard.PAD, 0, ThumbCard.PAD, 0)
        self.heading = QLabel("Home")
        self.heading.setStyleSheet(DISPLAY_QSS + "color:#fff;font-size:38px;font-weight:600;")
        self.split = SplitButton()
        self.split.openRequested.connect(self.openRequested)
        self.split.openFolderRequested.connect(self.openFolderRequested)
        self.sort_btn = HomeSortButton()
        self.sort_btn.clicked.connect(self._sort_menu)
        head.addWidget(self.heading)
        head.addStretch(1)
        head.addWidget(self.sort_btn, 0, Qt.AlignVCenter)
        head.addSpacing(8)
        head.addWidget(self.split, 0, Qt.AlignVCenter)
        v.addLayout(head)
        v.addSpacing(20)

        self.bar_slot = QWidget()              # room for the selection bar (height 0 outside selection mode)
        self.bar_slot.setFixedHeight(0)
        v.addWidget(self.bar_slot)
        self.board = FolderBoard(store, self._stack_files)      # folder sections, above "Recent media"
        self.board.sel_fn = lambda e: (self.sel_mode, self._selkey(e) in self.sel)
        self.board.playRequested.connect(self.playRequested)
        self.board.cardMenuRequested.connect(self.cardMenuRequested)
        self.board.headMenuRequested.connect(self.folderMenuRequested)
        self.board.checkToggled.connect(self.toggle_entry)
        self.board.selectToggled.connect(self.toggle_entry)
        self.board.layoutChanged.connect(self._on_board_layout)
        v.addWidget(self.board, 0, Qt.AlignLeft | Qt.AlignTop)

        self.section = QLabel("Recent media")
        self.section.setStyleSheet(SEMI_QSS + "color:#fff;font-size:20px;font-weight:600;")
        self.section.setContentsMargins(ThumbCard.PAD, 0, 0, 0)
        v.addWidget(self.section)
        self.sub = QLabel()
        self.sub.setStyleSheet(f"color:{MUTED};font-size:14px;")
        self.sub.setContentsMargins(ThumbCard.PAD, 0, 0, 0)
        v.addSpacing(6)
        v.addWidget(self.sub)
        v.addSpacing(12)

        self.grid_host = QWidget()                 # cards are placed by hand (see _reflow): a QGridLayout
        self.grid_host.setFixedSize(0, 0)          # re-spread them for one frame while the window was resizing
        v.addWidget(self.grid_host, 0, Qt.AlignLeft | Qt.AlignTop)
        v.addStretch(1)

        self.empty = EmptyHome(self)           # first screen: no saved folder and no Recent media
        self.empty.openRequested.connect(self.openRequested)
        self.empty.openFolderRequested.connect(self.openFolderRequested)
        self.empty.hide()
        self.selbar = SelectionBar(self)       # floats above the page, in the slot above the folder sections
        self.selbar.hide()
        self.selbar.clearRequested.connect(self.clear_selection)
        self.selbar.allToggled.connect(self.toggle_all)
        self.scroll.verticalScrollBar().rangeChanged.connect(lambda *_: self._place_bar())
        self.scroll.verticalScrollBar().valueChanged.connect(lambda *_: self._place_bar())

    def reload_pictures(self, only_key=None):
        """Read the card pictures from the cache again (a new or changed picture)."""
        for c in self.all_cards():
            if only_key is None or Store._key(c.e["path"]) == only_key:
                c.pm = ThumbCard._load(self.store.thumb_path(c.e["path"], c.e["size"]))
                c._rest = None
                c.update()

    def card_rects_on_screen(self):
        """Cards that are inside the visible part of the page, top to bottom, left to right."""
        vp = self.scroll.viewport()
        out = []
        for c in self.all_cards():
            try:
                if not c.isVisibleTo(self):
                    continue
                pt = c.mapTo(vp, QPoint(0, 0))
            except RuntimeError:
                continue
            if QRect(pt, c.size()).intersects(vp.rect()):
                out.append((pt.y(), pt.x(), c))
        out.sort(key=lambda t: (t[0], t[1]))
        return [c for _y, _x, c in out]

    @staticmethod
    def _selkey(e):
        """Selection key of a card: a stack stands for all its videos, a video is its path key."""
        grp = e.get("group")
        if grp and len(grp) >= 2:
            return "grp:" + Store._key(grp[0]) + ":" + str(len(grp))
        return Store._key(e["path"])

    def _visible_keys(self):
        return {self._selkey(c.e) for c in self.all_cards()}

    def set_sort(self, cur):
        self.sort_cur = dict(cur)
        self.sort_btn.set_sort(cur)

    def _sort_menu(self):
        m = DarkMenu(self)
        for key, label in HOME_SORT_LABELS.items():
            arrow = ""
            if self.sort_cur.get("key") == key:
                arrow = "\u2191" if self.sort_cur.get("dir") == "asc" else "\u2193"
            m.item(label, lambda k=key: self.sortChosen.emit(k), None, shortcut=arrow)
        m.ensurePolished()
        m.exec(self.sort_btn.mapToGlobal(QPoint(self.sort_btn.width() - m.sizeHint().width(), self.sort_btn.height() + 2)))

    def refresh(self, entries, animate=True, searching=False):
        for c in self.cards:
            c.hide()
            c.deleteLater()
        self.cards = []
        empty = not entries and not searching and not self.board.sections and not self.recent_off
        self.is_empty = empty
        if entries:
            self.section.setText("Recent media")
            self._sec_on, self._sub_on = True, False
        elif searching:
            self.section.setText("No matches")
            self.sub.setText("Try a different search.")
            self._sec_on, self._sub_on = True, True
        elif self.recent_off:
            self.section.setText("Recent media")
            self.sub.setText("Recent media is turned off. You can turn it on in Settings.")
            self._sec_on, self._sub_on = True, True
        else:                                          # nothing to show: no heading and no "Nothing here yet" line
            self._sec_on, self._sub_on = False, False
        for e in entries:
            card = ThumbCard(e, self.store.thumb_path(e["path"], e["size"]),
                             stack_files=self._stack_files(e))
            card.clicked.connect(self.playRequested)
            card.menuRequested.connect(self.cardMenuRequested)
            card.checkToggled.connect(self._check_used)
            card.selectToggled.connect(self.toggle_entry)
            self.cards.append(card)
        self._cols = 0
        self._reflow()
        shown = self._visible_keys()
        self.sel = {k: v for k, v in self.sel.items() if k in shown}     # cards that are gone drop out
        if self.sel_mode and not self.sel:
            self.sel_mode = False
        self.empty.setVisible(empty)
        self.sort_btn.setVisible(not empty)
        self.split.setVisible(not empty)
        self._place_empty()
        self._sync_cards()
        for i, c in enumerate(self.cards):
            if animate:
                c.appear_in(60 + i * 60)
            else:
                c.appear.set(1)

    def hide_recent_heading(self):
        self._sec_on = self._sub_on = False
        self._update_section_vis()

    def _update_section_vis(self):
        # in selection mode the bar sits above the folder sections; with no sections it takes the heading's place
        self.section.setVisible(self._sec_on and not (self.sel_mode and not self.board.sections))
        self.sub.setVisible(self._sub_on)

    def _place_empty(self):
        y0 = max(84, self.board.mapTo(self, QPoint(0, 0)).y())
        self.empty.setGeometry(0, y0, self.width(), max(120, self.height() - y0 - 20))
        self.empty.raise_()

    # --- selection mode (D4)
    def _sync_cards(self):
        for c in self.all_cards():
            c.sel_mode = self.sel_mode
            c.selected = self._selkey(c.e) in self.sel
            c.update()
        self._update_section_vis()
        self.bar_slot.setFixedHeight(SelectionBar.H + 8 if self.sel_mode else 0)
        keys = self._visible_keys()
        n = len(self.sel)
        self.selbar.set_count(n, bool(keys) and keys <= set(self.sel))
        self.selbar.setVisible(self.sel_mode)
        self.body.layout().activate()
        self._place_bar()
        QTimer.singleShot(0, self._place_bar)
        self.selectionChanged.emit()

    def _on_board_layout(self):
        """A stack opened or closed. A selected stack that opened becomes its episodes; when every episode of a
        stack is selected and it closes, the stack is selected; other cards that went away drop out."""
        if not self.sel:
            return
        shown = self._visible_keys()
        for sec in self.board.sections:
            for it in sec["items"]:
                if it["kind"] != "stack":
                    continue
                sk = self._selkey(it["e"])
                eks = [self._selkey(ep) for ep in it["eps"]]
                if sk in self.sel and sk not in shown:
                    self.sel.pop(sk)
                    for ep in it["eps"]:
                        self.sel[self._selkey(ep)] = ep
                elif sk in shown and eks and all(k in self.sel and k not in shown for k in eks):
                    for k in eks:
                        self.sel.pop(k)
                    self.sel[sk] = it["e"]
        self.sel = {k: v for k, v in self.sel.items() if k in shown}
        if not self.sel:
            self.sel_mode = False
        self._sync_cards()

    def _place_bar(self):
        vw = self.scroll.viewport().width()
        y = max(58, self.bar_slot.mapTo(self, QPoint(0, 0)).y())        # sits above the folder sections, stays visible when scrolling
        self.selbar.setGeometry(self.SIDE, y, max(200, vw - 2 * self.SIDE), SelectionBar.H)
        self.selbar.raise_()

    def _ordered_cards(self):
        def pos(c):
            p = c.mapTo(self.body, QPoint(0, 0))
            return (p.y(), p.x())
        return sorted(self.all_cards(), key=pos)

    def selected_entries(self):
        """Selected cards in the order they appear on screen (a stack is one entry)."""
        out, seen = [], set()
        for c in self._ordered_cards():
            k = self._selkey(c.e)
            if k in self.sel and k not in seen:
                seen.add(k)
                out.append(c.e)
        for k, e in self.sel.items():
            if k not in seen:
                out.append(e)
        return out

    def select_entry(self, e):
        self.sel[self._selkey(e)] = e
        self.sel_mode = True
        self._sync_cards()

    def toggle_entry(self, e):
        k = self._selkey(e)
        if k in self.sel:
            del self.sel[k]
            if not self.sel:
                self.sel_mode = False
        else:
            self.sel[k] = e
            self.sel_mode = True
        self._sync_cards()

    def _check_used(self, e):
        self.toggle_entry(e)

    def toggle_all(self):
        keys = self._visible_keys()
        if keys and keys <= set(self.sel):
            self.clear_selection()
            return
        self.sel = {self._selkey(c.e): c.e for c in self.all_cards()}
        self.sel_mode = bool(self.sel)
        self._sync_cards()

    def clear_selection(self):
        if not self.sel_mode and not self.sel:
            return
        self.sel, self.sel_mode = {}, False
        self._sync_cards()

    def _stack_files(self, e):
        """Thumbnail files of the other videos in a playlist entry (max 3). Videos that already have
        a cached thumbnail come first; the rest use the normal fallback look."""
        group = e.get("group") or []
        if len(group) < 2:
            return []
        me = Store._key(e["path"])
        others = [p for p in group if Store._key(p) != me]
        files = []
        for p in others:
            try:
                files.append(self.store.thumb_path(p, os.path.getsize(p)))
            except OSError:
                files.append(None)
        have = [f for f in files if f is not None and f.exists()]
        rest = [f for f in files if f is None or not f.exists()]
        return (have + rest)[:min(3, len(group) - 1)]

    def calc_cols(self):
        avail = self.width() - 2 * self.SIDE - 12
        return max(1, (avail + self.GAP) // (ThumbCard.CW + self.GAP))

    def set_folders(self, sections, animate=True):
        """Folder sections (see MainWindow._folder_sections) at the top of Home."""
        self.board.set_sections(sections, self.calc_cols(), animate)
        self.body.layout().activate()

    def all_cards(self):
        return list(self.cards) + [w for w in self.board.w.values() if isinstance(w, ThumbCard)]

    def _reflow(self):
        cols = self.calc_cols()
        self.board.set_cols(cols)
        if cols == self._cols:
            return
        self._cols = cols
        self.grid_host.setUpdatesEnabled(False)        # one repaint after the cards have moved, not one per card
        try:
            cw, vgap = ThumbCard.CW, 9
            ch = self.cards[0].height() if self.cards else 0
            for i, c in enumerate(self.cards):
                if c.parent() is not self.grid_host:
                    c.setParent(self.grid_host)
                c.move((i % cols) * (cw + self.GAP), (i // cols) * (ch + vgap))   # exact, immediate position
                if c.isHidden():
                    c.show()
            n = len(self.cards)
            rows = (n + cols - 1) // cols
            self.grid_host.setFixedSize(cols * cw + (cols - 1) * self.GAP if n else 0,
                                        rows * ch + (rows - 1) * vgap if n else 0)
        finally:
            self.grid_host.setUpdatesEnabled(True)
        self.body.layout().activate()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._reflow()
        self._place_bar()
        self._place_empty()


# --------------------------------------------------------------------------
# Left navigation (strip + expanded panel), like the Windows Media Player
# --------------------------------------------------------------------------
NAV_W = 48
NAV_SEL = "#2d2d2d"                # selected / hovered item background
NAV_SEP = "#323232"
NAV_PANEL_W = 320
NAV_ROWS = (("home", "home", "Home"), ("queue", "queue", "Play queue"),
            ("playlists", "playlists", "Playlists"), ("settings", "settings", "Settings"))


def nav_centres(h):
    """Vertical centre of every row, in logical pixels (measured from the screenshots)."""
    return {"menu": 26, "search": 65, "home": 112, "sep": 136, "queue": 160,
            "playlists": 200, "settings": max(248, h - 24)}


class NavStrip(QWidget):
    navigate = Signal(str)            # home | queue | playlists | settings
    toggleRequested = Signal()
    searchRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(NAV_W)
        self.setMouseTracking(True)
        self.active = "home"
        self._over = None
        self.hov = {k: Tween(self, 0, 140, self.update)
                    for k in ("menu", "search", "home", "queue", "playlists", "settings")}

    def set_active(self, key):
        self.active = key
        self.update()

    def _rect(self, key):
        cy = nav_centres(self.height())[key]
        return QRectF(4, cy - 18, NAV_W - 8, 36)

    def _hit(self, pos):
        for k in self.hov:
            if self._rect(k).contains(QPointF(pos)):
                return k
        return None

    def mouseMoveEvent(self, e):
        k = self._hit(e.position().toPoint())
        if k != self._over:
            self._over = k
            for name, tw in self.hov.items():
                tw.to(1.0 if name == k else 0.0, 140)
            self.setCursor(Qt.PointingHandCursor if k else Qt.ArrowCursor)

    def leaveEvent(self, e):
        self._over = None
        for tw in self.hov.values():
            tw.to(0.0, 200)

    def mouseReleaseEvent(self, e):
        k = self._hit(e.position().toPoint())
        if e.button() != Qt.LeftButton or not k:
            return
        if k == "menu":
            self.toggleRequested.emit()
        elif k == "search":
            self.searchRequested.emit()
        else:
            self.navigate.emit(k)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(FLAT_NAV))
        for key, tw in self.hov.items():
            r = self._rect(key)
            h = max(0.0, min(1.0, tw.value))
            on = key == self.active
            if on or h > 0.01:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(NAV_SEL) if on else with_alpha(QColor(NAV_SEL), h))
                p.drawRoundedRect(r, 6, 6)
            if on:
                p.setBrush(QColor(ACCENT))
                p.drawRoundedRect(QRectF(r.left(), r.center().y() - 8, 3, 16), 1.5, 1.5)
            icon = {"menu": "menu", "search": "search"}.get(key, key)
            paint_icon(p, icon, r.center().x(), r.center().y(), 18, QColor(TEXT))
        sy = nav_centres(self.height())["sep"]
        p.setPen(QPen(QColor(NAV_SEP), 1))
        p.drawLine(QPointF(10, sy), QPointF(NAV_W - 10, sy))


class NavPanel(QWidget):
    """The wide acrylic panel that opens over the page (hamburger button)."""
    navigate = Signal(str)
    collapseRequested = Signal()
    searchChanged = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.active = "home"
        self._over = None
        self.search = QLineEdit(self)
        self.search.setPlaceholderText("Search")
        self.search.addAction(menu_icon("search"), QLineEdit.TrailingPosition)
        self.search.setClearButtonEnabled(False)
        self.search.setStyleSheet(
            "QLineEdit{background:#363636;color:#fff;border:1px solid #484848;"
            "border-bottom:2px solid #5a5a5a;border-radius:5px;padding:6px 10px;font-size:14px;"
            "selection-background-color:#ff8a2a;}QLineEdit:focus{border-bottom:2px solid #ff8a2a;}")
        self.search.textChanged.connect(self.searchChanged)

    def set_active(self, key):
        self.active = key
        self.update()

    def _rows(self):
        c = nav_centres(self.height())
        rows = {"menu": QRectF(8, c["menu"] - 18, NAV_PANEL_W - 28, 36)}
        for key, _, _ in NAV_ROWS:
            rows[key] = QRectF(8, c[key] - 18, NAV_PANEL_W - 28, 36)
        return rows

    def resizeEvent(self, e):
        c = nav_centres(self.height())
        self.search.setGeometry(12, int(c["search"]) - 18, NAV_PANEL_W - 36, 36)

    def _hit(self, pos):
        for k, r in self._rows().items():
            if r.contains(QPointF(pos)):
                return k
        return None

    def mouseMoveEvent(self, e):
        k = self._hit(e.position().toPoint())
        if k != self._over:
            self._over = k
            self.setCursor(Qt.PointingHandCursor if k else Qt.ArrowCursor)
            self.update()

    def leaveEvent(self, e):
        self._over = None
        self.update()

    def mouseReleaseEvent(self, e):
        k = self._hit(e.position().toPoint())
        if e.button() != Qt.LeftButton or not k:
            return
        if k == "menu":
            self.collapseRequested.emit()
        else:
            self.navigate.emit(k)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        body = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(QColor("#3d3d3d"), 1))
        p.setBrush(QColor(43, 43, 43, 255))
        p.drawRoundedRect(body, 8, 8)
        rows = self._rows()
        for key, icon, label in (("menu", "menu", ""),) + NAV_ROWS:
            r = rows[key]
            on = key == self.active
            if on or key == self._over:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(255, 255, 255, 28 if on else 18))
                p.drawRoundedRect(r, 6, 6)
            if on:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(ACCENT))
                p.drawRoundedRect(QRectF(r.left(), r.center().y() - 8, 3, 16), 1.5, 1.5)
            paint_icon(p, icon, r.left() + 22, r.center().y(), 18, QColor(TEXT))
            if label:
                p.setPen(QColor(TEXT))
                p.setFont(ui_font(14))
                p.drawText(QRectF(r.left() + 48, r.top(), r.width() - 56, r.height()),
                           Qt.AlignVCenter | Qt.AlignLeft, label)
        sy = nav_centres(self.height())["sep"]
        p.setPen(QPen(QColor(255, 255, 255, 36), 1))
        p.drawLine(QPointF(14, sy), QPointF(NAV_PANEL_W - 22, sy))


class NavScrim(QWidget):
    clicked = Signal()

    def paintEvent(self, e):
        QPainter(self).fillRect(self.rect(), QColor(0, 0, 0, 90))

    def mouseReleaseEvent(self, e):
        self.clicked.emit()


class PlaceholderPage(QWidget):
    """A page that is built in a later design step."""

    def __init__(self, title, note, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(56, 14, 56, 40)
        h = QLabel(title)
        h.setStyleSheet(DISPLAY_QSS + "color:#fff;font-size:38px;font-weight:600;")
        n = QLabel(note)
        n.setStyleSheet(f"color:{MUTED};font-size:14px;")
        lay.addWidget(h)
        lay.addSpacing(4)
        lay.addWidget(n)
        lay.addStretch(1)


class ToggleSwitch(QAbstractButton):
    """WinUI ToggleSwitch: 40 x 20 track with the On / Off label on its left."""
    TRACK_W, TRACK_H, GAP = 40, 20, 12

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self._kb = False
        fm = QFontMetrics(ui_font(14))
        self._label_w = max(fm.horizontalAdvance("On"), fm.horizontalAdvance("Off"))
        self.setFixedSize(self._label_w + self.GAP + self.TRACK_W, 32)

    def focusInEvent(self, e):
        self._kb = e.reason() in (Qt.TabFocusReason, Qt.BacktabFocusReason, Qt.ShortcutFocusReason)
        super().focusInEvent(e)

    def focusOutEvent(self, e):
        self._kb = False
        super().focusOutEvent(e)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        on, hot = self.isChecked(), self.underMouse()
        p.setPen(QColor(TEXT))
        p.setFont(ui_font(14))
        p.drawText(QRectF(0, 0, self._label_w, self.height()), Qt.AlignVCenter | Qt.AlignRight,
                   "On" if on else "Off")
        tr = QRectF(self.width() - self.TRACK_W, (self.height() - self.TRACK_H) / 2, self.TRACK_W, self.TRACK_H)
        if on:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#ff9a40") if hot else QColor(ACCENT))
            p.drawRoundedRect(tr, 10, 10)
            knob, kc = QColor("#1b1b1b"), QPointF(tr.right() - 10, tr.center().y())
        else:
            p.setPen(QPen(QColor(255, 255, 255, 160), 1))
            p.setBrush(QColor(255, 255, 255, 16 if hot else 0))
            p.drawRoundedRect(tr.adjusted(0.5, 0.5, -0.5, -0.5), 9.5, 9.5)
            knob, kc = QColor(255, 255, 255, 215), QPointF(tr.left() + 10, tr.center().y())
        d = 14 if (hot or self.isDown()) else 12
        p.setPen(Qt.NoPen)
        p.setBrush(knob)
        p.drawEllipse(kc, d / 2, d / 2)
        if self._kb:
            p.setPen(QPen(QColor(TEXT), 1.5))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(tr.adjusted(-2.5, -2.5, 2.5, 2.5), 12, 12)


class CardButton(QAbstractButton):
    """Text button inside a settings card (32 high, about 7 % white fill, 4 px corners)."""

    def __init__(self, text, parent=None):
        super().__init__(parent)
        self.setText(text)
        self.setCursor(Qt.PointingHandCursor)
        self._kb = False
        self.setFixedSize(max(70, QFontMetrics(ui_font(14)).horizontalAdvance(text) + 24), 32)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        a = 30 if self.isDown() else (24 if self.underMouse() else 18)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, a))
        p.drawRoundedRect(QRectF(self.rect()), 4, 4)
        if self.hasFocus() and self._kb:
            p.setPen(QPen(QColor(TEXT), 1.5))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(QRectF(self.rect()).adjusted(0.75, 0.75, -0.75, -0.75), 4, 4)
        p.setPen(QColor(TEXT) if self.isEnabled() else QColor(255, 255, 255, 90))
        p.setFont(ui_font(14))
        p.drawText(QRectF(self.rect()), Qt.AlignCenter, self.text())

    def focusInEvent(self, e):
        self._kb = e.reason() in (Qt.TabFocusReason, Qt.BacktabFocusReason, Qt.ShortcutFocusReason)
        super().focusInEvent(e)

    def focusOutEvent(self, e):
        self._kb = False
        super().focusOutEvent(e)


class SettingsCombo(QAbstractButton):
    """Boxed drop-down of a settings card (value on the left, chevron on the right). Opens a menu."""
    activated = Signal(QPoint)               # global point at the bottom right of the box

    def __init__(self, options, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self._kb = False
        fm = QFontMetrics(ui_font(14))
        widest = max(fm.horizontalAdvance(lab) for lab, _ in options)
        self.setFixedSize(max(84, widest + 12 + 14 + 12 + 10), 32)
        self.clicked.connect(lambda: self.activated.emit(self.mapToGlobal(QPoint(self.width(), self.height()))))

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.click()
        else:
            super().keyPressEvent(e)

    def focusInEvent(self, e):
        self._kb = e.reason() in (Qt.TabFocusReason, Qt.BacktabFocusReason, Qt.ShortcutFocusReason)
        super().focusInEvent(e)

    def focusOutEvent(self, e):
        self._kb = False
        super().focusOutEvent(e)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        a = 30 if self.isDown() else (24 if self.underMouse() else 18)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(QColor(255, 255, 255, 14), 1))
        p.setBrush(QColor(255, 255, 255, a))
        p.drawRoundedRect(r, 4, 4)
        if self.hasFocus() and self._kb:
            p.setPen(QPen(QColor(TEXT), 1.5))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), 4, 4)
        p.setPen(QColor(TEXT) if self.isEnabled() else QColor(255, 255, 255, 90))
        p.setFont(ui_font(14))
        p.drawText(QRectF(12, 0, self.width() - 36, self.height()), Qt.AlignVCenter | Qt.AlignLeft, self.text())
        cx, cy = self.width() - 17, self.height() / 2
        p.setPen(QPen(QColor(TEXT), 1.2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        p.drawPolyline(_poly([(cx - 4.0, cy - 2.2), (cx, cy + 1.8), (cx + 4.0, cy - 2.2)]))


class SettingsCard(QWidget):
    """One row of the Settings page. Cards are 69 px high (measured on REF-21 at 125 %), 4 px corners,
    about 6 % white fill, icon centred 28 px from the left, text at 60. Optional grey description line.
    kind: "toggle", "button", "combo" (boxed drop-down), "expander" (chevron, opens its rows), "info".
    sub=True makes a row inside an expander (56 high, square, no icon)."""
    H, H_SUB = 69, 56
    toggled = Signal(bool)
    clicked = Signal()                       # button card
    comboRequested = Signal(QPoint)
    headClicked = Signal()                   # expander header
    linkClicked = Signal()

    def __init__(self, icon, title, kind, button_text="", desc="", options=None, sub=False, link="", parent=None):
        super().__init__(parent)
        self.icon_kind, self.title, self.kind, self.desc, self.sub = icon, title, kind, desc, sub
        self.link, self._link_rect, self.value, self.expanded = link, QRectF(), "", False
        self._down = self._kb = False
        self.setFixedHeight(self.H_SUB if sub else self.H)
        self.setAccessibleName(title)
        self.control = None
        if kind == "toggle":
            self.control = ToggleSwitch(self)
            self.control.toggled.connect(self.toggled)
        elif kind == "button":
            self.control = CardButton(button_text, self)
            self.control.clicked.connect(self.clicked)
        elif kind == "combo":
            self.control = SettingsCombo(options, self)
            self.control.activated.connect(self.comboRequested)
        elif kind == "expander":
            self.setCursor(Qt.PointingHandCursor)
            self.setFocusPolicy(Qt.StrongFocus)
        if self.control:
            self.control.setAccessibleName(f"{title}: {button_text}" if button_text else title)
        if link:
            self.setMouseTracking(True)

    # ---- values
    def set_value(self, text):
        self.value = text
        if self.kind == "combo":
            self.control.setText(text)
            self.control.setAccessibleName(f"{self.title}: {text}")
        self.update()

    def set_desc(self, text):
        self.desc = text
        self.update()

    def set_expanded(self, on):
        self.expanded = on
        self.update()

    # ---- geometry
    def _right(self):
        return 46 if (self.kind == "expander" or self.sub) else 20

    def _place(self):
        if self.control:
            self.control.move(self.width() - self._right() - self.control.width(),
                              (self.height() - self.control.height()) // 2)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._place()

    def showEvent(self, e):
        super().showEvent(e)
        self._place()

    # ---- input
    def enterEvent(self, e):
        self.update()

    def leaveEvent(self, e):
        self.update()

    def focusInEvent(self, e):
        self._kb = e.reason() in (Qt.TabFocusReason, Qt.BacktabFocusReason, Qt.ShortcutFocusReason)
        self.update()
        super().focusInEvent(e)

    def focusOutEvent(self, e):
        self._kb = False
        self.update()
        super().focusOutEvent(e)

    def mouseMoveEvent(self, e):
        if self.link:
            self.setCursor(Qt.PointingHandCursor if self._link_rect.contains(e.position()) else Qt.ArrowCursor)

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        if self.link and self._link_rect.contains(e.position()):
            self.linkClicked.emit()
        elif self.kind == "expander":
            self._down = True
            self.update()

    def mouseReleaseEvent(self, e):
        was = self._down
        self._down = False
        self.update()
        if was and self.rect().contains(e.position().toPoint()):
            self.headClicked.emit()

    def keyPressEvent(self, e):
        if self.kind == "expander" and e.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.headClicked.emit()
        else:
            super().keyPressEvent(e)

    # ---- painting
    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        head = self.kind == "expander"
        a = 10 if self.sub else 15
        if head and self._down:
            a = 11
        elif head and self.underMouse():
            a = 22
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(QColor(0, 0, 0, 30), 1))
        p.setBrush(QColor(255, 255, 255, a))
        p.drawRoundedRect(r, 0 if self.sub else 4, 0 if self.sub else 4)
        if self._kb:
            p.setPen(QPen(QColor(TEXT), 1.5))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), 4, 4)
        if self.icon_kind:
            paint_icon(p, self.icon_kind, 28, self.height() / 2, 20, QColor(TEXT), sw=0.8)
        # text block: title, optional description (12 px, grey)
        right = self._right() + (self.control.width() + 16 if self.control else 0) + (24 if head else 0)
        if self.kind == "info":
            right += QFontMetrics(ui_font(14)).horizontalAdvance(self.value) + 16
        tw = max(40, self.width() - 60 - right)
        p.setPen(QColor(TEXT))
        p.setFont(ui_font(14))
        if self.desc or self.link:
            top = (self.height() - 36) / 2
            p.drawText(QRectF(60, top, tw, 20), Qt.AlignVCenter | Qt.AlignLeft, self.title)
            p.setFont(ui_font(12))
            p.setPen(QColor(MUTED))
            fm = QFontMetrics(ui_font(12))
            link_w = (fm.horizontalAdvance(self.link) + 10) if self.link else 0
            txt = fm.elidedText(self.desc, Qt.ElideMiddle, int(max(30, tw - link_w)))
            p.drawText(QRectF(60, top + 19, tw, 16), Qt.AlignVCenter | Qt.AlignLeft, txt)
            if self.link:
                lx = 60 + fm.horizontalAdvance(txt) + 10
                self._link_rect = QRectF(lx, top + 19, fm.horizontalAdvance(self.link), 16)
                p.setPen(QColor("#8aa8c4"))
                p.drawText(self._link_rect, Qt.AlignVCenter | Qt.AlignLeft, self.link)
        else:
            p.drawText(QRectF(60, 0, tw, self.height()), Qt.AlignVCenter | Qt.AlignLeft, self.title)
        if self.kind == "info":
            p.setPen(QColor(TEXT))
            p.setFont(ui_font(14))
            p.drawText(QRectF(self.width() * 0.4, 0, self.width() * 0.6 - 20, self.height()),
                       Qt.AlignVCenter | Qt.AlignRight, self.value)
        if head:
            cx, cy = self.width() - 25, self.height() / 2
            p.setPen(QPen(QColor(TEXT), 1.2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.setBrush(Qt.NoBrush)
            d = -1 if self.expanded else 1
            p.drawPolyline(_poly([(cx - 4.4, cy - 2.4 * d), (cx, cy + 2.0 * d), (cx + 4.4, cy - 2.4 * d)]))


class SettingsExpander(QWidget):
    """A card that opens to show more rows (like "On-screen controls"). The rows slide open."""

    def __init__(self, icon, title, desc, rows, parent=None):
        super().__init__(parent)
        self.head = SettingsCard(icon, title, "expander", desc=desc, parent=self)
        self.rows = rows
        self.body = QWidget(self)
        self.body.setStyleSheet("background:transparent;")
        v = QVBoxLayout(self.body)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        for r in rows:
            v.addWidget(r)
        self.body.hide()
        self._body_h = sum(r.height() for r in rows)
        self._p = 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(170)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._set_p)
        self.head.headClicked.connect(self.toggle)
        self.setFixedHeight(SettingsCard.H)
        self.setAccessibleName(title)

    @property
    def expanded(self):
        return self.head.expanded

    def toggle(self):
        self.set_expanded(not self.head.expanded)

    def set_expanded(self, on, animate=True):
        self.head.set_expanded(on)
        if on:
            self.body.show()
        self._anim.stop()
        end = 1.0 if on else 0.0
        if animate:
            self._anim.setStartValue(self._p)
            self._anim.setEndValue(end)
            self._anim.start()
        else:
            self._set_p(end)

    def _set_p(self, v):
        self._p = float(v)
        self.setFixedHeight(SettingsCard.H + int(round((self._body_h + 1) * self._p)))
        if self._p <= 0.0 and not self.head.expanded:
            self.body.hide()

    def set_rows(self, rows):
        """Swap the rows (a list that changes while the app runs)."""
        lay = self.body.layout()
        while lay.count():
            w = lay.takeAt(0).widget()
            if w is not None:
                w.hide()
                w.deleteLater()
        for r in rows:
            lay.addWidget(r)
        self.rows = rows
        self._body_h = sum(r.height() for r in rows)
        self.body.setGeometry(0, SettingsCard.H + 1, self.width(), self._body_h)
        if self._p > 0:
            self.setFixedHeight(SettingsCard.H + int(round((self._body_h + 1) * self._p)))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.head.setGeometry(0, 0, self.width(), SettingsCard.H)
        self.body.setGeometry(0, SettingsCard.H + 1, self.width(), self._body_h)


class SettingsPage(QWidget):
    """The Settings page, with this app's own settings (layout after the Screenbox Settings recording:
    cards with a grey description line, boxed drop-downs, On / Off switches, one expander)."""
    toggled = Signal(str, bool)              # key, new state
    comboRequested = Signal(str, QPoint)     # key, global point under the box
    buttonClicked = Signal(str)
    rulesRemove = Signal(int)                # index of the hidden piece to remove

    SIDE, RIGHT = 56, 56
    HEAD_H = 46

    def __init__(self, options, parent=None):
        super().__init__(parent)
        self.cards = {}
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.viewport().setAutoFillBackground(False)
        self.scroll.setStyleSheet(
            "QScrollArea{background:transparent;border:none;}"
            "QScrollBar:vertical{background:transparent;width:10px;margin:2px;}"
            "QScrollBar::handle:vertical{background:#4a4448;border-radius:4px;min-height:30px;}"
            "QScrollBar::handle:vertical:hover{background:#625b60;}"
            "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;}"
            "QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical{background:transparent;}")
        outer.addWidget(self.scroll)
        body = QWidget()
        body.setStyleSheet("background:transparent;")
        self.scroll.setWidget(body)
        v = QVBoxLayout(body)
        v.setContentsMargins(self.SIDE, 14, self.RIGHT, 40)
        v.setSpacing(0)
        head = QLabel("Settings")
        head.setFixedHeight(self.HEAD_H)
        head.setStyleSheet(DISPLAY_QSS + "color:#fff;font-size:38px;font-weight:600;")
        v.addWidget(head)
        self.heading = head
        self.headers = []

        def toggle(key, icon, title, desc, sub=False):
            c = SettingsCard(icon, title, "toggle", desc=desc, sub=sub)
            c.toggled.connect(lambda on, k=key: self.toggled.emit(k, on))
            self.cards[key] = c
            return c

        def combo(key, icon, title, desc, sub=False):
            c = SettingsCard(icon, title, "combo", desc=desc, options=options[key], sub=sub)
            c.comboRequested.connect(lambda pos, k=key: self.comboRequested.emit(k, pos))
            self.cards[key] = c
            return c

        def button(key, icon, title, desc, text, link=""):
            c = SettingsCard(icon, title, "button", button_text=text, desc=desc, link=link)
            c.clicked.connect(lambda k=key: self.buttonClicked.emit(k))
            c.linkClicked.connect(lambda k=key + "_link": self.buttonClicked.emit(k))
            self.cards[key] = c
            return c

        onscreen = SettingsExpander("video", "On-screen controls",
                                    "Choose when the controls and the title bar show on the video page", [
            toggle("show_paused", None, "Show controls when paused", "", sub=True),
            combo("hide_s", None, "Hide controls after this amount of time", "", sub=True)])
        self.cards["onscreen"] = onscreen
        self.onscreen = onscreen

        v.addSpacing(44)
        self._group(v, "Player", [
            combo("rewind", "back10", "Rewind", "The amount of time skipped backward when you rewind"),
            combo("ff", "fwd30", "Fast forward", "The amount of time skipped forward when you fast forward"),
            onscreen,
            combo("boost", "volume", "Volume boost", "Allow the maximum volume to go above 100%"),
            combo("subsize", "cc", "Subtitle size", "Changing it restarts the video and resumes where you were"),
            combo("aout", "eq", "Audio output", "Changing it restarts the video and resumes where you were"),
            toggle("autoplay", "playnext", "Play next automatically",
                   "When a video ends, play the next one in the play queue")])
        v.addSpacing(30)
        self._group(v, "General", [
            combo("folderq", "queue", "Add other files in the folder to the queue",
                  "What happens when you open a single video"),
            button("shot", "shot", "Frame captures will save to", "", "Open folder", link="Change"),
            button("defplayer", "check", "Default player", "Make Simple Player the app Windows opens videos with",
                   "Set as default"),
            SettingsExpander("rename", "Hidden name pieces", "", [])])
        self.rules_exp = self.cards["namerules"] = v.itemAt(v.count() - 1).widget()
        v.addSpacing(30)
        self._group(v, "Privacy", [
            toggle("recent", "film", "Show recent media", "See recently played media on the home page"),
            toggle("history", "play_o", "Playback position history",
                   "Remember where you stopped, so a video can carry on from there"),
            button("clear", "trash", "Continue watching", "Remove every video from Continue watching. Video files stay.",
                   "Clear")])
        v.addSpacing(30)
        self._group(v, "About", [])
        about = SettingsCard("info", "Simple Player", "info", desc="Plays the formats that VLC plays")
        self.cards["about"] = about
        v.addWidget(about)
        v.addStretch(1)

    def _group(self, v, title, cards):
        h = QLabel(title)
        h.setFixedHeight(20)
        h.setStyleSheet(SEMI_QSS + "color:#fff;font-size:14px;font-weight:600;padding-left:0px;")
        v.addWidget(h)
        self.headers.append(h)
        v.addSpacing(6)
        for i, c in enumerate(cards):
            if i:
                v.addSpacing(2)
            v.addWidget(c)

    def set_rules(self, rules):
        """List the pieces hidden on Home names (Rename), each with a Remove button."""
        rows = []
        for i, r in enumerate(rules):
            c = SettingsCard(None, r["text"], "button", button_text="Remove", sub=True,
                             desc="Hidden at the start of names" if r["side"] == "start" else "Hidden at the end of names")
            c.clicked.connect(lambda i=i: self.rulesRemove.emit(i))
            rows.append(c)
        if not rows:
            rows.append(SettingsCard(None, "Nothing is hidden yet", "info", sub=True,
                                     desc="When you rename a video you can choose to hide a piece on other videos too"))
        self.rules_exp.set_rows(rows)
        n = len(rules)
        self.rules_exp.head.set_desc("Pieces of names that are hidden on every video" if n == 0 else
                                     f"{n} piece{'s' if n != 1 else ''} hidden on every video")

    def set_toggle(self, key, on):
        sw = self.cards[key].control
        sw.blockSignals(True)
        sw.setChecked(bool(on))
        sw.blockSignals(False)
        sw.update()

    def set_combo(self, key, label):
        self.cards[key].set_value(label)


class HomeView(QWidget):
    """The 'library' side of the app: navigation strip + pages (Home, Playlists, Settings)."""
    openRequested = Signal()
    openFolderRequested = Signal()
    playRequested = Signal(str)
    cardMenuRequested = Signal(object, QPoint)
    folderMenuRequested = Signal(str, QPoint)
    sortChosen = Signal(str)
    pageChanged = Signal(str)

    PAGES = {"home": 0, "playlists": 1, "settings": 2, "queue": 3, "pl_detail": 4}

    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.page = "home"
        self._entries, self._filter = [], ""
        self._sections = []                          # folder sections for the top of Home
        self.nav = NavStrip(self)
        self.stack = QStackedWidget(self)
        self.home_page = HomePage(store)
        self.stack.addWidget(self.home_page)
        self.playlists_page = PlaylistsPage()
        self.stack.addWidget(self.playlists_page)
        self.settings_page = SettingsPage(SETTING_OPTIONS)
        self.stack.addWidget(self.settings_page)
        self.queue_page = QueuePage()
        self.stack.addWidget(self.queue_page)
        self.detail_page = PlaylistDetailPage()
        self.stack.addWidget(self.detail_page)
        self.toast = Toast(self)
        self.scrim = NavScrim(self)
        self.panel = NavPanel(self)
        self.scrim.hide()
        self.panel.hide()
        self._closing = False
        self._t = Tween(self, 0, 240, self._animate_panel)
        for sig in ("openRequested", "openFolderRequested", "playRequested", "cardMenuRequested",
                    "folderMenuRequested", "sortChosen"):
            getattr(self.home_page, sig).connect(getattr(self, sig))
        self.nav.navigate.connect(self._navigate)
        self.nav.toggleRequested.connect(self.expand)
        self.nav.searchRequested.connect(lambda: (self.expand(), self.panel.search.setFocus()))
        self.panel.navigate.connect(lambda k: (self._navigate(k), self.collapse()))
        self.panel.collapseRequested.connect(self.collapse)
        self.panel.searchChanged.connect(self.set_filter)
        self.scrim.clicked.connect(self.collapse)

    def paintEvent(self, e):                         # cool grey at the top, warm brown at the bottom
        p = QPainter(self)
        g = QLinearGradient(0, 0, 0, self.height())
        g.setColorAt(0.0, QColor(FLAT_HOME_TOP))
        g.setColorAt(1.0, QColor(FLAT_HOME_BOTTOM))
        p.fillRect(self.rect(), g)

    # --- same interface the rest of the app already uses
    @property
    def cards(self):
        return self.home_page.cards

    def refresh(self, entries, animate=True, sections=None):
        self._entries = list(entries)
        if sections is not None:
            self._sections = list(sections)
        self._apply(animate)

    @staticmethod
    def _match(q, e):
        return (not q or q in display_name(e["path"]).lower() or q in os.path.basename(e["path"]).lower()
                or q in (e.get("title") or "").lower())

    def _filtered_sections(self, q):
        """The folder sections that have something matching the search (a stack matches by its name or any episode)."""
        out = []
        for sec in self._sections:
            its = [it for it in sec["items"]
                   if self._match(q, it["e"]) or any(self._match(q, ep) for ep in it.get("eps", []))]
            if its:
                out.append(dict(sec, items=its))
        return out

    def _apply(self, animate):
        q = self._filter.strip().lower()
        shown = [e for e in self._entries if self._match(q, e)]
        secs = self._filtered_sections(q)
        self.home_page.set_folders(secs, animate)
        self.home_page.refresh(shown, animate, searching=bool(q) and not shown)
        if q and secs and not shown:                 # the matches are all in folder sections: no "No matches" text
            self.home_page.hide_recent_heading()

    def set_filter(self, text):
        self._filter = text
        if self.page != "home":
            self.show_page("home")
        self._apply(False)

    # --- pages
    def show_page(self, key):
        if key != "home":
            self.home_page.clear_selection()
        if key != "queue":
            self.queue_page.clear_selection()
        if key != "playlists":
            self.playlists_page.clear_selection()
        if key != "pl_detail":
            self.detail_page.clear_selection()
        changed = key != getattr(self, "page", None)
        self.page = key
        self.stack.setCurrentIndex(self.PAGES[key])
        if changed and self.fade_pages and self.isVisible() and not getattr(self.parent(), "_fading", False):
            self._fade_in_page()
        nav_key = "playlists" if key == "pl_detail" else key          # a playlist belongs to the Playlists item
        self.nav.set_active(nav_key)
        self.panel.set_active(nav_key)
        self.pageChanged.emit(key)

    fade_pages = False                    # set by the Stage: on Windows the pages fade in (170 ms)

    def _fade_in_page(self):
        """The page that was just shown fades in (Home pages hold no video window, so a normal fade works)."""
        self._end_page_fade()
        w = self.stack.currentWidget()
        eff = QGraphicsOpacityEffect(w)
        eff.setOpacity(0.0)
        w.setGraphicsEffect(eff)
        a = QPropertyAnimation(eff, b"opacity", self)
        a.setDuration(170)
        a.setStartValue(0.0)
        a.setEndValue(1.0)
        a.setEasingCurve(EASE_OUT)
        a.finished.connect(self._end_page_fade)
        self._pg_anim, self._pg_w = a, w
        a.start()

    def _end_page_fade(self):
        a, w = getattr(self, "_pg_anim", None), getattr(self, "_pg_w", None)
        self._pg_anim = self._pg_w = None
        if a is not None:
            a.stop()
            a.deleteLater()
        if w is not None:
            w.setGraphicsEffect(None)

    def _navigate(self, key):
        self.show_page(key)

    # --- the wide panel
    def expand(self):
        self.scrim.setGeometry(self.rect())
        self.panel.setGeometry(6 - NAV_PANEL_W, 4, NAV_PANEL_W, max(300, self.height() - 8))
        self._closing = False
        self.scrim.show()
        self.panel.show()
        self.scrim.raise_()
        self.panel.raise_()
        self._t.to(1.0, 240)

    def collapse(self):
        self._closing = True
        self._t.to(0.0, 200)

    def _animate_panel(self):
        t = max(0.0, min(1.0, self._t.value))
        self.panel.move(int(6 - NAV_PANEL_W * (1 - t)), 4)
        self.scrim.setVisible(t > 0.01)
        if self._closing and t <= 0.01:
            self.panel.hide()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        w, h = self.width(), self.height()
        self.nav.setGeometry(0, 0, NAV_W, h)
        self.stack.setGeometry(NAV_W, 0, max(0, w - NAV_W), h)
        self.scrim.setGeometry(0, 0, w, h)
        self.panel.resize(NAV_PANEL_W, max(300, h - 8))

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape and self.panel.isVisible():
            self.collapse()
        else:
            super().keyPressEvent(e)


class ThumbPreview(QWidget):
    """Small thumbnail of the playing video at the left of the control bar (128 x 72, 16:9; Media Player ref6 shows about 160 x 90 px at 125 %)."""
    clicked = Signal()
    W, H = 128, 72

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(self.W, self.H)
        self.setCursor(Qt.PointingHandCursor)
        self.pm, self._key = None, None
        self.vpath = None                 # PHASE B: the video (for the placeholder tint and tag)
        self.live = None                  # PHASE 12a: the VideoSurface while the live video sits in this slot
        self.live_pos = None              # where the Stage validated this slot (Stage coordinates): the live picture is drawn only there

    def set_source(self, path):
        """path: a thumbnail file (cached frame of the video) or None for the placeholder."""
        key = None
        try:
            if path is not None and path.exists():
                key = (str(path), path.stat().st_mtime_ns)
        except OSError:
            key = None
        if key == self._key:
            return
        self._key = key
        pm = QPixmap(str(path)) if key else None
        self.pm = pm if (pm is not None and not pm.isNull()) else None
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        box = QRectF(self.rect())
        clip = QPainterPath()
        clip.addRoundedRect(box, 4, 4)
        p.setClipPath(clip)
        if self.live is not None and self.live.has_frame():
            st_ = self.live.parentWidget()
            if st_ is None or self.live_pos is None or self.mapTo(st_, QPoint(0, 0)) != self.live_pos:
                return          # the bar is still laying out (seen once on the first Home: picture 5 px right, 11 px low): draw nothing this frame
            p.fillRect(box, QColor(0, 0, 0))
            if self.live.draw_into(p, box, use_crop=True):    # the live picture, same crop as before (slot_fill_crop)
                return
        if self.pm:                                   # fit inside the box without stretching
            ThumbCard._draw_fit(p, box, self.pm)
        elif self.vpath:
            paint_clapper(p, box, self.vpath)             # PHASE B: same clapperboard placeholder
        else:
            g = QLinearGradient(0, 0, 0, self.H)
            g.setColorAt(0, QColor("#2c2c32"))
            g.setColorAt(1, QColor("#1c1c20"))
            p.fillRect(box, g)
            p.setPen(QPen(QColor(255, 255, 255, 150), 1.5))
            paint_icon(p, "film", box.center().x(), box.center().y(), 22, QColor(255, 255, 255, 170))


CONTROLS_BG = "#141414"             # measured on the Media Player control bar


class ControlsBar(QWidget):
    FULL_H = 117                 # video page (Media Player: 661.5 to 808 px at 125 % = 117, measured on a full size screenshot)
    HOME_H = 117                 # Home with the preview slot: 3 + seek row + 10 + 72 slot + 6 (icon row 42 px above the bottom edge, like FULL_H)
    COMPACT_H = 104

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_NativeWindow)
        self.setFixedHeight(self.FULL_H)
        self.setAttribute(Qt.WA_OpaquePaintEvent)       # paintEvent fills every pixel: no background erase while resizing
        self.setAttribute(Qt.WA_NoSystemBackground)
        root = QVBoxLayout(self)
        self._root = root
        # Measured on the Media Player at 125 % (px / 1.25): time text starts 16 px from the left and ends 16.8 px
        # from the right, the track runs from 87 to 872 px, the icon row is centred 42 px above the bottom edge,
        # the last icon is centred 22 px from the right edge. The mini-player slot stays at x = 12.
        root.setContentsMargins(12, 3, 2, 15)
        root.setSpacing(10)

        row1 = QHBoxLayout()
        row1.setContentsMargins(4, 0, 15, 0)
        row1.setSpacing(0)
        self.t_cur = self._time_label(Qt.AlignLeft, 60)
        self.t_tot = self._time_label(Qt.AlignRight, 50)
        self.seek = FlatSlider(1000)
        self.seek.tip = fmt
        self.seek.th0, self.seek.ring_r, self.seek.dot_r = 4.0, 10.4, 5.2       # measured: 5, 26 and 13 px at 125 %
        self.seek.ring_col = QColor("#454545")
        self.seek.track_col = QColor("#949494")
        self.seek.fill_col = QColor("#ff8232")
        row1.addWidget(self.t_cur)
        row1.addWidget(self.seek, 1)
        row1.addWidget(self.t_tot)
        root.addLayout(row1)

        row2 = QHBoxLayout()
        row2.setSpacing(0)
        self.title = TipLabel()
        self.title.setFont(ui_font(20, QFont.DemiBold))
        self.title.setFixedHeight(40)

        self.shuffle = IconButton("shuffle")
        self.prev = IconButton("prev")
        self.back = IconButton("back10", 40, 24)
        self.play = IconButton("play", 54)
        self.fwd = IconButton("fwd30", 40, 24)
        self.next = IconButton("next")
        self.repeat = IconButton("repeat_all")
        center = QHBoxLayout()
        center.setSpacing(4)                       # icon pitch 44 px, next to the play button 51 px (measured)
        for b in (self.shuffle, self.prev, self.back, self.play, self.fwd, self.next, self.repeat):
            center.addWidget(b, 0, Qt.AlignVCenter)

        self.cc = IconButton("cc_bar")
        self.vol = IconButton("volume")
        self.full = IconButton("fullscreen")
        self.mini = IconButton("mini_bar")
        self.more = IconButton("more")
        right = QHBoxLayout()
        right.setSpacing(4)
        right.addStretch(1)
        for b in (self.cc, self.vol, self.full, self.mini, self.more):
            right.addWidget(b, 0, Qt.AlignVCenter)

        self.thumb = ThumbPreview()
        self.thumb_gap = QWidget()
        self.thumb_gap.setFixedWidth(14)
        self.lead = QWidget()                      # keeps the title at x = 22 when there is no preview
        self.lead.setFixedWidth(4)
        row2.addWidget(self.lead)
        row2.addWidget(self.thumb, 0, Qt.AlignVCenter)
        row2.addWidget(self.thumb_gap)
        row2.addWidget(self.title, 1)
        self.lspace = QWidget()                    # compact mode: balances the right side so the cluster is centred
        self.lspace.hide()
        self.lfill = QWidget()
        self.lfill.setFixedWidth(0)                # measured: the sides already balance once the title is hidden
        self.lfill.hide()
        row2.addWidget(self.lspace, 1)
        row2.addWidget(self.lfill)
        row2.addSpacing(16)
        row2.addLayout(center)
        self.rgap = QWidget()                      # compact mode only: balances the right side
        self.rgap.setFixedWidth(0)
        row2.addWidget(self.rgap)
        self._centre = center
        row2.addLayout(right, 1)
        root.addLayout(row2)

        self.shuffle.off_slash = True
        self.repeat.off_slash = True
        for b in (self.shuffle, self.prev, self.back, self.play, self.fwd, self.next, self.repeat,
                  self.cc, self.vol, self.full, self.mini, self.more):
            b.sw = 0.78                           # stroke 1.55 px at 125 % (the Media Player icons), was 1.99 px
            b.dis = "#6a6a6a"                     # dimmed icon (measured 106 / 255)
            b.cut = QColor(CONTROLS_BG)
            b.fill = QColor(CONTROLS_BG)
        self.seek.fill = QColor(CONTROLS_BG)
        self.title.fill = QColor(CONTROLS_BG)
        self.compact = False
        self._fill = QColor(CONTROLS_BG)
        self.block_hov = Tween(self, 0.0, 160, self._block_changed)     # PHASE B: title block hover
        self.title.fill_fn = self._title_fill_now
        self.translucent = False            # PHASE 12c: the picture window paints this bar with a see-through fill (children clear nothing)
        self.hidden_by_mask = False         # PHASE 12c: while the picture window paints the bar, this native window shows only ONE pixel
        self._line = None
        self.show_thumb(False)
        self._compact_hidden = [self.title, self.shuffle, self.repeat, self.cc, self.vol,
                                self.full, self.more]

    def _all_buttons(self):
        return (self.shuffle, self.prev, self.back, self.play, self.fwd, self.next, self.repeat,
                self.cc, self.vol, self.full, self.mini, self.more)

    def set_translucent(self, on, color=None):
        """PHASE 12c. on: the bar colour carries its own alpha and no child clears its own square, so a rendering of this
        bar has a see-through background and fully solid icons and text (the picture window draws that rendering over the video).
        off: back to the solid look; the next set_fill() repaints everything."""
        if on:
            c = QColor(color)
            if self.translucent and c == self._fill:
                return
            self.translucent = True
            self._fill = c
            for b in self._all_buttons():
                b.cut = QColor(c)
                b.fill = None
                b.update()
            self.seek.fill = None
            self.title.fill = None
            self.title.fill_fn = None
            for lab in (self.t_cur, self.t_tot):
                lab.setStyleSheet(SMALL_QSS + "color:#ffffff;font-size:12px;background:transparent;")
            self.seek.update()
            self.title.update()
            self.update()
        elif self.translucent:
            self.translucent = False
            self.title.fill_fn = self._title_fill_now
            self._fill = QColor(1, 2, 3, 4)               # never equal to a real colour: the next set_fill() applies in full

    def set_native_hidden(self, on):
        """PHASE 12c: Qt's stackUnder() does NOT move a native window below another one on a real window system (seen on X11 and
        in his Windows recording Nov 18: the unpainted native bar stayed ON TOP and hid the painted one). So while the picture window
        paints the bar, this native window keeps its place, its size and its logic, but a one pixel window shape makes it
        invisible and lets every mouse event fall through to the picture window. (Never an EMPTY shape: Qt must not get one.)"""
        if on and not self.hidden_by_mask:
            self.setMask(QRegion(0, 0, 1, 1))
            self.hidden_by_mask = True
        elif not on and self.hidden_by_mask:
            self.clearMask()
            self.hidden_by_mask = False

    def set_fill(self, color):
        """Bar colour. Home is solid #141414; on the video page the see-through bar is #1C1C1C at 70 % (measured:
        over black it shows 20, over a bright frame it lets 30 % of the picture through)."""
        c = QColor(color)
        if c == self._fill:
            return
        self._fill = c
        for b in (self.shuffle, self.prev, self.back, self.play, self.fwd, self.next, self.repeat,
                  self.cc, self.vol, self.full, self.mini, self.more):
            b.cut = QColor(c)
            b.fill = QColor(c)
            b.update()
        self.seek.fill = QColor(c)
        self.title.fill = QColor(c)
        self._sync_title_fill()
        for lab in (self.t_cur, self.t_tot):
            lab.setStyleSheet(SMALL_QSS + f"color:#ffffff;font-size:12px;background:{c.name()};")
        self.seek.update()
        self.title.update()
        self.update()

    def _time_label(self, align, width):
        lab = QLabel("00:00:00")
        lab.setFixedWidth(width)
        lab.setAlignment(align | Qt.AlignVCenter)
        lab.setStyleSheet(SMALL_QSS + f"color:#ffffff;font-size:12px;background:{CONTROLS_BG};")      # measured: white, digits 11 px tall at 125 %
        return lab

    def show_thumb(self, on):
        """The 128 x 72 mini-player slot (Home only). Home is taller (117) and has a 6 px bottom margin, the video page stays 108 / 15."""
        changed = bool(on) != self.thumb.isVisibleTo(self)
        self.thumb.setVisible(on)
        self.thumb_gap.setVisible(on)
        self.lead.setVisible(not on)
        self._apply_height()
        self._balance()
        if self.layout() is not None:
            self.layout().activate()                  # settle the row NOW, so the slot is never painted at its old place
        if changed and not self.compact:
            self._fade_title()

    def _fade_title(self):
        """The title moves when the preview slot appears or goes: it fades in at its new place instead of sliding."""
        fx = QGraphicsOpacityEffect(self.title)
        fx.setOpacity(0.0)
        self.title.setGraphicsEffect(fx)
        self._title_fx = fx
        if getattr(self, "_title_tw", None) is None:
            self._title_tw = Tween(self, 0.0, 240, self._title_step)
        self._title_tw.set(0.0)
        self._title_tw.to(1.0, 240, EASE_INOUT)

    def _title_step(self):
        fx = getattr(self, "_title_fx", None)
        if fx is None:
            return
        v = max(0.0, min(1.0, self._title_tw.value))
        if v >= 0.999:
            self._title_fx = None
            self.title.setGraphicsEffect(None)       # plain painting again
        else:
            fx.setOpacity(v)

    def set_compact(self, on):
        self.compact = on
        if on:
            self.show_thumb(False)
        for w in self._compact_hidden:
            w.setVisible(not on)
        self.lspace.setVisible(on)
        self.lfill.setVisible(on)
        self.lead.setVisible(True)
        self.rgap.setFixedWidth(30 if on else 0)
        self._apply_height()
        self._balance()

    def _apply_height(self):
        home = self.thumb.isVisibleTo(self) and not self.compact
        m = self.layout().contentsMargins()
        self.layout().setContentsMargins(m.left(), m.top(), m.right(), 6 if home else 15)
        self._root.setSpacing(19 if not home and not self.compact else 10)   # video page: seek row stays 26 px high at the top, icon row 42 px above the bottom
        self.setFixedHeight(self.COMPACT_H if self.compact else (self.HOME_H if home else self.FULL_H))

    def _balance(self):
        """Keep the play button in the middle of the bar (the Media Player does this also with the preview on
        Home): the title gets exactly the room left of the icon cluster."""
        if self.compact:
            return
        cluster = sum(self._centre.itemAt(i).widget().width() for i in range(self._centre.count())) \
            + 4 * (self._centre.count() - 1)
        left = 12 + (ThumbPreview.W + 14 if self.thumb.isVisible() else self.lead.maximumWidth()) + 16
        want = max(0, int(self.width() / 2 - cluster / 2 - left))
        if self.title.width() != want or self.title.minimumWidth() != want:
            self.title.setFixedWidth(want)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._balance()

    def set_top_line(self, color):
        """Home: a 1 physical px darker line on top of the bar (None = no line)."""
        c = QColor(color) if color else None
        if c == self._line:
            return
        self._line = c
        self.update()

    # ---------------------------------------------------------- PHASE B: the block behind the title (and the slot on Home)
    BLOCK_LEFT, BLOCK_H, BLOCK_PAD = 6, 80, 8

    def block_rect(self):
        """The area that lights up when the mouse is on the title (on Home together with the preview slot); None when
        there is no title (compact bar)."""
        if self.compact or not self.title.isVisibleTo(self):
            return None
        t = self.title.geometry()
        return QRect(self.BLOCK_LEFT, t.center().y() - self.BLOCK_H // 2, t.right() + 1 + self.BLOCK_PAD - self.BLOCK_LEFT, self.BLOCK_H)

    def _block_changed(self, *_):
        self._sync_title_fill()
        self.update()

    def _title_fill_now(self):
        """Colour the title label clears to, worked out when it paints: the bar colour, or the block colour while the
        block is lit (Home: 55,50,52; video page: soft white wash). One box, no second box inside it."""
        a, base = self.block_hov.value, QColor(self._fill)
        if a > 0.003 and self.block_rect() is not None:
            home = self.thumb.isVisibleTo(self)
            top, k = (QColor(55, 50, 52), a) if home else (QColor(255, 255, 255), 0.10 * a)
            base = QColor(int(lerp(base.red(), top.red(), k)), int(lerp(base.green(), top.green(), k)),
                          int(lerp(base.blue(), top.blue(), k)), base.alpha())
        return base

    def _sync_title_fill(self):
        self.title.update()

    def set_block_hover(self, on):
        want = 1.0 if on else 0.0
        if self.block_hov._target != want:
            self.block_hov.to(want, 160 if on else 220)

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), self._fill)
        if self._line is not None:
            p.fillRect(QRect(0, 0, self.width(), max(1, round(1.0 / self.devicePixelRatioF()))), self._line)
        a, r = self.block_hov.value, self.block_rect()
        if r is not None and a > 0.003:
            home = self.thumb.isVisibleTo(self)
            col = QColor(55, 50, 52, int(255 * a)) if home else QColor(255, 255, 255, int(26 * a))   # Home: measured; video page: soft wash
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(Qt.NoPen)
            p.setBrush(col)
            p.drawRoundedRect(QRectF(r), 6, 6)


# --------------------------------------------------------------------------
# Stage: video + home + controls, laid out by hand so controls can overlay
# --------------------------------------------------------------------------
CONTROLS_ALPHA = 140               # 55 % of 255: mini window bar (D9, unchanged)
VIDEO_BAR_ALPHA = 178              # 70 % of 255: the video page bar (fit to the Media Player: 3 brightness levels agree within 2)
VIDEO_BAR_FILL = "#1C1C1C"


def controls_alpha(home_visible, amount=1.0):
    """Opacity (0..255) of the control bar: 70 % while a video is shown, fully opaque on Home.
    `amount` (0..1) is the fade while the bar is appearing or disappearing."""
    base = 255 if home_visible else VIDEO_BAR_ALPHA
    return int(round(base * max(0.0, min(1.0, amount))))


CAPTION_GAP = 13                   # logical px right of the close button (see MainWindow._fit_visible)


def caption_gap(overhang):
    """Gap right of the close button. When the window trim already moved the bar inside the visible
    frame no extra gap is added."""
    return 0 if overhang != (0, 0) else CAPTION_GAP


def layered_alpha_apply(widget, alpha, prev):
    """Make a native child window alpha/255 see-through (Windows 8+ allows layered child windows).
    `prev` is what was applied last time; returns the new value to remember (None if nothing was done)."""
    if sys.platform != "win32":
        return None
    try:
        hwnd = int(widget.winId())
        if prev == (hwnd, alpha):
            return prev
        u32 = ctypes.windll.user32
        u32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        u32.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
        u32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
        u32.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
        h = ctypes.c_void_p(hwnd)
        ex = u32.GetWindowLongPtrW(h, -20)
        if alpha >= 255:
            if ex & 0x00080000:                                  # fully opaque: a normal window again,
                u32.SetWindowLongPtrW(h, -20, ex & ~0x00080000)  # layered windows flicker more on resize
        else:
            if not ex & 0x00080000:
                u32.SetWindowLongPtrW(h, -20, ex | 0x00080000)   # WS_EX_LAYERED
            u32.SetLayeredWindowAttributes(h, 0, alpha, 2)       # LWA_ALPHA
        return (hwnd, alpha)
    except Exception:
        return None


CURTAIN_HOME = "#2B2628"       # middle of the Home page gradient (#29262C to #2E2524)
CURTAIN_BLACK = "#000000"
class CurtainWidget(QWidget):
    """The page-change curtain: a native window (above VLC's own window) that shows a flat colour or a picture of a page.
    A picture of the Home page lets the change be a real cross-fade, like Windows Media Player (no dip to a flat colour)."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WA_NativeWindow)
        self.setAutoFillBackground(True)
        self._img = None
        self._img_gy = None          # screen y where the top of the picture belongs (the Stage moves 32 px between Home and the video page)

    def set_color(self, color):
        pal = self.palette()
        pal.setColor(QPalette.Window, QColor(color))
        self.setPalette(pal)

    def set_image(self, pix, gy=None):
        self._img = pix if (pix is not None and not pix.isNull()) else None
        self._img_gy = gy
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), self.palette().color(QPalette.Window))
        if self._img is not None:
            oy = 0
            if self._img_gy is not None:
                oy = int(self._img_gy - self.mapToGlobal(QPoint(0, 0)).y())
            p.drawPixmap(0, oy, self._img)
        p.end()


LOAD_SHOW_MS = 250            # the loading page shows only if the video is not on screen after this long
LOAD_SETTLE_S = 0.30          # the first picture is only ready this long after libVLC says so: the page stays until then
LOAD_GIVE_UP_S = 20.0         # no picture after this long: the bar comes back and a message shows
LOAD_TURN_MS = 1500           # one turn of the ring


class LoadingCover(QWidget):
    """Black page with a turning arc in the play ring colours while a video starts. PHASE 12b: an ordinary Qt widget (no native
    window): the picture surface stays hidden while the page shows, so nothing native has to sit above it. The control bar stays
    hidden until the first picture is there."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setAutoFillBackground(True)
        pal = self.palette()
        pal.setColor(QPalette.Window, QColor("#000000"))
        self.setPalette(pal)
        self._t0 = time.monotonic()
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self.update)
        self.hide()

    def start(self):
        self._t0 = time.monotonic()
        self._timer.start()
        self.show()

    def stop(self):
        self._timer.stop()
        self.hide()
        self.resize(1, 1)            # probable cause of a 2-frame black ring page at the first Home press: a hidden full-size page can leave its last picture

    def angle(self):
        return ((time.monotonic() - self._t0) * 1000.0 / LOAD_TURN_MS * 360.0) % 360.0

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor("#000000"))
        d = max(72.0, min(160.0, min(self.width(), self.height()) * 0.22))
        stroke = max(4.0, d / 14.0)
        r = d / 2 - stroke / 2
        p.translate(self.width() / 2.0, self.height() / 2.0)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor("#454545"), stroke))
        p.drawEllipse(QPointF(0, 0), r, r)
        tri = d * 0.15
        pts = [QPointF(-tri * 0.7, -tri), QPointF(-tri * 0.7, tri), QPointF(tri * 1.1, 0)]
        p.setPen(QPen(QColor(255, 255, 255, 245), 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(QColor(255, 255, 255, 245))
        p.drawPolygon(QPolygonF(pts))
        p.setBrush(Qt.NoBrush)
        p.rotate(self.angle())
        span = 95.0
        end = QPointF(r * math.sin(math.radians(span)), -r * math.cos(math.radians(span)))
        g = QLinearGradient(QPointF(0, -r), end)
        for pos, rgb in ((0.0, (237, 117, 37)), (0.25, (225, 90, 96)), (0.55, (191, 56, 134)), (1.0, (111, 46, 132))):
            g.setColorAt(pos, QColor(*rgb))
        pen = QPen(QBrush(g), stroke)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.drawArc(QRectF(-r, -r, 2 * r, 2 * r), 90 * 16, int(-span * 16))   # from 12 o'clock, clockwise
        p.end()


def _track_video_info(t):
    """The VideoTrack struct of a media track. Old python-vlc: track.u.video. python-vlc 3.0.21+ declares the C union as three
    separate pointer fields, so track.video points at the wrong memory (NULL or garbage: it crashed libVLC calls). The real union
    starts at the 'audio' field, so the pointer is read from there."""
    u = getattr(t, "u", None)
    if u is not None:
        return u.video.contents
    off = type(t).audio.offset
    addr = ctypes.c_void_p.from_address(ctypes.addressof(t) + off).value
    if not addr:
        return None
    return ctypes.cast(addr, ctypes.POINTER(vlc.VideoTrack)).contents


def _track_audio_info(t):
    """The AudioTrack struct (channels, rate) of a media track. Same python-vlc 3.0.21 union problem as _track_video_info:
    there is no track.u, so the pointer is read from the 'audio' field's place. None when the pointer is empty."""
    u = getattr(t, "u", None)
    if u is not None:
        return u.audio.contents
    off = type(t).audio.offset
    addr = ctypes.c_void_p.from_address(ctypes.addressof(t) + off).value
    if not addr:
        return None
    return ctypes.cast(addr, ctypes.POINTER(vlc.AudioTrack)).contents


def video_track_sar(player):
    """Pixel aspect (sar_num / sar_den) of the playing video's first video track, 1.0 when unknown."""
    try:
        media = player.get_media()
        for t in (media.tracks_get() or []):
            if t.type != vlc.TrackType.video:
                continue
            v = _track_video_info(t)
            if v is not None and v.sar_num and v.sar_den:
                return v.sar_num / v.sar_den
            break
    except Exception:
        pass
    return 1.0


def parse_aspect(text):
    """'16:9' -> 1.777..., None or junk -> None."""
    try:
        a, b = str(text).split(":")
        a, b = float(a), float(b)
        return a / b if a > 0 and b > 0 else None
    except Exception:
        return None


def picture_fit(box_w, box_h, ratio, zoom="fit"):
    """Size (w, h) of a picture with display aspect `ratio` in a box: 'fit' = whole picture inside (bars), 'fill' = box covered
    (cropped), a number = fit times that number."""
    if box_w <= 0 or box_h <= 0 or ratio <= 0:
        return 0.0, 0.0
    if box_w / box_h > ratio:
        fw, fh = box_h * ratio, float(box_h)          # box wider than the picture: bars left and right
        cw, ch = float(box_w), box_w / ratio
    else:
        fw, fh = float(box_w), box_w / ratio
        cw, ch = box_h * ratio, float(box_h)
    if zoom == "fill":
        return cw, ch
    if zoom == "fit":
        return fw, fh
    try:
        z = float(zoom)
    except Exception:
        z = 1.0
    return fw * z, fh * z


class _VideoWindow(QRasterWindow):
    """The video's own small window inside the video widget. It paints the pictures into ITS OWN buffer (not the shared Qt widget
    buffer), so a see-through control bar above it always blends with the real video, never with stale widget pixels (the "ghost" of
    the Home bar, seen Nov 13). Mouse, wheel and key events are passed on to the video widget / main window as if nothing was there."""

    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self.paints = 0
        self.setFlag(Qt.WindowDoesNotAcceptFocus, True)

    def paintEvent(self, e):
        t0 = time.perf_counter()
        p = QPainter(self)
        self.owner.paint_to(p, QRectF(0, 0, self.width(), self.height()))
        self.owner.paint_bar(p)                                 # PHASE 12c: the control bar of the video page (if this window paints it)
        p.end()
        self.paints += 1
        if len(self.owner.paint_ms) < 600:
            self.owner.paint_ms.append((time.perf_counter() - t0) * 1000)

    def _mouse(self, ev):
        if self.owner.bar_mouse(ev):                            # PHASE 12c: the bar is painted here, so its mouse events come here too
            if ev.type() == QEvent.MouseButtonPress:
                QTimer.singleShot(0, self.owner.give_focus_back)
            ev.accept()
            return
        me = QMouseEvent(ev.type(), ev.position(), ev.globalPosition(), ev.button(), ev.buttons(), ev.modifiers())
        QApplication.sendEvent(self.owner, me)
        if ev.type() == QEvent.MouseButtonPress:               # the picture window must never keep the keyboard focus
            QTimer.singleShot(0, self.owner.give_focus_back)
        ev.setAccepted(me.isAccepted())

    mousePressEvent = mouseReleaseEvent = mouseMoveEvent = mouseDoubleClickEvent = _mouse

    def wheelEvent(self, ev):
        if self.owner.bar_wheel(ev):
            ev.accept()
            return
        we = QWheelEvent(ev.position(), ev.globalPosition(), ev.pixelDelta(), ev.angleDelta(), ev.buttons(), ev.modifiers(),
                         ev.phase(), ev.inverted())
        QApplication.sendEvent(self.owner, we)
        ev.accept()

    def leaveEvent(self, ev):
        self.owner.bar_left()

    def _key(self, ev):
        top = self.owner.window()
        QApplication.sendEvent(top, QKeyEvent(ev.type(), ev.key(), ev.modifiers(), ev.text(), ev.isAutoRepeat(), ev.count()))
        ev.accept()

    keyPressEvent = keyReleaseEvent = _key

    def focusInEvent(self, ev):
        QTimer.singleShot(0, self.owner.give_focus_back)


class VideoSurface(QWidget):
    """PHASE 12a: the video page is drawn by Qt. libVLC hands every picture to us through video callbacks (RV32, the video's OWN
    size); Qt draws it scaled once, centred, with aspect ratio and zoom applied here, into a small child window (_VideoWindow) that
    sits inside this native widget (same structure as VLC's old window: masks, moves and stacking work as before).
    The libVLC callbacks run on libVLC's threads: they only touch the buffers and post one queued signal, never a widget."""
    frameArrived = Signal()
    firstFrame = Signal()            # the first picture of a (new) video was delivered (UI thread)
    NBUF = 3

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_NativeWindow)
        self.setAutoFillBackground(True)
        pal = self.palette()
        pal.setColor(QPalette.Window, QColor(0, 0, 0))
        self.setPalette(pal)
        self.setMouseTracking(True)
        self._win = _VideoWindow(self)
        # NOT createWindowContainer: Qt hangs that window on the TOP-LEVEL window, above the bar, title bar and loading ring
        # (found on the user's PC, Nov 13). The picture window is made a child of THIS widget's native window, like VLC's was.
        self.winId()                                   # this widget gets its native window now (the same order as VLC's old window)
        self._win.setParent(self.windowHandle())
        self._frame = None            # (buffer, width, height, pitch): swapped in one assignment, painted by the UI thread
        self._bufs, self._ptrs, self._w = [], [], 0
        self._fmt = (0, 0, 0)
        self._pending = False
        self._first = False
        self._player = None
        self._cbs = None
        self._sar, self._need_sar = 1.0, True
        self.sar_fn = None            # optional: callable returning the pixel aspect (read once per new picture size)
        self._aspect = None           # forced display aspect (float) or None
        self._zoom = "fit"
        self._crop = None             # (l, t, r, b) fractions of the frame, used only in the preview slot
        self.slot_mode = False        # the picture is drawn by the bar's preview slot, not by this widget
        self.slot_widget = None
        self.move_paint = None        # PHASE 12b: during a Home <-> video move this surface paints the whole move itself (no window mask)
        self.bar = None               # PHASE 12c: the control bar this surface paints over the picture (video page), or None
        self.bar_amount = 1.0         # its fade (0..1)
        self._bar_img = None          # cached rendering of the bar (ARGB, see-through background, solid icons)
        self._bar_dirty = True
        self._bar_rendering = False
        self._bar_hover = None        # the widget of the bar under the mouse
        self._bar_grab = None         # the widget that got the button press (it keeps the mouse until the release)
        self._bar_filtered = None
        self._bar_timer = QTimer(self)    # PHASE 12c: Qt sends no paint requests for a bar that sits UNDER the picture window: look again 30 x a second
        self._bar_timer.setInterval(200)
        self._bar_fast_until = 0.0        # until this time (monotonic) the check runs 30 x a second, else 5 x a second
        self._bar_timer.timeout.connect(self._bar_tick)
        self.frames = 0
        self.paint_ms = []
        self.frameArrived.connect(self._on_frame, Qt.QueuedConnection)

    # ---- libVLC side (libVLC threads)
    def attach(self, player):
        """Give libVLC the callbacks. Safe to call again for every new media (it just sets them again)."""
        self._player = player
        if self._cbs is None:
            self._cbs = self._make_callbacks()
        lock, unlock, disp, setup, clean = self._cbs
        player.video_set_callbacks(lock, unlock, disp, None)
        vlc.libvlc_video_set_format_callbacks(player, setup, clean)

    def _make_callbacks(self):
        me = self
        SetupCb = ctypes.CFUNCTYPE(ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint),
                                   ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint))
        CleanCb = ctypes.CFUNCTYPE(None, ctypes.c_void_p)

        @SetupCb
        def setup(opaque, chroma, w, h, pitches, lines):
            try:
                W, H = int(w[0]), int(h[0])
                try:                                   # the size libVLC gives here is padded (368 for 360) or stretched: ask for the real one
                    vs = me._player.video_get_size(0)
                    if vs and vs[0] > 0 and vs[1] > 0:
                        W, H = int(vs[0]), int(vs[1])
                except Exception:
                    pass
                W, H = max(16, W), max(16, H)
                w[0], h[0] = W, H
                ctypes.memmove(chroma, b"RV32", 4)
                pitch = ((W * 4 + 63) // 64) * 64
                pitches[0], lines[0] = pitch, H
                if (W, H, pitch) != me._fmt or not me._bufs:
                    me._bufs = [ctypes.create_string_buffer(pitch * H) for _ in range(me.NBUF)]
                    me._ptrs = [ctypes.cast(b, ctypes.c_void_p) for b in me._bufs]
                    me._fmt = (W, H, pitch)
                me._w = 0
                me._need_sar = True
                me._first = True
                return me.NBUF
            except Exception:
                return 0

        @CleanCb
        def cleanup(opaque):
            pass

        @vlc.CallbackDecorators.VideoLockCb
        def lock(opaque, planes):
            planes[0] = me._ptrs[me._w]
            return None

        @vlc.CallbackDecorators.VideoUnlockCb
        def unlock(opaque, picture, planes):
            pass

        @vlc.CallbackDecorators.VideoDisplayCb
        def display(opaque, picture):
            try:
                W, H, pitch = me._fmt
                me._frame = (me._bufs[me._w], W, H, pitch)
                me._w = (me._w + 1) % me.NBUF
                me.frames += 1
                if not me._pending:
                    me._pending = True
                    me.frameArrived.emit()
            except Exception:
                pass

        return lock, unlock, display, setup, cleanup

    # ---- UI side
    def reset(self):
        """A new video is coming: drop the old picture (black until the first new one)."""
        self._frame = None
        self._sar, self._need_sar = 1.0, True
        self.update()

    def has_frame(self):
        return self._frame is not None

    def frame_size(self):
        f = self._frame
        return (f[1], f[2]) if f else (0, 0)

    def _on_frame(self):
        self._pending = False
        if self._need_sar and self._frame is not None:
            self._need_sar = False
            try:
                if self.sar_fn:
                    self._sar = float(self.sar_fn() or 1.0)
            except Exception:
                self._sar = 1.0
        if self._first:
            self._first = False
            self.firstFrame.emit()
        if self.slot_mode:
            if self.slot_widget is not None:
                self.slot_widget.update()
        elif self.isVisible():
            self._win_do(lambda w_: w_.update())

    def set_aspect(self, ratio_text):
        self._aspect = parse_aspect(ratio_text)
        self.update()
        if self.slot_widget is not None and self.slot_mode:
            self.slot_widget.update()

    def set_zoom(self, z):
        self._zoom = z
        self.update()

    def set_crop(self, rect):
        """rect: (l, t, r, b) fractions of the frame or None. Only the preview slot uses it."""
        self._crop = rect
        if self.slot_widget is not None and self.slot_mode:
            self.slot_widget.update()

    def display_ratio(self, w, h):
        return self._aspect if self._aspect else max(0.01, w * self._sar) / max(1, h)

    def image(self):
        """The current picture as a QImage (shares the buffer: use it right away or copy)."""
        f = self._frame
        if f is None:
            return None, 0, 0
        buf, w, h, pitch = f
        return QImage(buf, w, h, pitch, QImage.Format_RGB32), w, h

    def geometry_for(self, box_w, box_h, use_crop=False):
        """(source rect, destination rect) in the frame's pixels / the box's pixels, or None without a picture."""
        f = self._frame
        if f is None or box_w <= 0 or box_h <= 0:
            return None
        _buf, w, h, _pitch = f
        sx, sy, sw, sh = 0.0, 0.0, float(w), float(h)
        if use_crop and self._crop:
            l, t, r, b = self._crop
            sx, sy = l * w, t * h
            sw, sh = max(2.0, (r - l) * w), max(2.0, (b - t) * h)
        ratio = self.display_ratio(sw, sh) if not (use_crop and self._crop) else sw * self._sar / sh
        zoom = "fill" if use_crop else self._zoom
        pw, ph = picture_fit(box_w, box_h, ratio, zoom)
        dst = QRectF((box_w - pw) / 2.0, (box_h - ph) / 2.0, pw, ph)
        return QRectF(sx, sy, sw, sh), dst

    def draw_into(self, p, box, use_crop=False):
        """Draw the picture inside `box` (QRectF) with painter p. Returns False when there is no picture."""
        g = self.geometry_for(box.width(), box.height(), use_crop)
        if g is None:
            return False
        src, dst = g
        img, _w, _h = self.image()
        if img is None:
            return False
        dst.translate(box.x(), box.y())
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        p.save()
        p.setClipRect(box, Qt.IntersectClip)
        p.drawImage(dst, img, src)
        p.restore()
        return True

    def picture_rect(self):
        """Where the picture (without the black bars) sits in this widget, rounded inwards; None when there is no picture."""
        g = self.geometry_for(self.width(), self.height())
        if g is None:
            return None
        d = g[1].intersected(QRectF(self.rect()))
        l, t = math.ceil(d.left()), math.ceil(d.top())
        r, b = math.floor(d.right()), math.floor(d.bottom())
        return QRect(l, t, max(1, r - l), max(1, b - t))

    def snapshot_image(self):
        """The current picture at its display shape (pixel aspect or forced aspect applied), or None."""
        img, w, h = self.image()
        if img is None:
            return None
        ratio = self.display_ratio(w, h)
        out_w, out_h = (w, h) if abs(ratio - w / h) < 0.002 else (max(1, int(round(h * ratio))), h)
        img = img.copy()
        return img if (out_w, out_h) == (w, h) else img.scaled(out_w, out_h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)

    def setMask(self, region):
        """The widget's mask (the Home <-> video move, the rounded slot) must clip the picture window too: it is a child window,
        and a window region on the parent alone may not hide it."""
        QWidget.setMask(self, region)
        self._win_do(lambda w_: w_.setMask(QRegion(region)))

    def clearMask(self):
        QWidget.clearMask(self)
        self._win_do(lambda w_: w_.setMask(QRegion()))

    def _win_do(self, fn):
        """Run fn on the picture window; at shutdown Qt may have deleted it already (then nothing to do)."""
        try:
            fn(self._win)
        except RuntimeError:
            pass

    def resizeEvent(self, e):
        self._win_do(lambda w_: w_.setGeometry(0, 0, self.width(), self.height()))
        super().resizeEvent(e)

    def showEvent(self, e):
        self._win_do(lambda w_: (w_.setGeometry(0, 0, self.width(), self.height()), w_.show()))
        super().showEvent(e)

    def hideEvent(self, e):
        self._win_do(lambda w_: w_.hide())
        super().hideEvent(e)

    def set_move(self, state):
        """PHASE 12b: state = None, or {"mode": "up"|"down", "clip": QRegion, "home": QPixmap|None, "hoff": int, "v": 0..1}.
        While it is set, this one surface paints a picture of the Home page and the video inside `clip`; only update() runs per step."""
        self.move_paint = state
        self.update()

    def repaint_now(self):
        """Paint the picture window at once (before the event loop), so a window that was just shown or resized never shows an unpainted frame."""
        def go(w_):
            w_.update()
            QApplication.sendPostedEvents(w_, QEvent.UpdateRequest)       # QRasterWindow has no repaint(): deliver its update request now
        self._win_do(go)

    def _paint_move(self, p, box):
        mv = self.move_paint
        p.fillRect(box, QColor(0, 0, 0))
        home = mv.get("home")
        if home is not None:
            p.save()
            # Home dims away as the panel rises (Media Player); on the way down it stays at full strength, the picture is wiped off it
            p.setOpacity(max(0.0, 1.0 - mv.get("v", 0.0)) if mv.get("mode") == "up" else 1.0)
            p.drawPixmap(QPoint(0, int(mv.get("hoff", 0))), home)
            p.restore()
        clip = mv.get("clip")
        if clip is not None and not clip.isEmpty():
            p.save()
            p.setClipRegion(clip)
            if mv.get("mode") == "up":
                p.fillRect(box, QColor(0, 0, 0))
            self.draw_into(p, box)
            p.restore()

    def paint_to(self, p, box):
        """Paint black, then the picture (unless the Home slot draws it). Used by the picture window and by the tests."""
        if self.move_paint is not None:
            self._paint_move(p, box)
            return
        p.fillRect(box, QColor(0, 0, 0))
        if not self.slot_mode:
            self.draw_into(p, box)

    # ---- PHASE 12c: the control bar of the video page is painted by the picture window (see-through background, solid icons)
    def set_bar(self, bar, amount=1.0):
        """bar: the ControlsBar to paint over the picture, or None. The bar widget keeps all its logic (layout, animations, signals);
        this surface only draws a rendering of it and hands it the mouse events."""
        if bar is not self.bar:
            self._bar_leave()
            self._bar_grab = None
            self.bar = bar
            self._bar_dirty = True
            if bar is not None and self._bar_filtered is not bar:
                self._bar_filtered = bar
                for w_ in [bar] + bar.findChildren(QWidget):
                    w_.installEventFilter(self)           # a paint request of the bar or of any part of it = the rendering is old
        self.bar_amount = max(0.0, min(1.0, float(amount)))
        if bar is not None:
            self._bar_fast()
        if bar is not None and not self._bar_timer.isActive():
            self._bar_timer.start()
        elif bar is None:
            self._bar_timer.stop()
        self.update()

    def _render_bar(self, img):
        """The bar's own window has a one pixel shape (set_native_hidden), so render() must IGNORE the shape or it draws nothing."""
        flags = QWidget.RenderFlag.DrawWindowBackground | QWidget.RenderFlag.DrawChildren | QWidget.RenderFlag.IgnoreMask
        self.bar.render(img, QPoint(), QRegion(), flags)

    def _bar_fast(self):
        """Something moves (mouse, fade, a change was found): look 30 x a second for the next 0.5 s."""
        self._bar_fast_until = time.monotonic() + 0.5
        if self._bar_timer.interval() != 33:
            self._bar_timer.setInterval(33)

    def _bar_tick(self):
        """The bar may have changed (hover, time, a tween): render it again and repaint the window only if the picture of it differs.
        Runs only while the bar is shown (it hides itself after 3 s without mouse movement), so a hidden bar costs nothing."""
        if self._bar_timer.interval() == 33 and time.monotonic() > self._bar_fast_until:
            self._bar_timer.setInterval(200)             # quiet again: slow check (the time and seek line still move 4 x a second)
        r = self.bar_rect()
        if r is None or self._bar_img is None or self._bar_rendering:
            return
        new = QImage(self._bar_img.size(), QImage.Format_ARGB32_Premultiplied)
        new.setDevicePixelRatio(self._bar_img.devicePixelRatio())
        new.fill(0)
        self._bar_rendering = True
        try:
            self._render_bar(new)
        finally:
            self._bar_rendering = False
        if new != self._bar_img:
            self._bar_img = new
            self._win_do(lambda w_: w_.update())

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Paint and not self._bar_rendering and self.bar is not None:
            self._bar_dirty = True
            self._win_do(lambda w_: w_.update())
        return False

    def bar_rect(self):
        """Where the bar sits in this surface (None: it is not painted now)."""
        b = self.bar
        if b is None or not b.isVisible() or self.bar_amount <= 0.003:
            return None
        return QRect(b.x() - self.x(), b.y() - self.y(), b.width(), b.height())

    def paint_bar(self, p):
        r = self.bar_rect()
        if r is None:
            return
        dpr = 1.0
        try:
            dpr = float(self._win.devicePixelRatio()) if self._win is not None else 1.0
        except Exception:
            pass
        want = QSize(max(1, round(r.width() * dpr)), max(1, round(r.height() * dpr)))
        if self._bar_img is None or self._bar_img.size() != want or self._bar_img.devicePixelRatio() != dpr:
            self._bar_img = QImage(want, QImage.Format_ARGB32_Premultiplied)
            self._bar_img.setDevicePixelRatio(dpr)
            self._bar_dirty = True
        if self._bar_dirty:
            self._bar_dirty = False
            self._bar_img.fill(0)
            self._bar_rendering = True
            try:
                self._render_bar(self._bar_img)
            finally:
                self._bar_rendering = False
        p.save()
        p.setOpacity(self.bar_amount)
        p.drawImage(QPointF(r.topLeft()), self._bar_img)
        p.restore()

    def _bar_chain(self, w_):
        out = []
        while w_ is not None:
            out.append(w_)
            if w_ is self.bar:
                break
            w_ = w_.parentWidget()
        return out

    def _bar_target(self, pos):
        """(widget, its local point, point in the bar) under pos (surface coordinates), or None when pos is not on the painted bar."""
        r = self.bar_rect()
        if r is None or not r.contains(pos.toPoint()):
            return None
        lp = QPoint(pos.toPoint().x() - r.x(), pos.toPoint().y() - r.y())
        w_ = self._bar_child_at(self.bar, lp)
        return w_, w_.mapFrom(self.bar, lp), lp

    def _bar_child_at(self, parent, p):
        """The deepest visible child of parent under p (p in parent's coordinates), by rectangles only. QWidget.childAt() cannot be used:
        it also asks the bar's own window shape (one pixel, set_native_hidden) and so finds nothing."""
        for ch in reversed(parent.children()):
            if isinstance(ch, QWidget) and not ch.isWindow() and ch.isVisible() and ch.geometry().contains(p):
                return self._bar_child_at(ch, p - ch.pos())
        return parent

    def _bar_set_hover(self, new):
        old = self._bar_hover
        if new is old:
            return
        oc, nc = self._bar_chain(old) if old is not None else [], self._bar_chain(new) if new is not None else []
        for w_ in oc:
            if w_ not in nc:
                w_.setAttribute(Qt.WA_UnderMouse, False)
                QApplication.sendEvent(w_, QEvent(QEvent.Leave))
        for w_ in reversed(nc):
            if w_ not in oc:
                w_.setAttribute(Qt.WA_UnderMouse, True)
                g = w_.mapToGlobal(QPoint(1, 1))
                QApplication.sendEvent(w_, QEnterEvent(QPointF(1, 1), QPointF(1, 1), QPointF(g)))
        self._bar_hover = new
        cur = None                                              # the cursor the widget under the mouse asks for (the picture window shows it)
        w_ = new
        while w_ is not None and cur is None:
            if w_.testAttribute(Qt.WA_SetCursor):
                cur = w_.cursor()
            w_ = w_.parentWidget() if w_ is not self.bar else None
        if cur is not None:
            self._win_do(lambda x: x.setCursor(cur))
        else:
            c0 = QWidget.cursor(self)
            self._win_do(lambda x: x.setCursor(c0) if self.testAttribute(Qt.WA_SetCursor) else x.unsetCursor())

    def _bar_leave(self):
        if self._bar_hover is not None:
            self._bar_set_hover(None)

    def bar_left(self):
        """The mouse left the picture window."""
        if self._bar_grab is None:
            self._bar_leave()

    def bar_mouse(self, ev):
        """Hand a mouse event of the picture window to the painted bar. True = the bar used it (it is not a click on the picture)."""
        if self.bar is None:
            return False
        t, pos = ev.type(), ev.position()
        hit = self._bar_target(pos)
        if t == QEvent.MouseButtonRelease and self._bar_grab is not None:
            g, self._bar_grab = self._bar_grab, None
            lp = g.mapFrom(self.bar, self._bar_local(pos))
            QApplication.sendEvent(g, QMouseEvent(t, QPointF(lp), ev.globalPosition(), ev.button(), ev.buttons(), ev.modifiers()))
            self._bar_set_hover(hit[0] if hit else None)
            return True
        if t == QEvent.MouseMove and self._bar_grab is not None:                    # a drag goes on outside the bar too
            g = self._bar_grab
            lp = g.mapFrom(self.bar, self._bar_local(pos))
            QApplication.sendEvent(g, QMouseEvent(t, QPointF(lp), ev.globalPosition(), ev.button(), ev.buttons(), ev.modifiers()))
            return True
        if hit is None:
            self._bar_leave()
            return False
        w_, lp, _bp = hit
        if t in (QEvent.MouseButtonPress, QEvent.MouseButtonDblClick):
            self._bar_set_hover(w_)
            self._bar_grab = w_
        elif t == QEvent.MouseMove:
            self._bar_set_hover(w_)
        QApplication.sendEvent(w_, QMouseEvent(t, QPointF(lp), ev.globalPosition(), ev.button(), ev.buttons(), ev.modifiers()))
        self._bar_dirty = True
        self._bar_fast()
        self._win_do(lambda x: x.update())
        return True

    def _bar_local(self, pos):
        r = self.bar_rect() or QRect(self.bar.x() - self.x(), self.bar.y() - self.y(), 0, 0)
        return QPoint(int(round(pos.x())) - r.x(), int(round(pos.y())) - r.y())

    def bar_wheel(self, ev):
        if self.bar is None:
            return False
        hit = self._bar_target(ev.position())
        if hit is None:
            return False
        w_, lp, _bp = hit
        QApplication.sendEvent(w_, QWheelEvent(QPointF(lp), ev.globalPosition(), ev.pixelDelta(), ev.angleDelta(), ev.buttons(),
                                               ev.modifiers(), ev.phase(), ev.inverted()))
        return True

    def render_image(self, w=None, h=None, bar=False):
        """What the picture window shows, as a QImage (tests and checks; the real window paints the same way).
        bar=True: with the painted control bar on top (the S key saves the picture WITHOUT the bar)."""
        w, h = w or self.width(), h or self.height()
        img = QImage(w, h, QImage.Format_RGB32)
        p = QPainter(img)
        self.paint_to(p, QRectF(0, 0, w, h))
        if bar:
            self.paint_bar(p)
        p.end()
        return img

    def update(self, *a):
        QWidget.update(self)
        self._win_do(lambda w_: w_.update())

    def setCursor(self, c):
        QWidget.setCursor(self, c)
        self._win_do(lambda w_: w_.setCursor(c))

    def unsetCursor(self):
        QWidget.unsetCursor(self)
        self._win_do(lambda w_: w_.unsetCursor())

    def give_focus_back(self):
        """The picture window never keeps the keyboard: shortcuts belong to the main window."""
        try:
            top = self.window()
            if QApplication.focusWindow() is self._win and top.windowHandle() is not None:
                top.windowHandle().requestActivate()
                top.setFocus()
        except Exception:
            pass


class Stage(QWidget):
    raisedChild = Signal()                 # a native child was raised: the title bar must go back on top
    miniShown = Signal(bool)               # PHASE B: the live video moved into / out of the Home slot

    def __init__(self, store, parent=None):
        super().__init__(parent)
        self._alpha_done = None
        self._fade_gen = 0               # which curtain run a timer belongs to (a timer of an older run must do nothing)
        self._loading = False
        self._load_t0 = 0.0
        self._load_target = 0            # resume position (ms) the picture must reach before the bar shows
        self._load_seen = None           # when VLC first said Playing with a video size
        self._load_ready = None
        self._load_show_ms = LOAD_SHOW_MS
        self._load_ready = None          # when the ready condition first held (the page stays LOAD_SETTLE_S longer)
        self._load_show_ms = LOAD_SHOW_MS
        self._noslide = False            # the bar fades in without drifting (nothing repaints a black video window behind it)
        self._ctrl_shown = True
        self._opaque_file = (store.dir / "opaque_controls.txt") if hasattr(store, "dir") else None
        self._native_bar_file = (store.dir / "native_bar.txt") if hasattr(store, "dir") else None   # PHASE 12c: empty file = the old see-through native bar
        self._painted = False            # PHASE 12c: the picture window paints the video-page bar (the native bar sits under it)
        self.setAutoFillBackground(True)
        pal = self.palette()
        pal.setColor(QPalette.Window, QColor("#000000"))
        self.setPalette(pal)
        self.setMouseTracking(True)

        self.video = VideoSurface(self)                  # PHASE 12a: an ordinary Qt widget, libVLC draws into it through callbacks
        self.video.setMouseTracking(True)
        self.video.slot_widget = None                    # set below, once the bar exists

        self.home = HomeView(store, self)
        self.controls = ControlsBar(self)
        self.video.slot_widget = self.controls.thumb     # in the Home slot the bar's preview widget draws the same picture
        self.loader = LoadingCover(self)
        self.overlay = False
        self.mini_mode = False
        self.mini_live = False
        self._masked = False
        self.auto = False                  # video page: picture fills the window, bars fade in and out
        self._resizing = False
        self._need_raise = True
        self._was_mini = False
        self._mini_fix = QTimer(self)
        self._mini_fix.setSingleShot(True)
        self._mini_fix.setInterval(130)
        self._mini_fix.timeout.connect(self._end_resize)
        self.controls_visible = True
        self._no_controls = False
        self.ctrl = Tween(self, 1.0, 260, self.relayout)   # 1 = controls fully shown
        # Page change Home <-> video page: a black see-through window fades in, the pages switch under it, it fades out
        # (hides the black frames while VLC redraws at the new size). An empty no_transition.txt switches it off.
        self.curtain = CurtainWidget(self)
        self.curtain.setPalette(pal)
        self.curtain.hide()
        self.fade_enabled = sys.platform == "win32" and not (hasattr(store, "dir") and (store.dir / "no_transition.txt").exists())
        self._fading = False
        self._wait_mini = False
        self._mini_ok = 0
        self._fade_switch = None
        self._cur_done = None
        self.cur_alpha = 0
        self.cur_t = Tween(self, 0.0, self.FADE_OUT_MS, self._curtain_step)
        # Home <-> video page (seamless, no curtain, no fade): the video window keeps ONE size while it moves and a mask edge moves over it;
        # Home stays live. Home -> video ("up"): the edge rises out of the bar. Video -> Home ("down"): the edge sinks into the bar.
        # The picture is never resized while it moves (VLC lags behind a resize).
        self.pic_rect_fn = None           # set by the window: where the picture (without the black bars) sits in the video window, or None when unsure
        self.home_off = TITLEBAR_H        # Home sits this far below the top of the Stage (under the title bar); the Stage itself never moves or resizes
        self._move = None                 # None, or {"done": callable, "gen": n} while the move runs
        self._move_v = 0.0
        self._move_gen = 0
        self._move_finishing = False
        self._log_fn = lambda msg: None   # set by the window (player.log)
        self.move_t = Tween(self, 0.0, self.MOVE_MS, self._move_step)
        self.winId()
        self.curtain.winId()
        self.home.fade_pages = self.fade_enabled
        self.relayout()

    FADE_OUT_MS, HOLD_MS, FADE_IN_MS = 110, 80, 200      # to dark, dark while the pages switch, back to the picture
    MOVE_MS = 250                                        # the edge rises out of / sinks into the bar
    MOVE_WAIT_MS = 0                                    # the video window gets its full size first (hidden under the bar) so VLC can follow before the edge moves
    MINI_WAIT_MS = 600                                   # video -> Home: stay dark until the live video sits in the slot (at most 600 ms)

    def fade_switch(self, switch):
        """Run switch() (a page change) behind the flat curtain: it fades in, the switch runs, it fades out (used for the Play queue).
        Returns False when the transition is off (the caller then switches at once), True when it took over the switch or
        ignored a second request during a running fade. Home <-> video page has no curtain: see move_to_video / move_to_home."""
        if not self.fade_enabled:
            return False
        if self._fading or self._move is not None:
            return True
        self._fading = True
        self._fade_gen += 1
        g = self._fade_gen
        QTimer.singleShot(2500, lambda: self._fade_guard(g))      # safety net: no run may last longer than this
        self._set_curtain(CURTAIN_HOME)
        self._fade_switch = switch
        self.curtain.setGeometry(0, 0, self.width(), self.height())
        self.cur_t.set(0.0)
        self.curtain.show()
        self._curtain_on_top()
        self.cur_t.to(1.0, self.FADE_OUT_MS, EASE_INOUT)
        return True

    def _move_mask(self):
        """The part of the (full size) video window that shows: full width, its top edge rising from the top of the bar to the top of the page."""
        w, h = self.width(), self.height()
        bt = max(0, h - self.controls.height())
        v = max(0.0, min(1.0, self._move_v))
        mv = self._move or {}
        if mv.get("mode", "up") == "up":
            top = int(round(bt * (1.0 - v)))
            return QRegion(QRect(0, top, w, max(1, h - top)))
        top = int(round(bt * v))
        pic = mv.get("pic")
        if pic is None:
            return QRegion(QRect(0, top, w, max(1, h - top)))
        # video -> Home: only the PICTURE is wiped away, the black bars around it never show (Home is visible there from the start)
        t = max(top, pic.top())
        if t >= pic.top() + pic.height():
            return QRegion(QRect(0, h - 1, 1, 1))        # nothing of the picture is left; never an empty region (Qt reads that as "no mask")
        return QRegion(QRect(pic.left(), t, pic.width(), pic.top() + pic.height() - t))

    def _home_snapshot(self):
        """PHASE 12b: a picture of the Home page (without the bar) for the surface to paint during a move; None when Home is not laid out."""
        try:
            if self.home.width() < 10 or self.home.height() < 10 or not self._home_laid_out():
                return None
            pm = self.home.grab()
            return None if pm.isNull() else pm
        except Exception:
            return None

    def _move_sync(self, home=None):
        """Hand the surface the current state of the move (clip edge, step, Home picture). Nothing native changes: it only repaints."""
        mv = self._move
        if mv is None:
            return
        if home is not None:
            mv["home"] = home
        st = self.video.move_paint
        if st is None:
            st = {"mode": mv.get("mode", "up"), "home": mv.get("home"), "hoff": self.home_off, "clip": None, "v": 0.0}
            self.video.move_paint = st
        st["home"] = mv.get("home")
        st["hoff"] = self.home_off
        st["v"] = self._move_v
        st["clip"] = self._move_mask()
        self.video.update()

    def move_to_video(self, done):
        """Home -> video page. Returns False when the move cannot run (the caller then switches at once).
        Home stays live. The video window is set to the full page size once, with a mask that shows only a strip exactly on the
        bar (the bar stays above it). After MOVE_WAIT_MS the mask's top edge rises to the top of the page in MOVE_MS (ease-out).
        Then done() flips the page; nothing changes on screen."""
        self.move_why = ""
        if not self.fade_enabled:
            self.move_why = "transitions are off (not Windows, or no_transition.txt)"
            return False
        if self._fading or self._move is not None:
            self.move_why = "a move or fade is already running"
            return True                                  # a move is running: ignore the second request
        no = [n for n, bad in (("home not visible", not self.home.isVisible()), ("stage not visible", not self.isVisible()),
                               ("window too small", self.width() < 200 or self.height() < 200), ("resizing", self._resizing),
                               ("no controls", self._no_controls), ("mini mode", self.mini_mode), ("loading", self._loading)) if bad]
        if no:
            self.move_why = ", ".join(no)
            return False
        snap = self._home_snapshot()
        if snap is None:
            self.move_why = "no picture of the Home page"
            return False
        self._move_gen += 1
        g = self._move_gen
        self._move = {"done": done, "gen": g, "mode": "up", "home": snap}
        self._move_finishing = False
        self._move_v = 0.0
        QTimer.singleShot(1500, lambda: self._move_guard(g))      # safety net
        self._round_mask(False)
        self.video.move_paint = None
        self._move_sync()                                # the surface first learns what to paint (Home picture + a strip on the bar) ...
        g0 = QRect(0, 0, self.width(), self.height())
        if self.video.geometry() != g0:
            self.video.setGeometry(g0)                   # ... then the size, once
        if not self.video.isVisible():
            self.video.show()
        self.video.repaint_now()                         # painted at its new size before the event loop runs: no unpainted frame over Home
        self.video.stackUnder(self.controls)             # the rising picture must be ABOVE the Home page, and the bar stays above it
        self.controls.raise_()
        self.raisedChild.emit()
        if self._was_mini:
            self._was_mini = False
            self.miniShown.emit(False)                   # the preview slot is gone: its overlays step aside
        QTimer.singleShot(self.MOVE_WAIT_MS, lambda: self._move_begin(g))      # 0 ms since 12b: Qt draws at any size, nothing has to catch up
        return True

    def move_to_home(self, flip):
        """Video page -> Home, seamless. Returns False when it cannot run (the caller then switches at once).
        flip() shows Home right away (Home is live under the video, which keeps its full size). A mask edge then sinks from the top
        of the page into the bar over MOVE_MS (ease-out), uncovering Home. At the end the video goes into the preview slot."""
        if not self.fade_enabled:
            return False
        if self._fading or self._move is not None:
            return True
        if (self.home.isVisible() or not self.isVisible() or self.width() < 200 or self.height() < 200
                or self._resizing or self.mini_mode or self._loading):
            return False
        self._move_gen += 1
        g = self._move_gen
        pic = None
        try:
            pic = self.pic_rect_fn() if self.pic_rect_fn else None
        except Exception:
            pic = None
        self._move = {"done": None, "gen": g, "mode": "down", "pic": pic}
        self._move_finishing = False
        self._move_v = 0.0
        QTimer.singleShot(1500, lambda: self._move_guard(g))      # safety net
        self.video.move_paint = None
        self._run_switch(flip)                           # Home is shown now; relayout leaves the video alone while the move runs
        if self._move is None or self._move["gen"] != g:
            return True
        snap = self._home_snapshot()                     # Home, as it is now, under the sinking picture
        if snap is None:                                 # Home is not laid out yet: no move, Home just shows (never a wrong picture)
            self._move = None
            self.video.set_move(None)
            self._need_raise = True
            self.relayout()
            self._log_fn("move video -> Home: no picture of the Home page, Home shown at once")
            return True
        self._move_sync(snap)                            # the whole video still shows (edge at the top)
        if self.video.isVisible():
            self.video.repaint_now()
            self.video.raise_()
        self.controls.raise_()                           # the bar and the title bar stay above the video
        self.raisedChild.emit()
        self.move_t.set(0.0)
        self.move_t.to(1.0, self.MOVE_MS, EASE_OUT)
        return True

    def _move_begin(self, g):
        if self._move is None or self._move["gen"] != g:
            return
        self.move_t.set(0.0)
        self.move_t.to(1.0, self.MOVE_MS, EASE_OUT)

    def _move_step(self):
        if self._move is None:
            return
        v = max(0.0, min(1.0, self.move_t.value))
        self._move_v = 1.0 if v >= 0.999 else v           # the last step is exactly the full page
        self._move_sync()
        if self._move_v >= 1.0 and not self._move_finishing:
            self._move_finishing = True
            g = self._move["gen"]
            QTimer.singleShot(0, lambda: self._move_finish(g))

    def _move_finish(self, g):
        mv = self._move
        if mv is None or mv["gen"] != g:
            return
        self._move = None
        self._move_finishing = False
        self._move_v = 1.0
        self.video.set_move(None)
        if mv["mode"] == "up":
            self._run_switch(mv["done"])                 # flips the page flags; the margin is already the video page's, nothing changes on screen
        else:
            self._need_raise = True                      # the bar was raised above the video for the move: the live video must go back ABOVE the bar in the slot
            self.relayout()                              # the video goes into the preview slot, once

    def _move_guard(self, g):
        if self._move is not None and self._move["gen"] == g:
            self._move_finish(g)

    def _run_switch(self, sw):
        try:
            sw()
        except Exception:
            sys.excepthook(*sys.exc_info())              # shown in CMD, but the curtain must still lift

    def _schedule_lift(self, g):
        """After the switch: wait (dark) until the new page is ready, then lift the curtain."""
        if self.mini_live and self.home.isVisible():
            self._wait_mini = True                       # Home with the live slot: the card picture must not show first
            self._mini_ok = 0
            QTimer.singleShot(self.MINI_WAIT_MS, lambda: self._fade_back(g))    # the cap
            QTimer.singleShot(30, lambda: self._poll_mini(g))
        else:
            QTimer.singleShot(self.HOLD_MS, lambda: self._fade_back(g))

    COVER_HOLD_MS = 150

    def fade_cover(self):
        """A page change that cannot be delayed (a video starts): the curtain is dark at once, the caller switches,
        then it fades out. No-op when the transition is off or already running."""
        if not self.fade_enabled or self._fading:
            return False
        self._fading = True
        self._fade_gen += 1
        g = self._fade_gen
        QTimer.singleShot(2500, lambda: self._fade_guard(g))
        self._set_curtain(CURTAIN_BLACK)             # a video starts: black, like the loading page that follows
        self._fade_switch = None
        self.curtain.setGeometry(0, 0, self.width(), self.height())
        self.cur_t.set(1.0)
        self.curtain.show()
        self._curtain_on_top()
        QTimer.singleShot(self.COVER_HOLD_MS, lambda: self._fade_back(g))
        return True

    def _set_curtain(self, color):
        self.curtain.set_color(color)
        self.curtain.set_image(None)

    def _curtain_on_top(self):
        self.curtain.raise_()
        if self.controls.isVisible():
            self.controls.raise_()                         # the bar stays above the curtain
        self.raisedChild.emit()

    def _curtain_step(self):
        a = int(round(255 * max(0.0, min(1.0, self.cur_t.value))))
        self.cur_alpha = a
        a_apply = a
        if a >= 255 and self._fading and self._cur_done and self._cur_done[1] < 255:
            a_apply = 254                    # still layered: dropping the layered style at the switch showed black frames on Windows
        self._cur_done = layered_alpha_apply(self.curtain, a_apply, self._cur_done)
        if not self._fading:
            return
        if a >= 255 and self._fade_switch is not None:      # fully dark: switch now, hold, then fade back
            sw, self._fade_switch = self._fade_switch, None
            g = self._fade_gen
            self._run_switch(sw)
            self.curtain.setGeometry(0, 0, self.width(), self.height())
            self.curtain.update()
            self._curtain_on_top()
            self._schedule_lift(g)
        elif a <= 0 and self._fade_switch is None and self.cur_t._target <= 0.0:
            self._cur_done = layered_alpha_apply(self.curtain, 255, self._cur_done)
            self.curtain.hide()
            self._fading = False
            self._end_curtain()

    def _home_laid_out(self):
        """The Home cards exist and have their place (a page that was just shown may still be empty for a moment)."""
        try:
            hp = self.home.home_page
            if self.home.page != "home" or not hp.cards:
                return True
            return all(c.width() > 20 and c.height() > 20 and c.isVisible() for c in hp.cards[:4])
        except Exception:
            return True

    def _poll_mini(self, g=None):
        """Wait (dark) until the live video really sits in the Home slot and is shown, then let the curtain lift."""
        if (g is not None and g != self._fade_gen) or not self._fading or not self._wait_mini:
            return
        # settled = the live video is shown in the slot AND no resize is pending (a Stage resize hides it again for a moment)
        if self._was_mini and self.video.isVisible() and not self._resizing and not self._mini_fix.isActive() and self._home_laid_out():
            self._mini_ok += 1
        else:
            self._mini_ok = 0                                # a late resize hid it again: start counting again
        if self._mini_ok >= 3:                               # still there after 3 checks in a row (about 90 ms)
            self._wait_mini = False
            self._fade_back(g)
        else:
            QTimer.singleShot(30, lambda: self._poll_mini(g))

    def _fade_back(self, g=None):
        if g is not None and g != self._fade_gen:
            return                                           # a timer of an older curtain run: it must not touch this one
        self._wait_mini = False
        # only after the switch has run (a fade-in that is still going dark must never be turned around)
        if self._fading and self._fade_switch is None and self.cur_t._target > 0.0:      # once only (cap timer and slot signal)
            self.cur_t.to(0.0, self.FADE_IN_MS, EASE_INOUT)

    def _fade_guard(self, g):
        """Safety net: a curtain run that is still on after 2.5 s is finished by force (the switch is done, the curtain goes)."""
        if g != self._fade_gen or not self._fading:
            return
        sw, self._fade_switch = self._fade_switch, None
        if sw is not None:
            try:
                sw()
            except Exception:
                sys.excepthook(*sys.exc_info())
        self._wait_mini = False
        self.cur_t.set(0.0)
        self.curtain.hide()
        self._fading = False
        self._end_curtain()
        self.relayout()

    def _end_curtain(self):
        """The curtain is gone: the bar goes back on top."""
        if self.controls.isVisible():
            self.controls.raise_()
            self.raisedChild.emit()

    def _apply_ctrl_alpha(self):
        """Windows 8+ lets a child window be layered, so the bar can be see-through over the picture.
        An empty opaque_controls.txt in the data folder switches this off."""
        layered = sys.platform == "win32" and not (self._opaque_file is not None and self._opaque_file.exists())
        on_home = self.home.isVisible()
        if self._painted_wanted(on_home):
            amount = max(0.0, min(1.0, self.ctrl.value))
            self.controls.set_translucent(True, QColor(28, 28, 28, VIDEO_BAR_ALPHA))      # #1C1C1C at 70 %: the alpha is IN the colour now
            self._alpha_done = layered_alpha_apply(self.controls, 255, self._alpha_done)  # no layered window any more
            self._set_painted(True, amount)
            return
        self._set_painted(False)
        self.controls.set_fill(FLAT_HOME_BAR if on_home else (VIDEO_BAR_FILL if layered else CONTROLS_BG))
        self.controls.set_top_line(HOME_BAR_LINE if on_home else None)
        if not layered:
            return
        amount = max(0.0, min(1.0, self.ctrl.value)) if (self._ov() and not self._no_controls) else 1.0
        alpha = controls_alpha(self.home.isVisible(), amount)
        self._alpha_done = layered_alpha_apply(self.controls, alpha, self._alpha_done)

    def _painted_wanted(self, on_home):
        """PHASE 12c: on the video page the picture window paints the bar (Windows, or SIMPLEPLAYER_PAINTED_BAR=1 for tests).
        Not while the page moves, loads, or on Home. opaque_controls.txt or native_bar.txt switch it off (old native bar)."""
        env = os.environ.get("SIMPLEPLAYER_PAINTED_BAR", "")
        if env == "0" or not (sys.platform == "win32" or env == "1"):
            return False
        for fl in (self._opaque_file, self._native_bar_file):
            if fl is not None and fl.exists():
                return False
        return (not on_home and self._ov() and not self._no_controls and not self._loading and self._move is None
                and self.video.isVisible())

    def _set_painted(self, on, amount=1.0):
        v = self.video
        if on:
            first = not self._painted
            v.set_bar(self.controls, amount)
            if first:
                self._painted = True
                v.repaint_now()                                  # the painted bar is on screen first, then the native bar is shrunk to one pixel
                self.controls.set_native_hidden(True)
                self._log_fn("video-page bar: painted by the picture window")
        elif self._painted:
            self._painted = False
            self.controls.set_native_hidden(False)
            v.set_bar(None)
            self.controls.set_translucent(False)
            if self.controls.isVisible():
                self.controls.raise_()
                self.raisedChild.emit()
            self._log_fn("video-page bar: native window again")

    def _ov(self):
        """Controls float over the picture: full screen / mini player, or the video page (auto). Only on the VIDEO page: on Home
        (also in full screen) the bar sits below the page and the picture goes into the preview slot (in full screen the overlay
        layout skipped the slot check and left the picture full screen above Home)."""
        return not self.mini_mode and not self.home.isVisible() and (self.overlay or self.auto)

    def begin_loading(self, target_ms=0, show_ms=None):
        """A video is starting on the video page: no control bar until the first picture is there. The turning ring
        shows only if that takes longer than LOAD_SHOW_MS (show_ms=0: at once, under the curtain of a card start)."""
        self._loading = True
        self._load_frames0 = None
        self._load_t0 = time.monotonic()
        self._load_target = int(target_ms or 0)
        self._load_seen = None
        self._load_ready = None
        self._load_show_ms = LOAD_SHOW_MS if show_ms is None else int(show_ms)
        QTimer.singleShot(self._load_show_ms, self._show_loader)

    def _show_loader(self):
        if not self._loading or self.loader.isVisible():
            return
        if time.monotonic() - self._load_t0 < self._load_show_ms / 1000.0 - 0.05:
            return                                   # a newer start: its own timer shows it
        self.loader.setGeometry(0, 0, self.width(), self.height())
        self.loader.start()
        self.loader.raise_()
        if self._fading:
            self._curtain_on_top()
        else:
            self.raisedChild.emit()                  # the title bar stays above the page

    def end_loading(self, fade=True, reveal=True):
        """The first picture is there (or Home was opened, or it took too long): the page and the bar come back.
        PHASE 12b, ONE reveal: the picture surface is shown and painted at once, then the loading page goes, then the bar fades in.
        reveal=False (Home opened): the caller decides whether the surface shows."""
        if not self._loading:
            return
        self._loading = False
        self._ctrl_shown = None
        if fade and self._ov() and not self.home.isVisible():
            self._noslide = True                      # a plain fade: a drifting translucent bar left a second copy behind
            QTimer.singleShot(BAR_FADE_IN + 80, self._end_noslide)
            self.ctrl.set(0.0)
            self.ctrl.to(1.0, BAR_FADE_IN, EASE_INOUT)
        self.relayout()
        if reveal and not self.home.isVisible():
            if not self.video.isVisible():
                self.video.show()
            self.video.repaint_now()                  # the picture is painted before the loading page goes: no black gap between them
            if self.controls.isVisible():
                self.controls.raise_()                # a freshly shown native window sits above the bar: the bar goes back on top
            self.raisedChild.emit()
        self.loader.stop()
        self.video.update()

    def _end_noslide(self):
        self._noslide = False

    def set_mini(self, on):
        """Mini player: no control bar at all, the picture fills the window (the overlay has the controls)."""
        if on != self.mini_mode:
            self.mini_mode = on
            self._ctrl_shown = None
            self.relayout()

    def set_overlay(self, on):
        self.overlay = on
        self.controls_visible = True
        self.ctrl.set(1.0)
        self.relayout()

    def show_controls(self, show):
        if show == self.controls_visible:
            return
        self.controls_visible = show
        if self._ov():
            self.ctrl.to(1.0 if show else 0.0, BAR_FADE_IN if show else BAR_FADE_OUT, EASE_INOUT)
        else:
            self.ctrl.set(1.0 if show else 0.0)

    def show_home(self, on, with_controls=True, mini=False):
        """mini=True: on Home the (same) video surface is shown inside the mini-player slot."""
        if on:
            self.end_loading(fade=False, reveal=False)
        self.home.setVisible(on)
        self.controls_visible = True       # bars are always shown again when the page changes
        self.ctrl.set(1.0)
        self.mini_live = bool(on and mini and with_controls)
        self.video.setVisible(((not on) and not self._loading) or self.mini_live)   # 12b: hidden while the loading page shows
        self._no_controls = on and not with_controls
        self.controls.show_thumb(self.mini_live and not self.controls.compact)
        self.relayout()
        if not on and self.controls.isVisible():
            # the video window was just shown (or grew out of the mini slot) and sits above the bar:
            # bring the bar and the title bar back on top, otherwise they only return after a mouse leave/enter
            self.controls.raise_()
            self.raisedChild.emit()
        if self._fading:
            self._curtain_on_top()                       # the video window must not end up above the curtain

    def _mini_rect(self):
        self.controls.layout().activate()
        th = self.controls.thumb
        tl = th.mapTo(self, QPoint(0, 0))
        return QRect(tl.x(), tl.y(), th.width(), th.height())

    def _round_mask(self, on):
        if on:
            size = (self.video.width(), self.video.height())
            if self._masked == size:               # same mask as before: do not touch the native window
                return
            path = QPainterPath()
            path.addRoundedRect(QRectF(0, 0, size[0], size[1]), 4, 4)
            self.video.setMask(QRegion(path.toFillPolygon().toPolygon()))
            self._masked = size
        elif self._masked:
            self.video.clearMask()
            self._masked = False

    def relayout(self):
        self._apply_ctrl_alpha()
        w, h = self.width(), self.height()
        ch = self.controls.height()
        if self._no_controls or self.mini_mode or self._loading:
            vh, visible, y = h, False, h
        elif self._ov():
            amount = max(0.0, min(1.0, self.ctrl.value))
            vh, visible = h, amount > 0.01
            y = h - ch + (0 if self._noslide else int(round(BAR_SLIDE * (1.0 - amount))))   # fades while drifting down a little
        else:
            vh, visible, y = max(0, h - ch), True, h - ch
        hoff = self.home_off if self.home.isVisibleTo(self) else 0
        self.home.setGeometry(0, hoff, w, max(1, vh - hoff))
        self.controls.setGeometry(0, y, w, ch)
        if self._fading:
            self.curtain.setGeometry(0, 0, w, h)
        if self._loading:
            self.loader.setGeometry(0, 0, w, h)
        if self._painted:
            self.video.set_bar(self.controls, max(0.0, min(1.0, self.ctrl.value)))     # new place / fade: paint again
        if visible != self._ctrl_shown:
            self._ctrl_shown = visible
            self.controls.setVisible(visible)
            if visible:
                self.controls.raise_()                       # only when it appears, not on every frame
                self.raisedChild.emit()
        if self._move is not None:
            g = QRect(0, 0, w, h)                        # the video keeps the full size (also after a resize), the mask moves
            if self.video.geometry() != g:
                self.video.setGeometry(g)
            self._move_sync()
            self._set_slot_live(False)
            return
        if self._loading:
            self._set_video_visible(False)               # 12b: the loading page is a plain widget, the surface stays hidden until the reveal
        # The video surface is a Qt widget: it is moved ONCE per layout, straight to its final place.
        # (Setting it full-size first and then to the mini slot showed a stray copy at the top-left.)
        target, mini = QRect(0, 0, w, vh), False
        if self.mini_live and visible and not self._ov() and not self._no_controls:
            if self._resizing:
                self._set_video_visible(False)                # no moving video while the window is being dragged
                return
            r = self._mini_rect()
            ok = (r.width() == ThumbPreview.W and r.height() == ThumbPreview.H
                  and r.bottom() <= self.height() and r.top() >= self.height() - ch - 1)
            if not ok:
                self._set_video_visible(False)               # layout not settled yet: never show it in a wrong place
                self._mini_fix.start()
                return
            target, mini = r, True
        if self.video.geometry() != target:
            if not mini:
                self._round_mask(False)
            self.video.setGeometry(target)
        if mini:
            self._round_mask(True)
            if not self.video.isVisible():
                self.video.show()
            if self._need_raise:
                self.video.stackUnder(self.controls)         # PHASE 12a: the slot picture is drawn by the bar's slot widget; the surface sits right UNDER the bar (still above Home: lower() hid the Home -> video rise)
        else:
            self._round_mask(False)
            if self._was_mini and self.controls.isVisible():
                self.controls.raise_()                       # video grew out of the mini slot: bar back on top
                self.raisedChild.emit()
        self._set_slot_live(mini and self.video.isVisible(), target.topLeft() if mini else None)
        if mini != self._was_mini:
            self.miniShown.emit(mini)
        self._was_mini = mini
        self._need_raise = False

    def _set_video_visible(self, on):
        if on != self.video.isVisible():
            self.video.setVisible(on)
            self._need_raise = on
        if not on:
            self._set_slot_live(False)

    def _set_slot_live(self, on, pos=None):
        """In the Home slot the picture is drawn by the bar's preview widget (the bar is a native window and always sits above
        a Qt widget), from the SAME surface and the same libVLC player."""
        on = bool(on)
        th = self.controls.thumb
        pos = pos if on else None
        if pos != th.live_pos:
            th.live_pos = pos
            th.update()
        if self.video.slot_mode != on or (th.live is not None) != on:
            self.video.slot_mode = on
            th.live = self.video if on else None
            th.update()
            self.video.update()

    def resizeEvent(self, e):
        if self.mini_live:
            self._resizing = True                      # the small video stays hidden while the size keeps changing
            self._mini_fix.start()
        self.relayout()

    def _end_resize(self):
        self._resizing = False
        self._need_raise = True
        self.relayout()


# --------------------------------------------------------------------------
# Windows 11 style menus and dialogs
# --------------------------------------------------------------------------
_MENU_ICONS = {}


def menu_icon(kind):
    if kind not in _MENU_ICONS:
        pm = QPixmap(40, 40)
        pm.setDevicePixelRatio(2.0)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        paint_icon(p, kind, 10, 10, 20, QColor("#f2f2f2"), 0.0, 2, False, QColor("#2b2b2b"))
        p.end()
        _MENU_ICONS[kind] = QIcon(pm)
    return _MENU_ICONS[kind]


# --------------------------------------------------------------------------
# Full language names for track lists (display only, never used for matching)
# --------------------------------------------------------------------------
_LANGS = {
    "af": "Afrikaans", "sq": "Albanian", "am": "Amharic", "ar": "Arabic", "hy": "Armenian", "az": "Azerbaijani",
    "eu": "Basque", "be": "Belarusian", "bn": "Bengali", "bs": "Bosnian", "bg": "Bulgarian", "my": "Burmese",
    "ca": "Catalan", "zh": "Chinese", "hr": "Croatian", "cs": "Czech", "da": "Danish", "nl": "Dutch",
    "en": "English", "et": "Estonian", "fil": "Filipino", "fi": "Finnish", "fr": "French", "gl": "Galician",
    "ka": "Georgian", "de": "German", "el": "Greek", "gu": "Gujarati", "he": "Hebrew", "hi": "Hindi",
    "hu": "Hungarian", "is": "Icelandic", "id": "Indonesian", "ga": "Irish", "it": "Italian", "ja": "Japanese",
    "kn": "Kannada", "kk": "Kazakh", "km": "Khmer", "ko": "Korean", "lo": "Lao", "lv": "Latvian",
    "lt": "Lithuanian", "mk": "Macedonian", "ms": "Malay", "ml": "Malayalam", "mt": "Maltese", "mr": "Marathi",
    "mn": "Mongolian", "ne": "Nepali", "no": "Norwegian", "nb": "Norwegian Bokmal", "nn": "Norwegian Nynorsk",
    "fa": "Persian", "pl": "Polish", "pt": "Portuguese", "pa": "Punjabi", "ro": "Romanian", "ru": "Russian",
    "sr": "Serbian", "si": "Sinhala", "sk": "Slovak", "sl": "Slovenian", "es": "Spanish", "sw": "Swahili",
    "sv": "Swedish", "ta": "Tamil", "te": "Telugu", "th": "Thai", "tr": "Turkish", "uk": "Ukrainian",
    "ur": "Urdu", "uz": "Uzbek", "vi": "Vietnamese", "cy": "Welsh", "yue": "Cantonese",
    "eng": "English", "fre": "French", "fra": "French", "ger": "German", "deu": "German", "spa": "Spanish",
    "ita": "Italian", "por": "Portuguese", "rus": "Russian", "jpn": "Japanese", "kor": "Korean",
    "chi": "Chinese", "zho": "Chinese", "ara": "Arabic", "hin": "Hindi", "ben": "Bengali", "tur": "Turkish",
    "dut": "Dutch", "nld": "Dutch", "swe": "Swedish", "dan": "Danish", "nor": "Norwegian", "fin": "Finnish",
    "pol": "Polish", "cze": "Czech", "ces": "Czech", "gre": "Greek", "ell": "Greek", "heb": "Hebrew",
    "hun": "Hungarian", "rum": "Romanian", "ron": "Romanian", "ukr": "Ukrainian", "vie": "Vietnamese",
    "tha": "Thai", "ind": "Indonesian", "may": "Malay", "msa": "Malay", "per": "Persian", "fas": "Persian",
    "tam": "Tamil", "tel": "Telugu", "urd": "Urdu", "bul": "Bulgarian", "hrv": "Croatian", "srp": "Serbian",
    "slk": "Slovak", "slo": "Slovak", "slv": "Slovenian", "lit": "Lithuanian", "lav": "Latvian", "est": "Estonian",
}
_LANG_CODE = re.compile(r"^([A-Za-z]{2,3})(?:[-_]([A-Za-z]{2,4}))?$")
_LANG_BRACKET = re.compile(r"\[([A-Za-z]{2,3})\]")


def full_lang(name):
    """'en' -> 'English', 'pt-BR' -> 'Portuguese (BR)', 'Track 2 - [fr]' -> 'Track 2 - [French]'.
    Anything that is not clearly a language code is returned unchanged. Display only."""
    t = (name or "").strip()
    m = _LANG_CODE.match(t)
    if m and m.group(1).lower() in _LANGS:
        out = _LANGS[m.group(1).lower()]
        return f"{out} ({m.group(2).upper()})" if m.group(2) else out
    return _LANG_BRACKET.sub(lambda b: f"[{_LANGS.get(b.group(1).lower(), b.group(1))}]", t)



_TRACK_NUM = re.compile(r"^(?:track|audio|subtitle)\s*\d+$", re.I)
_TRACK_LANG = re.compile(r"^(?P<label>.*?)\s+-\s+\[(?P<lang>.+)\]\s*$")


def track_label(name):
    """What a track shows in the subtitle and audio lists (display only, never used for matching):
    'Track 2 - [English]' -> 'English', 'Forced - [English]' -> 'English (Forced)',
    'Simplified - [Chinese (Simplified)]' -> 'Chinese (Simplified)', 'Track 11 - [Greek, Modern (1453-)]' -> 'Greek',
    'en' -> 'English'. A name with no language in it (plain 'Track 3') stays as it is."""
    t = full_lang(name)
    m = _TRACK_LANG.match(t)
    if not m:
        return t
    lang = m.group("lang").strip()
    lang = re.split(r"\s*[;,]\s*(?![^(]*\))", lang, 1)[0].strip() or lang   # 'Greek, Modern' -> 'Greek' (not inside brackets)
    label = m.group("label").strip()
    if not label or _TRACK_NUM.match(label) or label.lower() in lang.lower():
        return lang
    return f"{lang} ({label})"


class AppStyle(QProxyStyle):
    """Fusion with slightly larger menu icons."""

    def pixelMetric(self, metric, option=None, widget=None):
        if metric == QStyle.PM_SmallIconSize:
            return 20
        return super().pixelMetric(metric, option, widget)


class DarkMenu(QMenu):
    """Rounded, dark, Windows 11 look: icon, label, shortcut on the right, arrow for submenus."""

    def __init__(self, title="", parent=None):
        super().__init__(title, parent)
        self.setWindowFlags(self.windowFlags() | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)

    def sub(self, title, kind=None):
        m = DarkMenu(title, self)
        if kind:
            m.menuAction().setIcon(menu_icon(kind))
        self.addMenu(m)
        return m

    def item(self, text, slot=None, kind=None, shortcut="", checkable=False, checked=False,
             enabled=True):
        label = f"{text}\t{shortcut}" if shortcut else text
        a = self.addAction(menu_icon(kind), label) if kind else self.addAction(label)
        a.setCheckable(checkable)
        if checkable:
            a.setChecked(checked)
        a.setEnabled(enabled)
        if slot:
            a.triggered.connect(lambda _=False, s=slot: s())
        return a


class TrackMenu(DarkMenu):
    """Submenu that holds a TrackFlyout. It stays inside the player window: its bottom edge is at the bottom of
    the row that opened it, it grows upwards, never higher than the top edge of the window."""
    FRAME = 6                                             # the menu's own border and padding (3 + 3)

    def __init__(self, title, parent, host):
        super().__init__(title, parent)
        self._host = host
        self._fly = None
        self.aboutToShow.connect(self._fit)               # size it BEFORE Qt lays the menu out

    def _row_bottom_and_top(self):
        par = self.parentWidget()
        row = par.actionGeometry(self.menuAction())
        bottom = par.mapToGlobal(row.bottomLeft()).y()
        top = self._host.mapToGlobal(QPoint(0, 0)).y()
        try:
            top = max(top, self.screen().availableGeometry().top())
        except Exception:
            pass
        return bottom, top

    def _fit(self):
        try:
            if self._fly is not None and self.parentWidget() is not None:
                bottom, top = self._row_bottom_and_top()
                self._fly.set_max(max(2 * _TrackDelegate.ROW, bottom - top - self.FRAME))
        except Exception:
            pass                                          # never break the menu over sizing

    def showEvent(self, ev):
        super().showEvent(ev)
        try:
            if self._fly is not None and self.parentWidget() is not None:
                bottom, _top = self._row_bottom_and_top()
                self.move(self.x(), bottom - self.height())
        except Exception:
            pass


class _TrackDelegate(QStyledItemDelegate):
    """Row look of the Media Player track flyout: plain text, soft rounded hover, orange bar on the chosen one."""
    ROW = 43

    def sizeHint(self, option, index):
        return QSize(option.rect.width(), self.ROW)

    def paint(self, p, option, index):
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(option.rect).adjusted(5, 4, -5, -4)
        chosen = bool(index.data(Qt.UserRole))
        if option.state & QStyle.State_MouseOver or chosen:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#3a3a3a") if option.state & QStyle.State_MouseOver else QColor("#333333"))
            p.drawRoundedRect(r, 6, 6)
        if chosen:
            p.setBrush(QColor("#ff8232"))
            p.drawRoundedRect(QRectF(r.left() + 1, r.center().y() - 8, 3, 16), 1.5, 1.5)
        if index.data(Qt.UserRole + 1):                      # thin line above an action row
            p.setPen(QPen(QColor("#3d3d3d"), 1))
            p.drawLine(int(r.left() + 5), option.rect.top(), int(r.right() - 5), option.rect.top())
        p.setPen(QColor("#f2f2f2"))
        f = QFont(option.font)
        f.setPixelSize(14)
        p.setFont(f)
        txt = QFontMetrics(f).elidedText(index.data(Qt.DisplayRole) or "", Qt.ElideRight, int(r.width() - 22))
        p.drawText(QRectF(r.left() + 14, r.top(), r.width() - 18, r.height()), Qt.AlignVCenter | Qt.AlignLeft, txt)
        p.restore()


class TrackList(QListWidget):
    """Scrolling list for subtitle and audio tracks (thin scrollbar, no frame). Used inside a menu."""
    WIDTH = 168

    def __init__(self, max_height, parent=None):
        super().__init__(parent)
        self._max = max_height
        self.setItemDelegate(_TrackDelegate(self))
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setSelectionMode(QAbstractItemView.NoSelection)
        self.setFocusPolicy(Qt.NoFocus)
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.setStyleSheet(
            "QListWidget{background:transparent;border:0;outline:0}"
            "QScrollBar:vertical{width:8px;background:transparent;margin:6px 1px 6px 3px}"
            "QScrollBar::handle:vertical{background:#8c8c8c;border-radius:2px;min-height:30px}"
            "QScrollBar::handle:vertical:hover{background:#b0b0b0}"
            "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0}"
            "QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical{background:transparent}")
        self.setFixedWidth(self.WIDTH)

    def add_row(self, text, chosen=False, action=False):
        it = QListWidgetItem(text)
        it.setData(Qt.UserRole, bool(chosen))
        it.setData(Qt.UserRole + 1, bool(action))
        self.addItem(it)
        self.fit()
        return it

    def fit(self):
        self.setFixedHeight(max(_TrackDelegate.ROW, min(self.count() * _TrackDelegate.ROW + 2, self._max)))


class TrackFlyout(QWidget):
    """Scrolling track list on top, a few rows pinned at the bottom (Load subtitle file, Disable)."""

    def __init__(self, rows, fixed_rows, max_height, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        foot_h = len(fixed_rows) * _TrackDelegate.ROW + 2 if fixed_rows else 0
        self.scroll = TrackList(max(_TrackDelegate.ROW, max_height - foot_h))
        for text, chosen, payload, _ in rows:
            self.scroll.add_row(text, chosen, False).setData(Qt.UserRole + 2, payload)
        self.footer = TrackList(foot_h)
        self.footer.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        for i, (text, chosen, payload, _) in enumerate(fixed_rows):
            self.footer.add_row(text, chosen, i == 0 and self.scroll.count() > 0).setData(Qt.UserRole + 2, payload)
        if self.scroll.count():
            lay.addWidget(self.scroll)
        else:
            self.scroll.hide()
        if self.footer.count():
            lay.addWidget(self.footer)
        else:
            self.footer.hide()
        self.setFixedWidth(TrackList.WIDTH)

    def set_max(self, h):
        """New tallest total height (scroll part plus the fixed rows)."""
        self.scroll._max = max(_TrackDelegate.ROW, h - self.footer.height())
        self.scroll.fit()
        self.updateGeometry()


def add_track_list(menu, rows, fixed_rows, max_height, on_pick):
    """Put a track flyout into a DarkMenu. rows scroll, fixed_rows stay at the bottom.
    A row is (text, chosen, payload, unused)."""
    fly = TrackFlyout(rows, fixed_rows, max_height)
    wa = QWidgetAction(menu)
    wa.setDefaultWidget(fly)
    menu.addAction(wa)
    if isinstance(menu, TrackMenu):
        menu._fly = fly

    def clicked(it):
        payload = it.data(Qt.UserRole + 2)
        wa.trigger()                                          # closes the whole menu chain
        QTimer.singleShot(0, lambda: on_pick(payload))
    fly.scroll.itemClicked.connect(clicked)
    fly.footer.itemClicked.connect(clicked)
    return fly


def human_size(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.2f} {unit}"
        n /= 1024.0


class InfoDialog(QDialog):
    """Properties window: sections of label/value rows."""

    def __init__(self, parent, title, sections):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(520)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(26, 22, 26, 22)
        lay.setSpacing(4)
        for heading, rows in sections:
            if not rows:
                continue
            h = QLabel(heading)
            h.setStyleSheet(SEMI_QSS + f"color:{ACCENT};font-size:13px;font-weight:600;margin-top:10px;")
            lay.addWidget(h)
            grid = QGridLayout()
            grid.setHorizontalSpacing(18)
            grid.setVerticalSpacing(6)
            for i, (k, v) in enumerate(rows):
                a = QLabel(k)
                a.setStyleSheet(f"color:{MUTED};font-size:13px;")
                a.setAlignment(Qt.AlignTop | Qt.AlignLeft)
                b = QLabel(str(v))
                b.setWordWrap(True)
                b.setTextInteractionFlags(Qt.TextSelectableByMouse)
                b.setStyleSheet("color:#fff;font-size:13px;")
                grid.addWidget(a, i, 0)
                grid.addWidget(b, i, 1)
            grid.setColumnStretch(1, 1)
            lay.addLayout(grid)
        lay.addSpacing(10)
        close = QPushButton("Close")
        close.setCursor(Qt.PointingHandCursor)
        close.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(close)
        lay.addLayout(row)


ROLE_PATH = Qt.UserRole + 1
ROLE_DUR = Qt.UserRole + 2
ROLE_MISSING = Qt.UserRole + 3
ROLE_CUR = Qt.UserRole + 4


ROLE_CHK = Qt.UserRole + 5
QCOL = QColor(255, 130, 50)            # orange of the Play queue page (measured in the Media Player)


def qfmt(ms):
    """Length like the Media Player queue: 00:17, 56:59, 1:02:03."""
    s = max(0, int((ms or 0) // 1000))
    h, m, sec = s // 3600, s // 60 % 60, s % 60
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"


class QueueDelegate(QStyledItemDelegate):
    """One row of the Play queue page. Sizes are the Media Player's, measured at 125 % and given in
    pixels at 100 %: row 46 (pitch 52), icons at 24 / 53 px, title at 72 px, length 18 px from the right."""
    ROW, PITCH = 46, 52

    def sizeHint(self, option, index):
        return QSize(0, self.PITCH)

    @classmethod
    def box_rect(cls, r):
        return QRectF(r.left() + 14, r.center().y() - 10, 20, 20)

    def paint(self, p, option, index):
        lst = self.parent()
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(option.rect.left(), option.rect.top(), option.rect.width(), self.ROW)
        hovered = bool(option.state & QStyle.State_MouseOver)
        cur, missing = index.data(ROLE_CUR), index.data(ROLE_MISSING)
        chk = bool(index.data(ROLE_CHK))
        p.setPen(Qt.NoPen)
        stripe = getattr(lst, "zebra", False) and index.row() % 2 == 1     # playlist page: every second row
        p.setBrush(QColor(255, 255, 255, 20 if chk else (18 if hovered else (0 if stripe else 15))))
        p.drawRoundedRect(r, 6, 6)
        if cur or chk:
            col = QColor(QCOL)
        elif missing:
            col = QColor("#7a7a7a")
        else:
            col = QColor(TEXT)
        cy = r.center().y()
        if lst.sel_mode or hovered:                         # check box (a hover or a selection shows it)
            b = self.box_rect(r)
            if chk:
                p.setPen(Qt.NoPen)
                p.setBrush(QCOL)
                p.drawRoundedRect(b, 4, 4)
                p.setPen(QPen(QColor("#000000"), 1.8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
                p.setBrush(Qt.NoBrush)
                p.drawPolyline(QPolygonF([QPointF(b.left() + 5, b.center().y() + 0.5),
                                          QPointF(b.left() + 8.5, b.center().y() + 4),
                                          QPointF(b.right() - 4.5, b.top() + 5.5)]))
            else:
                p.setPen(QPen(QColor(255, 255, 255, 170), 1.4))
                p.setBrush(QColor(20, 20, 20, 110))
                p.drawRoundedRect(b.adjusted(0.7, 0.7, -0.7, -0.7), 4, 4)
        elif cur:                                           # the video that is playing
            paint_icon(p, "video", r.left() + 28, cy, 22, col, 0.0, 2, False, None, 0.8)
        if hovered and not lst.sel_mode:                    # hover turns the film icon into a play button
            paint_icon(p, "play_o", r.left() + 51, cy, 24, col, 0.0, 2, False, None, 0.8)
        else:
            paint_icon(p, "film_sq", r.left() + 53, cy, 23, col, 0.0, 2, False, None, 0.8)
        dur = index.data(ROLE_DUR)
        dtxt = qfmt(dur) if dur else ("missing" if missing else "")
        p.setFont(ui_font(12))
        dw = p.fontMetrics().horizontalAdvance(dtxt) if dtxt else 0
        right = r.right() - 18
        p.setPen(QColor("#d46a6a") if (missing and not dur) else col)
        p.drawText(QRectF(right - dw, r.top(), dw + 1, r.height()), Qt.AlignVCenter | Qt.AlignRight, dtxt)
        p.setFont(ui_font(14))
        p.setPen(col)
        x0 = r.left() + 72
        room = max(20, int(right - dw - 16 - x0))
        title = p.fontMetrics().elidedText(index.data(Qt.DisplayRole) or "", Qt.ElideRight, room)
        p.drawText(QRectF(x0, r.top(), room, r.height()), Qt.AlignVCenter | Qt.AlignLeft, title)
        p.restore()


class QueueList(QListWidget):
    filesDropped = Signal(list)      # files dragged in from Explorer
    orderChanged = Signal(list)      # paths in their new order after an internal drag
    playRequested = Signal(int)
    checkChanged = Signal()
    menuRequested = Signal(int, QPoint)
    deleteRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setAcceptDrops(True)
        self.setMouseTracking(True)
        self.setItemDelegate(QueueDelegate(self))
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setFocusPolicy(Qt.ClickFocus)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.viewport().setAutoFillBackground(False)
        self.setStyleSheet(
            "QListWidget{background:transparent;border:none;outline:none;}"
            "QListWidget::item{background:transparent;border:none;}"
            "QScrollBar:vertical{background:transparent;width:10px;margin:2px;}"
            "QScrollBar::handle:vertical{background:#4a4448;border-radius:4px;min-height:30px;}"
            "QScrollBar::handle:vertical:hover{background:#625b60;}"
            "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;}"
            "QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical{background:transparent;}")
        self.sel_mode = False
        self._keys = set()           # ticked rows (path keys), they survive a refresh
        self._press = None

    # --- paths
    def paths(self):
        return [self.item(i).data(ROLE_PATH) for i in range(self.count())]

    def selected_paths(self):
        return [it.data(ROLE_PATH) for it in self.selectedItems()]

    def checked_paths(self):
        return [p for p in self.paths() if Store._key(p) in self._keys]

    def is_checked(self, row):
        return 0 <= row < self.count() and bool(self.item(row).data(ROLE_CHK))

    # --- ticks
    def _sync_checks(self):
        have = {Store._key(p) for p in self.paths()}
        self._keys &= have
        for i in range(self.count()):
            it = self.item(i)
            it.setData(ROLE_CHK, Store._key(it.data(ROLE_PATH)) in self._keys)
        self.sel_mode = bool(self._keys)
        self.viewport().update()
        self.checkChanged.emit()

    def toggle_row(self, row):
        if not (0 <= row < self.count()):
            return
        k = Store._key(self.item(row).data(ROLE_PATH))
        self._keys ^= {k}
        self._sync_checks()

    def check_all(self, on):
        self._keys = {Store._key(p) for p in self.paths()} if on else set()
        self._sync_checks()

    def refresh(self, queue, current, dur_of, exists):
        """Rebuild the rows. dur_of(path) -> ms or None; exists(path) -> bool."""
        bar = self.verticalScrollBar()
        keep = bar.value()
        self.blockSignals(True)
        self.clear()
        for i, path in enumerate(queue):
            it = QListWidgetItem(display_name(path))
            it.setData(ROLE_PATH, path)
            it.setData(ROLE_DUR, dur_of(path))
            it.setData(ROLE_MISSING, not exists(path))
            it.setData(ROLE_CUR, i == current)
            it.setSizeHint(QSize(0, QueueDelegate.PITCH))
            self.addItem(it)
        self.blockSignals(False)
        bar.setValue(keep)
        self._sync_checks()

    # --- mouse
    def mousePressEvent(self, e):
        self._press = None
        if e.button() == Qt.LeftButton:
            idx = self.indexAt(e.position().toPoint())
            if idx.isValid():
                r = QRectF(self.visualRect(idx))
                box = QueueDelegate.box_rect(r).adjusted(-6, -8, 6, 8)
                if box.contains(e.position()) and (self.sel_mode or self.viewport().underMouse()):
                    self.toggle_row(idx.row())
                    return
                self._press = (idx.row(), e.position().toPoint())
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        super().mouseReleaseEvent(e)
        pr, self._press = self._press, None
        if e.button() != Qt.LeftButton or pr is None:
            return
        idx = self.indexAt(e.position().toPoint())
        if idx.isValid() and idx.row() == pr[0] and (e.position().toPoint() - pr[1]).manhattanLength() < 6:
            if self.sel_mode:
                self.toggle_row(idx.row())
            else:
                self.playRequested.emit(idx.row())

    def contextMenuEvent(self, e):
        idx = self.indexAt(e.pos())
        if idx.isValid():
            self.menuRequested.emit(idx.row(), e.globalPos())

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Delete:
            self.deleteRequested.emit()
        elif e.key() in (Qt.Key_Return, Qt.Key_Enter) and self.currentRow() >= 0:
            self.playRequested.emit(self.currentRow())
        else:
            super().keyPressEvent(e)

    # --- drops
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            super().dragEnterEvent(e)

    def dragMoveEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            super().dragMoveEvent(e)

    def dropEvent(self, e):
        if e.mimeData().hasUrls():
            files = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
            e.acceptProposedAction()
            if files:
                self.filesDropped.emit(files)
            return
        super().dropEvent(e)
        self.orderChanged.emit(self.paths())


class QueueBar(SelectionBar):
    """Selection command bar of the Play queue page: 48 px tall, measured (Edit with Clipchamp omitted)."""
    H = 48
    COMPACT = True


class QueuePage(QWidget):
    """The Play queue as a page (REF: Media Player Play queue). The old dialog is gone."""
    playRequested = Signal(int)
    addRequested = Signal()
    addFolderRequested = Signal()
    clearRequested = Signal()
    addToRequested = Signal(QPoint)
    removeRequested = Signal(list)
    reordered = Signal(list)
    filesDropped = Signal(list)
    menuRequested = Signal(int, QPoint)
    selPlay = Signal()
    selPlayNext = Signal()
    selAdd = Signal(QPoint)
    selMore = Signal(QPoint)

    SIDE = 56
    HEAD_H = 46                 # heading row; the command row then starts 74 px below the top (measured)
    SLOT_H = 48

    def __init__(self, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(self.SIDE, 14, self.SIDE, 0)
        v.setSpacing(0)
        head = QWidget()
        head.setFixedHeight(self.HEAD_H)
        hl = QHBoxLayout(head)
        hl.setContentsMargins(0, 0, 0, 0)
        self.heading = QLabel("Play queue")
        self.heading.setStyleSheet(DISPLAY_QSS + "color:#fff;font-size:38px;font-weight:600;")
        self.split = SplitButton(label="Add file(s)", items=(
            ("Add file(s)", "Browse for files to add to the play queue"),
            ("Add folder", "Choose a folder, and add all the media in that folder")))
        self.split.openRequested.connect(self.addRequested)
        self.split.openFolderRequested.connect(self.addFolderRequested)
        hl.addWidget(self.heading)
        hl.addStretch(1)
        hl.addWidget(self.split, 0, Qt.AlignVCenter)
        v.addWidget(head)
        v.addSpacing(14)

        self.slot = QWidget()
        self.slot.setFixedHeight(self.SLOT_H)
        v.addWidget(self.slot)
        self.btn_clear = SelBtn("Clear", "trash", False, self.slot, True, 35)
        self.btn_add = SelBtn("Add to", "add", False, self.slot, True, 35)
        self.btn_clear.clicked.connect(self.clearRequested)
        self.btn_add.clicked.connect(lambda: self.addToRequested.emit(
            self.btn_add.mapToGlobal(QPoint(0, self.btn_add.height() + 2))))
        self.selbar = QueueBar(self.slot)
        self.selbar.hide()
        v.addSpacing(19)

        self.list = QueueList()
        v.addWidget(self.list, 1)
        self.list.playRequested.connect(self.playRequested)
        self.list.orderChanged.connect(self.reordered)
        self.list.filesDropped.connect(self.filesDropped)
        self.list.menuRequested.connect(self.menuRequested)
        self.list.checkChanged.connect(self._checks)
        self.list.deleteRequested.connect(self._delete)
        self.selbar.clearRequested.connect(self.clear_selection)
        self.selbar.allToggled.connect(self._toggle_all)
        self.selbar.playRequested.connect(self.selPlay)
        self.selbar.playNextRequested.connect(self.selPlayNext)
        self.selbar.addRequested.connect(self.selAdd)
        self.selbar.moreRequested.connect(self.selMore)
        self._place()
        self._checks()

    def _place(self):
        w = max(200, self.slot.width())
        self.btn_clear.move(0, (self.SLOT_H - self.btn_clear.height()) // 2)
        self.btn_add.move(self.btn_clear.width() + 16, (self.SLOT_H - self.btn_add.height()) // 2)
        self.selbar.setGeometry(0, 0, w, self.SLOT_H)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._place()

    def showEvent(self, e):
        super().showEvent(e)
        self._place()

    def paintEvent(self, e):
        if not self.selbar.isVisible():
            return
        p = QPainter(self)                       # soft shadow under the selection bar (visible in the Media Player)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 6))
        r = QRectF(self.slot.geometry())
        for i in range(10):
            p.drawRoundedRect(r.adjusted(-i * 0.5, 1 + i * 0.25, i * 0.5, 1 + i * 0.9), 8, 8)

    # --- state
    def refresh(self, queue, current, dur_of, exists):
        self.list.refresh(queue, current, dur_of, exists)
        have = bool(queue)
        self.btn_clear.setEnabled(have)
        self.btn_add.setEnabled(have)

    def _checks(self):
        n = len(self.list.checked_paths())
        self.selbar.set_count(n, n > 0 and n == self.list.count())
        on = self.list.sel_mode
        self.selbar.setVisible(on)
        self.btn_clear.setVisible(not on)
        self.btn_add.setVisible(not on)

    def checked_paths(self):
        return self.list.checked_paths()

    def is_checked(self, row):
        return self.list.is_checked(row)

    def toggle_row(self, row):
        self.list.toggle_row(row)

    def clear_selection(self):
        if self.list.sel_mode:
            self.list.check_all(False)

    def _toggle_all(self):
        self.list.check_all(not (self.list.count() and len(self.list.checked_paths()) == self.list.count()))

    def _delete(self):
        paths = self.list.checked_paths() or self.list.selected_paths()
        if paths:
            self.removeRequested.emit(paths)


# ======================================================================= Playlists page (phase D8)
def paint_cover(p, rect, pms, n, radius=6.0):
    """Playlist cover: 2 x 2 mosaic of the first four videos' cached thumbnails (a missing thumbnail is a
    grey tile); an empty playlist shows the playlist icon on a dark tile. Measured on the Media Player."""
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    clip = QPainterPath()
    clip.addRoundedRect(rect, radius, radius)
    p.setClipPath(clip)
    p.fillRect(rect, QColor("#1b1b1b"))
    if n <= 0:
        k = rect.width() * 0.29 / 131.0                    # the same glyph as the empty page, small and grey
        draw_playlist_glyph(p, rect.center().x() - 131 * k / 2, rect.center().y() - 150 * k / 2, k, QColor(205, 205, 205))
    else:
        cw, ch = rect.width() / 2.0, rect.height() / 2.0
        for i in range(min(4, n)):
            cell = QRectF(rect.left() + (i % 2) * cw, rect.top() + (i // 2) * ch, cw, ch)
            p.fillRect(cell, QColor(0, 0, 0))
            pm = pms[i] if i < len(pms) else None
            inner = cell.adjusted(0, ch * 0.055, 0, -ch * 0.055)
            if pm:
                p.drawPixmap(inner, pm, ThumbCard._cover(pm.size(), inner))
            else:
                g = QLinearGradient(0, inner.top(), 0, inner.bottom())
                g.setColorAt(0, QColor("#34343a"))
                g.setColorAt(1, QColor("#1d1d21"))
                p.fillRect(inner, g)
    p.setClipping(False)
    p.setPen(QPen(QColor(255, 255, 255, 40), 1))
    p.setBrush(Qt.NoBrush)
    p.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)
    p.restore()


def draw_playlist_glyph(p, x, y, k, brush):
    """The Media Player's playlist picture (a bar above a rounded square with a ring and a dot), drawn in a
    131 x 150 box scaled by k. `brush` is a colour or a gradient (given in the box's own coordinates)."""
    p.save()
    p.translate(x, y)
    p.scale(k, k)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QPen(QBrush(brush), 8.8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.setBrush(Qt.NoBrush)
    p.drawLine(QPointF(19, 4.4), QPointF(111, 4.4))
    p.drawRoundedRect(QRectF(4.4, 22, 131 - 8.8, 122), 22, 22)
    c = QPointF(65.6, 83)
    p.drawEllipse(c, 41.6, 41.6)
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(brush))
    p.drawEllipse(c, 9.6, 9.6)
    p.restore()


def items_text(n):
    return "1 item" if n == 1 else f"{n} items"


class PlaylistCard(ThumbCard):
    """One playlist tile: square cover (160 px), bold name, item count."""
    TW = TH = 160
    CW = TW + 2 * ThumbCard.PAD
    playClicked = Signal(str)

    def __init__(self, pl, thumbs, parent=None):
        super().__init__(pl, None, parent)
        self.pms = [self._load(f) for f in thumbs]

    def _thumb_rect(self):
        return QRectF(self.PAD, self.PAD, self.TW, self.TH)

    def mouseReleaseEvent(self, ev):
        self.press.to(0, 420, EASE_SPRING)
        if ev.button() != Qt.LeftButton or not self.rect().contains(ev.position().toPoint()):
            return
        hit = self._hit(ev.position())
        if hit == "check":
            self.checkToggled.emit(self.e)
        elif hit == "more":
            r = self._more_rect()
            self._open_menu(self.mapToGlobal(QPoint(int(r.right()), int(r.bottom()))))
        elif hit == "play":
            self.playClicked.emit(self.e["id"])
        elif self.sel_mode:
            self.selectToggled.emit(self.e)
        else:
            self.clicked.emit(self.e["id"])

    def _paint_full(self, p):
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        h = max(0.0, min(1.0, self.hover.value))
        a = max(0.0, min(1.0, self.appear.value))
        p.setOpacity(a)
        p.translate(0, (1 - a) * 14)
        sc = 1.0 - 0.025 * self.press.value
        c = QRectF(self.rect()).center()
        p.translate(c)
        p.scale(sc, sc)
        p.translate(-c)
        if h > 0.01:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, int(26 * h)))
            p.drawRoundedRect(QRectF(self.rect()), 8, 8)
        t = self._thumb_rect()
        paint_cover(p, t, self.pms, len(self.e["items"]), 6)
        self._paint_overlay(p, a, h)
        p.setPen(QColor(TEXT))
        p.setFont(ui_font(14, QFont.DemiBold))
        fm = p.fontMetrics()
        w = self.TW - 2
        p.drawText(QRectF(self.PAD + 1, t.bottom() + 7, w, 18), Qt.AlignVCenter | Qt.AlignLeft,
                   fm.elidedText(self.e["name"], Qt.ElideRight, int(w)))
        p.setPen(QColor(MUTED))
        p.setFont(ui_font(14))
        p.drawText(QRectF(self.PAD + 1, t.bottom() + 23, w, 18), Qt.AlignVCenter | Qt.AlignLeft,
                   items_text(len(self.e["items"])))


class SortButton(QAbstractButton):
    """'Sort by: A - Z v' (the value in the accent colour)."""
    LABELS = {"az": "A - Z", "date": "Date modified"}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.key = "az"
        self.setCursor(Qt.PointingHandCursor)
        self._fit()

    def set_key(self, key):
        self.key = key if key in self.LABELS else "az"
        self._fit()
        self.update()

    def _fit(self):
        fm = QFontMetrics(ui_font(14))
        self.setFixedSize(10 + fm.horizontalAdvance("Sort by: ") + fm.horizontalAdvance(self.LABELS[self.key]) + 26 + 10, 32)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if self.underMouse():
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 16))
            p.drawRoundedRect(QRectF(self.rect()), 4, 4)
        p.setFont(ui_font(14))
        fm = p.fontMetrics()
        x = 10.0
        p.setPen(QColor(TEXT))
        t1 = "Sort by: "
        p.drawText(QRectF(x, 0, fm.horizontalAdvance(t1) + 2, self.height()), Qt.AlignVCenter | Qt.AlignLeft, t1)
        x += fm.horizontalAdvance(t1)
        p.setPen(QColor(ACCENT))
        t2 = self.LABELS[self.key]
        p.drawText(QRectF(x, 0, fm.horizontalAdvance(t2) + 2, self.height()), Qt.AlignVCenter | Qt.AlignLeft, t2)
        x += fm.horizontalAdvance(t2) + 14
        p.setPen(QPen(QColor(TEXT), 1.4, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        cy = self.height() / 2.0 + 0.5
        p.drawPolyline(QPolygonF([QPointF(x, cy - 2.5), QPointF(x + 4.5, cy + 2.0), QPointF(x + 9, cy - 2.5)]))


class PlaylistBar(QueueBar):
    """Selection command bar of the Playlists page: Play, Play next, Add to, Rename, Delete."""
    NOUN = ("playlist", "playlists")
    renameRequested = Signal()
    deleteRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.btn_more.hide()
        self.btn_rename = SelBtn("Rename", "rename", False, self, True, 35)
        self.btn_delete = SelBtn("Delete", "trash", False, self, True, 35)
        for b in (self.btn_play, self.btn_next, self.btn_add):
            b.setFixedHeight(35)
        self.btn_rename.clicked.connect(self.renameRequested)
        self.btn_delete.clicked.connect(self.deleteRequested)
        self._layout()

    def _layout(self):
        if not hasattr(self, "btn_delete"):
            return
        x = self.width() - 15
        for b in (self.btn_delete, self.btn_rename, self.btn_add, self.btn_next, self.btn_play):
            x -= b.width()
            b.move(x, (self.height() - b.height()) // 2)
            x -= 16


class PlaylistsEmpty(QWidget):
    """'You don't have any playlists' + Create a new playlist (the big gradient icon is drawn here)."""
    createRequested = Signal(object)
    ICON = 131
    TEXT_X = 172

    def __init__(self, parent=None):
        super().__init__(parent)
        self.title = QLabel("You don't have any playlists", self)
        self.title.setStyleSheet(DISPLAY_QSS + "color:#fff;font-size:28px;font-weight:600;background:transparent;")
        self.title.adjustSize()
        self.title.move(self.TEXT_X, 54 - self.title.height() // 2)
        self.btn = SelBtn("Create a new playlist", "add", True, self, True, 32)
        self.btn.move(self.TEXT_X, 81)
        self.btn.clicked.connect(lambda: self.createRequested.emit(self.btn))
        self.setFixedSize(self.TEXT_X + max(self.title.width(), self.btn.width()) + 2, 150)

    def paintEvent(self, ev):
        p = QPainter(self)
        g = QLinearGradient(0, 0, self.ICON, 150)
        g.setColorAt(0.0, QColor(246, 134, 46))
        g.setColorAt(0.5, QColor(205, 50, 120))
        g.setColorAt(1.0, QColor(100, 55, 160))
        draw_playlist_glyph(p, 0, 0, 1.0, g)


class PlaylistsPage(QWidget):
    """Saved playlists as a grid (REF: Media Player Playlists). Create, sort, select, play."""
    newRequested = Signal(object)          # the widget to anchor the name flyout under
    openRequested = Signal(str)
    playRequested = Signal(str)
    menuRequested = Signal(object, QPoint)
    sortChosen = Signal(str)
    selectionChanged = Signal()
    selPlay = Signal()
    selPlayNext = Signal()
    selAdd = Signal(QPoint)
    selRename = Signal()
    selDelete = Signal()

    SIDE = 56
    TOP = 14
    HEAD_H = 46
    SLOT_H = 48
    GRID_Y = 141

    def __init__(self, parent=None):
        super().__init__(parent)
        self.cards = []
        self.sel = {}
        self.sel_mode = False
        self.heading = QLabel("Playlists", self)
        self.heading.setStyleSheet(DISPLAY_QSS + "color:#fff;font-size:38px;font-weight:600;background:transparent;")
        self.slot = QWidget(self)
        self.btn_new = SelBtn("New playlist", "add", True, self.slot, True, 32)
        self.btn_new.clicked.connect(lambda: self.newRequested.emit(self.btn_new))
        self.sort = SortButton(self.slot)
        self.sort.clicked.connect(self._sort_menu)
        self.selbar = PlaylistBar(self.slot)
        self.selbar.hide()
        self.selbar.clearRequested.connect(self.clear_selection)
        self.selbar.allToggled.connect(self._toggle_all)
        self.selbar.playRequested.connect(self.selPlay)
        self.selbar.playNextRequested.connect(self.selPlayNext)
        self.selbar.addRequested.connect(self.selAdd)
        self.selbar.renameRequested.connect(self.selRename)
        self.selbar.deleteRequested.connect(self.selDelete)
        self.scroll = QScrollArea(self)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.viewport().setAutoFillBackground(False)
        self.scroll.setStyleSheet(
            "QScrollArea{background:transparent;border:none;}"
            "QScrollBar:vertical{background:transparent;width:10px;margin:2px;}"
            "QScrollBar::handle:vertical{background:#4a4448;border-radius:4px;min-height:30px;}"
            "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;}"
            "QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical{background:transparent;}")
        self.body = QWidget()
        self.scroll.setWidget(self.body)
        self.body.setAutoFillBackground(False)            # (setWidget turns the fill on)
        self.body.setStyleSheet("background:transparent;")
        self.empty = PlaylistsEmpty(self)
        self.empty.createRequested.connect(self.newRequested)
        self.empty.hide()
        self._sync()

    # --- data
    def refresh(self, pls, sort_key, thumbs_of, animate=False):
        for c in self.cards:
            c.setParent(None)
            c.deleteLater()
        self.cards = []
        live = {p["id"]: p for p in pls}
        self.sel = {k: live[k] for k in self.sel if k in live}
        for i, pl in enumerate(pls):
            c = PlaylistCard(pl, thumbs_of(pl), self.body)
            c.clicked.connect(self.openRequested)
            c.playClicked.connect(self.playRequested)
            c.menuRequested.connect(self.menuRequested)
            c.checkToggled.connect(self._toggle)
            c.selectToggled.connect(self._toggle)
            c.sel_mode, c.selected = bool(self.sel), pl["id"] in self.sel
            if animate:
                c.appear_in(i * 35)
            else:
                c.appear.set(1)
            c.show()
            self.cards.append(c)
        self.sort.set_key(sort_key)
        self._sync()
        self._place()

    def card_of(self, pid):
        return next((c for c in self.cards if c.e["id"] == pid), None)

    # --- sort
    def _sort_menu(self):
        m = DarkMenu(self)
        for key, label in SortButton.LABELS.items():
            m.item(label, lambda k=key: self.sortChosen.emit(k), None)
        m.exec(self.sort.mapToGlobal(QPoint(0, self.sort.height() + 2)))

    # --- selection
    def selected(self):
        return [c.e for c in self.cards if c.e["id"] in self.sel]

    def is_selected(self, pid):
        return pid in self.sel

    def _toggle(self, pl):
        if pl["id"] in self.sel:
            del self.sel[pl["id"]]
        else:
            self.sel[pl["id"]] = pl
        self._sync_sel()

    def select_pl(self, pl):
        self.sel[pl["id"]] = pl
        self._sync_sel()

    def clear_selection(self):
        if self.sel:
            self.sel = {}
            self._sync_sel()

    def _toggle_all(self):
        if self.cards and len(self.sel) == len(self.cards):
            self.sel = {}
        else:
            self.sel = {c.e["id"]: c.e for c in self.cards}
        self._sync_sel()

    def _sync_sel(self):
        for c in self.cards:
            c.sel_mode, c.selected = bool(self.sel), c.e["id"] in self.sel
            c._rest = None
            c.update()
        self._sync()
        self.selectionChanged.emit()

    def _sync(self):
        has = bool(self.cards)
        self.sel_mode = bool(self.sel)
        self.empty.setVisible(not has)
        self.slot.setVisible(has)
        self.scroll.setVisible(has)
        self.selbar.set_count(len(self.sel), has and len(self.sel) == len(self.cards))
        self.selbar.setVisible(self.sel_mode)
        self.selbar.btn_rename.setEnabled(len(self.sel) == 1)
        self.btn_new.setVisible(not self.sel_mode)
        self.sort.setVisible(not self.sel_mode)

    # --- layout
    def _place(self):
        w, h = self.width(), self.height()
        S = self.SIDE
        self.heading.setGeometry(S, self.TOP, max(0, w - 2 * S), self.HEAD_H)
        self.slot.setGeometry(S + 2, self.TOP + self.HEAD_H + 14, max(0, w - 2 * S - 4), self.SLOT_H)
        self.btn_new.move(0, (self.SLOT_H - self.btn_new.height()) // 2)
        self.sort.move(max(0, self.slot.width() - 17 - self.sort.width()), (self.SLOT_H - self.sort.height()) // 2)
        self.selbar.setGeometry(0, 0, self.slot.width(), self.SLOT_H)
        self.selbar.raise_()
        gx = S - 4
        self.scroll.setGeometry(gx, self.GRID_Y, max(0, w - 2 * gx), max(0, h - self.GRID_Y))
        vw = max(1, self.scroll.viewport().width())
        cols = max(1, vw // PlaylistCard.CW)
        ch = self.cards[0].height() if self.cards else 0
        for i, c in enumerate(self.cards):
            c.move((i % cols) * c.width(), (i // cols) * ch)
        rows = -(-len(self.cards) // cols)
        self.body.setFixedSize(vw, max(1, rows * ch + 12))
        self.empty.move(max(0, (w - self.empty.width()) // 2), int(h * 0.427))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._place()

    def showEvent(self, e):
        super().showEvent(e)
        self._place()


class PlaylistDetailPage(QWidget):
    """One playlist: header card (cover, name, count, Play all / Add to / Rename / Delete) and its videos
    (the same rows as the Play queue page)."""
    playAll = Signal()
    addToRequested = Signal(QPoint)
    renameRequested = Signal()
    deleteRequested = Signal()
    playRequested = Signal(int)
    menuRequested = Signal(int, QPoint)
    removeRequested = Signal(list)
    reordered = Signal(list)
    filesDropped = Signal(list)
    selPlay = Signal()
    selPlayNext = Signal()
    selAdd = Signal(QPoint)
    selMore = Signal(QPoint)

    X = 58
    CARD_Y, CARD_H, COVER = 21, 238, 208

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pid = None
        self.pl = None
        self.pms = []
        self.title = QLabel(self)
        self.title.setStyleSheet(DISPLAY_QSS + "color:#fff;font-size:40px;font-weight:600;background:transparent;")
        self.count = QLabel(self)
        self.count.setStyleSheet(f"color:{MUTED};font-size:14px;background:transparent;")
        self.btn_play = SelBtn("Play all", "play_o", True, self, True, 34)
        self.btn_add = SelBtn("Add to", "add", False, self, True, 34)
        self.btn_rename = SelBtn("Rename", "rename", False, self, True, 34)
        self.btn_delete = SelBtn("Delete", "trash", False, self, True, 34)
        self.btn_play.clicked.connect(self.playAll)
        self.btn_add.clicked.connect(lambda: self.addToRequested.emit(
            self.btn_add.mapToGlobal(QPoint(0, self.btn_add.height() + 2))))
        self.btn_rename.clicked.connect(self.renameRequested)
        self.btn_delete.clicked.connect(self.deleteRequested)
        self.selbar = QueueBar(self)
        self.selbar.hide()
        self.list = QueueList(self)
        self.list.zebra = True        # the recording shows the first row lighter and the second plain
        self.list.playRequested.connect(self.playRequested)
        self.list.orderChanged.connect(self.reordered)
        self.list.filesDropped.connect(self.filesDropped)
        self.list.menuRequested.connect(self.menuRequested)
        self.list.checkChanged.connect(self._checks)
        self.list.deleteRequested.connect(self._delete)
        self.selbar.clearRequested.connect(self.clear_selection)
        self.selbar.allToggled.connect(self._toggle_all)
        self.selbar.playRequested.connect(self.selPlay)
        self.selbar.playNextRequested.connect(self.selPlayNext)
        self.selbar.addRequested.connect(self.selAdd)
        self.selbar.moreRequested.connect(self.selMore)
        self._place()

    def show_playlist(self, pl, thumbs, dur_of, exists):
        self.pid, self.pl = pl["id"], pl
        self.pms = [ThumbCard._load(f) for f in thumbs]
        self.count.setText(items_text(len(pl["items"])))
        self.list.refresh(pl["items"], -1, dur_of, exists)
        have = bool(pl["items"])
        self.btn_play.setEnabled(have)
        self.btn_add.setEnabled(have)
        self._fit_title()
        self._checks()
        self.update()

    def _fit_title(self):
        if self.pl is None:
            return
        fm = QFontMetrics(ui_font(40, QFont.DemiBold))
        room = max(60, self.width() - self.X * 2 - 16 - self.COVER - 14 - 16)
        self.title.setText(fm.elidedText(self.pl["name"], Qt.ElideRight, room))

    def _place(self):
        w = self.width()
        cx = self.X + 16 + self.COVER + 14
        self.title.setGeometry(cx, self.CARD_Y + 18, max(0, w - cx - self.X - 16), 54)
        self.count.setGeometry(cx + 2, self.CARD_Y + 79, 300, 20)
        x = cx + 3
        for b in (self.btn_play, self.btn_add, self.btn_rename, self.btn_delete):
            b.move(x, self.CARD_Y + 123)
            x += b.width() + 17
        top = self.CARD_Y + self.CARD_H + 6
        self.selbar.setGeometry(self.X, top, max(0, w - 2 * self.X), 48)
        self.list.setGeometry(self.X, top + 53, max(0, w - 2 * self.X), max(0, self.height() - top - 53))
        self._fit_title()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._place()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        card = QRectF(self.X, self.CARD_Y, self.width() - 2 * self.X, self.CARD_H)
        p.setPen(QPen(QColor(255, 255, 255, 10), 1))
        p.setBrush(QColor(255, 255, 255, 16))
        p.drawRoundedRect(card.adjusted(0.5, 0.5, -0.5, -0.5), 8, 8)
        if self.pl is not None:
            paint_cover(p, QRectF(self.X + 16, self.CARD_Y + 16, self.COVER, self.COVER), self.pms,
                        len(self.pl["items"]), 6)

    # --- selection of rows (same behaviour as the Play queue page)
    def _checks(self):
        n = len(self.list.checked_paths())
        self.selbar.set_count(n, n > 0 and n == self.list.count())
        self.selbar.setVisible(self.list.sel_mode)

    def checked_paths(self):
        return self.list.checked_paths()

    def is_checked(self, row):
        return self.list.is_checked(row)

    def toggle_row(self, row):
        self.list.toggle_row(row)

    def clear_selection(self):
        if self.list.sel_mode:
            self.list.check_all(False)

    def _toggle_all(self):
        self.list.check_all(not (self.list.count() and len(self.list.checked_paths()) == self.list.count()))

    def _delete(self):
        paths = self.list.checked_paths() or self.list.selected_paths()
        if paths:
            self.removeRequested.emit(paths)


class _ClearX(QAbstractButton):
    """The small x inside the name box."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.PointingHandCursor)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor(255, 255, 255, 210 if self.underMouse() else 150), 1.3, Qt.SolidLine, Qt.RoundCap))
        c = self.rect().center()
        p.drawLine(QPointF(c.x() - 4, c.y() - 4), QPointF(c.x() + 4, c.y() + 4))
        p.drawLine(QPointF(c.x() + 4, c.y() - 4), QPointF(c.x() - 4, c.y() + 4))


class NameFlyout(QWidget):
    """Small popup with a name box and one button (Create playlist / Rename), like the Media Player's."""
    accepted = Signal(str)
    W, H = 312, 112

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(self.W, self.H)
        self.edit = QLineEdit(self)
        self.edit.setGeometry(16, 14, self.W - 32, 32)
        self.edit.setMaxLength(100)
        self.edit.setStyleSheet(
            "QLineEdit{background:#1f1d1f;border:1px solid rgba(255,255,255,22);border-bottom:2px solid #f47b36;"
            "border-radius:4px;color:#fff;font-size:14px;padding:0 30px 0 9px;"
            "selection-background-color:#f47b36;selection-color:#fff;}")
        self.edit.returnPressed.connect(self._go)
        self.x = _ClearX(self)
        self.x.setGeometry(self.W - 16 - 30, 15, 28, 28)
        self.x.clicked.connect(lambda: (self.edit.clear(), self.edit.setFocus()))
        self.btn = SelBtn("Create playlist", None, True, self, True, 32)
        self.btn.clicked.connect(self._go)

    def _go(self):
        text = self.edit.text()
        self.close()
        self.accepted.emit(text)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor(255, 255, 255, 26), 1))
        p.setBrush(QColor("#2c292b"))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 8, 8)

    def popup(self, text, label, gpos):
        self.edit.setText(text)
        self.btn.setText(label)
        fm = QFontMetrics(ui_font(14))
        self.btn.setFixedWidth(26 + fm.horizontalAdvance(label))
        self.btn.move((self.W - self.btn.width()) // 2, 64)
        scr = QApplication.screenAt(gpos) or QApplication.primaryScreen()
        g = scr.availableGeometry()
        self.move(max(g.left(), min(gpos.x(), g.right() - self.W)), max(g.top(), min(gpos.y(), g.bottom() - self.H)))
        self.show()
        self.edit.setFocus()
        self.edit.selectAll()


class RenameFlyout(NameFlyout):
    """Rename popup of a Home card: the name box, Rename, and Use automatic name (clears the typed name)."""
    automatic = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.auto = SelBtn("Use automatic name", None, False, self, True, 32)
        self.auto.clicked.connect(self._auto)

    def _auto(self):
        self.close()
        self.automatic.emit()

    def popup(self, text, label, gpos):
        super().popup(text, label, gpos)
        gap = 8
        x = (self.W - (self.btn.width() + gap + self.auto.width())) // 2
        self.btn.move(x, 64)
        self.auto.move(x + self.btn.width() + gap, 64)


def _clock(ms):
    sec = int(ms // 1000)
    return f"{sec // 3600}:{sec // 60 % 60:02d}:{sec % 60:02d}" if sec >= 3600 else f"{sec // 60}:{sec % 60:02d}"


class PictureFlyout(QWidget):
    """Change picture: five sampled frames to click, 'Use this frame' (the frame of the open video) and 'Use
    automatic picture' (only when a picture was picked)."""
    framePicked = Signal(object)        # QImage (raw frame)
    useCurrent = Signal()
    automatic = Signal()
    W, MX = 344, 16
    FW, FH, FGAP, FY = 56, 31.5, 8, 60

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.path, self.name, self.frames, self.hov = "", "", [], -1
        self.cur_text, self.picked = "", False
        self.use_btn = SelBtn("Use this frame", None, False, self, True, 30)
        self.auto_btn = SelBtn("Use automatic picture", None, False, self, True, 30)
        self.use_btn.clicked.connect(lambda: (self.close(), self.useCurrent.emit()))
        self.auto_btn.clicked.connect(lambda: (self.close(), self.automatic.emit()))

    def frame_rect(self, i):
        return QRectF(self.MX + i * (self.FW + self.FGAP), self.FY, self.FW, self.FH)

    def popup(self, path, name, can_use, cur_text, picked, gpos):
        self.path, self.name, self.frames, self.hov = path, name, [], -1
        self.cur_text, self.picked = cur_text if can_use else "", picked
        self.use_btn.setEnabled(can_use)
        self.use_btn.move(self.W - self.MX - self.use_btn.width(), 128)
        self.auto_btn.move(self.W - self.MX - self.auto_btn.width(), 166)
        self.auto_btn.setVisible(picked)
        self.setFixedSize(self.W, 206 if picked else 168)
        scr = QApplication.screenAt(gpos) or QApplication.primaryScreen()
        g = scr.availableGeometry()
        self.move(max(g.left(), min(gpos.x(), g.right() - self.W)), max(g.top(), min(gpos.y(), g.bottom() - self.height())))
        self.show()

    def set_frames(self, frames):
        self.frames = list(frames)               # [(ms, picture QImage, raw QImage)]
        self.update()

    def mouseMoveEvent(self, ev):
        h = next((i for i in range(len(self.frames)) if self.frame_rect(i).contains(ev.position())), -1)
        if h != self.hov:
            self.hov = h
            self.setCursor(Qt.PointingHandCursor if h >= 0 else Qt.ArrowCursor)
            self.update()

    def leaveEvent(self, ev):
        self.hov = -1
        self.update()

    def mouseReleaseEvent(self, ev):
        if ev.button() == Qt.LeftButton and 0 <= self.hov < len(self.frames):
            raw = self.frames[self.hov][2]
            self.close()
            self.framePicked.emit(raw)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor(255, 255, 255, 26), 1))
        p.setBrush(QColor("#2c292b"))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 8, 8)
        p.setPen(QColor(TEXT))
        p.setFont(ui_font(14, QFont.DemiBold))
        p.drawText(QRectF(self.MX, 12, self.W - 2 * self.MX, 22), Qt.AlignVCenter | Qt.AlignLeft, "Change picture")
        p.setPen(QColor(MUTED))
        p.setFont(ui_font(12))
        fm = p.fontMetrics()
        p.drawText(QRectF(self.MX, 34, self.W - 2 * self.MX, 18), Qt.AlignVCenter | Qt.AlignLeft,
                   fm.elidedText(self.name, Qt.ElideRight, self.W - 2 * self.MX))
        for i in range(5):
            r = self.frame_rect(i)
            if i < len(self.frames):
                ms, pic, _raw = self.frames[i]
                p.setPen(Qt.NoPen)
                p.drawImage(r, pic)
                p.setPen(QColor(TEXT if i == self.hov else MUTED))
                p.setFont(ui_font(11))
                p.drawText(QRectF(r.left() - 4, r.bottom() + 3, r.width() + 8, 16), Qt.AlignHCenter | Qt.AlignTop, _clock(ms))
                if i == self.hov:
                    p.setBrush(Qt.NoBrush)
                    p.setPen(QPen(QColor(ACCENT), 2))
                    p.drawRoundedRect(r.adjusted(-1, -1, 1, 1), 4, 4)
            else:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(255, 255, 255, 16))
                p.drawRoundedRect(r, 4, 4)
        p.setPen(QPen(QColor(255, 255, 255, 26), 1))
        p.drawLine(self.MX, 118, self.W - self.MX, 118)
        p.setPen(QColor(MUTED))
        p.setFont(ui_font(12))
        p.drawText(QRectF(self.MX, 128, 150, 30), Qt.AlignVCenter | Qt.AlignLeft, self.cur_text)
        if self.picked:
            p.drawText(QRectF(self.MX, 166, 120, 30), Qt.AlignVCenter | Qt.AlignLeft, "Picked by you")


class Toast(QWidget):
    """Message box at the bottom right with a close button ("Playlist 'x' created.")."""
    W, H = 358, 62

    def __init__(self, parent):
        super().__init__(parent)
        self.text = ""
        self.hide()
        self.setFixedSize(self.W, self.H)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)
        parent.installEventFilter(self)

    def show_text(self, text, ms=5000):
        self.text = text
        fm = QFontMetrics(ui_font(14))
        w = max(self.W, min(fm.horizontalAdvance(text) + 90, max(self.W, self.parentWidget().width() - 32)))
        self.setFixedSize(w, self.H)
        self.reposition()
        self.show()
        self.raise_()
        self.update()
        self._timer.start(ms)

    def reposition(self):
        pw = self.parentWidget()
        self.move(max(0, pw.width() - self.width() - 16), max(0, pw.height() - self.height() - 17))

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Resize and self.isVisible():
            self.reposition()
        return False

    def _x_rect(self):
        return QRectF(self.width() - 52, 0, 44, self.height())

    def mouseReleaseEvent(self, ev):
        if ev.button() == Qt.LeftButton and self._x_rect().contains(ev.position()):
            self._timer.stop()
            self.hide()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor(255, 255, 255, 18), 1))
        p.setBrush(QColor("#2c2a2c"))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 8, 8)
        p.setPen(QColor(TEXT))
        p.setFont(ui_font(14))
        fm = p.fontMetrics()
        p.drawText(QRectF(16, 0, self.width() - 70, self.height()), Qt.AlignVCenter | Qt.AlignLeft,
                   fm.elidedText(self.text, Qt.ElideRight, self.width() - 70))
        c = self._x_rect().center()
        p.setPen(QPen(QColor(255, 255, 255, 220), 1.3, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(c.x() - 4.5, c.y() - 4.5), QPointF(c.x() + 4.5, c.y() + 4.5))
        p.drawLine(QPointF(c.x() + 4.5, c.y() - 4.5), QPointF(c.x() - 4.5, c.y() + 4.5))


class ConfirmDialog(QDialog):
    """'Delete playlist permanently?' - body, darker footer, two equal buttons (Cancel is the default)."""
    W, H = 696, 206

    def __init__(self, parent, title, text, ok_text, cancel_text="Cancel"):
        super().__init__(parent, Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setModal(True)
        self.setFixedSize(self.W, self.H)
        self.title, self.text = title, text
        self.ok = _DlgBtn(ok_text, False, self)
        self.cancel = _DlgBtn(cancel_text, True, self)
        self.ok.setGeometry(24, 150, 320, 32)
        self.cancel.setGeometry(352, 150, 320, 32)
        self.ok.clicked.connect(self.accept)
        self.cancel.clicked.connect(self.reject)
        self.cancel.setFocus()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.focusWidget().click() if isinstance(self.focusWidget(), _DlgBtn) else self.reject()
        elif e.key() == Qt.Key_Escape:
            self.reject()
        else:
            super().keyPressEvent(e)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        clip = QPainterPath()
        clip.addRoundedRect(r, 8, 8)
        p.setClipPath(clip)
        p.fillRect(self.rect(), QColor("#2b2b2b"))
        p.fillRect(QRectF(0, 126, self.W, self.H - 126), QColor("#202020"))
        p.setClipping(False)
        p.setPen(QPen(QColor(255, 255, 255, 30), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(r, 8, 8)
        p.setPen(QColor("#fff"))
        p.setFont(ui_font(20, QFont.DemiBold))
        p.drawText(QRectF(25, 22, self.W - 50, 34), Qt.AlignVCenter | Qt.AlignLeft, self.title)
        p.setFont(ui_font(14))
        p.drawText(QRectF(25, 62, self.W - 50, 54), Qt.AlignTop | Qt.AlignLeft | Qt.TextWordWrap, self.text)


class _DlgBtn(QAbstractButton):
    def __init__(self, text, accent, parent=None):
        super().__init__(parent)
        self.setText(text)
        self.accent = accent
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if self.accent:
            bg = QColor(255, 154, 64) if self.underMouse() else QColor(243, 121, 54)
        else:
            bg = QColor(60, 60, 60) if self.underMouse() else QColor(45, 45, 45)
        if self.isDown():
            bg = bg.darker(115)
        p.setPen(Qt.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(QRectF(self.rect()), 4, 4)
        if self.hasFocus() and not self.accent:
            p.setPen(QPen(QColor(255, 255, 255, 140), 1.5))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 4, 4)
        p.setPen(QColor("#1b1b1b") if self.accent else QColor("#fff"))
        p.setFont(ui_font(14))
        p.drawText(QRectF(self.rect()), Qt.AlignCenter, self.text())


class EqualizerDialog(QDialog):
    changed = Signal()

    def __init__(self, parent, state, preset_names, freqs, loader):
        super().__init__(parent)
        self.setWindowTitle("Equalizer")
        self.setMinimumWidth(600)
        self.state, self.loader = state, loader
        self._busy = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 18, 22, 18)
        lay.setSpacing(12)

        top = QHBoxLayout()
        self.enable = QCheckBox("Enable equalizer")
        self.enable.setChecked(bool(state["enabled"]))
        self.enable.toggled.connect(self._on_enable)
        self.combo = QComboBox()
        self.combo.addItem("Custom")
        self.combo.addItems(preset_names)
        self.combo.setCurrentIndex(state["preset"] + 1)
        self.combo.currentIndexChanged.connect(self._on_preset)
        reset = QPushButton("Reset")
        reset.setCursor(Qt.PointingHandCursor)
        reset.clicked.connect(self._reset)
        top.addWidget(self.enable)
        top.addStretch(1)
        top.addWidget(self.combo)
        top.addWidget(reset)
        lay.addLayout(top)

        row = QHBoxLayout()
        row.setSpacing(10)
        self.sliders, self.labels = [], []
        names = ["Preamp"] + [self._hz(f) for f in freqs]
        values = [state["preamp"]] + list(state["bands"])
        for i, (n, v) in enumerate(zip(names, values)):
            col = QVBoxLayout()
            col.setSpacing(4)
            val = QLabel()
            val.setAlignment(Qt.AlignCenter)
            val.setStyleSheet(SMALL_QSS + f"color:{MUTED};font-size:11px;")
            sl = QSlider(Qt.Vertical)
            sl.setRange(-200, 200)
            sl.setValue(int(round(v * 10)))
            sl.setFixedHeight(170)
            sl.valueChanged.connect(lambda _=0, k=i: self._on_slider(k))
            name = QLabel(n)
            name.setAlignment(Qt.AlignCenter)
            name.setStyleSheet(SMALL_QSS + "color:#fff;font-size:12px;")
            col.addWidget(val)
            col.addWidget(sl, 1, Qt.AlignHCenter)
            col.addWidget(name)
            row.addLayout(col)
            if i == 0:
                row.addSpacing(14)
            self.sliders.append(sl)
            self.labels.append(val)
        lay.addLayout(row)
        self._refresh_labels()

    @staticmethod
    def _hz(f):
        return f"{f / 1000:g}k" if f >= 1000 else f"{f:g}"

    def _refresh_labels(self):
        for sl, lab in zip(self.sliders, self.labels):
            lab.setText(f"{sl.value() / 10:+.1f}")

    def _read(self):
        self.state["preamp"] = self.sliders[0].value() / 10.0
        self.state["bands"] = [s.value() / 10.0 for s in self.sliders[1:]]

    def _on_slider(self, k):
        if self._busy:
            return
        self._read()
        self._refresh_labels()
        if self.state["preset"] != -1:
            self.state["preset"] = -1
            self._busy = True
            self.combo.setCurrentIndex(0)
            self._busy = False
        if not self.state["enabled"]:
            self.state["enabled"] = True
            self.enable.setChecked(True)
        self.changed.emit()

    def _on_enable(self, on):
        self.state["enabled"] = bool(on)
        self.changed.emit()

    def _on_preset(self, idx):
        if self._busy:
            return
        self.state["preset"] = idx - 1
        if idx > 0:
            preamp, bands = self.loader(idx - 1)
            self._busy = True
            self.sliders[0].setValue(int(round(preamp * 10)))
            for s, b in zip(self.sliders[1:], bands):
                s.setValue(int(round(b * 10)))
            self._busy = False
            self._read()
            self._refresh_labels()
            self.state["enabled"] = True
            self.enable.setChecked(True)
        self.changed.emit()

    def _reset(self):
        self._busy = True
        for s in self.sliders:
            s.setValue(0)
        self.combo.setCurrentIndex(0)
        self._busy = False
        self.state["preset"] = -1
        self._read()
        self._refresh_labels()
        self.changed.emit()


# --------------------------------------------------------------------------
# "Set as default player" (Windows) and single-instance forwarding
# --------------------------------------------------------------------------
def register_default_apps():
    """Registers Simple Player for video types for this Windows user, then opens the Default apps page."""
    if sys.platform != "win32":
        return False
    import winreg
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable)
        cmd = f'"{exe}" "%1"'
        icon = f'"{exe}",0'
    else:
        pyw = Path(sys.executable).with_name("pythonw.exe")
        exe = pyw if pyw.exists() else Path(sys.executable)
        cmd = f'"{exe}" "{Path(__file__).resolve()}" "%1"'
        icon = str(_base_dir() / "icon.ico")
    prog = "SimplePlayer.Video"

    def setv(path, name, value):
        key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, path)
        winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
        winreg.CloseKey(key)

    setv(rf"Software\Classes\{prog}", "", "Video file (Simple Player)")
    setv(rf"Software\Classes\{prog}\DefaultIcon", "", icon)
    setv(rf"Software\Classes\{prog}\shell\open\command", "", cmd)
    cap = r"Software\SimplePlayer\Capabilities"
    setv(cap, "ApplicationName", "Simple Player")
    setv(cap, "ApplicationDescription", "Minimal video player on VLC's engine")
    for ext in sorted(VIDEO_EXT):
        setv(rf"Software\Classes\{ext}\OpenWithProgids", prog, "")
        setv(cap + r"\FileAssociations", ext, prog)
    setv(r"Software\RegisteredApplications", "SimplePlayer", cap)
    try:
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)
    except Exception:
        pass
    try:
        os.startfile("ms-settings:defaultapps?registeredAppUser=SimplePlayer")
    except OSError:
        try:
            os.startfile("ms-settings:defaultapps")
        except OSError:
            pass
    return True


SERVER_NAME = "SimplePlayer.SingleInstance"


def forward_to_running_instance(files):
    """If Simple Player is already open, hand it the files and return True."""
    sock = QLocalSocket()
    sock.connectToServer(SERVER_NAME)
    if not sock.waitForConnected(250):
        return False
    sock.write(json.dumps(files).encode("utf-8"))
    sock.flush()
    sock.waitForBytesWritten(1000)
    sock.disconnectFromServer()
    return True


# --------------------------------------------------------------------------
# Custom title bar (Windows Media Player style)
# --------------------------------------------------------------------------
TITLEBAR_H = 32                    # measured: Media Player bar is 75% of the earlier 44 px bar in the same screenshot
CAPTION_BTN_H = 32
TITLEBAR_BG = "#141414"
MINI_TB_H = 29                      # mini player title bar (measured on the Media Player mini view: 36 px at 125 %)
MINI_TB_BG = "#1a1a1a"              # measured: 26 / 255

HTCAPTION = 2
HTLEFT, HTRIGHT, HTTOP, HTTOPLEFT, HTTOPRIGHT = 10, 11, 12, 13, 14
HTBOTTOM, HTBOTTOMLEFT, HTBOTTOMRIGHT = 15, 16, 17


def calc_client(rect, fx, fy, zoomed, full):
    """New client rect (l, t, r, b) for WM_NCCALCSIZE: the whole window, minus the thin invisible
    resize borders left/right/bottom (so the video can never cover them)."""
    l, t, r, b = rect
    if full:
        return l, t, r, b
    if zoomed:                                  # a maximised window overhangs the screen by the frame
        return l + fx, t + fy, r - fx, b - fy
    return l + fx, t, r - fx, b - fy


def visible_overhang(client_br, visible_br, dpr):
    """How far (logical px) the client area reaches past the visible window on the right and bottom.
    client_br / visible_br are the bottom-right corners in physical screen pixels."""
    r = max(0, client_br[0] - visible_br[0])
    b = max(0, client_br[1] - visible_br[1])
    if r > 80 or b > 80:                         # not a frame border: do not trust it
        return 0, 0
    return int(round(r / dpr)), int(round(b / dpr))


def hit_code(wx, wy, w, h, fx, fy, top_zone, zoomed, full, in_caption, over_button):
    """WM_NCHITTEST answer for a point (wx, wy) relative to the window rect, or None = let Qt decide."""
    if not zoomed and not full:
        left, right = wx < fx, wx >= w - fx
        bottom, top = wy >= h - fy, wy < top_zone
        if top and left:
            return HTTOPLEFT
        if top and right:
            return HTTOPRIGHT
        if bottom and left:
            return HTBOTTOMLEFT
        if bottom and right:
            return HTBOTTOMRIGHT
        if left:
            return HTLEFT
        if right:
            return HTRIGHT
        if bottom:
            return HTBOTTOM
        if top:
            return HTTOP
    if in_caption and not over_button:
        return HTCAPTION                         # Windows then handles drag, snap and double-click
    return None


class CaptionButton(QAbstractButton):
    """Minimise / maximise / restore / close, drawn like the Windows 11 ones."""

    def __init__(self, kind, parent=None):
        super().__init__(parent)
        self.kind = kind
        self.hover = Tween(self, 0, 120, self.update)
        self.setFixedSize(46, CAPTION_BTN_H)
        self.setFocusPolicy(Qt.NoFocus)

    def set_kind(self, kind):
        if kind != self.kind:
            self.kind = kind
            self.update()

    def enterEvent(self, e):
        self.hover.to(1, 120)

    def leaveEvent(self, e):
        self.hover.to(0, 160)

    def paintEvent(self, e):
        p = QPainter(self)
        h = max(0.0, min(1.0, self.hover.value))
        if h > 0.01:
            col = QColor("#c42b1c") if self.kind == "close" else QColor(255, 255, 255)
            col.setAlpha(int((255 if self.kind == "close" else 22) * h))
            p.fillRect(self.rect(), col)
        cx, cy = self.width() / 2, self.height() / 2
        k = 4.2                                   # half size of a glyph (measured on the Media Player buttons)
        p.setPen(QPen(QColor(255, 255, 255, 235), 1.0))
        p.setBrush(Qt.NoBrush)
        if self.kind == "min":
            p.drawLine(QPointF(cx - k, cy + 0.5), QPointF(cx + k, cy + 0.5))
        elif self.kind == "max":
            p.drawRect(QRectF(cx - k + 0.5, cy - k + 0.5, 2 * k - 1, 2 * k - 1))
        elif self.kind == "restore":
            d = 2 * k - 2.2
            p.drawRect(QRectF(cx - k + 0.5, cy - k + 2.7, d, d))
            p.drawPolyline(_poly([(cx - k + 2.7, cy - k + 2.2), (cx - k + 2.7, cy - k + 0.5),
                                  (cx + k - 0.5, cy - k + 0.5), (cx + k - 0.5, cy + k - 2.7),
                                  (cx + k - 2.2, cy + k - 2.7)]))
        else:
            p.setRenderHint(QPainter.Antialiasing)
            p.drawLine(QPointF(cx - k, cy - k), QPointF(cx + k, cy + k))
            p.drawLine(QPointF(cx - k, cy + k), QPointF(cx + k, cy - k))


class TitleBar(QWidget):
    """Back arrow, app icon, title and (on Windows) the three window buttons."""
    doubleClicked = Signal()

    EDGE = 5

    def _edge(self, pos):
        """Window edges under `pos` (the bar is a native child, so Windows does not see its top border)."""
        edges = Qt.Edges()
        if pos.y() < self.EDGE:
            edges |= Qt.TopEdge
            if pos.x() < 2 * self.EDGE:
                edges |= Qt.LeftEdge
            elif pos.x() > self.width() - 2 * self.EDGE:
                edges |= Qt.RightEdge
        return edges

    def mouseMoveEvent(self, e):
        if self.caption and not self.window().isMaximized() and self._edge(e.position()):
            self.setCursor(Qt.SizeFDiagCursor if self._edge(e.position()) != Qt.TopEdge else Qt.SizeVerCursor)
        else:
            self.unsetCursor()

    def mousePressEvent(self, e):
        # Windows normally handles dragging itself (WM_NCHITTEST). This is the backup.
        if self.caption and e.button() == Qt.LeftButton:
            wh = self.window().windowHandle()
            if wh is None:
                return
            edges = self._edge(e.position())
            if edges and not self.window().isMaximized():
                wh.startSystemResize(edges)
            elif e.position().y() >= self.EDGE:
                wh.startSystemMove()

    def mouseDoubleClickEvent(self, e):
        if self.caption and e.button() == Qt.LeftButton:
            self.doubleClicked.emit()

    def __init__(self, parent=None, caption=True):
        super().__init__(parent)
        self.setFixedHeight(TITLEBAR_H)
        self.setMouseTracking(True)
        self.caption = caption
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 0, 0, 0)
        lay.setSpacing(0)
        self.back = IconButton("back", 40, 20)
        self.back.setFixedSize(40, TITLEBAR_H - 4)
        self.back.shape = "rect"
        self.side = False
        sp = self.back.sizePolicy()
        sp.setRetainSizeWhenHidden(True)
        self.back.setSizePolicy(sp)
        self.back.setEnabled(False)
        self.back.hide()
        lay.addWidget(self.back)
        self.btn_min = self.btn_max = self.btn_close = None
        self.mini_mode = False
        self._icon = self._icon_gap = None
        if caption:
            lay.addSpacing(8)
            dpr = 2.0
            icon = QLabel()
            pm = draw_app_icon(int(18 * dpr))
            pm.setDevicePixelRatio(dpr)
            icon.setPixmap(pm)
            icon.setFixedSize(18, 18)
            self._icon = icon
            lay.addWidget(icon)
            lay.addSpacing(10)
            self._icon_gap = lay.itemAt(lay.count() - 1)
            self.title = QLabel(APP_TITLE)
            self.title.setStyleSheet(SMALL_QSS + "color:#ffffff;font-size:12px;")
            lay.addWidget(self.title)
        lay.addStretch(1)
        if caption:
            self.btn_min = CaptionButton("min")
            self.btn_max = CaptionButton("max")
            self.btn_close = CaptionButton("close")
            for b in (self.btn_min, self.btn_max, self.btn_close):
                lay.addWidget(b, 0, Qt.AlignTop)

    def set_mini(self, on):
        """Mini player: our icon, the title and ONLY the close button, a little lower (29 px) than the normal bar."""
        if on == self.mini_mode or self.btn_close is None:
            return
        self.mini_mode = on
        h = MINI_TB_H if on else TITLEBAR_H
        self.setFixedHeight(h)
        sp = self.back.sizePolicy()
        sp.setRetainSizeWhenHidden(not on)
        self.back.setSizePolicy(sp)
        if on:
            self.back.hide()
        self.btn_min.setVisible(not on)
        self.btn_max.setVisible(not on)
        self.btn_close.setFixedSize(46, h if on else CAPTION_BTN_H)
        px = 16 if on else 18
        pm = draw_app_icon(int(px * 2.0))
        pm.setDevicePixelRatio(2.0)
        self._icon.setPixmap(pm)
        self._icon.setFixedSize(px, px)
        self._icon_gap.changeSize(13 if on else 10, 0)
        m = self.layout().contentsMargins()
        self.layout().setContentsMargins(4, m.top(), m.right(), m.bottom())
        self.layout().invalidate()
        self.update()

    def set_nav(self, kind, show):
        """kind: 'home' or 'back'. Back with no previous view stays visible but disabled (dim grey), so the
        spot never looks empty and nothing shifts."""
        if self.mini_mode:
            return
        self.back.set_kind(kind)
        self.back.setVisible(show or kind == "back")
        self.back.setEnabled(show)

    def set_maximized(self, on):
        if self.btn_max is not None:
            self.btn_max.set_kind("restore" if on else "max")

    def set_gap(self, px):
        """Space to the right of the close button."""
        m = self.layout().contentsMargins()
        if m.right() != px:
            self.layout().setContentsMargins(m.left(), m.top(), px, m.bottom())

    def set_side(self, on):
        """Library pages: the left column continues the sidebar colour and the bar blends into the page."""
        if on != self.side:
            self.side = on
            self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        if self.side:
            p.fillRect(self.rect(), QColor(FLAT_TITLE))              # native child window: always flat (step 1)
            p.fillRect(QRect(0, 0, NAV_W, self.height()), QColor(FLAT_NAV))
        else:
            p.fillRect(self.rect(), QColor(MINI_TB_BG if self.mini_mode else TITLEBAR_BG))


# --------------------------------------------------------------------------
# Main window
# --------------------------------------------------------------------------
class TraceMarker(QWidget):
    """Diagnosis only (an empty trace.txt exists): a 90x10 px strip at the top of the window. One magenta cell and 8 cells that show
    the number of the latest trace line in bits (blue = 0, yellow = 1). A screen recording can then be matched to trace.log frame by frame."""
    CELL = 10

    def __init__(self):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setFixedSize(self.CELL * 9, self.CELL)
        self.n = 0

    def paintEvent(self, e):
        p = QPainter(self)
        c = self.CELL
        p.fillRect(0, 0, c, c, QColor(255, 0, 255))
        for i in range(8):
            bit = (self.n >> (7 - i)) & 1
            p.fillRect((i + 1) * c, 0, c, c, QColor(255, 255, 0) if bit else QColor(0, 0, 255))


class FrameTrace(QObject):
    """Diagnosis only (an empty trace.txt in %APPDATA%\\SimplePlayer): writes trace.log with one line for every show, hide, move, resize,
    z-order change, mask change, page switch, loading step and VLC event, with a millisecond clock, and shows the line number in a
    marker strip (TraceMarker) so a screen recording can be matched to it. It only watches: it changes nothing in the player."""
    TYPES = {QEvent.Show: "Show", QEvent.Hide: "Hide", QEvent.Resize: "Resize", QEvent.Move: "Move", QEvent.ZOrderChange: "ZOrder"}
    STAGE_CALLS = ("fade_switch", "move_to_video", "move_to_home", "_move_begin", "_move_finish", "fade_cover", "_set_curtain",
                   "_fade_back", "_end_curtain", "begin_loading", "_show_loader", "end_loading", "show_home", "show_controls",
                   "set_overlay", "_set_video_visible", "_round_mask", "set_mini")

    def __init__(self, win):
        super().__init__(win)
        self.win, self.n, self.t0 = win, 0, time.monotonic()
        self.lock = threading.Lock()
        self.pending = []
        self.names = {}
        self.inner_last = None
        self._busy = False
        self.fh = open(win.store.dir / "trace.log", "w", encoding="utf-8", buffering=1)
        self.fh.write(f"trace started {time.strftime('%Y-%m-%d %H:%M:%S')}  (line number in the marker = line number mod 256)\n")
        self.marker = TraceMarker()
        self._place()
        self.marker.show()
        st = win.stage
        for name, w in (("video", st.video), ("home", st.home), ("controls", st.controls), ("loader", st.loader),
                        ("curtain", st.curtain), ("stage", st), ("window", win)):
            self.names[id(w)] = name
            w.installEventFilter(self)
        self._wrap(st.video, "setMask", "video.setMask")
        self._wrap(st.loader, "start", "loader.start")
        self._wrap(st.loader, "stop", "loader.stop")
        for nm in self.STAGE_CALLS:
            self._wrap(st, nm, "stage." + nm)
        for nm in ("play_index", "show_home", "toggle_mini"):
            self._wrap(win, nm, "window." + nm)
        try:
            ev = win.player.event_manager()
            for nm in ("MediaPlayerOpening", "MediaPlayerPlaying", "MediaPlayerVout", "MediaPlayerStopped", "MediaPlayerEndReached",
                       "MediaPlayerPaused", "MediaPlayerSeekableChanged"):
                et = getattr(vlc.EventType, nm, None)
                if et is not None:
                    ev.event_attach(et, lambda e, _n=nm: self._from_thread("vlc " + _n))
        except Exception as ex:
            self.note(f"vlc events not attached: {ex}")
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(8)
        self._objs = [st.video, st.home, st.controls, st.loader, st.curtain, st, win]
        QApplication.instance().aboutToQuit.connect(self.stop)
        self.note("trace on")

    def stop(self):
        """At quit: stop watching first, so that nothing touches the marker while the windows are being deleted."""
        try:
            self.timer.stop()
            for w in self._objs:
                w.removeEventFilter(self)
            self.marker.close()
            self.fh.close()
        except Exception:
            pass

    def _place(self):
        g = self.win.frameGeometry()
        self.marker.move(g.x() + (g.width() - self.marker.width()) // 2, g.y())

    def _args(self, a):
        out = []
        for x in a:
            if isinstance(x, QRegion):
                r = x.boundingRect()
                out.append(f"region {r.x()},{r.y()} {r.width()}x{r.height()}")
            elif callable(x):
                out.append("fn")
            else:
                out.append(repr(x)[:40])
        return "(" + ", ".join(out) + ")"

    def _wrap(self, obj, name, label):
        orig = getattr(obj, name, None)
        if orig is None:
            return
        tr = self

        def wrapper(*a, **k):
            tr.note(f"call {label}{tr._args(a)}")
            return orig(*a, **k)
        try:
            setattr(obj, name, wrapper)
        except Exception as ex:
            self.note(f"cannot watch {label}: {ex}")

    def _from_thread(self, text):
        with self.lock:
            self.pending.append((time.monotonic(), text))

    def note(self, text, obj=None, at=None):
        self.n += 1
        extra = ""
        st = self.win.stage
        try:
            if obj is not None:
                g = obj.geometry()
                extra = f"  geo={g.x()},{g.y()} {g.width()}x{g.height()} vis={int(obj.isVisible())}"
                if obj is st.video:
                    m = obj.mask()
                    extra += " mask=none" if m.isEmpty() else " mask=%d,%d %dx%d" % m.boundingRect().getRect()
            extra += f"  curtain_a={getattr(st, 'cur_alpha', '?')}"
        except Exception:
            pass
        ms = ((at or time.monotonic()) - self.t0) * 1000
        self.fh.write(f"#{self.n:05d} T+{ms:9.1f}  {text}{extra}\n")
        self.marker.n = self.n & 255
        self.marker.update()                  # not repaint(): that fed new events back into this filter

    def eventFilter(self, obj, ev):
        t = ev.type()
        if t in self.TYPES and not self._busy:
            nm = self.names.get(id(obj))
            if nm:
                self._busy = True
                try:
                    self.note(f"{nm} {self.TYPES[t]}", obj)
                except Exception:
                    pass
                finally:
                    self._busy = False
        return False

    def _inner(self):
        """VLC makes its own window inside our video window: log its rectangle when it changes (is it late after our resize?)."""
        if True:                                         # PHASE 12a: VLC has no inner window any more
            return
        try:
            u32 = ctypes.windll.user32
            from ctypes import wintypes
            parent = int(self.win.stage.video.winId())
            child = u32.GetWindow(wintypes.HWND(parent), 5)           # GW_CHILD
            if not child:
                state = "none"
            else:
                r, pr = wintypes.RECT(), wintypes.RECT()
                u32.GetWindowRect(wintypes.HWND(child), ctypes.byref(r))
                u32.GetWindowRect(wintypes.HWND(parent), ctypes.byref(pr))
                state = "%d,%d %dx%d vis=%d" % (r.left - pr.left, r.top - pr.top, r.right - r.left, r.bottom - r.top,
                                               int(bool(u32.IsWindowVisible(wintypes.HWND(child)))))
            if state != self.inner_last:
                self.inner_last = state
                self.note("vlc inner window " + state)
        except Exception:
            pass

    def _tick(self):
        now = time.monotonic()
        gap = (now - getattr(self, "_last_tick", now)) * 1000
        self._last_tick = now
        if gap > 60:                                # this 8 ms timer ran late: the UI thread was busy or blocked for that long
            self.note(f"UI stall: the UI thread was blocked for about {gap:.0f} ms (it ended just now)")
        with self.lock:
            items, self.pending = self.pending, []
        for at, text in items:
            self.note(text, at=at)
        self._inner()
        self._place()


class MainWindow(QMainWindow):
    NORMAL_MIN = QSize(820, 520)
    MINI_SIZE = QSize(384, 216)                # 16:9 picture, title bar on top of it (measured on the Media Player)
    durationFound = Signal(str, int)         # from the background duration scan

    def __init__(self, start_files):
        super().__init__()
        self.store = Store()
        self._make_engine()

        self.queue, self.qi, self.current = [], -1, None
        self.volume = int(self.store.data.get("volume", 100))
        self.muted = bool(self.store.data.get("muted", False))
        self.shuffle = bool(self.store.data.get("shuffle", False))
        self.repeat = int(self.store.data.get("repeat", 0))
        self.rate = 1.0
        self._pref_tracks = {"audio": None, "spu": None}   # language the user picked, kept for the next item
        self._pending = None
        self._ended = False
        self._last_state_play = None
        self._from_video = False                     # True once Home was opened from the video page
        self._mini_down = False
        self._blk_down = self._x_down = False         # PHASE B: press on the title block / on the X
        self._blk_dbg = False
        self._blk_home = False                        # was Home showing when the press began
        self.slotx = SlotX(self)
        self._mini_press = None                      # (global pos, button or None) of a press in the mini player
        self._mini_moved = False
        self._log_state = None
        self._started = False
        self._start_files = start_files
        self.mini = False
        self._was_max = False
        self._normal_geo = None
        self._topmost = False
        self._skip_release = False
        self._g = None
        self._last_live = 0.0
        self._wheel_target, self._wheel_t, self._hold_last = 0, 0.0, 0
        self._completed = set()          # videos completed during this watch (history key)
        self._seen_playing = False
        self._last_wheel = 0.0
        self._last_pos = QCursor.pos()
        self._last_move = time.monotonic()
        self._vt_last = -1
        self._vt_stamp = time.monotonic()
        self._tick_n = 0
        self._snap_n = 0
        self._thumb_for = None
        self._lbl_cur = ""
        self._lbl_tot = ""
        self._zoom, self._zoom_done = "fit", True
        self._aspect = None
        self._adelay = self._sdelay = 0
        self._eq_dlg = None
        self._queue_from_video = False
        self.autoplay_next = bool(self.store.data.get("autoplay_next", True))
        self._load_settings()
        self._exist_cache = {}
        self._folder_cache = {}
        self._dir_cache = {}                  # folder -> (mtime, subfolders): the folder tree walk is cached too
        self._tree_cache = {}                 # saved folder -> (signature, tree)
        self._group_of = {}                   # path key -> play order of the group the video is shown in
        self._folder_sizes = {}               # path -> size, from the folder scan (no extra stat per file)
        self._folder_mtimes = {}              # path -> modified time, same scan (for "Date modified")
        self._undo = []                       # removals Ctrl+Z can bring back (this session, newest last)
        self._home_sig = None                 # what Home last showed, to see if a rescan changed anything
        self._deact_t = 0.0
        self._focus_chk = 0.0
        self._folder_of = {}                  # path key -> folder, for every video shown in a Home folder section
        self._dirstack_of = {}                # PHASE B: path key -> key of its subfolder stack (last played video)
        self._durations = {}
        self._scan_pending, self._scan_running = [], False
        self._probe = None
        self._scan_lock = threading.Lock()
        self._closing = False
        self._queue_timer = None
        self._server = None

        # Our own title bar on Windows. Put an empty file called native_titlebar.txt in
        # %APPDATA%\\SimplePlayer to go back to the standard Windows title bar.
        self._custom_frame = (sys.platform == "win32"
                              and not (self.store.dir / "native_titlebar.txt").exists())
        if self._custom_frame:
            self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)
        self.setWindowTitle(APP_TITLE)
        self.setWindowIcon(make_app_icon())
        self.setMinimumSize(self.NORMAL_MIN)
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.StrongFocus)

        self.stage = Stage(self.store)
        self.stage.auto = True                      # video page: bars fade away when idle
        self.titlebar = TitleBar(caption=self._custom_frame or bool(os.environ.get("SIMPLEPLAYER_FORCE_TITLEBAR")))
        self.shell = QWidget()
        pal = self.shell.palette()
        pal.setColor(QPalette.Window, QColor("#000000"))
        self.shell.setAutoFillBackground(True)
        self.shell.setPalette(pal)
        sl = QVBoxLayout(self.shell)
        self._shell_lay = sl
        self._tb_show = True
        self._tb_tw = Tween(self, 1.0, 240, self._tb_step)
        self._tb_alpha = None
        self._margins = (0, 0, TITLEBAR_H)
        self._overhang = (0, 0)
        self._fit_pending = False
        sl.setContentsMargins(0, 0, 0, 0)                # the Stage covers the whole window; Home keeps the title bar's room inside it (Stage.home_off)
        sl.setSpacing(0)
        sl.addWidget(self.stage, 1)
        self.titlebar.setParent(self.shell)              # not in the layout: placed by hand, over the stage
        self.titlebar.setAttribute(Qt.WA_NativeWindow, True)
        self.titlebar.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self.shell.installEventFilter(self)
        self.stage.raisedChild.connect(self._titlebar_on_top)
        self.setCentralWidget(self.shell)
        self._place_titlebar()
        self.titlebar.show()
        self.titlebar.back.clicked.connect(self.go_back)
        self.titlebar.doubleClicked.connect(self._toggle_maximize)
        self.stage.controls.thumb.clicked.connect(self._slot_thumb_clicked)
        if self.titlebar.btn_min is not None:
            self.titlebar.btn_min.clicked.connect(self.showMinimized)
            self.titlebar.btn_max.clicked.connect(self._toggle_maximize)
            self.titlebar.btn_close.clicked.connect(self.close)
        self.osd = Osd(self)
        self.bubble = TimeBubble(self)
        self.mini_ov = MiniOverlay(self)
        self.volpop = VolumePopup(self)
        self.volpop.changed.connect(lambda v: self.set_volume(v, osd=False))
        self.volpop.muteClicked.connect(self.toggle_mute)
        self.volpop.wheel.connect(self.step_volume)

        self._wire()
        self.durationFound.connect(self._on_duration)
        self._init_thumbs()
        self._init_tips()
        self._slot_cropped = False
        self.stage.miniShown.connect(lambda on: self._slot_crop())
        self.stage.pic_rect_fn = self._video_picture_rect
        self.stage._log_fn = self._log
        self.stage.video.installEventFilter(self)
        self.stage.installEventFilter(self)
        self._click_timer = QTimer(self, singleShot=True, interval=230)
        self._click_timer.timeout.connect(self.toggle_play)

        geo = self.store.data.get("geometry")
        if geo and self.store.data.get("geometry_v") == 2:
            self.restoreGeometry(QByteArray.fromHex(geo.encode()))
        else:
            self.resize(1100, 780)

        self._wd_n = 0
        self._wd_file = None
        try:
            self._wd_file = open(self.store.dir / "freeze.log", "a", encoding="utf-8")   # a frozen window writes where it is stuck
        except Exception:
            pass
        self.timer = QTimer(self, interval=16)           # ~60 fps UI clock
        self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.timeout.connect(self._tick)
        self.idle = QTimer(self, interval=100)
        self.idle.timeout.connect(self._idle_tick)
        self.saver = QTimer(self, interval=5000)
        self.saver.timeout.connect(self._periodic)
        self.mini_timer = QTimer(self, interval=30)
        self.mini_timer.timeout.connect(self._mini_poll)

        self._refresh_buttons()
        self._start_server()

    # ---------------------------------------------------------------- setup
    def _wire(self):
        c = self.stage.controls
        c.play.clicked.connect(self.toggle_play)
        c.back.clicked.connect(lambda: self.seek_rel(-self.rewind_s * 1000))
        c.fwd.clicked.connect(lambda: self.seek_rel(self.ff_s * 1000))
        c.next.clicked.connect(self.next_item)
        c.prev.clicked.connect(self.prev_item)
        c.shuffle.clicked.connect(self.toggle_shuffle)
        c.repeat.clicked.connect(self.cycle_repeat)
        c.full.clicked.connect(self.toggle_fullscreen)
        c.mini.clicked.connect(self.toggle_mini)
        c.vol.clicked.connect(self.open_volume_popup)
        c.cc.clicked.connect(lambda: self.show_menu(c.cc, tracks_only=True))
        c.more.clicked.connect(lambda: self.show_menu(c.more))
        c.seek.moved.connect(self._seek_moved)
        c.seek.released.connect(self._seek_released)
        c.seek.wheel_enabled = True
        c.seek.wheelStep.connect(self._seek_wheel)
        c.seek.hoverMoved.connect(lambda v, pt: self.bubble.show_time(fmt(v), pt))
        c.seek.hoverLeft.connect(self.bubble.hide)
        self.stage.home.openRequested.connect(self.open_dialog)
        self.stage.home.openFolderRequested.connect(self.open_folder_dialog)
        self.stage.home.pageChanged.connect(self._page_changed)
        qp = self.stage.home.queue_page
        qp.playRequested.connect(self._play_from_queue)
        qp.addRequested.connect(self.add_dialog)
        qp.addFolderRequested.connect(self.add_folder_dialog)
        qp.clearRequested.connect(self.clear_queue)
        qp.addToRequested.connect(self._queue_add_menu)
        qp.removeRequested.connect(self.remove_from_queue)
        qp.reordered.connect(self.reorder_queue)
        qp.filesDropped.connect(self.add_to_queue)
        qp.menuRequested.connect(self._queue_menu)
        qp.selPlay.connect(self._qsel_play)
        qp.selPlayNext.connect(self._qsel_play_next)
        qp.selAdd.connect(self._queue_add_menu)
        qp.selMore.connect(self._qsel_more_menu)
        sp = self.stage.home.settings_page
        sp.toggled.connect(self._on_setting_toggle)
        sp.comboRequested.connect(self._on_setting_combo)
        sp.buttonClicked.connect(self._on_setting_button)
        sp.rulesRemove.connect(self._remove_rule)
        self.volpop.slider.setMaximum(self.max_volume)
        self._sync_settings()
        self.stage.home.playRequested.connect(self._home_play)
        self.stage.home.cardMenuRequested.connect(self._card_menu)
        self.stage.home.folderMenuRequested.connect(self._heading_menu)
        self.stage.home.sortChosen.connect(self._home_sort_pick)
        hp = self.stage.home.home_page
        hp.selbar.playRequested.connect(self._sel_play)
        hp.selbar.playNextRequested.connect(self._sel_play_next)
        hp.selbar.addRequested.connect(self._sel_add_menu)
        hp.selbar.moreRequested.connect(self._sel_more_menu)
        self._wire_playlists()

    def showEvent(self, e):
        super().showEvent(e)
        if not self._started:
            self._started = True
            if (self.store.dir / "trace.txt").exists():   # diagnosis switch: see the README
                try:
                    self._trace = FrameTrace(self)
                except Exception as ex:
                    self._log(f"trace failed: {ex}")
            try:                                   # which font and weight Qt really picks (for font checks)
                from PySide6.QtGui import QFontInfo
                for px, wt in ((14, QFont.Normal), (14, QFont.DemiBold), (14, QFont.Bold), (20, QFont.Bold), (40, QFont.DemiBold)):
                    fi = QFontInfo(ui_font(px, wt))
                    self._log(f"font {px}px asked weight {int(wt)} -> {fi.family()} / {fi.styleName()} weight {fi.weight()} exact {fi.exactMatch()}")
            except Exception:
                pass
            if self._custom_frame:
                self._install_frame()
            else:
                style_titlebar(self)
            QTimer.singleShot(0, self._startup)

    # ------------------------------------------------- custom frame (Windows only)
    def _install_frame(self):
        """Give the frameless window a real resizable/snappable Windows frame whose client area
        covers everything (so our own title bar can take the place of the standard one)."""
        try:
            user32 = ctypes.windll.user32
            hwnd = ctypes.c_void_p(int(self.winId()))
            user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
            user32.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
            user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
            user32.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
            style = user32.GetWindowLongPtrW(hwnd, -16)
            style |= 0x00C00000 | 0x00040000 | 0x00020000 | 0x00010000 | 0x00080000
            user32.SetWindowLongPtrW(hwnd, -16, style)          # CAPTION|THICKFRAME|MIN|MAX|SYSMENU
            user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x0020 | 0x0002 | 0x0001 | 0x0004 | 0x0010)

            class MARGINS(ctypes.Structure):
                _fields_ = [("l", ctypes.c_int), ("r", ctypes.c_int), ("t", ctypes.c_int), ("b", ctypes.c_int)]
            m = MARGINS(0, 0, 1, 0)                              # shadow and rounded corners (top edge only)
            dwm = ctypes.windll.dwmapi
            dwm.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(m))

            def dwm_attr(attr, value):                           # dark frame, so no light border shows
                v = ctypes.c_int(value)
                dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(v), ctypes.sizeof(v))
            dwm_attr(20, 1)                                      # dark mode
            dwm_attr(33, 2)                                      # round corners
            dwm_attr(34, 0x002C2626)                             # border colour (same as the title bar)
        except Exception as exc:
            self._log(f"custom frame setup failed: {exc}")

    def _fit_soon(self):
        if not self._fit_pending:
            self._fit_pending = True
            QTimer.singleShot(0, self._fit_visible)
            for ms in (150, 500):          # Windows settles the frame after the call: look again (leaving maximise / full screen)
                QTimer.singleShot(ms, self._fit_visible)

    def _fit_visible(self):
        """If Windows draws the window smaller than the area we paint into (right and bottom), trim the
        layout to the visible frame, so nothing (buttons, picture centre) ends up outside it."""
        self._fit_pending = False
        r = b = 0
        try:
            if sys.platform == "win32" and self._custom_frame and not self.isFullScreen() \
                    and not self.isMaximized():
                hwnd = ctypes.c_void_p(int(self.winId()))
                u32 = ctypes.windll.user32
                rc, ext, pt, wr = wintypes.RECT(), wintypes.RECT(), wintypes.POINT(), wintypes.RECT()
                u32.GetClientRect(hwnd, ctypes.byref(rc))
                u32.GetWindowRect(hwnd, ctypes.byref(wr))
                pt.x, pt.y = rc.right, rc.bottom
                u32.ClientToScreen(hwnd, ctypes.byref(pt))
                if ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(ext), ctypes.sizeof(ext)) == 0:
                    r, b = visible_overhang((pt.x, pt.y), (ext.right, ext.bottom), self.devicePixelRatioF())
                    dpr = self.devicePixelRatioF()
                    qr = int(round(self.width() * dpr)) - (rc.right - rc.left)     # Qt paints wider than the window's client area
                    qb = int(round(self.height() * dpr)) - (rc.bottom - rc.top)
                    if 0 < qr <= 80:
                        r = max(r, int(round(qr / dpr)))
                    if 0 < qb <= 80:
                        b = max(b, int(round(qb / dpr)))
                    diag = (f"frame: window rect right {wr.right} bottom {wr.bottom}, client right {pt.x} bottom {pt.y}, "
                            f"visible frame right {ext.right} bottom {ext.bottom}, scale {self.devicePixelRatioF():.2f}, "
                            f"client size {rc.right - rc.left}x{rc.bottom - rc.top}, Qt size {self.width()}x{self.height()}")
                    if diag != getattr(self, "_fit_diag", None):
                        self._fit_diag = diag
                        self._log(diag)
        except Exception:
            r = b = 0
        if sys.platform == "win32" and self._custom_frame:
            self.titlebar.set_gap(0 if self.isMaximized() or self.isFullScreen() else caption_gap((r, b)))
        if (r, b) != self._overhang:
            self._overhang = (r, b)
            self._apply_margins()
            self._log(f"client area is larger than the visible window: right {r}, bottom {b} (logical px), trimmed")

    def _frame_metrics(self, hwnd):
        user32 = ctypes.windll.user32
        try:
            dpi = user32.GetDpiForWindow(hwnd)
        except Exception:
            dpi = 96

        def metric(i):
            try:
                return user32.GetSystemMetricsForDpi(i, dpi)
            except Exception:
                return user32.GetSystemMetrics(i)
        return dpi, metric(32) + metric(92), metric(33) + metric(92)   # SIZEFRAME + PADDEDBORDER

    def nativeEvent(self, eventType, message):
        if not self._custom_frame or wintypes is None or bytes(eventType) != b"windows_generic_MSG":
            return super().nativeEvent(eventType, message)
        try:
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == 0x0083 and msg.wParam:             # WM_NCCALCSIZE
                class NCCALCSIZE_PARAMS(ctypes.Structure):
                    _fields_ = [("rgrc", wintypes.RECT * 3), ("lppos", ctypes.c_void_p)]
                params = ctypes.cast(msg.lParam, ctypes.POINTER(NCCALCSIZE_PARAMS)).contents
                r = params.rgrc[0]
                hwnd = ctypes.c_void_p(msg.hWnd)
                _, fx, fy = self._frame_metrics(hwnd)
                r.left, r.top, r.right, r.bottom = calc_client(
                    (r.left, r.top, r.right, r.bottom), fx, fy,
                    bool(ctypes.windll.user32.IsZoomed(hwnd)), self.isFullScreen())
                return True, 0
            if msg.message in (0x0005, 0x0047):                  # WM_SIZE, WM_WINDOWPOSCHANGED
                self._fit_soon()
            if msg.message == 0x0084:                            # WM_NCHITTEST
                code = self._hit_test(msg)
                if code is not None:
                    return True, code
        except Exception:
            self._native_errors = getattr(self, "_native_errors", 0) + 1
            if self._native_errors <= 3:                          # keep a trace in player.log
                import traceback
                self._log("nativeEvent error: " + traceback.format_exc().replace("\n", " | "))
        return super().nativeEvent(eventType, message)

    def _hit_test(self, msg):
        user32 = ctypes.windll.user32
        hwnd = ctypes.c_void_p(msg.hWnd)
        x = ctypes.c_short(msg.lParam & 0xFFFF).value
        y = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        dpi, fx, fy = self._frame_metrics(hwnd)
        zoomed, full = bool(user32.IsZoomed(hwnd)), self.isFullScreen()
        wx, wy = x - rect.left, y - rect.top
        dpr = dpi / 96.0
        ox, oy = (0, 0) if full else (fx, fy if zoomed else 0)
        in_caption, over_button = False, False
        if self.titlebar.isVisible():
            pt = QPoint(int((wx - ox) / dpr), int((wy - oy) / dpr))
            if 0 <= pt.y() < self.titlebar.height():
                in_caption = True
                child = self.titlebar.childAt(pt)
                over_button = isinstance(child, (QAbstractButton,))
        return hit_code(wx, wy, rect.right - rect.left, rect.bottom - rect.top, fx, fy,
                        int(6 * dpr), zoomed, full, in_caption, over_button)

    def _toggle_maximize(self):
        if self.mini:
            return
        self.showNormal() if self.isMaximized() else self.showMaximized()

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() == QEvent.ActivationChange:
            if self.isActiveWindow():
                QTimer.singleShot(0, self._focus_refresh)
            else:
                self._deact_t = time.monotonic()
        if e.type() == QEvent.WindowStateChange:
            self.titlebar.set_maximized(self.isMaximized())
            if self._custom_frame and not self.isFullScreen():
                QTimer.singleShot(0, self._install_frame)         # Qt resets the style after full screen
            self._fit_soon()

    # --------------------------------------------------------- back navigation
    def go_back(self):
        """Title-bar navigation button. Video page: Home icon -> Home. Home: Back arrow ->
        the video (if Home was opened from it) or, on Playlists/Settings, -> Home."""
        if not self.stage.home.isVisible():
            if self.current:
                self.show_home(fade=True)
        elif self.stage.home.page == "pl_detail":
            self.stage.home.show_page("playlists")     # a playlist -> the Playlists page
        elif self.stage.home.page != "home":
            self.stage.home.show_page("home")          # Playlists / Settings -> Home
        elif self.current and self._from_video:
            def to_video():
                if self.stage.home.isVisible() and self.current:
                    self.stage.show_home(False)        # Home -> the video
                    self._update_back()
            ok_ = self.stage.move_to_video(to_video)
            self._log("move Home -> video page: " + ("started" if ok_ else "NOT started, page switched at once: " + getattr(self.stage, "move_why", "?")))
            if not ok_:
                to_video()
        self._update_back()

    def _sync_protected_thumbs(self):
        """Keep the cached thumbnails of every video in the loaded playlist (they are used by the
        stacked playlist thumbnail), even after a video was watched and left Recent media."""
        names = set()
        for p in self.queue:
            try:
                names.add(self.store.thumb_path(p, os.path.getsize(p)).name)
            except OSError:
                pass
        self.store.protected = frozenset(names)

    def _update_bar_thumb(self):
        th = self.stage.controls.thumb
        th.vpath = self.current
        th.update()
        if self.current:
            th.set_source(self.store.thumb_path(self.current, self._size(self.current)))
        else:
            th.set_source(None)

    def _open_video_page(self):
        """Mini-player click: back to the main playback window, same session, same position (with the fade)."""
        if self.stage.home.isVisible() and self.current:
            ok_ = self.stage.move_to_video(self._open_video_page_now)
            self._log("move Home -> video page (slot click): " + ("started" if ok_ else "NOT started, page switched at once: " + getattr(self.stage, "move_why", "?")))
            if not ok_:
                self._open_video_page_now()

    def _open_video_page_now(self):
        if self.stage.home.isVisible() and self.current:
            self._from_video = True
            self.stage.show_home(False)
            if self._zoom != "fit":
                self._zoom_done = False
            self._update_back()

    def _mini_poll(self):
        """Every 30 ms. The mini window has its own tick. Otherwise this watches the mouse for the title block, the X on
        the preview slot and their clicks: VLC's own window swallows mouse events, so the buttons are read directly."""
        if self.mini:
            self._mini_tick()
            return
        self._block_poll()

    def _cursor_on_x(self):
        """True while the mouse is on the X of the preview slot: a click there stops the video, it must not open the
        video page (VLC's window lets the click through to the slot, so the old click paths have to step aside)."""
        if not self.slotx.want:
            return False
        _b, _s, x = self._block_geometry()
        return bool(x and x.contains(QCursor.pos()))

    def _slot_thumb_clicked(self):
        if not self._cursor_on_x():
            self._open_video_page()

    @staticmethod
    def _left_down():
        """Is the left mouse button down right now? On Windows asked from the system, because VLC's own window takes
        the click and Qt never hears about it (the swapped-buttons setting is respected)."""
        if sys.platform == "win32":
            try:
                u = ctypes.windll.user32
                return bool(u.GetAsyncKeyState(0x02 if u.GetSystemMetrics(23) else 0x01) & 0x8000)
            except Exception:
                pass
        return bool(QApplication.mouseButtons() & Qt.LeftButton)

    def _app_active(self):
        """The window counts as active also while the picture window (our own child window) holds the keyboard focus: Qt then
        reports the main window as not active, and the name box / slot clicks (found by polling the mouse) were ignored."""
        if self.isActiveWindow():
            return True
        fw = QApplication.focusWindow()
        return fw is not None and fw is getattr(self.stage.video, "_win", None)

    def _block_geometry(self):
        """(block, slot, x_disc) as global rects; None for what is not there right now."""
        st, cb = self.stage, self.stage.controls
        if not (self.current and cb.isVisible() and self._app_active() and QApplication.activePopupWidget() is None):
            return None, None, None
        br = cb.block_rect()
        if br is None:
            return None, None, None
        block = QRect(cb.mapToGlobal(br.topLeft()), br.size())
        slot = x = None
        if st.home.isVisible() and st.mini_live and st.video.isVisible() and st._move is None:
            slot = QRect(st.video.mapToGlobal(QPoint(0, 0)), st.video.size())
            x = SlotX.disc_rect(slot)
        return block, slot, x

    def _block_poll(self):
        pos = QCursor.pos()
        down = self._left_down()
        block, slot, x = self._block_geometry()
        on_x = bool(x and self.slotx.armed() and x.contains(pos))
        hover = bool(block and block.contains(pos))
        if not down:
            self._blk_dbg = False
        elif not self._blk_dbg and block is None and self.current and self.stage.controls.isVisible():
            br_ = self.stage.controls.block_rect()                # a press on the name box while it is "not there": say why (once)
            if br_ is not None and QRect(self.stage.controls.mapToGlobal(br_.topLeft()), br_.size()).contains(pos):
                self._blk_dbg = True
                fw_ = QApplication.focusWindow()
                self._log(f"name box press ignored: active={self.isActiveWindow()} focusWindow={type(fw_).__name__} "
                          f"popup={QApplication.activePopupWidget() is not None} maximized={self.isMaximized()} full={self.isFullScreen()}")
        self.stage.controls.set_block_hover(hover)
        self.slotx.sync(slot if (slot and (slot.contains(pos) or (x and x.contains(pos)))) else None, pos, down)
        if down and not self._blk_down and not self._x_down:
            self._blk_down, self._x_down = hover and not on_x, on_x
            self._blk_home = self.stage.home.isVisible()
        elif not down and (self._blk_down or self._x_down):
            was_block, was_x = self._blk_down, self._x_down
            self._blk_down = self._x_down = False
            if was_x and on_x:
                self._stop_from_x()
            elif was_block and hover and not on_x:
                if self._blk_home:                       # the press began on Home: slot or title: back to the video page
                    if self.stage.home.isVisible():      # (the older click path may already have opened it: then do nothing,
                        self._open_video_page()          #  or the page would open and close again at once)
                else:
                    self.show_home(fade=True)            # video page: title: Home, the video keeps playing

    def _leave_fullscreen_if_stuck(self):
        """Full screen on a Home page with no video has no bar and no title bar: nothing to click to get out. Leave it (Esc and F
        also work)."""
        if self.isFullScreen() and not self.current:
            QTimer.singleShot(0, lambda: self.isFullScreen() and not self.current and self.toggle_fullscreen())

    def _stop_from_x(self):
        """The X on the preview slot: save the position, stop the video, close the preview and the bar."""
        path = self.current
        if not path:
            return
        self._save_progress()
        try:
            snap = self._snapshot()
            if snap:
                time.sleep(0.25)                          # let libVLC write the last frame (the card picture)
                self._finish_snapshot(*snap)
        except Exception:
            pass
        self.slotx.sync(None, QPoint(), False)
        self._stop_if_playing([path])
        self.stage.show_home(True, with_controls=False, mini=False)
        self._refresh_home(animate=False)
        self._update_back()
        self._leave_fullscreen_if_stuck()

    def _update_back(self):
        """One navigation button, never two: Home icon on the video page; Back arrow on Home only
        when there is a previous view to return to."""
        self.titlebar.set_side(self.stage.home.isVisible())
        self._sync_titlebar()
        if self.mini:
            return
        if not self.stage.home.isVisible():
            self.titlebar.set_nav("home", True)
        else:
            back = self.stage.home.page != "home" or bool(self.current and self._from_video)
            self.titlebar.set_nav("back", back)

    def _bind_video(self):
        sv = self.stage.video                       # PHASE 12a: no native window; libVLC writes pictures into our surface
        sv.sar_fn = lambda: video_track_sar(self.player)
        sv.attach(self.player)
        sv.reset()
        sv.set_aspect(self._aspect)
        sv.set_zoom(self._zoom)

    def _startup(self):
        self._bind_video()
        self._apply_audio()
        self.timer.start()
        self.idle.start()
        self.saver.start()
        self.mini_timer.start()
        files = [f for f in self._start_files if os.path.exists(f)]
        if files:
            self.open_paths(files)
        else:
            # open on the "continue watching" grid instead of auto-playing the last video
            self._refresh_home()
            self.stage.show_home(True, with_controls=False)

    # ------------------------------------------------------------- playback
    @staticmethod
    def _size(path):
        try:
            return os.path.getsize(path)
        except OSError:
            return -1

    def open_paths(self, paths, paused=False):
        if len(paths) == 1 and os.path.isdir(paths[0]):
            self.open_folder(paths[0], paused=paused)
            return
        paths = [p for p in paths if os.path.isfile(p)]
        if not paths:
            return
        self._t_click, self._tm_flags = time.monotonic(), set()
        self._starting_until = time.monotonic() + 20      # PHASE B: background work waits until the video plays
        self._tm_last_tick = None
        self._log(f"--- play request: {os.path.basename(paths[0])} (open_paths, {len(paths)} file(s))")
        if len(paths) == 1:
            first = paths[0]
            k = Store._key(first)
            idx = next((i for i, q in enumerate(self.queue) if Store._key(q) == k), -1)
            if idx >= 0:
                qi = idx                     # already in the playlist: keep the list (and my edits)
            else:
                self.queue, qi = self._playlist_for(first)
        else:
            self.queue, qi = self._clean_paths(paths), 0
        self._tm(f"queue ready ({len(self.queue)} items)")
        self._scan_durations(self.queue)
        self._tm("durations scan started")
        self.play_index(qi, resume=True, paused=paused)

    # ---------------------------------------------- TV episodes and folders
    def _scan_folder(self, folder):
        """[(path, episode_info_or_None)] for the video files directly inside `folder`
        (no subfolders). Cached until the folder changes, so it is not re-read every time."""
        try:
            mtime = os.stat(folder).st_mtime_ns
        except OSError:
            return []
        hit = self._folder_cache.get(folder)
        if hit and hit[0] == mtime:
            return hit[1]
        items = []
        try:
            with os.scandir(folder) as it:
                for f in it:
                    if f.is_file() and os.path.splitext(f.name)[1].lower() in VIDEO_EXT:
                        items.append((f.path, parse_episode(f.name)))
                        try:
                            st = f.stat()
                            self._folder_sizes[f.path] = st.st_size
                            self._folder_mtimes[f.path] = st.st_mtime
                        except OSError:
                            pass
        except OSError:
            return []
        items.sort(key=lambda t: natural_key(os.path.basename(t[0])))
        self._folder_cache[folder] = (mtime, items)
        return items

    SUB_DEPTH = 3                                     # folder levels below a saved folder that are read

    @staticmethod
    def _skip_dir(entry):
        """Hidden, system, recycle-bin and link/junction folders are not read (no loops, no junk)."""
        n = entry.name
        if n.startswith((".", "$")) or n.lower() == "system volume information":
            return True
        try:
            if entry.is_symlink() or getattr(entry, "is_junction", lambda: False)():
                return True
            return bool(getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0) & 0x6)
        except OSError:
            return True

    def _subdirs(self, folder):
        """(mtime, [subfolder paths]) of `folder`, cached until the folder changes."""
        try:
            mtime = os.stat(folder).st_mtime_ns
        except OSError:
            return None, []
        hit = self._dir_cache.get(folder)
        if hit and hit[0] == mtime:
            return mtime, hit[1]
        subs = []
        try:
            with os.scandir(folder) as it:
                for f in it:
                    if f.is_dir(follow_symlinks=False) and not self._skip_dir(f):
                        subs.append(f.path)
        except OSError:
            return mtime, []
        subs.sort(key=lambda p: natural_key(os.path.basename(p)))
        self._dir_cache[folder] = (mtime, subs)
        return mtime, subs

    def _walk_videos(self, folder, level, sig, base):
        """Videos of `folder` and of the folders below it (down to SUB_DEPTH levels from the saved folder).
        `sig` collects (folder, mtime) so a change anywhere in the tree is noticed."""
        mtime, subs = self._subdirs(folder)
        sig.append((folder, mtime))
        out = list(self._scan_folder(folder))
        if level < self.SUB_DEPTH:
            for d in subs:
                out += self._walk_videos(d, level + 1, sig, base)
        return out

    def _scan_tree(self, folder):
        """{"loose": [(path, info)], "subs": [(folder name, folder path, [(path, info)])]} for a saved folder.
        loose = the videos directly in it. subs = one entry per direct subfolder that has videos anywhere below
        it (3 levels), its videos in natural order of their relative path. No cap on the number of videos."""
        mtime, subs = self._subdirs(folder)
        if mtime is None:
            return {"loose": [], "subs": []}
        sig = [(folder, mtime)]
        loose = list(self._scan_folder(folder))
        found = []
        for d in subs:
            files = self._walk_videos(d, 1, sig, d)
            if files:
                files.sort(key=lambda t: natural_key(os.path.relpath(t[0], d)))
                found.append((os.path.basename(d), d, files))
        sig = tuple(sig)
        hit = self._tree_cache.get(folder)
        if hit and hit[0] == sig:
            return hit[1]
        tree = {"loose": loose, "subs": found}
        self._tree_cache[folder] = (sig, tree)
        return tree

    def _playlist_for(self, path):
        """Playlist when one video is opened: the other episodes of the same show in its folder,
        in season/episode order. Anything that is not clearly an episode opens on its own."""
        if self.folder_queue == "never":
            return [path], 0
        if self.folder_queue == "all":
            opened = Store._key(path)
            ordered = [p for p, _ in self._scan_folder(os.path.dirname(os.path.abspath(path)))]
            idx = next((n for n, p in enumerate(ordered) if Store._key(p) == opened), -1)
            if idx < 0:
                ordered.append(path)
                idx = len(ordered) - 1
            return ordered, idx
        info = parse_episode(path)
        if not info:
            return [path], 0
        opened = Store._key(path)
        best = {}
        for p, i in self._scan_folder(os.path.dirname(os.path.abspath(path))):
            if i and i["gkey"] == info["gkey"]:
                se = (i["season"], i["episode"])
                if se not in best or Store._key(p) == opened:   # two files, one episode: keep one
                    best[se] = p
        ordered = [best[se] for se in sorted(best)]
        idx = next((n for n, p in enumerate(ordered) if Store._key(p) == opened), -1)
        if idx < 0:
            ordered.append(path)
            idx = len(ordered) - 1
        return ordered, idx

    @staticmethod
    def _folder_order(items):
        """Play order of a folder: shows one after another (by season and episode), then everything else by name."""
        groups, others = {}, []
        for p, i in items:
            if i:
                groups.setdefault(i["gkey"], []).append((i["season"], i["episode"], p))
            else:
                others.append(p)
        ordered = []
        for key in sorted(groups):                       # one show after another
            seen = set()
            for se, ep, p in sorted(groups[key], key=lambda t: (t[0], t[1], natural_key(os.path.basename(t[2])))):
                if (se, ep) not in seen:
                    seen.add((se, ep))
                    ordered.append(p)
        ordered += others                                # already in natural name order
        return ordered

    def open_folder(self, folder, paused=False):
        """Put a folder on Home as a section. Nothing starts playing (`paused` is kept for old callers)."""
        tree = self._scan_tree(folder)
        if not tree["loose"] and not tree["subs"]:
            self.flash("No videos found in that folder")
            return
        self.store.add_folder(folder)
        if self.current is None:
            self._refresh_home()
            self.stage.home.show_page("home")
            self.stage.show_home(True, with_controls=False)
        elif self.stage.home.isVisible():
            self.stage.home.show_page("home")
            self._refresh_home()
        else:
            self.show_home()
        self._home_pg().scroll.verticalScrollBar().setValue(0)

    def _folder_sections(self):
        """Folder sections for Home, newest folder first. A section holds items: a stack or a single card.
        Each direct subfolder with videos (3 levels deep) is ONE stack named after the folder (a single card when
        it holds one video). Then come the stacks of the loose videos (a show with 2 or more different episodes,
        matched by the episode tags), then the other loose videos in natural name order. Fills self._folder_of
        (video -> saved folder, keeps them out of Recent media) and self._group_of (video -> the group it plays in)."""
        out, of, gof, dso = [], {}, {}, {}
        folders = self.store.data["folders"]
        vkeys = {}                                        # PHASE B: every video each saved folder shows
        for f in folders:
            t = self._scan_tree(f["path"])
            vkeys[f["path"]] = ({Store._key(p) for p, _i in t["loose"]}
                                | {Store._key(p) for _n, _d, fl in t["subs"] for p, _i in fl})

        def inside_other(folder):
            """True when another saved folder already shows all its videos (it sits inside that folder): one entry
            only, the outer one. The inner folder stays saved and comes back if the outer one is removed."""
            kf = Store._key(folder)
            return bool(vkeys.get(folder)) and any(
                g["path"] != folder and kf.startswith(Store._key(g["path"]) + os.sep) and vkeys[folder] <= vkeys.get(g["path"], set())
                for g in folders)

        for f in folders:
            folder = f["path"]
            if inside_other(folder):
                continue
            tree = self._scan_tree(folder)
            hidden = self.store.folder_hidden(folder)     # cards taken off Home with Remove from Home
            wmap = self.store.watched_map()

            def seen(p):                                  # watched to the end: not shown (same path and size)
                ws = wmap.get(Store._key(p))
                if ws is None:
                    return False
                sz = self._folder_sizes.get(p)
                return ws == 0 or ws == (sz if sz is not None else self._size(p))

            scan = [(p, i) for p, i in tree["loose"] if Store._key(p) not in hidden and not seen(p)]
            subs = []
            for name, d, files in tree["subs"]:
                if "dir:" + Store._key(d) in hidden:
                    continue                              # a removed subfolder stays gone, also for new videos in it
                files = [(p, i) for p, i in files if Store._key(p) not in hidden and not seen(p)]
                if files:
                    subs.append((name, d, files))
            if not scan and not subs:
                continue

            def entry(path, folder=folder, hide=None):
                size = self._folder_sizes.get(path)
                if size is None:
                    size = self._size(path)
                e = self.store.find(path, size)
                e = dict(e) if e else {"path": path, "size": size, "position": 0, "duration": 0, "finished": False}
                e["folder"] = folder
                e["hidekeys"] = list(hide) if hide else [Store._key(path)]    # what Remove from Home hides
                return e

            items, used = [], set()
            for name, d, files in subs:                   # a subfolder: one stack, the queue is that group
                eps = self._folder_order(files)
                ents = [entry(p) for p in eps]
                if len(eps) >= 2:
                    started = {Store._key(e["path"]): e for e in ents if e.get("position", 0) > 0}
                    lp = self.store.lastplayed("dir:" + Store._key(d))
                    if lp:                                # PHASE B: the video played last; if it is finished (hidden),
                        front = next((e for e in ents if Store._key(e["path"]) == Store._key(lp)), ents[0])   # the first one shown
                    else:
                        front = next((started[Store._key(r["path"])] for r in self.store.recent
                                      if Store._key(r["path"]) in started), ents[0])
                    st = dict(front)
                    st["group"] = list(eps)
                    st["dirstack"] = True
                    st["title"] = folder_title(name)
                    st["hidekeys"] = ["dir:" + Store._key(d)]
                    items.append({"kind": "stack", "key": "dir:" + Store._key(d), "e": st, "eps": ents})
                else:
                    ents[0]["hidekeys"] = ["dir:" + Store._key(d)]
                    items.append({"kind": "single", "e": ents[0]})
                for p in eps:
                    gof[Store._key(p)] = list(eps)
                    if len(eps) >= 2:
                        dso[Store._key(p)] = "dir:" + Store._key(d)
            groups = {}
            for p, i in scan:
                if i:
                    groups.setdefault(i["gkey"], []).append((i["season"], i["episode"], p))
            for key in sorted(groups):
                eps, seen = [], set()
                for se, ep, p in sorted(groups[key], key=lambda t: (t[0], t[1], natural_key(os.path.basename(t[2])))):
                    if (se, ep) not in seen:
                        seen.add((se, ep))
                        eps.append(p)
                if len(eps) < 2:
                    continue                              # one episode of a show: a single card
                ents = [entry(p) for p in eps]
                started = {Store._key(e["path"]): e for e in ents if e.get("position", 0) > 0}
                front = next((started[Store._key(r["path"])] for r in self.store.recent
                              if Store._key(r["path"]) in started), ents[0])
                st = dict(front)
                st["group"] = list(eps)
                st["title"] = self._playlist_title(eps, front)
                st["hidekeys"] = [Store._key(p) for p in eps]
                items.append({"kind": "stack", "key": key, "e": st, "eps": ents, "loose": True})
                used.update(eps)
            for p, _i in scan:
                if p not in used:
                    items.append({"kind": "single", "e": entry(p), "loose": True})
            items = self._sort_items(items)
            loose_q = []                                  # a loose video queues the loose videos in the order shown
            for it in items:
                if it.get("loose"):
                    loose_q += [ep["path"] for ep in it["eps"]] if it["kind"] == "stack" else [it["e"]["path"]]
            for p in loose_q:
                gof[Store._key(p)] = loose_q
            for it in items:
                for e in ([it["e"]] + it.get("eps", [])):
                    of[Store._key(e["path"])] = folder
            out.append({"folder": folder, "name": folder_title(os.path.basename(os.path.normpath(folder)) or folder),
                        "items": items})
        self._folder_of = of
        self._group_of = gof
        self._dirstack_of = dso
        names = set()                                     # PHASE B: the pictures of every shown video stay in the cache
        for sec in out:
            for it in sec["items"]:
                for e in ([it["e"]] + it.get("eps", [])):
                    names.add(self.store.thumb_path(e["path"], e["size"]).name)
        self.store.folder_thumbs = frozenset(names)
        self.store.folders_ok = all(os.path.isdir(f["path"]) for f in folders)
        return out

    def _mtime(self, path):
        m = self._folder_mtimes.get(path)
        if m is None:
            try:
                m = os.path.getmtime(path)
            except OSError:
                m = 0.0
            self._folder_mtimes[path] = m
        return m

    def _sort_items(self, items):
        """Stacks (subfolder stacks and show stacks) first, then single videos; each group in the chosen order.
        Name = natural order of the shown name; Date modified = newest video of a stack, or the file."""
        srt = self.store.data.get("home_sort") or {}
        rev = srt.get("dir") == "desc"

        def title(it):
            return (it["e"].get("title") or display_name(it["e"]["path"])).lower()

        def key(it):
            if srt.get("key") == "date":
                ps = [ep["path"] for ep in it["eps"]] if it["kind"] == "stack" else [it["e"]["path"]]
                return (max(self._mtime(p) for p in ps), natural_key(title(it)))
            return (natural_key(title(it)),)

        stacks = sorted([i for i in items if i["kind"] == "stack"], key=key, reverse=rev)
        singles = sorted([i for i in items if i["kind"] != "stack"], key=key, reverse=rev)
        return stacks + singles

    def _home_sort_pick(self, key):
        self.store.data["home_sort"] = next_home_sort(self.store.data["home_sort"], key)
        self.store.save()
        self._refresh_home(animate=False)

    def _home_play(self, path):
        """A Home card was clicked. A video of a folder section plays with the whole folder as the queue."""
        k = Store._key(path)
        if k not in self._group_of:
            self._folder_sections()
        ordered = list(self._group_of.get(k) or [])
        idx = next((n for n, p in enumerate(ordered) if Store._key(p) == k), -1)
        if idx < 0 or not os.path.isfile(path):
            self.open_paths([path])
            return
        self._t_click, self._tm_flags = time.monotonic(), set()
        self._starting_until = time.monotonic() + 20      # PHASE B: background work waits until the video plays
        self._tm_last_tick = None
        self._log(f"--- play request: {os.path.basename(path)} (folder card, {len(ordered)} in the group)")
        qi = next((n for n, q in enumerate(self.queue) if Store._key(q) == k), -1)
        if qi >= 0 and {Store._key(q) for q in self.queue} == {Store._key(p) for p in ordered}:
            idx = qi                                      # same folder queue is already loaded: keep my edits
        else:
            self.queue = ordered
        self._tm(f"queue ready ({len(self.queue)} items)")
        self._scan_durations(self.queue)
        self.play_index(idx, resume=True)

    def open_folder_dialog(self):
        folder = QFileDialog.getExistingDirectory(self, "Open folder", self.store.data.get("last_dir", ""))
        if folder:
            self.store.data["last_dir"] = folder
            self.open_folder(folder)

    def _on_home(self):
        return self.stage.home.isVisible()

    def play_index(self, i, resume=True, paused=False, stay=False, start_at=None):
        if not (0 <= i < len(self.queue)):
            return False
        if not os.path.isfile(self.queue[i]):
            self.flash(f"File not found: {display_name(self.queue[i])}")
            self._exist_cache.pop(self.queue[i], None)
            self._refresh_queue()
            return False
        if getattr(self, "_t_click", None) is None or time.monotonic() - self._t_click > 30:
            self._t_click, self._tm_flags, self._tm_last_tick = time.monotonic(), set(), None
            self._starting_until = time.monotonic() + 20
            self._log(f"--- play request: {os.path.basename(self.queue[i])} (play_index direct)")
        self._tm("play_index entered")
        self._save_progress()
        self._tm("progress saved")
        path = self.queue[i]
        size = self._size(path)
        entry = self.store.find(path, size) if (resume and self.keep_history) else None
        start, audio, spu = 0, None, None
        if entry:
            audio, spu = entry.get("audio"), entry.get("spu")
            if not entry.get("finished") and entry.get("position", 0) > 5000:
                start = entry["position"]
        if start_at is not None:                 # seeking a video that already ended: start there
            start = start_at if start_at > 5000 else 0
        media = self._new_media(path)
        if media is None:                        # VLC could not make a media object from this file
            self.flash("Can't open this file. See player.log")
            self._refresh_queue()
            return False
        self.qi, self.current, self._ended = i, path, False
        self._completed.discard(Store._key(path))        # playing it again starts a new watch
        self.store.unmark_watched(path)                  # and it shows in its Home folder again while unfinished
        self._seen_playing = False       # an "Ended" state only counts once this video has played
        self._thumb_for = None
        self._zoom, self._zoom_done = "fit", True
        self._aspect = None
        self._adelay = self._sdelay = 0

        if start:
            media.add_option(f":start-time={start / 1000:.3f}")
        self._log(f"play {os.path.basename(path)} start={start} state_before={self.player.get_state()}")
        self._tm(f"media object ready, start={start} ms")
        dk = self._dirstack_of.get(Store._key(path))      # PHASE B: the subfolder card follows the video played last
        if dk:
            self.store.set_lastplayed(dk, path)
        self.player.stop()                       # drop a paused or playing video completely
        self._tm("player.stop() returned")
        self.player.set_media(media)
        self._tm("set_media() returned")
        self._bind_video()                       # fresh video output (the home screen hid the old one)
        self._tm("video bound")
        self._pending = {"pause": paused, "wait": 2, "audio": audio, "spu": spu, "tracks": False,
                         "t0": time.monotonic(), "retried": False, "start": start}
        if paused:
            self.player.audio_set_mute(True)
        self.player.play()
        self._tm("player.play() returned")
        self.player.set_rate(self.rate)

        if self.keep_history:
            self.store.touch(path, size, position=start, finished=False)
            self.store.save()
        name = display_name(path)
        self.stage.controls.title.setText(name)
        self.setWindowTitle(f"{name} - {APP_TITLE}")
        self._vt_last = -1
        if stay and self.stage.home.isVisible():
            self.stage.show_home(True, with_controls=True, mini=True)   # stay on Home, mini-player plays it
        else:
            from_home = self.stage.home.isVisible() and self.isVisible() and not self.mini
            if from_home:
                self.stage.fade_cover()                # Home -> the video page: dark at once, fades out after the switch
            if not self.mini:
                # no control bar until the first picture; from Home the ring is under the curtain from the start
                self.stage.begin_loading(start, show_ms=0 if from_home else None)
            self.stage.show_home(False)
            if self._zoom != "fit":
                self._zoom_done = False
        self._tm("home hidden / UI switched")
        self._refresh_buttons()
        self._sync_protected_thumbs()
        self._refresh_queue()
        self._update_bar_thumb()
        self._update_back()
        self._tm("play_index finished (UI thread free again)")
        return True

    def _apply_audio(self):
        self.player.audio_set_volume(self.volume)
        self.player.audio_set_mute(self.muted)

    def _new_media(self, path):
        """VLC media for a file. media_new_path() can return nothing for some paths, so try the
        other ways VLC accepts a file, and write what happened to player.log."""
        attempts = []
        full = os.path.abspath(path)
        try:
            attempts.append(("path", lambda: self.instance.media_new_path(full)))
            attempts.append(("uri", lambda: self.instance.media_new_location(Path(full).as_uri())))
            if sys.platform == "win32" and not full.startswith("\\\\?\\"):
                attempts.append(("long path", lambda: self.instance.media_new_path("\\\\?\\" + full)))
        except Exception:
            pass
        for name, make in attempts:
            try:
                m = make()
            except Exception as exc:
                self._log(f"media via {name} failed: {exc}")
                continue
            if m is not None:
                if name != "path":
                    self._log(f"media_new_path failed, worked via {name}: {full!r}")
                return m
        self._log(f"no media could be created for {full!r} (length {len(full)}, exists={os.path.exists(full)})")
        return None

    def _resume_fallback(self):
        """The video did not start with its resume position. Start it from the beginning and
        jump to the saved position once it is playing."""
        p = self._pending
        if not p or not self.current:
            return
        self._log(f"resume fallback: {os.path.basename(self.current)} did not start at {p['start']} ms")
        p.update(fallback=True, seek=p["start"], t0=time.monotonic(), retried=False)
        media = self._new_media(self.current)
        if media is None:
            return
        self.player.stop()
        self.player.set_media(media)
        self._bind_video()
        self.player.play()
        self.player.set_rate(self.rate)

    def _apply_pending(self):
        p = self._pending
        if not p.get("eq"):
            p["eq"] = True
            self._apply_eq()
        if p.get("seek") and not p.get("seek_done"):
            if self.player.is_seekable() or time.monotonic() - p["t0"] > 3.0:
                self._log(f"seeking to {p['seek']} ms after start")
                self.player.set_time(int(p["seek"]))
                p["seek_done"] = True
            else:
                return                                  # wait until VLC says it can seek
        if not p["tracks"] and (p["audio"] is not None or p["spu"] is not None
                                or any(self._pref_tracks.values())):
            p["tracks"] = True
            QTimer.singleShot(700, lambda a=p["audio"], s=p["spu"]: self._restore_tracks(a, s))
        if p["pause"]:
            p["wait"] -= 1
            if p["wait"] <= 0:
                self.player.set_pause(1)
                self._pending = None
                self._apply_audio()
        else:
            self._pending = None
            self._apply_audio()

    def _lang_key(self, name):
        """Language part of a VLC track name, lower case, or None when the name says no language
        ("Track 3"). "Disable" gives "off"."""
        n = self._tname(name).strip()
        if n.lower() in ("disable", "off", "none"):
            return "off"
        m = re.search(r"\[([^\]]+)\]", n)
        n = (m.group(1) if m else re.sub(r"^track\s*\d+\s*-?\s*", "", n, flags=re.I)).strip().lower()
        return None if not n or re.fullmatch(r"track\s*\d*", n) else n

    def _match_track(self, kind, tracks):
        """Id of the track in this file that has the language the user picked, or None."""
        want = self._pref_tracks.get(kind)
        if not want:
            return None
        keys = [(tid, self._lang_key(nm)) for tid, nm in tracks]
        for tid, k in keys:
            if k == want:
                return tid
        return None

    def _pick_track(self, kind, tid, name):
        """Menu choice: set the track and remember its language for the next item."""
        if kind == "audio":
            self.player.audio_set_track(tid)
        else:
            self.player.video_set_spu(tid)
        key = self._lang_key(name)
        if key and not (kind == "audio" and key == "off"):   # no language: not remembered; muted audio is not carried
            self._pref_tracks[kind] = key

    def _restore_tracks(self, audio, spu, attempt=0):
        try:
            if attempt == 0:
                a_list = self.player.audio_get_track_description() or []
                s_list = self.player.video_get_spu_description() or []
                self._log("tracks audio=%s spu=%s rate=%s" % (
                    [self._tname(n) for _, n in a_list], [self._tname(n) for _, n in s_list],
                    self.player.get_rate()))
            retry = False
            # this file's own saved choice wins; otherwise use the language picked on the last item
            if audio is not None and audio >= 0:
                self.player.audio_set_track(audio)
            elif audio is None and self._pref_tracks["audio"]:
                tid = self._match_track("audio", self.player.audio_get_track_description() or [])
                if tid is None:
                    retry = True
                elif tid != self.player.audio_get_track():
                    self.player.audio_set_track(tid)
                    self._log(f"audio language kept: id {tid}")
            if spu is not None:
                self.player.video_set_spu(spu)
            elif self._pref_tracks["spu"]:
                tid = self._match_track("spu", self.player.video_get_spu_description() or [])
                if tid is None:
                    retry = True
                elif tid != self.player.video_get_spu():
                    self.player.video_set_spu(tid)
                    self._log(f"subtitle language kept: id {tid}")
            if retry and attempt < 2:             # the track list can still be filling up
                QTimer.singleShot(1000, lambda: self._restore_tracks(audio, spu, attempt + 1))
        except Exception as exc:
            self._log(f"restore tracks failed: {exc}")

    def _state_playing(self, st=None):
        st = self.player.get_state() if st is None else st
        return st in (vlc.State.Playing, vlc.State.Opening, vlc.State.Buffering)

    def toggle_play(self):
        if not self.current:
            self.open_dialog()
            return
        st = self.player.get_state()
        if st in (vlc.State.Ended, vlc.State.Stopped, vlc.State.Error, vlc.State.NothingSpecial):
            self.play_index(self._replay_index(), resume=not self._ended, stay=self._on_home())
        elif st == vlc.State.Paused:
            self.player.play()
        else:
            self.player.set_pause(1)
            self._save_progress()
            self._snapshot_async()

    def seek_to(self, ms):
        self._wheel_t = 0.0                          # the wheel handler sets this again after seeking
        if not self.current:
            return
        st = self.player.get_state()
        if st in (vlc.State.Ended, vlc.State.Stopped, vlc.State.Error):
            self.play_index(self._replay_index(), resume=True, stay=self._on_home(), start_at=ms)
            return
        self.player.set_time(int(ms))
        self._vt_last = -1

    def seek_rel(self, delta):
        length, t = self.player.get_length(), self.player.get_time()
        if length <= 0 or t < 0:
            return
        self.seek_to(max(0, min(length - 500, t + delta)))

    def _show_remaining(self, pos):
        """Right time label: time LEFT (changed from total length at the user's request, October 2026)."""
        length = self.player.get_length()
        if length > 0 and pos >= 0:
            txt = fmt(max(0, length - pos))
            if txt != self._lbl_tot:
                self._lbl_tot = txt
                self.stage.controls.t_tot.setText(txt)

    def _seek_moved(self, v):
        self.stage.controls.t_cur.setText(fmt(v))
        self._show_remaining(v)
        now = time.monotonic()
        # live scrubbing only while paused (seeking a playing video every few ms makes audio stutter)
        if self.player.get_state() == vlc.State.Paused and now - self._last_live > 0.1:
            self._last_live = now
            self.player.set_time(int(v))

    def _seek_released(self, v):
        self.seek_to(v)

    WHEEL_SEEK_MS = 5000

    def _seek_wheel(self, steps):
        """Mouse wheel over the timeline: scroll up = forward, down = back, 5 s per notch."""
        c = self.stage.controls
        length = self.player.get_length()
        if not self.current or length <= 0 or c.seek.dragging():
            return                                   # no video, unknown length, or mid-drag
        now = time.monotonic()
        if now - self._wheel_t < 0.5:
            base = self._wheel_target                # still moving: build on the last target
        else:
            base = self.player.get_time()
            if base < 0:
                return
        target = max(0, min(length - 500, base + steps * self.WHEEL_SEEK_MS))
        self.seek_to(target)
        self._wheel_target, self._wheel_t = target, now
        c.seek.glide_to(target)                      # smooth move (also when paused)
        c.t_cur.setText(fmt(target))

    COMPLETE_FRACTION = 0.95        # a video counts as watched at about 95%

    def _is_completed(self, path=None):
        path = path or self.current
        return bool(path) and Store._key(path) in self._completed

    def _complete_current(self):
        """Remove the playing video from Continue Watching. Only the history entry goes;
        the video file is never touched. Safe to call more than once."""
        if not self.current or self._is_completed():
            return
        self._completed.add(Store._key(self.current))
        self.store.remove(self.current)
        if self.keep_history and self.store.in_saved_folder(self.current):     # hidden in its Home folder section
            self.store.mark_watched(self.current, self._size(self.current))

    def _save_progress(self):
        if not self.current or self._pending or self._is_completed():
            return
        if self.player.get_state() not in (vlc.State.Playing, vlc.State.Paused):
            return
        length, t = self.player.get_length(), self.player.get_time()
        if length <= 0 or t < 0:
            return
        if t >= length * self.COMPLETE_FRACTION:
            self._complete_current()
            return
        if not self.keep_history:
            return
        self.store.touch(self.current, self._size(self.current),
                         position=t, duration=length, finished=False,
                         audio=self.player.audio_get_track(), spu=self.player.video_get_spu())
        self.store.save()

    def _periodic(self):
        if self._closing:
            return
        self._save_progress()
        self._snap_n += 1
        if self._snap_n % 6 == 0 and self.player.get_state() == vlc.State.Playing:
            self._snapshot_async()

    # ---------------------------------------------------------- PHASE B: name tooltip
    def _init_tips(self):
        self.nametip = NameTip(self)
        self._tip_key, self._tip_args = None, None
        self._tip_timer = QTimer(self, singleShot=True, interval=400)
        self._tip_timer.timeout.connect(self._tip_show)
        self._tip_watch = QTimer(self, interval=150)
        self._tip_watch.timeout.connect(self._tip_check)

    def tip_request(self, key, text, anchor, above):
        """Ask for the name tooltip of `key` (a card or the bar title); it opens after a short delay."""
        if self._tip_key is key and (self.nametip.isVisible() or self._tip_timer.isActive()):
            return
        self.nametip.hide()
        self._tip_key, self._tip_args = key, (text, anchor, above)
        self._tip_timer.start()

    def tip_cancel(self, key=None):
        if key is None or key is self._tip_key:
            self._tip_timer.stop()
            self.nametip.hide()
            self._tip_watch.stop()
            self._tip_key = None

    def _tip_show(self):
        if self._tip_args and self.isActiveWindow() and not self.mini:
            text, anchor, above = self._tip_args
            if anchor.contains(QCursor.pos()):
                self.nametip.popup(text, anchor, above)
                self._tip_watch.start()

    def _tip_check(self):
        """Backup for a missed leave event: the tooltip goes when the mouse is no longer on the name."""
        if not self._tip_args or not self._tip_args[1].contains(QCursor.pos()) or not self.isActiveWindow():
            self.tip_cancel()

    # ---------------------------------------------------------- PHASE B: automatic pictures
    def _init_thumbs(self):
        self.thumbs = ThumbWorker(self.store, self._thumbs_busy, self._log)
        self.thumbs.done.connect(self._thumb_done)
        self.thumbs.noFrame.connect(lambda path, size: self.store.add_nopic(path, size))
        self.thumbs._failed |= self.store.nopic_set()
        self.thumbs.samplesReady.connect(self._samples_ready)
        self._thumbs_timer = QTimer(self, singleShot=True, interval=300)
        self._thumbs_timer.timeout.connect(self._thumbs_kick)
        self.stage.home.home_page.scroll.verticalScrollBar().valueChanged.connect(lambda *_: self._thumbs_kick_soon())
        QTimer.singleShot(900, self._thumbs_kick)

    def _thumbs_busy(self):
        """True while a video is opening or playing: the picture worker waits (it runs on another thread)."""
        if time.monotonic() < getattr(self, "_starting_until", 0.0):
            return True
        try:
            return self.player.get_state() in (vlc.State.Opening, vlc.State.Buffering, vlc.State.Playing)
        except Exception:
            return False

    def _thumbs_kick_soon(self):
        if not self._closing:
            self._thumbs_timer.start()

    def _thumbs_kick(self):
        """Look at the cards on Home and queue the pictures that are missing (or still have black bars)."""
        if self._closing or self.thumbs.off or (self.store.dir / "no_thumbs.txt").exists() \
                or os.environ.get("SIMPLEPLAYER_NO_THUMBS"):        # no_thumbs.txt / the variable: tests and safety switch
            return
        hp = self.stage.home.home_page
        cards = hp.all_cards()
        vis = hp.card_rects_on_screen() if self.stage.home.isVisible() else []
        seen, jobs_vis, jobs_rest = set(), [], []

        def want(c):
            path, size = c.e["path"], c.e["size"]
            k = (Store._key(path), size)
            if k in seen:
                return None
            f = self.store.thumb_path(path, size)
            kind = "auto" if not f.exists() else (None if f.with_suffix(".crop").exists() else "fix")
            if kind:
                seen.add(k)
                return (kind, path, size)
            return None

        for c in vis:
            j = want(c)
            if j:
                jobs_vis.append(j)
        for c in cards:
            j = want(c)
            if j:
                jobs_rest.append(j)
        n_auto = sum(1 for j in jobs_vis + jobs_rest if j[0] == "auto")
        if n_auto > 100:                                  # approved: only the cards on screen
            jobs = jobs_vis + [j for j in jobs_rest if j[0] == "fix"]
        else:
            jobs = jobs_vis + jobs_rest
        if not jobs:
            return
        self.thumbs.submit(jobs)

    def _slot_crop(self, tries=0):
        """The small live video in the Home slot is cut like the card pictures (black bars cut, then always 16:9 so the slot is
        filled, no bars on any side) with VLC's crop setting. Only while the video sits in the slot; the video page is never cropped."""
        want = None
        try:
            if (self.stage.mini_live and self.stage.home.isVisible() and self.current and not self._pending
                    and not (self.store.dir / "no_slot_crop.txt").exists()):
                note = read_crop_note(self.store.thumb_path(self.current, self._size(self.current)))
                l, t, r, b = note[1] if (note and note[0] == "fill") else (0.0, 0.0, 1.0, 1.0)
                want = (l, t, r, b)                   # PHASE 12a: the slot widget draws this part of the frame, filled (cover)
            if want:
                self.stage.video.set_crop(want)
                self._slot_cropped = True
                self._log(f"slot crop {tuple(round(x, 3) for x in want)}")
            elif self._slot_cropped:
                self.stage.video.set_crop(None)
                self._slot_cropped = False
                self._log("slot crop cleared")
        except Exception as ex:
            self._log(f"slot crop failed: {type(ex).__name__}: {ex}")

    def _thumb_done(self, path):
        self.stage.home.home_page.reload_pictures(Store._key(path))
        if self.current and Store._key(path) == Store._key(self.current):
            self._update_bar_thumb()

    # ---------------------------------------------------------- thumbnails
    def _snapshot(self):
        """Ask libVLC to save the current frame as this video's thumbnail. Returns (tmp, final)."""
        if not self.current or self._pending or self._is_completed():
            return None
        if self.player.get_state() not in (vlc.State.Playing, vlc.State.Paused):
            return None
        if self.player.get_time() < 1500:
            return None
        if self.store.is_picked(self.current, self._size(self.current)):
            return None                           # PHASE B: a picture the user picked is never replaced by a stop frame
        final = self.store.thumb_path(self.current, self._size(self.current))
        tmp = final.with_name(final.stem + ".new.jpg")
        try:
            if tmp.exists():
                tmp.unlink()
            if self.player.video_take_snapshot(0, str(tmp), 480, 0) != 0:
                return None
        except Exception:
            return None
        return tmp, final

    @staticmethod
    def _save_card_picture(img, tmp, final):
        return save_card_picture(img, tmp, final)

    def _snapshot_async(self):
        r = self._snapshot()
        if r:
            QTimer.singleShot(450, lambda: self._finish_snapshot(*r))

    def _finish_snapshot(self, tmp, final):
        try:
            if not tmp.exists():
                return
            img = QImage(str(tmp))
            if img.isNull():
                tmp.unlink()
                return
            small = img.scaled(8, 8, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
            lum = sum(QColor(small.pixel(x, y)).lightness() for x in range(8) for y in range(8)) / 64
            if lum < 14 and final.exists():      # near-black frame: keep the old thumbnail
                tmp.unlink()
                return
            self._save_card_picture(img, tmp, final)      # PHASE B: bars cut, 16:9, crop note saved next to it
        except OSError:
            return
        self._update_bar_thumb()
        if self.stage.home.isVisible():
            self._refresh_home(animate=False)

    def _playlist_title(self, queue, entry):
        infos = [parse_episode(p) for p in queue]
        if all(infos) and len({i["gkey"] for i in infos}) == 1 and infos[0]["show"]:
            i0 = infos[0]                                  # one show, one season: the show name (no year)
            return _SHOW_NAMES.get(i0["key"]) or _SHOW_NAMES.get(i0["rawkey"]) or clean_title(strip_show_year(i0["show"]))
        folders = {os.path.dirname(p) for p in queue}
        if len(folders) == 1:
            folder = os.path.basename(next(iter(folders)))
            return (clean_title(folder) if folder else "") or display_name(entry["path"])
        return display_name(entry["path"])

    def _home_entries(self, secs=None):
        """Recent videos for the Home grid. A video that is shown in a folder section is not listed here too
        (finished videos leave the history anyway). Stacks live in the folder sections only."""
        if secs is None:
            self._folder_sections()
        if not self.show_recent:
            return []
        return [e for e in self.store.recent if Store._key(e["path"]) not in self._folder_of]

    def _refresh_home(self, animate=True):
        self.store.clean()
        self.stage.home.home_page.recent_off = not self.show_recent
        self.stage.home.home_page.set_sort(self.store.data["home_sort"])
        secs = self._folder_sections()
        self._home_sig = self._sections_sig(secs)
        self.stage.home.refresh(self._home_entries(secs), animate, secs)
        if hasattr(self, "thumbs"):
            self._thumbs_kick_soon()

    @staticmethod
    def _sections_sig(secs):
        return tuple((sec["folder"], tuple((it["kind"], it["key"] if it["kind"] == "stack" else "",
                                            it["e"]["path"], it["e"].get("title") or "",
                                            tuple(ep["path"] for ep in it.get("eps", ())))
                                           for it in sec["items"])) for sec in secs)

    def _focus_refresh(self):
        """The window came back to the front: rescan the saved folders (cheap, cached by folder time) and rebuild
        Home only if something really changed. Playback and the queue are not touched."""
        now = time.monotonic()
        if not self.store.data["folders"] or now - self._focus_chk < 1.5:
            return
        self._focus_chk = now
        if self._sections_sig(self._folder_sections()) != self._home_sig:
            self._refresh_home(animate=False)

    def _remove_recent(self, path):
        self.store.remove(path)
        self._stop_if_playing([path])
        self._refresh_home(animate=False)

    def _stop_if_playing(self, paths):
        """The playing video was taken off Home: stop everything and show the plain Home (no bar, no preview slot),
        like a fresh start. Called after the history entry is gone, and nothing is saved for the video any more."""
        keys = {Store._key(p) for p in paths}
        if not self.current or Store._key(self.current) not in keys:
            return False
        if self.mini:
            self.toggle_mini()
        try:
            self.player.stop()
        except Exception:
            pass
        self.queue, self.qi, self.current = [], -1, None
        self._pending, self._ended, self._from_video = None, False, False
        self.setWindowTitle(APP_TITLE)
        self.stage.controls.title.setText("")
        self._refresh_queue()
        self._refresh_buttons()
        self._sync_protected_thumbs()
        self._update_back()
        self.stage.show_home(True, with_controls=False)
        self._leave_fullscreen_if_stuck()
        return True

    # ----------------------------------------- Home card menu and selection (D4)
    @staticmethod
    def _entry_paths(entries):
        """Video paths behind Home cards (a stacked playlist card stands for all its videos)."""
        out, seen = [], set()
        for e in entries:
            for p in (e.get("group") or [e["path"]]):
                k = Store._key(p)
                if k not in seen:
                    seen.add(k)
                    out.append(p)
        return out

    def _home_pg(self):
        return self.stage.home.home_page

    def _folder_card_menu(self, e, pos):
        self._card_menu(e, pos)

    def _remove_folder(self, folder):
        """Takes the folder off Home together with the history of its videos (progress of those is dropped, like
        Remove on a card), so they do not move into Recent media. Files are never touched."""
        fk = Store._key(folder)
        inside = [k for k, f in self._folder_of.items() if Store._key(f) == fk]
        paths = [e["path"] for e in self.store.recent if Store._key(e["path"]) in set(inside)]
        snap = self.store.folder_snapshot(folder)
        wat = self.store.watched_under(folder)
        self._push_undo({"kind": "folder", "snap": snap, "hist": self._hist_copy(paths), "watched": wat,
                         "label": os.path.basename(os.path.normpath(folder)) or folder})
        self.store.remove_folder(folder)
        if wat:                                           # a folder added again starts fresh
            for w in wat:
                self.store.unmark_watched(w["path"])
        for p in paths:
            self.store.remove(p)
        self._stop_if_playing(paths + ([self.current] if self.current and Store._key(self.current) in set(inside) else []))
        self._refresh_home(animate=False)
        self._pl_toast("Folder removed from Home. Your files were not touched. Press Ctrl+Z to undo.")

    # ---------------------------------------------- Remove from Home and Ctrl+Z
    def _hist_copy(self, paths):
        keys = {Store._key(p) for p in paths}
        return [dict(e) for e in self.store.recent if Store._key(e["path"]) in keys]

    def _push_undo(self, rec):
        self._undo.append(rec)
        del self._undo[:-10]                               # the last 10 removals
        self._sync_keep_thumbs()

    def _sync_keep_thumbs(self):
        names = set()
        for r in self._undo:
            names |= {self.store.thumb_path(e["path"], e["size"]).name for e in r["hist"]}
        self.store.keep_thumbs = frozenset(names)          # their thumbnails are not cleaned up meanwhile

    def _heading_menu(self, folder, pos):
        m = DarkMenu(self)
        m.item("Remove folder from Home", lambda: self._remove_folder(folder), "remove")
        m.exec(pos)

    def _remove_cards(self, entries):
        """Takes cards off Home. A card of a saved folder is hidden there (its video files are never touched);
        the history of those videos is dropped, like Remove on a Recent media card."""
        if not entries:
            return
        paths = self._entry_paths(entries)
        by_folder = {}
        for e in entries:
            if e.get("folder"):
                by_folder.setdefault(e["folder"], []).extend(e.get("hidekeys") or [Store._key(e["path"])])
        label = (entries[0].get("title") or display_name(entries[0]["path"])) if len(entries) == 1 else f"{len(entries)} items"
        rec = {"kind": "cards", "folders": {}, "hist": self._hist_copy(paths), "label": label}
        self._push_undo(rec)
        for folder, keys in by_folder.items():
            rec["folders"][folder] = self.store.hide_in_folder(folder, keys)
        for p in paths:
            self.store.remove(p)
        self.store.save()
        self._stop_if_playing(paths)                       # taking the playing video off Home stops it
        self._home_pg().clear_selection()
        self._refresh_home(animate=False)
        self._pl_toast(f"Removed {label}. Press Ctrl+Z to undo.")

    def _undo_remove(self):
        """Ctrl+Z on Home: the last removal comes back (progress included)."""
        if not self._undo:
            return False
        rec = self._undo.pop()
        if rec["kind"] == "folder":
            if rec["snap"]:
                self.store.restore_folder(rec["snap"])
            self.store.restore_watched(rec.get("watched") or [])
        else:
            for folder, keys in rec["folders"].items():
                self.store.unhide_in_folder(folder, keys)
        self.store.restore_recent(rec["hist"])
        self._sync_keep_thumbs()
        self.store.save()
        self._refresh_home(animate=False)
        self._pl_toast(f"Restored {rec['label']}.")
        return True

    def _card_menu(self, e, pos):
        """Menu of a Home card (Recent media or a folder section). With several cards selected it acts on all."""
        hp = self._home_pg()
        in_sel = hp.sel_mode and hp._selkey(e) in hp.sel
        targets = hp.selected_entries() if in_sel else [e]
        folder_cards = any(t.get("folder") for t in targets)
        m = DarkMenu(self)
        m.item("Play", lambda: self._play_entries(targets), "play_o")
        m.item("Play next", lambda: self._play_next(self._entry_paths(targets)), "playnext")
        m.addSeparator()
        self._add_to_submenu(m, targets)
        if not folder_cards:
            m.item("Remove", lambda: self._remove_cards(targets), "remove")
        m.item("Rename", lambda: self._card_rename(targets[0]), "rename", shortcut="F2",
               enabled=len(targets) == 1 and self._can_rename(targets[0]))
        one_video = len(targets) == 1 and not targets[0].get("group")
        m.item("Change picture", lambda: self._card_change_picture(targets[0]), "photo", enabled=one_video)
        if one_video and self.store.is_picked(targets[0]["path"], targets[0].get("size", 0)):
            m.item("Use automatic picture",
                   lambda: self._use_auto_picture(targets[0]["path"], targets[0].get("size", 0)), "refresh")
        m.item("Properties", lambda: self.show_file_properties(targets[0]["path"]), "info",
               enabled=len(targets) == 1)
        if folder_cards:
            m.addSeparator()
            m.item("Remove from Home", lambda: self._remove_cards(targets), "remove")
        m.item("Deselect" if in_sel else "Select", lambda: hp.toggle_entry(e) if in_sel else hp.select_entry(e),
               "select")
        m.exec(pos)

    # ------------------------------------------------ PHASE B: Change picture
    def _card_change_picture(self, e):
        path, size = e["path"], e.get("size") or self._size(e["path"])
        key = Store._key(path)
        card = next((c for c in self._home_pg().all_cards() if Store._key(c.e["path"]) == key), None)
        pos = card.mapToGlobal(QPoint(0, card.height() // 2)) if card else self.mapToGlobal(QPoint(200, 200))
        is_open = bool(self.current) and Store._key(self.current) == key and not self._pending \
            and self.player.get_state() in (vlc.State.Playing, vlc.State.Paused)
        fl = PictureFlyout(self)
        self._pic_flyout = fl
        fl.framePicked.connect(lambda img, p=path, s=size: self._set_picked_image(p, s, img))
        fl.useCurrent.connect(lambda p=path, s=size: self._use_current_frame(p, s))
        fl.automatic.connect(lambda p=path, s=size: self._use_auto_picture(p, s))
        fl.popup(path, display_name(path), is_open, f"Paused at {_clock(self.player.get_time())}" if is_open else "",
                 self.store.is_picked(path, size), pos)
        self.thumbs.submit_samples(path, size)

    def _samples_ready(self, path, frames):
        fl = getattr(self, "_pic_flyout", None)
        if fl is not None and fl.isVisible() and fl.path == path:
            fl.set_frames(frames)

    def _set_picked_image(self, path, size, img):
        """The user picked this frame: bars cut like every picture, kept until Use automatic picture."""
        final = self.store.thumb_path(path, size)
        try:
            save_card_picture(img, final.with_name(final.stem + ".new.jpg"), final)
        except OSError:
            return
        self.store.set_picked(path, size)
        self._thumb_done(path)
        if self.stage.home.isVisible():
            self._refresh_home(animate=False)

    def _use_current_frame(self, path, size):
        """Use the frame of the open video (the one the slot shows) as the card picture."""
        if not self.current or Store._key(self.current) != Store._key(path):
            return
        tmp = self.store.thumb_dir / "pick.new.jpg"
        try:
            if tmp.exists():
                tmp.unlink()
            if self.player.video_take_snapshot(0, str(tmp), PIC_W, 0) != 0:
                return
        except Exception:
            return

        def finish(tries=0):
            img = QImage(str(tmp)) if tmp.exists() else QImage()
            if img.isNull():
                if tries < 6:
                    QTimer.singleShot(150, lambda: finish(tries + 1))
                return
            self._set_picked_image(path, size, img)
            try:
                tmp.unlink()
            except OSError:
                pass
        QTimer.singleShot(300, finish)

    def _use_auto_picture(self, path, size):
        """Forget the picked picture: the card gets the automatic picture again (made in the background)."""
        self.store.clear_picked(path)
        final = self.store.thumb_path(path, size)
        for f in (final, final.with_suffix(".crop")):
            try:
                f.unlink()
            except OSError:
                pass
        self._thumb_done(path)
        self._thumbs_kick_soon()

    # ------------------------------------------------ rename on Home cards
    @staticmethod
    def _show_key(group):
        """Show key when every video of a stack is an episode of one show, else None."""
        infos = [parse_episode(p) for p in group]
        if infos and all(infos) and len({i["gkey"] for i in infos}) == 1 and infos[0]["show"]:
            return infos[0]["key"]
        return None

    def _can_rename(self, e):
        if e.get("dirstack"):
            return False                                 # a subfolder stack is named after its folder
        return not e.get("group") or self._show_key(e["group"]) is not None

    def _rename_selected(self):
        """F2 on Home: rename the one selected card."""
        hp = self._home_pg()
        sel = hp.selected_entries() if hp.sel_mode else []
        if self.stage.home.page == "home" and self.stage.home.isVisible() and len(sel) == 1 \
                and self._can_rename(sel[0]):
            self._card_rename(sel[0])

    def _card_rename(self, e):
        key = Store._key(e["path"])
        card = next((c for c in self._home_pg().all_cards() if Store._key(c.e["path"]) == key), None)
        pos = card.mapToGlobal(QPoint(0, card.height() // 2)) if card else self.mapToGlobal(QPoint(200, 200))
        group = e.get("group")
        fl = RenameFlyout(self)
        self._name_flyout = fl                       # keep it alive while it is open
        if group:
            show = self._show_key(group)
            if show is None:
                return
            fl.accepted.connect(lambda text, k=show: self._show_rename_done(k, text))
            fl.automatic.connect(lambda k=show: self._show_rename_done(k, ""))
            fl.popup(self._playlist_title(group, e), "Rename", pos)
        else:
            path, old = e["path"], display_name(e["path"])
            fl.accepted.connect(lambda text, p=path, o=old: self._file_rename_done(p, o, text))
            fl.automatic.connect(lambda p=path: self._file_rename_done(p, "", "", reset=True))
            fl.popup(old, "Rename", pos)

    def _names_changed(self):
        """Every label that shows a name follows the new name."""
        self._exist_cache = {}
        self._refresh_home(animate=False)
        self._refresh_queue()
        self.stage.home.settings_page.set_rules(self.store.data["name_rules"])
        if self.current:
            name = display_name(self.current)
            self.stage.controls.title.setText(name)
            self.setWindowTitle(f"{name} - {APP_TITLE}")

    def _show_rename_done(self, key, text):
        self.store.set_show_name(key, text)
        self._names_changed()
        self._pl_toast("Show name changed." if " ".join(text.split()) else "Automatic show name restored.")

    def _file_rename_done(self, path, old, text, reset=False):
        text = " ".join(text.split())
        if reset:
            self.store.set_custom_name(path, "")
            self._names_changed()
            self._pl_toast("Automatic name restored.")
            return
        if not text or text == old:
            return
        self.store.set_custom_name(path, text)
        self._names_changed()
        self._pl_toast(f"Renamed to '{text}'.")
        cut = detect_cut(old, text)
        if cut and piece_is_safe(cut[1]) and {"side": cut[0], "text": cut[1]} not in self.store.data["name_rules"]:
            side, piece = cut
            where = "start" if side == "start" else "end"
            if self._confirm("Also hide this piece on other videos?",
                             f"Hide \u201c{piece}\u201d at the {where} of the name on every video that has it.",
                             "Yes, hide it", "No, only this video"):
                self.store.add_name_rule(side, piece)
                self._names_changed()
                self._pl_toast(f"\u201c{piece}\u201d is hidden. You can undo it in Settings.")

    def _remove_rule(self, i):
        self.store.remove_name_rule(i)
        self._names_changed()

    # ------------------------------------------------ saved playlists (phase D8)
    def _wire_playlists(self):
        hv = self.stage.home
        pp, dp = hv.playlists_page, hv.detail_page
        pp.newRequested.connect(self._pl_new_clicked)
        pp.openRequested.connect(self._pl_open)
        pp.playRequested.connect(lambda pid: self._pl_play([pid]))
        pp.menuRequested.connect(self._pl_card_menu)
        pp.sortChosen.connect(self._pl_sort)
        pp.selPlay.connect(lambda: self._pl_play([p["id"] for p in pp.selected()]))
        pp.selPlayNext.connect(lambda: self._pl_play([p["id"] for p in pp.selected()], next_only=True))
        pp.selAdd.connect(self._pl_sel_add_menu)
        pp.selRename.connect(lambda: self._pl_rename(pp.selected()[0]) if len(pp.selected()) == 1 else None)
        pp.selDelete.connect(lambda: self._pl_delete(pp.selected()))
        dp.playAll.connect(lambda: self._pl_play([dp.pid]))
        dp.playRequested.connect(self._pl_detail_play)
        dp.addToRequested.connect(lambda pos: self._pl_detail_add_menu(pos, None))
        dp.renameRequested.connect(lambda: self._pl_rename(self.store.playlist(dp.pid)))
        dp.deleteRequested.connect(lambda: self._pl_delete([self.store.playlist(dp.pid)]))
        dp.menuRequested.connect(self._pl_row_menu)
        dp.removeRequested.connect(self._pl_remove_items)
        dp.reordered.connect(self._pl_reorder)
        dp.filesDropped.connect(lambda files: self._pl_add_to(dp.pid, files))
        dp.selPlay.connect(lambda: self._pl_detail_play_paths(dp.checked_paths()))
        dp.selPlayNext.connect(lambda: self._play_next(dp.checked_paths()))
        dp.selAdd.connect(lambda pos: self._pl_detail_add_menu(pos, dp.checked_paths()))
        dp.selMore.connect(self._pl_detail_more_menu)

    def _pl_toast(self, text):
        self.stage.home.toast.show_text(text)

    def _pl_refresh(self, animate=False):
        hv = self.stage.home
        if self.store.prune_playlists():
            self.store.save()
        hv.playlists_page.refresh(self.store.playlists_sorted(), self.store.data["playlist_sort"],
                                  self.store.playlist_thumbs, animate)
        if hv.page == "pl_detail":
            self._pl_show_detail(hv.detail_page.pid)

    def _pl_show_detail(self, pid):
        hv = self.stage.home
        pl = self.store.playlist(pid)
        if pl is None:
            if hv.page == "pl_detail":
                hv.show_page("playlists")
            return
        if self.store.prune_playlists(pid):
            self.store.save()
        self._scan_durations(pl["items"])
        hv.detail_page.show_playlist(pl, self.store.playlist_thumbs(pl), self._dur_of, self._exists)

    def _pl_open(self, pid):
        self._exist_cache = {}
        self._pl_show_detail(pid)
        if self.store.playlist(pid):
            self.stage.home.show_page("pl_detail")

    def _pl_sort(self, key):
        self.store.data["playlist_sort"] = key
        self.store.save()
        self._pl_refresh()

    # --- names (create / rename)
    def _pl_name_flyout(self, text, label, gpos, done):
        fl = NameFlyout(self)
        self._name_flyout = fl                       # keep it alive while it is open
        fl.accepted.connect(done)
        fl.popup(text, label, gpos)

    def _pl_new_clicked(self, anchor):
        pos = anchor.mapToGlobal(QPoint(0, anchor.height() + 8))
        if anchor is self.stage.home.playlists_page.empty.btn:
            pos = anchor.mapToGlobal(QPoint((anchor.width() - NameFlyout.W) // 2, anchor.height() + 8))
        self._pl_name_flyout("Untitled playlist", "Create playlist", pos, lambda name: self._pl_create(name, []))

    def _pl_new_from(self, paths):
        paths = list(paths)
        self._pl_name_flyout("Untitled playlist", "Create playlist", QCursor.pos(),
                             lambda name: self._pl_create(name, paths))

    def _pl_create(self, name, paths):
        paths = self._clean_paths(paths)
        pl = self.store.new_playlist(name, paths)
        self.store.save()
        self._home_pg().clear_selection()
        self._pl_refresh()
        n = len(pl["items"])
        self._pl_toast(f"{items_text(n)} added to playlist '{pl['name']}'." if n
                       else f"Playlist '{pl['name']}' created.")
        return pl

    def _pl_rename(self, pl):
        if not pl:
            return
        pid = pl["id"]
        card = self.stage.home.playlists_page.card_of(pid)
        if self.stage.home.page == "pl_detail" or card is None:
            dp = self.stage.home.detail_page
            pos = dp.btn_rename.mapToGlobal(QPoint(0, dp.btn_rename.height() + 8))
        else:
            pos = card.mapToGlobal(QPoint(0, card.height() // 2))
        self._pl_name_flyout(pl["name"], "Rename", pos, lambda name, i=pid: self._pl_rename_done(i, name))

    def _pl_rename_done(self, pid, name):
        pl = self.store.rename_playlist(pid, name)
        if pl:
            self.store.save()
            self.stage.home.playlists_page.clear_selection()
            self._pl_refresh()
            self._pl_toast(f"Playlist renamed to '{pl['name']}'.")

    # --- delete
    def _confirm(self, title, text, ok_text, cancel_text="Cancel"):
        scrim = QWidget(self)
        scrim.setAttribute(Qt.WA_StyledBackground, True)
        scrim.setStyleSheet("background:rgba(0,0,0,110);")
        scrim.setGeometry(self.rect())
        scrim.show()
        scrim.raise_()
        dlg = ConfirmDialog(self, title, text, ok_text, cancel_text)
        dlg.move(self.mapToGlobal(QPoint((self.width() - dlg.width()) // 2, (self.height() - dlg.height()) // 2)))
        self._confirm_dlg = dlg
        try:
            return dlg.exec() == QDialog.Accepted
        finally:
            scrim.deleteLater()
            self._confirm_dlg = None

    def _pl_delete(self, pls):
        pls = [p for p in pls if p]
        if not pls:
            return
        if len(pls) == 1:
            text = (f"Are you sure you want to delete this playlist? If you delete '{pls[0]['name']}', "
                    f"you won't be able to recover it.")
            title = "Delete playlist permanently?"
        else:
            text = (f"Are you sure you want to delete these {len(pls)} playlists? "
                    f"If you delete them, you won't be able to recover them.")
            title = "Delete playlists permanently?"
        if not self._confirm(title, text, "Delete"):
            return
        for p in pls:
            self.store.delete_playlist(p["id"])
        self.store.save()
        self.stage.home.playlists_page.clear_selection()
        hv = self.stage.home
        if hv.page == "pl_detail":
            hv.show_page("playlists")
        self._pl_refresh()
        self._pl_toast(f"Playlist '{pls[0]['name']}' deleted." if len(pls) == 1 else f"{len(pls)} playlists deleted.")

    # --- add to
    def _pl_add_items(self, menu, get_paths, skip_id=None):
        """'New playlist' and every saved playlist, as menu rows. `get_paths()` runs when a row is chosen."""
        menu.item("New playlist", lambda: self._pl_new_from(get_paths()), "add")
        for pl in self.store.playlists_sorted():
            if pl["id"] != skip_id:
                menu.item(pl["name"].replace("&", "&&"), lambda i=pl["id"]: self._pl_add_to(i, get_paths()), "playlists")

    def _pl_add_menu(self, menu, get_paths, skip_id=None):
        sub = menu.sub("Add to", "add")
        sub.item("Play queue", lambda: self._add_paths_to_queue(get_paths()), "queue")
        self._pl_add_items(sub, get_paths, skip_id)

    def _add_paths_to_queue(self, paths):
        paths = self._clean_paths(paths, self.queue)
        if not paths:
            self.flash("Already in the play queue")
            return
        self.add_to_queue(paths)
        self._refresh_home(animate=False)
        self.flash("Added to the play queue" if len(paths) == 1 else f"Added {len(paths)} videos to the play queue")

    def _pl_add_to(self, pid, paths):
        pl = self.store.playlist(pid)
        paths = self._clean_paths(paths)
        self._home_pg().clear_selection()
        if not pl:
            return
        if not paths:
            self._pl_toast("Those files were not found")
            return
        n = self.store.add_to_playlist(pid, paths)
        if n:
            self.store.save()
            self._pl_refresh()
            self._pl_toast(f"{items_text(n)} added to playlist '{pl['name']}'.")
        else:
            self._pl_toast(f"Already in playlist '{pl['name']}'.")

    def _pl_paths(self, ids):
        """Existing videos of the playlists, in order, without duplicates."""
        out = []
        for i in ids:
            pl = self.store.playlist(i)
            if pl:
                if self.store.prune_playlists(i):
                    self.store.save()
                out += pl["items"]
        return self._clean_paths(out)

    # --- play
    def _open_exact(self, paths, index=0):
        """Load exactly these videos as the play queue (no folder scan for single episodes)."""
        self.queue = list(paths)
        self._scan_durations(self.queue)
        self.play_index(index, resume=True)

    def _pl_play(self, ids, next_only=False):
        paths = self._pl_paths(ids)
        self.stage.home.playlists_page.clear_selection()
        if not paths:
            self._pl_toast("There are no videos to play in this playlist")
            return
        if next_only:
            self._play_next(paths)
        else:
            self._open_exact(paths)

    def _pl_detail_play(self, row):
        dp = self.stage.home.detail_page
        items = dp.list.paths()
        if not (0 <= row < len(items)):
            return
        self._pl_detail_play_paths([items[row]])

    def _pl_detail_play_paths(self, chosen):
        dp = self.stage.home.detail_page
        if not chosen:
            return
        paths = self._pl_paths([dp.pid])
        key = Store._key(chosen[0])
        idx = next((n for n, p in enumerate(paths) if Store._key(p) == key), -1)
        dp.clear_selection()
        if idx < 0:
            self._pl_toast("That file was not found")
            return
        self._open_exact(paths, idx)

    # --- menus
    def _pl_card_menu(self, pl, pos):
        pp = self.stage.home.playlists_page
        in_sel = pp.is_selected(pl["id"])
        targets = pp.selected() if in_sel else [pl]
        ids = [t["id"] for t in targets]
        m = DarkMenu(self)
        m.item("Play", lambda: self._pl_play(ids), "play_o")
        m.item("Play next", lambda: self._pl_play(ids, next_only=True), "playnext")
        m.addSeparator()
        self._pl_add_menu(m, lambda: self._pl_paths(ids))
        m.item("Rename", lambda: self._pl_rename(targets[0]), "rename", enabled=len(targets) == 1)
        m.item("Delete", lambda: self._pl_delete(targets), "trash")
        m.addSeparator()
        m.item("Deselect" if in_sel else "Select", lambda: pp._toggle(pl) if in_sel else pp.select_pl(pl), "select")
        m.exec(pos)

    def _pl_sel_add_menu(self, pos):
        pp = self.stage.home.playlists_page
        ids = [p["id"] for p in pp.selected()]
        m = DarkMenu(self)
        m.item("Play queue", lambda: self._add_paths_to_queue(self._pl_paths(ids)), "queue")
        self._pl_add_items(m, lambda: self._pl_paths(ids))
        m.exec(pos)

    def _pl_detail_add_menu(self, pos, paths):
        dp = self.stage.home.detail_page
        m = DarkMenu(self)
        get = (lambda: paths) if paths else (lambda: self._pl_paths([dp.pid]))
        m.item("Play queue", lambda: self._add_paths_to_queue(get()), "queue")
        self._pl_add_items(m, get, skip_id=dp.pid)
        m.exec(pos)

    def _pl_row_menu(self, row, pos):
        dp = self.stage.home.detail_page
        items = dp.list.paths()
        if not (0 <= row < len(items)):
            return
        path = items[row]
        in_sel = dp.is_checked(row)
        targets = dp.checked_paths() if in_sel else [path]
        m = DarkMenu(self)
        m.item("Play", lambda: self._pl_detail_play_paths([targets[0]]), "play_o")
        m.item("Play next", lambda: self._play_next(targets), "playnext")
        m.addSeparator()
        self._pl_add_menu(m, lambda: targets, skip_id=dp.pid)
        m.item("Remove from playlist", lambda: self._pl_remove_items(targets), "remove")
        m.item("Properties", lambda: self.show_file_properties(targets[0]), "info", enabled=len(targets) == 1)
        m.addSeparator()
        m.item("Deselect" if in_sel else "Select", lambda: dp.toggle_row(row), "select")
        m.exec(pos)

    def _pl_detail_more_menu(self, pos):
        dp = self.stage.home.detail_page
        paths = dp.checked_paths()
        m = DarkMenu(self)
        m.item("Remove from playlist", lambda: self._pl_remove_items(paths), "remove")
        m.item("Properties", lambda: self.show_file_properties(paths[0]), "info", enabled=len(paths) == 1)
        m.exec(pos)

    def _pl_remove_items(self, paths):
        dp = self.stage.home.detail_page
        if not paths or dp.pid is None:
            return
        self.store.remove_from_playlist(dp.pid, paths)       # the video files are never touched
        self.store.save()
        dp.clear_selection()
        self._pl_refresh()

    def _pl_reorder(self, paths):
        dp = self.stage.home.detail_page
        self.store.set_playlist_order(dp.pid, paths)
        self.store.save()

    def _add_to_submenu(self, menu, targets):
        sub = menu.sub("Add to", "add")
        sub.item("Play queue", lambda: self._add_entries_to_queue(targets), "queue")
        self._pl_add_items(sub, lambda: self._entry_paths(targets))
        return sub

    def _play_entries(self, entries):
        paths = self._entry_paths(entries)
        if not paths:
            return
        self._home_pg().clear_selection()
        if len(entries) == 1:
            self._home_play(entries[0]["path"])          # same as clicking the card
        else:
            self.open_paths(paths)

    def _play_next(self, paths):
        """Put the videos right after the one that is playing (existing queue, nothing else changes)."""
        paths = self._clean_paths(paths)
        self._home_pg().clear_selection()
        if not paths:
            self.flash("Those files were not found")
            return
        if not self.current or not self.queue:
            self.queue = paths
            self._scan_durations(paths)
            self.play_index(0, resume=True, stay=self._on_home())
            return
        cur = Store._key(self.current)
        paths = [p for p in paths if Store._key(p) != cur]
        if not paths:
            self.flash("That video is already playing")
            return
        keys = {Store._key(p) for p in paths}
        self.queue = [q for q in self.queue if Store._key(q) not in keys]
        at = self._qi_of_current() + 1
        self.queue[at:at] = paths
        self.qi = self._qi_of_current()
        self._scan_durations(paths)
        self._sync_protected_thumbs()
        self._refresh_queue()
        self._refresh_nav()
        self._refresh_home(animate=False)
        self.flash("Will play next" if len(paths) == 1 else f"{len(paths)} videos will play next")

    def _add_entries_to_queue(self, entries):
        paths = self._clean_paths(self._entry_paths(entries), self.queue)
        self._home_pg().clear_selection()
        if not paths:
            self.flash("Already in the play queue")
            return
        self.add_to_queue(paths)
        self._refresh_home(animate=False)
        self.flash("Added to the play queue" if len(paths) == 1 else f"Added {len(paths)} videos to the play queue")

    def _remove_entries(self, entries):
        self._remove_cards(entries)

    def show_file_properties(self, path):
        if self.current and Store._key(self.current) == Store._key(path):
            self.show_properties()                        # the playing video: full details
            return
        p = Path(path)
        try:
            st = p.stat()
            size = human_size(st.st_size)
            mod = datetime.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
        except OSError:
            size = mod = "-"
        e = next((x for x in self.store.recent if Store._key(x["path"]) == Store._key(path)), {})
        dur = e.get("duration", 0)
        general = [("Name", p.name), ("Folder", str(p.parent)), ("Size", size), ("Modified", mod),
                   ("Duration", fmt(dur) if dur > 0 else "-")]
        InfoDialog(self, "Properties", [("File", general)]).exec()

    def _sel_play(self):
        self._play_entries(self._home_pg().selected_entries())

    def _sel_play_next(self):
        self._play_next(self._entry_paths(self._home_pg().selected_entries()))

    def _sel_add_menu(self, pos):
        hp = self._home_pg()
        targets = hp.selected_entries()
        m = DarkMenu(self)
        m.item("Play queue", lambda: self._add_entries_to_queue(targets), "queue")
        self._pl_add_items(m, lambda: self._entry_paths(targets))
        m.exec(pos)

    def _sel_more_menu(self, pos):
        hp = self._home_pg()
        targets = hp.selected_entries()
        m = DarkMenu(self)
        m.item("Remove from Home" if any(t.get("folder") for t in targets) else "Remove",
               lambda: self._remove_cards(targets), "remove")
        m.item("Properties", lambda: self.show_file_properties(targets[0]["path"]), "info",
               enabled=len(targets) == 1)
        m.exec(pos)

    # ----------------------------------------------------------- queue logic
    def _exists(self, path):
        """File check with a short cache, so the buttons don't hit the disk on every update."""
        now = time.monotonic()
        hit = self._exist_cache.get(path)
        if hit and now - hit[0] < 3.0:
            return hit[1]
        ok = os.path.isfile(path)
        self._exist_cache[path] = (now, ok)
        return ok

    def _clean_paths(self, paths, existing=()):
        """Existing files only, without duplicates (also against `existing`)."""
        seen = {Store._key(p) for p in existing}
        out = []
        for p in paths:
            if not os.path.isfile(p):
                continue
            k = Store._key(p)
            if k not in seen:
                seen.add(k)
                out.append(p)
        return out

    def _next_index(self):
        n = len(self.queue)
        if not self.current or n == 0 or (n < 2 and self.qi >= 0):
            return None
        if self.shuffle:
            choices = [k for k in range(n) if k != self.qi and self._exists(self.queue[k])]
            return random.choice(choices) if choices else None
        for k in range(self.qi + 1, n):
            if self._exists(self.queue[k]):
                return k
        if self.repeat == 1:
            for k in range(0, self.qi):
                if self._exists(self.queue[k]):
                    return k
        return None

    def _prev_index(self):
        n = len(self.queue)
        if n < 2 or self.qi < 0:
            return None
        for k in range(self.qi - 1, -1, -1):
            if self._exists(self.queue[k]):
                return k
        if self.repeat == 1:
            for k in range(n - 1, self.qi, -1):
                if self._exists(self.queue[k]):
                    return k
        return None

    def next_item(self):
        i = self._next_index()
        if i is not None:
            self.play_index(i, resume=True, stay=self._on_home())

    def prev_item(self):
        if not self.current:
            return
        if self.player.get_time() > 3000:
            self.seek_to(0)
            return
        i = self._prev_index()
        if i is None:
            self.seek_to(0)
        else:
            self.play_index(i, resume=True, stay=self._on_home())

    def _has_next(self):
        return self._next_index() is not None

    # ------------------------------------------------------------- play queue
    def _dur_of(self, path):
        k = Store._key(path)
        d = self._durations.get(k)
        if d:
            return d
        for e in self.store.recent:
            if Store._key(e["path"]) == k and e.get("duration", 0) > 0:
                return e["duration"]
        return None

    def _page_changed(self, key):
        if key != "queue":
            self._queue_from_video = False
        else:
            self._refresh_queue()
        if key == "settings":
            self._sync_settings()
        if key == "playlists":
            self._pl_refresh()
        self._update_back()

    def _refresh_queue(self, delay=False):
        if self.stage.home.page not in ("queue", "pl_detail"):
            return
        if delay:                                   # many results arriving: rebuild once
            if self._queue_timer is None:
                self._queue_timer = QTimer(self, singleShot=True, interval=150)
                self._queue_timer.timeout.connect(self._refresh_queue)
            self._queue_timer.start()
            return
        if self.stage.home.page == "pl_detail":
            self._pl_show_detail(self.stage.home.detail_page.pid)
            return
        self.stage.home.queue_page.refresh(self.queue, self.qi, self._dur_of, self._exists)

    def _queue_status(self, text):
        if self.stage.home.page == "queue":
            self.flash(text)

    def _qi_of_current(self):
        if not self.current:
            return -1
        k = Store._key(self.current)
        return next((i for i, p in enumerate(self.queue) if Store._key(p) == k), -1)

    def add_to_queue(self, paths):
        new = self._clean_paths(paths, self.queue)
        if not new:
            self._queue_status("Nothing new to add")
            return
        start = len(self.queue)
        self.queue.extend(new)
        self._scan_durations(new)
        if not self.current:                        # nothing playing yet: start with the first added
            self.play_index(start, resume=True, stay=self._on_home())
        self._sync_protected_thumbs()
        self._refresh_queue()
        self._queue_status(f"Added {len(new)} video" + ("s" if len(new) != 1 else ""))

    def remove_from_queue(self, paths):
        """Take videos out of the list (files are never deleted). The playing video may go too: it keeps playing."""
        keys = {Store._key(p) for p in paths}
        cur = Store._key(self.current) if self.current else None
        before = len(self.queue)
        self.queue = [p for p in self.queue if Store._key(p) not in keys]
        self.qi = self._qi_of_current()
        removed = before - len(self.queue)
        self._refresh_queue()
        self._refresh_nav()
        if not removed:
            return
        msg = f"Removed {removed} from the list (files are not deleted)"
        if cur in keys:
            msg = f"Removed {removed}. The video that is playing keeps playing."
        self._queue_status(msg)

    def _replay_index(self):
        """Index to restart the current video at. If it was taken out of the list while it played, it goes back in."""
        if self.current and self.qi < 0:
            self.queue.insert(0, self.current)
            self.qi = 0
            self._refresh_queue()
        return self.qi

    def reorder_queue(self, paths):
        if sorted(map(Store._key, paths)) != sorted(map(Store._key, self.queue)):
            self._refresh_queue()                   # something odd: show the real order again
            return
        self.queue = list(paths)
        self.qi = self._qi_of_current()
        self._refresh_nav()

    def _play_from_queue(self, i):
        if 0 <= i < len(self.queue) and not os.path.isfile(self.queue[i]):
            self._queue_status("That file was not found")
            self._exist_cache.pop(self.queue[i], None)
            self._refresh_queue()
            return
        self.play_index(i, resume=True, stay=self._on_home())

    def _set_autoplay(self, on):
        self.autoplay_next = bool(on)
        self.store.data["autoplay_next"] = self.autoplay_next
        self.store.save()
        self._sync_settings()

    # --- Settings page
    def _load_settings(self):
        d = self.store.data

        def pick(key, default, allowed):
            v = d.get(key, default)
            return v if v in allowed else default
        self.rewind_s = pick("rewind_s", 10, (5, 10, 15, 20, 30))
        self.ff_s = pick("ff_s", 30, (5, 10, 15, 20, 30))
        self.max_volume = pick("max_volume", 200, (100, 200))
        self.hide_after = pick("hide_s", 3, (2, 3, 5, 10))
        self.folder_queue = pick("folder_queue", "episodes", ("episodes", "all", "never"))
        self.show_when_paused = bool(d.get("show_paused", True))
        self.show_recent = bool(d.get("show_recent", True))
        self.keep_history = bool(d.get("keep_history", True))
        self.volume = min(self.volume, self.max_volume)
        SKIP_SECONDS["back"], SKIP_SECONDS["fwd"] = self.rewind_s, self.ff_s

    def _sub_size_value(self):
        return self.store.data.get("sub_size", "") or "16"

    @staticmethod
    def _label_of(options, value):
        return next((lab for lab, val in options if val == value), str(value))

    def _shot_folder(self):
        custom = self.store.data.get("shot_dir", "")
        if custom:
            return Path(custom)
        pics = QStandardPaths.writableLocation(QStandardPaths.PicturesLocation) or str(Path.home())
        return Path(pics) / "Simple Player"

    def _combo_current(self, key):
        return {"rewind": self.rewind_s, "ff": self.ff_s, "boost": self.max_volume, "hide_s": self.hide_after,
                "subsize": self._sub_size_value(), "aout": self.store.data.get("aout", ""),
                "folderq": self.folder_queue}[key]

    def _sync_settings(self):
        sp = self.stage.home.settings_page
        for key, opts in SETTING_OPTIONS.items():
            sp.set_combo(key, self._label_of(opts, self._combo_current(key)))
        sp.set_toggle("autoplay", self.autoplay_next)
        sp.set_rules(self.store.data["name_rules"])
        sp.set_toggle("show_paused", self.show_when_paused)
        sp.set_toggle("recent", self.show_recent)
        sp.set_toggle("history", self.keep_history)
        sp.cards["shot"].set_desc(str(self._shot_folder()))
        sp.cards["about"].set_value(f"Version {APP_VERSION}")

    def _on_setting_combo(self, key, pos):
        ctl = self.stage.home.settings_page.cards[key].control
        self._settings_menu(SETTING_OPTIONS[key], self._combo_current(key),
                            lambda v, k=key: self._apply_setting(k, v), pos, box_h=ctl.height())

    def _apply_setting(self, key, v):
        d = self.store.data
        if key == "subsize":
            self._set_sub_size(v)
            return
        if key == "aout":
            self._set_aout(v)
            return
        if key in ("rewind", "ff"):
            if key == "rewind":
                self.rewind_s, d["rewind_s"] = v, v
            else:
                self.ff_s, d["ff_s"] = v, v
            SKIP_SECONDS["back"], SKIP_SECONDS["fwd"] = self.rewind_s, self.ff_s
            self.stage.controls.back.update()
            self.stage.controls.fwd.update()
            self.stage.home.settings_page.cards["rewind"].update()
            self.stage.home.settings_page.cards["ff"].update()
        elif key == "boost":
            self.max_volume, d["max_volume"] = v, v
            self.volpop.slider.setMaximum(v)
            if self.volume > v:
                self.set_volume(v)
        elif key == "hide_s":
            self.hide_after, d["hide_s"] = v, v
        elif key == "folderq":
            self.folder_queue, d["folder_queue"] = v, v
        self.store.save()
        self._sync_settings()

    def _on_setting_toggle(self, key, on):
        d = self.store.data
        if key == "autoplay":
            self._set_autoplay(on)
            return
        if key == "show_paused":
            self.show_when_paused, d["show_paused"] = on, on
        elif key == "recent":
            self.show_recent, d["show_recent"] = on, on
        elif key == "history":
            self.keep_history, d["keep_history"] = on, on
        self.store.save()
        if key == "recent":
            self._refresh_home(animate=False)
        self._sync_settings()

    def _on_setting_button(self, key):
        if key == "defplayer":
            self.set_default_player()
        elif key == "clear":
            self.clear_history()
        elif key == "shot":
            folder = self._shot_folder()
            try:
                folder.mkdir(parents=True, exist_ok=True)
            except OSError:
                self.flash("Can't open that folder")
                return
            from PySide6.QtCore import QUrl
            from PySide6.QtGui import QDesktopServices
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
        elif key == "shot_link":
            start = str(self._shot_folder().parent if not self._shot_folder().exists() else self._shot_folder())
            new = QFileDialog.getExistingDirectory(self, "Choose where screenshots are saved", start)
            if new:
                self.store.data["shot_dir"] = new
                self.store.save()
                self._sync_settings()

    def _settings_menu(self, options, current, setter, pos, box_h=32):
        """Drop-down of a settings card: the choices as one check-marked group, right edge under the card."""
        m = DarkMenu(parent=self)
        grp = QActionGroup(m)
        for label, val in options:
            grp.addAction(m.item(label, lambda v=val: v != current and setter(v), checkable=True,
                                 checked=(val == current)))
        m.ensurePolished()
        size = m.sizeHint()
        scr = self.screen().availableGeometry()
        x = min(max(scr.left(), pos.x() - size.width()), scr.right() - size.width())
        y = pos.y() + 4
        if y + size.height() > scr.bottom():
            y = pos.y() - box_h - size.height() - 4
        m.exec(QPoint(x, y))

    def clear_history(self):
        """Settings > Continue watching > Clear: forgets the saved positions only, never a video file."""
        self.store.clean()
        n = len(self.store.recent)
        if not n:
            self.flash("Continue watching is already empty")
            return
        ask = QMessageBox.question(
            self, APP_TITLE,
            f"Remove {n} video{'s' if n != 1 else ''} from Continue watching?\n\n"
            "The videos themselves are not deleted. Their saved positions are forgotten.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ask != QMessageBox.Yes:
            return
        self.store.data["recent"] = []
        self.store.save()
        self.store._purge_thumbs()
        self._refresh_home(animate=False)
        self.flash("Continue watching cleared")

    def _pick_files(self, title):
        exts = " ".join(f"*{e}" for e in sorted(VIDEO_EXT))
        files, _ = QFileDialog.getOpenFileNames(
            self, title, self.store.data.get("last_dir", ""), f"Video files ({exts});;All files (*.*)")
        if files:
            self.store.data["last_dir"] = os.path.dirname(files[0])
        return files

    def add_dialog(self):
        files = self._pick_files("Add videos to the queue")
        if files:
            self.add_to_queue(files)

    def add_folder_dialog(self):
        folder = QFileDialog.getExistingDirectory(self, "Add folder to the play queue",
                                                  self.store.data.get("last_dir", ""))
        if not folder:
            return
        self.store.data["last_dir"] = folder
        paths = [p for p, _i in self._scan_folder(folder)]
        if not paths:
            self.flash("No videos found in that folder")
            return
        self.add_to_queue(paths)

    def clear_queue(self):
        """Clear button: the list is emptied (files stay, the playing video keeps playing)."""
        if self.queue:
            self.remove_from_queue(list(self.queue))

    # --- Play queue page: menus and selection actions
    def _queue_row_of(self, path):
        k = Store._key(path)
        return next((i for i, p in enumerate(self.queue) if Store._key(p) == k), -1)

    def _queue_play(self, path):
        self.stage.home.queue_page.clear_selection()
        i = self._queue_row_of(path)
        if i >= 0:
            self._play_from_queue(i)

    def _queue_play_next(self, paths):
        pg = self.stage.home.queue_page
        pg.clear_selection()
        if not self.current:
            if paths:
                self._queue_play(paths[0])
            return
        self._play_next(paths)

    def _queue_add_menu(self, pos):
        pg = self.stage.home.queue_page
        m = DarkMenu(self)
        self._pl_add_items(m, lambda: pg.checked_paths() or list(self.queue))
        m.exec(pos)

    def _queue_menu(self, row, pos):
        pg = self.stage.home.queue_page
        if not (0 <= row < len(self.queue)):
            return
        path = self.queue[row]
        in_sel = pg.is_checked(row)
        targets = pg.checked_paths() if in_sel else [path]
        m = DarkMenu(self)
        m.item("Play", lambda: self._queue_play(targets[0]), "play_o")
        m.item("Play next", lambda: self._queue_play_next(targets), "playnext")
        m.addSeparator()
        sub = m.sub("Add to", "add")
        self._pl_add_items(sub, lambda: targets)
        m.item("Remove", lambda: self.remove_from_queue(targets), "remove")
        m.item("Properties", lambda: self.show_file_properties(targets[0]), "info", enabled=len(targets) == 1)
        m.addSeparator()
        m.item("Deselect" if in_sel else "Select", lambda: pg.toggle_row(row), "select")
        m.exec(pos)

    def _qsel_play(self):
        paths = self.stage.home.queue_page.checked_paths()
        if paths:
            self._queue_play(paths[0])

    def _qsel_play_next(self):
        paths = self.stage.home.queue_page.checked_paths()
        if paths:
            self._queue_play_next(paths)

    def _qsel_more_menu(self, pos):
        paths = self.stage.home.queue_page.checked_paths()
        m = DarkMenu(self)
        m.item("Remove", lambda: self.remove_from_queue(paths), "remove")
        m.item("Properties", lambda: self.show_file_properties(paths[0]), "info", enabled=len(paths) == 1)
        m.exec(pos)

    # --- durations (read in the background with a separate light VLC instance)
    def _scan_durations(self, paths):
        todo = [p for p in paths if self._dur_of(p) is None]
        if not todo:
            return
        with self._scan_lock:                        # the probe engine is made by the worker, not here (it took 1 s on the UI thread)
            self._scan_pending.extend(p for p in todo if p not in self._scan_pending)
            if self._scan_running:
                return
            self._scan_running = True
        threading.Thread(target=self._scan_worker, daemon=True).start()

    def _log(self, text):
        """Tiny diagnostic log in %APPDATA%\\SimplePlayer\\player.log (kept under 200 KB)."""
        try:
            f = self.store.dir / "player.log"
            if f.exists() and f.stat().st_size > 200_000:
                f.replace(f.with_suffix(".old"))
            with open(f, "a", encoding="utf-8") as fh:
                fh.write(f"{time.strftime('%H:%M:%S')} {text}\n")
        except Exception:
            pass

    def _tm(self, label):
        """Timing line for the slow-first-play diagnosis: milliseconds since the play request."""
        t0 = getattr(self, "_t_click", None)
        if t0 is None:
            return
        self._log(f"T+{(time.monotonic() - t0) * 1000:7.0f} ms  {label}")

    def _scan_worker(self):
        while True:
            with self._scan_lock:
                if self._closing or not self._scan_pending:
                    self._scan_running = False
                    break
                path = self._scan_pending.pop(0)
            while not self._closing and time.monotonic() < getattr(self, "_starting_until", 0.0):
                time.sleep(0.1)                  # PHASE B: do not read other files while the video is opening
            probe = self._probe
            if probe is None and not self._closing:
                try:                             # the second (light) engine is made here, in the background, after the video plays
                    _t = time.monotonic()
                    probe = self._probe = vlc.Instance("--quiet", "--no-video", "--no-audio")
                    self._tm(f"second VLC engine (probe) created, took {(time.monotonic() - _t) * 1000:.0f} ms (background)")
                except Exception:
                    probe = None
                if probe is None:                # no probe engine: give up on durations (as before)
                    with self._scan_lock:
                        self._scan_pending.clear()
                        self._scan_running = False
                    return
            if self._closing:
                continue
            try:
                _t = time.monotonic()
                m = probe.media_new_path(path)
                m.parse()
                ms = m.get_duration()
                m.release()
                self._tm(f"probe parse {os.path.basename(path)} took {(time.monotonic() - _t) * 1000:.0f} ms (background)")
            except Exception:
                continue
            if ms and ms > 0:
                self.durationFound.emit(path, int(ms))

    def _on_duration(self, path, ms):
        self._durations[Store._key(path)] = ms
        self._refresh_queue(delay=True)

    def toggle_shuffle(self):
        self.shuffle = not self.shuffle
        self.store.data["shuffle"] = self.shuffle
        self._refresh_buttons()
        self.flash("Shuffle on" if self.shuffle else "Shuffle off")

    def cycle_repeat(self):
        self.repeat = (self.repeat + 1) % 3
        self.store.data["repeat"] = self.repeat
        self._refresh_buttons()
        self.flash(["Repeat off", "Repeat all", "Repeat one"][self.repeat])

    def _on_end(self):
        self._ended = True
        self._complete_current()          # VLC reached the end of the media
        if self.repeat == 2:
            self.play_index(self._replay_index(), resume=False, stay=self._on_home())
        elif self.autoplay_next and self._has_next():
            self.next_item()

    # ---------------------------------------------------------------- volume
    def set_volume(self, v, osd=True):
        self.volume = max(0, min(self.max_volume, int(v)))
        if self.volume > 0:
            self.muted = False
        self._apply_audio()
        self.store.data["volume"] = self.volume
        self.store.data["muted"] = self.muted
        self._sync_volume()
        if osd:
            self.flash(f"Volume {self.volume}%", accent=self.volume > 100)

    def step_volume(self, direction):
        v = self.volume
        if direction > 0:
            nv = v + 5
            if v < 100 < nv:       # stop at 100% once before boosting further
                nv = 100
        else:
            nv = v - 5
        self.set_volume(nv)

    def toggle_mute(self):
        self.muted = not self.muted
        self._apply_audio()
        self.store.data["muted"] = self.muted
        self._sync_volume()
        self.flash("Muted" if self.muted else f"Volume {self.volume}%")

    def _sync_volume(self):
        self.stage.controls.vol.set_kind("mute" if self.muted or self.volume == 0 else "volume")
        self.stage.controls.vol.set_waves(1 if self.volume <= 50 else 2)
        self.volpop.sync(self.volume, self.muted)

    def open_volume_popup(self):
        btn = self.stage.controls.vol
        self.volpop.sync(self.volume, self.muted)
        self.volpop.adjustSize()
        self.volpop.move(btn.mapToGlobal(QPoint(btn.width() // 2 - self.volpop.width() // 2,
                                                -self.volpop.height() - 10)))
        self.volpop.fade_in()

    # ------------------------------------------------------------- windowing
    def toggle_fullscreen(self):
        if self.mini:
            self.toggle_mini()
        if self.isFullScreen():
            self.stage.set_overlay(False)
            self.titlebar.show()
            self.showMaximized() if self._was_max else self.showNormal()
            QTimer.singleShot(0, self._sync_titlebar)
            self.stage.controls.full.set_kind("fullscreen")
            self._cursor_hidden(False)
        else:
            self._was_max = self.isMaximized()
            self.titlebar.hide()
            self.showFullScreen()
            QTimer.singleShot(0, self._sync_titlebar)
            self.stage.set_overlay(True)
            self.stage.controls.full.set_kind("restore")
        self.setFocus()

    def toggle_mini(self):
        if self.isFullScreen():
            self.toggle_fullscreen()
        if not self.mini:
            if not self.current:
                self.flash("Open a video first")
                return
            if self.stage.home.isVisible():
                self.stage.show_home(False)
            self._was_max = self.isMaximized()
            if self._was_max:
                self.showNormal()
            self._normal_geo = self.saveGeometry()
            self._normal_rect = self.geometry()
            self.mini = True
            self.stage.set_mini(True)
            self.titlebar.set_mini(True)
            self._tb_reset()
            self.setMinimumSize(QSize(320, 180))
            self.resize(self.MINI_SIZE.width(), self.MINI_SIZE.height())
            area = self.screen().availableGeometry()
            fg = self.frameGeometry()
            self.move(area.right() - fg.width() - 24, area.bottom() - fg.height() - 24)
            set_topmost(self, True)
            self._mini_down, self._mini_press = False, None
            self.mini_ov.hov.set(0.0)
            self.mini_ov._hover = False
            self._sync_titlebar()                    # margins: the picture fills the window, the title bar floats on it
            self._mini_sync()
        else:
            self.mini = False
            self.mini_ov.hide()
            self.stage.set_mini(False)
            self.titlebar.set_mini(False)
            self._sync_titlebar()
            self._tb_reset()
            self._mini_down, self._mini_press = False, None
            self.setMinimumSize(self.NORMAL_MIN)
            if self._normal_geo:
                self.restoreGeometry(self._normal_geo)
                rect = getattr(self, "_normal_rect", None)
                if rect is not None and not self._was_max and self.geometry() != rect:
                    self.setGeometry(rect)           # restoreGeometry can come back narrower after the minimum size changed
            if not self._topmost:
                set_topmost(self, False)
            if self._was_max:
                self.showMaximized()
            self._update_back()
        self._cursor_hidden(False)
        self.setFocus()

    def _visible_rect(self):
        """The window as it is drawn (global logical px). On Windows the client area reaches past the visible
        frame (invisible resize borders), so ask DWM for the real bounds instead of trusting the widget size."""
        tl = self.mapToGlobal(QPoint(0, 0))
        if sys.platform == "win32":
            try:
                hwnd = ctypes.c_void_p(int(self.winId()))
                u32 = ctypes.windll.user32
                ext, pt = wintypes.RECT(), wintypes.POINT()
                pt.x = pt.y = 0
                u32.ClientToScreen(hwnd, ctypes.byref(pt))
                if ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(ext), ctypes.sizeof(ext)) == 0:
                    dpr = self.devicePixelRatioF()
                    return QRect(int(round(tl.x() + (ext.left - pt.x) / dpr)), int(round(tl.y() + (ext.top - pt.y) / dpr)),
                                 int(round((ext.right - ext.left) / dpr)), int(round((ext.bottom - ext.top) / dpr)))
            except Exception:
                pass
        r, b = self._overhang
        return QRect(tl, QSize(max(1, self.width() - r), max(1, self.height() - b)))

    def _mini_sync(self):
        """Keep the overlay exactly over the visible window (the title bar part is not dimmed)."""
        ov = self.mini_ov
        if not self.mini or not self.isVisible() or self.isMinimized():
            if ov.isVisible():
                ov.hide()
            return
        r = self._visible_rect()
        if ov.geometry() != r:
            ov.setGeometry(r)
        if not ov.isVisible():
            ov.show()
            ov.raise_()

    def _mini_tick(self):
        """Mini player, every 30 ms: follow the window, show the controls while the mouse is over the window,
        and handle clicks and dragging by watching the mouse directly (VLC's window swallows mouse events)."""
        if self._closing:
            return
        self._mini_sync()
        ov = self.mini_ov
        pos = QCursor.pos()
        inside = ov.geometry().contains(pos) or QApplication.activePopupWidget() is not None
        ov.set_hover(inside)
        if self._tb_show != inside:                  # the title bar comes and goes with the controls
            self._tb_show = inside
            self._tb_tw.to(1.0 if inside else 0.0, BAR_FADE_IN if inside else BAR_FADE_OUT, EASE_INOUT)
        self._tb_apply()
        st = self.player.get_state()
        length = self.player.get_length()
        t = self.player.get_time()
        ov.set_state((t / length) if length > 0 and t >= 0 else 0.0, self._state_playing(st), self._has_next(),
                     max(0.0, min(1.0, self._tb_tw.value)))
        down = bool(QApplication.mouseButtons() & Qt.LeftButton)
        over = (ov.isVisible() and ov.geometry().contains(pos)
                and not (self.titlebar.isVisible() and pos.y() < ov.geometry().top() + MINI_TB_H))
        local = pos - ov.geometry().topLeft()
        if down and not self._mini_down:
            self._mini_down = over
            self._mini_moved = False
            self._mini_press = (pos, ov.button_at(local)) if over else None
        elif down and self._mini_down and self._mini_press and not self._mini_moved:
            p0, btn = self._mini_press
            if btn is None and (pos - p0).manhattanLength() > 6:       # drag the picture = move the window
                self._mini_moved = True
                wh = self.windowHandle()
                if wh is not None:
                    wh.startSystemMove()
        elif not down and self._mini_down:
            self._mini_down = False
            press, self._mini_press = self._mini_press, None
            if press and not self._mini_moved and over:
                btn = press[1]
                if btn is None:
                    self.toggle_play()
                elif btn == ov.button_at(local):
                    {"prev": self.prev_item, "play": self.toggle_play, "next": self.next_item,
                     "expand": self.toggle_mini}[btn]()

    def toggle_topmost(self):
        self._topmost = not self._topmost
        set_topmost(self, self._topmost or self.mini)

    def _cursor_hidden(self, hide):
        for w in (self.stage, self.stage.video):
            if hide:
                w.setCursor(Qt.BlankCursor)
            else:
                w.unsetCursor()

    def show_home(self, fade=False):
        if fade and not self.mini and not self.stage.home.isVisible() and self.stage.fade_enabled and not self.stage._fading:
            self._refresh_home(animate=False)        # once, here: this is the page that is uncovered under the video
            self._refresh_queue()
            self._home_ready = True                  # the switch must not rebuild it (that made the cards vanish and pop back)
        if fade and not self.mini and not self.stage.home.isVisible() and self.stage.move_to_home(lambda: self.show_home()):
            self._log("transition video->Home: the video sinks into the bar (seamless)")
            return
        if self.mini:
            self.toggle_mini()                       # the mini player has no Home: go back to the full window first
        self._log(f"home pressed, state={self.player.get_state()}")
        if not self.stage.home.isVisible():
            self._from_video = True                  # there is a video page to go back to
        self._save_progress()                        # playback itself keeps running
        self._snapshot_async()
        if getattr(self, "_home_ready", False):
            self._home_ready = False                 # Home was just rebuilt for the cross-fade picture
        else:
            self._refresh_home()
            self._refresh_queue()
        self.stage.show_home(True, with_controls=bool(self.current), mini=bool(self.current))
        self._bars_visible(True, instant=True)
        self._update_back()
        self.bubble.hide()

    # ----------------------------------------------------------------- menus
    @staticmethod
    def _tname(name):
        return name.decode("utf-8", "replace") if isinstance(name, bytes) else str(name)

    def show_menu(self, anchor=None, at=None, tracks_only=False):
        m = DarkMenu(parent=self)
        has = bool(self.current)
        if tracks_only:
            self._fill_av(m)
        else:
            m.item("Open file(s)", self.open_dialog, "folder", "Ctrl+O")
            m.item("Open folder…", self.open_folder_dialog, "folder")
            m.item("Show play queue", self.show_queue, "queue", "Ctrl+Q")
            m.item("Add to play queue…", self.add_dialog, "queue")
            m.addSeparator()
            m.item("Properties", self.show_properties, "info", "Ctrl+I", enabled=has)
            m.addSeparator()
            m.item("Equalizer", self.show_equalizer, "eq", "Ctrl+Shift+E")
            sp = m.sub("Speed", "speed")
            grp = QActionGroup(sp)
            for r in (0.5, 0.75, 1.0, 1.25, 1.5, 2.0):
                a = sp.item(f"{r:g}×" if r != 1.0 else "Normal", lambda v=r: self.set_rate(v),
                            checkable=True, checked=abs(r - self.rate) < 0.01)
                grp.addAction(a)
            vs = m.sub("Video settings", "video")
            ar = vs.sub("Aspect ratio")
            grp = QActionGroup(ar)
            for label, val in (("Default", None), ("16:9", "16:9"), ("4:3", "4:3"),
                               ("21:9", "21:9"), ("16:10", "16:10"), ("1:1", "1:1")):
                a = ar.item(label, lambda v=val: self.set_aspect(v), checkable=True,
                            checked=self._aspect == val)
                grp.addAction(a)
            zm = vs.sub("Zoom")
            grp = QActionGroup(zm)
            for label, val in (("Fit", "fit"), ("Fill screen (crop)", "fill"), ("125%", 1.25),
                               ("150%", 1.5), ("200%", 2.0)):
                a = zm.item(label, lambda v=val, t=label: self.set_zoom(v, t), checkable=True,
                            checked=self._zoom == val)
                grp.addAction(a)
            self._fill_av(m.sub("Audio and subtitles", "cc"))
            m.addSeparator()
            m.item("Take screenshot", self.take_screenshot, "shot", "S", enabled=has)
            m.item("Always on top", self.toggle_topmost, "mini", checkable=True, checked=self._topmost)
            m.item("Show in folder", self.show_in_folder, "folder", enabled=has)
            m.addSeparator()
            m.item("Set as default player…", self.set_default_player, "check")
        m.ensurePolished()
        size = m.sizeHint()
        if at is not None:
            pos = QPoint(min(at.x(), max(0, self.screen().availableGeometry().right() - size.width())),
                         min(at.y(), max(0, self.screen().availableGeometry().bottom() - size.height())))
        else:
            pos = anchor.mapToGlobal(QPoint(anchor.width() - size.width(), -size.height() - 8))
        m.exec(pos)

    def _flyout_height(self):
        """Tallest a track list may be: about the window height above the control bar, never past the screen."""
        try:
            scr = self.screen().availableGeometry().height() - 60
        except Exception:
            scr = 600
        return max(200, min(self.height() - 70, scr))

    def _fill_av(self, m):
        cap = self._flyout_height()
        sub = TrackMenu("Subtitles", m, self)
        sub.menuAction().setIcon(menu_icon("cc"))
        m.addMenu(sub)
        spus = self.player.video_get_spu_description() or []
        cur = self.player.video_get_spu()
        rows = [(track_label(self._tname(n)), t == cur, (t, n), False) for t, n in spus if t != -1]
        fixed = [("Load subtitle file…", False, None, True)]
        fixed += [(track_label(self._tname(n)), t == cur, (t, n), False) for t, n in spus if t == -1]
        add_track_list(sub, rows, fixed, cap,
                       lambda pl: self.load_subtitle() if pl is None else self._pick_track("spu", pl[0], pl[1]))
        aud = TrackMenu("Audio track", m, self)
        aud.menuAction().setIcon(menu_icon("volume"))
        m.addMenu(aud)
        tracks = self.player.audio_get_track_description() or []
        cur = self.player.audio_get_track()
        add_track_list(aud, [(track_label(self._tname(n)), t == cur, (t, n), False) for t, n in tracks if t != -1],
                       [(track_label(self._tname(n)), t == cur, (t, n), False) for t, n in tracks if t == -1],
                       cap, lambda pl: self._pick_track("audio", pl[0], pl[1]))
        aud.setEnabled(bool(tracks))
        m.addSeparator()
        ad = m.sub(f"Audio sync ({self._adelay:+d} ms)")
        ad.item("Audio earlier (50 ms)", lambda: self.adjust_audio_delay(-50), shortcut="J")
        ad.item("Audio later (50 ms)", lambda: self.adjust_audio_delay(50), shortcut="K")
        ad.item("Reset", lambda: self.adjust_audio_delay(None))
        sd = m.sub(f"Subtitle delay ({self._sdelay:+d} ms)")
        sd.item("Subtitles earlier (50 ms)", lambda: self.adjust_sub_delay(-50), shortcut="G")
        sd.item("Subtitles later (50 ms)", lambda: self.adjust_sub_delay(50), shortcut="H")
        sd.item("Reset", lambda: self.adjust_sub_delay(None))
        sz = m.sub("Subtitle size")
        grp = QActionGroup(sz)
        cur = self.store.data.get("sub_size", "") or "16"
        for label, val in (("Smaller", "20"), ("Small", "18"), ("Normal", "16"),
                           ("Large", "12"), ("Larger", "6")):
            grp.addAction(sz.item(label, lambda v=val: self._set_sub_size(v), checkable=True,
                                  checked=(cur == val)))

    # ---------------------------------------------------------------- engine
    def _make_engine(self):
        args = ["--no-video-title-show", "--quiet", "--no-osd", "--no-snapshot-preview"]
        aout = self.store.data.get("aout", "")
        if aout:
            args.append(f"--aout={aout}")
        size = self.store.data.get("sub_size", "")
        if size:
            args.append(f"--freetype-rel-fontsize={size}")
        if (self.store.dir / "debug.txt").exists():     # diagnostic switch: see the README
            args += ["--file-logging", f"--logfile={self.store.dir / 'vlc.log'}", "--verbose=2"]
        _t = time.monotonic()
        self.instance = vlc.Instance(*args)
        if self.instance is None:
            self._log(f"VLC engine did not start (libVLC: {VLC_SOURCE})")
            raise RuntimeError("VLC's engine did not start. Its 'plugins' folder is probably "
                               f"missing or incomplete.\nlibVLC was loaded from: {VLC_SOURCE}")
        self.player = self.instance.media_player_new()
        self._log(f"libVLC: {VLC_SOURCE}")
        self._log(f"program folder: {_base_dir()} (frozen={bool(getattr(sys, 'frozen', False))})")
        for _pr in _vlc_problems:                       # folders tried first and why they were skipped
            self._log(f"libVLC skipped: {_pr}")
        self._log(f"main VLC engine created, took {(time.monotonic() - _t) * 1000:.0f} ms")
        if (self.store.dir / "debug.txt").exists():
            self._start_vlc_log_stamper()

    def _start_vlc_log_stamper(self):
        """Diagnosis only (debug.txt exists): copies vlc.log into vlc_timed.log with a time stamp per line
        (ms since the last play request, same clock as the T+ lines of player.log). Reads only."""
        if getattr(self, "_stamper_on", False):
            return
        self._stamper_on = True
        src, dst = self.store.dir / "vlc.log", self.store.dir / "vlc_timed.log"

        def run():
            pos, buf, written = 0, b"", 0
            try:
                out = open(dst, "w", encoding="utf-8")
            except Exception:
                return
            with out:
                while not getattr(self, "_closing", False):
                    try:
                        with open(src, "rb") as fh:
                            fh.seek(pos)
                            data = fh.read()
                        pos += len(data)
                    except Exception:
                        data = b""
                    if data:
                        now = time.monotonic()
                        t0 = getattr(self, "_t_click", None)
                        stamp = f"T+{(now - t0) * 1000:7.0f}" if t0 is not None else "T+   idle"
                        buf += data
                        *lines, buf = buf.split(b"\n")
                        for ln in lines:
                            txt = ln.decode("utf-8", "replace").strip("\r\ufeff")
                            if "simple volume changed" in txt or written > 4_000_000:
                                continue
                            row = f"{stamp} {txt}\n"
                            written += len(row)
                            out.write(row)
                        out.flush()
                    time.sleep(0.02)

        threading.Thread(target=run, daemon=True).start()

    def _rebuild_engine(self):
        """Fresh VLC engine (audio output / subtitle size are fixed at start-up), then resume."""
        was_playing = self.player.get_state() == vlc.State.Playing
        self._save_progress()
        cur, qi = self.current, self._replay_index()
        try:
            self.player.stop()
            self.player.release()
            self.instance.release()
        except Exception:
            pass
        self._make_engine()
        self._bind_video()
        self._apply_audio()
        self._pending = None
        if cur and 0 <= qi < len(self.queue):
            self.play_index(qi, resume=True, paused=not was_playing, stay=self._on_home())

    def _set_sub_size(self, val):
        self.store.data["sub_size"] = "" if val == "16" else val
        self.store.save()
        self._rebuild_engine()
        self._sync_settings()
        self.flash("Subtitle size changed")

    # ------------------------------------------------------ new playback tools
    def take_screenshot(self):
        if not self.current or self.stage.home.isVisible():
            return
        folder = self._shot_folder()
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError:
            self.flash("Can't save screenshot")
            return
        stem = Path(self.current).stem[:60]
        stamp = fmt(max(0, self.player.get_time())).replace(":", "-")
        path, n = folder / f"{stem} {stamp}.png", 2
        while path.exists():
            path = folder / f"{stem} {stamp} ({n}).png"
            n += 1
        img = self.stage.video.snapshot_image()          # PHASE 12a: the surface holds the picture (subtitles included), at its display shape
        ok = bool(img is not None and img.save(str(path), "PNG"))
        if not ok:
            ok = self.player.video_take_snapshot(0, str(path), 0, 0) == 0
        self.flash("Screenshot saved" if ok else "Screenshot failed")

    def adjust_audio_delay(self, delta):
        self._adelay = 0 if delta is None else self._adelay + delta
        self.player.audio_set_delay(self._adelay * 1000)
        self.flash(f"Audio sync {self._adelay:+d} ms")

    def adjust_sub_delay(self, delta):
        self._sdelay = 0 if delta is None else self._sdelay + delta
        self.player.video_set_spu_delay(self._sdelay * 1000)
        self.flash(f"Subtitle delay {self._sdelay:+d} ms")

    def set_aspect(self, ratio):
        self._aspect = ratio
        self.stage.video.set_aspect(ratio)               # PHASE 12a: Qt draws the picture, so the aspect is applied there
        self.flash(f"Aspect ratio {ratio or 'Default'}")

    def set_zoom(self, z, label=""):
        self._zoom, self._zoom_done = z, False
        self._apply_zoom()
        self.flash(f"Zoom: {label}" if label else "Zoom")

    def _apply_zoom(self):
        self.stage.video.set_zoom(self._zoom)            # PHASE 12a: drawn by the surface (fit, fill or a factor of fit)
        self._zoom_done = True

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self._zoom != "fit":
            self._zoom_done = False
        if self.mini:
            self._mini_sync()

    def moveEvent(self, e):
        super().moveEvent(e)
        if self.mini:
            self._mini_sync()

    # ------------------------------------------------------------- equalizer
    def _eq_state(self):
        return self.store.data.setdefault(
            "eq", {"enabled": False, "preset": -1, "preamp": 0.0, "bands": [0.0] * 10})

    def _apply_eq(self):
        st = self._eq_state()
        try:
            if st["enabled"]:
                eq = vlc.libvlc_audio_equalizer_new()
                vlc.libvlc_audio_equalizer_set_preamp(eq, float(st["preamp"]))
                for i, b in enumerate(st["bands"][:10]):
                    vlc.libvlc_audio_equalizer_set_amp_at_index(eq, float(b), i)
                vlc.libvlc_media_player_set_equalizer(self.player, eq)
                vlc.libvlc_audio_equalizer_release(eq)       # libVLC keeps its own copy
            else:
                vlc.libvlc_media_player_set_equalizer(self.player, None)
        except Exception:
            pass

    def _preset_values(self, idx):
        eq = vlc.libvlc_audio_equalizer_new_from_preset(idx)
        preamp = vlc.libvlc_audio_equalizer_get_preamp(eq)
        bands = [vlc.libvlc_audio_equalizer_get_amp_at_index(eq, b) for b in range(10)]
        vlc.libvlc_audio_equalizer_release(eq)
        return preamp, bands

    def show_equalizer(self):
        if self._eq_dlg is None:
            try:
                names = [self._tname(vlc.libvlc_audio_equalizer_get_preset_name(i))
                         for i in range(vlc.libvlc_audio_equalizer_get_preset_count())]
                freqs = [vlc.libvlc_audio_equalizer_get_band_frequency(i) for i in range(10)]
            except Exception:
                names, freqs = [], [60, 170, 310, 600, 1000, 3000, 6000, 12000, 14000, 16000]
            self._eq_dlg = EqualizerDialog(self, self._eq_state(), names, freqs, self._preset_values)
            self._eq_dlg.changed.connect(self._on_eq_changed)
        self._eq_dlg.show()
        self._eq_dlg.raise_()
        self._eq_dlg.activateWindow()

    def _on_eq_changed(self):
        self._apply_eq()
        self.store.save()

    # ------------------------------------------------------ windows / dialogs
    def show_queue(self, *_):
        """Ctrl+Q and the menu: open the Play queue page; again: back to where it was opened from.
        From or to the video page it runs behind the curtain."""
        hv = self.stage.home
        crossing = (not hv.isVisible()) or (hv.page == "queue" and self._queue_from_video and self.current)
        if crossing and not self.mini and self.stage.fade_switch(self._show_queue_now):
            return
        self._show_queue_now()

    def _show_queue_now(self):
        hv = self.stage.home
        if hv.isVisible() and hv.page == "queue":
            from_video = self._queue_from_video
            hv.show_page("home")
            if from_video and self.current:
                self.stage.show_home(False)
                self._update_back()
            return
        from_video = not hv.isVisible()
        if self.mini:
            self.toggle_mini()
        if from_video:
            self.show_home()
        hv.show_page("queue")
        self._queue_from_video = from_video

    def _codec_name(self, tr):
        try:
            name = vlc.libvlc_media_get_codec_description(tr.type, tr.codec)
            return self._tname(name)
        except Exception:
            return ""

    def show_properties(self):
        if not self.current:
            return
        p = Path(self.current)
        try:
            st = p.stat()
            size = human_size(st.st_size)
            mod = datetime.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
        except OSError:
            size = mod = "-"
        length = self.player.get_length()
        general = [("Name", p.name), ("Folder", str(p.parent)), ("Size", size), ("Modified", mod),
                   ("Duration", fmt(length) if length > 0 else "-")]
        video, audio, subs = [], [], []
        try:
            w, h = self.player.video_get_size(0)
            if w and h:
                video.append(("Resolution", f"{w} × {h}"))
            fps = self.player.get_fps()
            if fps:
                video.append(("Frame rate", f"{fps:.2f} fps"))
        except Exception:
            pass
        try:
            media = self.player.get_media()
            for tr in media.tracks_get() or []:
                codec = self._codec_name(tr)
                if tr.type == vlc.TrackType.video and codec:
                    video.insert(0, ("Codec", codec))
                elif tr.type == vlc.TrackType.audio and codec:
                    extra = ""
                    try:
                        a = _track_audio_info(tr)
                        if a is not None and a.channels and a.rate:
                            extra = f", {a.channels} ch, {a.rate} Hz"
                    except Exception:
                        pass
                    audio.append(("Format", codec + extra))
        except Exception:
            pass
        for tid, name in self.player.audio_get_track_description() or []:
            if tid != -1:
                audio.append(("Track", self._tname(name)))
        for tid, name in self.player.video_get_spu_description() or []:
            if tid != -1:
                subs.append(("Track", self._tname(name)))
        InfoDialog(self, "Properties", [("File", general), ("Video", video),
                                        ("Audio", audio), ("Subtitles", subs)]).exec()

    def set_default_player(self):
        if sys.platform != "win32":
            self.flash("Only available on Windows")
            return
        try:
            register_default_apps()
        except Exception as exc:
            QMessageBox.warning(self, APP_TITLE, f"Could not register the player:\n{exc}")
            return
        QMessageBox.information(
            self, APP_TITLE,
            "Simple Player is now registered for video files.\n\n"
            "Windows lets only you confirm the choice: in the Settings page that just opened, "
            "pick Simple Player for the video types you want (for example .mp4 and .mkv).")

    # ------------------------------------------------------- single instance
    def _start_server(self):
        self._server = QLocalServer(self)
        QLocalServer.removeServer(SERVER_NAME)
        if self._server.listen(SERVER_NAME):
            self._server.newConnection.connect(self._on_connection)

    def _on_connection(self):
        sock = self._server.nextPendingConnection()
        if not sock:
            return
        sock.waitForReadyRead(500)
        data = bytes(sock.readAll()).decode("utf-8", "ignore")
        sock.disconnectFromServer()
        try:
            files = [f for f in json.loads(data) if isinstance(f, str)]
        except ValueError:
            files = []
        if files:
            self.open_paths(files)
        self.setWindowState((self.windowState() & ~Qt.WindowMinimized) | Qt.WindowActive)
        self.show()
        self.raise_()
        self.activateWindow()

    def _set_aout(self, key):
        self.store.data["aout"] = key
        self.store.save()
        self._rebuild_engine()
        self._sync_settings()
        self.flash("Audio output changed")

    def set_rate(self, r):
        self.rate = r
        self.player.set_rate(r)
        self.flash(f"Speed {r:g}×")

    def show_in_folder(self):
        if self.current and sys.platform == "win32":
            subprocess.Popen(f'explorer /select,"{os.path.normpath(self.current)}"')

    def open_dialog(self):
        files = self._pick_files("Open video")
        if files:
            self.open_paths(files)

    def load_subtitle(self):
        exts = " ".join(f"*{e}" for e in sorted(SUB_EXT))
        f, _ = QFileDialog.getOpenFileName(self, "Open subtitle file", "",
                                           f"Subtitles ({exts});;All files (*.*)")
        if f:
            self._add_subtitle(f)

    def _add_subtitle(self, path):
        self.player.add_slave(vlc.MediaSlaveType.subtitle, Path(path).as_uri(), True)
        self.flash("Subtitles loaded")

    # ------------------------------------------------------------------ misc
    def flash(self, text, accent=False):
        hv = self.stage.home
        if hv.isVisible():
            if hv.page == "queue":                      # the Play queue page shows its own messages
                self.osd.popup(text, hv.queue_page, accent)
            elif hv.page == "settings":
                self.osd.popup(text, hv.settings_page, accent)
            return
        self.osd.popup(text, self.stage.video, accent)

    def _refresh_buttons(self):
        c = self.stage.controls
        c.shuffle.set_active(self.shuffle)
        c.repeat.set_kind("repeat_one" if self.repeat == 2 else "repeat_all")
        c.repeat.set_active(self.repeat > 0)
        c.next.setEnabled(self._has_next())
        c.prev.setEnabled(bool(self.current))
        c.back.setEnabled(bool(self.current))
        c.fwd.setEnabled(bool(self.current))
        self._sync_volume()

    def _watchdog(self):
        """Every ~0.3 s the dump is re-armed. If the UI thread is stuck for 4 s the stack of every thread is written to
        freeze.log (data folder): it shows the exact line where the window froze."""
        self._wd_n += 1
        if self._wd_n % 20 or self._wd_file is None:
            return
        try:
            faulthandler.dump_traceback_later(4.0, repeat=False, file=self._wd_file)
        except Exception:
            self._wd_file = None

    def _tick(self):
        if self._closing:
            return
        self._watchdog()
        self._tick_n += 1
        if self._tick_n % 10 == 0 and sys.platform == "win32":
            self._fit_visible()                    # self-correcting: trims the bar if the visible frame changed unnoticed
        st = self.player.get_state()
        if st != self._log_state:
            self._log_state = st
            self._log(f"state -> {st}")
            self._tm(f"state -> {st}")
        if getattr(self, "_t_click", None) is not None and time.monotonic() - self._t_click < 25:
            self._tick_timing(st)
        if self.stage._loading:
            self._check_loading(st)
        pend = self._pending
        if pend and st != vlc.State.Playing:
            waited = time.monotonic() - pend.get("t0", time.monotonic())
            if pend.get("start") and not pend.get("fallback") and (st == vlc.State.Error or waited > 8.0):
                self._resume_fallback()
            elif (waited > 4.0 and not pend.get("retried")
                    and st not in (vlc.State.Opening, vlc.State.Buffering, vlc.State.Error)):
                pend["retried"] = True
                self._log(f"watchdog: still {st} after 4 s, calling play() again")
                self._bind_video()
                self.player.play()
        if self._pending and st == vlc.State.Playing:
            self._apply_pending()
        if st == vlc.State.Playing:
            self._seen_playing = True
        playing = self._state_playing(st)
        if playing != self._last_state_play:
            self._last_state_play = playing
            keep_awake(playing)
        c = self.stage.controls
        c.play.set_kind("pause" if playing else "play")

        t = self.player.get_time()
        now = time.monotonic()
        if t != self._vt_last:
            self._vt_last, self._vt_stamp = t, now
        est = t
        if st == vlc.State.Playing and t >= 0:
            # libVLC reports time in coarse steps; extrapolate so the bar glides
            est = t + min(0.25, now - self._vt_stamp) * 1000 * self.rate
        since = now - self._wheel_t
        expected = self._wheel_target + (since * 1000 * self.rate if st == vlc.State.Playing else 0)
        if since < 0.6 or (since < 2.0 and abs(t - expected) > 1500):
            # a wheel seek is settling: libVLC may still report the old time, so hold the target
            est = expected
            total = self.player.get_length()
            if total > 0:
                est = min(est, total)
            self._hold_last = est
        elif since < 3.0 and st == vlc.State.Playing and est >= 0:
            est = max(est, self._hold_last)          # never step back right after a wheel seek
        if est >= 0 and not c.seek.dragging():
            _len = self.player.get_length()
            if _len > 0:
                c.seek.setMaximum(_len)                  # before the value: a stale maximum clamped the dot to the left for a moment
            c.seek.setValue(est)
            if getattr(self, "_dbg_seek", 0) > 0:
                self._dbg_seek -= 1
                self._log(f"seek dbg: t={t} est={int(est)} dot={c.seek._val} max={c.seek._max}")
            if self._tick_n % 4 == 0:
                txt = fmt(est)
                if txt != self._lbl_cur:
                    self._lbl_cur = txt
                    c.t_cur.setText(txt)
                self._show_remaining(est)

        if self._tick_n % 6 == 0:
            length = self.player.get_length()
            if self.current and length > 0:
                self._durations.setdefault(Store._key(self.current), length)
            if (self.current and length > 0 and t >= 0 and st == vlc.State.Playing
                    and not self._pending and t >= length * self.COMPLETE_FRACTION):
                self._complete_current()
            if length > 0:
                c.seek.setMaximum(length)
                if not c.seek.dragging():
                    self._show_remaining(est if est >= 0 else t)
            if st == vlc.State.Ended and not self._ended and self._seen_playing:
                self._on_end()
            elif st == vlc.State.Error and self.current and not self._ended:
                self._ended = True
                self.flash("Can't play this file")
            if self._zoom != "fit" and not self._zoom_done and st == vlc.State.Playing:
                self._apply_zoom()
            if (self.current and self._thumb_for != self.current and st == vlc.State.Playing
                    and t > 4000 and not self._pending):
                self._thumb_for = self.current
                if not self.store.thumb_path(self.current, self._size(self.current)).exists():
                    self._snapshot_async()
            self._refresh_nav()

    def _check_loading(self, st):
        """Loading page: ends when the first picture is on screen (or a sound plays), on an error, or after too long."""
        waited = time.monotonic() - self.stage._load_t0
        try:
            if st in (vlc.State.Playing, vlc.State.Paused) and not self._pending:
                # "Playing" and a video size come BEFORE the first picture (real libVLC: about 30 ms vs 500 ms, much more after a
                # cold start). Ready means the start work is done and the time is really running at the resume position.
                w, h = self.player.video_get_size(0)
                t = self.player.get_time()
                target = self.stage._load_target
                if self.stage._load_seen is None and w and h:
                    self.stage._load_seen = time.monotonic()
                time_ok = t > 0 and (target <= 0 or t >= target - 5000)
                late = self.stage._load_seen is not None and time.monotonic() - self.stage._load_seen > 6.0 and t > 0
                if (time_ok and ((w and h) or t >= max(target, 0) + 700)) or late:
                    if self.stage._load_ready is None:
                        self.stage._load_ready = time.monotonic()
                        self.stage._load_frames0 = self.stage.video.frames
                    if (w and h) and self.stage.video.has_frame():
                        # PHASE 12b: the real signal. The time is right, so the first picture DELIVERED after this point is the one to show.
                        if self.stage.video.frames <= (self.stage._load_frames0 or 0) and time.monotonic() - self.stage._load_ready < 1.0:
                            return                   # (cap 1 s: a paused start must never wait for a frame that does not come)
                    elif time.monotonic() - self.stage._load_ready < LOAD_SETTLE_S:
                        return                       # sound only (or no picture yet): the old settle time
                    self.stage.end_loading()
                    self._dbg_seek = 14
                    self._log(f"loading page ended after {waited * 1000:.0f} ms (time {t} ms, target {target} ms)")
                    return
            if st == vlc.State.Error or (st in (vlc.State.Stopped, vlc.State.Ended, vlc.State.NothingSpecial) and waited > 3.0):
                self.stage.end_loading()
                self.flash("Can't play this file. See player.log")
                self._log(f"loading page ended: state {st} after {waited:.1f} s")
            elif waited > LOAD_GIVE_UP_S:
                self.stage.end_loading()
                self.flash("This video is taking a long time to start")
                self._log(f"loading page gave up after {waited:.0f} s")
        except Exception as ex:
            self.stage.end_loading()
            self._log(f"loading check failed: {type(ex).__name__}: {ex}")

    def _tick_timing(self, st):
        """Milestones after a play request (slow-start diagnosis). Reads only, changes nothing."""
        now = time.monotonic()
        last = getattr(self, "_tm_last_tick", None)
        self._tm_last_tick = now
        if last is not None and now - last > 0.25:
            self._tm(f"UI STALL: the UI thread was blocked for {(now - last) * 1000:.0f} ms")
        fl = self._tm_flags
        def once(key, label):
            if key not in fl:
                fl.add(key)
                self._tm(label)
        try:
            if st == vlc.State.Playing:
                once("playing", "first tick in state Playing")
                self._starting_until = 0.0
                t = self.player.get_time()
                if t >= 0:
                    once("time", f"first time value t={t} ms")
                if t > 0:
                    once("moving", f"time > 0 (t={t} ms)")
                if self.player.is_seekable():
                    once("seek", "seekable")
                if self.player.get_length() > 0:
                    once("len", f"length known ({self.player.get_length()} ms)")
                w, h = self.player.video_get_size(0)
                if w and h:
                    once("video", f"video output has a size {w}x{h} (first picture)")
                    if "slotcrop" not in self._tm_flags:
                        self._tm_flags.add("slotcrop")
                        QTimer.singleShot(400, self._slot_crop)
            if not self._pending:
                once("pend", "pending start work finished")
        except Exception:
            pass

    def _refresh_nav(self):
        self._update_back()
        c = self.stage.controls
        want = self._has_next()
        if c.next.isEnabled() != want:
            c.next.setEnabled(want)
        for b in (c.prev, c.back, c.fwd):
            if b.isEnabled() != bool(self.current):
                b.setEnabled(bool(self.current))

    def _idle_tick(self):
        if self._closing:
            return
        pos = QCursor.pos()
        moved = pos != self._last_pos
        if moved:
            self._last_pos = pos
            self._last_move = time.monotonic()
        self.titlebar.set_side(self.stage.home.isVisible())
        self._sync_titlebar()
        if self.mini:
            return                                      # the mini player drives its title bar itself (_mini_tick)
        if not self.stage._ov():
            self._bars_visible(True)                    # Home and other pages: bars always shown
            return
        inside = self.frameGeometry().contains(pos)
        popup_open = QApplication.activePopupWidget() is not None
        playing = self.player.get_state() == vlc.State.Playing
        idle = time.monotonic() - self._last_move > self.hide_after
        keep = (not playing) and (self.show_when_paused or self.player.get_state() != vlc.State.Paused)
        if self.stage.home.isVisible() or keep or popup_open or (inside and moved):
            self._bars_visible(True)
            self._cursor_hidden(False)
        elif idle or not inside:
            local = self.stage.mapFromGlobal(pos)
            over = (self.stage.controls.isVisible() and self.stage.controls.geometry().contains(local)) \
                or (self.titlebar.isVisible() and self.titlebar.rect().contains(self.titlebar.mapFromGlobal(pos)))
            if not over:
                self._bars_visible(False)
                self._cursor_hidden(True)

    def _bars_visible(self, show, instant=False):
        """Controls and the title bar fade away together after a few idle seconds (video page only).
        On Home the title bar is a normal, opaque bar above the page."""
        self.stage.show_controls(show)
        if self.isFullScreen():
            return
        if self.stage.home.isVisible():
            self._tb_tw.set(1.0)
            self._tb_show = True
            self._tb_apply()
            return
        if self._tb_show == show and not instant:
            return
        self._tb_show = show
        if instant:
            self._tb_tw.set(1.0 if show else 0.0)
        else:
            self._tb_tw.to(1.0 if show else 0.0, BAR_FADE_IN if show else BAR_FADE_OUT, EASE_INOUT)
        self._tb_apply()

    def _tb_step(self):
        self._tb_apply()

    def _tb_apply(self):
        """Opacity and visibility of the title bar: 55 % on the video page (fading in and out), 100 % on Home."""
        if self.isFullScreen():
            return
        overlay = not self.stage.home.isVisible()
        v = max(0.0, min(1.0, self._tb_tw.value)) if overlay else 1.0
        vis = v > 0.01
        y = -int(round(BAR_SLIDE * (1.0 - v))) if overlay else 0         # drifts up a little while it fades
        if self.titlebar.y() != y:
            self.titlebar.move(self.titlebar.x(), y)
        if self.titlebar.isHidden() == vis:
            self.titlebar.setVisible(vis)
            if vis:
                self.titlebar.raise_()
        if vis:
            opaque = (not overlay) or self.mini or self.stage._opaque_file is not None and self.stage._opaque_file.exists()
            base = 255 if opaque else CONTROLS_ALPHA
            self._tb_alpha = layered_alpha_apply(self.titlebar, int(round(base * v)), self._tb_alpha)

    def _tb_reset(self):
        self._tb_tw.set(1.0)
        self._tb_show = True
        self._tb_apply()

    def _place_titlebar(self):
        r = self._overhang[0]
        self.titlebar.setGeometry(0, self.titlebar.y() if not self.stage.home.isVisible() else 0,
                                  max(0, self.shell.width() - r), self.titlebar.height())

    def _titlebar_on_top(self):
        if self.titlebar.isVisible():
            self.titlebar.raise_()

    def _apply_margins(self):
        """The Stage always covers the whole window. Home and the other pages keep the title bar's room at the top inside it (Stage.home_off), the video page uses it all."""
        r, b = self._overhang
        off = 0 if self.isFullScreen() else TITLEBAR_H
        if (r, b, off) != self._margins:
            self._margins = (r, b, off)
            self._shell_lay.setContentsMargins(0, 0, r, b)   # the Stage always covers the whole window: it never moves between pages
            self.stage.home_off = off                        # Home keeps the title bar's room inside it (the title bar covers that strip)
            self.stage.relayout()
        self._place_titlebar()

    def _video_picture_rect(self):
        """Where the picture (without the black bars) sits in the video surface, in its own coordinates. None when it cannot be
        told (no picture yet, the preview slot crop is on, or the surface is tiny): then nothing is cut."""
        try:
            sv = self.stage.video
            if self._slot_cropped or sv.slot_mode or sv.width() < 50 or sv.height() < 50:
                return None
            return sv.picture_rect()
        except Exception:
            return None

    def _sync_titlebar(self):
        self._apply_margins()
        self._tb_apply()

    # ---------------------------------------------------------------- events
    def _gesture_move(self, ev):
        g = self._g
        pos = ev.position()
        dx, dy = pos.x() - g["p"].x(), pos.y() - g["p"].y()
        vw, vh = max(1, self.stage.video.width()), max(1, self.stage.video.height())
        if g["mode"] is None:
            if max(abs(dx), abs(dy)) < 12:
                return
            if abs(dx) > abs(dy) * 1.2:
                g["mode"] = "h"
            elif g["p"].x() > vw / 2:
                g["mode"] = "v"
            else:
                g["mode"] = "x"
            self._click_timer.stop()
        if g["mode"] == "h":                       # drag sideways to scrub
            length = self.player.get_length()
            if length <= 0 or g["t0"] < 0:
                return
            span = min(120000, length)
            target = max(0, min(length - 500, g["t0"] + dx / vw * span))
            g["target"] = target
            delta = int((target - g["t0"]) / 1000)
            sign = "+" if delta >= 0 else "−"
            self.flash(f"{fmt(target)}   {sign}{abs(delta)}s")
            now = time.monotonic()
            if self.player.get_state() == vlc.State.Paused and now - self._last_live > 0.1:
                self._last_live = now
                self.player.set_time(int(target))
        elif g["mode"] == "v":                     # drag up/down on the right half for volume
            self.set_volume(round(g["v0"] - dy / (vh * 0.7) * 100))

    def eventFilter(self, obj, ev):
        if obj is self.shell and ev.type() == QEvent.Resize:
            self._place_titlebar()
            return False
        if obj is not self.stage.video and obj is not self.stage:
            return super().eventFilter(obj, ev)
        t = ev.type()
        if t == QEvent.Wheel:
            if self.stage.home.isVisible():
                return False
            dx, dy = ev.angleDelta().x(), ev.angleDelta().y()
            if abs(dx) > abs(dy):                  # two-finger swipe sideways = seek
                now = time.monotonic()
                if now - self._last_wheel > 0.08:
                    self._last_wheel = now
                    self.seek_rel(5000 if dx > 0 else -5000)
            elif dy:
                self.step_volume(1 if dy > 0 else -1)
            return True
        if (t == QEvent.MouseButtonRelease and ev.button() == Qt.RightButton
                and not self.stage.home.isVisible()):
            self.show_menu(at=ev.globalPosition().toPoint())
            return True
        if obj is not self.stage.video:
            return False
        if self.mini:                                    # clicks and dragging are handled by _mini_tick
            return t in (QEvent.MouseButtonPress, QEvent.MouseButtonRelease, QEvent.MouseButtonDblClick,
                         QEvent.MouseMove)
        if self.stage.home.isVisible():                  # the video surface is the mini-player now
            if t == QEvent.MouseButtonRelease and ev.button() == Qt.LeftButton and not self._cursor_on_x():
                self._open_video_page()              # (a click on the X belongs to the X, see _block_poll)
            return t in (QEvent.MouseButtonPress, QEvent.MouseButtonRelease, QEvent.MouseButtonDblClick,
                         QEvent.MouseMove)
        if t == QEvent.MouseButtonPress and ev.button() == Qt.LeftButton:
            self._g = {"p": ev.position(), "mode": None, "t0": self.player.get_time(),
                       "v0": self.volume, "target": None}
            return False
        if t == QEvent.MouseMove and self._g and ev.buttons() & Qt.LeftButton:
            self._gesture_move(ev)
            return self._g["mode"] in ("h", "v")
        if t == QEvent.MouseButtonDblClick and ev.button() == Qt.LeftButton:
            self._g = None
            self._click_timer.stop()
            self._skip_release = True
            self.toggle_fullscreen()
            return True
        if t == QEvent.MouseButtonRelease and ev.button() == Qt.LeftButton:
            g, self._g = self._g, None
            if g and g["mode"] is not None:
                if g["mode"] == "h" and g["target"] is not None:
                    self.seek_to(g["target"])
                return True
            if self._skip_release:
                self._skip_release = False
            else:
                self._click_timer.start()
            return False
        return False

    def wheelEvent(self, e):
        if self.stage.home.isVisible():
            e.ignore()
            return
        d = e.angleDelta().y()
        if d:
            self.step_volume(1 if d > 0 else -1)

    def _undo_key(self):
        """Ctrl+Z: only on the Home page, and not while a text box has the focus (it has its own undo)."""
        fw = QApplication.focusWidget()
        if isinstance(fw, QLineEdit):
            return
        hv = self.stage.home
        if hv.isVisible() and hv.page == "home":
            self._undo_remove()

    def keyPressEvent(self, e):
        k, mods = e.key(), e.modifiers()
        ctrl = bool(mods & Qt.ControlModifier)
        if ctrl and k == Qt.Key_I:
            self.show_properties()
        elif ctrl and k == Qt.Key_Q:
            self.show_queue()
        elif k == Qt.Key_F2 and not ctrl:
            self._rename_selected()
        elif ctrl and k == Qt.Key_Z and not (mods & Qt.ShiftModifier):
            self._undo_key()
        elif ctrl and (mods & Qt.ShiftModifier) and k == Qt.Key_E:
            self.show_equalizer()
        elif k == Qt.Key_Space:
            self.toggle_play()
        elif k == Qt.Key_S and not ctrl:
            self.take_screenshot()
        elif k == Qt.Key_J:
            self.adjust_audio_delay(-50)
        elif k == Qt.Key_K:
            self.adjust_audio_delay(50)
        elif k == Qt.Key_G:
            self.adjust_sub_delay(-50)
        elif k == Qt.Key_H:
            self.adjust_sub_delay(50)
        elif k == Qt.Key_Left and (mods & Qt.AltModifier):
            self.go_back()
        elif k == Qt.Key_Left:
            self.seek_rel(-self.rewind_s * 1000)
        elif k == Qt.Key_Right:
            self.seek_rel(self.ff_s * 1000)
        elif k == Qt.Key_Up:
            self.step_volume(1)
        elif k == Qt.Key_Down:
            self.step_volume(-1)
        elif k in (Qt.Key_F, Qt.Key_Return, Qt.Key_Enter):
            self.toggle_fullscreen()
        elif k == Qt.Key_Escape:
            if self.stage.home.isVisible() and self._home_pg().sel_mode:
                self._home_pg().clear_selection()    # first Esc leaves selection mode
            elif self.stage.home.isVisible() and (self.current or self.stage.home.page != "home"):
                self.go_back()                       # one step back (page -> Home -> video)
            elif self.isFullScreen():
                self.toggle_fullscreen()
            elif self.mini:
                self.toggle_mini()
        elif k == Qt.Key_M:
            self.toggle_mute()
        elif k == Qt.Key_N:
            self.next_item()
        elif k == Qt.Key_P:
            self.prev_item()
        elif k == Qt.Key_O and mods & Qt.ControlModifier:
            self.open_dialog()
        elif k == Qt.Key_BracketLeft:
            self.set_rate(max(0.25, round(self.rate - 0.25, 2)))
        elif k == Qt.Key_BracketRight:
            self.set_rate(min(4.0, round(self.rate + 0.25, 2)))
        else:
            super().keyPressEvent(e)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        files = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        dirs = [f for f in files if os.path.isdir(f)]
        subs = [f for f in files if Path(f).suffix.lower() in SUB_EXT]
        vids = [f for f in files if f not in subs and f not in dirs]
        if dirs and not vids:
            self.open_folder(dirs[0])
        elif vids:
            self.open_paths(vids)
        for s in subs:
            self._add_subtitle(s)

    def closeEvent(self, e):
        self._closing = True
        for t in (self.timer, self.idle, self.saver, self.mini_timer, self._click_timer):
            t.stop()                           # the clocks must not ask libVLC anything once the player is released
        try:
            faulthandler.cancel_dump_traceback_later()
        except Exception:
            pass
        self.thumbs.stop()
        self.slotx.hide()
        self.nametip.hide()
        self.mini_ov.hide()
        self._save_progress()
        snap = self._snapshot()
        if snap:                               # let libVLC write the frame before we shut down
            time.sleep(0.25)
            self._finish_snapshot(*snap)
        if not self.isFullScreen() and not self.mini:
            self.store.data["geometry"] = bytes(self.saveGeometry().toHex()).decode()
            self.store.data["geometry_v"] = 2
        elif self._normal_geo is not None:
            self.store.data["geometry"] = bytes(QByteArray(self._normal_geo).toHex()).decode()
            self.store.data["geometry_v"] = 2
        self.store.save()
        keep_awake(False)
        try:
            self.player.stop()
            self.player.release()
            self.instance.release()
        except Exception:
            pass
        super().closeEvent(e)


# --------------------------------------------------------------------------
STYLE = """
QMenu{background:#2b2b2b;color:#f2f2f2;border:1px solid #3d3d3d;border-radius:10px;padding:2px;font-size:14px}
QMenu::item{min-height:30px;padding:0px 30px 0px 12px;margin:1px 2px;border-radius:6px}
QMenu::item:selected{background:#3a3a3a}
QMenu::item:disabled{color:#777}
QMenu::icon{padding-left:6px}
QMenu::separator{height:1px;background:#3d3d3d;margin:1px 10px}
QDialog,QMessageBox{background:#202020;color:#fff}
QLabel{color:#fff}
QPushButton{background:#333;color:#fff;border:1px solid #444;border-radius:8px;padding:7px 18px;font-size:13px}
QPushButton:hover{background:#3f3f3f}
QPushButton:pressed{background:#2a2a2a}
QListWidget{background:#1a1a1a;color:#fff;border:1px solid #333;border-radius:8px;font-size:13px;outline:0}
QListWidget::item{padding:8px 10px;border-radius:6px}
QListWidget::item:hover{background:#2a2a2a}
QListWidget::item:selected{background:#333;color:#ff8a2a}
QCheckBox{color:#fff;spacing:8px;font-size:13px}
QComboBox{background:#2b2b2b;color:#fff;border:1px solid #444;border-radius:8px;padding:6px 10px;min-width:150px}
QComboBox QAbstractItemView{background:#2b2b2b;color:#fff;selection-background-color:#3d3d3d;border:1px solid #444;outline:0}
QSlider::groove:vertical{background:#4a4a4a;width:4px;border-radius:2px}
QSlider::handle:vertical{background:#ff8a2a;border:3px solid #5a5a5a;height:10px;width:10px;margin:0 -8px;border-radius:8px}
QToolTip{background:#2b2b2b;color:#fff;border:1px solid #3d3d3d;padding:4px 8px}
"""


def main():
    if sys.platform == "win32":
        try:   # own taskbar identity, so the taskbar shows our icon instead of python.exe's
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("SimplePlayer.App")
        except Exception:
            pass
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)
    app.setStyle(AppStyle("Fusion"))
    app.setFont(ui_font(13))
    app.setStyleSheet(STYLE)
    app.setWindowIcon(make_app_icon())
    if vlc is None:
        QMessageBox.critical(
            None, APP_TITLE,
            "VLC's playback engine (libVLC) could not be loaded.\n\n"
            f"{VLC_ERROR}\n\n"
            "Install the 64-bit VLC from videolan.org, or place libvlc.dll, libvlccore.dll "
            "and the 'plugins' folder in a 'vlc' folder next to this program.")
        return 1
    if forward_to_running_instance([os.path.abspath(f) for f in sys.argv[1:]]):
        return 0                       # already open: the running window takes the file
    try:
        win = MainWindow(sys.argv[1:])
    except Exception as exc:
        QMessageBox.critical(None, APP_TITLE, f"Simple Player could not start.\n\n{exc}")
        return 1
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
