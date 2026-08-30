# SKMB-2026-08-30-005: Browser State and Recoverable UX

- status: accepted
- decided_by: designer
- approval_source: user approved the complete release design with “通过，后续无需再审批”
- date: 2026-08-30
- commit: 68721f8
- patterns:
  - B_state_persistence
  - C_concurrent_operations
  - F_fail_semantics
- scope: navigation, deep links, query state, notes, charts, error recovery

## Decision

Mobile navigation has four durable destinations: `在线`, `玩家`, `分析`, and `更多`.
The homepage defaults to current online players. Existing hash destinations retain
aliases. Entity, tab, date range, filters, pagination, and scroll anchors survive
refresh and browser Back/Forward navigation.

Global search is available from the top bar and command shortcut. Player detail is a
deep-linked route, not an unaddressable oversized modal. Complex charts support
hover, focus, and tap; tap pins the vertical cursor, row highlight, and detail card.
Sticky labels and an equivalent collapsible table keep mobile and assistive use
complete without blocking vertical page scrolling.

Already rendered data remains visible when refresh fails. Errors are categorized and
deduplicated; low-level SSL/curl/stack text is not shown to normal users. A panel
failure does not blank unrelated panels. Note autosave carries a revision; a
multi-device conflict preserves the local draft and newer server value for explicit
resolution.

## Applies To

- `web/src/navigation.ts`
- `web/src/components/AppShell.tsx`
- search, player detail, chart, status, note, and world components
- query keys, URL state, focus restoration, error boundaries, and mobile CSS
- frontend navigation, accessibility, and conflict tests

## Rationale

The approved product prioritizes immediate comprehension and continuity over adding
more top-level screens or hiding failures behind generic loading and toast behavior.

## Alternatives

- Adding more mobile navigation items was rejected because six existing items are already overloaded.
- Keeping player details only in a modal was rejected because refresh and Back lose context.
- Last-write-wins note autosave was rejected because another device can silently destroy a draft.

## Supersedes

None.

## Superseded By

None.
