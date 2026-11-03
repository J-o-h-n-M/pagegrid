"""PageGrid - a lightweight, fast PDF reader with a multi-page grid view.

Ctrl + mouse wheel zooms. Zooming out puts more pages on each row, zooming in
puts fewer, down to a single page that can then be magnified further.
"""
import bisect
import ctypes
import json
import math
import os
import sys
import time
import tkinter as tk
from collections import OrderedDict
from tkinter import filedialog, messagebox, simpledialog

import pymupdf

import winprint

APP = "PageGrid"
BG = "#2b2b2b"
BAR_BG = "#1e1e1e"
FG = "#dddddd"
MAX_COLS = 24
ZOOMS = (1.0, 1.25, 1.5, 2.0, 2.5, 3.0, 4.0)  # extra magnification at one page per row
TILE = 1024                  # pages are rendered in horizontal strips of this many px
CACHE_PIXELS = 40_000_000    # rendered-image cache budget (~160 MB)
DLIST_CACHE = 48             # parsed pages kept around for fast re-rendering
FRAME_BUDGET = 0.012         # seconds of rendering per UI tick
WHEEL_STEP = 100             # px scrolled per wheel notch
# File types MuPDF opens, grouped for the Open dialog (the first group is the default filter).
FILE_GROUPS = [
    ("PDF files", "pdf"),
    ("E-books and documents", "epub mobi fb2 xps oxps cbz"),
    ("Images", "png jpg jpeg bmp gif tif tiff jp2 jpx jxr pnm pbm pgm ppm pam psd svg"),
    ("Text and HTML", "txt html htm xhtml"),
]
CONFIG = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), APP, "config.json")


