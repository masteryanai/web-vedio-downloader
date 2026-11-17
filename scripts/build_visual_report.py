"""Assemble offline, unmodified real-window screenshot evidence."""
from pathlib import Path
import html
import shutil

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'docs/design/steam-review'
OUT.mkdir(parents=True, exist_ok=True)
panels = [('library', '影片库', 'library'), ('details', '季详情', 'details'),
          ('downloads', '下载任务', 'downloads'), ('storage', '存储空间', 'storage'),
          ('add', '添加影片', None), ('settings', '设置', None),
          ('small', '小窗口', None)]
sections = []
for name, title, ref in panels:
    images = []
    if ref:
        images.append(('Steam 参照（用户提供）', '../steam-reference/' + ref + '.png'))
    for stage, label, folder in [('before', '修改前：v0.14.0 真实窗口', 'steam-before'),
                                 ('after', '修改后：真实 Qt 窗口', 'steam-review-final')]:
        source = ROOT / 'scratch' / folder / 'populated' / (name + '.png')
        if source.exists():
            dest = stage + '-' + name + '.png'
            shutil.copyfile(source, OUT / dest)
            images.append((label, dest))
    sections.append('<section id="'+name+'"><h2>'+title+'</h2><div class="compare">'+''.join(
        '<figure><figcaption>'+html.escape(label)+'</figcaption><a href="'+path+'"><img src="'+path+'" alt="'+html.escape(label)+'"></a></figure>'
        for label, path in images)+'</div></section>')
for folder, name, label in [('steam-review-final/empty', 'library', '空库'),
                           ('steam-review-150/populated', 'small', '150% 缩放，小窗口')]:
    source = ROOT / 'scratch' / folder / (name + '.png')
    if source.exists():
        dest = 'empty-library.png' if label == '空库' else 'dpi150-small.png'
        shutil.copyfile(source, OUT / dest)
        sections.append('<section><h2>'+label+'</h2><a href="'+dest+'"><img class="wide" src="'+dest+'"></a></section>')
doc = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Steam 界面重构 · 真实客户端对照</title><style>
body{background:#171d25;color:#c7d5e0;font:15px/1.7 "Microsoft YaHei UI",sans-serif;margin:0;padding:28px}main{max-width:1800px;margin:auto}h1{color:#fff;margin:0 0 8px}h2{font-size:21px;color:#fff}p{max-width:1100px}a{color:#66c0f4}nav a{display:inline-block;margin-right:20px}section{padding:22px 0;border-top:1px solid #3b424c}.compare{display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:18px}figure{margin:0;background:#242930;padding:12px}figcaption{margin-bottom:10px}img{display:block;width:100%;height:auto}.wide{max-width:1250px}.note{border-left:3px solid #1a9fff;padding:10px 16px;background:#242e39}
</style><main><h1>Steam 界面重构：真实客户端对照</h1>
<p>对应 Issue #27 / v0.15.0。此报告记录模型自检，仍等待用户整体验收，不宣称量化相似度。</p>
<p class="note">全部修改前后截图来自实际 Windows Qt 窗口。参照原图 2560px，常规实现截图 1440px；按相同显示宽度比较结构，点击图片查看原尺寸。演示封面使用参照图裁片，仅用于固定数据布局对比；演示媒体文件不保证可播放，不作为真实下载/解码证明，也不随程序发布。正常影片封面仍来自原站或用户手动选择。</p>
<nav>'''+''.join('<a href="#'+n+'">'+t+'</a>' for n,t,_ in panels)+'''</nav>
<p>重点变化：双层深色导航、可调整影片侧栏、横向陈列架与 2:3 封面、详情横图与动作条、全宽下载列表、分类设置与存储空间。业务状态、已有文件和来源确认规则保留。</p>
'''+''.join(sections)+'''<section><h2>差异与验收边界</h2><p>保留 WVD 品牌和影片语义；不复制 Steam 商店、好友、成就与社区内容。Qt 控件、字体栅格化、窗口尺寸及封面素材会影响细节。当前提供固定的最近入库与全部影片架，不提供 Steam 的任意自定义陈列架编辑。最终以实际 EXE 和用户体验为准。</p><p>验证命令、功能测试结果及 EXE 记录见 <a href="../../releases/v0.15.0-validation.md">验证报告</a>。#27 未经用户明确认可保持打开。</p></section></main></html>'''
(OUT / 'index.html').write_text(doc, encoding='utf-8')
print(OUT / 'index.html')
