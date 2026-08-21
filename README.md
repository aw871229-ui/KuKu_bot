# KuKu_bot

私人 Telegram 文件/信息库 + 点位/汇率计算 + HTX USDT 行情助手。

## 外部界面

- 📐 点位与汇率自动计算
- 发送 `7 8.5` 自动判断为点位计算
- 发送 `17 7` 自动判断为汇率反算
- 支持 `实时 7 交易 8.5`、`点位 17 实时 7`
- 💵 HTX USDT/CNY 参考行情与 P2P 报价模块

## 隐藏私人入口

私人文件库不会显示在 `/start` 的公开菜单里。

- 只有 `ADMIN_USER_ID` 能进入
- 发送自定义的 4 位数字 `HIDDEN_PIN` 后解锁
- 默认解锁 30 分钟，可用 `HIDDEN_SESSION_MINUTES` 调整
- 解锁后才可保存/查看照片、视频、文件、音频和私人文字
- `/lock` 可以立即锁定

## 私人资料功能

- 照片、视频、文件、音频、语音自动登记
- 使用 Telegram `file_id` 保存文件引用，不把私人文件提交到 GitHub
- `/save` 保存私人文字
- `/list` 查看最近文件
- `/search` 搜索文件名/备注
- `/stats` 统计
- `/delete` 删除索引

## GitHub Secrets

仓库 Settings -> Secrets and variables -> Actions 中添加：

- `BOT_TOKEN`
- `ADMIN_USER_ID`
- `HIDDEN_PIN`（必须正好 4 位数字，例如 4827）
- `HIDDEN_SESSION_MINUTES`（可选，例如 30）
- `DATABASE_URL`（可选；推荐使用 PostgreSQL 以跨 Actions 运行保存索引）

## 启动

Actions -> KuKu Telegram Bot -> Run workflow。

本工作流不会自动定时运行，只会在你手动 Run workflow 时启动。

## 数据说明

GitHub Actions runner 是临时环境。不要依赖 runner 工作目录里的 SQLite 保存长期数据。若需要跨次运行保留私人文字和文件索引，请配置 `DATABASE_URL`。

Bot Token 和私人入口密码不要提交到仓库，必须放 GitHub Secrets。

HTX 模块使用公开市场数据；P2P 商家报价可能随平台在线状态变化，获取失败时仍会显示 HTX 官方参考价。仅作行情参考，不代表交易建议。
