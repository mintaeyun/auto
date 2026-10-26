# Mods 기획: 기획 안내 훅

## Context
업무자동화 프로젝트(`D:\chaai\07_auto`)의 작업 규칙("큰 기능은 플랜모드로 시작")을 `memory.md`에 의존하지 않고 Mods로 자동 적용한다. 1단계로 기획 안내 훅 하나만 만든다.

## 결정 사항
- 적용 범위: 이 프로젝트 한정 (작업 폴더가 `D:\chaai\07_auto`일 때만 동작)
- 1단계 모드: **기획 안내 훅** (`prompt.submit`)
- 보류: 기획 폴더 가드, `/기획` 명령어, 위험 명령 차단 (필요 시 2단계)

## 구현 계획
위치: `C:\Users\alsxo\.claude\dev-mods\<세션ID>/plan-guide/`

1. `.claude-plugin/plugin.json`: name `plan-guide`, version `0.1.0`
2. `hooks/hooks.json`: `{ "modules": ["./register.ts"] }`
3. `hooks/register.ts`: `on('prompt.submit', ...)` 훅
   - 현재 작업 폴더가 `D:\chaai\07_auto`가 아니면 `next(e)`로 통과
   - 프롬프트에 큰 기능 키워드(구현, 추가, 만들, 기능 등)가 있고 플랜모드가 아니면 `$.ui.toast`로 "플랜모드로 시작하세요(Shift+Tab). 기획은 `기획\` 폴더에 md로 저장됩니다" 안내
   - 프롬프트 자체는 바꾸지 않고 `next(e)`로 그대로 전달
4. `hooks/register.test.ts`: 키워드 감지와 폴더 조건 테스트

## 검증
- `claude plugin validate <mod 폴더>`
- `claude plugin test <mod 폴더>`
- 핫 리로드 허용 후, 이 프로젝트에서 "기능 구현해줘" 입력 시 안내가 뜨고 다른 폴더에서는 뜨지 않는지 확인

## 참고
- 프로젝트 규칙상 기획 문서는 `D:\chaai\07_auto\기획\`에 저장해야 한다. 플랜모드 중에는 이 계획 파일만 쓸 수 있으므로, 승인 후 이 내용을 `기획\mods-기획안내훅.md`로 복사한다.
- 키워드 감지는 오탐 가능성이 있어 안내만 하고 차단하지 않는다.
