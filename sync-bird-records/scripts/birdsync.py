#!/usr/bin/env python3
"""Prepare, reconcile, and migrate personal BirdReport/eBird checklists.

Remote writes are opt-in.  Reverse imports default to a deterministic dry-run
plan and require ``--apply`` before the BirdReport account is changed.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import csv
import hashlib
import io
import json
import os
import re
import sys
import tempfile
import uuid
from getpass import getpass
from http.cookies import SimpleCookie
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse


SKILL_DIR = Path(__file__).resolve().parent.parent
DEFAULT_MAPPING = SKILL_DIR / "assets" / "ch4_to_eb_taxon_map.json"
SOURCE_NAME = "birdreport.cn"
TOOL_VERSION = "0.5.0"
DEFAULT_MAX_BYTES = 950_000
CONFIG_DIR = Path(os.environ.get("BIRDSYNC_CONFIG_DIR", Path.home() / ".config" / "sync-bird-records"))
CREDENTIALS_FILE = CONFIG_DIR / "credentials.json"
WEB_SESSIONS_FILE = CONFIG_DIR / "web-sessions.json"

SERVICE_GUIDES = {
    "birdreport": {
        "display_name": "中国观鸟记录中心",
        "login_url": "https://www.birdreport.cn/member/login.html",
        "account_url": "https://www.birdreport.cn/member/index.html",
        "token_header": "X-Auth-Token",
        "steps": [
            "由 Agent 启动隔离浏览器并通过宿主的安全 Web 预览交给用户。",
            "用户仅在可视化页面完成本人账号登录，不运行终端命令、不把密码或 token 发到聊天。",
            "用户点击完成后，登录会话和 X-Auth-Token 自动写入本机受保护目录；Agent 继续原请求。",
        ],
    },
    "ebird": {
        "display_name": "eBird",
        "login_url": "https://ebird.org/home",
        "api_key_url": "https://ebird.org/api/keygen",
        "download_url": "https://ebird.org/downloadMyData",
        "import_url": "https://ebird.org/import/upload.form",
        "steps": [
            "由 Agent 在同一个隔离浏览器中打开 eBird 登录页。",
            "用户只完成本人账号登录；不要要求其运行终端命令、手工下载文件或上传中间产物。",
            "Agent 复用浏览器会话完成个人数据下载、重复检查和已明确授权的导入操作。",
        ],
    },
}

DIRECTION_SERVICES = {
    "birdreport-to-ebird": ("birdreport", "ebird"),
    "ebird-to-birdreport": ("ebird", "birdreport"),
}

CSV_COLUMNS = [
    "Common Name",
    "Genus",
    "Species",
    "Species Count",
    "Species Comments",
    "Location name",
    "Latitude",
    "Longitude",
    "Observation date",
    "Start time",
    "State",
    "Country",
    "Protocol",
    "Number of observers",
    "Duration",
    "All observations reported?",
    "Distance covered",
    "Area covered",
    "Checklist Comments",
]

REGION_BY_NAME = {
    "安徽省": "CN-34", "北京市": "CN-11", "重庆市": "CN-50",
    "福建省": "CN-35", "甘肃省": "CN-62", "广东省": "CN-44",
    "广西壮族自治区": "CN-45", "贵州省": "CN-52", "海南省": "CN-46",
    "河北省": "CN-13", "黑龙江省": "CN-23", "河南省": "CN-41",
    "湖北省": "CN-42", "湖南省": "CN-43", "江苏省": "CN-32",
    "江西省": "CN-36", "吉林省": "CN-22", "辽宁省": "CN-21",
    "内蒙古自治区": "CN-15", "宁夏回族自治区": "CN-64", "青海省": "CN-63",
    "陕西省": "CN-61", "山东省": "CN-37", "上海市": "CN-31",
    "山西省": "CN-14", "四川省": "CN-51", "天津市": "CN-12",
    "新疆维吾尔自治区": "CN-65", "西藏自治区": "CN-54", "云南省": "CN-53",
    "浙江省": "CN-33", "澳门": "MO-", "澳门特别行政区": "MO-",
    "香港": "HK-", "香港特别行政区": "HK-",
    "彰化县": "TW-CHA", "嘉义市": "TW-CYI", "嘉义县": "TW-CYQ",
    "新竹市": "TW-HSZ", "新竹县": "TW-HSQ", "花莲县": "TW-HUA",
    "高雄市": "TW-KHH", "基隆市": "TW-KEE", "金门县": "TW-KIN",
    "连江县": "TW-LIE", "苗栗县": "TW-MIA", "南投县": "TW-NAN",
    "新北市": "TW-TPQ", "澎湖县": "TW-PEN", "屏东县": "TW-PIF",
    "台中市": "TW-TXG", "台南市": "TW-TNN", "台北市": "TW-TPE",
    "台东县": "TW-TTT", "桃园市": "TW-TAO", "宜兰县": "TW-ILA",
    "云林县": "TW-YUN",
}

PROVINCE_ABBR = {
    "北京市": "BJ", "天津市": "TJ", "上海市": "SH", "安徽省": "AH",
    "江苏省": "JS", "江西省": "JX", "浙江省": "ZJ", "福建省": "FJ",
    "广东省": "GD", "广西壮族自治区": "GX", "贵州省": "GZ", "海南省": "HAN",
    "河北省": "HEB", "河南省": "HEN", "陕西省": "SHX", "山东省": "SD",
    "山西省": "SX", "湖南省": "HUN", "湖北省": "HUB", "黑龙江省": "HLJ",
    "吉林省": "JL", "辽宁省": "LN", "甘肃省": "GS", "内蒙古自治区": "NMG",
    "宁夏回族自治区": "NX", "青海省": "QH", "四川省": "SC", "重庆市": "CQ",
    "新疆维吾尔自治区": "XJ", "西藏自治区": "XZ", "云南省": "YN",
    "台湾省": "TW", "香港特别行政区": "HK", "澳门": "MO",
}


class BirdSyncError(RuntimeError):
    pass


class AuthRequired(BirdSyncError):
    def __init__(self, service: str):
        self.service = service
        super().__init__(f"Authentication is required for {service}")


@dataclass
class Issue:
    severity: str
    code: str
    report_id: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {
            "severity": self.severity,
            "code": self.code,
            "report_id": self.report_id,
            "message": self.message,
        }


@dataclass
class ConvertedChecklist:
    report_id: str
    content_hash: str
    rows: list[list[Any]]
    issues: list[Issue] = field(default_factory=list)


def json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_json(path: Path, value: Any) -> None:
    atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8") + b"\n")


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise BirdSyncError(f"Cannot read JSON {path}: {exc}") from exc


def load_credentials() -> dict[str, str]:
    value = read_json(CREDENTIALS_FILE, default={})
    if not isinstance(value, dict):
        raise BirdSyncError(f"Invalid credentials file: {CREDENTIALS_FILE}")
    return {
        key: str(raw).strip()
        for key, raw in value.items()
        if key in {"birdreport_token", "ebird_token"} and str(raw).strip()
    }


def save_credentials(value: dict[str, str]) -> None:
    CONFIG_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(CONFIG_DIR, 0o700)
    atomic_write(CREDENTIALS_FILE, json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8") + b"\n")
    os.chmod(CREDENTIALS_FILE, 0o600)


def credential_key(service: str) -> tuple[str, str]:
    if service == "birdreport":
        return "BIRDREPORT_TOKEN", "birdreport_token"
    if service == "ebird":
        return "EBIRD_TOKEN", "ebird_token"
    raise BirdSyncError(f"Unknown service: {service}")


def get_service_token(service: str) -> tuple[str, str]:
    env_name, file_key = credential_key(service)
    environment_value = "".join(os.environ.get(env_name, "").split())
    if environment_value:
        return environment_value, "environment"
    file_value = "".join(load_credentials().get(file_key, "").split())
    if file_value:
        return file_value, str(CREDENTIALS_FILE)
    return "", "missing"


def auth_guide(service: str) -> dict[str, Any]:
    if service not in SERVICE_GUIDES:
        raise BirdSyncError(f"Unknown service: {service}")
    token, source = get_service_token(service)
    web_session = load_web_session(service) if service == "ebird" else {}
    guide = dict(SERVICE_GUIDES[service])
    setup_command = f"{Path(__file__).resolve().parent / 'birdsync'} auth set {service}"
    if service == "birdreport":
        user_message = (
            "需要先登录中国观鸟记录中心。Agent 应启动 `scripts/birdsync-auth-browser --service birdreport`，"
            "再把本地端口接到当前宿主的安全 Web 预览。用户只需在可视化页面完成本人账号登录并点击“我已完成登录”；"
            "不要要求用户运行终端命令，也不要让用户在聊天中粘贴密码或 token。登录完成后 Agent 验证凭证并自动恢复请求。"
        )
    else:
        user_message = (
            "需要先登录 eBird。Agent 应启动 `scripts/birdsync-auth-browser --service ebird` 并通过宿主的安全 Web 预览交给用户；"
            "用户只需在可视化页面完成本人账号登录并点击“我已完成登录”。Agent 随后复用同一浏览器会话完成个人数据下载、"
            "重复检查和用户已明确请求的导入；不要要求用户运行终端命令或搬运中间文件。API key 只用于公开查询，不代表写入授权。"
        )
    configured = bool(web_session) if service == "ebird" else bool(token)
    guide.update({
        "status": "configured" if configured else "auth_required",
        "service": service,
        "credential_source": str(WEB_SESSIONS_FILE) if web_session else source,
        "web_session": "available" if web_session else "not_set",
        "secure_setup_command": setup_command,
        "credential_file": str(CREDENTIALS_FILE),
        "credential_precedence": "Environment variables override the protected credential file.",
        "security_note": "Prefer the isolated visual login browser. Never paste tokens or passwords into chat; terminal setup is a fallback for expert users only.",
        "browser_probe_command": f"{Path(__file__).resolve().parent / 'birdsync-auth-browser'} --service {service} --probe",
        "browser_handoff_command": f"{Path(__file__).resolve().parent / 'birdsync-auth-browser'} --service {service} --port <free-port>",
        "user_message_markdown": user_message,
    })
    return guide


def set_credential(service: str) -> dict[str, Any]:
    env_name, file_key = credential_key(service)
    if not sys.stdin.isatty():
        raise BirdSyncError(
            "Secure credential input needs a local interactive terminal. "
            f"Run: {Path(__file__).resolve().parent / 'birdsync'} auth set {service}"
        )
    value = "".join(getpass(f"Paste {service} token (input hidden): ").split())
    if not value:
        raise BirdSyncError("No token entered; credentials were not changed")
    credentials = load_credentials()
    credentials[file_key] = value
    save_credentials(credentials)
    return {
        "status": "saved_unverified",
        "service": service,
        "credential_file": str(CREDENTIALS_FILE),
        "environment_override_present": bool(os.environ.get(env_name)),
        "environment_note": (
            f"Unset or replace {env_name}; environment variables override this file."
            if os.environ.get(env_name) else None
        ),
        "next_command": f"{Path(__file__).resolve().parent / 'birdsync'} auth status {service} --validate",
    }


def clear_credential(service: str) -> dict[str, Any]:
    env_name, file_key = credential_key(service)
    credentials = load_credentials()
    removed = file_key in credentials
    credentials.pop(file_key, None)
    save_credentials(credentials)
    return {
        "status": "cleared",
        "service": service,
        "removed": removed,
        "environment_override_still_present": bool(os.environ.get(env_name)),
    }


def set_web_session(service: str) -> dict[str, Any]:
    if service != "ebird":
        raise BirdSyncError("Browser-session storage currently supports eBird only")
    if not sys.stdin.isatty():
        raise BirdSyncError("Secure session input needs an interactive hidden-input channel")
    raw = getpass("Paste eBird Cookie header (input hidden): ").strip()
    parsed = SimpleCookie()
    try:
        parsed.load(raw)
    except Exception as exc:
        raise BirdSyncError("Could not parse the Cookie header") from exc
    session_id = parsed.get("EBIRD_SESSIONID")
    if session_id is None or not session_id.value.strip():
        raise BirdSyncError("Cookie header does not contain EBIRD_SESSIONID")
    sessions = read_json(WEB_SESSIONS_FILE, default={})
    if not isinstance(sessions, dict):
        sessions = {}
    sessions[service] = {
        "cookies": {"EBIRD_SESSIONID": session_id.value.strip()},
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    CONFIG_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(CONFIG_DIR, 0o700)
    atomic_write(WEB_SESSIONS_FILE, json.dumps(sessions, ensure_ascii=False, indent=2).encode("utf-8") + b"\n")
    os.chmod(WEB_SESSIONS_FILE, 0o600)
    return {
        "status": "saved_unverified",
        "service": service,
        "stored_cookie_names": ["EBIRD_SESSIONID"],
        "discarded_nonessential_cookies": max(0, len(parsed) - 1),
        "session_file": str(WEB_SESSIONS_FILE),
    }


def load_web_session(service: str) -> dict[str, str]:
    sessions = read_json(WEB_SESSIONS_FILE, default={})
    if not isinstance(sessions, dict):
        return {}
    entry = sessions.get(service, {})
    cookies = entry.get("cookies", {}) if isinstance(entry, dict) else {}
    return {str(key): str(value) for key, value in cookies.items() if str(value)} if isinstance(cookies, dict) else {}


def load_reports(path: Path) -> list[dict[str, Any]]:
    value = read_json(path)
    if isinstance(value, dict):
        value = value.get("reports")
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise BirdSyncError("Input must be a JSON report array or an object with a reports array")
    return value


def report_id(report: dict[str, Any]) -> str:
    raw = report.get("serial_id") or report.get("id")
    if raw is None or str(raw).strip() == "":
        return "anonymous-" + sha256_bytes(json_bytes(report))[:12]
    return str(raw)


def report_hash(report: dict[str, Any]) -> str:
    return sha256_bytes(json_bytes(report))


def parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            pass
    return None


def condition_location(value: str) -> str:
    parts = value.split("-", 1)
    province_name = next((name for name, abbr in PROVINCE_ABBR.items() if abbr == parts[0]), parts[0])
    return province_name if len(parts) == 1 else f"{province_name}-{parts[1]}"


def apply_taxonomy(
    scientific_name: str,
    report: dict[str, Any],
    mapping: dict[str, Any],
) -> tuple[str, bool]:
    rule = mapping.get(scientific_name)
    if rule is None:
        return scientific_name, False
    if isinstance(rule, str):
        return rule, rule != scientific_name
    if not isinstance(rule, list):
        return scientific_name, False

    province = str(report.get("province_name") or "")
    city = str(report.get("city_name") or "")
    start = parse_time(report.get("start_time"))
    for choice in rule:
        if not isinstance(choice, dict) or not isinstance(choice.get("name"), str):
            continue
        if "loc" in choice:
            allowed = {condition_location(str(item)) for item in choice.get("loc", [])}
            if province not in allowed and f"{province}-{city}" not in allowed:
                continue
        if "time" in choice:
            if start is None:
                continue
            allowed_months: set[int] = set()
            for part in str(choice["time"]).split(","):
                if "-" in part:
                    low, high = part.split("-", 1)
                    allowed_months.update(range(int(low), int(high) + 1))
                elif part.strip().isdigit():
                    allowed_months.add(int(part))
            if start.month not in allowed_months:
                continue
        return choice["name"], choice["name"] != scientific_name
    return scientific_name, False


def region_for(report: dict[str, Any]) -> str | None:
    province = str(report.get("province_name") or "")
    city = str(report.get("city_name") or "")
    district = str(report.get("district_name") or "")
    if province == "台湾省":
        return REGION_BY_NAME.get(city) or REGION_BY_NAME.get(district)
    return REGION_BY_NAME.get(province)


def country_and_state(region: str, report: dict[str, Any]) -> tuple[str, str]:
    country = region.split("-", 1)[0]
    if country == "CN":
        province = str(report.get("province_name") or "")
        state = PROVINCE_ABBR.get(province, "")
    else:
        state = region.split("-", 1)[1] if "-" in region else ""
    if not 1 <= len(state) <= 3:
        raise BirdSyncError(f"Region {region} cannot be represented by eBird's 1-3 character State field")
    return country, state


def location_for(report: dict[str, Any], overrides: dict[str, Any]) -> tuple[str, Any, Any]:
    by_id = overrides.get("by_point_id", {}) if isinstance(overrides, dict) else {}
    by_name = overrides.get("by_name", {}) if isinstance(overrides, dict) else {}
    point_id = str(report.get("point_id") or "")
    source_name = str(report.get("point_name") or "未命名个人地点")
    choice = by_id.get(point_id) if point_id else None
    if not isinstance(choice, dict):
        choice = by_name.get(source_name)
    if not isinstance(choice, dict):
        choice = {}
    name = str(choice.get("name") or source_name)
    latitude = choice.get("latitude", choice.get("lat", report.get("lat", report.get("latitude", ""))))
    longitude = choice.get("longitude", choice.get("lng", report.get("lng", report.get("longitude", ""))))
    return name, latitude, longitude


def issue(severity: str, code: str, rid: str, message: str) -> Issue:
    return Issue(severity=severity, code=code, report_id=rid, message=message)


def convert_report(
    report: dict[str, Any],
    mapping: dict[str, Any],
    overrides: dict[str, Any],
) -> ConvertedChecklist:
    rid = report_id(report)
    digest = report_hash(report)
    issues: list[Issue] = []
    start = parse_time(report.get("start_time"))
    end = parse_time(report.get("end_time"))
    if start is None:
        issues.append(issue("blocker", "invalid_time", rid, "Missing or invalid start_time"))
    if end is None:
        issues.append(issue("blocker", "invalid_time", rid, "Missing or invalid end_time"))

    region = region_for(report)
    if region is None:
        issues.append(issue("blocker", "unmapped_region", rid, "Province/city cannot be mapped to an eBird region"))

    observations = report.get("obs")
    if not isinstance(observations, list) or not observations:
        issues.append(issue("blocker", "no_observations", rid, "No observations are available"))

    if any(item.severity == "blocker" for item in issues):
        return ConvertedChecklist(rid, digest, [], issues)

    assert start is not None and end is not None and region is not None and isinstance(observations, list)
    duration = int((end - start).total_seconds() // 60)
    if duration < 1 or duration > 1440:
        issues.append(issue("warning", "duration_clamped", rid, f"Duration {duration} minutes was clamped to the eBird-safe range"))
        duration = min(max(duration, 1), 1440)

    protocol = str(report.get("protocol") or "historical")
    if "protocol" not in report:
        issues.append(issue("warning", "assumed_protocol", rid, "Protocol defaulted to historical"))

    raw_observers = report.get("number_of_observers", report.get("observer_count"))
    try:
        observers = int(raw_observers)
        if observers < 1:
            raise ValueError
    except (TypeError, ValueError):
        observers = 1
        issues.append(issue("warning", "assumed_observers", rid, "Number of observers defaulted to 1"))

    if "eye_all_birds" in report:
        complete = "Y" if str(report.get("eye_all_birds") or "").strip() else "N"
    else:
        complete = "Y"
        issues.append(issue("warning", "assumed_complete", rid, "Checklist completeness defaulted to Y"))

    if "real_quantity" in report:
        exact_counts = report.get("real_quantity") == 1
    else:
        valid_observations = [item for item in observations if isinstance(item, dict)]
        exact_counts = not valid_observations or not all(item.get("taxon_count") == 1 for item in valid_observations)
        if not exact_counts:
            issues.append(issue("warning", "inferred_counts", rid, "All unit counts were interpreted as non-exact X"))

    location_name, latitude, longitude = location_for(report, overrides)
    if latitude in (None, "") or longitude in (None, ""):
        issues.append(issue("warning", "missing_coordinates", rid, f"Location {location_name!r} has no complete coordinates"))

    try:
        country, state = country_and_state(region, report)
    except BirdSyncError as exc:
        issues.append(issue("blocker", "unmapped_import_state", rid, str(exc)))
        return ConvertedChecklist(rid, digest, [], issues)
    checklist_note = str(report.get("note") or "").replace("\r\n", "\n").replace("\r", "\n")
    source_note = f"Converted from BirdReport CN, report ID: {rid}"
    checklist_note = f"{checklist_note}\\n{source_note}" if checklist_note else source_note

    rows: list[list[Any]] = []
    for observation in observations:
        if not isinstance(observation, dict):
            issues.append(issue("warning", "invalid_observation", rid, "Ignored a non-object observation"))
            continue
        original_name = str(observation.get("latinname") or "").strip()
        if not original_name:
            issues.append(issue("warning", "missing_taxon", rid, "Ignored an observation without a scientific name"))
            continue
        scientific_name, changed = apply_taxonomy(original_name, report, mapping)
        if changed:
            issues.append(issue("warning", "taxonomy_changed", rid, f"Mapped {original_name} -> {scientific_name}"))
        parts = scientific_name.split()
        genus = parts[0]
        species = " ".join(parts[1:])
        count: Any = observation.get("taxon_count", "") if exact_counts else "X"
        species_note = str(observation.get("note") or "").replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n")
        rows.append([
            "", genus, species, count, species_note, location_name, latitude, longitude,
            start.strftime("%m/%d/%Y"), start.strftime("%H:%M"), state, country,
            protocol, observers, duration, complete, "", "", checklist_note,
        ])

    if not rows:
        issues.append(issue("blocker", "no_usable_observations", rid, "No observations with scientific names remain"))
    return ConvertedChecklist(rid, digest, rows, issues)


def encode_rows(rows: Iterable[list[Any]]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


def load_ledger(path: Path) -> dict[str, Any]:
    ledger = read_json(path, default={"version": 1, "records": {}})
    if not isinstance(ledger, dict) or not isinstance(ledger.get("records"), dict):
        raise BirdSyncError(f"Invalid ledger: {path}")
    return ledger


REMOTE_PRESENT_STATUSES = {"imported", "existing_remote"}


def build_review_markdown(summary: dict[str, Any], issues: list[Issue]) -> str:
    counts = Counter(item.severity for item in issues)
    lines = [
        "# Bird record import review",
        "",
        f"- Status: **{summary['status']}**",
        f"- Input checklists: {summary['input_reports']}",
        f"- Prepared checklists: {summary['prepared_reports']}",
        f"- Skipped checklists: {summary['skipped_reports']}",
        f"- CSV rows: {summary['csv_rows']}",
        f"- CSV files: {summary['csv_files']}",
        f"- Blockers: {counts['blocker']}",
        f"- Warnings: {counts['warning']}",
        "",
    ]
    if issues:
        lines.extend(["## Items to review", ""])
        for item in issues:
            lines.append(f"- **{item.severity} / {item.code} / {item.report_id}**: {item.message}")
        lines.append("")
    else:
        lines.extend(["No unresolved items were found.", ""])
    lines.extend(["## Next step", ""])
    if summary["prepared_reports"]:
        lines.extend([
            "Review the warnings, then let the Agent reconcile duplicates and import the `ebird-import-*.csv` files through eBird.",
            "Only an eBird acceptance receipt may be marked imported; a matching pre-existing checklist is marked existing_remote.",
        ])
    elif summary.get("already_remote"):
        lines.append("No upload is needed: every eligible checklist already has matching remote evidence.")
    else:
        lines.append("No checklist is ready for upload; resolve the blockers shown above.")
    lines.append("")
    return "\n".join(lines)


def prepare(args: argparse.Namespace) -> dict[str, Any]:
    input_path = Path(args.input).resolve()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    reports = load_reports(input_path)
    mapping = read_json(Path(args.mapping), default={})
    if not isinstance(mapping, dict):
        raise BirdSyncError("Taxonomy mapping must be a JSON object")
    overrides = read_json(Path(args.location_overrides), default={}) if args.location_overrides else {}
    ledger_path = Path(args.ledger).resolve() if args.ledger else output_dir / "ledger.json"
    ledger = load_ledger(ledger_path)

    converted: list[ConvertedChecklist] = []
    all_issues: list[Issue] = []
    skipped_imported: list[str] = []
    skipped_existing: list[str] = []
    conflicts: list[str] = []
    for report in sorted(reports, key=lambda item: (str(item.get("start_time") or ""), report_id(item))):
        rid = report_id(report)
        digest = report_hash(report)
        old = ledger["records"].get(rid)
        old_status = old.get("status") if isinstance(old, dict) else None
        if isinstance(old, dict) and old_status in REMOTE_PRESENT_STATUSES and not args.include_imported:
            if old.get("content_hash") == digest:
                if old_status == "existing_remote":
                    skipped_existing.append(rid)
                    remote_id = str(old.get("remote_id") or "the reconciled remote checklist")
                    all_issues.append(issue("info", "already_exists_remote", rid, f"Skipped because the same content matches {remote_id}"))
                else:
                    skipped_imported.append(rid)
                    all_issues.append(issue("info", "already_imported", rid, "Skipped because the same content is marked imported"))
            else:
                conflicts.append(rid)
                all_issues.append(issue("blocker", "remote_record_changed", rid, "A remotely reconciled source record changed; manual reconciliation is required"))
            continue
        item = convert_report(report, mapping, overrides)
        converted.append(item)
        all_issues.extend(item.issues)

    prepared = [item for item in converted if item.rows and not any(x.severity == "blocker" for x in item.issues)]
    checklist_payloads = [(item, encode_rows(item.rows)) for item in prepared]
    chunks: list[tuple[list[ConvertedChecklist], bytes]] = []
    current_items: list[ConvertedChecklist] = []
    current_payload = b""
    for item, payload in checklist_payloads:
        if len(payload) > args.max_bytes:
            all_issues.append(issue("blocker", "checklist_too_large", item.report_id, f"One checklist is {len(payload)} bytes"))
            continue
        if current_payload and len(current_payload) + len(payload) > args.max_bytes:
            chunks.append((current_items, current_payload))
            current_items, current_payload = [], b""
        current_items.append(item)
        current_payload += payload
    if current_payload:
        chunks.append((current_items, current_payload))

    written_items = [item for items, _ in chunks for item in items]

    for old_file in output_dir.glob("ebird-import-*.csv"):
        old_file.unlink()

    files: list[dict[str, Any]] = []
    for index, (items, payload) in enumerate(chunks, start=1):
        path = output_dir / f"ebird-import-{index:03d}.csv"
        atomic_write(path, payload)
        files.append({
            "path": path.name,
            "bytes": len(payload),
            "sha256": sha256_bytes(payload),
            "rows": sum(len(item.rows) for item in items),
            "report_ids": [item.report_id for item in items],
        })

    blockers = sum(item.severity == "blocker" for item in all_issues)
    warnings = sum(item.severity == "warning" for item in all_issues)
    if blockers or warnings:
        result_status = "review_required"
    elif written_items:
        result_status = "ready_for_import"
    elif skipped_imported or skipped_existing:
        result_status = "already_reconciled"
    else:
        result_status = "no_eligible_records"
    summary = {
        "status": result_status,
        "input_reports": len(reports),
        "prepared_reports": len(written_items),
        "skipped_reports": len(reports) - len(written_items),
        "csv_rows": sum(item["rows"] for item in files),
        "csv_files": len(files),
        "already_imported": len(skipped_imported),
        "already_remote": len(skipped_imported) + len(skipped_existing),
        "existing_remote": len(skipped_existing),
        "conflicts": len(conflicts),
    }
    mapping_payload = Path(args.mapping).read_bytes()
    manifest = {
        "schema_version": 1,
        "tool_version": TOOL_VERSION,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source_system": SOURCE_NAME,
        "source_input": str(input_path),
        "source_sha256": sha256_bytes(input_path.read_bytes()),
        "mapping_sha256": sha256_bytes(mapping_payload),
        "csv_columns": CSV_COLUMNS,
        "summary": summary,
        "records": [
            {"source_id": item.report_id, "content_hash": item.content_hash}
            for item in written_items
        ],
        "files": files,
    }
    review = {"summary": summary, "issues": [item.as_dict() for item in all_issues]}
    write_json(output_dir / "manifest.json", manifest)
    write_json(output_dir / "review.json", review)
    atomic_write(output_dir / "review.md", build_review_markdown(summary, all_issues).encode("utf-8"))

    now = datetime.now().astimezone().isoformat(timespec="seconds")
    for item in written_items:
        prior = ledger["records"].get(item.report_id, {})
        if prior.get("status") not in REMOTE_PRESENT_STATUSES:
            ledger["records"][item.report_id] = {
                "status": "prepared",
                "content_hash": item.content_hash,
                "updated_at": now,
                "manifest": str(output_dir / "manifest.json"),
            }
    write_json(ledger_path, ledger)
    return {**summary, "output_dir": str(output_dir), "review": str(output_dir / "review.md")}


def mark_imported(args: argparse.Namespace) -> dict[str, Any]:
    manifest_path = Path(args.manifest).resolve()
    manifest = read_json(manifest_path)
    if not isinstance(manifest, dict) or not isinstance(manifest.get("records"), list):
        raise BirdSyncError("Invalid manifest")
    for file_info in manifest.get("files", []):
        if not isinstance(file_info, dict) or not file_info.get("path") or not file_info.get("sha256"):
            raise BirdSyncError("Manifest contains invalid file metadata")
        csv_path = manifest_path.parent / file_info["path"]
        if not csv_path.is_file():
            raise BirdSyncError(f"Cannot mark imported: missing CSV {csv_path.name}")
        if sha256_bytes(csv_path.read_bytes()) != file_info["sha256"]:
            raise BirdSyncError(f"Cannot mark imported: CSV changed after preparation: {csv_path.name}")
    ledger_path = Path(args.ledger).resolve() if args.ledger else manifest_path.parent / "ledger.json"
    ledger = load_ledger(ledger_path)
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    remote_id = str(getattr(args, "remote_id", None) or "")
    remote_service = str(getattr(args, "remote_service", None) or "")
    evidence_url = str(getattr(args, "evidence_url", None) or "")
    acceptance_path = Path(args.acceptance_receipt).resolve() if getattr(args, "acceptance_receipt", None) else None
    batch_id = str(getattr(args, "batch_id", None) or "")
    if any((remote_id, remote_service, evidence_url)) and not all((remote_id, remote_service, evidence_url)):
        raise BirdSyncError("--remote-service, --remote-id, and --evidence-url must be supplied together")
    if remote_id:
        validate_remote_evidence(remote_service, remote_id, evidence_url)
    if acceptance_path is not None and not acceptance_path.is_file():
        raise BirdSyncError(f"Acceptance receipt does not exist: {acceptance_path}")
    count = 0
    for record in manifest["records"]:
        rid = str(record.get("source_id") or "")
        digest = str(record.get("content_hash") or "")
        if not rid or not digest:
            continue
        prior = ledger["records"].get(rid)
        entry = {
            "status": "imported",
            "content_hash": digest,
            "updated_at": now,
            "manifest": str(manifest_path),
        }
        if remote_id:
            structured_receipt = manifest_path.parent / f"import-receipt-{sha256_bytes(rid.encode('utf-8'))[:12]}.json"
            receipt = {
                "schema_version": 1,
                "decision": "imported",
                "imported_at": now,
                "source_system": str(manifest.get("source_system") or SOURCE_NAME),
                "source_id": rid,
                "content_hash": digest,
                "remote_service": remote_service,
                "remote_id": remote_id,
                "evidence_url": evidence_url,
                "batch_id": batch_id or None,
                "acceptance_receipt": str(acceptance_path) if acceptance_path else None,
                "acceptance_receipt_sha256": sha256_bytes(acceptance_path.read_bytes()) if acceptance_path else None,
            }
            write_json(structured_receipt, receipt)
            entry.update({
                "remote_service": remote_service,
                "remote_id": remote_id,
                "evidence": {"url": evidence_url, "scope": "authenticated_personal_account"},
                "batch_id": batch_id or None,
                "receipt": str(structured_receipt),
            })
        if isinstance(prior, dict) and prior.get("status") in REMOTE_PRESENT_STATUSES:
            entry["prior_remote_reconciliation"] = {
                key: prior.get(key)
                for key in ("status", "remote_service", "remote_id", "evidence", "receipt", "updated_at")
                if prior.get(key) is not None
            }
        ledger["records"][rid] = entry
        count += 1
    write_json(ledger_path, ledger)
    return {
        "status": "ok",
        "marked_imported": count,
        "remote_service": remote_service or None,
        "remote_id": remote_id or None,
        "ledger": str(ledger_path),
    }


def validate_remote_evidence(service: str, remote_id: str, evidence_url: str) -> None:
    if service != "ebird":
        raise BirdSyncError(f"Unsupported remote service: {service}")
    if not re.fullmatch(r"S\d+", remote_id):
        raise BirdSyncError("eBird remote ID must look like S123456789")
    parsed = urlparse(evidence_url)
    if parsed.scheme != "https" or parsed.hostname not in {"ebird.org", "www.ebird.org"}:
        raise BirdSyncError("eBird evidence URL must use https://ebird.org")
    if parsed.path.rstrip("/") != f"/checklist/{remote_id}":
        raise BirdSyncError("Evidence URL checklist ID does not match --remote-id")


def mark_existing(args: argparse.Namespace) -> dict[str, Any]:
    """Record a conservative duplicate decision without claiming an import."""
    manifest_path = Path(args.manifest).resolve()
    manifest = read_json(manifest_path)
    if not isinstance(manifest, dict) or not isinstance(manifest.get("records"), list):
        raise BirdSyncError("Invalid manifest")
    matching = [
        item for item in manifest["records"]
        if isinstance(item, dict) and str(item.get("source_id") or "") == args.source_id
    ]
    if len(matching) != 1:
        raise BirdSyncError(f"Manifest must contain source ID {args.source_id} exactly once")
    digest = str(matching[0].get("content_hash") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise BirdSyncError("Manifest record has an invalid content hash")
    validate_remote_evidence(args.remote_service, args.remote_id, args.evidence_url)
    matched_fields = list(dict.fromkeys(args.matched_on or []))
    if not matched_fields:
        raise BirdSyncError("At least one --matched-on field is required")

    ledger_path = Path(args.ledger).resolve() if args.ledger else manifest_path.parent / "ledger.json"
    ledger = load_ledger(ledger_path)
    prior = ledger["records"].get(args.source_id)
    if isinstance(prior, dict):
        prior_hash = str(prior.get("content_hash") or "")
        if prior_hash and prior_hash != digest:
            raise BirdSyncError("Ledger content hash differs from the manifest; refusing to reconcile")
        if prior.get("status") == "imported":
            raise BirdSyncError("Record is already marked imported; refusing to downgrade it to existing_remote")

    reconciled_at = datetime.now().astimezone().isoformat(timespec="seconds")
    receipt_path = manifest_path.parent / f"reconciliation-{sha256_bytes(args.source_id.encode('utf-8'))[:12]}.json"
    receipt = {
        "schema_version": 1,
        "decision": "existing_remote",
        "reconciled_at": reconciled_at,
        "source_system": str(manifest.get("source_system") or SOURCE_NAME),
        "source_id": args.source_id,
        "content_hash": digest,
        "remote_service": args.remote_service,
        "remote_id": args.remote_id,
        "evidence": {
            "url": args.evidence_url,
            "scope": "authenticated_personal_account",
            "matched_fields": matched_fields,
        },
    }
    write_json(receipt_path, receipt)
    ledger["records"][args.source_id] = {
        "status": "existing_remote",
        "content_hash": digest,
        "updated_at": reconciled_at,
        "manifest": str(manifest_path),
        "receipt": str(receipt_path),
        "remote_service": args.remote_service,
        "remote_id": args.remote_id,
        "evidence": {
            "url": args.evidence_url,
            "scope": "authenticated_personal_account",
            "matched_fields": matched_fields,
        },
    }
    write_json(ledger_path, ledger)
    return {
        "status": "ok",
        "marked_existing_remote": 1,
        "source_id": args.source_id,
        "remote_service": args.remote_service,
        "remote_id": args.remote_id,
        "matched_fields": matched_fields,
        "ledger": str(ledger_path),
        "receipt": str(receipt_path),
    }


class BirdReportClient:
    """Minimal read-only adapter for the BirdReport web backend."""

    BASE = "https://api.birdreport.cn"
    AES_KEY = b"C8EB5514AF5ADDB94B2207B08C66601C"
    AES_IV = b"55DD79C6F04E1A67"

    def __init__(self, token: str):
        try:
            import httpx
            from Crypto.Cipher import AES
            from Crypto.Util.Padding import unpad
        except ImportError as exc:
            raise BirdSyncError("Live fetch requires httpx and pycryptodome; use scripts/birdsync launcher") from exc
        self.httpx = httpx
        self.AES = AES
        self.unpad = unpad
        self.token = "".join(token.split())
        if not self.token:
            raise BirdSyncError("BIRDREPORT_TOKEN is empty")

    def headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Content-Type": "application/json",
            "Origin": "https://www.birdreport.cn",
            "Referer": "https://www.birdreport.cn/",
            "User-Agent": "sync-bird-records/0.1 read-only adapter",
            "X-Auth-Token": self.token,
        }

    def decrypt(self, value: str) -> Any:
        cipher = self.AES.new(self.AES_KEY, self.AES.MODE_CBC, iv=self.AES_IV)
        decoded = base64.b64decode(value)
        plain = self.unpad(cipher.decrypt(decoded), self.AES.block_size)
        return json.loads(plain.decode("utf-8"))

    async def post(self, path: str, payload: dict[str, Any], decrypt: bool) -> Any:
        result = await self.post_envelope(path, payload)
        return self.decrypt(result["data"]) if decrypt else result.get("data", result)

    async def post_envelope(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        """POST JSON and retain the complete BirdReport response envelope."""
        compact = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                async with self.httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                    response = await client.post(self.BASE + path, headers=self.headers(), content=compact.encode("utf-8"))
                if response.status_code in (401, 403):
                    raise AuthRequired("birdreport")
                response.raise_for_status()
                result = response.json()
                code = result.get("code") if isinstance(result, dict) else None
                try:
                    failed = code is not None and int(code) < 0
                except (TypeError, ValueError):
                    failed = code not in (None, 0, "0")
                if failed:
                    raise BirdSyncError(f"BirdReport API error: {result.get('msg') or result.get('code')}")
                if not isinstance(result, dict):
                    raise BirdSyncError(f"Unexpected response from {path}")
                return result
            except BirdSyncError:
                raise
            except Exception as exc:  # bounded retry for network/contract errors
                last_error = exc
                if attempt < 2:
                    await asyncio.sleep(1 + attempt)
        raise BirdSyncError(f"BirdReport request failed after 3 attempts: {last_error}")

    async def paged(self, path: str, base: dict[str, Any], max_pages: int = 1000) -> list[dict[str, Any]]:
        collected: list[dict[str, Any]] = []
        limit = 200
        for page in range(1, max_pages + 1):
            payload = {**base, "page": str(page), "limit": str(limit)}
            result = await self.post(path, payload, decrypt=True)
            if not isinstance(result, list):
                raise BirdSyncError(f"Unexpected paged response from {path}")
            collected.extend(item for item in result if isinstance(item, dict))
            if len(result) < limit:
                return collected
        raise BirdSyncError(f"Pagination exceeded {max_pages} pages at {path}")

    async def export_rows(self, path: str, ids: list[Any]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for start in range(0, len(ids), 200):
            result = await self.post(path, {"ids": ",".join(str(x) for x in ids[start:start + 200])}, decrypt=False)
            if not isinstance(result, list):
                raise BirdSyncError(f"Unexpected export response from {path}")
            rows.extend(item for item in result if isinstance(item, dict))
        return rows

    async def fetch_reports(self, start_date: str, end_date: str, location: str | None = None) -> list[dict[str, Any]]:
        common = {"start_date": start_date, "start": start_date, "end_date": end_date, "end": end_date, "serial_id": "", "taxon_id": ""}
        point = await self.paged("/member/system/activity/search", {**common, "point_name": location or "", "state": ""})
        point_by_id = {str(item["id"]): item for item in point if "id" in item}
        for row in await self.export_rows("/member/system/record/excel", list(point_by_id)):
            target = point_by_id.get(str(row.get("activity_id")))
            if target is not None:
                target.setdefault("obs", []).append(row)

        handy = await self.paged("/member/system/handy/report/search", common)
        handy_by_id = {str(item["id"]): item for item in handy if "id" in item}
        for row in await self.export_rows("/member/system/handy/record/excel", list(handy_by_id)):
            target = handy_by_id.get(str(row.get("activity_id")))
            if target is not None:
                target.setdefault("obs", []).append(row)
        for checklist in handy_by_id.values():
            observations = checklist.get("obs") or []
            if observations:
                first = observations[0]
                checklist.setdefault("city_name", first.get("city_name", ""))
                checklist.setdefault("district_name", first.get("district_name", ""))
                checklist.setdefault("latitude", first.get("latitude", ""))
                checklist.setdefault("longitude", first.get("longitude", ""))
                checklist.setdefault("point_name", f"随手记地点-{checklist.get('start_time', '')}")
        reports = list(point_by_id.values()) + list(handy_by_id.values())
        if location:
            needle = location.casefold()
            reports = [item for item in reports if needle in str(item.get("point_name") or "").casefold()]
        return reports

    async def validate(self) -> dict[str, Any]:
        result = await self.post("/member/system/user/get", {}, decrypt=False)
        if not isinstance(result, dict):
            raise BirdSyncError("BirdReport validation returned an unexpected response")
        return {
            "username": str(result.get("username") or ""),
            "user_id": str(result.get("id") or result.get("user_id") or ""),
        }


def normalize_ebird_common_name(value: str) -> list[str]:
    value = " ".join(value.split()).strip()
    primary = re.sub(r"\s*[（(][^）)]*[）)]\s*$", "", value).strip()
    choices = [primary]
    match = re.search(r"[（(]([^）)]+)[）)]\s*$", value)
    if match:
        choices.append(match.group(1).strip())
    return list(dict.fromkeys(choice for choice in choices if choice))


def parse_ebird_checklist_html(path: Path) -> dict[str, Any]:
    try:
        from bs4 import BeautifulSoup
    except ImportError as exc:
        raise BirdSyncError("eBird HTML import requires beautifulsoup4; use scripts/birdsync launcher") from exc
    if not path.is_file():
        raise BirdSyncError(f"eBird checklist HTML does not exist: {path}")
    soup = BeautifulSoup(path.read_text(encoding="utf-8", errors="replace"), "html.parser")
    heading = soup.select_one("h1#content")
    source_match = re.search(r"\b(S\d+)\b", heading.get_text(" ", strip=True) if heading else "")
    time_node = soup.select_one('time[datetime^="20"]')
    hotspot = soup.select_one('a[href^="/hotspot/"]')
    map_link = soup.select_one('a[href*="google.com/maps/search"][href*="query="]')
    if not source_match or not time_node or not hotspot or not map_link:
        raise BirdSyncError("The input is not a complete authenticated eBird checklist page")
    start = datetime.fromisoformat(str(time_node.get("datetime")))
    coord_match = re.search(r"[?&]query=(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)", str(map_link.get("href")))
    if not coord_match:
        raise BirdSyncError("eBird checklist coordinates are missing")
    protocol_node = soup.select_one('#other-details-effort .Heading[title] .Heading-main, [title^="Protocol:"]')
    observer_node = soup.select_one('[title^="Observers:"], [title^="记录人："], [title^="记录人:"]')
    duration_node = soup.select_one('[title^="Duration:"], [title^="观鸟时长："], [title^="观鸟时长:"]')
    if protocol_node:
        protocol = protocol_node.get_text(" ", strip=True)
        if not protocol and protocol_node.has_attr("title"):
            protocol = str(protocol_node.get("title", "")).partition(":")[2].strip()
    else:
        protocol = ""
    observer_match = re.search(r"(\d+)", str(observer_node.get("title", ""))) if observer_node else None
    duration_text = " ".join(duration_node.get_text(" ", strip=True).split()) if duration_node else ""
    duration_text += " " + (str(duration_node.get("title", "")) if duration_node else "")
    duration_match = re.search(r"([0-9.]+)\s*(?:hour|小时)", duration_text, re.I)
    minute_match = re.search(r"([0-9.]+)\s*(?:minute|分钟)", duration_text, re.I)
    duration_hours = float(duration_match.group(1)) if duration_match else (float(minute_match.group(1)) / 60 if minute_match else 0.0)
    status_text = " ".join((soup.select_one("#status-info") or soup.new_tag("span")).get_text(" ", strip=True).split())
    status_answers = [node.get_text(" ", strip=True) for node in soup.select("#status-info > strong")]
    answer = status_answers[-1] if status_answers else status_text
    complete = answer.casefold() == "yes" or answer == "是" or bool(re.search(r"\bYes\b", answer, re.I))
    flagged_text = " ".join((soup.select_one('section[aria-labelledby="flagged"]') or soup.new_tag("span")).get_text(" ", strip=True).split())
    hidden = (
        "Hidden checklist" in flagged_text
        or "记录已隐藏" in flagged_text
        or "Hidden checklist" in soup.get_text(" ", strip=True)
    )
    comment_node = soup.select_one('section[aria-labelledBy="checklist-comments"] p')
    observations: list[dict[str, Any]] = []
    for section in soup.select("li[data-observation] section.Observation"):
        species = section.select_one("[data-species-code]")
        count_node = section.select_one(".Observation-numberObserved > span > span:last-child")
        if species is None or count_node is None:
            continue
        common = species.select_one(".Heading-main")
        scientific = species.select_one(".Heading-sub--sci")
        raw_count = count_node.get_text(" ", strip=True)
        if not raw_count.isdigit() or int(raw_count) <= 0:
            raise BirdSyncError(f"BirdReport requires a positive exact count: {raw_count!r}")
        observations.append({
            "species_code": str(species.get("data-species-code") or ""),
            "common_name": common.get_text(" ", strip=True) if common else "",
            "scientific_name": scientific.get_text(" ", strip=True) if scientific else "",
            "count": int(raw_count),
        })
    if not observations:
        raise BirdSyncError("The authenticated eBird checklist contains no observations")
    return {
        "source_id": source_match.group(1),
        "source_url": f"https://ebird.org/checklist/{source_match.group(1)}",
        "start_time": start.strftime("%Y-%m-%d %H:%M:%S"),
        "end_time": (start + timedelta(hours=duration_hours)).strftime("%Y-%m-%d %H:%M:%S"),
        "duration_hours": duration_hours,
        "location_id": str(hotspot.get("href")).rsplit("/", 1)[-1],
        "location_name": hotspot.get_text(" ", strip=True),
        "latitude": float(coord_match.group(1)),
        "longitude": float(coord_match.group(2)),
        "protocol": protocol,
        "observers": int(observer_match.group(1)) if observer_match else 1,
        "complete": complete,
        "hidden": hidden,
        "comments": comment_node.get_text("\n", strip=True) if comment_node else "",
        "observations": observations,
    }


def parse_ebird_personal_index(html: str) -> list[dict[str, str]]:
    """Parse the authenticated My Checklists page without using public search."""
    try:
        from bs4 import BeautifulSoup
    except ImportError as exc:
        raise BirdSyncError("eBird personal-history parsing requires beautifulsoup4; use scripts/birdsync launcher") from exc
    soup = BeautifulSoup(html, "html.parser")
    rows: list[dict[str, str]] = []
    for item in soup.select('li[id^="checklist-S"]'):
        link = item.select_one('a[href^="/checklist/S"]')
        location = item.select_one(".ResultsStats-details-location")
        date_node = item.select_one(".Heading-main")
        time_node = item.select_one(".Heading-sub")
        if not link or not location or not date_node:
            continue
        source_match = re.search(r"/checklist/(S\d+)", str(link.get("href") or ""))
        if not source_match:
            continue
        raw_date = " ".join(date_node.get_text(" ", strip=True).replace("\u202f", " ").split())
        raw_time = " ".join((time_node.get_text(" ", strip=True) if time_node else "").replace("\u202f", " ").split())
        parsed_date = None
        for fmt in ("%d %b %Y %I:%M %p", "%d %b %Y", "%b %d, %Y %I:%M %p", "%b %d, %Y"):
            candidate = f"{raw_date} {raw_time}".strip() if "%M" in fmt else raw_date
            try:
                parsed_date = datetime.strptime(candidate, fmt)
                break
            except ValueError:
                continue
        if parsed_date is None:
            title = " ".join(str(link.get("title") or "").replace("\u202f", " ").split())
            match = re.search(r"-\s*(?:[A-Za-z]+,\s*)?([A-Za-z]+ \d{1,2}, \d{4})(?:,\s*(\d{1,2}:\d{2}\s*[AP]M))?", title, re.I)
            if match:
                candidate = " ".join(part for part in match.groups() if part)
                fmt = "%B %d, %Y %I:%M %p" if match.group(2) else "%B %d, %Y"
                try:
                    parsed_date = datetime.strptime(candidate, fmt)
                except ValueError:
                    pass
        if parsed_date is None:
            raise BirdSyncError(f"Could not parse eBird My Checklists date for {source_match.group(1)}")
        rows.append({
            "source_id": source_match.group(1),
            "source_url": f"https://ebird.org/checklist/{source_match.group(1)}",
            "observed_at": parsed_date.strftime("%Y-%m-%d %H:%M:%S"),
            "location_name": " ".join(location.get_text(" ", strip=True).split()),
        })
    if not rows:
        lowered = html.casefold()
        if "login" in lowered or "cassso" in lowered or "sign in" in lowered:
            raise AuthRequired("ebird")
        raise BirdSyncError("Authenticated eBird My Checklists page contains no parseable personal checklists")
    return rows


class EbirdWebClient:
    """Authenticated adapter restricted to the user's own eBird pages."""

    BASE = "https://ebird.org"

    def __init__(self, session: dict[str, str]):
        try:
            import httpx
        except ImportError as exc:
            raise BirdSyncError("eBird personal access requires httpx; use scripts/birdsync launcher") from exc
        self.httpx = httpx
        self.session = {str(key): str(value) for key, value in session.items() if str(value)}
        if not self.session.get("EBIRD_SESSIONID"):
            raise AuthRequired("ebird")

    async def get(self, path: str, params: dict[str, Any] | None = None) -> str:
        response = None
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                async with self.httpx.AsyncClient(timeout=30, follow_redirects=True, cookies=self.session) as client:
                    response = await client.get(self.BASE + path, params=params, headers={"Accept-Language": "en-US,en;q=0.8"})
                break
            except self.httpx.RequestError as exc:
                last_error = exc
                if attempt < 2:
                    await asyncio.sleep(1 + attempt)
        if response is None:
            raise BirdSyncError(f"eBird request failed after 3 attempts: {last_error}")
        final_path = response.url.path.casefold()
        if response.status_code in (401, 403) or "login" in final_path or "cassso" in final_path:
            raise AuthRequired("ebird")
        response.raise_for_status()
        lowered = response.text.casefold()
        if "making sure" in lowered and "not a bot" in lowered:
            raise BirdSyncError("eBird returned an anti-bot challenge; use the isolated authenticated browser")
        return response.text

    async def personal_checklists(self, year: int) -> list[dict[str, str]]:
        html = await self.get("/mychecklists", {
            "currentRow": 1,
            "sortBy": "date",
            "o": "desc",
            "year": year,
            "sharedFilter": "all",
        })
        return parse_ebird_personal_index(html)

    async def checklist_html(self, source_id: str) -> str:
        return await self.get(f"/checklist/{source_id}")


