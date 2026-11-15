# SkyBlock Crafting Tracker

**Version 3.1 — "account + coin edition"**

![Version](https://img.shields.io/badge/version-3.1-blue)
![Python](https://img.shields.io/badge/python-3.9%2B-3776AB?logo=python&logoColor=white)
![GUI](https://img.shields.io/badge/GUI-Tkinter-informational)
![Dependencies](https://img.shields.io/badge/dependencies-none-brightgreen)

A desktop crafting tracker for Hypixel SkyBlock. Build recipe trees by hand, sync your account stock, and see exactly what you still need to gather, craft, or buy — including a coin-cost estimate from live Bazaar prices.

> **Disclaimer:** Not affiliated with or endorsed by Hypixel Inc. or Mojang Studios.

**Requirements:** Python 3.9+ and Tkinter. No third-party packages.

---

## Table of Contents

1. [Files](#1-files)
2. [Account Sync](#2-account-sync)
3. [Counting Defaults](#3-counting-defaults)
4. [Coins in Recipes](#4-coins-in-recipes)
5. [Coin Cost Tab](#5-coin-cost-tab)
6. [Caching, Timing and Policy Compliance](#6-caching-timing-and-policy-compliance)
7. [Counting Methodology](#7-counting-methodology)
8. [UI](#8-ui)
9. [Recipes](#9-recipes)
10. [Known Limitations](#10-known-limitations)
11. [Changelog](#11-changelog)

---

## 1. Files

### Source and support files

| File | Purpose |
| --- | --- |
| `skyblock_tracker.py` | UI, recipe tree, allocation engine, Coin Cost tab, icons |
| `hypixel_sync.py` | NBT reader, item identity, API client, cache, cooldowns, counting presets, coin sources |
| `hypixel_economy.py` | Bazaar client, price cache, coin formatting, cost maths |
| `build_exe.bat` | One-file Windows build (bundles all three modules) |
| `README.md` | This file (feature log) |
| `API_KEY_SETUP.txt` | Where the API key goes |
| `hypixel_api_policy.txt` | The policy this build is written against (reference) |
| `myicon.ico` | Optional window/exe icon; the build works without it |

### Created at runtime in the data folder

| File | Contents |
| --- | --- |
| `skyblock_tracker_data.json` | Recipes, goal, manual counters, settings, manual/island-chest counts, manual prices, bazaar links, `last_closed` |
| `hypixel_cache.json` | Aggregated item map (incl. coins) + sync time |
| `hypixel_state.json` | Profile cooldowns, name→UUID cache |
| `hypixel_bazaar.json` | Cached bazaar prices + fetch time |
| `hypixel_bazaar_state.json` | Bazaar cooldown (survives cache clearing) |
| `hypixel_api_key.txt` | **Your key** (only if you use the in-app field) |
| `icon_cache/` | FurfSky Reborn zips, vanilla textures, report |

### Data folder location

| Run mode | Location |
| --- | --- |
| From source | The folder containing `skyblock_tracker.py` |
| From the exe | `%APPDATA%\SkyBlockTracker` (an older save beside the exe is copied over once, automatically) |

---

## 2. Account Sync

### 2.1 Requests per sync

Authenticated requests use the `API-Key` header:

```
GET /v2/skyblock/profiles?uuid=<uuid>
GET /v2/skyblock/museum?profile=<profile_id>      (optional)
```

- One **unauthenticated** Mojang name→UUID lookup the first time a username is used. It is cached permanently, and the key is never sent there.
- The keyless `/v2/resources/skyblock/items` catalogue supplies names, rarities and icon materials.

### 2.2 Containers read

- Inventory
- Worn armour
- Worn equipment
- Quiver
- Ender Chest
- Backpacks (all)
- Personal vault
- Wardrobe
- Accessory bag
- Fishing bag
- Potion bag
- Candy bag
- Carnival masks
- Sacks
- Museum donations

Unknown/new NBT-bearing fields are decoded automatically, and nested `bag_contents` layouts are understood.

### 2.3 Coins

Purse, co-op bank and personal bank come from the **same** profiles response — zero extra requests. Three counted sources feed one pseudo-item, `COINS`.

| API field | Source |
| --- | --- |
| `member.currencies.coin_purse` | Purse |
| `profile.banking.balance` | Bank (co-op) |
| `member.currencies.personal_bank_balance` | Personal bank (if present) |

If none are returned, the sync warns you; you can type a coin total manually. Balances are a snapshot and do not update while the app is open.

### 2.4 Island chests are not available

The public API does not expose chests on your private island. Use **Manual Counts** or the **Account Stock** panel. Those counts persist and behave exactly like a synced container. Negative values reserve items.

### 2.5 Item identity

- `base64 → gzip → NBT` via a dependency-free reader (all 13 tag types, depth/size guards).
- Identity comes from `tag.ExtraAttributes.id`, which is the SkyBlock item id recipes use.
- Pets → `PET_<TYPE>`, runes → `RUNE_<NAME>`.
- Vanilla stacks without SkyBlock NBT use a legacy id/damage table.
- Enchanted books count as `ENCHANTED_BOOK`, not per-enchantment.
- Extra aliases can be added via `settings.id_aliases` in the save file.

### 2.6 The item map

Reduced to `{container: {item_id: count}}`, then one `{item_id: count}` pool. **Only counts are cached** — no raw NBT, no lore, no history.

---

## 3. Counting Defaults

### 3.1 Presets

Found under **Settings → COUNTING DEFAULTS**.

| Preset | What it counts |
| --- | --- |
| **Crafting-safe** (default) | The shipped default, see [3.2](#32-the-crafting-safe-default) |
| **Storage + inventory** | Storage / backpacks / vault / sacks / inventory only |
| **Carried inventory only** | Just your inventory |
| **Everything I own** | Every container, **including** museum donations |
| **Custom** | Appears automatically when you tick a box |

Buttons: **Apply Preset**, **Restore Defaults**.

### 3.2 The "Crafting-safe" default

| Counted | Not counted |
| --- | --- |
| Inventory | Wardrobe (spare sets) |
| Worn armour | Accessory bag (magical power) |
| Worn equipment | Potion bag |
| Quiver | Candy bag |
| Ender Chest | Carnival masks |
| Backpacks | Museum (donated items must be withdrawn before crafting) |
| Personal vault | |
| Sacks | |
| Fishing bag | |
| Purse | |
| Co-op bank | |
| Personal bank | |
| Manual/island chests | |

Each is one checkbox with a live item/coin count. Coin sources and manual counts are always counted.

### 3.3 Presets are non-destructive

Presets never touch recipes, manual counts or prices — only which containers feed the pool. Everything recomputes instantly.

---

## 4. Coins in Recipes

### 4.1 Adding coins

Open any recipe → **＋ Add Coins**. A locked Coins row appears; type any of:

```
1500000    1,500,000    1.5m    2b
```

You can also pick **Coins** from the item picker. Coins can be added at **any depth**. They can never be a recipe **output** and never the **goal** (the app blocks both).

### 4.2 How coins behave

`COINS` is a raw leaf in the same allocation engine as every item:

- Required coins roll up with the usual multipliers.
- Holding an intermediate reduces the coin cost of its children.
- Purse + bank + personal bank + manual adjustment form the pool.
- Tree, resources and stock views all show coin rows — compact in cells (`12.5M`) and full in the side panel.
- The **＋** button steps coins by 100,000 instead of 1.

Coins are **excluded** from the unit-based "Materials covered" percentage, so 100M coins cannot drown out 5,000 Mithril. They get their own card.

### 4.3 Aliases

`COIN`, `COINS` and `MONEY` all normalise to `COINS`.

---

## 5. Coin Cost Tab

*Shortcut: `Ctrl+4`*

### 5.1 Data source

- Keyless `GET /v2/skyblock/bazaar` — no API key sent.
- Cached in `hypixel_bazaar.json`; refreshed on a click (or `Ctrl+P`), or once per launch when stale.
- Minimum interval floor **5 min**, default **15 min**, persisted separately so clearing the price cache cannot bypass it.
- The auction endpoint is deliberately **not** polled (paged and heavy).

### 5.2 Price modes

| Mode | Field | Meaning |
| --- | --- | --- |
| **Insta-buy** | `quick_status.buyPrice` | Pay now |
| **Buy order** | `quick_status.sellPrice` | Estimate of what you must outbid |

Order books (`sell_summary` / `buy_summary`) are the fallback when `quick_status` is zero. Prices are indicative and move constantly.

### 5.3 Scopes

| Scope | Description |
| --- | --- |
| **Cost to finish** (missing only) | What you still must buy and pay |
| **Full value from scratch** | Ignores everything you own; a clean recursive walk of the recipe graph |

### 5.4 Three groups

| Group | Contents |
| --- | --- |
| **Raw items to buy** | Quantity short, unit price, subtotal, source |
| **Raw coin costs** | Every place a recipe demands coins, and how much of it your purse/bank already covers |
| **Crafted items** | Unit value rolled up from ingredients. **Informational** and never added to the total (that would double count the raws). `≥` marks a value with unpriced descendants |

### 5.5 Totals

Shopping cost + recipe coin costs = **total coins needed**, compared against purse + bank + personal bank + manual → *"still short"* / *"fully funded"*.

Unpriced items are listed by name and the total is labelled a **lower bound**.

### 5.6 Per-item controls

Available in the side panel and on the tree's right-click menu.

| Action | Effect |
| --- | --- |
| **Set Manual Price…** | Always wins over bazaar; accepts `1.25m` etc. |
| **Link Bazaar Product…** | Map an item to any bazaar product id |
| **Clear Override / Link** | Back to automatic |

All persist in the save file; wipe them in Settings.

### 5.7 ID mismatches

An alias table handles known differences:

| Item id | Bazaar id |
| --- | --- |
| `MITHRIL` | `MITHRIL_ORE` |
| `TITANIUM` | `TITANIUM_ORE` |
| `LAPIS_LAZULI` | `INK_SACK:4` |
| `GUNPOWDER` | `SULPHUR` |
| … | … |

Anything else shows **"not on bazaar"** until you link or price it.

---

## 6. Caching, Timing and Policy Compliance

### 6.1 No background polling. Ever.

A pull happens only on a click (**Sync** / `Ctrl+R`, **Refresh Prices** / `Ctrl+P`) or once at launch if that cache is stale. The policy bans continuous polling of player data and session tracking, and asks for caching measured in hours or days.

### 6.2 Persisted minimum intervals

| Endpoint | Default | Floor | Selectable up to |
| --- | --- | --- | --- |
| Profiles | 5 min | 5 min | 6 hours |
| Bazaar | 15 min | 5 min | 1 hour |

Both timestamps are written **before** the request, so crashing or restarting cannot pull faster. Clicking early shows the remaining time.

### 6.3 Close time / start-up check

On close, `last_closed` = system time is written. At launch the app compares *now* against `last_closed` **and** the cache's `fetched_at`; if both are older than the interval and the cooldown has expired, exactly one pull happens. **Fresh cache = zero requests.**

### 6.4 HTTP 429

`RateLimit-Reset` is honoured and turned into a cooldown.

### 6.5 Key hygiene

- Never in source, the `.bat`, the exe, the save file, either cache, a log or an error message.
- Masked in the UI.
- Sent only to `api.hypixel.net` as a header.
- Read from `HYPIXEL_API_KEY` (priority) or the local key file.
- Bazaar and the items catalogue never receive it.

### 6.6 Also honoured

- "Not affiliated or endorsed" notice in the footer and Settings.
- No gambling, monetisation, paywall or proxying.
- No tracking beyond the single account you link.
- No stat history.
- No de-anonymisation.
- No game-integrity impact.
- Register your own application in the Hypixel Developer Portal before using authenticated endpoints.

---

## 7. Counting Methodology

### 7.1 Modes

| Mode | Behaviour |
| --- | --- |
| **From Hypixel** | One account-wide pool per item (and one for coins) |
| **Manual** | Original behaviour: owned counts keyed by full tree path, credit cascading into ingredients at that position only |

The app starts in **Manual** until the first successful sync, then switches to **From Hypixel**. Manual progress is never overwritten by syncing.

### 7.2 Allocation (From Hypixel)

Depth-first, in tree order:

```
have                = min(required, remaining pool)
shortfall           = required - have
children required   = per-craft amount x shortfall
```

- Two occurrences share one real pool (first in tree order wins).
- Holding an intermediate reduces its own ingredients.
- Owning the goal marks the whole tree "not needed".

### 7.3 Quantity boxes

In From Hypixel mode every quantity box means "account stock for this item" (or "coins available"). Typing a number writes a **manual adjustment**, so you can record mining or spending without a request.

- **Mark Complete** adds exactly the shortfall.
- **Reset Progress** clears adjustments, never synced data.

### 7.4 Safety

- Duplicate ingredient rows are merged (unique paths).
- Recipe cycles are skipped.
- Depth is capped at 24 and the tree at 12,000 nodes, with an on-screen warning naming the loop.

---

## 8. UI

### 8.1 Header

Logo, title, live subtitle, account pill, status pill.

### 8.2 Toolbar

| Button | Shortcut |
| --- | --- |
| Sync Hypixel | `Ctrl+R` |
| Settings | `Ctrl+,` |
| Add/Edit Recipe | |
| Choose Goal | |
| Manual Counts | |
| Export | |
| Reset Progress | |

### 8.3 Five stat cards

1. **Goal**
2. **Materials covered** (coins excluded)
3. **Raw items still needed**
4. **Coins to finish** (have / short / unpriced)
5. **Items in your account** (coins excluded from the item count)

### 8.4 Four views (`Ctrl+1` … `Ctrl+4`)

| View | Description |
| --- | --- |
| **Recipe Tree** | Required / have / short / status / ＋, rarity colours, stripes, full-width green completion that survives selection, interactive triangles, right-click menu. Coins are gold |
| **Total Resources** | Every item combined, expandable into "N x in `<chain>`"; filter, Crafted / Raw / Hide-completed, expand/collapse |
| **Account Stock** | The raw item map: in stock / needed / allocated / surplus / containers, expandable per container, searchable, "only items this goal needs", Use as Goal |
| **Coin Cost** | See [section 5](#5-coin-cost-tab) |

### 8.5 Side panels

Consistent side panels with a per-item source breakdown.

### 8.6 Settings

Account · API key · Sync behaviour · Counting defaults · Coin cost & bazaar prices · Local data · About.

### 8.7 Quality-of-life

- Expansion, selection and scroll position survive rebuilds.
- 1-second footer clock (last sync, next allowed sync, price age, mode).
- Export writes a costed shopping list to a `.txt` **and** the clipboard.
- DPI scaling, dark title bar, dark comboboxes.
- `Esc` or a blank-area click clears the selection.

---

## 9. Recipes

Recipes are added **by hand** with the visual row editor (quantity + item picker, no syntax). Hypixel's items resource carries no recipe graph, so nothing about recipes is downloaded.

- The **Titanium Drill DR-X355** chain ships as a starter database.
- Saving an empty recipe intentionally deletes a starter recipe.
- The same editor is reused for manual counts, with a coins button in both.

---

## 10. Known Limitations

- **Island chests:** not in the public API (use Manual Counts).
- **Purse / bank:** a snapshot from the moment of the sync.
- **Personal bank:** read best-effort; the field may not exist for all accounts.
- **Auction prices:** not fetched; use a manual price for auction-only items.
- **"Buy order" pricing:** an estimate derived from the insta-sell price.
- **Enchanted books** are counted as `ENCHANTED_BOOK`; **pets** as `PET_<TYPE>`.
- **Vanilla stacks** without SkyBlock NBT use a best-effort legacy id table.
- **Museum items** currently borrowed back are skipped to avoid double counting.
- **Single account:** one linked account and one profile at a time.

---

## 11. Changelog

### 3.1

- Counting presets + Restore Defaults.
- Purse / co-op bank / personal bank pulled into the item map as coin sources (no extra requests).
- `COINS` as a first-class recipe ingredient with a **＋ Add Coins** button and `m`/`b`/`k` parsing.
- New **Coin Cost** tab: bazaar pricing, insta-buy vs buy-order, cost-to-finish vs from-scratch, rolled-up craft values, manual prices, manual bazaar links, unpriced lower-bound warnings.
- Fifth stat card.
- Costed export.
- Coins excluded from unit progress and item totals.
- Separate persisted bazaar cooldown.
- Gold coin styling and icon.

### 3.0

- Account sync (profiles + museum), NBT reader, item map, pool allocation with intermediate-item credit.
- Account Stock view, manual/island-chest layer.
- Settings dialog with key management.
- Persisted cooldown + close/start-up staleness check.
- Export, state-preserving rebuilds, recipe dedupe, tree safety caps, disclaimer footer.

### 2.0

- Per-tree-position counters, cascading credit.
- Total Resources view.
- FurfSky Reborn icons with CATS decoding, rarity row colours.

### 1.0

- Recursive recipe tree, visual recipe editor, dark theme, local saves.