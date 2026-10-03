"""Terraria seed cracker - screenshot driven GUI.

Workflow:
  1. Load a screenshot (file or clipboard).
  2. Click two corners of one tile to calibrate the tile grid.
  3. Click along the ground surface, one click per column.
  4. Optionally pick the tree style and forest background you can see,
     the moon phase, and which side the dungeon is on. Every one of these
     is skippable.
  5. Solve. Results appear in the log.

The heavy lifting is done by solve.exe (CUDA); this module only prepares the
observations and renders progress.
"""
import json
import os
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

from rangeslider import RangeSlider
from tilecanvas import TileCanvas

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ASSETS = os.path.join(ROOT, "assets")
SOLVER = os.path.join(HERE, "solve.exe")

WORLD_SIZES = {
    "Large  (8400 x 2400)": (8400, 2400),
    "Medium (6400 x 1800)": (6400, 1800),
    "Small  (4200 x 1200)": (4200, 1200),
    "Don't know (try all 3)": None,
}

# WorldGen.Reset rolls each ore tier with one genRand.Next(2): the base ore on
# a 1, the alternate on a 0. Order here is the order the draws happen in.
ORE_TIERS = [
    ("copper", "copper", "tin"),
    ("iron",   "iron",   "lead"),
    ("silver", "silver", "tungsten"),
    ("gold",   "gold",   "platinum"),
]

TREE_STYLES = [0, 1, 2, 3, 4, 5]
# Styles 4/5 and 3/31 render identically (they differ only in a far parallax
# layer), so only one of each pair is offered; the solver accepts either.
# RollRandomForestBGStyle returns 1..13, with 3->31, 5->51 and 7->71..73 as
# sub-variants. WorldGen.IsBackgroundConsideredTheSame groups {3,31}, {5,51}
# and {7,71,72,73} as one backdrop each, so the picker shows one entry per
# visual class and the solver matches on the same classes. 4 and 5 are
# separate backdrops despite both using the 93/94 mountain pair.
BG_STYLES = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13]
MOONS = list(range(9))
# How many candidate seeds to echo into the log while a run is in progress.
PRINT_LIMIT = 100
# How many to keep pinned in the live leaderboard pane.
CAND_SHOW = 10
# Must match MAX_PROFILE in solve.cu: the kernel matches the samples against a
# rolling history this wide, so the whole selection has to fit inside it.
MAX_PROFILE = 256
OCEAN_STYLES = list(range(8))

# Biome backdrops. Each is one world-wide roll, so seeing any of them anywhere
# in the world pins the seed regardless of where the terrain sample came from.
BIOME_STYLES = {
    "corrupt":    ([0, 1, 2, 3, 4, 51, 52], "Corruption backdrop"),
    "crimson":    ([0, 1, 2, 3, 4, 5, 6],   "Crimson backdrop"),
    "jungle":     ([0, 1, 2, 3, 4, 5, 6],   "Jungle backdrop"),
    "snow":       ([0, 1, 2, 3, 4, 5, 6, 7, 8, 21, 22, 31, 32, 41, 42],
                                            "Snow backdrop"),
    "hallow":     ([0, 1, 2, 3, 4, 5],      "Hallow backdrop (hardmode only)"),
    "mushroom":   ([0, 1, 2, 3, 4],         "Glowing mushroom backdrop"),
    "underworld": ([0, 1, 2],               "Underworld backdrop"),
    "desert":     ([0, 1, 2, 3, 4, 51, 52, 53], "Desert backdrop"),
}


def _hms(seconds):
    """Compact h/m/s for progress lines."""
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return "%dh%02dm" % (h, m)
    if m:
        return "%dm%02ds" % (m, sec)
    return "%ds" % sec


def _si(value):
    """Human-readable rate, e.g. 1.3M."""
    if value >= 1e6:
        return "%.2fM" % (value / 1e6)
    if value >= 1e3:
        return "%.0fk" % (value / 1e3)
    return "%.0f" % value


class SpritePicker(ttk.LabelFrame):
    """A row of clickable image buttons with a Skip option."""

    # Width available to a picker inside the options panel, in pixels.
    PANEL_W = 600

    def __init__(self, parent, title, styles, prefix, cols=5, multi=False):
        super().__init__(parent, text=title, padding=6)
        # multi=True lets several styles be named at once. A world shows one
        # forest background per zone (2 on small, 3 on medium, 4 on large), so
        # naming every one you have seen is a much stronger constraint than
        # naming a single one.
        self.multi = multi
        self.selected = set()
        self.value = None
        # Set by an attached ZoneOrderPicker so it can re-offer its choices
        # whenever the selection changes.
        self.on_change = None
        self._buttons = {}
        self._images = {}
        # Fit the thumbnails to the panel rather than assuming a size: with
        # cols+1 cells (Skip counts as one) plus padding and the frame border,
        # anything wider gets clipped off the right edge.
        avail = self.PANEL_W - 34
        cell = max(56, int(avail / (cols + 1)) - 8)

        skip = ttk.Button(self, text="Skip\n(unknown)", width=10,
                          command=lambda: self.select(None))
        skip.grid(row=0, column=0, padx=3, pady=3, sticky="nsew")
        self._buttons[None] = skip

        for i, style in enumerate(styles):
            path = os.path.join(ASSETS, "%s_%d.png" % (prefix, style))
            btn_kw = {"command": lambda s=style: self.select(s)}
            if os.path.exists(path):
                img = Image.open(path)
                img.thumbnail((cell, int(cell * 0.82)), Image.NEAREST)
                photo = ImageTk.PhotoImage(img)
                self._images[style] = photo
                btn = ttk.Button(self, image=photo, **btn_kw)
            else:
                btn = ttk.Button(self, text="style %d" % style, width=10, **btn_kw)
            r, c = divmod(i + 1, cols)
            btn.grid(row=r, column=c, padx=3, pady=3)
            btn.bind("<Enter>", lambda e, s=style, p=path: self._preview(e, s, p))
            btn.bind("<Leave>", lambda e: self._hide_preview())
            self._buttons[style] = btn

        self.status = ttk.Label(self, text="skipped", foreground="#888")
        self.status.grid(row=0, column=1, columnspan=cols - 1, sticky="w", padx=6)
        self.select(None)

    def _preview(self, ev, style, path):
        """Pop up a large view of the sprite, since the thumbnails are small."""
        self._hide_preview()
        if not os.path.exists(path):
            return
        img = Image.open(path)
        big = img.resize((img.width * 3, img.height * 3), Image.NEAREST)
        self._preview_img = ImageTk.PhotoImage(big)
        top = tk.Toplevel(self)
        top.overrideredirect(True)
        top.attributes("-topmost", True)
        x = ev.widget.winfo_rootx() - big.width - 12
        y = ev.widget.winfo_rooty() - big.height // 3
        top.geometry("+%d+%d" % (max(0, x), max(0, y)))
        frm = tk.Frame(top, bg="#1a1d22", bd=1, relief="solid")
        frm.pack()
        tk.Label(frm, image=self._preview_img, bg="#1a1d22").pack()
        tk.Label(frm, text="style %d" % style, bg="#1a1d22", fg="#d8d4cc").pack()
        self._preview_win = top

    def _hide_preview(self):
        win = getattr(self, "_preview_win", None)
        if win is not None:
            win.destroy()
            self._preview_win = None

    def select(self, style):
        if self.multi:
            if style is None:
                self.selected.clear()
            elif style in self.selected:
                self.selected.discard(style)
            else:
                self.selected.add(style)
            self.value = min(self.selected) if self.selected else None
            for key, btn in self._buttons.items():
                on = (key in self.selected) if key is not None                      else not self.selected
                try:
                    btn.state(["pressed"] if on else ["!pressed"])
                except tk.TclError:
                    pass
            if self.selected:
                self.status.config(
                    text="styles %s selected" % ", ".join(
                        str(v) for v in sorted(self.selected)),
                    foreground="#1a7f37")
            else:
                self.status.config(text="skipped", foreground="#888")
            if self.on_change:
                self.on_change()
            return
        self.value = style
        for key, btn in self._buttons.items():
            mark = "!" if key == style else ""
            try:
                btn.state(["pressed"] if key == style else ["!pressed"])
            except tk.TclError:
                pass
            del mark
        if style is None:
            self.status.config(text="skipped", foreground="#888")
        else:
            self.status.config(text="style %d selected" % style, foreground="#1a7f37")


