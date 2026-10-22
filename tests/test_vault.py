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
        for name, data in {"meta": {"league": "x"}, "teams": [1], "trades": [], "history": [],
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
        self.assertEqual(v["files"], ["rosters", "values", "weekly"])
        self.assertNotIn("val", v["ct"])
        files = vault.open_vault(v, "4821")
        self.assertEqual(files["values"][0]["val"], 10)
        with self.assertRaises(Exception):
            vault.open_vault(v, "0000")

    def test_no_pin_leaves_everything_open(self):
        (self.dir / "vault.json").write_text("{}")        # left over from a locked run
        self.assertEqual(vault.lock(self.dir, None)["locked"], 0)
        self.assertTrue((self.dir / "values.json").exists())
        self.assertFalse((self.dir / "vault.json").exists())


if __name__ == "__main__":
    unittest.main()
