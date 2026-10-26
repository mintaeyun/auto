"""Windows EXE와 소스 실행에서 사용하는 로컬 웹 UI 서버.

기능명세:
- 실행: py web_app.py [--port 18765] [--no-browser]. 기본 바인딩은 127.0.0.1이다.
- web/index.html 제공, 목록/저장/비교 작업 접수, 이력/파일/페이지 이미지 제공을 담당한다.
- compare_core에 업무 규칙을 위임하고 locate.render_page로 PDF 페이지를 렌더링한다.
- 긴 작업은 별도 데몬 스레드에서 한 번에 하나만 실행하며 LOCK/JOBS/BUSY로 진행 상태를 공유한다.
- POST는 Host/Origin 및 매 실행 새로 생성한 TOKEN을 검사한다. 서버 재시작 후 UI 새로고침이 필요하다.
- ASSETS는 EXE 임시 리소스 폴더 또는 소스 폴더이고 사용자 자료는 compare_core.ROOT에 저장한다.
- 이 모듈은 Playwright Chrome 제어를 자동으로 호출하지 않는다.
"""
from __future__ import annotations

import argparse
import base64
import json
import logging
import mimetypes
import secrets
import sys
import threading
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from locate import render_page
from compare_core import (ROOT, VERSION, cached_path, data_roots, compare_local_files, compare_mail,
                          load_catalog, prepare_files, read_catalog, save_reports, storage_status)

ASSETS = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent)) / 'web'
TOKEN = secrets.token_urlsafe(32)
JOBS = {}
LOCK = threading.Lock()
BUSY = False


def history():
    """누적 비교 이력을 읽는다.
    
    입력: 없음. ROOT/reports/history.json을 참조한다.
    반환: 메일 ID를 키로 하는 dict. 파일이 없으면 빈 dict.
    처리 규칙: UTF-8 JSON을 로드한다.
    예외/부작용: 로컬 읽기만 수행한다. 손상된 JSON/읽기 오류는 전달한다.
    """
    path = ROOT / 'reports' / 'history.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}


