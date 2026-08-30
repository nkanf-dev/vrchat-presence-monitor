# SKMB-2026-08-30-004: Portable Backup and Restore

- status: accepted
- decided_by: designer
- approval_source: user approved the complete release design with “通过，后续无需再审批”
- date: 2026-08-30
- commit: 68721f8
- patterns:
  - B_state_persistence
  - C_concurrent_operations
  - E_security_boundary
  - F_fail_semantics
  - G_irreversible_action
- scope: portable tenant export/import, raw archive, migration, production restore

## Decision

Portable backup version 3 is tenant-scoped and merge-only. The default full gzip JSON
export includes normalized records, organization data, observation evidence,
anomalies, preferences, and raw fetches with stable IDs. A lightweight export may
omit raw bodies. Neither form includes passwords, cookies, encrypted upstream
sessions, viewer tokens, collector tokens, or deployment secrets.

Stable raw IDs preserve every fetch, including two byte-identical responses. Legacy
rows derive their stable identity from the original row identity plus request and
body/error hashes; new rows receive a durable random ID when inserted.

Import streams into tenant-scoped staging storage, validates schema, expansion limits,
stable IDs, references, and credential exclusions, then performs one atomic merge.
Append-only rows are idempotent, newer mutable records win only by an explicit
revision/timestamp rule, and import never deletes data. Any validation or merge
failure rolls back the entire import.

Before a release migration, production creates an integrity-checked SQLite snapshot,
uploads it off-site, downloads it, and verifies restore without replacing the live
database. Legacy v1/v2 histories remain readable; unavailable coverage is labeled
unknown rather than fabricated.

## Applies To

- `server/backup_json.py`
- hosted and local storage backup adapters
- frontend backup worker and data view
- schema migration, R2 backup, and restore verification
- compatibility, quota, idempotency, isolation, and rollback tests

## Rationale

Users own their recorded data, while a failed import or release must never endanger
the only live copy or leak deployment authentication material.

## Alternatives

- Replacing tenant data from an import was rejected because imports are not authoritative deletes.
- Materializing large raw arrays in process memory was rejected because production has bounded memory.
- Treating upload success as backup proof was rejected; a downloaded restore drill is required.

## Supersedes

None.

## Superseded By

None.
