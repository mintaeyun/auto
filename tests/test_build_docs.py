"""tools/build_docs.py 검증.

목적: md 파서, ID·심볼·파일:줄 자동 링크, 백링크, 미해결 보고, 원본 md 불변이 기대대로 동작하는지 확인한다.
실행: py -m unittest tests.test_build_docs (프로젝트 루트에서). 네트워크와 mermaid 라이브러리는 필요 없다.
"""
import hashlib
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
import build_docs as bd  # noqa: E402


class FakeCode:
    """링크 해결 테스트용 가짜 코드 색인. 심볼 'alpha'는 1곳, 'dup'은 2곳에 정의돼 있다."""
    files = {'a.py': [{'name': 'alpha', 'qual': 'alpha', 'kind': 'function', 'line': 3, 'end': 9, 'doc': ''}]}

    def find(self, name):
        return {'alpha': [('a.py', self.files['a.py'][0])], 'dup': [('a.py', {}), ('b.py', {})]}.get(name, [])

    def locate(self, rel, line):
        return self.files['a.py'][0] if 3 <= line <= 9 else None

    def resolve_file(self, text):
        return 'a.py' if text == 'a.py' else None


class ParseTests(unittest.TestCase):
    def test_blocks(self):
        """제목·표·목록·코드블록·문단이 각각 올바른 블록 종류로 나뉜다."""
        blocks = bd.parse_md('# T\n\n| a | b |\n|--|--|\n| 1 | 2 |\n\n- x\n- y\n\n```mermaid\ngraph\n```\n\n문단')
        self.assertEqual([b[0] for b in blocks], ['h', 'table', 'list', 'code', 'p'])
        self.assertEqual(blocks[1][2], [['1', '2']])


class LinkTests(unittest.TestCase):
    def setUp(self):
        self.linker = bd.Linker({'FN-01': ('p.html', 'FN-01')}, FakeCode())
        self.ctx = ('q.html', 'h1', '제목')

    def test_id_symbol_fileline_links_and_backlinks(self):
        """ID·심볼·파일:줄이 링크되고 참조한 위치가 백링크로 기록된다."""
        out = bd.inline('FN-01 `alpha()` `a.py:5`', self.linker, self.ctx)
        self.assertIn('href="p.html#FN-01"', out)
        self.assertIn('코드색인.html#a_py__alpha', out)
        self.assertEqual(out.count('class="ref'), 3)
        self.assertIn(self.ctx, self.linker.refs['FN-01'])
        self.assertIn(self.ctx, self.linker.refs['code:a.py::alpha'])

    def test_unresolved_and_ambiguous(self):
        """없는 ID와 모호한 심볼은 링크하지 않고 미해결로 보고된다."""
        out = bd.inline('FN-99 `dup()` `missing()`', self.linker, self.ctx)
        self.assertNotIn('<a ', out)
        reasons = {t: r for _, t, r in self.linker.unresolved}
        self.assertIn('FN-99', reasons)
        self.assertIn('모호', reasons['dup()'])
        self.assertIn('missing()', reasons)


class BuildTests(unittest.TestCase):
    def test_build_keeps_markdown_and_resolves_everything(self):
        """실제 문서로 빌드해도 원본 md는 변하지 않고 미해결 참조가 없으며 추적표가 만들어진다."""
        def digest():
            return {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in bd.DOC_SOURCES if p.exists()}
        before = digest()
        self.assertEqual(bd.main(), 0)
        self.assertEqual(before, digest())
        trace = (bd.OUT / bd.TRACE_NAME).read_text(encoding='utf-8')
        self.assertIn('FN-04', trace)
        self.assertIn('compare_core.py:', trace)


if __name__ == '__main__':
    unittest.main()
