"""공개 사이트 자료 수집과 로컬 PDF 값 추출·비교의 핵심 모듈.

기능명세:
- pypdf로 텍스트를 추출하고 정규식으로 현재 새봄 양식의 필드를 찾는다.
- 금액/기간/계약 연결을 판정하고 locate.find_boxes로 근거 좌표를 붙인다.
- 웹 UI 서버 및 이전 Tkinter UI에서 import하여 사용한다. 자체 실행 진입점은 없다.
- fetch/load_catalog/온라인 download만 외부 사이트를 조회한다. 오프라인 비교는 저장 원본을 사용한다.
- ROOT는 SBGW_DATA_DIR 우선, EXE는 LOCALAPPDATA/SaebomCompare, 소스 실행은 프로젝트 폴더다.
- 현재 양식의 텍스트 순서/문장 형식에 의존하며 OCR이나 AI 의미 해석은 하지 않는다.
"""
from __future__ import annotations

import calendar
import csv
import hashlib
import io
import json
import re
import os
import sys
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote, urljoin, urlsplit
from urllib.request import Request, urlopen

from pypdf import PdfReader

from locate import find_boxes

BASE = 'https://sbgw.vercel.app/'
ROOT = Path(os.environ.get('SBGW_DATA_DIR') or (
    str(Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'SaebomCompare')
    if getattr(sys, 'frozen', False) else str(Path(__file__).resolve().parent)))
VERSION = '2.0'


def data_roots():
    """기존 다운로드를 검색할 저장 루트의 우선순위를 만든다.
    
    입력: 없음. ROOT, LOCALAPPDATA, EXE/소스 위치와 현재 디렉터리를 참조한다.
    반환: Path 목록. 기본 ROOT가 첫 항목이며 중복은 제거한다.
    처리 규칙: 기존 downloads 폴더가 있는 후보만 추가한다. EXE 실행 시 EXE 폴더와 상위 폴더도 후보에 넣는다.
    예외/부작용: 폴더 존재 여부만 확인한다. 파일 생성/이동과 네트워크 요청은 없다.
    """
    candidates = [ROOT, Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'SaebomCompare']
    if getattr(sys, 'frozen', False):
        exe = Path(sys.executable).resolve().parent
        candidates += [exe, exe.parent]
    else:
        candidates.append(Path(__file__).resolve().parent)
    candidates.append(Path.cwd())
    roots = []
    for root in candidates:
        root = Path(root)
        if root not in roots and (root / 'downloads').is_dir():
            roots.append(root)
    return [ROOT] + [r for r in roots if r != ROOT]


def fetch(url):
    """지원 사이트에서 응답 바이트를 가져온다.
    
    입력: url: HTTPS URL 문자열.
    반환: 응답 바이트. 최대 25MiB를 읽는다.
    처리 규칙: BASE와 같은 netloc 및 https만 허용한다. URL을 인코딩하고 User-Agent와 30초 타임아웃을 설정한다.
    예외/부작용: 외부 네트워크 GET이 발생한다. 허용 범위 밖 URL은 ValueError, HTTP/통신 오류는 전달한다. 파일은 쓰지 않는다.
    """
    if urlsplit(url).netloc != urlsplit(BASE).netloc or urlsplit(url).scheme != 'https':
        raise ValueError('지원 사이트 밖의 다운로드 주소입니다.')
    request = Request(quote(url, safe=':/?=&%'), headers={'User-Agent': 'SaebomLocalCompare/1.0'})
    with urlopen(request, timeout=30) as response:
        return response.read(25 * 1024 * 1024)


def catalog_path(expanded=True):
    """기본/확장 목록 캐시 경로를 결정한다.
    
    입력: expanded=True: 확장 목록 여부.
    반환: ROOT/downloads/catalog_expanded.json 또는 catalog_basic.json의 Path.
    처리 규칙: 파일명만 선택하며 실제 존재 여부를 검사하지 않는다.
    예외/부작용: 파일/폴더를 만들거나 네트워크에 접속하지 않는다.
    """
    return ROOT / 'downloads' / ('catalog_expanded.json' if expanded else 'catalog_basic.json')


