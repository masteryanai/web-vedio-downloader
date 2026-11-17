"""Derive the lightweight base and a pinned browser add-on from one clean full build."""
from pathlib import Path
import hashlib,json,shutil,zipfile
from runtime import APP_VERSION
from product_identity import render_for
from build_guard import check_output

def browser_file(relative):
    parts=Path(relative).as_posix()
    if not parts.startswith('_internal/PySide6/'):return False
    name=parts.removeprefix('_internal/PySide6/')
    if name.startswith(('qml/','resources/','translations/qtwebengine_locales/')):return True
    prefixes=('Qt6WebEngine','QtWebEngine','Qt6Qml','QtQml','Qt6Quick','QtQuick','Qt6WebChannel','QtWebChannel','Qt6Positioning','QtPositioning','Qt6Pdf','QtPdf')
    return name.startswith(prefixes)

def split(full,base,release):
    full,base,release=map(lambda p:Path(p).absolute(),(full,base,release));check_output(full,packaging=True)
    if full==base or full.is_relative_to(base) or base.is_relative_to(full):raise ValueError('基础包与完整包必须使用独立构建目录。')
    if base.exists():raise ValueError('基础包输出已存在，请指定新的构建目录；不会清理旧目录。')
    metadata=json.loads((full/'_internal/product.json').read_text(encoding='utf-8'))
    if metadata!={'product':'lite','edition':'full'}:raise ValueError('需要当前轻量版完整构建。')
    paths=[path for path in full.rglob('*') if path.is_file()]
    selected=[path for path in paths if browser_file(path.relative_to(full))]
    if not any(path.name=='Qt6WebEngineCore.dll' for path in selected):raise ValueError('完整构建缺少浏览器核心。')
    release.mkdir(parents=True,exist_ok=True);archive=release/f'WebVideoDownloaderLite-v{APP_VERSION}-browser.zip'
    rows=[]
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as output:
        for path in sorted(selected):
            relative=path.relative_to(full/'_internal').as_posix()
            with path.open('rb') as stream:sha=hashlib.file_digest(stream,'sha256').hexdigest()
            rows.append({'path':relative,'size':path.stat().st_size,'sha256':sha});output.write(path,relative)
    with archive.open('rb') as stream:sha=hashlib.file_digest(stream,'sha256').hexdigest()
    descriptor={'asset':archive.name,'tag':'v'+APP_VERSION,'size':archive.stat().st_size,'sha256':sha,'files':rows}
    manifest={'application':'WebVideoDownloaderLite','version':APP_VERSION,'components':{'browser':descriptor}}
    selected=set(selected);base.mkdir(parents=True)
    for path in paths:
        if path in selected or path.relative_to(full).as_posix()=='_internal/installation.json':continue
        target=base/path.relative_to(full);target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,target)
    for name in ('update_helper.ps1','update_install.ps1','update_window.ps1','recovery.ps1','recovery.cmd','recovery-root.cmd'):
        (base/'_internal'/name).write_text(render_for((Path(__file__).parent/name).read_text(encoding='utf-8-sig'),'lite','base'),encoding='utf-8-sig' if name.endswith('.ps1') else 'utf-8')
    (base/'_internal/product.json').write_text(json.dumps({'product':'lite','edition':'base'}),encoding='utf-8')
    for bundle in (full,base):(bundle/'_internal/components.json').write_text(json.dumps(manifest,separators=(',',':')),encoding='utf-8')
    check_output(base,packaging=True)
    result={'full_bytes':sum(p.stat().st_size for p in full.rglob('*') if p.is_file()),
            'base_bytes':sum(p.stat().st_size for p in base.rglob('*') if p.is_file()),'component_bytes':archive.stat().st_size,'component_files':len(rows)}
    print(json.dumps(result));return result

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('full',type=Path);parser.add_argument('base',type=Path);parser.add_argument('release',type=Path)
    args=parser.parse_args();split(args.full,args.base,args.release)
