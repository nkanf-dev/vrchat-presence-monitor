# SKMB-2026-08-30-006: Release and Production Deployment

- status: accepted
- decided_by: designer
- approval_source: user approved the complete release design with “通过，后续无需再审批”
- date: 2026-08-30
- commit: 68721f8
- patterns:
  - B_state_persistence
  - D_external_dependency
  - F_fail_semantics
  - G_irreversible_action
- scope: Git history, CI, immutable release, server deployment, tunnel, rollback

## Decision

Implementation proceeds directly on `main` with Conventional Commits, as explicitly
requested by the designer. Release `v0.3.0-beta.1` is created only from the public
main lineage through the existing immutable workflow. CI, CodeQL, frontend, backend,
container, migration, tenant-isolation, backup, and independent UX gates must pass.

The candidate image digest is recorded. Production preserves its database volume,
session encryption key, tunnel token, and other secrets. Deployment runs the verified
migration, checks tenant freshness and 429 behavior, and verifies the public tunnel.
If migration or runtime health fails, the prior immutable image and verified database
remain available; recovery never deletes history or edits the sole database in place.

The independent reviewer tests the deployed candidate from returning-mobile,
single-player-research, and power-search journeys. Every P0/P1 issue is fixed before
release rather than silently removing the feature.

## Applies To

- Conventional Commit sequence on `main`
- GitHub CI, CodeQL, release, GHCR, and release notes
- production backup, Docker Compose deployment, health verification, and tunnel
- final independent UX review and release-blocker fixes

## Rationale

The user explicitly requested a public, product-grade release and online update while
preserving existing local and production data and sessions.

## Alternatives

- Rewriting production data to fit a failed migration was rejected.
- Deploying a mutable local build instead of the released digest was rejected.
- Clearing P0/P1 findings by reducing the promised feature set was rejected.

## Supersedes

None.

## Superseded By

None.
