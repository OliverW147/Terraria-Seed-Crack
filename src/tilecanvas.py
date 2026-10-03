"""Interactive screenshot canvas: zoom, pan, and self-calibrating tile clicks.

The player clicks surface tiles in any order and at any spacing. Terraria tiles
are square and axis-aligned, so the clicks lie on a regular lattice; fitting
that lattice recovers both the tile size and the grid origin without asking the
user to calibrate anything by hand.

Fit method: candidate tile sizes are scored by how tightly every click's X and
Y snap to a common grid. Terraria renders at 16 px/tile times an integer-ish
zoom, so the search runs over a continuous range and keeps the best residual.
Each click is then drawn tinted green (snaps cleanly) through red (far off the
fitted grid), which makes a misclick obvious immediately.
"""
import math
import tkinter as tk

# Terraria's native tile is 16 px; screenshots are usually an integer multiple
# but windowed/UI scaling can land between, so search continuously.
MIN_TILE_PX = 6.0
MAX_TILE_PX = 96.0


def _alignment(values, size):
    """How well values line up on a lattice of the given size.

    Uses circular variance of the fractional positions, which needs no offset
    guess: 0.0 means every value sits exactly on a grid line, 1.0 means they
    are spread uniformly.
    """
    if size <= 0 or not values:
        return 1.0
    sx = sy = 0.0
    for v in values:
        a = 2.0 * math.pi * ((v / size) % 1.0)
        sx += math.cos(a)
        sy += math.sin(a)
    return 1.0 - math.hypot(sx, sy) / len(values)


def _residual(values, size, offset):
    """Mean absolute distance from values to the nearest grid line."""
    if size <= 0:
        return 1e9
    total = 0.0
    for v in values:
        d = (v - offset) / size
        total += abs(d - round(d))
    return total / max(1, len(values))


def fit_grid(points, hint=None):
    """Fit tile size and origin to clicked points.

    Returns (size, ox, oy, residual) or None when there is too little data.
    Residual is in tile units: 0 = perfect, 0.5 = worst possible.
    """
    if len(points) < 2:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]

    # Sweep tile size directly and score by lattice alignment. Any SUB-multiple
    # of the true size aligns just as well (every multiple of 16 is a multiple
    # of 8), so among all sizes that align near-optimally take the LARGEST --
    # that is the real tile. Sizes larger than the truth do not align at all.
    step = 0.02
    scores = []
    s = MIN_TILE_PX
    while s <= MAX_TILE_PX:
        scores.append((s, 0.5 * (_alignment(xs, s) + _alignment(ys, s))))
        s += step
    if not scores:
        return None
    best = min(sc for _s, sc in scores)
    viable = [s for s, sc in scores if sc <= best + 0.02]
    if hint:
        # A manual override wins outright when it aligns acceptably.
        h = 0.5 * (_alignment(xs, hint) + _alignment(ys, hint))
        if h <= best + 0.06:
            viable = [hint]
    size = max(viable)
    ox = _best_offset(xs, size)
    oy = _best_offset(ys, size)
    res = 0.5 * (_residual(xs, size, ox) + _residual(ys, size, oy))
    return size, ox, oy, res


def _best_offset(values, size):
    """Circular mean of the fractional positions, mapped back to pixels."""
    sx = sy = 0.0
    for v in values:
        ang = 2.0 * math.pi * ((v / size) % 1.0)
        sx += math.cos(ang)
        sy += math.sin(ang)
    ang = math.atan2(sy, sx)
    if ang < 0:
        ang += 2.0 * math.pi
    return (ang / (2.0 * math.pi)) * size


def error_colour(err):
    """Green (0) through yellow to red (>=0.35 tiles off the grid)."""
    t = max(0.0, min(1.0, err / 0.35))
    if t < 0.5:
        r = int(255 * (t / 0.5))
        g = 200
    else:
        r = 255
        g = int(200 * (1.0 - (t - 0.5) / 0.5))
    return "#%02x%02x%02x" % (r, g, 60)


