"""Tests for backup.py."""
import os
import subprocess
import unittest
from unittest.mock import MagicMock, patch, call


class TestRunPgDump(unittest.TestCase):
    @patch("backup.backup.subprocess.run")
    def test_creates_compressed_backup_file(self, mock_run):
        import tempfile
        from backup.backup import run_pg_dump
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = b"-- PostgreSQL dump\nSELECT 1;\n"
        mock_run.return_value.stderr = b""

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = os.path.join(tmpdir, "test.sql.gz")
            result = run_pg_dump("myuser", "alphadivision", output_path)

        self.assertTrue(result)
        mock_run.assert_called_once_with(
            ["docker", "compose", "exec", "-T", "postgres",
             "pg_dump", "-U", "myuser", "alphadivision"],
            cwd=unittest.mock.ANY,
            capture_output=True,
            timeout=300,
        )

    @patch("backup.backup.subprocess.run")
    def test_returns_false_on_nonzero_exit(self, mock_run):
        import tempfile
        from backup.backup import run_pg_dump
        mock_run.return_value.returncode = 1
        mock_run.return_value.stdout = b""
        mock_run.return_value.stderr = b"error: connection refused"

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = os.path.join(tmpdir, "test.sql.gz")
            result = run_pg_dump("myuser", "alphadivision", output_path)

        self.assertFalse(result)

    @patch("backup.backup.subprocess.run")
    def test_returns_false_on_exception(self, mock_run):
        import tempfile
        from backup.backup import run_pg_dump
        mock_run.side_effect = subprocess.TimeoutExpired(["docker"], 300)

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = os.path.join(tmpdir, "test.sql.gz")
            result = run_pg_dump("myuser", "alphadivision", output_path)

        self.assertFalse(result)

    @patch("backup.backup.subprocess.run")
    def test_writes_gzipped_content(self, mock_run):
        import tempfile, gzip as gz
        from backup.backup import run_pg_dump
        sql_bytes = b"-- PostgreSQL dump\nSELECT 1;\n"
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = sql_bytes
        mock_run.return_value.stderr = b""

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = os.path.join(tmpdir, "test.sql.gz")
            run_pg_dump("myuser", "alphadivision", output_path)
            with gz.open(output_path, "rb") as f:
                content = f.read()

        self.assertEqual(content, sql_bytes)


class TestUploadToOci(unittest.TestCase):
    @patch("backup.backup.subprocess.run")
    def test_calls_oci_put_with_correct_args(self, mock_run):
        from backup.backup import upload_to_oci
        mock_run.return_value.returncode = 0
        mock_run.return_value.stderr = b""
        result = upload_to_oci("my-bucket", "my-namespace", "alphadivision-20260516.sql.gz", "/backups/alphadivision-20260516.sql.gz")
        self.assertTrue(result)
        mock_run.assert_called_once_with(
            [
                "oci", "os", "object", "put",
                "--bucket-name", "my-bucket",
                "--namespace", "my-namespace",
                "--name", "alphadivision-20260516.sql.gz",
                "--file", "/backups/alphadivision-20260516.sql.gz",
                "--force",
            ],
            capture_output=True,
            timeout=120,
        )

    @patch("backup.backup.subprocess.run")
    def test_returns_false_on_nonzero_exit(self, mock_run):
        from backup.backup import upload_to_oci
        mock_run.return_value.returncode = 1
        mock_run.return_value.stderr = b"ServiceError"
        result = upload_to_oci("my-bucket", "my-namespace", "obj.sql.gz", "/backups/obj.sql.gz")
        self.assertFalse(result)

    @patch("backup.backup.subprocess.run")
    def test_returns_false_on_exception(self, mock_run):
        from backup.backup import upload_to_oci
        mock_run.side_effect = subprocess.TimeoutExpired(["oci"], 120)
        result = upload_to_oci("my-bucket", "my-namespace", "obj.sql.gz", "/backups/obj.sql.gz")
        self.assertFalse(result)


