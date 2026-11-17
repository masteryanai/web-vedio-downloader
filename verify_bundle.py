"""Reject a stale frozen executable before a release can be packaged."""
from pathlib import Path
import marshal
from PyInstaller.archive.readers import CArchiveReader


def verify(root, bundle=None):
    root=Path(root)
    bundle = Path(bundle) if bundle is not None else root / 'build' / 'release-output' / 'WebVideoDownloader'
    from build_guard import check_output
    check_output(bundle, packaging=True)
    import json
    from product_identity import render_for,PRODUCTS
    meta_path=bundle/'_internal/product.json'
    meta=json.loads(meta_path.read_text(encoding='utf-8')) if meta_path.exists() else {'product':'manager','edition':'full'}
    archive = CArchiveReader(str(bundle / (PRODUCTS[meta['product']][0]+'.exe')))
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
        actual=(bundle/'_internal'/name).read_text(encoding='utf-8-sig')
        expected=render_for((root/name).read_text(encoding='utf-8-sig'),meta['product'],meta.get('edition','full'))
        if actual != expected:
            raise RuntimeError(f'安装资源与源码不一致：{name}')
    print(f'冻结入口、{len(checked)} 个本项目模块与安装资源均与源码一致。')


if __name__ == '__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('bundle',nargs='?',type=Path)
    verify(Path(__file__).resolve().parent,parser.parse_args().bundle)
