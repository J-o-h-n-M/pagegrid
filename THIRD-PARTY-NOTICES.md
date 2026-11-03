# Third-party notices

The PageGrid source code is released under the [MIT License](LICENSE).

The `PageGrid.exe` builds published on the Releases page also contain the components below.
Because PyMuPDF and MuPDF are licensed under the GNU AGPL-3.0, **each PageGrid.exe build as a
whole is distributed under the terms of the [GNU AGPL-3.0](licenses/AGPL-3.0.txt)**. Its complete
corresponding source is this repository (`pagegrid.py`, `winprint.py`, and the build script
`build.bat`) together with the component sources linked below.

| Component | Version | License | Source |
|---|---|---|---|
| PyMuPDF | 1.28.2 | GNU AGPL-3.0 (or Artifex commercial license) | <https://github.com/pymupdf/PyMuPDF/tree/1.28.2> |
| MuPDF (by Artifex Software) | 1.28.2 | GNU AGPL-3.0 (or Artifex commercial license) | <https://github.com/ArtifexSoftware/mupdf/tree/1.28.2> |
| Python | 3.10.11 | [PSF License Version 2](https://docs.python.org/3.10/license.html) | <https://www.python.org/downloads/release/python-31011/> |
| Tcl/Tk | 8.6.12 | [Tcl/Tk license](https://www.tcl.tk/software/tcltk/license.html) (BSD-style) | <https://www.tcl.tk/software/tcltk/download.html> |
| OpenSSL | 1.1.1 | [OpenSSL and SSLeay license](https://www.openssl.org/source/license-openssl-ssleay.txt) | <https://www.openssl.org/source/> |
| PyInstaller bootloader | 6.22.3 | [GPL-2.0-or-later with bootloader exception](https://github.com/pyinstaller/pyinstaller/blob/develop/COPYING.txt) | <https://github.com/pyinstaller/pyinstaller> |
| Microsoft Visual C++ runtime and Universal C Runtime | — | Microsoft redistributable files | — |

Notes:

- MuPDF itself includes further libraries (such as FreeType, HarfBuzz, libjpeg, OpenJPEG,
  jbig2dec, Little CMS and zlib) under their own permissive licenses. They are listed in the
  MuPDF source linked above.
- Python includes libffi, expat, bzip2, xz and mpdecimal. Their licenses are listed in
  [Python's license documentation](https://docs.python.org/3.10/license.html).
- The PyInstaller bootloader exception allows programs built with PyInstaller to be distributed
  under any license.
- PyMuPDF and MuPDF are copyright Artifex Software, Inc. Python is copyright the Python Software
  Foundation. Tcl/Tk is copyright the Regents of the University of California, Sun Microsystems
  and other parties.
