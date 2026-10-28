"""Preserve public CSL bytes and headers; future public archiving is best effort.

Standard-library source capture; requests handles public archive endpoints.
No credentials, model calls, user-file uploads or mail.
Each invocation writes a new immutable capture directory. Test mode can never
claim that the future resolution snapshot has been obtained.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import http.client
import json
from pathlib import Path
import re
import shutil
import sys
import time
import traceback
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen
import uuid

import requests

SOURCE = "https://data.trade.gov/downloadable_consolidated_screening_list/v1/consolidated.json"
SOURCE_LABEL = "Entity List (EL) - Bureau of Industry and Security"
UTC = timezone.utc


def now():
    return datetime.now(UTC)


def iso(value):
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def write_json(path, value):
    # Exclusive creation prevents a retry from overwriting original evidence.
    with Path(path).open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def assess_payload(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise ValueError("Missing complete JSON results array")
    rows = payload["results"]
    total = payload.get("total")
    if isinstance(total, bool) or not isinstance(total, int) or len(rows) != total:
        raise ValueError("results length does not equal integer total")
    selected = [row for row in rows if row.get("source") == SOURCE_LABEL]
    country_sets = {}
    for row in selected:
        key = row.get("id")
        if not isinstance(key, str) or not key:
            raise ValueError("Selected record has missing/non-string id")
        addresses = row.get("addresses", [])
        if not isinstance(addresses, list):
            raise ValueError("Selected record addresses is not an array")
        countries = frozenset(
            address["country"].strip().upper()
            for address in addresses
            if isinstance(address, dict)
            and isinstance(address.get("country"), str)
            and address["country"].strip()
        )
        if key in country_sets and country_sets[key] != countries:
            raise ValueError("Conflicting selected duplicate id country sets")
        country_sets[key] = countries
    denominator = len(country_sets)
    numerator = sum(len(countries) >= 2 for countries in country_sets.values())
    return {
        "complete_results": True,
        "raw_row_count": len(rows),
        "reported_total": total,
        "selected_source_row_count": len(selected),
        "distinct_selected_ids": denominator,
        "multi_country_selected_ids": numerator,
        "unrounded_current_percentage": 100 * numerator / denominator if denominator else None,
        "selected_id_country_sets": {
            key: sorted(value) for key, value in sorted(country_sets.items())
        },
    }


def parse_http_date(value):
    try:
        result = parsedate_to_datetime(value)
        return result.replace(tzinfo=UTC) if result.tzinfo is None else result.astimezone(UTC)
    except (TypeError, ValueError, OverflowError):
        return None


def capture_source(directory, config, mode):
    started = now()
    parts = urlsplit(SOURCE)
    connection = http.client.HTTPSConnection(parts.hostname, timeout=120)
    result = {"url": SOURCE, "started_at_utc": iso(started), "mode": mode}
    try:
        connection.request("GET", parts.path, headers={
            "User-Agent": "Mantic-CSL-public-preservation/1.0",
            "Accept": "application/json", "Accept-Encoding": "identity",
            "Cache-Control": "no-cache",
        })
        response = connection.getresponse()
        received = now()
        pairs = list(response.headers.raw_items())
        headers = {key.lower(): value for key, value in pairs}
        write_json(directory / "response_headers.json", {
            "status": response.status, "reason": response.reason,
            "http_version": response.version, "raw_header_pairs": pairs,
        })
        with (directory / "response_headers.txt").open("x", encoding="utf-8", newline="") as stream:
            stream.write(f"HTTP/{response.version // 10}.{response.version % 10} {response.status} {response.reason}\r\n")
            for key, value in pairs:
                stream.write(f"{key}: {value}\r\n")
            stream.write("\r\n")
        digest = hashlib.sha256()
        length = 0
        body_path = directory / "original_response.json"
        with body_path.open("xb") as stream:
            while chunk := response.read(1024 * 1024):
                length += len(chunk)
                digest.update(chunk)
                stream.write(chunk)
        result.update({
            "status": response.status,
            "response_headers_received_at_utc": iso(received),
            "completed_at_utc": iso(now()), "byte_count": length,
            "sha256": digest.hexdigest(), "last_modified": headers.get("last-modified"),
            "etag": headers.get("etag"), "server_date": headers.get("date"),
            "content_length": headers.get("content-length"),
        })
        if response.status != 200:
            raise ValueError(f"Official source returned HTTP {response.status}")
        if headers.get("content-encoding", "identity").lower() != "identity":
            raise ValueError("Unexpected content encoding; raw transfer not treated as JSON")
        if headers.get("content-length") and int(headers["content-length"]) != length:
            raise ValueError("Downloaded bytes do not match Content-Length")
        assessment = assess_payload(json.loads(body_path.read_bytes().decode("utf-8-sig")))
        write_json(directory / "population_check.json", assessment)
        modified = parse_http_date(headers.get("last-modified"))
        cutoff = datetime.fromisoformat(config["cutoff_utc"].replace("Z", "+00:00"))
        result.update({
            "capture_valid": True,
            "last_modified_at_utc": iso(modified) if modified else None,
            "last_modified_no_later_than_cutoff": bool(modified and modified <= cutoff),
            "last_modified_on_target_date": bool(modified and modified.date() == cutoff.date()),
            "response_received_by_cutoff": received <= cutoff,
            "future_target_snapshot_obtained": False,
            "public_version_evidence_verified": False,
            "qualification_note": "A local capture is not independently public evidence and does not alone prove latest-at-cutoff status. Test runs never obtain the future target.",
        })
    except Exception as exc:
        result.update({"capture_valid": False, "error": f"{type(exc).__name__}: {exc}",
                       "completed_at_utc": iso(now()), "future_target_snapshot_obtained": False})
    finally:
        connection.close()
    write_json(directory / "capture_receipt.json", result)
    return result


def request_public_archive(directory):
    """Anonymous Save Page Now attempt; acceptance is never treated as proof."""
    endpoint = "https://web.archive.org/save"
    receipt = {"requested_at_utc": iso(now()), "official_source_url": SOURCE,
               "endpoint": endpoint, "public_capture_verified": False}
    try:
        response = requests.post(endpoint, data={"url": SOURCE},
                                 headers={"Accept": "application/json", "User-Agent": "Mantic-CSL-public-preservation/1.0"}, timeout=90)
        raw = response.content
        receipt.update({"http_status": response.status_code, "headers": list(response.headers.items()),
                        "response_sha256": hashlib.sha256(raw).hexdigest()})
        (directory / "archive_submit_response.bin").write_bytes(raw)
        try:
            data = json.loads(raw)
            receipt["response_json"] = data
            receipt["job_id"] = data.get("job_id")
        except ValueError:
            receipt["error"] = "Archive response was not JSON; no job acceptance inferred"
        if not receipt.get("job_id") and response.status_code in (200, 401, 403):
            # The public Save Page Now web route accepts anonymous submissions
            # even where the authenticated SPN2 JSON endpoint returns 401.
            # A redirect is only submission evidence, never full-copy proof.
            legacy = "https://web.archive.org/save/" + SOURCE
            receipt["legacy_attempts"] = []
            for attempt in range(2):
                try:
                    with requests.get(legacy, timeout=120, stream=True,
                                      headers={"User-Agent": "Mantic-CSL-public-preservation/1.0"}) as saved:
                        item = {"attempt": attempt + 1, "http_status": saved.status_code,
                                "final_url": saved.url, "headers": list(saved.headers.items()),
                                "redirects": [{"status": step.status_code, "url": step.url,
                                               "location": step.headers.get("Location")} for step in saved.history]}
                        prefix = saved.raw.read(4096)
                        (directory / f"archive_legacy_response_prefix_{attempt + 1}.bin").write_bytes(prefix)
                        item["saved_response_prefix_bytes"] = len(prefix)
                        item["response_prefix_is_not_a_complete_archive_copy"] = True
                        receipt["legacy_attempts"].append(item)
                        match = re.match(r"https://web\.archive\.org/web/(\d{14})/", saved.url)
                        if saved.status_code == 200 and match:
                            receipt["candidate_archive_timestamp"] = match.group(1)
                            receipt["candidate_archive_url"] = saved.url
                            receipt["archive_submission_observed"] = True
                            break
                except Exception as exc:
                    receipt["legacy_attempts"].append({"attempt": attempt + 1, "error": f"{type(exc).__name__}: {exc}"})
                if attempt == 0:
                    time.sleep(2)
    except Exception as exc:
        receipt["error"] = f"{type(exc).__name__}: {exc}"
    write_json(directory / "archive_request_receipt.json", receipt)
    return receipt


def verify_public_captures(directory, config):
    """Read CDX and verify replay bytes/headers. Does not create a capture."""
    day = config["cutoff_utc"][:10].replace("-", "")
    query = urlencode({"url": SOURCE, "output": "json", "from": day, "to": day,
                       "filter": "statuscode:200", "fl": "timestamp,original,statuscode,digest,length", "limit": "100"})
    receipt = {"checked_at_utc": iso(now()), "query_url": "https://web.archive.org/cdx/search/cdx?" + query,
               "public_capture_verified": False, "verified_replays": [], "errors": []}
    try:
        response = requests.get(receipt["query_url"], headers={"User-Agent": "Mantic-CSL-public-preservation/1.0"}, timeout=45)
        response.raise_for_status()
        raw = response.content
        (directory / "cdx_response.json").write_bytes(raw)
        rows = json.loads(raw)
        if not isinstance(rows, list):
            raise ValueError("Unexpected CDX response shape")
        columns = rows[0] if rows else []
        captures = [dict(zip(columns, row)) for row in rows[1:]]
        receipt["indexed_capture_count"] = len(captures)
        # Bound replay bandwidth and runtime; retain the latest three candidates.
        for item in sorted(captures, key=lambda row: row["timestamp"], reverse=True)[:3]:
            timestamp = item["timestamp"]
            if not re.fullmatch(r"\d{14}", timestamp):
                continue
            replay = "https://web.archive.org/web/" + timestamp + "id_/" + SOURCE
            evidence = {"archive_timestamp": timestamp, "replay_url": replay, "verified": False}
            try:
                retrieval_mode = "raw_id_replay"
                try:
                    response = requests.get(replay, headers={"Accept-Encoding": "identity"}, timeout=120)
                    response.raise_for_status()
                    raw = response.content
                except requests.RequestException as raw_error:
                    if getattr(getattr(raw_error, "response", None), "status_code", None) == 429:
                        raise
                    public_replay = "https://web.archive.org/web/" + timestamp + "/" + SOURCE
                    response = requests.get(public_replay, timeout=120)
                    response.raise_for_status()
                    public_bytes = response.content
                    raw = re.sub(rb"https?://web\.archive\.org/web/\d{14}(?:[a-z_]+)?/", b"", public_bytes)
                    restored_hash = hashlib.sha256(raw).hexdigest()
                    public_etag = response.headers.get("x-archive-orig-etag")
                    matching_local = []
                    for saved_receipt in Path(config["stable_output_directory"]).glob("*/capture_receipt.json"):
                        saved = json.loads(saved_receipt.read_text(encoding="utf-8"))
                        if saved.get("capture_valid") and saved.get("sha256") == restored_hash and saved.get("etag") == public_etag:
                            matching_local.append(str(saved_receipt))
                    if not matching_local:
                        raise ValueError("Reversing public URL rewrites did not match a complete original capture and its ETag")
                    (directory / ("archive_transformed_replay_" + timestamp + ".json")).write_bytes(public_bytes)
                    retrieval_mode = "reversed_wayback_url_prefixes_with_exact_local_hash_and_etag_match"
                    evidence["matching_original_capture_receipts"] = matching_local
                    evidence["public_transformed_replay_url"] = public_replay
                    evidence["raw_endpoint_error"] = str(raw_error)
                pairs = list(response.headers.items())
                headers = {key.lower(): value for key, value in pairs}
                replay_path = directory / ("archive_replay_" + timestamp + ".json")
                replay_path.write_bytes(raw)
                assessment = assess_payload(json.loads(raw.decode("utf-8-sig")))
                modified = parse_http_date(headers.get("x-archive-orig-last-modified"))
                cutoff = datetime.fromisoformat(config["cutoff_utc"].replace("Z", "+00:00"))
                original_length = headers.get("x-archive-orig-content-length")
                if not modified or modified > cutoff:
                    raise ValueError("Replay lacks qualifying original Last-Modified")
                if original_length is None or int(original_length) != len(raw):
                    raise ValueError("Replay original Content-Length missing or mismatched")
                evidence.update({"verified": True, "byte_count": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
                                 "retrieval_mode": retrieval_mode,
                                 "original_header_pairs": pairs, "last_modified_at_utc": iso(modified),
                                 "last_modified_on_target_date": modified.date() == cutoff.date(),
                                 "original_etag": headers.get("x-archive-orig-etag"),
                                 "raw_row_count": assessment["raw_row_count"], "reported_total": assessment["reported_total"]})
            except Exception as exc:
                evidence["error"] = f"{type(exc).__name__}: {exc}"
            receipt["verified_replays"].append(evidence)
        # Latest-at-cutoff evidence and the final question's date rule still need
        # reconciliation against before/after local receipts; never infer it.
        receipt["public_capture_verified"] = any(item["verified"] for item in receipt["verified_replays"])
        receipt["final_resolution_vintage_verified"] = False
    except Exception as exc:
        receipt["errors"].append(f"{type(exc).__name__}: {exc}")
    write_json(directory / "archive_verification_receipt.json", receipt)
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--mode", choices=["test", "production"], default="test")
    parser.add_argument("--archive", action="store_true")
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args(argv)
    config = json.loads(Path(args.config).read_text(encoding="utf-8-sig"))
    root = Path(config["stable_output_directory"])
    root.mkdir(parents=True, exist_ok=True)
    directory = root / (now().strftime("%Y%m%dT%H%M%S_%fZ") + "_" + uuid.uuid4().hex[:8])
    directory.mkdir(exist_ok=False)
    write_json(directory / "invocation.json", {"mode": args.mode, "archive_requested": args.archive,
                                               "verify_only": args.verify_only, "started_at_utc": iso(now())})
    if not args.verify_only:
        capture_source(directory, config, args.mode)
    if args.archive:
        if args.mode != "production":
            write_json(directory / "archive_request_receipt.json", {"skipped": True, "reason": "Test mode never creates public captures"})
        else:
            request_public_archive(directory)
    if args.verify_only or args.archive:
        verify_public_captures(directory, config)
    mirror = config.get("workspace_output_directory")
    if mirror:
        try:
            target = Path(mirror) / directory.name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(directory, target)
        except Exception as exc:
            write_json(directory / "workspace_mirror_error.json", {"error": f"{type(exc).__name__}: {exc}"})
    print(str(directory))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        fallback = Path(__file__).with_name("fatal_errors.log")
        with fallback.open("a", encoding="utf-8") as stream:
            stream.write(iso(now()) + "\n" + traceback.format_exc() + "\n")
        raise
