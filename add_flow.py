"""Progressive disclosure for a single cancellable add window."""
from PySide6.QtCore import QObject,QTimer
from PySide6.QtWidgets import QLabel,QPushButton,QHBoxLayout,QWidget,QComboBox,QLineEdit,QCheckBox

class AddFlow(QObject):
    def __init__(self,workspace,content):
        super().__init__(workspace.add_dialog);self.ws=workspace;self.window=workspace.window;self.dialog=workspace.add_dialog
        self.content=content;self.result_url=None;self.page='link';self.waiting_batch=False
        self.sources=[];self.active_source=None;self.current_source=None;self.rendering=False;self.advance=False;self.submitting=[];self.total_added=0
        layout=content.layout();self.link_card=self.window.url_input.parentWidget();self.video_card=self.window.quality.parentWidget()
        self.status_card=self.window.status_text.parentWidget();self.window.log_box.hide();self.status_card.hide()
        self.message=QLabel();self.message.setWordWrap(True);layout.addWidget(self.message)
        self.sources_box=QComboBox();self.sources_box.currentIndexChanged.connect(self.select_source);layout.insertWidget(2,self.sources_box);self.sources_box.hide()
        row=QHBoxLayout();self.back=QPushButton('返回修改链接');self.back.clicked.connect(self.show_link)
        self.stop=QPushButton('取消解析');self.stop.clicked.connect(self.cancel_parsing)
        self.retry=QPushButton('重试解析');self.retry.clicked.connect(self.retry_source)
        self.validate=QPushButton('前往验证');self.validate.clicked.connect(self.explicit_validation)
        row.addWidget(self.back);row.addWidget(self.retry);row.addWidget(self.validate);row.addStretch();row.addWidget(self.stop);layout.addLayout(row)
        self.timer=QTimer(self);self.timer.setInterval(150);self.timer.timeout.connect(self.sync);self.timer.start()
        self.show_link()

    def show_link(self):
        self.page='link';self.link_card.show();self.ws.add_controls.hide();self.video_card.hide();self.back.hide()
        self.sources_box.hide();self.retry.hide();self.validate.hide()
        self.dialog.resize(660,360);self.dialog.setWindowTitle('添加下载 · 粘贴链接')

    def show_results(self):
        if self.sources and not self.rendering and self.active_source is not None:
            source=self.sources[self.active_source];source.update(info=self.window.info,status='ready',error='');self.advance=True;self.show_link();return
        self.result_url=self.window.url_input.toPlainText();self.page='results'
        self.link_card.hide();self.ws.add_controls.show();self.video_card.show();self.back.show()
        self.dialog.resize(980,760);self.dialog.setWindowTitle('添加下载 · 选择内容')
        from motion import reveal
        reveal(self.dialog)
        self.sources_box.setVisible(len(self.sources)>1);self.retry.hide();self.validate.hide()
        self.message.setText('选择要下载的内容；成功加入队列后将在后台下载。')

    def sync(self):
        if self.advance and not self.window.is_busy():self.advance=False;self.next_source()
        if not self.dialog.isVisible():return
        busy=self.window.active_thread is not None or self.window.browser_dialog is not None
        self.stop.setVisible(busy);self.back.setEnabled(not busy)
        if busy:self.message.setText(self.window.status_text.text()+'\n'+self.window.detail.text())
        elif self.window.status_text.text().startswith(('本次解析未完成','解析已取消')):
            self.message.setText(self.window.detail.text());self.message.setStyleSheet('color:#f1c58a;')

    def submitted(self,count):
        if self.ws.batch_preparing:
            self.waiting_batch=True;self.message.setText(f'正在准备 {count} 项任务，确认全部入队后关闭。');return
        self.waiting_batch=False
        errors=getattr(self.ws,'last_enqueue_errors',[])
        self.mark_added()
        if self.sources and self.current_source is not None:
            self.save_selection();source=self.sources[self.current_source]
            source.update(status='ready' if errors else 'submitted',error='\n'.join(errors));self.total_added+=count
            if self.submitting:self.submit_next();return
            unfinished=[row for row in self.sources if row['status'] not in ('submitted','ignored')]
            if unfinished:
                self.message.setText(f'已加入 {self.total_added} 项；仍有待处理链接，可选择重试或前往验证。');return
        if errors:
            self.message.setText(f'已加入 {count} 项，其他项目未加入：\n'+'\n'.join(errors[:6]));return
        self.dialog.hide();self.ws.status.setText(f'已添加 {count} 项 · 查看下载任务')

    def start_urls(self,urls,automatic=False):
        self.sources=[{'url':url,'info':None,'status':'pending','error':''} for url in urls]
        self.active_source=None;self.current_source=None;self.total_added=0;self.automatic=automatic;self.ws.open_add();self.next_source()

    def next_source(self):
        pending=next((i for i,row in enumerate(self.sources) if row['status']=='pending'),None)
        if pending is None:
            self.active_source=None;self.refresh_sources();self.select_source(0);return
        self.active_source=pending;self.show_link();self.window.url_input.setPlainText(self.sources[pending]['url'])
        self.window._automatic_clipboard=getattr(self,'automatic',False);self.driving_parse=True
        try:self.window.inspect()
        finally:self.driving_parse=False

    def parse_failed(self,error,needs_validation=False):
        if not self.sources or self.active_source is None:return
        self.sources[self.active_source].update(status='validation' if needs_validation else 'failed',error=error);self.advance=True

    def refresh_sources(self):
        self.sources_box.blockSignals(True);self.sources_box.clear()
        states={'ready':'可选择','failed':'解析失败','validation':'需要验证','submitted':'已加入队列','ignored':'已忽略','pending':'待解析'}
        for row in self.sources:self.sources_box.addItem(states[row['status']]+' · '+((row['info'] or {}).get('title') or row['url']))
        self.sources_box.blockSignals(False)

    def save_selection(self):
        if self.current_source is None:return
        controls=self.ws.add_controls;value={}
        for name in ('name','season','route','library','height','strict','merge'):
            widget=getattr(controls,name,None)
            if isinstance(widget,QLineEdit):value[name]=widget.text()
            elif isinstance(widget,QComboBox):value[name]=widget.currentData()
            elif isinstance(widget,QCheckBox):value[name]=widget.isChecked()
        value['rows']=[(controls.table.item(i,0).checkState(),controls.table.item(i,2).data(__import__('PySide6.QtCore',fromlist=['Qt']).Qt.ItemDataRole.UserRole) if controls.table.columnCount()>2 and controls.table.item(i,2) else None) for i in range(controls.table.rowCount())]
        self.sources[self.current_source]['selection']=value

    def select_source(self,index):
        if self.rendering or not 0<=index<len(self.sources):return
        self.save_selection();self.current_source=index;source=self.sources[index];self.sources_box.blockSignals(True);self.sources_box.setCurrentIndex(index);self.sources_box.blockSignals(False)
        self.window.url_input.setPlainText(source['url'])
        if source['info']:
            self.rendering=True;kind=self.window.task_kind;self.window.task_kind='inspect'
            try:self.window.task_success(source['info'])
            finally:self.window.task_kind=kind;self.rendering=False
            controls=self.ws.add_controls;value=source.get('selection',{})
            for name in ('route','merge','name','season','library','height','strict'):
                widget=getattr(controls,name,None)
                if name not in value:continue
                if isinstance(widget,QLineEdit):widget.setText(value[name])
                elif isinstance(widget,QComboBox):
                    found=widget.findData(value[name])
                    if found>=0:widget.setCurrentIndex(found)
                elif isinstance(widget,QCheckBox):widget.setChecked(value[name])
            for i,(state,mapping) in enumerate(value.get('rows',[])):
                if i<controls.table.rowCount():controls.table.item(i,0).setCheckState(state)
            self.message.setText(source.get('error') or '选择内容后加入下载队列。')
        else:
            self.show_link();self.message.setText(source['error']);self.retry.show();self.validate.show()
        self.sources_box.setVisible(len(self.sources)>1)

    def retry_source(self):
        if self.current_source is None:return
        self.sources[self.current_source]['status']='pending';self.next_source()

    def explicit_validation(self):
        if self.current_source is not None and self.sources[self.current_source]['status']=='validation':
            self.automatic=False;self.retry_source()
        else:self.window.show_accounts()

    def cancel_parsing(self):
        for row in self.sources:
            if row['status']=='pending':row.update(status='failed',error='解析已取消，可手动重试。')
        self.window.cancel()

    def submit_requested(self):
        if not self.sources:
            count=self.ws.add_controls.submit()
            if count is not None:self.submitted(count)
            return
        self.save_selection();self.submitting=[i for i,row in enumerate(self.sources) if row['status']=='ready'];self.submit_next()

    def submit_next(self):
        if not self.submitting:return
        index=self.submitting.pop(0);self.select_source(index)
        try:
            count=self.ws.add_controls.submit()
            if count is not None:self.submitted(count)
        except Exception as error:
            self.sources[index]['error']=str(error);self.message.setText(str(error))
            if self.submitting:self.submit_next()

    def mark_added(self):
        controls=self.ws.add_controls
        ids=getattr(controls,'submitted_episode_ids',[])
        if not ids or not hasattr(self.ws,'store'):return
        for i,key in enumerate(ids):
            if self.ws.ep_active(self.ws.store.episode(key)) and controls.table.item(i,0):
                from PySide6.QtCore import Qt
                controls.table.item(i,0).setCheckState(Qt.CheckState.Unchecked)
                controls.table.item(i,1).setText('已在队列')
