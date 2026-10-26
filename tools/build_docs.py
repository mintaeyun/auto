"""개발문서(md)를 서로 링크된 Standalone HTML로 변환하는 스크립트.

기능명세:
- 원본 md(README.md, 기획/*.md, docs/*.md)는 읽기만 한다. 결과는 html/ 폴더에 매번 전체 덮어쓴다.
- md의 ID(FN/SC/API/TB, TB-nn.컬럼)와 소스 코드 심볼(함수/클래스/메서드), `파일:줄` 표기를 정의 위치로 자동 링크하고 백링크를 만든다.
- 코드 색인(py는 ast, web/index.html의 JS는 정규식)과 추적표(기능ID 표 기반)를 별도 페이지로 만든다.
- Mermaid 코드블록은 vendor/mermaid.min.js가 있으면 페이지에 인라인해 오프라인으로 그리고, 노드 텍스트가 정의와 일치하면 클릭 이동을 붙인다. 없으면 원문 코드를 보여 준다.
- 실행: `py tools/build_docs.py` 또는 루트의 build_docs.cmd. 외부 패키지와 네트워크를 쓰지 않는다.
- 한계: md 변환기는 이 프로젝트 문서에 쓰인 문법(제목, 표, 목록, 코드블록, 굵게, 링크, 코드스팬)만 지원한다.
  심볼 이름이 여러 곳에 정의되어 있으면(모호) 링크하지 않고 미해결로 보고한다. 중첩 함수는 색인하지 않는다.
"""
from __future__ import annotations

import ast
import html
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'html'
VENDOR_MERMAID = ROOT / 'vendor' / 'mermaid.min.js'
DOC_SOURCES = [ROOT / 'README.md', *sorted((ROOT / 'docs').glob('*.md')), *sorted((ROOT / '기획').glob('*.md'))]
CODE_SOURCES = ['compare_core.py', 'locate.py', 'web_app.py', 'app.py', 'browser_control.py', 'web/index.html']
INDEX_NAME, TRACE_NAME, CODE_NAME = 'index.html', '추적표.html', '코드색인.html'

ID_RE = re.compile(r'\b(?:FN|SC|API|TB)-\d+(?:\.[A-Za-z_]\w*)?')
FILELINE_RE = re.compile(r'(?<![\w/.-])([\w./-]+\.(?:py|html|js)):(\d+)\b')
SYMBOL_RE = re.compile(r'(?<![\w.])([A-Za-z_][\w.]*)\(\)')
FILE_RE = re.compile(r'(?<![\w/.-])([\w./-]+\.(?:py|html))(?![\w:])')
ID_HEAD_RE = re.compile(r'^((?:FN|SC|API|TB)-\d+)\b')


def slug(text):
    """앵커용 문자열을 만든다. 입력: 임의 텍스트. 반환: 영숫자·한글·_ 만 남긴 문자열(빈 값이면 'x')."""
    return re.sub(r'[^0-9A-Za-z가-힣_]+', '_', text).strip('_') or 'x'


def page_name(path):
    """원본 md 경로에 대응하는 html 파일명을 반환한다. 예: 기획/비교값-위치표시.md → 기획_비교값-위치표시.html"""
    rel = path.relative_to(ROOT).with_suffix('')
    return '_'.join(rel.parts) + '.html'


# ---------------------------------------------------------------- 1. md 블록 파서