def read_catalog(expanded=True):
    """로컬에 저장한 목록 JSON을 읽는다.
    
    입력: expanded=True: 읽을 목록 구분.
    반환: 메일/계약과 조회 시각이 포함된 dict. _source는 로컬 저장 목록으로 설정한다.
    처리 규칙: catalog_path의 UTF-8 JSON을 읽는다. 다운로드 원본의 존재 여부는 별도 검사한다.
    예외/부작용: 목록이 없으면 FileNotFoundError. JSON 파싱/읽기 오류는 전달하며 네트워크나 파일 쓰기는 없다.
    """
    path = catalog_path(expanded)
    if not path.exists():
        raise FileNotFoundError('저장된 목록이 없습니다. 인터넷 연결 후 최신 목록 가져오기를 누르세요. 로컬 PDF 직접 비교도 가능합니다.')
    data = json.loads(path.read_text(encoding='utf-8'))
    data['_source'] = '로컬 저장 목록'
    return data


def load_catalog(expanded=True):
    """사이트의 최신 목록을 가져와 로컬 캐시를 갱신한다.
    
    입력: expanded=True: cases.json의 추가 메일/계약 포함 여부.
    반환: 수신시각 내림차순 mails와 contracts 및 _synced_at/_source를 담은 dict.
    처리 규칙: seed.json 조회 후 필요 시 cases.json을 병합한다. .part 파일 작성 후 replace로 캐시를 교체한다.
    예외/부작용: 외부 GET, downloads 폴더/캐시 생성과 교체가 발생한다. 조회/JSON/저장 오류는 호출자에게 전달한다.
    """
    data = json.loads(fetch(BASE + 'data/seed.json'))
    if expanded:
        extra = json.loads(fetch(BASE + 'data/cases.json'))
        for key in ('mails', 'contracts'):
            data[key].extend(extra[key])
    # 콜백/정규화 함수: 아래 lambda는 해당 표현식의 인자를 받아 즉시 변환하거나 상위 함수의 작업을 위임한다.
    data['mails'].sort(key=lambda m: m['received_at'], reverse=True)
    data['_synced_at'] = datetime.now().isoformat(timespec='seconds')
    data['_source'] = '사이트 최신 조회'
    path = catalog_path(expanded)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.part')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)
    return data


def cached_path(item, category):
    """다운로드 항목의 기존 파일 또는 기본 저장 경로를 찾는다.
    
    입력: item: name/url을 갖는 dict. category: invoices 또는 contracts 등 하위 폴더명.
    반환: 기존 파일이 발견된 Path, 없으면 ROOT 아래의 예정 경로.
    처리 규칙: 금지 파일명 문자를 치환하고 URL의 SHA-256 앞 10자를 이름에 붙인다. data_roots 순서로 검색한다.
    예외/부작용: 파일 존재 여부만 검사한다. 해시는 URL 구분용이며 파일 내용의 무결성 검사는 아니다. 네트워크/파일 쓰기는 없다.
    """
    url = urljoin(BASE, item['url'])
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', item['name'])
    filename = hashlib.sha256(url.encode()).hexdigest()[:10] + '_' + name
    for root in data_roots():
        candidate = root / 'downloads' / category / filename
        if candidate.is_file():
            return candidate
    return ROOT / 'downloads' / category / filename


def read_pdf(path):
    """PDF 원본에서 전체 페이지의 텍스트를 추출한다.
    
    입력:
        path: 읽을 로컬 PDF 경로(str 또는 Path).
    반환:
        (Path, str): 원본 경로와 페이지별 텍스트를 줄바꿈으로 연결한 문자열.
    처리:
        파일 헤더가 %PDF-인지 확인하고 pypdf.PdfReader.extract_text()를 적용한다.
        텍스트가 없는 페이지는 빈 문자열로 처리한다. OCR은 수행하지 않는다.
    예외/부작용:
        파일 읽기 실패, 잘못된 PDF, PDF 파싱 오류를 호출자에게 전달한다.
        로컬 파일 읽기만 수행하며 네트워크 요청과 원본 수정은 없다.
    """
    path = Path(path)
    content = path.read_bytes()
    if not content.startswith(b'%PDF-'):
        raise ValueError(f'PDF 파일이 아닙니다: {path.name}')
    text = '\n'.join(page.extract_text() or '' for page in PdfReader(io.BytesIO(content)).pages)
    return path, text


