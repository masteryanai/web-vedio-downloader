"""Read the post response produced by the official page in the app's own session."""
import json
from PySide6.QtCore import QTimer, QUrl, Signal
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineScript
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton

CAPTURE = r"""+(() => {
 if (!/(^|\.)(douyin\.com|iesdouyin\.com)$/.test(location.hostname)) return;
 window.__wv_post = null;
 const accept = data => {
   const id = (location.href.match(/\/(?:video|note|slides)\/(\d+)/)||[])[1] || new URL(location.href).searchParams.get('modal_id');
   const walk = (v, depth=0) => {
     if (!v || typeof v !== 'object' || depth > 10) return;
     if (String(v.aweme_id||'') === id && (v.images || v.video || v.image_post_info)) {
       window.__wv_post = {post:v, url:location.href}; return;
     }
     for (const key of Object.keys(v).slice(0,100)) walk(v[key], depth+1);
   }; walk(data);
 };
 window.__wv_accept = accept;
 const wanted = url => /aweme\/.*(?:detail|iteminfo)|web\/api\/v2\/aweme\/iteminfo/.test(String(url));
 const oldFetch = window.fetch;
 window.fetch = async function(...args) {
   const response = await oldFetch.apply(this,args);
   if (wanted(args[0]?.url || args[0])) response.clone().json().then(accept).catch(()=>{});
   return response;
 };
 const open = XMLHttpRequest.prototype.open;
 XMLHttpRequest.prototype.open = function(method,url,...rest) {
   if (wanted(url)) this.addEventListener('load',()=>{try {accept(this.responseType==='json'?this.response:JSON.parse(this.responseText));} catch(e){}});
   return open.call(this,method,url,...rest);
 };
})();
""".replace('\\+', '')

POLL = r"""
(() => {
 if (window.__wv_accept) {
   for (const value of [window._ROUTER_DATA, window.__INITIAL_STATE__]) {
     try {window.__wv_accept(value);} catch(e){}
   }
   const node = document.getElementById('RENDER_DATA');
   if (node) try {window.__wv_accept(JSON.parse(decodeURIComponent(node.textContent)));} catch(e){}
 }
 return window.__wv_post ? JSON.stringify(window.__wv_post) : '';
})();
"""


class DouyinCapture(QDialog):
    captured = Signal(object)
    def __init__(self, profile, url, parent=None):
        super().__init__(parent)
        self.setWindowTitle('抖音 · 读取分享作品')
        self.resize(1040, 780)
        self._done = False
        self._disposed = False
        layout = QVBoxLayout(self)
        note = QLabel('正在读取官方页面的作品信息。若网站要求登录或访问验证，请在此页面手动完成。\n取得信息后会自动返回下载界面；图文可选择原图或带配乐视频。')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.view = QWebEngineView(self)
        self.page = QWebEnginePage(profile, self.view)
        self.view.setPage(self.page)
        script = QWebEngineScript()
        script.setName('capture-current-post')
        script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
        script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        script.setRunsOnSubFrames(False)
        script.setSourceCode(CAPTURE)
        self.page.scripts().insert(script)
        layout.addWidget(self.view, 1)
        cancel = QPushButton('取消解析')
        cancel.clicked.connect(self.reject)
        layout.addWidget(cancel)
        self.timer = QTimer(self)
        self.timer.setInterval(700)
        self.timer.timeout.connect(lambda: self.page.runJavaScript(POLL, self._received))
        self.timer.start()
        self.view.load(QUrl(url))

    def _received(self, value):
        if self._done or not value:
            return
        try:
            payload = json.loads(value)
            from douyin_media import post_info
            info = post_info(payload['post'], payload['url'])
        except Exception:
            return
        self._done = True
        self.timer.stop()
        self.captured.emit(info)
        self.accept()

    def done(self, result):
        if self._disposed:
            return
        self._disposed = True
        self._done = True
        self.timer.stop()
        self.view.stop()
        import shiboken6
        if shiboken6.isValid(self.view):
            shiboken6.delete(self.view)
        super().done(result)
