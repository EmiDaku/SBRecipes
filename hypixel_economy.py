"""
Coins, bazaar pricing and cost maths for the SkyBlock Crafting Tracker.

Standard library only, no tkinter.

Policy notes: /v2/skyblock/bazaar needs NO API key and none is sent to it.
Prices are cached on disk and refreshed only on an explicit click, or once per
launch when stale. A persisted minimum interval (floor 5 min, default 15) is
enforced. The auction endpoint is deliberately NOT used: it is paged and heavy.
Items bazaar does not cover get a manual price or a manual bazaar-product link.

NOT AFFILIATED WITH OR ENDORSED BY HYPIXEL INC. OR MOJANG STUDIOS.
"""
from __future__ import annotations

import time
from pathlib import Path

import hypixel_sync as hs

COINS = hs.COINS

PRICE_MODES = (("insta", "Insta-buy  (fastest)"), ("order", "Buy order  (cheaper, slower)"))
PRICE_MODE_LABELS = {k: v for k, v in PRICE_MODES}
SCOPES = (("finish", "Cost to finish  (missing only)"), ("full", "Full value from scratch"))
SCOPE_LABELS = {k: v for k, v in SCOPES}

# Only genuine mismatches between a SkyBlock item id and a bazaar product id.
BAZAAR_ALIASES = {
    "MITHRIL": "MITHRIL_ORE", "TITANIUM": "TITANIUM_ORE",
    "IRON": "IRON_INGOT", "GOLD": "GOLD_INGOT",
    "LAPIS_LAZULI": "INK_SACK:4", "COCOA_BEANS": "INK_SACK:3", "BONE_MEAL": "INK_SACK:15",
    "NETHER_WART": "NETHER_STALK", "GUNPOWDER": "SULPHUR",
    "SLIMEBALL": "SLIME_BALL", "SNOWBALL": "SNOW_BALL",
    "SALMON": "RAW_FISH:1", "CLOWNFISH": "RAW_FISH:2", "PUFFERFISH": "RAW_FISH:3",
    "CARROT": "CARROT_ITEM", "POTATO": "POTATO_ITEM", "MELON_SLICE": "MELON",
    "TREASUREITE": "TREASURITE",
}


# ------------------------------------------------------------------ formatting
def format_coins(value) -> str:
    """Compact: 1.25M, 3.4B. Used in table cells and stat cards."""
    try:
        n = float(value)
    except (TypeError, ValueError):
        return "0"
    sign = "-" if n < 0 else ""
    n = abs(n)
    for cutoff, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "k")):
        if n >= cutoff:
            text = f"{n / cutoff:.2f}".rstrip("0").rstrip(".")
            return f"{sign}{text}{suffix}"
    return f"{sign}{int(round(n)):,}"


def format_coins_full(value) -> str:
    try:
        return f"{int(round(float(value))):,}"
    except (TypeError, ValueError):
        return "0"


def qty(item_id, amount) -> str:
    if str(item_id).upper() == COINS:
        return format_coins(amount)
    try:
        return f"{int(amount):,}"
    except (TypeError, ValueError):
        return "0"


def parse_price(text):
    """Accept 1250000, 1,250,000, 1.25m, 3.4b, 12k. Returns float or None."""
    raw = str(text).strip().lower().replace(",", "").replace(" ", "").replace("_", "")
    if not raw:
        return None
    multiplier = 1.0
    if raw[-1] in "kmbt":
        multiplier = {"k": 1e3, "m": 1e6, "b": 1e9, "t": 1e12}[raw[-1]]
        raw = raw[:-1]
    try:
        value = float(raw) * multiplier
    except ValueError:
        return None
    if value < 0 or value != value or value in (float("inf"), float("-inf")):
        return None
    return value


