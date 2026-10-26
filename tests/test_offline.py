"""저장 목록과 오프라인 원본 처리에서 네트워크를 호출하지 않는지 검증한다.

실행: py -m unittest discover -s tests -v. tempfile과 fetch mock으로 운영 자료/실제 통신을 분리한다.
기본/확장 목록 캐시가 서로 섞이지 않는지도 확인한다.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from compare_core import download, load_catalog, read_catalog


class OfflineTests(unittest.TestCase):
    """OfflineTests: 관련 회귀 조건을 묶은 unittest.TestCase.
    
    입력: unittest 테스트 러너가 생성한다.
    반환: 테스트 케이스 인스턴스.
    처리 규칙: test_ 메서드별 조건을 검증하며 필요한 setUp/정리는 각 메서드에 기술한다.
    예외/부작용: AssertionError는 테스트 실패, skipTest는 샘플 미준비로 처리한다.
    """
    def test_saved_catalog_needs_no_network(self):
        """캐시 목록을 네트워크 없이 읽고 최초 조회시각을 유지하는지 검증한다.
        
        입력: self 및 테스트 내부의 고정 데이터/mock/샘플.
        반환: None. 조건이 맞지 않으면 테스트 실패.
        처리 규칙: 함수 본문의 assert 계열로 해당 회귀 조건을 확인한다.
        예외/부작용: 실패는 AssertionError. 임시 저장소/mock 또는 읽기 전용 샘플을 사용하며 운영 서버를 제어하지 않는다.
        """
        with tempfile.TemporaryDirectory() as folder, patch('compare_core.ROOT', Path(folder)):
            data = {'mails': [], 'contracts': []}
            with patch('compare_core.fetch', return_value=json.dumps(data).encode()):
                online = load_catalog(False)
            with patch('compare_core.fetch', side_effect=AssertionError('Network forbidden')):
                cached = read_catalog(False)
                self.assertEqual(cached['_synced_at'], online['_synced_at'])

    def test_missing_offline_pdf_never_downloads(self):
        """오프라인 PDF 누락 시 다운로드하지 않고 FileNotFoundError를 내는지 검증한다.
        
        입력: self 및 테스트 내부의 고정 데이터/mock/샘플.
        반환: None. 조건이 맞지 않으면 테스트 실패.
        처리 규칙: 함수 본문의 assert 계열로 해당 회귀 조건을 확인한다.
        예외/부작용: 실패는 AssertionError. 임시 저장소/mock 또는 읽기 전용 샘플을 사용하며 운영 서버를 제어하지 않는다.
        """
        with tempfile.TemporaryDirectory() as folder, patch('compare_core.ROOT', Path(folder)):
            with patch('compare_core.fetch', side_effect=AssertionError('Network forbidden')) as network:
                with self.assertRaises(FileNotFoundError):
                    download({'url': 'files/test.pdf', 'name': 'test.pdf'}, 'invoices', offline=True)
                network.assert_not_called()

    def test_catalog_variants_are_separate(self):
        """기본 목록만 저장했을 때 확장 목록을 잘못 재사용하지 않는지 검증한다.
        
        입력: self 및 테스트 내부의 고정 데이터/mock/샘플.
        반환: None. 조건이 맞지 않으면 테스트 실패.
        처리 규칙: 함수 본문의 assert 계열로 해당 회귀 조건을 확인한다.
        예외/부작용: 실패는 AssertionError. 임시 저장소/mock 또는 읽기 전용 샘플을 사용하며 운영 서버를 제어하지 않는다.
        """
        with tempfile.TemporaryDirectory() as folder, patch('compare_core.ROOT', Path(folder)):
            with patch('compare_core.fetch', return_value=b'{"mails":[],"contracts":[]}'):
                load_catalog(False)
            with self.assertRaises(FileNotFoundError):
                read_catalog(True)
