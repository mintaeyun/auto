"""PDF 비교값의 좌표 검색과 페이지 이미지 렌더링 모듈.

기능명세:
- pypdfium2를 사용하며 compare_core는 좌표 검색, web_app은 PNG 렌더링을 호출한다.
- 외부 서버/AI/OCR 없이 로컬 파일을 읽는다. 자체 실행 진입점은 없다.
- 좌표는 페이지 너비/높이에 대한 0~1 비율이고 웹 화면 좌상단을 원점으로 한다.
- SCALE은 PNG 확대 비율, PAD_X/PAD_Y는 검색 사각형의 추가 여백이다.
"""
from __future__ import annotations

import io
from pathlib import Path

import pypdfium2 as pdfium

SCALE = 1.6
PAD_X, PAD_Y = 0.004, 0.002  # 글자가 박스 테두리에 가리지 않도록 여백


def find_boxes(path, needles):
    """PDF에서 검색값이 나타나는 모든 위치를 페이지 비율 좌표로 반환한다.
    
    입력:
        path: 로컬 PDF 경로. needles: 검색할 금액/사업자번호 등의 문자열 목록.
    반환:
        [{value, page, box}]. page는 1부터 시작하고 box는 [x0, y0, x1, y1]이다.
    처리:
        PDFium 텍스트 검색으로 각 검색값의 모든 출현 위치를 찾고 글자 사각형을 얻는다.
        x좌표는 페이지 너비로 나누고 y좌표는 1-y/높이로 바꿔 웹의 위쪽 원점에 맞춘다.
        여백 PAD_X/PAD_Y를 더하고 0~1로 제한한 뒤 소수 넷째 자리까지 반올림한다.
    가정/한계:
        동일 숫자가 여러 영역에 있으면 모두 표시한다. 필드의 의미를 판별하거나
        OCR을 수행하지 않는다. PDF 열기 실패/검색 결과 없음은 빈 목록이다.
    예외/부작용:
        로컬 PDF를 읽고 마지막에 닫는다. PDF를 연 이후 발생한 검색 오류는 전달한다.
        원본 수정과 네트워크 요청은 없다.
    """
    found = []
    try:
        pdf = pdfium.PdfDocument(str(path))
    except Exception:
        return found
    try:
        for number in range(len(pdf)):
            page = pdf[number]
            width, height = page.get_size()
            text = page.get_textpage()
            for value in needles:
                if not value:
                    continue
                searcher = text.search(str(value))
                while True:
                    hit = searcher.get_next()
                    if hit is None:
                        break
                    index, count = hit
                    for k in range(text.count_rects(index, count)):
                        left, bottom, right, top = text.get_rect(k)
                        found.append({'value': str(value), 'page': number + 1,
                                      'box': [round(max(0, left / width - PAD_X), 4), round(max(0, 1 - top / height - PAD_Y), 4),
                                              round(min(1, right / width + PAD_X), 4), round(min(1, 1 - bottom / height + PAD_Y), 4)]})
    finally:
        pdf.close()
    return found


def render_page(path, number):
    """지정한 PDF 페이지를 화면 표시용 PNG로 렌더링한다.
    
    입력: path: 로컬 PDF 경로. number: 1부터 시작하는 페이지 번호.
    반환: (PNG bytes, 전체 페이지 수).
    처리 규칙: PDF를 열어 페이지 범위를 검사하고 SCALE 배율의 이미지를 메모리 버퍼에 PNG로 저장한다.
    예외/부작용: 로컬 파일 읽기와 메모리 할당이 발생한다. 범위 밖 번호는 ValueError, 열기/렌더링 오류는 전달한다. finally에서 PDF를 닫고 파일을 쓰지 않는다.
    """
    pdf = pdfium.PdfDocument(str(Path(path)))
    try:
        if not 1 <= number <= len(pdf):
            raise ValueError('페이지 번호가 범위를 벗어났습니다.')
        buffer = io.BytesIO()
        pdf[number - 1].render(scale=SCALE).to_pil().save(buffer, format='PNG')
        return buffer.getvalue(), len(pdf)
    finally:
        pdf.close()
