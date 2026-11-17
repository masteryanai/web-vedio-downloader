"""Download-first workspace. Files are ordinary outputs, never a film catalogue."""
from pathlib import Path
from PySide6.QtCore import Qt,QTimer,QSize
from PySide6.QtWidgets import (QWidget,QFrame,QDialog,QScrollArea,QVBoxLayout,QHBoxLayout,QLabel,
    QPushButton,QLineEdit,QComboBox,QCheckBox,QTableWidget,QTableWidgetItem,QHeaderView)
import download_tasks as tasks
import preferences
from library_ui import Jobs,Trend
from media_library import component
from add_flow import AddFlow
from steam_widgets import control_icon
from motion import FeedbackButton

class DownloadSelection(QFrame):
    def __init__(self,workspace):
        super().__init__();self.ws=workspace;self.window=workspace.window;self.collection=None
        box=QVBoxLayout(self);self.name=QLineEdit();self.name.setPlaceholderText('下载文件名称 / 合集目录名称');box.addWidget(self.name)
        row=QHBoxLayout();self.height=QComboBox()
        for value in (2160,1440,1080,720,480,360):self.height.addItem(f'{value}P 优先',value)
        self.height.setCurrentIndex(max(0,self.height.findData(preferences.load()['last_quality_height'])))
        self.strict=QCheckBox('严格画质：缺失则跳过');row.addWidget(self.height);row.addWidget(self.strict);box.addLayout(row)
        self.table=QTableWidget(0,2);self.table.setHorizontalHeaderLabels(['选择内容','状态']);self.table.horizontalHeader().setSectionResizeMode(0,QHeaderView.ResizeMode.Stretch);self.table.setMinimumHeight(170);box.addWidget(self.table)
        row=QHBoxLayout()
        for title,checked in [('全选可下载',True),('全不选',False)]:
            button=QPushButton(title);button.clicked.connect(lambda _,v=checked:self.select_all(v));row.addWidget(button)
        box.addLayout(row);self.notice=QLabel();self.notice.setWordWrap(True);box.addWidget(self.notice)

    def reload_libraries(self):pass

    def receive(self,info):
        from collection_media import single
        self.collection=info.get('_collection') or single(info);group=self.collection;self.name.setText(group['title'])
        rows=tasks.records();self.table.setRowCount(len(group['entries']))
        for i,entry in enumerate(group['entries']):
            existing=next((row for row in rows if row['url']==entry['url'] and row['state']!='cancelled'),None)
            complete=existing and existing['state']=='completed' and Path(existing.get('result',{}).get('path','')).is_file()
            active=existing and existing['id'] in self.ws.active_ids()
            state='已下载' if complete else '已在队列' if active else '待继续' if existing else '未下载'
            item=QTableWidgetItem(entry['title']);item.setFlags((item.flags()|Qt.ItemFlag.ItemIsUserCheckable)&~Qt.ItemFlag.ItemIsEditable)
            item.setCheckState(Qt.CheckState.Unchecked if complete or active else Qt.CheckState.Checked);item.setData(Qt.ItemDataRole.UserRole,(existing or {}).get('id'))
            self.table.setItem(i,0,item);self.table.setItem(i,1,QTableWidgetItem(state))
        self.notice.setText(f"{len(group['entries'])} 项 · 下载历史不建立影片库，已下载文件不会覆盖。")

    def select_all(self,checked):
        for i in range(self.table.rowCount()):
            allowed=self.table.item(i,1).text() not in ('已下载','已在队列')
            self.table.item(i,0).setCheckState(Qt.CheckState.Checked if checked and allowed else Qt.CheckState.Unchecked)

    def submit(self):
        if not self.collection:raise ValueError('请先解析链接。')
        base=Path(self.window.output_path.text()).expanduser()
        if not base.is_absolute():raise ValueError('请选择完整的保存路径。')
        title=self.name.text().strip()
        if not title:raise ValueError('请填写文件或合集名称。')
        group=self.collection;folder=base/component(title) if len(group['entries'])>1 else base
        preferences.update(last_download_dir=str(base),last_quality_height=self.height.currentData())
        self.ws.last_enqueue_errors=[];count=0
        for i,entry in enumerate(group['entries']):
            item=self.table.item(i,0)
            if item.checkState()!=Qt.CheckState.Checked:continue
            try:
                previous=item.data(Qt.ItemDataRole.UserRole)
                task=tasks.load(previous) if previous else None
                if task is None:
                    name=f'{i+1:03d} - {entry["title"]}' if len(group['entries'])>1 else title
                    task=tasks.create({'url':entry['url'],'site':group['site'],'title':name},'download-auto',folder)
                    task.update(library_title=name,quality_policy={'height':self.height.currentData(),'strict':self.strict.isChecked(),
                        'kind':self.window.quality.currentData() if self.window.info.get('_kind')=='images' else None});tasks.save(task)
                    item.setData(Qt.ItemDataRole.UserRole,task['id'])
                cookies=self.window.accounts.cookies_for(task['site']) if task['site'] in __import__('runtime').SITES else []
                payload=self.window.info if self.window.info['url']==entry['url'] else None
                if self.window.queue.enqueue(task['id'],cookies,payload):
                    count+=1;item.setCheckState(Qt.CheckState.Unchecked);self.table.item(i,1).setText('已在队列')
            except Exception as error:self.ws.last_enqueue_errors.append(entry['title']+'：'+str(error))
        return count

