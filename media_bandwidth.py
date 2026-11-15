"""Cancelable process-wide bandwidth budget for media payloads only."""
from collections import deque
import math
import os
import threading
import time

MAX_READ=64*1024


class UnsupportedMediaTransport(ValueError):
    """A network downloader that cannot share the media budget."""


class Budget:
    def __init__(self):
        self.condition=threading.Condition();self.rate=0.;self.tokens=0.;self.updated=time.monotonic();self.epoch=0;self.waiters=deque()
    def configure(self,bytes_per_second):
        rate=float(bytes_per_second)
        if not math.isfinite(rate) or rate<0:raise ValueError('下载限速值无效。')
        with self.condition:
            if rate==self.rate:return
            self.rate=rate;self.tokens=0.;self.updated=time.monotonic();self.epoch+=1;self.condition.notify_all()
    def _refill(self):
        now=time.monotonic();self.tokens=min(max(1,min(MAX_READ,self.rate*.1)),self.tokens+max(0,now-self.updated)*self.rate);self.updated=now
    def take(self,count,check):
        ticket=object()
        with self.condition:self.waiters.append(ticket)
        try:
            while True:
                check()
                with self.condition:
                    if self.waiters[0] is ticket:
                        if not self.rate:
                            self.waiters.popleft();self.condition.notify_all();return min(count,MAX_READ),None
                        self._refill();size=max(1,min(count,MAX_READ,int(max(1,self.rate*.1))))
                        if self.tokens>=size:
                            self.tokens-=size;self.waiters.popleft();self.condition.notify_all();return size,self.epoch
                        delay=min(.1,max(.001,(size-self.tokens)/self.rate))
                    else:delay=.1
                    self.condition.wait(delay)
        finally:
            with self.condition:
                if ticket in self.waiters:self.waiters.remove(ticket);self.condition.notify_all()
    def refund(self,count,epoch):
        if count<=0 or epoch is None:return
        with self.condition:
            if epoch==self.epoch and self.rate:
                self._refill();self.tokens=min(max(1,min(MAX_READ,self.rate*.1)),self.tokens+count);self.condition.notify_all()
    def read(self,response,count,check):
        if count==0:return b''
        size,epoch=self.take(max(1,min(count,MAX_READ)),check)
        try:
            check();reader=getattr(response,'read1',None) or response.read;chunk=reader(size)
        except BaseException:self.refund(size,epoch);raise
        self.refund(size-len(chunk),epoch);return chunk


_shared=Budget();_profile=None;_config_guard=threading.RLock()


def shared():
    global _profile
    from runtime import app_data_dir
    profile=os.path.normcase(str(app_data_dir()))
    if profile!=_profile:
        with _config_guard:
            if profile!=_profile:
                import preferences
                _shared.configure(preferences.load()['download_limit_mib']*1024**2);_profile=profile
    return _shared


def configure_mib(value):
    global _profile
    from runtime import app_data_dir
    with _config_guard:_shared.configure(float(value)*1024**2);_profile=os.path.normcase(str(app_data_dir()))


def read_media(response,count,check):return shared().read(response,count,check)


class MediaResponse:
    def __init__(self,response,check):self.response=response;self.check=check
    def __getattr__(self,name):return getattr(self.response,name)
    def read(self,size=None):
        if size is not None and size>=0:return read_media(self.response,size,self.check)
        chunks=[]
        while True:
            chunk=read_media(self.response,MAX_READ,self.check)
            if not chunk:return b''.join(chunks)
            chunks.append(chunk)
    def read1(self,size=MAX_READ):return self.read(size)
    def close(self):return self.response.close()
    def __enter__(self):return self
    def __exit__(self,*args):self.close()
