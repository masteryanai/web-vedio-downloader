"""Shared paths for the portable Windows client and its bundled tools."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import sys

from product_identity import PRODUCT,PRODUCT_ID,APP_NAME,UPDATE_MANIFEST,HISTORY_MANIFEST,IS_LITE,EXECUTABLE
APP_VERSION = "0.16.0"
SITES = ("央视网", "好看视频", "抖音", "B站")


def resource_path(*parts: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base.joinpath(*parts)


def find_tool(name: str) -> str:
    suffix = ".exe" if os.name == "nt" else ""
    bundled = resource_path("tools", name + suffix)
    if bundled.is_file():
        return str(bundled)
    installed = shutil.which(name)
    if installed:
        return installed
    raise RuntimeError(f"缺少 {name}，请使用完整的程序文件夹。")


def default_download_dir() -> Path:
    return Path.home() / "Downloads" / PRODUCT_ID


def program_dir() -> Path:
    return Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent


def legacy_data_dir() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / (PRODUCT_ID+'Prototype')


def app_data_dir() -> Path:
    # Explicit override is for isolated tests/development; never a silent fallback.
    override = os.environ.get('WVD_DATA_DIR')
    if override:
        path = Path(override)
        if not path.is_absolute():raise ValueError('WVD_DATA_DIR 必须使用绝对路径。')
        return path
    return program_dir() / ('data-lite' if IS_LITE and not getattr(sys,'frozen',False) else 'data')