class Workspace:
    def __init__(self,window,content,old_shell):
        self.window=window;self.closed=False;self.batch_preparing=False;self.batch_worker=False;self.last_enqueue_errors=[];self.jobs=Jobs(window)
        self.add_dialog=QDialog(window);self.add_dialog.resize(660,360)
        layout=QVBoxLayout(self.add_dialog);scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setWidget(content);layout.addWidget(scroll)
        self.add_controls=DownloadSelection(self);content.layout().insertWidget(2,self.add_controls)
        self.add_flow=AddFlow(self,content);self.add_dialog.finished.connect(self.add_closed)
        window.download_button.setText('加入下载队列');window.output_path.setText(preferences.load()['last_download_dir'] or str(__import__('runtime').default_download_dir()))
        shell=QWidget();box=QVBoxLayout(shell);box.setContentsMargins(20,16,20,12);box.setSpacing(14)
        head=QHBoxLayout();title=QLabel('下载任务');title.setObjectName('title');head.addWidget(title);head.addStretch()
        add=FeedbackButton('添加下载');add.setIcon(control_icon('add'));add.setIconSize(QSize(20,20));add.setObjectName('primary');add.clicked.connect(self.open_add);head.addWidget(add)
        for label,callback in [('账号',window.show_accounts),('设置',window.show_settings),('更新',window.check_updates),('故障反馈',window.show_diagnostics)]:
            button=QPushButton(label);button.clicked.connect(callback);head.addWidget(button)
        box.addLayout(head);self.trend=Trend();self.trend.setMinimumHeight(110);self.trend.setMaximumHeight(140);box.addWidget(self.trend)
        from task_view import TaskDialog
        window.task_dialog=TaskDialog(window.queue,lambda key:window.resume_task(key,report_error=False),window)
        window.task_dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose,False);window.task_dialog.setWindowFlags(Qt.WindowType.Widget);box.addWidget(window.task_dialog,1)
        self.status=QPushButton('暂无任务');self.status.clicked.connect(self.show_tasks);box.addWidget(self.status)
        window.setCentralWidget(shell);old_shell.deleteLater();window.resize(1080,760)
        self.timer=QTimer(window);self.timer.setInterval(1000);self.timer.timeout.connect(self.tick);self.timer.start()

    def tick(self):
        stats=self.window.queue.statistics();self.trend.add(stats['speed'])
    def active_ids(self):return set(self.window.queue.active)|{row[0] for row in self.window.queue.waiting}
    def open_add(self):self.add_dialog.show();self.add_dialog.raise_();self.add_dialog.activateWindow()
    def add_closed(self,_):
        if self.window.active_thread or self.window.browser_dialog:self.window.cancel()
    def inspected(self,info):
        self.add_controls.receive(info);images=info.get('_kind')=='images';self.window.quality.setVisible(images);self.add_controls.height.setVisible(not images);self.add_controls.strict.setVisible(not images);self.add_flow.show_results()
    def show_tasks(self):self.window.task_dialog.show();self.window.task_dialog.refresh()
    def home(self):self.show_tasks()
    def cancel_batches(self):pass
    def shutdown(self):self.closed=True;self.timer.stop();return self.jobs.shutdown()

def install(window,content,old):return Workspace(window,content,old)
