"""Windows global hotkeys that fail to register are reported (stderr, on_event, recording warning, UI).

Cases 1-5 and the extra cases run anywhere: ctypes.windll / WinDLL / get_last_error / GetLastError are replaced
with a stand-in user32 and tk_recorder.IS_WIN is set to True; the Tk root is a FakeRoot that only records after().
start_hotkeys, its thread, the queue and drain are the real code. Case 6 needs Windows: the test process holds
Alt+Shift+P for real, then the recorder registers.
"""
import contextlib
import ctypes
import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import importlib.util

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "vendor/blurt/scripts"
sys.path.insert(0, str(SCRIPTS))
import tk_recorder  # noqa: E402
SPEC = importlib.util.spec_from_file_location("hotkeys_record", SCRIPTS / "record.py")
record = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(record)
BRIDGE_SPEC = importlib.util.spec_from_file_location("hotkeys_review_bridge", ROOT / "scripts" / "review_bridge.py")
bridge = importlib.util.module_from_spec(BRIDGE_SPEC)
BRIDGE_SPEC.loader.exec_module(bridge)

WM_HOTKEY, WM_QUIT = 0x0312, 0x0012
ERROR_HOTKEY_ALREADY_REGISTERED = 1409
VK_P, VK_M, VK_S, VK_A = 0x50, 0x4D, 0x53, 0x41


class FakeUser32:
    """RegisterHotKey 对 taken 里的虚拟键返回 0，其余返回 1；最后错误码固定为 last_error。"""

    def __init__(self, taken=(), last_error=ERROR_HOTKEY_ALREADY_REGISTERED, deliver=()):
        self.taken, self.last_error, self.deliver = set(taken), last_error, list(deliver)
        self.calls, self.quit, self.in_loop = [], threading.Event(), threading.Event()
        self.bad_windll = []   # WinDLL 调用里没带 use_last_error=True 的参数

    def RegisterHotKey(self, hwnd, id_, mods, vk):
        ok = 0 if vk in self.taken else 1
        self.calls.append((id_, mods, vk, ok))
        return ok

    def GetLastError(self):
        return self.last_error

    def GetMessageW(self, pmsg, *_):
        self.in_loop.set()
        if self.deliver:
            pmsg._obj.message, pmsg._obj.wParam = WM_HOTKEY, self.deliver.pop(0)
            return 1
        self.quit.wait(10)
        return 0

    def __getattr__(self, name):
        return lambda *a: 1


@contextlib.contextmanager
def fake_windows(fake):
    lib = SimpleNamespace(user32=fake, kernel32=fake, shcore=fake)

    # 真实 Windows 上只有 use_last_error=True 的库才会在调用后保存错误码，ctypes.get_last_error() 读的就是它；
    # 直接调 GetLastError 可能已被 ctypes 自己的调用覆盖。替身按这个规则：不带 use_last_error=True 就报错，
    # GetLastError 返回 0。
    def win_dll(name, *args, **kwargs):
        if kwargs.get("use_last_error") is not True:
            fake.bad_windll.append((name, args, kwargs))
            raise AssertionError(f"WinDLL({name!r}) 必须带 use_last_error=True: {args} {kwargs}")
        return fake
    with mock.patch.object(ctypes, "windll", lib, create=True), \
            mock.patch.object(ctypes, "WinDLL", win_dll, create=True), \
            mock.patch.object(ctypes, "get_last_error", fake.GetLastError, create=True), \
            mock.patch.object(ctypes, "GetLastError", lambda: 0, create=True), \
            mock.patch.object(tk_recorder, "IS_WIN", True):
        try:
            yield
        finally:
            fake.quit.set()


REPORT = '''<!doctype html><html><head>
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'sha256-original'; style-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'">
</head><body><section id="panel-A1"><h2>A1 Test</h2></section></body></html>'''


