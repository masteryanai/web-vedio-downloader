"""Capture real Qt windows with isolated reference-only demonstration data.

Run from the project root. No account, network or user installation is used.
Steam screenshot crops are comparison fixtures, never shipped application assets.
"""
from pathlib import Path
import argparse
import json
import os
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def prepare(folder, empty=False):
    data = folder / 'data'
    data.mkdir(parents=True, exist_ok=True)
    os.environ['WVD_DATA_DIR'] = str(data)
    os.environ['LOCALAPPDATA'] = str(folder / 'legacy')
    (data / 'settings.json').write_text(json.dumps({
        'auto_check_updates': False, 'close_action': 'exit',
    }), encoding='utf-8')
    if empty:
        return
    from media_library import Catalogue
    from PySide6.QtGui import QImage
    import download_tasks as tasks
    store = Catalogue()
    lib = store.library(str(folder / 'films'), '视觉测试库')
    art = QImage(str(ROOT / 'docs/design/steam-reference/library.png'))
    banner = QImage(str(ROOT / 'docs/design/steam-reference/details.png'))
    covers = data / 'library/covers'
    covers.mkdir(parents=True, exist_ok=True)
    names = ['无尽传说 II', '装甲精英', '自动化 101', '异想侦探社', '战地守望',
             '山河长歌', '铁巢重炮', '维修物语', '影之刃零', '漫长标题测试：山河之间的旅途与重逢']
    for i, title in enumerate(names):
        group = {'site': '视觉演示', 'key': str(i), 'url': 'https://example.invalid/' + str(i),
                 'title': title, 'entries': [{'key': str(j), 'title': f'第 {j:02} 集',
                     'url': f'https://example.invalid/{i}/{j}'} for j in range(1, 13)]}
        wid, eps = store.ingest(group, title, '第一季' if i % 3 else '', lib)
        cover = covers / f'cover-{i}.png'
        # Reference library cards are 220x330 in the supplied 2560px screenshot.
        art.copy(384 + min(i, 8) * 240, 84, 220, 330).save(str(cover))
        big = covers / f'banner-{i}.png'
        banner.copy(360, 79, 2188, 321).save(str(big))
        store.patch_work(wid, cover=str(cover), banner=str(big))
        # A readable existing fixture verifies file availability, not media decoding.
        if i % 2 == 0:
            file = folder / 'films' / f'fixture-{i}.mp4'
            file.write_bytes(b'visual fixture only, not playable media')
            with store.change() as snapshot:
                ep = next(e for e in snapshot['episodes'] if e['id'] == eps[0])
                ep['files'] = [{'path': str(file), 'kind': 'video', 'external': False}]
        info = {'site': '视觉演示', 'title': title, 'url': group['entries'][0]['url'],
                'formats': [{'id': 'test'}]}
        task = tasks.create(info, 'test', str(folder / 'films'))
        task.update(state='completed' if i > 6 else 'paused', download_percent=100 if i > 6 else 30 + i * 7,
                    stage='下载完成' if i > 6 else '已暂停')
        tasks.save(task)
        if task['state'] == 'completed':
            tasks.finish(task)
        store.bind(eps[0], task['id'])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--empty', action='store_true')
    parser.add_argument('--scale', default='1')
    parser.add_argument('--output', type=Path, default=ROOT / 'scratch/steam-review')
    parser.add_argument('--prepare-only', action='store_true')
    args = parser.parse_args()
    os.environ['QT_SCALE_FACTOR'] = args.scale
    folder = args.output.resolve() / ('empty' if args.empty else 'populated')
    folder.mkdir(parents=True, exist_ok=True)
    # Fresh data makes successive captures independent and repeatable.
    fixture = folder / ('fixture-' + uuid.uuid4().hex)
    prepare(fixture, args.empty)
    if args.prepare_only:
        print(fixture / 'data')
        return
    from argparse import Namespace
    from unittest.mock import patch
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    import main as client
    original = QApplication.exec
    failures = []

    def run():
        app = QApplication.instance()
        window = next(w for w in app.topLevelWidgets() if type(w).__name__ == 'Window')
        ws = window.workspace

        def capture():
            try:
                window.resize(1440, 960)
                app.processEvents()
                window.grab().save(str(folder / 'library.png'))
                if not args.empty:
                    data = ws.store.read()
                    ws.show_work(data['works'][0]['id'])
                    app.processEvents()
                    window.grab().save(str(folder / 'details.png'))
                    ws.episode_search.setText('02')
                    app.processEvents()
                    assert ws.episodes.rowCount() == 1
                    ws.episode_search.clear()
                    ws.episode_checked.add(data['episodes'][1]['id'])
                    ws.render_work()
                ws.show_tasks()
                app.processEvents()
                window.grab().save(str(folder / 'downloads.png'))
                ws.home()
                window.resize(1000, 700)
                app.processEvents()
                window.grab().save(str(folder / 'small.png'))
                ws.open_add()
                app.processEvents()
                ws.add_dialog.grab().save(str(folder / 'add.png'))
                ws.add_dialog.hide()
                from library_ui import StorageDialog
                storage = StorageDialog(ws)
                storage.show()
                from PySide6.QtTest import QTest
                for _ in range(100):
                    QTest.qWait(20)
                    if '读取中' not in storage.capacity.text():
                        break
                storage.grab().save(str(folder / 'storage.png'))
                storage.close()
                window.show_settings()
                app.processEvents()
                settings = app.activeModalWidget()
                if settings:
                    settings.grab().save(str(folder / 'settings.png'))
                    settings.reject()
                print('Screenshots:', folder, flush=True)
            except BaseException:
                import traceback
                failures.append(traceback.format_exc())
            finally:
                window.request_exit()

        # Settings uses exec(), so close and capture its actual modal event loop.
        def close_modal():
            modal = app.activeModalWidget()
            if modal and modal.windowTitle().startswith('设置'):
                modal.grab().save(str(folder / 'settings.png'))
                modal.reject()
        timer = QTimer(window)
        timer.timeout.connect(close_modal)
        timer.start(200)
        QTimer.singleShot(1800, capture)
        QTimer.singleShot(20000, window.request_exit)
        return original()

    with patch.object(QApplication, 'exec', side_effect=run):
        client.gui(Namespace(smoke_ui=False, url=None, screenshot=None))
    if failures:
        raise RuntimeError('\n'.join(failures))


if __name__ == '__main__':
    main()


