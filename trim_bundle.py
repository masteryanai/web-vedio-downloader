"""Remove duplicate media DLLs and unused browser debug resources from our build."""
from __future__ import annotations

import hashlib
from pathlib import Path


def trim() -> None:
    root = Path(__file__).resolve().parent
    release = (root / "dist" / "WebVideoDownloaderPrototype").resolve()
    expected_parent = (root / "dist").resolve()
    if release.parent != expected_parent or not release.is_dir():
        raise RuntimeError("未找到项目内的构建目录。")
    internal = (release / "_internal").resolve()
    if not internal.is_relative_to(release):
        raise RuntimeError("构建目录验证失败。")
    removed = 0

    def remove(file: Path) -> None:
        nonlocal removed
        resolved = file.resolve()
        if not resolved.is_relative_to(internal):
            raise RuntimeError("拒绝修改构建目录之外的文件。")
        if file.is_file():
            removed += file.stat().st_size
            file.unlink()

    # Media tools load the DLLs beside their executable; PyInstaller also copies
    # these to _internal during dependency scanning. Remove only identical copies.
    for original in (internal / "tools").glob("*.dll"):
        duplicate = internal / original.name
        if duplicate.is_file() and hashlib.sha256(original.read_bytes()).digest() == hashlib.sha256(duplicate.read_bytes()).digest():
            remove(duplicate)
    remove(internal / "PySide6" / "resources" / "qtwebengine_devtools_resources.debug.pak")
    locales = internal / "PySide6" / "translations" / "qtwebengine_locales"
    for locale in locales.glob("*.pak"):
        if locale.name not in {"en-US.pak", "zh-CN.pak", "zh-TW.pak"}:
            remove(locale)
    print(f"移除重复及未使用的资源：{removed / 1024 / 1024:.1f} MiB")


if __name__ == "__main__":
    trim()
