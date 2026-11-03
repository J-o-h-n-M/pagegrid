# PageGrid

A small, fast PDF reader for Windows (Python + MuPDF + Tk). It shows pages in a grid,
and **Ctrl + mouse wheel** changes how many pages fit on each row.

## Use

Build it (see below) and run `dist\PageGrid.exe`, or `PageGrid.exe file.pdf`. The exe is one
self-contained file you can copy anywhere. To make it your default PDF viewer, right-click a PDF →
*Open with* → *Choose another app* → browse to `PageGrid.exe`.

| Action | Input |
|---|---|
| Fewer / more pages per row (zoom) | Ctrl + wheel, Ctrl + `+` / `-`, toolbar `+` / `−` |
| Magnify a single page (up to 400%) | keep zooming in at 1 page per row |
| Set pages per row directly | keys `1`–`9` |
| Fit a whole page in the window | Ctrl + `0`, *Fit page* |
| Scroll | wheel, arrows, drag with the left mouse button; Shift + wheel scrolls sideways |
| Next / previous screen of rows | PageDown / PageUp, Space / Shift + Space |
| First / last page | Home / End |
| Go to a page | type it in the page box (Ctrl + G), then Enter |
| Open | Ctrl + O, or click the empty window |
| Print (printer, pages, copies) | Ctrl + P, *Print* |
| Full screen | F11 (Esc to leave) |

It also opens XPS, EPUB, CBZ and FB2 files. Your pages-per-row setting and window size are saved
in `%APPDATA%\PageGrid\config.json`.

## Build

`build.bat` builds the single `dist\PageGrid.exe` in its own virtual environment (`.buildenv`),
so nothing else installed on your PC gets bundled. The single exe unpacks itself on every launch,
so it takes about 1.5 s to open a file. `build.bat folder` builds `dist\PageGrid\PageGrid.exe`
plus an `_internal` folder instead, which starts in about 0.3 s; move that whole folder together.

To run from source: `pip install pymupdf`, then `python pagegrid.py [file.pdf]`.

## License

The PageGrid source code in this repository is released under the [MIT License](LICENSE).

PageGrid uses [PyMuPDF](https://github.com/pymupdf/PyMuPDF) and the MuPDF library, which are
licensed under the [GNU AGPL-3.0](https://www.gnu.org/licenses/agpl-3.0.html) (or a commercial
license from Artifex). The built `PageGrid.exe` contains them, so if you distribute the exe,
the AGPL-3.0 terms apply to that build, including making its source code available.