# --------------------------------------------------------------- bazaar client
class BazaarMarket:
    URL = "https://api.hypixel.net/v2/skyblock/bazaar"
    MIN_INTERVAL = 300
    DEFAULT_INTERVAL = 900
    INTERVALS = (300, 600, 900, 1800, 3600)

    def __init__(self, cache_file, state_file):
        self.cache_file, self.state_file = Path(cache_file), Path(state_file)
        self.prices, self.fetched_at = {}, 0.0
        self.state = hs._load_json(self.state_file, {})
        if not isinstance(self.state, dict):
            self.state = {}
        cache = hs._load_json(self.cache_file, None)
        if isinstance(cache, dict) and isinstance(cache.get("prices"), dict):
            self.prices = cache["prices"]
            self.fetched_at = float(cache.get("fetched_at", 0) or 0)

    def interval(self, requested=None):
        try:
            requested = int(requested)
        except (TypeError, ValueError):
            requested = self.DEFAULT_INTERVAL
        return max(self.MIN_INTERVAL, requested)

    def age(self):
        return (time.time() - self.fetched_at) if self.fetched_at else float("inf")

    def cooldown_remaining(self, requested=None):
        now = time.time()
        retry = float(self.state.get("retry_after", 0) or 0)
        if retry > now:
            return retry - now
        last = float(self.state.get("last_request", 0) or 0)
        return max(0.0, self.interval(requested) - (now - last)) if last else 0.0

    def _save_state(self):
        try:
            hs._save_json(self.state_file, self.state)
        except OSError:
            pass

    def clear_cache(self):
        self.prices, self.fetched_at = {}, 0.0
        try:
            self.cache_file.unlink()
        except OSError:
            pass

    def refresh(self, min_interval=DEFAULT_INTERVAL):
        remaining = self.cooldown_remaining(min_interval)
        if remaining > 0:
            raise hs.CooldownError(remaining)
        self.state["last_request"] = time.time()
        self._save_state()
        payload = hs._get_json(self.URL, api_key=None, timeout=30)   # keyless
        products = payload.get("products")
        if not isinstance(products, dict) or not products:
            raise hs.SyncError("Hypixel returned no bazaar products.")

        def number(value):
            try:
                return float(value or 0)
            except (TypeError, ValueError):
                return 0.0

        prices = {}
        for pid, product in products.items():
            if not isinstance(product, dict):
                continue
            quick = product.get("quick_status") or {}
            buy = number(quick.get("buyPrice"))     # pay this to insta-buy
            sell = number(quick.get("sellPrice"))   # receive this to insta-sell
            if buy <= 0:
                offers = [number(o.get("pricePerUnit")) for o in (product.get("sell_summary") or [])
                          if isinstance(o, dict)]
                offers = [v for v in offers if v > 0]
                buy = min(offers) if offers else 0.0
            if sell <= 0:
                orders = [number(o.get("pricePerUnit")) for o in (product.get("buy_summary") or [])
                          if isinstance(o, dict)]
                orders = [v for v in orders if v > 0]
                sell = max(orders) if orders else 0.0
            prices[str(pid).upper()] = {
                "buy": buy, "sell": sell,
                "volume": int(number(quick.get("buyMovingWeek")) + number(quick.get("sellMovingWeek"))),
            }
        self.prices, self.fetched_at = prices, time.time()
        self.state["retry_after"] = 0
        self.state["last_sync"] = self.fetched_at
        self._save_state()
        try:
            hs._save_json(self.cache_file, {"fetched_at": self.fetched_at, "prices": prices})
        except OSError:
            pass
        return prices

    def product_for(self, item_id, links=None):
        item = str(item_id).upper()
        if links and item in links:
            return str(links[item]).upper()
        return BAZAAR_ALIASES.get(item, item)

    def unit_price(self, item_id, mode="insta", overrides=None, links=None):
        """(unit_price or None, source_label, bazaar_product)."""
        item = str(item_id).upper()
        if item == COINS:
            return 1.0, "currency", None
        if overrides and item in overrides:
            try:
                return float(overrides[item]), "manual price", None
            except (TypeError, ValueError):
                pass
        product = self.product_for(item, links)
        entry = self.prices.get(product)
        if not isinstance(entry, dict):
            return None, "not on bazaar", product
        primary = float(entry.get("buy" if mode == "insta" else "sell") or 0)
        fallback = float(entry.get("sell" if mode == "insta" else "buy") or 0)
        label = "bazaar insta-buy" if mode == "insta" else "bazaar buy order (est.)"
        if primary > 0:
            return primary, label, product
        if fallback > 0:
            return fallback, label + " (fallback)", product
        return None, "no live price", product


