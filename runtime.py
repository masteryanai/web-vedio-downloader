"""Paths shared by the Windows prototype and its bundled tools."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import sys

APP_NAME = "Web Video Downloader"
APP_VERSION = "0.2.1"
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
    raise RuntimeError(f"缺少 {name}，请使用完整的原型程序文件夹。")


def default_download_dir() -> Path:
    return Path.home() / "Downloads" / "WebVideoDownloader"


def app_data_dir() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "WebVideoDownloaderPrototype"
