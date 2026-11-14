"""One definition of live throughput and byte-weighted current-batch progress."""
import math
import time

SPEED_TTL=3


def number(value):
    if type(value) not in (int,float):return None
    try:converted=float(value)
    except (ValueError,OverflowError):return None
    return converted if math.isfinite(converted) and converted>=0 else None


def speed_text(value):
    value=number(value)
    if value is None:return '—'
    for unit in ('B/s','KiB/s','MiB/s','GiB/s'):
        if value<1024 or unit=='GiB/s':return (f'{value:.3g} {unit}' if value>9999 else f'{value:.1f} {unit}') if unit!='B/s' else f'{value:.0f} {unit}'
        value/=1024


def row_speed(queue,row,now=None):
    now=time.monotonic() if now is None else now
    task_id=row['id'];entry=queue.active.get(task_id)
    if task_id not in queue.active or row['state'] in ('paused','failed','cancelled','completed'):return None
    if task_id in getattr(queue,'deleting',set()):return None
    if isinstance(entry,tuple) and len(entry)>2 and entry[2].is_set():return None
    live=queue.snapshots.get(task_id,{})
    if live.get('phase')!='download':return None
    if now-live.get('_speed_at',0)>SPEED_TTL:return 0.
    return number(live.get('speed')) or 0.


def summarize(queue,now=None):
    now=time.monotonic() if now is None else now
    rows=[];transfers=[];speed=0.;total=done=0.;unknown=0
    for task_id,base in getattr(queue,'batch_rows',getattr(queue,'entries',{})).items():
        row={**base,**queue.snapshots.get(task_id,{})}
        row['state']=base['state']
        if row['state']=='cancelled':continue
        percent=number(row.get('download_percent'));row['download_percent']=min(100.,percent) if percent is not None else None
        rows.append(row);rate=row_speed(queue,row,now)
        if rate is not None:
            transfers.append({**row,'live_speed':rate});speed+=rate
        size=number(row.get('total_bytes'))
        percent=number(row.get('download_percent'))
        count=number(row.get('downloaded_bytes'))
        if not size:unknown+=1;continue
        total+=size
        if row['state']=='completed':done+=size
        elif count is not None:done+=min(size,count)
        elif percent is not None:done+=size*min(100,percent)/100
    if not math.isfinite(total) or not math.isfinite(done):unknown+=1
    completed=bool(rows) and all(row['state']=='completed' for row in rows)
    percent=100. if completed else min(100.,done/total*100) if total and not unknown else None
    active=sum(row['id'] in queue.active for row in rows)
    return {'speed':speed,'percent':percent,'unknown':unknown,'count':len(rows),'completed':completed,
            'queued':len(queue.waiting),'active':active,'processing':max(0,active-len(transfers)),
            'paused':sum(row['state'] in ('paused','failed') for row in rows),'transfers':transfers}


def overview(stats):
    progress='本轮下载完成' if stats['completed'] else f"本轮下载 {stats['percent']:.1f}%" if stats['percent'] is not None else '本轮总量未知' if stats['count'] else '本轮暂无任务'
    return f"{progress} · 活动 {stats['active']} · 排队 {stats['queued']} · 解析/处理 {stats['processing']}"
