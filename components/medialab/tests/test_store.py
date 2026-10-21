import tempfile
import unittest
from pathlib import Path

from medialab.store import STATUS_DONE, STATUS_FAILED, STATUS_INTERRUPTED, STATUS_RUNNING, Store


class TestStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "test.db")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_crud(self):
        task_id = self.store.create_task("https://x.com/v", "generic", {"format": "best"})
        self.assertEqual(self.store.get_task(task_id)["status"], "queued")
        self.store.set_status(task_id, STATUS_RUNNING)
        self.assertEqual(self.store.get_task(task_id)["status"], STATUS_RUNNING)
        self.store.set_out_dir(task_id, "C:\\delivery")
        self.store.add_file(task_id, "video", "C:\\delivery\\a.mp4", 123)
        self.store.set_status(task_id, STATUS_DONE)
        t = self.store.get_task(task_id)
        self.assertEqual(t["status"], STATUS_DONE)
        self.assertEqual(t["files"], [{"kind": "video", "path": "C:\\delivery\\a.mp4", "size": 123}])
        self.assertEqual(t["params"]["format"], "best")
        self.assertIsNotNone(t["done_at"])

    def test_failed_keeps_error(self):
        task_id = self.store.create_task("u", "bilibili", {})
        self.store.set_status(task_id, STATUS_FAILED, error_code="network", error_detail="503")
        t = self.store.get_task(task_id)
        self.assertEqual(t["error_code"], "network")
        self.assertEqual(t["error_detail"], "503")

    def test_list(self):
        for i in range(3):
            self.store.create_task(f"url{i}", "generic", {})
        rows = self.store.list_tasks()
        self.assertEqual(len(rows), 3)

    def test_mark_interrupted(self):
        """上次进程遗留的 queued/running 任务统一标记为 interrupted，done/failed 不受影响。"""
        q = self.store.create_task("u1", "generic", {})
        r = self.store.create_task("u2", "generic", {})
        self.store.set_status(r, STATUS_RUNNING)
        d = self.store.create_task("u3", "generic", {})
        self.store.set_status(d, STATUS_DONE)
        self.store.mark_interrupted()
        self.assertEqual(self.store.get_task(q)["status"], STATUS_INTERRUPTED)
        self.assertEqual(self.store.get_task(r)["status"], STATUS_INTERRUPTED)
        self.assertEqual(self.store.get_task(d)["status"], STATUS_DONE)


if __name__ == "__main__":
    unittest.main()
