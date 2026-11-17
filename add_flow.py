"""Progressive disclosure for a single cancellable add window."""
from PySide6.QtCore import QObject,QTimer
from PySide6.QtWidgets import QLabel,QPushButton,QHBoxLayout,QWidget

class AddFlow(QObject):
    def __init__(self,workspace,content):
        super().__init__(workspace.add_dialog);self.ws=workspace;self.window=workspace.window;self.dialog=workspace.add_dialog
        self.content=content;self.result_url=None;self.page='link';self.waiting_batch=False
        layout=content.layout();self.link_card=self.window.url_input.parentWidget();self.video_card=self.window.quality.parentWidget()
        self.status_card=self.window.status_text.parentWidget();self.window.log_box.hide();self.status_card.hide()
        self.message=QLabel();self.message.setWordWrap(True);layout.addWidget(self.message)
        row=QHBoxLayout();self.back=QPushButton('返回修改链接');self.back.clicked.connect(self.show_link)
        self.stop=QPushButton('取消解析');self.stop.clicked.connect(self.window.cancel)
        row.addWidget(self.back);row.addStretch();row.addWidget(self.stop);layout.addLayout(row)
        self.timer=QTimer(self);self.timer.setInterval(150);self.timer.timeout.connect(self.sync);self.timer.start()
        self.show_link()

    def show_link(self):
        self.page='link';self.link_card.show();self.ws.add_controls.hide();self.video_card.hide();self.back.hide()
        self.dialog.resize(660,360);self.dialog.setWindowTitle('添加下载 · 粘贴链接')

    def show_results(self):
        self.result_url=self.window.url_input.toPlainText();self.page='results'
        self.link_card.hide();self.ws.add_controls.show();self.video_card.show();self.back.show()
        self.dialog.resize(980,760);self.dialog.setWindowTitle('添加下载 · 选择内容')
        self.message.setText('选择要下载的内容；成功加入队列后将在后台下载。')

    def sync(self):
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
        if errors:
            self.message.setText(f'已加入 {count} 项，其他项目未加入：\n'+'\n'.join(errors[:6]));return
        self.dialog.hide();self.ws.status.setText(f'已添加 {count} 项 · 查看下载任务')

    def mark_added(self):
        controls=self.ws.add_controls
        ids=getattr(controls,'submitted_episode_ids',[])
        if not ids or not hasattr(self.ws,'store'):return
        for i,key in enumerate(ids):
            if self.ws.ep_active(self.ws.store.episode(key)) and controls.table.item(i,0):
                from PySide6.QtCore import Qt
                controls.table.item(i,0).setCheckState(Qt.CheckState.Unchecked)
                controls.table.item(i,1).setText('已在队列')
