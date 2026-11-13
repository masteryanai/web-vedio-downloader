# 第三方依赖与来源

本文件记录原型使用的主要第三方程序、运行库和构建工具。各依赖保持其原有许可证，本文不改变其授权范围。发布目录应保留 `licenses` 中的原始许可文本。

## 桌面与媒体运行依赖

| 依赖 | 本次版本 | 许可证或来源 |
| --- | --- | --- |
| Python | 3.14.4 | PSF；原文来源为 Python 安装目录 `LICENSE.txt`。[许可说明](https://www.python.org/psf/license/) |
| PySide6、Shiboken6、Qt / QtWebEngine | PySide6 6.12.0，WebEngine 6.12.0.140 | 本次 wheel 元数据声明 `LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only`。QtWebEngine 还包含 Chromium 等第三方代码，各模块及第三方代码按自身许可保留通知。[许可说明](https://doc.qt.io/qt-6/licensing.html) |
| yt-dlp | 2026.10.26 | Unlicense。[许可原文](https://github.com/yt-dlp/yt-dlp/blob/master/LICENSE) |
| FFmpeg / ffprobe 共享构建 | `n9.0.2-57-gd838f9f9ae-20261112`，Windows x64 LGPL shared | LGPL v3 或后续版本；原文在 `licenses/FFmpeg-LICENSE.txt`。[构建来源](https://github.com/FFmpeg-Builds/FFmpeg-Builds/releases/tag/latest) |
| Node.js | 24.14.1 | MIT 及其第三方通知，保留该版本完整 `LICENSE`。[许可原文](https://github.com/nodejs/node/blob/v24.14.1/LICENSE) |

FFmpeg 使用发布资产 `ffmpeg-n9.0-latest-win64-lgpl-shared-9.0.zip`，SHA-256 为：

```text
b1faadd1ea9c4fe3fb9b059b1f268f57ed111dbebfeda66700c85e12d1bd1c23
```

校验值与 GitHub 资产提供的摘要一致。构建配置启用共享库和 version3，未启用 GPL；运行所需 DLL 与两个可执行文件一同保留。下载元数据在开发目录 `scratch/ffmpeg-download.json`，发布时不依赖该文件。

## 央视格式处理与构建依赖

| 依赖 | 本次版本 | 许可证或来源 |
| --- | --- | --- |
| `xiaoxi-ij478/cctv-h5e-decrypt` | 固定提交 `033b487971673a2dc02cbaf7b16f537a27c2f515` | 项目为 MIT；保留原仓库 `LICENSE`。[源码](https://github.com/xiaoxi-ij478/cctv-h5e-decrypt) |
| `js-crc` | 0.3.1 | MIT；原文为包内 `LICENSE.txt`。[源码](https://github.com/emn178/js-crc) |
| PyInstaller | 6.22.3 | GPL v2 或后续版本，包含 Bootloader Exception；保留其 `COPYING.txt`。[许可说明](https://pyinstaller.org/en/stable/license.html) |
| TypeScript | 6.0.3，仅开发时转译 | Apache-2.0；不作为此原型的运行依赖打包。[许可原文](https://github.com/microsoft/TypeScript/blob/main/LICENSE.txt) |

央视格式处理依赖中的 `external/cctv.worker.js` 是上游仓库适配的外部播放器代码。原型保留其来源及上游文件；此处的 MIT 项目标注不构成对央视播放器代码的单独授权声明。

本次原型没有为网站视频内容授予新的许可。账号会话与用户自行选择下载的内容不属于软件依赖许可证的一部分。

## AGE 浏览器解析依赖

Playwright for Python 1.65.1（Apache-2.0），用于调用本机Chrome/Edge；浏览器本体不随软件分发。运行时复用随包Node.js。Playwright许可原文随licenses目录保存。
