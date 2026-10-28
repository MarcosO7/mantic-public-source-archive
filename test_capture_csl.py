import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

spec = importlib.util.spec_from_file_location("capture_csl", Path(__file__).with_name("capture_csl.py"))
csl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(csl)


def row(key="A", countries=("CN", "HK"), source=csl.SOURCE_LABEL):
    return {"id": key, "source": source, "addresses": [{"country": value} for value in countries]}


class CaptureValidation(unittest.TestCase):
    def test_complete_normalization_and_raw_id_dedup(self):
        rows = [row(countries=(" cn ", "CN", "hk")), row(countries=("HK", "CN")), row("B", ())]
        result = csl.assess_payload({"total": 3, "results": rows})
        self.assertEqual(result["distinct_selected_ids"], 2)
        self.assertEqual(result["multi_country_selected_ids"], 1)

    def test_other_sources_do_not_conflict_with_selected_id(self):
        rows = [row(), row(countries=("US",), source="Other source")]
        self.assertEqual(csl.assess_payload({"total": 2, "results": rows})["multi_country_selected_ids"], 1)

    def test_missing_or_truncated_population_rejected(self):
        for payload in ({}, {"results": [], "total": 1}, {"results": [], "total": True}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                csl.assess_payload(payload)

    def test_missing_selected_id_rejected(self):
        with self.assertRaises(ValueError):
            csl.assess_payload({"results": [row(key="")], "total": 1})

    def test_conflicting_selected_duplicate_rejected(self):
        with self.assertRaises(ValueError):
            csl.assess_payload({"results": [row(), row(countries=("CN",))], "total": 2})

    def test_invalid_http_date_is_not_assumed(self):
        self.assertIsNone(csl.parse_http_date(None))
        self.assertIsNone(csl.parse_http_date("not a date"))

    def test_test_mode_never_submits_public_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.json"
            config.write_text(json.dumps({"stable_output_directory": str(root / "records"), "cutoff_utc": "2027-01-15T12:00:00Z"}))
            with patch.object(csl, "capture_source"), patch.object(csl, "verify_public_captures"), patch.object(csl, "request_public_archive") as submit:
                csl.main(["--config", str(config), "--mode", "test", "--archive"])
                submit.assert_not_called()

    def test_evidence_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "original.json"
            csl.write_json(path, {"original": True})
            with self.assertRaises(FileExistsError):
                csl.write_json(path, {"original": False})
            self.assertEqual(json.loads(path.read_text()), {"original": True})

    def test_legacy_redirect_is_not_claimed_as_verified_public_copy(self):
        api = MagicMock(status_code=401, content=b'{"message":"unauthorized"}', headers={})
        legacy = MagicMock(status_code=200, url="https://web.archive.org/web/20270115115500/" + csl.SOURCE, headers={}, history=[])
        legacy.raw.read.return_value = b"prefix"
        context = MagicMock()
        context.__enter__.return_value = legacy
        with tempfile.TemporaryDirectory() as directory, patch.object(csl.requests, "post", return_value=api), patch.object(csl.requests, "get", return_value=context):
            result = csl.request_public_archive(Path(directory))
            self.assertTrue(result["archive_submission_observed"])
            self.assertFalse(result["public_capture_verified"])

    def test_archive_rate_limit_does_not_trigger_extra_legacy_submission(self):
        api = MagicMock(status_code=429, content=b'{"message":"rate limited"}', headers={})
        with tempfile.TemporaryDirectory() as directory, patch.object(csl.requests, "post", return_value=api), patch.object(csl.requests, "get") as legacy:
            result = csl.request_public_archive(Path(directory))
            legacy.assert_not_called()
            self.assertFalse(result["public_capture_verified"])

    def test_truncated_archive_population_cannot_pass(self):
        cdx = MagicMock(content=json.dumps([["timestamp", "original", "statuscode", "digest", "length"], ["20270115115500", csl.SOURCE, "200", "digest", "10"]]).encode())
        replay = MagicMock(content=b'{"results":[],"total":1}', headers={"x-archive-orig-last-modified":"Fri, 15 Jan 2027 10:00:00 GMT", "x-archive-orig-content-length":"24"})
        with tempfile.TemporaryDirectory() as directory, patch.object(csl.requests, "get", side_effect=[cdx, replay]):
            result = csl.verify_public_captures(Path(directory), {"cutoff_utc":"2027-01-15T12:00:00Z"})
            self.assertFalse(result["public_capture_verified"])
            self.assertFalse(result["verified_replays"][0]["verified"])


if __name__ == "__main__":
    unittest.main()
