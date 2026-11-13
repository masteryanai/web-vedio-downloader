# Web Video Downloader v0.2.1

## 无需认证的在线升级

- 仓库已公开，检查更新和下载更新包均直接匿名访问。
- 移除更新窗口中的 GitHub 授权入口及令牌读取流程，无需安装 GitHub CLI。
- 升级清单和完整包使用公开 Release 附件地址。
- 无正式版本、网络错误和 GitHub 请求限流时给出明确提示。
- 保留大小与 SHA-256 校验、旧版备份、失败回退及用户文件保留。

## 升级方式

v0.2.0 用户可直接在“检查更新”中升级；未保存失效授权时无需先点击授权。若旧版因已保存的失效令牌报错，可先清除其 GitHub 授权，或手动下载此版。

0.1.x 用户需手动下载 ZIP，解压完整目录后运行 `WebVideoDownloader.exe`。

## 附件

- `WebVideoDownloader-v0.2.1-windows-x64.zip`：完整 Windows x64 便携版。
- `SHA256SUMS.txt`：下载包 SHA-256。
- `update.json`：在线升级清单。

修复 #5。