def parse_md(text):
    """md 텍스트를 블록 목록으로 나눈다.

    입력: text: md 전체. 반환: 튜플 목록. ('h', 레벨, 텍스트) / ('code', 언어, 본문) /
    ('table', 헤더[], 행[][]) / ('list', ordered, 항목[]) / ('p', 텍스트).
    처리: 줄 단위로 코드펜스, 제목, 표(| 로 시작하는 연속 줄), 목록, 문단을 식별한다. 부작용 없음.
    """
    lines, blocks, i = text.splitlines(), [], 0
    while i < len(lines):
        line = lines[i]
        if line.startswith('```'):
            lang, body, i = line[3:].strip(), [], i + 1
            while i < len(lines) and not lines[i].startswith('```'):
                body.append(lines[i]); i += 1
            blocks.append(('code', lang, '\n'.join(body))); i += 1
        elif re.match(r'^#{1,6}\s', line):
            level = len(line) - len(line.lstrip('#'))
            blocks.append(('h', level, line[level:].strip())); i += 1
        elif line.lstrip().startswith('|'):
            rows = []
            while i < len(lines) and lines[i].lstrip().startswith('|'):
                rows.append([c.strip() for c in lines[i].strip().strip('|').split('|')]); i += 1
            body = [r for r in rows[1:] if not all(re.fullmatch(r':?-{2,}:?', c) for c in r)]
            blocks.append(('table', rows[0], body))
        elif re.match(r'^\s*([-*]|\d+\.)\s', line):
            ordered, items = bool(re.match(r'^\s*\d+\.', line)), []
            while i < len(lines) and re.match(r'^\s*([-*]|\d+\.)\s', lines[i]):
                items.append(re.sub(r'^\s*([-*]|\d+\.)\s+', '', lines[i])); i += 1
            blocks.append(('list', ordered, items))
        elif line.strip():
            para = []
            while i < len(lines) and lines[i].strip() and not re.match(r'^(#{1,6}\s|```|\s*\||\s*([-*]|\d+\.)\s)', lines[i]):
                para.append(lines[i].strip().lstrip('> ').strip()); i += 1
            blocks.append(('p', ' '.join(para)))
        else:
            i += 1
    return blocks


# ---------------------------------------------------------------- 2. 코드 색인

class CodeIndex:
    """소스 파일의 클래스·함수·메서드를 색인한다.

    속성: files: {상대경로: [심볼 dict]}. 심볼 dict = name(단순 이름), qual(Class.method), kind, line, end, doc(첫 줄).
    조회: find(이름)로 단순 이름 또는 qualname 일치 목록을, locate(파일, 줄)로 그 줄을 포함하는 심볼을 찾는다.
    """

    def __init__(self):
        self.files = {}
        for rel in CODE_SOURCES:
            path = ROOT / rel
            if path.exists():
                self.files[rel] = self._py(path) if rel.endswith('.py') else self._js(path)

    @staticmethod
    def _py(path):
        """py 파일을 ast로 읽어 최상위 함수·클래스와 클래스 메서드만 반환한다(중첩 함수 제외). 구문 오류 파일은 빈 목록."""
        try:
            tree = ast.parse(path.read_text(encoding='utf-8'))
        except (SyntaxError, UnicodeDecodeError):
            return []
        out = []

        def add(node, prefix=''):
            kind = 'class' if isinstance(node, ast.ClassDef) else 'function'
            doc = (ast.get_docstring(node) or '').strip().splitlines()
            out.append({'name': node.name, 'qual': prefix + node.name, 'kind': kind, 'line': node.lineno,
                        'end': node.end_lineno, 'doc': doc[0] if doc else ''})

        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                add(node)
                if isinstance(node, ast.ClassDef):
                    for sub in node.body:
                        if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            add(sub, node.name + '.')
        return out

    @staticmethod
    def _js(path):
        """html 안의 'function 이름(' / 'async function 이름(' 선언을 줄 단위 정규식으로 찾는다. 끝 줄은 알 수 없어 시작 줄만 기록한다."""
        out = []
        for n, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
            for m in re.finditer(r'(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(', line):
                out.append({'name': m.group(1), 'qual': m.group(1), 'kind': 'function', 'line': n, 'end': n, 'doc': ''})
        return out

    def find(self, name):
        """이름(단순 이름 또는 Class.method)과 일치하는 (파일, 심볼) 목록을 반환한다."""
        return [(rel, s) for rel, syms in self.files.items() for s in syms if s['qual'] == name or s['name'] == name and '.' not in name]

    def locate(self, rel, line):
        """파일의 line 줄을 포함하는 가장 안쪽 심볼을 반환한다. 없으면 None. JS는 줄 이하에서 가장 가까운 시작 줄."""
        syms = self.files.get(rel, [])
        inside = [s for s in syms if s['line'] <= line <= s['end']]
        if inside:
            return max(inside, key=lambda s: s['line'])
        before = [s for s in syms if s['line'] <= line and rel.endswith('.html')]
        return max(before, key=lambda s: s['line']) if before else None

    def resolve_file(self, text):
        """'compare_core.py' 또는 'web/index.html' 같은 표기를 색인된 상대경로로 바꾼다. 없거나 모호하면 None."""
        text = text.replace('\\', '/')
        hits = [rel for rel in self.files if rel == text or rel.endswith('/' + text)]
        return hits[0] if len(hits) == 1 else None


