"""Windows printing via the standard print dialog and GDI (ctypes only, no extra packages)."""
import ctypes
from ctypes import wintypes as wt

import pymupdf

comdlg32 = ctypes.WinDLL("comdlg32")
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32")
user32 = ctypes.WinDLL("user32")
ole32 = ctypes.WinDLL("ole32")

PD_NOSELECTION = 0x4
PD_PAGENUMS = 0x2
PD_RETURNDC = 0x100
PD_USEDEVMODECOPIESANDCOLLATE = 0x40000
PD_CURRENTPAGE = 0x400000
PD_HIDEPRINTTOFILE = 0x100000  # "Microsoft Print to PDF" covers that need
PD_RESULT_PRINT = 1
START_PAGE_GENERAL = 0xFFFFFFFF
GA_ROOT = 2
HORZRES, VERTRES = 8, 10
HALFTONE = 4
SRCCOPY = 0x00CC0020
ERROR_CANCELLED = 1223
MAX_DPI = 300  # render resolution cap; the printer driver scales up from here


class Cancelled(Exception):
    pass


class PRINTPAGERANGE(ctypes.Structure):
    _fields_ = [("nFromPage", wt.DWORD), ("nToPage", wt.DWORD)]


class PRINTDLGEXW(ctypes.Structure):
    _fields_ = [
        ("lStructSize", wt.DWORD), ("hwndOwner", wt.HWND),
        ("hDevMode", wt.HGLOBAL), ("hDevNames", wt.HGLOBAL), ("hDC", wt.HDC),
        ("Flags", wt.DWORD), ("Flags2", wt.DWORD), ("ExclusionFlags", wt.DWORD),
        ("nPageRanges", wt.DWORD), ("nMaxPageRanges", wt.DWORD),
        ("lpPageRanges", ctypes.POINTER(PRINTPAGERANGE)),
        ("nMinPage", wt.DWORD), ("nMaxPage", wt.DWORD), ("nCopies", wt.DWORD),
        ("hInstance", wt.HINSTANCE), ("lpPrintTemplateName", wt.LPCWSTR),
        ("lpCallback", ctypes.c_void_p), ("nPropertyPages", wt.DWORD),
        ("lphPropertyPages", ctypes.c_void_p), ("nStartPage", wt.DWORD),
        ("dwResultAction", wt.DWORD),
    ]


class DOCINFOW(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_int), ("lpszDocName", wt.LPCWSTR), ("lpszOutput", wt.LPCWSTR),
                ("lpszDatatype", wt.LPCWSTR), ("fwType", wt.DWORD)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG),
                ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]


comdlg32.PrintDlgExW.argtypes = [ctypes.POINTER(PRINTDLGEXW)]
comdlg32.PrintDlgExW.restype = ctypes.c_long
kernel32.GlobalFree.argtypes = [wt.HGLOBAL]
kernel32.GlobalFree.restype = wt.HGLOBAL
user32.GetAncestor.argtypes = [wt.HWND, wt.UINT]
user32.GetAncestor.restype = wt.HWND
gdi32.CreateDCW.argtypes = [wt.LPCWSTR, wt.LPCWSTR, wt.LPCWSTR, ctypes.c_void_p]
gdi32.CreateDCW.restype = wt.HDC
gdi32.GetDeviceCaps.argtypes = [wt.HDC, ctypes.c_int]
gdi32.StartDocW.argtypes = [wt.HDC, ctypes.POINTER(DOCINFOW)]
for fn in (gdi32.StartPage, gdi32.EndPage, gdi32.EndDoc, gdi32.AbortDoc, gdi32.DeleteDC):
    fn.argtypes = [wt.HDC]
gdi32.SetStretchBltMode.argtypes = [wt.HDC, ctypes.c_int]
gdi32.SetBrushOrgEx.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
gdi32.StretchDIBits.argtypes = [wt.HDC] + [ctypes.c_int] * 8 + [
    ctypes.c_void_p, ctypes.POINTER(BITMAPINFOHEADER), wt.UINT, wt.DWORD]


