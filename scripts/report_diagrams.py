"""Validate evidence-bound report diagrams and emit static AM flow components."""
import argparse
import hashlib
import importlib.util
import json
import re
from pathlib import Path

MARKER = re.compile(r'^\{\{diagram:(D[1-9][0-9]*)\}\}$', re.M)
BASES = {'observed': '来源记录', 'inferred': '解释推断', 'proposed': '建议路线'}
STATES = {'observed': '已观察', 'blocked': '阻塞', 'failed': '失败', 'unknown': '未知', 'proposed': '待验证'}


def require(condition, message):
    if not condition:
        raise ValueError('diagram: ' + message)


def plain(value):
    require(isinstance(value, str) and value.strip() and not re.search(r'[\r\n<>`\[\]{}*|:]|->', value), 'labels must be single-line plain text without AM syntax')
    return value


def md(value):
    return re.sub(r'([\\`*_{}\[\]()#+.!|>~-])', r'\\\1', str(value)).replace('<', '&lt;').replace('\r', ' ').replace('\n', ' ')


def indexed(items):
    require(isinstance(items, list), 'expected an array')
    result = {}
    for item in items:
        require(isinstance(item, dict) and isinstance(item.get('id'), str) and re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]*', item['id']), 'invalid item ID')
        require(item['id'] not in result, 'duplicate item ID')
        result[item['id']] = item
    return result


def evidence_for(root, panel, sources, items):
    evidence = indexed(items)
    for item in evidence.values():
        source_id = item.get('source_id')
        require(source_id in panel['source_ids'] and source_id in sources, 'evidence must belong to this panel')
        source = sources[source_id]
        upstream = source.get('upstream', {})
        allowed = [source.get('snapshot_file'), upstream.get('view_file'), upstream.get('dossier_file')]
        filename = item.get('file')
        require(isinstance(filename, str) and filename in allowed, 'snapshot file is not declared by its source')
        relative = Path(filename)
        require(relative.parts and not relative.is_absolute() and '..' not in relative.parts and relative.parts[0] == 'sources', 'snapshot must be inside RUN/sources')
        file = root / relative
        require(not any(part.is_symlink() for part in [file, *file.parents] if part != root.parent), 'symlink snapshot is not allowed')
        require(file.is_file() and file.stat().st_size <= 32 * 1024 * 1024, 'snapshot missing or too large')
        data = file.read_bytes()
        require(hashlib.sha256(data).hexdigest() == item.get('sha256'), 'snapshot hash changed')
        value = json.loads(data)
        pointer = item.get('pointer')
        require(isinstance(pointer, str) and pointer.startswith('/'), 'expected a JSON pointer to source text')
        try:
            for key in pointer[1:].split('/'):
                key = key.replace('~1', '/').replace('~0', '~')
                if isinstance(value, list):
                    require(re.fullmatch(r'0|[1-9][0-9]*', key), 'invalid array index in JSON pointer')
                    value = value[int(key)]
                else:
                    value = value[key]
        except (KeyError, IndexError, TypeError, ValueError):
            raise ValueError('diagram: source pointer does not resolve') from None
        require(isinstance(value, str) and isinstance(item.get('quote'), str) and item['quote'].strip() and item['quote'] in value, 'quote is not present at the source pointer')
    return evidence


