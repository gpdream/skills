#!/usr/bin/env python3
"""Build an offline target index and prepare evidence-aware mapping plans."""

import argparse
from collections import Counter, defaultdict
import csv
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import unicodedata
import xml.etree.ElementTree as ET
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
DEFAULT_CACHE = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "ebird-to-birdreport/crosswalk.json"
CONFIRMED_RELATIONS = {"same_taxon", "synonym"}
REVIEW_RELATIONS = {"split", "lump", "geographic_inference"}


def normalize(value):
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).split()).casefold()


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, value):
    with Path(path).open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def build_index(args):
    with ZipFile(args.xlsx) as archive:
        shared = []
        if "xl/sharedStrings.xml" in archive.namelist():
            shared = [
                "".join(item.itertext())
                for item in ET.fromstring(archive.read("xl/sharedStrings.xml"))
            ]
        sheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        rows = []
        for row in sheet.findall(".//s:sheetData/s:row", NS):
            values = {}
            for cell in row.findall("s:c", NS):
                column = re.sub(r"\d", "", cell.attrib["r"])
                kind = cell.get("t")
                value = cell.findtext("s:v", default="", namespaces=NS)
                if kind == "s":
                    value = shared[int(value)]
                elif kind == "inlineStr":
                    value = "".join(
                        node.text or "" for node in cell.findall(".//s:t", NS)
                    )
                values[column] = value.strip()
            rows.append(values)
    if not rows:
        raise ValueError("Empty taxonomy worksheet")
    columns = {label: col for col, label in rows[0].items()}
    if not {"中文名", "拉丁名"} <= columns.keys():
        raise ValueError("Expected 中文名 and 拉丁名 headers in first worksheet")
    taxa = []
    for row in rows[1:]:
        name = row.get(columns["中文名"], "")
        scientific = row.get(columns["拉丁名"], "")
        if name and scientific:
            taxa.append({"name": name, "scientific_name": scientific})
    write(args.output, {
        "version": args.version,
        "live_ids": False,
        "source_url": args.source_url,
        "built_on": date.today().isoformat(),
        "note": "Old downloadable taxonomy; row numbers are not live taxon IDs.",
        "taxa": taxa,
    })
    print(f"Indexed {len(taxa)} taxa; live_ids=false")


