"""Task browsing, filtering and ID-based actions, independent of download workers."""
from PySide6.QtCore import Qt, QTimer, QEvent, QSignalBlocker
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QLabel,QLineEdit,QTabBar,
    QTableWidget,QTableWidgetItem,QHeaderView,QAbstractItemView,QProgressBar,
    QPushButton,QMessageBox,QStyledItemDelegate,QWidget,QRadioButton,QDialogButtonBox)
import download_tasks as tasks

# Fixed application messages form the stable default width. Variable source logs
# (titles, extractor details, errors) use the same cell clipping and tooltip.
TASK_STATES={'queued':'排队中','paused':'已暂停','failed':'失败，待处理','completed':'已完成','cancelled':'已取消'}
STATUS_SHORTCUTS={'AGE：连接视频源，确认已下载进度。':'确认已下载进度'}
STATUS_TEXTS=tuple(TASK_STATES.values())+(
    '准备中','其他窗口运行或上次中断','准备媒体地址','重新解析媒体地址',
    '确认已下载进度','下载中','续传中','下载文件已就绪',
    '源站未提供可靠续传标识，重新下载当前文件','源文件变化或不支持续传，重新下载当前文件',
    '检查已下载的媒体信息','校验视频完整性','无损封装为 MP4','下载央视视频',
    '处理已下载的央视分片','下载音视频文件','合并或处理音视频文件','下载完成',
    '打包原图','处理图文画面','合成带配乐的视频',
    'AGE：启动浏览器并加载播放页。','AGE：播放器元数据不足，正在探测正片信息。',
    'AGE：媒体地址已失效，正在刷新一次。',
    '央视高清处理组件报告错误；将检查处理结果。',
    '正在获取央视原始高清流并进行专用媒体处理。','正在下载好看视频原始媒体文件。',
    '下载已完成，正在核对分辨率、时长并解码全片。','视频已通过完整性校验并保存。',
)


def group_of(task):
    return {'completed':2,'cancelled':3}.get(task['state'],1)


def matches(task,group,query):
    return (not group or group_of(task)==group) and query.strip().casefold() in ' '.join(
        str(task.get(key,'')) for key in ('title','site','output')).casefold()