def sym_anchor(rel, sym):
    """코드 색인 페이지에서 심볼의 앵커 id."""
    return f'{slug(rel)}__{slug(sym["qual"])}'


# ---------------------------------------------------------------- 3. 링크 해결기

class Linker:
    """토큰(ID, 심볼, 파일:줄, 파일)을 링크 대상으로 해결하고 참조(백링크)와 미해결을 기록한다.

    속성: defs: {ID 또는 TB-nn.컬럼: (페이지, 앵커)}, code: CodeIndex, refs: {대상키: {(페이지, 앵커, 제목)}},
    unresolved: [(페이지, 토큰, 사유)]. 대상키는 ID 문자열 또는 'code:<파일>::<qual>'.
    """

    def __init__(self, defs, code):
        self.defs, self.code = defs, code
        self.refs, self.unresolved = defaultdict(set), []

    def _note(self, key, ctx):
        self.refs[key].add(ctx)

    def link_id(self, token, ctx):
        """ID 토큰을 <a>로 바꾼다. 컬럼이 정의에 없으면 미해결로 기록하고 원문을 그대로 둔다."""
        if token in self.defs:
            page, anchor = self.defs[token]
            self._note(token, ctx)
            return f'<a class="ref" href="{page}#{anchor}">{token}</a>'
        self.unresolved.append((ctx[0], token, '정의를 찾을 수 없음'))
        return f'<span class="broken" title="미해결">{token}</span>'

    def link_symbol(self, name, ctx):
        """함수/클래스 이름을 코드 색인 링크로 바꾼다. 정의가 0개이면 미해결, 2개 이상이면 모호로 보고하고 링크하지 않는다."""
        hits = self.code.find(name)
        if len(hits) == 1:
            rel, sym = hits[0]
            self._note(f'code:{rel}::{sym["qual"]}', ctx)
            return f'<a class="ref code" href="{CODE_NAME}#{sym_anchor(rel, sym)}">{name}()</a>'
        self.unresolved.append((ctx[0], name + '()', '코드에서 찾을 수 없음' if not hits else f'정의 {len(hits)}곳(모호)'))
        return f'<span class="broken" title="미해결">{name}()</span>'

    def link_fileline(self, rel_text, line, ctx):
        """파일:줄 표기를 그 줄을 포함하는 심볼 앵커(없으면 파일 앵커)로 연결한다."""
        rel = self.code.resolve_file(rel_text)
        if not rel:
            self.unresolved.append((ctx[0], f'{rel_text}:{line}', '색인되지 않은 파일'))
            return f'{rel_text}:{line}'
        sym = self.code.locate(rel, int(line))
        if sym:
            self._note(f'code:{rel}::{sym["qual"]}', ctx)
            return f'<a class="ref code" href="{CODE_NAME}#{sym_anchor(rel, sym)}">{rel_text}:{line}</a>'
        return f'<a class="ref code" href="{CODE_NAME}#{slug(rel)}">{rel_text}:{line}</a>'

    def link_file(self, text):
        """파일 경로(줄 없음)를 코드 색인의 파일 섹션으로 연결한다. 색인에 없으면 그대로 둔다."""
        rel = self.code.resolve_file(text)
        return f'<a class="ref code" href="{CODE_NAME}#{slug(rel)}">{text}</a>' if rel else text