def store_results(results):
    """실행 보고서와 메일별 최신 비교 이력을 저장한다.
    
    입력: results: 비교 결과 dict 목록.
    반환: results와 CSV 상대 경로 report를 담은 dict.
    처리 규칙: save_reports 호출 후 compared_at을 각 결과에 추가하고 mail_id별 최신 값으로 history를 병합한다. .part 후 replace로 이력을 교체한다.
    예외/부작용: 입력 결과 객체와 보고서/이력 파일을 변경한다. save_reports가 먼저 호출되므로 개별 실행 보고서는 이 함수가 뒤에 붙인 compared_at을 포함하지 않을 수 있다. 저장/파싱 오류는 전달한다.
    """
    folder = save_reports(results)
    values = history()
    for result in results:
        result['compared_at'] = datetime.now().isoformat(timespec='seconds')
        values[result['mail_id']] = result
    path = ROOT / 'reports' / 'history.json'
    temporary = path.with_suffix('.part')
    temporary.write_text(json.dumps(values, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)
    return {'results': results, 'report': str(folder.relative_to(ROOT) / 'results.csv')}


def start_job(work):
    """긴 작업을 데몬 스레드에서 한 번에 하나만 실행한다.
    
    입력: work: progress(message) 콜백을 받아 결과를 반환하는 callable.
    반환: 새 job_id 문자열. 작업 결과는 JOBS에 비동기로 저장된다.
    처리 규칙: LOCK 안에서 BUSY를 검사/설정하고 running 상태를 등록한다. 완료/실패 상태와 메시지를 갱신하며 최대 최근 50개를 남긴다.
    예외/부작용: 스레드와 JOBS/BUSY 공유 상태를 변경한다. 이미 작업 중이면 ValueError. work 예외는 기록 후 error 상태로 변환한다.
    """
    global BUSY
    with LOCK:
        if BUSY:
            raise ValueError('다른 작업이 진행 중입니다. 완료 후 다시 시도하세요.')
        BUSY = True
        job_id = secrets.token_hex(8)
        JOBS[job_id] = {'state': 'running', 'message': '작업 시작', 'result': None}
    def progress(message):
        """실행 중 작업의 진행 문구를 갱신한다.
        
        입력: message: UI에 전달할 진행 문자열.
        반환: None.
        처리 규칙: LOCK을 얻어 현재 job_id의 message를 바꾼다.
        예외/부작용: JOBS 공유 상태만 변경한다.
        """
        with LOCK:
            JOBS[job_id]['message'] = message
    def run():
        """비동기 작업 본체를 실행하고 성공/오류 상태를 정리한다.
        
        입력: 명시 인자 없음. work/progress/job_id 클로저를 참조한다.
        반환: None.
        처리 규칙: work 결과를 done으로 저장하거나 예외를 error로 저장한다. finally에서 BUSY를 해제하고 오래된 작업을 제거한다.
        예외/부작용: work의 파일/네트워크 부작용이 있을 수 있다. 예외 로그와 공유 상태를 갱신하며 데몬 스레드에서 실행된다.
        """
        global BUSY
        try:
            result = work(progress)
            with LOCK:
                JOBS[job_id].update(state='done', message='작업 완료', result=result)
        except Exception as exc:
            logging.exception('Job failed')
            with LOCK:
                JOBS[job_id].update(state='error', message=str(exc))
        finally:
            with LOCK:
                BUSY = False
                # 한 실행에서 완료 작업을 무한히 보관하지 않음
                for old in list(JOBS)[:-50]:
                    JOBS.pop(old, None)
    threading.Thread(target=run, daemon=True).start()
    return job_id


def selected_mails(payload):
    """요청의 선택 ID를 저장된 목록과 대조한다.
    
    입력: payload: expanded와 ids를 담은 요청 dict.
    반환: (선택 메일 목록, 전체 계약 목록).
    처리 규칙: 해당 캐시를 읽고 빈 선택/잘못된 ID를 거부한다. 중복 ID는 set 기준으로 검증한다.
    예외/부작용: 로컬 목록을 읽는다. 빈 선택/누락 ID는 ValueError, 캐시/JSON 오류는 전달한다.
    """
    catalog = read_catalog(bool(payload.get('expanded', True)))
    ids = payload.get('ids', [])
    if not isinstance(ids, list) or not ids:
        raise ValueError('메일을 한 건 이상 선택하세요.')
    mails = [m for m in catalog['mails'] if m['id'] in ids]
    if len(mails) != len(set(ids)):
        raise ValueError('목록에 없는 메일입니다. 목록을 다시 불러오세요.')
    return mails, catalog['contracts']


class Handler(BaseHTTPRequestHandler):
    """로컬 웹 API의 요청 검증과 응답을 담당하는 HTTP 핸들러.
    
    입력: HTTPServer가 전달하는 연결/클라이언트/서버 인자.
    반환: 요청별 Handler 인스턴스와 HTTP 응답.
    처리 규칙: GET/POST 라우트별 기능은 do_GET/do_POST에 명시한다.
    예외/부작용: 소켓 응답 및 라우트별 파일/작업 상태 변경이 발생한다.
    """
    def log_message(self, format, *args):
        """HTTP 요청 로그를 애플리케이션 로거로 보낸다.
        
        입력: format: 로그 형식. *args: 형식에 넣을 값.
        반환: None.
        처리 규칙: BaseHTTPRequestHandler의 기본 로그 출력을 logging.info로 대체한다.
        예외/부작용: 설정된 app.log에 기록될 수 있다.
        """
        logging.info(format, *args)

    def valid_host(self):
        """현재 요청의 Host가 로컬 서버 주소인지 검사한다.
        
        입력: self.headers의 Host 및 서버 포트.
        반환: bool.
        처리 규칙: 127.0.0.1:포트 또는 localhost:포트와 정확히 일치해야 한다.
        예외/부작용: 헤더 읽기만 수행한다. Origin/토큰 검사는 do_POST에서 별도 수행한다.
        """
        return self.headers.get('Host') in (f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}')

    def send_bytes(self, content, content_type, status=200, attachment=None):
        """공통 헤더와 바이트 본문으로 HTTP 응답을 전송한다.
        
        입력: content: bytes. content_type: MIME. status=200. attachment=None: 참이면 다운로드 헤더 추가.
        반환: None.
        처리 규칙: 길이/캐시금지/nosniff/CSP 헤더를 설정한다. 첨부 응답 파일명은 report.csv로 고정한다.
        예외/부작용: 클라이언트 소켓에 응답을 쓴다. 연결 종료 등의 I/O 오류는 전달할 수 있다.
        """
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(content)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        if content_type == 'application/pdf':
            # PDF는 화면의 iframe(같은 출처)에서 미리보기로 열려야 한다. frame-ancestors 'none'이나
            # object-src 'none'이 PDF 응답에 붙으면 Chrome이 "127.0.0.1에서 연결을 거부했습니다"로 차단한다.
            self.send_header('Content-Security-Policy', "frame-ancestors 'self'")
        else:
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; frame-src 'self'; object-src 'none'; frame-ancestors 'none'")
        if attachment:
            self.send_header('Content-Disposition', 'attachment; filename="report.csv"')
        self.end_headers()
        self.wfile.write(content)

    def json(self, value, status=200):
        """객체를 UTF-8 JSON 응답으로 전송한다.
        
        입력: value: JSON 직렬화 가능한 객체. status=200: HTTP 상태.
        반환: None.
        처리 규칙: ensure_ascii=False로 직렬화한 바이트를 send_bytes에 위임한다.
        예외/부작용: 응답 소켓에 쓴다. 직렬화/소켓 오류는 전달한다.
        """
        self.send_bytes(json.dumps(value, ensure_ascii=False).encode(), 'application/json; charset=utf-8', status)

    def do_GET(self):
        """UI/목록/이력/작업상태/허용 파일/PDF 페이지 조회를 처리한다.
        
        입력: HTTP 경로와 쿼리. /api/catalog은 expanded, /files와 /api/page는 path, page는 1부터 시작한다.
        반환: HTML, JSON, 파일 바이트 또는 PNG HTTP 응답.
        처리 규칙: Host를 검증하고 정해진 라우트로 분기한다. 파일은 허용 저장 루트와 확장자를 검사한다. 목록에 파일별 저장 상태를 추가하며 /api/page?info=1은 페이지 수를 반환한다.
        예외/부작용: 로컬 읽기/렌더링/소켓 응답이 발생한다. 없는 경로/파일은 404, 잘못된 Host는 403, 기타 예외는 로그와 500 JSON으로 변환한다. 목록 조회 자체는 외부 네트워크를 사용하지 않는다.
        """
        if not self.valid_host():
            return self.json({'error': '허용되지 않은 호스트'}, 403)
        route = urlsplit(self.path)
        query = parse_qs(route.query)
        try:
            if route.path == '/':
                self.send_bytes((ASSETS / 'index.html').read_bytes(), 'text/html; charset=utf-8')
            elif route.path == '/api/state':
                self.json({'token': TOKEN, 'data_dir': str(ROOT), 'app': 'SaebomCompare', 'version': VERSION, 'busy': BUSY})
            elif route.path == '/api/catalog':
                try:
                    catalog = read_catalog(query.get('expanded', ['1'])[0] == '1')
                    for mail in catalog['mails']:
                        pdfs = [a for a in mail['attachments'] if a['name'].lower().endswith('.pdf')]
                        mail['_pdf_count'] = len(pdfs)
                        mail['_saved'] = bool(pdfs) and all(cached_path(a, 'invoices').exists() for a in pdfs)
                        mail['_invoice_storage'], mail['_contract_storage'] = storage_status(mail, catalog['contracts'])
                    catalog['_contracts_saved'] = sum(cached_path(c['file'], 'contracts').exists() for c in catalog['contracts'] if c['kind'] == '지출')
                    self.json(catalog)
                except FileNotFoundError:
                    self.json({'mails': [], 'contracts': [], '_synced_at': None})
            elif route.path == '/api/history':
                self.json(history())
            elif route.path.startswith('/api/jobs/'):
                with LOCK:
                    job = dict(JOBS.get(route.path.rsplit('/', 1)[-1], {}))
                self.json(job if job else {'error': '작업 없음'}, 200 if job else 404)
            elif route.path == '/files':
                relative = query.get('path', [''])[0]
                path = (ROOT / relative).resolve()
                allowed = any(path.is_relative_to((root / name).resolve()) for root in data_roots() for name in ('downloads', 'reports'))
                if not allowed or path.suffix.lower() not in ('.pdf', '.csv', '.json', '.txt') or not path.is_file():
                    return self.json({'error': '파일 없음'}, 404)
                self.send_bytes(path.read_bytes(), mimetypes.guess_type(path.name)[0] or 'application/octet-stream', attachment=path.suffix == '.csv')
            elif route.path == '/api/page':
                path = (ROOT / query.get('path', [''])[0]).resolve()
                allowed = any(path.is_relative_to((root / 'downloads').resolve()) for root in data_roots())
                if not allowed or path.suffix.lower() != '.pdf' or not path.is_file():
                    return self.json({'error': '파일 없음'}, 404)
                image, pages = render_page(path, int(query.get('page', ['1'])[0]))
                if query.get('info'):
                    return self.json({'pages': pages})
                self.send_bytes(image, 'image/png')
            else:
                self.json({'error': '경로 없음'}, 404)
        except Exception as exc:
            logging.exception('Request failed')
            self.json({'error': str(exc)}, 500)

    def do_POST(self):
        """동기화/다운로드/오프라인 비교/로컬 업로드/종료 요청을 처리한다.
        
        입력: JSON 요청 본문. 로컬 Host, 허용 Origin(있는 경우), X-App-Token이 필요하다.
        반환: 일반 작업은 job_id JSON. 종료는 ok JSON. 실패는 error JSON.
        처리 규칙: 70MiB 본문 제한을 검사한다. 선택 목록을 검증한 뒤 start_job에 작업을 등록한다. 직접 업로드 PDF는 파일당 25MiB/헤더를 검사한다. 종료는 작업 중일 때 거부한다.
        예외/부작용: 라우트별 네트워크/파일 저장/서버 종료가 발생할 수 있다. 검증 오류 400, 인증 403, 본문 초과 413, 작업 중 종료 409, 기타 500으로 응답한다.
        """
        origin = self.headers.get('Origin')
        allowed_origins = (f'http://127.0.0.1:{self.server.server_port}', f'http://localhost:{self.server.server_port}')
        if not self.valid_host() or (origin and origin not in allowed_origins) or self.headers.get('X-App-Token') != TOKEN:
            return self.json({'error': '허용되지 않은 요청'}, 403)
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if size > 70 * 1024 * 1024:
                return self.json({'error': '파일 크기가 너무 큽니다. PDF당 25MB 이하로 선택하세요.'}, 413)
            payload = json.loads(self.rfile.read(size))
            route = urlsplit(self.path).path
            if route == '/api/sync':
                # 콜백/정규화 함수: 아래 lambda는 해당 표현식의 인자를 받아 즉시 변환하거나 상위 함수의 작업을 위임한다.
                job_id = start_job(lambda progress: {'synced_at': load_catalog(bool(payload.get('expanded', True)))['_synced_at']})
            elif route == '/api/prepare':
                mails, contracts = selected_mails(payload)
                def work(progress):
                    """선택 PDF와 지출 계약의 온라인 저장 작업 본체.
                    
                    입력: progress: 상태 문구 콜백. mails/contracts 클로저.
                    반환: saved 성공 개수와 errors 실패 목록 dict.
                    처리 규칙: prepare_files에 진행 콜백을 전달한다.
                    예외/부작용: 외부 GET과 원본/TXT 저장이 발생한다. 파일별 오류는 prepare_files가 수집한다.
                    """
                    count, errors = prepare_files(mails, contracts, progress)
                    return {'saved': count, 'errors': errors}
                job_id = start_job(work)
            elif route == '/api/compare':
                mails, contracts = selected_mails(payload)
                def work(progress):
                    """선택 메일들을 저장 원본만으로 비교하는 작업 본체.
                    
                    입력: progress: 진행 콜백. mails/contracts 클로저.
                    반환: store_results의 results/report dict.
                    처리 규칙: offline=True로 메일별 비교한다. 각 오류는 자료 확인/확인 필요 결과로 바꾸고 다음 메일을 처리한다.
                    예외/부작용: 로컬 PDF/보고서/이력을 읽고 쓴다. 외부 다운로드는 하지 않는다. 최종 저장 오류는 start_job이 처리한다.
                    """
                    results = []
                    for i, mail in enumerate(mails, 1):
                        progress(f'{i}/{len(mails)} 로컬 비교: {mail["subject"]}')
                        try:
                            result = compare_mail(mail, contracts, offline=True)
                        except Exception as exc:
                            result = dict(mail_id=mail['id'], subject=mail['subject'], status='확인 필요', invoice_path='', contract_path='', contract_id='',
                                          checks=[dict(item='자료 확인', status='확인 필요', detail=str(exc))])
                        results.append(result)
                    return store_results(results)
                job_id = start_job(work)
            elif route == '/api/local-compare':
                def work(progress):
                    """직접 업로드한 PDF 두 개의 비교 작업 본체.
                    
                    입력: progress: 진행 콜백. payload.invoice/contract의 Base64 content.
                    반환: store_results의 results/report dict.
                    처리 규칙: 고유 uploads 폴더를 만들고 Base64/25MiB/PDF 헤더를 검사한 뒤 로컬 비교한다.
                    예외/부작용: 로컬 PDF 및 보고서/이력을 생성한다. 유효성/파싱/쓰기 오류는 start_job의 error 상태로 전달한다.
                    """
                    folder = ROOT / 'downloads' / 'uploads' / secrets.token_hex(8)
                    folder.mkdir(parents=True)
                    paths = []
                    for key in ('invoice', 'contract'):
                        raw = base64.b64decode(payload[key]['content'], validate=True)
                        if len(raw) > 25 * 1024 * 1024 or not raw.startswith(b'%PDF-'):
                            raise ValueError('25MB 이하의 PDF 파일을 선택하세요.')
                        path = folder / (key + '.pdf')
                        path.write_bytes(raw)
                        paths.append(path)
                    progress('지정한 PDF 두 개를 로컬에서 비교 중')
                    return store_results([compare_local_files(*paths)])
                job_id = start_job(work)
            elif route == '/api/shutdown':
                if BUSY:
                    return self.json({'error': '진행 중인 작업이 끝난 뒤 종료하세요.'}, 409)
                self.json({'ok': True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            else:
                return self.json({'error': '경로 없음'}, 404)
            self.json({'job_id': job_id})
        except (ValueError, KeyError, FileNotFoundError) as exc:
            self.json({'error': str(exc)}, 400)
        except Exception as exc:
            logging.exception('Request failed')
            self.json({'error': str(exc)}, 500)


def main():
    """로컬 웹 서버를 시작하거나 중복 실행을 기존 화면으로 연결한다.
    
    입력: CLI --port=18765, --no-browser 플래그.
    반환: 정상 종료 시 None.
    처리 규칙: ROOT와 로그를 준비하고 127.0.0.1에 바인딩한다. 이미 사용 중이면 기존 /api/state의 앱 이름을 확인한다. 필요 시 기본 브라우저를 열고 serve_forever한다.
    예외/부작용: 폴더/로그/서버 소켓 생성과 선택적 브라우저 실행이 발생한다. 다른 앱의 포트 점유는 메시지/예외로 처리한다. finally에서 서버 소켓을 닫는다.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=18765)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    ROOT.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=str(ROOT / 'app.log'), level=logging.INFO, encoding='utf-8')
    url = f'http://127.0.0.1:{args.port}/'
    try:
        server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    except OSError:
        # 중복 실행은 기존 프로그램 화면으로 연결
        from urllib.request import urlopen
        try:
            with urlopen(url + 'api/state', timeout=2) as response:
                if json.load(response).get('app') != 'SaebomCompare':
                    raise ValueError('다른 프로그램이 포트를 사용 중입니다.')
        except Exception:
            if sys.platform == 'win32':
                import ctypes
                ctypes.windll.user32.MessageBoxW(0, '18765 포트를 사용할 수 없습니다. app.log를 확인하세요.', '새봄 PDF 비교', 0)
            raise
        if not args.no_browser:
            webbrowser.open(url)
        return
    if not args.no_browser:
        # 콜백/정규화 함수: 아래 lambda는 해당 표현식의 인자를 받아 즉시 변환하거나 상위 함수의 작업을 위임한다.
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
