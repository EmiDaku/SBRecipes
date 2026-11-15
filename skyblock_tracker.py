"""
Hypixel SkyBlock Crafting Tree Tracker — account + coin edition (v3.1)

* Recursive recipe tree, visual recipe editor. Recipes are added BY HAND and
  stay local: Hypixel's items resource has no recipe graph.
* Account sync: inventory, armour, equipment, quiver, Ender Chest, backpacks,
  personal vault, wardrobe, accessory/fishing/potion/candy bags, sacks, museum,
  plus purse / co-op bank / personal bank.
* Coins are a first-class recipe ingredient (item id COINS).
* Coin Cost tab: bazaar pricing, rolled-up craft values, manual prices and
  manual bazaar links for anything bazaar does not cover.
* Counting presets decide what "I have it" means.
* Island chests are NOT in the public API; a persistent manual layer covers them.

Policy: one pull per user action or per launch when stale, persisted minimum
interval, no background polling, no stat history, key never in source or build.

NOT AFFILIATED WITH OR ENDORSED BY HYPIXEL INC. OR MOJANG STUDIOS.
Requires Python 3.9+ and Tkinter. No third-party packages.
"""
from __future__ import annotations

import base64
import gzip
import json
import os
import struct
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import tkinter as tk
from tkinter import ttk, messagebox, filedialog

import hypixel_sync as hs
import hypixel_economy as he

API_URL = "https://api.hypixel.net/v2/resources/skyblock/items"
DISCLAIMER = "Not affiliated with or endorsed by Hypixel Inc. or Mojang Studios."
COINS = hs.COINS


def _data_dir() -> Path:
    name = "skyblock_tracker_data.json"
    if not getattr(sys, "frozen", False):
        return Path(__file__).resolve().parent
    import shutil
    base = Path(os.environ.get("APPDATA") or Path.home()) / "SkyBlockTracker"
    base.mkdir(parents=True, exist_ok=True)
    beside = Path(sys.executable).with_name(name)
    if not (base / name).exists() and beside.exists():
        try:
            shutil.copy2(beside, base / name)
        except OSError:
            pass
    return base


DATA_DIR = _data_dir()
DATA_FILE = DATA_DIR / "skyblock_tracker_data.json"
KEY_FILE = DATA_DIR / "hypixel_api_key.txt"
CACHE_FILE = DATA_DIR / "hypixel_cache.json"
STATE_FILE = DATA_DIR / "hypixel_state.json"
BAZAAR_FILE = DATA_DIR / "hypixel_bazaar.json"
BAZAAR_STATE = DATA_DIR / "hypixel_bazaar_state.json"

BG, SURFACE, SURFACE_ALT, SURFACE_HOVER = "#0b1017", "#141b26", "#1c2636", "#263248"
BORDER, BORDER_STRONG, HEADER_BG = "#263043", "#3a4963", "#080c12"
TEXT, TEXT_STRONG, MUTED = "#e6e9ef", "#ffffff", "#8593aa"
ACCENT, ACCENT_HOVER, ACCENT_PRESSED = "#4c8dff", "#3b7bf0", "#2f66cf"
SELECT_BG, TREE_ALT_BG = "#29456b", "#182331"
TREE_DONE_BG, TREE_DONE_SELECTED_BG, TREE_DONE_FG = "#195138", "#26734e", "#e1ffed"
GREEN, AMBER, RED, VIOLET, CYAN, GOLD = "#4ade80", "#fbbf24", "#f87171", "#a78bfa", "#22d3ee", "#f5c542"
FONT, PLUS, UI_SCALE = "Segoe UI", "＋", 1.0


def px(value):
    return max(1, int(round(value * UI_SCALE)))


def prepare_windows_process():
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


def enable_dark_title_bar(window):
    if sys.platform != "win32":
        return
    try:
        import ctypes
        window.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        value = ctypes.c_int(1)
        for attribute in (20, 19):
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value)) == 0:
                break
    except Exception:
        pass


def set_window_icon(window):
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    icon = base / "myicon.ico"
    if icon.exists():
        try:
            window.iconbitmap(default=str(icon))
        except tk.TclError:
            pass


def center_window(window, width, height, parent=None):
    window.update_idletasks()
    sw, sh = window.winfo_screenwidth(), window.winfo_screenheight()
    width, height = min(width, sw - px(40)), min(height, sh - px(100))
    if parent is not None:
        x = parent.winfo_rootx() + (parent.winfo_width() - width) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - height) // 2
    else:
        x, y = (sw - width) // 2, (sh - height) // 2 - px(20)
    x = max(0, min(x, sw - width))
    y = max(0, min(y, sh - height - px(40)))
    window.geometry(f"{width}x{height}+{x}+{y}")


class Card(tk.Frame):
    def __init__(self, parent, padding=12, accent=None, **kwargs):
        super().__init__(parent, background=SURFACE, highlightbackground=BORDER,
                         highlightcolor=BORDER, highlightthickness=1, borderwidth=0, **kwargs)
        if accent:
            tk.Frame(self, height=px(3), background=accent, borderwidth=0).pack(side="top", fill="x")
        self.body = ttk.Frame(self, style="Card.TFrame", padding=padding)
        self.body.pack(side="top", fill="both", expand=True)


def _round_rect(canvas, x1, y1, x2, y2, r, **kw):
    points = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
              x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return canvas.create_polygon(points, smooth=True, **kw)


def draw_logo(canvas, size):
    s = size / 100.0
    canvas.delete("all")
    _round_rect(canvas, 2 * s, 2 * s, 98 * s, 98 * s, 20 * s, fill=SURFACE, outline=BORDER_STRONG)
    root, ml, mr = (50, 26), (30, 54), (70, 54)
    leaves = [(18, 80), (42, 80), (58, 80), (82, 80)]
    w = max(2, int(3 * s))

    def link(a, b, c):
        canvas.create_line(a[0] * s, a[1] * s, b[0] * s, b[1] * s, fill=c, width=w)

    def dot(c, r, col):
        canvas.create_oval((c[0] - r) * s, (c[1] - r) * s, (c[0] + r) * s, (c[1] + r) * s,
                           fill=col, outline="")

    link(root, ml, ACCENT); link(root, mr, ACCENT)
    link(ml, leaves[0], GREEN); link(ml, leaves[1], GREEN)
    link(mr, leaves[2], AMBER); link(mr, leaves[3], GOLD)
    dot(root, 11, ACCENT); dot(ml, 8, ACCENT); dot(mr, 8, ACCENT)
    dot(leaves[0], 6, GREEN); dot(leaves[1], 6, GREEN)
    dot(leaves[2], 6, AMBER); dot(leaves[3], 6, GOLD)


RARITY_COLORS = {
    "COMMON": "#ffffff", "UNCOMMON": "#55ff55", "RARE": "#6f8dff", "EPIC": "#b65cff",
    "LEGENDARY": "#ffaa00", "MYTHIC": "#ff55ff", "DIVINE": "#55ffff", "SPECIAL": "#ff5555",
    "VERY_SPECIAL": "#ff4444", "ULTIMATE": "#ff4444", "ADMIN": "#ff4444",
    "UNOBTAINABLE": "#ff5555", "UNKNOWN": TEXT,
}


def configure_rarity_tags(tree):
    for tier, color in RARITY_COLORS.items():
        tree.tag_configure(f"rar_{tier}", foreground=color, background=SURFACE)
        tree.tag_configure(f"rar_{tier}_alt", foreground=color, background=TREE_ALT_BG)
        tree.tag_configure(f"rar_{tier}_selected", foreground=TEXT_STRONG, background=SELECT_BG)
        tree.tag_configure(f"rar_{tier}_done", foreground=TREE_DONE_FG, background=TREE_DONE_BG,
                           font=(FONT, 10, "bold"))
        tree.tag_configure(f"rar_{tier}_done_selected", foreground="#ffffff",
                           background=TREE_DONE_SELECTED_BG, font=(FONT, 10, "bold"))