class ZoneOrderPicker(ttk.LabelFrame):
    """Optional left-to-right zone assignment for a multi-select SpritePicker.

    The plain pickers ask only "does this style appear somewhere", which is
    order-free. A player who can see which band is which knows more than that,
    so this pins a style to a specific zone index. Zones are numbered left to
    right, matching Main.treeStyle[]/bg[] order, which is how the generator
    lays them out along Main.treeX[].

    Every dropdown is independently skippable: knowing zone 1 but not zone 2 is
    common and still a real constraint. Four are always shown because the zone
    count depends on world size (2 small / 3 medium / 4 large) and the size may
    be "try all 3"; the solver ignores entries past the count for each size.
    """

    MAX_ZONES = 4
    SKIP = "Skip"

    def __init__(self, parent, title, source, note=None):
        super().__init__(parent, text=title, padding=6)
        self._source = source
        self._boxes = []
        self._combos = []
        row = ttk.Frame(self)
        row.pack(fill="x")
        for z in range(self.MAX_ZONES):
            cell = ttk.Frame(row)
            cell.pack(side="left", padx=(0, 10))
            ttk.Label(cell, text="Zone %d" % (z + 1)).pack(anchor="w")
            var = tk.StringVar(value=self.SKIP)
            box = ttk.Combobox(cell, textvariable=var, values=[self.SKIP],
                               state="readonly", width=10)
            box.pack()
            box.bind("<<ComboboxSelected>>", lambda _e: self._sync_status())
            self._boxes.append(var)
            self._combos.append(box)
        self.status = ttk.Label(self, text="no order set", foreground="#888")
        self.status.pack(anchor="w", pady=(4, 0))
        if note:
            ttk.Label(self, foreground="#888", wraplength=560, justify="left",
                      text=note).pack(fill="x", pady=(2, 0))

    def refresh(self):
        """Re-offer whichever styles are currently selected in the picker.

        Called whenever the source picker changes. A zone already pinned to a
        style that has since been deselected is reset, so the two controls can
        never disagree.
        """
        opts = [self.SKIP] + [str(s) for s in sorted(self._source.selected)]
        for var, box in zip(self._boxes, self._combos):
            box.configure(values=opts)
            if var.get() not in opts:
                var.set(self.SKIP)
        self._sync_status()

    def _sync_status(self):
        pinned = self.value
        if not any(v >= 0 for v in pinned):
            self.status.config(text="no order set", foreground="#888")
        else:
            self.status.config(
                text="order: " + " | ".join(
                    ("zone%d=%d" % (i + 1, v)) if v >= 0 else ("zone%d=?" % (i + 1))
                    for i, v in enumerate(pinned)),
                foreground="#1a7f37")

    @property
    def value(self):
        """Per-zone styles, -1 where the zone is unconstrained."""
        out = []
        for var in self._boxes:
            raw = var.get()
            out.append(-1 if raw == self.SKIP else int(raw))
        return out


