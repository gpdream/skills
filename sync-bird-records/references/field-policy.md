# Field and conflict policy

## Canonical checklist identity

Use `(source_system, source_id)` as identity. For BirdReport, prefer `serial_id`, then internal `id`. Store a stable content hash separately; a changed hash means the same logical checklist was edited.

## Taxonomy

- Start from the source scientific name, not the localized common name.
- Apply the bundled CH4 -> eBird rule set only when its location/month conditions match.
- Record every changed scientific name as a warning so the user can review splits and subspecies groups.
- When no rule exists, preserve the source scientific name and split the first token into Genus and the remainder into Species.
- Never invent a taxon from fuzzy common-name similarity.

## Location

- Prefer explicit reviewed overrides keyed by BirdReport `point_id`.
- Otherwise preserve the BirdReport point name and coordinates as an eBird personal location.
- Use region codes only to fill Country/State. Do not treat a region match as hotspot identity.
- Missing coordinates are a warning; an unmapped country/state is a blocker.

## Effort fields

BirdReport and eBird do not have identical effort semantics. Apply these conservative fallbacks and report them:

- Protocol: preserve an explicit source protocol; otherwise use `historical` with a warning.
- Observers: preserve a positive explicit observer count; otherwise use `1` with a warning.
- Duration: calculate from start/end; clamp to 1-1440 minutes with a warning.
- Complete checklist: use BirdReport `eye_all_birds` when present; otherwise use `Y` with a warning.
- Counts: if BirdReport says quantities are not exact, write `X`. If that flag is absent and every count is 1, infer `X` and warn.

## Conflict policy

Ledger states are `prepared`, `imported`, and `existing_remote`.

- Same source ID + same hash + prepared: regenerate deterministically.
- Same source ID + same hash + imported: skip by default.
- Same source ID + same hash + existing_remote: skip by default and retain the remote checklist ID and evidence URL.
- Same source ID + changed hash + imported/existing_remote: conflict; skip and reconcile the changed source before creating a corrected import.
- Never delete or overwrite a remote checklist automatically.

