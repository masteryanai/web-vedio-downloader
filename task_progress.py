"""Shared presentation of download and verification progress."""
import math

def percentage(task):
    if task.get('state')=='completed':return 100.
    value=task.get('verification_percent') if task.get('phase')=='verify' or task.get('verification') else task.get('download_percent')
    return float(value) if type(value) in (float,int) and math.isfinite(value) and 0<=value<=100 else None

def clock(value):
    value=max(0,int(value or 0));return f'{value//3600:02}:{value%3600//60:02}:{value%60:02}'

def detail(task):
    if task.get('state')=='completed':return '已完成 · 可播放' if task.get('result',{}).get('verified') else '已完成'
    if task.get('phase')=='verify' or task.get('verification'):
        text='完整性校验'
        duration=task.get('verification_duration')
        if duration:text+=f" · {clock(task.get('verification_time'))} / {clock(duration)}"
        if task.get('verification_elapsed') is not None:text+=f" · 已耗时 {clock(task['verification_elapsed'])}"
        if task.get('verification_eta') is not None:text+=f" · 预计剩余 {clock(task['verification_eta'])}"
        return text
    return task.get('stage','')
