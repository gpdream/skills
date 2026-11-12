---
name: sync-bird-records
description: Prepare, validate, reconcile, and safely migrate personal bird checklists in both directions between 中国观鸟记录中心 (birdreport.cn) and eBird. Use when a user asks to sync, migrate, convert, export, import, compare, or deduplicate birding records, including 观鸟记录、随手记、eBird Extended CSV、地点匹配、鸟种名录转换 or taxonomy review. Supports authenticated eBird checklist pages to BirdReport with dry-run, duplicate checks, privacy preservation, remote write receipts, and read-back verification; never claim remote submission without evidence.
license: MIT
---

# Sync Bird Records

Turn a natural-language migration request into a reviewable import plan, then perform the explicitly requested remote write and verify it. Keep the user-facing flow short; put deterministic data work in `scripts/birdsync.py`.

## User experience

Ask only for information that is actually missing:

1. Infer direction. Default to BirdReport -> eBird when the user says only "同步观鸟记录".
2. Infer the date range. If absent, ask one concise question for it.
3. Before fetching data, run the direction-aware live `preflight`. It validates both personal accounts and silently continues when they are usable; a stored value alone is not proof of authentication.
4. If one service is missing or invalid, probe the isolated browser profile first. Only if the probe fails, expose one visual login handoff for the failed service. The user only logs in; never ask for a password or terminal work.
5. Capture only the minimum reusable credential locally, revalidate the real personal page, resume the saved request, and do not ask the user to move intermediate files.
6. Search only authenticated personal-history pages, reconcile duplicates before writes, and require a remote ID plus read-back evidence before reporting success.
7. If review is required, ask one grouped question covering only the unresolved items.

Users should be able to say things like:

- "把我 2026 年 10 月在观鸟记录中心的记录整理成 eBird 导入包。"
- "检查这个 commonBird 快照，先别上传。"
- "这批记录是不是已经导入过？"

## Supported boundary

- Fully support direction-aware authentication preflight, resumable login handoff, and automatic continuation.
- Fully support BirdReport snapshot/live fetch -> normalized review -> authenticated eBird personal-history reconciliation -> eBird Record Format (Extended) CSV files -> local import ledger.
- Treat CSV creation as `prepared`, not `synced` or `imported`.
- Mark records imported only after the authenticated eBird page shows an accepted import or the user independently confirms it.
- The Agent may drive the authenticated eBird website when the user explicitly requested synchronization. Before the final submit, reconcile exact and likely duplicates; exact duplicates are skipped by default, including test runs.
- Fully support authenticated eBird My Checklists selection by date/location -> anti-bot browser fallback -> normalized observations -> BirdReport current taxonomy/point resolution -> duplicate check -> explicit `--apply` -> remote read-back receipt.
- Preserve eBird privacy: a hidden eBird checklist is created as a private BirdReport record. Never make a private source public during migration.
- A reverse import is not complete until BirdReport returns an activity ID and serial ID and read-back matches the source marker, species count, and individual count.
- Never guess browser actions: inspect the live form, preserve a screenshot/result receipt, and stop before an ambiguous or destructive action.

## Runtime

Set `<skill_dir>` to this skill directory. Use the launcher so dependencies stay in a dedicated cache environment:

```bash
<skill_dir>/scripts/birdsync doctor
```

The first command installs two small dependencies into a user-cache module directory. It does not need sudo or modify global Python packages. It also reports credential status without revealing values.

## Authentication onboarding

Always start a full synchronization with the direction-aware live check:

```bash
<skill_dir>/scripts/birdsync preflight \
  --direction birdreport-to-ebird \
  --output-dir /path/to/run
```

Use `--direction ebird-to-birdreport` for the reverse flow. A `ready` result means the Agent continues without messaging the user. `unreachable` means retry the network check; it must not trigger a pointless relogin.

If a service is missing or invalid, first save the exact request, then start the visual login handoff:

```bash
<skill_dir>/scripts/birdsync-auth-browser --service birdreport --probe
<skill_dir>/scripts/birdsync-auth-browser --service birdreport --port <free-port>
```

Run `--probe` first for exactly the failed service. It checks the protected browser profile and captures a still-valid session without user interaction. Start the port-based handoff only when the probe returns `auth_required`.

Register that loopback port with the current agent host's authenticated Web-preview feature. In Botmux, use `botmux preview <free-port>` and privately deliver the workbench card; do not expose a dashboard token in the group. The user logs in on the rendered website and clicks **我已完成登录**. Browser state is persisted under `~/.config/sync-bird-records/browser/`, and BirdReport's token is copied to the protected credentials file automatically without printing it.

