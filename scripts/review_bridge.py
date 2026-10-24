#!/usr/bin/env python3
"""Serve one frozen report and hand user-triggered recording/approval events to Codex."""
from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import webbrowser
import importlib.util

MAX_BODY = 65536

_spec = importlib.util.spec_from_file_location("twohours_dispatch", Path(__file__).with_name("dispatch.py"))
dispatch = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dispatch)


def now():
    return datetime.now().astimezone().isoformat()


class Rejected(Exception):
    def __init__(self, message, status=409):
        super().__init__(message)
        self.status = status


def emit(event):
    print(json.dumps(event, ensure_ascii=False), flush=True)


def inject_bridge(report, config, script):
    """Pin only our additional script, preserving every existing CSP directive."""
    pattern = r'(<meta\s+http-equiv="Content-Security-Policy"\s+content=")([^"]+)(")'
    match = re.search(pattern, report, re.I)
    if not match or not re.search(r'(?:^|;)\s*script-src\s', match[2]):
        raise ValueError("报告缺少可识别的 CSP/script-src；请用 answer-me-with-html 重新生成")
    policy = html.unescape(match[2])
    if not re.search(r"(?:^|;)\s*connect-src\s+[^;]*'self'", policy):
        raise ValueError("报告 CSP 必须允许 connect-src 'self'")
    digest = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
    policy = re.sub(r"((?:^|;)\s*script-src\s+)", rf"\1'sha256-{digest}' ", policy, count=1)
    report = report[:match.start(2)] + html.escape(policy, quote=True) + report[match.end(2):]
    payload = json.dumps(config, ensure_ascii=True).replace("<", "\\u003c")
    addition = f'<script id="twohours-config" type="application/json">{payload}</script><script>{script}</script>'
    if not re.search(r"</body\s*>", report, re.I):
        raise ValueError("报告缺少 body 结束标签")
    return re.sub(r"</body\s*>", lambda _: addition + "</body>", report, count=1, flags=re.I)