class TileCanvas(tk.Frame):
    """Screenshot view with wheel zoom, right-drag pan and tile-snapped clicks."""

    def __init__(self, parent, on_change=None):
        super().__init__(parent)
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.canvas = tk.Canvas(self, bg="#20242b", highlightthickness=0,
                                cursor="crosshair")
        self.canvas.grid(row=0, column=0, sticky="nsew")

        self.on_change = on_change
        self.image = None          # full-res PIL image
        self._photo = None
        self.zoom = 1.0
        self.pan = [0.0, 0.0]      # top-left of view in image coords
        self.points = []           # image-space clicks
        self.fit = None           # (size, ox, oy, residual)
        self.manual_size = None   # cell width (x); see self.cell
        self.cell = None          # (width, height) in px, independent axes
        self.have_cols = False
        self.have_rows = False
        self.show_grid = False
        self._drag = None
        # Calibration box: drag a rectangle over a known number of tiles. This
        # beats clicking for precision because any pixel error in the two
        # corners is divided by the tile count.
        self.box_mode = False
        self.box = None            # (x0, y0, x1, y1) in image coords
        self._box_drag = None
        self._handle = None        # which box corner is being dragged
        self.box_cols = None       # tiles wide, as told by the user
        self.box_rows = None
        self._paint = None         # last painted point, image coords
        self._paint_moved = False

        c = self.canvas
        c.bind("<Button-1>", self._press)
        c.bind("<B1-Motion>", self._motion)
        c.bind("<ButtonRelease-1>", self._release)
        c.bind("<Button-3>", self._pan_start)
        c.bind("<B3-Motion>", self._pan_move)
        c.bind("<ButtonRelease-3>", self._pan_end)
        c.bind("<MouseWheel>", self._wheel)
        c.bind("<Configure>", lambda e: self.redraw())
        c.bind("<Motion>", self._hover)

    # ---------- coordinate helpers ----------
    def to_image(self, sx, sy):
        return (self.pan[0] + sx / self.zoom, self.pan[1] + sy / self.zoom)

    def to_screen(self, ix, iy):
        return ((ix - self.pan[0]) * self.zoom, (iy - self.pan[1]) * self.zoom)

    # ---------- image ----------
    def set_image(self, img):
        self.image = img.convert("RGB")
        self.points = []
        self.fit = None
        # A calibration belongs to the screenshot it was drawn on, so drop the
        # box too; otherwise a stale grid is drawn over the new image.
        self.box = None
        self.box_cols = None
        self.box_rows = None
        self.manual_size = None
        self.cell = None
        self._box_origin = None
        self._box_drag = None
        self._handle = None
        self.zoom = 1.0
        self.pan = [0.0, 0.0]
        self.fit_view()

    def fit_view(self):
        if self.image is None:
            return
        cw = max(1, self.canvas.winfo_width())
        ch = max(1, self.canvas.winfo_height())
        self.zoom = min(cw / self.image.width, ch / self.image.height)
        self.pan = [(self.image.width - cw / self.zoom) / 2.0,
                    (self.image.height - ch / self.zoom) / 2.0]
        self.redraw()

    # ---------- interaction ----------
    def _wheel(self, ev):
        if self.image is None:
            return "break"
        before = self.to_image(ev.x, ev.y)
        factor = 1.18 if ev.delta > 0 else 1 / 1.18
        self.zoom = max(0.05, min(60.0, self.zoom * factor))
        after = self.to_image(ev.x, ev.y)
        self.pan[0] += before[0] - after[0]
        self.pan[1] += before[1] - after[1]
        self.redraw()
        return "break"

    def _pan_start(self, ev):
        self._drag = (ev.x, ev.y)
        self.canvas.configure(cursor="fleur")

    def _pan_move(self, ev):
        if not self._drag:
            return
        dx = (ev.x - self._drag[0]) / self.zoom
        dy = (ev.y - self._drag[1]) / self.zoom
        self.pan[0] -= dx
        self.pan[1] -= dy
        self._drag = (ev.x, ev.y)
        self.redraw()

    def _pan_end(self, _ev):
        self._drag = None
        self.canvas.configure(cursor="crosshair")

    def _press(self, ev):
        # Corner handles stay live whenever the box is visible, so the grid can
        # be nudged without leaving click mode.
        h = self._hit_handle(ev.x, ev.y)
        if h is not None:
            self._handle = h
            return
        if self.box_mode:
            ix, iy = self.to_image(ev.x, ev.y)
            self._box_drag = (ix, iy)
            self.box = (ix, iy, ix, iy)
            self.redraw()
            return
        # A press that turns into a drag paints a run of columns; a press that
        # does not is an ordinary click, including the click-again-to-remove
        # behaviour. That decision is deferred to _release.
        self._paint = None
        self._paint_moved = False
        self._add_point(ev)

    def _hit_handle(self, sx, sy, radius=9):
        """Which box corner (if any) is under the cursor, in screen pixels."""
        if not self.box:
            return None
        x0, y0, x1, y1 = self.box
        corners = ((x0, y0), (x1, y0), (x0, y1), (x1, y1))
        for i, (cx, cy) in enumerate(corners):
            hx, hy = self.to_screen(cx, cy)
            if abs(hx - sx) <= radius and abs(hy - sy) <= radius:
                return i
        return None

    def _motion(self, ev):
        if self._handle is not None:
            ix, iy = self.to_image(ev.x, ev.y)
            x0, y0, x1, y1 = self.box
            # Corners 0..3 are TL, TR, BL, BR; dragging one moves the two edges
            # it touches and leaves the opposite corner anchored.
            if self._handle in (0, 2):
                x0 = ix
            else:
                x1 = ix
            if self._handle in (0, 1):
                y0 = iy
            else:
                y1 = iy
            self.box = (x0, y0, x1, y1)
            self.apply_box()       # live recalibration while dragging
            return
        if self.box_mode and self._box_drag:
            ix, iy = self.to_image(ev.x, ev.y)
            self.box = (self._box_drag[0], self._box_drag[1], ix, iy)
            self.redraw()
            return
        # Drag-to-paint: one point per tile column, following the cursor.
        self._paint_to(ev)

    def _paint_to(self, ev):
        """Mark every column between the last painted point and this one.

        Motion events fire every few pixels, which at normal zoom can skip
        whole columns, so the gap is walked rather than sampled: dragging once
        across 50 columns must leave 50 points, not a scattering of them.
        """
        if self.image is None:
            return
        size = self.tile_size()
        if not size:
            return                      # no lattice yet, nothing to snap to
        ix, iy = self.to_image(ev.x, ev.y)
        self._paint_moved = True
        prev = self._paint
        pts = []
        if prev is not None and abs(ix - prev[0]) >= size:
            span = int(abs(ix - prev[0]) / size)
            step = 1.0 if ix > prev[0] else -1.0
            for i in range(1, span + 1):
                t = i / float(span)
                pts.append((prev[0] + step * i * size,
                            prev[1] + (iy - prev[1]) * t))
        else:
            pts.append((ix, iy))
        for p in pts:
            self._set_column(p[0], p[1])
        self._paint = pts[-1] if pts else (ix, iy)
        self.refit()

    def _set_column(self, ix, iy):
        """Store one point for this column, replacing whatever was there.

        tile_coords() already collapses duplicates per column, but leaving
        them in self.points makes the lattice fit and the error tints read
        from stale clicks, so they are dropped here instead.
        """
        fit = self.fit
        size = self.tile_size()
        if not size:
            self.points.append((ix, iy))
            return
        ox = fit[1] if fit else 0.0
        col = int(round((ix - ox) / size))
        keep = []
        for (px, py) in self.points:
            if int(round((px - ox) / size)) != col:
                keep.append((px, py))
        keep.append((ix, iy))
        self.points = keep

    def _release(self, ev):
        if self._handle is not None:
            self._handle = None
            self.apply_box()
            return
        if self.box_mode and self._box_drag:
            self._box_drag = None
            self.apply_box()
            return
        self._paint = None
        self._paint_moved = False

    def apply_box(self):
        """Derive the grid from the box and its tile counts.

        Width and height are handled INDEPENDENTLY: the box gives cell width
        from w/cols and cell height from h/rows. Combining them (by averaging
        or least squares) is wrong whenever the box is not square in tiles --
        for a 20x5 box, w/20 and h/5 are different numbers. Keeping the axes
        separate makes the grid land exactly on the box edges either way.
        """
        if not self.box:
            return
        x0, y0, x1, y1 = self.box
        w = abs(x1 - x0)
        h = abs(y1 - y0)
        sw = (w / float(self.box_cols)) if (self.box_cols and w > 2) else None
        sh = (h / float(self.box_rows)) if (self.box_rows and h > 2) else None
        # If only one axis is given, assume square tiles and use it for both.
        if sw is None and sh is None:
            self.redraw()
            if self.on_change:
                self.on_change()
            return
        if sw is None:
            sw = sh
        if sh is None:
            sh = sw
        if sw <= 0.5 or sh <= 0.5:
            return
        self.cell = (sw, sh)
        # Track which axes were actually given, so the overlay does not draw
        # rows from a height that was merely assumed square.
        self.have_cols = bool(self.box_cols and w > 2)
        self.have_rows = bool(self.box_rows and h > 2)
        self.manual_size = sw
        # Box edges are tile BOUNDARIES; centres sit half a cell in from them.
        self._box_origin = ((min(x0, x1) + sw * 0.5) % sw,
                            (min(y0, y1) + sh * 0.5) % sh)
        self.refit()

    def _add_point(self, ev):
        if self.image is None:
            return
        ix, iy = self.to_image(ev.x, ev.y)
        # Remove instead of add when clicking an existing point.
        for i, (px, py) in enumerate(self.points):
            tol = (self.tile_size() or 12) * 0.5
            if abs(px - ix) < tol and abs(py - iy) < tol:
                self.points.pop(i)
                self._paint = (ix, iy)
                self.refit()
                return
        # One point per column, whether it arrives by click or by drag: a
        # second click in a column moves the height rather than adding a
        # rival point at the same x.
        self._set_column(ix, iy)
        self._paint = (ix, iy)
        self.refit()

    def _hover(self, ev):
        if self.image is None:
            return
        self._hover_xy = self.to_image(ev.x, ev.y)
        if self._hit_handle(ev.x, ev.y) is not None:
            self.canvas.configure(cursor="sizing")
        elif self.box_mode:
            self.canvas.configure(cursor="tcross")
        else:
            self.canvas.configure(cursor="crosshair")

    # ---------- calibration ----------
    def cell_size(self):
        """(width, height) of one tile in pixels; axes are independent."""
        if self.cell:
            return self.cell
        sz = self.tile_size()
        return (sz, sz) if sz else None

    def tile_size(self):
        if self.manual_size:
            return self.manual_size
        return self.fit[0] if self.fit else None

    def refit(self):
        """Recompute the lattice.

        The calibration box alone defines the grid: its size and origin come
        from the rectangle and the tile counts typed for it. Clicks are pure
        data entry and never move the grid, so adding or removing a ground
        point can never disturb a calibration that already looks right.
        """
        origin = getattr(self, "_box_origin", None)
        if self.manual_size and origin:
            self.fit = (self.manual_size, origin[0], origin[1], 0.0)
        elif len(self.points) >= 2:
            # No box yet: fall back to fitting the clicks so the tool still
            # works if calibration is skipped entirely.
            self.fit = fit_grid(self.points, hint=self.manual_size)
        else:
            self.fit = None
        self.redraw()
        if self.on_change:
            self.on_change()

    def _point_residual(self, size, origin):
        if not self.points:
            return 0.0
        ox, oy = origin
        tot = 0.0
        for (x, y) in self.points:
            dx = (x - ox) / size
            dy = (y - oy) / size
            tot += 0.5 * (abs(dx - round(dx)) + abs(dy - round(dy)))
        return tot / len(self.points)

    def point_errors(self):
        """Per-click distance (in tiles) from the fitted lattice."""
        size = self.tile_size()
        if not size or not self.fit:
            return [0.0] * len(self.points)
        _s, ox, oy, _r = self.fit
        out = []
        for (x, y) in self.points:
            dx = (x - ox) / size
            dy = (y - oy) / size
            out.append(max(abs(dx - round(dx)), abs(dy - round(dy))))
        return out

    def tile_coords(self):
        """Clicks as integer (col, row), deduplicated by column."""
        cs = self.cell_size()
        if not cs or not self.fit:
            return None
        sw, sh = cs
        _s, ox, oy, _r = self.fit
        cols = {}
        for (x, y) in self.points:
            c = int(round((x - ox) / sw))
            r = int(round((y - oy) / sh))
            cols[c] = r
        return cols

    def clear_box(self):
        self.box = None
        self.manual_size = None
        self._box_origin = None
        self.cell = None
        self.refit()

    def clear(self):
        self.points = []
        self.fit = None
        self.redraw()
        if self.on_change:
            self.on_change()

    def undo(self):
        if self.points:
            self.points.pop()
            self.refit()

    # ---------- drawing ----------
    def redraw(self):
        c = self.canvas
        c.delete("all")
        if self.image is None:
            return
        cw = max(1, c.winfo_width())
        ch = max(1, c.winfo_height())

        # Crop just the visible region then scale it: keeps huge zooms cheap.
        from PIL import Image
        x0 = max(0, int(self.pan[0]))
        y0 = max(0, int(self.pan[1]))
        x1 = min(self.image.width, int(self.pan[0] + cw / self.zoom) + 2)
        y1 = min(self.image.height, int(self.pan[1] + ch / self.zoom) + 2)
        if x1 <= x0 or y1 <= y0:
            return
        crop = self.image.crop((x0, y0, x1, y1))
        tw = max(1, int((x1 - x0) * self.zoom))
        th = max(1, int((y1 - y0) * self.zoom))
        resample = Image.NEAREST if self.zoom >= 1 else Image.LANCZOS
        crop = crop.resize((tw, th), resample)
        from PIL import ImageTk
        self._photo = ImageTk.PhotoImage(crop)
        sx, sy = self.to_screen(x0, y0)
        c.create_image(sx, sy, anchor="nw", image=self._photo)

        if self.box:
            bx0, by0 = self.to_screen(self.box[0], self.box[1])
            bx1, by1 = self.to_screen(self.box[2], self.box[3])
            c.create_rectangle(bx0, by0, bx1, by1, outline="#ffd54f", width=2,
                               dash=(5, 3))
            lbl = []
            if self.box_cols:
                lbl.append("%d wide" % self.box_cols)
            if self.box_rows:
                lbl.append("%d tall" % self.box_rows)
            if lbl:
                c.create_text(min(bx0, bx1) + 4, min(by0, by1) - 10,
                              anchor="w", fill="#ffd54f",
                              text=" x ".join(lbl))
            # Grab handles: drag any corner to stretch the box and recalibrate.
            for (hx, hy) in ((bx0, by0), (bx1, by0), (bx0, by1), (bx1, by1)):
                c.create_rectangle(hx - 5, hy - 5, hx + 5, hy + 5,
                                   fill="#ffd54f", outline="#1a1d22", width=1)

        size = self.tile_size()
        if self.show_grid and size and self.fit:
            self._draw_grid(size, cw, ch)
        self._draw_points(size)

    def _draw_grid(self, size, cw, ch):
        """Draw tile BOUNDARIES, using independent x and y cell sizes."""
        cs = self.cell_size()
        if not cs or not self.fit:
            return
        sw, sh = cs
        _s, ox, oy, _r = self.fit
        if sw * self.zoom < 4 or sh * self.zoom < 4:
            return                      # too dense to be useful
        c = self.canvas
        gx = ox - sw * 0.5              # first boundary left of centre 0
        gy = oy - sh * 0.5
        # Only draw an axis the user has actually specified: before a tile
        # count is typed the spacing for that axis is a guess, not a fact.
        draw_cols = self.have_cols or not self.manual_size
        draw_rows = self.have_rows or not self.manual_size
        x = gx + math.floor((self.pan[0] - gx) / sw) * sw
        while draw_cols:
            sx, _ = self.to_screen(x, 0)
            if sx > cw:
                break
            if sx >= 0:
                c.create_line(sx, 0, sx, ch, fill="#3d84c6", width=1)
            x += sw
        y = gy + math.floor((self.pan[1] - gy) / sh) * sh
        while draw_rows:
            _, sy = self.to_screen(0, y)
            if sy > ch:
                break
            if sy >= 0:
                c.create_line(0, sy, cw, sy, fill="#3d84c6", width=1)
            y += sh

    def _draw_points(self, size):
        c = self.canvas
        errs = self.point_errors()
        ordered = sorted(range(len(self.points)), key=lambda i: self.points[i][0])
        prev = None
        for i in ordered:
            ix, iy = self.points[i]
            colour = "#3ddc84"   # clicks are data entry only; never an error signal
            # Snapped tile box, so it is obvious which tile a click claimed.
            cs = self.cell_size()
            if cs and self.fit:
                sw, sh = cs
                _s, ox, oy, _r = self.fit
                col = round((ix - ox) / sw)
                row = round((iy - oy) / sh)
                bx0, by0 = self.to_screen(ox + (col - 0.5) * sw,
                                          oy + (row - 0.5) * sh)
                bx1, by1 = self.to_screen(ox + (col + 0.5) * sw,
                                          oy + (row + 0.5) * sh)
                c.create_rectangle(bx0, by0, bx1, by1, outline=colour, width=2)
                cx, cy = self.to_screen(ox + col * sw, oy + row * sh)
            else:
                cx, cy = self.to_screen(ix, iy)
            if prev is not None:
                c.create_line(prev[0], prev[1], cx, cy, fill="#ff4081", width=1)
            prev = (cx, cy)
            c.create_oval(cx - 3, cy - 3, cx + 3, cy + 3, fill=colour, outline="#fff")
