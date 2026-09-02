# Dashboard Sharing And Exploration Design

## Outcome

Promote the custom dashboard to a top-level workspace, make charts filterable by the
people and presence dimensions users care about, and let an owner publish a polished
standalone dashboard with optional password access and a useful audit trail.

## Workspace Experience

The product navigation becomes 在线 / 玩家 / 仪表盘 / 分析 / 更多. Desktop edit mode
uses React Grid Layout v2 with a dedicated handle, bounded movement, resize handles,
and measured container width. The toolbar wraps before it can overflow. Mobile renders
the same panels as a normal single-column document and never captures vertical scroll.

Each panel editor exposes the filters supported by its metric. People are selected by
searchable multi-select; status and platform are bounded multi-select dimensions; the
self account remains an explicit toggle. New panels cover platform distribution,
selected-player online time, and data coverage in addition to the existing metrics,
rankings, heatmap, and world views.

## Sharing

“分享” publishes the currently saved dashboard as a snapshot. The owner chooses an
optional password and receives a standalone `/s/{share_id}` URL. Layout and filters do
not change until “更新分享内容” is used, while chart data remains current. Revocation
and password changes invalidate existing visitor grants.

The public route renders only the brand, title, publication time, and published
panels. It has no tenant navigation, search, account controls, editor, or arbitrary
query inputs. Public panel data is produced by a dedicated API which evaluates the
stored snapshot, never visitor-supplied tenant or filter identifiers.

## Security And Audit

Optional passwords are normalized as user input and stored only as a strong scrypt
hash with a random salt. Successful unlock creates a random HttpOnly share-session
cookie whose hash is persisted. Unlock attempts are rate limited. Audit events contain
the result, timestamp, coarse device class, and HMAC address fingerprint; they never
contain plaintext passwords, cookies, raw IP addresses, or complete user agents.

## Invalid Pipeline Entities

Only VRChat `usr_` identifiers may become player projections. Non-user Pipeline
notifications remain in raw fetch evidence but do not create friends or status events.
Existing `not_` projections are removed with a targeted migration after backup; no raw
fetch row is modified.