class ReviewRun:
    def __init__(self, run, blurt_skill, event_sink=emit):
        self.run = Path(run).resolve(strict=True)
        self.blurt = Path(blurt_skill).expanduser().resolve()
        self.snapshot = {name: (self.run / name).read_bytes() for name in ("report.html", "manifest.json")}
        if (self.run / "report.md").is_file():
            self.snapshot["report.md"] = (self.run / "report.md").read_bytes()
        manifest = json.loads(self.snapshot["manifest.json"])
        self.panels = manifest.get("panels", [])
        ids = [p["id"] for p in self.panels]
        if not ids or len(set(ids)) != len(ids) or any(not re.fullmatch(r"[A-Za-z0-9_-]+", i) for i in ids):
            raise ValueError("manifest.panels 必须包含唯一的稳定 ID")
        report = self.snapshot["report.html"].decode("utf-8")
        if any(not re.search(r'\bid=[\'\"]panel-' + re.escape(i) + r'[\'\"]', report) for i in ids):
            raise ValueError("报告 panel ID 与 manifest 不匹配")
        self.token = secrets.token_urlsafe(32)
        script = (Path(__file__).resolve().parent.parent / "assets" / "bridge.js").read_text()
        self.page = inject_bridge(report, {"token": self.token, "panels": ids, "run_id": manifest.get("run_id", self.run.name)}, script).encode()
        self.lock = threading.Lock()
        self.event_sink = event_sink
        self.log = self.run / "recording.log"
        self.state = {"state": "existing" if self.log.exists() else "idle"}
        self.state_file = Path(os.environ.get("BLURT_HOME", Path.home() / ".blurt")) / "recording.json"

    def unchanged(self):
        if any(not (self.run / name).is_file() or (self.run / name).read_bytes() != content
               for name, content in self.snapshot.items()):
            raise Rejected("本轮报告或 manifest 已改变，请新建运行，避免批注定位失效")

    def status(self):
        with self.lock:
            result = dict(self.state)
        missing = []
        if not shutil.which("uv"):
            missing.append("未找到 uv")
        if not (self.blurt / "scripts" / "record.py").is_file():
            missing.append("未找到 Blurt scripts/record.py")
        result.update(can_record=result["state"] == "idle" and not missing, dependencies=missing)
        return result

    def review_state(self):
        path = self.run / "review-state.json"
        state = json.loads(path.read_text()) if path.exists() else {"panels": {}}
        if not isinstance(state, dict) or not isinstance(state.get("panels"), dict):
            raise Rejected("审阅状态文件无效；请保留文件并在当前会话检查")
        for panel in self.panels:
            state["panels"].setdefault(panel["id"], {"read": False, "comment": "", "revision": 0})
        submitted = self.run / "written-items.json"
        state["submitted"] = json.loads(submitted.read_text()) if submitted.exists() else None
        return state

    def save_review(self, body):
        panel_id = body.get("panel_id")
        if (panel_id not in {p["id"] for p in self.panels}
                or type(body.get("revision")) is not int
                or not set(body) <= {"panel_id", "revision", "read", "comment"}
                or not ({"read", "comment"} & set(body))
                or ("read" in body and type(body["read"]) is not bool)
                or ("comment" in body and (not isinstance(body["comment"], str) or len(body["comment"]) > 10000))):
            raise Rejected("无效的审阅状态或意见（最多 10000 字）", 400)
        with self.lock:
            self.unchanged()
            state = self.review_state()
            current = state["panels"][panel_id]
            if body["revision"] != current["revision"]:
                raise Rejected("此卡片已在另一页面更新。请先复制当前输入，再刷新核对，避免覆盖意见")
            current.update({key: body[key] for key in ("read", "comment") if key in body})
            current.update(revision=current["revision"] + 1, updated_at=now())
            dispatch.write_json(self.run / "review-state.json", {"panels": state["panels"]})
            return state

    def submit_comments(self, body):
        with self.lock, dispatch.locked(self.run):
            self.unchanged()
            state = self.review_state()
            versions = {key: value["revision"] for key, value in state["panels"].items()}
            if body.get("revisions") != versions:
                raise Rejected("批注已改变，请刷新汇总后重新提交")
            rows = [{"id": f"W-{p['id']}", "kind": "task", "title": p["title"],
                     "panel_ids": [p["id"]], "quote": state["panels"][p["id"]]["comment"],
                     "status": "confirmed", "confidence": "high", "questions": [], "answer": "",
                     "input_method": "written"}
                    for p in self.panels if state["panels"][p["id"]]["comment"].strip()]
            if not rows:
                raise Rejected("暂无已保存的文字意见", 400)
            previous = state["submitted"]
            if previous and previous.get("items") == rows:
                return previous
            if (self.run / dispatch.CONFIRMATION).exists():
                raise Rejected("本轮已完成集中审阅。请新建下一轮，避免改变已确认的意见")
            attempts = self.run / "dispatch-state.json"
            if attempts.exists() and json.loads(attempts.read_text()).get("batches"):
                raise Rejected("本轮已有分发记录。请新建下一轮审阅，避免覆盖已发送的意见")
            submission = {"reviewed": True, "input_method": "written", "submitted_at": now(), "items": rows}
            dispatch.write_json(self.run / "written-items.json", submission)
        self.event_sink({"event": "comments_submitted", "count": len(rows), "file": str(self.run / "written-items.json")})
        return submission

    def active_recording(self):
        if not self.state_file.exists():
            return False
        try:
            state = json.loads(self.state_file.read_text())
            if not isinstance(state, dict):
                raise ValueError("invalid state")
            pid = state.get("pid")
            if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
                raise ValueError("invalid pid")
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except (ValueError, OSError):
            raise Rejected("无法核验 Blurt 录制状态，请先检查 Blurt；不会启动或停止其他录制")

    def start(self):
        with self.lock:
            self.unchanged()
            if self.state["state"] != "idle":
                raise Rejected("本轮已经启动过录制，请继续处理结果或新建运行")
            if self.active_recording():
                raise Rejected("Blurt 已有活跃录制，请先在原录制窗口完成；本页面不会停止它")
            uv = shutil.which("uv")
            script = self.blurt / "scripts" / "record.py"
            if not uv or not script.is_file():
                raise Rejected("需要已安装的 uv 和 Blurt scripts/record.py", 503)
            session = self.run / "blurt"
            if session.is_symlink() or (session.exists() and any(session.iterdir())):
                raise Rejected("本轮 blurt 目录已有内容，请新建运行，避免覆盖录制")
            session.mkdir(mode=0o700, exist_ok=True)
            log_created = False
            process = None
            try:
                with self.log.open("x", encoding="utf-8") as log:
                    log_created = True
                    os.chmod(self.log, 0o600)
                    process = subprocess.Popen(
                        [uv, "run", str(script), "start", "--session", str(session)],
                        cwd=self.run, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                        start_new_session=True,
                    )
            except OSError as error:
                if process is None and log_created:
                    if self.log.stat().st_size == 0:
                        self.log.unlink()
                    else:
                        self.log.rename(self.run / f"recording-start-error-{secrets.token_hex(4)}.log")
                    self.state = {"state": "idle", "message": f"启动失败，可再次点击重试：{error}"}
                else:
                    self.state = {"state": "failed", "message": str(error)}
                raise Rejected(f"无法启动录制：{error}", 503)
            self.state = {"state": "recording", "started_at": now()}
            threading.Thread(target=self.watch, args=(process,), daemon=True).start()
            return dict(self.state)

    def watch(self, process):
        rc = process.wait()
        video = self.run / "blurt" / "recording.mp4"
        try:
            with self.log.open("rb") as log:
                log.seek(max(0, self.log.stat().st_size - MAX_BODY))
                output = log.read().decode("utf-8", errors="replace")
            marker = f"DONE video={video} duration="
            if rc == 0 and marker in output and video.is_file() and not video.is_symlink():
                state = {"state": "recorded", "video": str(video), "session": str(video.parent)}
            elif rc == 2:
                state = {"state": "cancelled", "message": "录制已取消，请新建运行后重试"}
            else:
                state = {"state": "failed", "message": f"录制未完成（退出码 {rc}），请查看 recording.log"}
        except OSError as error:
            state = {"state": "failed", "message": f"无法读取录制结果：{error}"}
        with self.lock:
            self.state = state
        self.event_sink({"event": state["state"], **state, "log": str(self.log)})

    def plan(self):
        path = self.run / "dispatch-plan.json"
        if not path.exists():
            return None
        try:
            plan = json.loads(path.read_text())
            for name, digest in plan.get("snapshot", {}).items():
                if name not in (*dispatch.INPUTS, *dispatch.ITEM_INPUTS, dispatch.CONFIRMATION):
                    raise ValueError("invalid snapshot file")
                source = self.run / name
                if not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != digest:
                    return None
            ids = [batch["id"] for batch in plan["batches"]]
            if not isinstance(plan["plan_id"], str) or not plan["plan_id"] or not ids or len(ids) != len(set(ids)):
                raise ValueError("invalid plan")
            if any(not isinstance(i, str) or not i for i in ids):
                raise ValueError("invalid batch id")
            plan["approved_batch_ids"] = []
            approval_path = self.run / "approval.json"
            if approval_path.exists():
                approval = json.loads(approval_path.read_text())
                if approval.get("plan_id") == plan["plan_id"]:
                    approved = approval["batch_ids"]
                    if not isinstance(approved, list) or not set(approved) <= set(ids):
                        raise ValueError("invalid approval")
                    plan["approved_batch_ids"] = approved
            return plan
        except (ValueError, KeyError, TypeError) as error:
            raise Rejected("dispatch-plan.json 无效，请重新准备分发方案") from error

    def review_plan(self):
        if not (self.run / "review-plan.json").exists():
            return None
        try:
            with dispatch.locked(self.run):
                self.unchanged()
                review = dispatch.current_review(self.run)
                confirmation_path = self.run / dispatch.CONFIRMATION
                decisions = {}
                if confirmation_path.exists():
                    confirmation = dispatch.read_json(confirmation_path)
                    if confirmation.get("review_id") != review["review_id"]:
                        raise dispatch.DispatchError("Review source snapshot changed")
                    decisions = dispatch.review_decisions(review, confirmation.get("tasks"))
                review["approval"] = None
                approval_path = self.run / "approval.json"
                if approval_path.exists():
                    approval = dispatch.read_json(approval_path)
                    plan = dispatch.current_plan(self.run)
                    if approval.get("review_id") != review["review_id"] or approval.get("plan_id") != plan["plan_id"]:
                        raise dispatch.DispatchError("Approval does not match this review")
                    review["approval"] = approval
                for task in review["tasks"]:
                    decision = decisions.get(task["id"])
                    task.update(decision or {"selected": False, "answers": {}})
                    for item in task["source_items"]:
                        if item["id"] in task["answers"]:
                            item["answer"] = task["answers"][item["id"]]
                        images = []
                        for frame in item.get("frames", []):
                            value = frame.get("path") if isinstance(frame, dict) else frame
                            path = dispatch.frame_path(self.run, value)
                            if not path:
                                continue
                            name = path.relative_to(self.run).as_posix()
                            if name in review["evidence_files"]:
                                key = hashlib.sha256(name.encode()).hexdigest()
                                images.append({"url": f"/evidence/{review['review_id']}/{key}",
                                               "caption": frame.get("caption", "口述时的画面") if isinstance(frame, dict) else "口述时的画面"})
                        item["evidence_images"] = images
                return review
        except dispatch.DispatchError as error:
            raise Rejected(f"集中审阅已变化：{error}。请重新准备或新建一轮") from error

    def evidence(self, url):
        match = re.fullmatch(r"/evidence/([0-9a-f]{64})/([0-9a-f]{64})", url)
        if not match:
            raise Rejected("不存在的画面", 404)
        try:
            with dispatch.locked(self.run):
                review = dispatch.current_review(self.run)
                if match[1] != review["review_id"]:
                    raise Rejected("画面所属审阅已变化", 409)
                name = next((name for name in review["evidence_files"]
                             if hashlib.sha256(name.encode()).hexdigest() == match[2]), None)
                path = dispatch.frame_path(self.run, name.removeprefix("blurt/")) if name else None
                if not path:
                    raise Rejected("不存在的画面", 404)
                data = path.read_bytes()
                if hashlib.sha256(data).hexdigest() != review["evidence_files"][name]:
                    raise Rejected("画面内容已变化，请重新准备审阅")
                if data.startswith(b"\x89PNG\r\n\x1a\n"):
                    mime = "image/png"
                elif data.startswith(b"\xff\xd8\xff"):
                    mime = "image/jpeg"
                elif data.startswith(b"RIFF") and data[8:12] == b"WEBP":
                    mime = "image/webp"
                else:
                    raise Rejected("不支持的画面格式", 415)
                return data, mime
        except dispatch.DispatchError as error:
            raise Rejected(f"画面所属审阅已变化：{error}") from error

    def approve_review(self, body):
        try:
            with self.lock:
                self.unchanged()
                approval, fresh = dispatch.approve_review(self.run, body)
        except dispatch.DispatchError as error:
            raise Rejected(f"无法完成集中审阅：{error}") from error
        if fresh:
            self.event_sink({"event": "approved", **approval})
        return approval

    def approve(self, body):
        with self.lock:
            self.unchanged()
            if (self.run / "review-plan.json").exists():
                raise Rejected("本轮请在集中审阅中统一确认理解与分发")
            plan = self.plan()
            chosen = body.get("batch_ids")
            if not plan or body.get("plan_id") != plan["plan_id"]:
                raise Rejected("分发方案已变化，请刷新并重新确认")
            if (not isinstance(chosen, list) or not chosen or any(not isinstance(i, str) for i in chosen)
                    or len(chosen) != len(set(chosen)) or not set(chosen) <= {b["id"] for b in plan["batches"]}):
                raise Rejected("请选择当前方案中的有效批次", 400)
            approval = {"plan_id": plan["plan_id"], "batch_ids": chosen, "approved_at": now()}
            path = self.run / "approval.json"
            if path.exists():
                previous = json.loads(path.read_text())
                if previous.get("plan_id") == approval["plan_id"]:
                    merged = set(plan["approved_batch_ids"]) | set(chosen)
                    if merged == set(plan["approved_batch_ids"]):
                        return previous
                    approval["batch_ids"] = [batch["id"] for batch in plan["batches"] if batch["id"] in merged]
            temporary = self.run / ".approval.tmp"
            with temporary.open("w", encoding="utf-8") as file:
                os.chmod(temporary, 0o600)
                json.dump(approval, file, ensure_ascii=False, indent=2)
                file.flush()
                os.fsync(file.fileno())
            temporary.replace(path)
        self.event_sink({"event": "approved", **approval})
        return approval

    def focus(self, body):
        if body.get("panel_id") not in {p["id"] for p in self.panels} or body.get("kind") not in ("click", "focus"):
            raise Rejected("无效的报告定位事件", 400)
        with self.lock, (self.run / "focus.jsonl").open("a", encoding="utf-8") as file:
            os.chmod(self.run / "focus.jsonl", 0o600)
            file.write(json.dumps({"panel_id": body["panel_id"], "kind": body["kind"],
                                   "wall_time": now(), "time_basis": "wall_clock_only"}) + "\n")
        return {"ok": True}