class App(ttk.Frame):
    def __init__(self, master):
        super().__init__(master, padding=8)
        self.grid(sticky="nsew")
        master.columnconfigure(0, weight=1)
        master.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        self.image = None
        self.photo = None
        self.scale = 1.0
        self.calib = []           # two clicks bounding one tile
        self.tile_px = None       # pixels per tile
        self.points = []          # clicked ground points, image coords
        self.mode = tk.StringVar(value="calibrate")
        self.proc = None

        self._build_toolbar()
        self._build_canvas()
        self._build_side()
        self._build_log()

    # ---------- layout ----------
    def _build_toolbar(self):
        bar = ttk.Frame(self)
        bar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        ttk.Button(bar, text="Open screenshot...", command=self.open_image).pack(side="left")
        ttk.Button(bar, text="Paste from clipboard", command=self.paste_image).pack(side="left", padx=4)
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Button(bar, text="Undo click", command=self.undo).pack(side="left", padx=4)
        ttk.Button(bar, text="Clear", command=self.clear_points).pack(side="left")
        self.info = ttk.Label(bar, text="load a screenshot to begin")
        self.info.pack(side="left", padx=12)

    def _build_canvas(self):
        wrap = ttk.Frame(self)
        wrap.grid(row=1, column=0, sticky="nsew")
        wrap.rowconfigure(0, weight=1)
        wrap.columnconfigure(0, weight=1)
        self.view = TileCanvas(wrap, on_change=self._on_points_changed)
        self.view.grid(row=0, column=0, sticky="nsew")

        # --- calibration box row ---
        cal = ttk.LabelFrame(wrap, text="Step 1 - calibrate by box (most accurate)",
                             padding=6)
        cal.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        self.box_mode = tk.BooleanVar(value=True)
        ttk.Checkbutton(cal, text="Box mode (drag a rectangle)",
                        variable=self.box_mode,
                        command=self._toggle_box).pack(side="left")
        ttk.Label(cal, text="tiles wide").pack(side="left", padx=(12, 3))
        self.box_w = tk.StringVar(value="")
        ew = ttk.Entry(cal, textvariable=self.box_w, width=6)
        ew.pack(side="left")
        ttk.Label(cal, text="tiles tall").pack(side="left", padx=(8, 3))
        self.box_h = tk.StringVar(value="")
        eh = ttk.Entry(cal, textvariable=self.box_h, width=6)
        eh.pack(side="left")
        # Redraw on every keystroke so the columns/rows update as they are typed.
        self.box_w.trace_add("write", lambda *_a: self._live_box())
        self.box_h.trace_add("write", lambda *_a: self._live_box())
        for w in (ew, eh):
            w.bind("<Return>", self._apply_box)
        ttk.Button(cal, text="Apply box", command=self._apply_box).pack(side="left", padx=6)
        ttk.Button(cal, text="Clear box", command=self._clear_box).pack(side="left")
        ttk.Label(cal, text="Drag a big box over many tiles, type its size, "
                           "then turn box mode off and click the ground.",
                  foreground="#888").pack(side="left", padx=10)

        opts = ttk.Frame(wrap)
        opts.grid(row=2, column=0, sticky="ew", pady=(4, 0))
        self.grid_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(opts, text="Show tile grid", variable=self.grid_var,
                        command=self._toggle_grid).pack(side="left")
        ttk.Button(opts, text="Fit view", command=self.view.fit_view).pack(side="left", padx=6)
        ttk.Label(opts, text="Tile px override").pack(side="left", padx=(12, 3))
        self.tilepx_var = tk.StringVar(value="")
        e = ttk.Entry(opts, textvariable=self.tilepx_var, width=7)
        e.pack(side="left")
        e.bind("<Return>", self._apply_override)
        ttk.Button(opts, text="Apply", command=self._apply_override).pack(side="left", padx=3)
        self.calib_lbl = ttk.Label(opts, text="no calibration yet", foreground="#888")
        self.calib_lbl.pack(side="left", padx=12)

    def _sync_world_size(self, _ev=None):
        """Keep the search-window slider scaled to the selected world width.

        For "try all 3" the slider spans the largest world; the window is sent
        to each size as the same FRACTION of that world's width, since an
        absolute tile range from one size is meaningless at another.
        """
        chosen = WORLD_SIZES[self.size_var.get()]
        mx = chosen[0] if chosen else 8400
        self.win_slider.set_maximum(mx)
        if hasattr(self, "win_note"):
            self.win_note.configure(
                text=("spans all 3 sizes proportionally"
                      if chosen is None else "tiles across a %d-wide world" % mx))

    def _build_ores(self, parent):
        """Ore tier pickers: four independent coin flips rolled in WorldGen.Reset.

        Each ore is either the base tier or its alternate, decided by one
        genRand.Next(2). Naming all four cuts the space 16x, which beats any
        other single stage-1 clue, and unlike the sprite pickers there is
        nothing to misidentify - you either mined copper or you mined tin.
        """
        box = ttk.LabelFrame(parent, text="Ore tiers you have seen (optional)",
                             padding=6)
        box.pack(fill="x", pady=6)
        self.ore_vars = {}
        self.ore_boxes = {}
        row = ttk.Frame(box)
        row.pack(fill="x")
        for i, (key, base, alt) in enumerate(ORE_TIERS):
            cell = ttk.Frame(row)
            cell.pack(side="left", padx=(0, 10))
            ttk.Label(cell, text=base.capitalize()).pack(anchor="w")
            var = tk.StringVar(value="Skip")
            cb = ttk.Combobox(cell, textvariable=var, state="readonly", width=10,
                              values=["Skip", base.capitalize(), alt.capitalize()])
            cb.pack()
            cb.bind("<<ComboboxSelected>>", lambda _e: self._sync_ore_status())
            self.ore_vars[key] = var
            self.ore_boxes[key] = cb
        self.ore_status = ttk.Label(box, text="none set", foreground="#888")
        self.ore_status.pack(anchor="w", pady=(4, 0))
        self.ore_note = ttk.Label(
            box, foreground="#888", wraplength=560, justify="left",
            text="One glance at your ore or bars answers this, and each one "
                 "you fill in halves the search space.")
        self.ore_note.pack(fill="x", pady=(2, 0))

    def _sync_ore_status(self):
        named = [k for k, v in self.ore_vars.items() if v.get() != "Skip"]
        if named:
            self.ore_status.config(
                text="%d of 4 set (%dx reduction)" % (len(named), 2 ** len(named)),
                foreground="#1a7f37")
        else:
            self.ore_status.config(text="none set", foreground="#888")

    def _sync_ores(self):
        """The Constant skips the iron and gold rolls, forcing the base tier.

        With that flag on (and drunk off) those two draws never happen, so the
        world always has iron and gold and the controls would be lying if they
        stayed editable. They are pinned and disabled instead.
        """
        forced = self.dont_starve.get() and not self.drunk.get()
        for key, base, _alt in ORE_TIERS:
            if key not in ("iron", "gold"):
                continue
            cb = self.ore_boxes[key]
            if forced:
                self.ore_vars[key].set(base.capitalize())
                cb.configure(state="disabled")
            else:
                cb.configure(state="readonly")
        self.ore_note.configure(
            text=("The Constant never rolls iron or gold, so those are always "
                  "base tier and are locked here. Only copper and silver carry "
                  "information in that world."
                  if forced else
                  "One glance at your ore or bars answers this, and each one "
                  "you fill in halves the search space."))
        self._sync_ore_status()

    def _sync_evil(self):
        """Show the crimson-side control only for drunk worlds."""
        # Drunk re-enables the iron and gold rolls that the Constant skips.
        self._sync_ores()
        if self.drunk.get():
            self.evil_lbl.grid(row=0, column=0, sticky="w")
            self.evil_box.grid(row=0, column=1)
            self.evil_note.grid(row=3, column=0, columnspan=2, sticky="w",
                                pady=(4, 0))
        else:
            self.evil_var.set("Skip")
            self.evil_lbl.grid_remove()
            self.evil_box.grid_remove()
            self.evil_note.grid_remove()

    def _toggle_box(self):
        self.view.box_mode = self.box_mode.get()
        self.view.canvas.configure(
            cursor="tcross" if self.box_mode.get() else "crosshair")

    def _live_box(self):
        """Update the grid as the tile counts are typed, without any messages."""
        def num(var):
            t = var.get().strip()
            if not t:
                return None
            try:
                v = int(round(float(t)))
                return v if v > 0 else None
            except ValueError:
                return None
        self.view.box_cols = num(self.box_w)
        self.view.box_rows = num(self.box_h)
        if self.view.box and (self.view.box_cols or self.view.box_rows):
            self.grid_var.set(True)
            self.view.show_grid = True
            self.view.apply_box()

    def _apply_box(self, _ev=None):
        def num(var):
            t = var.get().strip()
            if not t:
                return None
            try:
                v = int(round(float(t)))
                return v if v > 0 else None
            except ValueError:
                return None
        self.view.box_cols = num(self.box_w)
        self.view.box_rows = num(self.box_h)
        if self.view.box is None:
            self.say("Drag a box on the screenshot first, then type its size.")
            return
        if not self.view.box_cols and not self.view.box_rows:
            self.say("Type how many tiles wide and/or tall the box is.")
            return
        self.view.apply_box()
        cs = self.view.cell_size()
        if cs:
            sw, sh = cs
            if abs(sw - sh) > 0.05:
                self.say("Calibrated from box: %.2f px wide x %.2f px tall."
                         % (sw, sh))
            else:
                self.say("Calibrated from box: %.2f px per tile." % sw)
            self.box_mode.set(False)
            self._toggle_box()
            self.say("Box mode off - click the ground tiles now. Drag the yellow "
                     "corner handles any time to refine; clicks refine it too.")

    def _clear_box(self):
        self.view.clear_box()
        self.box_w.set("")
        self.box_h.set("")
        self.say("Box calibration cleared.")

    def _toggle_grid(self):
        self.view.show_grid = self.grid_var.get()
        self.view.redraw()

    def _apply_override(self, _ev=None):
        txt = self.tilepx_var.get().strip()
        if not txt:
            self.view.manual_size = None
        else:
            try:
                self.view.manual_size = float(txt)
            except ValueError:
                self.say("Tile size must be a number, e.g. 16")
                return
        self.view.refit()

    def _on_points_changed(self):
        g = self.view.fit
        n = len(self.view.points)
        if not g:
            self.calib_lbl.config(text="%d click%s (need 2+ to calibrate)"
                                       % (n, "" if n == 1 else "s"),
                                  foreground="#888")
            self.info.config(text="click surface tiles, any order")
            return
        cs = self.view.cell_size()
        src = "box" if self.view.manual_size else "clicks"
        if cs and abs(cs[0] - cs[1]) > 0.05:
            txt = "%.2f x %.2f px/tile (from %s)" % (cs[0], cs[1], src)
        else:
            txt = "%.2f px/tile (from %s)" % (cs[0] if cs else g[0], src)
        self.calib_lbl.config(text=txt, foreground="#1a7f37")
        cols = self.view.tile_coords() or {}
        span = (max(cols) - min(cols) + 1) if cols else 0
        self.info.config(text="%d columns spanning %d (gaps: %d)"
                              % (len(cols), span, max(0, span - len(cols))))

    def _build_side(self):
        # The options column is taller than most screens, so it lives inside a
        # scrollable canvas rather than being clipped at the bottom.
        outer = ttk.Frame(self)
        outer.grid(row=1, column=1, sticky="ns", padx=(8, 0))
        outer.rowconfigure(0, weight=1)
        shell = tk.Canvas(outer, width=SpritePicker.PANEL_W, highlightthickness=0,
                          bg=self.winfo_toplevel().cget("bg"))
        shell.grid(row=0, column=0, sticky="ns")
        sb = ttk.Scrollbar(outer, orient="vertical", command=shell.yview)
        sb.grid(row=0, column=1, sticky="ns")
        shell.configure(yscrollcommand=sb.set)

        side = ttk.Frame(shell)
        shell.create_window((0, 0), window=side, anchor="nw")
        side.bind("<Configure>",
                  lambda e: shell.configure(scrollregion=shell.bbox("all")))

        def _wheel(ev):
            shell.yview_scroll(-1 * (ev.delta // 120), "units")
            return "break"

        # bind_all while the pointer is inside the panel, so the wheel works no
        # matter which child widget it happens to be over, and is released again
        # so the image canvas keeps its own scrolling.
        shell.bind("<Enter>", lambda e: shell.bind_all("<MouseWheel>", _wheel))
        shell.bind("<Leave>", lambda e: shell.unbind_all("<MouseWheel>"))
        self._side_shell = shell

        world = ttk.LabelFrame(side, text="World", padding=6)
        world.pack(fill="x")
        self.size_var = tk.StringVar(value="Large  (8400 x 2400)")
        ttk.Label(world, text="Size").grid(row=0, column=0, sticky="w")
        size_box = ttk.Combobox(world, textvariable=self.size_var,
                                values=list(WORLD_SIZES), state="readonly",
                                width=20)
        size_box.grid(row=0, column=1, pady=2)
        size_box.bind("<<ComboboxSelected>>", self._sync_world_size)
        self.ftw = tk.BooleanVar(value=False)
        self.drunk = tk.BooleanVar(value=False)
        self.remix = tk.BooleanVar(value=False)
        self.dont_starve = tk.BooleanVar(value=False)
        ttk.Checkbutton(world, text="For The Worthy", variable=self.ftw).grid(
            row=1, column=0, columnspan=2, sticky="w")
        ttk.Checkbutton(world, text="Drunk world", variable=self.drunk,
                        command=self._sync_evil).grid(
            row=2, column=0, columnspan=2, sticky="w")
        ttk.Checkbutton(world, text="Remix (Don't dig up)", variable=self.remix).grid(
            row=3, column=0, columnspan=2, sticky="w")
        ttk.Checkbutton(world, text="The Constant (Don't starve)",
                        variable=self.dont_starve,
                        command=self._sync_ores).grid(
            row=4, column=0, columnspan=2, sticky="w")
        # Off by default: a full sweep takes about ten seconds, and stopping at
        # 100 hides both the true candidate count and the ranked leaderboard,
        # which is the part that says whether a match is convincing.
        self.early_stop = tk.BooleanVar(value=False)
        ttk.Checkbutton(world, text="Stop early at 100 candidates (faster, "
                                    "hides the true count)",
                        variable=self.early_stop).grid(
            row=5, column=0, columnspan=2, sticky="w")
        ttk.Label(world, text="Difficulty does not affect terrain.",
                  foreground="#888").grid(row=6, column=0, columnspan=2, sticky="w",
                                          pady=(4, 0))

        self.tree_picker = SpritePicker(side,
                                        "Forest tree styles you can see "
                                        "(pick every one you have seen)",
                                        TREE_STYLES, "tree", multi=True)
        self.tree_picker.pack(fill="x", pady=6)
        ttk.Label(side, foreground="#b58900", wraplength=560, justify="left",
                  text="Only ordinary forest trees on plain grass carry seed "
                       "information. Jungle, corruption, crimson, snow, mushroom "
                       "and desert/palm trees have fixed styles - skip this "
                       "picker unless you are looking at a normal forest. "
                       "The world is split into 2 to 4 bands with a different "
                       "tree style each, so naming several narrows the search "
                       "much faster than naming one."
                  ).pack(fill="x", padx=4)
        self.tree_order = ZoneOrderPicker(
            side, "Tree style order, left to right (optional)",
            self.tree_picker,
            note="Only fill this in if you can tell which band is which - "
                 "normally that means seeing where the style changes, or "
                 "having walked the world. A wrong guess here filters out the "
                 "true seed, so leave a zone on Skip unless you are sure.")
        self.tree_order.pack(fill="x", pady=(4, 6))
        self.tree_picker.on_change = self.tree_order.refresh

        self.bg_picker = SpritePicker(side,
                                      "Forest backgrounds you can see "
                                      "(pick every one you have seen)",
                                      BG_STYLES, "bg", multi=True)
        self.bg_picker.pack(fill="x")
        self.bg_order = ZoneOrderPicker(
            side, "Forest background order, left to right (optional)",
            self.bg_picker,
            note="Same caveat as the tree order: zones run left to right and a "
                 "wrong pin rejects the true seed. Styles that render alike "
                 "(3/31, 5/51, 7/71/72/73) are still treated as one backdrop.")
        self.bg_order.pack(fill="x", pady=(4, 6))
        self.bg_picker.on_change = self.bg_order.refresh

        self.ocean_picker = SpritePicker(side, "Ocean backdrop (if you are at the sea)",
                                         OCEAN_STYLES, "ocean")
        self.ocean_picker.pack(fill="x", pady=6)

        # One picker per biome backdrop, all optional.
        self.biome_pickers = {}
        for key, (styles, title) in BIOME_STYLES.items():
            pk = SpritePicker(side, title, styles, key)
            pk.pack(fill="x", pady=6)
            self.biome_pickers[key] = pk

        self._build_ores(side)

        self.moon_picker = SpritePicker(side, "Moon (same every night)", MOONS,
                                        "moon")
        self.moon_picker.pack(fill="x", pady=6)

        extra = ttk.LabelFrame(side, text="Other clues (all optional)", padding=6)
        extra.pack(fill="x", pady=6)
        self.side_var = tk.StringVar(value="Skip")
        ttk.Label(extra, text="Dungeon side").grid(row=1, column=0, sticky="w")
        ttk.Combobox(extra, textvariable=self.side_var, state="readonly", width=8,
                     values=["Skip", "Left", "Right"]).grid(row=1, column=1)
        # crimsonLeft only affects generation in a drunk world, so the control
        # is hidden entirely rather than shown greyed out.
        self.evil_var = tk.StringVar(value="Skip")
        self.evil_lbl = ttk.Label(extra, text="Crimson side")
        self.evil_box = ttk.Combobox(extra, textvariable=self.evil_var,
                                     state="readonly", width=8,
                                     values=["Skip", "Left", "Right"])
        self.evil_note = ttk.Label(
            extra, foreground="#888", wraplength=380, justify="left",
            text="Drunk world only: this picks which half gets Crimson and "
                 "which gets Corruption.")
        ttk.Label(extra, text="Search window (tiles)").grid(row=2, column=0,
                                                            columnspan=2,
                                                            sticky="w",
                                                            pady=(8, 0))
        self.win_note = ttk.Label(extra, foreground="#888",
                                  text="tiles across a 8400-wide world")
        self.win_note.grid(row=3, column=0, columnspan=2, sticky="w")
        self.win_slider = RangeSlider(extra, maximum=8400)
        self.win_slider.grid(row=4, column=0, columnspan=2, sticky="ew")
        extra.columnconfigure(1, weight=1)

        self._sync_evil()

        run = ttk.Frame(side)
        run.pack(fill="x", pady=8)
        self.run_btn = ttk.Button(run, text="Solve", command=self.solve)
        self.run_btn.pack(side="left")
        ttk.Button(run, text="Stop", command=self.stop).pack(side="left", padx=4)
        self.progress = ttk.Progressbar(side, mode="determinate", maximum=1.0)
        self.progress.pack(fill="x")

    def _build_log(self):
        """Console on the left, live candidate leaderboard on the right.

        The solver streams candidates as it goes, but the log scrolls them
        away as fast as they arrive. The right pane keeps the best few pinned
        and updating in place, so a long run always shows its current answer.
        """
        wrap = ttk.Frame(self)
        wrap.grid(row=2, column=0, columnspan=2, sticky="nsew", pady=(8, 0))
        # The candidate table is a fixed 8 columns wide, so give it the width it
        # needs and let the console take the rest rather than splitting 50/50
        # and clipping the table.
        wrap.columnconfigure(0, weight=1)
        wrap.columnconfigure(1, weight=0)
        wrap.rowconfigure(0, weight=1)

        left = ttk.Frame(wrap)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        left.rowconfigure(1, weight=1)
        left.columnconfigure(0, weight=1)
        ttk.Label(left, text="Console", foreground="#888").grid(row=0, column=0,
                                                                sticky="w")
        self.log = tk.Text(left, height=18, bg="#12151a", fg="#d8d4cc",
                           insertbackground="#d8d4cc", wrap="word")
        self.log.grid(row=1, column=0, sticky="nsew")
        lsb = ttk.Scrollbar(left, orient="vertical", command=self.log.yview)
        lsb.grid(row=1, column=1, sticky="ns")
        self.log.configure(yscrollcommand=lsb.set)

        right = ttk.Frame(wrap)
        right.grid(row=0, column=1, sticky="nsew", padx=(4, 0))
        right.rowconfigure(1, weight=1)
        right.columnconfigure(0, weight=1)
        self.cand_lbl = ttk.Label(right, text="Top candidates", foreground="#888")
        self.cand_lbl.grid(row=0, column=0, sticky="w")
        # 52 chars fits the widest row the near-miss table produces.
        self.cand = tk.Text(right, height=18, width=52, bg="#12151a",
                            fg="#d8d4cc", insertbackground="#d8d4cc",
                            wrap="none", state="disabled",
                            font=("Consolas", 9))
        self.cand.grid(row=1, column=0, sticky="nsew")
        csb = ttk.Scrollbar(right, orient="vertical", command=self.cand.yview)
        csb.grid(row=1, column=1, sticky="ns")
        self.cand.configure(yscrollcommand=csb.set)
        self.cand.tag_configure("hdr", foreground="#888")
        self.cand.tag_configure("best", foreground="#7ee08a")

        self.rowconfigure(2, weight=0)
        self.say("Load a screenshot, calibrate one tile, then click along the ground.")
        self.say("Pick a HILLY stretch: flat ground carries almost no information.")
        self.show_candidates([])

    def say(self, msg):
        self.log.insert("end", msg + "\n")
        self.log.see("end")

    def show_candidates(self, rows, total=None):
        """Redraw the leaderboard in place.

        `rows` is best-first. Each entry is either a bare seed (all that is
        known while a run streams) or the full near-miss tuple
        (seed, run, at_x, n0, n1, n2, n3, n4plus), in which case the same
        columns the log prints are shown here too.
        """
        self.cand.configure(state="normal")
        self.cand.delete("1.0", "end")
        if not rows:
            self.cand.insert("end", "no candidates yet\n", "hdr")
        else:
            full = isinstance(rows[0], (tuple, list))
            if full:
                self.cand.insert(
                    "end", "%11s %4s %6s %6s %4s %4s %4s %5s\n"
                    % ("seed", "run", "at x", "exact", "+-1", "+-2", "+-3",
                       "worse"), "hdr")
            else:
                self.cand.insert("end", "%-4s %12s\n" % ("#", "seed"), "hdr")
            for i, r in enumerate(rows):
                tag = "best" if i == 0 else ""
                if full:
                    self.cand.insert("end", "%11d %4d %6d %6d %4d %4d %4d %5d\n"
                                     % tuple(r[:8]), tag)
                else:
                    self.cand.insert("end", "%-4d %12d\n" % (i + 1, r), tag)
        self.cand.configure(state="disabled")
        if total is None:
            total = len(rows)
        n = len(rows)
        # Under "try all 3" the pane is rebuilt per size, so name the size it
        # belongs to; a seed without its world size is not actionable.
        head = "Top candidates" + self._size_tag()
        self.cand_lbl.configure(
            text=head if total <= n
            else head + "  (%d of %d found)" % (n, total))

    # ---------- image ----------
    def open_image(self):
        path = filedialog.askopenfilename(
            filetypes=[("Images", "*.png *.jpg *.jpeg *.bmp"), ("All", "*.*")])
        if path:
            self.set_image(Image.open(path))

    def paste_image(self):
        try:
            from PIL import ImageGrab
            img = ImageGrab.grabclipboard()
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Clipboard", str(exc))
            return
        if isinstance(img, Image.Image):
            self.set_image(img)
        else:
            messagebox.showinfo("Clipboard", "No image found in clipboard.")

    def set_image(self, img):
        self.view.set_image(img)
        self.box_w.set("")
        self.box_h.set("")
        self.grid_var.set(False)
        self.view.show_grid = False
        self.say("Loaded image %dx%d. Scroll to zoom, right-drag to pan."
                 % (img.width, img.height))
        self.view.box_mode = self.box_mode.get()
        self.say("Step 1: drag a box over many tiles and type its size.")
        self._on_points_changed()

    # ---------- clicking ----------
    def undo(self):
        self.view.undo()

    def clear_points(self):
        self.view.clear()

    def build_profile(self):
        """Clicked tiles -> (heights, column offsets).

        Gaps are allowed: solve.cu carries a per-sample column offset (cCol),
        so columns the player skipped -- ground cut by a cave, built over, or
        just hidden behind a tree -- simply are not scored. The samples still
        have to sit inside one MAX_PROFILE-wide window, because the kernel
        matches them against a single rolling history of that width.
        """
        cols = self.view.tile_coords()
        if not cols or len(cols) < 2:
            return None
        keys = sorted(cols)
        lo, hi = keys[0], keys[-1]
        span = hi - lo + 1
        if span > MAX_PROFILE:
            self.say("Selection spans %d columns; the solver matches at most "
                     "%d. Narrow it or drop the far-out points."
                     % (span, MAX_PROFILE))
            return None
        heights = [cols[c] for c in keys]
        offsets = [c - lo for c in keys]
        return heights, offsets

    def solve(self):
        if not os.path.exists(SOLVER):
            messagebox.showerror("Solver missing",
                                 "solve.exe not found. Build it with:\n"
                                 "nvcc -O3 -arch=sm_89 -o solve.exe solve.cu")
            return
        prof = self.build_profile()
        if prof is None:
            messagebox.showwarning(
                "Need more clicks",
                "Click or drag along the ground to mark tiles. Gaps are fine - "
                "skip anything you are unsure of. About 25-30 columns of HILLY "
                "ground works well.")
            return
        heights, offsets = prof

        chosen = WORLD_SIZES[self.size_var.get()]
        if chosen is None:
            # "Don't know": try each size in turn. A wrong size yields no match
            # rather than a wrong seed, so this is safe, just 3x the work.
            sizes = [(8400, 2400), (6400, 1800), (4200, 1200)]
        else:
            sizes = [chosen]
        mx, my = sizes[0]
        cfg = {
            "maxTilesX": mx,
            "maxTilesY": my,
            "ftw": int(self.ftw.get()),
            "drunk": int(self.drunk.get()),
            "remix": int(self.remix.get()),
            "dontStarve": int(self.dont_starve.get()),
            "heights": heights,
            "treeStyles": sorted(self.tree_picker.selected),
            "bgStyles": sorted(self.bg_picker.selected),
            "treeOrder": self.tree_order.value,
            "bgOrder": self.bg_order.value,
            # 0 = base tier, 1 = alternate, -1 = not seen.
            "ores": [{"Skip": -1, base.capitalize(): 0, alt.capitalize(): 1}[
                         self.ore_vars[key].get()]
                     for key, base, alt in ORE_TIERS],
            "moon": self.moon_picker.value if self.moon_picker.value is not None else -1,
            "dungeonSide": {"Skip": -1, "Left": 0, "Right": 1}[self.side_var.get()],
            "oceanBG": self.ocean_picker.value if self.ocean_picker.value is not None else -1,
            # Later worldgen passes (grass, biomes) shift the surface by a tile,
            # so exact matching rejects the true seed. Validated against two
            # real world files: +-1 tile with a couple of misses allowed.
            # Leave tol/maxMiss out so solve.exe applies its measured
            # defaults: later worldgen passes (SmoothWorld) nudge individual
            # columns by a tile, and a hardcoded 2 rejects the true seed on
            # any profile longer than about 25 columns.
            "tol": 1,
            "evilLeft": ({"Skip": -1, "Left": 1, "Right": 0}[self.evil_var.get()]
                         if self.drunk.get() else -1),
            # 0 sweeps the whole space however many candidates turn up.
            "maxHits": 100 if self.early_stop.get() else 0,
        }
        # Only send explicit columns when the selection actually has gaps;
        # solve.cu assumes contiguous when the array is absent or mismatched.
        if offsets != list(range(len(offsets))):
            cfg["columns"] = offsets
        # The search window is size-dependent, so _write_cfg sets it per size.
        self._pending_sizes = sizes[1:]
        self._base_cfg = cfg
        # "try all 3" reruns the solver per size; remember which one produced
        # the results currently on screen so the tables can name it.
        self._multi_size = chosen is None
        self._cur_size = (mx, my)
        path = self._write_cfg(cfg, mx, my)

        nonflat = sum(1 for i in range(1, len(heights))
                      if heights[i] != heights[i - 1])
        span = offsets[-1] + 1
        gaps = span - len(offsets)
        self.say("")
        flags = "".join(", " + n for n, k in (("FTW", "ftw"), ("Drunk", "drunk"),
                                              ("Remix", "remix"),
                                              ("Constant", "dontStarve"))
                        if cfg[k])
        self.say("Solving: %d columns (%d non-flat steps), %dx%d%s%s"
                 % (len(heights), nonflat, mx, my, flags,
                    ", %d gap%s across a %d-wide span"
                    % (gaps, "" if gaps == 1 else "s", span) if gaps else ""))
        clues = [n for n, v in (("ocean", cfg["oceanBG"]), ("moon", cfg["moon"]),
                                ("side", cfg["dungeonSide"]), ("evil", cfg["evilLeft"]))
                 if v >= 0]
        if cfg.get("treeStyles"):
            n_ord = sum(1 for v in cfg.get("treeOrder", []) if v >= 0)
            clues.append("tree x%d%s" % (len(cfg["treeStyles"]),
                                         " (%d ordered)" % n_ord if n_ord else ""))
        if cfg.get("bgStyles"):
            n_ord = sum(1 for v in cfg.get("bgOrder", []) if v >= 0)
            clues.append("bg x%d%s" % (len(cfg["bgStyles"]),
                                       " (%d ordered)" % n_ord if n_ord else ""))
        if len(heights) >= 80:
            self.say("Long profile: a seed will also be accepted on an unbroken "
                     "run of %d matching columns, so one unmodelled surface "
                     "feature (a mountain-cave mound) in the window will not "
                     "reject the true seed." % (len(heights) * 2 // 5))
        n_ore = sum(1 for v in cfg.get("ores", []) if v >= 0)
        if n_ore:
            clues.append("ore x%d" % n_ore)
        clues += [k for k in BIOME_STYLES if cfg.get(k + "BG", -1) >= 0]
        self.say("Clues used: %s" % (", ".join(clues) if clues else "terrain only"))
        if nonflat < 8:
            self.say("WARNING: very flat selection, expect many false matches.")

        self.run_btn.state(["disabled"])
        self.progress["value"] = 0
        self.show_candidates([])
        threading.Thread(target=self._run_solver, args=(path,), daemon=True).start()

    def _write_cfg(self, cfg, mx, my):
        cfg = dict(cfg)
        cfg["maxTilesX"], cfg["maxTilesY"] = mx, my
        for key, pk in self.biome_pickers.items():
            cfg[key + "BG"] = pk.value if pk.value is not None else -1

        # The slider is in absolute tiles, but "try all 3" re-runs this for each
        # world width. A window chosen against 8400 means nothing on a 4200-wide
        # world, so carry it across as a FRACTION of the world and rescale.
        lo, hi = self.win_slider.get()
        smax = float(self.win_slider.maximum) or 1.0
        cfg["winLo"] = int(round(lo / smax * mx))
        cfg["winHi"] = int(round(hi / smax * mx))
        cfg["winHi"] = max(cfg["winHi"], cfg["winLo"] + 1)
        path = os.path.join(HERE, "_gui_config.json")
        with open(path, "w") as fh:
            json.dump(cfg, fh)
        # Keep the screenshot and the click geometry alongside the config so a
        # failed run can be diagnosed by drawing the detected surface back onto
        # the original image.
        try:
            if self.view.image is not None:
                self.view.image.save(os.path.join(HERE, "_gui_image.png"))
            cs = self.view.cell_size()
            dbg = {
                "points": self.view.points,
                "fit": self.view.fit,
                "cell": cs,
                "box": self.view.box,
                "box_cols": self.view.box_cols,
                "box_rows": self.view.box_rows,
            }
            with open(os.path.join(HERE, "_gui_debug.json"), "w") as dh:
                json.dump(dbg, dh)
        except Exception as exc:  # noqa: BLE001 - debug aid must never block a run
            self.say("(could not save debug snapshot: %s)" % exc)
        return path

    def _run_solver(self, cfg_path):
        try:
            self.proc = subprocess.Popen(
                [SOLVER, cfg_path], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            found = []
            near = []
            for line in self.proc.stdout:
                line = line.strip()
                if line.startswith("P "):
                    parts = line.split()
                    frac = float(parts[1])
                    self.after(0, lambda f=frac: self.progress.configure(value=f))
                    if len(parts) >= 6:
                        hits = int(parts[2])
                        rate = float(parts[3])
                        elapsed = float(parts[4])
                        eta = float(parts[5])
                        msg = ("  %5.1f%%  %s seeds/s  elapsed %s  ETA %s%s"
                               % (frac * 100.0, _si(rate), _hms(elapsed),
                                  _hms(eta),
                                  "  hits %d" % hits if hits else ""))
                        self.after(0, lambda m=msg: self.say(m))
                elif line.startswith("S "):
                    seed = int(line.split()[1])
                    found.append(seed)
                    # With the early stop off a run can report thousands of
                    # seeds; printing every one floods the log and makes the
                    # progress lines unreadable. Show the first PRINT_LIMIT as
                    # they arrive and leave the rest to the final count.
                    if len(found) <= PRINT_LIMIT:
                        self.after(0,
                                   lambda s=seed: self.say("  candidate seed: %d" % s))
                    elif len(found) == PRINT_LIMIT + 1:
                        self.after(0, lambda: self.say(
                            "  ... more than %d candidates, listing suppressed "
                            "until the run finishes." % PRINT_LIMIT))
                    # Keep the side pane current. These arrive in seed order,
                    # not quality order; the ranked list replaces them when the
                    # near-miss block arrives at the end.
                    if len(found) <= CAND_SHOW or len(found) % 500 == 0:
                        self.after(0, lambda f=list(found): self.show_candidates(
                            f[:CAND_SHOW], len(f)))
                elif line.startswith("D "):
                    n = int(line.split()[1])
                    self._last_near = near
                    self.after(0, lambda n=n: self._done(n))
                elif line.startswith("B "):
                    # Start of the near-miss block; clear in place so the
                    # reference handed to _done stays the same list.
                    del near[:]
                elif line.startswith("C "):
                    # seed misses offset n0 n1 n2 n3 n4plus
                    p_ = line.split()
                    near.append(tuple(int(x) for x in p_[1:]))
                    # The near-miss block is ranked best-first, so it is a
                    # better leaderboard than the seed-ordered hit list, and it
                    # carries the run/offset/error columns worth showing.
                    self.after(0, lambda n=list(near), t=len(found):
                               self.show_candidates(n, t if t else None))
                elif line.startswith("!"):
                    self.after(0, lambda t=line: self.say(t))
                elif line.startswith("R "):
                    self.after(0, lambda t=line: self.say("  " + t[2:]))
        except Exception as exc:  # noqa: BLE001
            self.after(0, lambda e=exc: self.say("Solver failed: %s" % e))
        finally:
            self.after(0, lambda: self.run_btn.state(["!disabled"]))

    def _done(self, n):
        self.progress["value"] = 1.0
        if n == 0 and getattr(self, "_pending_sizes", None):
            mx, my = self._pending_sizes.pop(0)
            self._cur_size = (mx, my)
            self.say("No match at that size; trying %dx%d "
                     "(search window rescaled)..." % (mx, my))
            path = self._write_cfg(self._base_cfg, mx, my)
            self.progress["value"] = 0
            threading.Thread(target=self._run_solver, args=(path,),
                             daemon=True).start()
            return
        if n == 0:
            if getattr(self, "_multi_size", False):
                self.say("No seed matched at any of the 3 world sizes. Check the "
                         "flags, and make sure the ground clicks are exact and "
                         "gap-free.")
            else:
                self.say("No seed matched. Check the world size and flags, and make "
                         "sure the ground clicks are exact and gap-free.")
            self._show_near_misses()
        elif n == 1:
            self.say("Done: exactly one seed matches%s." % self._size_tag())
            # Show the breakdown here too: a single hit is exactly when it is
            # worth seeing how well it actually fits, since a lone match on a
            # weak profile can still be a coincidence.
            self._show_near_misses(matched=True)
        else:
            self.say("Done: %d candidates%s. Add a clue or click more hilly "
                     "ground to narrow it down." % (n, self._size_tag()))
            self._show_near_misses()

    def _size_tag(self):
        """Return " at 6400x1800" while several sizes are being tried, else "".

        With a single chosen size the caller already knows it, so naming it
        every time is noise. Under "try all 3" it is the one thing the tables
        cannot be read without: a seed is only meaningful together with the
        world size it was found at, since the width changes the RNG draw
        sequence and therefore the terrain.
        """
        if not getattr(self, "_multi_size", False):
            return ""
        mx, my = getattr(self, "_cur_size", (0, 0))
        return " at %dx%d" % (mx, my)

    def _show_near_misses(self, matched=False):
        """Report the scored seeds and how well each fits.

        The per-column error spread is the useful part: if the best seed is
        mostly exact with a few +-1s, the clues are right and the tolerance or
        a couple of misclicked tiles are to blame. If even the best seed has
        columns off by 3 or more, the profile does not belong to this search
        (wrong world size, wrong region, or ground that has been built on).

        Printed after a successful run too, where it answers a different
        question: how convincingly the winning seed beat the runners-up.
        """
        near = getattr(self, "_last_near", None)
        if not near:
            return
        total = sum(near[0][3:])
        self.say("")
        self.say("%s (of %d columns clicked)%s:"
                 % ("Match quality" if matched else "Closest seeds", total,
                    self._size_tag()))
        self.say("      seed    run   at x   exact   +-1   +-2   +-3   worse")
        for seed, run, at, n0, n1, n2, n3, n4 in near:
            self.say("  %11d  %4d  %6d  %6d  %4d  %4d  %4d  %6d"
                     % (seed, run, at, n0, n1, n2, n3, n4))
        best = near[0]
        if matched:
            # The gap between the winner and the next seed is what says whether
            # the match is solid or just the least-bad of a weak field.
            if len(near) > 1:
                lead = best[3] - near[1][3]
                if lead >= 5:
                    self.say("The match is clear: %d more exact columns than the "
                             "next best seed." % lead)
                else:
                    self.say("Only %d exact column(s) ahead of the next seed, so "
                             "add a clue or click more ground to be sure." % lead)
            return
        if best[7] == 0 and best[6] == 0:
            self.say("The best seed is within 2 tiles everywhere, so it is worth "
                     "raising the tolerance by 1 and rerunning.")
        elif best[3] < total // 3:
            self.say("Even the best seed misses most columns, so the search is "
                     "probably in the wrong place: check world size, and that the "
                     "x range covers where the screenshot was taken.")

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            self.say("Stopped.")


def main():
    root = tk.Tk()
    root.title("Terraria seed cracker")
    root.geometry("1400x900")
    try:
        ttk.Style().theme_use("vista")
    except tk.TclError:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
