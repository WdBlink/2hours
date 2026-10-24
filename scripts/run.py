#!/usr/bin/env python3
"""Prepare a private review run and render its host-authored evidence report."""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]


def codex_home():
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex").expanduser()


def skill_dir(name):
    candidates = [ROOT / "vendor" / name, codex_home() / "skills" / name, Path.home() / ".agents/skills" / name]
    return next((p for p in candidates if (p / "SKILL.md").is_file()), candidates[0])


def scope_for(period, timezone, start=None, end=None, now=None):
    zone = ZoneInfo(timezone)
    now = (now or datetime.now(zone)).astimezone(zone)
    today = now.date()
    if start or end:
        if not (start and end):
            raise ValueError("--start and --end are required together (inclusive dates)")
        first = datetime.strptime(start, "%Y-%m-%d").date()
        last = datetime.strptime(end, "%Y-%m-%d").date()
    elif period == "yesterday":
        first = last = today - timedelta(days=1)
    elif period == "last-week":
        last = today - timedelta(days=today.weekday() + 1)
        first = last - timedelta(days=6)
    elif period == "this-week":
        first, last = today - timedelta(days=today.weekday()), today
    elif period == "last-7-days":
        first, last = today - timedelta(days=6), today
    else:
        first = last = today
    begin = datetime.combine(first, time.min, zone)
    finish = min(datetime.combine(last + timedelta(days=1), time.min, zone), now)
    if begin >= finish or first > last:
        raise ValueError("review scope must be nonempty and not in the future")
    return {"start": begin.isoformat(), "end_exclusive": finish.isoformat(), "timezone": timezone}


def init(args):
    scope = scope_for(args.period, args.timezone, args.start, args.end)
    root = Path(args.run).expanduser() if args.run else Path.home() / ".2hours/runs" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    root.mkdir(parents=True, exist_ok=False, mode=0o700)
    manifest = {"run_id": root.name, "scope": scope, "sources": [], "panels": [],
                "coverage": {"read": 0, "failures": [], "skipped": [], "status": "not_collected"}, "targets": []}
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"run": str(root.resolve()), "scope": scope, "next": "Collect evidence with Traceink and Cubox; write manifest.json and report.md"}, ensure_ascii=False))


def doctor(args):
    names = ["traceink", "blurt", "cubox", "answer-me-with-html"]
    paths = {name: str(skill_dir(name)) if (skill_dir(name) / "SKILL.md").is_file() else None for name in names}
    bins = {name: shutil.which(name) for name in ["node", "uv", "cubox-cli"]}
    cubox = skill_dir("cubox") / "cli/bin" / ("cubox-cli.exe" if os.name == "nt" else "cubox-cli")
    if cubox.is_file() and os.access(cubox, os.X_OK):
        bins["cubox-cli"] = str(cubox)
    node_version = None
    if bins["node"]:
        node_version = subprocess.check_output([bins["node"], "--version"], text=True).strip()
    missing = [name for name, path in {**paths, **bins}.items() if not path]
    if node_version and int(node_version.lstrip("v").split(".")[0]) < 20:
        missing.append("Node.js >=20")
    print(json.dumps({"skills": paths, "commands": bins, "node_version": node_version,
                      "missing": missing, "cubox_setup": ["node", str(skill_dir("cubox") / "cli/scripts/install.js")],
                      "recording_permissions": "not_tested", "asr": "run Blurt doctor before first recording"}, ensure_ascii=False, indent=2))
    return 1 if missing else 0


def validate_report(manifest, markdown):
    if not isinstance(manifest.get("sources"), list) or not manifest.get("panels"):
        raise ValueError("collect evidence and create panels before rendering")
    sources = [s["id"] for s in manifest["sources"]]
    if len(sources) != len(set(sources)):
        raise ValueError("duplicate source IDs")
    panels = [p["id"] for p in manifest["panels"]]
    if len(panels) != len(set(panels)) or any(not re.fullmatch(r"[A-Z][0-9]{0,2}", p) for p in panels):
        raise ValueError("panels need unique IDs such as A1 or B2")
    for panel in manifest["panels"]:
        refs = panel.get("source_ids")
        empty_review = not sources and manifest.get("coverage", {}).get("status") == "complete"
        if not isinstance(refs, list) or (not refs and not empty_review) or not set(refs).issubset(sources):
            raise ValueError(f"panel {panel['id']} has unknown or missing sources")
    headings = re.findall(r"^## ([A-Z][0-9]{0,2})\s+", markdown, re.M)
    if headings != panels:
        raise ValueError("report headings must match manifest panel IDs in order")
    if "<" in markdown or re.search(r"^\s*(`{3,}|~{3,})\s*(html|svg)\b", markdown, re.M | re.I):
        raise ValueError("raw HTML/SVG is disabled for source reports; escape '<' as '&lt;'")
    if re.search(r"^\s*(`{3,}|~{3,}).*\bsrc\s*=", markdown, re.M):
        raise ValueError("file inclusion is disabled; quote only evidence already read")


def render(args):
    root = Path(args.run).expanduser().resolve()
    if (root / "report.html").exists():
        raise ValueError("report already frozen; create a new run to change its content")
    manifest = json.loads((root / "manifest.json").read_text())
    markdown = (root / "report.md").read_text()
    validate_report(manifest, markdown)
    renderer = skill_dir("answer-me-with-html") / "scripts/am.mjs"
    if not renderer.is_file() or not shutil.which("node"):
        raise ValueError("answer-me-with-html and Node.js >=20 are required; run doctor")
    draft = root / "report.build.html"
    env = dict(os.environ, AM_NO_UPDATE_CHECK="1")
    completed = subprocess.run([shutil.which("node"), str(renderer), "render", str(root / "report.md"),
                                "-o", str(draft), "--no-open"], cwd=root, env=env, capture_output=True, text=True, timeout=60)
    if completed.returncode or not draft.is_file():
        raise ValueError("renderer failed: " + completed.stdout + completed.stderr)
    html = draft.read_text()
    actual = re.findall(r'<section\b[^>]*\bid="panel-([A-Z][0-9]{0,2})"', html)
    if actual != [p["id"] for p in manifest["panels"]]:
        draft.unlink()
        raise ValueError("renderer panel IDs differ from manifest; report was not frozen")
    if (root / "board.json").exists():
        draft.replace(root / "report.am.html")
        subprocess.run([sys.executable, str(Path(__file__).with_name("build_board.py")),
                        "--run", str(root)], check=True, timeout=30)
        html = (root / "report.html").read_text()
    else:
        draft.replace(root / "report.html")
    print(completed.stdout.strip())
    print(json.dumps({"report": str(root / "report.html"), "sha256": hashlib.sha256(html.encode()).hexdigest(),
                      "next": "review_bridge.py --run RUN"}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("init")
    p.add_argument("--period", choices=["today", "yesterday", "last-week", "this-week", "last-7-days"], default="yesterday")
    p.add_argument("--timezone", default="Asia/Shanghai")
    p.add_argument("--start")
    p.add_argument("--end")
    p.add_argument("--run")
    p.set_defaults(fn=init)
    p = sub.add_parser("doctor")
    p.set_defaults(fn=doctor)
    p = sub.add_parser("render")
    p.add_argument("--run", required=True)
    p.set_defaults(fn=render)
    args = parser.parse_args()
    try:
        return args.fn(args) or 0
    except (ValueError, KeyError, OSError, subprocess.SubprocessError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
