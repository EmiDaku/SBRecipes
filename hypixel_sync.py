"""
Hypixel SkyBlock account sync — NBT reader, item identity, API client, cache.

Standard library only. No tkinter.

Policy compliance (hypixel_api_policy.txt):
  * API key never in source or a built binary: read from HYPIXEL_API_KEY or a
    local key file beside the save data.
  * Player data fetched once per explicit user action, or once per launch when
    the cache is stale. No background polling, no session/stat history.
  * Hard minimum interval between authenticated pulls, persisted to disk so
    restarting cannot be used to poll faster. HTTP 429 honours RateLimit-Reset.
  * Responses reduced to item counts before caching.
  * The key is only ever sent to api.hypixel.net, never logged.

NOT AFFILIATED WITH OR ENDORSED BY HYPIXEL INC. OR MOJANG STUDIOS.
"""
from __future__ import annotations

import base64
import gzip
import json
import os
import re
import struct
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

APP_AGENT = "SkyBlock-Crafting-Tree-Tracker/3.1 (personal desktop crafting planner)"
API_ROOT = "https://api.hypixel.net"
PROFILES_URL = API_ROOT + "/v2/skyblock/profiles?uuid={uuid}"
MUSEUM_URL = API_ROOT + "/v2/skyblock/museum?profile={profile}"
MOJANG_URL = "https://api.mojang.com/users/profiles/minecraft/{name}"

MIN_INTERVAL_FLOOR = 300
INTERVAL_CHOICES = (300, 600, 900, 1800, 3600, 21600)
MAX_BLOB = 8 * 1024 * 1024
MAX_NBT = 24 * 1024 * 1024
UUID_RE = re.compile(r"^[0-9a-fA-F]{32}$")

COINS = "COINS"

# key, label, group, counted-by-default
SOURCE_DEFS = (
    ("inv_contents",                     "Inventory",         "Carried",     True),
    ("inv_armor",                        "Armour (worn)",     "Carried",     True),
    ("equipment_contents",               "Equipment (worn)",  "Carried",     True),
    ("quiver",                           "Quiver",            "Carried",     True),
    ("ender_chest_contents",             "Ender Chest",       "Storage",     True),
    ("backpack_contents",                "Backpacks",         "Storage",     True),
    ("personal_vault_contents",          "Personal Vault",    "Storage",     True),
    ("sacks",                            "Sacks",             "Storage",     True),
    ("wardrobe_contents",                "Wardrobe",          "Storage",     False),
    ("talisman_bag",                     "Accessory Bag",     "Bags",        False),
    ("fishing_bag",                      "Fishing Bag",       "Bags",        True),
    ("potion_bag",                       "Potion Bag",        "Bags",        False),
    ("candy_inventory_contents",         "Candy Bag",         "Bags",        False),
    ("carnival_mask_inventory_contents", "Carnival Masks",    "Bags",        False),
    ("museum",                           "Museum (donated)",  "Locked away", False),
    ("purse",                            "Purse",             "Coins",       True),
    ("bank",                             "Bank (co-op)",      "Coins",       True),
    ("personal_bank",                    "Personal Bank",     "Coins",       True),
)
SOURCE_LABELS = {k: l for k, l, _g, _d in SOURCE_DEFS}
SOURCE_GROUPS = {k: g for k, _l, g, _d in SOURCE_DEFS}
DEFAULT_SOURCES = {k: d for k, _l, _g, d in SOURCE_DEFS}
MANUAL_SOURCE = "manual"
COIN_SOURCES = ("purse", "bank", "personal_bank")
NBT_KEYS = tuple(k for k in SOURCE_LABELS if k not in COIN_SOURCES)


def _preset(**overrides):
    out = dict(DEFAULT_SOURCES)
    out.update(overrides)
    for key in COIN_SOURCES:
        out[key] = True
    return out


COUNT_PRESETS = {
    "crafting": ("Crafting-safe (default)", _preset()),
    "storage": ("Storage + inventory", _preset(
        inv_armor=False, equipment_contents=False, quiver=False, fishing_bag=False)),
    "carried": ("Carried inventory only", _preset(
        ender_chest_contents=False, backpack_contents=False, personal_vault_contents=False,
        sacks=False, inv_armor=False, equipment_contents=False, quiver=False, fishing_bag=False)),
    "everything": ("Everything I own (incl. museum)", _preset(
        wardrobe_contents=True, talisman_bag=True, potion_bag=True,
        candy_inventory_contents=True, carnival_mask_inventory_contents=True, museum=True)),
    "custom": ("Custom", None),
}
PRESET_LABELS = {k: v[0] for k, v in COUNT_PRESETS.items()}

