# -*- mode: python ; coding: utf-8 -*-
# Empacotamento do Bob Music com PyInstaller (Windows e macOS).
#   Windows: build\build-windows.ps1
#   macOS:   build/build-mac.sh
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

ROOT = Path(SPECPATH).parent
VENDOR = Path(SPECPATH) / "vendor"
IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform == "win32"

datas = [
    (str(ROOT / "app" / "web"), "app/web"),
    (str(ROOT / "app" / "assets"), "app/assets"),
]
binaries = []
hiddenimports = ["app.server", "app.apple", "app.youtube"]

# pacotes com dados/submódulos carregados dinamicamente
for pkg in ["spotdl", "ytmusicapi", "pykakasi", "yt_dlp", "yt_dlp_ejs", "syncedlyrics",
            "spotipy", "spotipyFree", "soundcloud", "webview", "uvicorn", "mutagen"]:
    try:
        d, b, h = collect_all(pkg)
    except Exception:
        continue
    datas += d
    binaries += b
    hiddenimports += h

# ffmpeg embutido (build/vendor/ffmpeg[.exe]); sem ele o app baixa sozinho na 1ª vez
ffmpeg = VENDOR / ("ffmpeg.exe" if IS_WIN else "ffmpeg")
if ffmpeg.exists():
    binaries.append((str(ffmpeg), "ffmpeg"))
    lic = VENDOR / "FFMPEG-LICENSE.txt"
    if lic.exists():
        datas.append((str(lic), "ffmpeg"))

a = Analysis(
    [str(ROOT / "run.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "matplotlib", "PyQt5", "PyQt6", "PySide2", "PySide6", "gi"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Bob Music",
    console=False,
    icon=str(ROOT / "app" / "assets" / ("bobmusic.icns" if IS_MAC else "bobmusic.ico")),
    upx=False,
    target_arch=None,
)

coll = COLLECT(exe, a.binaries, a.datas, name="Bob Music", upx=False)

if IS_MAC:
    app = BUNDLE(
        coll,
        name="Bob Music.app",
        icon=str(ROOT / "app" / "assets" / "bobmusic.icns"),
        bundle_identifier="com.bobmusic.app",
        info_plist={
            "CFBundleDisplayName": "Bob Music",
            "CFBundleShortVersionString": "1.1.0",
            "CFBundleVersion": "1.1.0",
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
        },
    )