def compile_diagram(root, manifest, diagram):
    panels = {item['id']: item for item in manifest['panels']}
    require(diagram.get('panel_id') in panels, 'unknown panel')
    panel = panels[diagram['panel_id']]
    evidence = evidence_for(root, panel, {item['id']: item for item in manifest['sources']}, diagram.get('evidence'))

    def refs(item):
        ids = item.get('evidence_ids')
        require(isinstance(ids, list) and ids and all(key in evidence for key in ids), 'a claim has unknown or missing evidence')
        return ' '.join(f'[{evidence[key]["source_id"]}] {key}' for key in ids)

    nodes = indexed(diagram.get('nodes'))
    edges = indexed(diagram.get('transitions'))
    require(nodes and edges and diagram.get('current_node') in nodes, 'nodes, transitions and current stop are required')
    labels = {}
    for key, node in nodes.items():
        require(node.get('state') in STATES and node.get('basis') in BASES, 'unknown node state or basis')
        allowed = {'observed': set(STATES) - {'proposed'}, 'inferred': {'unknown'}, 'proposed': {'proposed'}}
        require(node['state'] in allowed[node['basis']], 'node state contradicts its evidence basis')
        refs(node)
        qualifier = {'observed': '', 'inferred': '，推断', 'proposed': '，建议'}[node['basis']]
        labels[key] = ('当前 · ' if key == diagram['current_node'] else '') + plain(node.get('label')) + '（' + STATES[node['state']] + qualifier + '）'
    require(len(set(labels.values())) == len(labels), 'node labels must be distinct')
    require(nodes[diagram['current_node']]['basis'] == 'observed', 'current stop needs an observed source')
    lines = [f'### {md(plain(diagram.get("question")))}', '', f'范围与截止点：{md(plain(diagram.get("scope")))}。', '', '```flow TB']
    for key in nodes:
        lines.append(('*' if key == diagram['current_node'] else '') + '[' + labels[key] + ']')
    for key, edge in edges.items():
        require(edge.get('from') in nodes and edge.get('to') in nodes and edge.get('basis') in BASES, 'invalid transition')
        refs(edge)
        require(nodes[edge['to']]['basis'] != 'proposed' or edge['basis'] != 'observed', 'an observed transition cannot prove a proposed outcome')
        arrow = '->' if edge['basis'] == 'observed' else '-->'
        lines.append(f'[{labels[edge["from"]]}] {arrow} [{labels[edge["to"]]}]: {key} · {plain(edge.get("condition"))}')
    lines += ['```', '', '实线表示来源记录，虚线表示推断或建议。高亮仅标当前停点。', '', '| 状态 | 记录性质 | 依据 |', '|---|---|---|']
    for node in nodes.values():
        lines.append(f'| {md(node["label"])}（{STATES[node["state"]]}） | {BASES[node["basis"]]} | {md(refs(node))} |')
    lines += ['', '| 迁移 | 条件及记录性质 | 依据 |', '|---|---|---|']
    for key, edge in edges.items():
        lines.append(f'| {key} | {md(edge["condition"])}（{BASES[edge["basis"]]}） | {md(refs(edge))} |')
    paths = diagram.get('paths')
    require(isinstance(paths, list) and paths, 'at least one evidence replay path is required')
    lines += ['', '#### 路径回放', '']
    for number, replay in enumerate(paths, 1):
        steps = replay.get('steps')
        require(isinstance(steps, list) and steps and all(key in edges for key in steps), 'invalid replay path')
        require(all(edges[left]['to'] == edges[right]['from'] for left, right in zip(steps, steps[1:])), 'replay path is disconnected')
        description = ' → '.join(labels[edges[key]['from']] + '（' + edges[key]['condition'] + '）' for key in steps) + ' → ' + labels[edges[steps[-1]]['to']]
        visited = {edges[key]['from'] for key in steps} | {edges[key]['to'] for key in steps}
        observed = all(edges[key]['basis'] == 'observed' for key in steps) and all(nodes[key]['basis'] == 'observed' for key in visited)
        kind = '来源回放' if observed else '含推断或建议，尚未验证'
        lines.append(f'{number}. {md(plain(replay.get("title")))}。{md(description)}。{kind}。')
        if replay.get('draw') is True:
            lines += ['', '```flow TB']
            for key in steps:
                edge = edges[key]
                arrow = '->' if edge['basis'] == 'observed' else '-->'
                lines.append(f'[{labels[edge["from"]]}] {arrow} [{labels[edge["to"]]}]: {key} · {edge["condition"]}')
            lines += ['```', '']
    for title, field in [('规则与反证', 'checks'), ('未知条件', 'unknowns')]:
        require(isinstance(diagram.get(field), list), field + ' must be explicit')
        lines += ['', f'#### {title}', '']
        if not diagram[field]:
            lines.append('本图未登记该类主张；以范围和限制说明为准。')
        for claim in diagram[field]:
            lines.append(f'- {md(plain(claim.get("text")))}。{md(refs(claim))}')
    lines += ['', '#### 文字证据', '']
    for key, item in evidence.items():
        lines.append(f'- {key} [{item["source_id"]}]，{md(item["file"])} {md(item["pointer"])}。原文摘录「{md(item["quote"])}」。')
    lines += ['', f'解释边界：{md(plain(diagram.get("limitations")))}。此图回放来源与解释，未执行 TLA+ 模型检查。', '']
    return '\n'.join(lines)


def expand_diagrams(root, manifest, markdown):
    root = Path(root).resolve()
    plan_file = root / 'diagram-plan.json'
    spec = importlib.util.spec_from_file_location('twohours_markdown_context', Path(__file__).with_name('markdown_context.py'))
    context = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(context)
    prose = {offset for offset, _, is_prose in context.prose_lines(markdown) if is_prose}
    markers = [marker for marker in MARKER.finditer(markdown) if marker.start() in prose]
    if not plan_file.exists():
        require(not markers, 'diagram marker has no plan')
        return markdown
    require(not plan_file.is_symlink(), 'symlink plan is not allowed')
    plan = json.loads(plan_file.read_text(encoding='utf-8'))
    require(plan.get('schema') == 'twohours-diagrams/v1', 'unsupported plan schema')
    diagrams = indexed(plan.get('diagrams'))
    require(len(markers) == len(diagrams) and {m[1] for m in markers} == set(diagrams), 'each diagram must appear once in the report')
    rendered = {}
    for marker in markers:
        diagram = diagrams[marker[1]]
        heading = re.findall(r'^## ([A-Z][0-9]{0,2})\s+', markdown[:marker.start()], re.M)
        require(heading and heading[-1] == diagram.get('panel_id'), 'diagram is placed in a different panel')
        rendered[marker[1]] = compile_diagram(root, manifest, diagram)
    result, end = [], 0
    for marker in markers:
        result.extend([markdown[end:marker.start()], rendered[marker[1]]])
        end = marker.end()
    return ''.join(result) + markdown[end:]


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    args = parser.parse_args()
    try:
        expand_diagrams(args.run, json.loads((args.run / 'manifest.json').read_text(encoding='utf-8')), (args.run / 'report.md').read_text(encoding='utf-8'))
        print(json.dumps({'diagram_plan': 'verified', 'formal_model_check': False}))
    except (ValueError, KeyError, TypeError, OSError) as error:
        parser.exit(1, str(error) + '\n')
