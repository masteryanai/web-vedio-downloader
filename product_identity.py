"""Two explicit products; frozen builds take identity only from their own bundle."""
import json
import os
from pathlib import Path
import sys

PRODUCTS = {
    'manager': ('WebVideoDownloader', 'Web Video Downloader', 'update.json', 'history.json'),
    'lite': ('WebVideoDownloaderLite', 'Web Video Downloader Lite', 'update-lite.json', 'history-lite.json'),
}

def selected():
    if getattr(sys,'frozen',False):
        path=Path(sys._MEIPASS)/'product.json'
        value=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'product':'manager'}
        if set(value)!={'product'}:raise RuntimeError('产品身份文件无效，请使用完整发布包。')
        name=value['product']
    else:name=os.environ.get('WVD_PRODUCT','manager')
    if name not in PRODUCTS:raise RuntimeError('无法识别产品身份，未读取用户资料。')
    return name

PRODUCT=selected()
PRODUCT_ID,APP_NAME,UPDATE_MANIFEST,HISTORY_MANIFEST=PRODUCTS[PRODUCT]
IS_LITE=PRODUCT=='lite'
EXECUTABLE=PRODUCT_ID+'.exe'

def render_script(text):
    """Keep the manager's proven installer template, with a separate product boundary."""
    return text.replace('WebVideoDownloader',PRODUCT_ID) if IS_LITE else text
