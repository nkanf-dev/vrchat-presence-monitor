# VRChat Presence Monitor

一个常驻服务器的 VRChat 好友状态记录器。用户在网页中登录自己的 VRChat 账号，服务会持续记录好友与本人状态、在线时段、位置和世界信息，并提供搜索、分页、统计和可视化。

## 使用

访问已部署的站点，输入 VRChat 账号与密码；如果账号启用了两步验证，再输入验证码。登录成功后会直接进入自己的面板，关闭浏览器不影响服务器继续采集。

## 部署

需要 Docker Engine 24+ 和 Docker Compose v2。

```bash
git clone https://github.com/nkanf-dev/vrchat-presence-monitor.git
cd vrchat-presence-monitor
mkdir -p .secrets backups
openssl rand -base64 32 | tr -d '\n' > .secrets/vrchat_session_key
openssl rand -base64 32 | tr -d '\n' > .secrets/bootstrap_token
chmod 600 .secrets/*
docker compose up -d --build vrchat-monitor backup-scheduler
```

服务默认只监听 `127.0.0.1:8080`。生产环境应通过 HTTPS 反向代理访问。仓库内置 Cloudflare Tunnel 服务；将 remotely-managed tunnel token 写入 `.secrets/tunnel_token` 后启动：

```bash
docker compose --profile tunnel up -d cloudflared
```

打开 `http://127.0.0.1:8080/readyz` 检查服务状态。

## 数据与备份

主数据位于 Docker volume `presence-monitor_monitor-data`。在线快照不会停止采集：

```bash
docker compose --profile tools run --rm backup
```

备份会写入 `./backups` 并在生成后校验 SQLite 完整性。可选的 Cloudflare R2 异地备份配置见 [部署说明](docs/deployment.md)。用户也可以在面板中导入或导出自己的数据。

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

后端使用 FastAPI 与 SQLite，前端使用 React、TypeScript 和 Vite。采集器与展示层通过租户化存储接口解耦；服务器采集是默认模式，外部采集器仍可通过 `/v1/telemetry` 接入。

## License

[Apache-2.0](LICENSE)