The hidden-input commands are Agent-operated ingestion primitives, not instructions for ordinary users:

```bash
<skill_dir>/scripts/birdsync auth set birdreport
<skill_dir>/scripts/birdsync auth set ebird
```

Tokens are saved to `~/.config/sync-bird-records/credentials.json` with mode `0600`. Environment variables still take precedence. Never execute `auth set` through chat input, a visible command argument, or a non-interactive pipe.

For eBird personal records, use the website session rather than an API token. When visual login cannot be exposed, send `https://ebird.org/home`, ask the user to copy the logged-in request's Cookie header, and pass it privately to `auth set-session ebird`. The portable session file retains only `EBIRD_SESSIONID` (mode `0600`); the protected browser profile may separately retain eBird's short-lived anti-bot cookie. Analytics cookies are not copied. Never echo the supplied header.

After setup, validate without exposing the token:

```bash
<skill_dir>/scripts/birdsync auth status birdreport --validate
<skill_dir>/scripts/birdsync auth status ebird --validate
```

BirdReport has no portable OAuth/device flow. The isolated browser is the handoff boundary: the user authenticates and the Agent does everything else. For eBird, the same browser profile preserves the login cookies needed for personal-data and import pages; an eBird API key does not authorize checklist writes.

## Workflow

### A. Use an existing BirdReport/commonBird snapshot

The input may be either a JSON array of reports or `{ "reports": [...] }`.

```bash
<skill_dir>/scripts/birdsync prepare \
  --input /path/to/checklists.json \
  --output-dir /path/to/output
```

This writes:

- `review.md`: concise human review
- `review.json`: structured warnings and blockers
- `manifest.json`: hashes, report identities, and CSV metadata
- `ebird-import-*.csv`: files kept below the configured byte limit
- `ledger.json`: local prepared/imported state

### B. Run BirdReport -> eBird with personal-history reconciliation

Use the full command for a user-requested synchronization. It preflights both accounts, fetches only the authenticated user's BirdReport records, prepares CSV, and checks authenticated eBird My Checklists for the exact embedded BirdReport source marker before deciding whether upload is needed:

```bash
<skill_dir>/scripts/birdsync sync-birdreport-to-ebird \
  --start-date 2026-10-01 \
  --end-date 2026-10-31 \
  --location 南湖 --latest 1 \
  --output-dir /path/to/output
```

`already_reconciled` means no upload is needed. `ready_for_ebird_import` means only the unmatched CSV files may enter the authenticated eBird import state machine. `review_required` means a remote read or mapping is unresolved; do not upload yet.

Use the lower-level `sync` command only when the user asked for a local package rather than a complete synchronization.

After the user completes local setup, resume exactly where it stopped:

```bash
<skill_dir>/scripts/birdsync resume --state /path/to/output/resume.json
```

The token must come from an agent secret store, the user's local environment, or the protected local credentials file. Never pass it as a command-line argument, print it, save it in the skill, or include it in an attachment.

### C. Run eBird -> BirdReport directly from personal history

The normal reverse flow does not ask the user to download or save HTML. It preflights both accounts, searches authenticated My Checklists, fetches the selected page through the session or isolated-browser anti-bot fallback, dry-runs location/taxonomy/duplicate checks, and writes only with `--apply`:

```bash
<skill_dir>/scripts/birdsync sync-ebird-to-birdreport \
  --start-date 2026-10-01 \
  --end-date 2026-10-31 \
  --location 镜湖 --latest 1 \
  --output-dir /path/to/output
```

If the dry-run returns `ready_to_apply` and the user's request authorizes synchronization, rerun the same command with `--apply`. `already_reconciled` means the matching BirdReport activity already exists and no write occurred. Use `request-ebird-data` only as the fallback for bulk/history work that cannot be represented by the current personal checklist page.

### D. Import one already-saved authenticated eBird checklist into BirdReport

Save the authenticated checklist HTML under the private run directory (mode `0600`). Run the default dry-run first:

```bash
<skill_dir>/scripts/birdsync import-ebird-checklist \
  --input /path/to/authenticated-checklist.html \
  --output-dir /path/to/run
```

The dry-run:

- verifies the page is a full authenticated checklist rather than an anti-bot/login page;
- maps every eBird Chinese common name to the current enabled BirdReport taxonomy;
- resolves the nearest same-name BirdReport point within 5 km (or uses a reviewed `--point-id`);
- searches only the authenticated user's BirdReport records for a source marker or same time/point/species-count duplicate;
- writes `birdreport-import-plan.json` without credentials.

