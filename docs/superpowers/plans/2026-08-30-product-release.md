# Product Release Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Present, independently review, publish, deploy, and verify Presence Monitor v0.3.0-beta.1 without losing sessions or data.

**Architecture:** Treat the public repository and immutable GHCR image as the release authority, while production retains its SQLite volume and file-backed secrets. Real application screenshots and repository-backed badges describe the shipped product. A pre-deploy local/R2 restore drill, post-deploy tenant audit, and independent deployed UX review make the release observable and reversible.

**Tech Stack:** Markdown, GitHub Actions, GitHub CLI, GHCR, Docker Compose, SQLite, Cloudflare Tunnel, R2 backup, SSH

**Execution context:** Execute after all implementation plans pass. Work directly on the designer-authorized main branch and deploy to nkanf@8.163.100.148:/home/nkanf/services/vrchat-presence-monitor at https://vrc.kanglives.top.

---

### Task 1: Configurable immutable runtime image

**Files:**
- Modify: docker-compose.yml
- Modify: docs/deployment.md
- Modify: .github/workflows/ci.yml

- [ ] **Step 1: Make every application service use one overridable image**

Change vrchat-monitor, backup, backup-scheduler, and offsite-backup to:

~~~yaml
image: ${PRESENCE_MONITOR_IMAGE:-presence-monitor:local}
~~~

Keep the build section only on vrchat-monitor for local source builds. Production
commands will pull the released digest and use --no-build, so all four services run
the same immutable artifact.

- [ ] **Step 2: Add CI validation for a digest-pinned Compose model**

~~~bash
release_image="ghcr.io/nkanf-dev/vrchat-presence-monitor@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
PRESENCE_MONITOR_IMAGE="$release_image" docker compose --profile tunnel --profile offsite config --images > /tmp/presence-monitor-images.txt
test "$(grep -Fxc "$release_image" /tmp/presence-monitor-images.txt)" -eq 4
rm -f /tmp/presence-monitor-images.txt
~~~

Expected: the four Python application/backup services resolve to the digest while
cloudflared retains its separately pinned image.

- [ ] **Step 3: Document source-build and released-image deployment modes**

Local/self-hosted development continues to use docker compose build. Released
production deployment sets PRESENCE_MONITOR_IMAGE to the GHCR digest, pulls profiles,
and starts with --no-build. Neither mode changes data or secret mounts.

- [ ] **Step 4: Verify and commit the deployment model**

~~~bash
docker compose config --quiet
git add docker-compose.yml docs/deployment.md .github/workflows/ci.yml
git commit -m "build(docker): support immutable release images"
~~~

### Task 2: Product README, real badges, and screenshots

**Files:**
- Modify: README.md
- Modify: docs/architecture.md
- Modify: docs/deployment.md
- Create: docs/assets/presence-monitor-desktop.webp
- Create: docs/assets/presence-monitor-mobile.webp
- Modify: web/package.json
- Modify: web/package-lock.json

- [ ] **Step 1: Capture the shipped application at desktop and mobile widths**

Run the production frontend against a sanitized test tenant and capture one desktop
view at 1440×960 and one mobile view at 390×844. The screenshots must show current
online players, trusted coverage language, the new navigation/search, and no personal
tokens, email addresses, access codes, or private note content. Convert to high-quality
WebP and visually inspect both files.

Expected artifacts:

~~~text
docs/assets/presence-monitor-desktop.webp
docs/assets/presence-monitor-mobile.webp
~~~

- [ ] **Step 2: Rewrite README around the actual product**

The opening must point only to real artifacts:

~~~markdown
# Presence Monitor

