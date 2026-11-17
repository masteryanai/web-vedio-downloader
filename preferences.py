"""Validated, merged preferences; writes never discard unrelated settings."""
import json
import math
from pathlib import Path
import uuid
from runtime import app_data_dir

DEFAULTS={'parallel_downloads':5,'close_action':'ask','float_enabled':False,
          'last_download_dir':'','last_quality_height':1080,'clipboard_enabled':True,'reduce_motion':False,
          'float_opacity':85,'float_auto_hide':True,'float_position':None,'float_edge':'','auto_check_updates':True,'update_interval_hours':4,'download_limit_mib':0,'update_cache_dir':'','external_player':''}

def valid(key,value):
    if key=='last_download_dir':return isinstance(value,str) and (not value or Path(value).is_absolute()) and '\x00' not in value
    if key=='last_quality_height':return type(value) is int and value in (2160,1440,1080,720,480,360)
    if key in ('clipboard_enabled','reduce_motion'):return type(value) is bool
    if key=='external_player':return isinstance(value,str) and (not value or Path(value).is_absolute()) and '\x00' not in value
    if key=='update_cache_dir':return isinstance(value,str) and (not value or Path(value).is_absolute()) and len(value)<32000 and "\x00" not in value
    if key=='download_limit_mib':
        try:return type(value) in (int,float) and math.isfinite(value) and (value==0 or .01<=value<=102400)
        except OverflowError:return False
    if key=='parallel_downloads':return type(value) is int and 1<=value<=10
    if key=='close_action':return value in ('ask','hide','exit')
    if key in ('float_enabled','float_auto_hide','auto_check_updates'):return type(value) is bool
    if key=='update_interval_hours':return type(value) is int and 1<=value<=168
    if key=='float_opacity':return type(value) is int and 20<=value<=100
    if key=='float_position':return value is None or isinstance(value,list) and len(value)==2 and all(type(v) is int and abs(v)<=100000 for v in value)
    if key=='float_edge':return value in ('','left','right')
    return True

def raw():
    try:
        value=json.loads((app_data_dir()/'settings.json').read_text(encoding='utf-8'))
        return value if isinstance(value,dict) else {}
    except (OSError,ValueError):return {}

def load():
    data=raw()
    return {key:data[key] if key in data and valid(key,data[key]) else default for key,default in DEFAULTS.items()}

def update(**changes):
    if any(key not in DEFAULTS or not valid(key,value) for key,value in changes.items()):raise ValueError('设置值无效。')
    from diagnostics import file_lock
    folder=app_data_dir();folder.mkdir(parents=True,exist_ok=True)
    with file_lock(folder):
        data=raw();data.update(changes)
        temporary=folder/('settings-'+uuid.uuid4().hex+'.tmp')
        try:
            temporary.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8');temporary.replace(folder/'settings.json')
        finally:temporary.unlink(missing_ok=True)
    if 'download_limit_mib' in changes:
        from media_bandwidth import configure_mib
        configure_mib(changes['download_limit_mib'])
    return load()

def parallel_limit():return load()['parallel_downloads']
def save_limit(value):return update(parallel_downloads=value)
