import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "birdsync.py"


class ReconciliationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.input = self.base / "input.json"
        self.mapping = self.base / "mapping.json"
        self.first = self.base / "first"
        self.second = self.base / "second"
        self.mapping.write_text("{}\n", encoding="utf-8")
        self.report = {
            "serial_id": "source-1",
            "start_time": "2026-11-10 09:00:00",
            "end_time": "2026-11-10 10:00:00",
            "province_name": "浙江省",
            "city_name": "杭州市",
            "point_name": "南湖",
            "latitude": 30.0,
            "longitude": 120.0,
            "protocol": "historical",
            "number_of_observers": 1,
            "eye_all_birds": "1",
            "real_quantity": 1,
            "obs": [{"latinname": "Passer montanus", "taxon_count": 1}],
        }
        self.write_input()

    def tearDown(self):
        self.temp.cleanup()

    def write_input(self):
        self.input.write_text(json.dumps({"reports": [self.report]}), encoding="utf-8")

    def run_cli(self, *args, expected=0):
        result = subprocess.run(
            [sys.executable, str(CLI), *map(str, args)],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(expected, result.returncode, result.stderr or result.stdout)
        stream = result.stdout if result.stdout else result.stderr
        return json.loads(stream)

    def prepare(self, output, ledger=None):
        args = [
            "prepare", "--input", self.input, "--output-dir", output,
            "--mapping", self.mapping,
        ]
        if ledger:
            args.extend(["--ledger", ledger])
        return self.run_cli(*args)

    def test_existing_remote_is_receipted_and_skipped(self):
        first = self.prepare(self.first)
        self.assertEqual("ready_for_import", first["status"])

        marked = self.run_cli(
            "mark-existing",
            "--manifest", self.first / "manifest.json",
            "--source-id", "source-1",
            "--remote-id", "S123456",
            "--evidence-url", "https://ebird.org/checklist/S123456",
            "--matched-on", "date",
            "--matched-on", "location",
        )
        self.assertEqual(1, marked["marked_existing_remote"])
        self.assertTrue(Path(marked["receipt"]).is_file())

        second = self.prepare(self.second, self.first / "ledger.json")
        self.assertEqual("already_reconciled", second["status"])
        self.assertEqual(1, second["existing_remote"])
        self.assertEqual(0, second["csv_files"])

        review = json.loads((self.second / "review.json").read_text(encoding="utf-8"))
        self.assertEqual("already_exists_remote", review["issues"][0]["code"])

    def test_changed_source_becomes_conflict(self):
        self.prepare(self.first)
        self.run_cli(
            "mark-existing",
            "--manifest", self.first / "manifest.json",
            "--source-id", "source-1",
            "--remote-id", "S123456",
            "--evidence-url", "https://ebird.org/checklist/S123456",
            "--matched-on", "date",
        )
        self.report["obs"][0]["taxon_count"] = 2
        self.write_input()
        result = self.prepare(self.second, self.first / "ledger.json")
        self.assertEqual("review_required", result["status"])
        self.assertEqual(1, result["conflicts"])

    def test_evidence_url_must_match_remote_id(self):
        self.prepare(self.first)
        result = self.run_cli(
            "mark-existing",
            "--manifest", self.first / "manifest.json",
            "--source-id", "source-1",
            "--remote-id", "S123456",
            "--evidence-url", "https://ebird.org/checklist/S999999",
            "--matched-on", "date",
            expected=1,
        )
        self.assertEqual("error", result["status"])

    def test_china_state_uses_short_import_code(self):
        self.prepare(self.first)
        row = (self.first / "ebird-import-001.csv").read_text(encoding="utf-8").split(",")
        self.assertEqual("ZJ", row[10])
        self.assertEqual("CN", row[11])

    def test_import_receipt_preserves_prior_remote_match(self):
        self.prepare(self.first)
        self.run_cli(
            "mark-existing",
            "--manifest", self.first / "manifest.json",
            "--source-id", "source-1",
            "--remote-id", "S123456",
            "--evidence-url", "https://ebird.org/checklist/S123456",
            "--matched-on", "date",
        )
        acceptance = self.first / "accepted.html"
        acceptance.write_text("accepted", encoding="utf-8")
        result = self.run_cli(
            "mark-imported",
            "--manifest", self.first / "manifest.json",
            "--remote-service", "ebird",
            "--remote-id", "S654321",
            "--evidence-url", "https://ebird.org/checklist/S654321",
            "--batch-id", "F123",
            "--acceptance-receipt", acceptance,
        )
        self.assertEqual("S654321", result["remote_id"])
        ledger = json.loads((self.first / "ledger.json").read_text(encoding="utf-8"))
        entry = ledger["records"]["source-1"]
        self.assertEqual("imported", entry["status"])
        self.assertEqual("S123456", entry["prior_remote_reconciliation"]["remote_id"])
        self.assertTrue(Path(entry["receipt"]).is_file())


if __name__ == "__main__":
    unittest.main()