ISLAND_CHEST_NOTE = ("Island chests are not exposed by the Hypixel public API. "
                     "Use 'Manual / island chests' counts for those.")
COIN_NOTE = ("Purse and bank are a snapshot from the moment of the sync; they do not update "
             "while the app is open. Use a manual coin adjustment to correct them.")


def basic_normalize(item_id) -> str:
    return str(item_id).strip().upper().replace(" ", "_")


def friendly_source(key: str) -> str:
    if key in SOURCE_LABELS:
        return SOURCE_LABELS[key]
    if key == MANUAL_SOURCE:
        return "Manual / island chests"
    return key.replace("_contents", "").replace("_", " ").strip().title()


# ---------------------------------------------------------------- NBT reader
class NBTError(Exception):
    pass


class _Reader:
    __slots__ = ("data", "pos")

    def __init__(self, data):
        self.data, self.pos = data, 0

    def read(self, n):
        if n < 0 or self.pos + n > len(self.data):
            raise NBTError("truncated at %d, wanted %d of %d" % (self.pos, n, len(self.data)))
        chunk = self.data[self.pos:self.pos + n]
        self.pos += n
        return chunk

    def num(self, fmt):
        return struct.unpack(fmt, self.read(struct.calcsize(fmt)))[0]

    def string(self):
        return self.read(self.num(">H")).decode("utf-8", "replace")


def _payload(r, tag, depth):
    if depth > 64:
        raise NBTError("too deep")
    if tag == 1: return r.num(">b")
    if tag == 2: return r.num(">h")
    if tag == 3: return r.num(">i")
    if tag == 4: return r.num(">q")
    if tag == 5: return r.num(">f")
    if tag == 6: return r.num(">d")
    if tag == 7:
        n = r.num(">i")
        if n < 0 or n > MAX_NBT: raise NBTError("byte array %d" % n)
        return r.read(n)
    if tag == 8: return r.string()
    if tag == 9:
        inner = r.num(">B")
        n = r.num(">i")
        if n < 0 or n > 500000: raise NBTError("list %d" % n)
        return [_payload(r, inner, depth + 1) for _ in range(n)]
    if tag == 10:
        out = {}
        for _ in range(8192):
            inner = r.num(">B")
            if inner == 0:
                return out
            name = r.string()                       # name BEFORE payload — do not inline
            out[name] = _payload(r, inner, depth + 1)
        raise NBTError("compound too large")
    if tag in (11, 12):
        n = r.num(">i")
        width = 4 if tag == 11 else 8
        if n < 0 or n * width > MAX_NBT: raise NBTError("array %d" % n)
        if not n: return []
        return list(struct.unpack(">%d%s" % (n, "i" if tag == 11 else "q"), r.read(n * width)))
    raise NBTError("unknown tag %d at byte %d" % (tag, r.pos - 1))


def read_nbt(data):
    """Parse NBT; validate by requiring the parse to consume the buffer."""
    last, fallback = None, None
    for mode in ("standard", "noname", "bare"):
        try:
            r = _Reader(data)
            if mode == "standard":
                tag = r.num(">B"); r.string()
            elif mode == "noname":
                tag = r.num(">B")
            else:
                tag = 10
            value = _payload(r, tag, 0)
        except Exception as exc:
            last = exc
            continue
        if not isinstance(value, dict):
            continue
        if r.pos == len(data):
            return value
        if "i" in value and fallback is None:
            fallback = value
    if fallback is not None:
        return fallback
    raise NBTError("no NBT layout parsed cleanly (%s)" % last)


def _looks_like_nbt(value):
    return isinstance(value, str) and 24 < len(value) <= MAX_BLOB and value.startswith("H4sI")


