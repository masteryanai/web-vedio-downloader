"""Lazily loaded official account browser; the task workspace does not import it."""
from __future__ import annotations
from PySide6.QtCore import Qt,QTimer,QUrl
from PySide6.QtWebEngineCore import QWebEnginePage,QWebEngineProfile,QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QDialog,QVBoxLayout,QLabel,QCheckBox,QDialogButtonBox,QMessageBox
import shiboken6
from accounts import AccountManager,LOGIN_URLS

class _SessionView(QWebEngineView):
    def __init__(self, profile: QWebEngineProfile, owner, parent=None):
        super().__init__(parent)
        self._owner = owner
        self.setPage(QWebEnginePage(profile, self))
        self.settings().setAttribute(
            QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, True)

    def createWindow(self, _window_type):
        return self._owner._make_popup()


class _LoginDialog(QDialog):
    def __init__(self, manager: AccountManager, site: str, parent=None,
                 *, navigate: bool = True):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle(f"{site} · 官方网页登录")
        self.resize(1100, 800)
        self._manager, self._site = manager, site
        self._views: list[_SessionView] = []
        self._popups: list[QDialog] = []
        self._disposed = False
        layout = QVBoxLayout(self)
        info = QLabel("请在下方官方网页中点击登录，使用网站提供的扫码、短信或验证码流程。\n"
                      "完成后点击“使用当前会话”。本程序不会验证用户名或会员身份；下载权限会在解析时检查。")
        info.setWordWrap(True)
        layout.addWidget(info)
        self._address = QLabel(LOGIN_URLS[site])
        self._address.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self._address)
        self._view = self._new_view(self)
        self._view.urlChanged.connect(lambda url: self._address.setText(url.toString()))
        layout.addWidget(self._view, 1)
        self._remember = QCheckBox("记住登录（仅本机当前 Windows 用户可解密）")
        self._remember.setChecked(site in manager._remembered)
        layout.addWidget(self._remember)
        note = QLabel("默认仅保留到程序退出。勾选后仅加密保存会话 Cookie，仍可能因网站过期或风控而失效。")
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QDialogButtonBox()
        self._use = buttons.addButton("使用当前会话", QDialogButtonBox.ButtonRole.AcceptRole)
        self._use.clicked.connect(self._confirm)
        refresh = buttons.addButton("重新加载", QDialogButtonBox.ButtonRole.ActionRole)
        refresh.clicked.connect(self._view.reload)
        cancel = buttons.addButton("关闭", QDialogButtonBox.ButtonRole.RejectRole)
        cancel.clicked.connect(self.reject)
        layout.addWidget(buttons)
        if navigate:
            self._view.setUrl(QUrl(LOGIN_URLS[site]))

    def _new_view(self, parent) -> _SessionView:
        view = _SessionView(self._manager._profile(self._site), self, parent)
        self._views.append(view)
        return view

    def _make_popup(self) -> QWebEngineView:
        popup = QDialog(self)
        popup.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        popup.setWindowTitle(f"{self._site} · 官方登录窗口")
        popup.resize(950, 720)
        layout = QVBoxLayout(popup)
        view = self._new_view(popup)
        layout.addWidget(view)
        popup.finished.connect(lambda _result, v=view: self._release_view(v))
        self._popups.append(popup)
        popup.show()
        return view

    def _confirm(self) -> None:
        self._use.setEnabled(False)
        self._use.setText("读取当前会话…")
        self._manager._profile(self._site).cookieStore().loadAllCookies()
        # cookieAdded is delivered asynchronously; the continuously maintained
        # cookie map is also refreshed before taking the user's snapshot.
        QTimer.singleShot(250, self._finish_confirm)

    def _finish_confirm(self) -> None:
        if self._disposed:
            return
        try:
            imported = self._manager._import_current(self._site, self._remember.isChecked())
        except OSError:
            QMessageBox.warning(self, "会话保存失败",
                                "当前会话已导入本次运行，但未能更新本机加密存储。请检查文件夹权限。")
            self.accept()
            return
        if imported:
            self.accept()
            return
        self._use.setEnabled(True)
        self._use.setText("使用当前会话")
        QMessageBox.information(self, "尚未读取到会话",
                                "请先在官方网页完成登录，并等待页面加载，再点击“使用当前会话”。")

    def _release_view(self, view) -> None:
        if shiboken6.isValid(view):
            view.stop()
            # Destroy the page synchronously, before its isolated profile.
            shiboken6.delete(view)

    def _dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        for popup in self._popups:
            if shiboken6.isValid(popup):
                popup.reject()
        for view in self._views:
            self._release_view(view)
        self._views.clear()
        self._popups.clear()

    def done(self, result: int) -> None:
        self._dispose()
        super().done(result)

    def closeEvent(self, event) -> None:
        self._dispose()
        super().closeEvent(event)


