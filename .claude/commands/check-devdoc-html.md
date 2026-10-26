---
description: HTML 개발문서의 깨진 링크·앵커·옵시디언 링크·외부 URL·Mermaid 오류를 점검만 한다 (파일 수정 안 함)
argument-hint: [점검할 파일 경로, 생략하면 Todo.md 기준]
allowed-tools: Bash(py tools/check_devdoc_html.py:*), Bash(python tools/check_devdoc_html.py:*), Read, Grep, Glob
---

Standalone HTML 개발문서 작업 후 문서 상태를 점검한다. **점검만 하고 어떤 파일도 수정하지 않는다.** 수정은 사용자가 따로 요청할 때만 한다.

대상: `$ARGUMENTS`
- 비어 있으면: 프로젝트 루트의 `Todo.md`를 기본 대상으로, Todo.md가 언급한 문서와 `html/*.html`(현재 작업 중인 HTML 개발문서)을 함께 점검한다.
- 경로가 있으면: 그 파일만 점검한다. (예: `/check-devdoc-html docs/plan.html`)

## 절차

1. 점검 스크립트를 실행한다. 경로가 주어졌으면 그대로 인자로 넘긴다. 스크립트는 읽기 전용이다.
   - `py tools/check_devdoc_html.py $ARGUMENTS`
   - 점검 항목: 파일 링크의 실제 파일 존재, `#앵커`와 `파일#앵커`의 대상 존재, 옵시디언 `[[노트#제목]]`의 파일·제목 존재, 외부 `http(s)` URL 접속, Mermaid 정적 검사(다이어그램 종류·괄호/따옴표 균형·빈 블록).
2. Mermaid는 정적 검사로 문법 오류를 모두 잡지 못한다. 대상에 Mermaid 블록이 있고 Chrome 도구(`mcp__claude-in-chrome__*`)를 쓸 수 있으면, 임시로 로컬 서버(`python -m http.server <포트> --bind 127.0.0.1`)를 `html/` 폴더에서 띄워 페이지를 열고 `.mermaid` 안에 "Syntax error" 텍스트나 렌더되지 않은 블록이 있는지 확인한다. 확인이 끝나면 서버와 탭을 정리한다. Chrome을 쓸 수 없으면 이 단계를 건너뛰고 보고에 "렌더 확인 못 함"이라고 적는다.
3. 외부 URL 실패는 봇 차단·일시 장애일 수 있으므로 원인 칸에 그 가능성을 표시한다.

## 결과 보고

아래 형식의 표 하나로 정리한다. 문제가 없으면 "문제 없음"과 점검한 파일 수만 적는다.

| 줄번호 | 유형(링크/Mermaid) | 내용 | 원인 | 수정 제안 |
|---|---|---|---|---|

- 여러 파일을 점검했으면 맨 앞에 파일 열을 추가한다.
- 표 아래에 한 줄로 총 건수와 점검한 파일, Mermaid 렌더 확인 여부를 적는다.
- 수정 제안은 제안만 적고 적용하지 않는다. "고칠까요?" 같은 추가 질문 없이 표로 끝낸다.