def decode_inventory(blob, errors=None):
    """base64 -> gzip -> NBT -> list of item stacks."""
    def fail(reason):
        if errors is not None:
            errors.append(reason)
        return []

    try:
        raw = base64.b64decode(blob)
    except Exception as exc:
        return fail("base64: %s" % exc)
    if not raw:
        return fail("empty payload")
    if raw[:2] == b"\x1f\x8b":
        try:
            raw = gzip.decompress(raw)
        except Exception as exc:
            return fail("gzip: %s" % exc)
    if not raw:
        return fail("empty after inflate")
    if len(raw) > MAX_NBT:
        return fail("payload too large (%d bytes)" % len(raw))
    try:
        root = read_nbt(raw)
    except Exception as exc:
        return fail("nbt: %s: %s" % (type(exc).__name__, exc))
    if not isinstance(root, dict):
        return fail("root is %s, not a compound" % type(root).__name__)
    stacks = root.get("i")
    if not isinstance(stacks, list):
        for value in root.values():
            if isinstance(value, list) and value and isinstance(value[0], dict):
                stacks = value
                break
    if not isinstance(stacks, list):
        return fail("no item list; root keys = %s" % sorted(root)[:12])
    return [s for s in stacks if isinstance(s, dict)]


def _blobs(value, depth=0):
    if depth > 5:
        return
    if _looks_like_nbt(value):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _blobs(v, depth + 1)
    elif isinstance(value, list):
        for v in value:
            yield from _blobs(v, depth + 1)


# ------------------------------------------------------------- item identity
# Best effort for vanilla stacks carrying no SkyBlock ExtraAttributes.
VANILLA_ITEMS = {
    1: "STONE", 4: "COBBLESTONE", 12: "SAND", 13: "GRAVEL", 17: "LOG", 19: "SPONGE",
    20: "GLASS", 22: "LAPIS_BLOCK", 24: "SANDSTONE", 35: "WOOL", 41: "GOLD_BLOCK",
    42: "IRON_BLOCK", 45: "BRICK", 46: "TNT", 47: "BOOKSHELF", 48: "MOSSY_COBBLESTONE",
    49: "OBSIDIAN", 54: "CHEST", 57: "DIAMOND_BLOCK", 79: "ICE", 80: "SNOW_BLOCK",
    81: "CACTUS", 82: "CLAY", 86: "PUMPKIN", 87: "NETHERRACK", 88: "SOUL_SAND",
    89: "GLOWSTONE", 98: "SMOOTH_BRICK", 103: "MELON_BLOCK", 110: "MYCEL",
    112: "NETHER_BRICK", 121: "ENDER_STONE", 133: "EMERALD_BLOCK", 152: "REDSTONE_BLOCK",
    155: "QUARTZ_BLOCK", 168: "PRISMARINE", 169: "SEA_LANTERN", 170: "HAY_BLOCK",
    172: "HARD_CLAY", 173: "COAL_BLOCK", 174: "PACKED_ICE", 179: "RED_SANDSTONE",
    260: "APPLE", 261: "BOW", 262: "ARROW", 263: "COAL", 264: "DIAMOND",
    265: "IRON_INGOT", 266: "GOLD_INGOT", 280: "STICK", 287: "STRING", 288: "FEATHER",
    289: "SULPHUR", 295: "SEEDS", 296: "WHEAT", 297: "BREAD", 318: "FLINT",
    322: "GOLDEN_APPLE", 331: "REDSTONE", 332: "SNOW_BALL", 334: "LEATHER",
    336: "CLAY_BRICK", 337: "CLAY_BALL", 338: "SUGAR_CANE", 339: "PAPER", 340: "BOOK",
    341: "SLIME_BALL", 344: "EGG", 345: "COMPASS", 348: "GLOWSTONE_DUST",
    349: "RAW_FISH", 350: "COOKED_FISH", 351: "INK_SACK", 352: "BONE", 353: "SUGAR",
    357: "COOKIE", 360: "MELON", 361: "PUMPKIN_SEEDS", 362: "MELON_SEEDS",
    363: "RAW_BEEF", 364: "COOKED_BEEF", 365: "RAW_CHICKEN", 366: "COOKED_CHICKEN",
    367: "ROTTEN_FLESH", 368: "ENDER_PEARL", 369: "BLAZE_ROD", 370: "GHAST_TEAR",
    371: "GOLD_NUGGET", 372: "NETHER_STALK", 374: "GLASS_BOTTLE", 375: "SPIDER_EYE",
    376: "FERMENTED_SPIDER_EYE", 377: "BLAZE_POWDER", 378: "MAGMA_CREAM",
    381: "EYE_OF_ENDER", 382: "SPECKLED_MELON", 384: "EXP_BOTTLE", 385: "FIREBALL",
    386: "BOOK_AND_QUILL", 388: "EMERALD", 389: "ITEM_FRAME", 391: "CARROT_ITEM",
    392: "POTATO_ITEM", 393: "BAKED_POTATO", 396: "GOLDEN_CARROT", 397: "SKULL_ITEM",
    399: "NETHER_STAR", 400: "PUMPKIN_PIE", 403: "ENCHANTED_BOOK",
    405: "NETHER_BRICK_ITEM", 406: "QUARTZ", 409: "PRISMARINE_SHARD",
    410: "PRISMARINE_CRYSTALS", 411: "RABBIT", 412: "COOKED_RABBIT",
    414: "RABBIT_FOOT", 415: "RABBIT_HIDE", 421: "NAME_TAG", 423: "MUTTON",
    424: "COOKED_MUTTON", 425: "BANNER",
}
VANILLA_DAMAGE = {
    (263, 1): "CHARCOAL", (17, 1): "SPRUCE_LOG", (17, 2): "BIRCH_LOG", (17, 3): "JUNGLE_LOG",
    (351, 2): "CACTUS_GREEN", (351, 3): "COCOA_BEANS", (351, 4): "LAPIS_LAZULI",
    (351, 15): "BONE_MEAL", (349, 1): "SALMON", (349, 2): "CLOWNFISH", (349, 3): "PUFFERFISH",
}
VANILLA_NAMES = {
    "GUNPOWDER": "SULPHUR", "NETHER_WART": "NETHER_STALK", "COD": "RAW_FISH",
    "WHEAT_SEEDS": "SEEDS", "SNOWBALL": "SNOW_BALL", "ENDER_EYE": "EYE_OF_ENDER",
    "EXPERIENCE_BOTTLE": "EXP_BOTTLE", "FIRE_CHARGE": "FIREBALL",
    "WRITABLE_BOOK": "BOOK_AND_QUILL", "MELON_SLICE": "MELON",
    "CARROT": "CARROT_ITEM", "POTATO": "POTATO_ITEM",
}