async def export_ebird_checklist_with_browser(source_id: str, output: Path) -> None:
    helper = Path(__file__).resolve().parent / "birdsync-auth-browser"
    process = await asyncio.create_subprocess_exec(
        str(helper),
        "--fetch-checklist", source_id,
        "--output", str(output),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode == 2:
        raise AuthRequired("ebird")
    if process.returncode != 0 or not output.is_file():
        message = stderr.decode("utf-8", errors="replace").strip().splitlines()
        detail = message[-1] if message else "browser export did not create a file"
        raise BirdSyncError(f"Authenticated browser could not fetch eBird checklist {source_id}: {detail}")


async def fetch_ebird_personal_checklists(
    start_date: str,
    end_date: str,
    output_dir: Path,
    location: str | None = None,
    latest: int | None = None,
    allow_empty: bool = False,
    allow_partial: bool = False,
) -> dict[str, Any]:
    session = load_web_session("ebird")
    if not session:
        raise AuthRequired("ebird")
    client = EbirdWebClient(session)
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")
    rows: list[dict[str, str]] = []
    for year in range(end.year, start.year - 1, -1):
        rows.extend(await client.personal_checklists(year))
    selected = [
        row for row in rows
        if start_date <= row["observed_at"][:10] <= end_date
        and (not location or location.casefold() in row["location_name"].casefold())
    ]
    selected.sort(key=lambda row: (row["observed_at"], int(row["source_id"][1:])), reverse=True)
    if latest is not None:
        selected = selected[:latest]
    if not selected and not allow_empty:
        raise BirdSyncError("No matching checklist exists in the authenticated user's eBird My Checklists page")
    output_dir.mkdir(parents=True, exist_ok=True)
    files: list[dict[str, str]] = []
    failures: list[dict[str, str]] = []
    for row in selected:
        path = output_dir / f"ebird-checklist-{row['source_id']}.html"
        try:
            try:
                html = await client.checklist_html(row["source_id"])
                atomic_write(path, html.encode("utf-8"))
            except BirdSyncError as exc:
                if "anti-bot challenge" not in str(exc):
                    raise
                await export_ebird_checklist_with_browser(row["source_id"], path)
        except AuthRequired:
            raise
        except BirdSyncError as exc:
            if not allow_partial:
                raise
            failures.append({"source_id": row["source_id"], "error": str(exc)})
            continue
        os.chmod(path, 0o600)
        parsed = parse_ebird_checklist_html(path)
        if parsed["source_id"] != row["source_id"]:
            raise BirdSyncError(f"Fetched eBird checklist identity mismatch for {row['source_id']}")
        files.append({**row, "file": str(path)})
    index_path = output_dir / "ebird-personal-selection.json"
    write_json(index_path, {
        "schema_version": 1,
        "scope": "authenticated_personal_account",
        "start_date": start_date,
        "end_date": end_date,
        "location_filter": location,
        "latest": latest,
        "checklists": files,
        "fetch_failures": failures,
    })
    os.chmod(index_path, 0o600)
    return {
        "status": "ok",
        "scope": "authenticated_personal_account",
        "checklists": files,
        "fetch_failures": failures,
        "selection": str(index_path),
    }


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    from math import asin, cos, radians, sin, sqrt
    dlat, dlon = radians(lat2 - lat1), radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 6371.0088 * 2 * asin(sqrt(a))


async def resolve_birdreport_point(client: BirdReportClient, checklist: dict[str, Any], point_id: str | None) -> tuple[dict[str, Any], float]:
    if point_id:
        point = await client.post("/member/system/point/get", {"point_id": str(point_id)}, decrypt=False)
        if not isinstance(point, dict) or not point.get("point_id"):
            raise BirdSyncError(f"BirdReport point does not exist: {point_id}")
        return point, distance_km(checklist["latitude"], checklist["longitude"], float(point["latitude"]), float(point["longitude"]))
    chinese = re.sub(r"\s*[（(].*$", "", checklist["location_name"]).replace("--", "-").strip()
    fragments = [chinese]
    fragments.extend(part.strip() for part in re.split(r"[-—]", chinese) if len(part.strip()) >= 2)
    candidates: dict[str, dict[str, Any]] = {}
    for query in dict.fromkeys(reversed(fragments)):
        items = await client.post("/member/system/point/hots", {"limit": 100, "keywords": query, "t": "0"}, decrypt=False)
        if isinstance(items, list):
            candidates.update({str(item.get("point_id")): item for item in items if isinstance(item, dict) and item.get("point_id")})
    if not candidates:
        raise BirdSyncError(f"No BirdReport point matches {checklist['location_name']}")
    ranked = sorted(
        ((distance_km(checklist["latitude"], checklist["longitude"], float(item["latitude"]), float(item["longitude"])), item) for item in candidates.values()),
        key=lambda pair: pair[0],
    )
    distance, selected = ranked[0]
    if distance > 5:
        raise BirdSyncError(f"Nearest BirdReport point is {distance:.2f} km away; explicit review is required")
    point = await client.post("/member/system/point/get", {"point_id": str(selected["point_id"])}, decrypt=False)
    return point, distance


async def build_ebird_to_birdreport_plan(args: argparse.Namespace) -> tuple[dict[str, Any], BirdReportClient]:
    checklist = parse_ebird_checklist_html(Path(args.input).resolve())
    token, _ = get_service_token("birdreport")
    if not token:
        raise AuthRequired("birdreport")
    client = BirdReportClient(token)
    identity = await client.validate()
    point, point_distance = await resolve_birdreport_point(client, checklist, args.point_id)
    taxa = await client.post("/member/system/taxon/version", {}, decrypt=False)
    if not isinstance(taxa, list):
        raise BirdSyncError("BirdReport taxonomy response is invalid")
    by_name = {str(item.get("name")): item for item in taxa if isinstance(item, dict) and item.get("enable") != 0}
    mapped: list[dict[str, Any]] = []
    missing: list[str] = []
    for observation in checklist["observations"]:
        match = next((by_name[name] for name in normalize_ebird_common_name(observation["common_name"]) if name in by_name), None)
        if match is None:
            missing.append(observation["common_name"])
            continue
        mapped.append({**observation, "taxon_id": int(match["id"]), "taxon_name": str(match["name"])})
    if missing:
        raise BirdSyncError("Unmapped BirdReport taxa: " + ", ".join(missing))
    marker = f"eBird:{checklist['source_id']}"
    date = checklist["start_time"][:10]
    existing = await client.fetch_reports(date, date)
    duplicates: list[dict[str, Any]] = []
    for report in existing:
        note = str(report.get("note") or "")
        same_marker = marker in note or checklist["source_url"] in note
        same_fields = (
            str(report.get("start_time") or "")[:16] == checklist["start_time"][:16]
            and str(report.get("point_id") or "") == str(point.get("point_id") or "")
            and len(report.get("obs") or []) == len(mapped)
        )
        if same_marker or same_fields:
            duplicates.append({
                "id": str(report.get("id") or ""),
                "serial_id": str(report.get("serial_id") or ""),
                "matched_on": "source_marker" if same_marker else "time_point_taxon_count",
            })
    plan = {
        "schema_version": 1,
        "tool_version": TOOL_VERSION,
        "direction": "ebird-to-birdreport",
        "source": {**checklist, "observations": mapped},
        "destination_identity": identity,
        "destination_point": point,
        "point_distance_km": round(point_distance, 3),
        "duplicate_matches": duplicates,
        "request_hash": sha256_bytes(json_bytes({"source": checklist, "point_id": point.get("point_id"), "taxa": mapped})),
    }
    return plan, client


async def verify_birdreport_activity(
    client: BirdReportClient,
    plan: dict[str, Any],
    activity_id: int,
    output_dir: Path,
    point_id: int | None = None,
) -> dict[str, Any]:
    source = plan["source"]
    marker = f"eBird:{source['source_id']}"
    detail = await client.post("/member/system/activity/get", {"id": activity_id}, decrypt=False)
    observations = await client.post(
        "/member/system/record/search",
        {"activity_id": activity_id, "page": "1", "limit": "1500"},
        decrypt=True,
    )
    if not isinstance(detail, dict) or not isinstance(observations, list):
        raise BirdSyncError(f"BirdReport verification response is invalid for activity {activity_id}")
    actual_individuals = sum(int(item.get("taxon_count") or 0) for item in observations)
    expected_individuals = sum(item["count"] for item in source["observations"])
    if len(observations) != len(source["observations"]) or actual_individuals != expected_individuals or marker not in str(detail.get("note") or ""):
        raise BirdSyncError(
            f"BirdReport verification failed for activity {activity_id}: expected {len(source['observations'])} taxa/{expected_individuals} individuals, got {len(observations)}/{actual_individuals}"
        )
    receipt_path = output_dir / "birdreport-write-receipt.json"
    receipt = {
        "status": "verified_remote",
        "source_id": source["source_id"],
        "activity_id": activity_id,
        "serial_id": str(detail.get("serial_id") or ""),
        "point_id": int(point_id or detail.get("point_id") or plan["destination_point"]["point_id"]),
        "request_hash": plan["request_hash"],
        "taxon_count": len(observations),
        "individual_count": actual_individuals,
        "state": str(detail.get("state") or ""),
        "verified_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    atomic_write(receipt_path, json_bytes(receipt) + b"\n")
    resume_path = output_dir / "resume.json"
    if resume_path.exists():
        atomic_write(resume_path, json_bytes({
            "schema_version": 1,
            "status": "complete",
            "direction": "ebird-to-birdreport",
            "source_id": source["source_id"],
            "remote_activity_id": activity_id,
            "remote_serial_id": receipt["serial_id"],
            "receipt": str(receipt_path),
        }) + b"\n")
    return {**receipt, "receipt": str(receipt_path), "point_name": str(plan["destination_point"]["point_name"])}


async def import_ebird_to_birdreport(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    plan, client = await build_ebird_to_birdreport_plan(args)
    plan_path = output_dir / "birdreport-import-plan.json"
    atomic_write(plan_path, json_bytes(plan) + b"\n")
    if args.verify_activity_id:
        result = await verify_birdreport_activity(client, plan, int(args.verify_activity_id), output_dir)
        return {**result, "plan": str(plan_path)}
    if plan["duplicate_matches"]:
        return {"status": "already_exists_remote", "source_id": plan["source"]["source_id"], "matches": plan["duplicate_matches"], "plan": str(plan_path)}
    if not args.apply:
        return {
            "status": "ready_to_apply",
            "source_id": plan["source"]["source_id"],
            "taxon_count": len(plan["source"]["observations"]),
            "individual_count": sum(item["count"] for item in plan["source"]["observations"]),
            "point_id": str(plan["destination_point"]["point_id"]),
            "point_name": str(plan["destination_point"]["point_name"]),
            "plan": str(plan_path),
        }
    source = plan["source"]
    point = plan["destination_point"]
    member_id = int(plan["destination_identity"]["user_id"])
    marker = f"eBird:{source['source_id']}"
    source_note = source["comments"].strip()
    metadata_note = f"[同步来源] {marker} {source['source_url']}；观察者 {source['observers']}；协议 {source['protocol'] or 'unknown'}"
    activity = {
        "start_time": source["start_time"],
        "end_time": source["end_time"],
        "state": "1" if source["hidden"] else "2",
        "note": (source_note + "\n\n" + metadata_note).strip(),
        "keywords": f"eBird,{source['source_id']}",
        "member_id": member_id,
    }
    point_payload = {
        key: point.get(key, "") for key in (
            "point_id", "point_name", "province_name", "city_name", "district_name", "adcode",
            "longitude", "latitude", "altitude",
        )
    }
    point_payload.update({"member_id": member_id, "isopen": 0})
    created = await client.post("/member/system/activity/saveReport", {
        "point": point_payload,
        "activity": activity,
        "units_activity": [],
        "domain_point_id": "",
    }, decrypt=False)
    if not isinstance(created, dict) or not created.get("activity_id"):
        raise BirdSyncError("BirdReport did not return an activity ID after saveReport")
    activity_id = int(created["activity_id"])
    actual_point_id = int(created.get("point_id") or point["point_id"])
    partial_path = output_dir / "birdreport-write-receipt.json"
    partial = {
        "status": "activity_created",
        "source_id": source["source_id"],
        "activity_id": activity_id,
        "point_id": actual_point_id,
        "request_hash": plan["request_hash"],
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    atomic_write(partial_path, json_bytes(partial) + b"\n")
    records = [{
        "activity_id": activity_id,
        "taxon_id": item["taxon_id"],
        "taxon_count": item["count"],
        "member_id": member_id,
        "uuid": str(uuid.uuid4()),
    } for item in source["observations"]]
    pushed = await client.post_envelope("/member/system/upload/pushTaxon", {
        "point": {"point_id": actual_point_id},
        "activity": {"id": activity_id},
        "records": records,
    })
    if pushed.get("success") is False:
        raise BirdSyncError(f"BirdReport taxon upload failed for activity {activity_id}: {pushed.get('msg')}")
    updated = await client.post_envelope("/member/system/activity/updateOptions", {
        "reportId": activity_id,
        "eye_all_birds": "1" if source["complete"] else "0",
        "real_quantity": "1",
        "effective_hours": source["duration_hours"],
    })
    if updated.get("success") is False:
        raise BirdSyncError(f"BirdReport option update failed for activity {activity_id}: {updated.get('msg')}")
    verified = await verify_birdreport_activity(client, plan, activity_id, output_dir, point_id=actual_point_id)
    return {**verified, "plan": str(plan_path)}


async def run_ebird_to_birdreport(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = Path(args.output_dir).resolve()
    auth = await preflight("ebird-to-birdreport", str(output_dir))
    if auth["status"] != "ready":
        return auth
    fetched = await fetch_ebird_personal_checklists(
        args.start_date,
        args.end_date,
        output_dir / "source",
        location=args.location,
        latest=args.latest,
    )
    results: list[dict[str, Any]] = []
    for selected in fetched["checklists"]:
        source_id = selected["source_id"]
        child_args = argparse.Namespace(
            input=selected["file"],
            output_dir=str(output_dir / source_id),
            point_id=args.point_id,
            apply=args.apply,
            verify_activity_id=None,
        )
        result = await import_ebird_to_birdreport(child_args)
        results.append(result)
    statuses = Counter(str(result.get("status") or "unknown") for result in results)
    if statuses and set(statuses) == {"already_exists_remote"}:
        status = "already_reconciled"
    elif args.apply and statuses.get("verified_remote") == len(results):
        status = "complete"
    elif statuses.get("ready_to_apply"):
        status = "ready_to_apply"
    else:
        status = "review_required"
    summary = {
        "status": status,
        "direction": "ebird-to-birdreport",
        "scope": "authenticated_personal_account",
        "selected": len(results),
        "result_counts": dict(statuses),
        "results": results,
        "selection": fetched["selection"],
    }
    summary_path = output_dir / "sync-summary.json"
    write_json(summary_path, summary)
    os.chmod(summary_path, 0o600)
    return {**summary, "summary": str(summary_path)}


async def run_birdreport_to_ebird(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = Path(args.output_dir).resolve()
    auth = await preflight("birdreport-to-ebird", str(output_dir))
    if auth["status"] != "ready":
        return auth
    prepared = await run_sync(args)
    manifest_path = output_dir / "manifest.json"
    manifest = read_json(manifest_path)
    if not isinstance(manifest, dict):
        raise BirdSyncError("Forward synchronization did not create a valid manifest")
    personal = await fetch_ebird_personal_checklists(
        args.start_date,
        args.end_date,
        output_dir / "ebird-personal",
        location=args.location,
        latest=None,
        allow_empty=True,
        allow_partial=True,
    )
    parsed_remote: list[dict[str, Any]] = []
    for selected in personal["checklists"]:
        checklist = parse_ebird_checklist_html(Path(selected["file"]))
        parsed_remote.append({**selected, "checklist": checklist})

    reconciled: list[dict[str, Any]] = []
    unmatched: list[str] = []
    for record in manifest.get("records", []):
        source_id = str(record.get("source_id") or "")
        marker = f"Converted from BirdReport CN, report ID: {source_id}"
        matches = [
            remote for remote in parsed_remote
            if marker in str(remote["checklist"].get("comments") or "")
        ]
        if not matches:
            unmatched.append(source_id)
            continue
        selected = max(matches, key=lambda item: int(item["source_id"][1:]))
        receipt = mark_existing(argparse.Namespace(
            manifest=str(manifest_path),
            source_id=source_id,
            remote_service="ebird",
            remote_id=selected["source_id"],
            evidence_url=selected["source_url"],
            matched_on=["authenticated_personal_history", "source_marker"],
            ledger=args.ledger,
        ))
        reconciled.append(receipt)

    if manifest.get("records") and not unmatched:
        status = "already_reconciled"
    elif personal.get("fetch_failures"):
        status = "review_required"
    else:
        status = "ready_for_ebird_import"
    summary = {
        "status": status,
        "direction": "birdreport-to-ebird",
        "scope": "authenticated_personal_accounts",
        "prepared": prepared,
        "reconciled": reconciled,
        "unmatched_source_ids": unmatched,
        "remote_fetch_failures": personal.get("fetch_failures", []),
        "selection": personal["selection"],
        "next_action": (
            "No upload is needed; every prepared source checklist already exists in the authenticated eBird account."
            if status == "already_reconciled"
            else (
                "Retry failed authenticated eBird checklist reads before uploading; duplicate status is not yet proven."
                if status == "review_required"
                else "Use the authenticated eBird import workflow for only the unmatched prepared checklist files, then read back checklist IDs."
            )
        ),
    }
    summary_path = output_dir / "sync-summary.json"
    write_json(summary_path, summary)
    os.chmod(summary_path, 0o600)
    return {**summary, "summary": str(summary_path)}


async def fetch_to_file(
    start_date: str,
    end_date: str,
    output: Path,
    location: str | None = None,
    latest: int | None = None,
) -> dict[str, Any]:
    token, _ = get_service_token("birdreport")
    if not token:
        raise AuthRequired("birdreport")
    client = BirdReportClient(token)
    reports = await client.fetch_reports(start_date, end_date, location=location)
    reports.sort(key=lambda item: str(item.get("start_time") or ""), reverse=True)
    if latest is not None:
        reports = reports[:latest]
    payload = {
        "schema_version": 1,
        "source": SOURCE_NAME,
        "fetched_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "start_date": start_date,
        "end_date": end_date,
        "location_filter": location,
        "latest": latest,
        "reports": reports,
    }
    write_json(output, payload)
    return {
        "status": "ok",
        "reports": len(reports),
        "output": str(output),
        "location_filter": location,
        "latest": latest,
    }


async def validate_service(service: str) -> dict[str, Any]:
    token, source = get_service_token(service)
    web_session = load_web_session(service) if service == "ebird" else {}
    if not token and not web_session:
        return {**auth_guide(service), "validation": "missing"}
    try:
        if service == "birdreport":
            identity = await BirdReportClient(token).validate()
        elif service == "ebird":
            import httpx

            if web_session:
                async with httpx.AsyncClient(timeout=20, follow_redirects=True, cookies=web_session) as client:
                    response = await client.get(SERVICE_GUIDES["ebird"]["import_url"])
                page = response.text.casefold()
                signed_in = (
                    response.status_code == 200
                    and "login" not in response.url.path.casefold()
                    and "multipart/form-data" in page
                    and "import" in page
                )
                if not signed_in:
                    return {**auth_guide(service), "status": "invalid", "validation": "invalid"}
                identity = {"session_scope": "personal_import_page"}
                source = str(WEB_SESSIONS_FILE)
            else:
                async with httpx.AsyncClient(timeout=20) as client:
                    response = await client.get(
                        "https://api.ebird.org/v2/ref/region/list/subnational1/MO",
                        params={"fmt": "json"},
                        headers={"X-eBirdApiToken": token},
                    )
                if response.status_code in (401, 403):
                    return {**auth_guide(service), "status": "invalid", "validation": "invalid"}
                response.raise_for_status()
                response.json()
                return {
                    **auth_guide(service),
                    "status": "auth_required",
                    "validation": "api_key_only",
                    "credential_source": source,
                    "identity": {"scope": "public_api_only"},
                }
        else:
            raise BirdSyncError(f"Unknown service: {service}")
        return {
            "status": "valid",
            "service": service,
            "validation": "valid",
            "credential_source": source,
            "identity": identity,
        }
    except BirdSyncError as exc:
        text = str(exc).lower()
        validation = "invalid" if "authentication" in text or "invalid token" in text else "unreachable"
        return {**auth_guide(service), "status": validation, "validation": validation, "error": str(exc)}
    except Exception as exc:
        return {**auth_guide(service), "status": "unreachable", "validation": "unreachable", "error": str(exc)}


async def preflight(direction: str, output_dir: str | None = None) -> dict[str, Any]:
    """Validate every personal account needed for a full synchronization.

    This is intentionally a live validation, not a presence check.  It lets an
    agent silently continue when both sessions are usable and produces one
    grouped login handoff when either side needs attention.
    """
    services = DIRECTION_SERVICES[direction]
    validations = await asyncio.gather(*(validate_service(service) for service in services))
    entries = dict(zip(services, validations))
    auth_needed = [
        service for service, result in entries.items()
        if result.get("validation") in {"missing", "invalid", "api_key_only"}
        or result.get("status") in {"auth_required", "invalid"}
    ]
    unreachable = [
        service for service, result in entries.items()
        if result.get("validation") == "unreachable" or result.get("status") == "unreachable"
    ]
    if unreachable:
        status = "unreachable"
    elif auth_needed:
        status = "auth_required"
    else:
        status = "ready"

    resume_file = None
    if output_dir:
        target = Path(output_dir).resolve()
        target.mkdir(parents=True, exist_ok=True)
        state_path = target / "resume.json"
        state = {
            "schema_version": 1,
            "status": status,
            "command": "preflight",
            "direction": direction,
            "services": list(services),
            "auth_needed": auth_needed,
            "unreachable": unreachable,
            "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        }
        write_json(state_path, state)
        os.chmod(state_path, 0o600)
        resume_file = str(state_path)

    result: dict[str, Any] = {
        "status": status,
        "direction": direction,
        "services": entries,
        "auth_needed": auth_needed,
        "unreachable": unreachable,
        "resume_file": resume_file,
    }
    if auth_needed:
        labels = [SERVICE_GUIDES[service]["display_name"] for service in auth_needed]
        result["user_message_markdown"] = (
            "需要补充登录：" + "、".join(labels) + "。Agent 会先探测隔离浏览器中的既有登录；"
            "仍未登录时，只需在一次性认证页完成登录，之后会自动验证并继续原同步请求。"
        )
        result["next_actions"] = [
            {
                "service": service,
                "probe_command": entries[service]["browser_probe_command"],
                "handoff_command": entries[service]["browser_handoff_command"],
                "login_url": SERVICE_GUIDES[service]["login_url"],
            }
            for service in auth_needed
        ]
    elif status == "ready":
        result["next_action"] = "Continue the requested synchronization without further user input."
    else:
        result["next_action"] = "Retry only the unreachable validation; do not ask the user to log in again yet."
    return result


def doctor() -> dict[str, Any]:
    modules = {}
    for name in ("httpx", "Crypto", "bs4"):
        try:
            __import__(name)
            modules[name] = "ok"
        except ImportError:
            modules[name] = "missing"
    birdreport_token, birdreport_source = get_service_token("birdreport")
    ebird_token, ebird_source = get_service_token("ebird")
    ebird_session = load_web_session("ebird")
    return {
        "status": "ok" if all(value == "ok" for value in modules.values()) and DEFAULT_MAPPING.exists() else "needs_setup",
        "python": sys.version.split()[0],
        "dependencies": modules,
        "mapping": str(DEFAULT_MAPPING),
        "mapping_exists": DEFAULT_MAPPING.exists(),
        "birdreport_token": "available" if birdreport_token else "not_set",
        "birdreport_credential_source": birdreport_source,
        "ebird_token": "available" if ebird_token else "not_set",
        "ebird_credential_source": ebird_source,
        "ebird_web_session": "available" if ebird_session else "not_set",
        "ebird_web_session_file": str(WEB_SESSIONS_FILE),
        "credential_file": str(CREDENTIALS_FILE),
        "remote_write": {"birdreport": True, "ebird": "authenticated_browser_only"},
    }


def validate_date(value: str) -> str:
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected YYYY-MM-DD") from exc
    return value


def positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected a positive integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("expected a positive integer")
    return parsed


def sync_state(args: argparse.Namespace, service: str) -> tuple[Path, dict[str, Any]]:
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    state_path = output_dir / "resume.json"
    state = {
        "schema_version": 1,
        "status": "auth_required",
        "service": service,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "command": "sync",
        "arguments": {
            "start_date": args.start_date,
            "end_date": args.end_date,
            "output_dir": str(output_dir),
            "mapping": args.mapping,
            "location_overrides": args.location_overrides,
            "ledger": args.ledger,
            "max_bytes": args.max_bytes,
            "include_imported": args.include_imported,
            "location": args.location,
            "latest": args.latest,
        },
    }
    write_json(state_path, state)
    return state_path, state


async def run_sync(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    snapshot = output_dir / "birdreport-snapshot.json"
    await fetch_to_file(
        args.start_date,
        args.end_date,
        snapshot,
        location=args.location,
        latest=args.latest,
    )
    args.input = str(snapshot)
    result = prepare(args)
    state_path = output_dir / "resume.json"
    if state_path.exists():
        state = read_json(state_path, default={})
        if isinstance(state, dict):
            state["status"] = "completed"
            state["completed_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
            state["result"] = result
            write_json(state_path, state)
    return result


def resume_namespace(state_path: Path) -> argparse.Namespace:
    state = read_json(state_path)
    if not isinstance(state, dict) or state.get("command") != "sync" or not isinstance(state.get("arguments"), dict):
        raise BirdSyncError(f"Invalid or unsupported resume state: {state_path}")
    values = dict(state["arguments"])
    values["command"] = "sync"
    return argparse.Namespace(**values)


def request_ebird_data(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    state_path = output_dir / "resume.json"
    state = {
        "schema_version": 1,
        "status": "data_required",
        "service": "ebird",
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "command": "ebird-to-birdreport",
        "arguments": {"location": args.location, "latest": args.latest},
        "expected_input": "eBird Download My Data spreadsheet",
    }
    write_json(state_path, state)
    guide = auth_guide("ebird")
    return {
        **guide,
        "status": "data_required",
        "requested_location": args.location,
        "latest": args.latest,
        "resume_file": str(state_path),
        "next_action": "Download personal eBird data and provide the file path or attach the file to the conversation.",
    }


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Prepare reviewed BirdReport -> eBird import packages")
    root.add_argument("--version", action="version", version=TOOL_VERSION)
    sub = root.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="Check runtime and credential availability")

    auth = sub.add_parser("auth", help="Guide, store, and validate local credentials")
    auth_sub = auth.add_subparsers(dest="auth_command", required=True)
    guide = auth_sub.add_parser("guide", help="Show official login/data links and safe setup steps")
    guide.add_argument("service", choices=sorted(SERVICE_GUIDES))
    status = auth_sub.add_parser("status", help="Show credential status without revealing secrets")
    status.add_argument("service", choices=["all", *sorted(SERVICE_GUIDES)], nargs="?", default="all")
    status.add_argument("--validate", action="store_true", help="Validate configured credentials against the service")
    auth_set = auth_sub.add_parser("set", help="Store a token using hidden local terminal input")
    auth_set.add_argument("service", choices=sorted(SERVICE_GUIDES))
    auth_session = auth_sub.add_parser("set-session", help="Store a minimal browser session using hidden input")
    auth_session.add_argument("service", choices=["ebird"])
    auth_clear = auth_sub.add_parser("clear", help="Remove a locally stored token")
    auth_clear.add_argument("service", choices=sorted(SERVICE_GUIDES))

    preflight_parser = sub.add_parser("preflight", help="Live-check both accounts before a full synchronization")
    preflight_parser.add_argument("--direction", choices=sorted(DIRECTION_SERVICES), required=True)
    preflight_parser.add_argument("--output-dir", help="Optionally save a protected resumable preflight state")

    fetch = sub.add_parser("fetch", help="Read BirdReport checklists into a local snapshot")
    fetch.add_argument("--start-date", required=True, type=validate_date)
    fetch.add_argument("--end-date", required=True, type=validate_date)
    fetch.add_argument("--output", required=True)
    fetch.add_argument("--location", help="Keep checklists whose point name contains this text")
    fetch.add_argument("--latest", type=positive_int, help="Keep only the newest N matching checklists")

    fetch_ebird = sub.add_parser("fetch-ebird", help="Fetch matching checklists only from the authenticated user's eBird history")
    fetch_ebird.add_argument("--start-date", required=True, type=validate_date)
    fetch_ebird.add_argument("--end-date", required=True, type=validate_date)
    fetch_ebird.add_argument("--output-dir", required=True)
    fetch_ebird.add_argument("--location", help="Keep personal checklists whose location contains this text")
    fetch_ebird.add_argument("--latest", type=positive_int, help="Keep only the newest N matching checklists")

    def prepare_args(target: argparse.ArgumentParser, include_input: bool = True) -> None:
        if include_input:
            target.add_argument("--input", required=True)
        target.add_argument("--output-dir", required=True)
        target.add_argument("--mapping", default=str(DEFAULT_MAPPING))
        target.add_argument("--location-overrides")
        target.add_argument("--ledger")
        target.add_argument("--max-bytes", type=positive_int, default=DEFAULT_MAX_BYTES)
        target.add_argument("--include-imported", action="store_true")

    prepare_parser = sub.add_parser("prepare", help="Create review and eBird import CSV files")
    prepare_args(prepare_parser)

    sync = sub.add_parser("sync", help="Fetch and prepare in one local, read-only workflow")
    sync.add_argument("--start-date", required=True, type=validate_date)
    sync.add_argument("--end-date", required=True, type=validate_date)
    sync.add_argument("--location", help="Keep checklists whose point name contains this text")
    sync.add_argument("--latest", type=positive_int, help="Keep only the newest N matching checklists")
    prepare_args(sync, include_input=False)

    forward_sync = sub.add_parser("sync-birdreport-to-ebird", help="Prepare personal BirdReport records and reconcile against authenticated eBird history")
    forward_sync.add_argument("--start-date", required=True, type=validate_date)
    forward_sync.add_argument("--end-date", required=True, type=validate_date)
    forward_sync.add_argument("--location", help="Keep personal BirdReport checklists whose point name contains this text")
    forward_sync.add_argument("--latest", type=positive_int, help="Keep only the newest N matching source checklists")
    prepare_args(forward_sync, include_input=False)

    imported = sub.add_parser("mark-imported", help="Mark a manifest imported after user confirmation")
    imported.add_argument("--manifest", required=True)
    imported.add_argument("--ledger")
    imported.add_argument("--remote-service", choices=["ebird"])
    imported.add_argument("--remote-id")
    imported.add_argument("--evidence-url")
    imported.add_argument("--batch-id")
    imported.add_argument("--acceptance-receipt")

    existing = sub.add_parser("mark-existing", help="Record that a source checklist already exists remotely")
    existing.add_argument("--manifest", required=True)
    existing.add_argument("--source-id", required=True)
    existing.add_argument("--remote-service", choices=["ebird"], default="ebird")
    existing.add_argument("--remote-id", required=True)
    existing.add_argument("--evidence-url", required=True)
    existing.add_argument("--matched-on", action="append", default=[])
    existing.add_argument("--ledger")

    resume = sub.add_parser("resume", help="Resume a saved request after authentication")
    resume.add_argument("--state", required=True)

    ebird_data = sub.add_parser("request-ebird-data", help="Save a reverse-migration request and guide personal-data download")
    ebird_data.add_argument("--location", required=True)
    ebird_data.add_argument("--latest", type=positive_int, default=1)
    ebird_data.add_argument("--output-dir", required=True)

    reverse = sub.add_parser("import-ebird-checklist", help="Plan or apply one authenticated eBird checklist to BirdReport")
    reverse.add_argument("--input", required=True, help="Saved authenticated eBird checklist HTML")
    reverse.add_argument("--output-dir", required=True)
    reverse.add_argument("--point-id", help="Use an explicitly reviewed BirdReport point instead of automatic nearest-name matching")
    reverse.add_argument("--apply", action="store_true", help="Write the checklist after dry-run validation and duplicate checks")
    reverse.add_argument("--verify-activity-id", type=positive_int, help="Verify and receipt a previously created partial import without writing again")

    reverse_sync = sub.add_parser("sync-ebird-to-birdreport", help="Fetch personal eBird checklists and plan/apply BirdReport imports")
    reverse_sync.add_argument("--start-date", required=True, type=validate_date)
    reverse_sync.add_argument("--end-date", required=True, type=validate_date)
    reverse_sync.add_argument("--output-dir", required=True)
    reverse_sync.add_argument("--location", help="Keep personal checklists whose location contains this text")
    reverse_sync.add_argument("--latest", type=positive_int, default=1)
    reverse_sync.add_argument("--point-id", help="Use an explicitly reviewed BirdReport point")
    reverse_sync.add_argument("--apply", action="store_true", help="Write only checklists that pass review and are not duplicates")
    return root


def main() -> int:
    args = parser().parse_args()
    active_args = args
    try:
        if args.command == "doctor":
            result = doctor()
        elif args.command == "auth":
            if args.auth_command == "guide":
                result = auth_guide(args.service)
            elif args.auth_command == "set":
                result = set_credential(args.service)
            elif args.auth_command == "set-session":
                result = set_web_session(args.service)
            elif args.auth_command == "clear":
                result = clear_credential(args.service)
            elif args.auth_command == "status":
                services = sorted(SERVICE_GUIDES) if args.service == "all" else [args.service]
                entries: dict[str, Any] = {}
                for service in services:
                    if args.validate:
                        entries[service] = asyncio.run(validate_service(service))
                    else:
                        entries[service] = auth_guide(service)
                result = {"status": "ok", "services": entries}
            else:
                raise BirdSyncError(f"Unknown auth command: {args.auth_command}")
        elif args.command == "fetch":
            result = asyncio.run(fetch_to_file(
                args.start_date,
                args.end_date,
                Path(args.output).resolve(),
                location=args.location,
                latest=args.latest,
            ))
        elif args.command == "fetch-ebird":
            result = asyncio.run(fetch_ebird_personal_checklists(
                args.start_date,
                args.end_date,
                Path(args.output_dir).resolve(),
                location=args.location,
                latest=args.latest,
            ))
        elif args.command == "preflight":
            result = asyncio.run(preflight(args.direction, args.output_dir))
        elif args.command == "prepare":
            result = prepare(args)
        elif args.command == "sync":
            result = asyncio.run(run_sync(args))
        elif args.command == "sync-birdreport-to-ebird":
            result = asyncio.run(run_birdreport_to_ebird(args))
        elif args.command == "mark-imported":
            result = mark_imported(args)
        elif args.command == "mark-existing":
            result = mark_existing(args)
        elif args.command == "resume":
            active_args = resume_namespace(Path(args.state).resolve())
            result = asyncio.run(run_sync(active_args))
        elif args.command == "request-ebird-data":
            result = request_ebird_data(args)
        elif args.command == "import-ebird-checklist":
            result = asyncio.run(import_ebird_to_birdreport(args))
        elif args.command == "sync-ebird-to-birdreport":
            result = asyncio.run(run_ebird_to_birdreport(args))
        else:
            raise BirdSyncError(f"Unknown command: {args.command}")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if result.get("status") in {"auth_required", "data_required"}:
            return 2
        if result.get("status") == "unreachable":
            return 1
        return 0
    except AuthRequired as exc:
        state_path = None
        if getattr(active_args, "command", None) == "sync":
            state_path, _ = sync_state(active_args, exc.service)
        response = {
            **auth_guide(exc.service),
            "status": "auth_required",
            "resume_file": str(state_path) if state_path else None,
            "resume_command": (
                f"{Path(__file__).resolve().parent / 'birdsync'} resume --state {state_path}"
                if state_path else None
            ),
        }
        print(json.dumps(response, ensure_ascii=False, indent=2))
        return 2
    except BirdSyncError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