def storage_status(mail, contracts):
    """메일 세금계산서와 연결 계약서의 저장 상태를 각각 계산한다.
    
    입력: mail: from_name/attachments를 갖는 메일 dict. contracts: 계약 목록.
    반환: (invoice_state, contract_state) dict 튜플. 계약 상태에는 id/basis/detail이 포함된다.
    처리 규칙: PDF 저장 후에는 첫 공급자 번호로 연결한다. 저장 전에는 보낸 사람/첨부명에서 회사명을 정규화해 잠정 연결한다. 후보가 1개일 때만 계약 저장 여부를 확정한다.
    예외/부작용: 로컬 파일 존재 확인 및 저장 PDF 읽기만 수행한다. PDF 읽기/번호 추출 실패는 연결 확인 필요로 변환한다. 저장됨은 금액 일치를 의미하지 않는다.
    """
    pdfs = [a for a in mail['attachments'] if a['name'].lower().endswith('.pdf')]
    saved = [cached_path(a, 'invoices') for a in pdfs if cached_path(a, 'invoices').is_file()]
    invoice_state = ('PDF 없음' if not pdfs else '미저장' if not saved else
                     '저장됨' if len(saved) == len(pdfs) else f'일부 저장 ({len(saved)}/{len(pdfs)})')
    invoice = {'status': invoice_state, 'saved': len(saved), 'total': len(pdfs)}
    expenses = [c for c in contracts if c['kind'] == '지출']
    candidates = []
    basis = '메일 정보 기준'
    if len(pdfs) == 1 and saved:
        try:
            parsed = parse_invoice(read_pdf(saved[0])[1])
        except Exception:
            return invoice, {'status': '연결 확인 필요', 'detail': '세금계산서 PDF를 읽지 못했습니다.', 'id': '', 'basis': ''}
        if not parsed['biz_no']:
            return invoice, {'status': '연결 확인 필요', 'detail': 'PDF 공급자 사업자번호를 추출하지 못했습니다.', 'id': '', 'basis': ''}
        candidates = [c for c in expenses if c['partner_biz_no'] == parsed['biz_no']]
        basis = 'PDF 사업자번호 기준'
    else:
        # 원본 저장 전에는 보낸 사람/첨부명으로 잠정 연결. 본문에는 다른 거래처가 언급될 수 있어 제외.
        # 콜백/정규화 함수: 아래 lambda는 해당 표현식의 인자를 받아 즉시 변환하거나 상위 함수의 작업을 위임한다.
        normalize = lambda s: re.sub(r'[^가-힣a-zA-Z0-9]', '', re.sub(r'주식회사|\(주\)', '', s))
        hints = [normalize(mail.get('from_name', ''))] + [normalize(a['name']) for a in mail['attachments']]
        candidates = [c for c in expenses if normalize(c['partner']) and any(normalize(c['partner']) in hint for hint in hints)]
    if len(candidates) != 1:
        status = '연결 확인 필요' if candidates else '연결 대상 없음'
        return invoice, {'status': status, 'detail': f'{basis}: 후보 {len(candidates)}개. 계약 저장 여부를 확정할 수 없습니다.', 'id': '', 'basis': basis}
    contract = candidates[0]
    exists = cached_path(contract['file'], 'contracts').is_file()
    return invoice, {'status': '저장됨' if exists else '미저장', 'id': contract['id'], 'basis': basis,
                     'detail': f'{contract["id"]} · {contract["partner"]} · {basis}. 파일 저장 여부이며 비교 판정은 아닙니다.'}


