"""HTML/md 개발문서의 링크·앵커·옵시디언 링크·Mermaid를 점검만 하는 읽기 전용 스크립트.

기능명세:
- 어떤 파일도 수정하지 않는다(읽기와 외부 URL HEAD/GET 요청만 한다).
- 대상 인자가 없으면 루트의 Todo.md를 점검하고, Todo.md가 언급한 .html/.md 경로와 html/ 폴더의 *.html도 함께 점검한다.
  인자로 파일 경로를 주면 그 파일만 점검한다.
- 점검: (1) 상대 경로 링크의 실제 파일 존재 (2) '#앵커'와 'file#앵커'의 대상 id/제목 존재
  (3) 옵시디언 [[노트|별칭#제목]] 대상 파일·제목 존재 (4) http(s) URL 응답 (5) Mermaid 블록 정적 검사.
- 출력: 마크다운 표 `줄번호 | 유형 | 내용 | 원인 | 수정 제안`. 문제가 없으면 0, 있으면 1을 종료 코드로 반환한다.
- 한계: Mermaid는 실제 렌더러 없이 정적 규칙(다이어그램 종류, 괄호·따옴표 균형, 빈 블록)만 검사하므로
  문법 오류를 모두 잡지는 못한다. 브라우저 렌더 결과 확인은 슬래시 커맨드가 별도로 안내한다.
  외부 URL은 네트워크 상태와 사이트 정책(봇 차단)에 따라 거짓 경고가 있을 수 있다.
- 실행: py tools/check_devdoc_html.py [파일경로] [--no-external]
"""
from __future__ import annotations

import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MERMAID_TYPES = ('graph', 'flowchart', 'sequenceDiagram', 'classDiagram', 'stateDiagram', 'erDiagram', 'journey', 'gantt',
                 'pie', 'quadrantChart', 'requirementDiagram', 'gitGraph', 'mindmap', 'timeline', 'sankey', 'xychart',
                 'block', 'packet', 'architecture', 'C4Context', 'C4Container', 'C4Component', 'C4Dynamic', 'C4Deployment')


