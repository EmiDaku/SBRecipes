"""
Visual recipe tree for the SkyBlock Crafting Tracker.

Renders the crafting plan as a real node-and-branch tree on a zoomable,
pannable canvas: rarity-coloured item cards, curved ingredient links with
per-craft multipliers, foldable branches, a live minimap, hover tooltips and
a permanently visible zoom toolbar.

Branch structure is derived from each entry's path tuple, never from a
reconstructed key string, so it cannot disagree with the tracker's own
path_to_key() separator or id normalisation.

Standard library + Tkinter only. Drop-in companion for skyblock_tracker.py.
NOT AFFILIATED WITH OR ENDORSED BY HYPIXEL INC. OR MOJANG STUDIOS.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk, font as tkfont

NODE_W, NODE_H = 252, 74
H_GAP, V_GAP = 96, 26
ZOOM_MIN, ZOOM_MAX = 0.20, 3.00
MAP_W, MAP_H = 196, 132

DEFAULT_PALETTE = {
    "BG": "#0b1017", "SURFACE": "#141b26", "SURFACE_ALT": "#1c2636",
    "SURFACE_HOVER": "#263248", "BORDER": "#263043", "BORDER_STRONG": "#3a4963",
    "HEADER_BG": "#080c12", "TEXT": "#e6e9ef", "TEXT_STRONG": "#ffffff",
    "MUTED": "#8593aa", "ACCENT": "#4c8dff", "GREEN": "#4ade80",
    "AMBER": "#fbbf24", "RED": "#f87171", "GOLD": "#f5c542",
    "TREE_DONE_BG": "#195138", "TREE_DONE_FG": "#e1ffed", "SELECT_BG": "#29456b",
}


def _round_rect(canvas, x1, y1, x2, y2, r, **kw):
    r = max(0.5, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    points = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
              x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return canvas.create_polygon(points, smooth=True, **kw)


def _norm(item):
    return str(item).strip().upper()


class RecipeTreeCanvas(ttk.Frame):
    """A zoomable, pannable crafting-tree diagram.

    Public API used by the tracker (all take the tracker's own entry key):
        set_plan(plan_order, root_item, truncated=None)
        selected_entry() / select(key) / clear_selection() / reveal(key)
        refresh_icons() / expand_branch(key) / collapse_branch(key)
    Callbacks:
        on_select(entry), on_bump(entry), on_menu(entry, x_root, y_root),
        on_background()
    """

    def __init__(self, parent, *, display_name, rarity_color, icon_for, fmt_qty,
                 recipe_of, status_text, on_select, on_bump, on_menu,
                 on_background, palette=None, font_family="Segoe UI"):
        super().__init__(parent, style="Card.TFrame")
        self.pal = dict(DEFAULT_PALETTE)
        if palette:
            self.pal.update(palette)
        self.font_family = font_family
        self.display_name = display_name
        self.rarity_color = rarity_color
        self.icon_for = icon_for
        self.fmt_qty = fmt_qty
        self.recipe_of = recipe_of
        self.status_text = status_text
        self.on_select = on_select
        self.on_bump = on_bump
        self.on_menu = on_menu
        self.on_background = on_background

        self.zoom = 1.0
        self._plan = {}          # tracker key -> entry
        self._by_path = {}       # path tuple  -> entry
        self._key_path = {}      # tracker key -> path tuple
        self._children = {}      # path tuple  -> [child path tuples]
        self._desc = {}          # path tuple  -> descendant count
        self._collapsed = set()  # path tuples
        self._selected = None    # path tuple
        self._root_path = None
        self._truncated = []
        self._pos = {}
        self._visible = []
        self._edges = []
        self._bbox = (0.0, 0.0, float(NODE_W), float(NODE_H))
        self._tag_path = {}
        self._border_items = {}
        self._base_border = {}
        self._icon_refs = []
        self._fonts = {}
        self._pan = None
        self._press = None
        self._suppress_release = False
        self._suppress_double = False
        self._pending_fit = False
        self._tip = None
        self._tip_job = None
        self._tip_path = None
        self._flash_job = None
        self._flash_item = None
        self._map_transform = None
        self._map_view = None

        self._build()
        self._bind()

    # ------------------------------------------------------------------ build
    def _build(self):
        p = self.pal
        style = ttk.Style(self)
        style.configure("Horizontal.TScrollbar", background=p["SURFACE_HOVER"],
                        troughcolor=p["SURFACE"], bordercolor=p["SURFACE"],
                        lightcolor=p["SURFACE_HOVER"], darkcolor=p["SURFACE_HOVER"],
                        gripcount=0, arrowsize=12, width=12)
        style.map("Horizontal.TScrollbar",
                  background=[("pressed", p["MUTED"]), ("active", p["BORDER_STRONG"])],
                  lightcolor=[("pressed", p["MUTED"]), ("active", p["BORDER_STRONG"])],
                  darkcolor=[("pressed", p["MUTED"]), ("active", p["BORDER_STRONG"])])
        style.configure("Tree.Horizontal.TScale", background=p["SURFACE_HOVER"],
                        troughcolor=p["SURFACE_ALT"], bordercolor=p["BORDER"],
                        lightcolor=p["SURFACE_HOVER"], darkcolor=p["SURFACE_HOVER"])

        bar = ttk.Frame(self, style="Card.TFrame", padding=(10, 8, 10, 6))
        bar.pack(side="top", fill="x")
        ttk.Button(bar, text="−", width=3, style="Small.TButton",
                   command=self.zoom_out).pack(side="left")
        self._slider = ttk.Scale(bar, from_=ZOOM_MIN * 100, to=ZOOM_MAX * 100,
                                 orient="horizontal", length=150,
                                 style="Tree.Horizontal.TScale",
                                 command=lambda v: self._set_zoom(float(v) / 100.0))
        self._slider.set(100)
        self._slider.pack(side="left", padx=(8, 8))
        ttk.Button(bar, text="＋", width=3, style="Small.TButton",
                   command=self.zoom_in).pack(side="left")
        self._zoom_var = tk.StringVar(value="100%")
        ttk.Label(bar, textvariable=self._zoom_var, style="Muted.Card.TLabel",
                  width=6, anchor="e").pack(side="left", padx=(8, 10))
        ttk.Button(bar, text="Fit tree", style="Small.TButton",
                   command=self.fit).pack(side="left", padx=(0, 6))
        ttk.Button(bar, text="100%", style="Small.TButton",
                   command=self.reset_zoom).pack(side="left", padx=(0, 14))
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=(0, 14))
        ttk.Button(bar, text="Expand all", style="Small.TButton",
                   command=self.expand_all).pack(side="left", padx=(0, 6))
        ttk.Button(bar, text="Collapse all", style="Small.TButton",
                   command=self.collapse_all).pack(side="left", padx=(0, 14))
        self._map_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text="Minimap", variable=self._map_var,
                        style="Card.TCheckbutton",
                        command=self._toggle_map).pack(side="left")
        ttk.Label(bar, text="Ctrl+wheel zoom  ·  drag to pan  ·  double-click a node or its ± badge to expand",
                  style="Muted.Card.TLabel").pack(side="right")

        wrap = ttk.Frame(self, style="Card.TFrame")
        wrap.pack(side="top", fill="both", expand=True)
        wrap.columnconfigure(0, weight=1)
        wrap.rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(wrap, background=p["SURFACE"], highlightthickness=0,
                                takefocus=1, cursor="")
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self._vbar = ttk.Scrollbar(wrap, orient="vertical", command=self.canvas.yview)
        self._vbar.grid(row=0, column=1, sticky="ns")
        self._hbar = ttk.Scrollbar(wrap, orient="horizontal", command=self.canvas.xview)
        self._hbar.grid(row=1, column=0, sticky="ew")
        self.canvas.configure(yscrollcommand=self._on_yscroll,
                              xscrollcommand=self._on_xscroll)

        self._map_frame = tk.Frame(self.canvas, background=p["SURFACE"],
                                   highlightbackground=p["BORDER_STRONG"],
                                   highlightthickness=1, borderwidth=0)
        self.map = tk.Canvas(self._map_frame, width=MAP_W, height=MAP_H,
                             background=p["HEADER_BG"], highlightthickness=0,
                             cursor="hand2")
        self.map.pack(padx=3, pady=3)
        self._map_frame.place(relx=1.0, rely=1.0, x=-16, y=-16, anchor="se")
        self.map.bind("<Button-1>", self._map_nav)
        self.map.bind("<B1-Motion>", self._map_nav)

    def _bind(self):
        c = self.canvas
        c.bind("<Configure>", self._on_configure)
        c.bind("<MouseWheel>", self._on_wheel)
        c.bind("<Shift-MouseWheel>", self._on_wheel_h)
        c.bind("<Control-MouseWheel>", self._on_wheel_zoom)
        c.bind("<Button-4>", lambda e: self._scroll(0, -1))
        c.bind("<Button-5>", lambda e: self._scroll(0, 1))
        c.bind("<Shift-Button-4>", lambda e: self._scroll(-1, 0))
        c.bind("<Shift-Button-5>", lambda e: self._scroll(1, 0))
        c.bind("<Control-Button-4>", lambda e: self._zoom_step(1.12, e))
        c.bind("<Control-Button-5>", lambda e: self._zoom_step(1 / 1.12, e))
        c.bind("<ButtonPress-1>", self._on_press)
        c.bind("<B1-Motion>", self._on_drag)
        c.bind("<ButtonRelease-1>", self._on_release)
        c.bind("<ButtonPress-2>", self._on_pan_press)
        c.bind("<B2-Motion>", self._on_pan_drag)
        c.bind("<ButtonRelease-2>", self._on_pan_release)
        c.bind("<Double-Button-1>", self._on_double)
        c.bind("<Button-3>", self._on_context)
        c.bind("<Motion>", self._on_hover)
        c.bind("<Leave>", lambda _e: self._hide_tip())
        for seq, fn in (("<plus>", self.zoom_in), ("<equal>", self.zoom_in),
                        ("<KP_Add>", self.zoom_in), ("<minus>", self.zoom_out),
                        ("<KP_Subtract>", self.zoom_out), ("<Key-0>", self.reset_zoom),
                        ("<Key-f>", self.fit), ("<Home>", self.fit),
                        ("<Return>", self._toggle_selected), ("<space>", self._toggle_selected),
                        ("<Right>", self._expand_selected), ("<Left>", self._collapse_selected)):
            c.bind(seq, lambda _e, f=fn: f())

    # ------------------------------------------------------------- public API
    def set_plan(self, order, root, truncated=None):
        center = self._view_center_world()
        had_plan = bool(self._by_path)
        self._truncated = list(truncated or [])
        self._plan = {e["key"]: e for e in order}
        self._by_path = {tuple(e["path"]): e for e in order}
        self._key_path = {e["key"]: tuple(e["path"]) for e in order}

        # Structure comes from the path tuples themselves — no key rebuilding.
        self._children, self._desc = {}, {}
        for entry in order:
            path = tuple(entry["path"])
            if len(path) > 1:
                self._children.setdefault(path[:-1], []).append(path)
            for i in range(1, len(path)):
                ancestor = path[:i]
                self._desc[ancestor] = self._desc.get(ancestor, 0) + 1

        root_path = next((tuple(e["path"]) for e in order if len(e["path"]) == 1), None)
        if root_path is None and order:
            root_path = min((tuple(e["path"]) for e in order), key=len)
        root_changed = root_path != self._root_path
        self._root_path = root_path

        self._collapsed = {p for p in self._collapsed if p in self._by_path}
        if not had_plan or root_changed:
            # Only the root starts open: the goal shows its immediate children,
            # and every child branch stays folded until the user expands it.
            self._collapsed = {p for p, kids in self._children.items()
                               if kids and p != self._root_path}
        if self._selected not in self._by_path:
            self._selected = None

        self._rebuild_layout()
        self._redraw()
        if not had_plan or root_changed:
            self.fit()
        else:
            self._restore_center(center)
        self._draw_minimap()

    def selected_entry(self):
        return self._by_path.get(self._selected)

    def select(self, key):
        path = self._key_path.get(key)
        if path is None or path not in self._pos:
            return
        prev = self._selected
        self._selected = path
        for p, color, width in ((prev, self._base_border.get(prev), 1.2),
                                (path, self.pal["ACCENT"], 2.6)):
            item = self._border_items.get(p)
            if item and color:
                self.canvas.itemconfigure(item, outline=color,
                                          width=max(1.0, width * self.zoom))

    def clear_selection(self):
        prev = self._selected
        self._selected = None
        item = self._border_items.get(prev)
        color = self._base_border.get(prev)
        if item and color:
            self.canvas.itemconfigure(item, outline=color, width=max(1.0, 1.2 * self.zoom))

    def reveal(self, key):
        path = self._key_path.get(key)
        if path is None:
            return
        for i in range(1, len(path) + 1):
            self._collapsed.discard(path[:i])
        self._rebuild_layout()
        self._redraw()
        self.select(key)
        x, y = self._pos[path]
        self._center_world(x + NODE_W / 2, y + NODE_H / 2)
        self._draw_minimap()

    def expand_branch(self, key):
        path = self._key_path.get(key)
        if path is None:
            return
        n = len(path)
        self._collapsed = {p for p in self._collapsed if p[:n] != path}
        self._relayout_keep_view()
        self._ensure_visible(path)

    def collapse_branch(self, key):
        path = self._key_path.get(key)
        if path is None:
            return
        n = len(path)
        for p, kids in self._children.items():
            if kids and p[:n] == path:
                self._collapsed.add(p)
        self._relayout_keep_view()

    def refresh_icons(self):
        center = self._view_center_world()
        self._redraw()
        self._restore_center(center)

    def zoom_in(self):
        self._zoom_step(1.25)

    def zoom_out(self):
        self._zoom_step(1 / 1.25)

    def reset_zoom(self):
        self._set_zoom(1.0)

    def fit(self):
        if not self._pos:
            return
        cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
        if cw < 60 or ch < 60:
            self._pending_fit = True
            return
        x0, y0, x1, y1 = self._bbox
        z = min((cw - 24) / max(1.0, x1 - x0), (ch - 24) / max(1.0, y1 - y0))
        self.zoom = min(1.25, max(ZOOM_MIN, z))
        self._slider.set(self.zoom * 100)
        self._zoom_var.set(f"{int(round(self.zoom * 100))}%")
        self._redraw()
        self._center_world((x0 + x1) / 2, (y0 + y1) / 2)

    def expand_all(self):
        self._collapsed.clear()
        self._relayout_keep_view()

    def collapse_all(self):
        self._collapsed = {p for p, kids in self._children.items()
                           if kids and p != self._root_path}
        self._relayout_keep_view()

    # ----------------------------------------------------------------- layout
    def _rebuild_layout(self):
        self._pos, self._edges, self._visible = {}, [], []
        if self._root_path is None or self._root_path not in self._by_path:
            self._bbox = (0.0, 0.0, float(NODE_W), float(NODE_H))
            return
        next_y = [0.0]

        def per_of(parent_item, child_item):
            target = _norm(child_item)
            for child, per in self.recipe_of(parent_item):
                if _norm(child) == target:
                    return per
            return 1

        def place(path, depth):
            entry = self._by_path[path]
            x = depth * (NODE_W + H_GAP)
            kids = [] if path in self._collapsed else self._children.get(path, [])
            if kids:
                ys = [place(k, depth + 1) for k in kids]
                y = (ys[0] + ys[-1]) / 2.0
            else:
                y = next_y[0]
                next_y[0] += NODE_H + V_GAP
            self._pos[path] = (x, y)
            self._visible.append(entry)
            for kid in kids:
                self._edges.append((path, kid,
                                    per_of(entry["item"], self._by_path[kid]["item"])))
            return y

        place(self._root_path, 0)
        xs = [p[0] for p in self._pos.values()]
        ys = [p[1] for p in self._pos.values()]
        self._bbox = (min(xs) - 48, min(ys) - 48,
                      max(xs) + NODE_W + 72, max(ys) + NODE_H + 48)

    def _relayout_keep_view(self):
        center = self._view_center_world()
        self._rebuild_layout()
        self._redraw()
        self._restore_center(center)
        self._draw_minimap()

    def _toggle_fold(self, path):
        entry = self._by_path.get(path)
        if not entry:
            return
        if not self._children.get(path):
            self._explain_leaf(entry)
            return
        if path in self._collapsed:
            self._collapsed.discard(path)
        else:
            self._collapsed.add(path)
        self._relayout_keep_view()
        self._ensure_visible(path)

    def _ensure_visible(self, path):
        if path not in self._pos:
            return
        x, y = self._pos[path]
        z = self.zoom
        cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
        vx0, vy0 = self.canvas.canvasx(0), self.canvas.canvasy(0)
        vx1, vy1 = self.canvas.canvasx(cw), self.canvas.canvasy(ch)
        m = 24 * z
        if (x * z < vx0 + m or (x + NODE_W) * z > vx1 - m
                or y * z < vy0 + m or (y + NODE_H) * z > vy1 - m):
            self._center_world(x + NODE_W / 2, y + NODE_H / 2)

    def _toggle_selected(self, _event=None):
        if self._selected:
            self._toggle_fold(self._selected)
        return "break"

    def _expand_selected(self, _event=None):
        path = self._selected
        if path and path in self._collapsed:
            self._collapsed.discard(path)
            self._relayout_keep_view()
            self._ensure_visible(path)
        return "break"

    def _collapse_selected(self, _event=None):
        path = self._selected
        if path and self._children.get(path) and path not in self._collapsed:
            self._collapsed.add(path)
            self._relayout_keep_view()
        return "break"

    # ---------------------------------------------------------------- drawing
    def _font(self, size, bold=False):
        size = max(6, int(round(size)))
        key = (size, bold)
        if key not in self._fonts:
            self._fonts[key] = tkfont.Font(family=self.font_family, size=size,
                                           weight="bold" if bold else "normal")
        return self._fonts[key]

    def _fit_text(self, text, font, max_width):
        if font.measure(text) <= max_width:
            return text
        while text and font.measure(text + "…") > max_width:
            text = text[:-1]
        return (text + "…") if text else "…"

    def _icon_photo(self, item):
        photo = self.icon_for(item)
        if photo is None:
            return None
        z = self.zoom
        try:
            if z >= 2.6:
                photo = photo.zoom(3, 3)
            elif z >= 1.6:
                photo = photo.zoom(2, 2)
            elif z <= 0.45:
                photo = photo.subsample(3, 3)
            elif z <= 0.7:
                photo = photo.subsample(2, 2)
        except tk.TclError:
            pass
        self._icon_refs.append(photo)
        return photo

    def _status_of(self, entry):
        req, own = entry["required"], entry["owned"]
        if req <= 0 or own >= req:
            return "done"
        return "partial" if own > 0 else "open"

    def _redraw(self):
        c = self.canvas
        c.delete("all")
        self._clear_flash()
        self._icon_refs = []
        self._border_items = {}
        self._base_border = {}
        self._tag_path = {}
        self._hide_tip()
        p = self.pal
        z = self.zoom
        if not self._pos:
            c.create_text(40, 40, anchor="nw", fill=p["MUTED"],
                          font=self._font(11),
                          text="Choose a goal to grow its recipe tree.")
            c.configure(scrollregion=(0, 0, 80, 80))
            return

        x0, y0, x1, y1 = (v * z for v in self._bbox)
        if z >= 0.4:
            step = 140 * z
            nx, ny = int((x1 - x0) / step) + 1, int((y1 - y0) / step) + 1
            if nx + ny <= 500:
                for i in range(nx + 1):
                    gx = x0 + i * step
                    c.create_line(gx, y0, gx, y1, fill="#16202e", width=1, tags="grid")
                for i in range(ny + 1):
                    gy = y0 + i * step
                    c.create_line(x0, gy, x1, gy, fill="#16202e", width=1, tags="grid")
                c.tag_lower("grid")

        simple = z < 0.45 or len(self._visible) > 1200
        edge_font = self._font(8 * z, bold=True)
        for parent_path, child_path, per in self._edges:
            px, py = self._pos[parent_path]
            cx, cy = self._pos[child_path]
            ax, ay = (px + NODE_W) * z, (py + NODE_H / 2) * z
            bx, by = cx * z, (cy + NODE_H / 2) * z
            off = max(28 * z, (bx - ax) * 0.5)
            child = self._by_path[child_path]
            state = self._status_of(child)
            color = {"done": p["GREEN"], "partial": p["AMBER"]}.get(state, p["BORDER_STRONG"])
            c.create_line(ax, ay, ax + off, ay, bx - off, by, bx, by,
                          smooth=True, fill=color, width=max(1.0, 2.0 * z),
                          capstyle="round", joinstyle="round",
                          arrow=tk.LAST,
                          arrowshape=(max(4, 8 * z), max(5, 10 * z), max(2, 4 * z)),
                          tags="edge")
            if per > 1 and not simple:
                label = "×" + self.fmt_qty(child["item"], per)
                mx, my = (ax + bx) / 2, (ay + by) / 2
                tw, th = edge_font.measure(label) + 12 * z, 16 * z
                _round_rect(c, mx - tw / 2, my - th / 2, mx + tw / 2, my + th / 2,
                            7 * z, fill=p["SURFACE"], outline=p["BORDER"],
                            width=max(1.0, z), tags="edge")
                c.create_text(mx, my, text=label, fill=p["MUTED"],
                              font=edge_font, tags="edge")

        for index, entry in enumerate(self._visible):
            self._draw_node(entry, f"n{index}", simple)
        c.configure(scrollregion=(x0, y0, x1, y1))
        self._sync_minimap_view()

    def _draw_node(self, entry, tag, simple):
        c = self.canvas
        p = self.pal
        z = self.zoom
        path = tuple(entry["path"])
        self._tag_path[tag] = path
        x, y = self._pos[path]
        X, Y, W, H = x * z, y * z, NODE_W * z, NODE_H * z
        state = self._status_of(entry)
        done = state == "done"
        rarity = self.rarity_color(entry["item"])
        bg = p["TREE_DONE_BG"] if done else p["SURFACE_ALT"]
        border = p["GREEN"] if done else p["BORDER_STRONG"]
        if path == self._selected:
            border = p["ACCENT"]
        base_tags = ("node", tag)

        _round_rect(c, X + 2 * z, Y + 3 * z, X + W + 2 * z, Y + H + 3 * z, 10 * z,
                    fill=p["HEADER_BG"], outline="", tags=base_tags)
        card = _round_rect(c, X, Y, X + W, Y + H, 10 * z, fill=bg, outline=border,
                           width=max(1.0, (2.6 if path == self._selected else 1.2) * z),
                           tags=base_tags)
        self._border_items[path] = card
        self._base_border[path] = p["GREEN"] if done else p["BORDER_STRONG"]
        c.create_rectangle(X + 1.5 * z, Y + 8 * z, X + 5.5 * z, Y + H - 8 * z,
                           fill=p["GREEN"] if done else rarity, outline="", tags=base_tags)

        photo = self._icon_photo(entry["item"])
        if photo is not None:
            c.create_image(X + 16 * z, Y + H / 2, anchor="w", image=photo, tags=base_tags)

        name_font = self._font(10 * z, bold=True)
        name = self._fit_text(self.display_name(entry["item"]), name_font, W - 104 * z)
        c.create_text(X + 56 * z, Y + 11 * z, anchor="nw", text=name,
                      fill=p["TREE_DONE_FG"] if done else rarity,
                      font=name_font, tags=base_tags)

        if not simple:
            counts = (f"{self.fmt_qty(entry['item'], entry['owned'])} / "
                      f"{self.fmt_qty(entry['item'], entry['required'])}")
            c.create_text(X + 56 * z, Y + 32 * z, anchor="nw", text=counts,
                          fill=p["TEXT"], font=self._font(9 * z), tags=base_tags)
            status = self.status_text(entry["required"], entry["owned"])
            if not done:
                short = max(0, entry["required"] - entry["owned"])
                status += f"  ·  short {self.fmt_qty(entry['item'], short)}"
            scolor = {"done": p["GREEN"], "partial": p["AMBER"]}.get(state, p["RED"])
            c.create_text(X + 56 * z, Y + 50 * z, anchor="nw", text=status,
                          fill=scolor, font=self._font(8 * z, bold=True), tags=base_tags)

        bs = 24 * z
        bx, by = X + W - 34 * z, Y + 10 * z
        plus_tags = ("node", "plus", tag)
        _round_rect(c, bx, by, bx + bs, by + bs, 6 * z, fill=p["SURFACE_HOVER"],
                    outline=p["BORDER"], width=max(1.0, z), tags=plus_tags)
        c.create_text(bx + bs / 2, by + bs / 2, text="＋", fill=p["GREEN"],
                      font=self._font(10 * z, bold=True), tags=plus_tags)

        if self._children.get(path):
            folded = path in self._collapsed
            fx, fy, r = X + W, Y + H / 2, 9 * z
            fold_tags = ("node", "fold", tag)
            c.create_oval(fx - r, fy - r, fx + r, fy + r, fill=p["SURFACE"],
                          outline=p["ACCENT"] if folded else p["BORDER_STRONG"],
                          width=max(1.0, 1.4 * z), tags=fold_tags)
            c.create_text(fx, fy - 0.5 * z, text="+" if folded else "−",
                          fill=p["ACCENT"] if folded else p["TEXT"],
                          font=self._font(9 * z, bold=True), tags=fold_tags)
            if folded:
                c.create_text(fx, fy + r + 8 * z, text=f"+{self._desc.get(path, 0)}",
                              fill=p["MUTED"], font=self._font(7 * z, bold=True),
                              tags=fold_tags)

    # ------------------------------------------------------------- view maths
    def _view_center_world(self):
        if not self._pos:
            return None
        cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
        return (self.canvas.canvasx(cw / 2) / self.zoom,
                self.canvas.canvasy(ch / 2) / self.zoom)

    def _world_to_frac(self, wx, wy, px, py):
        x0, y0, x1, y1 = self._bbox
        z = self.zoom
        vw, vh = max(1.0, (x1 - x0) * z), max(1.0, (y1 - y0) * z)
        fx = (wx * z - px - x0 * z) / vw
        fy = (wy * z - py - y0 * z) / vh
        return min(1.0, max(0.0, fx)), min(1.0, max(0.0, fy))

    def _center_world(self, wx, wy):
        cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
        fx, fy = self._world_to_frac(wx, wy, cw / 2, ch / 2)
        self.canvas.xview_moveto(fx)
        self.canvas.yview_moveto(fy)
        self._sync_minimap_view()

    def _restore_center(self, center):
        if center:
            self._center_world(*center)
        elif self._pos:
            x0, y0, x1, y1 = self._bbox
            self._center_world((x0 + x1) / 2, (y0 + y1) / 2)

    def _set_zoom(self, z, anchor=None):
        z = min(ZOOM_MAX, max(ZOOM_MIN, z))
        if abs(z - self.zoom) < 1e-6:
            return
        if anchor:
            center = (self.canvas.canvasx(anchor[0]) / self.zoom,
                      self.canvas.canvasy(anchor[1]) / self.zoom)
        else:
            center = self._view_center_world()
        self.zoom = z
        self._slider.set(z * 100)
        self._zoom_var.set(f"{int(round(z * 100))}%")
        self._redraw()
        if anchor and center:
            fx, fy = self._world_to_frac(center[0], center[1], anchor[0], anchor[1])
            self.canvas.xview_moveto(fx)
            self.canvas.yview_moveto(fy)
            self._sync_minimap_view()
        else:
            self._restore_center(center)

    def _zoom_step(self, factor, event=None):
        anchor = (event.x, event.y) if event else None
        self._set_zoom(self.zoom * factor, anchor)

    # ----------------------------------------------------------------- events
    def _on_configure(self, _event=None):
        if self._pending_fit and self.canvas.winfo_width() > 60:
            self._pending_fit = False
            self.fit()
        else:
            self._sync_minimap_view()

    def _scroll(self, dx, dy):
        if dx:
            self.canvas.xview_scroll(dx * 3, "units")
        if dy:
            self.canvas.yview_scroll(dy * 3, "units")
        self._sync_minimap_view()
        self._hide_tip()

    def _on_wheel(self, event):
        self._scroll(0, -1 if event.delta > 0 else 1)
        return "break"

    def _on_wheel_h(self, event):
        self._scroll(-1 if event.delta > 0 else 1, 0)
        return "break"

    def _on_wheel_zoom(self, event):
        self._zoom_step(1.15 if event.delta > 0 else 1 / 1.15, event)
        return "break"

    def _tags_at(self, cx, cy):
        for item in reversed(self.canvas.find_overlapping(cx, cy, cx, cy)):
            tags = self.canvas.gettags(item)
            if "node" in tags:
                return tags
        return ()

    def _path_from_tags(self, tags):
        return next((self._tag_path[t] for t in tags if t in self._tag_path), None)

    def _path_at(self, cx, cy):
        return self._path_from_tags(self._tags_at(cx, cy))

    def _on_press(self, event):
        self.canvas.focus_set()
        self._hide_tip()
        self._suppress_release = False
        self._suppress_double = False
        cx, cy = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        tags = self._tags_at(cx, cy)
        self._press = (event.x, event.y, tags, self._path_from_tags(tags))
        if "node" in tags:
            self._pan = None
        else:
            self._pan = (event.x, event.y)
            self.canvas.scan_mark(event.x, event.y)
            self.canvas.configure(cursor="fleur")

    def _on_drag(self, event):
        if self._pan:
            self.canvas.scan_dragto(event.x, event.y, gain=1)
            self._sync_minimap_view()

    def _on_release(self, event):
        if self._suppress_release:
            self._suppress_release = False
            self._press = None
            self._pan = None
            self.canvas.configure(cursor="")
            return
        if self._pan:
            moved = abs(event.x - self._pan[0]) + abs(event.y - self._pan[1])
            self._pan = None
            self._press = None
            self.canvas.configure(cursor="")
            if moved < 5:
                self.clear_selection()
                self.on_background()
            return
        press = self._press
        self._press = None
        if not press:
            return
        tags, path = press[2], press[3]
        if path is None or path not in self._by_path:
            return
        entry = self._by_path[path]
        if "plus" in tags:
            self._suppress_double = True
            self.select(entry["key"])
            self.on_bump(entry)
        elif "fold" in tags:
            self._suppress_double = True
            self._toggle_fold(path)
        else:
            self.select(entry["key"])
            self.on_select(entry)

    def _on_pan_press(self, event):
        self._hide_tip()
        self.canvas.scan_mark(event.x, event.y)
        self.canvas.configure(cursor="fleur")

    def _on_pan_drag(self, event):
        self.canvas.scan_dragto(event.x, event.y, gain=1)
        self._sync_minimap_view()

    def _on_pan_release(self, _event):
        self.canvas.configure(cursor="")

    def _on_double(self, event):
        # Consume the trailing release so it cannot re-fire on stale canvas tags
        # after the branch redraws.
        self._press = None
        self._pan = None
        self.canvas.configure(cursor="")
        if self._suppress_double:
            self._suppress_double = False
            self._suppress_release = True
            return "break"
        self._suppress_release = True
        cx, cy = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        tags = self._tags_at(cx, cy)
        path = self._path_from_tags(tags)
        if path is None or "plus" in tags or path not in self._by_path:
            return "break"
        entry = self._by_path[path]
        self.select(entry["key"])
        self.on_select(entry)
        self._toggle_fold(path)
        return "break"

    def _on_context(self, event):
        path = self._path_at(self.canvas.canvasx(event.x), self.canvas.canvasy(event.y))
        if path and path in self._by_path:
            entry = self._by_path[path]
            self.select(entry["key"])
            self.on_select(entry)
            self.on_menu(entry, event.x_root, event.y_root)
        return "break"

    def _on_hover(self, event):
        if self._pan:
            return
        cx, cy = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        tags = self._tags_at(cx, cy)
        self.canvas.configure(cursor="hand2" if "node" in tags else "")
        path = self._path_from_tags(tags)
        if path != self._tip_path:
            self._hide_tip()
            if path:
                self._tip_path = path
                self._tip_job = self.after(450, lambda: self._show_tip(path))

    # ---------------------------------------------------------------- tooltip
    def _show_tip(self, path):
        self._tip_job = None
        entry = self._by_path.get(path)
        if not entry or path not in self._pos:
            return
        p = self.pal
        self._tip = tk.Toplevel(self)
        self._tip.wm_overrideredirect(True)
        self._tip.configure(background=p["BORDER_STRONG"])
        box = tk.Frame(self._tip, background=p["SURFACE_ALT"], padx=12, pady=9)
        box.pack(padx=1, pady=1)
        tk.Label(box, text=self.display_name(entry["item"]), background=p["SURFACE_ALT"],
                 foreground=self.rarity_color(entry["item"]),
                 font=(self.font_family, 10, "bold")).pack(anchor="w")
        tk.Label(box, text=entry["item"], background=p["SURFACE_ALT"],
                 foreground=p["MUTED"], font=(self.font_family, 8)).pack(anchor="w")
        counts = (f"Have {self.fmt_qty(entry['item'], entry['owned'])}  /  "
                  f"need {self.fmt_qty(entry['item'], entry['required'])}  ·  "
                  f"short {self.fmt_qty(entry['item'], max(0, entry['required'] - entry['owned']))}")
        tk.Label(box, text=counts, background=p["SURFACE_ALT"], foreground=p["TEXT"],
                 font=(self.font_family, 9)).pack(anchor="w", pady=(4, 0))
        chain = " → ".join(self.display_name(i) for i in path[:-1])
        tk.Label(box, text=("In: " + chain) if chain else "Root goal",
                 background=p["SURFACE_ALT"], foreground=p["MUTED"],
                 font=(self.font_family, 8), wraplength=340, justify="left").pack(anchor="w")
        kids = self._children.get(path) or []
        if kids:
            state = "collapsed" if path in self._collapsed else "expanded"
            tk.Label(box, text=f"{len(kids)} ingredient type(s)  ·  {state}",
                     background=p["SURFACE_ALT"], foreground=p["ACCENT"],
                     font=(self.font_family, 8, "bold")).pack(anchor="w", pady=(4, 0))
        self._tip.wm_geometry(f"+{self.canvas.winfo_pointerx() + 18}+{self.canvas.winfo_pointery() + 18}")

    def _hide_tip(self):
        if self._tip_job:
            try:
                self.after_cancel(self._tip_job)
            except ValueError:
                pass
            self._tip_job = None
        self._tip_path = None
        if self._tip:
            try:
                self._tip.destroy()
            except tk.TclError:
                pass
            self._tip = None

    # ------------------------------------------------------- leaf explanation
    def _explain_leaf(self, entry):
        path = tuple(entry["path"])
        item = entry["item"]
        if _norm(item) == "COINS":
            message = "Coins are a currency — edit the parent recipe to change this cost."
        else:
            try:
                recipe = self.recipe_of(item) or []
            except Exception:
                recipe = []
            if not recipe:
                message = "No recipe defined yet — right-click → Define / Edit Recipe."
            else:
                chain = {_norm(part) for part in path}
                if all(_norm(child) in chain for child, _n in recipe):
                    message = "Its ingredients already appear above it in this branch (loop guard)."
                elif self._truncated:
                    message = "Branch stopped at the tree safety limit — check for a recipe loop."
                else:
                    message = "No sub-ingredients are listed for this branch."
        self._flash(path, message)

    def _flash(self, path, text):
        self._clear_flash()
        if path not in self._pos:
            return
        p = self.pal
        z = self.zoom
        x, y = self._pos[path]
        cx, cy = (x + NODE_W / 2) * z, y * z - 12 * z
        font = self._font(8 * z, bold=True)
        tw, th = font.measure(text) + 18 * z, 22 * z
        bg = _round_rect(self.canvas, cx - tw / 2, cy - th, cx + tw / 2, cy, 8 * z,
                         fill=p["SURFACE_ALT"], outline=p["AMBER"],
                         width=max(1.0, z), tags="flash")
        tx = self.canvas.create_text(cx, cy - th / 2, text=text, fill=p["TEXT"],
                                     font=font, tags="flash")
        self._flash_item = (bg, tx)
        self._flash_job = self.after(2200, self._clear_flash)

    def _clear_flash(self):
        if self._flash_job:
            try:
                self.after_cancel(self._flash_job)
            except ValueError:
                pass
            self._flash_job = None
        if self._flash_item:
            for item in self._flash_item:
                try:
                    self.canvas.delete(item)
                except tk.TclError:
                    pass
            self._flash_item = None

    # ---------------------------------------------------------------- minimap
    def _toggle_map(self):
        if self._map_var.get():
            self._map_frame.place(relx=1.0, rely=1.0, x=-16, y=-16, anchor="se")
            self._draw_minimap()
        else:
            self._map_frame.place_forget()

    def _draw_minimap(self):
        if not self._map_var.get():
            return
        m = self.map
        m.delete("all")
        self._map_view = None
        self._map_transform = None
        if not self._pos:
            return
        p = self.pal
        x0, y0, x1, y1 = self._bbox
        s = min((MAP_W - 10) / max(1.0, x1 - x0), (MAP_H - 10) / max(1.0, y1 - y0))
        ox = (MAP_W - (x1 - x0) * s) / 2 - x0 * s
        oy = (MAP_H - (y1 - y0) * s) / 2 - y0 * s
        self._map_transform = (s, ox, oy)
        for parent_path, child_path, _per in self._edges:
            px, py = self._pos[parent_path]
            cx, cy = self._pos[child_path]
            m.create_line(ox + (px + NODE_W) * s, oy + (py + NODE_H / 2) * s,
                          ox + cx * s, oy + (cy + NODE_H / 2) * s,
                          fill=p["BORDER"], width=1)
        for entry in self._visible:
            x, y = self._pos[tuple(entry["path"])]
            state = self._status_of(entry)
            color = {"done": p["GREEN"], "partial": p["AMBER"]}.get(state, p["BORDER_STRONG"])
            m.create_rectangle(ox + x * s, oy + y * s,
                               ox + (x + NODE_W) * s, oy + (y + NODE_H) * s,
                               fill=color, outline="")
        self._map_view = m.create_rectangle(0, 0, 1, 1, outline=p["ACCENT"],
                                            fill=p["ACCENT"], stipple="gray12", width=2)
        self._sync_minimap_view()

    def _sync_minimap_view(self):
        if not self._map_var.get() or not self._map_transform or self._map_view is None:
            return
        s, ox, oy = self._map_transform
        cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
        wx0, wy0 = self.canvas.canvasx(0), self.canvas.canvasy(0)
        wx1, wy1 = self.canvas.canvasx(cw), self.canvas.canvasy(ch)
        self.map.coords(self._map_view, ox + wx0 * s, oy + wy0 * s,
                        ox + wx1 * s, oy + wy1 * s)

    def _map_nav(self, event):
        if not self._map_transform:
            return
        s, ox, oy = self._map_transform
        self._center_world((event.x - ox) / s, (event.y - oy) / s)

    def _on_yscroll(self, *args):
        self._vbar.set(*args)
        self._sync_minimap_view()

    def _on_xscroll(self, *args):
        self._hbar.set(*args)
        self._sync_minimap_view()
