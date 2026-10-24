#!/usr/bin/env python3
"""Build the grouped review board from rendered AM panels and board.json."""
import argparse
import base64
import hashlib
import html
import json
import re
from html.parser import HTMLParser
from pathlib import Path

class PanelExtractor(HTMLParser):
    def __init__(self, source):
        super().__init__(convert_charrefs=False)
        self.source = source
        self.offsets = [0]
        for line in source.splitlines(keepends=True):
            self.offsets.append(self.offsets[-1] + len(line))
        self.root_attrs = {}
        self.styles = []
        self.panels = {}
        self.panel_start = None
        self.panel_id = None
        self.section_depth = 0
        self.style_start = None

    def source_position(self):
        line, column = self.getpos()
        return self.offsets[line - 1] + column

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "html":
            self.root_attrs = attrs
        if tag == "style":
            assert self.style_start is None, "Nested style element"
            self.style_start = self.source_position() + len(self.get_starttag_text())
        if tag == "section":
            if self.panel_start is not None:
                self.section_depth += 1
            elif "am-panel" in attrs.get("class", "").split():
                self.panel_start = self.source_position()
                self.panel_id = attrs.get("id")
                assert self.panel_id, "Panel missing ID"
                assert self.panel_id not in self.panels, "Duplicate panel ID"
                self.section_depth = 1

    def handle_endtag(self, tag):
        if tag == "style":
            assert self.style_start is not None, "Unmatched style end"
            self.styles.append(self.source[self.style_start:self.source_position()])
            self.style_start = None
        if tag == "section" and self.panel_start is not None:
            self.section_depth -= 1
            if self.section_depth == 0:
                end = self.source.index(">", self.source_position()) + 1
                self.panels[self.panel_id] = self.source[self.panel_start:end]
                self.panel_start = None
                self.panel_id = None


