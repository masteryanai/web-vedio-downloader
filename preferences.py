"""Validated user preferences."""
import json
from runtime import app_data_dir


def parallel_limit():
    try:
        value = json.loads((app_data_dir()/'settings.json').read_text(encoding='utf-8'))['parallel_downloads']
        return value if type(value) is int and 1 <= value <= 10 else 5
    except (OSError, ValueError, KeyError, TypeError):
        return 5


def save_limit(value):
    if type(value) is not int or not 1 <= value <= 10:
        raise ValueError('并行下载数须为 1～10。')
    folder = app_data_dir(); folder.mkdir(parents=True, exist_ok=True)
    target = folder/'settings.json'; temporary = folder/'settings.tmp'
    temporary.write_text(json.dumps({'parallel_downloads':value}), encoding='utf-8')
    temporary.replace(target)
