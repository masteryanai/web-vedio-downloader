"""Storage presentation and metadata reads for catalogue-registered paths only."""
import os
from pathlib import Path
import shutil
import stat
from PySide6.QtCore import Qt,QRectF
from PySide6.QtGui import QColor,QPainter
from PySide6.QtWidgets import QFrame,QHBoxLayout,QVBoxLayout,QLabel,QWidget


def byte_text(value):
    for unit in ('B','KiB','MiB','GiB','TiB'):
        if value<1024 or unit=='TiB':return f'{value:.1f} {unit}' if unit!='B' else f'{value} B'
        value/=1024


def storage_snapshot(root,works,episodes,cancel):
    """Read registered files, never enumerate any directory or mount."""
    if cancel.is_set():return None
    root=Path(root)
    try:disk=shutil.disk_usage(root)
    except OSError:disk=None
    if cancel.is_set():return None
    measured={};rows={};owned={}
    for work in works:
        files={os.path.normcase(f['path']):f for e in episodes if e['work_id']==work['id'] for f in e.get('files',[])}
        known=unknown=external=0
        for key,file in files.items():
            if cancel.is_set():return None
            path=Path(file['path']);external+=bool(file.get('external'))
            if key not in measured:
                try:
                    info=path.stat();measured[key]=info.st_size if stat.S_ISREG(info.st_mode) else None
                except OSError:measured[key]=None
            size=measured[key]
            if size is None:unknown+=1;continue
            known+=size
            # External references and files outside this root never inflate this root's segment.
            if not file.get('external'):
                try:
                    if path.resolve().is_relative_to(root.resolve()):owned[key]=size
                except OSError:pass
        rows[work['id']]={'known':known,'unknown':unknown,'files':len(files),'external':external}
    return {'disk':disk,'rows':rows,'owned':sum(owned.values())}


class StorageBar(QWidget):
    def __init__(self):
        super().__init__();self.setFixedHeight(10);self.total=0;self.used=0;self.owned=0
    def set_usage(self,total=0,used=0,owned=0):
        self.total=total;self.used=used;self.owned=owned;self.update()
    def paintEvent(self,event):
        painter=QPainter(self);painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen);painter.setBrush(QColor('#343b45'));painter.drawRoundedRect(QRectF(self.rect()),3,3)
        if not self.total:return
        used=min(1,self.used/self.total)*self.width();owned=min(self.used,self.owned)/self.total*self.width()
        painter.fillRect(QRectF(0,0,used,self.height()),QColor('#8176cb'))
        painter.fillRect(QRectF(0,0,owned,self.height()),QColor('#1a9fff'))


class StorageRow(QFrame):
    def __init__(self,work,pixmap,episodes):
        super().__init__();self.setObjectName('storageRow')
        box=QHBoxLayout(self);box.setContentsMargins(12,10,16,10);box.setSpacing(16)
        self.cover=QLabel();self.cover.setFixedSize(106,68);self.cover.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cover.setPixmap(pixmap.scaled(106,68,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation));box.addWidget(self.cover)
        info=QVBoxLayout();info.setSpacing(5)
        title=QLabel(work['title']+' '+work.get('season',''));title.setObjectName('storageTitle');title.setWordWrap(True);title.setMaximumHeight(44);info.addWidget(title)
        sites=' / '.join(dict.fromkeys(v.get('site','未知来源') for v in work.get('sources',[]))) or '外部导入'
        source=QLabel(sites+f' · {episodes} 个选集');source.setObjectName('muted');source.setWordWrap(True);info.addWidget(source)
        self.file_label=QLabel('读取已登记文件…');self.file_label.setObjectName('muted');info.addWidget(self.file_label)
        box.addLayout(info,1)
        self.size_label=QLabel('读取中');self.size_label.setObjectName('storageSize');self.size_label.setAlignment(Qt.AlignmentFlag.AlignRight|Qt.AlignmentFlag.AlignVCenter);self.size_label.setMinimumWidth(98);box.addWidget(self.size_label)
    def set_measurement(self,value):
        unknown=value['unknown'];known=value['known'];files=value['files']
        self.size_label.setText((byte_text(known)+' + 未知') if unknown and known else ('未知 / 离线' if unknown else byte_text(known)))
        self.size_label.setToolTip('已登记文件的逻辑大小；未登记文件不统计。')
        self.file_label.setText(f'{files} 个登记文件'+(f" · 外部引用 {value['external']}" if value['external'] else '')+(f' · {unknown} 个不可用' if unknown else ''))