# ---------------------------------------------------------------- 4. 인라인 변환

def inline(text, linker, ctx, in_code_ok=True):
    """한 줄 텍스트의 인라인 요소를 HTML로 바꾼다.

    입력: text(md 인라인), linker, ctx=(페이지, 앵커, 제목). 처리 순서: 코드스팬 분리 → 코드스팬은 심볼/파일:줄/파일/ID 링크,
    그 밖은 HTML 이스케이프 후 굵게·md 링크·ID·파일:줄 링크. 반환: HTML 문자열. 링크 시 linker에 참조를 기록하는 부작용이 있다.
    """
    parts, out = re.split(r'(`[^`]+`)', text), []
    for part in parts:
        if part.startswith('`') and part.endswith('`') and len(part) > 1:
            raw = html.escape(part[1:-1])
            raw = SYMBOL_RE.sub(lambda m: linker.link_symbol(m.group(1), ctx), raw)
            raw = _outside_tags(raw, lambda s: FILELINE_RE.sub(lambda m: linker.link_fileline(m.group(1), m.group(2), ctx), s))
            raw = _outside_tags(raw, lambda s: ID_RE.sub(lambda m: linker.link_id(m.group(0), ctx), s))
            raw = _outside_tags(raw, lambda s: FILE_RE.sub(lambda m: linker.link_file(m.group(1)), s) if '.' in s else s)
            out.append(f'<code>{raw}</code>')
        else:
            seg = html.escape(part)
            seg = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', seg)
            seg = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', lambda m: _md_link(m, linker), seg)
            seg = _outside_tags(seg, lambda s: FILELINE_RE.sub(lambda m: linker.link_fileline(m.group(1), m.group(2), ctx), s))
            seg = _outside_tags(seg, lambda s: ID_RE.sub(lambda m: linker.link_id(m.group(0), ctx), s))
            out.append(seg)
    return ''.join(out)


def _outside_tags(text, fn):
    """HTML 태그(<...>)와 <a>…</a> 안은 건드리지 않고 나머지 텍스트에만 fn을 적용한다."""
    pieces, depth, result = re.split(r'(<[^>]+>)', text), 0, []
    for piece in pieces:
        if piece.startswith('<'):
            if piece.startswith('<a '):
                depth += 1
            elif piece == '</a>':
                depth -= 1
            result.append(piece)
        else:
            result.append(piece if depth else fn(piece))
    return ''.join(result)


def _md_link(m, linker):
    """[텍스트](주소) 변환. .md 주소는 대응하는 html 파일명으로 바꾸고, 외부 주소는 그대로 둔다."""
    label, url = m.group(1), m.group(2)
    if url.endswith('.md'):
        url = page_name(ROOT / url) if (ROOT / url).exists() else url
    return f'<a href="{url}">{label}</a>'


# ---------------------------------------------------------------- 5. 정의 수집 · 페이지 렌더

def collect_defs(docs):
    """모든 문서에서 정의를 모은다. 반환: {키: (페이지, 앵커)}.

    규칙: (1) 제목이 ID로 시작하면 그 ID가 정의. (2) 표의 첫 칸이 ID이면 그 행이 정의(제목 정의가 먼저면 유지).
    (3) 헤더 첫 칸이 '컬럼'인 표는 직전의 TB-nn 제목 아래 컬럼 정의 'TB-nn.컬럼'.
    """
    defs = {}
    for path, blocks in docs:
        page, current_tb = page_name(path), None
        for block in blocks:
            if block[0] == 'h':
                m = ID_HEAD_RE.match(re.sub(r'[`*]', '', block[2]))
                if m:
                    defs.setdefault(m.group(1), (page, m.group(1)))
                    current_tb = m.group(1) if m.group(1).startswith('TB-') else None
            elif block[0] == 'table':
                header, rows = block[1], block[2]
                for row in rows:
                    first = re.sub(r'[`*]', '', row[0])
                    if ID_HEAD_RE.fullmatch(first):
                        defs.setdefault(first, (page, first))
                    elif header[0] == '컬럼' and current_tb:
                        defs.setdefault(f'{current_tb}.{first}', (page, slug(f'{current_tb}.{first}')))
    return defs


