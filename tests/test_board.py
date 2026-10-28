import importlib.util
import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).parents[1] / "scripts/build_board.py"
SPEC = importlib.util.spec_from_file_location("build_board", SCRIPT)
board = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(board)


class BoardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.run = Path(self.temp.name)
        (self.run / "manifest.json").write_text(json.dumps({"panels": [{"id": "A1"}]}))
        (self.run / "report.am.html").write_text('<html lang="zh-CN"><style>body{margin:0}</style><section class="am-panel" id="panel-A1"><header class="am-panel-head"><h2>Test</h2></header><div class="am-panel-body">Full context</div></section></html>')
        self.data = {"title": "<script>literal title</script>", "subtitle": "Summary", "period_label": "Yesterday",
                     "review_label": "Today", "coverage_label": "One session", "cards": [
                         {"id": "A1", "title": "Read me", "lane": "decide"}]}
        self.save()

    def save(self):
        (self.run / "board.json").write_text(json.dumps(self.data))

    def test_preserves_panel_body_and_exposes_recording_and_comment_slots(self):
        result = board.build(self.run).read_text()
        self.assertIn("Full context", result)
        self.assertIn('class="card-id">A1</span>', result)
        self.assertIn('aria-label="打开：A1 · Read me"', result)
        self.assertNotIn('<small>集中审阅</small>', result)
        self.assertIn("&lt;script&gt;literal title&lt;/script&gt;", result)
        self.assertIn('id="review-controls"', result)
        self.assertIn('id="notes-window"', result)
        self.assertIn('aria-label="收起意见与审核，保留草稿"', result)
        self.assertIn("保存意见 → 提交整理 → 集中审核 · 最后确认一次", result)
        self.assertNotIn('id="twohours-review-style"', result)
        self.assertIn("connect-src 'self'", result)
        self.assertIn("data:image/svg+xml;base64," + base64.b64encode((SCRIPT.parents[1] / "assets/brand/mark.svg").read_bytes()).decode(), result)
        self.assertIn("data:image/svg+xml;base64," + base64.b64encode((SCRIPT.parents[1] / "assets/desk-keyboard.svg").read_bytes()).decode(), result)
        self.assertIn('<aside class="desk-note"><h1>Yesterday</h1><span>Today</span><span>One session</span></aside>', result)
        self.assertIn('rel="icon" type="image/png" href="data:image/png;base64,', result)
        with self.assertRaisesRegex(ValueError, "already frozen"):
            board.build(self.run)

    def test_rejects_mismatched_ids_unknown_lanes_and_active_content(self):
        for update in ({"id": "A2"}, {"lane": "unmapped"}):
            previous = dict(self.data["cards"][0])
            self.data["cards"][0].update(update)
            self.save()
            with self.assertRaises(ValueError):
                board.build(self.run)
            self.data["cards"][0] = previous
        self.save()
        source = self.run / "report.am.html"
        source.write_text(source.read_text().replace("Full context", '<script>alert(1)</script>'))
        with self.assertRaises(AssertionError):
            board.build(self.run)
        self.assertFalse((self.run / "report.html").exists())

    def test_many_uneven_cards_and_empty_areas_keep_every_panel(self):
        cards = [{"id": f"A{i}", "title": f"Work {i}", "lane": "decide", "area": "work"} for i in range(1, 15)]
        for members in (cards, cards[:2], []):
            with self.subTest(count=len(members)):
                (self.run / "report.html").unlink(missing_ok=True)
                self.data["cards"] = members
                self.save()
                (self.run / "manifest.json").write_text(json.dumps({"panels": [{"id": c["id"]} for c in members]}))
                panels = ''.join(f'<section class="am-panel" id="panel-{c["id"]}"><p>Evidence {c["id"]}</p></section>' for c in members)
                (self.run / "report.am.html").write_text(f'<html>{panels}</html>')
                result = board.build(self.run).read_text()
                parsed = board.PanelExtractor(result)
                parsed.feed(result)
                self.assertEqual(set(parsed.panels), {f'panel-{c["id"]}' for c in members})
                self.assertEqual(result.count('class="review-card"'), len(members))
                self.assertIn('本轮没有阅读材料', result)
                for c in members:
                    self.assertIn(f'Evidence {c["id"]}', result)
                if not members:
                    self.assertIn('本轮没有工作事项', result)

    def test_reading_area_uses_sources_and_keeps_article_links_outside_button(self):
        self.data["cards"][0]["summary"] = '<b>Article content</b>'
        self.data["cards"][0].pop("lane")
        self.save()
        manifest = {"sources": [{"id": "C1", "provider": "cubox", "title": "Original article", "url": "https://cubox.pro/web/card/123"}],
                    "panels": [{"id": "A1", "source_ids": ["C1"]}]}
        (self.run / "manifest.json").write_text(json.dumps(manifest))
        result = board.build(self.run).read_text()
        self.assertIn('&lt;b&gt;Article content&lt;/b&gt;', result)
        self.assertIn('</button><div class="reading-links"', result)
        self.assertIn('href="https://cubox.pro/web/card/123"', result)
        self.assertIn('本轮没有工作事项', result)
        self.assertNotIn('本轮没有阅读材料', result)
        (self.run / "report.html").unlink()
        manifest["sources"][0]["url"] = 'javascript:alert(1)'
        (self.run / "manifest.json").write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, 'HTTPS'):
            board.build(self.run)
        self.assertFalse((self.run / "report.html").exists())
        self.data["cards"][0]["area"] = "unknown"
        self.save()
        with self.assertRaisesRegex(ValueError, 'area'):
            board.build(self.run)

    def test_mixed_sources_need_explicit_reading_area_without_changing_panel_ids(self):
        manifest = {"sources": [{"id": "C1", "provider": "cubox", "url": "https://cubox.pro/web/card/123"},
                                 {"id": "S1", "provider": "codex"}],
                    "panels": [{"id": "A1", "source_ids": ["C1", "S1"]}]}
        (self.run / "manifest.json").write_text(json.dumps(manifest))
        result = board.build(self.run).read_text()
        self.assertIn('本轮没有阅读材料', result)
        (self.run / "report.html").unlink()
        self.data["cards"][0]["area"] = "reading"
        self.save()
        result = board.build(self.run).read_text()
        self.assertIn('本轮没有工作事项', result)
        self.assertIn('data-panel="panel-A1"', result)
        self.assertIn('href="https://cubox.pro/web/card/123"', result)
        self.assertIn('Full context', result)

    @unittest.skipUnless(shutil.which("node"), "pagination behavior requires Node.js")
    def test_density_tries_more_columns_before_pagination_and_keeps_every_card(self):
        source = (SCRIPT.parents[1] / "assets/board.js").read_text()
        logic = source[source.index('function pageRows'):source.index('function fitBoard')]
        program = """
const assert = require('node:assert/strict');
function fixture(heights, width, available, area = 'work') {
  const controls = new Map(['[data-step="-1"]', '[data-step="1"]', '.page-status', '.shelf-pages'].map(key => [key, {}]));
  const classes = new Set();
  const shelf = {
    element: {hidden: false, dataset: {area}, querySelector: key => controls.get(key)},
    grid: {clientWidth: width, get clientHeight() {return available - (controls.get('.shelf-pages').hidden ? 0 : 54);},
      classList: {add(name) {classes.add(name);}, remove(name) {classes.delete(name);}, toggle(name, value) {value ? classes.add(name) : classes.delete(name);}},
      style: {}, dataset: {}},
    cards: heights.map((height, id) => ({id, hidden: false, getBoundingClientRect: () => ({height})})),
    page: 0, pages: [{start: 0, end: 0, rows: []}], controls, classes
  };
  return shelf;
}
""" + logic + """
const five = fixture([178, 224, 202, 180, 224], 820, 480);
fitShelf(five);
assert.equal(five.pages.length, 1, 'five work cards fit without unnecessary pagination');
assert.equal(five.pages[0].end, 5);
assert.equal(five.grid.style.gridTemplateColumns, 'repeat(3, minmax(0,1fr))');
assert.equal(five.controls.get('.shelf-pages').hidden, true);
assert.equal(five.cards.filter(card => !card.hidden).length, 5);
const many = fixture(Array(14).fill(200), 820, 650);
fitShelf(many);
assert.equal(many.pages.length, 3);
assert.equal(many.controls.get('.shelf-pages').hidden, false);
const visited = [];
for (let page = 0; page < many.pages.length; page++) {
  many.page = page;
  paintPage(many);
  visited.push(...many.cards.filter(card => !card.hidden).map(card => card.id));
}
assert.deepEqual(visited, Array.from({length: 14}, (_, id) => id));
assert.equal(many.controls.get('[data-step="1"]').disabled, true);
const first = many.pages[many.page].start;
many.grid.clientWidth = 360;
fitShelf(many);
assert(many.pages[many.page].start <= first && many.pages[many.page].end > first);
const reading = fixture([82, 190, 84, 108], 400, 300, 'reading');
fitShelf(reading);
assert.deepEqual(reading.pages.map(page => [page.start, page.end]), [[0, 1], [1, 2], [2, 4]], 'article rows use their own measured heights');
assert.equal(reading.grid.style.gridTemplateColumns, 'repeat(1, minmax(0,1fr))');
assert.equal(reading.grid.style.gridTemplateRows, '82px', 'short reading entries do not stretch into large cards');
const long = fixture([370, 190], 360, 410);
fitShelf(long);
assert(long.classes.has('has-tall-row'), 'an exceptionally tall item remains scrollable instead of clipped');
const empty = fixture([], 360, 410);
fitShelf(empty);
assert.equal(empty.controls.get('.page-status').textContent, '0 份');
assert.equal(empty.controls.get('.shelf-pages').hidden, true);
"""
        subprocess.run([shutil.which("node"), '-e', program], check=True, capture_output=True, text=True, timeout=10)

    @unittest.skipUnless(shutil.which("node"), "review gallery requires Node.js")
    def test_review_gallery_keeps_only_bound_evidence_in_source_order(self):
        source = (SCRIPT.parents[1] / "assets/bridge.js").read_text()
        logic = source[source.index('function reviewImages'):source.index('function renderReviewTask')]
        program = "const assert = require('node:assert/strict');\n" + logic + """
const first = Object.freeze({url:'/evidence/first.jpg',caption:'用户圈画'});
const second = Object.freeze({url:'/evidence/second.jpg',caption:'下一步现场'});
assert.deepEqual(reviewImages([]), []);
assert.deepEqual(reviewImages([{id:'written'}]), []);
const images = reviewImages([
  {evidence_images:[first, null, {url:'https://example.com/image.jpg'}, {url:'javascript:alert(1)'}]},
  {evidence_images:[{url:'//example.com/image.jpg'}, {url:'data:image/png;base64,AAAA'}, {}, second]}
]);
assert.deepEqual(images, [first, second], 'only server-bound images may enter the gallery or zoom links');
assert.equal(images[0], first, 'captions and source metadata are preserved');
"""
        subprocess.run([shutil.which("node"), '-e', program], check=True, capture_output=True, text=True, timeout=10)

    @unittest.skipUnless(shutil.which("node"), "review field sizing requires Node.js")
    def test_review_fields_grow_shrink_and_refit_after_hidden_layout(self):
        source = (SCRIPT.parents[1] / "assets/bridge.js").read_text()
        logic = source[source.index('function fitReviewFields'):source.index('new ResizeObserver(fitReviewFields)')]
        program = """
const assert = require('node:assert/strict');
const fields = [0, 2, 4].map(border => ({
  style: {height: '80px'}, contentHeight: 160,
  get offsetHeight() {return parseFloat(this.style.height) || 50;},
  get clientHeight() {return this.offsetHeight - border;},
  get scrollHeight() {return Math.max(this.contentHeight, this.clientHeight);}
}));
const reviewUI = {page: {clientWidth: 0, querySelectorAll: () => fields}};
""" + logic + """
fitReviewFields();
assert(fields.every(field => field.style.height === '80px'), 'a closed dialog must not collapse fields');
reviewUI.page.clientWidth = 640;
fitReviewFields();
assert(fields.every(field => field.clientHeight >= field.scrollHeight), 'long content must fit including borders');
const expanded = fields.map(field => field.offsetHeight);
fields.forEach(field => {field.contentHeight = 40;});
fitReviewFields();
assert(fields.every((field, i) => field.offsetHeight < expanded[i]), 'deleting content must shrink the field');
reviewUI.page.clientWidth = 300;
fields.forEach(field => {field.contentHeight = 240;});
fitReviewFields();
assert(fields.every(field => field.clientHeight >= field.scrollHeight), 'wrapped content must fit after resizing');
"""
        subprocess.run([shutil.which("node"), '-e', program], check=True, capture_output=True, text=True, timeout=10)

    @unittest.skipUnless(shutil.which("node"), "bundled AM renderer requires Node.js")
    def test_real_renderer_keeps_diagrams_data_and_sources_in_all_eight_panels(self):
        root = SCRIPT.parents[1]
        for name in ("manifest.json", "board.json", "report.md"):
            shutil.copyfile(root / "examples/review" / name, self.run / name)
        subprocess.run([
            shutil.which("node"), str(root / "vendor/answer-me-with-html/scripts/am.mjs"),
            "render", str(self.run / "report.md"), "-o", str(self.run / "report.am.html"), "--no-open",
        ], check=True, capture_output=True, text=True, timeout=60,
            env=dict(os.environ, AM_NO_UPDATE_CHECK="1"))
        rendered = (self.run / "report.am.html").read_text()
        original = board.PanelExtractor(rendered)
        original.feed(rendered)
        self.assertEqual(len(original.panels), 8)
        self.assertIn('<svg', original.panels['panel-A1'])
        self.assertIn('am-ch-data', original.panels['panel-A1'])
        self.assertIn('<svg', original.panels['panel-A6'])
        self.assertIn('DEMO', original.panels['panel-A6'])
        result = board.build(self.run).read_text()
        extracted = board.PanelExtractor(result)
        extracted.feed(result)
        self.assertEqual(extracted.panels, original.panels)
        self.assertEqual(result.count('class="review-card"'), 8)


if __name__ == "__main__":
    unittest.main()
