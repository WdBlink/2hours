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
from urllib.parse import urlparse

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
    if (len(ids) != len(set(ids)) or set(ids) != expected
            or set(parser.panels) != {f'panel-{i}' for i in ids}
            or any(not re.fullmatch(r'[A-Za-z0-9_-]+', i) for i in ids)):
        raise ValueError('board cards and rendered panels must match manifest')
    check = StaticPanelCheck()
    for panel in parser.panels.values():
        check.feed(panel)
    groups = [('需要判断', 'decide'), ('继续跟进', 'follow'), ('结果与来源', 'result')]
    sources = {item['id']: item for item in manifest.get('sources', [])}
    panels = {item['id']: item for item in manifest['panels']}
    lanes = {key: label for label, key in groups}
    areas = {}
    for c in cards:
        refs = [sources.get(key, {}) for key in panels[c['id']].get('source_ids', [])]
        area = c.get('area', 'reading' if refs and all(item.get('provider') == 'cubox' for item in refs) else 'work')
        if area not in {'work', 'reading'}:
            raise ValueError('unknown board area')
        if c.get('lane') not in lanes and not (area == 'reading' and 'lane' not in c):
            raise ValueError('unknown board lane')
        areas[c['id']] = area
    assets = Path(__file__).resolve().parent.parent / 'assets'
    css = (assets / 'board.css').read_text()
    js = (assets / 'board.js').read_text()
    digest = base64.b64encode(hashlib.sha256(js.encode()).digest()).decode()
    mark = base64.b64encode((assets / 'brand/mark.svg').read_bytes()).decode()
    icon = base64.b64encode((assets / 'brand/icon.png').read_bytes()).decode()

    def card(c):
        title = esc(c['title'])
        area = areas[c['id']]
        summary = c.get('summary', '')
        refs = [sources.get(key, {}) for key in panels[c['id']].get('source_ids', [])]
        links = []
        if area == 'reading':
            for item in refs:
                if item.get('provider') != 'cubox' or not item.get('url'):
                    continue
                url = urlparse(item['url'])
                if url.scheme != 'https' or not url.netloc or url.username or url.password:
                    raise ValueError('reading source links must be absolute HTTPS URLs')
                label = esc(item.get('title', item['id']))
                links.append(f'<a href="{esc(item["url"])}" target="_blank" rel="noopener noreferrer" title="{label}" aria-label="原文：{label}">原文{len(links) + 1}</a>')
        source_links = f'<div class="reading-links" aria-label="文章原文">{"".join(links)}</div>' if links else ''
        summary_html = f'<span class="card-summary">{esc(summary)}</span>' if summary else ''
        value = f'<span class="card-value">{esc(c.get("value", ""))}<small>{esc(c.get("unit", ""))}</small></span>' if c.get('value') not in (None, '') else ''
        category = '文章' if area == 'reading' else lanes[c['lane']]
        return f'''<article class="card-shell"><button type="button" class="review-card" data-panel="panel-{esc(c['id'])}" data-title="{title}" data-context="{esc(c.get('context', ''))}" data-summary="{esc(summary)}" data-value="{esc(c.get('value', ''))}" data-unit="{esc(c.get('unit', ''))}" data-state="{esc(c.get('state', ''))}" data-read="false" aria-haspopup="dialog" aria-controls="review-window" aria-label="打开：{esc(c['id'])} · {title}">
<span class="paperclip" aria-hidden="true"></span><span class="card-top"><span class="card-label">{esc(c.get('label') or category)}<span class="comment-label"></span></span><span class="read-label">待审阅</span></span>
<h2><span class="card-id">{esc(c['id'])}</span>{title}</h2>{summary_html}<span class="card-bottom">{value}<span class="card-state">{esc(c.get('state', ''))}</span></span></button>{source_links}</article>'''

    sections = []
    tabs = []
    for area, label in [('reading', 'Cubox 阅读'), ('work', 'Agent 工作')]:
        members = [c for c in cards if areas[c['id']] == area]
        tabs.append(f'<button type="button" data-area="{area}" aria-controls="shelf-{area}" aria-pressed="false">{label}<span>{len(members)}</span></button>')
        empty = '<p class="empty-shelf">本轮没有阅读材料</p>' if area == 'reading' else '<p class="empty-shelf">本轮没有工作事项</p>'
        sections.append(f'''<section class="board-shelf" id="shelf-{area}" data-area="{area}" aria-labelledby="heading-{area}"><header class="shelf-heading"><h2 id="heading-{area}">{label}</h2><span>{len(members)} 份</span></header>
<div class="card-grid" id="cards-{area}">{''.join(card(c) for c in members) or empty}</div>
<nav class="shelf-pages" aria-label="{label}分页" hidden><button type="button" data-step="-1" aria-controls="cards-{area}" aria-label="{label}上一页">上一页</button><span class="page-status" aria-live="polite"></span><button type="button" data-step="1" aria-controls="cards-{area}" aria-label="{label}下一页">下一页</button></nav></section>''')
    content = f'''<!doctype html><html lang="zh-CN" data-theme="shadcn" data-mode="light" data-scale="1.25"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'sha256-{digest}'; style-src 'unsafe-inline'; img-src 'self' data:; font-src data:; connect-src 'self'; form-action 'none'; base-uri 'none'">
<title>2hours · {esc(data.get("title", "集中审阅"))}</title><link rel="icon" type="image/png" href="data:image/png;base64,{icon}"><style>{''.join(parser.styles)}</style><style>{css}</style></head><body class="layout-review">
<main class="desk"><header class="topbar"><a class="brand" href="/" aria-label="2hours 集中审阅"><img src="data:image/svg+xml;base64,{mark}" alt="" width="32" height="32"><span>Two Hours</span></a><section id="review-controls"></section></header>
<section class="report-info" aria-label="本次报告信息"><h1>{esc(data['period_label'])}</h1><span>{esc(data['review_label'])}</span><span>{esc(data['coverage_label'])}</span></section>
<nav class="board-nav" aria-label="阅读与工作区域">{''.join(tabs)}</nav>
<div class="board" aria-label="{len(cards)} 份审阅简报">{''.join(sections)}</div>
<footer class="footer"><span>曲别针表示待审阅</span><span id="review-status"></span></footer></main>
<dialog id="review-window" class="review-window" aria-labelledby="window-label"><header class="window-bar"><h2 class="window-heading"><span id="window-label"></span><span class="window-context"></span></h2><button class="window-close" type="button" aria-label="关闭详情，返回看板" title="关闭（Esc）"><span aria-hidden="true">×</span></button></header><div class="window-content"></div></dialog>
<dialog id="notes-window" class="review-window" aria-labelledby="notes-label"><header class="window-bar"><h2 id="notes-label" class="window-heading"><span id="notes-title">汇总意见</span><span class="window-context">保存意见 → 提交整理 → 集中审核 · 最后确认一次</span></h2><button class="window-close" type="button" aria-label="收起意见与审核，保留草稿" title="收起（Esc）"><span aria-hidden="true">×</span></button></header><div id="notes-content" class="window-content"></div></dialog>
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
