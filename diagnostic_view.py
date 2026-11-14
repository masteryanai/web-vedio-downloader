"""Explicit, user-reviewed export and GitHub feedback; uploads nothing."""
from pathlib import Path
from urllib.parse import urlencode
from PySide6.QtCore import QObject,QThread,Signal,Slot,QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QDialog,QVBoxLayout,QLabel,QPlainTextEdit,QPushButton,QFileDialog
import diagnostics
from runtime import APP_VERSION,default_download_dir


def feedback_url(report_id,description):
    body=f'''版本：{APP_VERSION}
诊断编号：{report_id}

操作步骤及现象：
{diagnostics.redact(description)[:1500]}

请在提交前检查内容，并手动添加刚刚导出的诊断 ZIP。导出诊断包不代表已提交故障报告。
'''
    return 'https://github.com/masteryanai/web-vedio-downloader/issues/new?'+urlencode({'title':f'[故障反馈] v{APP_VERSION}','body':body})


class ExportJob(QObject):
    success=Signal(str);failure=Signal(str);finished=Signal()
    def __init__(self,path,description):super().__init__();self.path=path;self.description=description
    @Slot()
    def run(self):
        try:self.success.emit(diagnostics.export_bundle(self.path,self.description))
        except Exception as error:
            diagnostics.exception('diagnostic.export.failed',error,error.__traceback__)
            self.failure.emit('导出失败：'+diagnostics.redact(str(error)))
        finally:self.finished.emit()


class DiagnosticDialog(QDialog):
    def __init__(self,parent=None):
        super().__init__(parent);self.setWindowTitle('日志与故障反馈');self.resize(620,440)
        self.thread=None;self.job=None;self.closing=False;self.submitting=False;self.path=None
        layout=QVBoxLayout(self)
        note=QLabel('日志默认开启，每份最多 5 MiB，最多保留 5 份。\n主界面连续 15 秒无心跳时记录疑似卡死；恢复后记录恢复时间。\n诊断包不包含账号会话、浏览器资料、下载文件或完整内存转储。')
        note.setWordWrap(True);layout.addWidget(note)
        self.description=QPlainTextEdit();self.description.setPlaceholderText('请描述操作步骤、预期结果和实际现象。链接和路径默认脱敏。');layout.addWidget(self.description)
        self.status=QLabel('仅导出到本地。反馈时由你检查内容、添加 ZIP 附件并提交。');self.status.setWordWrap(True);layout.addWidget(self.status)
        state=diagnostics.health()
        if not state['started']:self.status.setText('诊断记录当前不可用，仍可尝试导出现有记录。')
        elif not state['independent_watchdog']:self.status.setText('独立现场监测不可用，仍可导出已有日志和记录。')
        elif state['dropped_records']:self.status.setText(f"已有 {state['dropped_records']} 条记录未能保存；可导出其余诊断信息。")
        self.export_button=QPushButton('导出诊断包');self.export_button.clicked.connect(lambda:self.begin(False));layout.addWidget(self.export_button)
        self.feedback_button=QPushButton('导出并打开故障反馈页');self.feedback_button.clicked.connect(lambda:self.begin(True));layout.addWidget(self.feedback_button)
        self.close_button=QPushButton('关闭');self.close_button.clicked.connect(self.reject);layout.addWidget(self.close_button)

    def begin(self,submit):
        if self.thread is not None:return
        path,_=QFileDialog.getSaveFileName(self,'保存诊断包',str(default_download_dir()/f'WebVideoDownloader-diagnostic-{APP_VERSION}.zip'),'ZIP 文件 (*.zip)',options=QFileDialog.Option.DontUseNativeDialog)
        if not path:return
        self.path=Path(path);self.submitting=submit
        self.export_button.setEnabled(False);self.feedback_button.setEnabled(False);self.status.setText('正在后台收集并脱敏诊断信息…')
        self.thread=QThread(self);self.job=ExportJob(self.path,self.description.toPlainText());self.job.moveToThread(self.thread)
        self.thread.started.connect(self.job.run);self.job.success.connect(self.exported);self.job.failure.connect(self.status.setText)
        self.job.finished.connect(self.thread.quit);self.job.finished.connect(self.job.deleteLater)
        self.thread.finished.connect(self.done_export);self.thread.finished.connect(self.thread.deleteLater);self.thread.start()

    def exported(self,report_id):
        self.status.setText('诊断包已保存：'+str(self.path))
        if self.submitting and not self.closing:
            opened=QDesktopServices.openUrl(QUrl(feedback_url(report_id,self.description.toPlainText())))
            self.status.setText('诊断包已保存。'+('已打开反馈页，请检查内容、手动添加 ZIP 并提交。' if opened else '无法打开浏览器，请稍后手动到仓库 Issues 提交并添加 ZIP。'))
        diagnostics.event('diagnostic.export.complete',report_id)

    def done_export(self):
        self.thread=None;self.job=None;self.export_button.setEnabled(True);self.feedback_button.setEnabled(True)
        if self.closing:self.reject()

    def reject(self):
        if self.thread is not None:
            self.closing=True;self.status.setText('正在完成导出，完成后关闭；不会打开反馈页。');return
        super().reject()

    def closeEvent(self,event):
        if self.thread is not None:self.reject();event.ignore()
        else:event.accept()
