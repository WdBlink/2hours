"""Local retention settings and explicit, preview-bound media cleanup."""
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import os
from pathlib import Path
import secrets
import stat

_spec = importlib.util.spec_from_file_location("retention_dispatch", Path(__file__).with_name("dispatch.py"))
dispatch = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dispatch)

POLICIES = {"keep": ("一直保留", None), "after_review": ("处理并完成审核后", 0),
            "1_day": ("保留 1 天", 1), "2_days": ("保留 2 天", 2),
            "1_week": ("保留 1 周", 7), "1_month": ("保留 1 个月（30 天）", 30)}
MEDIA = ("recording.mp4", "audio.wav", "video.mkv")


def safe(path):
    return not any(part.is_symlink() for part in (path, *path.parents))


def signature(value):
    return [value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns]


class RetentionSettings:
    def __init__(self, home=None):
        self.home = Path(home) if home is not None else Path.home() / ".2hours"
        self.home = self.home.absolute()
        self.runs = self.home / "runs"
        self.path = self.home / "settings.json"

    def read(self):
        if not safe(self.path) or not safe(self.home / ".dispatch.lock"):
            raise ValueError("设置目录或文件是符号链接，已停止操作")
        value = dispatch.read_json(self.path) if self.path.exists() else {"retention": "keep", "revision": 0}
        if (not isinstance(value, dict) or not isinstance(value.get("retention"), str) or value["retention"] not in POLICIES
                or type(value.get("revision")) is not int):
            raise ValueError("留存设置无效，已保留全部文件")
        return {**value, **dispatch.dispatch_preferences(value)}

    def save(self, body):
        if (not set(body) <= {"retention", "dispatch_mode", "revision"} or not ({"retention", "dispatch_mode"} & set(body))
                or ("retention" in body and (not isinstance(body["retention"], str) or body["retention"] not in POLICIES))
                or ("dispatch_mode" in body and body["dispatch_mode"] not in ("manual", "automatic"))
                or type(body.get("revision")) is not int):
            raise ValueError("无效的留存设置")
        self.read()
        self.home.mkdir(mode=0o700, parents=True, exist_ok=True)
        with dispatch.locked(self.home):
            current = self.read()
            if body["revision"] != current["revision"]:
                raise ValueError("设置已更新，请重新打开设置后再保存")
            changed = {**current, **body, "revision": current["revision"] + 1}
            if changed["dispatch_mode"] != current["dispatch_mode"]:
                changed["dispatch_consent"] = {"id": secrets.token_hex(24), "enabled_at": dispatch.now()} if changed["dispatch_mode"] == "automatic" else None
            dispatch.write_json(self.path, changed)
        return self.preview()

    def protection(self, run):
        try:
            names = (*dispatch.INPUTS, *dispatch.ITEM_INPUTS, dispatch.CONFIRMATION, "review-plan.json")
            if any(not safe(run / name) for name in names):
                return "审核资料含符号链接，已保护"
            manifest = dispatch.read_json(run / "manifest.json")
            if manifest.get("run_id") != run.name or not isinstance(manifest.get("panels"), list):
                return "无法确认是 Two Hours 运行目录，已保护"
            items_path = run / "blurt/items.json"
            if not items_path.is_file():
                return "尚未处理，已保护"
            items = dispatch.read_json(items_path)
            if not isinstance(items, dict) or not isinstance(items.get("items"), list):
                return "处理结果无效，已保护"
            confirmation_path = run / dispatch.CONFIRMATION
            if confirmation_path.exists():
                review = dispatch.current_review(run)
                confirmation = dispatch.read_json(confirmation_path)
                if confirmation.get("review_id") != review["review_id"]:
                    return "审核资料已变化，已保护"
                dispatch.review_decisions(review, confirmation.get("tasks"))
            elif items.get("reviewed") is not True:
                return "尚未完成审核，已保护"
            return None
        except (OSError, ValueError, TypeError, AttributeError, KeyError):
            return "无法核验处理和审核状态，已保护"

    def active_session(self):
        path = self.home / "recorder/recording.json"
        if not path.exists() and not path.is_symlink():
            return None
        try:
            if not safe(path):
                return "unknown"
            value = dispatch.read_json(path)
            pid = value.get("pid")
            if type(pid) is not int or pid <= 0:
                return "unknown"
            os.kill(pid, 0)
            return str(Path(value["session"]).absolute())
        except ProcessLookupError:
            return None
        except (OSError, ValueError, TypeError, AttributeError, KeyError):
            return "unknown"

    def preview(self):
        settings = self.read()
        rows, warnings = [], []
        days = POLICIES[settings["retention"]][1]
        active = self.active_session()
        if not safe(self.runs):
            warnings.append("运行目录含符号链接，已排除整个目录")
        elif self.runs.exists():
            for run in sorted(self.runs.iterdir()):
                session = run / "blurt"
                if not safe(session):
                    warnings.append(f"{run.name} 含符号链接，已排除")
                    continue
                if not session.is_dir():
                    continue
                reason = self.protection(run)
                if active == "unknown" or active == str(session):
                    reason = "录制进行中或状态无法核验，已保护"
                for name in MEDIA:
                    path = session / name
                    if not path.exists() and not path.is_symlink():
                        continue
                    info = path.lstat()
                    blocked = reason
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                        blocked = "符号链接、硬链接或非普通文件，已保护"
                    elif not blocked and info.st_mtime_ns > (session / "items.json").stat().st_mtime_ns:
                        blocked = "录音晚于处理结果，需重新处理，已保护"
                    expires = None if days is None else datetime.fromtimestamp(info.st_mtime, timezone.utc) + timedelta(days=days)
                    eligible = not blocked and expires is not None and expires <= datetime.now(timezone.utc)
                    rows.append({"path": path.relative_to(self.runs).as_posix(), "bytes": info.st_size,
                                 "expires_at": expires.isoformat() if expires else None,
                                 "eligible": eligible, "reason": blocked or ("一直保留" if days is None else "已到期，可清理" if eligible else "尚未到期"),
                                 "signature": signature(info)})
        payload = {"settings": settings, "files": rows, "warnings": warnings}
        return {**payload, "preview_id": hashlib.sha256(dispatch.encoded(payload)).hexdigest(),
                "scope": str(self.runs), "media_names": list(MEDIA),
                "options": [{"value": key, "label": label} for key, (label, _) in POLICIES.items()],
                "eligible_bytes": sum(row["bytes"] for row in rows if row["eligible"])}

    def cleanup(self, body):
        if set(body) != {"preview_id"} or not isinstance(body["preview_id"], str):
            raise ValueError("请先查看清理预览")
        self.read()
        with dispatch.locked(self.home):
            preview = self.preview()
            if body["preview_id"] != preview["preview_id"]:
                raise ValueError("文件、审核状态或设置已变化；请刷新预览后再清理")
            deleted, failed = [], []
            for row in preview["files"]:
                if not row["eligible"]:
                    continue
                path = self.runs / row["path"]
                run = path.parent.parent
                recovery_path = None
                try:
                    if not safe(run / ".dispatch.lock"):
                        raise ValueError("运行目录或锁含符号链接，已保留")
                    with ExitStack() as stack:
                        descriptor = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
                        stack.callback(os.close, descriptor)
                        for part in path.parts[1:-2]:
                            descriptor = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                            stack.callback(os.close, descriptor)
                        run_descriptor = descriptor
                        stack.enter_context(dispatch.locked(run))
                        descriptor = os.open("blurt", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=run_descriptor)
                        stack.callback(os.close, descriptor)
                        info = os.stat(path.name, dir_fd=descriptor, follow_symlinks=False)
                        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or signature(info) != row["signature"]:
                            raise ValueError("文件已变化，已保留")
                        self.check_cleanup_protection(run, info)
                        recovery = f".retention-recovery-{secrets.token_hex(16)}"
                        os.mkdir(recovery, mode=0o700, dir_fd=run_descriptor)
                        recovery_descriptor = os.open(recovery, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=run_descriptor)
                        stack.callback(os.close, recovery_descriptor)
                        try:
                            os.rename(path.name, path.name, src_dir_fd=descriptor, dst_dir_fd=recovery_descriptor)
                            recovery_path = str(run / recovery / path.name)
                            moved = os.stat(path.name, dir_fd=recovery_descriptor, follow_symlinks=False)
                            # Rename changes ctime; inode, size and mtime must still match the preview.
                            if not stat.S_ISREG(moved.st_mode) or moved.st_nlink != 1 or signature(moved)[:4] != row["signature"][:4]:
                                raise ValueError("移动时文件已变化，已保留在恢复位置")
                            self.check_cleanup_protection(run, moved)
                            os.unlink(path.name, dir_fd=recovery_descriptor)
                            recovery_path = None
                        finally:
                            if recovery_path is None:
                                try:
                                    os.rmdir(recovery, dir_fd=run_descriptor)
                                except OSError:
                                    pass
                    deleted.append(row["path"])
                except (OSError, ValueError) as error:
                    failed.append({"path": row["path"], "error": str(error), "recovery_path": recovery_path})
            return {"deleted": deleted, "failed": failed, "preview": self.preview()}

    def check_cleanup_protection(self, run, info):
        reason = self.protection(run)
        if reason:
            raise ValueError(reason)
        active = self.active_session()
        if active == "unknown" or active == str(run / "blurt"):
            raise ValueError("录制进行中或状态无法核验，已保护")
        if info.st_mtime_ns > (run / "blurt/items.json").stat().st_mtime_ns:
            raise ValueError("录音晚于处理结果，需重新处理，已保护")