def download(item, category, offline=False, refresh=False):
    """캐시 PDF를 읽거나 온라인 원본을 내려받아 저장한다.
    
    입력: item: name/url. category: 저장 구분. offline=False: 네트워크 금지 여부. refresh=False: 기존 파일 강제 갱신 여부.
    반환: (Path, 추출 텍스트) 튜플.
    처리 규칙: 캐시가 있고 refresh가 아니면 즉시 읽는다. 오프라인 누락은 실패한다. 온라인 응답의 PDF 헤더와 텍스트 추출 성공을 확인한 뒤 .part 교체 및 .txt 저장을 한다.
    예외/부작용: 온라인 분기만 GET을 수행한다. 원본/TXT 작성이 발생하며 누락/HTTP/PDF/쓰기 오류는 전달한다. OCR은 수행하지 않는다.
    """
    path = cached_path(item, category)
    if path.exists() and not refresh:
        return read_pdf(path)
    if offline:
        raise FileNotFoundError(f'오프라인 원본 없음: {item["name"]}. 연결 시 선택 자료 저장을 먼저 실행하세요.')
    url = urljoin(BASE, item['url'])
    content = fetch(url)
    if not content.startswith(b'%PDF-'):
        raise ValueError(f'PDF가 아닌 응답: {item["name"]}')
    # 추출까지 성공한 파일만 교체
    text = '\n'.join(page.extract_text() or '' for page in PdfReader(io.BytesIO(content)).pages)
    path.parent.mkdir(parents=True, exist_ok=True)
    # 덮어쓰기 중단 시에도 기존 원본 보존
    temporary = path.with_suffix('.part')
    temporary.write_bytes(content)
    temporary.replace(path)
    path.with_suffix('.txt').write_text(text, encoding='utf-8')
    return path, text


def prepare_files(mails, contracts, progress=None):
    """선택 메일의 PDF와 지출 계약 원본을 오프라인용으로 갱신한다.
    
    입력: mails: 선택 메일 목록. contracts: 전체 계약 목록. progress=None: 진행 문구 콜백.
    반환: (저장 성공 파일 수, 개별 실패 문자열 목록).
    처리 규칙: 선택 메일 PDF 및 모든 지출 계약을 (category, URL)로 중복 제거하고 refresh=True로 순차 저장한다.
    예외/부작용: 네트워크/파일 저장이 발생한다. 각 download 예외는 수집하고 다음 파일을 계속 처리한다. progress 콜백 예외는 전달될 수 있다.
    """
    items = [(a, 'invoices') for m in mails for a in m['attachments'] if a['name'].lower().endswith('.pdf')]
    items += [(c['file'], 'contracts') for c in contracts if c['kind'] == '지출']
    unique = {(category, item['url']): (item, category) for item, category in items}
    errors = []
    for i, (item, category) in enumerate(unique.values(), 1):
        if progress:
            progress(f'{i}/{len(unique)} 저장 중: {item["name"]}')
        try:
            download(item, category, refresh=True)
        except Exception as exc:
            errors.append(f'{item["name"]}: {exc}')
    return len(unique) - len(errors), errors


def money(value):
    """쉼표가 포함된 금액 문자열을 정수로 바꾼다.
    
    입력: value: 예를 들어 1,650,000 형태의 문자열.
    반환: 쉼표가 제거된 int.
    처리 규칙: replace로 쉼표를 없앤 뒤 int를 적용한다.
    예외/부작용: 숫자 형식이 아니면 ValueError, 문자열이 아니면 타입 관련 오류를 전달한다. 외부 부작용은 없다.
    """
    return int(value.replace(',', ''))


def parse_invoice(text):
    """세금계산서 텍스트에서 고정 양식의 필드 여섯 개를 추출한다.
    
    입력:
        text: read_pdf() 등으로 추출한 전체 PDF 텍스트.
    반환:
        dict: biz_no, partner, date, supply, vat, total.
        금액은 쉼표를 제거한 정수이며 추출하지 못한 필드는 None이다.
    처리:
        첫 사업자번호를 공급자로 간주하고, 상호~성명 사이에서 거래처를 찾는다.
        작성일자/공급가액/세액 제목 다음의 값과 합계 영역 아래 숫자를 읽는다.
    가정/한계:
        현재 사이트의 텍스트 순서와 제목에 의존한다. 공급받는자 번호가 먼저
        나타나는 다른 양식은 오인할 수 있다. 세액 비율 검증은 이 함수의 역할이 아니다.
    예외/부작용:
        매칭 실패는 None으로 표현한다. 파일/네트워크 접근과 입력 변경은 없다.
    """
    result = {'biz_no': None, 'partner': None, 'date': None, 'supply': None, 'vat': None, 'total': None}
    ids = re.findall(r'\b\d{3}-\d{2}-\d{5}\b', text)
    if ids:
        result['biz_no'] = ids[0]
    partner = re.search(r'상호\*?\s+(.+?)\s+성명', text)
    if partner:
        result['partner'] = partner[1].strip()
    values = re.search(r'작성일자\*?\s+공급가액\*?\s+세액\*?\s+(\d{4}-\d{2}-\d{2})\s+([\d,]+)\s+([\d,]+)', text)
    if values:
        result.update(date=values[1], supply=money(values[2]), vat=money(values[3]))
    total = re.search(r'합계\s+현금\s+수표\s+어음\s+외상미수금.*?\n([\d,]+)\s', text, re.S)
    if total:
        result['total'] = money(total[1])
    return result


