"""실제 샘플 PDF를 이용한 값 좌표와 PNG 렌더링 unittest.

실행: py -m unittest discover -s tests -v. 샘플 PDF가 없으면 해당 테스트는 skip한다.
좌표 범위/페이지 번호, 없는 값의 빈 결과, PNG 헤더와 범위 밖 페이지 오류를 검증한다.
"""

import glob
import unittest

from locate import find_boxes, render_page


class LocateTests(unittest.TestCase):
    """LocateTests: 관련 회귀 조건을 묶은 unittest.TestCase.
    
    입력: unittest 테스트 러너가 생성한다.
    반환: 테스트 케이스 인스턴스.
    처리 규칙: test_ 메서드별 조건을 검증하며 필요한 setUp/정리는 각 메서드에 기술한다.
    예외/부작용: AssertionError는 테스트 실패, skipTest는 샘플 미준비로 처리한다.
    """
    def setUp(self):
        """좌표/렌더링 테스트의 샘플 PDF 경로를 준비한다.
        
        입력: self. downloads/invoices의 지정 샘플.
        반환: None.
        처리 규칙: glob으로 샘플을 찾고 없으면 skipTest로 해당 테스트를 건너뛴다.
        예외/부작용: 파일 목록만 조회하며 샘플 원본은 변경하지 않는다.
        """
        files = glob.glob('downloads/invoices/8352005f78*.pdf')
        if not files:
            self.skipTest('샘플 세금계산서 PDF가 없습니다.')
        self.path = files[0]

    def test_finds_value_with_normalized_box(self):
        """찾은 값의 좌표가 0~1 유효 사각형이며 페이지 번호가 1부터인지 검증한다.
        
        입력: self 및 테스트 내부의 고정 데이터/mock/샘플.
        반환: None. 조건이 맞지 않으면 테스트 실패.
        처리 규칙: 함수 본문의 assert 계열로 해당 회귀 조건을 확인한다.
        예외/부작용: 실패는 AssertionError. 임시 저장소/mock 또는 읽기 전용 샘플을 사용하며 운영 서버를 제어하지 않는다.
        """
        boxes = find_boxes(self.path, ['1,650,000'])
        self.assertTrue(boxes)
        x0, y0, x1, y1 = boxes[0]['box']
        self.assertTrue(0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1)
        self.assertEqual(boxes[0]['page'], 1)

    def test_missing_value_returns_empty(self):
        """PDF에 없는 숫자 검색은 빈 목록인지 검증한다.
        
        입력: self 및 테스트 내부의 고정 데이터/mock/샘플.
        반환: None. 조건이 맞지 않으면 테스트 실패.
        처리 규칙: 함수 본문의 assert 계열로 해당 회귀 조건을 확인한다.
        예외/부작용: 실패는 AssertionError. 임시 저장소/mock 또는 읽기 전용 샘플을 사용하며 운영 서버를 제어하지 않는다.
        """
        self.assertEqual(find_boxes(self.path, ['9,999,999']), [])

    def test_render_page_png(self):
        """렌더 결과가 PNG이고 범위 밖 페이지가 ValueError인지 검증한다.
        
        입력: self 및 테스트 내부의 고정 데이터/mock/샘플.
        반환: None. 조건이 맞지 않으면 테스트 실패.
        처리 규칙: 함수 본문의 assert 계열로 해당 회귀 조건을 확인한다.
        예외/부작용: 실패는 AssertionError. 임시 저장소/mock 또는 읽기 전용 샘플을 사용하며 운영 서버를 제어하지 않는다.
        """
        image, pages = render_page(self.path, 1)
        self.assertTrue(image.startswith(b'\x89PNG'))
        self.assertEqual(pages, 1)
        with self.assertRaises(ValueError):
            render_page(self.path, 5)


if __name__ == '__main__':
    unittest.main()
