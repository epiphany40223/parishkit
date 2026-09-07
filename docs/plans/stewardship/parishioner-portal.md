# Parishioner portal implementation plan

This plan implements the mobile-first
[Parishioner portal specification](../../specs/stewardship/parishioner-portal/spec.md).
The flow is a single guided response, not a general Family dashboard.

## Work packages

### FAM-01: Availability, code entry, and secure-link exchange

1. Build `/` campaign-status/code-entry behavior for unconfigured, maintenance,
   no-current-campaign, pre-start, active, Testing-draft, closed, and ended
   states.
2. Implement manual code authentication and `/access/<token>` digest exchange,
   session rotation, clean redirect, no-referrer/no-store headers, generic
   invalid pages, manual-code retry link, and the Valkey-backed secure-link
   anti-flood limit with bounded per-process fallback.
3. In Testing mode, require the authenticated Family session to pass an
   explicit server-recorded rehearsal acknowledgement before household data is
   shown; show the persistent Testing banner throughout the Family flow.
4. Apply active/registered Family eligibility and exact interval gates on every
   Family route, not only login.
5. Emit privacy-safe access/session/logout/denial audit and active-Family
   presence metadata.
6. Test no-current-campaign, guessing limiters, invalid/inactive/deactivated
   Families, token close/rotation, missing/stale Testing acknowledgement,
   banner persistence, and restore/go-live gates.

### FAM-02: In-memory form engine and navigation

1. Build a mobile-first multi-step form shell with progress, forward/back,
   focus management, accessible errors, and conditional module steps.
2. Prefill one immutable browser model from current source merged with the
   latest effective Family response; visually distinguish Family changes from
   current parish records without naming ParishSoft.
3. Keep all edits in memory until final Submit; add dirty-navigation/unload and
   session-expiry warnings without server drafts or local persistent storage.
4. Integrate inline validation, server validation-error mapping, idle warning,
   and the untrusted activity keepalive without carrying answers.
5. Test refresh/loss semantics, browser back/forward, long interaction, expiry,
   no-JavaScript degradation policy, and mobile keyboard/focus behavior.

### FAM-03: Family census step

1. Display envelope number/registration date read-only and implement home/
   mailing addresses plus parish-wide email opt-out.
2. Apply required/optional, normalization, address, and cross-field validation
   from the shared schema.
3. Support mailing-same-as-home without losing an explicitly distinct prior
   mailing address until confirmed.
4. Add field-level changed indicators and review summaries.
5. Test empty/partial/internationally malformed input against the specified US
   rules and census-disabled omission.

### FAM-04: Existing and proposed Member steps

1. Build clearly delineated repeated existing-Member sections for names,
   prefix/suffix/maiden, birth date, gender, email, phone, marital status,
   language/other, moved-household, deceased, and optional death date.
2. Implement terminal semantic controls and their validation/interactions
   without silently converting them to simple field edits.
3. Build proposed-Member add/edit/remove with local UUID identity and the
   specification's supported fields and attestation.
4. Ensure mandatory rules account for terminal Members and optional contacts.
5. Test all field combinations, ages/dates, email/phone, language other, moved/
   deceased proposals, and proposed-Member removal.

### FAM-05: Ministry and financial stewardship steps

1. Show deterministic current Ministries per Member, leave choices, and an
   on-demand searchable/sorted join list excluding current participation.
2. Apply the campaign Ministry set and create no source roster mutation.
3. Display prior pledge/current contribution with mapped period/source-as-of,
   collect nonnegative annual pledge and frequency, and calculate per-period
   amount deterministically.
4. Render configurable stable-ID share options with the specified zero/one/many
   Member wording, parish/year placeholders, multi-select, and conditional Other
   text; preserve the financial step and answers when all Members are terminal.
5. Show the upcoming period start reminder and Unavailable rather than false
   financial zeroes.
6. Test disabled modules, long Ministry lists, duplicate/excluded choices,
   decimal rounding/display, zero pledge, and option-version changes. Cover
   zero/one/many Member labels, terminal/proposed Member counting, and navigating
   back to mark all Members terminal without losing financial answers.

### FAM-06: Additional information, review, and atomic submit

1. Add optional Admin-controlled additional-information prompt with bounded
   plain text and no rich HTML.
2. Build a complete review/attestation page grouped by Family, Members,
   Ministries, financial data, and additional information with changed markers.
3. Perform complete server validation and stale effective-version/source checks
   at the definitive Submit endpoint; commit through DAT-06 only once.
4. On success, enqueue confirmation work, show versioned Thank You content, and
   silently invalidate/logout the Family session.
5. On failure, retain in-memory answers, return accessible field/summary errors,
   and never create a partial submission/workflow.
6. Require the same server-side Testing rehearsal acknowledgement at final
   submission; a banner or client-side flag alone never authorizes the write.
7. Test double click/retry, network interruption, stale concurrent visit,
   no-change response, all modules, and transaction rollback.

### FAM-07: Repeat visits and source-change merge

1. Show prior submission time and prefill from current source plus the latest
   immutable Family response.
2. Apply DAT-08 merge/conflict results while keeping Admin-edited proposal
   values out of Family-attributed fields.
3. Show source-caught-up fields as current, retain unresolved Family changes,
   and explain true conflicts in plain parish-facing language.
4. Supersede/cancel derived follow-up work only through the new final
   submission transaction.
5. Test upstream catch-up, source-only change, same/different concurrent change,
   withdrawn additional text, and repeated Ministry requests.

### FAM-08: Responsive, accessibility, privacy, and browser completion

1. Exercise the full flow at supported mobile/desktop breakpoints and current/
   previous browser versions.
2. Complete WCAG 2.2 AA automated and manual keyboard/screen-reader checks,
   including dynamic Ministry lists and repeated Member sections.
3. Verify no form answers enter URLs, logs, analytics, local storage, cache, or
   presence/keepalive requests.
4. Add realistic slow-network, expired-session, worker-delay, and server-error
   browser scenarios.
5. Measure form payload/render behavior with maximum supported Family/Ministry
   fixtures and fix material latency/focus regressions.

## Review handoffs

- Review Gate 2 is the main Family vertical-slice review: FAM-01 through FAM-07
  plus a representative FAM-08 mobile/accessibility pass.
- Review Gate 3 rechecks confirmation mail and delayed-worker behavior.
- Review Gate 5 completes the full browser, privacy, and accessibility matrix.

## Completion criteria

- A Family can complete every enabled combination without any intermediate
  answer persistence.
- Every access path, boundary, repeat merge, and submit race is tested.
- No page names ParishSoft or attributes an Admin edit to the Family.