def parse_contract(text):
    """계약서의 정해진 문장 패턴에서 금액·기간·사업자번호·분할 조건을 찾는다.
    
    입력:
        text: 계약 PDF에서 추출한 텍스트.
    반환:
        dict: amounts[(월/총, 금액문자열, 포함/별도)], period[날짜문자열],
        biz_numbers[사업자번호], installments[(차수, 비율, 금액문자열, 세금조건)].
    처리:
        월/총 금 ...원(부가가치세 포함/별도)과 차수별 지급 문장을 정규식으로 검색한다.
        동일 금액조건은 중복 제거하며 대괄호 연월일의 첫 두 항목을 기간으로 사용한다.
    가정/한계:
        자유로운 계약 문장의 의미를 해석하지 않는다. 다른 날짜가 먼저 등장하면
        계약기간으로 오인할 수 있으며, 복수 금액은 호출자가 확인 필요로 판정한다.
    예외/부작용:
        없는 조건은 빈 목록으로 반환한다. 파일/네트워크 접근은 없다.
    """
    amounts = re.findall(r'(월|총)\s*금\s*([\d,]+)원\s*\(부가가치세\s*(포함|별도)\)', text)
    amounts = list(dict.fromkeys(amounts))
    dates = re.findall(r'\[(\d{4})\]년\s*\[(\d{2})\]월\s*\[(\d{2})\]', text)
    installments = re.findall(r'(\d+)차분\s*:\s*계약금액의\s*(\d+)%인\s*금\s*([\d,]+)원\s*\(부가가치세\s*(포함|별도)\)', text)
    return {'amounts': amounts, 'period': ['-'.join(d) for d in dates[:2]],
            'biz_numbers': list(dict.fromkeys(re.findall(r'\b\d{3}-\d{2}-\d{5}\b', text))),
            'installments': installments}


def gross(amount, tax_mode):
    """계약금액에 명시된 부가세 조건을 반영해 예상 합계를 구한다.
    
    입력: amount: 정수 금액. tax_mode: 포함 또는 별도 문자열.
    반환: 예상 합계 int.
    처리 규칙: 포함이면 amount, 그 외에는 amount + amount // 10을 반환한다. 정수 나눗셈이므로 원 미만은 버린다.
    예외/부작용: 파일/네트워크 변경은 없다. 세율은 10%로 고정되어 있으며 면세/다른 세율/별도 반올림 정책은 지원하지 않는다.
    """
    return amount if tax_mode == '포함' else amount + amount // 10