# ------------------------------------------------------------------ item icons
class IconStore:
    API_URL = "https://api.github.com/repos/furfsky/reborn/releases/latest"
    VANILLA_URL = "https://raw.githubusercontent.com/rom1504/minecraft-assets/master/data/1.8.8/{}.png"
    RECHECK_SECONDS = 7 * 24 * 3600
    SKIP_PATHS = ("/gui/", "/entity/", "/environment/", "/particle/", "/font/", "/colormap/",
                  "/painting/", "/mob_effect/", "/models/", "/misc/", "/map/", "/trims/", "/armor/")
    VANILLA_ALIASES = {
        "REDSTONE": "redstone_dust", "SULPHUR": "gunpowder", "NETHER_STALK": "nether_wart",
        "CARROT_ITEM": "carrot", "POTATO_ITEM": "potato", "RAW_FISH": "fish_cod_raw",
        "COOKED_FISH": "fish_cod_cooked", "SEEDS": "seeds_wheat", "SNOW_BALL": "snowball",
        "EYE_OF_ENDER": "ender_eye", "EXP_BOTTLE": "experience_bottle", "FIREBALL": "fireball",
        "BOOK_AND_QUILL": "book_writable", "MELON": "melon",
    }
    VANILLA_SKIP = {"SKULL_ITEM", "INK_SACK", "MONSTER_EGG", "POTION", "BANNER", "WOOL",
                    "LOG", "LOG_2", "LEAVES"}

    def __init__(self, root, cache_dir, material_of, on_change, on_note):
        self.root, self.dir = root, Path(cache_dir)
        self.material_of, self.on_change, self.on_note = material_of, on_change, on_note
        self._lock = threading.Lock()
        self._zips, self._index, self._index_done = [], {}, False
        self._member_cache, self._models, self._model_by_stem = {}, {}, {}
        self._definitions, self._all_pngs, self._resolved = {}, {}, {}
        self._report_info, self._cat_notes = [], []
        self._bytes, self._photos, self._placeholders = {}, {}, {}
        self._bad, self._missing, self._queue, self._queued = set(), set(), [], set()
        self._running = True
        threading.Thread(target=self._run, daemon=True).start()

    def get(self, item_id, big=False, network=True):
        key = (item_id, big)
        if key in self._photos:
            return self._photos[key]
        data = self._lookup(item_id)
        if data:
            photo = self._make_photo(data, big)
            if photo is not None:
                self._photos[key] = photo
                return photo
            self._bad.add(item_id)
            self._bytes.pop(item_id, None)
        elif network and (not self._index_done or item_id not in self._missing):
            self._enqueue(item_id)
        return self._placeholder(big)

    def _lookup(self, item_id):
        if item_id in self._bytes:
            return self._bytes[item_id]
        if not self._index_done:
            return None
        data = self._from_furfsky(item_id)
        if data is None:
            data = self._from_vanilla_disk(item_id)
        if data is not None:
            self._bytes[item_id] = data
        return data

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
                matches = [k for k in self._index if k.endswith("_" + stem)]
                if matches:
                    result = (self._index[min(matches, key=len)], "suffix")
        except Exception:
            result = (None, "")
        self._member_cache[stem] = result
        return result

    def _from_models(self, stem):
        refs = []
        for entry in self._definitions.get(stem, []):
            refs.extend(self._definition_models(entry))
        refs.extend(self._model_by_stem.get(stem, []))
        for ref in refs:
            texture = self._texture_of_model(ref, 0)
            if texture is not None:
                return texture
        return None

    def _definition_models(self, entry):
        data, found = self._read_json(entry), []

        def walk(node):
            if isinstance(node, dict):
                for k, v in node.items():
                    if k == "model" and isinstance(v, str):
                        found.append(v.lower())
                    else:
                        walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)

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
                ns, _s, path = (value if ":" in value else "minecraft:" + value).partition(":")
                if ns == "minecraft" and path in ("item/paper", "item/air", "item/barrier",
                                                  "item/empty", "item/stick", "item/name_tag",
                                                  "item/map"):
                    continue
                texture = self._all_pngs.get(f"{ns}/{path}")
                if texture is not None:
                    return texture
        parent = data.get("parent")
        if isinstance(parent, str) and ":" in parent and not parent.lower().startswith("minecraft:"):
            return self._texture_of_model(parent.lower(), depth + 1)
        return None

    def _vanilla_candidates(self, item_id):
        if str(item_id).upper() == COINS:
            return ["items/gold_nugget"]
        material = str(self.material_of(item_id) or "").upper()
        if not material or material in self.VANILLA_SKIP:
            return []
        if material == "PAPER" and item_id.upper() != "PAPER":
            return []
        name = self.VANILLA_ALIASES.get(material, material.lower())
        return [f"items/{name}", f"blocks/{name}"]

    def _vanilla_path(self, rel):
        return self.dir / "vanilla" / (rel.replace("/", "_") + ".png")

    def _from_vanilla_disk(self, item_id):
        for rel in self._vanilla_candidates(item_id):
            path = self._vanilla_path(rel)
            if path.exists():
                try:
                    return path.read_bytes()
                except OSError:
                    pass
        return None

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
            try:
                found = self._from_furfsky(item_id) is not None or self._fetch_vanilla(item_id)
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
        for rel in self._vanilla_candidates(item_id):
            path, marker = self._vanilla_path(rel), self._vanilla_path(rel).with_suffix(".missing")
            if path.exists():
                self._bytes[item_id] = path.read_bytes()
                return True
            if marker.exists():
                continue
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                req = urllib.request.Request(self.VANILLA_URL.format(rel),
                                             headers={"User-Agent": hs.APP_AGENT})
                with urllib.request.urlopen(req, timeout=15) as response:
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
        self._note("FurfSky Reborn" if self._index else "Minecraft textures only")

    def _cached_zips(self):
        return sorted(self.dir.glob("furfsky_*.zip"), key=lambda p: p.stat().st_size, reverse=True)

    def _download_furfsky(self, meta_path, meta):
        req = urllib.request.Request(self.API_URL, headers={
            "User-Agent": hs.APP_AGENT, "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=20) as response:
            release = json.loads(response.read().decode("utf-8"))
        tag = str(release.get("tag_name", ""))
        existing = self._cached_zips()
        if existing and meta.get("tag") == tag:
            save_json(meta_path, {"tag": tag, "checked": time.time()})
            return
        assets = [a for a in release.get("assets", [])
                  if str(a.get("name", "")).lower().endswith(".zip") and a.get("browser_download_url")]
        downloaded = []
        for number, asset in enumerate(assets[:4]):
            if int(asset.get("size", 0) or 0) > 80_000_000:
                continue
            dest = self.dir / f"furfsky_{number}.zip"
            partial = dest.with_suffix(".part")
            req = urllib.request.Request(asset["browser_download_url"],
                                         headers={"User-Agent": hs.APP_AGENT})
            with urllib.request.urlopen(req, timeout=60) as response, partial.open("wb") as out:
                while True:
                    chunk = response.read(1 << 16)
                    if not chunk:
                        break
                    out.write(chunk)
            downloaded.append((partial, dest))
        if not downloaded:
            return
        for old in existing:
            try:
                old.unlink()
            except OSError:
                pass
        for partial, dest in downloaded:
            partial.replace(dest)
        save_json(meta_path, {"tag": tag, "checked": time.time()})

    def _decode_cats(self, packed, output):
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
            raise ValueError("Not a Catharsis CATS archive")
        version = number(">B")
        entries = []

        def walk(prefix, depth=0):
            if depth > 30:
                raise ValueError("Catharsis directory too deep")
            for _ in range(number(">H")):
                kind = number(">B")
                name = consume(number(">B")).decode("ascii", "replace")
                path = f"{prefix}{name}"
                if kind == 1:
                    walk(path + "/", depth + 1)
                elif kind == 0:
                    offset, size, comp = number(">i"), number(">i"), number(">B")
                    entries.append((path, offset, size, comp))
                    if len(entries) > 100000:
                        raise ValueError("Too many Catharsis entries")
                else:
                    raise ValueError(f"Unknown Catharsis entry type: {kind}")

        walk("")
        data_start, exported = position, 0
        for path, offset, size, comp in entries:
            name = path.replace("\\", "/").lstrip("/")
            lower = name.lower()
            if ("/assets/" not in "/" + lower or not lower.endswith((".png", ".json"))
                    or ".." in name.split("/")):
                continue
            if offset < 0 or size < 0 or size > 30_000_000:
                continue
            first, last = data_start + offset, data_start + offset + size
            if last > len(packed):
                continue
            blob = packed[first:last]
            try:
                if comp == 0xfe:
                    blob = gzip.decompress(blob)
                elif comp != 0xff:
                    continue
            except (OSError, EOFError):
                continue
            if len(blob) > 30_000_000:
                continue
            output.writestr(name, blob)
            exported += 1
        return version, len(entries), exported

    def _expanded_zip(self, source):
        with zipfile.ZipFile(source) as archive:
            containers = [n for n in archive.namelist() if n.lower().endswith(".cats")]
            if not containers:
                return source
            expanded = self.dir / f"expanded_{source.stem}.zip"
            if expanded.exists() and expanded.stat().st_mtime_ns >= source.stat().st_mtime_ns:
                if zipfile.is_zipfile(expanded):
                    self._cat_notes.append(f"using decoded CATS cache: {expanded.name}")
                    return expanded
            partial = expanded.with_suffix(".tmp")
            try:
                total, notes = 0, []
                with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_STORED,
                                     allowZip64=True) as out:
                    for filename in containers:
                        ver, count, exported = self._decode_cats(archive.read(filename), out)
                        total += exported
                        notes.append(f"{filename}: v{ver}, {count} files, {exported} assets")
                if total == 0:
                    raise ValueError("Catharsis pack contained no indexable assets")
                os.replace(partial, expanded)
                self._cat_notes.extend(notes)
                return expanded
            finally:
                if partial.exists():
                    partial.unlink()

    def _build_index(self, zip_paths):
        main, vanilla_ns = {}, {}
        ranks = {id(main): {}, id(vanilla_ns): {}}
        models, by_stem, definitions, all_pngs, namespaces, zips = {}, {}, {}, {}, {}, []
        self._cat_notes = []

        def put(target, stem, entry, rank):
            known = ranks[id(target)]
            if stem not in target or rank < known[stem]:
                target[stem], known[stem] = entry, rank

        for path in zip_paths:
            try:
                archive = zipfile.ZipFile(self._expanded_zip(path))
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
                ns, rest = parts[0], "/".join(parts[1:])
                base = rest.rsplit("/", 1)[-1]
                if lower.endswith(".json"):
                    if rest.startswith("models/"):
                        key = f"{ns}:{rest[7:-5]}"
                        models[key] = (number, member)
                        if ns != "minecraft":
                            by_stem.setdefault(base[:-5], []).append(key)
                    elif rest.startswith("items/") and ns != "minecraft":
                        definitions.setdefault(base[:-5], []).append((number, member))
                    continue
                if not lower.endswith(".png") or not rest.startswith("textures/"):
                    continue
                all_pngs[f"{ns}/{rest[9:-4]}"] = (number, member)
                namespaces[ns] = namespaces.get(ns, 0) + 1
                if any(part in "/" + rest for part in self.SKIP_PATHS):
                    continue
                stem = base[:-4]
                if not stem or stem.startswith("_"):
                    continue
                rank = 0 if ("/item/" in "/" + rest or "/items/" in "/" + rest) else 1
                put(vanilla_ns if ns == "minecraft" else main, stem, (number, member), rank)
        self._zips = zips
        self._index = main if len(main) >= 50 else {**vanilla_ns, **main}
        self._models, self._model_by_stem = models, by_stem
        self._definitions, self._all_pngs = definitions, all_pngs
        self._report_info = [
            "zips: " + ", ".join(f"{p.name} ({p.stat().st_size:,} bytes)" for p in zip_paths),
            f"textures per namespace: {dict(sorted(namespaces.items(), key=lambda kv: -kv[1])[:12])}",
            f"index: {len(self._index)}  models: {len(models)}  definitions: {len(definitions)}",
            *self._cat_notes]

    def _write_report(self):
        try:
            lines = ["FurfSky icon report", ""] + self._report_info + ["", "sample indexed names:"]
            for stem in sorted(self._index)[:25]:
                lines.append(f"  {stem} -> {self._index[stem][1]}")
            lines += ["", "icons resolved for this tree:"]
            for item_id, (how, member) in sorted(self._resolved.items())[:300]:
                lines.append(f"  {item_id:36s} {how:7s} {member}")
            (self.dir / "furfsky_report.txt").write_text("\n".join(lines), encoding="utf-8")
        except OSError:
            pass

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
        width, target = raw.width(), px(48 if big else 32)
        if width >= target:
            f = max(1, int(width / target + 0.5))
            return raw.subsample(f, f) if f > 1 else raw
        f = max(1, int(target / width + 0.5))
        return raw.zoom(f, f) if f > 1 else raw

    def _make_photo(self, data, big):
        try:
            raw = tk.PhotoImage(data=base64.b64encode(data))
            w, h = raw.width(), raw.height()
            if w <= 0 or h <= 0:
                return None
            if h >= 2 * w and h % w == 0:
                frame = tk.PhotoImage()
                frame.tk.call(str(frame), "copy", str(raw), "-from", 0, 0, w, w)
                raw = frame
            return self._scale(raw, big)
        except (tk.TclError, ValueError):
            return None

    def _placeholder(self, big):
        if big not in self._placeholders:
            raw = tk.PhotoImage(width=16, height=16)
            raw.put(BORDER_STRONG, to=(0, 0, 16, 16))
            raw.put(SURFACE_ALT, to=(1, 1, 15, 15))
            raw.put(BORDER_STRONG, to=(5, 5, 11, 11))
            self._placeholders[big] = self._scale(raw, big)
        return self._placeholders[big]


# ------------------------------------------------------- starter recipe database
RECIPES = {
    "TITANIUM_DRILL_1": [("REFINED_TITANIUM", 8), ("REFINED_MITHRIL", 8), ("DRILL_MOTOR", 1),
                         ("FUEL_CANISTER", 1), ("GOLDEN_PLATE", 6)],
    "REFINED_TITANIUM": [("ENCHANTED_TITANIUM", 16)],
    "ENCHANTED_TITANIUM": [("TITANIUM", 160)],
    "REFINED_MITHRIL": [("ENCHANTED_MITHRIL", 160)],
    "ENCHANTED_MITHRIL": [("MITHRIL", 160)],
    "DRILL_MOTOR": [("TREASUREITE", 10), ("ENCHANTED_IRON_BLOCK", 1),
                    ("ENCHANTED_REDSTONE_BLOCK", 3), ("GOLDEN_PLATE", 1)],
    "ENCHANTED_IRON_BLOCK": [("ENCHANTED_IRON", 160)],
    "ENCHANTED_IRON": [("IRON_INGOT", 160)],
    "ENCHANTED_REDSTONE_BLOCK": [("ENCHANTED_REDSTONE", 160)],
    "ENCHANTED_REDSTONE": [("REDSTONE", 160)],
    "GOLDEN_PLATE": [("REFINED_DIAMOND", 1), ("ENCHANTED_GOLD_BLOCK", 2), ("GLACITE_JEWEL", 5)],
    "REFINED_DIAMOND": [("ENCHANTED_DIAMOND_BLOCK", 2)],
    "ENCHANTED_DIAMOND_BLOCK": [("ENCHANTED_DIAMOND", 160)],
    "ENCHANTED_DIAMOND": [("DIAMOND", 160)],
    "ENCHANTED_GOLD_BLOCK": [("ENCHANTED_GOLD", 160)],
    "ENCHANTED_GOLD": [("GOLD_INGOT", 160)],
    "FUEL_CANISTER": [("ENCHANTED_COAL_BLOCK", 2)],
    "ENCHANTED_COAL_BLOCK": [("ENCHANTED_COAL", 160)],
    "ENCHANTED_COAL": [("COAL", 160)],
}
ALIASES = {"TREASURITE": "TREASURITE", "TREASUREITE": "TREASURITE",
           "IRON": "IRON_INGOT", "GOLD": "GOLD_INGOT",
           "COIN": COINS, "COINS": COINS, "MONEY": COINS}
FALLBACK_NAMES = {
    COINS: "Coins", "IRON_INGOT": "Iron Ingot", "GOLD_INGOT": "Gold Ingot",
    "TITANIUM": "Titanium", "MITHRIL": "Mithril", "REDSTONE": "Redstone", "DIAMOND": "Diamond",
    "COAL": "Coal", "GLACITE_JEWEL": "Glacite Jewel", "TREASURITE": "Treasurite",
    "TITANIUM_DRILL_1": "Titanium Drill DR-X355", "REFINED_TITANIUM": "Refined Titanium",
    "REFINED_MITHRIL": "Refined Mithril", "DRILL_MOTOR": "Drill Motor",
    "FUEL_CANISTER": "Fuel Canister", "GOLDEN_PLATE": "Golden Plate",
    "REFINED_DIAMOND": "Refined Diamond", "ENCHANTED_TITANIUM": "Enchanted Titanium",
    "ENCHANTED_MITHRIL": "Enchanted Mithril", "ENCHANTED_IRON": "Enchanted Iron",
    "ENCHANTED_IRON_BLOCK": "Enchanted Iron Block", "ENCHANTED_REDSTONE": "Enchanted Redstone",
    "ENCHANTED_REDSTONE_BLOCK": "Enchanted Redstone Block",
    "ENCHANTED_DIAMOND": "Enchanted Diamond", "ENCHANTED_DIAMOND_BLOCK": "Enchanted Diamond Block",
    "ENCHANTED_GOLD": "Enchanted Gold", "ENCHANTED_GOLD_BLOCK": "Enchanted Gold Block",
    "ENCHANTED_COAL": "Enchanted Coal", "ENCHANTED_COAL_BLOCK": "Enchanted Coal Block",
}
MAX_TREE_NODES, MAX_DEPTH, PATH_SEP = 12000, 24, ">"


def normalize_id(item_id):
    item_id = str(item_id).strip().upper().replace(" ", "_")
    return ALIASES.get(item_id, item_id)


def path_to_key(path):
    return PATH_SEP.join(path)


def load_json(path, default):
    try:
        with Path(path).open("r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def save_json(path, data):
    path = Path(path)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    tmp.replace(path)


def comma(value):
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "0"


# ------------------------------------------------------------------ item picker
class ItemPicker(tk.Toplevel):
    MAX_SHOWN = 500

    def __init__(self, parent, title, items, display_name, initial_id=None,
                 icon_for=None, extra_note=None):
        super().__init__(parent)
        self.icon_for, self.display_name = icon_for, display_name
        self.all_ids = sorted(set(items), key=lambda x: display_name(x).casefold())
        self.result, self.matches = None, []
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
        ttk.Label(header, text="Choose an item", style="HeaderTitle.TLabel",
                  font=(FONT, 15, "bold")).pack(anchor="w")
        ttk.Label(header, text=extra_note or title, style="HeaderSub.TLabel").pack(anchor="w", pady=(2, 0))
        self.search_var = tk.StringVar()
        search = ttk.Entry(self, textvariable=self.search_var, font=(FONT, 11))
        search.grid(row=1, column=0, sticky="ew", padx=px(20), pady=(px(16), px(10)))
        search.focus_set()
        search.bind("<KeyRelease>", self._filter)
        search.bind("<Return>", self._accept)
        search.bind("<Escape>", lambda _e: self.destroy())
        search.bind("<Down>", lambda _e: self._move(1))
        search.bind("<Up>", lambda _e: self._move(-1))
        card = Card(self, padding=px(2))
        card.grid(row=2, column=0, sticky="nsew", padx=px(20), pady=(0, px(4)))
        frame = card.body
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(frame, columns=("id",), show="tree headings",
                                 selectmode="browse", style="Picker.Treeview")
        self.tree.heading("#0", text="Item", anchor="w")
        self.tree.heading("id", text="ID", anchor="w")
        self.tree.column("#0", width=px(340), anchor="w")
        self.tree.column("id", width=px(220), anchor="w")
        configure_rarity_tags(self.tree)
        self.tree.grid(row=0, column=0, sticky="nsew")
        bar = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        bar.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=bar.set)
        self.tree.bind("<Double-Button-1>", self._double)
        self.tree.bind("<Return>", self._accept)
        bottom = ttk.Frame(self)
        bottom.grid(row=3, column=0, sticky="ew", padx=px(20), pady=px(14))
        bottom.columnconfigure(0, weight=1)
        self.count_var = tk.StringVar()
        ttk.Label(bottom, textvariable=self.count_var, style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Button(bottom, text="Cancel", command=self.destroy).grid(row=0, column=1, padx=(px(8), 0))
        ttk.Button(bottom, text="Choose", style="Accent.TButton", command=self._accept).grid(
            row=0, column=2, padx=(px(8), 0))
        self.bind("<Escape>", lambda _e: self.destroy())
        self._filter()
        if initial_id and initial_id in self.all_ids:
            self._select_id(initial_id)
        enable_dark_title_bar(self)
        self.wait_window(self)

    def _filter(self, _event=None):
        query = self.search_var.get().strip().casefold()
        matches = [i for i in self.all_ids if not query or query in self.display_name(i).casefold()
                   or query in i.casefold()]
        total = len(matches)
        self.matches = matches[: self.MAX_SHOWN]
        self.tree.delete(*self.tree.get_children())
        for index, item_id in enumerate(self.matches):
            opts = {"image": self.icon_for(item_id)} if self.icon_for else {}
            self.tree.insert("", "end", iid=str(index), text=self.display_name(item_id),
                             values=(item_id,), **opts)
        if total > self.MAX_SHOWN:
            self.count_var.set(f"Showing the first {self.MAX_SHOWN} of {total:,} — keep typing")
        elif query:
            self.count_var.set(f"{total:,} match{'es' if total != 1 else ''}")
        else:
            self.count_var.set(f"{total:,} entries  •  type a name or ID to filter")
        if self.matches:
            self._select_index(0)

    def _select_index(self, index):
        iid = str(index)
        if self.tree.exists(iid):
            self.tree.selection_set(iid)
            self.tree.focus(iid)
            self.tree.see(iid)

    def _select_id(self, item_id):
        if item_id in self.matches:
            self._select_index(self.matches.index(item_id))

    def _move(self, delta):
        if not self.matches:
            return "break"
        sel = self.tree.selection()
        current = int(sel[0]) if sel else -1
        self._select_index(max(0, min(len(self.matches) - 1, current + delta)))
        return "break"

    def _double(self, event):
        if self.tree.identify_region(event.x, event.y) in ("cell", "tree"):
            self._accept()

    def _accept(self, _event=None):
        sel = self.tree.selection()
        if sel:
            self.result = self.matches[int(sel[0])]
            self.destroy()
        elif self.matches:
            self.result = self.matches[0]
            self.destroy()


# -------------------------------------------------- row editor (recipes / counts)
class ItemRowsEditor(tk.Toplevel):
    def __init__(self, parent, output_id, recipe, item_ids, display_name, picker,
                 icon_for=None, color_for=None, *, heading="Recipe Editor",
                 noun="INGREDIENTS", add_text="＋  Add Ingredient", save_text="Save Recipe",
                 delete_text="Delete Recipe", intro=None, allow_negative=False,
                 allow_empty=False, delete_confirm=None, coins_button=False):
        super().__init__(parent)
        self.icon_for, self.color_for = icon_for, color_for
        self.output_id, self.display_name, self.picker = output_id, display_name, picker
        self.allow_negative, self.allow_empty = allow_negative, allow_empty
        self.delete_confirm = delete_confirm
        self.result, self.rows = None, []
        self.title(f"{heading} • {display_name(output_id)}")
        self.configure(background=BG)
        self.minsize(px(660), px(520))
        center_window(self, px(740), px(660), parent)
        self.transient(parent)
        try:
            self.grab_set()
        except tk.TclError:
            pass
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        header = ttk.Frame(self, style="Header.TFrame", padding=(px(20), px(16)))
        header.grid(row=0, column=0, sticky="ew")
        ttk.Label(header, text=heading, style="HeaderTitle.TLabel",
                  font=(FONT, 16, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(header, text=f"{display_name(output_id)}  •  {output_id}",
                  style="HeaderSub.TLabel").grid(row=1, column=0, sticky="w", pady=(3, 0))
        wrapf = ttk.Frame(self, padding=(px(20), px(14), px(20), px(8)))
        wrapf.grid(row=1, column=0, sticky="ew")
        ttk.Label(wrapf, style="Muted.TLabel", wraplength=px(660), justify="left",
                  text=intro or ("Add each ingredient below. You never need to type item IDs or "
                                 "recipe syntax. Use ＋ Add Coins for recipes that cost raw coins.")
                  ).grid(row=0, column=0, sticky="w")
        card = Card(self, padding=px(14))
        card.grid(row=2, column=0, sticky="nsew", padx=px(20), pady=px(4))
        body = card.body
        body.columnconfigure(0, weight=1)
        body.rowconfigure(2, weight=1)
        ttk.Label(body, text=noun, style="Caption.Card.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, px(8)))
        heads = ttk.Frame(body, style="Card.TFrame")
        heads.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, px(2)))
        heads.columnconfigure(1, weight=1)
        ttk.Label(heads, text="Quantity", style="Muted.Card.TLabel", width=12, anchor="e").grid(
            row=0, column=0, padx=(px(2), px(10)))
        ttk.Label(heads, text="Item", style="Muted.Card.TLabel").grid(row=0, column=1, sticky="w")
        canvas = tk.Canvas(body, highlightthickness=0, background=SURFACE)
        bar = ttk.Scrollbar(body, orient="vertical", command=canvas.yview)
        canvas.grid(row=2, column=0, sticky="nsew")
        bar.grid(row=2, column=1, sticky="ns")
        self.rows_frame = ttk.Frame(canvas, style="Card.TFrame")
        win = canvas.create_window((0, 0), window=self.rows_frame, anchor="nw")
        canvas.configure(yscrollcommand=bar.set)
        self.rows_frame.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win, width=e.width))

        def wheel(event):
            bbox = canvas.bbox("all")
            if bbox and (bbox[3] - bbox[1]) > canvas.winfo_height():
                canvas.yview_scroll(int(-event.delta / 120), "units")

        canvas.bind("<Enter>", lambda _e: canvas.bind_all("<MouseWheel>", wheel))
        canvas.bind("<Leave>", lambda _e: canvas.unbind_all("<MouseWheel>"))
        self._canvas = canvas
        for ingredient_id, amount in recipe:
            self._add_row(ingredient_id, amount)
        if not self.rows:
            self._add_row()
        add = ttk.Frame(self, padding=(px(20), px(10)))
        add.grid(row=3, column=0, sticky="ew")
        ttk.Button(add, text=add_text, command=lambda: self._add_row()).pack(side="left")
        if coins_button:
            ttk.Button(add, text="＋  Add Coins", style="Plus.TButton",
                       command=lambda: self._add_row(COINS, 1000000)).pack(side="left", padx=(px(8), 0))
            ttk.Label(add, text="Coins accept 1500000, 1.5m or 2b",
                      style="Muted.TLabel").pack(side="left", padx=(px(10), 0))
        bottom = ttk.Frame(self, padding=(px(20), px(10), px(20), px(18)))
        bottom.grid(row=4, column=0, sticky="ew")
        ttk.Button(bottom, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(bottom, text=save_text, style="Accent.TButton", command=self._save).pack(
            side="right", padx=(0, px(8)))
        if delete_text:
            ttk.Button(bottom, text=delete_text, style="Danger.TButton",
                       command=self._delete).pack(side="left")
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
        if self.icon_for:
            label.configure(image=self.icon_for(item_id))
        if self.color_for:
            label.configure(foreground=self.color_for(item_id))

    def _add_row(self, item_id=None, amount=1):
        item_id = normalize_id(item_id) if item_id else None
        is_coin = item_id == COINS
        row = ttk.Frame(self.rows_frame, style="Card.TFrame", padding=(px(2), px(7), px(2), 0))
        row.pack(fill="x")
        row.columnconfigure(1, weight=1)
        qty = tk.StringVar(value=str(amount))
        ttk.Entry(row, textvariable=qty, width=12, justify="right").grid(
            row=0, column=0, padx=(0, px(10)))
        name_var = tk.StringVar(value=self.display_name(item_id) if item_id else "Choose an item…")
        label = ttk.Label(row, textvariable=name_var, anchor="w", compound="left",
                          style="Card.TLabel" if item_id else "Muted.Card.TLabel")
        label.grid(row=0, column=1, sticky="ew")
        if item_id:
            self._decorate(label, item_id)
        selected = {"id": item_id}

        def choose():
            picked = self.picker("Choose Item", selected["id"])
            if picked:
                selected["id"] = picked
                name_var.set(f"{self.display_name(picked)}  [{picked}]")
                label.configure(style="Card.TLabel")
                self._decorate(label, picked)

        choose_btn = ttk.Button(row, text="Choose…", style="Small.TButton", command=choose)
        choose_btn.grid(row=0, column=2, padx=(px(10), px(6)))
        if is_coin:
            name_var.set("Coins  (raw coin cost)")
            choose_btn.configure(state="disabled")
        data = {"frame": row, "qty": qty, "selected": selected}

        def remove():
            row.destroy()
            if data in self.rows:
                self.rows.remove(data)

        ttk.Button(row, text="Remove", style="SmallDanger.TButton", command=remove).grid(row=0, column=3)
        tk.Frame(row, height=1, background=BORDER, borderwidth=0).grid(
            row=1, column=0, columnspan=4, sticky="ew", pady=(px(7), 0))
        self.rows.append(data)

    def _save(self):
        parsed = []
        for index, data in enumerate(self.rows, start=1):
            raw_qty = data["qty"].get().strip()
            item_id = data["selected"].get("id", "")
            if not raw_qty and not item_id:
                continue
            if item_id == COINS:
                value = he.parse_price(raw_qty)
                if value is None or value <= 0:
                    messagebox.showerror("Invalid coin amount",
                                         f"Row {index}: enter coins as 1500000, 1.5m or 2b.", parent=self)
                    return
                amount = int(round(value))
            else:
                try:
                    amount = int(raw_qty.replace(",", ""))
                    if amount == 0 or (amount < 0 and not self.allow_negative):
                        raise ValueError
                except ValueError:
                    rule = "a non-zero whole number" if self.allow_negative else "a positive whole number"
                    messagebox.showerror("Invalid quantity", f"Row {index} needs {rule}.", parent=self)
                    return
            if not item_id:
                messagebox.showerror("Missing item", f"Choose an item for row {index}.", parent=self)
                return
            parsed.append((normalize_id(item_id), amount))
        if not parsed and not self.allow_empty:
            messagebox.showerror("Nothing entered", "Add at least one row, or use Delete.", parent=self)
            return
        self.result = parsed
        self.destroy()

    def _delete(self):
        if messagebox.askyesno("Clear", self.delete_confirm or "Remove this entry?", parent=self):
            self.result = []
            self.destroy()


# ------------------------------------------------------------- manual price dialog
class PriceDialog(tk.Toplevel):
    def __init__(self, app, item_id):
        super().__init__(app)
        self.app, self.item_id, self.result = app, item_id, None
        self.title(f"Price • {app.display_name(item_id)}")
        self.configure(background=BG)
        self.minsize(px(480), px(330))
        center_window(self, px(560), px(400), app)
        self.transient(app)
        try:
            self.grab_set()
        except tk.TclError:
            pass
        self.columnconfigure(0, weight=1)
        header = ttk.Frame(self, style="Header.TFrame", padding=(px(20), px(14)))
        header.grid(row=0, column=0, sticky="ew")
        ttk.Label(header, text="Set a manual price", style="HeaderTitle.TLabel",
                  font=(FONT, 15, "bold")).pack(anchor="w")
        ttk.Label(header, text=f"{app.display_name(item_id)}  •  {item_id}",
                  style="HeaderSub.TLabel").pack(anchor="w", pady=(3, 0))
        card = Card(self, padding=px(16), accent=GOLD)
        card.grid(row=1, column=0, sticky="ew", padx=px(20), pady=px(16))
        body = card.body
        ttk.Label(body, text="PRICE PER UNIT (COINS)", style="Caption.Card.TLabel").pack(anchor="w")
        self.price_var = tk.StringVar(value=str(app.prices.get(item_id, "")))
        entry = ttk.Entry(body, textvariable=self.price_var, font=(FONT, 12))
        entry.pack(fill="x", pady=(px(6), px(8)))
        entry.focus_set()
        entry.bind("<Return>", lambda _e: self._save())
        price, label, _product = app.price_of(item_id)
        current = "no price available" if price is None else f"{he.format_coins_full(price)} coins ({label})"
        ttk.Label(body, text="Currently: " + current, style="Muted.Card.TLabel",
                  wraplength=px(480), justify="left").pack(anchor="w")
        ttk.Label(body, style="Muted.Card.TLabel", wraplength=px(480), justify="left",
                  text=("Accepts 1250000, 1,250,000, 1.25m or 2b. A manual price always wins over "
                        "bazaar. Use this for auction-only items — the auction endpoint is "
                        "deliberately not polled.")).pack(anchor="w", pady=(px(8), 0))
        bottom = ttk.Frame(self, padding=(px(20), px(4), px(20), px(16)))
        bottom.grid(row=2, column=0, sticky="ew")
        ttk.Button(bottom, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(bottom, text="Save Price", style="Accent.TButton", command=self._save).pack(
            side="right", padx=(0, px(8)))
        ttk.Button(bottom, text="Clear Manual Price", style="Danger.TButton",
                   command=self._clear).pack(side="left")
        self.bind("<Escape>", lambda _e: self.destroy())
        enable_dark_title_bar(self)
        self.wait_window(self)

    def _save(self):
        value = he.parse_price(self.price_var.get())
        if value is None:
            messagebox.showerror("Invalid price", "Enter a number such as 1250000 or 1.25m.", parent=self)
            return
        self.result = ("set", value)
        self.destroy()

    def _clear(self):
        self.result = ("clear", None)
        self.destroy()


# --------------------------------------------------------------------- settings
class SettingsDialog(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("Account, Counting & Prices")
        self.configure(background=BG)
        self.minsize(px(680), px(560))
        center_window(self, px(790), px(800), app)
        self.transient(app)
        try:
            self.grab_set()
        except tk.TclError:
            pass
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        header = ttk.Frame(self, style="Header.TFrame", padding=(px(20), px(16)))
        header.grid(row=0, column=0, sticky="ew")
        ttk.Label(header, text="Account, Counting & Prices", style="HeaderTitle.TLabel",
                  font=(FONT, 16, "bold")).pack(anchor="w")
        ttk.Label(header, text="Everything here is stored locally on this PC only.",
                  style="HeaderSub.TLabel").pack(anchor="w", pady=(3, 0))
        outer = ttk.Frame(self, padding=(px(20), px(14), px(20), px(6)))
        outer.grid(row=1, column=0, sticky="nsew")
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(0, weight=1)
        canvas = tk.Canvas(outer, highlightthickness=0, background=BG)
        canvas.grid(row=0, column=0, sticky="nsew")
        bar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        bar.grid(row=0, column=1, sticky="ns")
        canvas.configure(yscrollcommand=bar.set)
        inner = ttk.Frame(canvas)
        win = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win, width=e.width))
        canvas.bind("<Enter>", lambda _e: canvas.bind_all(
            "<MouseWheel>", lambda e: canvas.yview_scroll(int(-e.delta / 120), "units")))
        canvas.bind("<Leave>", lambda _e: canvas.unbind_all("<MouseWheel>"))
        self._canvas = canvas
        self.bind("<Destroy>", self._destroy)
        inner.columnconfigure(0, weight=1)
        self._account(inner)
        self._key(inner)
        self._sync(inner)
        self._sources(inner)
        self._prices(inner)
        self._data(inner)
        about = Card(inner, padding=px(14), accent=BORDER_STRONG)
        about.grid(row=6, column=0, sticky="ew", pady=(0, px(10)))
        ttk.Label(about.body, text="ABOUT", style="Caption.Card.TLabel").pack(anchor="w")
        ttk.Label(about.body, style="Muted.Card.TLabel", wraplength=px(690), justify="left",
                  text=(DISCLAIMER + "\n\nRegister your own application in the Hypixel Developer "
                        "Portal before using authenticated endpoints. Do not share your key, do not "
                        "paste it into websites or mods, and never ship it inside a distributed "
                        "build.\n\nPlayer data is pulled once per click, or once per launch when the "
                        "cache is stale. Bazaar prices use the keyless endpoint and are cached "
                        "separately. No background polling, no stat history.")
                  ).pack(anchor="w", pady=(px(4), 0))
        bottom = ttk.Frame(self, padding=(px(20), px(10), px(20), px(16)))
        bottom.grid(row=2, column=0, sticky="ew")
        ttk.Button(bottom, text="Close", command=self.destroy).pack(side="right")
        ttk.Button(bottom, text="Save & Sync Now", style="Accent.TButton",
                   command=self._save_and_sync).pack(side="right", padx=(0, px(8)))
        self.bind("<Escape>", lambda _e: self.destroy())
        enable_dark_title_bar(self)
        self.wait_window(self)

    def _destroy(self, event):
        if event.widget is self:
            try:
                self._canvas.unbind_all("<MouseWheel>")
            except tk.TclError:
                pass
            self.app._settings_closed()

    def _section(self, parent, row, title, accent):
        card = Card(parent, padding=px(14), accent=accent)
        card.grid(row=row, column=0, sticky="ew", pady=(0, px(10)))
        ttk.Label(card.body, text=title, style="Caption.Card.TLabel").pack(anchor="w")
        return card.body

    def _note(self, body, text):
        ttk.Label(body, style="Muted.Card.TLabel", wraplength=px(690), justify="left",
                  text=text).pack(anchor="w", pady=(px(6), 0))

    def _account(self, parent):
        body = self._section(parent, 0, "HYPIXEL ACCOUNT", VIOLET)
        s = self.app.settings
        row = ttk.Frame(body, style="Card.TFrame")
        row.pack(fill="x", pady=(px(6), 0))
        row.columnconfigure(0, weight=1)
        self.name_var = tk.StringVar(value=s.get("username", ""))
        ttk.Entry(row, textvariable=self.name_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(row, text="Look up UUID", command=self._lookup).grid(row=0, column=1, padx=(px(8), 0))
        self.uuid_var = tk.StringVar(value=s.get("uuid", "") or "Not resolved yet")
        ttk.Label(body, textvariable=self.uuid_var, style="Muted.Card.TLabel").pack(anchor="w", pady=(px(6), 0))
        self._note(body, "Minecraft username, or your UUID if the name lookup is unavailable. The "
                         "lookup uses Mojang's public endpoint and never sends your API key.")
        prow = ttk.Frame(body, style="Card.TFrame")
        prow.pack(fill="x", pady=(px(8), 0))
        prow.columnconfigure(1, weight=1)
        ttk.Label(prow, text="Profile", style="Card.TLabel").grid(row=0, column=0, padx=(0, px(8)))
        profiles = (self.app.snapshot or {}).get("profiles") or []
        self.profile_map = {"(currently selected in game)": ""}
        for p in profiles:
            self.profile_map[p["name"] + ("  •  selected" if p.get("selected") else "")] = p["id"]
        current = s.get("profile_id", "")
        shown = next((k for k, v in self.profile_map.items() if v == current),
                     "(currently selected in game)")
        self.profile_var = tk.StringVar(value=shown)
        ttk.Combobox(prow, textvariable=self.profile_var, state="readonly",
                     values=list(self.profile_map)).grid(row=0, column=1, sticky="ew")
        if not profiles:
            self._note(body, "Sync once to list this account's profiles.")

    def _key(self, parent):
        body = self._section(parent, 1, "API KEY  (never bundled, never shared)", ACCENT)
        key, origin = self.app.sync.api_key()
        self.key_var = tk.StringVar(value=key if origin == "file" else "")
        self.show_key = tk.BooleanVar(value=False)
        row = ttk.Frame(body, style="Card.TFrame")
        row.pack(fill="x", pady=(px(6), 0))
        row.columnconfigure(0, weight=1)
        self.key_entry = ttk.Entry(row, textvariable=self.key_var, show="•")
        self.key_entry.grid(row=0, column=0, sticky="ew")
        ttk.Checkbutton(row, text="Show", variable=self.show_key, style="Card.TCheckbutton",
                        command=lambda: self.key_entry.configure(
                            show="" if self.show_key.get() else "•")).grid(row=0, column=1, padx=(px(8), 0))
        buttons = ttk.Frame(body, style="Card.TFrame")
        buttons.pack(fill="x", pady=(px(8), 0))
        ttk.Button(buttons, text="Save Key", style="Small.TButton", command=self._save_key).pack(side="left")
        ttk.Button(buttons, text="Remove Key", style="SmallDanger.TButton",
                   command=self._remove_key).pack(side="left", padx=(px(6), 0))
        self.key_state = tk.StringVar()
        ttk.Label(body, textvariable=self.key_state, style="Muted.Card.TLabel").pack(anchor="w", pady=(px(8), 0))
        self._note(body, "Key file: %s\n\nYou can also set the HYPIXEL_API_KEY environment variable "
                         "instead; it takes priority over the file. Get a key from "
                         "developer.hypixel.net after registering your own application. Bazaar "
                         "prices need no key at all." % KEY_FILE)
        self._refresh_key_state()

    def _refresh_key_state(self):
        key, origin = self.app.sync.api_key()
        if not key:
            self.key_state.set("No key configured — inventory syncing is disabled (prices still work).")
        elif origin == "environment":
            self.key_state.set("Using HYPIXEL_API_KEY from the environment: " + hs.HypixelSync.mask(key))
        else:
            self.key_state.set("Using local key file: " + hs.HypixelSync.mask(key))

    def _sync(self, parent):
        body = self._section(parent, 2, "SYNC BEHAVIOUR", CYAN)
        s = self.app.settings
        self.auto_var = tk.BooleanVar(value=bool(s.get("auto_sync_on_launch", True)))
        ttk.Checkbutton(body, text="Sync once on launch when the cached data is older than the interval",
                        variable=self.auto_var, style="Card.TCheckbutton").pack(anchor="w", pady=(px(6), 0))
        self.museum_var = tk.BooleanVar(value=bool(s.get("fetch_museum", True)))
        ttk.Checkbutton(body, text="Also fetch the museum (one extra request per sync)",
                        variable=self.museum_var, style="Card.TCheckbutton").pack(anchor="w", pady=(px(4), 0))
        row = ttk.Frame(body, style="Card.TFrame")
        row.pack(fill="x", pady=(px(10), 0))
        ttk.Label(row, text="Minimum time between pulls", style="Card.TLabel").pack(side="left")
        self.interval_labels = {300: "5 minutes (fastest allowed)", 600: "10 minutes", 900: "15 minutes",
                                1800: "30 minutes (recommended)", 3600: "1 hour", 21600: "6 hours"}
        cur = int(s.get("min_refresh_seconds", 300))
        self.interval_var = tk.StringVar(value=self.interval_labels.get(cur, self.interval_labels[300]))
        ttk.Combobox(row, textvariable=self.interval_var, state="readonly", width=28,
                     values=list(self.interval_labels.values())).pack(side="left", padx=(px(10), 0))
        self._note(body, "Hypixel's policy asks developers to cache player data and avoid continuous "
                         "polling. This app never polls on a timer: each pull is a click, or one pull "
                         "per launch. The interval is a hard floor stored on disk, so restarting "
                         "cannot pull faster. Purse and bank come from the same response.")

    def _sources(self, parent):
        body = self._section(parent, 3, "COUNTING DEFAULTS  —  WHAT COUNTS AS 'I HAVE IT'", GREEN)
        self._note(body, "Pick a preset, then fine-tune. Anything you would not want to consume is off "
                         "in the default preset: accessories, spare wardrobe sets and museum donations "
                         "(donated items must be withdrawn before crafting). Coins in your purse and "
                         "bank are always counted. Island chests are not in the public API — use "
                         "Manual Counts.")
        prow = ttk.Frame(body, style="Card.TFrame")
        prow.pack(fill="x", pady=(px(10), px(10)))
        ttk.Label(prow, text="Preset", style="Card.TLabel").pack(side="left")
        self.preset_map = {label: key for key, label in hs.PRESET_LABELS.items()}
        cur = self.app.settings.get("count_preset", "crafting")
        self.preset_var = tk.StringVar(value=hs.PRESET_LABELS.get(cur, hs.PRESET_LABELS["crafting"]))
        ttk.Combobox(prow, textvariable=self.preset_var, state="readonly", width=32,
                     values=[hs.PRESET_LABELS[k] for k in
                             ("crafting", "storage", "carried", "everything", "custom")]
                     ).pack(side="left", padx=(px(10), px(8)))
        ttk.Button(prow, text="Apply Preset", style="Small.TButton",
                   command=self._apply_preset).pack(side="left")
        ttk.Button(prow, text="Restore Defaults", style="Small.TButton",
                   command=lambda: self._apply_preset("crafting")).pack(side="left", padx=(px(6), 0))
        grid = ttk.Frame(body, style="Card.TFrame")
        grid.pack(fill="x")
        grid.columnconfigure(0, weight=1)
        grid.columnconfigure(1, weight=1)
        self.source_vars = {}
        counts = (self.app.snapshot or {}).get("counts", {})
        keys = [k for k, _l, _g, _d in hs.SOURCE_DEFS]
        keys += [k for k in counts if k not in keys]
        for index, key in enumerate(keys):
            default = hs.DEFAULT_SOURCES.get(key, True)
            var = tk.BooleanVar(value=bool(self.app.settings["sources"].get(key, default)))
            self.source_vars[key] = var
            total = sum(counts.get(key, {}).values())
            shown = he.format_coins(total) if key in hs.COIN_SOURCES else comma(total)
            text = f"{hs.friendly_source(key)}  ({shown})" if total else hs.friendly_source(key)
            ttk.Checkbutton(grid, text=text, variable=var, style="Card.TCheckbutton",
                            command=self._mark_custom).grid(row=index // 2, column=index % 2,
                                                            sticky="w", pady=px(2))
        self._note(body, "Manual / island-chest counts are always counted.")

    def _mark_custom(self):
        self.preset_var.set(hs.PRESET_LABELS["custom"])

    def _apply_preset(self, key=None):
        key = key or self.preset_map.get(self.preset_var.get(), "crafting")
        preset = hs.COUNT_PRESETS.get(key, (None, None))[1]
        if not preset:
            return
        for source, var in self.source_vars.items():
            var.set(bool(preset.get(source, hs.DEFAULT_SOURCES.get(source, True))))
        self.preset_var.set(hs.PRESET_LABELS[key])

    def _prices(self, parent):
        body = self._section(parent, 4, "COIN COST  &  BAZAAR PRICES", GOLD)
        s = self.app.settings
        row = ttk.Frame(body, style="Card.TFrame")
        row.pack(fill="x", pady=(px(6), 0))
        ttk.Label(row, text="Price used for planning", style="Card.TLabel").pack(side="left")
        self.price_mode_var = tk.StringVar(value=he.PRICE_MODE_LABELS.get(
            s.get("price_mode", "insta"), he.PRICE_MODE_LABELS["insta"]))
        ttk.Combobox(row, textvariable=self.price_mode_var, state="readonly", width=30,
                     values=[v for _k, v in he.PRICE_MODES]).pack(side="left", padx=(px(10), 0))
        self.auto_price_var = tk.BooleanVar(value=bool(s.get("auto_refresh_prices", True)))
        ttk.Checkbutton(body, text="Refresh bazaar prices once on launch when the cache is stale",
                        variable=self.auto_price_var, style="Card.TCheckbutton").pack(anchor="w", pady=(px(8), 0))
        irow = ttk.Frame(body, style="Card.TFrame")
        irow.pack(fill="x", pady=(px(8), 0))
        ttk.Label(irow, text="Minimum time between price refreshes", style="Card.TLabel").pack(side="left")
        self.price_interval_labels = {300: "5 minutes", 600: "10 minutes", 900: "15 minutes (default)",
                                      1800: "30 minutes", 3600: "1 hour"}
        cur = int(s.get("bazaar_interval", he.BazaarMarket.DEFAULT_INTERVAL))
        self.price_interval_var = tk.StringVar(value=self.price_interval_labels.get(
            cur, self.price_interval_labels[900]))
        ttk.Combobox(irow, textvariable=self.price_interval_var, state="readonly", width=24,
                     values=list(self.price_interval_labels.values())).pack(side="left", padx=(px(10), 0))
        buttons = ttk.Frame(body, style="Card.TFrame")
        buttons.pack(fill="x", pady=(px(10), 0))
        ttk.Button(buttons, text="⟳ Refresh Prices Now", style="Small.TButton",
                   command=self._refresh_prices).pack(side="left")
        ttk.Button(buttons, text="Clear All Manual Prices", style="SmallDanger.TButton",
                   command=self._clear_prices).pack(side="left", padx=(px(6), 0))
        ttk.Button(buttons, text="Clear Bazaar Links", style="Small.TButton",
                   command=self._clear_links).pack(side="left", padx=(px(6), 0))
        self._note(body, "Bazaar is keyless; no API key is sent to it. 'Buy order' uses the insta-sell "
                         "price as an estimate of what you must outbid — cheaper but slower than "
                         "insta-buy. The auction endpoint is not polled: for auction-only items set a "
                         "manual price, or link the item to a bazaar product, from the Coin Cost tab.")

    def _refresh_prices(self):
        self._collect()
        self.app._refresh_prices()

    def _clear_prices(self):
        if messagebox.askyesno("Clear manual prices", "Remove every manual price override?", parent=self):
            self.app.prices.clear()
            self.app._refresh_all()

    def _clear_links(self):
        if messagebox.askyesno("Clear bazaar links", "Remove every manual item → bazaar link?", parent=self):
            self.app.bazaar_links.clear()
            self.app._refresh_all()

    def _data(self, parent):
        body = self._section(parent, 5, "LOCAL DATA", AMBER)
        ttk.Label(body, text=str(DATA_DIR), style="Muted.Card.TLabel",
                  wraplength=px(690)).pack(anchor="w", pady=(px(6), px(8)))
        row = ttk.Frame(body, style="Card.TFrame")
        row.pack(fill="x")
        for text, cmd, style in (("Open data folder", self.app._open_data_folder, "Small.TButton"),
                                 ("Clear inventory cache", self._clear_cache, "Small.TButton"),
                                 ("Clear price cache", self._clear_bazaar, "Small.TButton"),
                                 ("Container report", self.app._open_container_report, "Small.TButton"),
                                 ("Clear manual counts", self._clear_manual, "SmallDanger.TButton")):
            ttk.Button(row, text=text, style=style, command=cmd).pack(side="left", padx=(0, px(6)))

    def _lookup(self):
        text = self.name_var.get().strip()
        if not text:
            messagebox.showinfo("Enter a name", "Type your Minecraft username or UUID first.", parent=self)
            return
        self.uuid_var.set("Looking up…")

        def work():
            try:
                uuid, name = self.app.sync.resolve_player(text)
                self.after(0, lambda: self._lookup_done(uuid, name))
            except hs.SyncError as exc:
                self.after(0, lambda: self._lookup_failed(str(exc), exc.hint))

        threading.Thread(target=work, daemon=True).start()

    def _lookup_done(self, uuid, name):
        self.app.settings.update(uuid=uuid, username=name)
        self.name_var.set(name)
        self.uuid_var.set(uuid)
        self.app._save()

    def _lookup_failed(self, message, hint):
        self.uuid_var.set("Not resolved")
        messagebox.showerror("Lookup failed", message + ("\n\n" + hint if hint else ""), parent=self)

    def _save_key(self):
        key = self.key_var.get().strip()
        if not key:
            messagebox.showinfo("Empty key", "Paste your Hypixel API key first.", parent=self)
            return
        if len(key) < 20 and not messagebox.askyesno(
                "Short key", "That does not look like a full API key. Save anyway?", parent=self):
            return
        self.app.sync.save_api_key(key)
        self.key_var.set("")
        self.key_entry.configure(show="•")
        self.show_key.set(False)
        self._refresh_key_state()
        self.app._update_account_labels()

    def _remove_key(self):
        if messagebox.askyesno("Remove key", "Delete the locally stored API key?", parent=self):
            self.app.sync.clear_api_key()
            self._refresh_key_state()
            self.app._update_account_labels()

    def _clear_cache(self):
        if messagebox.askyesno("Clear cache", "Forget the cached inventory snapshot?", parent=self):
            self.app.sync.clear_cache()
            self.app.snapshot = None
            self.app._refresh_all()

    def _clear_bazaar(self):
        if messagebox.askyesno("Clear price cache", "Forget cached bazaar prices?", parent=self):
            self.app.market.clear_cache()
            self.app._refresh_all()

    def _clear_manual(self):
        if messagebox.askyesno("Clear manual counts", "Remove every manual / island-chest count?", parent=self):
            self.app.extra.clear()
            self.app._refresh_all()

    def _collect(self):
        s = self.app.settings
        s["username"] = self.name_var.get().strip()
        s["profile_id"] = self.profile_map.get(self.profile_var.get(), "")
        s["auto_sync_on_launch"] = bool(self.auto_var.get())
        s["fetch_museum"] = bool(self.museum_var.get())
        s["min_refresh_seconds"] = next((k for k, v in self.interval_labels.items()
                                         if v == self.interval_var.get()), 300)
        for key, var in self.source_vars.items():
            s["sources"][key] = bool(var.get())
        s["count_preset"] = self.preset_map.get(self.preset_var.get(), "custom")
        s["price_mode"] = next((k for k, v in he.PRICE_MODES if v == self.price_mode_var.get()), "insta")
        s["auto_refresh_prices"] = bool(self.auto_price_var.get())
        s["bazaar_interval"] = next((k for k, v in self.price_interval_labels.items()
                                     if v == self.price_interval_var.get()), 900)
        self.app._save()
        self.app._refresh_all()

    def _save_and_sync(self):
        self._collect()
        self.destroy()
        self.app._sync_now()


# ------------------------------------------------------------------ main window
class SkyblockTracker(tk.Tk):
    def __init__(self):
        super().__init__()
        global UI_SCALE
        UI_SCALE = max(1.0, self.winfo_fpixels("1i") / 96.0)
        set_window_icon(self)
        self.title("SkyBlock Crafting Tracker")
        self.minsize(px(1120), px(720))
        center_window(self, px(1400), px(900))
        self.items, self.name_to_id = {}, {}
        self.obtained, self.extra, self.prices, self.bazaar_links = {}, {}, {}, {}
        self.root_item, self.mode = "TITANIUM_DRILL_1", "manual"
        self.snapshot, self.stock = None, {}
        self.plan, self.plan_order, self.plan_truncated = {}, [], []
        self.current_view = "tree"
        self.sel_item = self.sel_path = self.res_sel = self.stock_sel = self.cost_sel = None
        self._icon_job, self._icon_note = None, ""
        self._syncing = self._pricing = False
        self._dirty = {"tree": True, "resources": True, "stock": True, "cost": True}
        self._highlight_ids, self._cost_groups = {}, {}
        self._status_state = ("Loading Hypixel item data…", "Loading item data…", "busy")
        self.settings = self._default_settings()
        self.last_closed = 0.0
        self._load_saved()
        self.sync = hs.HypixelSync(DATA_DIR, KEY_FILE, CACHE_FILE, STATE_FILE, normalize=normalize_id)
        self.market = he.BazaarMarket(BAZAAR_FILE, BAZAAR_STATE)
        self.snapshot = self._migrate_snapshot(self.sync.load_cache())
        if self.snapshot and self.settings.get("mode") == "synced":
            self.mode = "synced"
        self._configure_style()
        self.icons = IconStore(self, DATA_DIR / "icon_cache",
                               lambda i: (self.items.get(i) or {}).get("material"),
                               self._icons_changed, self._icon_status)
        self._build_ui()
        enable_dark_title_bar(self)
        self._refresh_all(persist=False)
        self._load_api()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(1000, self._tick)
        self.after(1200, self._startup_sync_check)
        self.after(1800, self._startup_price_check)

    # ---------------- settings / persistence ----------------
    @staticmethod
    def _default_settings():
        return {"username": "", "uuid": "", "profile_id": "", "auto_sync_on_launch": True,
                "fetch_museum": True, "min_refresh_seconds": hs.MIN_INTERVAL_FLOOR,
                "sources": dict(hs.DEFAULT_SOURCES), "count_preset": "crafting", "mode": "manual",
                "id_aliases": {}, "price_mode": "insta", "auto_refresh_prices": True,
                "bazaar_interval": he.BazaarMarket.DEFAULT_INTERVAL}

    def _load_saved(self):
        saved = load_json(DATA_FILE, {})
        if not isinstance(saved, dict):
            return
        self.obtained = {str(k): max(0, int(v)) for k, v in (saved.get("obtained") or {}).items()
                         if isinstance(v, (int, float))}
        self.extra = {normalize_id(k): int(v) for k, v in (saved.get("extra") or {}).items()
                      if isinstance(v, (int, float))}
        self.prices = {normalize_id(k): float(v) for k, v in (saved.get("price_overrides") or {}).items()
                       if isinstance(v, (int, float)) and v >= 0}
        self.bazaar_links = {normalize_id(k): str(v).upper()
                             for k, v in (saved.get("bazaar_links") or {}).items() if v}
        root = saved.get("root_item")
        if isinstance(root, str) and root.strip():
            self.root_item = normalize_id(root)
        stored = saved.get("settings")
        if isinstance(stored, dict):
            for key, value in stored.items():
                if key == "sources" and isinstance(value, dict):
                    self.settings["sources"].update({str(k): bool(v) for k, v in value.items()})
                elif key == "id_aliases" and isinstance(value, dict):
                    self.settings["id_aliases"].update({str(k).upper(): str(v).upper()
                                                        for k, v in value.items()})
                elif key in self.settings:
                    self.settings[key] = value
        for key, default in hs.DEFAULT_SOURCES.items():
            self.settings["sources"].setdefault(key, default)
        ALIASES.update(self.settings["id_aliases"])
        self.mode = "synced" if saved.get("mode") == "synced" else "manual"
        self.settings["mode"] = self.mode
        self.last_closed = float(saved.get("last_closed", 0) or 0)
        for output, ingredients in (saved.get("recipes") or {}).items():
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
            else:
                RECIPES.pop(normalize_id(output), None)

    @staticmethod
    def _migrate_snapshot(snapshot):
        if not isinstance(snapshot, dict):
            return snapshot
        counts = snapshot.setdefault("counts", {})
        for source in hs.COIN_SOURCES:
            if source in counts:
                continue
            try:
                amount = int(float(snapshot.get(source, 0) or 0))
            except (TypeError, ValueError):
                amount = 0
            if amount > 0:
                counts[source] = {COINS: amount}
        return snapshot

    def _save(self):
        data = {"root_item": self.root_item, "obtained": self.obtained, "extra": self.extra,
                "price_overrides": self.prices, "bazaar_links": self.bazaar_links, "mode": self.mode,
                "settings": self.settings, "last_closed": self.last_closed,
                "recipes": {o: [[i, n] for i, n in r] for o, r in RECIPES.items()}}
        try:
            save_json(DATA_FILE, data)
        except OSError:
            pass

    def _on_close(self):
        self.last_closed = time.time()
        self._save()
        self.destroy()

    # ---------------- style ----------------
    def _configure_style(self):
        self.configure(background=BG)
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(".", background=BG, foreground=TEXT, bordercolor=BORDER, lightcolor=BORDER,
                        darkcolor=BORDER, troughcolor=SURFACE, focuscolor=BG,
                        fieldbackground=SURFACE_ALT, font=(FONT, 10))
        for name, bg in (("TFrame", BG), ("Card.TFrame", SURFACE), ("Header.TFrame", HEADER_BG)):
            style.configure(name, background=bg, relief="flat")
        labels = {
            "TLabel": (BG, TEXT, (FONT, 10)), "Muted.TLabel": (BG, MUTED, (FONT, 10)),
            "Card.TLabel": (SURFACE, TEXT, (FONT, 10)),
            "Muted.Card.TLabel": (SURFACE, MUTED, (FONT, 9)),
            "Gold.Card.TLabel": (SURFACE, GOLD, (FONT, 11, "bold")),
            "Caption.Card.TLabel": (SURFACE, MUTED, (FONT, 8, "bold")),
            "Stat.Card.TLabel": (SURFACE, TEXT_STRONG, (FONT, 19, "bold")),
            "StatText.Card.TLabel": (SURFACE, TEXT_STRONG, (FONT, 14, "bold")),
            "ItemName.Card.TLabel": (SURFACE, TEXT_STRONG, (FONT, 13, "bold")),
            "HeaderTitle.TLabel": (HEADER_BG, TEXT_STRONG, (FONT, 18, "bold")),
            "HeaderSub.TLabel": (HEADER_BG, MUTED, (FONT, 10)),
            "Pill.TLabel": (SURFACE_ALT, TEXT, (FONT, 9, "bold")),
            "Footer.TLabel": (HEADER_BG, MUTED, (FONT, 9)),
        }
        for name, (bg, fg, font) in labels.items():
            style.configure(name, background=bg, foreground=fg, font=font)

        def button(name, bg, hover, pressed, fg, border, hb, font, pad, hfg=None):
            style.configure(name, background=bg, foreground=fg, bordercolor=border, lightcolor=bg,
                            darkcolor=bg, focuscolor=bg, focusthickness=0, relief="flat",
                            borderwidth=1, padding=(px(pad[0]), px(pad[1])), font=font)
            style.map(name, background=[("disabled", SURFACE), ("pressed", pressed), ("active", hover)],
                      lightcolor=[("disabled", SURFACE), ("pressed", pressed), ("active", hover)],
                      darkcolor=[("disabled", SURFACE), ("pressed", pressed), ("active", hover)],
                      bordercolor=[("pressed", hb), ("active", hb)],
                      foreground=[("disabled", MUTED), ("active", hfg or fg)])

        button("TButton", SURFACE_ALT, SURFACE_HOVER, BORDER, TEXT, BORDER, BORDER_STRONG, (FONT, 10), (13, 8))
        button("Small.TButton", SURFACE_ALT, SURFACE_HOVER, BORDER, TEXT, BORDER, BORDER_STRONG, (FONT, 9), (10, 5))
        button("Accent.TButton", ACCENT, ACCENT_HOVER, ACCENT_PRESSED, "#fff", ACCENT, ACCENT_HOVER, (FONT, 10, "bold"), (15, 8))
        button("Sync.TButton", "#1f7a4d", "#25925c", "#1a6641", "#fff", "#1f7a4d", "#2fbb75", (FONT, 10, "bold"), (15, 8))
        button("Gold.TButton", "#7a5f14", "#95741a", "#634d10", "#fff", "#7a5f14", "#c9a227", (FONT, 10, "bold"), (15, 8))
        button("Danger.TButton", SURFACE_ALT, "#3a2029", "#2c181f", RED, BORDER, "#6b3340", (FONT, 10), (13, 8))
        button("SmallDanger.TButton", SURFACE_ALT, "#3a2029", "#2c181f", RED, BORDER, "#6b3340", (FONT, 9), (10, 5))
        button("Seg.TButton", SURFACE, SURFACE_HOVER, BORDER, MUTED, BORDER, BORDER_STRONG, (FONT, 10, "bold"), (16, 8), TEXT)
        button("SegOn.TButton", ACCENT, ACCENT_HOVER, ACCENT_PRESSED, "#fff", ACCENT, ACCENT_HOVER, (FONT, 10, "bold"), (16, 8))
        button("Plus.TButton", SURFACE_ALT, "#1d3a2b", "#16301f", GREEN, BORDER, "#2f6b4a", (FONT, 10, "bold"), (10, 8))
        style.configure("Card.TCheckbutton", background=SURFACE, foreground=TEXT, font=(FONT, 10),
                        focuscolor=SURFACE, indicatorbackground=SURFACE_ALT, indicatorforeground="#fff",
                        upperbordercolor=BORDER_STRONG, lowerbordercolor=BORDER_STRONG,
                        indicatormargin=(0, 0, px(6), 0))
        style.map("Card.TCheckbutton", background=[("active", SURFACE)], foreground=[("active", TEXT_STRONG)],
                  indicatorbackground=[("selected", ACCENT), ("active", SURFACE_HOVER)],
                  upperbordercolor=[("selected", ACCENT)], lowerbordercolor=[("selected", ACCENT)])
        style.configure("TCombobox", fieldbackground=SURFACE_ALT, background=SURFACE_ALT, foreground=TEXT,
                        bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER, arrowcolor=TEXT,
                        insertcolor=TEXT, padding=px(5))
        style.map("TCombobox", fieldbackground=[("readonly", SURFACE_ALT)],
                  foreground=[("readonly", TEXT)], bordercolor=[("focus", ACCENT)])
        for opt, val in (("background", SURFACE_ALT), ("foreground", TEXT),
                         ("selectBackground", ACCENT), ("selectForeground", "#ffffff")):
            self.option_add("*TCombobox*Listbox." + opt, val)
        style.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
        style.configure("Treeview", rowheight=px(48), font=(FONT, 10), indent=px(26), background=SURFACE,
                        fieldbackground=SURFACE, foreground=TEXT, bordercolor=SURFACE,
                        lightcolor=SURFACE, darkcolor=SURFACE, borderwidth=0, relief="flat")
        style.configure("Treeview.Heading", font=(FONT, 9, "bold"), background=SURFACE_ALT,
                        foreground=MUTED, bordercolor=SURFACE, lightcolor=SURFACE_ALT,
                        darkcolor=SURFACE_ALT, padding=(px(10), px(8)), relief="flat")
        style.map("Treeview.Heading", background=[("active", SURFACE_HOVER)], foreground=[("active", TEXT)])
        style.map("Treeview", background=[], foreground=[])
        style.map("Picker.Treeview", background=[("selected", SELECT_BG)], foreground=[("selected", TEXT_STRONG)])
        style.configure("TEntry", padding=px(7), fieldbackground=SURFACE_ALT, foreground=TEXT,
                        insertcolor=TEXT, bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER,
                        selectbackground=ACCENT, selectforeground="#fff")
        style.map("TEntry", bordercolor=[("focus", ACCENT)], lightcolor=[("focus", ACCENT)],
                  darkcolor=[("focus", ACCENT)])
        style.layout("Vertical.TScrollbar", [("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
            ("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})]})])
        style.configure("Vertical.TScrollbar", background=SURFACE_HOVER, troughcolor=SURFACE,
                        bordercolor=SURFACE, lightcolor=SURFACE_HOVER, darkcolor=SURFACE_HOVER,
                        gripcount=0, arrowsize=px(12), width=px(12))
        style.map("Vertical.TScrollbar", background=[("pressed", MUTED), ("active", BORDER_STRONG)],
                  lightcolor=[("pressed", MUTED), ("active", BORDER_STRONG)],
                  darkcolor=[("pressed", MUTED), ("active", BORDER_STRONG)])
        for name, color in (("Horizontal.TProgressbar", ACCENT), ("Item.Horizontal.TProgressbar", GREEN)):
            style.configure(name, thickness=px(8), troughcolor=SURFACE_ALT, background=color,
                            bordercolor=SURFACE_ALT, lightcolor=color, darkcolor=color)
        style.configure("TSeparator", background=BORDER)

    # ---------------- header / status ----------------
    def _set_status(self, text, sub, level):
        self._status_state = (text, sub, level)
        self._apply_status()

    def _apply_status(self):
        text, sub, level = self._status_state
        self.status.set(text)
        self.header_sub.set(sub + (f"   •   Icons: {self._icon_note}" if self._icon_note else ""))
        self.status_dot.itemconfigure(self.status_dot_item,
                                      fill={"busy": AMBER, "ok": GREEN, "error": RED}.get(level, MUTED))

    def _icon_status(self, note):
        self._icon_note = note
        if hasattr(self, "status_dot"):
            self._apply_status()

    def _pill(self, parent, variable):
        frame = tk.Frame(parent, background=SURFACE_ALT, highlightbackground=BORDER,
                         highlightcolor=BORDER, highlightthickness=1, borderwidth=0)
        ttk.Label(frame, textvariable=variable, style="Pill.TLabel").pack(side="left", padx=px(12), pady=px(7))
        return frame

    def _build_ui(self):
        header = ttk.Frame(self, style="Header.TFrame", padding=(px(22), px(14)))
        header.pack(fill="x")
        header.columnconfigure(1, weight=1)
        size = px(46)
        logo = tk.Canvas(header, width=size, height=size, background=HEADER_BG, highlightthickness=0)
        logo.grid(row=0, column=0, rowspan=2, padx=(0, px(14)))
        draw_logo(logo, size)
        ttk.Label(header, text="SkyBlock Crafting Tracker", style="HeaderTitle.TLabel").grid(row=0, column=1, sticky="sw")
        self.header_sub = tk.StringVar(value="Loading item data…")
        ttk.Label(header, textvariable=self.header_sub, style="HeaderSub.TLabel").grid(row=1, column=1, sticky="nw")
        self.account_var = tk.StringVar(value="No account linked")
        self._pill(header, self.account_var).grid(row=0, column=2, rowspan=2, sticky="e", padx=(0, px(8)))
        self.status = tk.StringVar(value="Loading Hypixel item data…")
        pill = tk.Frame(header, background=SURFACE_ALT, highlightbackground=BORDER,
                        highlightcolor=BORDER, highlightthickness=1, borderwidth=0)
        pill.grid(row=0, column=3, rowspan=2, sticky="e")
        d = px(10)
        self.status_dot = tk.Canvas(pill, width=d, height=d, background=SURFACE_ALT, highlightthickness=0)
        self.status_dot.pack(side="left", padx=(px(12), px(7)), pady=px(8))
        self.status_dot_item = self.status_dot.create_oval(1, 1, d - 1, d - 1, fill=AMBER, outline="")
        ttk.Label(pill, textvariable=self.status, style="Pill.TLabel").pack(side="left", padx=(0, px(14)), pady=px(6))
        tk.Frame(self, height=1, background=BORDER, borderwidth=0).pack(fill="x")

        toolbar = ttk.Frame(self, padding=(px(20), px(14), px(20), px(4)))
        toolbar.pack(fill="x")
        self.sync_button = ttk.Button(toolbar, text="⟳  Sync Hypixel", style="Sync.TButton", command=self._sync_now)
        self.sync_button.pack(side="left")
        for text, cmd in (("⚙  Settings", self._open_settings), ("＋  Add / Edit Recipe", self._edit_recipe),
                          ("⌕  Choose Goal", self._choose_root), ("✎  Manual Counts", self._edit_manual_counts)):
            ttk.Button(toolbar, text=text, command=cmd).pack(side="left", padx=(px(8), 0))
        ttk.Button(toolbar, text="Reset Progress", style="Danger.TButton",
                   command=self._reset_progress).pack(side="right")
        ttk.Button(toolbar, text="⤓  Export", command=self._export_missing).pack(side="right", padx=(0, px(8)))

        self.footer_var = tk.StringVar()
        footer = ttk.Frame(self, style="Header.TFrame", padding=(px(22), px(7)))
        footer.pack(side="bottom", fill="x")
        footer.columnconfigure(0, weight=1)
        ttk.Label(footer, textvariable=self.footer_var, style="Footer.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(footer, text=DISCLAIMER, style="Footer.TLabel").grid(row=0, column=1, sticky="e")

        main = ttk.Frame(self, padding=(px(20), px(12), px(20), px(16)))
        main.pack(fill="both", expand=True)
        main.columnconfigure(0, weight=1)
        main.rowconfigure(3, weight=1)
        stats = ttk.Frame(main)
        stats.grid(row=0, column=0, sticky="ew", pady=(0, px(12)))
        for c in range(5):
            stats.columnconfigure(c, weight=1, uniform="stat")
        pad = (px(14), px(10), px(14), px(12))
        self.goal_var, self.goal_id_var = tk.StringVar(value="—"), tk.StringVar()
        self.progress_var, self.missing_var = tk.StringVar(value="0%"), tk.StringVar(value="0")
        self.missing_sub, self.coin_var = tk.StringVar(), tk.StringVar(value="0")
        self.coin_sub_var = tk.StringVar(value="add coins to a recipe")
        self.stock_var, self.stock_sub_var = tk.StringVar(value="0"), tk.StringVar(value="link your account")

        goal = Card(stats, padding=pad, accent=VIOLET)
        goal.grid(row=0, column=0, sticky="nsew", padx=(0, px(5)))
        ttk.Label(goal.body, text="GOAL", style="Caption.Card.TLabel").pack(anchor="w")
        row = ttk.Frame(goal.body, style="Card.TFrame")
        row.pack(anchor="w", pady=(px(2), 0))
        self.goal_icon = ttk.Label(row, style="Card.TLabel")
        self.goal_icon.pack(side="left", padx=(0, px(8)))
        self.goal_label = ttk.Label(row, textvariable=self.goal_var, style="StatText.Card.TLabel",
                                    wraplength=px(160), justify="left")
        self.goal_label.pack(side="left")
        ttk.Label(goal.body, textvariable=self.goal_id_var, style="Muted.Card.TLabel").pack(anchor="w")

        prog = Card(stats, padding=pad, accent=ACCENT)
        prog.grid(row=0, column=1, sticky="nsew", padx=px(5))
        ttk.Label(prog.body, text="MATERIALS COVERED", style="Caption.Card.TLabel").pack(anchor="w")
        ttk.Label(prog.body, textvariable=self.progress_var, style="Stat.Card.TLabel").pack(anchor="w", pady=(0, px(6)))
        self.progress = ttk.Progressbar(prog.body, orient="horizontal", mode="determinate", maximum=100)
        self.progress.pack(fill="x")
        ttk.Label(prog.body, text="coins counted separately", style="Muted.Card.TLabel").pack(anchor="w")

        miss = Card(stats, padding=pad, accent=AMBER)
        miss.grid(row=0, column=2, sticky="nsew", padx=px(5))
        ttk.Label(miss.body, text="RAW ITEMS STILL NEEDED", style="Caption.Card.TLabel").pack(anchor="w")
        ttk.Label(miss.body, textvariable=self.missing_var, style="Stat.Card.TLabel",
                  foreground=AMBER).pack(anchor="w", pady=(0, px(2)))
        ttk.Label(miss.body, textvariable=self.missing_sub, style="Muted.Card.TLabel").pack(anchor="w")

        coin = Card(stats, padding=pad, accent=GOLD)
        coin.grid(row=0, column=3, sticky="nsew", padx=px(5))
        ttk.Label(coin.body, text="COINS TO FINISH", style="Caption.Card.TLabel").pack(anchor="w")
        ttk.Label(coin.body, textvariable=self.coin_var, style="Stat.Card.TLabel",
                  foreground=GOLD).pack(anchor="w", pady=(0, px(2)))
        ttk.Label(coin.body, textvariable=self.coin_sub_var, style="Muted.Card.TLabel",
                  wraplength=px(200), justify="left").pack(anchor="w")

        stock = Card(stats, padding=pad, accent=CYAN)
        stock.grid(row=0, column=4, sticky="nsew", padx=(px(5), 0))
        ttk.Label(stock.body, text="ITEMS IN YOUR ACCOUNT", style="Caption.Card.TLabel").pack(anchor="w")
        ttk.Label(stock.body, textvariable=self.stock_var, style="Stat.Card.TLabel",
                  foreground=CYAN).pack(anchor="w", pady=(0, px(2)))
        ttk.Label(stock.body, textvariable=self.stock_sub_var, style="Muted.Card.TLabel",
                  wraplength=px(200), justify="left").pack(anchor="w")

        toggle = ttk.Frame(main)
        toggle.grid(row=1, column=0, sticky="ew")
        toggle.columnconfigure(4, weight=1)
        self.view_buttons = {}
        for c, (key, label) in enumerate((("tree", "Recipe Tree"), ("resources", "Total Resources"),
                                          ("stock", "Account Stock"), ("cost", "Coin Cost"))):
            b = ttk.Button(toggle, text=label, style="Seg.TButton", command=lambda k=key: self._show_view(k))
            b.grid(row=0, column=c, padx=(0, px(6)))
            self.view_buttons[key] = b
        ttk.Label(toggle, text="Counts:", style="Muted.TLabel").grid(row=0, column=5, padx=(0, px(6)))
        self.mode_buttons = {}
        for c, (key, label) in enumerate((("synced", "From Hypixel"), ("manual", "Manual")), start=6):
            b = ttk.Button(toggle, text=label, style="Seg.TButton", command=lambda k=key: self._set_mode(k))
            b.grid(row=0, column=c, padx=(0, px(6)))
            self.mode_buttons[key] = b
        self.view_hint = tk.StringVar()
        ttk.Label(main, textvariable=self.view_hint, style="Muted.TLabel").grid(
            row=2, column=0, sticky="w", pady=(px(6), px(8)))
        views = ttk.Frame(main)
        views.grid(row=3, column=0, sticky="nsew")
        views.columnconfigure(0, weight=1)
        views.rowconfigure(0, weight=1)
        self._build_tree_view(views)
        self._build_resource_view(views)
        self._build_stock_view(views)
        self._build_cost_view(views)
        self._show_view("tree")
        self.bind_all("<Button-1>", self._on_background_click, add="+")
        self.bind("<Escape>", lambda _e: self._deselect_current())
        for n, name in ((1, "tree"), (2, "resources"), (3, "stock"), (4, "cost")):
            self.bind(f"<Control-Key-{n}>", lambda _e, k=name: self._show_view(k))
        self.bind("<Control-r>", lambda _e: self._sync_now())
        self.bind("<Control-p>", lambda _e: self._refresh_prices())
        self.bind("<Control-comma>", lambda _e: self._open_settings())

    # ---------------- reusable widgets ----------------
    def _table(self, body, row, columns, widths, title):
        tree = ttk.Treeview(body, columns=tuple(columns), show="tree headings", selectmode="browse")
        tree.heading("#0", text=title, anchor="w")
        tree.column("#0", width=px(widths["#0"]), minwidth=px(260), anchor="w")
        for key, (label, width, anchor) in columns.items():
            tree.heading(key, text=label)
            tree.column(key, width=px(width), anchor=anchor, stretch=(key != "add"))
        tree.grid(row=row, column=0, sticky="nsew")
        bar = ttk.Scrollbar(body, orient="vertical", command=tree.yview)
        bar.grid(row=row, column=1, sticky="ns")
        tree.configure(yscrollcommand=bar.set)
        configure_rarity_tags(tree)
        return tree

    def _make_panel(self, parent, *, on_set, on_bump, on_complete, action_text, on_action):
        card = Card(parent, padding=px(18), width=px(340))
        card.pack_propagate(False)
        sb, wrap, p = card.body, px(300), {"card": card}
        ttk.Label(sb, text="SELECTED ITEM", style="Caption.Card.TLabel").pack(anchor="w")
        row = ttk.Frame(sb, style="Card.TFrame")
        row.pack(fill="x", pady=(px(6), 0))
        p["icon"] = ttk.Label(row, style="Card.TLabel")
        p["icon"].pack(side="left", padx=(0, px(10)))
        p["name"] = tk.StringVar(value="Nothing selected")
        p["label"] = ttk.Label(row, textvariable=p["name"], style="ItemName.Card.TLabel",
                               wraplength=wrap - px(46), justify="left")
        p["label"].pack(side="left", fill="x", expand=True)
        p["id"] = tk.StringVar(value="Pick a row to see details")
        ttk.Label(sb, textvariable=p["id"], style="Muted.Card.TLabel", wraplength=wrap,
                  justify="left").pack(anchor="w")
        p["progress"] = ttk.Progressbar(sb, orient="horizontal", mode="determinate", maximum=100,
                                        style="Item.Horizontal.TProgressbar")
        p["progress"].pack(fill="x", pady=(px(12), px(5)))
        p["count"], p["loc"] = tk.StringVar(), tk.StringVar()
        ttk.Label(sb, textvariable=p["count"], style="Card.TLabel").pack(anchor="w")
        ttk.Label(sb, textvariable=p["loc"], style="Muted.Card.TLabel", wraplength=wrap,
                  justify="left").pack(anchor="w", pady=(px(4), 0))
        ttk.Separator(sb).pack(fill="x", pady=px(14))
        p["entry_caption"] = tk.StringVar(value="OWNED QUANTITY")
        ttk.Label(sb, textvariable=p["entry_caption"], style="Caption.Card.TLabel").pack(anchor="w")
        p["entry"] = tk.StringVar(value="0")
        erow = ttk.Frame(sb, style="Card.TFrame")
        erow.pack(fill="x", pady=(px(6), px(8)))
        erow.columnconfigure(0, weight=1)
        entry = ttk.Entry(erow, textvariable=p["entry"])
        entry.grid(row=0, column=0, sticky="ew")
        entry.bind("<Return>", lambda _e: on_set())
        ttk.Button(erow, text=f"{PLUS} 1", style="Plus.TButton", width=5, command=on_bump).grid(
            row=0, column=1, padx=(px(6), 0))
        p["set_button"] = ttk.Button(sb, text="Set Amount", style="Accent.TButton", command=on_set)
        p["set_button"].pack(fill="x")
        ttk.Button(sb, text="Mark Complete", command=on_complete).pack(fill="x", pady=(px(7), 0))
        p["stock"] = tk.StringVar()
        ttk.Label(sb, textvariable=p["stock"], style="Muted.Card.TLabel", wraplength=wrap,
                  justify="left").pack(anchor="w", pady=(px(8), 0))
        ttk.Separator(sb).pack(fill="x", pady=px(14))
        p["hint_caption"] = tk.StringVar(value="RECIPE")
        ttk.Label(sb, textvariable=p["hint_caption"], style="Caption.Card.TLabel").pack(anchor="w")
        p["hint"] = tk.StringVar(value="Select an item to see its options.")
        ttk.Label(sb, textvariable=p["hint"], style="Muted.Card.TLabel", wraplength=wrap,
                  justify="left").pack(anchor="w", pady=(px(5), px(10)))
        p["action"] = ttk.Button(sb, text=action_text, command=on_action)
        p["action"].pack(fill="x")
        return card, p

    def _panel_clear(self, p):
        p["name"].set("Nothing selected")
        p["id"].set("Pick a row to see details")
        for key in ("loc", "count", "stock"):
            p[key].set("")
        p["progress"]["value"] = 0
        p["entry"].set("0")
        p["icon"].configure(image="")
        p["label"].configure(foreground=TEXT_STRONG)
        p["hint"].set("Select an item to see its options.")

    def _panel_update(self, p, item_id, required, owned, location):
        coin = item_id == COINS
        p["icon"].configure(image=self.icons.get(item_id, big=True))
        p["label"].configure(foreground=self.rarity_color(item_id))
        p["name"].set(self.display_name(item_id))
        p["id"].set("COINS  •  currency" if coin else
                    f"{item_id}  •  {'Crafted' if RECIPES.get(item_id) else 'Raw'}")
        short = max(0, required - owned)
        fmt = he.format_coins_full if coin else comma
        p["count"].set(f"{fmt(owned)} / {fmt(required)} covered   •   {fmt(short)} short")
        p["progress"]["value"] = min(100, owned / required * 100) if required else (100 if owned else 0)
        p["loc"].set(location)
        if self.mode == "synced":
            p["entry_caption"].set("COINS AVAILABLE (PURSE + BANK + MANUAL)" if coin
                                   else "ACCOUNT STOCK FOR THIS ITEM")
            p["set_button"].configure(text="Set Coin Total" if coin else "Set Account Stock")
            p["entry"].set(str(self.stock.get(item_id, 0)))
            p["stock"].set("Sources: " + (self.stock_breakdown(item_id) or "none found in your account"))
        else:
            p["entry_caption"].set("COINS SET ASIDE (THIS POSITION)" if coin
                                   else "OWNED QUANTITY (THIS TREE POSITION)")
            p["set_button"].configure(text="Set Owned Amount")
            p["entry"].set(str(owned))
            p["stock"].set("Manual mode: counts belong to this row only.")

    # ---------------- views ----------------
    def _build_tree_view(self, parent):
        view = ttk.Frame(parent)
        view.grid(row=0, column=0, sticky="nsew")
        view.columnconfigure(0, weight=1)
        view.rowconfigure(0, weight=1)
        self.tree_view = view
        card = Card(view, padding=px(2))
        card.grid(row=0, column=0, sticky="nsew")
        body = card.body
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        cols = {"required": ("Required", 100, "e"), "owned": ("Have", 95, "e"),
                "remaining": ("Short", 100, "e"), "status": ("Status", 110, "center"),
                "add": ("", 48, "center")}
        self.tree = self._table(body, 0, cols, {"#0": 470}, "  CRAFTING TREE  /  ITEMS")
        self.tree.tag_configure("root", font=(FONT, 11, "bold"))
        self.tree.tag_configure("branch", font=(FONT, 10, "bold"))
        self.tree.bind("<<TreeviewSelect>>", self._sel_tree)
        self.tree.bind("<Button-3>", self._tree_menu)
        self.tree.bind("<Button-1>", lambda e: self._plus_click(e, self.tree, "#5", self._tree_bump, self._deselect_tree))
        self.tree.bind("<Motion>", lambda e: self._plus_motion(e, self.tree, "#5"))
        legend = ttk.Frame(body, style="Card.TFrame", padding=(px(10), px(8), px(10), px(6)))
        legend.grid(row=1, column=0, columnspan=2, sticky="ew")
        for tier in ("COMMON", "UNCOMMON", "RARE", "EPIC", "LEGENDARY", "MYTHIC"):
            ttk.Label(legend, text=f"●  {tier.title()}", style="Muted.Card.TLabel",
                      foreground=RARITY_COLORS[tier]).pack(side="left", padx=(0, px(12)))
        ttk.Label(legend, text="●  Coins", style="Muted.Card.TLabel", foreground=GOLD).pack(side="left")
        panel, self.p_tree = self._make_panel(view, on_set=self._tree_set, on_bump=self._tree_bump_selected,
                                              on_complete=self._tree_complete,
                                              action_text="Define / Edit Recipe",
                                              on_action=self._edit_selected_recipe)
        panel.grid(row=0, column=1, sticky="ns", padx=(px(12), 0))

    def _build_resource_view(self, parent):
        view = ttk.Frame(parent)
        view.grid(row=0, column=0, sticky="nsew")
        view.columnconfigure(0, weight=1)
        view.rowconfigure(0, weight=1)
        self.res_view = view
        self.res_filter = tk.StringVar()
        self.res_hide_done, self.res_show_crafted, self.res_show_raw = (tk.BooleanVar(value=False),
                                                                       tk.BooleanVar(value=True),
                                                                       tk.BooleanVar(value=True))
        self.res_summary = tk.StringVar()
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
        for c, (text, var) in enumerate((("Crafted", self.res_show_crafted), ("Raw", self.res_show_raw),
                                         ("Hide completed", self.res_hide_done)), start=2):
            ttk.Checkbutton(bar, text=text, variable=var, style="Card.TCheckbutton").grid(
                row=0, column=c, padx=(px(12), 0))
        ttk.Button(bar, text="Expand all", style="Small.TButton",
                   command=lambda: self._set_open(self.res_tree, True)).grid(row=0, column=5, padx=(px(14), px(6)))
        ttk.Button(bar, text="Collapse all", style="Small.TButton",
                   command=lambda: self._set_open(self.res_tree, False)).grid(row=0, column=6)
        cols = {"kind": ("Type", 75, "center"), "needed": ("Needed", 100, "e"), "owned": ("Have", 95, "e"),
                "remaining": ("Short", 100, "e"), "status": ("Status", 110, "center"), "add": ("", 48, "center")}
        self.res_tree = self._table(body, 1, cols, {"#0": 420}, "  RESOURCES  /  WHERE NEEDED")
        self.res_tree.tag_configure("resource", font=(FONT, 10, "bold"))
        self.res_tree.bind("<<TreeviewSelect>>", self._sel_res)
        self.res_tree.bind("<Button-1>", lambda e: self._plus_click(e, self.res_tree, "#6", self._res_bump, self._deselect_res))
        self.res_tree.bind("<Motion>", lambda e: self._plus_motion(e, self.res_tree, "#6"))
        foot = ttk.Frame(body, style="Card.TFrame", padding=(px(10), px(8), px(10), px(6)))
        foot.grid(row=2, column=0, columnspan=2, sticky="ew")
        ttk.Label(foot, textvariable=self.res_summary, style="Muted.Card.TLabel").pack(side="left")
        panel, self.p_res = self._make_panel(view, on_set=self._res_set, on_bump=self._res_bump_selected,
                                             on_complete=self._res_complete, action_text="Show in Recipe Tree",
                                             on_action=self._res_show_in_tree)
        panel.grid(row=0, column=1, sticky="ns", padx=(px(12), 0))
        self.p_res["hint_caption"].set("HOW THIS IS COUNTED")
        for var in (self.res_filter, self.res_hide_done, self.res_show_crafted, self.res_show_raw):
            var.trace_add("write", lambda *_: self._render_resources())

    def _build_stock_view(self, parent):
        view = ttk.Frame(parent)
        view.grid(row=0, column=0, sticky="nsew")
        view.columnconfigure(0, weight=1)
        view.rowconfigure(0, weight=1)
        self.stock_view = view
        self.stock_filter = tk.StringVar()
        self.stock_only_recipe = tk.BooleanVar(value=False)
        self.stock_summary = tk.StringVar()
        card = Card(view, padding=px(2))
        card.grid(row=0, column=0, sticky="nsew")
        body = card.body
        body.columnconfigure(0, weight=1)
        body.rowconfigure(1, weight=1)
        bar = ttk.Frame(body, style="Card.TFrame", padding=(px(10), px(10), px(10), px(8)))
        bar.grid(row=0, column=0, columnspan=2, sticky="ew")
        bar.columnconfigure(1, weight=1)
        ttk.Label(bar, text="Search", style="Muted.Card.TLabel").grid(row=0, column=0, padx=(0, px(8)))
        ttk.Entry(bar, textvariable=self.stock_filter).grid(row=0, column=1, sticky="ew")
        ttk.Checkbutton(bar, text="Only items this goal needs", variable=self.stock_only_recipe,
                        style="Card.TCheckbutton").grid(row=0, column=2, padx=(px(14), 0))
        ttk.Button(bar, text="Expand all", style="Small.TButton",
                   command=lambda: self._set_open(self.stock_tree, True)).grid(row=0, column=3, padx=(px(14), px(6)))
        ttk.Button(bar, text="Collapse all", style="Small.TButton",
                   command=lambda: self._set_open(self.stock_tree, False)).grid(row=0, column=4)
        cols = {"total": ("In stock", 100, "e"), "needed": ("Needed", 100, "e"), "used": ("Allocated", 100, "e"),
                "surplus": ("Surplus", 100, "e"), "where": ("Sources", 200, "w"), "add": ("", 48, "center")}
        self.stock_tree = self._table(body, 1, cols, {"#0": 370}, "  ACCOUNT STOCK  /  ITEM MAP")
        self.stock_tree.tag_configure("resource", font=(FONT, 10, "bold"))
        self.stock_tree.bind("<<TreeviewSelect>>", self._sel_stock)
        self.stock_tree.bind("<Button-1>", lambda e: self._plus_click(e, self.stock_tree, "#6", self._stock_bump, self._deselect_stock))
        self.stock_tree.bind("<Motion>", lambda e: self._plus_motion(e, self.stock_tree, "#6"))
        foot = ttk.Frame(body, style="Card.TFrame", padding=(px(10), px(8), px(10), px(6)))
        foot.grid(row=2, column=0, columnspan=2, sticky="ew")
        ttk.Label(foot, textvariable=self.stock_summary, style="Muted.Card.TLabel").pack(side="left")
        panel, self.p_stock = self._make_panel(view, on_set=self._stock_set, on_bump=self._stock_bump_selected,
                                               on_complete=self._stock_complete, action_text="Use as Goal",
                                               on_action=self._stock_use_as_goal)
        panel.grid(row=0, column=1, sticky="ns", padx=(px(12), 0))
        self.p_stock["hint_caption"].set("NOTE")
        for var in (self.stock_filter, self.stock_only_recipe):
            var.trace_add("write", lambda *_: self._render_stock())

    def _build_cost_view(self, parent):
        view = ttk.Frame(parent)
        view.grid(row=0, column=0, sticky="nsew")
        view.columnconfigure(0, weight=1)
        view.rowconfigure(0, weight=1)
        self.cost_view = view
        self.cost_mode_var = tk.StringVar(value=he.PRICE_MODE_LABELS.get(
            self.settings.get("price_mode", "insta"), he.PRICE_MODE_LABELS["insta"]))
        self.cost_scope_var = tk.StringVar(value=he.SCOPE_LABELS["finish"])
        self.cost_summary, self.price_age_var = tk.StringVar(), tk.StringVar()
        card = Card(view, padding=px(2))
        card.grid(row=0, column=0, sticky="nsew")
        body = card.body
        body.columnconfigure(0, weight=1)
        body.rowconfigure(1, weight=1)
        bar = ttk.Frame(body, style="Card.TFrame", padding=(px(10), px(10), px(10), px(8)))
        bar.grid(row=0, column=0, columnspan=2, sticky="ew")
        bar.columnconfigure(5, weight=1)
        ttk.Label(bar, text="Prices", style="Muted.Card.TLabel").grid(row=0, column=0, padx=(0, px(6)))
        ttk.Combobox(bar, textvariable=self.cost_mode_var, state="readonly", width=26,
                     values=[v for _k, v in he.PRICE_MODES]).grid(row=0, column=1, padx=(0, px(10)))
        ttk.Label(bar, text="Scope", style="Muted.Card.TLabel").grid(row=0, column=2, padx=(0, px(6)))
        ttk.Combobox(bar, textvariable=self.cost_scope_var, state="readonly", width=28,
                     values=[v for _k, v in he.SCOPES]).grid(row=0, column=3, padx=(0, px(10)))
        self.price_button = ttk.Button(bar, text="⟳  Refresh Prices", style="Gold.TButton",
                                       command=self._refresh_prices)
        self.price_button.grid(row=0, column=4, padx=(0, px(10)))
        ttk.Label(bar, textvariable=self.price_age_var, style="Muted.Card.TLabel").grid(row=0, column=5, sticky="e")
        cols = {"kind": ("Type", 80, "center"), "qty": ("Quantity", 110, "e"), "unit": ("Unit price", 110, "e"),
                "subtotal": ("Subtotal", 120, "e"), "source": ("Price source", 190, "w")}
        self.cost_tree = self._table(body, 1, cols, {"#0": 420}, "  COIN COST  /  BREAKDOWN")
        self.cost_tree.tag_configure("group", font=(FONT, 11, "bold"), foreground=TEXT_STRONG,
                                     background=SURFACE_ALT)
        self.cost_tree.bind("<<TreeviewSelect>>", self._sel_cost)
        self.cost_tree.bind("<Button-1>", self._cost_click)
        foot = ttk.Frame(body, style="Card.TFrame", padding=(px(10), px(8), px(10), px(6)))
        foot.grid(row=2, column=0, columnspan=2, sticky="ew")
        ttk.Label(foot, textvariable=self.cost_summary, style="Muted.Card.TLabel",
                  wraplength=px(880), justify="left").pack(anchor="w")

        side = Card(view, padding=px(18), width=px(340), accent=GOLD)
        side.grid(row=0, column=1, sticky="ns", padx=(px(12), 0))
        side.pack_propagate(False)
        sb, wrap = side.body, px(300)
        ttk.Label(sb, text="PRICE DETAILS", style="Caption.Card.TLabel").pack(anchor="w")
        row = ttk.Frame(sb, style="Card.TFrame")
        row.pack(fill="x", pady=(px(6), 0))
        self.cost_icon = ttk.Label(row, style="Card.TLabel")
        self.cost_icon.pack(side="left", padx=(0, px(10)))
        self.cost_name = tk.StringVar(value="Nothing selected")
        self.cost_label = ttk.Label(row, textvariable=self.cost_name, style="ItemName.Card.TLabel",
                                    wraplength=wrap - px(46), justify="left")
        self.cost_label.pack(side="left", fill="x", expand=True)
        self.cost_id = tk.StringVar(value="Pick a row to see its price")
        ttk.Label(sb, textvariable=self.cost_id, style="Muted.Card.TLabel", wraplength=wrap,
                  justify="left").pack(anchor="w")
        ttk.Separator(sb).pack(fill="x", pady=px(14))
        self.cost_unit, self.cost_source = tk.StringVar(value="—"), tk.StringVar()
        self.cost_qty, self.cost_subtotal = tk.StringVar(), tk.StringVar()
        ttk.Label(sb, textvariable=self.cost_unit, style="Gold.Card.TLabel").pack(anchor="w")
        for var in (self.cost_source, self.cost_qty, self.cost_subtotal):
            ttk.Label(sb, textvariable=var, style="Muted.Card.TLabel", wraplength=wrap,
                      justify="left").pack(anchor="w", pady=(px(4), 0))
        ttk.Separator(sb).pack(fill="x", pady=px(14))
        self.cost_price_button = ttk.Button(sb, text="Set Manual Price…", style="Accent.TButton",
                                            command=self._cost_set_price)
        self.cost_price_button.pack(fill="x")
        self.cost_link_button = ttk.Button(sb, text="Link Bazaar Product…", command=self._cost_link_product)
        self.cost_link_button.pack(fill="x", pady=(px(7), 0))
        self.cost_clear_button = ttk.Button(sb, text="Clear Override / Link", style="Danger.TButton",
                                            command=self._cost_clear_override)
        self.cost_clear_button.pack(fill="x", pady=(px(7), 0))
        ttk.Separator(sb).pack(fill="x", pady=px(14))
        self.cost_note = tk.StringVar(value="Prices are indicative and move constantly.")
        ttk.Label(sb, textvariable=self.cost_note, style="Muted.Card.TLabel", wraplength=wrap,
                  justify="left").pack(anchor="w", pady=(0, px(10)))
        ttk.Button(sb, text="Show in Recipe Tree", command=self._cost_show_in_tree).pack(fill="x")
        self.cost_mode_var.trace_add("write", lambda *_: self._cost_mode_changed())
        self.cost_scope_var.trace_add("write", lambda *_: self._render_cost())

    def _cost_mode_changed(self):
        self.settings["price_mode"] = next((k for k, v in he.PRICE_MODES
                                            if v == self.cost_mode_var.get()), "insta")
        self._refresh_all()

    def _show_view(self, name):
        self.current_view = name
        for key, button in self.view_buttons.items():
            button.configure(style="SegOn.TButton" if key == name else "Seg.TButton")
        for key, widget in (("tree", self.tree_view), ("resources", self.res_view),
                            ("stock", self.stock_view), ("cost", self.cost_view)):
            widget.grid() if key == name else widget.grid_remove()
        if self._dirty.get(name):
            self._render_view(name)
        self.view_hint.set({
            "tree": "Expand a branch to trace ingredients  •  green rows are covered  •  ＋ adds one  •  "
                    "Coins behave like any other ingredient",
            "resources": "Every item combined across all sub-recipes — expand a row to see where it is used",
            "stock": "The item map pulled from your account — expand a row to see which container holds it",
            "cost": "Bazaar prices for what you are missing, plus raw coin costs  •  Ctrl+P refreshes prices",
        }[name])

    def _set_mode(self, mode):
        if mode == self.mode:
            return
        if mode == "synced" and not self.snapshot:
            key, _ = self.sync.api_key()
            if not key:
                messagebox.showinfo("Link your account first",
                                    "Add your API key and Minecraft name in Settings, then sync.")
                self._open_settings()
                return
            self.mode = "synced"
            self._sync_now()
        else:
            self.mode = mode
        self.settings["mode"] = self.mode
        self._refresh_all()

    def _render_view(self, name):
        {"tree": self._render_tree, "resources": self._render_resources,
         "stock": self._render_stock, "cost": self._render_cost}[name]()
        self._dirty[name] = False

    def _set_open(self, tree, flag):
        for iid in tree.get_children(""):
            tree.item(iid, open=flag)

    # ---------------- item helpers ----------------
    def display_name(self, item_id):
        item_id = normalize_id(item_id)
        if item_id == COINS:
            return "Coins"
        if item_id in self.items:
            return self.items[item_id].get("name") or item_id
        return FALLBACK_NAMES.get(item_id, item_id.replace("_", " ").title())

    def available_item_ids(self):
        return sorted(set(self.items) | set(RECIPES) | set(FALLBACK_NAMES) | set(self.stock) | {COINS})

    def rarity_of(self, item_id):
        if normalize_id(item_id) == COINS:
            return "LEGENDARY"
        tier = str((self.items.get(item_id) or {}).get("tier", "")).strip().upper().replace(" ", "_")
        return tier if tier in RARITY_COLORS else "UNKNOWN"

    def rarity_color(self, item_id):
        return GOLD if normalize_id(item_id) == COINS else RARITY_COLORS[self.rarity_of(item_id)]

    def rarity_tag(self, item_id, done=False, selected=False, alternate=False):
        tag = f"rar_{self.rarity_of(item_id)}"
        if done:
            return tag + ("_done_selected" if selected else "_done")
        if selected:
            return tag + "_selected"
        return tag + ("_alt" if alternate else "")

    def _q(self, item_id, amount):
        return he.qty(item_id, amount)

    def price_of(self, item_id):
        return self.market.unit_price(normalize_id(item_id), self.settings.get("price_mode", "insta"),
                                      self.prices, self.bazaar_links)

    def _pick_item(self, title="Choose Item", initial_id=None):
        return ItemPicker(self, title, self.available_item_ids(), self.display_name, initial_id,
                          icon_for=lambda i: self.icons.get(i, network=False)).result

    def _highlight(self, tree, name):
        sel = tree.selection()
        current = sel[0] if sel else None
        previous = self._highlight_ids.get(name)
        for node in {previous, current} - {None}:
            if not tree.exists(node):
                continue
            tags = tuple(t for t in tree.item(node, "tags") if not t.startswith("rar_"))
            if "group" in tags:
                tree.item(node, tags=tags)
                continue
            item_id = tags[1] if len(tags) >= 2 else ""
            tree.item(node, tags=tags + (self.rarity_tag(item_id, done="complete" in tags,
                                                         selected=node == current,
                                                         alternate="stripe" in tags),))
        self._highlight_ids[name] = current

    def _icons_changed(self):
        if self._icon_job is None:
            self._icon_job = self.after(200, self._refresh_icons)

    def _iter_nodes(self, tree, parent=""):
        for child in tree.get_children(parent):
            yield child
            yield from self._iter_nodes(tree, child)

    def _refresh_icons(self):
        self._icon_job = None
        for tree in (self.tree, self.res_tree, self.stock_tree, self.cost_tree):
            for node in self._iter_nodes(tree):
                tags = tree.item(node, "tags")
                if len(tags) >= 2 and tags[1] and "group" not in tags:
                    tree.item(node, image=self.icons.get(normalize_id(tags[1])))
        self.goal_icon.configure(image=self.icons.get(self.root_item, big=True))
        res_item = None
        if self.res_sel:
            res_item = self.res_sel[1] if self.res_sel[0] == "resource" else self.res_sel[1][-1]
        for p, item in ((self.p_tree, self.sel_item), (self.p_stock, self.stock_sel), (self.p_res, res_item)):
            if item:
                p["icon"].configure(image=self.icons.get(normalize_id(item), big=True))
        if self.cost_sel:
            self.cost_icon.configure(image=self.icons.get(self.cost_sel, big=True))

    def _on_background_click(self, event):
        widget = event.widget
        try:
            if widget.winfo_toplevel() is not self:
                return
            plain = widget is self or (widget.winfo_class() == "TFrame"
                                       and str(widget.cget("style")) in ("", "TFrame"))
        except (tk.TclError, AttributeError, KeyError):
            return
        if plain:
            self._deselect_current()

    def _deselect_tree(self):
        self.tree.selection_remove(*self.tree.selection())
        self.sel_item = self.sel_path = None
        self._panel_clear(self.p_tree)

    def _deselect_res(self):
        self.res_tree.selection_remove(*self.res_tree.selection())
        self.res_sel = None
        self._panel_clear(self.p_res)

    def _deselect_stock(self):
        self.stock_tree.selection_remove(*self.stock_tree.selection())
        self.stock_sel = None
        self._panel_clear(self.p_stock)

    def _deselect_cost(self):
        self.cost_tree.selection_remove(*self.cost_tree.selection())
        self.cost_sel = None
        self.cost_name.set("Nothing selected")
        self.cost_id.set("Pick a row to see its price")
        self.cost_unit.set("—")
        for var in (self.cost_source, self.cost_qty, self.cost_subtotal):
            var.set("")
        self.cost_icon.configure(image="")
        self.cost_label.configure(foreground=TEXT_STRONG)

    def _deselect_current(self):
        {"tree": self._deselect_tree, "resources": self._deselect_res,
         "stock": self._deselect_stock, "cost": self._deselect_cost}[self.current_view]()

    def _plus_hit(self, tree, event, column):
        if tree.identify_region(event.x, event.y) != "cell" or tree.identify_column(event.x) != column:
            return None
        return tree.identify_row(event.y) or None

    def _plus_motion(self, event, tree, column):
        tree.configure(cursor="hand2" if self._plus_hit(tree, event, column) else "")

    def _plus_click(self, event, tree, column, bump, deselect):
        node = self._plus_hit(tree, event, column)
        if not node:
            if tree.identify_region(event.x, event.y) == "nothing":
                deselect()
                return "break"
            return None
        tree.selection_set(node)
        tree.focus(node)
        bump(node)
        return "break"

    def _cost_click(self, event):
        if self.cost_tree.identify_region(event.x, event.y) == "nothing":
            self._deselect_cost()
            return "break"
        return None

    # ---------------- item catalogue ----------------
    def _load_api(self):
        self._set_status("Downloading item data…", "Connecting to Hypixel…", "busy")
        threading.Thread(target=self._api_worker, daemon=True).start()

    def _api_worker(self):
        try:
            req = urllib.request.Request(API_URL, headers={"User-Agent": hs.APP_AGENT})
            with urllib.request.urlopen(req, timeout=20) as response:
                data = json.loads(response.read().decode("utf-8"))
            if not data.get("success"):
                raise RuntimeError("Hypixel API returned success=false")
            parsed, names = {}, {}
            for item in data.get("items", []):
                item_id, name = item.get("id"), item.get("name")
                if item_id and name:
                    item_id = normalize_id(item_id)
                    parsed[item_id] = item
                    names[name.casefold()] = item_id
            self.after(0, lambda: self._api_loaded(parsed, names))
        except Exception as exc:
            message = str(exc)
            self.after(0, lambda: self._api_failed(message))

    def _api_loaded(self, items, names):
        self.items, self.name_to_id = items, names
        if self.root_item not in self.items:
            candidate = self.name_to_id.get("titanium drill dr-x355")
            if candidate:
                self.root_item = candidate
        self._set_status(f"{len(self.items):,} items loaded", "Hypixel item catalogue connected", "ok")
        self._refresh_all()

    def _api_failed(self, error):
        self._set_status("Offline / local data", "Could not connect to Hypixel", "error")
        self._refresh_all(persist=False)
        messagebox.showwarning("Hypixel API unavailable",
                               "The item catalogue could not be downloaded.\n\n" + error +
                               "\n\nSaved recipes, cached inventory, cached prices and progress still "
                               "work; item names may fall back to IDs.")

    # ---------------- account sync ----------------
    def _open_settings(self):
        SettingsDialog(self)

    def _settings_closed(self):
        self.cost_mode_var.set(he.PRICE_MODE_LABELS.get(self.settings.get("price_mode", "insta"),
                                                        he.PRICE_MODE_LABELS["insta"]))
        self._refresh_all()

    def _update_account_labels(self):
        key, _ = self.sync.api_key()
        name = self.settings.get("username") or (self.snapshot or {}).get("name") or ""
        profile = (self.snapshot or {}).get("profile_name", "")
        if not key:
            self.account_var.set("No API key  •  open Settings")
        elif not name:
            self.account_var.set("Key ready  •  no account linked")
        else:
            self.account_var.set(name + (f"  •  {profile}" if profile else ""))
        for key_name, button in self.mode_buttons.items():
            button.configure(style="SegOn.TButton" if key_name == self.mode else "Seg.TButton")

    def _startup_sync_check(self):
        if not self.settings.get("auto_sync_on_launch"):
            return
        key, _ = self.sync.api_key()
        if not key or not (self.settings.get("uuid") or self.settings.get("username")):
            return
        interval = self.sync.interval(self.settings.get("min_refresh_seconds"))
        fetched = float((self.snapshot or {}).get("fetched_at", 0) or 0)
        now = time.time()
        if fetched and (now - fetched) < interval:
            return
        if self.last_closed and fetched and (now - self.last_closed) < interval:
            return
        if self.sync.cooldown_remaining(interval) > 0:
            return
        self._sync_now(auto=True)

    def _sync_now(self, auto=False):
        if self._syncing:
            return
        key, _ = self.sync.api_key()
        if not key:
            if not auto:
                messagebox.showinfo("API key needed",
                                    "Add your own Hypixel API key in Settings.\n\nGet one from "
                                    "developer.hypixel.net after registering your application. The key "
                                    "is stored locally and never bundled into a build.\n\nBazaar "
                                    "prices work without a key.")
                self._open_settings()
            return
        if not (self.settings.get("uuid") or self.settings.get("username")):
            if not auto:
                messagebox.showinfo("Link your account", "Enter your Minecraft username or UUID first.")
                self._open_settings()
            return
        interval = self.sync.interval(self.settings.get("min_refresh_seconds"))
        remaining = self.sync.cooldown_remaining(interval)
        if remaining > 0:
            if not auto:
                messagebox.showinfo("Cached data is still fresh",
                                    f"Next sync available in {hs.format_seconds(remaining)}.\n\n"
                                    "Hypixel's developer policy asks for caching and respectful "
                                    "intervals, so the cooldown survives restarts.")
            return
        self._syncing = True
        self.sync_button.configure(text="⟳  Syncing…", state="disabled")
        self._set_status("Syncing account…", "Reading your SkyBlock storage and coins", "busy")
        threading.Thread(target=self._sync_worker, daemon=True).start()

    def _sync_worker(self):
        def progress(text):
            self.after(0, lambda: self._set_status("Syncing account…", text, "busy"))

        try:
            snapshot = self.sync.sync(username=self.settings.get("username", ""),
                                      uuid=self.settings.get("uuid", ""),
                                      profile_id=self.settings.get("profile_id", ""),
                                      include_museum=bool(self.settings.get("fetch_museum", True)),
                                      min_interval=self.settings.get("min_refresh_seconds"),
                                      progress=progress)
            self.after(0, lambda: self._sync_done(snapshot))
        except hs.SyncError as exc:
            message, hint = str(exc), exc.hint
            self.after(0, lambda: self._sync_failed(message, hint))
        except Exception as exc:
            message = str(exc)
            self.after(0, lambda: self._sync_failed("Unexpected sync error.", message))

    def _sync_done(self, snapshot):
        self._syncing = False
        self.sync_button.configure(text="⟳  Sync Hypixel", state="normal")
        self.snapshot = self._migrate_snapshot(snapshot)
        self.settings["uuid"] = snapshot.get("uuid", self.settings.get("uuid", ""))
        self.settings["username"] = snapshot.get("name", self.settings.get("username", ""))
        if not self.settings.get("profile_id"):
            self.settings["profile_id"] = snapshot.get("profile_id", "")
        self.mode = self.settings["mode"] = "synced"
        self._recompute_stock()
        total = sum(v for k, v in self.stock.items() if k != COINS)
        self._set_status("Account synced",
                         f"{comma(total)} items and {he.format_coins(self.stock.get(COINS, 0))} coins read",
                         "ok")
        self._refresh_all()
        self._startup_price_check()

    def _sync_failed(self, message, hint):
        self._syncing = False
        self.sync_button.configure(text="⟳  Sync Hypixel", state="normal")
        self._set_status("Sync failed", "Using cached data", "error")
        messagebox.showwarning("Could not sync", message + ("\n\n" + hint if hint else ""))
        self._update_account_labels()

    # ---------------- prices ----------------
    def _startup_price_check(self):
        if not self.settings.get("auto_refresh_prices"):
            return
        interval = self.market.interval(self.settings.get("bazaar_interval"))
        if self.market.age() < interval or self.market.cooldown_remaining(interval) > 0:
            return
        self._refresh_prices(auto=True)

    def _refresh_prices(self, auto=False):
        if self._pricing:
            return
        interval = self.market.interval(self.settings.get("bazaar_interval"))
        remaining = self.market.cooldown_remaining(interval)
        if remaining > 0:
            if not auto:
                messagebox.showinfo("Prices are still fresh",
                                    f"Next price refresh available in {hs.format_seconds(remaining)}.\n\n"
                                    "Bazaar prices are cached to keep requests respectful.")
            return
        self._pricing = True
        self.price_button.configure(text="⟳  Refreshing…", state="disabled")

        def work():
            try:
                self.market.refresh(interval)
                self.after(0, self._prices_done)
            except hs.SyncError as exc:
                message, hint = str(exc), exc.hint
                self.after(0, lambda: self._prices_failed(message, hint, auto))
            except Exception as exc:
                message = str(exc)
                self.after(0, lambda: self._prices_failed("Unexpected price error.", message, auto))

        threading.Thread(target=work, daemon=True).start()

    def _prices_done(self):
        self._pricing = False
        self.price_button.configure(text="⟳  Refresh Prices", state="normal")
        self._refresh_all()

    def _prices_failed(self, message, hint, auto):
        self._pricing = False
        self.price_button.configure(text="⟳  Refresh Prices", state="normal")
        if not auto:
            messagebox.showwarning("Could not refresh prices", message + ("\n\n" + hint if hint else ""))
        self._refresh_all(persist=False)

    def _tick(self):
        interval = self.sync.interval(self.settings.get("min_refresh_seconds"))
        fetched = float((self.snapshot or {}).get("fetched_at", 0) or 0)
        parts = ["Last sync " + hs.format_age(time.time() - fetched) if fetched else "Never synced"]
        remaining = self.sync.cooldown_remaining(interval)
        parts.append("next sync in " + hs.format_seconds(remaining) if remaining > 0 else "sync available")
        age = self.market.age()
        price_text = hs.format_age(time.time() - age) if age != float("inf") else "not loaded"
        parts.append("prices " + price_text)
        if self.mode == "manual":
            parts.append("manual counting")
        self.footer_var.set("   •   ".join(parts) + f"   •   {PLUS} adds one   •   right-click for options")
        self.price_age_var.set(f"{comma(len(self.market.prices))} bazaar products  •  prices {price_text}")
        self.after(1000, self._tick)

    # ---------------- stock pool ----------------
    def _enabled(self, source):
        return bool(self.settings["sources"].get(source, hs.DEFAULT_SOURCES.get(source, True)))

    def _recompute_stock(self):
        stock = {}
        for source, counts in ((self.snapshot or {}).get("counts") or {}).items():
            if not self._enabled(source):
                continue
            for item, amount in counts.items():
                item = normalize_id(item)
                stock[item] = stock.get(item, 0) + int(amount)
        for item, amount in self.extra.items():
            item = normalize_id(item)
            stock[item] = stock.get(item, 0) + int(amount)
        self.stock = {k: max(0, v) for k, v in stock.items()}

    def synced_count(self, item_id):
        item_id = normalize_id(item_id)
        return sum(int(counts.get(item_id, 0))
                   for source, counts in ((self.snapshot or {}).get("counts") or {}).items()
                   if self._enabled(source))

    def stock_breakdown(self, item_id):
        item_id = normalize_id(item_id)
        coin = item_id == COINS
        parts = []
        for source, counts in ((self.snapshot or {}).get("counts") or {}).items():
            amount = int(counts.get(item_id, 0))
            if amount and self._enabled(source):
                parts.append(f"{hs.friendly_source(source)} {he.format_coins(amount) if coin else comma(amount)}")
        manual = int(self.extra.get(item_id, 0))
        if manual:
            parts.append(f"Manual {he.format_coins(manual) if coin else comma(manual)}")
        return ", ".join(parts)

    def _set_stock(self, item_id, value):
        item_id = normalize_id(item_id)
        delta = max(0, int(value)) - self.synced_count(item_id)
        if delta:
            self.extra[item_id] = delta
        else:
            self.extra.pop(item_id, None)
        self._refresh_all()

    def _bump_stock(self, item_id, amount=1):
        item_id = normalize_id(item_id)
        self._set_stock(item_id, self.stock.get(item_id, 0) + amount)

    # ---------------- plan engine ----------------
    def _recipe_of(self, item_id):
        return he.merged_recipe(RECIPES, normalize_id(item_id))

    def _rebuild_plan(self):
        self.plan, self.plan_order, truncated = {}, [], []

        def record(path, item_id, required, owned, recipe):
            entry = {"path": path, "key": path_to_key(path), "item": item_id, "required": required,
                     "owned": owned, "crafted": bool(recipe)}
            self.plan[entry["key"]] = entry
            self.plan_order.append(entry)

        if self.mode == "synced":
            pool = dict(self.stock)

            def walk(item_id, required, ancestors, path):
                if len(self.plan_order) >= MAX_TREE_NODES or len(path) > MAX_DEPTH:
                    truncated.append(item_id)
                    return
                item_id = normalize_id(item_id)
                recipe = self._recipe_of(item_id)
                take = min(required, pool.get(item_id, 0))
                pool[item_id] = pool.get(item_id, 0) - take
                record(path, item_id, required, take, recipe)
                short, new = required - take, ancestors | {item_id}
                for child, per in recipe:
                    if child not in ancestors:
                        walk(child, per * short, new, path + (child,))
        else:
            def walk(item_id, multiplier, ancestors, path):
                if len(self.plan_order) >= MAX_TREE_NODES or len(path) > MAX_DEPTH:
                    truncated.append(item_id)
                    return
                item_id = normalize_id(item_id)
                recipe = self._recipe_of(item_id)
                owned = max(0, int(self.obtained.get(path_to_key(path), 0)))
                record(path, item_id, multiplier, owned, recipe)
                new = ancestors | {item_id}
                for child, per in recipe:
                    if child not in ancestors:
                        walk(child, per * multiplier, new, path + (child,))

        walk(self.root_item, 1, {self.root_item}, (self.root_item,))
        self.plan_truncated = sorted(set(truncated))

    def _totals(self):
        raw = [e for e in self.plan_order if not e["crafted"] and e["required"] > 0 and e["item"] != COINS]
        required = sum(e["required"] for e in raw)
        covered = sum(min(e["required"], e["owned"]) for e in raw)
        return {"required": required, "covered": covered,
                "pct": (covered / required * 100) if required else (100.0 if self.plan_order else 0.0),
                "missing": sum(max(0, e["required"] - e["owned"]) for e in raw),
                "raw_types": len({e["item"] for e in raw})}

    def _demand(self):
        out = {}
        for e in self.plan_order:
            needed, used = out.get(e["item"], (0, 0))
            out[e["item"]] = (needed + e["required"], used + min(e["required"], e["owned"]))
        return out

    def _cost_inputs(self, scope):
        if scope == "full":
            totals = he.requirements_from_scratch(self.root_item, RECIPES)
            rows, coins = [], 0
            for item, amount in totals.items():
                if item == COINS:
                    coins += amount
                else:
                    rows.append((item, amount, 0, bool(RECIPES.get(item))))
            return rows, [("whole recipe, from scratch", coins, 0)]
        groups, coin_places = {}, []
        for e in self.plan_order:
            if e["item"] == COINS:
                chain = " → ".join(self.display_name(p) for p in e["path"][:-1])
                coin_places.append((f"for {chain}" if chain else "for the goal itself",
                                    e["required"], e["owned"]))
                continue
            needed, covered = groups.get(e["item"], (0, 0))
            groups[e["item"]] = (needed + e["required"], covered + min(e["required"], e["owned"]))
        return [(i, n, c, bool(RECIPES.get(i))) for i, (n, c) in groups.items()], coin_places

    def _report(self, scope="finish"):
        rows, coin_places = self._cost_inputs(scope)
        return he.cost_report(rows, coin_places, RECIPES, self.price_of)

    def _refresh_all(self, persist=True):
        self._recompute_stock()
        self._rebuild_plan()
        totals = self._totals()
        self.progress["value"] = totals["pct"]
        self.progress_var.set(f"{totals['pct']:.1f}%")
        self.missing_var.set(comma(totals["missing"]))
        self.missing_sub.set(f"{totals['raw_types']} raw item types" if totals["required"]
                             else "all raw items on hand")
        self.goal_var.set(self.display_name(self.root_item))
        self.goal_id_var.set(self.root_item)
        self.goal_icon.configure(image=self.icons.get(self.root_item, big=True))
        self.goal_label.configure(foreground=self.rarity_color(self.root_item))
        report = self._report("finish")
        available, outlay = self.stock.get(COINS, 0), report["total_outlay"]
        gap = max(0, outlay - available)
        self.coin_var.set(he.format_coins(outlay))
        bits = [f"have {he.format_coins(available)}",
                f"short {he.format_coins(gap)}" if gap > 0 else "fully funded"]
        if report["unpriced"]:
            bits.append(f"{len(report['unpriced'])} unpriced")
        self.coin_sub_var.set("  •  ".join(bits))
        types = len([k for k in self.stock if k != COINS])
        self.stock_var.set(comma(sum(v for k, v in self.stock.items() if k != COINS)))
        active = sum(1 for s in ((self.snapshot or {}).get("counts") or {})
                     if self._enabled(s) and s not in hs.COIN_SOURCES)
        self.stock_sub_var.set(f"{comma(types)} item types  •  {active} containers" if self.snapshot
                               else "link your account")
        self._dirty = {"tree": True, "resources": True, "stock": True, "cost": True}
        self._render_view(self.current_view)
        self._update_account_labels()
        if persist:
            self._save()

    # ---------------- rendering ----------------
    def _capture_state(self, tree):
        opened = {i for i in self._iter_nodes(tree) if tree.item(i, "open")}
        sel = tree.selection()
        try:
            scroll = tree.yview()[0]
        except (tk.TclError, IndexError, TypeError):
            scroll = 0.0
        return opened, (sel[0] if sel else None), scroll

    def _restore_state(self, tree, state):
        opened, selected, scroll = state
        for iid in opened:
            if tree.exists(iid):
                tree.item(iid, open=True)
        try:
            tree.yview_moveto(scroll)
        except tk.TclError:
            pass
        if selected and tree.exists(selected):
            tree.selection_set(selected)
            tree.focus(selected)
            tree.see(selected)

    @staticmethod
    def _status_text(required, owned):
        if required <= 0:
            return "✓ Not needed"
        if owned >= required:
            return "✓ Complete"
        return "In progress" if owned > 0 else "Needed"

    def _render_tree(self):
        state = self._capture_state(self.tree)
        self.tree.delete(*self.tree.get_children(""))
        self._highlight_ids["tree"] = None
        first = not state[0]
        for index, entry in enumerate(self.plan_order):
            path, item = entry["path"], entry["item"]
            parent = "t|" + path_to_key(path[:-1]) if len(path) > 1 else ""
            required, owned = entry["required"], entry["owned"]
            done = (required > 0 and owned >= required) or required == 0
            if len(path) == 1:
                kind, text = "root", "◆  " + self.display_name(item)
            else:
                siblings = self._recipe_of(path[-2])
                pos = next((i for i, (c, _n) in enumerate(siblings) if c == item), 0)
                conn = "└─" if pos == len(siblings) - 1 else "├─"
                kind = "branch" if entry["crafted"] else "missing_recipe"
                text = f"{conn}  {self.display_name(item)}"
            tags = [kind, item] + (["stripe"] if index % 2 else []) + (["complete"] if done else [])
            tags.append(self.rarity_tag(item, done, alternate=bool(index % 2)))
            self.tree.insert(parent, "end", iid="t|" + entry["key"], text=text,
                             values=(self._q(item, required), self._q(item, owned),
                                     self._q(item, max(0, required - owned)),
                                     self._status_text(required, owned), PLUS),
                             open=(len(path) <= 2 and entry["crafted"]) if first else False,
                             tags=tuple(tags), image=self.icons.get(item))
        self._restore_state(self.tree, state)
        if self.tree.selection():
            self._sel_tree()
        if self.plan_truncated:
            self.view_hint.set("Tree stopped at a safety limit — check for a recipe loop involving "
                               + ", ".join(self.plan_truncated[:4]))

    def _render_resources(self):
        tree = self.res_tree
        state = self._capture_state(tree)
        tree.delete(*tree.get_children(""))
        self._highlight_ids["resources"] = None
        groups = {}
        for entry in self.plan_order:
            if len(entry["path"]) == 1 and entry["crafted"]:
                continue
            groups.setdefault(entry["item"], []).append(entry)
        query = self.res_filter.get().strip().casefold()
        crafted_types = raw_types = raw_missing = coin_short = 0
        rows = []
        for item, places in groups.items():
            crafted = bool(RECIPES.get(item))
            needed = sum(p["required"] for p in places)
            covered = sum(min(p["required"], p["owned"]) for p in places)
            if item == COINS:
                coin_short += needed - covered
            elif crafted:
                crafted_types += 1
            else:
                raw_types += 1
                raw_missing += needed - covered
            if (crafted and not self.res_show_crafted.get()) or (not crafted and not self.res_show_raw.get()):
                continue
            name = self.display_name(item)
            if query and query not in name.casefold() and query not in item.casefold():
                continue
            if self.res_hide_done.get() and needed > 0 and covered >= needed:
                continue
            order = 0 if item == COINS else (1 if crafted else 2)
            rows.append((order, name.casefold(), item, name, crafted, places, needed, covered))
        rows.sort(key=lambda r: (r[0], r[1]))
        for n, (_o, _s, item, name, crafted, places, needed, covered) in enumerate(rows):
            done = needed > 0 and covered >= needed
            kind = "Coins" if item == COINS else ("Crafted" if crafted else "Raw")
            iid = "r|" + item
            tree.insert("", "end", iid=iid, text=name,
                        values=(kind, self._q(item, needed), self._q(item, covered),
                                self._q(item, needed - covered), self._status_text(needed, covered), PLUS),
                        tags=("resource", item) + (("stripe",) if n % 2 else ())
                             + (("complete",) if done else ())
                             + (self.rarity_tag(item, done, alternate=bool(n % 2)),),
                        image=self.icons.get(item))
            for pn, place in enumerate(places):
                req, own = place["required"], place["owned"]
                pdone = (req > 0 and own >= req) or req == 0
                chain = " → ".join(self.display_name(p) for p in place["path"][1:-1])
                where = f"in {chain}" if chain else f"directly in {self.display_name(place['path'][0])}"
                tree.insert(iid, "end", iid="p|" + place["key"],
                            text=f"{self._q(item, req)} × {where}",
                            values=(kind, self._q(item, req), self._q(item, own),
                                    self._q(item, max(0, req - own)), self._status_text(req, own), PLUS),
                            tags=("occurrence", item) + (("stripe",) if pn % 2 else ())
                                 + (("complete",) if pdone else ())
                                 + (self.rarity_tag(item, pdone, alternate=bool(pn % 2)),),
                            image=self.icons.get(item))
        summary = (f"{crafted_types:,} crafted types   •   {raw_types:,} raw item types   •   "
                   f"{comma(raw_missing)} raw items still to gather")
        if coin_short:
            summary += f"   •   {he.format_coins(coin_short)} coins still to pay"
        self.res_summary.set(summary)
        self._restore_state(tree, state)
        if tree.selection():
            self._sel_res()

    def _render_stock(self):
        tree = self.stock_tree
        state = self._capture_state(tree)
        tree.delete(*tree.get_children(""))
        self._highlight_ids["stock"] = None
        demand = self._demand()
        query = self.stock_filter.get().strip().casefold()
        rows = []
        for item, total in self.stock.items():
            needed, used = demand.get(item, (0, 0))
            if self.stock_only_recipe.get() and needed <= 0:
                continue
            name = self.display_name(item)
            if query and query not in name.casefold() and query not in item.casefold():
                continue
            rows.append((0 if item == COINS else 1, name.casefold(), item, name, total, needed, used))
        rows.sort()
        for n, (_o, _s, item, name, total, needed, used) in enumerate(rows):
            done = needed > 0 and used >= needed
            tree.insert("", "end", iid="s|" + item, text=name,
                        values=(self._q(item, total), self._q(item, needed), self._q(item, used),
                                self._q(item, max(0, total - used)), self.stock_breakdown(item) or "—", PLUS),
                        tags=("resource", item) + (("stripe",) if n % 2 else ())
                             + (("complete",) if done else ())
                             + (self.rarity_tag(item, done, alternate=bool(n % 2)),),
                        image=self.icons.get(item))
            for source, counts in sorted(((self.snapshot or {}).get("counts") or {}).items()):
                amount = int(counts.get(item, 0))
                if not amount:
                    continue
                enabled = self._enabled(source)
                tree.insert("s|" + item, "end", iid=f"src|{item}|{source}",
                            text=hs.friendly_source(source) + ("" if enabled else "  (not counted)"),
                            values=(self._q(item, amount), "", "", "",
                                    "counted" if enabled else "excluded", ""),
                            tags=("occurrence", item, self.rarity_tag(item)), image=self.icons.get(item))
            manual = int(self.extra.get(item, 0))
            if manual:
                tree.insert("s|" + item, "end", iid=f"src|{item}|manual", text="Manual / island chests",
                            values=(self._q(item, manual), "", "", "", "always counted", ""),
                            tags=("occurrence", item, self.rarity_tag(item)), image=self.icons.get(item))
        types = len([k for k in self.stock if k != COINS])
        self.stock_summary.set(f"{len(rows):,} rows shown   •   {comma(types)} item types   •   "
                               f"{he.format_coins(self.stock.get(COINS, 0))} coins   •   "
                               "expand a row to see which container holds it")
        self._restore_state(tree, state)
        if tree.selection():
            self._sel_stock()

    def _render_cost(self):
        tree = self.cost_tree
        state = self._capture_state(tree)
        tree.delete(*tree.get_children(""))
        self._highlight_ids["cost"] = None
        self._cost_groups = {}
        scope = next((k for k, v in he.SCOPES if v == self.cost_scope_var.get()), "finish")
        report = self._report(scope)
        available = self.stock.get(COINS, 0)
        first = not state[0]

        def group(iid, text, subtotal, count):
            self._cost_groups[iid] = subtotal
            tree.insert("", "end", iid=iid, text=text,
                        values=("", "", "", he.format_coins(subtotal), f"{count} rows"),
                        open=first, tags=("group", ""), image=self.icons.get(COINS))

        group("g|raw", f"RAW ITEMS TO BUY  —  {he.format_coins(report['shopping'])} coins",
              report["shopping"], len(report["raw_rows"]))
        for n, row in enumerate(report["raw_rows"]):
            item = row["item"]
            unit = "—" if row["unit"] is None else he.format_coins(row["unit"])
            sub = "—" if row["unit"] is None else he.format_coins(row["subtotal"])
            done = row["short"] <= 0
            tree.insert("g|raw", "end", iid="c|raw|" + item, text=self.display_name(item),
                        values=("Raw", self._q(item, row["short"]), unit, sub, row["source"]),
                        tags=("costrow", item) + (("stripe",) if n % 2 else ())
                             + (("complete",) if done else ())
                             + (self.rarity_tag(item, done, alternate=bool(n % 2)),),
                        image=self.icons.get(item))
        group("g|coins", f"RAW COIN COSTS IN RECIPES  —  {he.format_coins(report['coin_total'])} coins",
              report["coin_total"], len(report["coin_rows"]))
        if not report["coin_rows"]:
            tree.insert("g|coins", "end", iid="c|coin|none",
                        text="No recipe in this tree costs coins — use ＋ Add Coins in the recipe editor",
                        values=("", "", "", "", ""), tags=("costrow", COINS), image=self.icons.get(COINS))
        for n, row in enumerate(report["coin_rows"]):
            done = row["short"] <= 0
            tree.insert("g|coins", "end", iid=f"c|coin|{n}", text="Coins " + row["label"],
                        values=("Coins", he.format_coins(row["required"]), "1",
                                he.format_coins(row["required"]),
                                f"{he.format_coins(row['covered'])} already covered"),
                        tags=("costrow", COINS) + (("stripe",) if n % 2 else ())
                             + (("complete",) if done else ())
                             + (self.rarity_tag(COINS, done, alternate=bool(n % 2)),),
                        image=self.icons.get(COINS))
        group("g|crafted", "CRAFTED ITEMS — UNIT VALUES (not added to the total)", 0,
              len(report["crafted_rows"]))
        for n, row in enumerate(report["crafted_rows"]):
            item = row["item"]
            prefix = "" if row["complete"] else "≥ "
            tree.insert("g|crafted", "end", iid="c|craft|" + item, text=self.display_name(item),
                        values=("Crafted", self._q(item, row["needed"]),
                                prefix + he.format_coins(row["unit"]),
                                prefix + he.format_coins(row["subtotal"]), "computed from ingredients"),
                        tags=("costrow", item) + (("stripe",) if n % 2 else ())
                             + (self.rarity_tag(item, alternate=bool(n % 2)),),
                        image=self.icons.get(item))
        outlay = report["total_outlay"]
        gap = max(0, outlay - available)
        summary = (f"Shopping cost {he.format_coins_full(report['shopping'])}  +  recipe coins "
                   f"{he.format_coins_full(report['coin_total'])}  =  {he.format_coins_full(outlay)} "
                   f"coins needed   •   you have {he.format_coins_full(available)}   •   "
                   + (f"still short {he.format_coins_full(gap)}" if gap else "fully funded"))
        if report["unpriced"]:
            summary += (f"\n{len(report['unpriced'])} item(s) have no price, so this is a LOWER BOUND: "
                        + ", ".join(self.display_name(i) for i in report["unpriced"][:6])
                        + (" …" if len(report["unpriced"]) > 6 else "")
                        + "  — select one and set a manual price.")
        self.cost_summary.set(summary)
        self._restore_state(tree, state)
        if tree.selection():
            self._sel_cost()

    # ---------------- selection ----------------
    def _sel_tree(self, _event=None):
        self._highlight(self.tree, "tree")
        sel = self.tree.selection()
        if not sel:
            return
        entry = self.plan.get(sel[0][2:])
        if not entry:
            return
        self.sel_item, self.sel_path = entry["item"], entry["path"]
        ancestors = [self.display_name(p) for p in entry["path"][:-1]]
        self._panel_update(self.p_tree, entry["item"], entry["required"], entry["owned"],
                           "In: " + " → ".join(ancestors) if ancestors else "Root goal")
        if entry["item"] == COINS:
            self.p_tree["hint"].set("Raw coin cost. Coins come from your purse and bank; they are "
                                    "never treated as a crafted item.")
            self.p_tree["action"].configure(text="Edit Parent Recipe")
        elif entry["item"] in RECIPES:
            self.p_tree["hint"].set(f"Recipe has {len(RECIPES[entry['item']])} ingredient type(s).")
            self.p_tree["action"].configure(text="Edit Recipe")
        else:
            self.p_tree["hint"].set("No local recipe. If it is crafted or forged, define it here.")
            self.p_tree["action"].configure(text="Define Recipe")

    def _sel_res(self, _event=None):
        self._highlight(self.res_tree, "resources")
        sel = self.res_tree.selection()
        if not sel:
            return
        kind, _sep, ref = sel[0].partition("|")
        if kind == "r":
            places = [e for e in self.plan_order if e["item"] == ref and len(e["path"]) > 1]
            if not places:
                return
            needed = sum(p["required"] for p in places)
            covered = sum(min(p["required"], p["owned"]) for p in places)
            self.res_sel, item = ("resource", ref), ref
            location = f"Needed in {len(places)} place{'s' if len(places) != 1 else ''} in this tree."
        else:
            entry = self.plan.get(ref)
            if not entry:
                return
            self.res_sel, item = ("position", entry["path"]), entry["item"]
            needed, covered = entry["required"], entry["owned"]
            location = "In: " + " → ".join(self.display_name(p) for p in entry["path"][:-1])
        self._panel_update(self.p_res, item, needed, covered, location)
        self.p_res["hint"].set(
            "Synced mode: one account pool, allocated top to bottom. Holding an intermediate item "
            "reduces what its own ingredients need." if self.mode == "synced" else
            "Manual mode: each tree position keeps its own owned count.")

    def _sel_stock(self, _event=None):
        self._highlight(self.stock_tree, "stock")
        sel = self.stock_tree.selection()
        if not sel:
            return
        item = sel[0].split("|")[1]
        self.stock_sel = item
        needed, used = self._demand().get(item, (0, 0))
        total = self.stock.get(item, 0)
        shown = he.format_coins_full(total) if item == COINS else comma(total)
        self._panel_update(self.p_stock, item, needed, used,
                           f"In stock: {shown}  •  surplus {self._q(item, max(0, total - used))}")
        self.p_stock["hint"].set(
            "Coins come from your purse, co-op bank and personal bank at the moment of the sync. "
            "Set a coin total here to correct them." if item == COINS else
            "Island chests are not in the public API. Add those counts here or with Manual Counts.")

    def _sel_cost(self, _event=None):
        self._highlight(self.cost_tree, "cost")
        sel = self.cost_tree.selection()
        if not sel:
            return
        parts = sel[0].split("|")
        buttons = (self.cost_price_button, self.cost_link_button, self.cost_clear_button)
        if parts[0] == "g":
            self.cost_sel = None
            self.cost_icon.configure(image=self.icons.get(COINS, big=True))
            self.cost_label.configure(foreground=GOLD)
            self.cost_name.set({"raw": "Raw items to buy", "coins": "Raw coin costs",
                                "crafted": "Crafted unit values"}.get(parts[1], "Group"))
            self.cost_id.set("Group total")
            self.cost_unit.set(he.format_coins_full(self._cost_groups.get(sel[0], 0)) + " coins")
            for var in (self.cost_source, self.cost_qty, self.cost_subtotal):
                var.set("")
            self.cost_note.set("Crafted unit values are informational and are not added to the total.")
            for b in buttons:
                b.configure(state="disabled")
            return
        item = parts[2] if parts[1] in ("raw", "craft") else COINS
        self.cost_sel = item
        self.cost_icon.configure(image=self.icons.get(item, big=True))
        self.cost_label.configure(foreground=self.rarity_color(item))
        self.cost_name.set(self.display_name(item))
        self.cost_id.set("COINS  •  currency" if item == COINS else item)
        values = self.cost_tree.item(sel[0], "values")
        self.cost_qty.set(f"Quantity: {values[1]}")
        self.cost_subtotal.set(f"Subtotal: {values[3]} coins")
        if item == COINS:
            self.cost_unit.set("1 coin = 1 coin")
            self.cost_source.set("Purse + bank + personal bank, from the profiles response")
            self.cost_note.set("Coins are currency, so they have no bazaar price. Edit the parent "
                               "recipe to change a coin cost.")
            for b in buttons:
                b.configure(state="disabled")
            return
        price, label, product = self.price_of(item)
        self.cost_unit.set("no price" if price is None else he.format_coins_full(price) + " coins each")
        source = label + (f"  •  {product}" if product else "")
        if item in self.bazaar_links:
            source += "  (manual link)"
        self.cost_source.set(source)
        self.cost_note.set("This value is computed from its ingredients. A manual price here overrides "
                           "the computed value for pricing purposes." if parts[1] == "craft" else
                           "Bazaar prices move constantly. Use a manual price for auction-only items, "
                           "or link the item to the right bazaar product.")
        for b in buttons:
            b.configure(state="normal")

    # ---------------- editing ----------------
    def _read_amount(self, var, item=None):
        text = var.get().strip()
        if item == COINS:
            value = he.parse_price(text)
            if value is None:
                messagebox.showerror("Invalid amount", "Enter coins as 1500000, 1.5m or 2b.")
                return None
            return int(round(value))
        try:
            amount = int(text.replace(",", ""))
            if amount < 0:
                raise ValueError
            return amount
        except ValueError:
            messagebox.showerror("Invalid amount", "Enter a non-negative whole number.")
            return None

    def _tree_set(self):
        if not self.sel_path:
            messagebox.showinfo("Select an item", "Select a row in the tree first.")
            return
        amount = self._read_amount(self.p_tree["entry"], self.sel_item)
        if amount is None:
            return
        if self.mode == "synced":
            self._set_stock(self.sel_item, amount)
        else:
            self._set_owned_amount(self.sel_path, amount)
        self._sel_tree()

    def _tree_bump(self, node):
        entry = self.plan.get(node[2:])
        if not entry:
            return
        step = 100000 if entry["item"] == COINS else 1
        if self.mode == "synced":
            self._bump_stock(entry["item"], step)
        else:
            self._set_owned_amount(entry["path"], int(self.obtained.get(entry["key"], 0)) + step)
        if self.tree.exists(node):
            self.tree.selection_set(node)
            self._sel_tree()

    def _tree_bump_selected(self):
        if not self.sel_path:
            messagebox.showinfo("Select an item", "Select a row in the tree first.")
            return
        self._tree_bump("t|" + path_to_key(self.sel_path))

    def _tree_complete(self):
        if not self.sel_path:
            return
        entry = self.plan.get(path_to_key(self.sel_path))
        if not entry:
            return
        if self.mode == "synced":
            short = max(0, entry["required"] - entry["owned"])
            if short:
                self._bump_stock(entry["item"], short)
        else:
            self._set_owned_amount(entry["path"], max(entry["owned"], entry["required"]))
        self._sel_tree()

    def _tree_menu(self, event):
        node = self.tree.identify_row(event.y)
        if not node:
            return
        self.tree.selection_set(node)
        self._sel_tree()
        menu = tk.Menu(self, tearoff=False, background=SURFACE_ALT, foreground=TEXT,
                       activebackground=ACCENT, activeforeground="#fff", activeborderwidth=0,
                       borderwidth=0, relief="flat", font=(FONT, 10))
        menu.add_command(label="Mark Complete", command=self._tree_complete)
        menu.add_command(label="Set Amount", command=self._tree_set)
        menu.add_separator()
        menu.add_command(label="Define / Edit Recipe", command=self._edit_selected_recipe)
        if self.sel_item and self.sel_item != COINS:
            menu.add_command(label="Set Manual Price…", command=lambda: self._set_price(self.sel_item))
            menu.add_command(label="Use as Goal", command=lambda: self._set_root(self.sel_item))
        menu.add_command(label="Add Manual / island-chest count", command=self._edit_manual_counts)
        menu.tk_popup(event.x_root, event.y_root)

    def _res_target(self):
        if not self.res_sel:
            messagebox.showinfo("Select an item", "Select a row in the list first.")
            return None
        kind, ref = self.res_sel
        if kind == "resource":
            places = [e for e in self.plan_order if e["item"] == ref and len(e["path"]) > 1]
            return ref, sum(p["required"] for p in places), sum(min(p["required"], p["owned"]) for p in places)
        entry = self.plan.get(path_to_key(ref))
        return (entry["item"], entry["required"], entry["owned"]) if entry else None

    def _res_apply(self, amount):
        target = self._res_target()
        if not target:
            return
        item, _needed, covered = target
        if self.mode == "synced":
            self._bump_stock(item, amount - covered)
        else:
            kind, ref = self.res_sel
            if kind == "resource":
                self._adjust_manual_resource(item, amount)
            else:
                self._set_owned_amount(ref, amount)
        self._sel_res()

    def _res_set(self):
        item = None
        if self.res_sel:
            item = self.res_sel[1] if self.res_sel[0] == "resource" else self.res_sel[1][-1]
        amount = self._read_amount(self.p_res["entry"], item)
        if amount is None:
            return
        if self.mode == "synced" and item:
            self._set_stock(item, amount)
            self._sel_res()
        else:
            self._res_apply(amount)

    def _res_bump(self, node):
        kind, _sep, ref = node.partition("|")
        if kind == "r":
            self.res_sel = ("resource", ref)
        else:
            entry = self.plan.get(ref)
            if not entry:
                return
            self.res_sel = ("position", entry["path"])
        target = self._res_target()
        if target:
            self._res_apply(target[2] + (100000 if target[0] == COINS else 1))
        if self.res_tree.exists(node):
            self.res_tree.selection_set(node)

    def _res_bump_selected(self):
        target = self._res_target()
        if target:
            self._res_apply(target[2] + (100000 if target[0] == COINS else 1))

    def _res_complete(self):
        target = self._res_target()
        if target:
            self._res_apply(target[1])

    def _res_show_in_tree(self):
        if not self.res_sel:
            return
        kind, ref = self.res_sel
        if kind == "resource":
            place = next((e for e in self.plan_order if e["item"] == ref and len(e["path"]) > 1), None)
            if not place:
                return
            path = place["path"]
        else:
            path = ref
        self._reveal_tree_path(path)

    def _reveal_tree_path(self, path):
        self._show_view("tree")
        iid = "t|" + path_to_key(path)
        if self.tree.exists(iid):
            parent = self.tree.parent(iid)
            while parent:
                self.tree.item(parent, open=True)
                parent = self.tree.parent(parent)
            self.tree.selection_set(iid)
            self.tree.focus(iid)
            self.tree.see(iid)
            self._sel_tree()

    def _stock_set(self):
        if not self.stock_sel:
            messagebox.showinfo("Select an item", "Select a row in the list first.")
            return
        amount = self._read_amount(self.p_stock["entry"], self.stock_sel)
        if amount is not None:
            self._set_stock(self.stock_sel, amount)
            self._sel_stock()

    def _stock_bump(self, node):
        parts = node.split("|")
        if len(parts) < 2:
            return
        self.stock_sel = parts[1]
        self._bump_stock(parts[1], 100000 if parts[1] == COINS else 1)
        if self.stock_tree.exists("s|" + parts[1]):
            self.stock_tree.selection_set("s|" + parts[1])
            self._sel_stock()

    def _stock_bump_selected(self):
        if self.stock_sel:
            self._bump_stock(self.stock_sel, 100000 if self.stock_sel == COINS else 1)
            self._sel_stock()

    def _stock_complete(self):
        if not self.stock_sel:
            return
        needed, used = self._demand().get(self.stock_sel, (0, 0))
        if needed > used:
            self._bump_stock(self.stock_sel, needed - used)
            self._sel_stock()

    def _stock_use_as_goal(self):
        if self.stock_sel == COINS:
            messagebox.showinfo("Not a craftable goal", "Coins are a currency, not a recipe output.")
            return
        if self.stock_sel:
            self._set_root(self.stock_sel)

    def _set_price(self, item_id):
        if not item_id or item_id == COINS:
            return
        dialog = PriceDialog(self, item_id)
        if dialog.result is None:
            return
        action, value = dialog.result
        if action == "set":
            self.prices[item_id] = float(value)
        else:
            self.prices.pop(item_id, None)
        self._refresh_all()
        if self.current_view == "cost":
            self._sel_cost()

    def _cost_set_price(self):
        self._set_price(self.cost_sel)

    def _cost_link_product(self):
        item = self.cost_sel
        if not item or item == COINS:
            return
        if not self.market.prices:
            messagebox.showinfo("No bazaar data", "Refresh prices first, then link the item.")
            return
        picked = ItemPicker(self, "Link to a bazaar product", sorted(self.market.prices), lambda p: p,
                            self.bazaar_links.get(item),
                            extra_note=f"Bazaar product for {self.display_name(item)}").result
        if picked:
            self.bazaar_links[item] = picked
            self._refresh_all()
            self._sel_cost()

    def _cost_clear_override(self):
        item = self.cost_sel
        if not item or item == COINS:
            return
        self.prices.pop(item, None)
        self.bazaar_links.pop(item, None)
        self._refresh_all()
        self._sel_cost()

    def _cost_show_in_tree(self):
        if self.cost_sel:
            entry = next((e for e in self.plan_order if e["item"] == self.cost_sel), None)
            if entry:
                self._reveal_tree_path(entry["path"])

    # ---------------- manual-mode counters ----------------
    def _store_owned(self, path, amount):
        key = path_to_key(path)
        if amount > 0:
            self.obtained[key] = amount
        else:
            self.obtained.pop(key, None)

    def _cascade(self, path, delta, ancestors):
        for ingredient, per in self._recipe_of(path[-1]):
            if ingredient in ancestors:
                continue
            child = path + (ingredient,)
            current = max(0, int(self.obtained.get(path_to_key(child), 0)))
            updated = max(0, current + per * delta)
            self._store_owned(child, updated)
            self._cascade(child, updated - current, ancestors | {ingredient})

    def _set_owned_amount(self, path, amount):
        key = path_to_key(path)
        old, new = max(0, int(self.obtained.get(key, 0))), max(0, int(amount))
        if new == old:
            return
        self._store_owned(path, new)
        self._cascade(path, new - old, set(path))
        self._refresh_all()

    def _adjust_manual_resource(self, item, target):
        places = [e for e in self.plan_order if e["item"] == item and len(e["path"]) > 1]
        needed = sum(p["required"] for p in places)
        covered = sum(min(p["required"], p["owned"]) for p in places)
        delta = max(0, min(int(target), needed)) - covered
        if delta > 0:
            for place in places:
                add = min(max(0, place["required"] - place["owned"]), delta)
                if add:
                    self._set_owned_amount(place["path"], place["owned"] + add)
                    delta -= add
                if delta <= 0:
                    break
        elif delta < 0:
            take = -delta
            for place in reversed(places):
                amount = min(min(place["required"], place["owned"]), take)
                if amount:
                    self._set_owned_amount(place["path"], place["owned"] - amount)
                    take -= amount
                if take <= 0:
                    break

    # ---------------- recipes / goal ----------------
    def _edit_selected_recipe(self):
        if self.sel_item == COINS:
            if self.sel_path and len(self.sel_path) > 1:
                self._open_recipe_editor(self.sel_path[-2])
            else:
                messagebox.showinfo("Coins", "Coins are a currency. Add them as an ingredient inside a recipe.")
            return
        if self.sel_item:
            self._open_recipe_editor(self.sel_item)
        else:
            self._edit_recipe()

    def _edit_recipe(self):
        output = self._pick_item("Choose Recipe Output")
        if not output:
            return
        if output == COINS:
            messagebox.showinfo("Coins are not a recipe",
                                "Coins are a raw currency. Open the recipe that costs coins and use "
                                "＋ Add Coins there.")
            return
        self._open_recipe_editor(output)

    def _open_recipe_editor(self, output):
        output = normalize_id(output)
        editor = ItemRowsEditor(
            self, output, RECIPES.get(output, []), self.available_item_ids(), self.display_name,
            lambda title, initial=None: self._pick_item(title, initial),
            icon_for=lambda i: self.icons.get(i), color_for=self.rarity_color, coins_button=True,
            delete_confirm=f"Remove the recipe for {self.display_name(output)}?\n\nThe item stays "
                           "available as a raw / leaf item.")
        if editor.result is None:
            return
        if editor.result == []:
            RECIPES.pop(output, None)
        else:
            RECIPES[output] = editor.result
        self._refresh_all()
        self._show_view("tree")
        for entry in self.plan_order:
            if entry["item"] == output:
                self._reveal_tree_path(entry["path"])
                break

    def _edit_manual_counts(self):
        rows = sorted(self.extra.items(), key=lambda kv: self.display_name(kv[0]).casefold())
        editor = ItemRowsEditor(
            self, "MANUAL", rows, self.available_item_ids(), self.display_name,
            lambda title, initial=None: self._pick_item(title, initial),
            icon_for=lambda i: self.icons.get(i), color_for=self.rarity_color,
            heading="Manual / island-chest counts", noun="COUNTED ITEMS", add_text="＋  Add Item",
            save_text="Save Counts", delete_text="Clear All", coins_button=True,
            intro=("Hypixel's public API does not expose the chests on your private island. Anything "
                   "you list here is added to your account stock, exactly like a synced container. "
                   "Use a negative number to reserve items you do not want spent. You can also add "
                   "Coins here to correct your purse / bank snapshot."),
            allow_negative=True, allow_empty=True,
            delete_confirm="Remove every manual / island-chest count?")
        if editor.result is None:
            return
        self.extra = {}
        for item, amount in editor.result:
            self.extra[item] = self.extra.get(item, 0) + amount
        self.extra = {k: v for k, v in self.extra.items() if v}
        self._refresh_all()

    def _set_root(self, item_id):
        if not item_id or item_id == COINS:
            return
        self.root_item = normalize_id(item_id)
        self._refresh_all()
        self._show_view("tree")

    def _choose_root(self):
        root = self._pick_item("Choose Goal", self.root_item)
        if root and root != COINS:
            self._set_root(root)

    def _reset_progress(self):
        if self.mode == "synced":
            if not messagebox.askyesno("Reset manual adjustments",
                                       "Clear every manual adjustment and manual / island-chest count?\n\n"
                                       "Synced inventory and coins are not affected."):
                return
            self.extra.clear()
        else:
            if not messagebox.askyesno("Reset progress", "Set every owned amount back to zero?"):
                return
            self.obtained.clear()
        self._refresh_all()

    # ---------------- export / tools ----------------
    def _export_missing(self):
        report = self._report("finish")
        available, outlay = self.stock.get(COINS, 0), report["total_outlay"]
        gap = max(0, outlay - available)
        mode = self.settings.get("price_mode", "insta")
        lines = ["SkyBlock crafting shopping list",
                 f"Goal: {self.display_name(self.root_item)}  [{self.root_item}]",
                 f"Generated: {time.strftime('%Y-%m-%d %H:%M')}",
                 f"Counting mode: {'Hypixel account sync' if self.mode == 'synced' else 'manual'}",
                 "Counting preset: " + hs.PRESET_LABELS.get(self.settings.get("count_preset", "custom"), "Custom"),
                 f"Price mode: {he.PRICE_MODE_LABELS.get(mode, mode)}"]
        if self.snapshot:
            lines.append(f"Account: {self.snapshot.get('name','?')} "
                         f"({self.snapshot.get('profile_name','?')})  •  synced "
                         f"{hs.format_age(time.time() - float(self.snapshot.get('fetched_at', 0)))}")
        age = self.market.age()
        lines.append("Bazaar prices: " + (hs.format_age(time.time() - age) if age != float("inf")
                                          else "not loaded"))
        lines += ["", "RAW ITEMS STILL NEEDED  (with coin cost)"]
        pending = [r for r in report["raw_rows"] if r["short"] > 0]
        for row in pending:
            item = row["item"]
            unit = "no price" if row["unit"] is None else f"{he.format_coins_full(row['unit'])} each"
            sub = "—" if row["unit"] is None else f"{he.format_coins_full(row['subtotal'])} coins"
            lines.append(f"  {row['short']:>12,}  {self.display_name(item)}  [{item}]   {unit}   =   {sub}")
        if not pending:
            lines.append("  nothing — every raw item is covered")
        lines += ["", "RAW COIN COSTS IN RECIPES"]
        for row in report["coin_rows"]:
            lines.append(f"  {he.format_coins_full(row['required']):>14}  coins {row['label']}"
                         f"   (already covered: {he.format_coins_full(row['covered'])})")
        if not report["coin_rows"]:
            lines.append("  none")
        lines += ["", "CRAFTED INTERMEDIATES STILL SHORT  (unit value from ingredients)"]
        crafted = [r for r in report["crafted_rows"] if r["needed"] > r["covered"]]
        for row in crafted:
            item = row["item"]
            prefix = "" if row["complete"] else ">= "
            lines.append(f"  {row['needed'] - row['covered']:>12,}  {self.display_name(item)}  [{item}]"
                         f"   unit {prefix}{he.format_coins_full(row['unit'])} coins")
        if not crafted:
            lines.append("  none")
        lines += ["", "TOTALS",
                  f"  Shopping cost      : {he.format_coins_full(report['shopping'])} coins",
                  f"  Recipe coin costs  : {he.format_coins_full(report['coin_total'])} coins",
                  f"  Total coins needed : {he.format_coins_full(outlay)} coins",
                  f"  Purse + bank       : {he.format_coins_full(available)} coins",
                  f"  Still short        : {he.format_coins_full(gap)} coins"]
        if report["unpriced"]:
            lines += ["", "NO PRICE AVAILABLE (total above is a LOWER BOUND):"]
            lines += [f"  {self.display_name(i)}  [{i}]" for i in report["unpriced"]]
            lines.append("Set a manual price for these in the Coin Cost tab.")
        lines += ["", "Prices are indicative and move constantly.", DISCLAIMER]
        text = "\n".join(lines)
        path = filedialog.asksaveasfilename(parent=self, title="Export shopping list",
                                            defaultextension=".txt", initialfile="missing_materials.txt",
                                            filetypes=[("Text file", "*.txt")])
        if path:
            try:
                Path(path).write_text(text, encoding="utf-8")
            except OSError as exc:
                messagebox.showerror("Could not save", str(exc))
                return
        self.clipboard_clear()
        self.clipboard_append(text)
        messagebox.showinfo("Export ready", "The shopping list (with coin costs) was copied to your "
                                            "clipboard" + (" and saved." if path else "."))

    def _open_data_folder(self):
        try:
            if sys.platform == "win32":
                os.startfile(str(DATA_DIR))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(DATA_DIR)])
            else:
                subprocess.Popen(["xdg-open", str(DATA_DIR)])
        except Exception as exc:
            messagebox.showinfo("Data folder", f"{DATA_DIR}\n\n{exc}")

    def _open_container_report(self):
        path = DATA_DIR / "inventory_report.txt"
        if not path.exists():
            messagebox.showinfo("Container report",
                                "Sync once first — the report is written on every sync.")
            return
        try:
            if sys.platform == "win32":
                os.startfile(str(path))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception as exc:
            messagebox.showinfo("Container report", f"{path}\n\n{exc}")


if __name__ == "__main__":
    prepare_windows_process()
    app = SkyblockTracker()
    app.mainloop()
