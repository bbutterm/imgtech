import tempfile
import unittest
from pathlib import Path

from api import job_queue as q


class JobQueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        q.JOB_DIR = Path(self.tmp.name) / "jobs"
        q.DB_PATH = q.JOB_DIR / "jobs.sqlite3"
        q.init_db()

    def tearDown(self):
        self.tmp.cleanup()

    def test_job_lifecycle_and_owner_isolation(self):
        job_id = q.new_job("user-a", b"pdf-a", b"pdf-b")
        self.assertEqual(q.get_job(job_id, "user-b"), None)
        row = q.get_job(job_id, "user-a")
        self.assertEqual(row["state"], "pending")
        self.assertTrue(Path(row["file1"]).exists())

        claimed = q.claim_next()
        self.assertEqual(claimed["id"], job_id)
        self.assertIsNone(q.claim_next())
        q.update_progress(job_id, stage="comparing", done=1, total=2)
        q.finish_job(job_id, {"pages": [], "totalPairs": 2})

        result = q.public_job(q.get_job(job_id, "user-a"))
        self.assertEqual(result["state"], "succeeded")
        self.assertEqual(result["result"]["totalPairs"], 2)
        self.assertFalse(Path(row["file1"]).exists())

    def test_failed_job_is_visible_without_file_leak(self):
        job_id = q.new_job("user-a", b"a", b"b")
        row = q.get_job(job_id, "user-a")
        q.fail_job(job_id, "bad pdf")
        result = q.public_job(q.get_job(job_id, "user-a"))
        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["error"], "bad pdf")
        self.assertFalse(Path(row["file2"]).exists())


if __name__ == "__main__":
    unittest.main()