def render_blocks(path, blocks, linker):
    """한 문서의 블록을 본문 HTML로 렌더링한다.

    반환: (본문 HTML, mermaid 사용 여부, [(레벨, 앵커, 제목)] 목차). 정의 위치(제목/표 첫 칸)에는 백링크 자리표시자
    <!--BL:키-->를 넣고, 나중에 replace_backlinks가 채운다. 링크 기록은 linker에 쌓인다.
    """
    page, body, toc, has_mermaid = page_name(path), [], [], False
    anchor, title, current_tb, count = 'top', path.stem, None, 0
    for block in blocks:
        kind = block[0]
        if kind == 'h':
            count += 1
            plain = re.sub(r'[`*]', '', block[2])
            m = ID_HEAD_RE.match(plain)
            anchor = m.group(1) if m else f'h{count}'
            title = plain
            current_tb = m.group(1) if m and m.group(1).startswith('TB-') else None
            toc.append((block[1], anchor, plain))
            head = f'<h{block[1]} id="{anchor}">{inline(block[2], linker, (page, anchor, plain))}</h{block[1]}>'
            body.append(head + (f'<!--BL:{m.group(1)}-->' if m else ''))
        elif kind == 'code':
            if block[1] == 'mermaid':
                has_mermaid = True
                body.append(f'<pre class="mermaid">{html.escape(block[2])}</pre>')
            else:
                body.append(f'<pre><code>{html.escape(block[2])}</code></pre>')
        elif kind == 'table':
            body.append(render_table(page, block, linker, (page, anchor, title), current_tb))
        elif kind == 'list':
            tag = 'ol' if block[1] else 'ul'
            items = ''.join(f'<li>{inline(i, linker, (page, anchor, title))}</li>' for i in block[2])
            body.append(f'<{tag}>{items}</{tag}>')
        else:
            body.append(f'<p>{inline(block[1], linker, (page, anchor, title))}</p>')
    return '\n'.join(body), has_mermaid, toc


def render_table(page, block, linker, ctx, current_tb):
    """표를 HTML로 바꾼다. 첫 칸이 ID 또는 TB 컬럼 정의이면 그 칸을 앵커로 만들고 백링크 자리표시자를 넣는다."""
    header, rows = block[1], block[2]
    head = ''.join(f'<th>{inline(c, linker, ctx)}</th>' for c in header)
    trs = []
    for row in rows:
        first = re.sub(r'[`*]', '', row[0])
        key = first if ID_HEAD_RE.fullmatch(first) else (f'{current_tb}.{first}' if header[0] == '컬럼' and current_tb else None)
        cells = []
        for n, cell in enumerate(row):
            if n == 0 and key:
                aid = key if ID_HEAD_RE.fullmatch(key) else slug(key)
                cells.append(f'<td id="{aid}"><b>{html.escape(cell.strip("`*"))}</b><!--BL:{key}--></td>')
            else:
                cells.append(f'<td>{inline(cell, linker, ctx)}</td>')
        trs.append('<tr>' + ''.join(cells) + '</tr>')
    return f'<div class="tw"><table><thead><tr>{head}</tr></thead><tbody>{"".join(trs)}</tbody></table></div>'


