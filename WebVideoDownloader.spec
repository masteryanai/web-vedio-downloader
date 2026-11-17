# Build a portable folder so the media tools and browser runtime stay replaceable.
from pathlib import Path
import json
import sys
import os
sys.path.insert(0, str(Path(SPECPATH)))
from build_guard import check_output
product_name='WebVideoDownloaderLite' if os.environ.get('WVD_PRODUCT')=='lite' else 'WebVideoDownloader'
check_output(Path(DISTPATH) / product_name)
from PyInstaller.utils.hooks import collect_submodules

root = Path(SPECPATH)
from product_identity import PRODUCT,EDITION,PRODUCT_ID,APP_NAME,render_script
generated=root/'build'/'product-resources'/(PRODUCT+'-'+EDITION)
generated.mkdir(parents=True,exist_ok=True)
(generated/'product.json').write_text(json.dumps({'product':PRODUCT,'edition':EDITION}),encoding='utf-8')
scripts=('update_helper.ps1','update_install.ps1','update_window.ps1','recovery.ps1','recovery.cmd','recovery-root.cmd')
for name in scripts:(generated/name).write_text(render_script((root/name).read_text(encoding='utf-8-sig')),encoding='utf-8-sig' if name.endswith('.ps1') else 'utf-8')
(generated/'version_info.txt').write_text((root/'version_info.txt').read_text(encoding='utf-8-sig').replace('WebVideoDownloader',PRODUCT_ID).replace('Web Video Downloader',APP_NAME),encoding='utf-8')
datas = [
    (str(generated/'product.json'),'.'),
    (str(root / "sites.json"), "."),
    *[(str(generated/name),'.') for name in scripts],
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
    hiddenimports=collect_submodules("yt_dlp.extractor") + [row["handler"].split(":")[0] for row in json.loads((root/"sites.json").read_text(encoding="utf-8"))],
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
    name=PRODUCT_ID,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    version=str(generated / "version_info.txt"),
)
check_output(Path(DISTPATH) / PRODUCT_ID)
collection = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name=PRODUCT_ID,
)
