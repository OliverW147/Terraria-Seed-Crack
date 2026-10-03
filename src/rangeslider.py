"""A two-handle range slider for choosing the X band to search.

The player usually knows roughly where in the world their screenshot came from
("somewhere left of centre"), which is far more useful than the fixed thirds a
dropdown offers. Dragging the two handles sets the low and high bound; the dot
marks the centre of the world, as a fixed reference to place them against.
"""
import tkinter as tk


class RangeSlider(tk.Frame):
    def __init__(self, parent, maximum=8400, on_change=None, height=54):
        super().__init__(parent)
        self.maximum = maximum
        self.on_change = on_change
        self.lo = 0
        self.hi = maximum
        self._drag = None

        self.canvas = tk.Canvas(self, height=height, highlightthickness=0,
                                bg=parent.winfo_toplevel().cget("bg"))
        self.canvas.pack(fill="x", expand=True)
        self.canvas.bind("<Configure>", lambda e: self.redraw())
        self.canvas.bind("<Button-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._motion)
        self.canvas.bind("<ButtonRelease-1>", self._release)

    # ---------- geometry ----------
    def _pad(self):
        return 14

    def _x_of(self, value):
        w = max(1, self.canvas.winfo_width() - self._pad() * 2)
        return self._pad() + (value / float(self.maximum)) * w

    def _value_of(self, x):
        w = max(1, self.canvas.winfo_width() - self._pad() * 2)
        v = (x - self._pad()) / w * self.maximum
        return int(max(0, min(self.maximum, v)))

    # ---------- interaction ----------
    def _press(self, ev):
        dl = abs(ev.x - self._x_of(self.lo))
        dh = abs(ev.x - self._x_of(self.hi))
        # Grab whichever handle is nearer; ties go to the one that leaves the
        # range valid when dragged toward the click.
        self._drag = "lo" if dl <= dh else "hi"
        self._motion(ev)

    def _motion(self, ev):
        if not self._drag:
            return
        v = self._value_of(ev.x)
        if self._drag == "lo":
            self.lo = min(v, self.hi - 1)
        else:
            self.hi = max(v, self.lo + 1)
        self.redraw()
        if self.on_change:
            self.on_change(self.lo, self.hi)

    def _release(self, _ev):
        self._drag = None

    # ---------- api ----------
    def set_maximum(self, maximum):
        """Rescale when the world size changes, keeping the band proportional."""
        if maximum == self.maximum:
            return
        frac_lo = self.lo / float(self.maximum)
        frac_hi = self.hi / float(self.maximum)
        self.maximum = maximum
        self.lo = int(frac_lo * maximum)
        self.hi = int(frac_hi * maximum)
        self.redraw()

    def set_range(self, lo, hi):
        self.lo = max(0, min(lo, self.maximum))
        self.hi = max(self.lo + 1, min(hi, self.maximum))
        self.redraw()

    def get(self):
        return self.lo, self.hi

    # ---------- drawing ----------
    def redraw(self):
        c = self.canvas
        c.delete("all")
        w = c.winfo_width()
        if w <= 1:
            return
        y = 26
        pad = self._pad()
        c.create_line(pad, y, w - pad, y, fill="#4a4f5a", width=5,
                      capstyle="round")
        xlo, xhi = self._x_of(self.lo), self._x_of(self.hi)
        c.create_line(xlo, y, xhi, y, fill="#3d84c6", width=5, capstyle="round")

        # The dot marks the centre of the whole track, not the centre of the
        # selected band, so it stays put as a fixed reference while the two
        # handles move around it.
        mid = self.maximum // 2
        xm = self._x_of(mid)
        c.create_oval(xm - 4, y - 4, xm + 4, y + 4, fill="#ffd54f", outline="")

        for x in (xlo, xhi):
            c.create_oval(x - 7, y - 7, x + 7, y + 7, fill="#e8eaf0",
                          outline="#2b2f38", width=2)

        c.create_text(pad, y + 18, anchor="w", fill="#aaa",
                      text="x=%d" % self.lo)
        c.create_text(w - pad, y + 18, anchor="e", fill="#aaa",
                      text="x=%d" % self.hi)
        c.create_text(xm, y - 16, anchor="c", fill="#ffd54f",
                      text="mid %d" % mid)