def load_config():
    try:
        with open(CONFIG, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_config(cfg):
    try:
        os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
        with open(CONFIG, "w", encoding="utf-8") as f:
            json.dump(cfg, f)
    except OSError:
        pass


def gap_for(cols):
    return 12 if cols <= 3 else 8 if cols <= 8 else 4


class Reader:
    def __init__(self, root, path=None):
        self.root = root
        self.doc = None
        self.path = None
        self.print_job = None
        self.print_pages, self.print_done = [], 0
        self.sizes = []        # page (width, height) in points
        self.boxes = []        # page (x, y, w, h) in canvas px
        self.row_tops, self.row_bottoms, self.row_starts = [], [], []
        self.content_w = self.content_h = 0
        self.eff_cols, self.gap, self.last_width = 1, 12, 0
        self.cur_page = 0

        self.cfg = load_config()
        self.cols = self.cfg.get("cols")   # None = fit a whole page on first open
        self.zi = 0

        self.drawn = set()               # pages whose background is on the canvas
        self.items = {}                  # (page, strip) -> canvas image id
        self.visible_tiles = set()
        self.wanted = []                 # tiles still to render, most important first
        self.cache = OrderedDict()       # (page, strip) -> (width, PhotoImage, pixels)
        self.cache_px = 0
        self.dlists = OrderedDict()      # page -> DisplayList

        self.pump_id = self.refresh_id = self.resize_id = None
        self.wheel_acc = 0.0
        self.zoom_acc = 0
        self.fullscreen = False

        self._build_ui()
        self._bind()
        if path:
            self.root.after(1, lambda: self.open(path))
        else:
            self.root.after(1, self._draw_hint)

    # ---------------------------------------------------------------- UI
    def _build_ui(self):
        r = self.root
        r.title(APP)
        r.configure(bg=BG)
        scale = r.winfo_fpixels("1i") / 96
        r.geometry(self.cfg.get("geometry") or f"{int(1200 * scale)}x{int(850 * scale)}")
        if self.cfg.get("maximized"):
            r.state("zoomed")
        r.protocol("WM_DELETE_WINDOW", self.quit)

        font = ("Segoe UI", 10)
        self.bar = bar = tk.Frame(r, bg=BAR_BG)
        bar.pack(side="top", fill="x")
        style = dict(bg=BAR_BG, fg=FG, activebackground="#3a3a3a", activeforeground="#fff",
                     bd=0, relief="flat", padx=10, pady=4, highlightthickness=0, font=font)
        tk.Button(bar, text="Open", command=self.open_dialog, **style).pack(side="left")
        tk.Button(bar, text="Print", command=self.print_dialog, **style).pack(side="left")
        tk.Button(bar, text="−", command=lambda: self.zoom(-1), **style).pack(side="left")
        tk.Button(bar, text="+", command=lambda: self.zoom(1), **style).pack(side="left")
        tk.Button(bar, text="Fit page", command=self.fit_page, **style).pack(side="left")

        self.page_var = tk.StringVar()
        self.page_entry = tk.Entry(bar, textvariable=self.page_var, width=6, justify="center",
                                   bg="#333", fg="#fff", insertbackground="#fff",
                                   relief="flat", font=font)
        self.page_entry.pack(side="left", padx=(14, 4), pady=4)
        self.page_entry.bind("<Return>", self._goto_entry)
        self.page_entry.bind("<Escape>", lambda e: self.canvas.focus_set())
        self.total_lbl = tk.Label(bar, text="/ 0", bg=BAR_BG, fg=FG, font=font)
        self.total_lbl.pack(side="left")
        self.info_lbl = tk.Label(bar, text="", bg=BAR_BG, fg="#999", font=font)
        self.info_lbl.pack(side="right", padx=10)

        frame = tk.Frame(r, bg=BG)
        frame.pack(fill="both", expand=True)
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        self.canvas = c = tk.Canvas(frame, bg=BG, highlightthickness=0, bd=0,
                                    xscrollincrement=1, yscrollincrement=1)
        self.vbar = tk.Scrollbar(frame, orient="vertical", command=c.yview)
        self.hbar = tk.Scrollbar(frame, orient="horizontal", command=c.xview)
        c.configure(yscrollcommand=self._yscroll, xscrollcommand=self._xscroll)
        c.grid(row=0, column=0, sticky="nsew")
        self.vbar.grid(row=0, column=1, sticky="ns")
        self.hbar.grid(row=1, column=0, sticky="ew")
        self.hbar.grid_remove()

    def _bind(self):
        r, c = self.root, self.canvas
        c.bind("<Configure>", self._on_configure)
        c.bind("<ButtonPress-1>", self._on_press)
        c.bind("<B1-Motion>", lambda e: c.scan_dragto(e.x, e.y, gain=1))
        c.bind("<ButtonRelease-1>", lambda e: c.configure(cursor=""))
        r.bind_all("<MouseWheel>", self._on_wheel)

        keys = {
            "<Control-o>": lambda e: self.open_dialog(),
            "<Control-p>": lambda e: self.print_dialog(),
            "<Control-q>": lambda e: self.quit(),
            "<Control-g>": lambda e: (self.page_entry.focus_set(), self.page_entry.select_range(0, "end")),
            "<Control-plus>": lambda e: self.zoom(1),
            "<Control-equal>": lambda e: self.zoom(1),
            "<Control-KP_Add>": lambda e: self.zoom(1),
            "<Control-minus>": lambda e: self.zoom(-1),
            "<Control-KP_Subtract>": lambda e: self.zoom(-1),
            "<Control-0>": lambda e: self.fit_page(),
            "<Next>": lambda e: self.page_step(1),
            "<Prior>": lambda e: self.page_step(-1),
            "<space>": lambda e: self.page_step(1),
            "<Shift-space>": lambda e: self.page_step(-1),
            "<Down>": lambda e: c.yview_scroll(60, "units"),
            "<Up>": lambda e: c.yview_scroll(-60, "units"),
            "<Right>": lambda e: self._horizontal(1),
            "<Left>": lambda e: self._horizontal(-1),
            "<Home>": lambda e: c.yview_moveto(0),
            "<End>": lambda e: c.yview_moveto(1),
            "<F11>": lambda e: self.toggle_fullscreen(),
            "<Escape>": lambda e: self.fullscreen and self.toggle_fullscreen(),
        }
        for n in range(1, 10):
            keys[f"<Key-{n}>"] = lambda e, n=n: self.set_cols(n)
        for seq, fn in keys.items():
            r.bind(seq, self._guard(fn))

    def _guard(self, fn):
        """Ignore shortcuts while typing a page number."""
        def handler(e):
            if e.widget is self.page_entry and not (e.state & 0x4 or e.keysym == "F11"):
                return None
            fn(e)
            return "break"
        return handler

    def _draw_hint(self):
        c = self.canvas
        c.delete("all")
        c.create_text(c.winfo_width() / 2, c.winfo_height() / 2, fill="#888",
                      font=("Segoe UI", 14), justify="center",
                      text="Click or press Ctrl+O to open a PDF\n\n"
                           "Ctrl + mouse wheel: more / fewer pages per row\n"
                           "1-9: pages per row   ·   Ctrl+0: fit page   ·   F11: full screen")

    # ------------------------------------------------------------ files
    def open_dialog(self):
        groups = [(name, " ".join(f"*.{ext}" for ext in exts.split())) for name, exts in FILE_GROUPS]
        path = filedialog.askopenfilename(
            parent=self.root, initialdir=self.cfg.get("last_dir") or None,
            filetypes=[("All supported files", " ".join(p for _, p in groups)), *groups,
                       ("All files", "*.*")])
        if path:
            self.open(path)

    def open(self, path):
        if self.print_job:
            messagebox.showinfo(APP, "Please wait until printing has finished.", parent=self.root)
            return
        try:
            doc = pymupdf.open(path)
        except Exception as exc:
            messagebox.showerror(APP, f"Could not open\n{path}\n\n{exc}", parent=self.root)
            return
        if doc.needs_pass:
            while True:
                pw = simpledialog.askstring(APP, "This PDF is password protected:", show="*",
                                            parent=self.root)
                if pw is None:
                    doc.close()
                    return
                if doc.authenticate(pw):
                    break
        if doc.page_count == 0:
            messagebox.showerror(APP, "This document has no pages.", parent=self.root)
            doc.close()
            return

        if self.doc:
            self.doc.close()
        self.doc = doc
        self.path = path
        self.sizes =[(p.rect.width, p.rect.height) for p in doc]
        self._reset_render_state()
        self.root.title(f"{os.path.basename(path)} — {APP}")
        self.total_lbl.config(text=f"/ {doc.page_count}")
        self.cfg["last_dir"] = os.path.dirname(os.path.abspath(path))
        save_config(self.cfg)

        self.root.update_idletasks()
        self.zi = 0
        if self.cols is None:
            self.cols = self._fit_cols(0)
        self.canvas.delete("all")
        self._relayout()
        self.canvas.xview_moveto(0)
        self.canvas.yview_moveto(0)
        self._schedule_refresh()

    def _reset_render_state(self):
        if self.pump_id:
            self.root.after_cancel(self.pump_id)
            self.pump_id = None
        self.drawn.clear()
        self.items.clear()
        self.visible_tiles.clear()
        self.wanted = []
        self.cache.clear()
        self.cache_px = 0
        self.dlists.clear()

    def quit(self):
        if self.print_job:
            if not messagebox.askyesno(APP, "Printing is not finished. Cancel it and quit?", parent=self.root):
                return
            self.print_job.abort()
            self.print_job = None
        maximized = self.root.state() == "zoomed"
        self.cfg["maximized"] = maximized
        if not maximized and not self.fullscreen:
            self.cfg["geometry"] = self.root.geometry()
        save_config(self.cfg)
        self.root.destroy()

    # ----------------------------------------------------------- layout
    def _relayout(self):
        """Place every page on a grid of self.cols columns filling the window width."""
        n = len(self.sizes)
        vw = max(self.canvas.winfo_width(), 50)
        self.last_width = vw
        cols = self.eff_cols = max(1, min(self.cols, n))
        zoom = ZOOMS[self.zi] if cols == 1 else 1.0
        gap = self.gap = gap_for(cols)
        cell = max(16.0, (vw - gap * (cols + 1)) / cols * zoom)
        grid_w = cell * cols + gap * (cols + 1)
        x0 = max(0.0, (vw - grid_w) / 2)

        boxes, tops, bottoms, starts = [], [], [], []
        y = gap
        w = int(cell)
        for start in range(0, n, cols):
            heights = [cell * self.sizes[i][1] / self.sizes[i][0] for i in range(start, min(start + cols, n))]
            row_h = max(heights)
            tops.append(y)
            starts.append(start)
            for j, h in enumerate(heights):
                x = x0 + gap + j * (cell + gap)
                boxes.append((int(x), int(y + (row_h - h) / 2), w, max(1, round(h))))
            y += row_h
            bottoms.append(y)
            y += gap

        self.boxes, self.row_tops, self.row_bottoms, self.row_starts = boxes, tops, bottoms, starts
        self.content_w = int(grid_w + 2 * x0)
        self.content_h = int(y)
        self.canvas.delete("all")
        self.drawn.clear()
        self.items.clear()
        self.wanted = []
        self.canvas.configure(scrollregion=(0, 0, max(self.content_w, vw), self.content_h))

    def _row_end(self, r):
        return self.row_starts[r + 1] if r + 1 < len(self.row_starts) else len(self.sizes)

    def _fit_cols(self, page):
        """Fewest columns at which a whole page fits in the window."""
        vw = max(self.canvas.winfo_width(), 50)
        vh = max(self.canvas.winfo_height(), 50)
        pw, ph = self.sizes[page]
        maxc = min(MAX_COLS, len(self.sizes))
        for c in range(1, maxc + 1):
            g = gap_for(c)
            if (vw - g * (c + 1)) / c * ph / pw + 2 * g <= vh:
                return c
        return maxc

    # ------------------------------------------------------------- zoom
    def zoom(self, direction, ax=None, ay=None):
        """direction > 0 zooms in (fewer pages per row), < 0 zooms out."""
        if not self.doc:
            return
        cols, zi = self.eff_cols, self.zi
        if direction > 0:
            if cols > 1:
                cols -= 1
            elif zi < len(ZOOMS) - 1:
                zi += 1
        else:
            if zi > 0:
                zi -= 1
            elif cols < min(MAX_COLS, len(self.sizes)):
                cols += 1
        self._set_view(cols, zi, ax, ay)

    def set_cols(self, cols):
        if self.doc:
            self._set_view(min(cols, len(self.sizes)), 0)

    def fit_page(self):
        if self.doc:
            page = self.cur_page
            self._set_view(self._fit_cols(page), 0)
            self.goto(page)

    def _set_view(self, cols, zi, ax=None, ay=None):
        if (cols, zi) == (self.eff_cols, self.zi):
            return
        if ax is None:
            ax, ay = self.canvas.winfo_width() / 2, self.canvas.winfo_height() / 2
        anchor = self._anchor_at(ax, ay)
        self.cols, self.zi = cols, zi
        self.cfg["cols"] = cols
        self._relayout()
        self._restore_anchor(anchor, ax, ay)
        self._schedule_refresh()

    def _anchor_at(self, ax, ay):
        """The page under a window point, and where on that page the point is."""
        cx, cy = self.canvas.canvasx(ax), self.canvas.canvasy(ay)
        r = min(max(bisect.bisect_right(self.row_tops, cy) - 1, 0), len(self.row_tops) - 1)
        page = min(range(self.row_starts[r], self._row_end(r)),
                   key=lambda i: abs(self.boxes[i][0] + self.boxes[i][2] / 2 - cx))
        x, y, w, h = self.boxes[page]
        return page, min(max((cx - x) / w, 0), 1), min(max((cy - y) / h, 0), 1)

    def _restore_anchor(self, anchor, ax, ay):
        page, fx, fy = anchor
        x, y, w, h = self.boxes[page]
        self.canvas.xview_moveto(max(0, x + fx * w - ax) / max(self.content_w, self.last_width))
        self.canvas.yview_moveto(max(0, y + fy * h - ay) / self.content_h)

    # ------------------------------------------------------- navigation
    def goto(self, page):
        page = min(max(page, 0), len(self.sizes) - 1)
        row = page // self.eff_cols
        self.canvas.yview_moveto(max(0, self.row_tops[row] - self.gap) / self.content_h)
        if self.content_w > self.last_width:
            self.canvas.xview_moveto(max(0, self.boxes[page][0] - self.gap) / self.content_w)

    def _goto_entry(self, _e):
        try:
            page = int(self.page_var.get()) - 1
        except ValueError:
            page = self.cur_page
        self.canvas.focus_set()
        if self.doc:
            self.goto(page)

    def page_step(self, d):
        """Scroll by whole rows of pages, or by most of a screen if a row is taller."""
        if not self.doc:
            return
        c = self.canvas
        top, vh = c.canvasy(0), c.winfo_height()
        nrows = len(self.row_tops)
        r = min(bisect.bisect_right(self.row_bottoms, top + 1), nrows - 1)
        if self.row_bottoms[r] - self.row_tops[r] + self.gap > vh:
            c.yview_scroll(int(d * vh * 0.9), "units")
            return
        k = 0
        while r + k < nrows and self.row_bottoms[r + k] <= top + vh + 1:
            k += 1
        k = max(k, 1)
        if d > 0:
            target = r + k
            if target >= nrows:
                c.yview_moveto(1)
                return
        else:
            aligned = self.row_tops[r] >= top - 1
            target = max(0, r - k if aligned else r - k + 1)
        c.yview_moveto(max(0, self.row_tops[target] - self.gap) / self.content_h)

    def _horizontal(self, d):
        if self.content_w > self.last_width:
            self.canvas.xview_scroll(d * 60, "units")
        else:
            self.page_step(d)

    def toggle_fullscreen(self):
        self.fullscreen = not self.fullscreen
        self.root.attributes("-fullscreen", self.fullscreen)
        if self.fullscreen:
            self.bar.pack_forget()
        else:
            self.bar.pack(side="top", fill="x", before=self.canvas.master)

    # ----------------------------------------------------------- events
    def _on_wheel(self, e):
        c = self.canvas
        x, y = e.x_root - c.winfo_rootx(), e.y_root - c.winfo_rooty()
        if not (0 <= x < c.winfo_width() and 0 <= y < c.winfo_height()):
            return
        if e.state & 0x4:  # Ctrl: zoom. Touchpad pinches send small deltas, so accumulate.
            if (self.zoom_acc > 0) != (e.delta > 0):
                self.zoom_acc = 0
            self.zoom_acc += e.delta
            while abs(self.zoom_acc) >= 120:
                step = 1 if self.zoom_acc > 0 else -1
                self.zoom_acc -= 120 * step
                self.zoom(step, x, y)
            return "break"
        self.wheel_acc += -e.delta / 120 * WHEEL_STEP
        n = int(self.wheel_acc)
        self.wheel_acc -= n
        if e.state & 0x1:
            c.xview_scroll(n, "units")
        else:
            c.yview_scroll(n, "units")
        return "break"

    def _on_press(self, e):
        self.canvas.focus_set()
        if not self.doc:
            self.open_dialog()
            return
        self.canvas.configure(cursor="fleur")
        self.canvas.scan_mark(e.x, e.y)

    def _on_configure(self, e):
        if not self.doc:
            self._draw_hint()
        elif e.width != self.last_width:
            if self.resize_id:
                self.root.after_cancel(self.resize_id)
            self.resize_id = self.root.after(60, self._resize_relayout)
        else:
            self._schedule_refresh()

    def _resize_relayout(self):
        self.resize_id = None
        anchor = self._anchor_at(self.canvas.winfo_width() / 2, 0)
        self._relayout()
        self._restore_anchor(anchor, self.canvas.winfo_width() / 2, 0)
        self._schedule_refresh()

    def _yscroll(self, lo, hi):
        self.vbar.set(lo, hi)
        self._schedule_refresh()

    def _xscroll(self, lo, hi):
        self.hbar.set(lo, hi)
        if float(lo) <= 0 and float(hi) >= 1:
            self.hbar.grid_remove()
        else:
            self.hbar.grid()
        self._schedule_refresh()

    # -------------------------------------------------------- rendering
    def _schedule_refresh(self):
        if self.refresh_id is None and self.doc:
            self.refresh_id = self.root.after_idle(self._refresh)

    def _refresh(self):
        """Draw what is (nearly) visible, drop what is not, and queue missing renders."""
        self.refresh_id = None
        if not self.doc or not self.boxes:
            return
        c = self.canvas
        top, vh = c.canvasy(0), c.winfo_height()
        bottom = top + vh
        lo, hi = top - vh / 2, bottom + vh / 2
        center = (top + bottom) / 2
        r0 = bisect.bisect_right(self.row_bottoms, lo)
        r1 = bisect.bisect_left(self.row_tops, hi)
        pages = range(self.row_starts[r0], self._row_end(r1 - 1)) if r0 < r1 else range(0)

        visible, want = set(), []
        for p in pages:
            x, y, w, h = self.boxes[p]
            if p not in self.drawn:
                c.create_rectangle(x, y, x + w, y + h, fill="white", outline="", tags=f"p{p}")
                self.drawn.add(p)
            for s in range(math.ceil(h / TILE)):
                ty0 = y + s * TILE
                ty1 = min(y + h, ty0 + TILE)
                if ty1 < lo or ty0 > hi:
                    continue
                key = (p, s)
                visible.add(key)
                if key in self.items:
                    continue
                cached = self.cache.get(key)
                if cached and cached[0] == w:
                    self.cache.move_to_end(key)
                    self.items[key] = c.create_image(x, ty0, image=cached[1], anchor="nw", tags=f"p{p}")
                else:
                    want.append((abs((ty0 + ty1) / 2 - center), key))

        page_set = set(pages)
        for p in [p for p in self.drawn if p not in page_set]:
            c.delete(f"p{p}")
            self.drawn.discard(p)
        for key in [k for k in self.items if k not in visible]:
            c.delete(self.items.pop(key))

        want.sort()
        self.wanted = [k for _, k in want]
        self.visible_tiles = visible
        if self.wanted and self.pump_id is None:
            self.pump_id = self.root.after(1, self._pump)
        self._update_status(top, bottom)

    def _pump(self):
        """Render queued tiles in small time slices so the UI stays responsive."""
        self.pump_id = None
        start = time.perf_counter()
        while self.wanted and time.perf_counter() - start < FRAME_BUDGET:
            key = self.wanted.pop(0)
            if key in self.items or key not in self.visible_tiles:
                continue
            p, s = key
            x, y, w, h = self.boxes[p]
            try:
                img = self._render(p, s, w, h)
            except Exception:
                continue  # damaged page: leave it blank
            self._cache_put(key, w, img)
            self.items[key] = self.canvas.create_image(x, y + s * TILE, image=img, anchor="nw", tags=f"p{p}")
        if self.wanted:
            self.pump_id = self.root.after(1, self._pump)

    def _render(self, p, s, w, h):
        dl = self.dlists.get(p)
        if dl is None:
            dl = self.dlists[p] = self.doc.load_page(p).get_displaylist()
            if len(self.dlists) > DLIST_CACHE:
                self.dlists.popitem(last=False)
        else:
            self.dlists.move_to_end(p)
        rect = dl.rect
        scale = w / rect.width
        y0, y1 = s * TILE, min(h, (s + 1) * TILE)
        clip = pymupdf.Rect(rect.x0, rect.y0 + y0 / scale, rect.x1, rect.y0 + y1 / scale)
        pix = dl.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False, clip=clip)
        return tk.PhotoImage(master=self.root, data=pix.tobytes("ppm"))

    def _cache_put(self, key, w, img):
        old = self.cache.pop(key, None)
        if old:
            self.cache_px -= old[2]
        px = img.width() * img.height()
        self.cache[key] = (w, img, px)
        self.cache_px += px
        if self.cache_px > CACHE_PIXELS:
            for k in list(self.cache):
                if self.cache_px <= CACHE_PIXELS:
                    break
                if k not in self.items:  # never drop an image that is on screen
                    self.cache_px -= self.cache.pop(k)[2]

    def _update_status(self, top, bottom):
        nrows = len(self.row_tops)
        r = min(bisect.bisect_right(self.row_bottoms, top), nrows - 1)
        # Count the top row as current only if a good part of it is still visible.
        if r + 1 < nrows and self.row_bottoms[r] - top < 0.3 * (self.row_bottoms[r] - self.row_tops[r]):
            r += 1
        last_row = max(r, min(bisect.bisect_left(self.row_tops, bottom) - 1, nrows - 1))
        self.cur_page = self.row_starts[r]
        last = self._row_end(last_row)
        if self.root.focus_get() is not self.page_entry:
            self.page_var.set(str(self.cur_page + 1))
        text = f"pages {self.cur_page + 1}–{last}" if last - 1 > self.cur_page else f"page {last}"
        text += f"   ·   {self.eff_cols} per row"
        if self.eff_cols == 1 and self.zi:
            text += f"   ·   {int(ZOOMS[self.zi] * 100)}%"
        if self.print_job:
            text = f"printing {self.print_done + 1} of {len(self.print_pages)}   ·   " + text
        self.info_lbl.config(text=text)

    # --------------------------------------------------------- printing
    def print_dialog(self):
        if not self.doc or self.print_job:
            return
        try:
            choice = winprint.ask(self.root.winfo_id(), len(self.sizes), self.cur_page)
            if not choice:
                return
            hdc, pages = choice
            self.print_job = winprint.PrintJob(hdc, os.path.basename(self.path))
        except winprint.Cancelled:
            return
        except OSError as exc:
            messagebox.showerror(APP, f"Printing failed.\n\n{exc}", parent=self.root)
            return
        self.print_pages, self.print_done = pages, 0
        self.root.after(1, self._print_next)

    def _print_next(self):
        """Print one page per tick so the window stays responsive."""
        job = self.print_job
        if not job:
            return
        if self.print_done == len(self.print_pages):
            self.print_job = None
            job.finish()
        else:
            try:
                job.add_page(self.doc.load_page(self.print_pages[self.print_done]))
            except Exception as exc:
                self.print_job = None
                job.abort()
                messagebox.showerror(APP, f"Printing failed.\n\n{exc}", parent=self.root)
            else:
                self.print_done += 1
                self.root.after(1, self._print_next)
        self._schedule_refresh()


def main():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # sharp rendering on scaled displays
    except Exception:
        pass
    root = tk.Tk()
    Reader(root, sys.argv[1] if len(sys.argv) > 1 else None)
    root.mainloop()


if __name__ == "__main__":
    main()
