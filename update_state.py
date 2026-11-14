"""Persistent, anonymous polling and version-specific notification preferences."""
import json
from pathlib import Path
import time
import math
from runtime import APP_VERSION,app_data_dir
from update_protocol import version


def load():
    try:
        path=app_data_dir()/'updates'/'state.json'
        if path.stat().st_size>2*1024**2:return {}
        data=json.loads(path.read_text(encoding='utf-8-sig'))
        if not isinstance(data,dict):return {}
        for key in ('last_success','next_retry','snooze_until','failures'):
            if type(data.get(key,0)) not in (int,float) or not math.isfinite(data.get(key,0)) or data.get(key,0)<0:data.pop(key,None)
        return data
    except (OSError,ValueError):return {}


def save(data):
    folder=app_data_dir()/'updates';folder.mkdir(parents=True,exist_ok=True)
    path=folder/'state.json';temporary=folder/'state.tmp';temporary.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8');temporary.replace(path)


def due(state,enabled,hours,now=None):
    now=time.time() if now is None else now
    def number(key):
        value=state.get(key,0);return value if type(value) in (int,float) and 0<=value<=now+7*86400 else 0
    return enabled and now>=max(number('last_success')+hours*3600,number('next_retry'))


def visible(state):
    info=state.get('known')
    try:return isinstance(info,dict) and info.get('available') is True and version(info['version'])>version(APP_VERSION) and state.get('ignored_version')!=info['version']
    except (ValueError,KeyError,TypeError):return False


def succeeded(state,info,now=None):
    now=time.time() if now is None else now
    state.update(last_success=now,next_retry=0,failures=0)
    if info.get('available'):state['known']=info
    else:state.pop('known',None)


def failed(state,now=None):
    now=time.time() if now is None else now
    count=min(3,int(state.get('failures',0))+1);state.update(failures=count,next_retry=now+(900,3600,14400)[count-1])
