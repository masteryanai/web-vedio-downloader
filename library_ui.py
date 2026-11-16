"""Library workspace, collection import and season-local playback controls."""
from collections import deque
import copy
import os
from pathlib import Path
import re
import subprocess
import threading
import time
from PySide6.QtCore import Qt,QObject,QThread,Signal,Slot,QTimer,QSize,QUrl,QEvent
from PySide6.QtGui import QIcon,QPixmap,QPainter,QColor,QPainterPath,QFont,QDesktopServices,QLinearGradient,QFontMetrics
from PySide6.QtWidgets import (QWidget,QDialog,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QLineEdit,QComboBox,
    QTableWidget,QTableWidgetItem,QHeaderView,QListWidget,QListWidgetItem,QListView,QStackedWidget,
    QScrollArea,QCheckBox,QSpinBox,QFileDialog,QMessageBox,QInputDialog,QMenu,QFrame,QSplitter,QProgressBar,QStyledItemDelegate,QSizePolicy,QTreeWidget,QTreeWidgetItem)
from media_library import Catalogue,component,source_key,choose_format
import download_tasks as tasks
import diagnostics


class Work(QObject):
    result=Signal(str,object);failed=Signal(str,str);finished=Signal()
    def __init__(self,action,cancel,key):super().__init__();self.action=action;self.cancel=cancel;self.key=key
    @Slot()
    def run(self):
        try:self.result.emit(self.key,self.action(self.cancel))
        except Exception as error:
            diagnostics.exception('library.worker.failed',error,error.__traceback__);self.failed.emit(self.key,str(error))
        finally:self.finished.emit()


class Jobs(QObject):
    def __init__(self,parent):super().__init__(parent);self.running={};self.callbacks={};self.closing=False
    def start(self,action,done,failed):
        if self.closing:return
        from media_library import identifier
        key=identifier();self.callbacks[key]=(done,failed)
        cancel=threading.Event();thread=QThread(self);job=Work(action,cancel,key);job.moveToThread(thread)
        self.running[thread]=(job,cancel)
        thread.started.connect(job.run);job.result.connect(self.received,Qt.ConnectionType.QueuedConnection);job.failed.connect(self.failed,Qt.ConnectionType.QueuedConnection)
        job.finished.connect(thread.quit);job.finished.connect(job.deleteLater)
        thread.finished.connect(self.reaped);thread.finished.connect(thread.deleteLater);thread.start()
    @Slot(str,object)
    def received(self,key,value):
        callbacks=self.callbacks.pop(key,None)
        if callbacks and not self.closing:
            try:callbacks[0](value)
            except Exception as error:
                diagnostics.exception('library.callback.failed',error,error.__traceback__);callbacks[1](str(error))
    @Slot(str,str)
    def failed(self,key,error):
        callbacks=self.callbacks.pop(key,None)
        if callbacks and not self.closing:callbacks[1](error)
    @Slot()
    def reaped(self):self.running.pop(self.sender(),None)
    def shutdown(self):
        self.closing=True
        for _,cancel in self.running.values():cancel.set()
        return not self.running


class Trend(QWidget):
    def __init__(self,parent=None):super().__init__(parent);self.values=deque(maxlen=120);self.setMinimumHeight(90);self.setAccessibleName('最近120秒媒体下载速度趋势')
    def add(self,value):self.values.append(max(0,value));self.update()
    def paintEvent(self,event):
        painter=QPainter(self);painter.setRenderHint(QPainter.RenderHint.Antialiasing);background=QLinearGradient(0,0,0,self.height());background.setColorAt(0,QColor('#223c55'));background.setColorAt(1,QColor('#18232e'));painter.fillRect(self.rect(),background)
        painter.setPen(QColor('#31485b'))
        for i in range(1,5):
            y=28+(self.height()-40)*i/4;painter.drawLine(0,int(y),self.width(),int(y))
        painter.setPen(QColor('#88a6bd'));painter.drawText(14,21,'网络使用情况');painter.drawText(self.width()-108,21,'过去 120 秒')
        from transfer_stats import speed_text as speed
        painter.drawText(14,45,'当前 '+speed(self.values[-1] if self.values else 0)+'    峰值 '+speed(max(self.values) if self.values else 0))
        if len(self.values)<2:
            painter.setPen(QColor('#258ac4'));painter.drawLine(0,self.height()-8,self.width(),self.height()-8);return
        path=QPainterPath();height=max(self.values) or 1
        for i,value in enumerate(self.values):
            x=i*self.width()/119;y=self.height()-8-value/height*max(1,self.height()-66)
            if i==0:path.moveTo(x,y)
            else:path.lineTo(x,y)
        painter.setPen(QColor('#30bee9'));painter.drawPath(path)


