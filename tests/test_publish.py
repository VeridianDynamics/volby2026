import tempfile
import unittest
from pathlib import Path

from publish import export_site, restore
from server import SOURCES, Store


class PublishingTests(unittest.TestCase):
    def test_history_survives_cloud_restore_and_duplicate_poll(self):
        raw = dict(generovano="2026-10-10T20:00:00", param=dict(kodzastup=551171),
                   prehled=[15, 1, 4, 1, 25, 100, 50, 50, 0, 50, 50, 100, 600],
                   vysledky=[[1, "List A", 400, 66.67, 15], [2, "List B", 200, 33.33, 15]])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = Store(root / "local.sqlite3")
            store.save(SOURCES[0], raw)
            export_site(store, root / "site")
            cloud = Store(root / "cloud.sqlite3")
            restore(cloud, root / "site")
            self.assertEqual(cloud.export("jundrov"), store.export("jundrov"))
            cloud.save(SOURCES[0], raw)
            self.assertEqual(len(cloud.export("jundrov")), 1)
            updated = dict(raw, generovano="2026-10-10T20:05:00")
            cloud.save(SOURCES[0], updated)
            self.assertEqual(len(cloud.export("jundrov")), 2)
            self.assertEqual(cloud.dashboard(300, None)["sources"][0]["history"][0]["id"],
                             store.dashboard(300, None)["sources"][0]["history"][0]["id"])
            export_site(cloud, root / "site")
            html = (root / "site" / "index.html").read_text()
            self.assertIn('name="published-dashboard"', html)
            self.assertIn('href="./style.css"', html)
