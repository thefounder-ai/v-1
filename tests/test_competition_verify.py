import unittest
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


class CompetitionVerifyTests(unittest.TestCase):
    def test_competition_verify_static_passes(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "competition_verify.py"), "--static"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Submission verify: PASSED", result.stdout)

    def test_readme_has_live_submission_block(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("v-1-ora9.onrender.com", readme)
        self.assertIn("competition_verify.py", readme)

    def test_quality_workflow_runs_competition_verify(self):
        workflow = (ROOT / ".github" / "workflows" / "quality.yml").read_text(encoding="utf-8")
        self.assertIn("competition_verify.py --ci", workflow)


if __name__ == "__main__":
    unittest.main()