def attach_locations(result):
    """비교 항목에 원본 PDF의 근거 좌표를 붙인다.
    
    입력: result: invoice/contract/checks 및 원본 경로를 갖는 비교 결과 dict.
    반환: where 정보가 추가된 동일 result 객체.
    처리 규칙: 금액은 쉼표 형태로 바꿔 항목별 검색값을 정하고 두 PDF에서 find_boxes를 호출한다. 같은 경로/값 묶음은 이 호출 안에서 캐시한다.
    예외/부작용: 입력 result의 checks를 변경하고 로컬 PDF를 읽는다. 지원하지 않는 항목에는 where를 추가하지 않는다. 검색 결과 없음은 빈 목록이며 OCR은 없다.
    """
    invoice, contract = result.get('invoice') or {}, result.get('contract') or {}
    # 콜백/정규화 함수: 아래 lambda는 해당 표현식의 인자를 받아 즉시 변환하거나 상위 함수의 작업을 위임한다.
    fmt = lambda v: f'{v:,}' if isinstance(v, int) else None
    total, biz = fmt(invoice.get('total')), invoice.get('biz_no')
    amount = [a[1] for a in contract.get('amounts', [])[:1]]
    installments = [i[2] for i in contract.get('installments', [])]
    wanted = {
        '계산서 합계': ([fmt(invoice.get('supply')), fmt(invoice.get('vat')), total], []),
        '메일과 PDF 합계': ([total], []),
        '사업자번호': ([biz], [biz]),
        '월 계약금액': ([total], amount),
        '계약금액': ([total], amount),
        '분할 지급': ([total], installments),
        '분할 금액': ([total], installments),
    }
    cache = {}

    def boxes(path, values):
        """같은 파일과 검색값의 좌표 검색을 중복 수행하지 않는다.
        
        입력: path: PDF 경로. values: 검색 문자열 목록.
        반환: find_boxes 결과 목록 또는 빈 목록.
        처리 규칙: 외부 함수의 cache를 (경로, 검색값 튜플) 키로 조회하고 필요 시 검색한다. 빈 값은 검색에서 제외한다.
        예외/부작용: 클로저 cache를 변경하고 필요 시 로컬 PDF를 읽는다. find_boxes의 검색 예외는 호출자에게 전달한다.
        """
        key = (path, tuple(values))
        if path and values and key not in cache:
            cache[key] = find_boxes(path, [v for v in values if v])
        return cache.get(key, [])

    for item in result['checks']:
        if item['item'] in wanted:
            invoice_values, contract_values = wanted[item['item']]
            item['where'] = {'invoice': boxes(result['invoice_path'], invoice_values),
                             'contract': boxes(result['contract_path'], contract_values)}
    return result


