"""Mock Windows APIs around the imported production Tk capture exclusion function."""

import builtins
import sys
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1] / "vendor/blurt/scripts"
sys.path.insert(0, str(SCRIPTS))
import tk_recorder  # noqa: E402  (import has no Tk display or capture side effect)


class FakeWindow:
    def __init__(self, events, fail_at=None):
        self.events = events
        self.fail_at = fail_at

    def update_idletasks(self):
        self.events.append(("update_idletasks",))
        if self.fail_at == "update_idletasks":
            raise RuntimeError("mock update_idletasks failure")

    def winfo_id(self):
        self.events.append(("winfo_id",))
        if self.fail_at == "winfo_id":
            raise RuntimeError("mock winfo_id failure")
        return 0x1234


class FakeUser32:
    def __init__(self, events, parent=0x5678, fail_at=None):
        self.events = events
        self.parent = parent
        self.fail_at = fail_at

    def GetParent(self, hwnd):
        self.events.append(("GetParent", hwnd))
        if self.fail_at == "GetParent":
            raise RuntimeError("mock GetParent failure")
        return self.parent

    def SetWindowDisplayAffinity(self, hwnd, affinity):
        self.events.append(("SetWindowDisplayAffinity", hwnd, affinity))
        if self.fail_at == "SetWindowDisplayAffinity":
            raise RuntimeError("mock affinity failure")
        return 1


class TkCaptureGateTests(unittest.TestCase):
    def exercise(self, build=None, *, windows=True, parent=0x5678, fail_at=None):
        events = []
        window = FakeWindow(events, fail_at)
        user32 = FakeUser32(events, parent, fail_at)
        fake_ctypes = SimpleNamespace(windll=SimpleNamespace(user32=user32))
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "ctypes":
                events.append(("import ctypes",))
                return fake_ctypes
            return real_import(name, *args, **kwargs)

        def fake_version():
            events.append(("getwindowsversion",))
            if fail_at == "getwindowsversion":
                raise RuntimeError("mock version query failure")
            return SimpleNamespace(build=build)

        with patch.object(tk_recorder, "IS_WIN", windows), \
             patch.object(tk_recorder.sys, "getwindowsversion", fake_version, create=True), \
             patch("builtins.__import__", side_effect=fake_import):
            result = tk_recorder.exclude_from_capture(window)
        self.assertIsNone(result, events)
        return events

    def assert_old_build_skips_before_windows_access(self, build):
        events = self.exercise(build)
        affinity = [event for event in events if event[0] == "SetWindowDisplayAffinity"]
        # This is the core behavioral red on base: the real production function
        # calls the mocked API with (parent HWND, 0x11) on these old builds.
        self.assertEqual(affinity, [], f"build={build}; all captured calls={events}")
        self.assertEqual(events, [("getwindowsversion",)], f"build={build}; {events}")

    def test_build_17763_skips_affinity(self):
        self.assert_old_build_skips_before_windows_access(17763)

    def test_build_18363_skips_affinity(self):
        self.assert_old_build_skips_before_windows_access(18363)

    def test_build_19040_skips_affinity(self):
        self.assert_old_build_skips_before_windows_access(19040)

    def test_build_19041_retains_parent_hwnd_and_affinity(self):
        self.assertEqual(self.exercise(19041), [
            ("getwindowsversion",), ("import ctypes",), ("update_idletasks",),
            ("winfo_id",), ("GetParent", 0x1234),
            ("SetWindowDisplayAffinity", 0x5678, 0x11),
        ])

    def test_build_22631_retains_parent_hwnd_and_affinity(self):
        self.assertEqual(self.exercise(22631), [
            ("getwindowsversion",), ("import ctypes",), ("update_idletasks",),
            ("winfo_id",), ("GetParent", 0x1234),
            ("SetWindowDisplayAffinity", 0x5678, 0x11),
        ])

    def test_getparent_zero_falls_back_to_second_winfo_id(self):
        self.assertEqual(self.exercise(22631, parent=0), [
            ("getwindowsversion",), ("import ctypes",), ("update_idletasks",),
            ("winfo_id",), ("GetParent", 0x1234), ("winfo_id",),
            ("SetWindowDisplayAffinity", 0x1234, 0x11),
        ])

    def test_non_windows_never_queries_version_or_windows_apis(self):
        self.assertEqual(self.exercise(windows=False), [])

    def test_version_query_exception_is_swallowed_before_ctypes(self):
        self.assertEqual(self.exercise(22631, fail_at="getwindowsversion"),
                         [("getwindowsversion",)])

    def test_update_exception_is_swallowed(self):
        self.assertEqual(self.exercise(22631, fail_at="update_idletasks"), [
            ("getwindowsversion",), ("import ctypes",), ("update_idletasks",),
        ])

    def test_winfo_id_exception_is_swallowed(self):
        self.assertEqual(self.exercise(22631, fail_at="winfo_id"), [
            ("getwindowsversion",), ("import ctypes",), ("update_idletasks",),
            ("winfo_id",),
        ])

    def test_getparent_exception_is_swallowed(self):
        self.assertEqual(self.exercise(22631, fail_at="GetParent"), [
            ("getwindowsversion",), ("import ctypes",), ("update_idletasks",),
            ("winfo_id",), ("GetParent", 0x1234),
        ])

    def test_affinity_exception_is_swallowed_after_one_call(self):
        self.assertEqual(self.exercise(22631, fail_at="SetWindowDisplayAffinity"), [
            ("getwindowsversion",), ("import ctypes",), ("update_idletasks",),
            ("winfo_id",), ("GetParent", 0x1234),
            ("SetWindowDisplayAffinity", 0x5678, 0x11),
        ])
