"""Bounded image cache; untrusted files must decode as images before use."""
import hashlib
from pathlib import Path
import urllib.request
from urllib.parse import urlsplit
from PySide6.QtGui import QImage
from runtime import app_data_dir

def image_file(value,cancel=lambda:False):
    if value.startswith('//'):value='https:'+value
    if value.startswith('http://') and (urlsplit(value).hostname or '').endswith(('.hdslb.com','.biliimg.com')):value='https://'+value[7:]
    if value.startswith(('https://','http://')):
        request=urllib.request.Request(value,headers={'User-Agent':'Mozilla/5.0'})
        with urllib.request.urlopen(request,timeout=15) as response:
            data=bytearray()
            while True:
                if cancel():raise ValueError('已取消封面下载。')
                part=response.read(65536)
                if not part:break
                data.extend(part)
                if len(data)>10*1024**2:raise ValueError('封面超过10 MiB，请选择较小图片。')
    else:
        path=Path(value)
        if path.stat().st_size>10*1024**2:raise ValueError('封面超过10 MiB。')
        data=path.read_bytes()
    image=QImage()
    if not image.loadFromData(bytes(data)) or image.width()*image.height()>60_000_000:raise ValueError('封面不是有效图片或尺寸过大。')
    folder=app_data_dir()/'library'/'covers';folder.mkdir(parents=True,exist_ok=True)
    file=folder/(hashlib.sha256(data).hexdigest()+'.png')
    if not file.exists():
        image=image.scaled(1920,1920,aspectMode=__import__('PySide6.QtCore',fromlist=['Qt']).Qt.AspectRatioMode.KeepAspectRatio)
        temp=file.with_suffix('.tmp');image.save(str(temp),'PNG');temp.replace(file)
    return str(file)
