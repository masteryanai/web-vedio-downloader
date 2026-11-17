"""Verified optional browser runtime, kept inside this product's installation."""
import hashlib,importlib.util,json,os,re,shutil,sys,tempfile,zipfile
from pathlib import Path,PurePosixPath
from runtime import PRODUCT_ID,APP_VERSION,program_dir,resource_path

class ComponentRequired(RuntimeError):pass

def descriptor(name='browser'):
    path=resource_path('components.json')
    if not path.is_file():raise ComponentRequired('当前发布包缺少组件清单，请使用完整包或重新下载轻量基础包。')
    if path.stat().st_size>2*1024**2:raise ValueError('组件清单过大。')
    data=json.loads(path.read_text(encoding='utf-8'))
    if data.get('application')!=PRODUCT_ID or data.get('version')!=APP_VERSION:raise ValueError('组件不属于当前产品或版本。')
    value=data.get('components',{}).get(name)
    if not isinstance(value,dict):raise ComponentRequired('此版本未提供所需组件，请使用离线完整包。')
    if value.get('tag')!='v'+APP_VERSION or not re.fullmatch('[A-Za-z0-9._-]+',str(value.get('asset',''))) or not re.fullmatch('[a-f0-9]{64}',str(value.get('sha256',''))):raise ValueError('组件下载来源或校验信息无效。')
    if type(value.get('size')) is not int or not 0<value['size']<=1024**3:raise ValueError('组件大小无效。')
    rows=value.get('files')
    if not isinstance(rows,list) or not 1<=len(rows)<=10000:raise ValueError('组件文件清单无效。')
    names=set();total=0
    for row in rows:
        name=row.get('path') if isinstance(row,dict) else None
        safe_relative(name)
        if name.casefold() in names or type(row.get('size')) is not int or row['size']<0 or not re.fullmatch('[a-f0-9]{64}',str(row.get('sha256',''))):raise ValueError('组件文件清单不完整。')
        names.add(name.casefold());total+=row['size']
    if total>2*1024**3:raise ValueError('组件展开大小超限。')
    return value

def safe_relative(name):
    if not isinstance(name,str) or '\\' in name or ':' in name or '\x00' in name:raise ValueError('组件文件路径无效。')
    parts=name.split('/')
    if parts[0]!='PySide6' or len(parts)<2 or any(p in ('','.','..') or p.endswith((' ','.')) for p in parts):raise ValueError('组件路径越界。')
    # Qt ships a legitimate nested QML "Components" directory. Only the
    # installation namespace immediately below PySide6 is reserved here.
    if parts[1].casefold() in ('data','update','components','.wvd-library','sessions.dpapi'):raise ValueError('组件不能包含用户数据。')
    return PurePosixPath(name)

def folder(value):
    return program_dir()/'components'/'browser'/(APP_VERSION+'-'+value['sha256'][:16])

def verify(root,value):
    from library_archive import reject_links
    for row in value['files']:
        path=Path(root).joinpath(*safe_relative(row['path']).parts);reject_links(path)
        if not path.is_file() or path.stat().st_size!=row['size']:return False
        with path.open('rb') as stream:
            if hashlib.file_digest(stream,'sha256').hexdigest()!=row['sha256']:return False
    return True

def install_from_archive(archive,value,cancel,progress=lambda _:None):
    from library_archive import reject_links
    archive=Path(archive);reject_links(archive)
    with archive.open('rb') as stream:sha=hashlib.file_digest(stream,'sha256').hexdigest()
    if archive.stat().st_size!=value['size'] or sha!=value['sha256']:raise ValueError('浏览器组件包校验失败，未安装。')
    target=folder(value);reject_links(target)
    if target.exists():
        if verify(target,value):return target
        raise ValueError('已有组件文件损坏或改变，原件保留；请使用离线完整包修复。')
    cache=program_dir()/'update';reject_links(cache);cache.mkdir(parents=True,exist_ok=True)
    expected={row['path']:row for row in value['files']}
    with tempfile.TemporaryDirectory(prefix='component-stage-',dir=cache) as temporary:
        stage=Path(temporary);seen=set()
        with zipfile.ZipFile(archive) as package:
            for item in package.infolist():
                if cancel.is_set():raise RuntimeError('已取消组件安装，任务保留。')
                if item.is_dir():continue
                safe_relative(item.filename)
                if item.filename not in expected or item.filename.casefold() in seen or ((item.external_attr>>16)&0o170000)==0o120000:raise ValueError('组件包包含未声明文件或链接。')
                seen.add(item.filename.casefold());row=expected[item.filename]
                if item.file_size!=row['size']:raise ValueError('组件文件大小与清单不一致。')
                path=stage.joinpath(*PurePosixPath(item.filename).parts);path.parent.mkdir(parents=True,exist_ok=True)
                sha=hashlib.sha256()
                with package.open(item) as source,path.open('xb') as output:
                    while block:=source.read(1024*1024):
                        if cancel.is_set():raise RuntimeError('已取消组件安装，任务保留。')
                        output.write(block);sha.update(block)
                if sha.hexdigest()!=row['sha256']:raise ValueError('组件文件SHA-256不匹配。')
                progress({'stage':'校验并安装浏览器组件','files_done':len(seen),'files_total':len(expected)})
        if seen!={name.casefold() for name in expected}:raise ValueError('组件包缺少文件。')
        if cancel.is_set():raise RuntimeError('已取消组件安装，任务保留。')
        reject_links(target);target.parent.mkdir(parents=True,exist_ok=True)
        if target.exists():raise ValueError('组件目录已出现，未覆盖现有文件。')
        stage.rename(target)
    return target

def fetch_browser(cancel,progress):
    import updater
    from update_transfer import download
    from library_archive import reject_links
    value=descriptor();cache=program_dir()/'update'/'components';reject_links(cache);cache.mkdir(parents=True,exist_ok=True)
    archive=cache/value['asset']
    download(archive,value,lambda headers,timeout:updater.request(updater.PREFIX+value['tag']+'/'+value['asset'],True,headers,timeout),cancel,progress)
    return install_from_archive(archive,value,cancel,progress)

_handles=[]
_activated=False
def activate_browser():
    global _activated
    if _activated:return
    if importlib.util.find_spec('PySide6.QtWebEngineCore') is not None:_activated=True;return
    value=descriptor();root=folder(value)
    if not verify(root,value):raise ComponentRequired('需要浏览器组件，请在账号或解析窗口点击前往验证后安装。')
    import PySide6
    path=root/'PySide6';PySide6.__path__.append(str(path))
    if os.name=='nt':
        # An extension in the add-on directory also needs the retained Qt DLLs
        # (for example Qt6PrintSupport) in the base package. Python's Windows
        # extension loader does not resolve those dependencies through PATH.
        for directory in (path,resource_path('PySide6'),resource_path()):_handles.append(os.add_dll_directory(str(directory)))
    os.environ['PATH']=os.pathsep.join((str(path),str(resource_path('PySide6')),str(resource_path()),os.environ.get('PATH','')))
    os.environ['QTWEBENGINEPROCESS_PATH']=str(path/'QtWebEngineProcess.exe')
    os.environ['QTWEBENGINE_RESOURCES_PATH']=str(path/'resources')
    os.environ['QTWEBENGINE_LOCALES_PATH']=str(path/'translations'/'qtwebengine_locales')
    _activated=True
