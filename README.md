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
## 6. Recipe Tree Canvas

Shortcut: `Ctrl+1` · Module: `recipe_tree_canvas.py`

The Recipe Tree tab is a node-and-branch diagram drawn on a canvas, not a table. The **goal sits on the left** and ingredients flow to the right, one column per recipe depth. Total Resources, Account Stock and Coin Cost are still tables.

Branch structure is taken from each entry's recipe **path**, the same paths the allocation engine uses, so what you can expand always matches what is actually being counted.

### 6.1 Node cards

| Element | Meaning |
| --- | --- |
| Left accent bar | Rarity colour — green once the node is complete |
| Icon | FurfSky Reborn texture, or the vanilla fallback |
| Name | Rarity colour, bold; truncated with `…` when narrow (full text in the tooltip) |
| Counts line | `have / required` — coins compact (`12.5M`), items comma-grouped |
| Status line | `✓ Complete`, `✓ Not needed`, `In progress` or `Needed`, plus `short N` |
| ＋ button | Adds 1 to owned (100,000 for coins) at that node |
| ± badge on the right edge | Folds/unfolds the branch; `+N` underneath counts hidden descendants |

A completed node keeps its green card fill **even while selected**; selection is shown by an accent outline. Below the canvas is the legend: rarity tiers, Coins (gold) and Complete (green).

### 6.2 Links

- Curved arrow from the parent's right edge to the child's left edge.
- Colour follows the **child's** state: green = complete, amber = in progress, muted = still needed.
- A `×N` pill on the link is the per-craft ingredient amount from the parent's recipe. It is shown when the amount is more than 1 and hidden when you zoom far out.
- A faint blueprint grid sits behind the graph for depth reference.

### 6.3 Zoom

