@echo off
REM [기능명세]
REM 파일 역할: 웹 UI와 PDF 렌더러를 포함한 Windows 단일 EXE를 빌드한다.
REM 입력: web_app.py, web 폴더, requirements.txt와 관련 Python 모듈.
REM 출력: dist/SaebomCompare_v2.exe와 build/중간파일 및 spec.
REM 처리: 의존성과 PyInstaller 설치 후 onefile/windowed로 빌드하며 pypdfium2 리소스를 collect한다.
REM 실패 처리: 설치 또는 빌드 실패 시 코드 1로 종료한다.
REM 부작용/한계: 네트워크/패키지환경/빌드출력이 변경된다. 실행 중인 EXE 교체와 Chrome 제어 도구 패키징은 이 스크립트의 기능이 아니다.
chcp 65001 >nul
cd /d "%~dp0"
py -m pip install -r requirements.txt pyinstaller
if errorlevel 1 exit /b 1
py -m PyInstaller --noconfirm --clean --onefile --windowed --name SaebomCompare_v2 --collect-all pypdfium2 --add-data "web;web" web_app.py
if errorlevel 1 exit /b 1
echo 빌드 완료: %~dp0dist\SaebomCompare_v2.exe
pause