[![CI](https://github.com/nkanf-dev/vrchat-presence-monitor/actions/workflows/ci.yml/badge.svg)](https://github.com/nkanf-dev/vrchat-presence-monitor/actions/workflows/ci.yml)
[![CodeQL](https://github.com/nkanf-dev/vrchat-presence-monitor/actions/workflows/codeql.yml/badge.svg)](https://github.com/nkanf-dev/vrchat-presence-monitor/actions/workflows/codeql.yml)
[![Release](https://img.shields.io/github/v/release/nkanf-dev/vrchat-presence-monitor?include_prereleases)](https://github.com/nkanf-dev/vrchat-presence-monitor/releases)
[![License](https://img.shields.io/github/license/nkanf-dev/vrchat-presence-monitor)](LICENSE)
[![Container](https://img.shields.io/badge/GHCR-container-2496ED?logo=docker)](https://github.com/nkanf-dev/vrchat-presence-monitor/pkgs/container/vrchat-presence-monitor)

一次登录，服务器持续记录。Presence Monitor 把 VRChat 好友与世界状态变成可搜索、可解释、可恢复的个人时间线。
~~~

Then present: what users can learn, real screenshots, read-only boundary, Docker quick
start, data ownership/import/export/backup, architecture/limits, and development/release
checks. Preserve the direct VRChat login journey and remove primary-flow bridge/access-
code language. Do not claim uptime, official VRChat affiliation, unsupported encryption,
or platform restrictions that do not exist.

- [ ] **Step 3: Align architecture and deployment docs with v0.3**

Document the observation ledger, background world resolver, streaming v3 backup,
single-node SQLite boundary, PostgreSQL scale-out threshold, and restore-first
deployment. Keep D1 discussion factual and separate from the supported path.

- [ ] **Step 4: Set the frontend version and verify links/assets**

Set package and lockfile versions to 0.3.0-beta.1. Verify every badge, image, and link
resolves and both WebP files decode.

~~~bash
marker_pattern='local bridge|访问码|macOS only|TB''D|TO''DO|coming soon'
rg -n "$marker_pattern" README.md docs || true
git diff --check
~~~

Expected: no primary-journey wording drift or whitespace errors.

- [ ] **Step 5: Commit product presentation**

~~~bash
git add README.md docs/architecture.md docs/deployment.md docs/assets/presence-monitor-desktop.webp docs/assets/presence-monitor-mobile.webp web/package.json web/package-lock.json
git commit -m "docs(readme): present the shipped intelligence product"
~~~

### Task 3: Complete verification and independent UX review

**Files:**
- Modify for verified frontend findings: web/src/App.tsx
- Modify for verified frontend findings: web/src/styles.css
- Modify for verified frontend findings: web/src/components/AppShell.tsx
- Modify for verified frontend findings: web/src/components/PresenceCharts.tsx
- Modify for verified frontend findings: web/src/components/WorldCharts.tsx
- Modify for verified backend findings: server/app.py
- Modify for verified backend findings: server/analytics.py
- Modify for verified backend findings: server/observation.py
- Create: docs/ux/2026-08-30-v0.3.0-beta.1-review.md
- Modify if semantics change: docs/isme/SKMB.md
- Modify if semantics change: docs/isme/decisions/

- [ ] **Step 1: Run the complete local verification matrix once**

~~~bash
verification_dir="$(mktemp -d)"
case "$verification_dir" in
  /tmp/*|/private/tmp/*|/var/folders/*) ;;
  *) exit 1 ;;
esac
cleanup_verification() {
  rm -rf -- "$verification_dir" web/node_modules infra/r2-backup-worker/node_modules
  find server scripts vrchat_monitor tests -type d -name __pycache__ -prune -exec rm -rf -- {} +
}
trap cleanup_verification EXIT
python3 -m venv "$verification_dir/venv"
"$verification_dir/venv/bin/python" -m pip install --require-hashes -r requirements-dev.txt
"$verification_dir/venv/bin/python" -m unittest discover -s tests -v
"$verification_dir/venv/bin/python" -m compileall -q server scripts vrchat_monitor
npm ci --prefix web --ignore-scripts --no-audit --no-fund
npm --prefix web run check
npm --prefix web test
npm --prefix web run build
npm ci --prefix infra/r2-backup-worker --ignore-scripts --no-audit --no-fund
npm --prefix infra/r2-backup-worker run check
npm --prefix infra/r2-backup-worker test
docker compose config --quiet
~~~

Expected: every command exits zero. While fixing one failure, run its focused suite;
rerun the full matrix after all focused fixes are green.

- [ ] **Step 2: Dispatch an independent UX reviewer against the built candidate**

The reviewer must not modify source. Provide the approved design, rendered app, and
three journeys:

~~~text
1. Returning mobile user: see who is online, inspect a chart value, recover from stale data.
2. Friend researcher: deep-link one player, compare overlap/co-presence, inspect names/worlds, edit a note conflict.
3. Power user: command-search a historical name/world ID, filter Hot Worlds, export a complete backup.
~~~

Require evidence at 360, 390, and 430 CSS pixels, desktop keyboard, 200% zoom, reduced
motion, screen-reader structure, and touch gestures. Classify findings P0/P1/P2 and
record reproducible steps plus screenshots in the review file.

- [ ] **Step 3: Fix every P0/P1 finding with focused commits**

For each finding, add or update a regression test, run its focused suite, implement the
fix, rerun the focused suite, and commit with fix(scope). If a finding changes an
approved state/failure decision, update the matching accepted SKMB record before code.
Do not silently narrow scope.

- [ ] **Step 4: Repeat the independent key journeys and finalize the review**

The same reviewer role verifies every closed P0/P1 against the final candidate and
marks each item verified. P2 items may remain only in a future-enhancement section and
cannot contradict the release specification.

- [ ] **Step 5: Commit the verified review artifact**

~~~bash
git add docs/ux/2026-08-30-v0.3.0-beta.1-review.md docs/isme
git commit -m "docs(ux): record independent v0.3 release review"
~~~

### Task 4: Push main and publish the immutable release

**Files:**
- Verify: .github/workflows/ci.yml
- Verify: .github/workflows/codeql.yml
- Verify: .github/workflows/release.yml

- [ ] **Step 1: Audit commit subjects and repository state**

~~~bash
git status --short --branch
python3 scripts/check_commit_messages.py origin/main..HEAD
git log --oneline --decorate origin/main..HEAD
git diff --check origin/main..HEAD
~~~

Expected: clean working tree, only Conventional Commit subjects, and no generated cache files.

- [ ] **Step 2: Push main and wait for CI/CodeQL**

~~~bash
git push origin main
gh run list --branch main --limit 10
~~~

Wait for CI and CodeQL on the pushed SHA. Inspect failed job logs rather than retrying
blindly. Both workflows must succeed before publication.

- [ ] **Step 3: Dispatch the manual immutable release**

~~~bash
gh workflow run release.yml --ref main \
  -f version=0.3.0-beta.1 \
  -f prerelease=true \
  -f publish=true \
  -f confirmation="RELEASE 0.3.0-beta.1"
~~~

Watch the exact run:

~~~bash
release_run_id="$(gh run list --workflow release.yml --branch main --limit 1 --json databaseId --jq '.[0].databaseId')"
test -n "$release_run_id"
gh run watch "$release_run_id" --exit-status
~~~

Expected: annotated tag v0.3.0-beta.1, prerelease, checksummed source archive,
attestation, and immutable GHCR image exist.

- [ ] **Step 4: Record and verify release identity**

~~~bash
gh release view v0.3.0-beta.1 --json tagName,isPrerelease,url,targetCommitish,assets
gh api users/nkanf-dev/packages/container/vrchat-presence-monitor/versions --jq ".[0].metadata.container.tags"
git ls-remote --tags origin v0.3.0-beta.1
~~~

Record the image digest from release notes for production deployment.

### Task 5: Back up, deploy, and audit production

**Files:**
- Production checkout: /home/nkanf/services/vrchat-presence-monitor
- Production data: Docker volume presence-monitor_monitor-data
- Production backups: /home/nkanf/services/vrchat-presence-monitor/backups

- [ ] **Step 1: Capture the pre-deploy production baseline**

~~~bash
ssh -o ConnectTimeout=15 nkanf@8.163.100.148 '
  set -e
  cd /home/nkanf/services/vrchat-presence-monitor
  git status --short --branch
  git rev-parse HEAD
  docker compose ps
  curl --fail --silent http://127.0.0.1:8080/readyz
  docker compose exec -T vrchat-monitor python -c "import sqlite3; db=sqlite3.connect(\"/data/hosted.sqlite3\"); print(\"integrity\",db.execute(\"PRAGMA integrity_check\").fetchone()[0]); [print(t,db.execute(f\"SELECT COUNT(*) FROM {t}\").fetchone()[0]) for t in (\"tenants\",\"friends\",\"status_events\",\"raw_fetches\")]"
'
~~~

Expected: clean checkout, healthy containers/readiness, integrity ok, and recorded counts.

- [ ] **Step 2: Create and restore-verify local and R2 backups**

~~~bash
ssh nkanf@8.163.100.148 '
  set -e
  cd /home/nkanf/services/vrchat-presence-monitor
  docker compose --profile tools run --rm backup
  docker compose exec -T backup-scheduler python -m scripts.backup_hosted --output /backups --health --max-age-seconds 7200
  docker compose exec -T offsite-backup python -m scripts.r2_backup --backup-dir /backups --state-dir /backups --health --max-upload-age-seconds 7200 --max-drill-age-seconds 93600
'
~~~

If the off-site profile is not running, use the configured token and remote URL with
scripts.restore_hosted to download and verify the latest hourly artifact. Never replace
the live database during a drill.

- [ ] **Step 3: Deploy released source without replacing secrets or volumes**

~~~bash
release_image="$(gh release view v0.3.0-beta.1 --json body --jq .body | sed -nE 's/^Container: ([^[:space:]]+)@(sha256:[0-9a-f]{64})$/\1@\2/p' | head -n 1)"
[[ "$release_image" =~ ^ghcr\.io/nkanf-dev/vrchat-presence-monitor@sha256:[0-9a-f]{64}$ ]]
ssh nkanf@8.163.100.148 "RELEASE_IMAGE=$release_image bash -s" <<'REMOTE'
  set -e
  cd /home/nkanf/services/vrchat-presence-monitor
  git fetch --tags origin
  git switch main
  git pull --ff-only origin main
  test "$(git rev-parse HEAD)" = "$(git rev-list -n 1 v0.3.0-beta.1)"
  PRESENCE_MONITOR_IMAGE="$RELEASE_IMAGE" docker compose --profile tunnel --profile offsite pull
  PRESENCE_MONITOR_IMAGE="$RELEASE_IMAGE" docker compose --profile tunnel --profile offsite up -d --no-build --remove-orphans
  docker compose ps
REMOTE
~~~

The local command extracts and validates the exact digest recorded by the successful
GitHub release before passing the immutable reference to the server. The existing
.secrets files, named data volume, backup directory, tunnel token, and session
encryption key remain untouched.

- [ ] **Step 4: Verify migration, freshness, backup, tunnel, and 429 behavior**

~~~bash
ssh nkanf@8.163.100.148 '
  set -e
  cd /home/nkanf/services/vrchat-presence-monitor
  curl --fail --silent http://127.0.0.1:8080/readyz
  docker compose ps
  docker compose logs --since=20m vrchat-monitor cloudflared | tail -n 400
  docker compose exec -T vrchat-monitor python -c "import sqlite3; db=sqlite3.connect(\"/data/hosted.sqlite3\"); print(\"integrity\",db.execute(\"PRAGMA integrity_check\").fetchone()[0]); [print(t,db.execute(f\"SELECT COUNT(*) FROM {t}\").fetchone()[0]) for t in (\"tenants\",\"friends\",\"status_events\",\"raw_fetches\",\"collection_samples\")]; print(\"accounts\",db.execute(\"SELECT display_name,state,last_sync,last_error FROM vrchat_accounts ORDER BY display_name\").fetchall())"
'
curl --fail --silent --show-error https://vrc.kanglives.top/readyz
~~~

Expected: schema and integrity are healthy, pre-existing counts never decrease,
nkanf and gerasilence resume minute-level collection, the imported legacy tenant stays
readable, no repeated 429 loop appears, and the tunnel is healthy.

- [ ] **Step 5: Run deployed read-only UX verification**

Repeat session reuse, current-online homepage, search, player deep link, touch chart
inspection, discovery filters, and full export against the public domain. Do not
disconnect any account. Confirm refresh and a second device reuse the same tenant
without stopping collection.

- [ ] **Step 6: Roll back only if a release gate fails**

If runtime health fails, stop the candidate containers, check out the previous release,
and start the prior immutable source. Keep the current database unless the migration
made incompatible writes. If restore is required, restore into a separate file, verify
integrity/schema/counts, stop writers, and atomically replace only after verification.
Never delete production history to make startup pass.
