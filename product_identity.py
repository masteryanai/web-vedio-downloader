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
        if set(value)-{'product','edition'} or 'product' not in value:raise RuntimeError('产品身份文件无效，请使用完整发布包。')
        name=value['product']
        edition=value.get('edition','full' if name=='manager' else 'base')
    else:
        name=os.environ.get('WVD_PRODUCT','manager');edition=os.environ.get('WVD_EDITION','full' if name=='manager' else 'base')
    if name not in PRODUCTS:raise RuntimeError('无法识别产品身份，未读取用户资料。')
    if edition not in ('base','full') or name=='manager' and edition!='full':raise RuntimeError('产品组件版本无效。')
    if getattr(sys,'frozen',False) and Path(sys.executable).name.casefold()!=(PRODUCTS[name][0]+'.exe').casefold():raise RuntimeError('程序与产品文件不匹配，请分别解压到独立目录。')
    return name,edition

PRODUCT,EDITION=selected()
PRODUCT_ID,APP_NAME,UPDATE_MANIFEST,HISTORY_MANIFEST=PRODUCTS[PRODUCT]
IS_LITE=PRODUCT=='lite'
EXECUTABLE=PRODUCT_ID+'.exe'
if IS_LITE and EDITION=='full':UPDATE_MANIFEST='update-lite-full.json';HISTORY_MANIFEST='history-lite-full.json'

def package_name(version):return f'{PRODUCT_ID}-v{version}'+('-full' if IS_LITE and EDITION=='full' else '')+'-windows-x64.zip'

def inventory_name(version):return 'inventory-'+('lite-full-' if IS_LITE and EDITION=='full' else 'lite-' if IS_LITE else '')+f'v{version}.json'

def render_script(text):
    """Keep the manager's proven installer template, with a separate product boundary."""
    if not IS_LITE:return text
    return text.replace('WebVideoDownloader',PRODUCT_ID).replace('update.json',UPDATE_MANIFEST).replace('history.json',HISTORY_MANIFEST).replace('.Substring(19)',f'.Substring({len(PRODUCT_ID)+1})')