def stack_identity(stack, normalize=basic_normalize):
    """(skyblock_item_id, count) for one NBT item stack."""
    count = stack.get("Count", stack.get("count", 1))
    if not isinstance(count, int):
        count = 1
    if count < 0:
        count += 256
    if count <= 0:
        return None, 0
    tag = stack.get("tag")
    tag = tag if isinstance(tag, dict) else {}
    extra = tag.get("ExtraAttributes")
    extra = extra if isinstance(extra, dict) else {}
    sid = extra.get("id")
    if isinstance(sid, str) and sid.strip():
        sid = sid.strip().upper()
        if sid == "PET":
            info = extra.get("petInfo")
            if isinstance(info, str):
                try:
                    info = json.loads(info)
                except ValueError:
                    info = None
            if isinstance(info, dict) and info.get("type"):
                sid = "PET_" + str(info["type"]).upper()
        elif sid == "RUNE":
            runes = extra.get("runes")
            if isinstance(runes, dict) and runes:
                sid = "RUNE_" + sorted(runes)[0].upper()
        return normalize(sid), count
    raw_id, damage = stack.get("id"), stack.get("Damage", 0)
    damage = damage if isinstance(damage, int) else 0
    if isinstance(raw_id, str):
        base = raw_id.split(":")[-1].upper()
        return normalize(VANILLA_NAMES.get(base, base)), count
    if isinstance(raw_id, int) and raw_id > 0:
        sid = VANILLA_DAMAGE.get((raw_id, damage)) or VANILLA_ITEMS.get(raw_id)
        if sid:
            return normalize(sid), count
    return None, 0


# ------------------------------------------------------------------ harvesting
# Containers holding the container ITEMS themselves, not their contents.
SKIP_CONTAINER_KEYS = {"backpack_icons", "sacks_counts", "trophy_fish"}


def _container_source(path):
    """Map a JSON path inside `inventory` to a source key."""
    if not path:
        return "inv_contents"
    if path[0] == "bag_contents" and len(path) > 1:
        return path[1]
    return path[0]


