"""
Hypixel SkyBlock Crafting Tree Tracker

Features
--------
* Downloads the current SkyBlock item catalogue from Hypixel.
* Tracks owned quantities through a recursive recipe tree.
* Uses a friendly visual recipe editor instead of requiring recipe syntax.
* Lets you define a recipe directly from a missing-recipe item.
* Searches SkyBlock items by name or ID.
* Saves progress, root item, and local recipes automatically.
* Dark theme throughout.
* Completing a crafted item also credits everything that goes into it,
  all the way down to the raw materials.
* Click the ＋ in any row (or the ＋ 1 button in the side panel) to add one.
* Inventory is tracked per tree position: if an ingredient (e.g. Golden
  Plate) is needed in two different places, each occurrence keeps its own
  owned count instead of sharing one number.

Hypixel's public SkyBlock item resource provides item identity/metadata, but
not a complete recipe graph, so recipes are kept locally and can be edited in
this application. See the official resource announcement:
https://hypixel.net/threads/publicapi-new-resource-skyblock-items.4567714/

Requires Python 3.9+ and Tkinter. No third-party packages are required.
"""

from __future__ import annotations

import base64
import gzip
import os
import struct
import hashlib
import json
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile
from collections import defaultdict
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox


API_URL = "https://api.hypixel.net/v2/resources/skyblock/items"
def _resolve_data_file() -> Path:
    """Where progress is saved.

    Running as a script: next to the script (unchanged behaviour).
    Running as a packaged .exe: in %APPDATA%\\SkyBlockTracker, because the exe's
    own folder is temporary (one-file builds) or may not be writable.
    If a data file already sits next to the exe, it is copied over once.
    """
    name = "skyblock_tracker_data.json"
    if not getattr(sys, "frozen", False):
        return Path(__file__).with_name(name)
    import os
    import shutil

    base = Path(os.environ.get("APPDATA") or Path.home()) / "SkyBlockTracker"
    base.mkdir(parents=True, exist_ok=True)
    target = base / name
    beside_exe = Path(sys.executable).with_name(name)
    if not target.exists() and beside_exe.exists():
        try:
            shutil.copy2(beside_exe, target)
        except OSError:
            pass
    return target


DATA_FILE = _resolve_data_file()


# ---------------------------------------------------------------------------
# Theme
# ---------------------------------------------------------------------------
BG = "#0b1017"             # window background
SURFACE = "#141b26"        # cards / panels / tree
SURFACE_ALT = "#1c2636"    # buttons, entries, headings
SURFACE_HOVER = "#263248"
BORDER = "#263043"
BORDER_STRONG = "#3a4963"
HEADER_BG = "#080c12"
TEXT = "#e6e9ef"
TEXT_STRONG = "#ffffff"
MUTED = "#8593aa"
ACCENT = "#4c8dff"
ACCENT_HOVER = "#3b7bf0"
ACCENT_PRESSED = "#2f66cf"
SELECT_BG = "#29456b"
TREE_ALT_BG = "#182331"          # quiet stripes to help follow long recipe branches
TREE_DONE_BG = "#195138"         # full-width completion highlight
TREE_DONE_SELECTED_BG = "#26734e" # selected rows stay green
TREE_DONE_FG = "#e1ffed"
GREEN = "#4ade80"
AMBER = "#fbbf24"
RED = "#f87171"
VIOLET = "#a78bfa"

FONT = "Segoe UI"
PLUS = "＋"              # the click-to-add glyph shown in the tree's last column

UI_SCALE = 1.0           # set from the real screen DPI when the window opens


def px(value) -> int:
    """Scale a pixel measurement for the current display DPI."""
    return max(1, int(round(value * UI_SCALE)))


def prepare_windows_process() -> None:
    """Crisp text on high-DPI screens + a proper taskbar identity (Windows only)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("SkyBlockTracker.App")
    except Exception:
        pass


def enable_dark_title_bar(window) -> None:
    """Ask Windows 10/11 for a dark title bar. Silently does nothing elsewhere."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        window.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        value = ctypes.c_int(1)
        # 20 = DWMWA_USE_IMMERSIVE_DARK_MODE (19 on early Windows 10 builds)
        for attribute in (20, 19):
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value)) == 0:
                break
    except Exception:
        pass


