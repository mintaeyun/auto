@echo off
REM [기능명세]
REM 파일 역할: 개발 PC에서 Python 로컬 웹 UI 서버를 시작한다. 배포 EXE 실행과는 별개다.
REM 입력: 명령행 인자를 전달하지 않는다. 이 파일이 있는 폴더를 작업 경로로 사용한다.
REM 출력: 기본 브라우저의 로컬 UI와 서버 프로세스. 실패하면 콘솔을 유지한다.
REM 실패 처리: pypdf import 실패 시 설치.cmd 안내 후 코드 1로 종료한다. pypdfium2는 아래 서버 import에서 필요하다.
REM 부작용: web_app.py 실행으로 로그/로컬 서버와 선택적 브라우저가 생성된다.
chcp 65001 >nul
cd /d "%~dp0"
py -c "import pypdf" >nul 2>&1
if errorlevel 1 (
  echo Python과 pypdf가 필요합니다. 먼저 설치.cmd를 실행하세요.
  pause
  exit /b 1
)
py web_app.py
if errorlevel 1 pause
