# Security policy

Presence Monitor handles social presence data. Treat a deployment as a private application even when its source code is public.

## Supported versions

Until the first stable release, security fixes are made on `main`. There are no supported release branches yet.

## Report a vulnerability

Use GitHub's private vulnerability reporting for this repository. Do not open a public issue containing credentials, access codes, collector tokens, tunnel tokens, database extracts, raw VRChat responses, usernames, world locations, or proof-of-concept data from another person.

Include the affected commit, deployment mode, reproduction steps, impact, and the smallest redacted evidence that demonstrates the problem. Expect an acknowledgement within seven days. No bounty program is currently offered.

## Credential model

- The Local monitor accepts a VRChat password only for the immediate login request. It stores the resulting session Cookie in macOS Keychain, or a `0600` file on unsupported systems. It never exports that Cookie.
- Hosted does not expose a VRChat login endpoint and does not accept VRChat passwords, Cookies, auth tokens, or encrypted session envelopes.
- A Hosted browser receives an opaque session in an `HttpOnly; SameSite=Strict` Cookie. Only a hash is stored server-side.
- A collector token is scoped to one tenant and can only submit telemetry. An access code can create browser sessions for one tenant. The bootstrap token can create tenants and has deployment-wide authority.
- A Cloudflare tunnel token can run one tunnel. It is deliberately kept separate from an account-wide `cert.pem`.
- The R2 backup token can append and read private backup objects through one Worker; it cannot delete objects. It is separate from the tunnel and bootstrap tokens and is never sent to a browser.

Encryption cannot hide a player's session from a server administrator who controls both the application and its decryption key. The project therefore avoids centralized VRChat session custody instead of claiming an impossible cryptographic guarantee.

## Data and deletion

Local backups can contain display names, avatars, biographies, locations, status history, world IDs, and raw API response bodies. Hosted exports contain normalized friends and status events for one tenant. Neither format is anonymous.

To delete Local data, stop the service, remove `~/.picoworks-vrchat-monitor/`, and delete the `picoworks.vrchat-monitor` item from macOS Keychain. To delete a Hosted deployment, stop the stack and remove its `presence-monitor_monitor-data` Docker volume, local backups, and retained R2 objects through the Cloudflare account; the application deliberately has no remote-delete route. Tenant-level deletion is not yet exposed as a self-service operation; operators should export first when requested and delete the tenant transactionally from an offline backup/maintenance session.

## Operator checklist

- Never publish the Local monitor or port 8842 through a tunnel, LAN bind, reverse proxy, or public firewall rule. Use Hosted for remote access.
- Put Hosted behind HTTPS and keep port 8080 bound to loopback.
- Store `.secrets/` with mode `0700` and each secret with mode `0600`.
- Rotate bootstrap, access, collector, tunnel, and R2 backup credentials after suspected disclosure.
- Back up and restore-test the database; do not copy a live SQLite file without the backup API.
- Keep the host, Docker engine, base images, Python and npm dependencies patched.
- Never use production presence data as a test fixture or attach it to an issue.

## Build and release integrity

- GitHub Actions dependencies use full commit SHAs. Python transitive dependencies are version- and hash-locked, npm uses `npm ci`, and Docker base images plus the Dockerfile frontend use registry digests.
- Pull-request jobs keep repository contents read-only; CodeQL receives only the additional `security-events: write` permission needed to upload results. CI also runs secret scanning, dependency review and a container vulnerability gate; fixable Critical findings fail the image job.
- The release candidate job has read-only repository permission. Only a manual, confirmed publish from `main` receives tag, package and attestation permissions.
- Existing Git tags, releases and GHCR version tags fail closed. The workflow never force-pushes a tag or writes a mutable `latest` image tag.
- Repository maintainers should also enable GitHub release immutability so published tags and assets are platform-enforced as immutable.
- Release archives and container digests receive provenance attestations; published containers also carry an SBOM. Before a GitHub Release is created, the published `linux/amd64` config digest must equal the already scanned candidate. Attestations establish origin and integrity, not an assertion that the software is vulnerability-free.
