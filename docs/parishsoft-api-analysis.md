# ParishSoft API Analysis: v1 vs. v2

This document compares the two ParishSoft REST API generations that a parish
deployment may encounter, records which one ParishKit uses and why, and analyzes
whether ParishKit should switch APIs, run a hybrid, or adopt currently-unused
capabilities (including the write endpoints both APIs now expose).

It is a companion to the [top-level ParishKit spec](specs/intro/spec.md) and is
referenced by every tool that reads ParishSoft data:
[`pk-sync-ps-to-cc`](specs/pk-sync-ps-to-cc/spec.md),
[`pk-sync-ps-to-ggroup`](specs/pk-sync-ps-to-ggroup/spec.md),
[`pk-create-ps-ministry-rosters`](specs/pk-create-ps-ministry-rosters/spec.md),
[`pk-query-ps-memfam`](specs/pk-query-ps-memfam/spec.md), and
[`pk-print-ps-ministries`](specs/pk-print-ps-ministries/spec.md).

The package-level data layer that consumes whichever API is selected lives in
`src/parishkit/parishsoft.py`; its runtime wiring is `parishsoft_runtime.py`.
See the [intro spec's ParishSoft data layer section](specs/intro/spec.md#parishsoft-data-layer)
for how that code is structured.

## 1. Identities: two generations, not two copies

The two hosts are **two API generations of the same ParishSoft Family Suite
database**, not diverged forks of one API. They expose nearly identical record
data under different URL shapes, naming conventions, and authentication models.

| Property | **API v2 (ParishKit uses this)** | **API v1 (the "other" API)** |
| --- | --- | --- |
| Host | `ps-fs-external-api-prod.azurewebsites.net` | `fsapi.parishsoft.app` |
| Swagger UI | `…/index.html` | `…/index.html` |
| Raw spec | `…/swagger/v2/swagger.json` | `…/swagger/v1/swagger.json` |
| Spec title | **ParishSoft Family Suite 2.0** | **ParishSoft Customer API v1** |
| Base path | `/api/v2` | `/api` |
| Path style | lowercase, REST-ish (`families/search`, `members/{id}/contact`) | PascalCase, RPC-ish (`Families/UpdateFamilyContactInfo`) |
| Path count | 34 | 56 |
| Schema count | 41 | 44 |
| Pagination | Envelope with `pagingInfo` (`*ListPagingResponse`) | Mostly bare JSON lists |
| **Authentication** | **`x-api-key` request header** (no login endpoint) | **`POST /api/Login` + `/api/Token/Authenticate`** (username/password → token) |
| Write verb shape | `PUT` with the record id in the URL | `POST` with the record id in the body |

ParishKit targets **v2**. The default base URL is
`https://ps-fs-external-api-prod.azurewebsites.net/api/v2`
(`DEFAULT_API_BASE_URL` in `parishsoft.py`), and the client authenticates with
the `x-api-key` header read from a credential file at runtime.

**The authentication difference is the single most important practical fact.**
v2 needs only a parish-provisioned API key; v1 requires storing and rotating
parish login credentials and minting tokens. Any move toward v1 conflicts with
ParishKit's credential posture (API-key files on disk, no stored passwords).

## 2. Venn diagram — endpoint coverage

### 2.1 Shared by BOTH (same capability, different spelling)

- **Constituents** — search + detail.
- **Families** — search, detail, member list, ministry list, group lookup,
  workgroup list + per-workgroup roster, family-changes (incremental) feed,
  **contact-info update (write)**, **autofill update (write)**.
- **Members** — search, detail, sacrament list, status lookup, type lookup,
  workgroup lookups + per-workgroup roster, contact-info list,
  **contact-info update (write)**.
- **Ministry** — type list, minister roster (read only).
- **Offering** — funds, givers, family contribution summary.
- **Organizations** — search / detail.
- **QuickSearch** — families / members.
- A health/test ping endpoint.

### 2.2 Unique to v2 (ParishKit's API) — *absent in v1*

- `offering/pledge/list` — **full pledge listing**.
- `offering/contributiondetail/list` — **per-contribution detail listing**.
- Org-wide `offering/{organizationId}/givers`.
- `families/quick-search` / `members/quick-search` as first-class POST endpoints.
- Structured pagination envelope (`PagingInfo`) across all list endpoints.
- `MemberSearchResponseDto` adds `fatherName`, `motherName`,
  `responsibleAdultName`.

> **ParishKit depends on two of these v2-only endpoints**:
> `offering/pledge/list` and `offering/contributiondetail/list`
> (`load_pledges` / `load_contribution_details` in `parishsoft.py`). v1 has no
> equivalent — it offers only a per-family contribution *summary*. This alone
> rules out a wholesale switch to v1 without regressing giving features.

### 2.3 Unique to v1 (the "other" API) — *absent in v2*

- **MinistryScheduler** + `MinistryScheduler/MinistryRecords/{ID}` — liturgical
  / volunteer scheduling.
- **ReligiousEducation** — `Classes/{ID}`, `MemberAttendanceSummary/{ID}`.
- **Sacrament register reports** — Baptism, Confirmation, First Eucharist,
  Funeral, Matrimony, RCIA dynamic filter, Kenedy statistics summary, plus a
  generic `Sacraments` endpoint.
- **LifeEvent** feed.
- **Staff** — `/api/Staff`, `QuickSearch/Staff/List/Organization/{ID}/Active`.
- **Member SecureFields** — `GET SecureFields/{ID}` **and**
  `POST UpdateMemberSecureFields` (10 parish-defined encrypted fields + notes).
- Richer QuickSearch — organizations by name, org/member lookups.
- Per-family givers — `Offering/{ID}/Givers/{FamilyDUID}` (v2's givers endpoint
  is org-wide only).

### 2.4 Data fields are near-identical where they overlap

The underlying record shapes are the same database, just renamed:

| Record | v2 schema | v1 schema | Shared fields | v2-only | v1-only |
| --- | --- | --- | --- | --- | --- |
| Member (search) | `MemberSearchResponseDto` | `MemberResultModel` | 59 | `fatherName`, `motherName`, `responsibleAdultName` | (none) |
| Member contact | `MemberContactListResponseDto` | `MemberContactInfoResultModel` | 16 | (none) | (none) |
| Family (search) | `FamilySearchResponseDto` | `FamilyResultModel` | 40 | (none) | `webLinkDescription` |
| Family member | `FamilyMemberResponseDto` | `FamilyMemberResultModel` | 44 | (none) | `ssn` |
| Sacrament | `SacramentDto` | `SacrementModel` | 42 | (none) | (none) |
| Giver | `GiverListResponseDto` | `FundGiverModel` | 13 | (none) | (none) |

The one real divergence is the **ministry roster**: v1 uses `*DUID`/`*ID`
naming (`ministryTypeDUID`, `ministryRoleDUID`, `eventTypeDUID`, `familyDUID`),
while v2 uses `*Id` naming plus `trained`, `subOnly`, `emailAddress`, and
explicit `startDate`/`endDate`. Same concept, incompatible field names — code
written against one cannot read the other's roster rows without a translation
layer. ParishKit's `parishsoft.py` already tolerates several of these
`DUID`/`Id` naming inconsistencies via `_copy_first_duid`.

## 3. The "read-only" axiom is obsolete — but only narrowly

ParishKit's README and `parishsoft.py` historically state the ParishSoft API is
read-only. **Both APIs now expose write endpoints**, so the axiom should be
restated precisely:

> The ParishSoft APIs are read-only **except** for member/family
> contact-information fields (and, in v1 only, member secure custom fields).
> Census structure, ministry rosters, workgroup membership, giving, and
> sacraments remain read-only.

### 3.1 Families — writable in BOTH

**Contact update** (v2 `PUT families/{familyId}/contact` ≡ v1
`POST UpdateFamilyContactInfo`) — identical 36-field payload (v1 also carries
`familyDUID` in the body since the id is not in the URL):

- Names/salutations: `firstName`, `lastName`, `mailingName`,
  `informalMailingName`, `formalSalutation`, `informalSalutation`.
- Contact: `emailAddress`, `primaryPhone`, `emergencyPhone`, `primaryAddress`.
- Three full address blocks — **home / mailing / other** — each with
  `…AddressLine1`, `…AddressLine2`, `…City`, `…State`, `…Country`,
  `…PostalCode`, `…PostalCodePlus4`, `…AddressPhone`; the "other" block adds
  `otherAddressFromDate` / `otherAddressToDate`.
- `sdiocesanId`.

**Autofill update** (v2 `PUT families/{familyId}/autofill` ≡ v1
`UpdateFamilyAutoFill`) — takes only `organizationID`. This triggers
ParishSoft's server-side recomputation of the family's formal/informal mailing
names and salutations from its members (a "regenerate derived fields" action,
not a field write).

### 3.2 Members — contact writable in BOTH; SecureFields write only in v1

**Contact update** (v2 `PUT members/{memberId}/contact` ≡ v1
`POST UpdateMemberContactInfo`) — 15-field payload (v1 also carries `memberDUID`
and `familyDUID` in the body):

- Names: `firstName`, `nickName`, `middleName`, `lastName`, `maidenName`.
- Dates: `dateOfBirth`, `dateOfDeath`.
- `language`, `gender`.
- Contact: `emailAddress`, `homePhone`, `cellPhone`, `workPhone`, `pager`,
  `fax`.

**Secure fields** — **v1 only**: `POST UpdateMemberSecureFields` writes
`field1`–`field10` + `notes` (10 parish-defined encrypted custom fields). v2 has
no SecureFields endpoint at all.

### 3.3 Ministry & Workgroup records — READ-ONLY in BOTH

Neither API can write ministry data. There is **no** endpoint in either API to:

- create / edit / delete a ministry type;
- add or remove a minister from a roster;
- change a minister's role, training flag, or start/end dates;
- assign or unassign members/families to workgroups.

This directly constrains ParishKit's roster tooling
([`pk-create-ps-ministry-rosters`](specs/pk-create-ps-ministry-rosters/spec.md)): it
can **plan and report** roster state but cannot push roster changes back into
ParishSoft through either API. Roster mutation still requires the ParishSoft UI.

## 4. What ParishKit uses today vs. what is available

ParishKit currently issues only reads and read-style search POSTs, all against
v2 (see `parishsoft.py` loaders):

`organizations/search`, `families/search`, `members/search`,
`families/group/lookup/list`, `families/workgroup/list` + `/{id}/list`,
`members/contact/list`, `members/workgroup/lookup/list` + `/{id}/list`,
`ministry/type/list`, `ministry/{id}/minister/list`,
`offering/{org}/funds`, `offering/pledge/list`,
`offering/contributiondetail/list`.

Available in v2 but unused — useful candidates, in priority order:

1. **`families/change/list` (FamilyChanges)** — an incremental "what changed
   since X" feed. ParishKit currently pages the entire census on every run; this
   could turn full re-syncs into deltas for the sync tools. Highest-value unused
   read endpoint.
2. **`PUT members/{id}/contact` + `PUT families/{id}/contact`** — would let
   ParishKit *write back* corrected emails/phones/addresses instead of only
   reporting discrepancies. The package client already anticipates this:
   `ParishSoftClient.post_uncached` exists "for future mutation-style API calls"
   (a v2 write would add a `put`/`put_uncached` sibling, since v2 writes are
   `PUT` with the id in the URL).
3. **`members/{id}/sacrament/{type}/list`** — sacramental data for any future
   reporting need.
4. **`offering/{org}/contribution/summary/list`** — pre-aggregated giving
   summaries, cheaper than paging full contribution detail when only totals are
   needed.

## 5. Recommendation

**Stay on v2 as the primary API. Do not switch wholesale. Treat a hybrid as a
last resort gated on a v1-exclusive requirement.**

### 5.1 Do not switch wholesale to v1

- v1 lacks `offering/pledge/list` and `offering/contributiondetail/list`, which
  ParishKit depends on (summaries only) — switching regresses giving features.
- v1 requires username/password login + token auth versus v2's single API key;
  heavier, less deployment-friendly, and at odds with ParishKit's secrets model.
- v1 has no pagination envelope (ParishKit's `_extract_page` handles both
  shapes, but v2's contract is cleaner).
- v1's only net-new *data* is `ssn`, `webLinkDescription`, and differently-named
  ministry roster fields — not worth a migration.

### 5.2 Stay on v2 and adopt its unused capabilities

v2 is the modern, actively-named ("Family Suite 2.0"), API-key-authenticated,
paginated generation, and is effectively a superset of v1 for everything
ParishKit needs. The near-term wins live entirely within v2: add
`families/change/list` for incremental sync, and (optionally) enable
contact write-back through new `PUT` helpers.

### 5.3 Consider a narrow hybrid only if a v1-exclusive feature is required

v1 is the *only* source for SecureFields read/write, MinistryScheduler,
ReligiousEducation, sacrament register reports, Staff lists, and LifeEvent. If
one of those becomes a hard requirement, add a *secondary* v1 client (its own
base URL and its own login-token auth path) used solely for those endpoints,
while v2 remains the primary census/giving/workgroup source. `ParishSoftConfig`
is parameterized by `api_base_url`, so a second client instance is feasible —
but it needs a distinct authentication mechanism, so treat it as real work, not
a config toggle.

### 5.4 If write-back is pursued

Start with **member/family contact updates** (identical in both APIs, low risk,
high value for closing the "we detected a bad email" loop). Do **not** plan on
ministry-roster write-back — neither API supports it. Any write path must honor
ParishKit's dry-run discipline (see
[intro spec, dry-run and write safety](specs/intro/spec.md#dry-run-and-write-safety))
and the `expected_organization` guard before mutating a tenant.

## 6. Reproducing this analysis

The findings above were derived from the published OpenAPI specs:

```sh
curl -fsS https://ps-fs-external-api-prod.azurewebsites.net/swagger/v2/swagger.json
curl -fsS https://fsapi.parishsoft.app/swagger/v1/swagger.json
```

The raw spec JSON is generated/third-party data and is intentionally **not**
committed to this repository (see the no-generated-artifacts rule in the
[intro spec](specs/intro/spec.md#development-guidelines)). Re-fetch and diff the
`paths`, `components.schemas`, and per-endpoint `requestBody` blocks to refresh
this comparison when ParishSoft revises either API.
