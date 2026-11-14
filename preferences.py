"""Validated, merged preferences; writes never discard unrelated settings."""
import json
from pathlib import Path
import uuid
from runtime import app_data_dir

DEFAULTS={'parallel_downloads':5,'close_action':'ask','float_enabled':False,
          'float_opacity':85,'float_auto_hide':True,'float_position':None,'float_edge':''}

def valid(key,value):
    if key=='parallel_downloads':return type(value) is int and 1<=value<=10
    if key=='close_action':return value in ('ask','hide','exit')
    if key in ('float_enabled','float_auto_hide'):return type(value) is bool
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
    return load()

def parallel_limit():return load()['parallel_downloads']
def save_limit(value):return update(parallel_downloads=value)
