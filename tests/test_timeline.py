import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("node"), "需要 node 来运行前端纯函数检查")
class TimelineModelTests(unittest.TestCase):
    def test_timeline_model_and_markup(self):
        result = subprocess.run(["node", str(ROOT / "tests" / "timeline_model.test.js")], capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("timeline ok", result.stdout)


if __name__ == "__main__":
    unittest.main()