| Control | Action |
| --- | --- |
| `Ctrl` + mouse wheel | Zoom anchored at the cursor — the point under the pointer stays put |
| **−** / **＋** toolbar buttons | Step zoom (×1.25) about the viewport centre |
| Slider | Continuous zoom |
| `%` readout | Current level |
| **Fit tree** | Frame the whole visible tree (capped at 125% so small trees don't blow up) |
| **100%** | Reset to 1:1 |
| `+` / `=` / `-` keys | Zoom in / out (canvas focused) |
| `0` | Back to 100% |
| `f` or `Home` | Fit tree |

Range **20% – 300%**. Cards, fonts, icons, link widths and arrowheads are **re-rendered** at every level rather than bitmap-stretched, so text stays sharp.

Below 45% zoom — or above 1,200 visible nodes — cards switch to a compact form (icon + name + buttons) and multiplier pills are hidden, so very large plans stay responsive.

### 6.4 Pan and scroll

| Control | Action |
| --- | --- |
| Drag on empty canvas | Pan |
| Middle-drag anywhere | Pan (works over cards too) |
| Mouse wheel | Scroll vertically |
| `Shift` + wheel | Scroll horizontally |
| Scrollbars | Themed horizontal **and** vertical bars |

Linux `Button-4/5` wheel events are handled, with `Ctrl` and `Shift` variants.

### 6.5 Folding branches

- **Only the root starts open.** A new goal shows its immediate ingredients; every deeper branch is folded until you open it.
- **Double-click a node** — or single-click its `±` badge — to expand/collapse one level.
- `Enter` or `Space` toggles the selected node; `→` expands it, `←` collapses it.
- A folded badge shows `+N`, the total number of descendants hidden beneath it.
- **Expand all** / **Collapse all** in the toolbar (Collapse all returns to root-only).
- Right-click → **Expand branch** / **Collapse branch** acts on the whole subtree.
- Double-clicking the `＋` count button bumps the count and never folds the branch.
- A node you expand is scrolled into view automatically if it would land off-screen.

### 6.6 Minimap

A live overview in the bottom-right corner: one block per visible node, coloured by status, with links, plus a draggable rectangle showing your viewport. Click or drag inside it to jump around a large tree. Toggle it with the **Minimap** checkbox.

### 6.7 Hover tooltips

Resting on a card for a moment shows the full item name, the raw item id, `have / need / short`, the `In: …` recipe chain (or "Root goal"), and the ingredient count with its expanded/collapsed state.

### 6.8 When a node will not expand

A short amber note appears above the node instead of failing silently:

| Note | Meaning |
| --- | --- |
| No recipe defined yet | It really is a raw leaf — right-click → **Define / Edit Recipe** |
| Its ingredients already appear above it in this branch | Loop guard: the recipe points back at an ancestor |
| Branch stopped at the tree safety limit | The node/depth cap was hit — see [8.4](#84-safety) |
| No sub-ingredients are listed for this branch | The recipe resolved to nothing walkable |
| Coins are a currency | Edit the parent recipe to change that coin cost |

### 6.9 Selection and actions

- **Click a card** → the side panel (same panel as every other tab: Set Amount, Mark Complete, Define/Edit Recipe).
- **Right-click a card** → Mark Complete · Set Amount · Expand branch · Collapse branch · Define/Edit Recipe · Set Manual Price… · Use as Goal · Add Manual/island-chest count.
- **`Esc`**, or a click on empty canvas, clears the selection and the panel.
- **Show in Recipe Tree** from Total Resources or Coin Cost opens every ancestor, expands the target node itself, selects it and centres it.

### 6.10 View state

Zoom, pan, folds and selection survive syncs, `＋` clicks, recipe edits, counting-preset changes and tab switches. **Fit tree** runs automatically on first load and whenever you change the goal; folds then reset to root-only. This is session view state — it is not written to the save file.


---

## 7. Caching, Timing and Policy Compliance

### 7.1 No background polling. Ever.

A pull happens only on a click (**Sync** / `Ctrl+R`, **Refresh Prices** / `Ctrl+P`) or once at launch if that cache is stale. The policy bans continuous polling of player data and session tracking, and asks for caching measured in hours or days.

### 7.2 Persisted minimum intervals

| Endpoint | Default | Floor | Selectable up to |
| --- | --- | --- | --- |
| Profiles | 5 min | 5 min | 6 hours |
| Bazaar | 15 min | 5 min | 1 hour |

Both timestamps are written **before** the request, so crashing or restarting cannot pull faster. Clicking early shows the remaining time.

### 7.3 Close time / start-up check

On close, `last_closed` = system time is written. At launch the app compares *now* against `last_closed` **and** the cache's `fetched_at`; if both are older than the interval and the cooldown has expired, exactly one pull happens. **Fresh cache = zero requests.**

### 7.4 HTTP 429

`RateLimit-Reset` is honoured and turned into a cooldown.

### 7.5 Key hygiene

- Never in source, the `.bat`, the exe, the save file, either cache, a log or an error message.
- Masked in the UI.
- Sent only to `api.hypixel.net` as a header.
- Read from `HYPIXEL_API_KEY` (priority) or the local key file.
- Bazaar and the items catalogue never receive it.

### 7.6 Also honoured

- "Not affiliated or endorsed" notice in the footer and Settings.
- No gambling, monetisation, paywall or proxying.
- No tracking beyond the single account you link.
- No stat history.
- No de-anonymisation.
- No game-integrity impact.
- Register your own application in the Hypixel Developer Portal before using authenticated endpoints.

---

## 8. Counting Methodology

### 8.1 Modes

| Mode | Behaviour |
| --- | --- |
| **From Hypixel** | One account-wide pool per item (and one for coins) |
| **Manual** | Original behaviour: owned counts keyed by full tree path, credit cascading into ingredients at that position only |

The app starts in **Manual** until the first successful sync, then switches to **From Hypixel**. Manual progress is never overwritten by syncing.

### 8.2 Allocation (From Hypixel)

Depth-first, in tree order:

```
have                = min(required, remaining pool)
shortfall           = required - have
children required   = per-craft amount x shortfall
```

- Two occurrences share one real pool (first in tree order wins).
- Holding an intermediate reduces its own ingredients.
- Owning the goal marks the whole tree "not needed".

### 8.3 Quantity boxes

In From Hypixel mode every quantity box means "account stock for this item" (or "coins available"). Typing a number writes a **manual adjustment**, so you can record mining or spending without a request.

- **Mark Complete** adds exactly the shortfall.
- **Reset Progress** clears adjustments, never synced data.

### 8.4 Safety

- Duplicate ingredient rows are merged (unique paths).
- Recipe cycles are skipped.
- Depth is capped at 24 and the tree at 12,000 nodes, with an on-screen warning naming the loop.

---

## 9. UI

### 9.1 Header

Logo, title, live subtitle, account pill, status pill.

### 9.2 Toolbar

| Button | Shortcut |
| --- | --- |
| Sync Hypixel | `Ctrl+R` |
| Settings | `Ctrl+,` |
| Add/Edit Recipe | |
| Choose Goal | |
| Manual Counts | |
| Export | |
| Reset Progress | |

### 9.3 Five stat cards

1. **Goal**
2. **Materials covered** (coins excluded)
3. **Raw items still needed**
4. **Coins to finish** (have / short / unpriced)
5. **Items in your account** (coins excluded from the item count)

### 9.4 Four views (`Ctrl+1` … `Ctrl+4`)

| View | Description |
| --- | --- |
| **Recipe Tree** | Required / have / short / status / ＋, rarity colours, stripes, full-width green completion that survives selection, interactive triangles, right-click menu. Coins are gold |
| **Total Resources** | Every item combined, expandable into "N x in `<chain>`"; filter, Crafted / Raw / Hide-completed, expand/collapse |
| **Account Stock** | The raw item map: in stock / needed / allocated / surplus / containers, expandable per container, searchable, "only items this goal needs", Use as Goal |
| **Coin Cost** | See [section 5](#5-coin-cost-tab) |

### 9.5 Side panels

Consistent side panels with a per-item source breakdown.

### 9.6 Settings

Account · API key · Sync behaviour · Counting defaults · Coin cost & bazaar prices · Local data · About.

### 9.7 Quality-of-life

- Expansion, selection and scroll position survive rebuilds.
- 1-second footer clock (last sync, next allowed sync, price age, mode).
- Export writes a costed shopping list to a `.txt` **and** the clipboard.
- DPI scaling, dark title bar, dark comboboxes.
- `Esc` or a blank-area click clears the selection.

---

## 10. Recipes

Recipes are added **by hand** with the visual row editor (quantity + item picker, no syntax). Hypixel's items resource carries no recipe graph, so nothing about recipes is downloaded.

- The **Titanium Drill DR-X355** chain ships as a starter database.
- Saving an empty recipe intentionally deletes a starter recipe.
- The same editor is reused for manual counts, with a coins button in both.

---

## 11. Known Limitations

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

## 12. Changelog

### 3.2 — visual tree edition

- **Recipe Tree tab rebuilt as a real node-and-branch canvas** (new module `recipe_tree_canvas.py`); the other three tabs are unchanged.
- Rarity-coloured node cards with icon, name, have/need, status and shortfall; green completion fill survives selection.
- Curved, status-coloured links with arrowheads and `×N` per-craft multiplier pills; blueprint grid backdrop.
- **Zoom 20–300%**: `Ctrl`+wheel anchored at the cursor, −/＋ buttons, live slider, `%` readout, **Fit tree**, **100%**, and `+` `-` `0` `f` `Home` keys. Everything re-renders at each level instead of bitmap-scaling.
- **Pan and scroll**: drag empty space, middle-drag anywhere, wheel / `Shift`+wheel, themed horizontal *and* vertical scrollbars.
- **Foldable branches** with `±` badges and `+N` hidden-descendant counts; only the root starts open; double-click, `Enter`/`Space`, `→`/`←`, Expand/Collapse all, and per-subtree right-click commands.
- **Live minimap** with a draggable viewport rectangle, toggleable from the toolbar.
- **Hover tooltips** with full name, item id, counts, recipe chain and branch state.
- Explanatory amber flash when a node cannot expand: no recipe / loop guard / safety limit / coins.
- `＋` bump, click-to-select, right-click menu and cross-tab **Show in Recipe Tree** all preserved; reveal now opens ancestors *and* the target node, then centres it.
- Zoom, pan, folds and selection survive syncs, count bumps, recipe edits and tab switches.
- Compact-card rendering keeps very large plans responsive.
- **Fix:** branch structure is derived from recipe paths rather than a reconstructed key string, so nodes below the first level expand correctly (previously only the root's children could be opened).
- **Fix:** the trailing button-release of a double-click no longer re-toggles a branch or selects a stale node.
- `build_exe.bat` unchanged — PyInstaller bundles the new module via the import.

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