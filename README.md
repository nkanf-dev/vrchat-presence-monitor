# VRChat Presence Monitor

把短暂的好友状态，变成可以回看的时间线。

Presence Monitor 常驻在服务器上。用户直接用 VRChat 登录，关掉网页后采集仍会继续；再次打开时会回到自己的数据空间，不需要每次重新验证。

[打开在线实例](https://vrc.kanglives.top)

## 你能看到什么

- 好友与本人当前状态、平台、位置、头像和公开资料
- 可搜索、可分页的完整状态事件历史
- 每日在线时间轴，以及精确到每位好友、每个小时的平均热力图
- 在线时间与游玩世界叠加视图，支持单人时间轴、全员对比和世界筛选
- 世界名称、缩略图、作者、容量、标签和简介
- 近 30 天游玩时间与好友排行
- 用户自己的 JSON 导入、导出和可恢复备份

登录状态分为两层：浏览器会话只负责打开面板，服务器上的 VRChat 会话负责持续采集。退出某台设备不会停止采集；只有明确执行“断开 VRChat”才会停止。

## Docker 部署

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

应用默认只监听 `127.0.0.1:8080`。请在生产环境前置 HTTPS 反向代理；健康检查地址为 `/readyz`。

仓库内置 Cloudflare Tunnel 服务。把 remotely-managed tunnel token 写入 `.secrets/tunnel_token` 后启动：

```bash
docker compose --profile tunnel up -d cloudflared
```

常用配置可以写入 `.env`：

```dotenv
TZ=Asia/Shanghai
SESSION_DAYS=365
HOSTED_COLLECTOR_POLL_SECONDS=180
HOSTED_COLLECTOR_CONCURRENCY=3
```

## 数据与备份

SQLite 主库保存在 Docker volume `presence-monitor_monitor-data`。创建一致性快照不会中断采集：

```bash
docker compose --profile tools run --rm backup
```

快照写入 `./backups`，生成后会自动检查 SQLite 完整性。需要异地备份时可启用仓库内的 Cloudflare R2 Worker 与 `offsite-backup` profile；恢复流程见 [部署说明](docs/deployment.md)。每位用户也可以在面板中独立导入或导出自己的数据。

## 工作方式

FastAPI 负责登录、租户隔离、查询与导入导出；后台采集器为每个 VRChat 账号维护独立会话，以 Pipeline 事件实时更新，并用低频 REST 同步校准。React 前端只读取当前浏览器会话对应的租户数据。

VRChat 密码只用于完成登录请求，不写入数据库。登录成功后保存的是 AES-GCM 加密的 VRChat 会话；部署时生成的 `vrchat_session_key` 是解密所需的唯一密钥，应与数据库备份分开保管。

采集器与展示层通过存储接口解耦。默认由服务器持续采集，也可以让外部采集器通过 `/v1/telemetry` 写入同一数据模型。

## 开发

```bash
python -m pip install --require-hashes -r requirements-dev.txt
python -m unittest discover -s tests

cd web
npm ci
npm run check
npm test
npm run build
```

项目使用 Python 3.11+、FastAPI、SQLite、React、TypeScript 和 Vite，平台无关。

## License

[Apache-2.0](LICENSE)
