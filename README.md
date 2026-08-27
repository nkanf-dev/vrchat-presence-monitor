# Presence Monitor for VRChat

[![CI](https://github.com/nkanf-dev/vrchat-presence-monitor/actions/workflows/ci.yml/badge.svg)](https://github.com/nkanf-dev/vrchat-presence-monitor/actions/workflows/ci.yml)

把好友状态变成一段可以回看的时间，而不是一张转瞬即逝的在线列表。

Presence Monitor 会在本机记录 VRChat 好友与自己的状态、位置和平台变化，并提供每日时间轴、好友时段热力图、世界停留记录、完整历史搜索以及数据备份。需要从外网查看时，可以把标准化后的 telemetry 发往隔离的 Hosted 查看器；Hosted 不接收 VRChat 密码或登录 Cookie。

> [!IMPORTANT]
> 这是社区维护的非官方项目，与 VRChat Inc. 没有关联。VRChat API 可能变化；请遵守平台规则，并尊重好友的隐私与知情权。

## 你能看到什么

- 好友与自己当前的在线、离线、可加入、先询问、忙碌等状态
- 搜索、筛选和分页后的完整状态历史，而不是固定数量的最近记录
- 每日好友在线时间轴，以及按独立日期范围计算的“好友 × 时段”热力图
- 在线时间与游玩世界叠加时间轴、世界筛选、缩略图和公开世界资料
- 玩家头像、简介、公开链接、累计记录时长与最近变化
- append-only 原始 API 响应、本地 v2 JSON.gz/CSV 导出，以及冲突即回滚的 merge-only 数据导入
- 适配手机的导航、表格、弹窗、触控滚动和可恢复页面锚点

## 两种运行方式

| | Local monitor | Hosted viewer |
| --- | --- | --- |
| 用途 | 在已有登录设备上采集并查看 | 多租户远程查看、备份与协作 |
| VRChat 凭据 | 会话保存在 macOS Keychain；密码不落盘 | 不接收密码、Cookie 或 VRChat token |
| 数据 | SQLite、raw fetch 和日志留在本机 | 只保存 bridge 上传的标准化好友和事件 |
| 网络 | 默认仅监听 `127.0.0.1:8842` | FastAPI + HttpOnly Cookie，可置于 Tunnel 后 |
| 状态 | 日常可用 | 小规模自托管预览；当前为单节点 SQLite |

Hosted 与采集器刻意解耦。电脑关机或睡眠时，本地采集自然会停止；Hosted 不会冒充玩家继续调用 VRChat API。若你的使用场景要求全天候采集，请在你控制、且玩家明确授权的常开设备上运行 Local monitor/bridge。

## 本机使用

要求 macOS 和 Python 3.11 或更新版本。

```bash
git clone https://github.com/nkanf-dev/vrchat-presence-monitor.git
cd vrchat-presence-monitor
./start-vrchat-monitor.command
```

打开 <http://127.0.0.1:8842/>，完成 VRChat 登录与 2FA。账号密码只用于当次认证；成功后仅会话 Cookie 进入 macOS Keychain。数据库、raw fetch 和日志位于 `~/.picoworks-vrchat-monitor/`，均已被 Git 忽略。

Local API 只面向当前电脑上的受信任用户，不提供远程认证。不要把 `8842` 端口、Local UI 或它的 API 直接暴露到局域网、Tunnel、反向代理或公网；远程访问必须使用下方带租户认证的 Hosted viewer。

需要随 macOS 登录启动时运行：

```bash
./install-vrchat-monitor.command
```

如果希望插电时保持采集、使用电池时仍允许休眠：

```bash
./keep-vrchat-awake-when-plugged.command
```

## 用 Docker 部署 Hosted

Hosted 适合一台小型 Linux 服务器。它只把端口绑定到 loopback；Cloudflare Tunnel 从 Docker 网络连接应用，不需要开放 8080 入站端口。

```bash
git clone https://github.com/nkanf-dev/vrchat-presence-monitor.git
cd vrchat-presence-monitor
install -d -m 700 .secrets
sudo install -d -o 10001 -g 10001 -m 700 backups
openssl rand -hex 32 > .secrets/bootstrap_token
operator_gid="$(id -g)"
sudo chown "10001:${operator_gid}" .secrets/bootstrap_token
sudo chmod 0440 .secrets/bootstrap_token
docker compose up -d --build vrchat-monitor backup-scheduler
curl --fail http://127.0.0.1:8080/readyz
```

创建第一个隔离空间：

```bash
curl --fail --silent --show-error \
  -H "X-Bootstrap-Token: $(cat .secrets/bootstrap_token)" \
  -H 'Content-Type: application/json' \
  --data '{"tenant_name":"我的监控","collector_name":"我的 bridge"}' \
  http://127.0.0.1:8080/v1/bootstrap
```

响应里的 `access_code` 给浏览器用户登录，`collector_token` 只给对应 bridge；两者都应当视为秘密。服务端只保存它们的 SHA-256 摘要。

### 连接现有 Local monitor

把 collector token 存到采集设备上的 `0600` 文件，然后先执行一次同步确认连接：

```bash
install -d -m 700 ~/.presence-monitor
printf '%s' '替换为 collector token' > ~/.presence-monitor/collector-token
chmod 600 ~/.presence-monitor/collector-token
python3 scripts/publish_telemetry.py \
  --remote-url https://presence.example.com \
  --token-file ~/.presence-monitor/collector-token
```

bridge 从本机的 `/api/state` 与 ID 顺序历史接口读取标准化数据，以“数据库行号 + 稳定事件 ID”双重断点续传；恢复旧 SQLite 快照后会安全重放，而不会永久跳过复用的行号。旧版 Local monitor 会自动回退到分页历史接口。遇到 429/5xx 时按 `Retry-After` 或指数退避重试。bridge 不会读取、上传或持有 VRChat Cookie。

上面的命令是一次性检查。macOS 日常使用应交给 `launchd`，每分钟增量同步并在登录后自动恢复；token 仍只保存在原来的 `0600` 文件中：

```bash
PRESENCE_REMOTE_URL=https://presence.example.com \
PRESENCE_COLLECTOR_TOKEN_FILE="$HOME/.presence-monitor/collector-token" \
  ./install-vrchat-monitor.command
```

安装器会同时守护 Local monitor 和 bridge。运行日志、断点与错误分别保存在 `~/.presence-monitor/bridge.log`、`bridge-state.json` 和 `bridge-error.log`；不会把 token 写入 plist 或日志。电脑睡眠时不会伪造采集，唤醒后会从稳定事件 ID 继续补传。

### Cloudflare Tunnel

推荐在 Cloudflare Dashboard 创建 remotely-managed tunnel，把公开主机名指向 `http://vrchat-monitor:8080`。将该 tunnel 的专用 token 写入服务器：

```bash
printf '%s' '替换为 tunnel token' > .secrets/tunnel_token
operator_gid="$(id -g)"
sudo chown "65532:${operator_gid}" .secrets/tunnel_token
sudo chmod 0440 .secrets/tunnel_token
docker compose --profile tunnel up -d cloudflared
```

容器使用 `TUNNEL_TOKEN_FILE`，不会挂载账号级 `cert.pem`。任何拥有 tunnel token 的人都可以运行该 tunnel，因此不要把 `.secrets/` 复制进镜像或提交到 Git。

### 备份

Local 下载使用 v2 `JSON.gz`：每条状态、同步与 raw 响应都有独立稳定 ID，数据库副本可以安全合并；相同 ID 的内容若不一致，整次导入会回滚。新版仍可读取 v1，旧版会拒绝 v2，而不会按旧去重规则静默吞记录。

Hosted 在浏览器 Worker 中流式检查备份，并在 JSON 解析器看到大字符串之前剥离 raw 响应；上传内容只有玩家与状态历史。服务器再次流式解析，先限制嵌套深度、对象数量与物化内存，再进入原子导入事务。页面从认证后的 `/v1/capabilities` 读取实例真实限制，不会自行猜测容量。默认请求上限 `MAX_IMPORT_BYTES` 为 32 MiB，最大可配置为 64 MiB；服务器 gzip 解压上限 `MAX_IMPORT_EXPANDED_BYTES` 固定不超过 64 MiB，以适配默认的 512 MiB 容器内存预算。浏览器本地源文件解压上限 `MAX_SOURCE_EXPANDED_BYTES` 默认 256 MiB、最高 512 MiB。这样可以在不把 150+ MiB raw 响应送进服务器内存的前提下迁移完整的 Local 备份。

新写入会在可移植备份超过容量前整批回滚。升级前已经超限的租户仍可启动和读取，且不会因为无增长的重放再次失败；网页会优先导出可回导的 JSON，必要时改用确定性 JSON.gz。若压缩后仍无法满足同实例的请求/解压限制，接口会明确返回 503，并要求使用部署者持有的 SQLite/R2 恢复制品，而不是生成一份看似成功、实际无法恢复的下载。

网页中的“数据”页提供按租户 JSON 导入/导出。`backup-scheduler` 会在启动时及每小时生成一次经过恢复校验的 SQLite gzip 制品，本机保留最近 48 份；也可以随时手工生成：

```bash
docker compose --profile tools run --rm backup
```

每份制品包含 manifest、压缩前后两组 SHA-256，并通过 SQLite `integrity_check`；不会直接复制正在写入的 WAL 文件。同机快照仍不足以抵抗服务器丢失。仓库提供一个 append-only R2 gateway，把最大 64 GiB 的制品按 8 MiB 分片写入私有 Cloudflare R2：

```bash
cd infra/r2-backup-worker
npm ci --ignore-scripts --no-audit --no-fund
npx wrangler login
npx wrangler r2 bucket create presence-monitor-backups
npx wrangler r2 bucket lifecycle set presence-monitor-backups --file lifecycle.json
npx wrangler r2 bucket lock set presence-monitor-backups --file bucket-lock.json
openssl rand -hex 32 > ../../.secrets/backup_token
npx wrangler secret put BACKUP_TOKEN < ../../.secrets/backup_token
npx wrangler deploy
cd ../..
operator_gid="$(id -g)"
sudo chown "10001:${operator_gid}" .secrets/backup_token
sudo chmod 0440 .secrets/backup_token

BACKUP_REMOTE_URL=https://你的-worker地址 \
  docker compose --profile offsite up -d backup-scheduler offsite-backup
```

默认部署把异地对象命名空间固定为 `production`，与仓库中的生命周期和 Bucket Lock 规则保持一致。若自行修改实例名，必须先为新前缀配置同等保留与锁定策略，不能只改容器参数。

R2 bucket 不开放公网读取；Worker 只有带备份 token 的上传、精确读取和 latest 查询，没有删除接口。生命周期与 Bucket Lock 共同保证小时/每日/每月制品至少保留 8/93/400 天，保留期内连误操作也不能覆盖或删除对象。`offsite-backup` 每天会真正下载一个对象到临时目录，重新核对双重摘要、schema 和 `integrity_check`；只有 `HEAD` 成功不算恢复演练。恢复命令默认只验证，不会覆盖生产数据库：

```bash
python3 scripts/restore_hosted.py \
  --archive backups/一份备份.sqlite3.gz \
  --manifest backups/对应备份.manifest.json
```

R2 提高的是数据耐久性，不会在 Linux 主机离线时让 API 自动高可用。生产替换仍应先停 API、保留损坏卷、验证新文件，再原子切换。

## 安全边界

- Hosted 浏览器会话使用 `HttpOnly; SameSite=Strict` Cookie，前端 JavaScript 读不到 token。
- 读写均从认证身份推导 `tenant_id`，collector 不能指定或跨越租户。
- 登录有固定窗口限速，429 带 `Retry-After`；敏感 mutation 校验 same-origin。
- 容器以非 root 用户运行，根文件系统只读，删除全部 Linux capabilities，并限制进程数。
- Cloudflare Tunnel 只需出站连接；应用端口仅绑定 `127.0.0.1`。
- Local JSON.gz 备份包含好友资料、历史与 raw API 响应，但不包含密码或会话 Cookie；Hosted 导入会在浏览器内剥离 raw 响应后再上传规范化数据。

完整威胁模型、数据删除和报告方式见 [SECURITY.md](SECURITY.md)。

## 开发与验证

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements-dev.txt
npm ci --prefix web --ignore-scripts --no-audit --no-fund
npm --prefix web run check
npm --prefix web test
npm --prefix web run build
npm ci --prefix infra/r2-backup-worker --ignore-scripts --no-audit --no-fund
npm --prefix infra/r2-backup-worker run check
npm --prefix infra/r2-backup-worker test
.venv/bin/python -m unittest discover -s tests -v
docker compose config --quiet
```

提交使用 [Conventional Commits](https://www.conventionalcommits.org/)；安全与隐私相关变更必须同时包含回归测试。CI 会重新构建前端、执行 Python/React 测试、检查提交格式与 Action 固定，并运行密钥扫描、CodeQL、依赖审查和容器扫描。含有可修复 Critical 漏洞的镜像不能进入发布链。

## 版本与供应链

`Release` 工作流只能从 `main` 手动触发。默认 `publish=false`，只构建、测试、扫描并保留七天候选归档；这个阶段只有仓库读取权限。正式发布还需要 `publish=true`、严格 SemVer、与版本后缀一致的 `prerelease` 开关，以及完全匹配的 `RELEASE <version>` 确认文本。

发布过程不会移动或覆盖已有 Git tag、release 或同版本 GHCR 标签，也不生成可变的 `latest` 镜像标签。公开仓库需要在 GitHub 设置中启用 release immutability，由平台锁定已发布的 tag 和附件。

- 源代码归档附带 SHA-256 和 GitHub provenance attestation。
- GHCR 写入版本标签与“commit SHA + 版本”标签。后者避免同一 commit 从预发布晋升为正式版时移动已有 SHA 标签。
- 发布镜像附带 BuildKit provenance、SBOM 和 GitHub attestation；发布前还会把 registry 中的 `linux/amd64` config digest 与已扫描候选逐字比对。
- Dockerfile frontend、基础镜像、Python 包、npm 包和 Actions 都固定到摘要或完整 commit SHA；构建时间戳被归一化。

当前自动发布镜像明确面向 `linux/amd64`。其他架构应从同一 tag 在目标机器本地构建。

下载后可以独立核对归档与容器：

```bash
gh release download v1.0.0 -R nkanf-dev/vrchat-presence-monitor
sha256sum --check presence-monitor-1.0.0.sha256
gh release verify v1.0.0 -R nkanf-dev/vrchat-presence-monitor
gh release verify-asset v1.0.0 presence-monitor-1.0.0.tar.gz \
  -R nkanf-dev/vrchat-presence-monitor
gh attestation verify presence-monitor-1.0.0.tar.gz \
  -R nkanf-dev/vrchat-presence-monitor
gh attestation verify \
  oci://ghcr.io/nkanf-dev/vrchat-presence-monitor:1.0.0 \
  -R nkanf-dev/vrchat-presence-monitor
```

## 存储选择

当前 Hosted 使用 WAL 模式 SQLite，面向单服务器和小型好友组；Cloudflare R2 只保存可恢复的异地制品，不参与在线查询。Cloudflare D1 不是 FastAPI 可直接连接的 SQLite 文件，站外数据路径需要额外 Worker API，因此没有被伪装成主库。需要多副本、高并发或稳定 SLA 时，应先实现并验证 PostgreSQL storage adapter，而不是通过 D1 管理 REST API 执行在线查询。

架构与部署取舍见 [docs/architecture.md](docs/architecture.md) 和 [docs/deployment.md](docs/deployment.md)。

## License

[Apache License 2.0](LICENSE)