def backlink_html(refs, key, current_page=None):
    """대상키를 참조하는 위치 목록을 <details> HTML로 만든다. 참조가 없으면 빈 문자열."""
    items = sorted(refs.get(key, ()))
    if not items:
        return ''
    lis = ''.join(f'<li><a href="{p}#{a}">{html.escape(t)}</a> <small>{html.escape(p)}</small></li>' for p, a, t in items)
    return f'<details class="bl"><summary>참조하는 곳 {len(items)}</summary><ul>{lis}</ul></details>'


CSS = """
:root{--bg:#fff;--fg:#1d2430;--mut:#667085;--line:#d9dee7;--acc:#1f6f78;--code:#f3f5f8;--bad:#b42318;--hl:#fff3bf}
@media(prefers-color-scheme:dark){:root{--bg:#14181f;--fg:#e4e8ef;--mut:#9aa5b5;--line:#2c3441;--acc:#6cc4cc;--code:#1e2530;--bad:#ff8a80;--hl:#4a4320}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.65 'Malgun Gothic','Noto Sans KR',sans-serif}
nav{position:sticky;top:0;z-index:5;display:flex;gap:16px;flex-wrap:wrap;padding:10px 20px;background:var(--bg);border-bottom:1px solid var(--line)}
nav a{font-weight:700}main{max-width:1100px;margin:0 auto;padding:24px 20px 80px}
a{color:var(--acc)}a.ref{text-decoration:none;border-bottom:1px dotted var(--acc)}code{background:var(--code);padding:1px 5px;border-radius:4px;font-size:.92em}
pre{background:var(--code);padding:14px;border-radius:8px;overflow:auto}pre code{background:none;padding:0}
.tw{overflow:auto}table{border-collapse:collapse;margin:12px 0;width:100%}th,td{border:1px solid var(--line);padding:6px 10px;text-align:left;vertical-align:top}th{background:var(--code)}
.broken{color:var(--bad);text-decoration:underline wavy}.bl{font-weight:400;font-size:.85em;color:var(--mut);margin:2px 0}.bl summary{cursor:pointer}
:target{background:var(--hl)}.mermaid{background:transparent;text-align:center}.warn{color:var(--bad)}small{color:var(--mut)}
.toc{columns:2;font-size:.9em}.empty{color:var(--bad);font-weight:700}
"""

NAV = f'<nav><a href="{INDEX_NAME}">문서 목록</a><a href="{TRACE_NAME}">추적표</a><a href="{CODE_NAME}">코드 색인</a></nav>'

MERMAID_JS = r"""
(function(){var map=%s;
function find(t){t=(t||'').replace(/\s+/g,' ').trim();if(!t)return null;var k=t.replace(/\(\)$/,'');return map[t]||map[k]||map[k+'()']||null}
function wire(){document.querySelectorAll('.mermaid svg').forEach(function(svg){
svg.querySelectorAll('text,span.nodeLabel,span.edgeLabel,p,div.label,.entityLabel,.messageText').forEach(function(el){
var h=find(el.textContent);if(!h)return;var tgt=el.closest('g.node,g.actor,g.entity,g.label,g')||el;
if(tgt.dataset.wired)return;tgt.dataset.wired='1';tgt.style.cursor='pointer';tgt.setAttribute('role','link');
tgt.addEventListener('click',function(){location.href=h});});});}
if(window.mermaid){mermaid.initialize({startOnLoad:false,securityLevel:'loose',theme:matchMedia('(prefers-color-scheme:dark)').matches?'dark':'default'});
mermaid.run({querySelector:'.mermaid'}).then(wire).catch(function(e){console.error(e)});}})();
"""


def page_html(title, body, linker=None, mermaid=False, nodes=None):
    """공통 틀(CSS, 내비게이션, 필요 시 Mermaid 인라인)로 한 페이지를 감싼다. 외부 요청이 없는 단일 HTML 문자열을 반환한다."""
    script = ''
    if mermaid:
        if VENDOR_MERMAID.exists():
            lib = VENDOR_MERMAID.read_text(encoding='utf-8').replace('</script', '<\\/script')
            script = f'<script>{lib}</script><script>{MERMAID_JS % json.dumps(nodes or {}, ensure_ascii=False)}</script>'
        else:
            script = '<p class="warn">vendor/mermaid.min.js가 없어 다이어그램은 원문 코드로 표시됩니다.</p>'
    return (f'<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{html.escape(title)}</title><style>{CSS}</style></head><body>{NAV}<main>{body}</main>{script}</body></html>')


