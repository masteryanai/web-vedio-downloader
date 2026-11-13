"""Task browsing, filtering and ID-based actions, independent of download workers."""
from PySide6.QtCore import Qt, QTimer, QEvent, QSignalBlocker
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QLabel,QLineEdit,QTabBar,
    QTableWidget,QTableWidgetItem,QHeaderView,QAbstractItemView,QProgressBar,
    QPushButton,QMessageBox,QStyledItemDelegate,QWidget,QRadioButton,QDialogButtonBox)
import download_tasks as tasks


def group_of(task):
    return {'completed':2,'cancelled':3}.get(task['state'],1)


def matches(task,group,query):
    return (not group or group_of(task)==group) and query.strip().casefold() in ' '.join(
        str(task.get(key,'')) for key in ('title','site','output')).casefold()


class TitleDelegate(QStyledItemDelegate):
    def initStyleOption(self,option,index):
        super().initStyleOption(option,index)
        option.textElideMode=Qt.TextElideMode.ElideRight


class DeleteDialog(QDialog):
    def __init__(self,parent=None):
        super().__init__(parent)
        self.setWindowTitle('删除历史记录');self.resize(480,230)
        layout=QVBoxLayout(self)
        description=QLabel('请选择删除范围。两种方式均保留已下载完成的 MP4／ZIP 文件。')
        description.setWordWrap(True);layout.addWidget(description)
        self.record_only=QRadioButton('仅删除记录');self.record_only.setChecked(True);layout.addWidget(self.record_only)
        self.with_cache=QRadioButton('删除记录及该任务的临时缓存');layout.addWidget(self.with_cache)
        note=QLabel('已取消任务通常已清理缓存。缓存清理失败时会保留记录，便于重试。')
        note.setWordWrap(True);layout.addWidget(note)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText('确认删除')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        buttons.accepted.connect(self.accept);buttons.rejected.connect(self.reject);layout.addWidget(buttons)


