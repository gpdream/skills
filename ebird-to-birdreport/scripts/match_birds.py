#!/usr/bin/env python3
"""Build an offline target index and prepare evidence-aware mapping plans."""

import argparse
from collections import Counter, defaultdict
from datetime import date
import json
from pathlib import Path
import re
import unicodedata
import xml.etree.ElementTree as ET
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


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


def prepare(args):
    source = load(args.source)
    taxonomy = load(args.taxonomy)
    by_scientific, by_name = defaultdict(list), defaultdict(list)
    for taxon in taxonomy["taxa"]:
        if normalize(taxon.get("scientific_name")):
            by_scientific[normalize(taxon["scientific_name"])].append(taxon)
        if normalize(taxon.get("name")):
            by_name[normalize(taxon["name"])].append(taxon)
    candidates = load(args.candidates)
    seeds = {row["code"]: row for row in candidates if row.get("code")}
    confirmed = defaultdict(list)
    cache_used = False
    if args.cache:
        cache = load(args.cache)
        source_version = source.get("source_taxonomy_version")
        target_version = taxonomy.get("version")
        cache_used = bool(
            normalize(source_version) and normalize(target_version)
            and cache.get("source_taxonomy_version") == source_version
            and cache.get("target_taxonomy_version") == target_version
        )
        if cache_used:
            for entry in cache.get("entries", []):
                if (entry.get("code") and normalize(entry.get("evidence"))
                        and entry.get("relation") in {"same_taxon", "synonym"}):
                    confirmed[entry["code"]].append(entry)
    results = []
    for observation in source["observations"]:
        result = {"source": observation, "status": "unresolved", "candidates": []}
        name = str(observation.get("name") or "")
        scientific = observation.get("scientific_name")
        category = observation.get("category", "species")
        uncertain = (
            observation.get("uncertain") or category != "species"
            or "/" in name or "／" in name
            or bool(re.search(r"\bsp\.|\bhybrid\b", str(scientific or ""), re.I))
        )
        if uncertain:
            result["reason"] = "Preserve uncertain or non-species source taxon."
            results.append(result)
            continue
        if observation.get("relation") in {"split", "lump", "geographic_inference"}:
            result["status"] = "needs_review"
            result["reason"] = "Taxonomic scope requires case-specific evidence."
            results.append(result)
            continue
        cached = confirmed.get(observation.get("code"), [])
        matches = []
        if scientific and normalize(observation.get("identity_evidence")):
            matches = by_scientific.get(normalize(scientific), [])
            result["reason"] = "Evidence-backed source scientific-name match."
        if not matches and len(cached) == 1:
            matches = by_scientific.get(
                normalize(cached[0].get("target_scientific_name")), []
            )
            result["reason"] = "Confirmed crosswalk with matching taxonomy versions."
        if len(matches) == 1:
            result["status"] = "matched"
            result["target"] = matches[0]
        else:
            suggestions = list(matches)
            seed = seeds.get(observation.get("code"), {})
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
            if len(cached) > 1:
                result["status"] = "needs_review"
                result["reason"] = "Conflicting confirmed cache entries."
        result["write_ready"] = bool(
            result["status"] == "matched"
            and taxonomy.get("live_ids") is True
            and result["target"].get("taxon_id")
            and number(observation.get("count")) is not None
        )
        results.append(result)
    counts = Counter(row["status"] for row in results)
    known = [number(row.get("count")) for row in source["observations"]]
    output = {
        "source_id": source.get("source_id"),
        "source_taxonomy_version": source.get("source_taxonomy_version"),
        "target_taxonomy_version": taxonomy.get("version"),
        "live_ids": taxonomy.get("live_ids") is True,
        "confirmed_cache_version_match": cache_used,
        "summary": {
            "source_rows": len(results),
            "known_individuals": sum(value for value in known if value is not None),
            "unknown_count_rows": sum(value is None for value in known),
            **{status: counts[status] for status in ("matched", "needs_review", "unresolved")},
        },
        "ready_for_write": bool(results) and all(row.get("write_ready") for row in results),
        "rows": results,
    }
    write(args.output, output)
    print(json.dumps(output["summary"], ensure_ascii=False))
    print(f"ready_for_write={output['ready_for_write']} (no network or report writes performed)")


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
    match.add_argument("source")
    match.add_argument("--taxonomy", default=ROOT / "assets/birdreport-taxonomy.json")
    match.add_argument("--candidates", default=ROOT / "references/mapping.json")
    match.add_argument("--cache")
    match.add_argument("--output", required=True)
    match.set_defaults(run=prepare)
    args = parser.parse_args()
    args.run(args)


if __name__ == "__main__":
    main()
