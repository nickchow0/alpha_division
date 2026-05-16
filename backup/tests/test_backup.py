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


if __name__ == "__main__":
    unittest.main()
