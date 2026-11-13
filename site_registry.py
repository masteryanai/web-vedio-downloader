"""One catalog for adapter routing, supported-site UI and adaptation dates."""
import importlib
import json
from datetime import datetime, timezone, timedelta
from urllib.parse import urlsplit
from runtime import resource_path, app_data_dir


def today():
    return datetime.now(timezone(timedelta(hours=8))).date().isoformat()


def entries():
    catalog = json.loads(resource_path('sites.json').read_text(encoding='utf-8'))
    local = app_data_dir() / 'adapted-sites.json'
    if local.exists():
        try:
            extra = json.loads(local.read_text(encoding='utf-8'))
            known = {domain for row in catalog for domain in row['domains']}
            if isinstance(extra, list):
                catalog += [row for row in extra if isinstance(row, dict) and isinstance(row.get('domains'), list) and not known.intersection(row['domains']) and all(key in row for key in ('name','status','adapted_at'))]
        except (ValueError, OSError):
            pass
    return catalog


def lookup(url):
    host = (urlsplit(url).hostname or '').lower()
    return next((row for row in entries() if any(host == d or host.endswith('.'+d) for d in row['domains'])), None)


def resolve(row):
    module, method = row['handler'].split(':', 1)
    return getattr(importlib.import_module(module), method)


def record_success(url, name):
    if lookup(url):
        return
    host = (urlsplit(url).hostname or '').lower()
    if not host:
        return
    folder = app_data_dir()
    folder.mkdir(parents=True, exist_ok=True)
    file = folder / 'adapted-sites.json'
    try:
        rows = json.loads(file.read_text(encoding='utf-8')) if file.exists() else []
        rows.append({'name':host if name == '其他网站' else name,'domains':[host],
                     'status':'曾成功下载，其他内容需验证','adapted_at':today()})
        temporary = file.with_suffix('.tmp')
        temporary.write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
        temporary.replace(file)
    except (OSError, ValueError):
        pass


if __name__ == '__main__':
    path = resource_path('sites.json')
    rows = json.loads(path.read_text(encoding='utf-8'))
    for row in rows:
        row.setdefault('adapted_at', today())
    path.write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