# ---------------------------------------------------------------- 6. 특수 페이지

def trace_page(docs, linker):
    """'기능ID'로 시작하는 표를 모아 추적표 페이지 본문을 만든다.

    각 셀은 inline()으로 링크한다(백링크 기록 포함). 코드 셀은 파일:줄을 덧붙인다. 화면/API/코드/데이터 칸이 비면 경고 표시,
    '-'는 의도적 없음으로 보고 경고하지 않는다. 반환: 본문 HTML.
    """
    rows_html, page = [], TRACE_NAME
    head = None
    for path, blocks in docs:
        for block in blocks:
            if block[0] != 'table' or block[1][0] != '기능ID':
                continue
            head = head or block[1]
            for row in block[2]:
                ctx = (page, row[0], row[0])
                cells = []
                for n, cell in enumerate(row):
                    text = cell.strip()
                    if n >= 2 and not text:
                        cells.append('<td class="empty">연결 없음</td>')
                    elif n == 4:
                        parts = []
                        for name in re.findall(r'`([^`]+)`', cell):
                            m = SYMBOL_RE.fullmatch(name)
                            hit = linker.code.find(m.group(1)) if m else []
                            where = f' <small>{hit[0][0]}:{hit[0][1]["line"]}</small>' if len(hit) == 1 else ''
                            parts.append(inline(f'`{name}`', linker, ctx) + where)
                        cells.append('<td>' + '<br>'.join(parts) + '</td>')
                    else:
                        cells.append(f'<td>{inline(cell, linker, ctx)}</td>')
                rows_html.append(f'<tr id="{html.escape(row[0])}">' + ''.join(cells) + '</tr>')  # 백링크 앵커
    if not head:
        return '<h1>추적표</h1><p class="warn">기능ID 표를 찾지 못했습니다(docs/기능정의서.md의 \'기능ID\' 표).</p>'
    th = ''.join(f'<th>{html.escape(c)}</th>' for c in head)
    return (f'<h1>추적표</h1><p>기능ID → 화면 → API → 코드(파일:줄) → 데이터. 원천은 기능정의서의 기능ID 표입니다.</p>'
            f'<div class="tw"><table><thead><tr>{th}</tr></thead><tbody>{"".join(rows_html)}</tbody></table></div>')


def code_page(linker, refs):
    """코드 색인 페이지 본문. 파일별로 클래스/함수/줄/첫 줄 설명과 이를 참조하는 문서 백링크를 나열한다."""
    out = ['<h1>코드 색인</h1><p>py는 ast, web/index.html은 function 선언 정규식으로 만든 색인입니다.</p>']
    for rel, syms in linker.code.files.items():
        out.append(f'<h2 id="{slug(rel)}">{html.escape(rel)}</h2>')
        rows = []
        for s in syms:
            aid = sym_anchor(rel, s)
            bl = backlink_html(refs, f'code:{rel}::{s["qual"]}')
            rows.append(f'<tr id="{aid}"><td><code>{html.escape(s["qual"])}</code> <small>{s["kind"]}</small></td><td>{s["line"]}</td>'
                        f'<td>{html.escape(s["doc"])}</td><td>{bl}</td></tr>')
        out.append('<div class="tw"><table><thead><tr><th>이름</th><th>줄</th><th>설명</th><th>백링크</th></tr></thead><tbody>'
                   + ''.join(rows) + '</tbody></table></div>')
    return '\n'.join(out)


