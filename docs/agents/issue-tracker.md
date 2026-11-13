# Issue tracker: GitHub

目标仓库：masteryanai/web-vedio-downloader。
需求、缺陷和规格存放于 GitHub Issues，使用 gh CLI 操作。

- 写入前通过 gh repo view 确认目标仓库。
- 创建：gh issue create --title "..." --body-file <文件>
- 读取：gh issue view <编号> --json number,title,body,labels,comments
- 列表：gh issue list --state open
- 多行正文和评论使用 UTF-8 文件及 --body-file。
- 子任务优先使用 GitHub 原生子 Issue；不可用时标记 Part of #编号。
- 阻塞关系优先使用原生依赖；不可用时标记 Blocked by: #编号。
- 关闭前发表评论，说明结果或原因。
- 技能要求“发布到问题跟踪器”时，创建本仓库 Issue。
- 技能要求“获取相关工单”时，读取对应 Issue。

## Pull requests as a triage surface

PRs as a request surface: no.
