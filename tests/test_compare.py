"""고정 텍스트와 다운로드 모의 객체로 비교 규칙을 검증하는 unittest.

실행: py -m unittest discover -s tests -v. 네트워크와 실제 다운로드는 mock으로 대체한다.
금액/기간/분할 지급/추출 실패가 잘못된 일치 판정으로 이어지지 않는지 검증한다.
"""

import unittest
from unittest.mock import patch
from pathlib import Path
from compare_core import compare_mail, parse_contract, parse_invoice

INVOICE = '''등록번호* 000-81-00032
상호* 별빛보안(주) 성명* 남별빛
작성일자* 공급가액* 세액*
2026-09-30 1,500,000 150,000
2026년 9월분
합계 현금 수표 어음 외상미수금
이 금액을 청구 함
1,650,000 0 0 0 0'''
CONTRACT = '''[2026]년 [01]월 [01]일부터 [2026]년 [12]월 [31]일까지
월 금 1,500,000원(부가가치세 별도)
사업자등록번호 000-81-00032'''


class ComparisonTests(unittest.TestCase):
    """ComparisonTests: 관련 회귀 조건을 묶은 unittest.TestCase.
    
    입력: unittest 테스트 러너가 생성한다.
    반환: 테스트 케이스 인스턴스.
    처리 규칙: test_ 메서드별 조건을 검증하며 필요한 setUp/정리는 각 메서드에 기술한다.
    예외/부작용: AssertionError는 테스트 실패, skipTest는 샘플 미준비로 처리한다.
    """
    def run_compare(self, body='청구 합계는 1,650,000원입니다.', text=INVOICE, contract=CONTRACT):
        """고정 계약/메일과 모의 PDF 텍스트로 비교 엔진을 실행한다.
        
        입력: body: 메일 본문 기본값. text: 계산서 텍스트 기본값. contract: 계약 텍스트 기본값.
        반환: compare_mail 결과 dict.
        처리 규칙: 다운로드 함수를 두 번의 고정 반환으로 patch하고 비교한다.
        예외/부작용: 실제 다운로드는 수행하지 않는다. 엔진의 예외/판정은 테스트로 전달한다.
        """
        mail = dict(id='M', subject='계산서', received_at='2026-10-15', body=body,
                    attachments=[dict(name='invoice.pdf', url='invoice.pdf')])
        contracts = [dict(id='C', kind='지출', partner='별빛보안(주)', partner_biz_no='000-81-00032', file={})]
        with patch('compare_core.download', side_effect=[(Path('invoice.pdf'), text), (Path('contract.pdf'), contract)]):
            return compare_mail(mail, contracts)

    def test_matching_month(self):
        """월 금액과 기간이 맞으면 최종 일치가 되는지 검증한다.
        
        입력: self 및 테스트 내부의 고정 데이터/mock/샘플.
        반환: None. 조건이 맞지 않으면 테스트 실패.
        처리 규칙: 함수 본문의 assert 계열로 해당 회귀 조건을 확인한다.
        예외/부작용: 실패는 AssertionError. 임시 저장소/mock 또는 읽기 전용 샘플을 사용하며 운영 서버를 제어하지 않는다.
        """
        self.assertEqual(self.run_compare()['status'], '일치')

    def test_mail_discrepancy_not_hidden_by_matching_contract(self):
        """계약금액이 맞더라도 메일/PDF 합계 차이가 불일치로 남는지 검증한다.
        
        입력: self 및 테스트 내부의 고정 데이터/mock/샘플.
        반환: None. 조건이 맞지 않으면 테스트 실패.
        처리 규칙: 함수 본문의 assert 계열로 해당 회귀 조건을 확인한다.
        예외/부작용: 실패는 AssertionError. 임시 저장소/mock 또는 읽기 전용 샘플을 사용하며 운영 서버를 제어하지 않는다.
        """
        result = self.run_compare(body='청구 합계는 1,815,000원입니다.')
        self.assertEqual(result['status'], '불일치')
        self.assertTrue(any(c['item'] == '메일과 PDF 합계' and c['status'] == '불일치' for c in result['checks']))

    def test_scanned_invoice_never_matches(self):
        """텍스트가 없는 계산서를 일치로 처리하지 않는지 검증한다.
        
        입력: self 및 테스트 내부의 고정 데이터/mock/샘플.
        반환: None. 조건이 맞지 않으면 테스트 실패.
        처리 규칙: 함수 본문의 assert 계열로 해당 회귀 조건을 확인한다.
        예외/부작용: 실패는 AssertionError. 임시 저장소/mock 또는 읽기 전용 샘플을 사용하며 운영 서버를 제어하지 않는다.
        """
        self.assertEqual(self.run_compare(text='')['status'], '확인 필요')

    def test_outside_contract_period(self):
        """청구월이 계약기간 밖이면 확인 필요가 되는지 검증한다.
        
        입력: self 및 테스트 내부의 고정 데이터/mock/샘플.
        반환: None. 조건이 맞지 않으면 테스트 실패.
        처리 규칙: 함수 본문의 assert 계열로 해당 회귀 조건을 확인한다.
        예외/부작용: 실패는 AssertionError. 임시 저장소/mock 또는 읽기 전용 샘플을 사용하며 운영 서버를 제어하지 않는다.
        """
        self.assertEqual(self.run_compare(contract=CONTRACT.replace('[12]월', '[08]월'))['status'], '확인 필요')

    def test_installment_requires_condition_review(self):
        """분할 금액이 맞아도 공급/검수 조건 확인을 남기는지 검증한다.
        
        입력: self 및 테스트 내부의 고정 데이터/mock/샘플.
        반환: None. 조건이 맞지 않으면 테스트 실패.
        처리 규칙: 함수 본문의 assert 계열로 해당 회귀 조건을 확인한다.
        예외/부작용: 실패는 AssertionError. 임시 저장소/mock 또는 읽기 전용 샘플을 사용하며 운영 서버를 제어하지 않는다.
        """
        contract = CONTRACT.replace('월 금 1,500,000', '총 금 5,000,000') + '\n1차분 : 계약금액의 30%인 금 1,500,000원(부가가치세 별도)'
        self.assertEqual(self.run_compare(contract=contract)['status'], '확인 필요')

    def test_tax_included(self):
        """부가세 포함 계약에 세액을 중복 가산하지 않는지 검증한다.
        
        입력: self 및 테스트 내부의 고정 데이터/mock/샘플.
        반환: None. 조건이 맞지 않으면 테스트 실패.
        처리 규칙: 함수 본문의 assert 계열로 해당 회귀 조건을 확인한다.
        예외/부작용: 실패는 AssertionError. 임시 저장소/mock 또는 읽기 전용 샘플을 사용하며 운영 서버를 제어하지 않는다.
        """
        contract = CONTRACT.replace('1,500,000원(부가가치세 별도)', '1,650,000원(부가가치세 포함)')
        self.assertEqual(self.run_compare(contract=contract)['status'], '일치')


if __name__ == '__main__':
    unittest.main()