class StaticPanelCheck(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()

    def handle_starttag(self, tag, attrs):
        assert tag not in {"script", "iframe", "object", "embed", "form", "style", "link", "meta", "base"}, "Active content in extracted panel"
        for name, value in attrs:
            assert not name.startswith("on"), "Inline event handler in extracted panel"
            if name in {"href", "src", "xlink:href"} and value:
                assert not re.match(r'(?i)\s*(javascript|vbscript|file):', value), "Executable or local-file URL"
            if name == "id":
                assert value not in self.ids, f"Duplicate ID: {value}"
                self.ids.add(value)


def esc(value):
    return html.escape(str(value), quote=True)


def build(run):
    run = Path(run).resolve()
    if (run / 'report.html').exists():
        raise ValueError('report already frozen; create a new run')
    manifest = json.loads((run / 'manifest.json').read_text())
    data = json.loads((run / 'board.json').read_text())
    source = (run / 'report.am.html').read_text()
    parser = PanelExtractor(source)
    parser.feed(source)
    parser.close()
    assert parser.panel_start is None and parser.style_start is None
    cards = data['cards']
    ids = [c['id'] for c in cards]
    expected = {p['id'] for p in manifest['panels']}
    if (not 1 <= len(cards) <= 8 or len(ids) != len(set(ids)) or set(ids) != expected
            or set(parser.panels) != {f'panel-{i}' for i in ids}
            or any(not re.fullmatch(r'[A-Za-z0-9_-]+', i) for i in ids)):
        raise ValueError('board cards and rendered panels must match manifest, at most 8')
    check = StaticPanelCheck()
    for panel in parser.panels.values():
        check.feed(panel)
    groups = [('需要判断', 'decide'), ('继续跟进', 'follow'), ('结果与来源', 'result')]
    if any(c.get('lane') not in {key for _, key in groups} for c in cards):
        raise ValueError('unknown board lane')
    if any(sum(c['lane'] == key for c in cards) > 3 for _, key in groups):
        raise ValueError('at most 3 cards per lane')
    assets = Path(__file__).resolve().parent.parent / 'assets'
    css = (assets / 'board.css').read_text()
    js = (assets / 'board.js').read_text()
    digest = base64.b64encode(hashlib.sha256(js.encode()).digest()).decode()

    def card(c):
        title = esc(c['title'])
        return f'''<button type="button" class="review-card" data-panel="panel-{esc(c['id'])}" data-title="{title}" data-context="{esc(c.get('context', ''))}" data-read="false" aria-haspopup="dialog" aria-controls="review-window" aria-label="打开：{title}">
<span class="paperclip" aria-hidden="true"></span><span class="card-top"><span class="card-label">{esc(c.get('label', ''))}<span class="comment-label"></span></span><span class="read-label">待审阅</span></span>
<h2>{title}</h2><span class="card-bottom"><span class="card-value">{esc(c.get('value', ''))}<small>{esc(c.get('unit', ''))}</small></span><span class="card-state">{esc(c.get('state', ''))}</span></span></button>'''

    note = f'''<aside class="desk-note" aria-label="本次报告信息"><p>{esc(data['period_label'])}</p><span>{esc(data['review_label'])}</span><span>{esc(data['coverage_label'])}</span></aside>'''
    lanes = []
    for label,key in groups:
        members = [c for c in cards if c['lane'] == key]
        lanes.append(f'<section class="lane"><h2>{label}<small>{len(members)} 份</small></h2>'+''.join(card(c) for c in members)+'</section>')
    content = f'''<!doctype html><html lang="zh-CN" data-theme="shadcn" data-mode="light" data-scale="1.25"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'sha256-{digest}'; style-src 'unsafe-inline'; img-src 'self' data:; font-src data:; connect-src 'self'; form-action 'none'; base-uri 'none'">
<title>2hours · 集中审阅</title><style>{''.join(parser.styles)}</style><style>{css}</style></head><body class="layout-lanes">
<main class="desk"><header class="topbar"><a class="brand" href="/" aria-label="2hours 集中审阅"><svg viewBox="0 0 30 34" fill="none" aria-hidden="true"><path d="M5 2h15l6 6v24H5zM10 14h11M10 19h11M10 24h7" stroke="#557178" stroke-width="1.2"/></svg>2hours<small>集中审阅</small></a><section id="review-controls"></section></header>
<section class="intro"><div><h1>{esc(data['title'])}</h1><p>{esc(data['subtitle'])}</p></div>{note}</section>
<div class="board" aria-label="{len(cards)} 份审阅简报">{''.join(lanes)}</div>
<footer class="footer"><span>曲别针＝待审阅 · 已读可在详情中标记</span><span id="review-status"></span></footer></main>
<dialog id="review-window" class="review-window" aria-labelledby="window-label"><header class="window-bar"><h2 class="window-heading"><span id="window-label"></span><span class="window-context"></span></h2><button class="window-close" type="button" aria-label="关闭详情，返回看板" title="关闭（Esc）"><span aria-hidden="true">×</span></button></header><div class="window-content"></div></dialog>
<dialog id="notes-window" class="review-window" aria-labelledby="notes-label"><header class="window-bar"><h2 id="notes-label" class="window-heading">集中审核<span class="window-context">原话、理解、去向 · 最后确认一次</span></h2><button class="window-close" type="button" aria-label="收起集中审核，保留草稿" title="收起（Esc）"><span aria-hidden="true">×</span></button></header><div id="notes-content" class="window-content"></div></dialog>
<div id="panel-store" hidden>{''.join(parser.panels.values())}</div><script>{js}</script></body></html>'''
    with (run / 'report.html').open('x') as file:
        file.write(content)
    return run / 'report.html'


if __name__ == '__main__':
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--run', required=True, type=Path)
    args = cli.parse_args()
    try:
        print(build(args.run))
    except (OSError, ValueError, KeyError, AssertionError) as error:
        cli.exit(1, f'Cannot build board: {error}\n')
