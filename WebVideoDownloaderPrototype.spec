# Build a portable folder so the media tools and browser runtime stay replaceable.
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules

root = Path(SPECPATH)
datas = [
    (str(root / "tools"), "tools"),
    (str(root / "vendor"), "vendor"),
    (str(root / "licenses"), "licenses"),
    (str(root / "README.md"), "."),
    (str(root / "LICENSES.md"), "."),
]

analysis = Analysis(
    [str(root / "main.py")],
    pathex=[str(root)],
    binaries=[],
    datas=datas,
    hiddenimports=collect_submodules("yt_dlp.extractor"),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "pytest", "unittest"],
    noarchive=False,
)
# Qt 6.12 uses Windows' unversioned ICU exports. The host's Poppler directory
# exposes a different ICU ABI through PATH; bundling that DLL breaks Qt startup.
analysis.binaries = [
    item for item in analysis.binaries
    if not (
        Path(item[0]).name.lower().startswith("icu")
        and Path(item[0]).suffix.lower() == ".dll"
        and "poppler" in item[1].lower()
    )
]
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="WebVideoDownloaderPrototype",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    version=str(root / "version_info.txt"),
)
collection = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="WebVideoDownloaderPrototype",
)