def ask(hwnd, page_count, current_page):
    """Show the Windows print dialog.

    Returns (printer DC, list of 0-based page numbers), or None if cancelled.
    """
    ole32.CoInitialize(None)
    ranges = (PRINTPAGERANGE * 64)()
    pd = PRINTDLGEXW()
    pd.lStructSize = ctypes.sizeof(pd)
    pd.hwndOwner = user32.GetAncestor(hwnd, GA_ROOT)
    pd.Flags = PD_RETURNDC | PD_NOSELECTION | PD_USEDEVMODECOPIESANDCOLLATE | PD_HIDEPRINTTOFILE
    pd.nMaxPageRanges = len(ranges)
    pd.lpPageRanges = ranges
    pd.nMinPage, pd.nMaxPage, pd.nCopies = 1, page_count, 1
    pd.nStartPage = START_PAGE_GENERAL
    hr = comdlg32.PrintDlgExW(ctypes.byref(pd))
    for handle in (pd.hDevMode, pd.hDevNames):
        if handle:
            kernel32.GlobalFree(handle)
    if hr != 0:
        raise OSError(f"The print dialog failed (error 0x{hr & 0xFFFFFFFF:08X}).")
    if pd.dwResultAction != PD_RESULT_PRINT or not pd.hDC:
        if pd.hDC:
            gdi32.DeleteDC(pd.hDC)
        return None

    if pd.Flags & PD_CURRENTPAGE:
        pages = [current_page]
    elif pd.Flags & PD_PAGENUMS:
        pages = []
        for r in ranges[:pd.nPageRanges]:
            lo, hi = sorted((r.nFromPage, r.nToPage))
            pages.extend(range(max(lo, 1) - 1, min(hi, page_count)))
    else:
        pages = list(range(page_count))
    if not pages:
        gdi32.DeleteDC(pd.hDC)
        return None
    return pd.hDC, pages


class PrintJob:
    """One print job on a printer DC. Takes ownership of the DC."""

    def __init__(self, hdc, title, output=None):
        self.hdc = hdc
        info = DOCINFOW(ctypes.sizeof(DOCINFOW), title, output, None, 0)
        if gdi32.StartDocW(hdc, ctypes.byref(info)) <= 0:
            err = ctypes.get_last_error()
            gdi32.DeleteDC(hdc)
            if err == ERROR_CANCELLED:  # e.g. "Save as" of Print to PDF was cancelled
                raise Cancelled()
            raise OSError(f"Could not start the print job (error {err}).")
        self.width = gdi32.GetDeviceCaps(hdc, HORZRES)    # printable area in device pixels
        self.height = gdi32.GetDeviceCaps(hdc, VERTRES)
        gdi32.SetStretchBltMode(hdc, HALFTONE)
        gdi32.SetBrushOrgEx(hdc, 0, 0, None)

    def add_page(self, page):
        """Print a page scaled to fit the paper, turned 90 degrees if that fits better."""
        rect = page.rect
        rotate = (rect.width > rect.height) != (self.width > self.height)
        pw, ph = (rect.height, rect.width) if rotate else (rect.width, rect.height)
        fit = min(self.width / pw, self.height / ph)
        dest_w, dest_h = int(pw * fit), int(ph * fit)
        scale = min(fit, MAX_DPI / 72)
        matrix = pymupdf.Matrix(scale, scale)
        if rotate:
            matrix.prerotate(90)
        pix = pymupdf.Pixmap(page.get_pixmap(matrix=matrix, alpha=False), 1)  # RGBA rows are 4-byte aligned
        buf = bytearray(pix.samples)
        buf[0::4], buf[2::4] = buf[2::4], buf[0::4]  # RGBA -> BGRA for the DIB
        header = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), pix.width, -pix.height, 1, 32)
        bits = (ctypes.c_char * len(buf)).from_buffer(buf)

        if gdi32.StartPage(self.hdc) <= 0:
            raise OSError("The printer refused a new page.")
        gdi32.StretchDIBits(self.hdc, (self.width - dest_w) // 2, (self.height - dest_h) // 2,
                            dest_w, dest_h, 0, 0, pix.width, pix.height,
                            ctypes.addressof(bits), ctypes.byref(header), 0, SRCCOPY)
        if gdi32.EndPage(self.hdc) <= 0:
            raise OSError("Sending the page to the printer failed.")

    def finish(self):
        gdi32.EndDoc(self.hdc)
        gdi32.DeleteDC(self.hdc)

    def abort(self):
        gdi32.AbortDoc(self.hdc)
        gdi32.DeleteDC(self.hdc)
