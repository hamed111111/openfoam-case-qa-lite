import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from openfoam_case_audit import Audit, render_html


EXAMPLE = ROOT / "examples" / "minimal_case"


class AuditTests(unittest.TestCase):
    def test_reference_case_has_no_critical_findings(self):
        report = Audit(EXAMPLE).run()
        self.assertEqual(report["counts"]["critical"], 0)
        self.assertIn(report["status"], {"PASS_STATIC", "REVIEW"})
        self.assertIn("OpenFOAM Case QA Lite", render_html(report))

    def test_missing_field_patch_blocks_handover(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "case"
            import shutil
            shutil.copytree(EXAMPLE, target)
            pressure = target / "0" / "p"
            pressure.write_text(pressure.read_text().replace("walls { type zeroGradient; }", ""))
            report = Audit(target).run()
            codes = {item["code"] for item in report["findings"] if item["severity"] == "critical"}
            self.assertIn("PATCH_COVERAGE", codes)
            self.assertEqual(report["status"], "BLOCK")


if __name__ == "__main__":
    unittest.main()