def harvest_inventory(inv, normalize=basic_normalize, report=None):
    """
    {source_key: {item_id: count}} from a profile member's inventory object.

    Walks the ENTIRE inventory object recursively, so Ender Chest, every
    backpack, personal vault, wardrobe, all bags, and any container Hypixel
    adds later are found regardless of how deeply they are nested.
    """
    out, stats = {}, {}
    if not isinstance(inv, dict):
        return out

    def add(source, blob):
        stat = stats.setdefault(source, {"blobs": 0, "stacks": 0, "items": 0, "errors": []})
        stat["blobs"] += 1
        errors = []
        stacks = decode_inventory(blob, errors)
        stat["errors"].extend(errors)
        counts = out.setdefault(source, {})
        for stack in stacks:
            sid, count = stack_identity(stack, normalize)
            if sid and count > 0:
                counts[sid] = counts.get(sid, 0) + count
                stat["stacks"] += 1
                stat["items"] += count
        if not counts:
            out.pop(source, None)

    def walk(node, path, depth=0):
        if depth > 6:
            return
        if _looks_like_nbt(node):
            add(_container_source(path), node)
        elif isinstance(node, dict):
            for key, value in node.items():
                if str(key) not in SKIP_CONTAINER_KEYS:
                    walk(value, path + (str(key),), depth + 1)
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, path + (str(index),), depth + 1)

    walk(inv, ())

    sacks = inv.get("sacks_counts")
    if isinstance(sacks, dict):
        counts = {}
        for item, amount in sacks.items():
            if isinstance(amount, (int, float)) and amount > 0:
                sid = normalize(item)
                counts[sid] = counts.get(sid, 0) + int(amount)
        if counts:
            out["sacks"] = counts
            stats["sacks"] = {"blobs": 0, "stacks": len(counts), "items": sum(counts.values())}

    if report is not None:
        report["fields"] = {k: type(v).__name__ for k, v in inv.items()}
        report["containers"] = stats
        report["missing"] = [k for k in ("ender_chest_contents", "backpack_contents",
                                         "personal_vault_contents") if k not in out]
    return out


def harvest_museum(member, normalize=basic_normalize):
    """Donated museum items. Entries currently borrowed back are skipped."""
    counts = {}
    if not isinstance(member, dict):
        return counts

    def tally(entry, fallback_id=None):
        found = 0
        for blob in _blobs(entry):
            for stack in decode_inventory(blob):
                sid, count = stack_identity(stack, normalize)
                if sid and count > 0:
                    counts[sid] = counts.get(sid, 0) + count
                    found += count
        if not found and fallback_id:
            sid = normalize(fallback_id)
            counts[sid] = counts.get(sid, 0) + 1

    items = member.get("items")
    if isinstance(items, dict):
        for item_id, entry in items.items():
            if isinstance(entry, dict) and entry.get("borrowing"):
                continue
            tally(entry, item_id)
    special = member.get("special")
    if isinstance(special, list):
        for entry in special:
            if isinstance(entry, dict) and entry.get("borrowing"):
                continue
            tally(entry)
    return counts


# ---------------------------------------------------------------------- errors
class SyncError(Exception):
    def __init__(self, message, hint=""):
        super().__init__(message)
        self.hint = hint


class CooldownError(SyncError):
    def __init__(self, remaining):
        self.remaining = max(0.0, float(remaining))
        super().__init__(
            "Please wait %s before syncing again." % format_seconds(self.remaining),
            "Hypixel's developer policy asks for caching and respectful intervals. "
            "The cooldown is stored on disk so restarting cannot bypass it.")


