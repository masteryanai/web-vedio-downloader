"""Isolated official-site login windows and optional Windows-protected sessions.

Importing a session only copies cookies from this application's own WebEngine
profile.  It does not establish the account's identity, membership or download
permissions; the downloader still has to verify those with the service.
"""

from __future__ import annotations

import copy
import ctypes
import json
import os
from pathlib import Path
import sys
import tempfile
import time

from PySide6.QtCore import QObject, QDateTime, Qt, QTimer, QUrl, Signal
from PySide6.QtNetwork import QNetworkCookie
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QDialogButtonBox,
    QLabel, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QHeaderView, QAbstractItemView,
)
import shiboken6


SITES = ("央视网", "好看视频", "抖音", "B站", "优酷")
LOGIN_URLS = {
    "央视网": "https://tv.cctv.com/",
    "好看视频": "https://haokan.baidu.com/",
    "抖音": "https://www.douyin.com/",
    "B站": "https://passport.bilibili.com/login",
    "优酷": "https://www.youku.com/",
}
_COOKIE_DOMAINS = {
    "央视网": ("cctv.com", "cntv.cn", "cctv.cn"),
    "好看视频": ("baidu.com",),
    "抖音": ("douyin.com", "iesdouyin.com", "amemv.com"),
    "B站": ("bilibili.com", "biligame.com"),
    "优酷": ("youku.com", "tudou.com"),
}
_EMPTY_STATUS = "未登录"
_IMPORTED_STATUS = "会话已导入，权限待验证"


def _storage_path() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    base = Path(local) if local else Path.home() / "AppData" / "Local"
    return base / "WebVideoDownloaderPrototype" / "sessions.dpapi"


def _dpapi(data: bytes, *, decrypt: bool = False) -> bytes:
    """Use current-user DPAPI, never machine-wide protection or a plaintext file."""
    if sys.platform != "win32":
        raise OSError("记住登录仅支持 Windows DPAPI。")
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    source_buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source = Blob(len(data), source_buffer)
    result = Blob()
    if decrypt:
        fn = crypt.CryptUnprotectData
        fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                       ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
                       ctypes.POINTER(Blob)]
        args = (ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result))
    else:
        fn = crypt.CryptProtectData
        fn.argtypes = [ctypes.POINTER(Blob), wintypes.LPCWSTR, ctypes.c_void_p,
                       ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
                       ctypes.POINTER(Blob)]
        args = (ctypes.byref(source), "WebVideoDownloaderPrototype", None,
                None, None, 1, ctypes.byref(result))
    fn.restype = wintypes.BOOL
    if not fn(*args):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        kernel.LocalFree(ctypes.cast(result.data, ctypes.c_void_p))


def _belongs_to_site(site: str, domain: str) -> bool:
    domain = domain.lstrip(".").lower()
    return any(domain == suffix or domain.endswith("." + suffix)
               for suffix in _COOKIE_DOMAINS[site])


def _clean_cookie(site: str, value: object) -> dict | None:
    if not isinstance(value, dict):
        return None
    name, content, domain = (value.get(key) for key in ("name", "value", "domain"))
    if not all(isinstance(item, str) for item in (name, content, domain)):
        return None
    if not name or not _belongs_to_site(site, domain):
        return None
    expiry = value.get("expires")
    if expiry is not None:
        if not isinstance(expiry, (int, float)) or expiry <= time.time():
            return None
        expiry = int(expiry)
    path = value.get("path", "/")
    return {
        "name": name, "value": content, "domain": domain,
        "path": path if isinstance(path, str) and path else "/",
        "secure": bool(value.get("secure", False)),
        "httpOnly": bool(value.get("httpOnly", False)), "expires": expiry,
    }


def _cookie_key(cookie: dict) -> tuple[str, str, str]:
    return cookie["domain"], cookie["path"], cookie["name"]


