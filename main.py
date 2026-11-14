"""Functional Windows prototype: inspect a link, choose a stream, save a video.

This prototype checks the real download and account-session workflow. It is not
a production release or a claim of universal compatibility with any platform.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
import threading
import time
import traceback
from urllib.parse import urlsplit

import diagnostics

from runtime import APP_NAME, APP_VERSION, SITES, default_download_dir

CCTV_SAMPLE = "https://tv.cctv.com/2018/08/10/VIDEQ6OJXSs99b8GFIAQ45YP180810.shtml"
HAOKAN_SAMPLE = "https://haokan.baidu.com/v?vid=6165840185958740297"


def site_for_url(text: str) -> str:
    text = text.replace("https\\://", "https://").replace("http\\://", "http://")
    match = re.search(r"https?://[^\s<>\"'，。！？【】（）]+", text, re.I)
    if not match:
        return "其他网站"
    try:
        host = (urlsplit(match.group(0)).hostname or "").lower()
    except ValueError:
        return "其他网站"
    from site_registry import lookup
    entry = lookup(match.group(0))
    if entry:
        return entry["name"]
    sites = (("cctv.com", "央视网"), ("cntv.cn", "央视网"),
             ("haokan.baidu.com", "好看视频"), ("douyin.com", "抖音"),
             ("agedm.io", "AGE动漫"), ("bilibili.com", "B站"), ("b23.tv", "B站"), ("youku.com", "优酷"))
    for domain, name in sites:
        if host == domain or host.endswith("." + domain):
            return name
    return "其他网站"


def cli(args: argparse.Namespace) -> int:
    from engine import inspect_video, download_video
    report: dict = {}
    try:
        url = args.inspect or args.download
        info = inspect_video(url, log=lambda message: None)
        def public(value):
            if isinstance(value, dict):
                return {k: public(v) for k, v in value.items() if not k.startswith("_")}
            if isinstance(value, list):
                return [public(item) for item in value]
            return value
        report = public(info)
        if args.download:
            selected = args.format or info["formats"][0]["id"]
            report["download"] = download_video(info, selected, args.output or str(default_download_dir()))
        report["ok"] = True
        result = 0
    except Exception as exc:
        report = {"ok": False, "error": str(exc)}
        result = 1
    body = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    if args.report:
        target = Path(args.report)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    if sys.stdout:
        print(body)
    return result


def gui(args: argparse.Namespace) -> int:
    if args.smoke_ui and os.name != "nt":
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal, Slot, QUrl
    from PySide6.QtGui import QDesktopServices, QFont
    from PySide6.QtWidgets import (
        QApplication, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel,
        QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar,
        QPushButton, QSizePolicy, QSplitter, QVBoxLayout, QWidget, QDialog, QTableWidget, QTableWidgetItem, QHeaderView,
    )
    from accounts import AccountManager, AccountsDialog
    from engine import inspect_video, download_video
    import download_tasks as tasks
    from download_queue import DownloadQueue
    import preferences

    class Worker(QObject):
        succeeded = Signal(object)
        failed = Signal(str)
        progress = Signal(object)
        log = Signal(str)
        finished = Signal()

        def __init__(self, action):
            super().__init__()
            self.action = action

        @Slot()
        def run(self):
            try:
                started=time.monotonic();diagnostics.event('worker.begin')
                result=self.action(self);diagnostics.event('worker.action.return',f'duration={time.monotonic()-started:.3f}s')
                self.succeeded.emit(result)
            except Exception as exc:
                diagnostics.exception('worker.failed',exc,exc.__traceback__)
                self.failed.emit(str(exc))
            finally:
                self.finished.emit()

    class Window(QMainWindow):
        def __init__(self):
            super().__init__()
            self.setWindowTitle(f"{APP_NAME} · {APP_VERSION}")
            self.resize(1160, 840)
            self.setMinimumSize(950, 730)
            self.info = None
            self.active_thread = None
            self.worker = None
            self.cancel_event = threading.Event()
            self.close_when_idle = False
            self.current_task_id = None
            self.resume_task_id = None
            self.resume_after_inspect = False
            self.task_kind = ""
            self.last_result = None
            self.browser_dialog = None
            self.resume_pending=[];self.resume_browser=None;self.resume_browser_id=None
            self.browser_result = None
            self.accounts = AccountManager(self)
            self.accounts.changed.connect(self.account_changed)
            self.accounts_dialog = None
            self.queue = DownloadQueue(preferences.parallel_limit(), self)
            self.diagnostic_dialog = None
            self.task_dialog = None
            self.add_animations = []
            self.queue.progress.connect(self.queue_progress)
            self.queue.message.connect(self.queue_log)
            self.queue.completed.connect(self.queue_completed)
            self.queue.failed.connect(self.queue_failed)
            self.queue.removed.connect(self.queue_removed)
            self.queue.changed.connect(self.refresh_buttons)
            self.queue.idle.connect(self.maybe_close)
            self._build()
            unfinished = tasks.pending()
            if unfinished:
                self.status_text.setText(f"有 {len(unfinished)} 个未完成任务，可在“下载任务”中继续")

        def _build(self):
            shell = QWidget()
            self.setCentralWidget(shell)
            outer = QHBoxLayout(shell)
            outer.setContentsMargins(0, 0, 0, 0)
            outer.setSpacing(0)

            sidebar = QFrame()
            sidebar.setObjectName("sidebar")
            sidebar.setFixedWidth(216)
            left = QVBoxLayout(sidebar)
            left.setContentsMargins(24, 30, 24, 24)
            brand = QLabel("WEB VIDEO\nDOWNLOADER")
            brand.setObjectName("brand")
            left.addWidget(brand)
            badge = QLabel("WINDOWS")
            badge.setObjectName("sideMuted")
            left.addWidget(badge)
            left.addSpacing(35)
            self.account_button = QPushButton("账号与扫码登录")
            self.account_button.setObjectName("sideButton")
            self.account_button.clicked.connect(self.show_accounts)
            left.addWidget(self.account_button)
            update_button = QPushButton("检查更新")
            update_button.setObjectName("sideButton")
            update_button.clicked.connect(self.check_updates)
            left.addWidget(update_button)
            task_button = QPushButton("下载任务")
            self.task_button = task_button
            task_button.setObjectName("sideButton")
            task_button.clicked.connect(self.show_tasks)
            left.addWidget(task_button)
            diagnostic_button=QPushButton('日志与故障反馈');diagnostic_button.setObjectName('sideButton')
            diagnostic_button.clicked.connect(self.show_diagnostics);left.addWidget(diagnostic_button)
            left.addSpacing(26)
            supported = QPushButton("查看已适配列表")
            supported.setObjectName("sideButton")
            supported.clicked.connect(self.show_supported)
            left.addWidget(supported)
            left.addStretch()
            note = QLabel("画质以实际视频流为准\n登录后可重新解析\n会员权限按视频验证")
            note.setObjectName("sideMuted")
            note.setWordWrap(True)
            left.addWidget(note)
            left.addSpacing(14)
            left.addWidget(QLabel(f"v{APP_VERSION}", objectName="sideMuted"))
            outer.addWidget(sidebar)

            content = QWidget()
            body = QVBoxLayout(content)
            body.setContentsMargins(32, 27, 32, 24)
            body.setSpacing(16)
            header = QHBoxLayout()
            heading = QVBoxLayout()
            heading.addWidget(QLabel("把视频保存到电脑", objectName="title"))
            heading.addWidget(QLabel("粘贴网页或分享文案，先查看实际可用的画质。", objectName="muted"))
            header.addLayout(heading)
            header.addStretch()
            settings = QPushButton('⚙ 设置'); settings.setToolTip('配置并行下载数')
            settings.clicked.connect(self.show_settings); header.addWidget(settings)
            self.state_badge = QLabel("等待链接")
            self.state_badge.setObjectName("stateBadge")
            self.state_badge.hide()
            body.addLayout(header)

            link_card = QFrame(objectName="card")
            links = QVBoxLayout(link_card)
            links.setContentsMargins(22, 18, 22, 18)
            row = QHBoxLayout()
            row.addWidget(QLabel("1  视频链接", objectName="sectionTitle"))
            row.addStretch()
            self.paste_button = QPushButton("粘贴")
            self.paste_button.clicked.connect(self.paste_url)
            row.addWidget(self.paste_button)
            links.addLayout(row)
            self.url_input = QPlainTextEdit()
            self.url_input.setObjectName("urlInput")
            self.url_input.setPlaceholderText("https://…\n也可以粘贴抖音或 B站的整段分享文案")
            self.url_input.setMaximumHeight(80)
            self.url_input.textChanged.connect(self.url_changed)
            links.addWidget(self.url_input)
            row = QHBoxLayout()
            self.site_label = QLabel("自动识别网站", objectName="muted")
            row.addWidget(self.site_label)
            row.addStretch()
            self.parse_button = QPushButton("解析视频")
            self.parse_button.setObjectName("primary")
            self.parse_button.setMinimumWidth(140)
            self.parse_button.clicked.connect(self.inspect)
            row.addWidget(self.parse_button)
            links.addLayout(row)
            body.addWidget(link_card)

            video_card = QFrame(objectName="card")
            video = QVBoxLayout(video_card)
            video.setContentsMargins(22, 18, 22, 18)
            video.addWidget(QLabel("2  画质与保存位置", objectName="sectionTitle"))
            self.video_title = QLabel("解析后在这里选择画质")
            self.video_title.setObjectName("videoTitle")
            self.video_title.setWordWrap(True)
            video.addWidget(self.video_title)
            self.video_meta = QLabel("这里会列出网站实际提供的画质。", objectName="muted")
            self.video_meta.setWordWrap(True)
            video.addWidget(self.video_meta)
            self.quality = QComboBox()
            self.quality.setEnabled(False)
            self.quality.addItem("请先解析视频")
            video.addWidget(self.quality)
            path_row = QHBoxLayout()
            self.output_path = QLineEdit(str(default_download_dir()))
            self.output_path.setReadOnly(True)
            path_row.addWidget(self.output_path, 1)
            self.path_button = QPushButton("选择文件夹")
            self.path_button.clicked.connect(self.choose_folder)
            path_row.addWidget(self.path_button)
            video.addLayout(path_row)
            row = QHBoxLayout()
            self.account_status = QLabel("账号：未登录", objectName="muted")
            row.addWidget(self.account_status)
            row.addStretch()
            self.download_button = QPushButton("下载所选画质")
            self.download_button.setObjectName("primary")
            self.download_button.setEnabled(False)
            self.download_button.setMinimumWidth(160)
            self.download_button.clicked.connect(self.download)
            row.addWidget(self.download_button)
            video.addLayout(row)
            body.addWidget(video_card)

            status_card = QFrame(objectName="card")
            status = QVBoxLayout(status_card)
            status.setContentsMargins(22, 16, 22, 16)
            row = QHBoxLayout()
            self.status_text = QLabel("准备就绪", objectName="sectionTitle")
            self.status_text.setWordWrap(True)
            row.addWidget(self.status_text, 1)
            self.cancel_button = QPushButton("停止")
            self.cancel_button.setEnabled(False)
            self.cancel_button.clicked.connect(self.cancel)
            row.addWidget(self.cancel_button)
            self.delete_button = QPushButton("取消下载")
            self.delete_button.clicked.connect(self.cancel_and_delete)
            self.delete_button.setEnabled(False)
            row.addWidget(self.delete_button)
            self.open_button = QPushButton("打开下载文件夹")
            self.open_button.clicked.connect(self.open_folder)
            row.addWidget(self.open_button)
            status.addLayout(row)
            self.progress_bar = QProgressBar()
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(0)
            self.progress_bar.setTextVisible(False)
            self.progress_bar.setFixedHeight(7)
            status.addWidget(self.progress_bar)
            self.detail = QLabel("下载完成后核对时长、分辨率和音视频。", objectName="muted")
            self.detail.setWordWrap(True)
            status.addWidget(self.detail)
            body.addWidget(status_card)

            self.log_box = QPlainTextEdit()
            self.log_box.setReadOnly(True)
            self.log_box.setMaximumBlockCount(150)
            self.log_box.setPlaceholderText("运行记录；网站要求登录或验证时会在这里说明。")
            self.log_box.setMinimumHeight(68)
            self.log_box.setMaximumHeight(120)
            body.addWidget(self.log_box, 1)
            outer.addWidget(content, 1)

        def is_busy(self):
            return self.active_thread is not None or self.browser_dialog is not None or self.resume_browser is not None

        def refresh_buttons(self):
            busy = self.is_busy() or self.close_when_idle
            self.parse_button.setEnabled(not busy and bool(self.url_input.toPlainText().strip()))
            self.download_button.setEnabled(not busy and bool(self.info))
            self.quality.setEnabled(not busy and bool(self.info))
            self.url_input.setReadOnly(busy)
            self.paste_button.setEnabled(not busy)
            self.path_button.setEnabled(not busy)
            active = self.current_task_id in self.queue.active or any(row[0] == self.current_task_id for row in self.queue.waiting)
            stopping_task = self.current_task_id in self.queue.active and self.queue.active[self.current_task_id][2].is_set()
            recoverable=False
            if self.current_task_id and tasks.task_file(self.current_task_id).exists():
                try:recoverable=tasks.load(self.current_task_id)['state'] not in tasks.TERMINAL
                except (OSError,ValueError):pass
            self.cancel_button.setText('取消解析' if busy else ('暂停' if active else '继续'))
            self.cancel_button.setEnabled((busy and not self.cancel_event.is_set()) or (not busy and not stopping_task and (active or recoverable)))
            self.delete_button.setEnabled(not busy and bool(self.current_task_id) and (active or recoverable))

        def url_changed(self):
            self.resume_task_id = None
            self.resume_after_inspect = False
            self.info = None
            self.quality.clear()
            self.quality.addItem("请先解析视频")
            site = site_for_url(self.url_input.toPlainText())
            self.site_label.setText(site)
            state = self.accounts.status(site) if site in SITES else "无需会话"
            self.account_status.setText("账号：" + state)
            self.video_title.setText("解析后在这里选择画质")
            self.video_meta.setText("这里会列出网站实际提供的画质。")
            self.state_badge.setText("等待解析")
            self.refresh_buttons()

        def paste_url(self):
            self.url_input.setPlainText(QApplication.clipboard().text().strip())

        def choose_folder(self):
            folder = QFileDialog.getExistingDirectory(self, "选择下载文件夹", self.output_path.text(),QFileDialog.Option.ShowDirsOnly|QFileDialog.Option.DontUseNativeDialog)
            if folder:
                self.output_path.setText(folder)

        def open_folder(self):
            folder = Path(self.output_path.text())
            folder.mkdir(parents=True, exist_ok=True)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

        def show_settings(self):
            from PySide6.QtWidgets import QSpinBox
            dialog = QDialog(self); dialog.setWindowTitle('下载设置'); dialog.resize(380, 200)
            layout = QVBoxLayout(dialog); layout.addWidget(QLabel('同时运行的下载任务数（1～10）'))
            count = QSpinBox(); count.setRange(1,10); count.setValue(self.queue.limit); layout.addWidget(count)
            explanation = QLabel('超过上限的任务排队等待。降低上限不会中断正在运行的任务。设置会在重启后保留。')
            explanation.setWordWrap(True); layout.addWidget(explanation)
            save = QPushButton('保存'); layout.addWidget(save)
            def apply():
                try:
                    preferences.save_limit(count.value()); self.queue.set_limit(count.value()); dialog.accept()
                except Exception as error: QMessageBox.warning(dialog, '设置未保存', str(error))
            save.clicked.connect(apply); dialog.exec()

        def show_diagnostics(self):
            if self.diagnostic_dialog:
                self.diagnostic_dialog.show();self.diagnostic_dialog.raise_();return
            from diagnostic_view import DiagnosticDialog
            dialog=DiagnosticDialog(self);dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
            self.diagnostic_dialog=dialog
            dialog.finished.connect(lambda _:setattr(self,'diagnostic_dialog',None));dialog.show()

        def show_tasks(self):
            if self.task_dialog:
                self.task_dialog.show();self.task_dialog.raise_();return
            from task_view import TaskDialog
            dialog=TaskDialog(self.queue,lambda task_id:self.resume_task(task_id,report_error=False),self);self.task_dialog=dialog
            def closed(_):self.task_dialog=None
            dialog.finished.connect(closed);dialog.show()

        def resume_task(self, task_id, report_error=True):
            if self.close_when_idle:return False
            if task_id in self.queue.active or any(row[0]==task_id for row in self.queue.waiting):return False
            try:
                task=tasks.load_record(task_id)
                if task['state'] in tasks.TERMINAL:return False
                if task['site']=='抖音' and self.queue.cached(task_id) is None:
                    if task_id in self.resume_pending or task_id==self.resume_browser_id:return False
                    self.resume_pending.append(task_id)
                    QTimer.singleShot(0,self.start_resume_verification)
                    return True
                cookies=self.accounts.cookies_for(task['site']) if task['site'] in SITES else []
                accepted=self.queue.enqueue(task_id,cookies)
                self.current_task_id=task_id;self.refresh_buttons()
                return accepted
            except Exception as error:
                if not report_error:raise
                QMessageBox.warning(self,'无法恢复任务',str(error));return False

        def start_resume_verification(self):
            if self.close_when_idle or self.resume_browser is not None:return
            if self.is_busy():
                QTimer.singleShot(500,self.start_resume_verification);return
            while self.resume_pending:
                task_id=self.resume_pending.pop(0)
                try:
                    task=tasks.load_record(task_id)
                    if task['state'] in tasks.TERMINAL:continue
                    from douyin_browser import DouyinCapture
                    from engine import _extract_url
                    dialog=DouyinCapture(self.accounts._profile('抖音'),_extract_url(task['url']),self)
                    self.resume_browser=dialog;self.resume_browser_id=task_id;result=[]
                    dialog.captured.connect(result.append)
                    def finished(_,task_id=task_id,result=result,dialog=dialog):
                        self.resume_browser=None;self.resume_browser_id=None
                        try:
                            task=tasks.load_record(task_id)
                            if not self.close_when_idle and task['state'] not in tasks.TERMINAL:
                                if not result:raise ValueError('已取消网页验证；任务保留，可重新继续。')
                                info=result[-1]
                                if not any(row['id']==task['format_id'] for row in info['formats']):
                                    raise ValueError('原任务画质已不可用，请重新解析。')
                                self.accounts._import_current('抖音','抖音' in self.accounts._remembered)
                                self.queue.enqueue(task_id,self.accounts.cookies_for('抖音'),info)
                        except Exception as error:self.queue.failed.emit(task_id,str(error))
                        dialog.deleteLater();self.refresh_buttons();self.maybe_close()
                        QTimer.singleShot(0,self.start_resume_verification)
                    dialog.finished.connect(finished);dialog.show();self.refresh_buttons();return
                except Exception as error:self.queue.failed.emit(task_id,str(error))

        def show_supported(self):
            dialog = QDialog(self)
            dialog.setWindowTitle("已适配列表")
            dialog.resize(760, 440)
            layout = QVBoxLayout(dialog)
            search = QLineEdit()
            search.setPlaceholderText("查询网站名称或域名")
            layout.addWidget(search)
            table = QTableWidget(0, 4)
            table.setHorizontalHeaderLabels(["网站", "域名", "验证情况", "适配时间"])
            table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
            table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
            layout.addWidget(table)
            from site_registry import entries
            rows = [(row['name'], ' / '.join(row['domains']), row['status'], row['adapted_at']) for row in entries()]
            controls = QHBoxLayout()
            previous, following = QPushButton("上一页"), QPushButton("下一页")
            page_label = QLabel()
            controls.addWidget(previous)
            controls.addWidget(page_label)
            controls.addWidget(following)
            layout.addLayout(controls)
            page = [0]
            def render(reset=False):
                if reset: page[0] = 0
                term = search.text().strip().lower()
                filtered = [row for row in rows if term in " ".join(row).lower()]
                pages = max(1, (len(filtered) + 3) // 4)
                page[0] = min(page[0], pages - 1)
                current = filtered[page[0]*4:page[0]*4+4]
                table.setRowCount(len(current))
                for r, row in enumerate(current):
                    for c, value in enumerate(row): table.setItem(r, c, QTableWidgetItem(value))
                page_label.setText(f"第 {page[0]+1} / {pages} 页 · 共 {len(filtered)} 个网站")
                previous.setEnabled(page[0] > 0)
                following.setEnabled(page[0] < pages - 1)
            def turn(delta):
                page[0] += delta
                render()
            search.textChanged.connect(lambda: render(True))
            previous.clicked.connect(lambda: turn(-1))
            following.clicked.connect(lambda: turn(1))
            render()
            dialog.exec()

        def check_updates(self):
            if self.is_busy() or self.queue.active or self.queue.waiting or self.resume_pending:
                QMessageBox.information(self, "请先完成当前任务", "下载或解析结束后再安装更新。")
                return
            from updater import show_update_dialog
            show_update_dialog(self)

        def show_accounts(self):
            if self.accounts_dialog is None:
                self.accounts_dialog = AccountsDialog(self.accounts, self)
            self.accounts_dialog.show()
            self.accounts_dialog.raise_()
            self.accounts_dialog.activateWindow()

        @Slot(str, str)
        def account_changed(self, site, status):
            self.append_log(f"{site}：{status}")
            if site == site_for_url(self.url_input.toPlainText()):
                self.account_status.setText("账号：" + status)
                if self.info and not self.is_busy():
                    self.info = None
                    self.quality.clear()
                    self.quality.addItem("账号状态已改变，请重新解析")
                    self.state_badge.setText("需要重新解析")
                    self.refresh_buttons()

        def start_task(self, kind, action):
            if self.is_busy():
                return
            self.cancel_event = threading.Event()
            self.task_kind = kind
            diagnostics.event('operation.begin',kind)
            self.active_thread = QThread(self)
            self.worker = Worker(action)
            self.worker.moveToThread(self.active_thread)
            self.active_thread.started.connect(self.worker.run)
            self.worker.succeeded.connect(self.task_success)
            self.worker.failed.connect(self.task_failure)
            self.worker.progress.connect(self.update_progress)
            self.worker.log.connect(self.append_log)
            self.worker.finished.connect(self.active_thread.quit)
            self.worker.finished.connect(self.worker.deleteLater)
            self.active_thread.finished.connect(self.thread_finished)
            self.active_thread.finished.connect(self.active_thread.deleteLater)
            self.active_thread.start()
            self.refresh_buttons()

        def inspect(self):
            text = self.url_input.toPlainText().strip()
            if not text:
                return
            site = site_for_url(text)
            cookies = self.accounts.cookies_for(site) if site in SITES else []
            self.info = None
            self.quality.clear()
            self.quality.addItem("正在获取画质列表…")
            self.video_title.setText("正在读取视频信息…")
            self.video_meta.setText("获取清晰度列表，不会自动开始下载。")
            self.state_badge.setText("解析中")
            self.status_text.setText("正在解析链接")
            self.progress_bar.setRange(0, 0)
            self.detail.setText("部分网站可能需要先登录账号或完成访问验证。")
            self.append_log(f"开始解析：{site}")
            if site == "抖音":
                from engine import _extract_url
                from douyin_browser import DouyinCapture
                self.task_kind = "inspect"
                self.browser_result = None
                self.browser_dialog = DouyinCapture(self.accounts._profile("抖音"), _extract_url(text), self)
                self.browser_dialog.captured.connect(self.capture_received)
                self.browser_dialog.finished.connect(self.capture_finished)
                self.browser_dialog.show()
                self.detail.setText("请在新窗口查看官方作品页面；需要时手动完成登录或验证。")
                self.refresh_buttons()
            else:
                self.start_task("inspect", lambda worker: inspect_video(text, cookies, worker.log.emit, self.cancel_event))

        def capture_received(self, info):
            self.browser_result = info

        def capture_finished(self, _result):
            self.browser_dialog = None
            if self.browser_result:
                try:
                    self.accounts._import_current("抖音", "抖音" in self.accounts._remembered)
                except OSError:
                    self.append_log("会话可用于本次下载，但未能更新本机加密存储。")
                self.task_kind = "inspect"
                self.task_success(self.browser_result)
                self.task_kind = ""
                self.refresh_buttons()
                if self.info.get("_kind") == "images" and not self.resume_after_inspect:
                    choice = QMessageBox(self)
                    choice.setWindowTitle("选择图文保存方式")
                    choice.setText(f"检测到 {len(self.info['_images'])} 张图片，请选择保存方式。")
                    choice.setInformativeText("合成视频将按原图顺序展示，每张 3 秒，并使用作品配乐。")
                    originals = choice.addButton("保存原图", QMessageBox.ButtonRole.AcceptRole)
                    movie = choice.addButton("合成带配乐的视频", QMessageBox.ButtonRole.ActionRole)
                    choice.addButton("稍后选择", QMessageBox.ButtonRole.RejectRole)
                    choice.exec()
                    self.quality.setCurrentIndex(1 if choice.clickedButton() is movie else 0)
            else:
                self.task_failure("已取消抖音解析，未开始下载；可重新打开官方页面完成验证后重试。")
                self.task_kind = ""
                self.refresh_buttons()
            if self.close_when_idle:
                self.close()
            elif self.resume_after_inspect and self.info:
                self.resume_after_inspect = False
                QTimer.singleShot(0, self.download)

        def download(self):
            if not self.info or self.is_busy(): return
            info=self.info; selected=self.quality.currentData();folder=self.output_path.text()
            if selected is None: return
            try:
                task=tasks.load(self.resume_task_id) if self.resume_task_id else tasks.create(info,selected,folder)
                if task['format_id']!=selected: raise ValueError('原任务画质已不可用，请重新选择任务。')
                cookies=self.accounts.cookies_for(info['site']) if info['site'] in SITES else []
                if not self.queue.enqueue(task['id'],cookies,info): return
            except Exception as error:
                QMessageBox.warning(self,'未能加入任务',str(error));return
            self.current_task_id=task['id'];self.resume_task_id=None
            self.animate_added()
            self.url_input.clear()
            self.status_text.setText('已加入下载任务');self.detail.setText('可继续粘贴其他链接，下载在后台执行。')
            self.refresh_buttons()

        def animate_added(self):
            from PySide6.QtCore import QPropertyAnimation,QEasingCurve
            label=QLabel('+1 已加入下载任务',self);label.setStyleSheet('color:#285ed4;background:#e9f0ff;padding:8px;border-radius:6px;')
            label.adjustSize();label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents);label.show();label.raise_()
            animation=QPropertyAnimation(label,b'pos',self);animation.setDuration(500)
            animation.setStartValue(self.download_button.mapTo(self,self.download_button.rect().topLeft()))
            animation.setEndValue(self.task_button.mapTo(self,self.task_button.rect().topLeft()))
            animation.setEasingCurve(QEasingCurve.Type.OutCubic);self.add_animations.append(animation)
            def finish():
                label.deleteLater();self.add_animations.remove(animation);animation.deleteLater()
            animation.finished.connect(finish);animation.start()

        @Slot(str,object)
        def queue_progress(self,task_id,data):
            if task_id==self.current_task_id and not self.is_busy():
                percent=data.get('download_percent')
                self.status_text.setText(str(data.get('stage') or '下载中'))
                if percent is not None:
                    self.progress_bar.setRange(0,100);self.progress_bar.setValue(int(percent))
                    self.detail.setText(f'已下载 {percent:.1f}% · 完成后继续封装与校验')

        @Slot(str,str)
        def queue_log(self,task_id,text):
            self.append_log(f'任务 {task_id[:6]}：{text}')

        @Slot(str,object)
        def queue_completed(self,task_id,result):
            self.last_result=result
            self.append_log('下载完成：'+result['path'])
            if task_id==self.current_task_id and not self.is_busy():
                self.status_text.setText('下载完成');self.progress_bar.setValue(100);self.detail.setText(result['path'])
            self.refresh_buttons()

        @Slot(str,str)
        def queue_failed(self,task_id,error):
            self.append_log(f'任务 {task_id[:6]}：{error}')
            if task_id==self.current_task_id and not self.is_busy():
                self.status_text.setText('任务已停止，进度保留；详情见下载任务');self.detail.setText(error[:450])
            self.refresh_buttons()

        @Slot()
        def maybe_close(self):
            if self.close_when_idle and not self.is_busy() and not self.queue.active:
                QTimer.singleShot(0,self.close)

        @Slot(str)
        def queue_removed(self,task_id):
            self.resume_pending=[key for key in self.resume_pending if key!=task_id]
            if self.resume_browser_id==task_id:self.resume_browser.reject()
            if task_id==self.current_task_id:
                self.current_task_id=None
                if not self.is_busy():
                    self.status_text.setText('已取消并清理缓存，历史记录已保留')
                    self.detail.clear();self.progress_bar.setValue(0)

        @Slot(object)
        def task_success(self, result):
            diagnostics.event('ui.result.received',self.task_kind)
            self.progress_bar.setRange(0, 100)
            if self.task_kind == "inspect":
                self.info = result
                self.video_title.setText(result["title"])
                duration = float(result.get("duration") or 0)
                duration_label = f"{int(duration)//60:02d}:{int(duration)%60:02d}" if duration else "时长待检测"
                self.video_meta.setText(f"{result['site']}  ·  {duration_label}  ·  {len(result['formats'])} 种可选格式")
                self.quality.clear()
                for option in result["formats"]:
                    self.quality.addItem(option["label"], option["id"])
                if self.resume_task_id:
                    selected_id = tasks.load(self.resume_task_id)['format_id']
                    index = self.quality.findData(selected_id)
                    if index < 0:
                        self.resume_after_inspect = False
                        self.append_log('原任务的画质暂不可用，进度已保留；请稍后从下载任务重试。')
                    self.quality.setCurrentIndex(index)
                self.status_text.setText("解析完成，选择画质后下载")
                self.detail.setText("列表来自网站当前返回的视频流；账号权限以实际结果为准。")
                self.state_badge.setText("可下载")
                self.progress_bar.setValue(0)
                self.append_log(f"解析完成：{result['site']}，{len(result['formats'])} 档画质。")
                diagnostics.event('ui.result.applied',result['site'])

        @Slot(str)
        def task_failure(self, message):
            self.progress_bar.setRange(0,100);self.progress_bar.setValue(0)
            self.status_text.setText('解析已取消' if self.cancel_event.is_set() else '本次解析未完成')
            self.detail.setText(message[:450]);self.append_log(message)
            self.resume_after_inspect=False;self.info=None
            self.video_title.setText('未取得可下载的视频信息')
            self.video_meta.setText('可完成官方登录或验证后重新解析。')

        @Slot()
        def thread_finished(self):
            diagnostics.event('operation.thread.finished',self.task_kind)
            self.active_thread = None
            self.worker = None
            self.task_kind = ""
            self.refresh_buttons()
            if self.close_when_idle:
                self.close()
            elif self.resume_after_inspect and self.info:
                self.resume_after_inspect = False
                QTimer.singleShot(0, self.download)

        @Slot(object)
        def update_progress(self, data):
            stage = str(data.get("stage") or "正在下载")
            self.status_text.setText(stage)
            if "校验" in stage or "解码" in stage or "封装" in stage:
                self.state_badge.setText("处理中")
            percent = data.get("percent")
            if percent is not None:
                self.progress_bar.setRange(0, 100)
                self.progress_bar.setValue(max(0, min(100, int(float(percent)))))
            details = []
            if percent is not None:
                details.append(f"{float(percent):.0f}%")
            speed = data.get("speed")
            if speed:
                details.append(f"{float(speed)/(1024*1024):.1f} MiB/s" if isinstance(speed, (int, float)) else str(speed))
            eta = data.get("eta")
            if eta is not None:
                details.append(f"剩余约 {int(eta)} 秒" if isinstance(eta, (int, float)) else str(eta))
            if details:
                self.detail.setText("  ·  ".join(details))

        @Slot(str)
        def append_log(self, message):
            diagnostics.event('ui.log',message)
            clean = re.sub(r"https?://\S+", "[网站地址]", str(message))
            clean = re.sub(r"(?i)(cookie|authorization|access_token|auth_key|sessdata)\s*[:=]\s*\S+", r"\1=[已隐藏]", clean)
            self.log_box.appendPlainText(clean)
            if self.is_busy() and self.task_kind=='inspect' and clean.startswith('AGE：'):
                self.status_text.setText(clean)

        def cancel_and_delete(self):
            if self.current_task_id: self.queue.discard(self.current_task_id)

        def cancel(self):
            if self.is_busy():
                self.cancel_event.set()
                if self.browser_dialog is not None: self.browser_dialog.reject()
                if self.resume_browser is not None:self.resume_browser.reject()
                self.refresh_buttons();return
            task_id=self.current_task_id
            if not task_id: return
            if task_id in self.queue.active or any(row[0]==task_id for row in self.queue.waiting): self.queue.pause(task_id)
            else:self.resume_task(task_id)

        def closeEvent(self,event):
            if self.diagnostic_dialog and self.diagnostic_dialog.thread is not None:
                self.diagnostic_dialog.reject();QTimer.singleShot(100,self.close);event.ignore();return
            self.resume_pending.clear()
            if self.resume_browser is not None:
                self.close_when_idle=True;self.resume_browser.reject()
            if self.is_busy() or self.queue.active or self.queue.waiting:
                self.close_when_idle=True
                self.queue.shutdown()
                if self.is_busy(): self.cancel()
                event.ignore();self.maybe_close();return
            if self.task_dialog:self.task_dialog.close()
            if self.accounts_dialog:self.accounts_dialog.close()
            if self.diagnostic_dialog:self.diagnostic_dialog.close()
            event.accept()

    app = QApplication(sys.argv[:1])
    app.setApplicationName("WebVideoDownloader")
    app.setOrganizationName("LocalTools")
    app.setFont(QFont("Microsoft YaHei UI", 10))
    app.setStyleSheet(STYLE)
    recorder=diagnostics.start()
    heartbeat=QTimer(app);heartbeat.setInterval(500)
    if recorder:heartbeat.timeout.connect(recorder.beat);heartbeat.start()
    window = Window()
    if args.url:
        window.url_input.setPlainText(args.url)
    window.refresh_buttons()
    window.show()
    if args.smoke_ui:
        def capture():
            if args.screenshot:
                target = Path(args.screenshot)
                target.parent.mkdir(parents=True, exist_ok=True)
                window.grab().save(str(target))
            window.close()
            app.quit()
        QTimer.singleShot(900, capture)
    if not args.smoke_ui and not getattr(args,'smoke_inspect',False):
        def notify_incidents():
            names=diagnostics.unreported()
            if not names:return
            answer=QMessageBox.question(window,'发现异常记录','发现上次异常退出或疑似卡死记录。是否打开日志与故障反馈？')
            diagnostics.acknowledge(names)
            if answer==QMessageBox.StandardButton.Yes:window.show_diagnostics()
        QTimer.singleShot(1500,notify_incidents)
    inspection_ok=[False]
    if getattr(args,'smoke_inspect',False):
        started=time.monotonic()
        def inspect_result():
            if window.info:
                inspection_ok[0]=True;window.close()
            elif time.monotonic()-started>100:window.cancel();window.close()
        probe=QTimer(window);probe.setInterval(250);probe.timeout.connect(inspect_result);probe.start()
        QTimer.singleShot(250,window.inspect)
    try:
        result=app.exec()
        return (0 if inspection_ok[0] else 2) if getattr(args,'smoke_inspect',False) else result
    finally:
        heartbeat.stop()
        if recorder:recorder.stop()


STYLE = """
QWidget { color:#17283b; font-family:'Microsoft YaHei UI'; font-size:13px; }
QMainWindow { background:#f3f5f8; }
QFrame#sidebar { background:#142335; }
QLabel#brand { color:#ffffff; font-size:17px; font-weight:700; letter-spacing:0px; }
QLabel#sideMuted { color:#98a9bd; font-size:11px; line-height:1.7; }
QLabel#activeNav { color:#ffffff; background:#294361; border-radius:7px; font-weight:600; }
QLabel#siteItem { color:#c8d3df; min-height:25px; }
QPushButton#sideButton { color:#d3deea; background:transparent; border:1px solid #3a4d63; margin-top:8px; }
QPushButton#sideButton:hover { background:#294361; }
QLabel#title { font-size:25px; font-weight:700; }
QLabel#muted { color:#6b7c90; font-size:12px; }
QLabel#stateBadge { color:#256b54; background:#e1f0e9; padding:7px 13px; border-radius:13px; font-size:12px; }
QFrame#card { background:#ffffff; border:1px solid #e0e5ed; border-radius:11px; }
QLabel#sectionTitle { font-size:14px; font-weight:600; }
QLabel#videoTitle { font-size:16px; font-weight:600; margin-top:4px; }
QPushButton { background:#ffffff; color:#34485e; border:1px solid #d7dfe9; padding:8px 14px; border-radius:6px; min-height:18px; }
QPushButton:hover { background:#edf2f7; border-color:#aebed0; }
QPushButton:disabled { color:#a6b0bc; background:#f3f5f8; border-color:#e3e8ee; }
QPushButton#primary { color:#ffffff; background:#285ed4; border-color:#285ed4; font-weight:600; }
QPushButton#primary:hover { background:#1c4fbf; }
QPushButton#primary:disabled { color:#dae3f3; background:#9db6e8; border-color:#9db6e8; }
QLineEdit, QPlainTextEdit, QComboBox { background:#f9fbfd; border:1px solid #dce3ec; border-radius:6px; padding:9px; selection-background-color:#bed2fc; }
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus { border-color:#6994ec; }
QComboBox { min-height:21px; }
QComboBox#examples { min-width:174px; padding:4px 8px; font-size:11px; }
QComboBox::drop-down { border:0; width:24px; }
QProgressBar { background:#eaf0f7; border:0; border-radius:3px; }
QProgressBar::chunk { background:#3877e8; border-radius:3px; }
QPlainTextEdit { font-size:12px; }
QToolTip { background:white; color:#17283b; border:1px solid #dce3ec; }
"""


def main() -> int:
    if len(sys.argv)==3 and sys.argv[1]=='--diagnostic-watchdog':
        diagnostics.watchdog(sys.argv[2]);return 0
    parser = argparse.ArgumentParser(description="Windows video downloader prototype")
    operation = parser.add_mutually_exclusive_group()
    operation.add_argument("--inspect", help="Inspect a URL without opening the GUI")
    operation.add_argument("--download", help="Inspect and download a URL without opening the GUI")
    parser.add_argument("--format", help="Selected format id; default is the first listed format")
    parser.add_argument("--output", help="Download directory")
    parser.add_argument("--report", help="Write a JSON result for command-line verification")
    parser.add_argument("--url", help="Prefill the GUI URL field")
    parser.add_argument("--smoke-ui", action="store_true", help="Open and close an offscreen window")
    parser.add_argument("--smoke-inspect",action="store_true",help=argparse.SUPPRESS)
    parser.add_argument("--screenshot", help="Save the offscreen window screenshot")
    args = parser.parse_args()
    if args.inspect or args.download:
        return cli(args)
    return gui(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        from runtime import app_data_dir
        folder = app_data_dir()
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "startup-error.log").write_text(diagnostics.redact_export(traceback.format_exc()), encoding="utf-8")
        raise
