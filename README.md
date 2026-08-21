# KuKu_bot

私人 Telegram 文件/信息库机器人。

## 功能

- 照片、视频、文件、音频、语音自动登记
- 使用 Telegram file_id 保存文件引用，不把私人文件提交到 GitHub
- `/save` 保存私人文字
- `/list` 查看最近文件
- `/search` 搜索文件名/备注
- `/stats` 统计
- `/delete` 删除索引
- Telegram User ID 白名单
- GitHub Actions 手动运行

## GitHub Secrets

仓库 Settings -> Secrets and variables -> Actions 中添加：

- `BOT_TOKEN`
- `ADMIN_USER_ID`
- `DATABASE_URL`（可选；推荐使用 PostgreSQL 以跨 Actions 运行保存索引）

## 启动

Actions -> KuKu Telegram Bot -> Run workflow。

本工作流不会自动定时运行，只会在你手动 Run workflow 时启动。

## 重要

GitHub Actions runner 是临时环境。不要依赖 runner 工作目录里的 SQLite 保存长期数据。若需要跨次运行保留私人文字和文件索引，请配置 `DATABASE_URL`。

Bot Token 不要提交到仓库，必须放 GitHub Secrets。