def format_seconds(seconds) -> str:
    seconds = int(max(0, seconds))
    if seconds < 60: return "%ds" % seconds
    if seconds < 3600: return "%dm %02ds" % (seconds // 60, seconds % 60)
    return "%dh %02dm" % (seconds // 3600, (seconds % 3600) // 60)


def format_age(seconds) -> str:
    seconds = int(max(0, seconds))
    if seconds < 45: return "just now"
    if seconds < 3600: return "%dm ago" % (seconds // 60)
    if seconds < 86400: return "%dh ago" % (seconds // 3600)
    return "%dd ago" % (seconds // 86400)


# ------------------------------------------------------------------------ HTTP
def _get_json(url, api_key=None, timeout=25):
    headers = {"User-Agent": APP_AGENT, "Accept": "application/json"}
    if api_key:
        headers["API-Key"] = api_key
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        cause = ""
        try:
            cause = str(json.loads(exc.read().decode("utf-8")).get("cause", ""))
        except Exception:
            pass
        if exc.code == 403:
            raise SyncError("Hypixel rejected the API key.",
                            "Generate a key at developer.hypixel.net and paste it into Settings. "
                            "Developer keys expire.") from None
        if exc.code == 429:
            try:
                wait = float(exc.headers.get("RateLimit-Reset"))
            except (TypeError, ValueError, AttributeError):
                wait = 60.0
            raise CooldownError(wait) from None
        if exc.code == 404:
            raise SyncError("Hypixel returned 404 for that request.", cause) from None
        raise SyncError("Hypixel returned HTTP %d." % exc.code, cause) from None
    except urllib.error.URLError as exc:
        raise SyncError("Could not reach the network.",
                        str(getattr(exc, "reason", exc))) from None
    except (ValueError, UnicodeDecodeError):
        raise SyncError("Hypixel sent a response that could not be read.") from None
    if not isinstance(payload, dict):
        raise SyncError("Unexpected response from Hypixel.")
    if payload.get("success") is False:
        raise SyncError("Hypixel reported a failure.", str(payload.get("cause", "")))
    return payload


def _load_json(path, default):
    try:
        with Path(path).open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return default


def _save_json(path, data):
    path = Path(path)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
    tmp.replace(path)


def _dash(uuid):
    u = uuid.replace("-", "")
    if len(u) != 32:
        return uuid
    return "%s-%s-%s-%s-%s" % (u[:8], u[8:12], u[12:16], u[16:20], u[20:])


# ---------------------------------------------------------------------- client
class HypixelSync:
    def __init__(self, data_dir, key_file, cache_file, state_file, normalize=basic_normalize):
        self.dir = Path(data_dir)
        self.key_file = Path(key_file)
        self.cache_file = Path(cache_file)
        self.state_file = Path(state_file)
        self.normalize = normalize
        self.dir.mkdir(parents=True, exist_ok=True)
        self.state = _load_json(self.state_file, {})
        if not isinstance(self.state, dict):
            self.state = {}
        self.state.setdefault("uuid_cache", {})

    # ---- key ----
    def api_key(self):
        env = os.environ.get("HYPIXEL_API_KEY", "").strip()
        if env:
            return env, "environment"
        try:
            stored = self.key_file.read_text(encoding="utf-8").strip()
        except OSError:
            stored = ""
        return (stored, "file") if stored else ("", "")

    def save_api_key(self, key):
        key = (key or "").strip()
        if not key:
            self.clear_api_key()
            return
        self.key_file.write_text(key + "\n", encoding="utf-8")
        try:
            os.chmod(self.key_file, 0o600)
        except OSError:
            pass

    def clear_api_key(self):
        try:
            self.key_file.unlink()
        except OSError:
            pass

    @staticmethod
    def mask(key):
        if not key:
            return ""
        return key[:4] + "•" * 12 + key[-4:] if len(key) > 12 else "•" * len(key)

    # ---- cache / state ----
    def load_cache(self):
        cache = _load_json(self.cache_file, None)
        if isinstance(cache, dict) and isinstance(cache.get("counts"), dict):
            return cache
        return None

    def clear_cache(self):
        try:
            self.cache_file.unlink()
        except OSError:
            pass

    def _save_state(self):
        try:
            _save_json(self.state_file, self.state)
        except OSError:
            pass

    # ---- cooldown ----
    def interval(self, requested=None):
        try:
            requested = int(requested)
        except (TypeError, ValueError):
            requested = MIN_INTERVAL_FLOOR
        return max(MIN_INTERVAL_FLOOR, requested)

    def cooldown_remaining(self, requested=None):
        now = time.time()
        retry = float(self.state.get("retry_after", 0) or 0)
        if retry > now:
            return retry - now
        last = float(self.state.get("last_request", 0) or 0)
        if not last:
            return 0.0
        return max(0.0, self.interval(requested) - (now - last))

    def _guard(self, requested):
        remaining = self.cooldown_remaining(requested)
        if remaining > 0:
            raise CooldownError(remaining)

    # ---- name -> uuid ----
    def resolve_player(self, text, progress=None):
        text = (text or "").strip()
        if not text:
            raise SyncError("No Minecraft account is linked.",
                            "Open Settings and enter your username or UUID.")
        compact = text.replace("-", "")
        if UUID_RE.match(compact):
            cached = self.state["uuid_cache"].get(compact.lower(), {})
            return compact.lower(), cached.get("name") or text
        cached = self.state["uuid_cache"].get(text.casefold())
        if cached and cached.get("uuid"):
            return cached["uuid"], cached.get("name", text)
        if progress:
            progress("Looking up Minecraft name…")
        try:
            payload = _get_json(MOJANG_URL.format(name=urllib.parse.quote(text)),
                                api_key=None, timeout=20)
        except SyncError as exc:
            raise SyncError("Could not look up that Minecraft name.",
                            str(exc) + " — you can paste your UUID instead.") from None
        uuid = str(payload.get("id", "")).replace("-", "").lower()
        name = str(payload.get("name", text))
        if not UUID_RE.match(uuid):
            raise SyncError("That Minecraft name was not found.",
                            "Check the spelling, or paste your UUID.")
        self.state["uuid_cache"][text.casefold()] = {"uuid": uuid, "name": name}
        self.state["uuid_cache"][uuid] = {"uuid": uuid, "name": name}
        self._save_state()
        return uuid, name

    # ---- the pull ----
    def _write_container_report(self, player, report, counts):
        try:
            lines = ["Hypixel SkyBlock container report",
                     "Generated: " + time.strftime("%Y-%m-%d %H:%M:%S"),
                     "Player: " + str(player), "",
                     "Fields present in profile.inventory:"]
            ident = report.get("identity") or {}
            if ident:
                lines += ["UUID: " + str(ident.get("uuid")),
                          "Member read: " + str(ident.get("member_read")),
                          "Profile: %s (%s)" % (ident.get("profile"), ident.get("profile_id")),
                          "Profile chosen by: " + str(ident.get("profile_choice")),
                          "Co-op members: " + ", ".join(ident.get("coop_members") or []),
                          "MATCH: " + ("YES - this is you" if str(ident.get("member_read"))
                                       .replace("-", "").lower() == str(ident.get("uuid"))
                                       else "NO - WRONG MEMBER")]
            for key, kind in sorted((report.get("fields") or {}).items()):
                lines.append(f"  {key:38s} {kind}")
            lines += ["", "Containers decoded (blobs = NBT payloads found):"]
            stats = report.get("containers") or {}
            if not stats:
                lines.append("  NONE — the API returned no readable inventory payloads.")
            for key, stat in sorted(stats.items()):
                lines.append(f"  {friendly_source(key):28s} blobs={stat['blobs']:<4d} "
                             f"stacks={stat['stacks']:<6d} items={stat['items']:,}")
                for reason in stat.get("errors", [])[:3]:
                    lines.append(f"      ERROR: {reason}")
            lines += ["", "Totals per source:"]
            for key, values in sorted(counts.items()):
                lines.append(f"  {friendly_source(key):28s} {len(values):>6,d} types  "
                             f"{sum(values.values()):>12,d} items")
            missing = report.get("missing") or []
            lines += ["", "Expected containers NOT found: " + (", ".join(missing) if missing else "none"),
                      "", "If Ender Chest / Backpacks are listed as missing, the API did not send",
                      "them. That is almost always the player's SkyBlock API visibility setting,",
                      "not this app. Island chests are never sent by the public API — use",
                      "Manual Counts for those."]
            (self.dir / "inventory_report.txt").write_text("\n".join(lines), encoding="utf-8")
        except OSError:
            pass
    
    def sync(self, *, username="", uuid="", profile_id="", include_museum=True,
             min_interval=MIN_INTERVAL_FLOOR, progress=None):
        key, _origin = self.api_key()
        if not key:
            raise SyncError("No Hypixel API key configured.",
                            "Open Settings and paste a key from developer.hypixel.net. Never "
                            "share that key or ship it inside a build.")
        self._guard(min_interval)
        uuid, name = self.resolve_player(uuid or username, progress=progress)
        self.state["last_request"] = time.time()   # stamped BEFORE the request
        self._save_state()

        if progress:
            progress("Downloading SkyBlock profiles…")
        uuid = str(uuid).replace("-", "").lower()
        payload = _get_json(PROFILES_URL.format(uuid=uuid), api_key=key)
        profiles = payload.get("profiles") or []
        if not isinstance(profiles, list) or not profiles:
            raise SyncError("No SkyBlock profiles were returned for that account.")

        chosen, choice_reason = None, ""
        if profile_id:
            chosen = next((p for p in profiles if p.get("profile_id") == profile_id), None)
            if chosen is not None:
                choice_reason = "saved profile_id"
        if chosen is None:
            chosen = next((p for p in profiles if p.get("selected")), None)
            if chosen is not None:
                choice_reason = "Hypixel 'selected' flag"
        if chosen is None:
            chosen = max(profiles, key=lambda p: p.get("last_save", 0) or 0)
            choice_reason = "most recently saved (no 'selected' flag) - VERIFY"

        pid = str(chosen.get("profile_id", ""))
        members = chosen.get("members") or {}
        member, matched_key = None, None
        for key_uuid, value in members.items():
            if str(key_uuid).replace("-", "").lower() == uuid:
                member, matched_key = value, str(key_uuid)
                break
        if not isinstance(member, dict):
            raise SyncError(
                "Your account is not a member of the chosen profile.",
                "Profile '%s' has %d member(s): %s. Your UUID is %s. "
                "Pick the correct profile in Settings."
                % (chosen.get("cute_name"), len(members),
                   ", ".join(list(members)[:6]), uuid))

        if progress:
            progress("Reading inventories, bags and storage…")
        inv = dict(member.get("inventory") or {})
        bag_storage = member.get("accessory_bag_storage")
        if isinstance(bag_storage, dict) and "talisman_bag" not in inv:
            for sub in ("talisman_bag", "contents", "data", "bag_contents"):
                if sub in bag_storage:
                    inv["talisman_bag"] = bag_storage[sub]
                    break
        container_report = {}
        container_report["identity"] = {
            "player": name, "uuid": uuid, "member_read": matched_key,
            "profile": chosen.get("cute_name"), "profile_id": pid,
            "profile_choice": choice_reason,
            "coop_members": [str(k) for k in members],
        }
        counts = harvest_inventory(inv, self.normalize, report=container_report)
        if "sacks" not in counts and isinstance(member.get("sacks_counts"), dict):
            counts.update(harvest_inventory({"sacks_counts": member["sacks_counts"]}, self.normalize))

        warnings = [ISLAND_CHEST_NOTE]
        if not counts:
            warnings.append("No item containers were readable in this profile. Inventory API "
                            "visibility is probably disabled for the account — see "
                            "inventory_report.txt in the data folder.")
        for key, label in (("ender_chest_contents", "Ender Chest"),
                           ("backpack_contents", "Backpacks"),
                           ("personal_vault_contents", "Personal Vault")):
            if key not in counts:
                warnings.append(
                    f"{label} was not returned for this profile. Either it is empty, or "
                    f"SkyBlock's per-player API visibility for inventory is switched off "
                    f"(try /api in game), then sync again.")
        self._write_container_report(name, container_report, counts)

        if include_museum and pid:
            if progress:
                progress("Downloading museum…")
            try:
                museum = _get_json(MUSEUM_URL.format(profile=urllib.parse.quote(pid)), api_key=key)
                m_members = museum.get("members") or {}
                entry = m_members.get(uuid) or m_members.get(_dash(uuid)) or {}
                donated = harvest_museum(entry, self.normalize)
                if donated:
                    counts["museum"] = donated
            except CooldownError:
                raise
            except SyncError as exc:
                warnings.append("Museum could not be read: %s" % exc)

        # ---- coins: same response, no extra request ----
        currencies = member.get("currencies") or {}
        purse = currencies.get("coin_purse", member.get("coin_purse", 0)) or 0
        bank = (chosen.get("banking") or {}).get("balance", 0) or 0
        personal = currencies.get("personal_bank_balance", 0) or 0
        if not personal:
            personal = (member.get("personal_banking") or {}).get("balance", 0) or 0
        try:
            purse, bank, personal = float(purse), float(bank), float(personal)
        except (TypeError, ValueError):
            purse = bank = personal = 0.0
        for source, amount in (("purse", purse), ("bank", bank), ("personal_bank", personal)):
            if amount > 0:
                counts[source] = {COINS: int(amount)}
        if not (purse or bank or personal):
            warnings.append("No purse or bank balance was returned. The profile may hide banking, "
                            "or the account has no coins. You can set a coin total manually.")
        warnings.append(COIN_NOTE)

        snapshot = {
            "fetched_at": time.time(), "uuid": uuid, "name": name,
            "profile_id": pid,
            "profile_name": str(chosen.get("cute_name", "")) or pid[:8],
            "counts": counts, "purse": purse, "bank": bank, "personal_bank": personal, "container_report": container_report,
            "profiles": [{"id": str(p.get("profile_id", "")),
                          "name": str(p.get("cute_name", "")) or str(p.get("profile_id", ""))[:8],
                          "selected": bool(p.get("selected"))}
                         for p in profiles if p.get("profile_id")],
            "warnings": warnings,
            "member_uuid": matched_key,
        }
        try:
            _save_json(self.cache_file, snapshot)
        except OSError:
            warnings.append("Could not write the disk cache; counts are in memory only.")
        self.state.update(last_sync=snapshot["fetched_at"], uuid=uuid,
                          player_name=name, profile_id=pid, retry_after=0)
        self._save_state()
        return snapshot
    