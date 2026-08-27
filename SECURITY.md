# Security policy

Security fixes are made on `main`. Report vulnerabilities through GitHub private vulnerability reporting; do not put credentials, session cookies, tunnel tokens, database extracts, or another user's presence data in a public issue.

## Deployment model

- VRChat passwords are used only for the immediate upstream login request and are not stored, logged, queued, backed up, or returned.
- VRChat session cookies are encrypted with AES-GCM. The encryption key is mounted as a Docker secret and is not included in database or R2 backups.
- Browser sessions use random `Secure`, `HttpOnly`, `SameSite=Strict` cookies. The database stores only their hashes.
- Every request derives its tenant from the authenticated server-side session. Browser input cannot select a tenant.
- Raw retention stores upstream response bodies, never request credentials or Cookie headers.
- The public service listens on loopback and is exposed through an HTTPS reverse proxy or Cloudflare Tunnel.

Keep `.secrets/` private, rotate credentials after suspected disclosure, apply host and dependency updates, and verify both local and offsite backups. Never use production presence data as a test fixture.

## Build integrity

Python dependencies are version- and hash-locked, npm uses its lockfile, container inputs are pinned, GitHub Actions performs secret scanning and CodeQL analysis, and releases are built from `main`.