class PageScan(HTMLParser):
    """HTML을 한 번 훑어 링크, id, mermaid 블록을 모은다.

    결과 속성: links[(줄, 속성, 주소)], ids(set), mermaid[(시작 줄, 본문)].
    class에 mermaid가 있는 pre/div 안의 텍스트를 mermaid 본문으로 모은다. 부작용 없음.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links, self.ids, self.mermaid, self._buf, self._depth, self._start = [], set(), [], None, 0, 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        line = self.getpos()[0]
        for key in ('id', 'name'):
            if a.get(key):
                self.ids.add(a[key])
        for key in ('href', 'src'):
            if a.get(key) is not None and tag in ('a', 'img', 'script', 'link', 'iframe', 'source'):
                self.links.append((line, key, a[key]))
        if self._buf is not None:
            self._depth += 1
        elif 'mermaid' in (a.get('class') or '').split():
            self._buf, self._depth, self._start = [], 1, line

    def handle_endtag(self, tag):
        if self._buf is not None:
            self._depth -= 1
            if self._depth <= 0:
                self.mermaid.append((self._start, ''.join(self._buf)))
                self._buf = None

    def handle_data(self, data):
        if self._buf is not None:
            self._buf.append(data)


def github_slug(title):
    """md 제목을 GitHub 방식 앵커로 바꾼다(소문자, 공백→-, 구두점 제거)."""
    t = re.sub(r'[`*_]', '', title.strip().lower())
    return re.sub(r'\s', '-', re.sub(r'[^\w\s가-힣-]', '', t))


def scan_md(text):
    """md 텍스트에서 링크, 옵시디언 링크, 제목 앵커, mermaid 블록을 찾는다. 반환: (links, wikilinks, anchors, mermaid)."""
    links, wiki, anchors, mer, in_code, buf, start = [], [], set(), [], False, [], 0
    for n, line in enumerate(text.splitlines(), 1):
        if line.startswith('```'):
            if in_code and buf is not None and start:
                mer.append((start, '\n'.join(buf)))
            in_code = not in_code
            buf, start = [], (n if in_code and line[3:].strip() == 'mermaid' else 0)
            continue
        if in_code:
            buf.append(line)
            continue
        m = re.match(r'^#{1,6}\s+(.*)', line)
        if m:
            anchors.add(github_slug(m.group(1)))
        for mm in re.finditer(r'(?<!!)\[[^\]]*\]\(([^)\s]+)[^)]*\)|!\[[^\]]*\]\(([^)\s]+)', line):
            links.append((n, 'md', mm.group(1) or mm.group(2)))
        for mm in re.finditer(r'\[\[([^\]]+)\]\]', line):
            wiki.append((n, mm.group(1)))
    return links, wiki, anchors, mer


def load(path):
    """파일을 읽어 점검에 필요한 정보 dict로 반환한다. html은 PageScan, md는 scan_md 결과. 읽기 실패 시 None."""
    try:
        text = path.read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError):
        return None
    if path.suffix.lower() in ('.html', '.htm'):
        p = PageScan()
        p.feed(text)
        return {'links': p.links, 'wiki': [], 'anchors': p.ids, 'mermaid': p.mermaid, 'html': True}
    links, wiki, anchors, mer = scan_md(text)
    return {'links': links, 'wiki': wiki, 'anchors': anchors, 'mermaid': mer, 'html': False}


_cache = {}


def info(path):
    """load 결과를 경로별로 캐시한다."""
    if path not in _cache:
        _cache[path] = load(path)
    return _cache[path]


def check_mermaid(code):
    """Mermaid 본문을 정적 검사해 문제 사유 목록을 반환한다(없으면 빈 목록).

    규칙: 비어 있음, 첫 유효 줄이 알려진 다이어그램 종류가 아님, 따옴표 짝 불일치, ()[]{} 짝 불일치.
    따옴표 안의 글자는 괄호 검사에서 제외한다. 렌더러가 아니므로 모든 문법 오류를 잡지는 못한다.
    """
    lines = [l for l in code.splitlines() if l.strip() and not l.strip().startswith('%%')]
    if not lines:
        return ['본문이 비어 있음']
    problems = []
    first = lines[0].strip()
    if first == '---':  # frontmatter 건너뜀
        rest = lines[1:]
        end = next((i for i, l in enumerate(rest) if l.strip() == '---'), None)
        first = rest[end + 1].strip() if end is not None and end + 1 < len(rest) else ''
    if not first.startswith(MERMAID_TYPES):
        problems.append(f'알 수 없는 다이어그램 종류: {first[:30]!r}')
    for n, l in enumerate(lines, 1):
        if l.count('"') % 2:
            problems.append(f'따옴표 짝 불일치 (본문 {n}번째 줄): {l.strip()[:40]}')
        flat = re.sub(r'"[^"]*"', '', l)
        for a, b in ('()', '[]', '{}'):
            if flat.count(a) != flat.count(b) and not re.search(r'[-=.]+[>)xo]|\|', flat) or flat.count(a) != flat.count(b) and a == '[':
                problems.append(f'{a}{b} 짝 불일치 (본문 {n}번째 줄): {l.strip()[:40]}')
                break
    return problems


def find_note(name):
    """옵시디언 노트 이름을 루트 아래 .md/.html 파일로 해석한다. 경로 포함이면 그대로, 이름만이면 파일명이 같은 첫 파일."""
    base = name.replace('\\', '/').strip()
    for suffix in ('', '.md', '.html'):
        cand = ROOT / (base + suffix)
        if cand.is_file():
            return cand
    stem = Path(base).name
    for suffix in ('.md', '.html'):
        hits = [p for p in ROOT.rglob(stem + suffix) if not {'.git', 'node_modules', 'build', 'dist', 'vendor'} & set(p.parts)]
        if hits:
            return hits[0]
    return None


def check_url(url):
    """외부 URL에 HEAD(실패 시 GET)로 접속해 (정상 여부, 사유)를 반환한다. 제한 시간 8초, 리다이렉트는 따라간다."""
    req_headers = {'User-Agent': 'Mozilla/5.0 devdoc-check'}
    last = ''
    for method in ('HEAD', 'GET'):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, method=method, headers=req_headers), timeout=8) as r:
                return True, f'HTTP {r.status}'
        except urllib.error.HTTPError as e:
            last = f'HTTP {e.code}'
            if e.code in (405, 501) or method == 'HEAD' and e.code >= 400:
                continue
            return False, last
        except Exception as e:  # noqa: BLE001  네트워크 오류 전체를 사유 문자열로 보고
            last = f'{type(e).__name__}: {e}'
            if method == 'GET':
                return False, last
    return False, last


def check_file(path, external=True):
    """한 파일을 점검해 문제 행 [(줄, 유형, 내용, 원인, 제안)]을 반환한다. 파일을 수정하지 않는다."""
    data = info(path)
    rel = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path
    if data is None:
        return [(0, '링크', str(rel), '파일을 읽을 수 없음', '경로·인코딩(UTF-8) 확인')]
    rows, urls = [], []
    for line, _, href in data['links']:
        href = href.strip()
        if not href or href.startswith(('mailto:', 'tel:', 'javascript:', 'data:')):
            continue
        if re.match(r'^https?://', href):
            urls.append((line, href))
            continue
        if href.startswith('//'):
            urls.append((line, 'https:' + href))
            continue
        target_part, _, frag = href.partition('#')
        target_part = urllib.parse.unquote(target_part.split('?')[0])
        target = path if not target_part else (path.parent / target_part)
        if target_part and not target.exists():
            rows.append((line, '링크', href, f'파일 없음: {target_part}', '경로·파일명 확인 또는 파일 생성'))
            continue
        if frag and target.is_file() and target.suffix.lower() in ('.html', '.htm', '.md'):
            other = info(target)
            frag_key = urllib.parse.unquote(frag)
            if other is not None and frag_key not in other['anchors'] and github_slug(frag_key) not in other['anchors']:
                rows.append((line, '링크', href, f'앵커 없음: #{frag_key}', '대상의 id/제목과 일치하게 수정'))
    for line, name in data['wiki']:
        note, _, heading = name.split('|')[0].partition('#')
        target = find_note(note) if note else path
        if target is None:
            rows.append((line, '링크', f'[[{name}]]', f'노트 파일 없음: {note}', '노트 이름·경로 확인'))
        elif heading:
            other = info(target)
            if other is not None and github_slug(heading) not in other['anchors'] and heading not in other['anchors']:
                rows.append((line, '링크', f'[[{name}]]', f'제목 없음: #{heading}', '노트의 제목과 일치하게 수정'))
    for start, code in data['mermaid']:
        for why in check_mermaid(code):
            rows.append((start, 'Mermaid', code.strip().splitlines()[0][:40] if code.strip() else '(빈 블록)', why, '문법 확인 후 수정'))
    if external and urls:
        unique = sorted({u for _, u in urls})
        with ThreadPoolExecutor(8) as pool:
            result = dict(zip(unique, pool.map(check_url, unique)))
        for line, url in urls:
            ok, why = result[url]
            if not ok:
                rows.append((line, '링크', url, f'외부 URL 응답 실패 ({why})', 'URL 오타·폐기 여부 확인(봇 차단이면 무시 가능)'))
    return [(r[0], r[1], r[2], r[3], r[4], str(rel)) for r in sorted(rows)]


def targets(arg):
    """점검 대상 파일 목록. 인자가 있으면 그 파일 하나, 없으면 Todo.md + Todo.md가 언급한 문서 + html/*.html."""
    if arg:
        p = Path(arg)
        return [p if p.is_absolute() else (Path.cwd() / p if (Path.cwd() / p).exists() else ROOT / p)]
    out = [ROOT / 'Todo.md']
    todo = (ROOT / 'Todo.md').read_text(encoding='utf-8') if (ROOT / 'Todo.md').exists() else ''
    for m in re.finditer(r'[\w가-힣./\\-]+\.(?:html|md)\b', todo):
        p = ROOT / m.group(0).replace('\\', '/')
        if p.is_file() and p not in out:
            out.append(p)
    out += [p for p in sorted((ROOT / 'html').glob('*.html')) if p not in out]
    return out


def main(argv):
    """점검을 실행하고 표를 출력한다. 반환: 문제가 있으면 1. --no-external이면 외부 URL을 건너뛴다."""
    args = [a for a in argv if not a.startswith('--')]
    files = targets(args[0] if args else None)
    all_rows = []
    for f in files:
        if not f.exists():
            all_rows.append((0, '링크', str(f), '점검 대상 파일이 없음', '경로 확인', str(f)))
            continue
        all_rows += check_file(f, '--no-external' not in argv)
    print(f'점검 파일 {len(files)}개: ' + ', '.join(f.name for f in files))
    if not all_rows:
        print('문제 없음')
        return 0
    print('\n| 파일 | 줄번호 | 유형(링크/Mermaid) | 내용 | 원인 | 수정 제안 |\n|---|---|---|---|---|---|')
    for line, kind, content, why, fix, name in all_rows:
        esc = lambda s: str(s).replace('|', '\\|').replace('\n', ' ')
        print(f'| {esc(name)} | {line} | {kind} | {esc(content)} | {esc(why)} | {esc(fix)} |')
    print(f'\n문제 {len(all_rows)}건')
    return 1


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.exit(main(sys.argv[1:]))
