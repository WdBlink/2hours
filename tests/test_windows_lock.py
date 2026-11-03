"""Lock acquisition must not follow junctions or mutate linked external files."""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("windows_lock_dispatch", Path(__file__).parents[1] / "scripts/dispatch.py")
dispatch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(dispatch)


@unittest.skipUnless(os.name == "nt", "Windows native locks")
class WindowsLockTests(unittest.TestCase):
    def setUp(self):
        import _winapi
        self.junction = _winapi.CreateJunction
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.run = self.root / "run"
        self.run.mkdir()
        self.external = self.root / "external"
        self.external.mkdir()

    def test_junction_directory_is_refused(self):
        linked = self.root / "linked"
        self.junction(str(self.external), str(linked))
        with self.assertRaises(ValueError):
            with dispatch.locked(linked):
                self.fail("junction lock unexpectedly acquired")
        self.assertFalse((self.external / ".dispatch.lock").exists())

    def test_reparse_lock_is_refused(self):
        self.junction(str(self.external), str(self.run / ".dispatch.lock"))
        with self.assertRaises(ValueError):
            with dispatch.locked(self.run):
                self.fail("reparse lock unexpectedly acquired")
        self.assertFalse(list(self.external.iterdir()))

    def test_hardlink_lock_is_refused_without_writing_external_file(self):
        target = self.external / "data.bin"
        target.write_bytes(b"")
        os.link(target, self.run / ".dispatch.lock")
        with self.assertRaises(ValueError):
            with dispatch.locked(self.run):
                self.fail("hardlink lock unexpectedly acquired")
        self.assertEqual(target.read_bytes(), b"")

    def test_hardlink_substitution_during_lock_open_is_refused(self):
        target = self.external / "data.bin"
        target.write_bytes(b"")
        native = dispatch.windows_files.checked_handle

        def substitute(path, **kwargs):
            if kwargs.get("lock"):
                os.link(target, path)
            return native(path, **kwargs)

        with patch.object(dispatch.windows_files, "checked_handle", side_effect=substitute):
            with self.assertRaises(ValueError):
                with dispatch.locked(self.run):
                    self.fail("hardlink substitution unexpectedly acquired")
        self.assertEqual(target.read_bytes(), b"")

    def test_parent_junction_substitution_during_open_is_refused(self):
        native = dispatch.windows_files.checked_handle
        moved = self.root / "moved"

        def substitute(path, **kwargs):
            if Path(path) == self.run and kwargs.get("directory"):
                self.run.rename(moved)
                self.junction(str(self.external), str(self.run))
            return native(path, **kwargs)

        with patch.object(dispatch.windows_files, "checked_handle", side_effect=substitute):
            with self.assertRaises(ValueError):
                with dispatch.locked(self.run):
                    self.fail("junction substitution unexpectedly acquired")
        self.assertFalse((self.external / ".dispatch.lock").exists())

    def test_locked_directory_and_lock_file_cannot_be_renamed(self):
        path = self.run / ".dispatch.lock"
        with dispatch.locked(self.run):
            with self.assertRaises(OSError):
                self.run.rename(self.root / "moved")
            with self.assertRaises(OSError):
                path.rename(self.run / "different.lock")
            with self.assertRaises(OSError):
                path.unlink()
        self.assertEqual(path.read_bytes(), b"")

    def test_lock_opens_relative_to_pinned_parent(self):
        native = dispatch.windows_files.open_relative
        calls = []

        def record(path, parent, *args):
            calls.append((Path(path).name, parent))
            return native(path, parent, *args)

        with patch.object(dispatch.windows_files, "open_relative", side_effect=record):
            with dispatch.locked(self.run):
                pass
        self.assertEqual(calls[-1][0], ".dispatch.lock")
        self.assertTrue(all(parent for _, parent in calls))


if __name__ == "__main__":
    unittest.main()
