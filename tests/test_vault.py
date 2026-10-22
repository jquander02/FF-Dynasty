import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from dynasty import vault


class VaultTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        for name, data in {"meta": {"league": "x", "my_roster_id": 3, "value_dates": ["2026-10-16"]},
                           "teams": [{"roster_id": 3, "team": "A", "wins": 4, "p_title": 0.3, "value_by_pos": {"QB": 1}}],
                           "trades": [], "history": [],
                           "rosters": {"3": [{"id": "1"}]}, "values": [{"id": "1", "val": 10}], "weekly": {"week": 6}}.items():
            (self.dir / f"{name}.json").write_text(json.dumps(data))

    def tearDown(self):
        shutil.rmtree(self.dir)

    def test_lock_moves_private_files_into_vault(self):
        out = vault.lock(self.dir, "4821")
        self.assertEqual(out["locked"], 3)
        left = sorted(p.name for p in self.dir.glob("*.json"))
        self.assertEqual(left, ["history.json", "meta.json", "teams.json", "trades.json", "vault.json"])
        v = json.loads((self.dir / "vault.json").read_text())
        self.assertEqual(v["files"], ["meta", "rosters", "teams", "values", "weekly"])
        self.assertNotIn("val", v["ct"])
        files = vault.open_vault(v, "4821")
        self.assertEqual(files["values"][0]["val"], 10)
        # open teams/meta keep only what the open screens show; the full versions are in the vault
        teams = json.loads((self.dir / "teams.json").read_text())
        self.assertEqual(teams, [{"roster_id": 3, "team": "A", "wins": 4}])
        self.assertEqual(files["teams"][0]["p_title"], 0.3)
        meta = json.loads((self.dir / "meta.json").read_text())
        self.assertNotIn("my_roster_id", meta)
        self.assertEqual(files["meta"]["my_roster_id"], 3)
        with self.assertRaises(Exception):
            vault.open_vault(v, "0000")

    def test_backup_file_round_trip(self):
        src, enc, back = self.dir / "db.gz", self.dir / "db.enc", self.dir / "db.out"
        src.write_bytes(b"sqlite bytes" * 1000)
        vault.encrypt_file(src, enc, "482193")
        self.assertNotIn(b"sqlite", enc.read_bytes())
        vault.decrypt_file(enc, back, "482193")
        self.assertEqual(back.read_bytes(), src.read_bytes())
        with self.assertRaises(Exception):
            vault.decrypt_file(enc, back, "000000")

    def test_no_pin_leaves_everything_open(self):
        (self.dir / "vault.json").write_text("{}")        # left over from a locked run
        self.assertEqual(vault.lock(self.dir, None)["locked"], 0)
        self.assertTrue((self.dir / "values.json").exists())
        self.assertFalse((self.dir / "vault.json").exists())


if __name__ == "__main__":
    unittest.main()
