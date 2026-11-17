"""Portable local verification checkpoint; never contains a signed media URL."""
import math
from pathlib import Path
import download_tasks as tasks

FIELDS={'media','size','mtime_ns','duration','selected','extension'}
SELECTED={'width','height','_audio_expected'}

def payload(value):
    if not isinstance(value,dict) or set(value)!=FIELDS:raise ValueError('待校验资料无效，现有文件保留。')
    relative=Path(value['media']) if isinstance(value.get('media'),str) else Path('..')
    if not value['media'] or relative.is_absolute() or relative.drive or '..' in relative.parts:raise ValueError('待校验路径不安全。')
    if any(type(value.get(k)) is not int or value[k]<0 for k in ('size','mtime_ns')):raise ValueError('待校验文件标识无效。')
    duration=value.get('duration')
    if type(duration) not in (int,float) or not math.isfinite(duration) or duration<0:raise ValueError('待校验时长无效。')
    selected=value.get('selected')
    if not isinstance(selected,dict) or set(selected)-SELECTED or any(type(v) not in (int,float,bool) or not math.isfinite(v) for v in selected.values()):raise ValueError('待校验画质无效。')
    if value['extension'] not in ('.mp4','.mkv','.webm'):raise ValueError('待校验成品类型无效。')
    return dict(value,selected=dict(selected))

def capture(work,media,info,selected,extension):
    from library_archive import reject_links
    work=Path(work).resolve();media=Path(media);reject_links(media)
    media=media.resolve()
    if not media.is_relative_to(work):raise ValueError('待校验文件不在本任务目录中。')
    stat=media.stat()
    return payload({'media':media.relative_to(work).as_posix(),'size':stat.st_size,'mtime_ns':stat.st_mtime_ns,
                    'duration':float(info.get('duration') or 0),
                    'selected':{k:v for k,v in selected.items() if k in SELECTED and v is not None},'extension':extension})

def validate(task):
    from library_archive import reject_links
    value=payload(task['verification']);work=tasks.work_path(task);media=work/value['media'];reject_links(media)
    if not media.resolve().is_relative_to(work.resolve()):raise ValueError('待校验文件路径越界。')
    try:stat=media.stat()
    except FileNotFoundError:raise ValueError('待校验文件缺失，请检查原保存位置；不会自动重新下载。') from None
    if stat.st_size!=value['size'] or stat.st_mtime_ns!=value['mtime_ns']:raise ValueError('待校验文件已改变，现有文件保留；请核对后重新添加。')
    return media,value
