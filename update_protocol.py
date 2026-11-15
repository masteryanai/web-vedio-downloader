"""Versioned release inventories and safe file-level package operations."""
import hashlib
import json
import os
from pathlib import Path,PurePosixPath
import re
import shutil
import stat
import zipfile

APPLICATION='WebVideoDownloader'
SCHEMA=2
DATA_SCHEMA=1
INSTALLATION='_internal/installation.json'
ROOT_FILES={'WebVideoDownloader.exe','README.md','LICENSES.md'}


def version(value):
    if not isinstance(value,str) or not re.fullmatch(r'v?\d+\.\d+\.\d+',value):raise ValueError('版本号无效。')
    return tuple(map(int,value.lstrip('v').split('.')))


def canonical(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')
def digest(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def relative(name):
    if not isinstance(name,str) or not name or '\\' in name or ':' in name or '\x00' in name:raise ValueError('程序清单路径无效。')
    parts=name.split('/')
    if any(part in ('','.','..') or part.endswith((' ','.')) for part in parts):raise ValueError('程序清单路径越界。')
    if parts[0] not in ROOT_FILES and not (parts[0]=='_internal' and len(parts)>1):raise ValueError('拒绝修改非程序文件。')
    if any(re.fullmatch(r'(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?',part) for part in parts):raise ValueError('拒绝设备路径。')
    return name


def owned_path(root,name):
    relative(name);root=Path(os.path.abspath(root));path=root.joinpath(*name.split('/'))
    # Validated relative components guarantee lexical containment. lstat checks each
    # existing component once, without following a symlink or junction as stat does.
    cursor=path
    while True:
        try:
            attributes=cursor.lstat()
            if stat.S_ISLNK(attributes.st_mode) or getattr(attributes,'st_file_attributes',0)&1024:
                raise ValueError('拒绝程序目录中的链接或重解析点。')
        except FileNotFoundError:pass
        if cursor==root:break
        cursor=cursor.parent
    return path


def inventory(ver,files):
    result={'application':APPLICATION,'schema':SCHEMA,'data_schema':DATA_SCHEMA,'version':ver,'files':sorted(files,key=lambda row:row['path'])}
    result['inventory_sha256']=hashlib.sha256(canonical(result['files'])).hexdigest()
    return validate_inventory(result)


def validate_inventory(data):
    if not isinstance(data,dict) or data.get('application')!=APPLICATION or data.get('schema')!=SCHEMA or type(data.get('data_schema')) is not int or not 1<=data['data_schema']<=100:raise ValueError('程序清单格式或数据兼容版本不支持。')
    version(data.get('version'));files=data.get('files')
    if not isinstance(files,list) or not 1<=len(files)<=20000:raise ValueError('程序清单数量无效。')
    seen=set()
    for row in files:
        if not isinstance(row,dict):raise ValueError('程序清单条目无效。')
        path=relative(row.get('path'))
        if path.casefold()==INSTALLATION.casefold() or path.casefold() in seen:raise ValueError('程序清单重复或包含自身。')
        seen.add(path.casefold())
        if type(row.get('size')) is not int or not 0<=row['size']<=8*1024**3 or not re.fullmatch('[0-9a-f]{64}',str(row.get('sha256',''))):raise ValueError('程序文件校验信息无效。')
    if 'webvideodownloader.exe' not in seen:raise ValueError('程序清单缺少主程序。')
    if hashlib.sha256(canonical(files)).hexdigest()!=data.get('inventory_sha256'):raise ValueError('程序清单哈希不匹配。')
    return data


def tree_inventory(folder,ver):
    root=Path(folder);files=[]
    for path in sorted(root.rglob('*')):
        if not path.is_file():continue
        name=path.relative_to(root).as_posix()
        if name==INSTALLATION:continue
        # A convenience root launcher is deliberately outside managed program data.
        if name.split('/')[0] not in ROOT_FILES and not name.startswith('_internal/'):continue
        owned_path(root,name);files.append({'path':name,'size':path.stat().st_size,'sha256':digest(path)})
    return inventory(ver,files)


def zip_inventory(archive,ver):
    files=[];seen=set()
    with zipfile.ZipFile(archive) as package:
        for item in package.infolist():
            if item.is_dir():continue
            if not item.filename.startswith(APPLICATION+'/'):raise ValueError('发布包目录无效。')
            name=item.filename[len(APPLICATION)+1:]
            if name==INSTALLATION or name=='修复与回退.cmd':continue
            relative(name)
            if name.casefold() in seen:raise ValueError('发布包条目重复。')
            seen.add(name.casefold())
            with package.open(item) as stream:sha=hashlib.file_digest(stream,'sha256').hexdigest()
            files.append({'path':name,'size':item.file_size,'sha256':sha})
    return inventory(ver,files)


def verify_tree(root,data,cancel=None,progress=None):
    validate_inventory(data);total=len(data['files']);done_bytes=0;total_bytes=sum(row['size'] for row in data['files'])
    for i,row in enumerate(data['files']):
        if cancel is not None and cancel.is_set():raise RuntimeError('已取消更新准备。')
        path=owned_path(root,row['path'])
        if not path.is_file() or path.stat().st_size!=row['size'] or digest(path)!=row['sha256']:return False,row['path']
        done_bytes+=row['size']
        if progress and (i%25==0 or i+1==total):progress({'phase':'verify','percent':int((i+1)*100/total),'stage':'核对程序文件',
            'files_done':i+1,'files_total':total,'processed_bytes':done_bytes,'total_bytes':total_bytes})
    return True,''


def differences(base,target):
    before={row['path']:row for row in validate_inventory(base)['files']};after={row['path']:row for row in validate_inventory(target)['files']}
    changed=[name for name,row in after.items() if name not in before or row!=before[name]]
    return sorted(changed),sorted(set(before)-set(after))


def extract_verified(archive,destination,target,changed=None,cancel=None,*,progress=None,verify_hash=True):
    expected={row['path']:row for row in validate_inventory(target)['files']}
    wanted=set(expected) if changed is None else set(changed);seen=set()
    if not wanted<=set(expected):raise ValueError('差异包包含未声明文件。')
    with zipfile.ZipFile(archive) as package:
        for item in package.infolist():
            if cancel is not None and cancel.is_set():raise RuntimeError('已取消更新准备。')
            if item.is_dir():continue
            if not item.filename.startswith(APPLICATION+'/'):raise ValueError('更新包路径无效。')
            name=item.filename[len(APPLICATION)+1:]
            # Full packages also carry the embedded inventory and bootstrap launcher.
            if changed is None and name in (INSTALLATION,'修复与回退.cmd'):continue
            relative(name)
            if name not in wanted or name.casefold() in seen or stat.S_ISLNK(item.external_attr>>16):raise ValueError('更新包包含未声明、重复或链接条目。')
            seen.add(name.casefold());row=expected[name]
            if item.file_size!=row['size']:raise ValueError('更新文件大小不符。')
            path=owned_path(destination,name);path.parent.mkdir(parents=True,exist_ok=True)
            with package.open(item) as source,path.open('wb') as output:shutil.copyfileobj(source,output,1024*1024)
            if verify_hash and digest(path)!=row['sha256']:raise ValueError('更新文件 SHA-256 校验失败。')
            if progress and (len(seen)%25==0 or len(seen)==len(wanted)):
                progress({'phase':'extract','percent':int(len(seen)*100/max(1,len(wanted))),'stage':'解压更新文件','files_done':len(seen),'files_total':len(wanted)})
    if seen!={name.casefold() for name in wanted}:raise ValueError('更新包缺少文件。')


def assemble_delta(install,archive,destination,base,target,cancel=None,progress=None,*,base_verified=False,verify_target=True):
    if not base_verified:
        valid,_=verify_tree(install,base,cancel,progress)
        if not valid:raise ValueError('本地基线已变化，不能继续增量更新。')
    changed,deleted=differences(base,target);changed=set(changed)
    unchanged=[row for row in target['files'] if row['path'] not in changed]
    for i,row in enumerate(unchanged):
        if cancel is not None and cancel.is_set():raise RuntimeError('已取消更新准备。')
        source=owned_path(install,row['path']);path=owned_path(destination,row['path']);path.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,path)
        if progress and (i%25==0 or i+1==len(unchanged)):
            progress({'phase':'copy','percent':int((i+1)*100/max(1,len(unchanged))),'stage':'准备安装：复制可复用程序文件','files_done':i+1,'files_total':len(unchanged)})
    extract_verified(archive,destination,target,changed,cancel,progress=progress,verify_hash=verify_target)
    if verify_target and not verify_tree(destination,target,cancel,progress)[0]:raise ValueError('增量组装后的完整程序校验失败。')
    return deleted
