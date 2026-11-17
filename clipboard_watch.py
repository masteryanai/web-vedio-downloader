"""Read clipboard only on activation; defer presentation while other work has focus."""
from PySide6.QtCore import QObject,QTimer,Qt
from PySide6.QtWidgets import QApplication,QDialog,QVBoxLayout,QLabel,QListWidget,QListWidgetItem,QDialogButtonBox
from clipboard_links import extract,SeenLinks
import preferences

def choose_links(urls,parent):
    dialog=QDialog(parent);dialog.setWindowTitle('选择要解析的链接');dialog.resize(680,410);box=QVBoxLayout(dialog)
    box.addWidget(QLabel(f'发现 {len(urls)} 个不同链接，选择后依次解析，不会自动下载。'))
    listing=QListWidget();box.addWidget(listing)
    for url in urls:
        item=QListWidgetItem(url);item.setFlags(item.flags()|Qt.ItemFlag.ItemIsUserCheckable);item.setCheckState(Qt.CheckState.Checked);listing.addItem(item)
    buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
    buttons.button(QDialogButtonBox.StandardButton.Ok).setText('解析所选链接');buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject);box.addWidget(buttons)
    if dialog.exec()!=QDialog.DialogCode.Accepted:return []
    return [listing.item(i).text() for i in range(listing.count()) if listing.item(i).checkState()==Qt.CheckState.Checked]

class ClipboardWatcher(QObject):
    def __init__(self,window):
        super().__init__(window);self.window=window;self.pending=[];self.seen=SeenLinks();self.handling=False
        QApplication.instance().applicationStateChanged.connect(self.activation)
        self.retry=QTimer(self);self.retry.setInterval(600);self.retry.timeout.connect(self.present);self.retry.start()
        QTimer.singleShot(1000,self.check)
    def activation(self,state):
        if state==Qt.ApplicationState.ApplicationActive:QTimer.singleShot(180,self.check)
    def check(self):
        if not preferences.load()['clipboard_enabled']:self.pending=[];return
        if self.handling:return
        self.pending=self.seen.unseen(extract(QApplication.clipboard().text()));self.present()
    def present(self):
        if not self.pending or self.handling or not preferences.load()['clipboard_enabled']:return
        window=self.window;app=QApplication.instance()
        if window.close_when_idle or window.is_busy() or app.activeWindow()!=window or window.workspace.add_dialog.isVisible() or app.activeModalWidget():return
        if any(isinstance(widget,QDialog) and widget.isWindow() and widget.isVisible() for widget in app.topLevelWidgets()):return
        self.handling=True
        try:
            urls=self.pending;self.pending=[]
            selected=choose_links(urls,window) if len(urls)>1 else urls
            self.seen.mark(urls)
            if selected:window.workspace.add_flow.start_urls(selected,automatic=True)
        finally:self.handling=False
