import unittest
from pathlib import Path

import yaml

WF = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "refresh.yml"


class WorkflowTest(unittest.TestCase):
    """Guards the database round-trip and the deploy: losing a step silently resets data or the site."""

    def setUp(self):
        self.wf = yaml.safe_load(WF.read_text())
        self.jobs = self.wf["jobs"]
        self.steps = {j: [s.get("name", s.get("uses")) for s in v["steps"]] for j, v in self.jobs.items()}

    def test_database_round_trip(self):
        order = ["Restore database", "Restore from backup if the cache is gone", "Unpack database", "Refresh data",
                 "Pack database", "Save database", "Back up database", "Upload app"]
        steps = self.steps["build"]
        for name in order:
            self.assertIn(name, steps)
        idx = [steps.index(n) for n in order]
        self.assertEqual(idx, sorted(idx))

    def test_save_only_after_success(self):
        for s in self.jobs["build"]["steps"]:
            if s.get("name") in ("Pack database", "Save database", "Back up database"):
                self.assertEqual(s.get("if"), "success()")

    def test_checkpoint_before_packing(self):
        pack = next(s for s in self.jobs["build"]["steps"] if s.get("name") == "Pack database")
        self.assertIn("wal_checkpoint", pack["run"])

    def test_cache_keys_match(self):
        text = WF.read_text()
        self.assertIn("restore-keys: dbz-", text)
        self.assertIn("key: dbz-${{ github.run_id }}", text)

    def test_jobs_and_permissions(self):
        self.assertEqual(self.wf["permissions"], {})
        self.assertNotIn("write", str(self.jobs["build"]["permissions"]))
        self.assertEqual(self.jobs["snapshot"]["permissions"], {"contents": "write"})
        self.assertTrue(self.jobs["snapshot"]["continue-on-error"])          # never blocks the deploy
        self.assertEqual(self.jobs["deploy"]["needs"], "build")
        self.assertEqual(set(self.jobs["deploy"]["permissions"]), {"pages", "id-token"})

    def test_actions_pinned_to_commits(self):
        for job in self.jobs.values():
            for s in job["steps"]:
                if "uses" in s:
                    ref = s["uses"].split("@")[1]
                    self.assertRegex(ref, r"^[0-9a-f]{40}$", s["uses"])

    def test_no_secrets_needed(self):
        self.assertNotIn("secrets.", WF.read_text())

    def test_quick_runs_cover_game_nights(self):
        crons = [c["cron"] for c in self.wf[True]["schedule"]]     # yaml reads "on" as True
        self.assertIn("17 9 * * *", crons)
        days = " ".join(crons)
        for d in ("0,1,6", "2,5"):
            self.assertIn(d, days)


if __name__ == "__main__":
    unittest.main()