def set_window_icon(window) -> None:
    """Use myicon.ico (next to the script, or bundled in the exe) as the window icon."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    icon = base / "myicon.ico"
    if icon.exists():
        try:
            window.iconbitmap(default=str(icon))
        except tk.TclError:
            pass


def center_window(window, width, height, parent=None) -> None:
    """Size a window (clamped to the screen) and centre it on the screen or its parent."""
    window.update_idletasks()
    screen_w, screen_h = window.winfo_screenwidth(), window.winfo_screenheight()
    width = min(width, screen_w - px(40))
    height = min(height, screen_h - px(100))
    if parent is not None:
        x = parent.winfo_rootx() + (parent.winfo_width() - width) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - height) // 2
    else:
        x = (screen_w - width) // 2
        y = (screen_h - height) // 2 - px(20)
    x = max(0, min(x, screen_w - width))
    y = max(0, min(y, screen_h - height - px(40)))
    window.geometry(f"{width}x{height}+{x}+{y}")


class Card(tk.Frame):
    """Flat panel with a thin border and an optional coloured top strip.

    Put child widgets in ``card.body`` (a padded ttk frame).
    """

    def __init__(self, parent, padding=12, accent=None, **kwargs):
        super().__init__(
            parent,
            background=SURFACE,
            highlightbackground=BORDER,
            highlightcolor=BORDER,
            highlightthickness=1,
            borderwidth=0,
            **kwargs,
        )
        if accent:
            tk.Frame(self, height=px(3), background=accent, borderwidth=0).pack(side="top", fill="x")
        self.body = ttk.Frame(self, style="Card.TFrame", padding=padding)
        self.body.pack(side="top", fill="both", expand=True)


def _round_rect(canvas, x1, y1, x2, y2, radius, **kwargs):
    points = [
        x1 + radius, y1, x2 - radius, y1, x2, y1, x2, y1 + radius,
        x2, y2 - radius, x2, y2, x2 - radius, y2, x1 + radius, y2,
        x1, y2, x1, y2 - radius, x1, y1 + radius, x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, **kwargs)


def draw_logo(canvas, size) -> None:
    """Draw the little crafting-tree logo used in the header."""
    s = size / 100.0
    canvas.delete("all")
    _round_rect(canvas, 2 * s, 2 * s, 98 * s, 98 * s, 20 * s, fill=SURFACE, outline=BORDER_STRONG)
    root, mid_l, mid_r = (50, 26), (30, 54), (70, 54)
    leaves = [(18, 80), (42, 80), (58, 80), (82, 80)]
    width = max(2, int(3 * s))

    def link(a, b, color):
        canvas.create_line(a[0] * s, a[1] * s, b[0] * s, b[1] * s, fill=color, width=width)

    link(root, mid_l, ACCENT)
    link(root, mid_r, ACCENT)
    link(mid_l, leaves[0], GREEN)
    link(mid_l, leaves[1], GREEN)
    link(mid_r, leaves[2], AMBER)
    link(mid_r, leaves[3], AMBER)

    def dot(center, radius, color):
        x, y = center
        canvas.create_oval((x - radius) * s, (y - radius) * s, (x + radius) * s, (y + radius) * s, fill=color, outline="")

    dot(root, 11, ACCENT)
    dot(mid_l, 8, ACCENT)
    dot(mid_r, 8, ACCENT)
    for leaf in leaves[:2]:
        dot(leaf, 6, GREEN)
    for leaf in leaves[2:]:
        dot(leaf, 6, AMBER)


# ---------------------------------------------------------------------------
# Item rarity colours
# ---------------------------------------------------------------------------
# Close to the in-game colours, with the darker ones (blue / purple) lightened
# a little so they stay readable on the dark theme.
RARITY_COLORS = {
    "COMMON": "#ffffff",
    "UNCOMMON": "#55ff55",
    "RARE": "#6f8dff",
    "EPIC": "#b65cff",
    "LEGENDARY": "#ffaa00",
    "MYTHIC": "#ff55ff",
    "DIVINE": "#55ffff",
    "SPECIAL": "#ff5555",
    "VERY_SPECIAL": "#ff4444",
    "ULTIMATE": "#ff4444",
    "ADMIN": "#ff4444",
    "UNOBTAINABLE": "#ff5555",
    "UNKNOWN": TEXT,
}


def blend(foreground: str, background: str, weight: float) -> str:
    """Mix two #rrggbb colours; `weight` is how much of `foreground` to keep."""
    f = [int(foreground[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(background[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(round(f[k] * weight + b[k] * (1 - weight)) for k in range(3))


def configure_rarity_tags(tree) -> None:
    """Row-wide state colors: rarity when needed, green when completed.

    A selected completed item remains green instead of being painted blue.
    The normal Treeview selection state is overridden below by our per-row tags.
    """
    for tier, color in RARITY_COLORS.items():
        tree.tag_configure(f"rar_{tier}", foreground=color, background=SURFACE)
        tree.tag_configure(f"rar_{tier}_alt", foreground=color, background=TREE_ALT_BG)
        tree.tag_configure(f"rar_{tier}_selected", foreground=TEXT_STRONG, background=SELECT_BG)
        tree.tag_configure(
            f"rar_{tier}_done", foreground=TREE_DONE_FG, background=TREE_DONE_BG,
            font=(FONT, 10, "bold"),
        )
        tree.tag_configure(
            f"rar_{tier}_done_selected", foreground="#ffffff", background=TREE_DONE_SELECTED_BG,
            font=(FONT, 10, "bold"),
        )


# ---------------------------------------------------------------------------
# Item icons: FurfSky Reborn first, vanilla Minecraft textures as a fallback
# ---------------------------------------------------------------------------
class IconStore:
    """Finds a small icon for every item and caches it on disk.

    1. FurfSky Reborn: download the release ZIP, unpack its Catharsis CATS
       container (if present), and resolve SkyBlock item definitions/models.
       Downloads are re-checked about once a week.
    2. Vanilla Minecraft (1.8.8) textures, looked up from the item's material
       as reported by the Hypixel API, downloaded one file at a time.
    3. A neutral placeholder square if neither has the item.

    Nothing here blocks the window: downloads run on one background thread and
    `on_change` is called when new icons are ready.
    """

    API_URL = "https://api.github.com/repos/furfsky/reborn/releases/latest"
    VANILLA_URL = "https://raw.githubusercontent.com/rom1504/minecraft-assets/master/data/1.8.8/{}.png"
    USER_AGENT = "SkyBlock-Crafting-Tree-Tracker/2.0"
    RECHECK_SECONDS = 7 * 24 * 3600
    SKIP_PATHS = (
        "/gui/", "/entity/", "/environment/", "/particle/", "/font/", "/colormap/",
        "/painting/", "/mob_effect/", "/models/", "/misc/", "/map/", "/trims/", "/armor/",
    )
    # Legacy material names whose 1.8.8 texture file is named differently.
    VANILLA_ALIASES = {
        "REDSTONE": "redstone_dust", "SULPHUR": "gunpowder", "NETHER_STALK": "nether_wart",
        "CARROT_ITEM": "carrot", "POTATO_ITEM": "potato", "RAW_FISH": "fish_cod_raw",
        "COOKED_FISH": "fish_cod_cooked", "SEEDS": "seeds_wheat", "SNOW_BALL": "snowball",
        "EYE_OF_ENDER": "ender_eye", "EXP_BOTTLE": "experience_bottle", "FIREBALL": "fireball",
        "BOOK_AND_QUILL": "book_writable", "MELON": "melon", "GOLD_RECORD": "record_13",
    }
    # Materials whose look depends on damage values / NBT we do not have.
    VANILLA_SKIP = {"SKULL_ITEM", "INK_SACK", "MONSTER_EGG", "POTION", "BANNER", "WOOL", "LOG", "LOG_2", "LEAVES"}

    def __init__(self, root, cache_dir, material_of, on_change, on_note):
        self.root = root
        self.dir = Path(cache_dir)
        self.material_of = material_of
        self.on_change = on_change
        self.on_note = on_note
        self._lock = threading.Lock()
        self._zips = []
        self._index = {}
        self._index_done = False
        self._member_cache = {}
        self._models = {}
        self._model_by_stem = {}
        self._definitions = {}
        self._all_pngs = {}
        self._resolved = {}
        self._digest_users = {}
        self._bad_digests = set()
        self._keepalive = []
        self._report_info = []
        self._bytes = {}
        self._photos = {}
        self._placeholders = {}
        self._bad = set()
        self._missing = set()
        self._queue = []
        self._queued = set()
        self._running = True
        threading.Thread(target=self._run, daemon=True).start()

    # ---------------------------- public ---------------------------------

    def get(self, item_id, big=False, network=True):
        """Return a PhotoImage right now (a placeholder until the real one is ready)."""
        key = (item_id, big)
        photo = self._photos.get(key)
        if photo is not None:
            return photo
        data = self._lookup(item_id)
        if data:
            photo = self._make_photo(data, big)
            if photo is not None:
                self._photos[key] = photo
                return photo
            self._bad.add(item_id)
            self._bytes.pop(item_id, None)
        elif network and self._index_done and item_id not in self._missing:
            self._enqueue(item_id)
        elif network and not self._index_done:
            self._enqueue(item_id)
        return self._placeholder(big)

    # --------------------------- lookups ----------------------------------

    def _lookup(self, item_id):
        if item_id in self._bytes:
            return self._bytes[item_id]
        if not self._index_done:
            return None
        data = self._from_furfsky(item_id)
        if data is not None:
            data = self._check_shared(item_id, data)
        if data is None:
            data = self._from_vanilla_disk(item_id)
        if data is not None:
            self._bytes[item_id] = data
        return data

    def _check_shared(self, item_id, data):
        """Accept explicitly mapped textures even when several IDs share a sprite.

        Shared PNG bytes are common for legitimate item variants. Formerly the
        fourth use of a texture invalidated earlier icons and could leave the
        interface showing generic placeholders. A PAPER material is now handled
        by _vanilla_candidates instead of by rejecting shared pack images.
        """
        return data

    def _furfsky_ok(self, item_id):
        data = self._from_furfsky(item_id)
        return data is not None and hashlib.md5(data).hexdigest() not in self._bad_digests

    def _from_furfsky(self, item_id):
        if item_id in self._bad:
            return None
        entry, how = self._resolve_member(item_id.lower())
        if entry is None:
            return None
        self._resolved[item_id] = (how, entry[1])
        return self._read(entry)

    def _read(self, entry):
        number, member = entry
        try:
            with self._lock:
                return self._zips[number].read(member)
        except (KeyError, OSError, IndexError, zipfile.BadZipFile):
            return None

    def _read_json(self, entry):
        raw = self._read(entry)
        if not raw:
            return None
        try:
            return json.loads(raw.decode("utf-8-sig"))
        except (ValueError, UnicodeDecodeError):
            return None

    def _resolve_member(self, stem):
        """Find the pack texture for a SkyBlock ID: follow the pack's own model
        files first, then fall back to a texture file with the same name."""
        if stem in self._member_cache:
            return self._member_cache[stem]
        result = (None, "")
        try:
            entry = self._from_models(stem)
            if entry is not None:
                result = (entry, "model")
            elif stem in self._index:
                result = (self._index[stem], "name")
            elif "_" in stem:
                matches = [key for key in self._index if key.endswith("_" + stem)]
                if matches:
                    result = (self._index[min(matches, key=len)], "suffix")
        except Exception:
            result = (None, "")
        self._member_cache[stem] = result
        return result

    def _from_models(self, stem):
        references = []
        for entry in self._definitions.get(stem, []):
            references.extend(self._definition_models(entry))
        references.extend(self._model_by_stem.get(stem, []))
        for reference in references:
            texture = self._texture_of_model(reference, 0)
            if texture is not None:
                return texture
        return None

    def _definition_models(self, entry):
        """Model references listed in a 1.21.4+ item definition (assets/<ns>/items/<id>.json)."""
        data = self._read_json(entry)
        found = []

        def walk(node):
            if isinstance(node, dict):
                for key, value in node.items():
                    if key == "model" and isinstance(value, str):
                        found.append(value.lower())
                    else:
                        walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)

        walk(data)
        return found

    def _texture_of_model(self, reference, depth):
        if depth > 4:
            return None
        reference = reference if ":" in reference else "minecraft:" + reference
        entry = self._models.get(reference)
        if entry is None:
            return None
        data = self._read_json(entry)
        if not isinstance(data, dict):
            return None
        textures = data.get("textures")
        if isinstance(textures, dict):
            choices = [textures.get("layer0")] + [v for k, v in textures.items() if k != "particle"]
            for value in choices:
                if not isinstance(value, str) or value.startswith("#"):
                    continue
                value = value.lower()
                namespace, _sep, path = (value if ":" in value else "minecraft:" + value).partition(":")
                if namespace == "minecraft" and path in (
                    "item/paper", "item/air", "item/barrier", "item/empty",
                    "item/stick", "item/name_tag", "item/map"):
                    continue  # generic base textures are not custom SkyBlock icons
                texture = self._all_pngs.get(f"{namespace}/{path}")
                if texture is not None:
                    return texture
        parent = data.get("parent")
        if isinstance(parent, str) and ":" in parent and not parent.lower().startswith("minecraft:"):
            return self._texture_of_model(parent.lower(), depth + 1)
        return None

    def _vanilla_candidates(self, item_id):
        material = str(self.material_of(item_id) or "").upper()
        if not material or material in self.VANILLA_SKIP:
            return []
        # Many SkyBlock items are PAPER underneath. Showing the vanilla paper
        # sprite for every such custom item is more misleading than no icon.
        if material == "PAPER" and item_id.upper() != "PAPER":
            return []
        name = self.VANILLA_ALIASES.get(material, material.lower())
        return [f"items/{name}", f"blocks/{name}"]

    def _vanilla_path(self, relative):
        return self.dir / "vanilla" / (relative.replace("/", "_") + ".png")

    def _from_vanilla_disk(self, item_id):
        for relative in self._vanilla_candidates(item_id):
            path = self._vanilla_path(relative)
            if path.exists():
                try:
                    return path.read_bytes()
                except OSError:
                    pass
        return None

    # ------------------------- background worker --------------------------

    def _enqueue(self, item_id):
        with self._lock:
            if item_id in self._queued:
                return
            self._queued.add(item_id)
            self._queue.append(item_id)
            if self._running:
                return
            self._running = True
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        if not self._index_done:
            try:
                self._load_index()
            except Exception:
                self._note("unavailable — check your internet connection")
            self._index_done = True
            self._notify()
        fetched = 0
        while True:
            with self._lock:
                if not self._queue:
                    self._running = False
                    break
                item_id = self._queue.pop(0)
            found = False
            try:
                if not self._furfsky_ok(item_id):
                    found = self._fetch_vanilla(item_id)
                else:
                    found = True  # the window picks it up from the pack on its next refresh
            except Exception:
                found = False
            with self._lock:
                self._queued.discard(item_id)
            if not found:
                self._missing.add(item_id)
            fetched += 1
            if found and fetched % 6 == 0:
                self._notify()
        self._write_report()
        self._notify()

    def _fetch_vanilla(self, item_id):
        for relative in self._vanilla_candidates(item_id):
            path = self._vanilla_path(relative)
            marker = path.with_suffix(".missing")
            if path.exists():
                self._bytes[item_id] = path.read_bytes()
                return True
            if marker.exists():
                continue
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                request = urllib.request.Request(self.VANILLA_URL.format(relative), headers={"User-Agent": self.USER_AGENT})
                with urllib.request.urlopen(request, timeout=15) as response:
                    data = response.read()
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    try:
                        marker.write_bytes(b"")
                    except OSError:
                        pass
                continue
            except Exception:
                continue
            if data[:8] != b"\x89PNG\r\n\x1a\n":
                continue
            try:
                path.write_bytes(data)
            except OSError:
                pass
            self._bytes[item_id] = data
            return True
        return False

    # ---------------------------- FurfSky pack ----------------------------

    def _load_index(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        meta_path = self.dir / "furfsky_meta.json"
        meta = load_json(meta_path, {})
        zips = self._cached_zips()
        stale = (time.time() - float(meta.get("checked", 0) or 0)) > self.RECHECK_SECONDS
        if not zips or stale:
            if not zips:
                self._note("downloading FurfSky Reborn (one-time)…")
            try:
                self._download_furfsky(meta_path, meta)
            except Exception:
                pass
            zips = self._cached_zips()
        self._build_index(zips)
        if self._index:
            self._note("FurfSky Reborn")
        else:
            self._note("Minecraft textures only (FurfSky download unavailable)")

    def _cached_zips(self):
        files = list(self.dir.glob("furfsky_*.zip"))
        return sorted(files, key=lambda p: p.stat().st_size, reverse=True)

    def _download_furfsky(self, meta_path, meta):
        request = urllib.request.Request(
            self.API_URL, headers={"User-Agent": self.USER_AGENT, "Accept": "application/vnd.github+json"}
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            release = json.loads(response.read().decode("utf-8"))
        tag = str(release.get("tag_name", ""))
        existing = self._cached_zips()
        if existing and meta.get("tag") == tag:
            save_json(meta_path, {"tag": tag, "checked": time.time()})
            return
        assets = [
            a for a in release.get("assets", [])
            if str(a.get("name", "")).lower().endswith(".zip") and a.get("browser_download_url")
        ]
        downloaded = []
        for number, asset in enumerate(assets[:4]):
            if int(asset.get("size", 0) or 0) > 80_000_000:
                continue
            destination = self.dir / f"furfsky_{number}.zip"
            partial = destination.with_suffix(".part")
            request = urllib.request.Request(asset["browser_download_url"], headers={"User-Agent": self.USER_AGENT})
            with urllib.request.urlopen(request, timeout=60) as response, partial.open("wb") as out:
                while True:
                    chunk = response.read(1 << 16)
                    if not chunk:
                        break
                    out.write(chunk)
            downloaded.append((partial, destination))
        if not downloaded:
            return
        for old in existing:
            try:
                old.unlink()
            except OSError:
                pass
        for partial, destination in downloaded:
            partial.replace(destination)
        save_json(meta_path, {"tag": tag, "checked": time.time()})

    def _decode_cats(self, packed, output):
        """Convert a Catharsis CATS blob into entries in a normal ZIP archive.

        CATS stores a directory tree followed by file data at offsets relative
        to the end of that tree. Entries may be raw (0xff) or gzip (0xfe).
        Only model/item JSON and PNGs are needed for icons.
        """
        position = 0

        def consume(length):
            nonlocal position
            if length < 0 or position + length > len(packed):
                raise ValueError("Truncated Catharsis directory")
            data = packed[position:position + length]
            position += length
            return data

        def number(fmt):
            return struct.unpack(fmt, consume(struct.calcsize(fmt)))[0]

        if consume(4) != b"CATS":
            raise ValueError("File is not a Catharsis CATS archive")
        version = number(">B")
        entries = []

        def walk(prefix, depth=0):
            if depth > 30:
                raise ValueError("Catharsis directory is too deep")
            count = number(">H")
            for _ in range(count):
                kind = number(">B")
                name = consume(number(">B")).decode("ascii", "replace")
                path = f"{prefix}{name}"
                if kind == 1:
                    walk(path + "/", depth + 1)
                elif kind == 0:
                    offset = number(">i")
                    size = number(">i")
                    compression = number(">B")
                    entries.append((path, offset, size, compression))
                    if len(entries) > 100000:
                        raise ValueError("Too many Catharsis entries")
                else:
                    raise ValueError(f"Unknown Catharsis entry type: {kind}")

        walk("")
        data_start = position
        exported = 0
        for path, offset, size, compression in entries:
            name = path.replace("\\", "/").lstrip("/")
            lower = name.lower()
            if ("/assets/" not in "/" + lower or
                    not lower.endswith((".png", ".json")) or
                    ".." in name.split("/")):
                continue
            if offset < 0 or size < 0 or size > 30_000_000:
                continue
            first, last = data_start + offset, data_start + offset + size
            if last > len(packed):
                continue
            blob = packed[first:last]
            try:
                if compression == 0xfe:
                    blob = gzip.decompress(blob)
                elif compression != 0xff:
                    continue
            except (OSError, EOFError):
                continue
            if len(blob) > 30_000_000:
                continue
            output.writestr(name, blob)
            exported += 1
        return version, len(entries), exported

    def _expanded_zip(self, source):
        """Return a ZIP the ordinary indexer can read, decoding CATS if needed.

        Cache is a different filename so _cached_zips() never mistakes the
        generated index archive for a new GitHub release asset.
        """
        with zipfile.ZipFile(source) as archive:
            containers = [name for name in archive.namelist()
                          if name.lower().endswith(".cats")]
            if not containers:
                return source
            expanded = self.dir / f"expanded_{source.stem}.zip"
            if expanded.exists() and expanded.stat().st_mtime_ns >= source.stat().st_mtime_ns:
                if zipfile.is_zipfile(expanded):
                    self._cat_notes.append(f"using decoded CATS cache: {expanded.name}")
                    return expanded
            partial = expanded.with_suffix(".tmp")
            try:
                total = 0
                descriptions = []
                with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_STORED,
                                     allowZip64=True) as out:
                    for filename in containers:
                        version, entries, exported = self._decode_cats(
                            archive.read(filename), out)
                        total += exported
                        descriptions.append(f"{filename}: v{version}, {entries} files, {exported} assets")
                if total == 0:
                    raise ValueError("Catharsis pack contained no indexable assets")
                os.replace(partial, expanded)
                self._cat_notes.extend(descriptions)
                return expanded
            finally:
                if partial.exists():
                    partial.unlink()

    def _build_index(self, zip_paths):
        main, vanilla_ns = {}, {}
        ranks = {id(main): {}, id(vanilla_ns): {}}
        models, model_by_stem, definitions, all_pngs = {}, {}, {}, {}
        namespaces = {}
        zips = []
        self._cat_notes = []

        def put(target, stem, entry, rank):
            known = ranks[id(target)]
            if stem not in target or rank < known[stem]:
                target[stem] = entry
                known[stem] = rank

        for path in zip_paths:
            try:
                index_path = self._expanded_zip(path)
                archive = zipfile.ZipFile(index_path)
                names = archive.namelist()
            except (OSError, ValueError, zipfile.BadZipFile, EOFError) as error:
                self._cat_notes.append(f"failed to index {path.name}: {error}")
                continue
            number = len(zips)
            zips.append(archive)
            for member in names:
                lower = member.lower()
                cut = lower.find("assets/")
                if cut < 0:
                    continue
                parts = lower[cut + 7:].split("/")
                if len(parts) < 3:
                    continue
                namespace, rest = parts[0], "/".join(parts[1:])
                base = rest.rsplit("/", 1)[-1]
                if lower.endswith(".json"):
                    if rest.startswith("models/"):
                        key = f"{namespace}:{rest[7:-5]}"
                        models[key] = (number, member)
                        if namespace != "minecraft":
                            model_by_stem.setdefault(base[:-5], []).append(key)
                    elif rest.startswith("items/") and namespace != "minecraft":
                        definitions.setdefault(base[:-5], []).append((number, member))
                    continue
                if not lower.endswith(".png") or not rest.startswith("textures/"):
                    continue
                all_pngs[f"{namespace}/{rest[9:-4]}"] = (number, member)
                namespaces[namespace] = namespaces.get(namespace, 0) + 1
                if any(part in "/" + rest for part in self.SKIP_PATHS):
                    continue
                stem = base[:-4]
                if not stem or stem.startswith("_"):
                    continue
                rank = 0 if ("/item/" in "/" + rest or "/items/" in "/" + rest) else 1
                put(vanilla_ns if namespace == "minecraft" else main, stem, (number, member), rank)

        self._zips = zips
        # SkyBlock textures live in the pack's own namespaces; the "minecraft"
        # namespace only holds vanilla overrides (paper, sticks, ...), which would
        # otherwise be mistaken for SkyBlock icons. Only use it if nothing else exists.
        self._index = main if len(main) >= 50 else {**vanilla_ns, **main}
        self._models = models
        self._model_by_stem = model_by_stem
        self._definitions = definitions
        self._all_pngs = all_pngs
        self._report_info = [
            "zips: " + ", ".join(f"{p.name} ({p.stat().st_size:,} bytes)" for p in zip_paths),
            f"textures per namespace: {dict(sorted(namespaces.items(), key=lambda kv: -kv[1])[:12])}",
            f"name index: {len(self._index)}  models: {len(models)}  item definitions: {len(definitions)}",
            *self._cat_notes,
        ]

    def _write_report(self):
        """Write icon_cache/furfsky_report.txt, which shows how each item's icon was found."""
        try:
            lines = ["FurfSky icon report", ""] + self._report_info + [""]
            lines.append("sample of indexed names:")
            for stem in sorted(self._index)[:25]:
                lines.append(f"  {stem} -> {self._index[stem][1]}")
            lines += ["", "icons resolved for this tree (how | file in the pack):"]
            for item_id, (how, member) in sorted(self._resolved.items())[:300]:
                lines.append(f"  {item_id:36s} {how:7s} {member}")
            if self._bad_digests:
                lines += ["", f"rejected as shared placeholder images: {len(self._bad_digests)}"]
                for item_id in sorted(self._bad)[:60]:
                    lines.append(f"  {item_id}")
            (self.dir / "furfsky_report.txt").write_text("\n".join(lines), encoding="utf-8")
        except OSError:
            pass

    # ---------------------------- images -----------------------------------

    def _notify(self):
        try:
            self.root.after(0, self.on_change)
        except Exception:
            pass

    def _note(self, text):
        try:
            self.root.after(0, lambda: self.on_note(text))
        except Exception:
            pass

    def _scale(self, raw, big):
        width = raw.width()
        target = px(48 if big else 32)
        if width >= target:
            factor = max(1, int(width / target + 0.5))
            return raw.subsample(factor, factor) if factor > 1 else raw
        factor = max(1, int(target / width + 0.5))
        return raw.zoom(factor, factor) if factor > 1 else raw

    def _make_photo(self, data, big):
        try:
            raw = tk.PhotoImage(data=base64.b64encode(data))
            width, height = raw.width(), raw.height()
            if width <= 0 or height <= 0:
                return None
            if height >= 2 * width and height % width == 0:
                # Animated texture strip: keep only the first frame.
                frame = tk.PhotoImage()
                frame.tk.call(str(frame), "copy", str(raw), "-from", 0, 0, width, width)
                raw = frame
            return self._scale(raw, big)
        except (tk.TclError, ValueError):
            return None

    def _placeholder(self, big):
        photo = self._placeholders.get(big)
        if photo is None:
            raw = tk.PhotoImage(width=16, height=16)
            raw.put(BORDER_STRONG, to=(0, 0, 16, 16))
            raw.put(SURFACE_ALT, to=(1, 1, 15, 15))
            raw.put(BORDER_STRONG, to=(5, 5, 11, 11))
            photo = self._scale(raw, big)
            self._placeholders[big] = photo
        return photo


# ---------------------------------------------------------------------------
# Starter recipe database
# ---------------------------------------------------------------------------
RECIPES = {
    "TITANIUM_DRILL_1": [
        ("REFINED_TITANIUM", 8),
        ("REFINED_MITHRIL", 8),
        ("DRILL_MOTOR", 1),
        ("FUEL_CANISTER", 1),
        ("GOLDEN_PLATE", 6),
    ],
    "REFINED_TITANIUM": [("ENCHANTED_TITANIUM", 16)],
    "ENCHANTED_TITANIUM": [("TITANIUM", 160)],
    "REFINED_MITHRIL": [("ENCHANTED_MITHRIL", 160)],
    "ENCHANTED_MITHRIL": [("MITHRIL", 160)],
    "DRILL_MOTOR": [
        ("TREASUREITE", 10),
        ("ENCHANTED_IRON_BLOCK", 1),
        ("ENCHANTED_REDSTONE_BLOCK", 3),
        ("GOLDEN_PLATE", 1),
    ],
    "ENCHANTED_IRON_BLOCK": [("ENCHANTED_IRON", 160)],
    "ENCHANTED_IRON": [("IRON_INGOT", 160)],
    "ENCHANTED_REDSTONE_BLOCK": [("ENCHANTED_REDSTONE", 160)],
    "ENCHANTED_REDSTONE": [("REDSTONE", 160)],
    "GOLDEN_PLATE": [
        ("REFINED_DIAMOND", 1),
        ("ENCHANTED_GOLD_BLOCK", 2),
        ("GLACITE_JEWEL", 5),
    ],
    "REFINED_DIAMOND": [("ENCHANTED_DIAMOND_BLOCK", 2)],
    "ENCHANTED_DIAMOND_BLOCK": [("ENCHANTED_DIAMOND", 160)],
    "ENCHANTED_DIAMOND": [("DIAMOND", 160)],
    "ENCHANTED_GOLD_BLOCK": [("ENCHANTED_GOLD", 160)],
    "ENCHANTED_GOLD": [("GOLD_INGOT", 160)],
    "FUEL_CANISTER": [("ENCHANTED_COAL_BLOCK", 2)],
    "ENCHANTED_COAL_BLOCK": [("ENCHANTED_COAL", 160)],
    "ENCHANTED_COAL": [("COAL", 160)],
}

ALIASES = {
    "TREASURITE": "TREASURITE",
    "TREASUREITE": "TREASURITE",
    "IRON": "IRON_INGOT",
    "GOLD": "GOLD_INGOT",
}

FALLBACK_NAMES = {
    "IRON_INGOT": "Iron Ingot",
    "GOLD_INGOT": "Gold Ingot",
    "TITANIUM": "Titanium",
    "MITHRIL": "Mithril",
    "REDSTONE": "Redstone",
    "DIAMOND": "Diamond",
    "COAL": "Coal",
    "GLACITE_JEWEL": "Glacite Jewel",
    "TREASURITE": "Treasurite",
    "TITANIUM_DRILL_1": "Titanium Drill DR-X355",
    "REFINED_TITANIUM": "Refined Titanium",
    "REFINED_MITHRIL": "Refined Mithril",
    "DRILL_MOTOR": "Drill Motor",
    "FUEL_CANISTER": "Fuel Canister",
    "GOLDEN_PLATE": "Golden Plate",
    "REFINED_DIAMOND": "Refined Diamond",
    "ENCHANTED_TITANIUM": "Enchanted Titanium",
    "ENCHANTED_MITHRIL": "Enchanted Mithril",
    "ENCHANTED_IRON": "Enchanted Iron",
    "ENCHANTED_IRON_BLOCK": "Enchanted Iron Block",
    "ENCHANTED_REDSTONE": "Enchanted Redstone",
    "ENCHANTED_REDSTONE_BLOCK": "Enchanted Redstone Block",
    "ENCHANTED_DIAMOND": "Enchanted Diamond",
    "ENCHANTED_DIAMOND_BLOCK": "Enchanted Diamond Block",
    "ENCHANTED_GOLD": "Enchanted Gold",
    "ENCHANTED_GOLD_BLOCK": "Enchanted Gold Block",
    "ENCHANTED_COAL": "Enchanted Coal",
    "ENCHANTED_COAL_BLOCK": "Enchanted Coal Block",
}


def normalize_id(item_id: str) -> str:
    item_id = str(item_id).strip().upper().replace(" ", "_")
    return ALIASES.get(item_id, item_id)


# ---------------------------------------------------------------------------
# Tree-position paths
#
# The same item (e.g. GOLDEN_PLATE) can be needed in more than one place in
# the crafting tree. Owned quantities are keyed by the full path from the
# root down to that occurrence (as a tuple of item ids), not by item id
# alone, so each occurrence tracks its own inventory.
# ---------------------------------------------------------------------------
PATH_TAG_PREFIX = "path:"
PATH_SEP = ">"


def path_to_key(path) -> str:
    """Turn a path tuple into the string used as an `obtained` dict key."""
    return PATH_SEP.join(path)


def make_path_tag(path) -> str:
    """Encode a path tuple as a single Treeview tag."""
    return PATH_TAG_PREFIX + path_to_key(path)


def path_from_tags(tags):
    """Recover the path tuple stored in a node's tags, if present."""
    for tag in tags or ():
        if isinstance(tag, str) and tag.startswith(PATH_TAG_PREFIX):
            return tuple(tag[len(PATH_TAG_PREFIX):].split(PATH_SEP))
    return None


def load_json(path: Path, default):
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def save_json(path: Path, data) -> None:
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    tmp.replace(path)


class ItemPicker(tk.Toplevel):
    """Search-and-select dialog used everywhere an item needs to be chosen."""

    MAX_SHOWN = 500

    def __init__(self, parent, title, items, display_name, initial_id=None, icon_for=None, tag_for=None):
        super().__init__(parent)
        self.parent = parent
        self.icon_for = icon_for
        self.tag_for = tag_for
        self.all_ids = sorted(set(items), key=lambda x: display_name(x).casefold())
        self.display_name = display_name
        self.result = None
        self.matches = []
        self.title(title)
        self.configure(background=BG)
        self.minsize(px(500), px(440))
        center_window(self, px(620), px(580), parent)
        self.transient(parent)
        try:
            self.grab_set()
        except tk.TclError:
            pass

        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        header = ttk.Frame(self, style="Header.TFrame", padding=(px(20), px(14)))
        header.grid(row=0, column=0, sticky="ew")
        ttk.Label(header, text="Choose a SkyBlock item", style="HeaderTitle.TLabel", font=(FONT, 15, "bold")).pack(anchor="w")
        ttk.Label(header, text=title, style="HeaderSub.TLabel").pack(anchor="w", pady=(2, 0))

        self.search_var = tk.StringVar()
        search = ttk.Entry(self, textvariable=self.search_var, font=(FONT, 11))
        search.grid(row=1, column=0, sticky="ew", padx=px(20), pady=(px(16), px(10)))
        search.focus_set()
        search.bind("<KeyRelease>", self._filter)
        search.bind("<Return>", self._accept_first)
        search.bind("<Escape>", lambda _e: self.destroy())
        search.bind("<Down>", lambda _e: self._move(1))
        search.bind("<Up>", lambda _e: self._move(-1))

        card = Card(self, padding=px(2))
        card.grid(row=2, column=0, sticky="nsew", padx=px(20), pady=(0, px(4)))
        frame = card.body
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        self.tree = ttk.Treeview(frame, columns=("id",), show="tree headings", selectmode="browse", style="Picker.Treeview")
        self.tree.heading("#0", text="Item", anchor="w")
        self.tree.heading("id", text="ID", anchor="w")
        self.tree.column("#0", width=px(340), anchor="w")
        self.tree.column("id", width=px(220), anchor="w")
        configure_rarity_tags(self.tree)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind("<Double-Button-1>", self._on_double_click)
        self.tree.bind("<Return>", self._accept)

        bottom = ttk.Frame(self)
        bottom.grid(row=3, column=0, sticky="ew", padx=px(20), pady=px(14))
        bottom.columnconfigure(0, weight=1)
        self.count_var = tk.StringVar(value="")
        ttk.Label(bottom, textvariable=self.count_var, style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Button(bottom, text="Cancel", command=self.destroy).grid(row=0, column=1, padx=(px(8), 0))
        ttk.Button(bottom, text="Choose", style="Accent.TButton", command=self._accept).grid(row=0, column=2, padx=(px(8), 0))

        self.bind("<Escape>", lambda _e: self.destroy())
        self._filter()
        if initial_id and initial_id in self.all_ids:
            self._select_id(initial_id)

        enable_dark_title_bar(self)
        self.wait_window(self)

    def _filter(self, _event=None):
        query = self.search_var.get().strip().casefold()
        matches = []
        for item_id in self.all_ids:
            name = self.display_name(item_id)
            if not query or query in name.casefold() or query in item_id.casefold():
                matches.append(item_id)
        total = len(matches)
        self.matches = matches[: self.MAX_SHOWN]
        self.tree.delete(*self.tree.get_children())
        for index, item_id in enumerate(self.matches):
            options = {}
            if self.icon_for:
                options["image"] = self.icon_for(item_id)
            if self.tag_for:
                options["tags"] = (self.tag_for(item_id),)
            self.tree.insert("", "end", iid=str(index), text=self.display_name(item_id), values=(item_id,), **options)
        if total > self.MAX_SHOWN:
            self.count_var.set(f"Showing the first {self.MAX_SHOWN} of {total:,} — keep typing to narrow it down")
        elif query:
            self.count_var.set(f"{total:,} match{'es' if total != 1 else ''}")
        else:
            self.count_var.set(f"{total:,} items  •  type a name or item ID to filter")
        if self.matches:
            self._select_index(0)

    def _select_index(self, index):
        iid = str(index)
        if self.tree.exists(iid):
            self.tree.selection_set(iid)
            self.tree.focus(iid)
            self.tree.see(iid)

    def _select_id(self, item_id):
        try:
            index = self.matches.index(item_id)
        except ValueError:
            return
        self._select_index(index)

    def _move(self, delta):
        """Move the selection up/down while typing in the search box."""
        if not self.matches:
            return "break"
        selected = self.tree.selection()
        current = int(selected[0]) if selected else -1
        self._select_index(max(0, min(len(self.matches) - 1, current + delta)))
        return "break"

    def _on_double_click(self, event):
        if self.tree.identify_region(event.x, event.y) in ("cell", "tree"):
            self._accept()

    def _accept_first(self, _event=None):
        selected = self.tree.selection()
        if selected:
            self.result = self.matches[int(selected[0])]
            self.destroy()
        elif self.matches:
            self.result = self.matches[0]
            self.destroy()

    def _accept(self, _event=None):
        selected = self.tree.selection()
        if selected:
            self.result = self.matches[int(selected[0])]
            self.destroy()


class RecipeEditor(tk.Toplevel):
    """Friendly recipe editor with quantity + item-picker rows."""

    def __init__(self, parent, output_id, recipe, item_ids, display_name, picker, icon_for=None, color_for=None):
        super().__init__(parent)
        self.parent = parent
        self.icon_for = icon_for
        self.color_for = color_for
        self.output_id = output_id
        self.display_name = display_name
        self.item_ids = item_ids
        self.picker = picker
        self.result = None
        self.rows = []

        self.title(f"Edit Recipe • {display_name(output_id)}")
        self.configure(background=BG)
        self.minsize(px(640), px(520))
        center_window(self, px(720), px(640), parent)
        self.transient(parent)
        try:
            self.grab_set()
        except tk.TclError:
            pass
        self.protocol("WM_DELETE_WINDOW", self.destroy)

        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        header = ttk.Frame(self, style="Header.TFrame", padding=(px(20), px(16)))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="Recipe Editor", style="HeaderTitle.TLabel", font=(FONT, 16, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(
            header,
            text=f"{display_name(output_id)}  •  {output_id}",
            style="HeaderSub.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(3, 0))

        intro = ttk.Frame(self, padding=(px(20), px(14), px(20), px(8)))
        intro.grid(row=1, column=0, sticky="ew")
        intro.columnconfigure(0, weight=1)
        ttk.Label(
            intro,
            text="Add each ingredient below. You never need to type item IDs or recipe syntax.",
            style="Muted.TLabel",
            wraplength=px(640),
        ).grid(row=0, column=0, sticky="w")

        card = Card(self, padding=px(14))
        card.grid(row=2, column=0, sticky="nsew", padx=px(20), pady=px(4))
        body = card.body
        body.columnconfigure(0, weight=1)
        body.rowconfigure(2, weight=1)

        ttk.Label(body, text="INGREDIENTS", style="Caption.Card.TLabel").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, px(8)))
        heads = ttk.Frame(body, style="Card.TFrame")
        heads.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, px(2)))
        heads.columnconfigure(1, weight=1)
        ttk.Label(heads, text="Quantity", style="Muted.Card.TLabel", width=9, anchor="e").grid(row=0, column=0, padx=(px(2), px(10)))
        ttk.Label(heads, text="Item", style="Muted.Card.TLabel").grid(row=0, column=1, sticky="w")

        canvas = tk.Canvas(body, highlightthickness=0, background=SURFACE)
        scrollbar = ttk.Scrollbar(body, orient="vertical", command=canvas.yview)
        canvas.grid(row=2, column=0, sticky="nsew")
        scrollbar.grid(row=2, column=1, sticky="ns")

        self.rows_frame = ttk.Frame(canvas, style="Card.TFrame")
        window_id = canvas.create_window((0, 0), window=self.rows_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        self.rows_frame.bind(
            "<Configure>",
            lambda _e: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas.bind(
            "<Configure>",
            lambda e: canvas.itemconfigure(window_id, width=e.width),
        )

        def on_wheel(event):
            bbox = canvas.bbox("all")
            if bbox and (bbox[3] - bbox[1]) > canvas.winfo_height():
                canvas.yview_scroll(int(-event.delta / 120), "units")

        canvas.bind("<Enter>", lambda _e: canvas.bind_all("<MouseWheel>", on_wheel))
        canvas.bind("<Leave>", lambda _e: canvas.unbind_all("<MouseWheel>"))
        self._canvas = canvas

        for ingredient_id, amount in recipe:
            self._add_row(ingredient_id, amount)

        if not self.rows:
            self._add_row()

        add = ttk.Frame(self, padding=(px(20), px(10)))
        add.grid(row=3, column=0, sticky="ew")
        ttk.Button(add, text="＋  Add Ingredient", command=self._add_row).pack(side="left")
        ttk.Label(add, text="Tip: intermediate items can have their own recipes.", style="Muted.TLabel").pack(side="left", padx=(px(12), 0))

        bottom = ttk.Frame(self, padding=(px(20), px(10), px(20), px(18)))
        bottom.grid(row=4, column=0, sticky="ew")
        ttk.Button(bottom, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(bottom, text="Save Recipe", style="Accent.TButton", command=self._save).pack(side="right", padx=(0, px(8)))
        ttk.Button(bottom, text="Delete Recipe", style="Danger.TButton", command=self._delete).pack(side="left")

        self.bind("<Escape>", lambda _e: self.destroy())
        self.bind("<Destroy>", self._on_destroy)
        enable_dark_title_bar(self)
        self.wait_window(self)

    def _on_destroy(self, event):
        if event.widget is self:
            try:
                self._canvas.unbind_all("<MouseWheel>")
            except tk.TclError:
                pass

    def _decorate(self, label, item_id):
        """Show the item's icon and rarity colour on an ingredient label."""
        if self.icon_for:
            label.configure(image=self.icon_for(item_id))
        if self.color_for:
            label.configure(foreground=self.color_for(item_id))

    def _add_row(self, item_id=None, amount=1):
        row = ttk.Frame(self.rows_frame, style="Card.TFrame", padding=(px(2), px(7), px(2), 0))
        row.pack(fill="x")
        row.columnconfigure(1, weight=1)

        qty = tk.StringVar(value=str(amount))
        ttk.Entry(row, textvariable=qty, width=9, justify="right").grid(row=0, column=0, padx=(0, px(10)))

        name_var = tk.StringVar(value=self.display_name(item_id) if item_id else "Choose an item…")
        label = ttk.Label(
            row,
            textvariable=name_var,
            style="Card.TLabel" if item_id else "Muted.Card.TLabel",
            anchor="w",
            compound="left",
        )
        label.grid(row=0, column=1, sticky="ew")
        if item_id:
            self._decorate(label, normalize_id(item_id))

        selected = {"id": normalize_id(item_id) if item_id else None}

        def choose():
            picked = self.picker("Choose Ingredient", selected["id"])
            if picked:
                selected["id"] = picked
                name_var.set(f"{self.display_name(picked)}  [{picked}]")
                label.configure(style="Card.TLabel")
                self._decorate(label, picked)

        ttk.Button(row, text="Choose…", style="Small.TButton", command=choose).grid(row=0, column=2, padx=(px(10), px(6)))

        def remove():
            row.destroy()
            try:
                self.rows.remove(data)
            except ValueError:
                pass

        ttk.Button(row, text="Remove", style="SmallDanger.TButton", command=remove).grid(row=0, column=3)
        tk.Frame(row, height=1, background=BORDER, borderwidth=0).grid(row=1, column=0, columnspan=4, sticky="ew", pady=(px(7), 0))
        data = {"frame": row, "qty": qty, "selected": selected}
        self.rows.append(data)

    def _save(self):
        parsed = []
        for index, data in enumerate(self.rows, start=1):
            raw_qty = data["qty"].get().strip()
            item_id = data["selected"].get("id", "")
            if not raw_qty and not item_id:
                continue
            try:
                qty = int(raw_qty)
                if qty <= 0:
                    raise ValueError
            except ValueError:
                messagebox.showerror("Invalid quantity", f"Ingredient {index} needs a positive whole-number quantity.", parent=self)
                return
            if not item_id:
                messagebox.showerror("Missing item", f"Choose an item for ingredient {index}.", parent=self)
                return
            parsed.append((normalize_id(item_id), qty))

        if not parsed:
            messagebox.showerror("No ingredients", "Add at least one ingredient, or use Delete Recipe.", parent=self)
            return

        self.result = parsed
        self.destroy()

    def _delete(self):
        if messagebox.askyesno(
            "Delete recipe",
            f"Remove the recipe for {self.display_name(self.output_id)}?\n\nThe item will remain available as a raw/leaf item until a recipe is added again.",
            parent=self,
        ):
            self.result = []
            self.destroy()


class SkyblockTracker(tk.Tk):
    def __init__(self):
        super().__init__()
        global UI_SCALE
        UI_SCALE = max(1.0, self.winfo_fpixels("1i") / 96.0)
        set_window_icon(self)
        self.title("SkyBlock Crafting Tracker")
        self.minsize(px(980), px(680))
        center_window(self, px(1240), px(820))

        self.items = {}
        self.name_to_id = {}
        self.obtained = {}
        self.root_item = "TITANIUM_DRILL_1"
        self.selected_item = None
        self.selected_path = None
        self.current_view = "tree"
        self._res_dirty = True
        self._icon_job = None
        self._icon_note = ""
        self._status_state = ("Loading Hypixel item data…", "Loading item data…", "busy")

        saved = load_json(DATA_FILE, {})
        self.obtained = {
            str(k): max(0, int(v))
            for k, v in saved.get("obtained", {}).items()
            if isinstance(v, (int, float))
        }
        saved_root = saved.get("root_item")
        if isinstance(saved_root, str) and saved_root.strip():
            self.root_item = normalize_id(saved_root)

        saved_recipes = saved.get("recipes")
        if isinstance(saved_recipes, dict):
            for output, ingredients in saved_recipes.items():
                parsed = []
                if isinstance(ingredients, list):
                    for pair in ingredients:
                        if isinstance(pair, list) and len(pair) == 2:
                            try:
                                parsed.append((normalize_id(pair[0]), max(1, int(pair[1]))))
                            except (ValueError, TypeError):
                                pass
                if parsed:
                    RECIPES[normalize_id(output)] = parsed
                elif normalize_id(output) in RECIPES:
                    # Empty saved recipes intentionally delete a starter recipe.
                    RECIPES.pop(normalize_id(output), None)

        self._configure_style()
        self.icons = IconStore(
            self,
            DATA_FILE.parent / "icon_cache",
            lambda item_id: (self.items.get(item_id) or {}).get("material"),
            self._icons_changed,
            self._icon_status,
        )
        self._build_ui()
        enable_dark_title_bar(self)
        self._load_api()

    # --------------------------- Style ------------------------------------

    def _configure_style(self):
        self.configure(background=BG)
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure(
            ".",
            background=BG,
            foreground=TEXT,
            bordercolor=BORDER,
            lightcolor=BORDER,
            darkcolor=BORDER,
            troughcolor=SURFACE,
            focuscolor=BG,
            fieldbackground=SURFACE_ALT,
            font=(FONT, 10),
        )

        # ---- frames & labels ----
        style.configure("TFrame", background=BG)
        style.configure("Card.TFrame", background=SURFACE, relief="flat")
        style.configure("Header.TFrame", background=HEADER_BG)
        style.configure("TLabel", background=BG, foreground=TEXT, font=(FONT, 10))
        style.configure("Muted.TLabel", background=BG, foreground=MUTED, font=(FONT, 10))
        style.configure("Card.TLabel", background=SURFACE, foreground=TEXT, font=(FONT, 10))
        style.configure("Muted.Card.TLabel", background=SURFACE, foreground=MUTED, font=(FONT, 9))
        style.configure("Caption.Card.TLabel", background=SURFACE, foreground=MUTED, font=(FONT, 8, "bold"))
        style.configure("Stat.Card.TLabel", background=SURFACE, foreground=TEXT_STRONG, font=(FONT, 20, "bold"))
        style.configure("StatText.Card.TLabel", background=SURFACE, foreground=TEXT_STRONG, font=(FONT, 15, "bold"))
        style.configure("Section.TLabel", background=SURFACE, foreground=TEXT_STRONG, font=(FONT, 11, "bold"))
        style.configure("ItemName.Card.TLabel", background=SURFACE, foreground=TEXT_STRONG, font=(FONT, 13, "bold"))
        style.configure("HeaderTitle.TLabel", background=HEADER_BG, foreground=TEXT_STRONG, font=(FONT, 18, "bold"))
        style.configure("HeaderSub.TLabel", background=HEADER_BG, foreground=MUTED, font=(FONT, 10))
        style.configure("Pill.TLabel", background=SURFACE_ALT, foreground=TEXT, font=(FONT, 9, "bold"))
        style.configure("Footer.TLabel", background=HEADER_BG, foreground=MUTED, font=(FONT, 9))

        # ---- buttons (flat, with hover + pressed states) ----
        def button(name, bg, hover, pressed, fg, border, hover_border, font, pad, hover_fg=None):
            style.configure(
                name,
                background=bg,
                foreground=fg,
                bordercolor=border,
                lightcolor=bg,
                darkcolor=bg,
                focuscolor=bg,
                focusthickness=0,
                relief="flat",
                borderwidth=1,
                padding=(px(pad[0]), px(pad[1])),
                font=font,
            )
            style.map(
                name,
                background=[("disabled", bg), ("pressed", pressed), ("active", hover)],
                lightcolor=[("disabled", bg), ("pressed", pressed), ("active", hover)],
                darkcolor=[("disabled", bg), ("pressed", pressed), ("active", hover)],
                bordercolor=[("pressed", hover_border), ("active", hover_border)],
                foreground=[("disabled", MUTED), ("active", hover_fg or fg)],
            )

        button("TButton", SURFACE_ALT, SURFACE_HOVER, BORDER, TEXT, BORDER, BORDER_STRONG, (FONT, 10), (14, 8))
        button("Small.TButton", SURFACE_ALT, SURFACE_HOVER, BORDER, TEXT, BORDER, BORDER_STRONG, (FONT, 9), (10, 5))
        button("Accent.TButton", ACCENT, ACCENT_HOVER, ACCENT_PRESSED, "#ffffff", ACCENT, ACCENT_HOVER, (FONT, 10, "bold"), (16, 8))
        button("Danger.TButton", SURFACE_ALT, "#3a2029", "#2c181f", RED, BORDER, "#6b3340", (FONT, 10), (14, 8))
        button("SmallDanger.TButton", SURFACE_ALT, "#3a2029", "#2c181f", RED, BORDER, "#6b3340", (FONT, 9), (10, 5))
        button("Seg.TButton", SURFACE, SURFACE_HOVER, BORDER, MUTED, BORDER, BORDER_STRONG, (FONT, 10, "bold"), (22, 8), hover_fg=TEXT)
        button("SegOn.TButton", ACCENT, ACCENT_HOVER, ACCENT_PRESSED, "#ffffff", ACCENT, ACCENT_HOVER, (FONT, 10, "bold"), (22, 8))
        button("Plus.TButton", SURFACE_ALT, "#1d3a2b", "#16301f", GREEN, BORDER, "#2f6b4a", (FONT, 10, "bold"), (10, 8))

        # ---- checkbuttons ----
        style.configure(
            "Card.TCheckbutton",
            background=SURFACE,
            foreground=TEXT,
            font=(FONT, 10),
            focuscolor=SURFACE,
            indicatorbackground=SURFACE_ALT,
            indicatorforeground="#ffffff",
            upperbordercolor=BORDER_STRONG,
            lowerbordercolor=BORDER_STRONG,
            indicatormargin=(0, 0, px(6), 0),
        )
        style.map(
            "Card.TCheckbutton",
            background=[("active", SURFACE)],
            foreground=[("active", TEXT_STRONG)],
            indicatorbackground=[("selected", ACCENT), ("active", SURFACE_HOVER)],
            upperbordercolor=[("selected", ACCENT)],
            lowerbordercolor=[("selected", ACCENT)],
        )

        # ---- tree ----
        style.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
        style.configure(
            "Treeview",
            rowheight=px(48),
            font=(FONT, 10),
            indent=px(26),
            background=SURFACE,
            fieldbackground=SURFACE,
            foreground=TEXT,
            bordercolor=SURFACE,
            lightcolor=SURFACE,
            darkcolor=SURFACE,
            borderwidth=0,
            relief="flat",
        )
        style.configure(
            "Treeview.Heading",
            font=(FONT, 9, "bold"),
            background=SURFACE_ALT,
            foreground=MUTED,
            bordercolor=SURFACE,
            lightcolor=SURFACE_ALT,
            darkcolor=SURFACE_ALT,
            padding=(px(10), px(8)),
            relief="flat",
        )
        style.map("Treeview.Heading", background=[("active", SURFACE_HOVER)], foreground=[("active", TEXT)])
        # Leave item colors to their tags, including when the item is selected.
        # This is essential for a checked-off item to remain green across *every*
        # column, not just its status cell. The item picker uses its own style.
        style.map("Treeview", background=[], foreground=[])
        style.map("Picker.Treeview", background=[("selected", SELECT_BG)], foreground=[("selected", TEXT_STRONG)])

        # ---- entries ----
        style.configure(
            "TEntry",
            padding=px(7),
            fieldbackground=SURFACE_ALT,
            foreground=TEXT,
            insertcolor=TEXT,
            bordercolor=BORDER,
            lightcolor=BORDER,
            darkcolor=BORDER,
            selectbackground=ACCENT,
            selectforeground="#ffffff",
        )
        style.map(
            "TEntry",
            bordercolor=[("focus", ACCENT)],
            lightcolor=[("focus", ACCENT)],
            darkcolor=[("focus", ACCENT)],
        )

        # ---- slim arrow-less scrollbars ----
        style.layout(
            "Vertical.TScrollbar",
            [("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})]})],
        )
        style.configure(
            "Vertical.TScrollbar",
            background=SURFACE_HOVER,
            troughcolor=SURFACE,
            bordercolor=SURFACE,
            lightcolor=SURFACE_HOVER,
            darkcolor=SURFACE_HOVER,
            gripcount=0,
            arrowsize=px(12),
            width=px(12),
        )
        style.map(
            "Vertical.TScrollbar",
            background=[("pressed", MUTED), ("active", BORDER_STRONG)],
            lightcolor=[("pressed", MUTED), ("active", BORDER_STRONG)],
            darkcolor=[("pressed", MUTED), ("active", BORDER_STRONG)],
        )

        # ---- progress bars ----
        for name, color in (("Horizontal.TProgressbar", ACCENT), ("Item.Horizontal.TProgressbar", GREEN)):
            style.configure(
                name,
                thickness=px(8),
                troughcolor=SURFACE_ALT,
                background=color,
                bordercolor=SURFACE_ALT,
                lightcolor=color,
                darkcolor=color,
            )
        style.configure("TSeparator", background=BORDER)

    # --------------------------- UI ---------------------------------------

    def _set_status(self, text, sub, level):
        """Update the header status pill. level: 'busy', 'ok' or 'error'."""
        self._status_state = (text, sub, level)
        self._apply_status()

    def _apply_status(self):
        text, sub, level = self._status_state
        self.status.set(text)
        self.header_sub.set(f"{sub}   •   Icons: {self._icon_note}" if self._icon_note else sub)
        color = {"busy": AMBER, "ok": GREEN, "error": RED}.get(level, MUTED)
        self.status_dot.itemconfigure(self.status_dot_item, fill=color)

    def _icon_status(self, note):
        self._icon_note = note
        if hasattr(self, "status_dot"):
            self._apply_status()

    def _build_ui(self):
        # ---- header ----
        header = ttk.Frame(self, style="Header.TFrame", padding=(px(22), px(14)))
        header.pack(fill="x")
        header.columnconfigure(1, weight=1)
        logo_size = px(46)
        logo = tk.Canvas(header, width=logo_size, height=logo_size, background=HEADER_BG, highlightthickness=0)
        logo.grid(row=0, column=0, rowspan=2, padx=(0, px(14)))
        draw_logo(logo, logo_size)
        ttk.Label(header, text="SkyBlock Crafting Tracker", style="HeaderTitle.TLabel").grid(row=0, column=1, sticky="sw")
        self.header_sub = tk.StringVar(value="Loading item data…")
        ttk.Label(header, textvariable=self.header_sub, style="HeaderSub.TLabel").grid(row=1, column=1, sticky="nw")

        self.status = tk.StringVar(value="Loading Hypixel item data…")
        pill = tk.Frame(header, background=SURFACE_ALT, highlightbackground=BORDER, highlightcolor=BORDER, highlightthickness=1, borderwidth=0)
        pill.grid(row=0, column=2, rowspan=2, sticky="e")
        dot_size = px(10)
        self.status_dot = tk.Canvas(pill, width=dot_size, height=dot_size, background=SURFACE_ALT, highlightthickness=0)
        self.status_dot.pack(side="left", padx=(px(12), px(7)), pady=px(8))
        self.status_dot_item = self.status_dot.create_oval(1, 1, dot_size - 1, dot_size - 1, fill=AMBER, outline="")
        ttk.Label(pill, textvariable=self.status, style="Pill.TLabel").pack(side="left", padx=(0, px(14)), pady=px(6))
        tk.Frame(self, height=1, background=BORDER, borderwidth=0).pack(fill="x")

        # ---- toolbar ----
        toolbar = ttk.Frame(self, padding=(px(20), px(14), px(20), px(4)))
        toolbar.pack(fill="x")
        ttk.Button(toolbar, text="↻  Refresh API", command=self._load_api).pack(side="left")
        ttk.Button(toolbar, text="＋  Add / Edit Recipe", style="Accent.TButton", command=self._edit_recipe).pack(side="left", padx=(px(8), 0))
        ttk.Button(toolbar, text="⌕  Choose Goal", command=self._choose_root).pack(side="left", padx=(px(8), 0))
        ttk.Button(toolbar, text="Reset Progress", style="Danger.TButton", command=self._reset_obtained).pack(side="right")

        # ---- footer (packed before the main area so it stays at the bottom) ----
        footer = ttk.Frame(self, style="Header.TFrame", padding=(px(22), px(7)))
        footer.pack(side="bottom", fill="x")
        footer.columnconfigure(0, weight=1)
        ttk.Label(
            footer,
            text=f"{PLUS} Add one in any row   •   Right-click for options   •   Green rows are complete",
            style="Footer.TLabel",
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            footer,
            text="Recipes are stored locally  •  Progress saves automatically",
            style="Footer.TLabel",
        ).grid(row=0, column=1, sticky="e")

        main = ttk.Frame(self, padding=(px(20), px(12), px(20), px(16)))
        main.pack(fill="both", expand=True)
        main.columnconfigure(0, weight=1)
        main.rowconfigure(2, weight=1)

        # ---- stat cards ----
        stats = ttk.Frame(main)
        stats.grid(row=0, column=0, sticky="ew", pady=(0, px(12)))
        for column in range(3):
            stats.columnconfigure(column, weight=1, uniform="stat")

        self.goal_var = tk.StringVar(value="—")
        self.goal_id_var = tk.StringVar(value="")
        self.progress_var = tk.StringVar(value="0%")
        self.missing_var = tk.StringVar(value="0")

        pad = (px(16), px(10), px(16), px(12))
        goal_card = Card(stats, padding=pad, accent=VIOLET)
        goal_card.grid(row=0, column=0, sticky="nsew", padx=(0, px(6)))
        ttk.Label(goal_card.body, text="GOAL", style="Caption.Card.TLabel").pack(anchor="w")
        goal_row = ttk.Frame(goal_card.body, style="Card.TFrame")
        goal_row.pack(anchor="w", pady=(px(2), 0))
        self.goal_icon = ttk.Label(goal_row, style="Card.TLabel")
        self.goal_icon.pack(side="left", padx=(0, px(8)))
        self.goal_label = ttk.Label(goal_row, textvariable=self.goal_var, style="StatText.Card.TLabel")
        self.goal_label.pack(side="left")
        ttk.Label(goal_card.body, textvariable=self.goal_id_var, style="Muted.Card.TLabel").pack(anchor="w")

        progress_card = Card(stats, padding=pad, accent=ACCENT)
        progress_card.grid(row=0, column=1, sticky="nsew", padx=px(6))
        ttk.Label(progress_card.body, text="PROGRESS", style="Caption.Card.TLabel").pack(anchor="w")
        ttk.Label(progress_card.body, textvariable=self.progress_var, style="Stat.Card.TLabel").pack(anchor="w", pady=(px(0), px(6)))
        self.progress = ttk.Progressbar(progress_card.body, orient="horizontal", mode="determinate", maximum=100)
        self.progress.pack(fill="x")

        missing_card = Card(stats, padding=pad, accent=AMBER)
        missing_card.grid(row=0, column=2, sticky="nsew", padx=(px(6), 0))
        ttk.Label(missing_card.body, text="RAW MATERIALS STILL NEEDED", style="Caption.Card.TLabel").pack(anchor="w")
        ttk.Label(missing_card.body, textvariable=self.missing_var, style="Stat.Card.TLabel", foreground=AMBER).pack(anchor="w", pady=(px(0), px(2)))

        # ---- view toggle ----
        toggle_bar = ttk.Frame(main)
        toggle_bar.grid(row=1, column=0, sticky="ew", pady=(0, px(10)))
        toggle_bar.columnconfigure(2, weight=1)
        self.view_buttons = {}
        for column, (key, label) in enumerate((("tree", "Recipe Tree"), ("resources", "Total Resources"))):
            button = ttk.Button(toggle_bar, text=label, style="Seg.TButton", command=lambda k=key: self._show_view(k))
            button.grid(row=0, column=column, padx=(0, px(6)))
            self.view_buttons[key] = button
        self.view_hint = tk.StringVar(value="")
        ttk.Label(toggle_bar, textvariable=self.view_hint, style="Muted.TLabel").grid(row=0, column=2, sticky="e")

        views = ttk.Frame(main)
        views.grid(row=2, column=0, sticky="nsew")
        views.columnconfigure(0, weight=1)
        views.rowconfigure(0, weight=1)

        # ---- tree + side panel ----
        content = ttk.Frame(views)
        content.grid(row=0, column=0, sticky="nsew")
        self.tree_view = content
        content.columnconfigure(0, weight=1)
        content.rowconfigure(0, weight=1)

        tree_card = Card(content, padding=px(2))
        tree_card.grid(row=0, column=0, sticky="nsew")
        tree_body = tree_card.body
        tree_body.columnconfigure(0, weight=1)
        tree_body.rowconfigure(0, weight=1)

        columns = ("required", "owned", "remaining", "status", "add")
        self.tree = ttk.Treeview(tree_body, columns=columns, show="tree headings", selectmode="browse")
        self.tree.heading("#0", text="  CRAFTING TREE  /  ITEMS", anchor="w")
        self.tree.heading("required", text="Required")
        self.tree.heading("owned", text="Owned")
        self.tree.heading("remaining", text="Remaining")
        self.tree.heading("status", text="Status")
        self.tree.column("#0", width=px(510), minwidth=px(320), anchor="w")
        self.tree.column("required", width=px(90), anchor="e")
        self.tree.column("owned", width=px(85), anchor="e")
        self.tree.column("remaining", width=px(100), anchor="e")
        self.tree.column("status", width=px(105), anchor="center")
        self.tree.heading("add", text="")
        self.tree.column("add", width=px(48), anchor="center", stretch=False)
        self.tree.tag_configure("root", font=(FONT, 11, "bold"))
        self.tree.tag_configure("branch", font=(FONT, 10, "bold"))
        configure_rarity_tags(self.tree)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(tree_body, orient="vertical", command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind("<<TreeviewSelect>>", self._selection_changed)
        self.tree.bind("<Button-3>", self._right_click)
        self.tree.bind("<Button-1>", self._tree_click)
        self.tree.bind("<Motion>", self._tree_motion)

        legend = ttk.Frame(tree_body, style="Card.TFrame", padding=(px(10), px(8), px(10), px(6)))
        legend.grid(row=1, column=0, columnspan=2, sticky="ew")
        for tier in ("COMMON", "UNCOMMON", "RARE", "EPIC", "LEGENDARY", "MYTHIC"):
            ttk.Label(legend, text=f"●  {tier.title()}", style="Muted.Card.TLabel", foreground=RARITY_COLORS[tier]).pack(side="left", padx=(0, px(14)))

        side = Card(content, padding=px(18), width=px(330))
        side.grid(row=0, column=1, sticky="ns", padx=(px(12), 0))
        side.pack_propagate(False)
        sb = side.body
        wrap = px(290)

        ttk.Label(sb, text="SELECTED ITEM", style="Caption.Card.TLabel").pack(anchor="w")
        self.sel_name = tk.StringVar(value="Nothing selected")
        self.sel_id = tk.StringVar(value="Pick an item in the crafting tree")
        self.sel_loc = tk.StringVar(value="")
        self.sel_count = tk.StringVar(value="")
        sel_row = ttk.Frame(sb, style="Card.TFrame")
        sel_row.pack(fill="x", pady=(px(6), 0))
        self.sel_icon = ttk.Label(sel_row, style="Card.TLabel")
        self.sel_icon.pack(side="left", padx=(0, px(10)))
        self.sel_label = ttk.Label(sel_row, textvariable=self.sel_name, style="ItemName.Card.TLabel", wraplength=wrap - px(46), justify="left")
        self.sel_label.pack(side="left", fill="x", expand=True)
        ttk.Label(sb, textvariable=self.sel_id, style="Muted.Card.TLabel", wraplength=wrap, justify="left").pack(anchor="w")
        self.item_progress = ttk.Progressbar(sb, orient="horizontal", mode="determinate", maximum=100, style="Item.Horizontal.TProgressbar")
        self.item_progress.pack(fill="x", pady=(px(12), px(5)))
        ttk.Label(sb, textvariable=self.sel_count, style="Card.TLabel").pack(anchor="w")
        ttk.Label(sb, textvariable=self.sel_loc, style="Muted.Card.TLabel", wraplength=wrap, justify="left").pack(anchor="w", pady=(px(4), 0))

        ttk.Separator(sb).pack(fill="x", pady=px(16))
        ttk.Label(sb, text="OWNED QUANTITY", style="Caption.Card.TLabel").pack(anchor="w")
        self.owned_var = tk.StringVar(value="0")
        owned_row = ttk.Frame(sb, style="Card.TFrame")
        owned_row.pack(fill="x", pady=(px(6), px(8)))
        owned_row.columnconfigure(0, weight=1)
        owned_entry = ttk.Entry(owned_row, textvariable=self.owned_var)
        owned_entry.grid(row=0, column=0, sticky="ew")
        owned_entry.bind("<Return>", lambda _e: self._set_owned())
        ttk.Button(owned_row, text=f"{PLUS} 1", style="Plus.TButton", width=5, command=self._increment_selected).grid(row=0, column=1, padx=(px(6), 0))
        ttk.Button(sb, text="Set Owned Amount", style="Accent.TButton", command=self._set_owned).pack(fill="x")
        ttk.Button(sb, text="Mark Complete", command=self._mark_complete).pack(fill="x", pady=(px(7), 0))

        ttk.Separator(sb).pack(fill="x", pady=px(16))
        ttk.Label(sb, text="RECIPE", style="Caption.Card.TLabel").pack(anchor="w")
        self.recipe_hint = tk.StringVar(value="Select an item to see its recipe options.")
        ttk.Label(sb, textvariable=self.recipe_hint, style="Muted.Card.TLabel", wraplength=wrap, justify="left").pack(anchor="w", pady=(px(5), px(10)))
        self.recipe_button = ttk.Button(sb, text="Define / Edit Recipe", command=self._edit_selected_recipe)
        self.recipe_button.pack(fill="x")

        self._build_resource_view(views)
        self._show_view("tree")
        self.bind_all("<Button-1>", self._on_background_click, add="+")
        self.bind("<Escape>", lambda _e: self._deselect_current())
        self._update_panel_looks()
        self.bind("<Control-Key-1>", lambda _e: self._show_view("tree"))
        self.bind("<Control-Key-2>", lambda _e: self._show_view("resources"))

    # --------------------------- API --------------------------------------

    def _load_api(self):
        self._set_status("Downloading item data…", "Connecting to Hypixel…", "busy")
        threading.Thread(target=self._api_worker, daemon=True).start()

    def _api_worker(self):
        try:
            request = urllib.request.Request(API_URL, headers={"User-Agent": "SkyBlock-Crafting-Tree-Tracker/2.0"})
            with urllib.request.urlopen(request, timeout=20) as response:
                data = json.loads(response.read().decode("utf-8"))
            if not data.get("success"):
                raise RuntimeError("Hypixel API returned success=false")
            parsed = {}
            names = {}
            for item in data.get("items", []):
                item_id = item.get("id")
                name = item.get("name")
                if not item_id or not name:
                    continue
                item_id = normalize_id(item_id)
                parsed[item_id] = item
                names[name.casefold()] = item_id
            self.after(0, lambda: self._api_loaded(parsed, names))
        except Exception as exc:
            self.after(0, lambda: self._api_failed(str(exc)))

    def _api_loaded(self, items, names):
        self.items = items
        self.name_to_id = names
        if self.root_item not in self.items:
            candidate = self.name_to_id.get("titanium drill dr-x355")
            if candidate:
                self.root_item = candidate
        self._set_status(f"{len(self.items):,} items loaded", "Hypixel item catalogue connected", "ok")
        self._render_tree()
        self._save()

    def _api_failed(self, error):
        self._set_status("Offline / local data", "Could not connect to Hypixel", "error")
        self._render_tree()
        messagebox.showwarning(
            "Hypixel API unavailable",
            "The item catalogue could not be downloaded.\n\n"
            f"{error}\n\n"
            "Your saved recipes and progress can still be used; item names may fall back to IDs.",
        )

    # ----------------------- Item helpers ---------------------------------

    def display_name(self, item_id):
        item_id = normalize_id(item_id)
        if item_id in self.items:
            return self.items[item_id].get("name") or item_id
        return FALLBACK_NAMES.get(item_id, item_id.replace("_", " ").title())

    def available_item_ids(self):
        return sorted(set(self.items) | set(RECIPES) | set(FALLBACK_NAMES))

    def _pick_item(self, title="Choose Item", initial_id=None):
        return ItemPicker(
            self, title, self.available_item_ids(), self.display_name, initial_id,
            icon_for=lambda item_id: self.icons.get(item_id, network=False),
            tag_for=self.rarity_tag,
        ).result

    # ----------------------- Rarity, icons, deselect -----------------------

    def rarity_of(self, item_id):
        tier = str((self.items.get(item_id) or {}).get("tier", "")).strip().upper().replace(" ", "_")
        return tier if tier in RARITY_COLORS else "UNKNOWN"

    def rarity_tag(self, item_id, done=False, selected=False, alternate=False):
        """One mutually exclusive color tag for each row's current state."""
        tag = f"rar_{self.rarity_of(item_id)}"
        if done:
            return tag + ("_done_selected" if selected else "_done")
        if selected:
            return tag + "_selected"
        return tag + ("_alt" if alternate else "")

    def _highlight_tree_selection(self, tree):
        """Highlight only the old and new selected rows, preserving green rows.

        ttk selection-color style maps cover row tag backgrounds by default,
        so selection is rendered via mutually exclusive rarity/state tags.
        """
        attribute = "_tree_highlight" if tree is self.tree else "_resource_highlight"
        selected = tree.selection()
        current = selected[0] if selected else None
        previous = getattr(self, attribute, None)
        for node in {previous, current} - {None}:
            if not tree.exists(node):
                continue
            tags = tuple(t for t in tree.item(node, "tags") if not t.startswith("rar_"))
            if tree is self.tree:
                item_id = tags[1] if len(tags) >= 2 else ""
            else:
                kind, _sep, reference = node.partition("|")
                item_id = reference if kind == "r" else reference.split(PATH_SEP)[-1]
            visual = self.rarity_tag(
                item_id, done="complete" in tags, selected=node == current,
                alternate="stripe" in tags,
            )
            tree.item(node, tags=tags + (visual,))
        setattr(self, attribute, current)

    def rarity_color(self, item_id):
        return RARITY_COLORS[self.rarity_of(item_id)]

    def _icons_changed(self):
        """New icons arrived from the background thread; refresh soon (batched)."""
        if self._icon_job is None:
            self._icon_job = self.after(150, self._refresh_icons)

    def _iter_nodes(self, tree, parent=""):
        for child in tree.get_children(parent):
            yield child
            yield from self._iter_nodes(tree, child)

    def _refresh_icons(self):
        self._icon_job = None
        if not hasattr(self, "res_tree"):
            return
        for node in self._iter_nodes(self.tree):
            tags = self.tree.item(node, "tags")
            if len(tags) >= 2:
                self.tree.item(node, image=self.icons.get(normalize_id(tags[1])))
        for iid in self._iter_nodes(self.res_tree):
            kind, _sep, ref = iid.partition("|")
            item_id = ref if kind == "r" else ref.split(PATH_SEP)[-1]
            self.res_tree.item(iid, image=self.icons.get(item_id))
        self._update_panel_looks()

    def _update_panel_looks(self):
        """Icons and rarity colours for the goal card and both side panels."""
        self.goal_icon.configure(image=self.icons.get(self.root_item, big=True))
        self.goal_label.configure(foreground=self.rarity_color(self.root_item))
        if self.selected_item:
            self.sel_icon.configure(image=self.icons.get(self.selected_item, big=True))
            self.sel_label.configure(foreground=self.rarity_color(self.selected_item))
        else:
            self.sel_icon.configure(image="")
            self.sel_label.configure(foreground=TEXT_STRONG)
        res_item = None
        if self.res_sel:
            kind, ref = self.res_sel
            res_item = ref if kind == "resource" else ref[-1]
        if res_item:
            self.res_icon.configure(image=self.icons.get(res_item, big=True))
            self.res_label.configure(foreground=self.rarity_color(res_item))
        else:
            self.res_icon.configure(image="")
            self.res_label.configure(foreground=TEXT_STRONG)

    def _deselect_tree(self):
        selection = self.tree.selection()
        if selection:
            self.tree.selection_remove(*selection)
        self.selected_item = None
        self.selected_path = None
        self.sel_name.set("Nothing selected")
        self.sel_id.set("Pick an item in the crafting tree")
        self.sel_loc.set("")
        self.sel_count.set("")
        self.item_progress["value"] = 0
        self.owned_var.set("0")
        self.recipe_hint.set("Select an item to see its recipe options.")
        self.recipe_button.configure(text="Define / Edit Recipe")
        self._update_panel_looks()

    def _deselect_res(self):
        selection = self.res_tree.selection()
        if selection:
            self.res_tree.selection_remove(*selection)
        self.res_sel = None
        self.res_name.set("Nothing selected")
        self.res_id.set("Pick an item in the list")
        self.res_loc.set("")
        self.res_count.set("")
        self.res_progress["value"] = 0
        self.res_owned_var.set("0")
        self._update_panel_looks()

    def _deselect_current(self):
        if self.current_view == "resources":
            self._deselect_res()
        else:
            self._deselect_tree()

    def _on_background_click(self, event):
        """Clicking blank window background (not a control or panel) clears the selection."""
        widget = event.widget
        try:
            if widget.winfo_toplevel() is not self:
                return
            plain = widget is self or (
                widget.winfo_class() == "TFrame" and str(widget.cget("style")) in ("", "TFrame")
            )
        except (tk.TclError, AttributeError, KeyError):
            return
        if plain:
            self._deselect_current()

    # --------------------- Tree construction ------------------------------

    def _render_tree(self):
        for child in self.tree.get_children():
            self.tree.delete(child)

        self._tree_highlight = None
        root_name = self.display_name(self.root_item)
        root_path = (self.root_item,)
        root = self.tree.insert(
            "", "end", text=f"◆  {root_name}",
            values=("1", self.obtained.get(path_to_key(root_path), 0), "-", "", PLUS),
            open=True, tags=("root", self.root_item, make_path_tag(root_path), self.rarity_tag(self.root_item)),
            image=self.icons.get(self.root_item),
        )
        self._insert_recipe_children(root, self.root_item, 1, {self.root_item}, root_path)
        self._update_all_statuses()
        self.goal_var.set(root_name)
        self.goal_id_var.set(self.root_item)
        self._update_panel_looks()

    def _insert_recipe_children(self, parent, item_id, multiplier, ancestors, path):
        item_id = normalize_id(item_id)
        ingredients = RECIPES.get(item_id, [])
        new_ancestors = ancestors | {item_id}
        valid_ingredients = [(normalize_id(i), n) for i, n in ingredients if normalize_id(i) not in ancestors]
        for position, (ingredient_id, amount) in enumerate(valid_ingredients):
            required = amount * multiplier
            child_path = path + (ingredient_id,)
            crafted = bool(RECIPES.get(ingredient_id))
            # Native indentation and disclosure triangles remain interactive.
            # Connector glyphs distinguish siblings at a glance.
            connector = "└─" if position == len(valid_ingredients) - 1 else "├─"
            child_tags = ["item", ingredient_id, make_path_tag(child_path)]
            if crafted:
                child_tags.append("branch")
            else:
                child_tags.append("missing_recipe")
            # Alternate stripes by sibling position, keeping completed rows solid green.
            if position % 2:
                child_tags.append("stripe")
            child_tags.append(self.rarity_tag(ingredient_id, alternate=bool(position % 2)))
            node = self.tree.insert(
                parent,
                "end",
                text=f"{connector}  {self.display_name(ingredient_id)}",
                values=(required, self.obtained.get(path_to_key(child_path), 0), "", "", PLUS),
                open=len(child_path) == 2 and crafted,
                tags=tuple(child_tags),
                image=self.icons.get(ingredient_id),
            )
            self._insert_recipe_children(node, ingredient_id, required, new_ancestors, child_path)

    def _update_all_statuses(self):
        required_totals = defaultdict(int)
        self._collect_raw_requirements(self.root_item, 1, {self.root_item}, required_totals, (self.root_item,))
        total_required = sum(required_totals.values())
        total_covered = sum(min(required, max(0, int(self.obtained.get(key, 0)))) for key, required in required_totals.items())
        pct = (total_covered / total_required * 100) if total_required else 0
        self.progress["value"] = pct
        self.progress_var.set(f"{pct:.1f}%")
        missing_count = sum(max(0, required - max(0, int(self.obtained.get(key, 0)))) for key, required in required_totals.items())
        self.missing_var.set(f"{missing_count:,}")

        def walk(parent):
            for node in self.tree.get_children(parent):
                tags = self.tree.item(node, "tags")
                path = path_from_tags(tags)
                values = self.tree.item(node, "values")
                try:
                    required = int(values[0])
                except (ValueError, TypeError, IndexError):
                    required = 0
                owned = int(self.obtained.get(path_to_key(path), 0)) if path else 0
                remaining = max(0, required - owned)
                complete = required > 0 and owned >= required
                status = "✓ Complete" if complete else "Needed"
                item_id = normalize_id(tags[1]) if len(tags) >= 2 else ""
                base = tuple(x for x in tags if x != "complete" and not x.startswith("rar_"))
                if complete:
                    new_tags = base + ("complete", self.rarity_tag(item_id, True))
                else:
                    new_tags = base + (self.rarity_tag(item_id, alternate="stripe" in base),)
                # Put completion in the green background and status column instead
                # of inserting a checkmark in the tree label itself.
                text = self.tree.item(node, "text").removeprefix("✓ ")
                self.tree.item(node, text=text, values=(required, owned, remaining, status, PLUS), tags=new_tags)
                walk(node)

        roots = self.tree.get_children("")
        if roots:
            root = roots[0]
            root_path = (self.root_item,)
            owned = int(self.obtained.get(path_to_key(root_path), 0))
            complete = owned >= 1
            root_tags = ("root", self.root_item, make_path_tag(root_path))
            if complete:
                root_tags += ("complete",)
            root_tags += (self.rarity_tag(self.root_item, complete),)
            self.tree.item(root, values=(1, owned, max(0, 1 - owned), "✓ Complete" if complete else "Needed", PLUS), tags=root_tags)
            walk(root)
        self._highlight_tree_selection(self.tree)

        # Keep the Total Resources view in step with every change.
        self._res_dirty = True
        if self.current_view == "resources":
            self._refresh_resources()

    def _collect_raw_requirements(self, item_id, multiplier, ancestors, output, path):
        """Accumulate raw-material requirements keyed by tree-position path.

        Every leaf occurrence (a raw material at one specific spot in the
        tree) gets its own entry, so two occurrences of the same raw item
        under different parents are never merged.
        """
        item_id = normalize_id(item_id)
        ingredients = RECIPES.get(item_id)
        if not ingredients:
            output[path_to_key(path)] += multiplier
            return
        new_ancestors = ancestors | {item_id}
        for child, amount in ingredients:
            child = normalize_id(child)
            if child in ancestors:
                continue  # cycle guard
            self._collect_raw_requirements(child, multiplier * amount, new_ancestors, output, path + (child,))

    # ----------------------- Selection / progress -------------------------

    def _selection_changed(self, _event=None):
        self._highlight_tree_selection(self.tree)
        selection = self.tree.selection()
        if not selection:
            return
        node = selection[0]
        tags = self.tree.item(node, "tags")
        if not tags:
            return
        item_id = tags[1] if len(tags) >= 2 else tags[0]
        self.selected_item = normalize_id(item_id)
        self.selected_path = path_from_tags(tags) or (self.selected_item,)
        values = self.tree.item(node, "values")
        try:
            required = int(values[0]) if values else 0
        except (ValueError, TypeError):
            required = 0
        owned = int(self.obtained.get(path_to_key(self.selected_path), 0))
        ancestor_names = [self.display_name(pid) for pid in self.selected_path[:-1]]
        self.sel_name.set(self.display_name(self.selected_item))
        self.sel_id.set(self.selected_item)
        self.sel_loc.set("In: " + " → ".join(ancestor_names) if ancestor_names else "Root goal")
        self.sel_count.set(f"{owned:,} / {required:,} owned   •   {max(0, required - owned):,} remaining")
        self.item_progress["value"] = min(100, owned / required * 100) if required else 0
        self.owned_var.set(str(owned))
        self._update_panel_looks()
        if self.selected_item in RECIPES:
            self.recipe_hint.set(f"Recipe has {len(RECIPES[self.selected_item])} ingredient type(s). Edit it if the quantities need changing.")
            self.recipe_button.configure(text="Edit Recipe")
        else:
            self.recipe_hint.set("No local recipe is defined for this item. If it is a crafted/forged item, define its recipe here.")
            self.recipe_button.configure(text="Define Recipe")

    # ---- click-to-add (＋) column ----

    def _plus_cell_at(self, event):
        """Return the tree node under the pointer if it is in the ＋ column."""
        if self.tree.identify_region(event.x, event.y) != "cell":
            return None
        if self.tree.identify_column(event.x) != "#5":
            return None
        return self.tree.identify_row(event.y) or None

    def _tree_motion(self, event):
        self.tree.configure(cursor="hand2" if self._plus_cell_at(event) else "")

    def _tree_click(self, event):
        node = self._plus_cell_at(event)
        if not node:
            if self.tree.identify_region(event.x, event.y) == "nothing":
                self._deselect_tree()  # clicked empty space below the rows
                return "break"
            return None  # normal selection behaviour
        path = self._node_path(node)
        if not path:
            return "break"
        self.tree.selection_set(node)
        self._selection_changed()
        self._set_owned_amount(path, self.obtained.get(path_to_key(path), 0) + 1)
        return "break"

    def _node_path(self, node):
        tags = self.tree.item(node, "tags")
        if not tags:
            return None
        path = path_from_tags(tags)
        if path:
            return path
        item_id = tags[1] if len(tags) >= 2 else tags[0]
        return (normalize_id(item_id),) if item_id else None

    # ---- owned amounts (with cascade into ingredients) ----

    def _apply_owned_change(self, path, new_amount):
        """Set one tree position's owned amount and carry the change down its recipe.

        If this occurrence now has 3 more Refined Titanium, then 3 x 16
        Enchanted Titanium went into it, and 3 x 16 x 160 Titanium before
        that -- all at this same tree position, never touching Refined
        Titanium (or its ingredients) needed anywhere else in the tree.
        Decreases work the same way in reverse (never dropping below zero),
        so a stray click can be undone by lowering the amount again.
        """
        key = path_to_key(path)
        old = max(0, int(self.obtained.get(key, 0)))
        new = max(0, int(new_amount))
        if new == old:
            return
        self._store_owned(path, new)
        self._cascade_to_ingredients(path, new - old, set(path))

    def _store_owned(self, path, amount):
        key = path_to_key(path)
        if amount > 0:
            self.obtained[key] = amount
        else:
            self.obtained.pop(key, None)

    def _cascade_to_ingredients(self, path, delta, ancestors):
        item_id = path[-1]
        for ingredient_id, per_craft in RECIPES.get(item_id, []):
            ingredient_id = normalize_id(ingredient_id)
            if ingredient_id in ancestors:
                continue
            child_path = path + (ingredient_id,)
            current = max(0, int(self.obtained.get(path_to_key(child_path), 0)))
            updated = max(0, current + per_craft * delta)
            self._store_owned(child_path, updated)
            # Pass on what actually changed (matters when clamped at zero).
            self._cascade_to_ingredients(child_path, updated - current, ancestors | {ingredient_id})

    def _set_owned_amount(self, path, amount):
        self._apply_owned_change(path, amount)
        self._save()
        self._update_all_statuses()
        self._selection_changed()

    def _increment_selected(self):
        if not self.selected_path:
            messagebox.showinfo("Select an item", "Select an item in the tree first.")
            return
        self._set_owned_amount(self.selected_path, self.obtained.get(path_to_key(self.selected_path), 0) + 1)

    def _set_owned(self):
        if not self.selected_path:
            messagebox.showinfo("Select an item", "Select an item in the tree first.")
            return
        try:
            amount = int(self.owned_var.get().replace(",", "").strip())
            if amount < 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("Invalid amount", "Owned amount must be a non-negative whole number.")
            return
        self._set_owned_amount(self.selected_path, amount)

    def _mark_complete(self):
        if not self.selected_path:
            return
        selection = self.tree.selection()
        required = 1
        if selection:
            try:
                required = int(self.tree.item(selection[0], "values")[0])
            except (ValueError, TypeError, IndexError):
                pass
        owned = self.obtained.get(path_to_key(self.selected_path), 0)
        self._set_owned_amount(self.selected_path, max(owned, required))

    def _right_click(self, event):
        node = self.tree.identify_row(event.y)
        if not node:
            return
        self.tree.selection_set(node)
        self._selection_changed()
        menu = tk.Menu(
            self,
            tearoff=False,
            background=SURFACE_ALT,
            foreground=TEXT,
            activebackground=ACCENT,
            activeforeground="#ffffff",
            activeborderwidth=0,
            borderwidth=0,
            relief="flat",
            font=(FONT, 10),
        )
        menu.add_command(label="Mark Complete", command=self._mark_complete)
        menu.add_command(label="Set Owned Amount", command=self._set_owned)
        menu.add_separator()
        menu.add_command(label="Define / Edit Recipe", command=self._edit_selected_recipe)
        menu.tk_popup(event.x_root, event.y_root)

    def _reset_obtained(self):
        if not messagebox.askyesno("Reset progress", "Set every owned amount back to zero?"):
            return
        self.obtained.clear()
        self._save()
        self._update_all_statuses()

    # -------------------------- Recipes -----------------------------------

    def _edit_selected_recipe(self):
        if not self.selected_item:
            self._edit_recipe()
            return
        self._open_recipe_editor(self.selected_item)

    def _edit_recipe(self):
        output_id = self._pick_item("Choose Recipe Output")
        if output_id:
            self._open_recipe_editor(output_id)

    def _open_recipe_editor(self, output_id):
        recipe = RECIPES.get(normalize_id(output_id), [])
        editor = RecipeEditor(
            self,
            normalize_id(output_id),
            recipe,
            self.available_item_ids(),
            self.display_name,
            lambda title, initial=None: self._pick_item(title, initial),
            icon_for=lambda item_id: self.icons.get(item_id),
            color_for=self.rarity_color,
        )
        if editor.result is None:
            return
        output_id = normalize_id(output_id)
        if editor.result == []:
            RECIPES.pop(output_id, None)
        else:
            RECIPES[output_id] = editor.result
        self._save()
        self._render_tree()
        self._select_tree_item(output_id)

    def _select_tree_item(self, item_id):
        def search(parent):
            for node in self.tree.get_children(parent):
                tags = self.tree.item(node, "tags")
                current = tags[1] if len(tags) >= 2 else (tags[0] if tags else "")
                if normalize_id(current) == normalize_id(item_id):
                    self.tree.selection_set(node)
                    self.tree.see(node)
                    self._selection_changed()
                    return True
                if search(node):
                    return True
            return False
        search("")

    def _choose_root(self):
        root = self._pick_item("Choose Goal", self.root_item)
        if not root:
            return
        self.root_item = normalize_id(root)
        self._save()
        self._render_tree()

    # ------------------- Total Resources view ------------------------------

    def _show_view(self, name):
        """Switch between the recipe tree and the combined-resources view."""
        self.current_view = name
        for key, button in self.view_buttons.items():
            button.configure(style="SegOn.TButton" if key == name else "Seg.TButton")
        if name == "resources":
            self.tree_view.grid_remove()
            self.res_view.grid()
            self.view_hint.set("Every crafted item and raw material, combined across all sub-recipes — expand a row to see where it is used")
            if self._res_dirty:
                self._refresh_resources()
        else:
            self.res_view.grid_remove()
            self.tree_view.grid()
            self.view_hint.set("Expand a branch to trace ingredients  •  Green = complete")

    def _build_resource_view(self, parent):
        view = ttk.Frame(parent)
        view.grid(row=0, column=0, sticky="nsew")
        view.columnconfigure(0, weight=1)
        view.rowconfigure(0, weight=1)
        self.res_view = view

        self.res_filter = tk.StringVar()
        self.res_hide_done = tk.BooleanVar(value=False)
        self.res_show_crafted = tk.BooleanVar(value=True)
        self.res_show_raw = tk.BooleanVar(value=True)
        self.res_summary = tk.StringVar(value="")

        card = Card(view, padding=px(2))
        card.grid(row=0, column=0, sticky="nsew")
        body = card.body
        body.columnconfigure(0, weight=1)
        body.rowconfigure(1, weight=1)

        bar = ttk.Frame(body, style="Card.TFrame", padding=(px(10), px(10), px(10), px(8)))
        bar.grid(row=0, column=0, columnspan=2, sticky="ew")
        bar.columnconfigure(1, weight=1)
        ttk.Label(bar, text="Filter", style="Muted.Card.TLabel").grid(row=0, column=0, padx=(0, px(8)))
        ttk.Entry(bar, textvariable=self.res_filter).grid(row=0, column=1, sticky="ew")
        ttk.Checkbutton(bar, text="Crafted", variable=self.res_show_crafted, style="Card.TCheckbutton").grid(row=0, column=2, padx=(px(14), 0))
        ttk.Checkbutton(bar, text="Raw", variable=self.res_show_raw, style="Card.TCheckbutton").grid(row=0, column=3, padx=(px(10), 0))
        ttk.Checkbutton(bar, text="Hide completed", variable=self.res_hide_done, style="Card.TCheckbutton").grid(row=0, column=4, padx=(px(10), 0))
        ttk.Button(bar, text="Expand all", style="Small.TButton", command=lambda: self._res_set_open(True)).grid(row=0, column=5, padx=(px(14), px(6)))
        ttk.Button(bar, text="Collapse all", style="Small.TButton", command=lambda: self._res_set_open(False)).grid(row=0, column=6)

        columns = ("kind", "needed", "owned", "remaining", "status", "add")
        self.res_tree = ttk.Treeview(body, columns=columns, show="tree headings", selectmode="browse")
        self.res_tree.heading("#0", text="  RESOURCES  /  WHERE NEEDED", anchor="w")
        self.res_tree.heading("kind", text="Type")
        self.res_tree.heading("needed", text="Needed")
        self.res_tree.heading("owned", text="Owned")
        self.res_tree.heading("remaining", text="Remaining")
        self.res_tree.heading("status", text="Status")
        self.res_tree.heading("add", text="")
        self.res_tree.column("#0", width=px(460), minwidth=px(310), anchor="w")
        self.res_tree.column("kind", width=px(75), anchor="center")
        self.res_tree.column("needed", width=px(90), anchor="e")
        self.res_tree.column("owned", width=px(85), anchor="e")
        self.res_tree.column("remaining", width=px(100), anchor="e")
        self.res_tree.column("status", width=px(105), anchor="center")
        self.res_tree.column("add", width=px(48), anchor="center", stretch=False)
        self.res_tree.tag_configure("resource", font=(FONT, 10, "bold"))
        configure_rarity_tags(self.res_tree)
        self.res_tree.grid(row=1, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(body, orient="vertical", command=self.res_tree.yview)
        scroll.grid(row=1, column=1, sticky="ns")
        self.res_tree.configure(yscrollcommand=scroll.set)
        self.res_tree.bind("<<TreeviewSelect>>", self._res_selection_changed)
        self.res_tree.bind("<Button-1>", self._res_click)
        self.res_tree.bind("<Motion>", self._res_motion)

        footer = ttk.Frame(body, style="Card.TFrame", padding=(px(10), px(8), px(10), px(6)))
        footer.grid(row=2, column=0, columnspan=2, sticky="ew")
        ttk.Label(footer, textvariable=self.res_summary, style="Muted.Card.TLabel").pack(side="left")

        side = Card(view, padding=px(18), width=px(330))
        side.grid(row=0, column=1, sticky="ns", padx=(px(12), 0))
        side.pack_propagate(False)
        sb = side.body
        wrap = px(290)

        self.res_sel = None
        self.res_name = tk.StringVar(value="Nothing selected")
        self.res_id = tk.StringVar(value="Pick an item in the list")
        self.res_loc = tk.StringVar(value="")
        self.res_count = tk.StringVar(value="")
        self.res_owned_var = tk.StringVar(value="0")

        ttk.Label(sb, text="SELECTED ITEM", style="Caption.Card.TLabel").pack(anchor="w")
        res_row = ttk.Frame(sb, style="Card.TFrame")
        res_row.pack(fill="x", pady=(px(6), 0))
        self.res_icon = ttk.Label(res_row, style="Card.TLabel")
        self.res_icon.pack(side="left", padx=(0, px(10)))
        self.res_label = ttk.Label(res_row, textvariable=self.res_name, style="ItemName.Card.TLabel", wraplength=wrap - px(46), justify="left")
        self.res_label.pack(side="left", fill="x", expand=True)
        ttk.Label(sb, textvariable=self.res_id, style="Muted.Card.TLabel", wraplength=wrap, justify="left").pack(anchor="w")
        self.res_progress = ttk.Progressbar(sb, orient="horizontal", mode="determinate", maximum=100, style="Item.Horizontal.TProgressbar")
        self.res_progress.pack(fill="x", pady=(px(12), px(5)))
        ttk.Label(sb, textvariable=self.res_count, style="Card.TLabel").pack(anchor="w")
        ttk.Label(sb, textvariable=self.res_loc, style="Muted.Card.TLabel", wraplength=wrap, justify="left").pack(anchor="w", pady=(px(4), 0))

        ttk.Separator(sb).pack(fill="x", pady=px(16))
        ttk.Label(sb, text="OWNED QUANTITY", style="Caption.Card.TLabel").pack(anchor="w")
        row = ttk.Frame(sb, style="Card.TFrame")
        row.pack(fill="x", pady=(px(6), px(8)))
        row.columnconfigure(0, weight=1)
        entry = ttk.Entry(row, textvariable=self.res_owned_var)
        entry.grid(row=0, column=0, sticky="ew")
        entry.bind("<Return>", lambda _e: self._res_set_owned())
        ttk.Button(row, text=f"{PLUS} 1", style="Plus.TButton", width=5, command=self._res_increment).grid(row=0, column=1, padx=(px(6), 0))
        ttk.Button(sb, text="Set Owned Amount", style="Accent.TButton", command=self._res_set_owned).pack(fill="x")
        ttk.Button(sb, text="Mark Complete", command=self._res_mark_complete).pack(fill="x", pady=(px(7), 0))

        ttk.Separator(sb).pack(fill="x", pady=px(16))
        ttk.Label(sb, text="SYNCED WITH THE RECIPE TREE", style="Caption.Card.TLabel").pack(anchor="w")
        ttk.Label(
            sb,
            text="Amounts you add here fill the places this item is needed from top to bottom. "
                 "Crafting an item also credits its ingredients, and amounts you mark in the tree show up here too.",
            style="Muted.Card.TLabel",
            wraplength=wrap,
            justify="left",
        ).pack(anchor="w", pady=(px(5), px(10)))
        ttk.Button(sb, text="Show in Recipe Tree", command=self._res_show_in_tree).pack(fill="x")

        self.res_filter.trace_add("write", lambda *_: self._refresh_resources())
        self.res_hide_done.trace_add("write", lambda *_: self._refresh_resources())
        self.res_show_crafted.trace_add("write", lambda *_: self._refresh_resources())
        self.res_show_raw.trace_add("write", lambda *_: self._refresh_resources())

    # ---- data ----

    def _occurrences(self):
        """Every position in the tree (crafted items and raw materials), top to bottom.

        Parents come before their ingredients. The goal itself is left out
        unless it has no recipe. Raw-material totals mirror
        _collect_raw_requirements, so they always agree with the progress
        numbers. Returns dicts: {"path", "item", "required", "crafted"}.
        """
        result = []
        index = {}

        def add(path, item_id, multiplier, crafted):
            key = path_to_key(path)
            if key in index:
                index[key]["required"] += multiplier
            else:
                entry = {"path": path, "item": item_id, "required": multiplier, "crafted": crafted}
                index[key] = entry
                result.append(entry)

        def walk(item_id, multiplier, ancestors, path):
            item_id = normalize_id(item_id)
            ingredients = RECIPES.get(item_id)
            if len(path) > 1 or not ingredients:
                add(path, item_id, multiplier, bool(ingredients))
            if not ingredients:
                return
            new_ancestors = ancestors | {item_id}
            for child, amount in ingredients:
                child = normalize_id(child)
                if child in ancestors:
                    continue  # cycle guard
                walk(child, multiplier * amount, new_ancestors, path + (child,))

        walk(self.root_item, 1, {self.root_item}, (self.root_item,))
        return result

    def _owned_at(self, path):
        return max(0, int(self.obtained.get(path_to_key(path), 0)))

    def _resource_group(self, item_id):
        """(places, needed, covered) for one item across the whole tree."""
        places = [o for o in self._occurrences() if o["item"] == item_id]
        needed = sum(o["required"] for o in places)
        covered = sum(min(o["required"], self._owned_at(o["path"])) for o in places)
        return places, needed, covered

    def _adjust_resource(self, item_id, target):
        """Make this item's covered total equal `target`, spread over its places.

        Raising it fills the places top to bottom (first unfinished place
        first); lowering it takes from the bottom up. Existing per-place
        amounts are kept wherever possible. Each change goes through the same
        routine the tree uses, so crafting an item also credits its
        ingredients at that position.
        """
        places, needed, covered = self._resource_group(item_id)
        target = max(0, min(int(target), needed))
        delta = target - covered
        if delta > 0:
            for place in places:
                owned = self._owned_at(place["path"])
                add = min(max(0, place["required"] - owned), delta)
                if add:
                    self._apply_owned_change(place["path"], owned + add)
                    delta -= add
                if delta <= 0:
                    break
        elif delta < 0:
            to_remove = -delta
            for place in reversed(places):
                current = min(place["required"], self._owned_at(place["path"]))
                take = min(current, to_remove)
                if take:
                    self._apply_owned_change(place["path"], current - take)
                    to_remove -= take
                if to_remove <= 0:
                    break

    def _commit_progress(self):
        self._save()
        self._update_all_statuses()
        self._selection_changed()

    # ---- rebuilding the list ----

    def _refresh_resources(self):
        self._res_dirty = False
        tree = self.res_tree
        open_state = {iid: str(tree.item(iid, "open")).strip().lower() in ("1", "true", "yes") for iid in tree.get_children("")}
        selected = tree.selection()
        selected_iid = selected[0] if selected else None
        try:
            scroll_position = tree.yview()[0]
        except (tk.TclError, IndexError, TypeError):
            scroll_position = 0.0

        groups = {}
        for place in self._occurrences():
            groups.setdefault(place["item"], []).append(place)

        query = self.res_filter.get().strip().casefold()
        hide_done = self.res_hide_done.get()
        show_crafted = self.res_show_crafted.get()
        show_raw = self.res_show_raw.get()
        crafted_types = raw_types = raw_remaining = 0
        rows = []
        for item_id, places in groups.items():
            crafted = bool(RECIPES.get(item_id))
            name = self.display_name(item_id)
            needed = sum(p["required"] for p in places)
            covered = sum(min(p["required"], self._owned_at(p["path"])) for p in places)
            if crafted:
                crafted_types += 1
            else:
                raw_types += 1
                raw_remaining += needed - covered
            if (crafted and not show_crafted) or (not crafted and not show_raw):
                continue
            if query and query not in name.casefold() and query not in item_id.casefold():
                continue
            if hide_done and covered >= needed:
                continue
            rows.append((0 if crafted else 1, name.casefold(), item_id, name, crafted, places, needed, covered))
        rows.sort(key=lambda row: (row[0], row[1]))  # crafted items first, then raw; A-Z in each

        for child in tree.get_children(""):
            tree.delete(child)
        self._resource_highlight = None

        def status_text(required, covered):
            if required > 0 and covered >= required:
                return "✓ Complete"
            return "In progress" if covered > 0 else "Needed"

        for row_number, (_group, _sort, item_id, name, crafted, places, needed, covered) in enumerate(rows):
            complete = needed > 0 and covered >= needed
            iid = f"r|{item_id}"
            kind = "Crafted" if crafted else "Raw"
            tree.insert(
                "", "end", iid=iid, text=name,
                values=(kind, f"{needed:,}", f"{covered:,}", f"{needed - covered:,}", status_text(needed, covered), PLUS),
                open=open_state.get(iid, False),
                tags=("resource",) + (("stripe",) if row_number % 2 else ()) +
                     (("complete",) if complete else ()) +
                     (self.rarity_tag(item_id, complete, alternate=bool(row_number % 2)),),
                image=self.icons.get(item_id),
            )
            for place_number, place in enumerate(places):
                required = place["required"]
                owned = self._owned_at(place["path"])
                place_covered = min(required, owned)
                chain = " → ".join(self.display_name(p) for p in place["path"][1:-1])
                where = f"in {chain}" if chain else f"directly in {self.display_name(place['path'][0])}"
                done = required > 0 and owned >= required
                tree.insert(
                    iid, "end", iid=f"p|{path_to_key(place['path'])}",
                    text=f"{required:,} × {where}",
                    values=(kind, f"{required:,}", f"{place_covered:,}", f"{required - place_covered:,}", status_text(required, place_covered), PLUS),
                    tags=("occurrence",) + (("stripe",) if place_number % 2 else ()) +
                         (("complete",) if done else ()) +
                         (self.rarity_tag(item_id, done, alternate=bool(place_number % 2)),),
                    image=self.icons.get(item_id),
                )

        if groups:
            self.res_summary.set(
                f"{crafted_types:,} crafted item type{'s' if crafted_types != 1 else ''}   •   "
                f"{raw_types:,} raw material type{'s' if raw_types != 1 else ''}   •   "
                f"{raw_remaining:,} raw materials still to gather"
            )
        else:
            self.res_summary.set("Nothing to gather yet — define a recipe for the goal first.")

        try:
            tree.yview_moveto(scroll_position)
        except tk.TclError:
            pass
        if selected_iid and tree.exists(selected_iid):
            tree.selection_set(selected_iid)
            self._res_selection_changed()
        elif selected_iid:
            self._deselect_res()

    def _res_set_open(self, flag):
        for iid in self.res_tree.get_children(""):
            self.res_tree.item(iid, open=flag)

    # ---- selection panel ----

    def _res_selection_changed(self, _event=None):
        self._highlight_tree_selection(self.res_tree)
        selection = self.res_tree.selection()
        if not selection:
            return
        kind, _sep, ref = selection[0].partition("|")
        if kind == "r":
            places, needed, covered = self._resource_group(ref)
            if not places:
                return
            self.res_sel = ("resource", ref)
            self.res_name.set(self.display_name(ref))
            kind = "Crafted" if RECIPES.get(ref) else "Raw"
            self.res_id.set(f"{ref}  •  {kind}  •  needed in {len(places)} place{'s' if len(places) != 1 else ''}")
            self.res_loc.set("Owned items fill these places from top to bottom.")
            owned_total, required_total = covered, needed
        else:
            path = tuple(ref.split(PATH_SEP))
            place = next((o for o in self._occurrences() if o["path"] == path), None)
            if place is None:
                return
            self.res_sel = ("position", path)
            self.res_name.set(self.display_name(place["item"]))
            self.res_id.set(place["item"])
            self.res_loc.set("In: " + " → ".join(self.display_name(p) for p in path[:-1]))
            required_total = place["required"]
            owned_total = self._owned_at(path)
        remaining = max(0, required_total - owned_total)
        self.res_count.set(f"{owned_total:,} / {required_total:,} owned   •   {remaining:,} remaining")
        self.res_progress["value"] = min(100, owned_total / required_total * 100) if required_total else 0
        self.res_owned_var.set(str(owned_total))
        self._update_panel_looks()

    # ---- editing ----

    def _res_apply(self, amount):
        if not self.res_sel:
            messagebox.showinfo("Select an item", "Select an item in the list first.")
            return
        kind, ref = self.res_sel
        if kind == "resource":
            self._adjust_resource(ref, amount)
            self._commit_progress()
        else:
            self._set_owned_amount(ref, amount)

    def _res_set_owned(self):
        try:
            amount = int(self.res_owned_var.get().replace(",", "").strip())
            if amount < 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("Invalid amount", "Owned amount must be a non-negative whole number.")
            return
        self._res_apply(amount)

    def _res_increment(self):
        if not self.res_sel:
            messagebox.showinfo("Select an item", "Select an item in the list first.")
            return
        kind, ref = self.res_sel
        if kind == "resource":
            _places, _needed, covered = self._resource_group(ref)
            self._res_apply(covered + 1)
        else:
            self._res_apply(self._owned_at(ref) + 1)

    def _res_mark_complete(self):
        if not self.res_sel:
            return
        kind, ref = self.res_sel
        if kind == "resource":
            _places, needed, _covered = self._resource_group(ref)
            self._res_apply(needed)
        else:
            place = next((o for o in self._occurrences() if o["path"] == ref), None)
            if place:
                self._res_apply(max(self._owned_at(ref), place["required"]))

    def _res_plus_cell_at(self, event):
        if self.res_tree.identify_region(event.x, event.y) != "cell":
            return None
        if self.res_tree.identify_column(event.x) != "#6":
            return None
        return self.res_tree.identify_row(event.y) or None

    def _res_motion(self, event):
        self.res_tree.configure(cursor="hand2" if self._res_plus_cell_at(event) else "")

    def _res_click(self, event):
        iid = self._res_plus_cell_at(event)
        if not iid:
            if self.res_tree.identify_region(event.x, event.y) == "nothing":
                self._deselect_res()  # clicked empty space below the rows
                return "break"
            return None  # normal selection behaviour
        self.res_tree.selection_set(iid)
        self._res_selection_changed()
        self._res_increment()
        return "break"

    def _res_show_in_tree(self):
        if not self.res_sel:
            return
        kind, ref = self.res_sel
        if kind == "resource":
            places = [o for o in self._occurrences() if o["item"] == ref]
            if not places:
                return
            path = places[0]["path"]
        else:
            path = ref
        self._show_view("tree")
        self._select_tree_path(path)

    def _select_tree_path(self, path):
        tag = make_path_tag(path)

        def search(parent):
            for node in self.tree.get_children(parent):
                if tag in self.tree.item(node, "tags"):
                    self.tree.selection_set(node)
                    self.tree.focus(node)
                    self.tree.see(node)
                    self._selection_changed()
                    return True
                if search(node):
                    return True
            return False

        search("")

    # --------------------------- Save -------------------------------------

    def _save(self):
        recipe_data = {output: [[ingredient, amount] for ingredient, amount in ingredients] for output, ingredients in RECIPES.items()}
        data = {"root_item": self.root_item, "obtained": self.obtained, "recipes": recipe_data}
        try:
            save_json(DATA_FILE, data)
        except OSError:
            pass


if __name__ == "__main__":
    prepare_windows_process()
    app = SkyblockTracker()
    app.mainloop()