class Artwork(QLabel):
    def __init__(self):super().__init__();self.artwork=QPixmap();self.caption=''
    def setPixmap(self,pixmap):self.artwork=pixmap;self.update()
    def setText(self,text):self.caption=text;self.update()
    def paintEvent(self,event):
        p=QPainter(self);p.fillRect(self.rect(),QColor('#233e52'))
        if not self.artwork.isNull():
            image=self.artwork.scaled(self.size(),Qt.AspectRatioMode.KeepAspectRatioByExpanding,Qt.TransformationMode.SmoothTransformation)
            p.drawPixmap((self.width()-image.width())//2,(self.height()-image.height())//2,image)
        gradient=QLinearGradient(0,0,0,self.height());gradient.setColorAt(0,QColor(12,24,34,30));gradient.setColorAt(1,QColor(12,24,34,235));p.fillRect(self.rect(),gradient)
        font=QFont('Microsoft YaHei UI');font.setPixelSize(23);font.setBold(True);p.setFont(font);p.setPen(QColor('#f0f7fe'))
        text=QFontMetrics(font).elidedText(self.caption,Qt.TextElideMode.ElideRight,max(0,self.width()-40));p.drawText(20,self.height()-23,text)


def message(parent,error):QMessageBox.warning(parent,'操作未完成',str(error)[:1200])

def thumbnail(work,width=280,height=420,banner=False):
    value=work.get('banner' if banner else 'cover','')
    from runtime import app_data_dir
    path=Path(value) if value else None
    if path and not path.is_absolute():path=app_data_dir()/path
    pixmap=QPixmap(str(path)) if path else QPixmap()
    if pixmap.isNull():
        pixmap=QPixmap(width,height);pixmap.fill(QColor('#253141'));p=QPainter(pixmap)
        gradient=QLinearGradient(0,0,width,height);gradient.setColorAt(0,QColor('#344d66'));gradient.setColorAt(1,QColor('#17212e'));p.fillRect(pixmap.rect(),gradient)
        p.setPen(QColor('#40576c'));p.drawEllipse(width//3,-height//6,width,height);p.drawEllipse(-width//2,height//3,width,height)
        font=QFont('Microsoft YaHei UI');font.setPixelSize(max(32,width//3));font.setBold(True);p.setFont(font);p.setPen(QColor('#61778c'));p.drawText(pixmap.rect().adjusted(14,0,-14,-height//5),Qt.AlignmentFlag.AlignCenter,work.get('title','影')[:1])
        p.setPen(QColor('#709bb9'));font=QFont('Microsoft YaHei UI');font.setPixelSize(16);p.setFont(font)
        source=work.get('sources',[{}])[0].get('site','影片') if work.get('sources') else '影片'
        p.drawText(pixmap.rect().adjusted(16,height//2,-16,-25),Qt.AlignmentFlag.AlignBottom|Qt.TextFlag.TextWordWrap,source+'\n'+work['title']);p.end()
    return pixmap if banner else pixmap.scaled(width,height,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation)


class StorageDialog(QDialog):
    capacity_ready=Signal(int,object)
    def __init__(self,workspace):
        super().__init__(workspace.window);self.ws=workspace;self.setWindowTitle('存储库与外部播放器');self.resize(730,500)
        from steam_dialogs import DialogPages,dark_titlebar
        from storage_presentation import StorageBar
        dark_titlebar(self);self._generation=0;self._cancel=threading.Event();self._rows={};self._capacity_lock=threading.Lock();self._capacity_pending=None;self._capacity_running=False;self._capacity_closed=False
        self.capacity_ready.connect(self.show_capacity)
        self.resize(940,690)
        outer=QVBoxLayout(self);outer.setContentsMargins(0,0,0,0)
        self.sections=DialogPages('应用设置');outer.addWidget(self.sections)
        layout=self.sections.page('存储空间')
        self.disks=QComboBox();self.disks.setObjectName('diskSelector');layout.addWidget(self.disks)
        self.path_label=QLabel();self.path_label.setObjectName('muted');self.path_label.setWordWrap(True);layout.addWidget(self.path_label)
        self.capacity=QLabel();layout.addWidget(self.capacity)
        self.usage=StorageBar();layout.addWidget(self.usage)
        self.legend=QLabel();self.legend.setObjectName('muted');self.legend.setWordWrap(True);layout.addWidget(self.legend)
        semantics=QLabel('本库仅统计此目录内已登记文件的逻辑大小，外部引用不计入；与实际磁盘分配可能不同。');semantics.setObjectName('storageSemantics');semantics.setWordWrap(True);layout.addWidget(semantics)
        heading=QHBoxLayout();self.entries_heading=QLabel();self.entries_heading.setObjectName('sectionTitle');heading.addWidget(self.entries_heading)
        rule=QFrame();rule.setFrameShape(QFrame.Shape.HLine);rule.setObjectName('storageRule');heading.addWidget(rule,1);layout.addLayout(heading)
        self.entries=QListWidget();self.entries.setObjectName('storageEntries');self.entries.setSpacing(2);self.entries.setMinimumHeight(220);self.entries.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff);layout.addWidget(self.entries,1)
        self.list=QListWidget();self.list.hide()
        row=QHBoxLayout();layout.addLayout(row)
        for title,fn in [('添加存储库',self.add),('移除存储库记录',self.remove)]:
            button=QPushButton(title);button.clicked.connect(fn);row.addWidget(button)
        note=QLabel('每季新增下载使用一个存储库；外部文件保留原路径。移除记录不会删除媒体文件。');note.setWordWrap(True);note.setObjectName('muted');layout.addWidget(note)
        import preferences
        player_layout=self.sections.page('外部播放器')
        player_layout.addWidget(QLabel('留空使用系统默认播放器'))
        line=QHBoxLayout();self.player=QLineEdit(preferences.load()['external_player']);line.addWidget(self.player)
        choose=QPushButton('选择 EXE');choose.clicked.connect(self.choose);line.addWidget(choose);player_layout.addLayout(line)
        save=QPushButton('保存播放器设置');save.setObjectName('primary');save.clicked.connect(self.save);player_layout.addWidget(save);player_layout.addStretch()
        self.disks.currentIndexChanged.connect(self.select_storage)
        self.list.currentRowChanged.connect(self.disks.setCurrentIndex)
        self.reload()
    def reload(self):
        selected=self.disks.currentData();self.list.blockSignals(True);self.disks.blockSignals(True)
        self.list.clear();self.disks.clear()
        for lib in self.ws.store.read()['libraries']:
            item=QListWidgetItem(lib['label']+'  ·  '+lib['path']);item.setData(Qt.ItemDataRole.UserRole,lib['id']);self.list.addItem(item)
            self.disks.addItem(lib['label']+'  ·  '+str(Path(lib['path']).anchor),lib['id'])
        index=max(0,self.disks.findData(selected));self.disks.setCurrentIndex(index);self.list.setCurrentRow(index)
        self.list.blockSignals(False);self.disks.blockSignals(False);self.select_storage()
    def select_storage(self,*_):
        from storage_presentation import StorageRow
        self._cancel.set();self._cancel=threading.Event();self._generation+=1;generation=self._generation;cancel=self._cancel
        self.list.blockSignals(True);self.list.setCurrentRow(self.disks.currentIndex());self.list.blockSignals(False)
        data=self.ws.store.read();lib=next((v for v in data['libraries'] if v['id']==self.disks.currentData()),None)
        self.entries.clear();self._rows={};self.usage.set_usage()
        if not lib:
            self.path_label.setText('添加专用影片目录以查看存储空间');self.capacity.setText('暂无存储库');self.legend.clear();self.entries_heading.setText('项目 0');return
        self.path_label.setText(lib['path']);self.capacity.setText('读取中 · 正在读取磁盘与已登记文件');self.legend.setText('只读取登记文件大小，不扫描目录。')
        works=[w for w in data['works'] if w.get('library_id')==lib['id']]
        self.entries_heading.setText(f'项目 {len(works)}')
        for work in works:
            episodes=[e for e in data['episodes'] if e['work_id']==work['id']]
            sites=' / '.join(dict.fromkeys(v.get('site','未知来源') for v in work.get('sources',[]))) or '外部导入'
            item=QListWidgetItem(work['title']+' '+work.get('season','')+' '+sites)
            item.setSizeHint(QSize(0,106));self.entries.addItem(item)
            row=StorageRow(work,thumbnail({**work,'banner':work.get('banner') or work.get('cover')},106,68,banner=True),len(episodes));self.entries.setItemWidget(item,row);self._rows[work['id']]=row
        if not works:self.entries.addItem('此存储库暂无季条目。新增下载或导入后将在这里显示。')
        with self._capacity_lock:
            if self._capacity_closed:return
            self._capacity_pending=(generation,lib['path'],works,data['episodes'],cancel)
            if self._capacity_running:return
            self._capacity_running=True
        threading.Thread(target=self._read_capacity,daemon=True,name='storage-metadata').start()
    def _read_capacity(self):
        from storage_presentation import storage_snapshot
        while True:
            with self._capacity_lock:
                request=self._capacity_pending;self._capacity_pending=None
                if self._capacity_closed or request is None:
                    self._capacity_running=False;return
            generation,path,works,episodes,cancel=request
            try:
                result=None if cancel.is_set() else storage_snapshot(path,works,episodes,cancel)
                if result is not None and not cancel.is_set():
                    try:self.capacity_ready.emit(generation,result)
                    except RuntimeError:pass # Native dialog may already have been deleted.
            except Exception as error:
                diagnostics.exception('storage.metadata.failed',error,error.__traceback__)
    def show_capacity(self,generation,result):
        if self._capacity_closed or generation!=self._generation:return
        from storage_presentation import byte_text
        for key,value in result['rows'].items():
            if key in self._rows:self._rows[key].set_measurement(value)
        disk=result['disk'];unknown=sum(v['unknown'] for v in result['rows'].values())
        if disk:
            self.capacity.setText(f'{byte_text(disk.total)} 中共有 {byte_text(disk.free)} 可用')
            self.usage.set_usage(disk.total,disk.used,result['owned'])
            other=max(0,disk.used-result['owned'])
            self.legend.setText(f'<span style="color:#1a9fff">●</span> 本库登记 {byte_text(result["owned"])}　'
                f'<span style="color:#a99ae9">●</span> 其他 / 未归类约 {byte_text(other)}　'
                f'<span style="color:#74808e">●</span> 可用 {byte_text(disk.free)}')
        else:self.capacity.setText('存储库离线 · 无法读取磁盘空间');self.legend.setText('磁盘容量未知；已登记文件仍保留在影片库。')
        note='本库仅含此目录内已登记且可读取的存储文件；外部引用归入其他。大小按逻辑字节统计，可能与磁盘实际分配不同。'
        if unknown:note+=f' {unknown} 个文件不可用，其大小未计入。'
        self.legend.setToolTip(note);self.path_label.setToolTip(note)
    def done(self,result):
        self._stop_capacity();super().done(result)
    def closeEvent(self,event):
        self._stop_capacity();super().closeEvent(event)
    def _stop_capacity(self):
        with self._capacity_lock:
            self._capacity_closed=True;self._capacity_pending=None;self._cancel.set()
    def add(self):
        path=QFileDialog.getExistingDirectory(self,'选择专用影片目录',options=QFileDialog.Option.DontUseNativeDialog)
        if path:
            try:self.ws.store.library(path);self.reload();self.ws.refresh(force=True);self.ws.add_controls.reload_libraries()
            except Exception as error:message(self,error)
    def remove(self):
        if self.list.currentItem():
            try:self.ws.store.remove_library(self.list.currentItem().data(Qt.ItemDataRole.UserRole));self.reload();self.ws.refresh(force=True)
            except Exception as error:message(self,error)
    def choose(self):
        path,_=QFileDialog.getOpenFileName(self,'选择外部播放器','','应用程序 (*.exe)')
        if path:self.player.setText(path)
    def save(self):
        import preferences
        value=self.player.text().strip()
        if value and (not Path(value).is_file() or Path(value).suffix.lower()!='.exe'):message(self,'请选择存在的播放器 EXE。');return
        preferences.update(external_player=value);self.accept()


class MappingDelegate(QStyledItemDelegate):
    def __init__(self,parent):super().__init__(parent);self.options=[]
    def createEditor(self,parent,option,index):
        combo=QComboBox(parent);combo.addItem('请选择对应选集',None)
        for label,key in self.options:combo.addItem(label,key)
        return combo
    def setEditorData(self,editor,index):editor.setCurrentIndex(max(0,editor.findData(index.data(Qt.ItemDataRole.UserRole))))
    def setModelData(self,editor,model,index):
        model.setData(index,editor.currentText(),Qt.ItemDataRole.EditRole);model.setData(index,editor.currentData(),Qt.ItemDataRole.UserRole)


class AddControls(QFrame):
    def __init__(self,workspace):
        super().__init__();self.ws=workspace;self.window=workspace.window;self.setObjectName('card');self.collection=None
        layout=QVBoxLayout(self);row=QHBoxLayout();layout.addLayout(row)
        self.name=QLineEdit();self.name.setPlaceholderText('作品名称');self.season=QLineEdit();self.season.setPlaceholderText('季名；信息不足请确认，不是播放线路')
        row.addWidget(self.name,2);row.addWidget(self.season,1)
        line=QHBoxLayout();self.library=QComboBox();line.addWidget(QLabel('存储库'));line.addWidget(self.library,1)
        manage=QPushButton('管理库');manage.clicked.connect(workspace.storage);line.addWidget(manage);layout.addLayout(line)
        line=QHBoxLayout();self.height=QComboBox()
        for n in (2160,1440,1080,720,480,360):self.height.addItem(str(n)+'P 优先',n)
        self.height.setCurrentIndex(2);self.strict=QCheckBox('严格画质：缺失时跳过');line.addWidget(self.height);line.addWidget(self.strict);layout.addLayout(line)
        self.merge=QComboBox();self.merge.addItem('新建或更新同来源作品',None);self.merge.currentIndexChanged.connect(self.rebuild_mapping);layout.addWidget(self.merge)
        self.route=QComboBox();self.route.currentIndexChanged.connect(self.change_route);layout.addWidget(self.route);self.route.hide()
        self.notice=QLabel('解析后在这里选择合集；原站不提供合集时保留单集下载。');self.notice.setWordWrap(True);layout.addWidget(self.notice)
        self.table=QTableWidget(0,3);self.table.setHorizontalHeaderLabels(['选集','本地状态','合并对应选集'])
        self.mapping_delegate=MappingDelegate(self.table);self.table.setItemDelegateForColumn(2,self.mapping_delegate)
        self.table.horizontalHeader().setSectionResizeMode(0,QHeaderView.ResizeMode.Stretch);self.table.setMinimumHeight(160);layout.addWidget(self.table)
        line=QHBoxLayout();layout.addLayout(line)
        for text,state in [('全选未下载',True),('全不选',False)]:
            button=QPushButton(text);button.clicked.connect(lambda _,s=state:self.check_all(s));line.addWidget(button)
        new=QPushButton('未匹配集确认为新增');new.clicked.connect(self.map_new);line.addWidget(new)
        self.reload_libraries()
        for combo in (self.library,self.merge,self.route):
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon);combo.setMinimumContentsLength(16)
    def reload_libraries(self):
        old=self.library.currentData();self.library.clear()
        for lib in self.ws.store.read()['libraries']:self.library.addItem(lib['label']+'  ·  '+lib['path'],lib['id'])
        index=self.library.findData(old)
        if index>=0:self.library.setCurrentIndex(index)
    def receive(self,info):
        from collection_media import single
        self.collection=info.get('_collection') or single(info);c=self.collection
        self.route.blockSignals(True);self.route.clear()
        for key,entries in c.get('routes',{}).items():self.route.addItem(f'播放线路 {key} · {len(entries)} 集（不是季）',key)
        self.route.setCurrentIndex(max(0,self.route.findData(c.get('line'))));self.route.setVisible(self.route.count()>1);self.route.blockSignals(False)
        self.name.setText(c['title']);self.season.setText(c.get('season',''));self.reload_libraries()
        self.season.setEnabled(c.get('kind')!='images')
        if c.get('kind')=='images':self.season.clear()
        data=self.ws.store.read();key=source_key(c['site'],c['key'],c.get('variant',''));work=next((w for w in data['works'] if any(s['key']==key for s in w['sources'])),None)
        self.merge.blockSignals(True);self.merge.clear();self.merge.addItem('新建或更新同来源作品',None)
        for w in data['works']:
            if w['id']!=(work or {}).get('id') and w['kind']==c.get('kind','video'):self.merge.addItem('确认合并到：'+w['title']+' '+w['season'],w['id'])
        self.merge.blockSignals(False)
        if work:
            self.name.setText(work['title']);self.season.setText(work['season']);index=self.library.findData(work.get('library_id'))
            if index>=0:self.library.setCurrentIndex(index)
        self.table.setRowCount(len(c['entries']))
        source=next((s for s in (work or {}).get('sources',[]) if s['key']==key),{})
        for i,e in enumerate(c['entries']):
            ep=next((v for v in data['episodes'] if v['id']==source.get('mapping',{}).get(e['key'])),None)
            state=self.ws.states.get(ep['id'],ep.get('status','未下载')) if ep else '未下载'
            if ep and self.ws.ep_active(ep):state='已在队列'
            item=QTableWidgetItem(e['title']);item.setFlags(item.flags()|Qt.ItemFlag.ItemIsUserCheckable);item.setCheckState(Qt.CheckState.Unchecked if state in ('已下载','已在队列','存储库离线','磁盘离线') else Qt.CheckState.Checked);self.table.setItem(i,0,item)
            status=QTableWidgetItem(state);status.setFlags(status.flags()&~Qt.ItemFlag.ItemIsEditable);self.table.setItem(i,1,status)
        self.rebuild_mapping();self.notice.setText(f"发现 {len(c['entries'])} 个选集。默认跳过已下载/已排队；缺少季信息时请填写季名，单部作品可留空。")
        if len(c['entries'])>1 and not self.season.text():self.season.setFocus()
    def change_route(self):
        if not self.collection or not self.route.currentData():return
        c=copy.deepcopy(self.collection);c['line']=self.route.currentData();c['variant']=c['line'];c['entries']=c['routes'][c['line']];c['url']=c['entries'][0]['url']
        name,season=self.name.text(),self.season.text();self.receive({**self.window.info,'_collection':c});self.name.setText(name);self.season.setText(season)
    def rebuild_mapping(self):
        work_id=self.merge.currentData();episodes=[e for e in self.ws.store.read()['episodes'] if e['work_id']==work_id]
        self.mapping_delegate.options=[('确认为新增选集','__new__')]+[(e['label'],e['id']) for e in episodes]
        def number(label):
            m=re.search(r'第\s*(\d+)\s*[集话期]',label);return int(m[1]) if m else None
        for row in range(self.table.rowCount()):
            entry=self.collection['entries'][row] if self.collection else {};index=number(entry.get('title',''))
            candidates=[e for e in episodes if index is not None and number(e['label'])==index]
            match=candidates[0] if len(candidates)==1 else None
            item=QTableWidgetItem(('建议：'+match['label']) if match else ('双击选择对应集' if work_id else '保持原来源'))
            item.setData(Qt.ItemDataRole.UserRole,match['id'] if match else None)
            if not work_id:item.setFlags(item.flags()&~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row,2,item)
    def map_new(self):
        if not self.merge.currentData():return
        for i in range(self.table.rowCount()):
            item=self.table.item(i,2)
            if item.data(Qt.ItemDataRole.UserRole) is None:item.setText('确认为新增选集');item.setData(Qt.ItemDataRole.UserRole,'__new__')
    def check_all(self,state):
        for i in range(self.table.rowCount()):
            allowed=self.table.item(i,1).text() not in ('已下载','已在队列','存储库离线','磁盘离线')
            self.table.item(i,0).setCheckState(Qt.CheckState.Checked if state and allowed else Qt.CheckState.Unchecked)
    def submit(self):
        if not self.collection:raise ValueError('请先解析链接。')
        if self.library.currentData() is None:raise ValueError('请先添加存储库。')
        if len(self.collection['entries'])>1 and not self.season.text().strip():raise ValueError('网站没有明确季信息，请填写季名（播放线路不是季）。')
        mapping={}
        if self.merge.currentData():
            for i,e in enumerate(self.collection['entries']):mapping[e['key']]=self.table.item(i,2).data(Qt.ItemDataRole.UserRole)
            if any(v is None for v in mapping.values()):raise ValueError('请逐集确认对应关系。')
            if QMessageBox.question(self,'确认来源合并','确认上述选集对应关系，并将此来源归入所选季？不会覆盖已有视频。')!=QMessageBox.StandardButton.Yes:return None
        wid,eids=self.ws.store.ingest(self.collection,self.name.text(),self.season.text(),self.library.currentData(),self.merge.currentData(),mapping)
        selected=[eids[i] for i in range(len(eids)) if self.table.item(i,0).checkState()==Qt.CheckState.Checked]
        kind=self.window.quality.currentData() if self.window.info.get('_kind')=='images' else None
        policy={'height':self.height.currentData(),'strict':self.strict.isChecked(),'kind':kind}
        count=self.ws.enqueue(wid,selected,policy,self.window.info)
        self.ws.refresh(force=True);self.ws.show_work(wid);self.ws.cache_covers(wid);return count


class ImportDialog(QDialog):
    def __init__(self,workspace):
        super().__init__(workspace.window);self.ws=workspace;self.rows=[];self.setWindowTitle('已有视频 · 预览后入库');self.resize(1000,660)
        layout=QVBoxLayout(self);note=QLabel('只导入你勾选的文件，保持原位置，不移动、不重命名。作品/季/选集均可在表格中修正。');note.setWordWrap(True);layout.addWidget(note)
        row=QHBoxLayout();layout.addLayout(row)
        for text,fn in [('读取下载记录',self.history),('扫描所选文件夹',self.scan),('选择视频文件',self.files)]:
            b=QPushButton(text);b.clicked.connect(fn);row.addWidget(b)
        self.table=QTableWidget(0,4);self.table.setHorizontalHeaderLabels(['原始路径（勾选导入）','作品名','季名（可空）','选集名'])
        self.table.horizontalHeader().setSectionResizeMode(0,QHeaderView.ResizeMode.Stretch);layout.addWidget(self.table)
        self.status=QLabel();self.status.setWordWrap(True);layout.addWidget(self.status)
        self.apply_button=QPushButton('确认导入所选文件');self.apply_button.clicked.connect(self.apply);layout.addWidget(self.apply_button)
        self.target=QComboBox();self.target.addItem('按表格新建／更新本地条目',None)
        for work in self.ws.store.read()['works']:self.target.addItem('归入已有：'+work['title']+' '+work['season'],work['id'])
        self.target.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon);self.target.setMinimumContentsLength(20)
        layout.insertWidget(1,self.target);self.target.currentIndexChanged.connect(self.mapping_changed)
        self.table.setColumnCount(5);self.table.setHorizontalHeaderLabels(['原始路径（勾选导入）','作品名','季名（可空）','选集名','对应库选集（双击确认）'])
        self.delegate=MappingDelegate(self.table);self.table.setItemDelegateForColumn(4,self.delegate)
    def receive(self,rows):
        self.rows=rows;self.table.setRowCount(len(rows));self.status.setText(f'发现 {len(rows)} 个媒体文件；请检查归属后勾选。')
        for i,(path,title,season,label) in enumerate(rows):
            for j,value in enumerate((path,title,season,label)):
                item=QTableWidgetItem(value)
                if j==0:item.setFlags((item.flags()|Qt.ItemFlag.ItemIsUserCheckable)&~Qt.ItemFlag.ItemIsEditable);item.setCheckState(Qt.CheckState.Unchecked)
                self.table.setItem(i,j,item)
        self.mapping_changed()
    def mapping_changed(self):
        key=self.target.currentData();episodes=[e for e in self.ws.store.read()['episodes'] if e['work_id']==key]
        self.delegate.options=[('确认为新增选集','__new__')]+[(e['label'],e['id']) for e in episodes]
        def number(label):
            m=re.search(r'第\s*(\d+)\s*[集话期]',label);return int(m[1]) if m else None
        for i in range(self.table.rowCount()):
            label=self.table.item(i,3).text();index=number(label)
            matches=[e for e in episodes if e['label']==label or index is not None and number(e['label'])==index]
            match=matches[0] if len(matches)==1 else None
            item=QTableWidgetItem('建议：'+match['label'] if match else '双击确认归属' if key else '按表格归档')
            item.setData(Qt.ItemDataRole.UserRole,match['id'] if match else None)
            if not key:item.setFlags(item.flags()&~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(i,4,item)
    def history(self):
        self.ws.jobs.start(lambda cancel:[(r['result']['path'],r['title'],'',r['title']) for r in tasks.records() if r['state']=='completed' and r.get('result',{}).get('path') and Path(r['result']['path']).is_file()],self.receive,lambda e:message(self,e))
    def scan(self):
        folder=QFileDialog.getExistingDirectory(self,'只扫描此文件夹',options=QFileDialog.Option.DontUseNativeDialog)
        if not folder:return
        from media_library import safe_folder
        try:
            folder=str(safe_folder(folder))
            if Path(folder)==Path(Path(folder).anchor):raise ValueError('请选择具体子文件夹，不扫描整块磁盘。')
        except Exception as error:message(self,error);return
        self.status.setText('正在扫描所选文件夹…')
        def scan(cancel):
            found=[]
            for root,dirs,files in os.walk(folder,followlinks=False):
                dirs[:]=[d for d in dirs if not d.startswith('.') and not Path(root,d).is_symlink() and not (getattr(Path(root,d).stat(),'st_file_attributes',0)&1024)]
                if cancel.is_set():break
                for name in files:
                    if Path(name).suffix.lower() in ('.mp4','.mkv','.avi','.mov','.webm','.zip') and not Path(root,name).is_symlink():
                        found.append((str(Path(root,name)),Path(root).name,'',Path(name).stem))
                        if len(found)>=10000:raise ValueError('文件超过10000个，请选择更小的目录。')
            return found
        self.ws.jobs.start(scan,self.receive,lambda e:message(self,e))
    def files(self):
        files,_=QFileDialog.getOpenFileNames(self,'选择已有媒体','','媒体 (*.mp4 *.mkv *.mov *.avi *.webm *.zip)')
        self.receive([(f,Path(f).stem,'',Path(f).stem) for f in files])
    def apply(self):
        selected=[(*tuple(self.table.item(i,j).text() for j in range(4)),self.table.item(i,4).data(Qt.ItemDataRole.UserRole)) for i in range(self.table.rowCount()) if self.table.item(i,0).checkState()==Qt.CheckState.Checked]
        if not selected:return
        target=self.target.currentData()
        if target and any(eid is None for *_,eid in selected):message(self,'请逐项确认对应选集，或明确选择作为新增选集。');return
        self.apply_button.setEnabled(False)
        def apply(cancel):
            count=0
            for path,title,season,label,episode_id in selected:
                if cancel.is_set():break
                self.ws.store.import_file(path,title,season,label,target,episode_id);count+=1
            return count
        def done(count):self.apply_button.setEnabled(True);self.status.setText(f'已原位导入 {count} 个文件。');self.ws.refresh(force=True)
        self.ws.jobs.start(apply,done,lambda e:(self.apply_button.setEnabled(True),message(self,e)))


class Workspace(QObject):
    def __init__(self,window,legacy_content,old_shell):
        super().__init__(window);self.window=window;self.store=Catalogue();self.jobs=Jobs(window);self.states={};self.current=None;self.fingerprint=None;self.cover_pending=set();self.check_pending=set();self.state_pending=False;self.last_scan=0
        self.import_dialog=None;self.total_samples=0;self.closed=False;self.check_retry={};self.episode_page=0;self.episode_checked=set();self.refresh_pending=False;self.batch_preparing=False;self.batch_worker=False;self.batch_generation=0;self.prepared_batch=deque();self.task_artwork={};self.side_group_state={}
        self.add_dialog=QDialog(window);self.add_dialog.setWindowTitle('添加影片 · 解析与入库');self.add_dialog.resize(1020,850)
        self.add_dialog.setMinimumSize(780,600);layout=QVBoxLayout(self.add_dialog);scroll=QScrollArea();scroll.setWidgetResizable(True);legacy_content.setObjectName('addFilmContent');scroll.viewport().setObjectName('addFilmViewport');scroll.setWidget(legacy_content);layout.addWidget(scroll)
        self.add_dialog.setStyleSheet("QWidget#addFilmContent,QWidget#addFilmViewport {background:#1b2838;color:#d6d7d8;}")
        self.add_controls=AddControls(self);legacy_content.layout().insertWidget(2,self.add_controls)
        window.download_button.setText('加入库并下载');window.path_button.hide();window.output_path.hide()
        self.add_dialog.finished.connect(self.add_closed)
        shell=QWidget(objectName='steamShell');outer=QVBoxLayout(shell);outer.setContentsMargins(0,0,0,0);outer.setSpacing(0)
        from steam_widgets import Chrome,EdgeResize,CoverDelegate,control_icon
        window.setWindowFlags(window.windowFlags()|Qt.WindowType.FramelessWindowHint)
        self.chrome=Chrome(window);outer.addWidget(self.chrome);self.edge_resize=EdgeResize(window)
        shell.setStyleSheet("""
            QWidget#steamShell {background:#171d25;}
            QFrame#windowChrome {background:#171d25;color:#88939f;}
            QPushButton#windowControl {border:0;background:transparent;padding:0;color:#88939f;min-height:0;}
            QPushButton#windowControl:hover {background:#3d434c;color:white;}
            QPushButton#chromeMenu {background:transparent;border:0;padding:0 2px;min-height:18px;color:#929ca7;font-size:12px;} QPushButton#chromeMenu::menu-indicator {image:none;}
            QPushButton#steamNav::menu-indicator {image:none;}
            QFrame#librarySidebar {background:#242930;}
            QFrame#librarySidebar QLineEdit,QFrame#librarySidebar QComboBox {background:#202429;border:0;border-radius:0;padding:6px;}
            QTreeWidget#sideCatalogue {background:#242930;border:0;font-size:12px;outline:0;}
            QTreeWidget#sideCatalogue::item {height:25px;padding:0 5px;}
            QTreeWidget#sideCatalogue::item:selected {background:#3e506a;color:white;}
            QTreeWidget#sideCatalogue::item:hover {background:#353d48;}
            QWidget#libraryHome,QWidget#seasonDetails,QListWidget#covers {background:#2b3038;}
            QListWidget#covers {outline:0;border:0;}
            QScrollBar:horizontal {background:#2b3038;height:6px;} QScrollBar::handle:horizontal {background:#535c68;min-width:40px;} QScrollBar::add-line:horizontal,QScrollBar::sub-line:horizontal {width:0;} QScrollBar::add-page:horizontal,QScrollBar::sub-page:horizontal {background:#2b3038;}
            QTableWidget {background:#252a32;alternate-background-color:#2a3038;} QHeaderView::section {background:#303640;color:#a5b0be;}
            QTableCornerButton::section {background:#303640;}
            QPushButton#steamNav {border:0;background:transparent;font-size:19px;padding:0 8px;color:#d7d8da;}
            QPushButton#steamNavActive {border:0;border-bottom:2px solid #1a9fff;background:transparent;font-size:19px;padding:0 5px;color:#1a9fff;}
            QPushButton#steamNav:focus,QPushButton#steamNavActive:focus,QPushButton#windowControl:focus,QPushButton#chromeMenu:focus {background:#354554;}
            QLabel#shelfHeading {color:#d5d8dd;font-size:15px;}
            QPushButton#playAction {background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #75b022,stop:1 #219e45);border:0;color:white;font-size:20px;padding:8px 30px;}
            QFrame#actionStrip {background:#252c35;border-bottom:1px solid #444c58;}
        """)
        top=QHBoxLayout();top.setContentsMargins(12,1,18,20);top.setSpacing(14)
        previous=QPushButton();previous.setIcon(control_icon('back'));previous.setAccessibleName('返回影片库');previous.setToolTip('返回影片库');previous.setObjectName('steamNav');previous.clicked.connect(self.home);top.addWidget(previous)
        library=QPushButton('影片库');library.setObjectName('steamNavActive');library.clicked.connect(self.home);self.library_nav=library;top.addWidget(library)
        window.task_button.setText('下载');window.task_button.setObjectName('steamNav');self.tasks_nav=window.task_button;top.addWidget(window.task_button)
        tools=QPushButton('管理 ▾');tools.setObjectName('steamNav');menu=QMenu(tools)
        for text,fn in [('设置',window.show_settings),('存储库与播放器',self.storage),('导入已有视频',self.import_files),('账号与扫码登录',window.show_accounts),('日志与故障反馈',window.show_diagnostics),('查看已适配列表',window.show_supported)]:menu.addAction(text,fn)
        tools.setMenu(menu);top.addWidget(tools);top.addStretch();top.addWidget(QLabel('WEB VIDEO DOWNLOADER',objectName='muted'));outer.addLayout(top)
        central=QSplitter();self.central_splitter=central;central.setHandleWidth(2);sidebar=QFrame(objectName='librarySidebar');self.sidebar=sidebar;sidebar.setMinimumWidth(190);sidebar.setMaximumWidth(390);left=QVBoxLayout(sidebar);left.setContentsMargins(6,5,6,4);left.setSpacing(5)
        home_button=QPushButton('主页   ▦');home_button.setObjectName('sideButton');home_button.clicked.connect(self.home);left.addWidget(home_button)
        self.filter=QComboBox();self.filter.addItems(['全部影片','已下载','未下载完整','图文作品']);self.filter.currentIndexChanged.connect(self.home);left.addWidget(self.filter)
        self.search=QLineEdit();self.search.setPlaceholderText('⌕  搜索影片 / 季');self.search.textChanged.connect(self.home);left.addWidget(self.search)
        self.library_filter=QComboBox();self.library_filter.currentIndexChanged.connect(self.home);left.addWidget(self.library_filter)
        self.side_count=QLabel('− 影片库');left.addWidget(self.side_count)
        self.side_titles=QTreeWidget();self.side_titles.setHeaderHidden(True);self.side_titles.setIndentation(12);self.side_titles.setRootIsDecorated(True);self.side_titles.setObjectName('sideCatalogue');self.side_titles.setIconSize(QSize(20,20));self.side_titles.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff);self.side_items={}
        self.side_titles.itemClicked.connect(lambda item,column:self.show_work(item.data(0,Qt.ItemDataRole.UserRole)) if item.data(0,Qt.ItemDataRole.UserRole) else None);left.addWidget(self.side_titles,1)
        window.update_button.setObjectName('sideButton');left.addWidget(window.update_button);central.addWidget(sidebar)
        self.pages=QStackedWidget();central.addWidget(self.pages);central.setStretchFactor(1,1);central.setSizes([265,1135]);outer.addWidget(central,1)
        home=QWidget(objectName='libraryHome');body=QVBoxLayout(home);body.setContentsMargins(20,9,20,10);body.setSpacing(9)
        self.hero=Artwork();self.hero.hide()
        self.recent_label=QLabel('最近入库',objectName='shelfHeading');body.addWidget(self.recent_label)
        self.recent=QListWidget();self.recent.setObjectName('covers');self.recent.setViewMode(QListView.ViewMode.IconMode);self.recent.setFlow(QListView.Flow.LeftToRight);self.recent.setWrapping(False);self.recent.setMovement(QListView.Movement.Static);self.recent.setFixedHeight(257);self.recent.setGridSize(QSize(153,246));self.recent.setIconSize(QSize(140,210));self.recent.setItemDelegate(CoverDelegate(self.recent));self.recent.setHorizontalScrollMode(QListView.ScrollMode.ScrollPerPixel);self.recent.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.recent.itemClicked.connect(lambda item:self.show_work(item.data(Qt.ItemDataRole.UserRole)));body.addWidget(self.recent)
        shelf=QHBoxLayout();self.shelf_label=QLabel('我的影片',objectName='shelfHeading');shelf.addWidget(self.shelf_label);shelf.addSpacing(15);shelf.addWidget(QLabel('排序方式：',objectName='muted'))
        self.sort=QComboBox();self.sort.addItems(['最近入库','影片名称']);self.sort.currentIndexChanged.connect(self.home);shelf.addWidget(self.sort);shelf.addStretch();body.addLayout(shelf)
        self.tiles=QListWidget();self.tiles.setObjectName('covers');self.tiles.setViewMode(QListView.ViewMode.IconMode);self.tiles.setResizeMode(QListView.ResizeMode.Adjust);self.tiles.setMovement(QListView.Movement.Static)
        self.tiles.setIconSize(QSize(152,228));self.tiles.setGridSize(QSize(170,275));self.tiles.setSpacing(0);self.tiles.setWordWrap(True);self.tiles.setMouseTracking(True);self.tiles.setItemDelegate(CoverDelegate(self.tiles))
        self.tiles.itemClicked.connect(lambda item:self.show_work(item.data(Qt.ItemDataRole.UserRole)));self.tiles.itemActivated.connect(lambda item:self.show_work(item.data(Qt.ItemDataRole.UserRole)))
        body.addWidget(self.tiles,1);self.empty=QLabel('你的影片库，由此开始。\n\n点击底部「添加影片」，或从管理菜单导入已有视频。');self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter);body.addWidget(self.empty);self.pages.addWidget(home)
        self.details=QWidget(objectName='seasonDetails');detail=QVBoxLayout(self.details);detail.setContentsMargins(0,0,0,14);detail.setSpacing(12)
        self.banner=Artwork();self.banner.setFixedHeight(275);detail.addWidget(self.banner)
        self.work_title=QLabel(objectName='sectionTitle');self.work_title.setWordWrap(True);self.work_title.setContentsMargins(20,0,20,0)
        action_strip=QFrame(objectName='actionStrip');row=QHBoxLayout(action_strip);row.setContentsMargins(20,10,20,10);self.download_missing=QPushButton('↓ 下载未下载集',objectName='playAction');self.download_missing.clicked.connect(self.download_work);row.addWidget(self.download_missing)
        self.source=QComboBox();self.source.setMinimumWidth(130);self.source.currentIndexChanged.connect(self.source_changed);row.addWidget(self.source)
        self.source.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon);self.source.setMinimumContentsLength(12);self.source.setMaximumWidth(280)
        back=QPushButton('返回影片库');back.clicked.connect(self.home);row.addWidget(back);row.addStretch();more=QPushButton('管理此季 ▾');management=QMenu(more)
        for text,fn in [('刷新选集与封面',self.refresh_season),('手动更换小封面',lambda:self.manual_cover(False)),('手动更换大封面',lambda:self.manual_cover(True)),('恢复本季播放确认',lambda:self.store.patch_work(self.current,no_play_prompt=False)),('迁移本季到其他存储库',self.migrate),('移除本季库记录（保留文件）',self.remove_work)]:management.addAction(text,fn)
        more.setMenu(management);row.addWidget(more);detail.addWidget(action_strip);detail.addWidget(self.work_title)
        options=QHBoxLayout();options.setContentsMargins(20,0,20,0);self.check_enabled=QCheckBox('定时检查新集（只提示）');self.check_hours=QSpinBox();self.check_hours.setRange(1,168);self.check_hours.setSuffix(' 小时');options.addWidget(self.check_enabled);options.addWidget(self.check_hours)
        self.check_enabled.clicked.connect(self.save_checks);self.check_hours.valueChanged.connect(self.save_checks);self.season_info=QLabel();self.season_info.setWordWrap(True);options.addWidget(self.season_info,1);detail.addLayout(options)
        paging=QHBoxLayout();paging.setContentsMargins(20,0,20,0);self.episode_search=QLineEdit();self.episode_search.setPlaceholderText('搜索本季选集');self.episode_search.textChanged.connect(self.episode_filter_changed);paging.addWidget(self.episode_search,1)
        self.page_label=QLabel();paging.addWidget(self.page_label)
        self.previous=QPushButton('上一页');self.following=QPushButton('下一页');self.previous.clicked.connect(lambda:self.turn_page(-1));self.following.clicked.connect(lambda:self.turn_page(1));paging.addWidget(self.previous);paging.addWidget(self.following);detail.addLayout(paging)
        self.episodes=QTableWidget(0,5);self.episodes.setHorizontalHeaderLabels(['选集（勾选下载）','状态','位置/来源','播放/下载','管理'])
        self.episodes.horizontalHeader().setSectionResizeMode(0,QHeaderView.ResizeMode.Stretch);self.episodes.horizontalHeader().setSectionResizeMode(2,QHeaderView.ResizeMode.Stretch);self.episodes.setColumnWidth(1,110);self.episodes.setColumnWidth(3,110);self.episodes.setColumnWidth(4,55);self.episodes.verticalHeader().setDefaultSectionSize(43);self.episodes.verticalHeader().hide();self.episodes.setShowGrid(False);self.episodes.setAlternatingRowColors(True);detail.addWidget(self.episodes,1)
        self.episodes.itemChanged.connect(self.episode_check_changed)
        buttons=QHBoxLayout();buttons.setContentsMargins(20,0,20,0)
        for title,fn in [('全选筛选结果中未下载集',self.check_missing),('全不选',self.uncheck),('下载所选',self.download_checked)]:
            b=QPushButton(title);b.clicked.connect(fn);buttons.addWidget(b)
        detail.addLayout(buttons);self.pages.addWidget(self.details)
        from task_view import TaskDialog
        window.task_dialog=TaskDialog(window.queue,lambda key:window.resume_task(key,report_error=False),window,cover_provider=lambda task:self.task_artwork.get(task['id'],''))
        window.task_dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose,False);window.task_dialog.setWindowFlags(Qt.WindowType.Widget)
        task_page=QWidget();tasks_layout=QVBoxLayout(task_page);tasks_layout.setContentsMargins(20,12,20,12);window.total_speed_label.setObjectName('sectionTitle');tasks_layout.addWidget(window.total_speed_label)
        self.trend=Trend();self.trend.setMinimumHeight(160);self.trend.setMaximumHeight(180);tasks_layout.addWidget(self.trend);tasks_layout.addWidget(window.task_dialog,1);self.pages.addWidget(task_page)
        footer=QHBoxLayout();footer.setContentsMargins(12,3,12,3);add=QPushButton('⊞  添加影片');add.setObjectName('sideButton');add.clicked.connect(self.open_add);footer.addWidget(add);footer.addStretch();self.status=QPushButton('暂无任务');self.status.setObjectName('sideButton');self.status.clicked.connect(self.show_tasks);footer.addWidget(self.status);footer.addStretch()
        floating=QPushButton('悬浮框');floating.clicked.connect(lambda:window.desktop.set_float_enabled(not window.desktop.cfg['float_enabled']));footer.addWidget(floating);outer.addLayout(footer)
        window.setCentralWidget(shell);old_shell.deleteLater()
        area=window.screen().availableGeometry();width=max(750,area.width()-40);height=max(480,area.height()-60)
        window.setMinimumSize(min(950,width),min(700,height));window.resize(min(1400,width),min(900,height))
        self.timer=QTimer(self);self.timer.setInterval(1000);self.timer.timeout.connect(self.tick);self.timer.start()
        window.queue.completed.connect(self.schedule_refresh);window.queue.changed.connect(self.schedule_refresh)
        self.window.installEventFilter(self);self.update_responsive_layout()
        self.refresh(True)

    def eventFilter(self,obj,event):
        if obj is self.window and event.type()==QEvent.Type.Resize:self.update_responsive_layout()
        return super().eventFilter(obj,event)
    def update_responsive_layout(self):
        self.banner.setFixedHeight(max(155,min(290,int(self.window.height()*.31))))
        self.episodes.setColumnHidden(2,self.window.width()<1100)
    def active_ids(self):return set(self.window.queue.active)|{v[0] for v in self.window.queue.waiting}
    def schedule_refresh(self,*_):
        if self.closed or self.refresh_pending:return
        self.refresh_pending=True
        def refresh():self.refresh_pending=False;self.refresh()
        QTimer.singleShot(250,refresh)
    def ep_active(self,ep):return bool(set(ep.get('task_ids',[ep.get('task_id')]))&self.active_ids())
    def unfinished(self,work_id):
        ids={key for e in self.store.read()['episodes'] if e['work_id']==work_id for key in e.get('task_ids',[e.get('task_id')])}
        return any(row['id'] in ids and row['state'] not in tasks.TERMINAL for row in tasks.records())
    def open_add(self):self.add_controls.reload_libraries();self.add_dialog.show();self.add_dialog.raise_();self.add_dialog.activateWindow()
    def add_closed(self,_):
        if self.window.active_thread or self.window.browser_dialog:self.window.cancel()
    def select_navigation(self,tasks_page=False):
        self.sidebar.setVisible(not tasks_page)
        for button,active in [(self.library_nav,not tasks_page),(self.tasks_nav,tasks_page)]:
            button.setObjectName('steamNavActive' if active else 'steamNav');button.style().unpolish(button);button.style().polish(button)
    def home(self,*_):self.current=None;self.pages.setCurrentIndex(0);self.select_navigation();self.refresh(True)
    def show_tasks(self):self.select_navigation(True);self.pages.setCurrentIndex(2);self.window.task_dialog.show();self.window.task_dialog.refresh()
    def storage(self):StorageDialog(self).exec()
    def import_files(self):
        if self.import_dialog is None:self.import_dialog=ImportDialog(self)
        self.import_dialog.show();self.import_dialog.raise_()
    def inspected(self,info):
        self.add_controls.receive(info);images=info.get('_kind')=='images'
        self.window.quality.setVisible(images);self.add_controls.height.setVisible(not images);self.add_controls.strict.setVisible(not images)
        if not images:
            from media_library import format_height
            heights=sorted({format_height(row) for row in info.get('formats',[]) if format_height(row)},reverse=True)
            available=' / '.join(str(int(h))+'P' for h in heights) if heights else '原始流／各集解析后确认'
            self.window.video_meta.setText('首个选集可用：'+available+'。下载按上方画质策略逐集选择。')

    def refresh_task_artwork(self,data):
        from runtime import app_data_dir
        covers={}
        for work in data['works']:
            value=work.get('banner') or work.get('cover') or ''
            path=Path(value) if value else None
            covers[work['id']]=str(path if path.is_absolute() else app_data_dir()/path) if path else ''
        self.task_artwork={task_id:covers.get(ep['work_id'],'') for ep in data['episodes'] for task_id in ep.get('task_ids',[ep.get('task_id')]) if task_id}

    def refresh(self,force=False):
        if self.closed:return
        try:
            data=self.store.read();self.refresh_task_artwork(data);fingerprint=(repr(data),self.search.text(),self.filter.currentIndex(),self.library_filter.currentData(),repr(self.states))
            if not force and fingerprint==self.fingerprint:return
            self.fingerprint=fingerprint;selected_lib=self.library_filter.currentData();self.library_filter.blockSignals(True);self.library_filter.clear();self.library_filter.addItem('全部存储库',None)
            for lib in data['libraries']:self.library_filter.addItem(lib['label'],lib['id'])
            self.library_filter.setCurrentIndex(max(0,self.library_filter.findData(selected_lib)));self.library_filter.blockSignals(False)
            for i in range(self.side_titles.topLevelItemCount()):
                group=self.side_titles.topLevelItem(i);self.side_group_state[group.text(0).split(' (')[0]]=group.isExpanded()
            term=self.search.text().strip().casefold();self.tiles.clear();self.recent.clear();self.side_titles.clear();self.side_items={};shown=[];side_groups={}
            for work in sorted(data['works'],key=(lambda w:w['title'].casefold()) if self.sort.currentIndex()==1 else (lambda w:w['created_at']),reverse=self.sort.currentIndex()==0):
                eps=[e for e in data['episodes'] if e['work_id']==work['id']];count=sum(self.states.get(e['id'])=='已下载' for e in eps)
                if term not in (work['title']+' '+work['season']).casefold():continue
                if selected_lib and work.get('library_id')!=selected_lib:continue
                if self.filter.currentIndex()==1 and not count:continue
                if self.filter.currentIndex()==2 and count==len(eps):continue
                if self.filter.currentIndex()==3 and work['kind']!='images':continue
                text=work['title']+(' · '+work['season'] if work['season'] else '')+f'\n{count} / {len(eps)} 已下载'+(f" · 新增 {work['new_count']}" if work.get('new_count') else '')
                item=QListWidgetItem(QIcon(thumbnail(work)),text);item.setData(Qt.ItemDataRole.UserRole,work['id']);item.setSizeHint(QSize(170,275));self.tiles.addItem(item);shown.append(work)
                category='图文作品' if work['kind']=='images' else '影片与剧集'
                if category not in side_groups:
                    parent=QTreeWidgetItem(self.side_titles,[category]);parent.setExpanded(self.side_group_state.get(category,True));side_groups[category]=parent
                side=QTreeWidgetItem(side_groups[category],[work['title']+(' · '+work['season'] if work['season'] else '')]);side.setIcon(0,QIcon(thumbnail(work,40,60)));side.setData(0,Qt.ItemDataRole.UserRole,work['id']);side.setToolTip(0,side.text(0));self.side_items[work['id']]=side
            self.empty.setVisible(not shown);self.tiles.setVisible(bool(shown));self.recent.setVisible(bool(shown));self.recent_label.setVisible(bool(shown));self.shelf_label.setText(f'我的影片 ({len(shown)})');self.side_count.setText(f'− 影片库 ({len(shown)})')
            for category,parent in side_groups.items():parent.setText(0,f'{category} ({parent.childCount()})')
            for work in sorted(shown,key=lambda w:w['created_at'],reverse=True)[:12]:
                item=QListWidgetItem(QIcon(thumbnail(work,280,420)),work['title']+'\n'+(work.get('season') or '已入库'));item.setData(Qt.ItemDataRole.UserRole,work['id']);self.recent.addItem(item)
            if shown:self.hero.setPixmap(thumbnail(shown[0],1000,190,True));self.hero.setText(shown[0]['title']+' '+shown[0]['season']);self.hero.setToolTip(shown[0]['title'])
            else:self.hero.setPixmap(QPixmap());self.hero.setText('你的影片，集中管理。')
            if self.current:
                self.render_work()
                if self.current in self.side_items:self.side_titles.setCurrentItem(self.side_items[self.current])
        except Exception as error:self.empty.setText('影片库读取失败：'+str(error));diagnostics.event('library.refresh.failed',str(error))

    def show_work(self,key):
        if self.current!=key:self.episode_page=0;self.episode_checked.clear();self.episode_search.blockSignals(True);self.episode_search.clear();self.episode_search.blockSignals(False)
        self.current=key;self.select_navigation();self.pages.setCurrentIndex(1);self.render_work()
        if key in self.side_items:self.side_titles.setCurrentItem(self.side_items[key])
    def episode_filter_changed(self):self.episode_page=0;self.render_work()
    def turn_page(self,delta):self.episode_page+=delta;self.render_work()
    def filtered_episodes(self):
        term=self.episode_search.text().strip().casefold()
        return [e for e in self.store.read()['episodes'] if e['work_id']==self.current and term in e['label'].casefold()]
    def episode_check_changed(self,item):
        if item.column()!=0:return
        key=item.data(Qt.ItemDataRole.UserRole)
        if item.checkState()==Qt.CheckState.Checked:self.episode_checked.add(key)
        else:self.episode_checked.discard(key)
    def render_work(self):
        data=self.store.read();work=next((w for w in data['works'] if w['id']==self.current),None)
        if work is None:self.home();return
        count=sum(self.states.get(e['id'])=='已下载' for e in data['episodes'] if e['work_id']==self.current)
        self.work_title.setText(f'选集与本地文件 · 已下载 {count} 集');self.banner.setText(work['title']+(' · '+work['season'] if work['season'] else ''))
        self.banner.setPixmap(thumbnail(work,1050,180,True));self.check_enabled.blockSignals(True);self.check_hours.blockSignals(True)
        self.check_enabled.setChecked(work['check_enabled']);self.check_hours.setValue(work.get('check_hours',24));self.check_enabled.blockSignals(False);self.check_hours.blockSignals(False)
        self.source.blockSignals(True);self.source.clear()
        for source in work['sources']:self.source.addItem(source['site']+' · '+source['work_key']+(' · 线路'+str(source['variant']) if source.get('variant') else ''),source['key'])
        self.source.setCurrentIndex(max(0,self.source.findData(work.get('default_source'))));self.source.blockSignals(False)
        lib=next((v for v in data['libraries'] if v['id']==work.get('library_id')),None)
        self.season_info.setText(('存储库：'+lib['label'] if lib else '外部文件／未指定存储库')+(f" · 发现 {work['new_count']} 个新集" if work.get('new_count') else ''))
        filtered=self.filtered_episodes();pages=max(1,(len(filtered)+49)//50);self.episode_page=max(0,min(self.episode_page,pages-1))
        self.page_label.setText(f'第 {self.episode_page+1} / {pages} 页 · {len(filtered)} 集 · 已选 {len(self.episode_checked)}')
        self.previous.setEnabled(self.episode_page>0);self.following.setEnabled(self.episode_page<pages-1)
        eps=filtered[self.episode_page*50:self.episode_page*50+50];checked=self.episode_checked
        self.episodes.blockSignals(True)
        self.episodes.setRowCount(0);self.episodes.setRowCount(len(eps))
        for i,ep in enumerate(eps):
            state=self.states.get(ep['id'],ep['status']);active=ep.get('task_id') in self.active_ids()
            if self.ep_active(ep):state='排队/下载/处理中';active=True
            item=QTableWidgetItem(ep['label']);item.setData(Qt.ItemDataRole.UserRole,ep['id']);item.setFlags((item.flags()|Qt.ItemFlag.ItemIsUserCheckable)&~Qt.ItemFlag.ItemIsEditable);item.setCheckState(Qt.CheckState.Checked if ep['id'] in checked else Qt.CheckState.Unchecked);self.episodes.setItem(i,0,item)
            for col,value in [(1,state),(2,'; '.join(('外部文件：' if f['external'] else '')+f['path'] for f in ep['files']) or ', '.join(s['site'] for s in ep['sources']))]:
                cell=QTableWidgetItem(value);cell.setFlags(cell.flags()&~Qt.ItemFlag.ItemIsEditable);cell.setToolTip(value);self.episodes.setItem(i,col,cell)
            button=QPushButton('▶ 播放' if state=='已下载' and any(f['kind']=='video' for f in ep['files']) else '打开原图目录' if state=='已下载' and any(f['kind']=='images' for f in ep['files']) else '↓ 下载')
            button.clicked.connect(lambda _,eid=ep['id']:self.episode_action(eid));button.setEnabled(not active and state not in ('磁盘离线','存储库离线'));self.episodes.setCellWidget(i,3,button)
            manage=QPushButton('···');manage.clicked.connect(lambda _,eid=ep['id']:self.manage_episode(eid));self.episodes.setCellWidget(i,4,manage)
        self.episodes.blockSignals(False)

    def enqueue(self,wid,episode_ids,policy,info=None,allow_existing=False):
        if len(episode_ids)>20 and not allow_existing:return self.enqueue_large(wid,episode_ids,policy,info)
        work=self.store.work(wid);folder=self.store.output(wid);count=0;skipped=[]
        for eid in episode_ids:
            ep=self.store.episode(eid)
            if self.ep_active(ep) or self.states.get(eid)=='已下载' and not allow_existing:continue
            sources=ep['sources'];preferred=work.get('default_source')
            source=next((s for s in sources if s['source']==preferred),None) if preferred else (sources[0] if sources else None)
            if not source or not source['url']:skipped.append(ep['label']+'：所选来源没有该集，请明确切换来源。');continue
            if ep.get('files') and self.store.file_state(ep) in (('存储库离线','磁盘离线') if allow_existing else ('已下载','存储库离线','磁盘离线')):continue
            # Recover the existing task instead of creating another task and cache.
            previous=None
            if ep.get('task_id') and not allow_existing:
                try:previous=tasks.load(ep['task_id'])
                except (OSError,ValueError):pass
            if previous and previous['state'] not in tasks.TERMINAL:
                cookies=self.window.accounts.cookies_for(previous['site']) if previous['site'] in __import__('runtime').SITES else []
                if self.window.queue.enqueue(previous['id'],cookies):count+=1
                continue
            title=work['title']+(' '+work['season'] if work['season'] else '')+' '+ep['label']
            payload=info if info and info['url']==source['url'] else None
            stub={'url':source['url'],'site':source['site'],'title':title,'formats':[]}
            task=tasks.create(stub,'library-auto',str(folder));task.update(quality_policy=policy,library_title=title,library_episode=eid);tasks.save(task)
            self.store.bind(eid,task['id']);cookies=self.window.accounts.cookies_for(source['site']) if source['site'] in __import__('runtime').SITES else []
            if self.window.queue.enqueue(task['id'],cookies,payload):count+=1
        if skipped:self.window.append_log('\n'.join(skipped));QMessageBox.information(self.window,'部分选集未加入','\n'.join(skipped))
        return count

    def enqueue_large(self,wid,episode_ids,policy,info=None):
        if self.batch_preparing or self.batch_worker:raise ValueError('上一批任务还在准备或停止，请稍后再添加。')
        work=self.store.work(wid);folder=str(self.store.output(wid));data=self.store.read();wanted=set(episode_ids)
        episodes=[e for e in data['episodes'] if e['id'] in wanted and not self.ep_active(e)]
        cookies={s['site']:self.window.accounts.cookies_for(s['site']) if s['site'] in __import__('runtime').SITES else [] for s in work['sources']}
        self.batch_preparing=True;self.batch_worker=True;self.batch_generation+=1;generation=self.batch_generation
        def prepare(cancel):
            prepared=[];pairs=[];skipped=[]
            try:
                for ep in episodes:
                    if cancel.is_set():break
                    if ep.get('files') and self.store.file_state(ep,data) in ('已下载','存储库离线','磁盘离线'):continue
                    preferred=work.get('default_source');source=next((s for s in ep['sources'] if s['source']==preferred),None) if preferred else (ep['sources'][0] if ep['sources'] else None)
                    if not source or not source['url']:skipped.append(ep['label']+'：所选来源没有此集。');continue
                    previous=None
                    if ep.get('task_id'):
                        try:previous=tasks.load(ep['task_id'])
                        except (OSError,ValueError):pass
                    if previous and previous['state'] not in tasks.TERMINAL:task=previous
                    else:
                        title=work['title']+(' '+work['season'] if work['season'] else '')+' '+ep['label']
                        task=tasks.create({'url':source['url'],'site':source['site'],'title':title,'formats':[]},'library-auto',folder)
                        task.update(quality_policy=policy,library_title=title,library_episode=ep['id']);tasks.save(task)
                    pairs.append((ep['id'],task['id']));prepared.append((task['id'],cookies.get(task['site'],[]),info if info and info['url']==source['url'] else None))
            finally:self.store.bind_many(pairs)
            return prepared,skipped
        def done(result):
            self.batch_worker=False
            if generation!=self.batch_generation:
                for task_id,_,_ in result[0]:
                    try:self.window.queue.initial_pending[task_id]=tasks.load(task_id)
                    except (OSError,ValueError):pass
                self.window.queue.changed.emit();return
            if self.closed or self.window.close_when_idle:self.batch_preparing=False;return
            prepared,skipped=result;self.prepared_batch.extend(prepared)
            if skipped:self.window.append_log('\n'.join(skipped));QMessageBox.information(self.window,'部分选集未加入',f'{len(skipped)} 集未加入，请查看日志；不会自动换源。')
            self.pump_batch()
        def failed(error):
            self.batch_preparing=False;self.batch_worker=False
            for task in tasks.pending():
                if task['id'] not in self.active_ids():self.window.queue.initial_pending[task['id']]=task
            message(self.window,error)
        self.jobs.start(prepare,done,failed);return len(episodes)
    def pump_batch(self):
        if self.closed or self.window.close_when_idle:self.cancel_batches();return
        for _ in range(min(10,len(self.prepared_batch))):
            task_id,cookies,info=self.prepared_batch.popleft();self.window.queue.enqueue(task_id,cookies,info)
        if self.prepared_batch:QTimer.singleShot(20,self.pump_batch)
        else:self.batch_preparing=False;self.window.refresh_buttons();self.refresh(True)
    def cancel_batches(self):
        self.batch_preparing=False;self.batch_generation+=1
        for task_id,_,_ in self.prepared_batch:
            try:self.window.queue.initial_pending[task_id]=tasks.load(task_id)
            except (OSError,ValueError):pass
        self.prepared_batch.clear()
        for _,cancel in self.jobs.running.values():cancel.set()

    def policy_dialog(self,work):
        dialog=QDialog(self.window);dialog.setWindowTitle('批量画质与存储');layout=QVBoxLayout(dialog);libs=QComboBox()
        for lib in self.store.read()['libraries']:libs.addItem(lib['label'],lib['id'])
        index=libs.findData(work.get('library_id'));libs.setCurrentIndex(max(0,index));libs.setEnabled(not work.get('library_id'));layout.addWidget(QLabel('存储库（已有本季存储位置需通过迁移更换）'));layout.addWidget(libs)
        heights=QComboBox()
        for n in (2160,1440,1080,720,480,360):heights.addItem(str(n)+'P 优先',n)
        heights.setCurrentIndex(2);layout.addWidget(heights);strict=QCheckBox('严格画质，缺失则跳过');layout.addWidget(strict)
        kind=QComboBox();kind.addItem('保存原图','images');kind.addItem('合成带配乐的视频','slideshow');kind.setVisible(work['kind']=='images');layout.addWidget(kind)
        heights.setVisible(work['kind']!='images');strict.setVisible(work['kind']!='images')
        ok=QPushButton('加入下载队列');ok.clicked.connect(dialog.accept);layout.addWidget(ok)
        if dialog.exec()!=QDialog.DialogCode.Accepted:return None
        if libs.currentData() is None:raise ValueError('请先在管理存储库中添加目录。')
        if not work.get('library_id'):
            with self.store.change() as data:next(w for w in data['works'] if w['id']==work['id'])['library_id']=libs.currentData()
        return {'height':heights.currentData(),'strict':strict.isChecked(),'kind':kind.currentData() if work['kind']=='images' else None}
    def download_ids(self,ids):
        try:
            policy=self.policy_dialog(self.store.work(self.current))
            if policy is None:return
            count=self.enqueue(self.current,ids,policy);self.status.setText(f'已加入 {count} 集。');self.refresh(True)
        except Exception as error:message(self.window,error)
    def download_work(self):
        self.download_ids([e['id'] for e in self.store.read()['episodes'] if e['work_id']==self.current and self.states.get(e['id']) not in ('已下载','磁盘离线','存储库离线')])
    def download_checked(self):self.download_ids([e['id'] for e in self.store.read()['episodes'] if e['work_id']==self.current and e['id'] in self.episode_checked])
    def check_missing(self):
        for e in self.filtered_episodes():
            if self.states.get(e['id'],e['status']) in ('未下载','文件缺失','失败','已暂停','已取消') and not self.ep_active(e):self.episode_checked.add(e['id'])
        self.render_work()
    def uncheck(self):
        self.episode_checked.clear();self.render_work()
    def episode_action(self,key):
        ep=self.store.episode(key)
        files=[f for f in ep['files'] if Path(f['path']).is_file()]
        if not files:self.download_ids([key]);return
        video=next((f for f in files if f['kind']=='video'),None)
        if video is None:QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(files[0]['path']).parent)));return
        work=self.store.work(ep['work_id'])
        if not work['no_play_prompt']:
            dialog=QMessageBox(self.window);dialog.setWindowTitle('播放此选集？');dialog.setText(work['title']+' '+work['season']+' · '+ep['label']);dialog.setInformativeText('使用系统默认或设置中指定的外部播放器打开本地视频。')
            check=QCheckBox('播放本季其他选集时不再提示' if work['season'] else '播放此作品时不再提示');dialog.setCheckBox(check);dialog.setStandardButtons(QMessageBox.StandardButton.Ok|QMessageBox.StandardButton.Cancel)
            if dialog.exec()!=QMessageBox.StandardButton.Ok:return
            if check.isChecked():self.store.patch_work(work['id'],no_play_prompt=True)
        import preferences
        player=preferences.load()['external_player']
        try:
            if player:subprocess.Popen([player,video['path']],shell=False)
            elif not QDesktopServices.openUrl(QUrl.fromLocalFile(video['path'])):raise ValueError('未能打开默认播放器，请在设置中指定播放器。')
        except OSError as error:message(self.window,error)

    def manage_episode(self,key):
        if self.batch_preparing:message(self.window,'批量任务正在准备，请稍后再修改文件。');return
        ep=self.store.episode(key);menu=QMenu(self.window);locate=menu.addAction('重新定位已有文件（原位引用）');variant=menu.addAction('另存其他画质／图文方式…');originals=menu.addAction('打开原图包所在文件夹');remove=menu.addAction('删除本工具下载的视频…');selected=menu.exec(self.episodes.mapToGlobal(self.episodes.rect().center()))
        if self.ep_active(ep):message(self.window,'请先暂停任务并等待停止后操作文件。');return
        try:
            if selected==locate:
                file,_=QFileDialog.getOpenFileName(self.window,'重新定位文件','','媒体 (*.mp4 *.mkv *.avi *.mov *.webm *.zip)')
                if file:self.store.attach(key,file,True,'images' if file.lower().endswith('.zip') else 'video')
            elif selected==variant:
                policy=self.policy_dialog(self.store.work(ep['work_id']))
                if policy:self.enqueue(ep['work_id'],[key],policy,allow_existing=True)
            elif selected==originals:
                file=next((f['path'] for f in ep['files'] if f['kind']=='images'),None)
                if file:QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(file).parent)))
            elif selected==remove:
                paths=[f['path'] for f in ep['files'] if not f['external']]
                if not paths:raise ValueError('此选集没有可删除的受管理文件；外部导入文件不参与删除。')
                if QMessageBox.question(self.window,'删除本地视频','将删除以下文件（保留选集记录）：\n'+'\n'.join(paths))==QMessageBox.StandardButton.Yes:self.store.delete_owned(key,paths)
            self.fingerprint=None
        except Exception as error:message(self.window,error)
    def source_changed(self):
        if self.current and self.source.currentData():self.store.patch_work(self.current,default_source=self.source.currentData())
    def save_checks(self):
        if self.current:self.store.patch_work(self.current,check_enabled=self.check_enabled.isChecked(),check_hours=self.check_hours.value())
    def remove_work(self):
        if self.batch_preparing:message(self.window,'批量任务正在准备，请先等待或取消准备。');return
        if self.unfinished(self.current):message(self.window,'本季还有未完成任务，请先在下载任务中完成或取消，再移除库记录。');return
        if any(self.ep_active(e) for e in self.store.read()['episodes'] if e['work_id']==self.current):message(self.window,'此季仍有任务，请先暂停并等待停止。');return
        if QMessageBox.question(self.window,'移除库记录','仅移除此季的库记录，所有媒体文件保留。是否继续？')==QMessageBox.StandardButton.Yes:self.store.remove(self.current);self.home()
    def migrate(self):
        if self.batch_preparing:message(self.window,'批量任务正在准备，请先等待或取消准备。');return
        if self.unfinished(self.current):message(self.window,'请先完成或取消本季未完成任务，再迁移；不会遗弃暂停任务的续传缓存。');return
        if any(self.ep_active(e) for e in self.store.read()['episodes'] if e['work_id']==self.current):message(self.window,'此季仍有任务，请先暂停并等待停止。');return
        libs=self.store.read()['libraries'];names=[v['label']+' · '+v['path'] for v in libs]
        value,ok=QInputDialog.getItem(self.window,'迁移本季','选择目标存储库；仅移动受管理视频，外部文件保留。',names,0,False)
        if not ok:return
        key=self.current;target=libs[names.index(value)]['id']
        if QMessageBox.question(self.window,'确认迁移','复制并完整校验后切换库索引，再清理原文件；外部文件不移动。是否继续？')!=QMessageBox.StandardButton.Yes:return
        self.jobs.start(lambda cancel:self.store.migrate(key,target,cancel.is_set),lambda remaining:(self.refresh(True),QMessageBox.information(self.window,'迁移完成','目标已校验。'+('部分原文件未清理，请手动检查。' if remaining else ''))),lambda e:message(self.window,e))

    def cache_covers(self,key,force=False):
        if key in self.cover_pending:return
        work=self.store.work(key);self.cover_pending.add(key)
        def fetch(cancel):
            from library_assets import image_file
            result={}
            for name in ('cover','banner'):
                url=work.get(name+'_url')
                if url and (force or not work.get(name)):
                    try:result[name]=image_file(url,cancel.is_set)
                    except Exception as error:diagnostics.event('library.cover.unavailable',str(error))
            return result
        def done(values):
            self.cover_pending.discard(key)
            if values:
                try:self.store.patch_work(key,**values);self.refresh(True)
                except StopIteration:pass
        self.jobs.start(fetch,done,lambda e:self.cover_pending.discard(key))
    def manual_cover(self,banner=False):
        file,_=QFileDialog.getOpenFileName(self.window,'选择封面；取消后可填写图片链接','','图片 (*.png *.jpg *.jpeg *.webp)')
        if not file:file,ok=QInputDialog.getText(self.window,'图片链接','填写原站或你选择的图片 HTTP(S) 链接：')
        if not file:return
        key=self.current;name='banner' if banner else 'cover'
        def fetch(cancel):
            from library_assets import image_file
            return image_file(file,cancel.is_set)
        self.jobs.start(fetch,lambda path:(self.store.patch_work(key,**{name:path}),self.refresh(True)),lambda e:message(self.window,e))
    def refresh_season(self,key=None,automatic=False):
        key=key if isinstance(key,str) else self.current
        if not key or key in self.check_pending:return
        work=self.store.work(key);source=next((s for s in work['sources'] if s['key']==work.get('default_source')),work['sources'][0]);self.check_pending.add(key)
        cookies=self.window.accounts.cookies_for(source['site']) if source['site'] in __import__('runtime').SITES else []
        def fetch(cancel):
            from collection_media import discover
            group=discover(source['url'],cookies,cancel)
            if group is None:raise ValueError('此来源暂不支持合集检查；已保留现有选集。')
            if source_key(group['site'],group['key'],group.get('variant',''))!=source['key']:raise ValueError('返回作品或线路标识变化，请手动检查，不会合并。')
            return group
        def done(group):
            self.check_pending.discard(key)
            if not any(w['id']==key for w in self.store.read()['works']):return
            self.store.ingest(group,work['title'],work['season'],work.get('library_id'));self.cache_covers(key,True);self.refresh(True)
            if self.store.work(key).get('new_count'):self.status.setText('发现新选集：'+work['title']+'；进入详情选择下载。')
        def failed(error):
            self.check_pending.discard(key);self.check_retry[key]=time.time()+3600
            if not automatic:message(self.window,error)
            else:diagnostics.event('library.check.failed',error)
        self.jobs.start(fetch,done,failed)
    def tick(self):
        if self.closed:return
        if not hasattr(self,'check_retry'):self.check_retry={}
        stats=self.window.queue.statistics();self.trend.add(stats['speed']);self.total_samples+=1
        if self.total_samples%3==0 and not self.state_pending:
            self.state_pending=True
            def scan(cancel):
                self.store.sync(tasks.records());data=self.store.read()
                return {e['id']:self.store.file_state(e,data) if e.get('files') else e['status'] for e in data['episodes']}
            def done(states):self.state_pending=False;self.states=states;self.refresh()
            self.jobs.start(scan,done,lambda e:(setattr(self,'state_pending',False),diagnostics.event('library.state.failed',e)))
        if self.total_samples%60==0:
            if self.window.is_busy():return
            for work in self.store.read()['works']:
                if work['check_enabled'] and time.time()-work['checked_at']>=work.get('check_hours',24)*3600 and time.time()>self.check_retry.get(work['id'],0):self.refresh_season(work['id'],True)
    def shutdown(self):
        self.closed=True;self.cancel_batches();self.timer.stop();return self.jobs.shutdown()


def install(window,content,old):return Workspace(window,content,old)