def node_map(linker):
    """Mermaid 노드 텍스트 → 이동 주소 사전. ID, 'TB-nn', 심볼 이름(괄호 유무 모두)을 정의 위치로 매핑한다. 모호한 심볼은 제외."""
    nodes = {k: f'{p}#{a}' for k, (p, a) in linker.defs.items()}
    for rel, syms in linker.code.files.items():
        for s in syms:
            if len(linker.code.find(s['qual'])) == 1:
                nodes[s['qual']] = f'{CODE_NAME}#{sym_anchor(rel, s)}'
                nodes[s['qual'] + '()'] = nodes[s['qual']]
        nodes.setdefault(rel, f'{CODE_NAME}#{slug(rel)}')
    return nodes


# ---------------------------------------------------------------- 7. 실행

def main():
    """전체 변환을 실행한다.

    절차: md 읽기·파싱 → 정의 수집 → 코드 색인 → 본문 렌더(링크·참조 기록) → 추적표·코드 색인 렌더 → 백링크 자리표시자 치환 →
    html/ 에 파일 쓰기 → 미해결 요약 출력. 원본 md는 수정하지 않는다. 반환: 미해결 참조가 있으면 1, 없으면 0(종료 코드).
    부작용: html/ 아래 파일 생성·덮어쓰기(이전 생성물 중 이번에 만들지 않은 .html은 삭제).
    """
    docs = [(p, parse_md(p.read_text(encoding='utf-8'))) for p in DOC_SOURCES if p.exists()]
    code = CodeIndex()
    defs = collect_defs(docs)
    linker = Linker(defs, code)
    rendered = {p: render_blocks(p, blocks, linker) for p, blocks in docs}
    trace_body = trace_page(docs, linker)
    code_body = code_page(linker, linker.refs)
    nodes = node_map(linker)

    def fill(text):
        """본문의 <!--BL:키--> 자리표시자를 백링크 HTML로 치환한다."""
        return re.sub(r'<!--BL:([^>]+)-->', lambda m: backlink_html(linker.refs, m.group(1)), text)

    OUT.mkdir(exist_ok=True)
    made = {}
    for path, (body, has_mermaid, toc) in rendered.items():
        name = page_name(path)
        toc_html = '<ul class="toc">' + ''.join(f'<li><a href="#{a}">{html.escape(t)}</a></li>' for lv, a, t in toc if lv <= 3) + '</ul>' if len(toc) > 4 else ''
        made[name] = page_html(path.stem, toc_html + fill(body), linker, has_mermaid, nodes)
    made[TRACE_NAME] = page_html('추적표', fill(trace_body))
    made[CODE_NAME] = page_html('코드 색인', code_body)
    listing = ''.join(f'<li><a href="{page_name(p)}">{html.escape(str(p.relative_to(ROOT)))}</a></li>' for p, _ in docs)
    made[INDEX_NAME] = page_html('문서 목록', f'<h1>개발문서</h1><p>md가 원본이고 이 HTML은 <code>build_docs.cmd</code>로 다시 만듭니다.</p>'
                                  f'<ul>{listing}</ul><ul><li><a href="{TRACE_NAME}">추적표</a></li><li><a href="{CODE_NAME}">코드 색인</a></li></ul>')
    for old in OUT.glob('*.html'):
        if old.name not in made:
            old.unlink()
    for name, text in made.items():
        (OUT / name).write_text(text, encoding='utf-8')

    seen = sorted(set(linker.unresolved))
    print(f'문서 {len(docs)}개, 정의 {len(defs)}개, 코드 심볼 {sum(len(v) for v in code.files.values())}개 → {OUT}')
    if not VENDOR_MERMAID.exists():
        print('주의: vendor/mermaid.min.js 없음. 다이어그램은 원문으로 표시됩니다.')
    for page, token, reason in seen:
        print(f'미해결: {page}: {token} ({reason})')
    print(f'미해결 참조 {len(seen)}건')
    return 1 if seen else 0


if __name__ == '__main__':
    sys.exit(main())