class AccountManager(QObject):
    changed = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sessions: dict[str, list[dict]] = {site: [] for site in SITES}
        self._remembered: set[str] = set()
        self._profiles: dict[str, QWebEngineProfile] = {}
        self._browser_cookies: dict[str, dict[tuple, dict]] = {}
        self._dialogs: dict[str, _LoginDialog] = {}
        self.clear_on_exit = False
        self.last_error = ""
        self._shutting_down = False
        self._load_remembered()
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.shutdown)
        if parent is not None:
            parent.destroyed.connect(self.shutdown)

    @staticmethod
    def _check_site(site: str) -> None:
        if site not in SITES:
            raise ValueError(f"未知站点：{site}")

    def cookies_for(self, site: str) -> list[dict]:
        self._check_site(site)
        return [clean for value in self._sessions[site]
                if (clean := _clean_cookie(site, value)) is not None]

    def status(self, site: str) -> str:
        return _IMPORTED_STATUS if self.cookies_for(site) else _EMPTY_STATUS

    def _profile(self, site: str) -> QWebEngineProfile:
        if site in self._profiles:
            return self._profiles[site]
        # No storage name: this is an off-the-record profile with no browser
        # storage shared with either the OS browsers or the other sites.
        profile = QWebEngineProfile(self)
        profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.MemoryHttpCache)
        profile.setPersistentCookiesPolicy(
            QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies)
        self._profiles[site] = profile
        self._browser_cookies[site] = {}
        store = profile.cookieStore()
        store.cookieAdded.connect(lambda cookie, s=site: self._cookie_added(s, cookie))
        store.cookieRemoved.connect(lambda cookie, s=site: self._cookie_removed(s, cookie))
        for item in self.cookies_for(site):
            cookie = QNetworkCookie(item["name"].encode(), item["value"].encode())
            cookie.setDomain(item["domain"])
            cookie.setPath(item["path"])
            cookie.setSecure(item["secure"])
            cookie.setHttpOnly(item["httpOnly"])
            if item["expires"] is not None:
                cookie.setExpirationDate(QDateTime.fromSecsSinceEpoch(item["expires"]))
            store.setCookie(cookie, QUrl(LOGIN_URLS[site]))
        store.loadAllCookies()
        return profile

    def _cookie_added(self, site: str, cookie: QNetworkCookie) -> None:
        value = {
            "name": bytes(cookie.name()).decode("utf-8", "replace"),
            "value": bytes(cookie.value()).decode("utf-8", "replace"),
            "domain": cookie.domain(), "path": cookie.path() or "/",
            "secure": cookie.isSecure(), "httpOnly": cookie.isHttpOnly(),
            "expires": None if cookie.isSessionCookie()
                       else cookie.expirationDate().toSecsSinceEpoch(),
        }
        clean = _clean_cookie(site, value)
        if clean is not None:
            self._browser_cookies[site][_cookie_key(clean)] = clean

    def _cookie_removed(self, site: str, cookie: QNetworkCookie) -> None:
        key = (cookie.domain(), cookie.path() or "/",
               bytes(cookie.name()).decode("utf-8", "replace"))
        self._browser_cookies.get(site, {}).pop(key, None)

    def show_login(self, site: str, parent=None) -> None:
        self._check_site(site)
        if self._shutting_down:
            return
        current = self._dialogs.get(site)
        if current is not None and shiboken6.isValid(current):
            current.show()
            current.raise_()
            current.activateWindow()
            return
        dialog = _LoginDialog(self, site, parent)
        self._dialogs[site] = dialog
        dialog.finished.connect(lambda _result, s=site: self._dialogs.pop(s, None))
        dialog.show()

    def _import_current(self, site: str, remember: bool) -> bool:
        cookies = [clean for value in self._browser_cookies.get(site, {}).values()
                   if (clean := _clean_cookie(site, value)) is not None]
        if not cookies:
            return False
        self._sessions[site] = copy.deepcopy(cookies)
        if remember:
            self._remembered.add(site)
        else:
            self._remembered.discard(site)
        self.changed.emit(site, self.status(site))
        self._save_remembered()
        return True

    def clear(self, site: str) -> None:
        self._check_site(site)
        current = self._dialogs.get(site)
        if current is not None and shiboken6.isValid(current):
            current.reject()
        self._sessions[site] = []
        self._remembered.discard(site)
        self._browser_cookies.get(site, {}).clear()
        # Drop the complete isolated profile, including web storage, after all
        # its pages have been destroyed synchronously by the login dialog.
        profile = self._profiles.pop(site, None)
        if profile is not None and shiboken6.isValid(profile):
            profile.cookieStore().deleteAllCookies()
            shiboken6.delete(profile)
        self.changed.emit(site, _EMPTY_STATUS)
        self._save_remembered()

    def _load_remembered(self) -> None:
        path = _storage_path()
        if not path.exists():
            return
        try:
            if path.stat().st_size > 8 * 1024 * 1024:
                raise ValueError("会话文件过大")
            payload = json.loads(_dpapi(path.read_bytes(), decrypt=True))
            if payload.get("version") != 1 or not isinstance(payload.get("sites"), dict):
                raise ValueError("会话文件格式不受支持")
            for site, values in payload["sites"].items():
                if site not in SITES or not isinstance(values, list):
                    continue
                cookies = [clean for value in values
                           if (clean := _clean_cookie(site, value)) is not None]
                if cookies:
                    self._sessions[site] = cookies
                    self._remembered.add(site)
        except (OSError, ValueError, TypeError, AttributeError):
            self.last_error = "无法读取已保存会话，请重新登录；原文件未修改。"

    def _save_remembered(self) -> None:
        path = _storage_path()
        saved = {site: self.cookies_for(site) for site in self._remembered
                 if self.cookies_for(site)}
        if not saved:
            path.unlink(missing_ok=True)
            self.last_error = ""
            return
        data = json.dumps({"version": 1, "sites": saved}, ensure_ascii=False).encode()
        encrypted = _dpapi(data)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix="session-",
                                             suffix=".tmp", delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(encrypted)
            os.replace(temporary, path)
            self.last_error = ""
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def shutdown(self, *_args) -> None:
        if self._shutting_down:
            return
        self._shutting_down = True
        for dialog in list(self._dialogs.values()):
            if shiboken6.isValid(dialog):
                dialog.reject()
        self._dialogs.clear()
        for profile in self._profiles.values():
            if shiboken6.isValid(profile):
                shiboken6.delete(profile)
        self._profiles.clear()
        self._browser_cookies.clear()
        self._sessions = {site: [] for site in SITES}
        if self.clear_on_exit:
            self._remembered.clear()
            try:
                self._save_remembered()
            except OSError:
                self.last_error = "退出时未能删除已保存会话，请检查文件夹权限。"


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


