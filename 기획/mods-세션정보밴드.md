# Mods 기획: 세션 정보 밴드 (최소 기능)

## Context
사용자가 플랜모드 없이 최소 기능으로 바로 시작하길 원함. 프롬프트 위 한 줄 밴드에 현재 세션 정보를 보여 주는 모드를 만든다. 이전 `plan-guide` 모드와는 별개의 새 모드.

## 표시할 정보 (최소)
1. 토큰 사용량과 남은 컨텍스트: `session.measure`의 `context` (`tokens`, `window`, `percent`) → "토큰 123k / 1000k (12%), 남음 877k"
2. 작업 폴더: `$.session.cwd()`
3. git 브랜치: `$.process.run(["git", "branch", "--show-current"])` (`$.session.repo()`에는 브랜치 필드가 없음; git 저장소가 아니면 생략)
4. memory.md: 작업 폴더에 `memory.md`가 있는지와 줄 수 (`$.fs.read`, 없으면 "없음")

## 구현 계획
위치: `C:\Users\alsxo\.claude\dev-mods\521f9241-8095-4310-b139-7b2d5e1df8a4\session-band\`

1. `.claude-plugin/plugin.json`: name `session-band`, version `0.1.0`, `"types": "./types/index.d.ts"`
2. `hooks/hooks.json`: `{ "modules": ["./register.tsx"] }`
3. `types/index.d.ts`: `$.state` 값 선언 (`info`: 토큰/폴더/브랜치/메모리 문자열 묶음)
4. `hooks/register.tsx`
   - `session.measure` 훅: `context`로 토큰·남은 컨텍스트 갱신 (`atom`/`update`)
   - `session.start`와 `prompt.submit` 훅: 폴더·브랜치·memory.md 갱신
   - `ui.render` (`component: 'AbovePrompt'`) 훅: `Box`/`Text`로 한 줄 표시, 값이 없으면 `next(e)`
   - 모든 훅에 `.catch`를 달아 실패해도 세션에 영향 없게 함
- 참고 예제: `plugin-authoring/examples/band.tsx`, `band-state.d.ts` (같은 `atom`/`read`/`update` 패턴 재사용)

## 검증
- `claude plugin validate <mod 폴더>`
- `tsc -p <mod 폴더>` 타입 체크
- 핫 리로드 허용 후 프롬프트 위에 한 줄이 뜨는지, git 저장소가 아닌 폴더와 memory.md가 없는 폴더에서도 깨지지 않는지 확인
- 최소 기능이므로 테스트 파일은 이후 단계로 미룸

## 참고
- 지난번 핫 리로드를 거부했으므로 로드되려면 허용하거나 세션을 다시 시작해야 함
- 기획 문서는 프로젝트 규칙에 따라 승인 후 `D:\chaai\07_auto\기획\mods-세션정보밴드.md`로 복사
