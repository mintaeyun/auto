@echo off
REM [기능명세]
REM 파일 역할: 현재 Python 환경에 requirements.txt의 PDF 처리 의존성을 설치한다.
REM 입력: 이 폴더의 requirements.txt 및 py 런처가 선택한 Python.
REM 출력: pip 설치 로그. pause로 창을 유지한다.
REM 부작용/한계: 패키지 저장소 네트워크와 Python 환경 변경이 발생한다. Playwright는 현재 requirements에 포함되어 있지 않다.
chcp 65001 >nul
cd /d "%~dp0"
py -m pip install -r requirements.txt
pause
