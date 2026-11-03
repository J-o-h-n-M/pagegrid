@echo off
rem Builds a single dist\PageGrid.exe.
rem Pass "folder" to get dist\PageGrid\PageGrid.exe plus an _internal folder instead
rem (starts faster, because a single exe unpacks itself on every launch):  build.bat folder
rem Uses its own clean virtual environment so nothing else installed on this PC gets bundled.
cd /d "%~dp0"

if not exist .buildenv\Scripts\python.exe (
  python -m venv .buildenv || exit /b 1
)
.buildenv\Scripts\python.exe -m pip install --quiet --upgrade pip pymupdf pyinstaller || exit /b 1

set MODE=--onefile
if /i "%1"=="folder" set MODE=--onedir

.buildenv\Scripts\python.exe -m PyInstaller --noconfirm --clean --windowed %MODE% --name PageGrid ^
  --workpath .buildenv\work --specpath .buildenv pagegrid.py || exit /b 1

echo.
if /i "%1"=="folder" (echo Built dist\PageGrid\PageGrid.exe) else (echo Built dist\PageGrid.exe)
