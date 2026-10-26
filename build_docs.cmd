@echo off
rem 개발문서 변환: README.md, docs\*.md, 기획\*.md(원본)를 html\ 폴더의 Standalone HTML로 다시 만든다.
rem md를 수정한 뒤 이 파일을 실행하면 된다. 원본 md는 변경하지 않는다.
cd /d "%~dp0"
py tools\build_docs.py
echo.
echo 결과: %~dp0html\index.html
pause
