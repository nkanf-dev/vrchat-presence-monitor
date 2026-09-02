# VRChat Presence Monitor

[![CI](https://github.com/nkanf-dev/vrchat-presence-monitor/actions/workflows/ci.yml/badge.svg)](https://github.com/nkanf-dev/vrchat-presence-monitor/actions/workflows/ci.yml)
[![CodeQL](https://github.com/nkanf-dev/vrchat-presence-monitor/actions/workflows/codeql.yml/badge.svg)](https://github.com/nkanf-dev/vrchat-presence-monitor/actions/workflows/codeql.yml)
[![Release](https://img.shields.io/github/v/release/nkanf-dev/vrchat-presence-monitor?include_prereleases&sort=semver)](https://github.com/nkanf-dev/vrchat-presence-monitor/releases)
[![License](https://img.shields.io/github/license/nkanf-dev/vrchat-presence-monitor)](LICENSE)

把短暂的好友状态，变成随时可以回看的时间线。

Presence Monitor 在服务器上持续记录 VRChat 好友动态。登录一次，关掉网页后记录仍会继续；再次打开时，可以直接查看自己的在线好友、历史状态、活跃时段与去过的世界。

**[打开在线实例](https://vrc.kanglives.top)**

## 主要功能

- **当前在线**：查看好友与本人状态、位置、平台、头像和公开资料
- **玩家洞察**：搜索玩家，查看在线时长、共同在线、一起游玩、常去世界与名称变化
- **每日在线**：按天叠加所有玩家的在线时间轴，并查看每位玩家的小时热力图
- **世界时间轴**：把玩家在线时间与游玩世界放在同一条时间线上，支持筛选与详情弹窗
- **世界发现**：按热度和上升趋势发现好友近期去过的世界，浏览缩略图、作者、容量与简介
- **自定义仪表盘**：拖拽组合指标、排行、趋势、平台分布、采集覆盖率与好友时段热力图；可按玩家、状态、平台、世界和时间范围筛选
- **独立分享页**：发布持续更新的仪表盘链接，可设置访问密码，并在应用内查看匿名化访问记录
- **状态历史**：搜索、筛选和分页浏览全部变化记录
- **数据备份**：独立导入或导出玩家、状态、备注、标签、偏好与采集记录
- **多设备使用**：同一账号可以在不同设备继续使用，服务器会持续维护采集会话

桌面端与移动端使用同一套界面。页面位置、筛选条件和已打开的详情会保留在链接中，刷新后可以继续浏览。

## 自行部署

需要 Docker Engine 24+ 与 Docker Compose v2。

```bash
git clone https://github.com/nkanf-dev/vrchat-presence-monitor.git
cd vrchat-presence-monitor

mkdir -p .secrets backups
openssl rand -base64 32 | tr '+/' '-_' | tr -d '\n' > .secrets/vrchat_session_key
openssl rand -base64 32 | tr -d '\n' > .secrets/bootstrap_token
chmod 600 .secrets/*

docker compose up -d --build vrchat-monitor backup-scheduler
```

应用默认监听 `127.0.0.1:8080`，健康检查地址为 `/readyz`。生产环境请通过 HTTPS 反向代理访问。

仓库也内置了 Cloudflare Tunnel 服务。把 remotely-managed tunnel token 写入 `.secrets/tunnel_token` 后启动：

```bash
docker compose --profile tunnel up -d cloudflared
```

常用设置可以写入 `.env`：

```dotenv
TZ=Asia/Shanghai
SESSION_DAYS=365
HOSTED_COLLECTOR_POLL_SECONDS=180
HOSTED_COLLECTOR_CONCURRENCY=3
```

更完整的部署、迁移与恢复步骤见 [部署说明](docs/deployment.md)。

## 备份与恢复

SQLite 数据库保存在 Docker volume `presence-monitor_monitor-data`。定时备份服务每小时生成快照并检查数据库完整性，也可以随时手动创建：

```bash
docker compose --profile tools run --rm backup
```

快照写入 `./backups`。需要异地副本时，可以启用仓库中的 Cloudflare R2 备份服务。每位用户也能从网页中单独导出或恢复自己的数据。

## 项目结构

```text
server/             FastAPI、多租户会话、采集与查询
web/                React 19、TypeScript、Vite 前端
infra/              Cloudflare R2 备份组件
scripts/            部署、备份与维护工具
tests/              服务端测试
```

服务器为每个 VRChat 账号维护独立会话，通过 Pipeline 实时事件与低频 REST 同步更新记录。展示、采集和存储模块彼此解耦，外部采集器也可以通过 `/v1/telemetry` 接入同一数据模型。

## 本地开发

```bash
python -m pip install --require-hashes -r requirements-dev.txt
python -m unittest discover -s tests

cd web
npm ci
npm run check
npm test
npm run build
```

项目支持 Python 3.11+ 与 Node.js 20.19+，可在常见 Linux、macOS 和 Windows 开发环境中运行。

## License

[Apache-2.0](LICENSE)
