import asyncio
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("birdsync_auth_test", ROOT / "scripts" / "birdsync.py")
BIRDSYNC = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = BIRDSYNC
SPEC.loader.exec_module(BIRDSYNC)


class AuthPreflightTest(unittest.TestCase):
    def test_ready_only_after_both_services_validate(self):
        async def fake_validate(service):
            return {"service": service, "status": "valid", "validation": "valid"}

        with patch.object(BIRDSYNC, "validate_service", side_effect=fake_validate):
            result = asyncio.run(BIRDSYNC.preflight("birdreport-to-ebird"))

        self.assertEqual("ready", result["status"])
        self.assertEqual([], result["auth_needed"])
        self.assertEqual(["birdreport", "ebird"], list(result["services"]))

    def test_only_failed_service_is_returned_for_one_time_handoff(self):
        async def fake_validate(service):
            if service == "ebird":
                return {
                    **BIRDSYNC.auth_guide(service),
                    "status": "invalid",
                    "validation": "invalid",
                }
            return {"service": service, "status": "valid", "validation": "valid"}

        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(BIRDSYNC, "validate_service", side_effect=fake_validate):
                result = asyncio.run(BIRDSYNC.preflight("birdreport-to-ebird", temporary))
            state = Path(temporary) / "resume.json"
            self.assertTrue(state.is_file())
            self.assertEqual(0o600, os.stat(state).st_mode & 0o777)

        self.assertEqual("auth_required", result["status"])
        self.assertEqual(["ebird"], result["auth_needed"])
        self.assertEqual(1, len(result["next_actions"]))
        self.assertIn("--probe", result["next_actions"][0]["probe_command"])

    def test_network_failure_does_not_trigger_relogin(self):
        async def fake_validate(service):
            return {"service": service, "status": "unreachable", "validation": "unreachable"}

        with patch.object(BIRDSYNC, "validate_service", side_effect=fake_validate):
            result = asyncio.run(BIRDSYNC.preflight("ebird-to-birdreport"))

        self.assertEqual("unreachable", result["status"])
        self.assertEqual([], result["auth_needed"])
        self.assertCountEqual(["ebird", "birdreport"], result["unreachable"])


if __name__ == "__main__":
    unittest.main()
