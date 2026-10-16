import unittest
from pathlib import Path

import yaml

WF = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "refresh.yml"


class WorkflowTest(unittest.TestCase):
    """Guards the database round-trip: losing the save step silently resets data every run."""

    def setUp(self):
        self.steps = [s.get("name", s.get("uses")) for s in yaml.safe_load(WF.read_text())["jobs"]["refresh"]["steps"]]

    def test_database_round_trip(self):
        order = ["Restore database", "Unpack database", "Refresh data", "Pack database", "Save database",
                 "Deploy to GitHub Pages"]
        for name in order:
            self.assertIn(name, self.steps)
        idx = [self.steps.index(n) for n in order]
        self.assertEqual(idx, sorted(idx))

    def test_cache_keys_match(self):
        text = WF.read_text()
        self.assertIn("restore-keys: dbz-", text)
        self.assertIn("key: dbz-${{ github.run_id }}", text)


if __name__ == "__main__":
    unittest.main()
