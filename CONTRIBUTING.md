# Contributing

Small, focused changes are welcome. This project values privacy, observable failure modes, and migrations that preserve append-only history over novelty for its own sake.

## Before changing code

Open an issue for a new storage backend, authentication model, destructive migration, VRChat API behavior change, or feature that expands what data is collected. Bug fixes and contained accessibility improvements can go straight to a pull request.

Never commit a real database, Cookie, raw response, friend list, access code, collector token, tunnel credential, personal hostname, email address, or screenshot that identifies another player.

## Setup

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements-dev.txt
npm ci --prefix web --ignore-scripts --no-audit --no-fund
```

Run the same checks as CI before opening a pull request:

```bash
npm --prefix web run check
npm --prefix web test
npm --prefix web run build
.venv/bin/python -m unittest discover -s tests -v
docker compose config --quiet
```

Python dependency inputs live in `requirements.in` and `requirements-dev.in`. Regenerate both hash-locked outputs with the recorded uv version and a universal Python 3.11 resolution; do not hand-edit package or hash lines:

```bash
uv pip compile requirements.in --generate-hashes --universal --upgrade \
  --python-version 3.11 --output-file requirements.txt
uv pip compile requirements-dev.in --generate-hashes --universal --upgrade \
  --python-version 3.11 --output-file requirements-dev.txt
```

## Changes and commits

- Keep the Local monitor, Hosted API, web client, bridge and deployment layer independently testable.
- Add a regression test before or with a bug fix.
- Preserve tenant scoping at the storage boundary; never trust a tenant ID from request JSON.
- Imports must merge and deduplicate. A routine import must never delete newer data.
- Respect `Retry-After`, bound retries and add jitter around external calls.
- Use semantic HTML, keyboard-operable controls, visible focus, 44 px touch targets and reduced-motion fallbacks.

Commit messages follow Conventional Commits, for example:

```text
fix: keep resolved world metadata after refresh
feat: add tenant-scoped telemetry export
docs: clarify hosted credential boundary
```

Use `!` and a `BREAKING CHANGE:` footer only when an operator or user must take explicit migration action.

## Maintainer releases

Run the `Release` workflow with `publish=false` first when reviewing a new version. An actual publish runs the complete candidate gate again before the write-capable job starts. The version must be strict SemVer without build metadata; a suffix such as `-rc.1` requires `prerelease=true`.

Published Git and GHCR version tags are reservations, not pointers that can be corrected later. Never delete, force-update, or reuse one. If a publish stops after reserving its tag, diagnose the failed step and use the next version rather than weakening the immutability check. The workflow intentionally does not publish `latest`.