class TestPruneLocalBackups(unittest.TestCase):
    def _make_backup_files(self, tmpdir: str, names: list) -> list:
        paths = []
        for name in names:
            p = os.path.join(tmpdir, name)
            with open(p, "w") as f:
                f.write("dummy")
            paths.append(p)
        return paths

    def test_deletes_files_older_than_retention(self):
        import tempfile
        from backup.backup import prune_local_backups
        from datetime import date, timedelta

        today = date(2026, 5, 16)
        old_date = today - timedelta(days=31)
        keep_date = today - timedelta(days=10)

        with tempfile.TemporaryDirectory() as tmpdir:
            old_name = f"alphadivision-{old_date.strftime('%Y%m%d')}.sql.gz"
            keep_name = f"alphadivision-{keep_date.strftime('%Y%m%d')}.sql.gz"
            self._make_backup_files(tmpdir, [old_name, keep_name])

            deleted = prune_local_backups(tmpdir, retention_days=30, today=today)

        self.assertEqual(deleted, [old_name])

    def test_keeps_files_within_retention(self):
        import tempfile
        from backup.backup import prune_local_backups
        from datetime import date, timedelta

        today = date(2026, 5, 16)
        keep_date = today - timedelta(days=29)

        with tempfile.TemporaryDirectory() as tmpdir:
            keep_name = f"alphadivision-{keep_date.strftime('%Y%m%d')}.sql.gz"
            self._make_backup_files(tmpdir, [keep_name])

            deleted = prune_local_backups(tmpdir, retention_days=30, today=today)

        self.assertEqual(deleted, [])

    def test_ignores_non_backup_files(self):
        import tempfile
        from backup.backup import prune_local_backups
        from datetime import date

        today = date(2026, 5, 16)

        with tempfile.TemporaryDirectory() as tmpdir:
            self._make_backup_files(tmpdir, ["somefile.txt"])
            deleted = prune_local_backups(tmpdir, retention_days=30, today=today)

        self.assertEqual(deleted, [])

    def test_returns_empty_list_for_nonexistent_dir(self):
        from backup.backup import prune_local_backups
        from datetime import date
        deleted = prune_local_backups("/nonexistent/path", retention_days=30, today=date(2026, 5, 16))
        self.assertEqual(deleted, [])


class TestPruneOciBackups(unittest.TestCase):
    def _list_output(self, items):
        import json
        return json.dumps({"data": items}).encode()

    @patch("backup.backup.subprocess.run")
    def test_deletes_old_objects(self, mock_run):
        from backup.backup import prune_oci_backups
        from datetime import date, timedelta

        today = date(2026, 5, 16)
        old_date = today - timedelta(days=31)
        keep_date = today - timedelta(days=10)

        list_output = self._list_output([
            {"name": f"alphadivision-{old_date.strftime('%Y%m%d')}.sql.gz",
             "time-created": f"{old_date.isoformat()}T03:00:00+00:00"},
            {"name": f"alphadivision-{keep_date.strftime('%Y%m%d')}.sql.gz",
             "time-created": f"{keep_date.isoformat()}T03:00:00+00:00"},
        ])
        # First call = list, second call = delete
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=list_output, stderr=b""),
            MagicMock(returncode=0, stdout=b"", stderr=b""),
        ]

        deleted = prune_oci_backups("my-bucket", "my-namespace", retention_days=30, today=today)

        self.assertEqual(len(deleted), 1)
        self.assertIn(f"alphadivision-{old_date.strftime('%Y%m%d')}.sql.gz", deleted)
        # Verify delete was called with the correct object name
        delete_call_args = mock_run.call_args_list[1][0][0]
        self.assertIn("--name", delete_call_args)

    @patch("backup.backup.subprocess.run")
    def test_keeps_objects_within_retention(self, mock_run):
        from backup.backup import prune_oci_backups
        from datetime import date, timedelta

        today = date(2026, 5, 16)
        keep_date = today - timedelta(days=5)

        list_output = self._list_output([
            {"name": f"alphadivision-{keep_date.strftime('%Y%m%d')}.sql.gz",
             "time-created": f"{keep_date.isoformat()}T03:00:00+00:00"},
        ])
        mock_run.return_value = MagicMock(returncode=0, stdout=list_output, stderr=b"")

        deleted = prune_oci_backups("my-bucket", "my-namespace", retention_days=30, today=today)

        self.assertEqual(deleted, [])
        self.assertEqual(mock_run.call_count, 1)  # only list, no delete

    @patch("backup.backup.subprocess.run")
    def test_returns_empty_on_list_failure(self, mock_run):
        from backup.backup import prune_oci_backups
        from datetime import date
        mock_run.return_value = MagicMock(returncode=1, stdout=b"", stderr=b"error")
        deleted = prune_oci_backups("my-bucket", "my-namespace", retention_days=30, today=date(2026, 5, 16))
        self.assertEqual(deleted, [])


