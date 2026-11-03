import contextlib
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[1]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


diagrams = load('diagrams', 'report_diagrams.py')
run = load('report_run', 'run.py')


class DiagramTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root / 'sources').mkdir()
        self.snapshot = self.root / 'sources/facts.json'
        self.snapshot.write_text(json.dumps({'fact': '本地检查通过，但缺少独立验收。', 'gap': '未核实异常出口。'}, ensure_ascii=False), encoding='utf-8')
        self.manifest = {'sources': [{'id': 'S1', 'snapshot_file': 'sources/facts.json'}],
                         'panels': [{'id': 'A1', 'source_ids': ['S1']}, {'id': 'A2', 'source_ids': ['S1']}]}
        evidence = {'id': 'E1', 'source_id': 'S1', 'file': 'sources/facts.json', 'pointer': '/fact',
                    'quote': '本地检查通过，但缺少独立验收。', 'sha256': hashlib.sha256(self.snapshot.read_bytes()).hexdigest()}
        gap = {**evidence, 'id': 'E2', 'pointer': '/gap', 'quote': '未核实异常出口。'}
        self.diagram = {'id': 'D1', 'panel_id': 'A1', 'question': '为什么还不能算完成', 'scope': '截至昨天的已读记录',
                        'current_node': 'blocked', 'limitations': '只证明本地检查，不代表正式验收',
                        'evidence': [evidence, gap],
                        'nodes': [{'id': 'tested', 'label': '本地检查通过', 'state': 'observed', 'basis': 'observed', 'evidence_ids': ['E1']},
                                  {'id': 'blocked', 'label': '等待独立验收', 'state': 'blocked', 'basis': 'observed', 'evidence_ids': ['E1']},
                                  {'id': 'done', 'label': '可验收交付', 'state': 'proposed', 'basis': 'proposed', 'evidence_ids': ['E1']}],
                        'transitions': [{'id': 'T1', 'from': 'tested', 'to': 'blocked', 'condition': '仍缺独立验收', 'basis': 'observed', 'evidence_ids': ['E1']},
                                        {'id': 'T2', 'from': 'blocked', 'to': 'done', 'condition': '未来补齐验收', 'basis': 'proposed', 'evidence_ids': ['E1']}],
                        'paths': [{'title': '已有停点', 'steps': ['T1'], 'draw': True}, {'title': '建议路线', 'steps': ['T1', 'T2']}],
                        'checks': [{'text': '本地通过不能单独证明完成', 'evidence_ids': ['E1']}],
                        'unknowns': [{'text': '异常出口尚未核实', 'evidence_ids': ['E2']}]}
        self.markdown = '---\ntitle: 工作线\ntemplate: doc\n---\n\n## A1 验收\n\n{{diagram:D1}}\n\n## A2 普通事实\n\n这张卡不需要图。\n'

    def save(self, diagram=None):
        (self.root / 'manifest.json').write_text(json.dumps(self.manifest), encoding='utf-8')
        (self.root / 'diagram-plan.json').write_text(json.dumps({'schema': 'twohours-diagrams/v1', 'diagrams': [diagram or self.diagram]}), encoding='utf-8')
        (self.root / 'report.md').write_text(self.markdown, encoding='utf-8')

    @unittest.skipUnless(shutil.which('node'), 'renderer requires Node.js')
    def test_render_keeps_current_stop_conditions_replay_unknowns_and_plain_panel(self):
        self.save()
        (self.root / 'board.json').write_text(json.dumps({'period_label': '昨日', 'review_label': '今天', 'coverage_label': '夹具',
            'cards': [{'id': key, 'title': key, 'lane': 'follow'} for key in ['A1', 'A2']]}), encoding='utf-8')
        with contextlib.redirect_stdout(io.StringIO()):
            run.render(SimpleNamespace(run=str(self.root)))
        html = (self.root / 'report.html').read_text(encoding='utf-8')
        for content in ['<svg', '当前 · 等待独立验收', '仍缺独立验收', '路径回放', '规则与反证', '未核实异常出口', '文字证据', '尚未验证', '未执行 TLA+']:
            self.assertIn(content, html)
        self.assertIn('这张卡不需要图', html)
        self.assertNotIn('{{diagram:D1}}', html.split('id="panel-store"')[1])
        self.assertEqual((self.root / 'report.md').read_text(encoding='utf-8'), self.markdown)

    def test_false_evidence_or_success_and_broken_replay_are_rejected(self):
        changes = [
            lambda d: d['evidence'][0].update(quote='这段内容不存在'),
            lambda d: d['evidence'][0].update(source_id='S2'),
            lambda d: d['evidence'][0].update(file='../outside.json'),
            lambda d: d['nodes'][0].update(evidence_ids=[]),
            lambda d: d.update(current_node='done'),
            lambda d: d['transitions'][1].update(basis='observed'),
            lambda d: d['paths'][0].update(steps=['T2', 'T1']),
            lambda d: d['nodes'][0].update(label='嵌入 -> 另一个节点'),
        ]
        for alter in changes:
            with self.subTest(alter=alter):
                diagram = copy.deepcopy(self.diagram)
                alter(diagram)
                self.save(diagram)
                with self.assertRaises(ValueError):
                    diagrams.expand_diagrams(self.root, self.manifest, self.markdown)
        self.save()
        self.snapshot.write_text('{}', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'hash changed'):
            diagrams.expand_diagrams(self.root, self.manifest, self.markdown)

    def test_placement_and_plan_are_optional_but_not_ambiguous(self):
        self.assertEqual(diagrams.expand_diagrams(self.root, self.manifest, '普通文字'), '普通文字')
        with self.assertRaisesRegex(ValueError, 'no plan'):
            diagrams.expand_diagrams(self.root, self.manifest, self.markdown)
        self.save()
        for markdown in (self.markdown.replace('D1', 'D2'), self.markdown + '\n{{diagram:D1}}\n',
                         self.markdown.replace('{{diagram:D1}}', '').replace('## A2', '{{diagram:D1}}\n\n## A2') + '\n{{diagram:D1}}\n'):
            with self.assertRaises(ValueError):
                diagrams.expand_diagrams(self.root, self.manifest, markdown)

    def test_literal_markers_in_code_are_preserved(self):
        for code in ('```text\n{{diagram:D1}}\n```\n', '- ```text\n  {{diagram:D1}}\n  ```\n',
                     '`start\n{{diagram:D1}}\nend`\n'):
            self.assertEqual(diagrams.expand_diagrams(self.root, self.manifest, code), code)
        self.save()
        source = self.markdown + '\n```text\n{{diagram:D1}}\n```\n'
        expanded = diagrams.expand_diagrams(self.root, self.manifest, source)
        self.assertTrue(expanded.endswith('\n```text\n{{diagram:D1}}\n```\n'))

    def test_replay_cannot_promote_an_inferred_node_to_observed(self):
        self.diagram['nodes'][0].update(basis='inferred', state='unknown')
        self.save()
        expanded = diagrams.expand_diagrams(self.root, self.manifest, self.markdown)
        self.assertIn('未知，推断', expanded)
        replay = expanded.split('#### 路径回放')[1].split('#### 规则与反证')[0]
        self.assertNotIn('。来源回放。', replay)
        self.diagram['nodes'][0].update(basis='proposed', state='observed')
        self.save()
        with self.assertRaisesRegex(ValueError, 'contradicts'):
            diagrams.expand_diagrams(self.root, self.manifest, self.markdown)

    @unittest.skipUnless(shutil.which('node'), 'renderer requires Node.js')
    def test_shipped_example_renders_through_the_product_entry(self):
        shutil.copytree(ROOT / 'examples/statechart', self.root, dirs_exist_ok=True)
        with contextlib.redirect_stdout(io.StringIO()):
            run.render(SimpleNamespace(run=str(self.root)))
        output = (self.root / 'report.html').read_text(encoding='utf-8')
        self.assertIn('虚构演练数据', output)
        self.assertIn('当前 · 等待独立验收', output)

    def test_declared_outside_or_symlink_snapshot_is_rejected(self):
        self.save()
        self.manifest['sources'][0]['snapshot_file'] = '../outside.json'
        self.diagram['evidence'][0]['file'] = '../outside.json'
        self.save()
        with self.assertRaisesRegex(ValueError, 'RUN/sources'):
            diagrams.expand_diagrams(self.root, self.manifest, self.markdown)
        self.manifest['sources'][0]['snapshot_file'] = 'sources/facts.json'
        self.diagram['evidence'][0]['file'] = 'sources/facts.json'
        saved = self.snapshot.read_bytes()
        self.snapshot.unlink()
        (self.root / 'facts.json').write_bytes(saved)
        try:
            self.snapshot.symlink_to(self.root / 'facts.json')
        except OSError:
            self.skipTest('symlinks are unavailable on this host')
        self.save()
        with self.assertRaisesRegex(ValueError, 'symlink'):
            diagrams.expand_diagrams(self.root, self.manifest, self.markdown)