# ----------------------------------------------------------------- recipe maths
def merged_recipe(recipes, item_id):
    out = {}
    for child, amount in recipes.get(str(item_id).upper(), []) or []:
        child = str(child).upper()
        try:
            out[child] = out.get(child, 0) + max(1, int(amount))
        except (TypeError, ValueError):
            continue
    return list(out.items())


def requirements_from_scratch(root, recipes, max_depth=24, max_nodes=20000):
    """{item_id: total amount} for one of `root`, ignoring anything you own.
    Includes crafted intermediates AND raw leaves (cost_report separates them)."""
    totals, seen = {}, [0]

    def walk(item, amount, ancestors, depth):
        if depth > max_depth or seen[0] > max_nodes:
            return
        seen[0] += 1
        totals[item] = totals.get(item, 0) + amount
        for child, per in merged_recipe(recipes, item):
            if child in ancestors:
                continue
            walk(child, per * amount, ancestors | {item}, depth + 1)

    root = str(root).upper()
    walk(root, 1, {root}, 0)
    totals.pop(root, None)
    return totals


def unit_value(item_id, recipes, price_of, memo=None, seen=frozenset()):
    """(value, complete) to obtain one of this item. complete=False if any
    descendant has no price, so the number is a lower bound."""
    item = str(item_id).upper()
    if memo is None:
        memo = {}
    if item in memo:
        return memo[item]
    if item in seen:
        return 0.0, False
    recipe = merged_recipe(recipes, item)
    if not recipe:
        price, _label, _product = price_of(item)
        result = (float(price), True) if price is not None else (0.0, False)
    else:
        total, complete = 0.0, True
        for child, per in recipe:
            value, ok = unit_value(child, recipes, price_of, memo, seen | {item})
            total += value * per
            complete = complete and ok
        result = (total, complete)
    memo[item] = result
    return result


def cost_report(rows, coin_places, recipes, price_of):
    """
    rows        : [(item_id, needed, covered, crafted)]
    coin_places : [(label, required, covered)]
    price_of    : callable(item_id) -> (price|None, label, product)
    """
    raw_rows, crafted_rows, unpriced = [], [], []
    shopping = 0.0
    memo = {}
    for item, needed, covered, crafted in rows:
        short = max(0, needed - covered)
        if crafted:
            value, complete = unit_value(item, recipes, price_of, memo)
            crafted_rows.append({"item": item, "needed": needed, "covered": covered,
                                 "unit": value, "complete": complete, "subtotal": value * short})
            continue
        price, label, product = price_of(item)
        if price is None:
            if short > 0:
                unpriced.append(item)
            subtotal = 0.0
        else:
            subtotal = float(price) * short
            shopping += subtotal
        raw_rows.append({"item": item, "needed": needed, "covered": covered, "short": short,
                         "unit": price, "source": label, "product": product, "subtotal": subtotal})
    raw_rows.sort(key=lambda r: (-r["subtotal"], r["item"]))
    crafted_rows.sort(key=lambda r: (-r["subtotal"], r["item"]))
    coin_rows, coin_total, coin_covered = [], 0, 0
    for label, required, covered in coin_places:
        coin_total += required
        coin_covered += min(required, covered)
        coin_rows.append({"label": label, "required": required, "covered": min(required, covered),
                          "short": max(0, required - covered)})
    return {"raw_rows": raw_rows, "crafted_rows": crafted_rows, "coin_rows": coin_rows,
            "shopping": shopping, "coin_total": coin_total, "coin_covered": coin_covered,
            "unpriced": sorted(set(unpriced)), "total_outlay": shopping + coin_total}
