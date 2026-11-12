# Authentication and troubleshooting

## Mandatory preflight

Before either full synchronization direction, run `birdsync preflight --direction <direction> --output-dir <run-dir>`. It validates the real personal pages for both services. When one service fails, run `birdsync-auth-browser --service <service> --probe` before asking the user to log in. A network `unreachable` result is not an authentication failure and must not trigger relogin.

## BirdReport token

Default to the Agent-operated visual login handoff. Start `scripts/birdsync-auth-browser --service birdreport --port <free-port>`, publish the loopback port through the host's authenticated Web preview, and ask the user only to log in and click **我已完成登录**. The browser profile and credentials file use mode `0700`/`0600`; typed text and tokens are never logged or printed.

When missing, show these links and steps rather than returning a bare error:

- Login: https://www.birdreport.cn/member/login.html
- User center: https://www.birdreport.cn/member/index.html
- The visual handoff stores the browser session and extracts `X-Auth-Token` locally after successful login.
- Run `<skill_dir>/scripts/birdsync auth status birdreport --validate`, then resume the saved request.

BirdReport does not provide a portable OAuth/device flow. `auth set` is only a fallback for expert local users. Do not make ordinary users run it, and do not collect account passwords through chat.

## eBird login and personal data

- Login: https://ebird.org/home
- API key: https://ebird.org/api/keygen
- Personal data: https://ebird.org/downloadMyData
- Spreadsheet import: https://ebird.org/import/upload.form

An API key supports public API queries such as hotspots and taxonomy. It does not provide the full personal checklist export or authorize checklist submission. For eBird -> BirdReport work, direct the logged-in user to `Download My Data`, then accept the downloaded file.

eBird personal checklist lookup and import use the logged-in website session, not the API key. When the visual handoff is unavailable, the practical fallback is:

1. Give the user the official eBird login/import URL.
2. Ask only for the browser's `Cookie` request header after login; never ask for the password.
3. Feed it to `birdsync auth set-session ebird` through a hidden Agent-controlled input channel.
4. The command extracts and stores only `EBIRD_SESSIONID`; analytics cookies are discarded. The isolated browser profile may retain eBird's short-lived anti-bot cookie locally because checklist pages can require JavaScript proof-of-work.
5. Validate the session against the personal Import Data page, then continue automatically.

Chat is not an ideal secret channel, so prefer a platform secret form when available. If the user voluntarily supplies a Cookie in chat, do not quote it, log it, put it in a command argument, or retain more than the minimum session cookie. Advise rotation after the task if chat retention is a concern.

The BirdReport interface used here is an unofficial web backend and may drift. A 401/403 means the token is expired or invalid. A JSON/decryption failure after a site update usually means the response contract changed; stop and compare with the current CKRainbow/commonBird implementation before modifying crypto or endpoints.

eBird checklist pages may return an Anubis "Making sure you're not a bot" page even when `EBIRD_SESSIONID` is valid. This is not a login failure. The fetch commands automatically retry through the persistent isolated browser, wait for every observation count to finish loading, and then parse the saved page. eBird can render English or Chinese markup; source ID, effort, completeness, privacy, and counts must be extracted structurally rather than by English labels alone.

## Runtime

The `scripts/birdsync` launcher installs dependencies into a cache-local Python module directory on first use. It does not need `python3-venv`, sudo, or a global package install. If package installation is blocked, run offline conversion directly with `python3 scripts/birdsync.py` and provide an existing JSON snapshot; offline conversion uses only the Python standard library.

Run:

```bash
<skill_dir>/scripts/birdsync doctor
```

## Common outcomes

- `unmapped_region`: add/correct province, city, or district information. Do not submit blank Country/State.
- `invalid_time`: correct the source snapshot; do not guess a date.
- `missing_coordinates`: review the location in eBird during import or add a location override.
- `taxonomy_changed`: review the mapped scientific name, especially conditional splits.
- `already_imported`: do not re-import unless the user is intentionally correcting the checklist.
- `already_exists_remote`: do not upload; keep the remote checklist ID and reconciliation receipt.
- `ready to be submitted`: file processing and matching are complete, but no checklist exists yet; follow the final Submit these checklists action and verify the new checklist ID.
- `imported_record_changed`: reconcile manually; it is not safe to append a second copy silently.
- `auth_required`: keep `resume.json`, start the visual login handoff, validate the captured session, and continue with `birdsync resume --state ...` automatically.
- `invalid`: refresh the token through the same official login flow; do not repeatedly retry an expired token.
- `unreachable`: keep the saved request and retry later; do not mislabel a network outage as invalid credentials.