class TestRunBackup(unittest.TestCase):
    def _cfg(self, tmpdir=None):
        backup_dir = tmpdir or "/backups/alphadivision"
        return {
            "pg_user": "pguser",
            "db_name": "alphadivision",
            "backup_dir": backup_dir,
            "oci_bucket": "my-bucket",
            "oci_namespace": "my-namespace",
        }

    @patch("backup.backup.prune_oci_backups")
    @patch("backup.backup.prune_local_backups")
    @patch("backup.backup.upload_to_oci")
    @patch("backup.backup.run_pg_dump")
    def test_full_success_flow(self, mock_dump, mock_upload, mock_prune_local, mock_prune_oci):
        import tempfile
        from backup.backup import run_backup
        from datetime import date
        mock_dump.return_value = True
        mock_upload.return_value = True
        mock_prune_local.return_value = []
        mock_prune_oci.return_value = []

        with tempfile.TemporaryDirectory() as tmpdir:
            today = date(2026, 5, 16)
            result = run_backup(self._cfg(tmpdir), today=today)

            self.assertTrue(result)
            # Dump called with correct args
            mock_dump.assert_called_once()
            dump_args = mock_dump.call_args[0]
            self.assertEqual(dump_args[0], "pguser")
            self.assertEqual(dump_args[1], "alphadivision")
            self.assertIn("alphadivision-20260516.sql.gz", dump_args[2])
            # Upload called with correct args
            mock_upload.assert_called_once()
            upload_args = mock_upload.call_args[0]
            self.assertEqual(upload_args[0], "my-bucket")
            self.assertEqual(upload_args[1], "my-namespace")
            self.assertEqual(upload_args[2], "alphadivision-20260516.sql.gz")
            # Pruning called
            mock_prune_local.assert_called_once()
            mock_prune_oci.assert_called_once()

    @patch("backup.backup.prune_oci_backups")
    @patch("backup.backup.prune_local_backups")
    @patch("backup.backup.upload_to_oci")
    @patch("backup.backup.run_pg_dump")
    def test_returns_false_when_dump_fails(self, mock_dump, mock_upload, mock_prune_local, mock_prune_oci):
        import tempfile
        from backup.backup import run_backup
        from datetime import date
        mock_dump.return_value = False

        with tempfile.TemporaryDirectory() as tmpdir:
            result = run_backup(self._cfg(tmpdir), today=date(2026, 5, 16))

            self.assertFalse(result)
            mock_upload.assert_not_called()
            mock_prune_local.assert_not_called()
            mock_prune_oci.assert_not_called()

    @patch("backup.backup.prune_oci_backups")
    @patch("backup.backup.prune_local_backups")
    @patch("backup.backup.upload_to_oci")
    @patch("backup.backup.run_pg_dump")
    def test_pruning_still_runs_when_upload_fails(self, mock_dump, mock_upload, mock_prune_local, mock_prune_oci):
        """Upload failure should not skip pruning — old backups must still be cleaned up."""
        import tempfile
        from backup.backup import run_backup
        from datetime import date
        mock_dump.return_value = True
        mock_upload.return_value = False
        mock_prune_local.return_value = []
        mock_prune_oci.return_value = []

        with tempfile.TemporaryDirectory() as tmpdir:
            result = run_backup(self._cfg(tmpdir), today=date(2026, 5, 16))

            self.assertFalse(result)
            mock_prune_local.assert_called_once()
            mock_prune_oci.assert_called_once()


if __name__ == "__main__":
    unittest.main()