def compare_mail(mail, contracts, offline=False, loader=None):
    # 콜백/정규화 함수: 아래 lambda는 해당 표현식의 인자를 받아 즉시 변환하거나 상위 함수의 작업을 위임한다.
    """메일 세금계산서와 유일한 지출 계약을 연결해 항목별 판정한다.
    
    입력: mail: 메일 dict. contracts: 계약 목록. offline=False. loader=None: (항목, 구분)→(Path, 텍스트) 대체 로더.
    반환: status/checks/원본경로/추출값/계약번호를 갖는 결과 dict. 정상 끝 경로에서는 근거 좌표도 포함한다.
    처리 규칙: 첨부 PDF 1개만 허용한다. 공급자 번호로 지출 계약을 연결하고 합계/본문금액/번호/상호/청구월/계약금액을 검사한다. 최종 우선순위는 불일치→확인 필요→일치. 분할 지급은 조건 이행 수동 확인을 남긴다.
    예외/부작용: 로더에 따라 다운로드 또는 로컬 읽기가 발생한다. 로더 오류는 전달한다. PDF 개수/계약 후보 오류로 중간 반환할 때는 기본 확인 필요 상태이며 최종 집계/좌표 부착을 건너뛴다. 별도의 세액 비율 검사는 없다.
    """
    load = loader or (lambda item, category: download(item, category, offline=offline))
    result = {'mail_id': mail['id'], 'subject': mail['subject'], 'received_at': mail['received_at'],
              'status': '확인 필요', 'checks': [], 'invoice_path': '', 'contract_path': '', 'contract_id': ''}

    def check(label, state, detail):
        """하나의 비교 항목을 현재 결과에 추가한다.
        
        입력: label: 항목명. state: 일치/불일치/확인 필요. detail: 근거 설명.
        반환: None.
        처리 규칙: 외부 result의 checks에 item/status/detail dict를 append한다.
        예외/부작용: 현재 비교 결과 객체만 변경한다. 파일/네트워크 접근은 없다.
        """
        result['checks'].append({'item': label, 'status': state, 'detail': detail})

    attachments = [a for a in mail['attachments'] if a['name'].lower().endswith('.pdf')]
    if len(attachments) != 1:
        check('세금계산서 PDF', '확인 필요', f'첨부 PDF {len(attachments)}개. 한 건씩 확인이 필요합니다.')
        return result
    path, text = load(attachments[0], 'invoices')
    result['invoice_path'] = str(path)
    invoice = parse_invoice(text)
    invoice_text = text
    result['invoice'] = invoice
    missing = [k for k, v in invoice.items() if v is None]
    if missing:
        check('PDF 추출', '확인 필요', '추출하지 못한 항목: ' + ', '.join(missing) + '. 스캔 PDF는 OCR이 필요합니다.')
    if invoice['supply'] is not None and invoice['vat'] is not None and invoice['total'] is not None:
        valid = invoice['supply'] + invoice['vat'] == invoice['total']
        check('계산서 합계', '일치' if valid else '불일치',
              f"공급가액 {invoice['supply']:,} + 세액 {invoice['vat']:,} / 합계 {invoice['total']:,}원")
    body_total = re.search(r'(?:청구\s*합계|합계(?:금액)?)\s*(?:는|:)?\s*([\d,]+)원', mail['body'])
    if body_total and invoice['total'] is not None:
        value = money(body_total[1])
        check('메일과 PDF 합계', '일치' if value == invoice['total'] else '불일치',
              f"메일 {value:,}원 / PDF {invoice['total']:,}원")
    candidates = [c for c in contracts if c['kind'] == '지출' and invoice['biz_no'] and c['partner_biz_no'] == invoice['biz_no']]
    if len(candidates) != 1:
        check('계약 연결', '확인 필요', f'공급자 사업자번호 기준 후보 {len(candidates)}개. 자동 확정하지 않았습니다.')
        return result
    contract = candidates[0]
    result.update(contract_id=contract['id'], partner=contract['partner'])
    path, text = load(contract['file'], 'contracts')
    result['contract_path'] = str(path)
    parsed = parse_contract(text)
    result['contract'] = parsed
    identity_ok = invoice['biz_no'] in parsed['biz_numbers']
    check('사업자번호', '일치' if identity_ok else '확인 필요',
          f"계산서 공급자 {invoice['biz_no']} / 계약 PDF 번호 {', '.join(parsed['biz_numbers']) or '추출 실패'}")
    # 콜백/정규화 함수: 아래 lambda는 해당 표현식의 인자를 받아 즉시 변환하거나 상위 함수의 작업을 위임한다.
    normalized = lambda s: re.sub(r'주식회사|\(주\)|\s', '', s or '')
    if invoice['partner']:
        same = normalized(invoice['partner']) == normalized(contract['partner'])
        check('거래처', '일치' if same else '불일치', f"PDF {invoice['partner']} / 계약목록 {contract['partner']}")
    if len(parsed['period']) == 2 and invoice['date']:
        try:
            # 발행일 대신 청구 대상 월의 전체 기간이 계약에 포함되는지 확인
            billing = re.search(r'(\d{4})년\s*(\d{1,2})월분', invoice_text)
            if billing:
                year, month = map(int, billing.groups())
                start, end = date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])
                valid = date.fromisoformat(parsed['period'][0]) <= start and end <= date.fromisoformat(parsed['period'][1])
                check('청구월과 계약기간', '일치' if valid else '확인 필요', f'{start} ~ {end} / 계약 {parsed["period"][0]} ~ {parsed["period"][1]}')
            else:
                check('청구월', '확인 필요', 'PDF에서 청구 대상 월을 확정하지 못했습니다.')
        except ValueError:
            check('계약기간', '확인 필요', '날짜 형식을 확인하세요.')
    else:
        check('계약기간', '확인 필요', 'PDF에서 기간을 추출하지 못했습니다.')
    if len(parsed['amounts']) != 1 or invoice['total'] is None:
        check('계약금액', '확인 필요', '유일한 계약금액 또는 계산서 합계를 확정할 수 없습니다.')
    else:
        basis, amount, tax = parsed['amounts'][0]
        expected = gross(money(amount), tax)
        if parsed['installments']:
            matched = [(part, percent, value, mode) for part, percent, value, mode in parsed['installments'] if gross(money(value), mode) == invoice['total']]
            check('분할 지급', '확인 필요',
                  ('금액상 ' + ', '.join(p[0] + '차분(' + p[1] + '%)' for p in matched) + '과 일치. 공급/검수 조건 이행 확인 필요.') if matched else '분할 지급 금액과 불일치. 계약 조항을 확인하세요.')
            if not matched:
                check('분할 금액', '불일치', f"계산서 {invoice['total']:,}원")
        elif basis != '월':
            check('계약금액', '확인 필요', f'총액 계약 {expected:,}원. 청구 조건을 수동 확인하세요.')
        else:
            check('월 계약금액', '일치' if expected == invoice['total'] else '불일치',
                  f"계약 {money(amount):,}원(VAT {tax}) → 예상 합계 {expected:,}원 / 계산서 {invoice['total']:,}원 / 차이 {invoice['total'] - expected:+,}원")
    states = [c['status'] for c in result['checks']]
    result['status'] = '불일치' if '불일치' in states else '확인 필요' if '확인 필요' in states else '일치'
    return attach_locations(result)