def number(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def read_source(args):
    source = load(args.source)
    if args.source_version:
        source["source_taxonomy_version"] = args.source_version
    if not args.source_taxonomy:
        return source
    path = Path(args.source_taxonomy)
    evidence = f"{path.resolve()}#sha256={hashlib.sha256(path.read_bytes()).hexdigest()}"
    # Both the eBird API CSV and the eBird/Clements download use these columns.
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        headers = {
            normalize(key).replace(" ", "_"): key for key in (reader.fieldnames or [])
        }
        code_key = headers.get("species_code") or headers.get("ebird_species_code")
        sci_key = headers.get("sci_name") or headers.get("scientific_name")
        category_key = headers.get("category")
        if not (code_key and sci_key and category_key):
            raise ValueError("Source CSV needs species_code, sci_name/scientific name and category")
        taxa = defaultdict(list)
        for row in reader:
            taxa[row[code_key].strip()].append(row)
    for observation in source["observations"]:
        rows = taxa.get(observation.get("code"), [])
        if len(rows) != 1:
            if rows:
                raise ValueError(f"Duplicate source taxonomy code: {observation['code']}")
            continue
        row = rows[0]
        if not row[sci_key].strip() or not row[category_key].strip():
            raise ValueError(f"Incomplete source taxonomy identity: {observation['code']}")
        if (observation.get("scientific_name")
                and normalize(observation["scientific_name"]) != normalize(row[sci_key])):
            raise ValueError(f"Source scientific-name conflict: {observation['code']}")
        observation.update(
            scientific_name=row[sci_key].strip(),
            category=row[category_key].strip().lower(),
            identity_evidence=evidence,
        )
    return source


def read_cache(path):
    if not path or not Path(path).expanduser().exists():
        return {"schema_version": 2, "entries": [], "deferred": []}
    cache = load(Path(path).expanduser())
    # Retain old explicit --cache files, with their global version constraints.
    for entry in cache.get("entries", []):
        for key in ("source_taxonomy_version", "target_taxonomy_version"):
            entry.setdefault(key, cache.get(key))
    cache.setdefault("entries", [])
    cache.setdefault("deferred", [])
    return cache


def save_cache(path, cache):
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    cache["schema_version"] = 2
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        json.dump(cache, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        temporary = stream.name
    os.replace(temporary, path)


def recent(value, days):
    try:
        return 0 <= (date.today() - date.fromisoformat(value)).days < days
    except (TypeError, ValueError):
        return False


def source_key(observation):
    return (observation.get("code"), normalize(observation.get("name")))


def deferred_key(source, observation):
    # Counts do not change taxonomy identity. Location and comments can add evidence.
    value = {
        "observation": {key: observation.get(key) for key in (
            "code", "name", "scientific_name", "category", "uncertain",
            "relation", "identity_evidence", "comment",
        )},
        "source_version": source.get("source_taxonomy_version"),
        "location": source.get("location"),
    }
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def is_uncertain(observation):
    label = f"{observation.get('name', '')} {observation.get('scientific_name', '')}"
    return bool(
        observation.get("uncertain")
        or observation.get("category", "species") != "species"
        or re.search(r"[/／]|\bsp\.|\bspp\.|\bhybrid\b|杂交|未定", label, re.I)
    )


def usable(entry, source, observation, max_age):
    if (not observation.get("code") or entry.get("code") != observation["code"]
            or entry.get("relation") not in CONFIRMED_RELATIONS
            or not normalize(entry.get("evidence"))):
        return False
    old_version = entry.get("source_taxonomy_version")
    new_version = source.get("source_taxonomy_version")
    if old_version and new_version and old_version != new_version:
        return False
    if not (old_version and new_version) and not recent(entry.get("verified_on"), max_age):
        return False
    if observation.get("scientific_name"):
        if normalize(observation["scientific_name"]) != normalize(entry.get("source_scientific_name")):
            return False
    elif entry.get("source_name"):
        if normalize(observation.get("name")) != normalize(entry["source_name"]):
            return False
    elif not (old_version and new_version):
        return False
    return True


def remember_match(cache, source, observation, target, relation, evidence):
    if not observation.get("code"):
        return
    entry = {
        "code": observation["code"],
        "source_name": observation.get("name"),
        "source_scientific_name": observation.get("scientific_name"),
        "source_taxonomy_version": source.get("source_taxonomy_version"),
        "target_name": target["name"],
        "target_scientific_name": target["scientific_name"],
        "relation": relation,
        "evidence": evidence,
        "verified_on": date.today().isoformat(),
    }
    cache["entries"] = [
        old for old in cache["entries"]
        if (old.get("code"), normalize(old.get("source_name")), old.get("source_taxonomy_version"))
        != (*source_key(observation), source.get("source_taxonomy_version"))
    ] + [entry]
    key = deferred_key(source, observation)
    cache["deferred"] = [old for old in cache["deferred"] if old.get("key") != key]


def prepare(args):
    source = read_source(args)
    taxonomy = load(args.taxonomy)
    by_scientific, by_name = defaultdict(list), defaultdict(list)
    for taxon in taxonomy["taxa"]:
        if normalize(taxon.get("scientific_name")):
            by_scientific[normalize(taxon["scientific_name"])].append(taxon)
        if normalize(taxon.get("name")):
            by_name[normalize(taxon["name"])].append(taxon)
    candidates = load(args.candidates)
    seeds = {row["code"]: row for row in candidates if row.get("code")}
    cache = read_cache(None if args.no_cache else args.cache)
    cache_changed = False
    results = []
    for observation in source["observations"]:
        result = {
            "source": observation, "status": "unresolved", "candidates": [],
            "write_ready": False, "lookup_required": False,
        }
        name = str(observation.get("name") or "")
        scientific = observation.get("scientific_name")
        if is_uncertain(observation):
            result["reason"] = "Preserve uncertain or non-species source taxon."
            results.append(result)
            continue
        seed = seeds.get(observation.get("code"), {})
        review_scope = observation.get("relation", seed.get("relation")) in REVIEW_RELATIONS
        cached = [] if args.refresh else [
            entry for entry in cache["entries"]
            if usable(entry, source, observation, args.cache_max_age_days)
        ]
        cached_targets = {normalize(entry.get("target_scientific_name")) for entry in cached}
        conflict = len(cached_targets) > 1
        matches = []
        if not review_scope and not conflict and len(cached_targets) == 1:
            matches = by_scientific.get(next(iter(cached_targets)), [])
            result["match_method"] = "confirmed_cache"
        if (not matches and not review_scope and not conflict
                and scientific and normalize(observation.get("identity_evidence"))):
            matches = by_scientific.get(normalize(scientific), [])
            result["match_method"] = "source_scientific_name"
        if len(matches) == 1:
            result["status"] = "matched"
            result["target"] = matches[0]
            result["reason"] = "Reuse confirmed identity; resolve target ID from the supplied catalog."
            if result["match_method"] == "source_scientific_name" and observation.get("code"):
                remember_match(cache, source, observation, matches[0], "same_taxon", observation["identity_evidence"])
                cache_changed = True
        else:
            suggestions = list(matches)
            suggestions.extend(by_scientific.get(
                normalize(seed.get("target_scientific_name")), []
            ))
            suggestions.extend(by_name.get(normalize(seed.get("target_name")), []))
            suggestions.extend(by_name.get(normalize(name), []))
            unique = {}
            for item in suggestions:
                key = (item.get("taxon_id"), item.get("name"), item.get("scientific_name"))
                unique[key] = item
            result["candidates"] = list(unique.values())
            result["status"] = "needs_review" if unique else "unresolved"
            result["reason"] = (
                "Candidate only; verify source identity and taxonomic scope."
                if unique else "No evidence-backed unique match; preserve source."
            )
            result["lookup_required"] = True
            result["lookup_kind"] = "source_identity" if not scientific else "crosswalk"
            if review_scope or conflict:
                result["status"] = "needs_review"
                result["lookup_kind"] = "taxonomic_scope"
                result["reason"] = "Taxonomic scope or conflicting cache entries require review."
            key = deferred_key(source, observation)
            deferred = next((
                entry for entry in cache["deferred"]
                if entry.get("key") == key and normalize(entry.get("evidence"))
                and entry.get("target_taxonomy_version") == taxonomy.get("version")
                and recent(entry.get("checked_on"), args.deferred_max_age_days)
            ), None)
            if deferred and not args.refresh:
                result.update(
                    status="deferred", lookup_required=False,
                    reason=deferred["reason"], evidence=deferred["evidence"],
                )
        result["write_ready"] = bool(
            result["status"] == "matched"
            and taxonomy.get("live_ids") is True
            and result["target"].get("taxon_id")
            and number(observation.get("count")) is not None
        )
        results.append(result)
    lookup_queue = {}
    id_queue = {}
    for index, row in enumerate(results):
        if row["lookup_required"]:
            observation = row["source"]
            identity = observation.get("code") or normalize(observation.get("scientific_name") or observation.get("name"))
            key = (identity, row["lookup_kind"])
            task = lookup_queue.setdefault(key, {
                "code": observation.get("code"), "name": observation.get("name"),
                "kind": row["lookup_kind"], "row_indices": [],
            })
            task["row_indices"].append(index)
        if row["status"] == "matched" and not (
            taxonomy.get("live_ids") is True and row["target"].get("taxon_id")
        ):
            target = row["target"]
            id_queue[normalize(target["scientific_name"])] = {
                "name": target["name"], "scientific_name": target["scientific_name"],
            }
    counts = Counter(row["status"] for row in results)
    known = [number(row.get("count")) for row in source["observations"]]
    output = {
        "source_id": source.get("source_id"),
        "source_taxonomy_version": source.get("source_taxonomy_version"),
        "target_taxonomy_version": taxonomy.get("version"),
        "live_ids": taxonomy.get("live_ids") is True,
        "cache_path": None if args.no_cache else str(Path(args.cache).expanduser()),
        "summary": {
            "source_rows": len(results),
            "known_individuals": sum(value for value in known if value is not None),
            "unknown_count_rows": sum(value is None for value in known),
            "cache_hits": sum(row.get("match_method") == "confirmed_cache" and row["status"] == "matched" for row in results),
            "unique_lookups": len(lookup_queue),
            **{status: counts[status] for status in ("matched", "needs_review", "unresolved", "deferred")},
        },
        "lookup_queue": list(lookup_queue.values()),
        "target_id_lookup": list(id_queue.values()),
        "ready_for_write": bool(results) and all(row.get("write_ready") for row in results),
        "rows": results,
    }
    write(args.output, output)
    if cache_changed and not args.no_cache:
        save_cache(args.cache, cache)
    print(json.dumps(output["summary"], ensure_ascii=False))
    print(f"ready_for_write={output['ready_for_write']} (no network or report writes performed)")


def remember(args):
    source = read_source(args)
    taxonomy = load(args.taxonomy)
    cache = read_cache(args.cache)
    decisions = load(args.decisions)
    for decision in decisions:
        index = decision.get("row_index")
        if type(index) is not int or not 0 <= index < len(source["observations"]):
            raise ValueError("row_index must select an existing source row, starting at zero")
        observation = source["observations"][index]
        evidence = decision.get("evidence")
        if not normalize(evidence):
            raise ValueError("Every decision needs actual verification or search evidence")
        if decision["status"] == "deferred":
            if not normalize(decision.get("reason")):
                raise ValueError("Deferred decisions need a reason")
            key = deferred_key(source, observation)
            cache["deferred"] = [entry for entry in cache["deferred"] if entry.get("key") != key] + [{
                "key": key, "reason": decision["reason"], "evidence": evidence,
                "target_taxonomy_version": taxonomy.get("version"),
                "checked_on": date.today().isoformat(),
            }]
        elif decision["status"] == "confirmed":
            relation = decision.get("relation")
            if (is_uncertain(observation) or relation not in CONFIRMED_RELATIONS
                    or observation.get("relation") in REVIEW_RELATIONS
                    or not observation.get("code") or not observation.get("scientific_name")
                    or not normalize(observation.get("identity_evidence"))):
                raise ValueError("Confirm only evidence-backed species identities with same_taxon/synonym relations")
            matches = [taxon for taxon in taxonomy["taxa"] if normalize(taxon["scientific_name"]) == normalize(decision.get("target_scientific_name"))]
            if len(matches) != 1:
                raise ValueError("Confirmed target scientific name must be unique in the catalog")
            remember_match(cache, source, observation, matches[0], relation, evidence)
        else:
            raise ValueError("Decision status must be confirmed or deferred")
    save_cache(args.cache, cache)
    print(f"Saved {len(decisions)} verified or deferred decisions to {args.cache}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    index = sub.add_parser("index", help="Convert official XLSX into an offline index")
    index.add_argument("xlsx")
    index.add_argument("--source-url", required=True)
    index.add_argument("--version", required=True)
    index.add_argument("--output", required=True)
    index.set_defaults(run=build_index)
    match = sub.add_parser("match", help="Prepare a plan; never submit records")
    match.add_argument("--candidates", default=ROOT / "references/mapping.json")
    match.add_argument("--no-cache", action="store_true", help="Disable persistent cache reads and writes")
    match.add_argument("--refresh", action="store_true", help="Ignore cached matches and deferred searches")
    match.add_argument("--cache-max-age-days", type=int, default=90)
    match.add_argument("--deferred-max-age-days", type=int, default=30)
    match.add_argument("--output", required=True)
    match.set_defaults(run=prepare)
    record = sub.add_parser("remember", help="Persist a batch of evidence-backed mapping decisions")
    record.add_argument("--decisions", required=True)
    record.set_defaults(run=remember)
    for command in (match, record):
        command.add_argument("source")
        command.add_argument("--taxonomy", default=ROOT / "assets/birdreport-taxonomy.json")
        command.add_argument("--source-taxonomy", help="Local official eBird taxonomy CSV; join all codes once")
        command.add_argument("--source-version", help="Actual eBird taxonomy version, if known")
        command.add_argument("--cache", default=DEFAULT_CACHE)
    args = parser.parse_args()
    args.run(args)


if __name__ == "__main__":
    main()
