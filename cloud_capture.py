"""Cloud-only public source preservation, with explicit test/target separation."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import requests
import capture_csl as csl

UTC = timezone.utc
CUTOFF = datetime(2027, 1, 15, 12, 0, tzinfo=UTC)


def publish_capture(mode, suffix, poll_history=None):
    stamp = csl.now().strftime("%Y%m%dT%H%M%SZ")
    run_id = os.environ.get("GITHUB_RUN_ID", "local")
    run_attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "1")
    collector_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    root = Path("output") / f"{mode}_{stamp}_{run_id}_{run_attempt}_{suffix}"
    root.mkdir(parents=True, exist_ok=False)
    receipt = csl.capture_source(root, {"cutoff_utc": csl.iso(CUTOFF)}, mode)
    if poll_history:
        csl.write_json(root / "source_version_poll_history.json", poll_history)
    csl.write_json(root / "cloud_run.json", {
        "mode": mode, "github_repository": os.environ.get("GITHUB_REPOSITORY"),
        "github_run_id": run_id, "github_run_attempt": run_attempt,
        "collector_commit": collector_commit,
        "workflow_run_url": f"https://github.com/{os.environ.get('GITHUB_REPOSITORY')}/actions/runs/{run_id}",
        "future_target_snapshot_obtained": False,
        "note": "Test captures are current observations, not the 1/15/2027 target. Production captures still require latest-at-cutoff reconciliation.",
    })
    manifest = {p.name: {"sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "byte_count": p.stat().st_size}
                for p in root.iterdir() if p.is_file()}
    csl.write_json(root / "SHA256_manifest.json", manifest)
    tag = f"csl-{mode}-{stamp}-{run_id}-{run_attempt}-{suffix}"
    notes = root / "release_notes.txt"
    notes.write_text(
        f"Public official CSL capture, mode={mode}.\n"
        f"Source: {csl.SOURCE}\n"
        f"Captured at: {receipt['started_at_utc']}\n"
        f"Capture valid: {receipt['capture_valid']}\n"
        f"Workflow: https://github.com/{os.environ.get('GITHUB_REPOSITORY')}/actions/runs/{run_id}\n"
        "This release does not certify the future resolution or latest-at-cutoff version.\n"
        "Verify original bytes, original headers, complete-population count and SHA256_manifest.json.\n",
        encoding="utf-8",
    )
    files = [str(p) for p in root.iterdir() if p.is_file()]
    subprocess.run(["gh", "release", "create", tag, *files,
                    "--repo", os.environ["GITHUB_REPOSITORY"],
                    "--target", collector_commit,
                    "--title", f"CSL {mode} capture {stamp}", "--notes-file", str(notes), "--latest=false"], check=True)
    # Verify release asset bytes without credentials, not through a privileged
    # Actions artifact URL. Preserve this independent public-access check too.
    endpoint = f"https://api.github.com/repos/{os.environ['GITHUB_REPOSITORY']}/releases/tags/{tag}"
    response = requests.get(endpoint, timeout=45)
    response.raise_for_status()
    assets = {item["name"]: item for item in response.json()["assets"]}
    verification = {"checked_at_utc": csl.iso(csl.now()), "release_tag": tag,
                    "unauthenticated_public_download": True, "assets": [], "all_hashes_match": True}
    for name, expected in manifest.items():
        item = {"name": name, "expected_sha256": expected["sha256"], "expected_byte_count": expected["byte_count"]}
        try:
            asset_url = assets[name]["browser_download_url"]
            downloaded = requests.get(asset_url, timeout=120)
            downloaded.raise_for_status()
            item.update({"public_url": asset_url, "actual_sha256": hashlib.sha256(downloaded.content).hexdigest(),
                         "actual_byte_count": len(downloaded.content)})
            item["matches"] = item["actual_sha256"] == item["expected_sha256"] and item["actual_byte_count"] == item["expected_byte_count"]
        except Exception as exc:
            item.update({"matches": False, "error": f"{type(exc).__name__}: {exc}"})
        verification["assets"].append(item)
        verification["all_hashes_match"] &= item["matches"]
    public_check = root / "public_asset_verification.json"
    csl.write_json(public_check, verification)
    subprocess.run(["gh", "release", "upload", tag, str(public_check), "--repo", os.environ["GITHUB_REPOSITORY"]], check=True)
    if not verification["all_hashes_match"]:
        raise RuntimeError("A public release asset did not pass unauthenticated byte verification")
    # Fail the run after preserving failure evidence rather than silently accept.
    if not receipt["capture_valid"]:
        raise RuntimeError("Official response did not pass completeness checks")
    return receipt


def read_source_metadata():
    # The official server can return 404 to HEAD while GET succeeds. A one-byte
    # range request observes original version headers without full downloads.
    with requests.get(csl.SOURCE, headers={"Cache-Control": "no-cache", "Accept-Encoding": "identity", "Range": "bytes=0-0"}, stream=True, timeout=20) as response:
        response.raise_for_status()
        record = {"observed_at_utc": csl.iso(csl.now()), "url": csl.SOURCE,
                  "http_status": response.status_code, "request_range": "bytes=0-0",
                  "headers": list(response.headers.items()),
                  "etag": response.headers.get("ETag"), "last_modified": response.headers.get("Last-Modified")}
    return record


def observe_metadata():
    if csl.now().date() > datetime(2027, 1, 19, tzinfo=UTC).date():
        print("The requested preservation period is complete; no further observation committed.")
        return
    record = read_source_metadata()
    directory = Path("observations")
    directory.mkdir(exist_ok=True)
    record["purpose"] = "Observe source refresh metadata and maintain an active monitored preservation repository before the January capture."
    (directory / "latest_headers.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "test"
    if mode == "metadata":
        observe_metadata()
        return
    if mode == "test":
        publish_capture("test", "current")
        return
    if mode != "production":
        raise ValueError("Unknown capture mode")
    current = csl.now()
    if current.date() != CUTOFF.date():
        print("No production collection: today is not 1/15/2027 UTC.")
        return
    if current.hour > 12 or (current.hour == 12 and current.minute > 20):
        raise RuntimeError("Production collection started too late to support the planned cutoff protocol")
    # Overlapping independent jobs and 30-second original-header observations
    # reduce the chance of missing a version between two isolated snapshots.
    history = []
    last = publish_capture("production", "start")
    checkpoints = [CUTOFF.replace(hour=11, minute=55),
                   CUTOFF.replace(hour=11, minute=59, second=50),
                   CUTOFF.replace(second=5),
                   CUTOFF.replace(minute=1), CUTOFF.replace(minute=7)]
    pending = [value for value in checkpoints if value > csl.now()]
    finish = CUTOFF.replace(minute=8)
    sequence = 0
    while csl.now() < finish:
        try:
            observed = read_source_metadata()
            history.append(observed)
            print(json.dumps(observed), flush=True)
            changed = observed.get("etag") != last.get("etag") or observed.get("last_modified") != last.get("last_modified")
            due = bool(pending and pending[0] <= csl.now())
            if changed or due:
                sequence += 1
                capture_started = csl.now()
                last = publish_capture("production", f"poll{sequence:03}", history)
                while pending and pending[0] <= capture_started:
                    pending.pop(0)
        except requests.RequestException as exc:
            record = {"observed_at_utc": csl.iso(csl.now()), "error": str(exc)}
            history.append(record)
            print(json.dumps(record), flush=True)
        remaining = (finish - csl.now()).total_seconds()
        if remaining <= 0:
            break
        delay = min(30, remaining)
        if pending:
            delay = min(delay, max(0.1, (pending[0] - csl.now()).total_seconds()))
        time.sleep(delay)


if __name__ == "__main__":
    main()