def make_server(run):
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def log_message(self, *args):
            pass

        def reply(self, status, value, content_type="application/json; charset=utf-8"):
            data = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode()
            self.send_response(status)
            for key, val in {"Content-Type": content_type, "Content-Length": str(len(data)),
                             "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                             "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer"}.items():
                self.send_header(key, val)
            self.end_headers()
            self.wfile.write(data)

        def check_origin(self, post=False):
            host = f"127.0.0.1:{self.server.server_port}"
            origin = self.headers.get("Origin")
            if self.headers.get("Host") != host or (origin is not None and origin != f"http://{host}"):
                raise Rejected("拒绝非本机同源请求", 403)
            if post and (origin != f"http://{host}" or not secrets.compare_digest(
                    self.headers.get("X-2hours-Token", "").encode("utf-8"), run.token.encode())):
                raise Rejected("请求缺少有效的同源确认", 403)

        def do_GET(self):
            try:
                self.check_origin()
                if self.path in ("/", "/report.html"):
                    self.reply(200, run.page, "text/html; charset=utf-8")
                elif self.path == "/status":
                    self.reply(200, run.status())
                elif self.path == "/plan":
                    self.reply(200, run.plan())
                elif self.path == "/review-state":
                    self.reply(200, run.review_state())
                elif self.path == "/review-plan":
                    self.reply(200, run.review_plan())
                elif self.path.startswith("/evidence/"):
                    data, mime = run.evidence(self.path)
                    self.reply(200, data, mime)
                else:
                    self.reply(404, {"error": "不存在的路径"})
            except Rejected as error:
                self.reply(error.status, {"error": str(error)})
            except (OSError, ValueError) as error:
                self.reply(500, {"error": f"本地状态读取失败：{error}"})

        def do_POST(self):
            try:
                self.check_origin(post=True)
                routes = {"/record": lambda _: run.start(), "/focus": run.focus, "/approve": run.approve,
                          "/approve-review": run.approve_review,
                          "/review-state": run.save_review, "/submit-comments": run.submit_comments}
                if self.path not in routes:
                    raise Rejected("不存在的操作", 404)
                if self.headers.get_content_type() != "application/json" or self.headers.get("Transfer-Encoding"):
                    raise Rejected("只接受有长度的 JSON 请求", 400)
                try:
                    length = int(self.headers.get("Content-Length", ""))
                except ValueError:
                    raise Rejected("缺少有效的请求长度", 400)
                if not 0 < length <= MAX_BODY:
                    raise Rejected("请求过大或为空", 413)
                try:
                    body = json.loads(self.rfile.read(length))
                    if not isinstance(body, dict):
                        raise ValueError("object required")
                except (ValueError, UnicodeError):
                    raise Rejected("无效 JSON 对象", 400)
                self.reply(200, routes[self.path](body))
            except Rejected as error:
                self.reply(error.status, {"error": str(error)})
            except (OSError, ValueError) as error:
                self.reply(500, {"error": f"本地文件操作失败：{error}"})

    return ThreadingHTTPServer(("127.0.0.1", 0), Handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--blurt-skill", type=Path, default=Path(__file__).resolve().parents[1] / "vendor/blurt")
    parser.add_argument("--open", action="store_true", help="打开报告；不会自动开始录制")
    args = parser.parse_args()
    try:
        run = ReviewRun(args.run, args.blurt_skill)
        server = make_server(run)
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, f"无法打开审阅：{error}\n")
    url = f"http://127.0.0.1:{server.server_port}/"
    emit({"event": "ready", "url": url, "run": str(run.run)})
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