def write_run(run: Path) -> Path:
    run.mkdir(parents=True)
    (run / "report.html").write_text(REPORT, encoding="utf-8")
    (run / "manifest.json").write_text(json.dumps({"run_id": "r", "sources": [{"id": "S1"}], "targets": [],
                                                   "panels": [{"id": "A1", "title": "A1", "source_ids": ["S1"]}]}),
                                       encoding="utf-8")
    return run


def write_skill(skill: Path) -> Path:
    (skill / "scripts").mkdir(parents=True)
    (skill / "scripts" / "record.py").write_text("")
    return skill


class FakeRoot:
    def __init__(self):
        self.pending = []

    def after(self, _ms, fn):
        self.pending.append(fn)

    def run_pending(self):
        fns, self.pending = self.pending, []
        for fn in fns:
            fn()

    def quit(self):
        pass


def make_recorder(tmp: Path, events: list):
    rec = tk_recorder.TkRecorder(tmp, None, 10, 320, "zh", 0, None, [0, 0, 320, 240], tmp / ".cmd", events.append)
    rec.root = FakeRoot()
    return rec


def wait_for(cond, timeout=3.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


class HotkeyRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.events: list = []
        self.err = io.StringIO()
        self.rec = make_recorder(self.dir, self.events)
        self.commands: list = []
        self.rec.command = self.commands.append

    def tearDown(self):
        self.tmp.cleanup()

    def start(self, fake):
        stack = contextlib.ExitStack()
        stack.enter_context(fake_windows(fake))
        stack.enter_context(contextlib.redirect_stderr(self.err))
        self.addCleanup(stack.close)
        self.rec.start_hotkeys()
        self.assertTrue(fake.in_loop.wait(3), f"注册线程没有走到消息循环（WinDLL 参数错误: {fake.bad_windll}）")
        time.sleep(0.1)
        self.rec.root.run_pending()

    # 1 某个热键被占用：stderr 有一行，写明是哪个键；其他键照常注册
    def test_taken_hotkey_is_reported_on_stderr(self):
        fake = FakeUser32(taken={VK_P})
        self.start(fake)
        out = self.err.getvalue()
        self.assertIn("Alt+Shift+P", out, f"热键被占用但 stderr 没有提示: {out!r}")
        self.assertEqual([line for line in out.splitlines() if "Alt+Shift+" in line].__len__(), 1, out)
        self.assertTrue({VK_M, VK_S} <= {vk for _, _, vk, ok in fake.calls if ok}, "其他热键应照常注册")

    # 2 失败要作为事件交给 on_event（record.py 会把它打到 stdout，进入 recording.log），带上动作和 Windows 错误码
    def test_taken_hotkey_is_sent_to_on_event_with_error_code(self):
        self.start(FakeUser32(taken={VK_P}))
        hits = [e for e in self.events if e.get("event") == "hotkey_unavailable"]
        self.assertEqual(len(hits), 1, f"on_event 没有收到 hotkey_unavailable: {self.events}")
        self.assertEqual(hits[0].get("hotkey"), "Alt+Shift+P")
        self.assertEqual(hits[0].get("error"), ERROR_HOTKEY_ALREADY_REGISTERED)

    def finalize(self):
        video, log = self.dir / "seg00.mkv", self.dir / "seg00.log"
        video.write_bytes(b"v")
        log.write_text("")
        self.rec.segments = [{"video": video, "log": log, "wall_start": 0, "audio_from": 0, "audio_to": 0}]

        def fake_run(cmd, check=False):
            Path(cmd[-1]).write_bytes(b"x")
            return SimpleNamespace(returncode=0, stderr="")
        with mock.patch.object(tk_recorder, "ffmpeg_bin", return_value="ffmpeg"), \
                mock.patch.object(tk_recorder, "run", side_effect=fake_run):
            return self.rec.finalize()

    # 3 失败写进录制结果的 warning：finish_session 会存进 meta.json，review_bridge 把它显示给用户
    def test_taken_hotkey_ends_up_in_recording_warning(self):
        self.start(FakeUser32(taken={VK_P}))
        result = self.finalize()
        self.assertEqual(result.get("event"), "stopped", result)
        self.assertIn("Alt+Shift+P", result.get("warning", ""), f"结果里没有热键提示: {result}")

    # 3b 界面路径：结果 → finish_session 写 meta.json → review_bridge.watch 把 warning 作为状态 message
    def test_taken_hotkey_warning_reaches_review_state(self):
        self.start(FakeUser32(taken={VK_P}))
        result = self.finalize()
        run = self.dir / "run"
        write_run(run)
        skill = write_skill(self.dir / "skill")
        states = []
        review = bridge.ReviewRun(run, skill, states.append)
        # 和生产一致：bridge 传 --session <review.run>/blurt，record.py cmd_start 先 resolve() 再 finish_session。
        # 直接用 self.dir 拼的路径不行：Windows CI 的 TEMP 是 8.3 短名（C:\Users\RUNNER~1\...），
        # ReviewRun 会 resolve 成长名，DONE 行和 watch 的路径对不上。
        session = Path(str(review.run / "blurt")).resolve()
        session.mkdir()
        (session / "recording.mp4").write_bytes(b"video")
        out = io.StringIO()
        with mock.patch.object(record, "probe_duration", return_value=3), contextlib.redirect_stdout(out):
            record.finish_session(session, result, {})
        meta = json.loads((session / "meta.json").read_text(encoding="utf-8"))
        self.assertIn("Alt+Shift+P", meta.get("warning", ""))
        self.assertIn("已被其他程序占用", meta["warning"])
        self.assertIn('"recording_warning"', out.getvalue())
        review.state_file = self.dir / "recording.json"
        review.retention = bridge.settings.RetentionSettings(self.dir / ".2hours")
        review.log.write_text(out.getvalue(), encoding="utf-8")
        review.watch(SimpleNamespace(wait=lambda: 0))
        self.assertEqual(states[-1]["event"], "recorded", states[-1])
        self.assertEqual(states[-1]["message"], meta["warning"])
        # 只是热键提示，录制本身完整：不能标成 incomplete（否则状态显示异常、recover_session 也不再拒绝）
        self.assertIs(meta.get("incomplete", False), False, meta)
        self.assertIs(states[-1]["incomplete"], False, states[-1])

    # 3c 其他错误码原样写出，不说成"被占用"
    def test_other_error_code_is_reported_as_is(self):
        self.start(FakeUser32(taken={VK_S}, last_error=5))
        self.assertIn("Alt+Shift+S (stop) could not be registered (error 5)", self.err.getvalue())
        self.assertEqual([e.get("error") for e in self.events if e.get("event") == "hotkey_unavailable"], [5])
        self.assertIn("热键 Alt+Shift+S（完成）注册失败（错误 5）", self.finalize()["warning"])

    # 3d 注册失败的 id 不分发（和上游一样，只按注册成功的 id 分发）
    def test_failed_id_is_not_dispatched(self):
        self.start(FakeUser32(taken={VK_P}, deliver=[1, 3]))
        self.assertEqual(self.commands, ["stop"])

    # 3e 倒计时窗口只列出注册成功的热键
    def test_countdown_hint_lists_only_registered_hotkeys(self):
        with mock.patch.object(tk_recorder, "IS_WIN", True):
            self.rec.hotkeys_ok, self.rec.hotkeys_ready = {"pause", "marker", "stop"}, threading.Event()
            self.rec.hotkeys_ready.set()
            self.assertEqual(self.rec.countdown_hint(), "Alt+Shift+P 暂停/继续 · Alt+Shift+S 完成 · 点一下跳过")
            self.rec.hotkeys_ok = {"marker", "stop"}
            self.assertEqual(self.rec.countdown_hint(), "Alt+Shift+S 完成 · 点一下跳过")
            self.rec.hotkeys_ok = set()
            self.assertEqual(self.rec.countdown_hint(), "点一下跳过")
        with mock.patch.object(tk_recorder, "IS_WIN", False):
            self.rec.hotkeys_ok = {"pause", "stop"}
            self.assertEqual(self.rec.countdown_hint(), "点一下跳过")

    # 3f 倒计时等注册线程结束后再决定显示哪些键（注册慢一点也要等到）
    def test_countdown_hint_waits_for_registration(self):
        fake = FakeUser32(taken={VK_P})
        register = fake.RegisterHotKey

        def slow_register(*args):
            time.sleep(0.05)
            return register(*args)
        fake.RegisterHotKey = slow_register
        with fake_windows(fake), contextlib.redirect_stderr(self.err):
            self.rec.start_hotkeys()
            self.assertEqual(self.rec.countdown_hint(), "Alt+Shift+S 完成 · 点一下跳过")

    # 3g on_event 抛异常（例如 stdout 管道断开）：注册线程不能退出，其余键照常注册、照常分发
    def test_on_event_error_does_not_stop_hotkey_thread(self):
        def broken(event):
            raise BrokenPipeError(32, "Broken pipe")
        self.rec.on_event = broken
        fake = FakeUser32(taken={VK_P, VK_M}, deliver=[3])
        self.start(fake)
        self.assertEqual([vk for _, _, vk, _ in fake.calls], [VK_P, VK_M, VK_S], "on_event 出错后应继续注册")
        self.assertEqual(self.commands, ["stop"], "注册成功的键应照常分发")
        self.assertEqual([f["hotkey"] for f in self.rec.hotkey_failures], ["Alt+Shift+P", "Alt+Shift+M"])
        lines = self.err.getvalue().splitlines()
        self.assertEqual(len([line for line in lines if "is taken by another app" in line]), 2, lines)
        self.assertEqual(len([line for line in lines if "hotkey_unavailable" in line and "BrokenPipeError" in line]), 2,
                         lines)
        self.assertIn("Alt+Shift+P", self.finalize()["warning"])

    # 3h 注册本身抛异常：hotkeys_ready 仍要 set，倒计时不会一直等
    def test_registration_error_still_sets_ready(self):
        fake = FakeUser32()

        def boom(*args):
            raise OSError("RegisterHotKey failed")
        fake.RegisterHotKey = boom
        hook = []
        with mock.patch.object(threading, "excepthook", hook.append), fake_windows(fake), \
                contextlib.redirect_stderr(self.err):
            self.rec.start_hotkeys()
            self.assertTrue(self.rec.hotkeys_ready.wait(3), "注册出异常后 hotkeys_ready 没有 set")
            self.assertTrue(wait_for(lambda: hook), "异常应交给 threading.excepthook")
        self.assertIsInstance(hook[0].exc_value, OSError)

    # 4 全部被占用：每个键一行，不崩溃，消息循环照常进入
    def test_all_taken_reports_each_once_and_keeps_running(self):
        fake = FakeUser32(taken={VK_P, VK_M, VK_S, VK_A})
        self.start(fake)
        lines = [line for line in self.err.getvalue().splitlines() if "Alt+Shift+" in line]
        registered = len(fake.calls)
        self.assertEqual(len(lines), registered, f"注册了 {registered} 个、提示了 {len(lines)} 行: {lines}")
        self.assertIsNone(self.rec.result)

    # 5 对照：注册成功时热键照常工作，也不打任何提示
    def test_registered_hotkeys_still_dispatch_commands(self):
        self.start(FakeUser32(deliver=[1, 3]))
        self.assertEqual(self.commands, ["toggle", "stop"])
        self.assertNotIn("hotkey", self.err.getvalue().lower())
        self.assertFalse([e for e in self.events if e.get("event") == "hotkey_unavailable"])


class RecordingPathAgreementTests(unittest.TestCase):
    """bridge 打开的 run 路径是别名（符号链接、Windows 8.3 短名）时，record.py 打出的 DONE 行仍要和 watch 对得上。

    走真实的 ReviewRun.start()（只替换 uv 和 Popen）拿到 --session 参数，再按 record.py cmd_start 的做法
    resolve() 后调用 finish_session 生成 DONE 行，最后由 watch 判定。"""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.run = write_run(self.root / "run-directory-with-long-name")
        self.skill = write_skill(self.root / "skill")

    def roundtrip(self, alias):
        states, launched = [], []

        def spawn(args, **kwargs):
            done = threading.Event()
            launched.append((args, done))
            return SimpleNamespace(wait=lambda: 0 if done.wait(5) else 1)
        review = bridge.ReviewRun(alias, self.skill, states.append)
        review.state_file = self.root / "recording.json"
        review.retention = bridge.settings.RetentionSettings(self.root / ".2hours")
        with mock.patch.object(bridge.shutil, "which", return_value="uv"), \
                mock.patch.object(bridge.subprocess, "Popen", side_effect=spawn):
            review.start()
        args, done = launched[0]
        session = Path(args[args.index("--session") + 1]).resolve()   # record.py cmd_start
        (session / "recording.mp4").write_bytes(b"video")
        out = io.StringIO()
        with mock.patch.object(record, "probe_duration", return_value=3), contextlib.redirect_stdout(out):
            record.finish_session(session, {"event": "stopped"}, {})
        review.log.write_text(out.getvalue(), encoding="utf-8")
        done.set()
        self.assertTrue(wait_for(lambda: states, 5), "watch 没有给出结果")
        self.assertEqual(states[-1]["event"], "recorded", (states[-1], out.getvalue()))

    def test_symlinked_run_directory(self):
        alias = self.root / "alias"
        try:
            alias.symlink_to(self.run, target_is_directory=True)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"无法创建符号链接: {error}")
        self.roundtrip(alias)

    @unittest.skipUnless(os.name == "nt", "8.3 短名只在 Windows 上有")
    def test_short_8dot3_run_directory(self):
        from ctypes import wintypes
        get_short = ctypes.WinDLL("kernel32", use_last_error=True).GetShortPathNameW
        get_short.argtypes, get_short.restype = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD], wintypes.DWORD
        buffer = ctypes.create_unicode_buffer(1024)
        if not get_short(str(self.run), buffer, len(buffer)):
            self.skipTest(f"GetShortPathNameW 失败（错误 {ctypes.get_last_error()}）")
        if Path(buffer.value) == self.run:
            self.skipTest("这台机器的卷关闭了 8.3 短名")
        self.roundtrip(Path(buffer.value))