class ElidingDelegate(QStyledItemDelegate):
    def initStyleOption(self,option,index):
        super().initStyleOption(option,index)
        option.textElideMode=Qt.TextElideMode.ElideRight

    def paint(self,painter,option,index):
        painter.save()
        painter.setClipRect(option.rect,Qt.ClipOperation.IntersectClip)
        super().paint(painter,option,index)
        painter.restore()


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
        self.queue=queue;self.resume_task=resume;self.all_rows=[];self.visible={}
        self.fit_pending=False;self.closing=False;self.checked_ids=set();self.status_default_width=None
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
        table.setItemDelegateForColumn(0,ElidingDelegate(table))
        table.setItemDelegateForColumn(3,ElidingDelegate(table))
        table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        table.verticalHeader().setDefaultSectionSize(32)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.viewport().installEventFilter(self);table.installEventFilter(self);layout.addWidget(table)
        selection=QHBoxLayout()
        self.select_all=QPushButton('全选');self.select_none=QPushButton('全不选');self.check_count=QLabel()
        self.select_all.clicked.connect(lambda:self.set_checks(True))
        self.select_none.clicked.connect(lambda:self.set_checks(False))
        selection.addWidget(self.select_all);selection.addWidget(self.select_none);selection.addWidget(self.check_count);selection.addStretch()
        layout.addLayout(selection)
        self.batch_result=QLabel();self.batch_result.setWordWrap(True);layout.addWidget(self.batch_result)
        controls=QHBoxLayout();self.buttons={}
        for key,text,action in (('pause','暂停所选任务',self.pause),('resume','继续所选任务',self.resume),
                                ('cancel','取消所选下载',self.cancel),('delete','删除所选记录…',self.delete),('clear','清除下载记录',self.delete)):
            button=QPushButton(text);button.clicked.connect(action);controls.addWidget(button);self.buttons[key]=button
        layout.addLayout(controls)
        table.itemSelectionChanged.connect(self.update_actions)
        table.itemChanged.connect(self.check_changed)
        self.tabs.currentChanged.connect(lambda _:self.filter_changed())
        self.search.textChanged.connect(lambda _:self.filter_changed())
        self.timer=QTimer(self);self.timer.setInterval(500);self.timer.timeout.connect(self.refresh)
        self.timer.start();self.resize(1100,520);self.refresh()

    def eventFilter(self,watched,event):
        if watched==self.table.viewport() and event.type()==QEvent.Type.Resize:
            self.schedule_fit()
        if watched==self.table and event.type() in (QEvent.Type.FontChange,QEvent.Type.StyleChange,QEvent.Type.DevicePixelRatioChange):
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
        status=max(metrics.horizontalAdvance(text) for text in STATUS_TEXTS)+metrics.horizontalAdvance('  ')+12
        if status!=self.status_default_width:
            self.table.setColumnWidth(3,status);self.status_default_width=status
        paths={row['output'] for row in self.visible.values()}
        natural=max([240]+[metrics.horizontalAdvance(path)+30 for path in paths])
        for column,size in ((0,title),(1,160),(2,100),(4,max(natural,width-title-260-self.table.columnWidth(3)))):
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
        self.checked_ids.intersection_update(ids)
        scroll=0 if reset_scroll else self.table.horizontalScrollBar().value()
        vertical=0 if reset_scroll else self.table.verticalScrollBar().value()
        with QSignalBlocker(self.table):
            if ids!=old_ids:
                self.table.setRowCount(0);self.table.setRowCount(len(rows))
            self.visible={row['id']:row for row in rows}
            for i,task in enumerate(rows):
                state=TASK_STATES.get(task['state'],task.get('stage') or '准备中')
                if task['state']=='running' and task['id'] not in self.queue.active:state='其他窗口运行或上次中断'
                for j,value in ((0,task['title']),(2,task['site']),(3,state),(4,task['output'])):
                    item=self.table.item(i,j)
                    if item is None:item=QTableWidgetItem();self.table.setItem(i,j,item)
                    display=STATUS_SHORTCUTS.get(value,value) if j==3 else value
                    item.setText(display.replace('\r',' ').replace('\n',' ') if j in (0,3) else display)
                    item.setToolTip(value)
                    if j==0:
                        item.setData(Qt.ItemDataRole.UserRole,task['id'])
                        item.setFlags(item.flags()|Qt.ItemFlag.ItemIsUserCheckable)
                        item.setCheckState(Qt.CheckState.Checked if task['id'] in self.checked_ids else Qt.CheckState.Unchecked)
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

    def filter_changed(self):
        self.checked_ids.clear();self.render(reset_scroll=True)

    def check_changed(self,item):
        if item.column()!=0:return
        task_id=item.data(Qt.ItemDataRole.UserRole)
        if item.checkState()==Qt.CheckState.Checked:self.checked_ids.add(task_id)
        else:self.checked_ids.discard(task_id)
        self.update_actions()

    def set_checks(self,checked):
        self.checked_ids=set(self.visible) if checked else set()
        self.render()

    def checked(self):
        return [row for key,row in self.visible.items() if key in self.checked_ids]

    def eligible(self,task,action):
        task_id=task['id'];terminal=task['state'] in tasks.TERMINAL
        busy=task_id in self.queue.active or any(row[0]==task_id for row in self.queue.waiting)
        return {'pause':not terminal and busy,'resume':not terminal and not busy,
                'cancel':not terminal,'delete':terminal and not busy}[action]

    def update_actions(self):
        rows=self.checked();group=self.tabs.currentIndex()
        self.check_count.setText(f'已勾选 {len(rows)} 条（仅当前列表）')
        self.select_all.setEnabled(bool(self.visible));self.select_none.setEnabled(bool(rows))
        for action in ('pause','resume','cancel','delete'):
            self.buttons[action].setEnabled(any(self.eligible(row,action) for row in rows))
        self.buttons['delete'].setVisible(group==3 or (group==0 and bool(rows) and all(row['state'] in tasks.TERMINAL for row in rows)))
        self.buttons['clear'].setVisible(group==2)
        self.buttons['clear'].setEnabled(bool(rows) and any(self.eligible(row,'delete') for row in rows))

    def targets(self,action):
        rows=self.checked()
        return [row['id'] for row in rows if self.eligible(row,action)],sum(not self.eligible(row,action) for row in rows)

    def apply_batch(self,action,ids,skipped,clean_cache=False):
        submitted=failed=0;errors=[]
        for task_id in ids:
            try:
                # Recheck state, but never add a task to the captured target IDs.
                row=tasks.load_record(task_id)
                if not self.eligible(row,action):skipped+=1;continue
                if action=='resume':result=self.resume_task(task_id)
                elif action=='delete':result=self.queue.delete_record(task_id,clean_cache=clean_cache)
                else:result=getattr(self.queue,'discard' if action=='cancel' else action)(task_id)
                if result is False:
                    if action=='cancel':failed+=1
                    else:skipped+=1
                else:submitted+=1
            except Exception as error:
                failed+=1;errors.append(str(error))
        self.batch_result.setText(f'已提交 {submitted} 条，跳过 {skipped} 条，失败 {failed} 条。'+('；'.join(errors[:3]) if errors else '最终处理状态见任务列表。'))
        self.refresh()

    def pause(self):
        ids,skipped=self.targets('pause');self.apply_batch('pause',ids,skipped)

    def resume(self):
        ids,skipped=self.targets('resume');self.apply_batch('resume',ids,skipped)

    def cancel(self):
        ids,skipped=self.targets('cancel')
        if not ids:return
        if QMessageBox.question(self,'取消下载',f'停止勾选的 {len(ids)} 条任务并清理缓存？另跳过 {skipped} 条。取消记录会保留，已完成的文件不会删除。')==QMessageBox.StandardButton.Yes:
            self.apply_batch('cancel',ids,skipped)

    def delete(self):
        ids,skipped=self.targets('delete')
        if not ids:return
        choice=DeleteDialog(self)
        choice.setWindowTitle(f'删除 {len(ids)} 条历史记录（跳过 {skipped} 条）')
        if choice.exec()==QDialog.DialogCode.Accepted:
            self.apply_batch('delete',ids,skipped,choice.with_cache.isChecked())
        choice.deleteLater()

    def closeEvent(self,event):
        self.closing=True;self.timer.stop();super().closeEvent(event)
