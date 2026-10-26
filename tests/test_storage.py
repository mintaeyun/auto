"""세금계산서/계약서 저장 상태 및 여러 저장 폴더 검색을 검증하는 unittest.

실행: py -m unittest discover -s tests -v. 임시 폴더와 mock을 사용하고 테스트 종료 후 정리한다.
저장 상태는 비교 일치 여부와 별개이며 계약 후보가 모호할 때 확정하지 않아야 한다.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from compare_core import cached_path, storage_status


class StorageStatusTests(unittest.TestCase):
    """StorageStatusTests: 관련 회귀 조건을 묶은 unittest.TestCase.
    
    입력: unittest 테스트 러너가 생성한다.
    반환: 테스트 케이스 인스턴스.
    처리 규칙: test_ 메서드별 조건을 검증하며 필요한 setUp/정리는 각 메서드에 기술한다.
    예외/부작용: AssertionError는 테스트 실패, skipTest는 샘플 미준비로 처리한다.
    """
    def setUp(self):
        """저장 상태 테스트용 임시 루트와 샘플 메일/계약을 준비한다.
        
        입력: self.
        반환: None.
        처리 규칙: TemporaryDirectory와 ROOT patch를 만들고 addCleanup으로 복구를 예약한다.
        예외/부작용: 테스트 동안 파일 루트만 임시 교체한다. 종료 시 patch와 임시 폴더가 정리된다.
        """
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root_patch = patch('compare_core.ROOT', Path(self.folder.name))
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        self.mail = {'from_name': '별빛보안 담당자', 'attachments': [{'name': 'invoice.pdf', 'url': 'files/invoice.pdf'}]}
        self.contract = {'id': 'C-1', 'kind': '지출', 'partner': '별빛보안(주)', 'partner_biz_no': '000-81-00032',
                         'file': {'name': 'contract.pdf', 'url': 'files/contract.pdf'}}

    def touch(self, item, category):
        """저장 여부 검사에 사용할 가짜 파일을 만든다.
        
        입력: item: 파일 항목 dict. category: 저장 구분.
        반환: None.
        처리 규칙: cached_path의 부모 폴더를 만들고 fixture 바이트를 쓴다.
        예외/부작용: 임시 폴더만 변경한다. 유효한 PDF는 아니므로 텍스트 추출 테스트는 별도 mock을 사용한다.
        """
        path = cached_path(item, category)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'fixture')

    def test_invoice_missing_contract_saved(self):
        """계산서 미저장/계약서 저장 상태가 독립적으로 표시되는지 검증한다.
        
        입력: self 및 테스트 내부의 고정 데이터/mock/샘플.
        반환: None. 조건이 맞지 않으면 테스트 실패.
        처리 규칙: 함수 본문의 assert 계열로 해당 회귀 조건을 확인한다.
        예외/부작용: 실패는 AssertionError. 임시 저장소/mock 또는 읽기 전용 샘플을 사용하며 운영 서버를 제어하지 않는다.
        """
        self.touch(self.contract['file'], 'contracts')
        invoice, contract = storage_status(self.mail, [self.contract])
        self.assertEqual(invoice['status'], '미저장')
        self.assertEqual(contract['status'], '저장됨')
        self.assertEqual(contract['basis'], '메일 정보 기준')

    def test_invoice_saved_contract_missing_uses_pdf_identity(self):
        """계산서 저장 후 사업자번호 연결 및 계약서 미저장 상태를 검증한다.
        
        입력: self 및 테스트 내부의 고정 데이터/mock/샘플.
        반환: None. 조건이 맞지 않으면 테스트 실패.
        처리 규칙: 함수 본문의 assert 계열로 해당 회귀 조건을 확인한다.
        예외/부작용: 실패는 AssertionError. 임시 저장소/mock 또는 읽기 전용 샘플을 사용하며 운영 서버를 제어하지 않는다.
        """
        self.touch(self.mail['attachments'][0], 'invoices')
        with patch('compare_core.read_pdf', return_value=(Path('invoice.pdf'), '등록번호 000-81-00032')):
            invoice, contract = storage_status(self.mail, [self.contract])
        self.assertEqual(invoice['status'], '저장됨')
        self.assertEqual(contract['status'], '미저장')
        self.assertEqual(contract['basis'], 'PDF 사업자번호 기준')

    def test_multiple_candidates_not_reported_as_saved(self):
        """계약 후보가 여러 개일 때 저장 상태를 확정하지 않는지 검증한다.
        
        입력: self 및 테스트 내부의 고정 데이터/mock/샘플.
        반환: None. 조건이 맞지 않으면 테스트 실패.
        처리 규칙: 함수 본문의 assert 계열로 해당 회귀 조건을 확인한다.
        예외/부작용: 실패는 AssertionError. 임시 저장소/mock 또는 읽기 전용 샘플을 사용하며 운영 서버를 제어하지 않는다.
        """
        other = dict(self.contract, id='C-2')
        invoice, contract = storage_status(self.mail, [self.contract, other])
        self.assertEqual(contract['status'], '연결 확인 필요')

    def test_notice_has_no_pdf_or_contract(self):
        """공지 메일에 PDF 없음/계약 연결 대상 없음이 표시되는지 검증한다.
        
        입력: self 및 테스트 내부의 고정 데이터/mock/샘플.
        반환: None. 조건이 맞지 않으면 테스트 실패.
        처리 규칙: 함수 본문의 assert 계열로 해당 회귀 조건을 확인한다.
        예외/부작용: 실패는 AssertionError. 임시 저장소/mock 또는 읽기 전용 샘플을 사용하며 운영 서버를 제어하지 않는다.
        """
        invoice, contract = storage_status({'from_name': '관리자', 'attachments': []}, [self.contract])
        self.assertEqual(invoice['status'], 'PDF 없음')
        self.assertEqual(contract['status'], '연결 대상 없음')


class MultiRootTests(unittest.TestCase):
    """MultiRootTests: 관련 회귀 조건을 묶은 unittest.TestCase.
    
    입력: unittest 테스트 러너가 생성한다.
    반환: 테스트 케이스 인스턴스.
    처리 규칙: test_ 메서드별 조건을 검증하며 필요한 setUp/정리는 각 메서드에 기술한다.
    예외/부작용: AssertionError는 테스트 실패, skipTest는 샘플 미준비로 처리한다.
    """
    def test_saved_pdf_found_in_other_data_folder(self):
        """다른 자료 루트에 저장된 기존 PDF를 재다운로드 없이 찾는지 검증한다.
        
        입력: self 및 테스트 내부의 고정 데이터/mock/샘플.
        반환: None. 조건이 맞지 않으면 테스트 실패.
        처리 규칙: 함수 본문의 assert 계열로 해당 회귀 조건을 확인한다.
        예외/부작용: 실패는 AssertionError. 임시 저장소/mock 또는 읽기 전용 샘플을 사용하며 운영 서버를 제어하지 않는다.
        """
        import tempfile
        from pathlib import Path
        import compare_core
        item = dict(name='a.pdf', url='files/a.pdf')
        with tempfile.TemporaryDirectory() as primary, tempfile.TemporaryDirectory() as other:
            (Path(primary) / 'downloads').mkdir()
            expected = Path(other) / 'downloads' / 'invoices' / compare_core.cached_path(item, 'invoices').name
            expected.parent.mkdir(parents=True)
            expected.write_bytes(b'%PDF-1.4')
            # 콜백/정규화 함수: 아래 lambda는 해당 표현식의 인자를 받아 즉시 변환하거나 상위 함수의 작업을 위임한다.
            with patch.object(compare_core, 'ROOT', Path(primary)), patch.object(compare_core, 'data_roots', lambda: [Path(primary), Path(other)]):
                self.assertEqual(compare_core.cached_path(item, 'invoices'), expected)