When the user's request already authorizes synchronization and the dry-run returns `ready_to_apply`, the Agent runs:

```bash
<skill_dir>/scripts/birdsync import-ebird-checklist \
  --input /path/to/authenticated-checklist.html \
  --output-dir /path/to/run \
  --apply
```

The adapter creates the activity, bulk-inserts its taxa, writes completion/quantity/effort options, then reads the activity and observations back. Success is `verified_remote`, with `activity_id`, `serial_id`, species/individual counts, privacy state, and `birdreport-write-receipt.json`.

If a request stopped after the activity was created, do not submit again. Verify and finalize the existing activity instead:

```bash
<skill_dir>/scripts/birdsync import-ebird-checklist \
  --input /path/to/authenticated-checklist.html \
  --output-dir /path/to/run \
  --verify-activity-id <BirdReport-activity-id>
```

### E. Apply reviewed location choices

Create an override file only for locations the user wants to change. Prefer point IDs; names can collide.

```json
{
  "by_point_id": {
    "12345": {"name": "世纪公园", "latitude": 31.217, "longitude": 121.553}
  },
  "by_name": {
    "旧地点名": {"name": "个人地点名", "latitude": 31.2, "longitude": 121.5}
  }
}
```

Then rerun `prepare` with `--location-overrides /path/to/locations.json`.

### F. Confirm eBird import

After the user confirms that eBird accepted the files:

```bash
<skill_dir>/scripts/birdsync mark-imported \
  --manifest /path/to/output/manifest.json \
  --remote-service ebird \
  --remote-id <accepted-eBird-checklist-id> \
  --evidence-url https://ebird.org/checklist/<accepted-eBird-checklist-id> \
  --batch-id <eBird-import-batch-id> \
  --acceptance-receipt /path/to/saved-acceptance.html
```

Do not run this merely because CSV files were generated.

Exact duplicates are skipped by default. If the user explicitly asks to test a real upload despite an existing checklist, that instruction overrides the default skip for that record only. Follow the complete eBird import state machine: upload, fix species, fix locations, submit ready checklists, then verify the new checklist ID in My Checklists/API. An uploaded or processed file is not yet a completed import.

When authenticated personal-history evidence shows that the checklist already exists, do not upload and do not call `mark-imported`. Record the duplicate decision instead:

```bash
<skill_dir>/scripts/birdsync mark-existing \
  --manifest /path/to/output/manifest.json \
  --source-id <source-id> \
  --remote-id <eBird-checklist-id> \
  --evidence-url https://ebird.org/checklist/<eBird-checklist-id> \
  --matched-on date --matched-on start_time --matched-on location --matched-on taxon_count
```

This writes an `existing_remote` ledger entry plus a reconciliation receipt. A subsequent prepare/sync skips unchanged content automatically; a changed source hash becomes a conflict.

## Review policy

Read `review.md` before reporting success. Use these rules:

- `blocker`: excluded from CSV; explain what data is missing.
- `warning`: included, but call out assumptions such as default observer count, inferred quantity, missing coordinates, or fallback protocol.
- `info`: no user action needed.

Never locate a personal source checklist by searching all public records for a place name. A record is eligible only when it came from the authenticated user's **My Records** view/API or from a snapshot whose ownership the user supplied or confirmed. Location text alone is not identity evidence.

Require explicit user review when any checklist has:

- an unmapped eBird region;
- an invalid/missing time;
- no usable observations;
- a taxonomy rule chosen by location/month;
- a fallback protocol or observer count;
- missing coordinates for a personal location.

Do not silently use last-write-wins. If a ledger says a source record is already imported but its content hash changed, report a conflict and leave it out unless the user explicitly requests `--include-imported`.

## Data and privacy rules

- Keep exact coordinates, tokens, raw payloads, and ledgers local by default.
- Do not commit generated snapshots, outputs, `.env`, or `ledger.json`.
- Prefer scientific names as the interchange key; common names are display labels only.
- Preserve the source report ID in checklist comments for later reconciliation.
- Preserve original input unchanged and record mapping hashes in `manifest.json`.
- When the user requested synchronization, the Agent handles eBird upload through the authenticated browser. The user participates only in login or an unavoidable platform confirmation. Store an acceptance/skip receipt before updating the ledger.

## References

- Read `references/field-policy.md` when explaining lossy fields, warnings, or conflicts.
- Read `references/auth-and-troubleshooting.md` for token setup, dependency failures, and API drift.
- Read `references/third-party-notices.md` before redistributing or modifying the mapping asset.