@unittest.skipUnless(os.name == "nt", "真实热键冲突只能在 Windows 上复现，交给 windows-latest CI")
class RealConflictOnWindows(unittest.TestCase):
    # 6 测试进程先占住 Alt+Shift+P，再启动录制器的注册：录制器要报出来
    def test_hotkey_held_by_test_process_is_reported(self):
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        MOD = 0x0001 | 0x0004 | 0x4000
        if not user32.RegisterHotKey(None, 0x7001, MOD, VK_P):
            self.skipTest(f"Alt+Shift+P 已被 CI 机器上的其他程序占用（错误 {ctypes.get_last_error()}）")
        self.addCleanup(user32.UnregisterHotKey, None, 0x7001)
        with tempfile.TemporaryDirectory() as d:
            events, err = [], io.StringIO()
            rec = make_recorder(Path(d), events)
            before = set(threading.enumerate())
            with contextlib.redirect_stderr(err):
                rec.start_hotkeys()
                ok = wait_for(lambda: "Alt+Shift+P" in err.getvalue(), 3)
            for t in set(threading.enumerate()) - before:      # 结束录制器的消息循环，释放它注册的热键
                user32.PostThreadMessageW(t.native_id, WM_QUIT, 0, 0)
                t.join(3)
            self.assertTrue(ok, f"真实冲突下 stderr 没有提示: {err.getvalue()!r}")
            self.assertIn("Alt+Shift+P (pause) is taken by another app", err.getvalue())
            hits = [e for e in events if e.get("event") == "hotkey_unavailable" and e.get("hotkey") == "Alt+Shift+P"]
            self.assertEqual(len(hits), 1, events)
            self.assertEqual(hits[0].get("error"), ERROR_HOTKEY_ALREADY_REGISTERED, hits)


if __name__ == "__main__":
    unittest.main(verbosity=2)