class TaskDialog(QDialog):
    def __init__(self,queue,resume,parent=None):
        super().__init__(parent)
        self.queue=queue;self.resume_task=resume;self.all_rows=[];self.visible={};self.path_widths={}
        self.fit_pending=False;self.closing=False
        self.setWindowTitle('下载任务');self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        layout=QVBoxLayout(self)
        self.summary=QLabel();self.summary.setWordWrap(True);layout.addWidget(self.summary)
        self.tabs=QTabBar();self.tabs.setExpanding(False)
        for name in ('全部','进行中','已完成','已取消'):self.tabs.addTab(name)
        layout.addWidget(self.tabs)
        self.search=QLineEdit();self.search.setClearButtonEnabled(True)
        self.search.setPlaceholderText('搜索作品名、网站或保存路径（当前分组）');layout.addWidget(self.search)
        self.table=QTableWidget(0,5);table=self.table
        table.setHorizontalHeaderLabels(['作品','进度','网站','状态','保存位置'])
        table.horizontalHeaderItem(4).setTextAlignment(Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignVCenter)
        table.horizontalHeader().setStretchLastSection(False)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        table.setWordWrap(False);table.setTextElideMode(Qt.TextElideMode.ElideNone)
        table.setItemDelegateForColumn(0,TitleDelegate(table))
        table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        table.verticalHeader().setDefaultSectionSize(32)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.viewport().installEventFilter(self);layout.addWidget(table)
        controls=QHBoxLayout();self.buttons={}
        for key,text,action in (('pause','暂停所选任务',self.pause),('resume','继续所选任务',self.resume),
                                ('cancel','取消下载',self.cancel),('delete','删除…',self.delete)):
            button=QPushButton(text);button.clicked.connect(action);controls.addWidget(button);self.buttons[key]=button
        layout.addLayout(controls)
        table.itemSelectionChanged.connect(self.update_actions)
        self.tabs.currentChanged.connect(lambda _:self.render(reset_scroll=True))
        self.search.textChanged.connect(lambda _:self.render(reset_scroll=True))
        self.timer=QTimer(self);self.timer.setInterval(500);self.timer.timeout.connect(self.refresh)
        self.timer.start();self.resize(1100,520);self.refresh()

    def eventFilter(self,watched,event):
        if watched==self.table.viewport() and event.type()==QEvent.Type.Resize:
            self.schedule_fit()
        return super().eventFilter(watched,event)

    def schedule_fit(self):
        if not self.closing and not self.fit_pending:
            self.fit_pending=True;QTimer.singleShot(0,self.fit_columns)

    def fit_columns(self):
        self.fit_pending=False
        if self.closing:return
        width=self.table.viewport().width()
        title=max(380,int(width*.34))
        metrics=QFontMetrics(self.table.font())
        paths={row['output'] for row in self.visible.values()}
        for path in paths:
            if path not in self.path_widths:self.path_widths[path]=metrics.horizontalAdvance(path)+30
        natural=max([240]+[self.path_widths[path] for path in paths])
        for column,size in enumerate((title,160,100,140,max(natural,width-title-400))):
            self.table.setColumnWidth(column,size)

    def selected(self):
        if not self.table.selectionModel().hasSelection():return None
        item=self.table.item(self.table.currentRow(),0)
        return self.visible.get(item.data(Qt.ItemDataRole.UserRole)) if item else None

    def refresh(self):
        if self.closing:return
        self.all_rows=self.queue.rows();self.render()

    def render(self,reset_scroll=False):
        selected=self.selected();selected_id=selected['id'] if selected else None
        counts=[len(self.all_rows)]+[sum(group_of(row)==group for row in self.all_rows) for group in (1,2,3)]
        for i,name in enumerate(('全部','进行中','已完成','已取消')):self.tabs.setTabText(i,f'{name} ({counts[i]})')
        group=self.tabs.currentIndex();query=self.search.text()
        rows=[row for row in self.all_rows if matches(row,group,query)]
        old_ids=list(self.visible);ids=[row['id'] for row in rows]
        scroll=0 if reset_scroll else self.table.horizontalScrollBar().value()
        vertical=0 if reset_scroll else self.table.verticalScrollBar().value()
        with QSignalBlocker(self.table):
            if ids!=old_ids:
                self.table.setRowCount(0);self.table.setRowCount(len(rows))
            self.visible={row['id']:row for row in rows}
            for i,task in enumerate(rows):
                state={'queued':'排队中','paused':'已暂停','failed':'失败，待处理','completed':'已完成','cancelled':'已取消'}.get(task['state'],task.get('stage') or '准备中')
                if task['state']=='running' and task['id'] not in self.queue.active:state='其他窗口运行或上次中断'
                for j,value in ((0,task['title']),(2,task['site']),(3,state),(4,task['output'])):
                    item=self.table.item(i,j)
                    if item is None:item=QTableWidgetItem();self.table.setItem(i,j,item)
                    item.setText(value.replace('\r',' ').replace('\n',' ') if j==0 else value)
                    item.setToolTip(value)
                    if j==0:item.setData(Qt.ItemDataRole.UserRole,task['id'])
                cell=self.table.cellWidget(i,1)
                if cell is None:
                    cell=QWidget();box=QVBoxLayout(cell);box.setContentsMargins(3,0,3,0)
                    bar=QProgressBar(cell);bar.setFixedHeight(20);bar.setAlignment(Qt.AlignmentFlag.AlignCenter)
                    box.addWidget(bar,0,Qt.AlignmentFlag.AlignVCenter)
                    self.table.setCellWidget(i,1,cell)
                else:bar=cell.findChild(QProgressBar)
                percent=task.get('download_percent');bar.setRange(0,100);bar.setValue(int(percent or 0))
                bar.setFormat(f'{percent:.1f}%' if isinstance(percent,(int,float)) else '总量未知')
                bar.setTextVisible(True)
            self.table.clearSelection();self.table.setCurrentCell(-1,-1)
            if selected_id in self.visible:self.table.selectRow(ids.index(selected_id))
        self.table.horizontalScrollBar().setValue(scroll);self.table.verticalScrollBar().setValue(vertical)
        self.summary.setText(f'运行 {len(self.queue.active)} / 并行上限 {self.queue.limit} · 排队 {len(self.queue.waiting)} · 当前显示 {len(rows)} / {counts[group]} 条；关闭此窗口不影响下载。')
        self.update_actions()
        if ids!=old_ids or not old_ids:self.schedule_fit()

    def update_actions(self):
        task=self.selected();state=task['state'] if task else None
        task_id=task['id'] if task else None
        active=task_id in self.queue.active
        queued=any(row[0]==task_id for row in self.queue.waiting)
        self.buttons['pause'].setEnabled(bool(task) and state not in tasks.TERMINAL and (active or queued))
        self.buttons['resume'].setEnabled(bool(task) and state not in tasks.TERMINAL and not active and not queued)
        self.buttons['cancel'].setEnabled(bool(task) and state not in tasks.TERMINAL)
        self.buttons['delete'].setEnabled(bool(task) and state in tasks.TERMINAL and not active and not queued)
        self.buttons['delete'].setVisible(self.tabs.currentIndex() in (2,3) or (self.tabs.currentIndex()==0 and state in tasks.TERMINAL))

    def pause(self):
        task=self.selected()
        if task:
            try:self.queue.pause(task['id'])
            except Exception as error:QMessageBox.warning(self,'无法暂停',str(error))
        self.refresh()

    def resume(self):
        task=self.selected()
        if task:self.resume_task(task['id'])
        self.refresh()

    def cancel(self):
        task=self.selected()
        if not task:return
        task_id=task['id']
        if QMessageBox.question(self,'取消下载','停止所选任务并清理缓存？取消记录会保留，已完成的文件不会删除。')==QMessageBox.StandardButton.Yes:
            self.queue.discard(task_id)
        self.refresh()

    def delete(self):
        task=self.selected()
        if not task:return
        task_id=task['id']
        if task['state'] not in tasks.TERMINAL:return
        choice=DeleteDialog(self)
        if choice.exec()==QDialog.DialogCode.Accepted:
            try:self.queue.delete_record(task_id,clean_cache=choice.with_cache.isChecked())
            except Exception as error:QMessageBox.warning(self,'无法删除记录',str(error))
        choice.deleteLater()
        self.refresh()

    def closeEvent(self,event):
        self.closing=True;self.timer.stop();super().closeEvent(event)