class AccountsDialog(QDialog):
    def __init__(self, manager: AccountManager, parent=None):
        super().__init__(parent)
        self.setWindowTitle("账号与官方登录会话")
        self.resize(850, 450)
        self._manager = manager
        layout = QVBoxLayout(self)
        explanation = QLabel("每个站点使用独立的官方网页登录窗口，支持网站原生扫码、短信和验证码。\n"
                             "登录后请手动点击“使用当前会话”。导入会话不代表已验证会员身份或高清视频权限。")
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        self._table = QTableWidget(len(SITES), 4, self)
        self._table.setHorizontalHeaderLabels(["站点", "会话状态", "官方登录", "清除"])
        self._table.verticalHeader().hide()
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for row, site in enumerate(SITES):
            self._table.setItem(row, 0, QTableWidgetItem(site))
            self._table.setItem(row, 1, QTableWidgetItem(manager.status(site)))
            login = QPushButton("打开官方登录")
            login.clicked.connect(lambda _checked=False, s=site: manager.show_login(s, self))
            self._table.setCellWidget(row, 2, login)
            clear = QPushButton("清除会话")
            clear.clicked.connect(lambda _checked=False, s=site: self._clear(s))
            self._table.setCellWidget(row, 3, clear)
        layout.addWidget(self._table, 1)
        self._clear_on_exit = QCheckBox("退出程序时清除全部会话（包括“记住登录”保存的会话）")
        self._clear_on_exit.setChecked(manager.clear_on_exit)
        self._clear_on_exit.toggled.connect(lambda checked: setattr(manager, "clear_on_exit", checked))
        layout.addWidget(self._clear_on_exit)
        note = QLabel("未勾选“记住登录”的会话仅保留在内存中；本程序不会读取系统浏览器的登录数据。")
        note.setWordWrap(True)
        layout.addWidget(note)
        if manager.last_error:
            warning = QLabel(manager.last_error)
            warning.setWordWrap(True)
            layout.addWidget(warning)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        manager.changed.connect(self._changed)

    def _changed(self, site: str, status: str) -> None:
        if site in SITES:
            self._table.item(SITES.index(site), 1).setText(status)

    def _clear(self, site: str) -> None:
        try:
            self._manager.clear(site)
        except OSError:
            QMessageBox.warning(self, "加密存储未更新",
                                "本次运行的会话已清除，但未能清除已保存文件。请检查文件夹权限。")

    def done(self, result: int) -> None:
        # Login windows are children of this management dialog. Explicitly close
        # them while their pages and profiles still have a valid owner.
        for dialog in list(self._manager._dialogs.values()):
            if shiboken6.isValid(dialog) and dialog.parent() is self:
                dialog.reject()
        super().done(result)
