"""Reject a stale frozen executable before a release can be packaged."""
from pathlib import Path
import marshal
from PyInstaller.archive.readers import CArchiveReader


def verify(root):
    bundle = root / 'dist' / 'WebVideoDownloader'
    archive = CArchiveReader(str(bundle / 'WebVideoDownloader.exe'))
    pyz = archive.open_embedded_archive(next(name for name in archive.toc if name.startswith('PYZ')))
    checked = []
    for source in sorted(root.glob('*.py')):
        name = source.stem
        if name not in pyz.toc:
            continue  # Build-only helpers are intentionally not runtime modules.
        code = pyz.extract(name)
        expected = compile(source.read_text(encoding='utf-8-sig'), code.co_filename,
                           'exec', dont_inherit=True)
        if code != expected:
            raise RuntimeError(f'冻结模块与源码不一致：{name}；请清洁构建后重试。')
        checked.append(name)
    main = marshal.loads(archive.extract('main'))
    expected = compile((root / 'main.py').read_text(encoding='utf-8-sig'),
                       main.co_filename, 'exec', dont_inherit=True)
    if main != expected:
        raise RuntimeError('冻结入口与源码不一致：main')
    for name in ('update_install.ps1', 'update_window.ps1', 'recovery.ps1'):
        if (bundle / '_internal' / name).read_bytes() != (root / name).read_bytes():
            raise RuntimeError(f'安装资源与源码不一致：{name}')
    print(f'冻结入口、{len(checked)} 个本项目模块与安装资源均与源码一致。')


if __name__ == '__main__':
    verify(Path(__file__).resolve().parent)