def compare_local_files(invoice_path, contract_path):
    """사용자가 직접 지정한 로컬 PDF 두 개를 비교한다.
    
    입력: invoice_path: 계산서 경로. contract_path: 계약서 경로.
    반환: compare_mail 형식의 비교 결과 dict.
    처리 규칙: 두 파일을 읽고 사용자 지정 계약/메일을 임시 생성한다. 메모리 로더로 compare_mail을 재사용하고 인위적인 상호 비교 항목은 제거한다. 계약 지정 수동 확인을 추가한다.
    예외/부작용: 로컬 PDF 읽기만 수행하며 네트워크 요청은 없다. 직접 지정 계약은 금액이 맞아도 불일치가 아닌 한 확인 필요다. 파일/파싱 오류는 전달한다.
    """
    invoice_path, invoice_text = read_pdf(invoice_path)
    contract_path, contract_text = read_pdf(contract_path)
    invoice = parse_invoice(invoice_text)
    mail = dict(id='LOCAL-' + datetime.now().strftime('%Y%m%d%H%M%S%f'),
                subject=f'{invoice_path.name} ↔ {contract_path.name}', received_at='', body='',
                attachments=[dict(name=invoice_path.name, url='local.pdf')])
    contract = dict(id='사용자 지정 계약', kind='지출', partner=invoice['partner'] or '',
                    partner_biz_no=invoice['biz_no'], file={})
    texts = {'invoices': (invoice_path, invoice_text), 'contracts': (contract_path, contract_text)}
    # 콜백/정규화 함수: 아래 lambda는 해당 표현식의 인자를 받아 즉시 변환하거나 상위 함수의 작업을 위임한다.
    result = compare_mail(mail, [contract], offline=True, loader=lambda item, category: texts[category])
    # 직접 지정 시 목록상의 거래처와 비교한 것처럼 표시하지 않음
    result['checks'] = [c for c in result['checks'] if c['item'] != '거래처']
    result['checks'].append(dict(item='계약 지정 방식', status='확인 필요',
                                detail='사용자가 지정한 PDF입니다. 거래처 상호와 서비스 대상이 맞는지 원본에서 확인하세요.'))
    if result['status'] != '불일치':
        result['status'] = '확인 필요'
    return result


def save_reports(results):
    """이번 비교 결과를 JSON과 CSV로 보관한다.
    
    입력: results: 비교 결과 dict 목록.
    반환: 생성한 reports/실행시각 폴더의 Path.
    처리 규칙: 마이크로초 포함 시각으로 폴더를 만들고 JSON 전체 구조 및 항목별 CSV 행을 저장한다. CSV는 UTF-8 BOM과 newline 설정을 사용한다.
    예외/부작용: 폴더와 두 보고서를 생성한다. 저장/JSON 직렬화/필수 키 오류는 전달한다. history.json 갱신은 web_app.store_results의 역할이다.
    """
    folder = ROOT / 'reports' / datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    folder.mkdir(parents=True)
    (folder / 'results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    with (folder / 'results.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(['메일', '제목', '거래처', '계약번호', '결과', '대조항목', '항목결과', '근거', '세금계산서', '계약서'])
        for r in results:
            for c in r['checks']:
                writer.writerow([r['mail_id'], r['subject'], r.get('partner', ''), r['contract_id'], r['status'], c['item'], c['status'], c['detail'], r['invoice_path'], r['contract_path']])
    return folder
