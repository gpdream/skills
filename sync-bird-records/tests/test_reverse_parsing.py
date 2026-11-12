import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("birdsync_reverse_test", ROOT / "scripts" / "birdsync.py")
BIRDSYNC = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = BIRDSYNC
SPEC.loader.exec_module(BIRDSYNC)


class ReverseParsingTest(unittest.TestCase):
    def test_authenticated_personal_index_parses_owned_checklists(self):
        html = """
        <html><body><ol id="place-species-observed-results">
          <li id="checklist-S408326226" class="ResultsStats ResultsStats--manageMyChecklists">
            <div class="ResultsStats-title"><a href="/checklist/S408326226" title="Checklist S408326226 - Friday, November 6, 2026, 6:00 AM">
              <span class="Heading-main">6 Nov 2026</span><span class="Heading-sub">6:00 AM</span>
            </a></div>
            <div class="ResultsStats-details-location">镜湖 (Jinghu Lake)</div>
          </li>
        </ol></body></html>
        """
        rows = BIRDSYNC.parse_ebird_personal_index(html)
        self.assertEqual("S408326226", rows[0]["source_id"])
        self.assertEqual("2026-11-06 06:00:00", rows[0]["observed_at"])
        self.assertEqual("镜湖 (Jinghu Lake)", rows[0]["location_name"])

    def test_login_page_is_not_accepted_as_personal_index(self):
        with self.assertRaises(BIRDSYNC.AuthRequired):
            BIRDSYNC.parse_ebird_personal_index("<html><title>Sign in to eBird</title></html>")

    def test_authenticated_checklist_parses_privacy_effort_and_observations(self):
        html = """
        <html><body>
          <h1 id="content">Checklist S123456789</h1>
          <h2 id="primary-details"></h2>
          <time datetime="2026-11-06T06:00"></time>
          <a href="/hotspot/L1">镜湖</a>
          <a href="https://www.google.com/maps/search/?api=1&query=30.1,120.2">map</a>
          <div title="Protocol: Stationary"></div>
          <span title="Observers:4"></span>
          <span title="Duration:2 hour(s)"></span>
          <p id="status-info">Are you submitting a complete checklist? No</p>
          <p>Hidden checklist.</p>
          <section aria-labelledBy="checklist-comments"><p>source note</p></section>
          <li data-observation><section class="Observation">
            <a data-species-code="eutspa"><span class="Heading-main">麻雀 (树麻雀)</span><span class="Heading-sub--sci">Passer montanus</span></a>
            <div class="Observation-numberObserved"><span><span>Number observed:</span><span>3</span></span></div>
          </section></li>
        </body></html>
        """
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "checklist.html"
            path.write_text(html, encoding="utf-8")
            parsed = BIRDSYNC.parse_ebird_checklist_html(path)
        self.assertEqual("S123456789", parsed["source_id"])
        self.assertEqual("2026-11-06 08:00:00", parsed["end_time"])
        self.assertEqual(4, parsed["observers"])
        self.assertFalse(parsed["complete"])
        self.assertTrue(parsed["hidden"])
        self.assertEqual(3, parsed["observations"][0]["count"])
        self.assertEqual(["麻雀", "树麻雀"], BIRDSYNC.normalize_ebird_common_name("麻雀 (树麻雀)"))

    def test_localized_checklist_markup_is_supported(self):
        html = """
        <html><body>
          <h1 id="content">记录 S987654321</h1>
          <time datetime="2026-11-06T06:00"></time>
          <a href="/hotspot/L2">镜湖</a>
          <a href="https://www.google.com/maps/search/?api=1&query=30.1,120.2">地图</a>
          <section id="other-details-effort">
            <div class="Heading" title="观测方法: 定点观鸟"><span class="Heading-main">定点观鸟</span></div>
            <span title="记录人：4"><span class="Badge-label">4</span></span>
            <span title="观鸟时长：2小时"><span class="Badge-label">2小时</span></span>
          </section>
          <p id="status-info"><em>您提交了所有鸟种么？</em><strong>否</strong></p>
          <section aria-labelledby="flagged"><p><strong>Hidden checklist.</strong></p></section>
          <section aria-labelledBy="checklist-comments"><p>来源备注</p></section>
          <li data-observation><section class="Observation">
            <a data-species-code="eutspa"><span class="Heading-main">麻雀</span><span class="Heading-sub--sci">Passer montanus</span></a>
            <div class="Observation-numberObserved"><span><span class="is-visuallyHidden">观察到的数量:</span><span>3</span></span></div>
          </section></li>
        </body></html>
        """
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "localized.html"
            path.write_text(html, encoding="utf-8")
            parsed = BIRDSYNC.parse_ebird_checklist_html(path)
        self.assertEqual("S987654321", parsed["source_id"])
        self.assertEqual("定点观鸟", parsed["protocol"])
        self.assertEqual(4, parsed["observers"])
        self.assertEqual(2.0, parsed["duration_hours"])
        self.assertFalse(parsed["complete"])
        self.assertTrue(parsed["hidden"])
        self.assertEqual(3, parsed["observations"][0]["count"])

    def test_challenge_or_login_page_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "challenge.html"
            path.write_text("<title>Making sure you're not a bot!</title>", encoding="utf-8")
            with self.assertRaises(BIRDSYNC.BirdSyncError):
                BIRDSYNC.parse_ebird_checklist_html(path)


if __name__ == "__main__":
    unittest.main()
