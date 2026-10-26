"""패키징된 EXE의 실제 로컬 HTTP 기능을 점검하는 통합 실행 스크립트.

기능명세:
- 실행: py tests/exe_smoke.py. dist/SaebomCompare.exe 및 프로젝트 downloads 샘플이 필요하다.
- import 시에도 테스트가 실행되므로 unittest 자동 수집용 파일이 아니다.
- 샘플 자료를 tmp/exe-smoke로 복사하고 전용 데이터 폴더/18766 포트로 EXE를 실행한다.
- UI, 목록/저장 상태, 선택 비교, PDF/CSV 응답, 직접 파일 비교, 이력을 검증한다.
- finally에서 테스트 서버만 종료하고 필요 시 생성한 자식 프로세스를 종료한다. 운영 서버를 대상으로 하지 않는다.
"""
import base64
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'tmp' / 'exe-smoke'
DATA.mkdir(parents=True, exist_ok=True)
shutil.copytree(ROOT / 'downloads', DATA / 'downloads', dirs_exist_ok=True)
env = dict(os.environ, SBGW_DATA_DIR=str(DATA))
process = subprocess.Popen([str(ROOT / 'dist' / 'SaebomCompare.exe'), '--no-browser', '--port', '18766'],
                           env=env, creationflags=subprocess.CREATE_NO_WINDOW)
BASE = 'http://127.0.0.1:18766'
token = ''


def get(path):
    """테스트 EXE에 GET을 보내 본문을 읽는다.
    
    입력: path: BASE 뒤에 붙일 HTTP 경로.
    반환: bytes.
    처리 규칙: 18766 로컬 서버를 5초 타임아웃으로 조회한다.
    예외/부작용: 테스트용 로컬 HTTP만 사용한다. HTTP/연결 오류는 테스트 실패로 전달한다.
    """
    with urlopen(BASE + path, timeout=5) as response:
        return response.read()


def post(path, payload):
    """테스트 EXE에 인증 JSON POST를 보낸다.
    
    입력: path: API 경로. payload: JSON 객체. 전역 token 사용.
    반환: 응답 JSON 객체.
    처리 규칙: Content-Type과 X-App-Token을 붙여 5초 타임아웃으로 요청한다.
    예외/부작용: 라우트에 따라 테스트 자료/작업 상태가 변경된다. HTTP/JSON/통신 오류는 전달한다.
    """
    request = Request(BASE + path, data=json.dumps(payload).encode(),
                      headers={'Content-Type': 'application/json', 'X-App-Token': token})
    with urlopen(request, timeout=5) as response:
        return json.load(response)


def wait_job(job):
    """비동기 테스트 작업이 완료될 때까지 조회한다.
    
    입력: job: job_id를 담은 POST 응답.
    반환: 완료 작업의 result.
    처리 규칙: 0.1초 간격으로 최대 30초 조회하며 done 이외의 종료 상태는 실패로 본다.
    예외/부작용: 로컬 GET을 반복한다. 오류 상태/시간 초과는 AssertionError로 테스트를 실패시킨다.
    """
    end = time.monotonic() + 30
    while time.monotonic() < end:
        state = json.loads(get('/api/jobs/' + job['job_id']))
        if state['state'] != 'running':
            assert state['state'] == 'done', state
            return state['result']
        time.sleep(.1)
    raise AssertionError('job timeout')


try:
    end = time.monotonic() + 30
    while time.monotonic() < end:
        try:
            state = json.loads(get('/api/state'))
            break
        except OSError:
            time.sleep(.2)
    else:
        raise AssertionError('EXE server did not start')
    token = state['token']
    assert state['data_dir'] == str(DATA)
    html = get('/').decode()
    assert 'previousSelection' in html
    catalog = json.loads(get('/api/catalog'))
    assert len(catalog['mails']) == 14
    first = next(m for m in catalog['mails'] if m['id'] == 'M-14')
    assert first['_invoice_storage']['saved'] == 1
    assert first['_contract_storage']['id'] == 'C-2026-022'
    assert first['_contract_storage']['status'] == first['_invoice_storage']['status']
    assert '_invoice_storage' in html and '_contract_storage' in html
    batch = wait_job(post('/api/compare', {'ids': ['M-14', 'M-07'], 'expanded': True}))
    assert len(batch['results']) == 2
    assert batch['results'][0]['invoice']['total'] == 1650000
    assert batch['results'][0]['status'] != batch['results'][1]['status']
    result = batch['results'][0]
    relative = Path(result['invoice_path']).relative_to(DATA).as_posix()
    assert get('/files?path=' + quote(relative)).startswith(b'%PDF-')
    assert len(get('/files?path=' + quote(batch['report']))) > 100
    payload = {}
    for key in ('invoice', 'contract'):
        payload[key] = {'content': base64.b64encode(Path(result[key + '_path']).read_bytes()).decode()}
    local = wait_job(post('/api/local-compare', payload))
    assert len(local['results']) == 1
    assert local['results'][0]['invoice']['total'] == 1650000
    assert len(json.loads(get('/api/history'))) >= 3
    print('EXE smoke passed: embedded UI, stored catalog, selected offline batch, PDF, CSV, direct PDF upload, history')
finally:
    if token:
        post('/api/shutdown', {})
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.terminate()
        process.wait(timeout=5)
    assert process.returncode == 0, process.returncode
