"""같은 Chrome 창/탭에서 클릭 명령을 순차 실행하는 별도 Playwright 도구.

기능명세:
- 서버 실행: py browser_control.py. 명령: py browser_control.py command list/open/read/menu/back/stop.
- open은 --subject, menu는 --name을 받는다. 초기 접속만 goto를 쓰고 이후 링크/행을 클릭한다.
- LOCALAPPDATA/SaebomCompare/browser-control의 inbox/outbox JSON으로 명령과 응답을 전달한다.
- chrome-profile은 개인 기본 프로필과 분리한다. 네트워크 사이트 접속과 실제 Chrome 화면 변경이 발생한다.
- 종료/오류 시 상태 파일을 갱신하고 해당 자동화 컨텍스트를 닫는다.
- 로컬 웹 UI 서버와는 분리되어 있고 이 도구 자체에는 PDF 다운로드/비교 명령이 없다.
"""
import argparse
import json
import os
import time
import uuid
from pathlib import Path

from playwright.sync_api import sync_playwright

DIRECTORY = Path(os.environ['LOCALAPPDATA']) / 'SaebomCompare' / 'browser-control'
INBOX = DIRECTORY / 'inbox'
OUTBOX = DIRECTORY / 'outbox'


def write_json(path, data):
    """명령/상태/응답 JSON을 임시 파일 후 교체 방식으로 기록한다.
    
    입력: path: 대상 Path. data: JSON 직렬화 가능한 객체.
    반환: None.
    처리 규칙: 상위 폴더 생성 후 UTF-8 .part를 쓰고 replace한다.
    예외/부작용: 로컬 파일을 생성/교체한다. 직렬화/쓰기 오류는 전달한다.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.part')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def read_mail(page):
    """현재 열린 메일 상세 화면의 내용을 읽는다.
    
    입력: page: 살아 있는 Playwright Page.
    반환: title/metadata/body/attachments/url dict.
    처리 규칙: mail-body가 보일 때까지 기다린 뒤 각 DOM 영역의 inner_text를 읽는다.
    예외/부작용: 현재 페이지 DOM만 읽는다. 메일 상세가 아니거나 브라우저가 닫혔으면 Playwright 오류/타임아웃을 전달한다. PDF 본문은 읽지 않는다.
    """
    page.locator('.mail-body').wait_for(state='visible')
    return {'title': page.locator('#app h2').inner_text(),
            'metadata': page.locator('.mail-meta').inner_text(),
            'body': page.locator('.mail-body').inner_text(),
            'attachments': page.locator('.attach-box').inner_text(),
            'url': page.url}


def act(page, command):
    """현재 탭에서 한 개의 브라우저 액션을 실행한다.
    
    입력: page: Page. command: action 및 subject/name dict.
    반환: 메일 내용, 목록 행 또는 메뉴 내용/url dict.
    처리 규칙: list/open은 필요 시 메일함 링크를 클릭한다. 제목 부분 일치 행이 정확히 하나일 때 클릭한다. menu는 정해진 네 메뉴만, back은 브라우저 뒤로가기를 사용한다.
    예외/부작용: 실제 Chrome 화면과 방문 이력을 바꾼다. 후보 개수/미지원 작업은 ValueError, DOM/타임아웃 오류는 전달한다. 초기 접속용 goto는 serve에만 있다.
    """
    action = command['action']
    if action in ('list', 'open'):
        # 최초 접속 이후에는 goto/reload 없이 사이트 링크를 클릭한다.
        if not page.url.endswith('#/mail'):
            page.get_by_role('link', name='메일함', exact=True).click()
        rows = page.get_by_test_id('mail-row')
        rows.first.wait_for(state='visible')
        if action == 'list':
            return {'url': page.url, 'rows': rows.all_inner_texts()}
        target = rows.filter(has_text=command['subject'])
        count = target.count()
        if count != 1:
            raise ValueError(f'일치하는 메일 {count}건. 제목을 더 구체적으로 지정하세요.')
        target.click()
        return read_mail(page)
    if action == 'read':
        return read_mail(page)
    if action == 'menu':
        name = command['name']
        if name not in ('메일함', '계약 문서함', '결재함', '기안 작성'):
            raise ValueError('지원하지 않는 메뉴입니다.')
        page.get_by_role('link', name=name, exact=True).click()
        heading = '받은 메일함' if name == '메일함' else name
        page.get_by_role('heading', name=heading, exact=True).wait_for(state='visible')
        return {'url': page.url, 'content': page.locator('#app').inner_text()}
    if action == 'back':
        page.go_back()
        page.locator('#app h2').wait_for(state='visible')
        return {'url': page.url, 'content': page.locator('#app').inner_text()}
    raise ValueError('지원하지 않는 작업입니다.')


def serve():
    """Chrome을 유지하며 파일 기반 명령함을 순차 처리한다.
    
    입력: 명시 인자 없음. DIRECTORY/INBOX/OUTBOX를 사용한다.
    반환: 브라우저 종료/stop 처리 후 None.
    처리 규칙: 전용 persistent context를 열고 최초 페이지를 로드한다. 유효시간 내 JSON 명령만 처리하며 150ms 대기로 Playwright 이벤트를 유지한다.
    예외/부작용: Chrome/사이트 네트워크/프로필/상태/명령/응답 파일을 사용한다. 명령 오류는 개별 error 응답으로 변환하고 종료 시 컨텍스트를 닫는다. 개인 기본 프로필은 사용하지 않는다.
    """
    INBOX.mkdir(parents=True, exist_ok=True)
    OUTBOX.mkdir(parents=True, exist_ok=True)
    started = time.time()
    profile = DIRECTORY.parent / 'chrome-profile'
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            str(profile), channel='chrome', headless=False, slow_mo=350,
            accept_downloads=True, no_viewport=True,
        )
        state_path = DIRECTORY / 'state.json'
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.set_default_timeout(12000)
            page.goto('https://sbgw.vercel.app/?cases=1#/mail', wait_until='domcontentloaded')
            page.get_by_test_id('mail-row').first.wait_for(state='visible')
            write_json(state_path, {'pid': os.getpid(), 'running': True, 'started': started})
            print('Chrome 연결 완료. 같은 창/탭에서 클릭 명령을 대기합니다.', flush=True)
            running = True
            while running and not page.is_closed():
                for path in sorted(INBOX.glob('*.json')):
                    request = json.loads(path.read_text(encoding='utf-8'))
                    path.unlink()
                    if request.get('expires', 0) < time.time():
                        write_json(OUTBOX / path.name, {'ok': False, 'error': '명령 유효시간이 지났습니다.'})
                        continue
                    try:
                        if request['action'] == 'stop':
                            result = {'stopped': True}
                            running = False
                        else:
                            result = act(page, request)
                        write_json(OUTBOX / path.name, {'ok': True, 'result': result})
                        print('처리 완료:', request['action'], flush=True)
                    except Exception as exc:
                        write_json(OUTBOX / path.name, {'ok': False, 'error': str(exc)})
                    if not running:
                        break
                if running:
                    page.wait_for_timeout(150)
        finally:
            write_json(state_path, {'pid': os.getpid(), 'running': False})
            context.close()


def command(args):
    """실행 중인 브라우저 제어기에 명령을 보내고 결과를 출력한다.
    
    입력: args: argparse Namespace(action, subject, name).
    반환: 성공 시 None; 실패 응답은 SystemExit(1).
    처리 규칙: running 상태 파일을 확인하고 UUID 요청을 25초 유효시간으로 기록한다. 응답을 최대 30초 대기한다.
    예외/부작용: inbox 파일 생성/outbox 읽기/콘솔 출력이 발생한다. 미실행은 RuntimeError, 무응답은 TimeoutError. 이 클라이언트는 Chrome을 새로 실행하지 않는다.
    """
    state_path = DIRECTORY / 'state.json'
    if not state_path.exists() or not json.loads(state_path.read_text(encoding='utf-8')).get('running'):
        raise RuntimeError('먼저 py browser_control.py로 자동화 창을 실행하세요.')
    request_id = uuid.uuid4().hex
    request = {'action': args.action, 'subject': args.subject, 'name': args.name, 'expires': time.time() + 25}
    write_json(INBOX / (request_id + '.json'), request)
    response = OUTBOX / (request_id + '.json')
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if response.exists():
            result = json.loads(response.read_text(encoding='utf-8'))
            print(json.dumps(result, ensure_ascii=False, indent=2))
            if not result['ok']:
                raise SystemExit(1)
            return
        time.sleep(.1)
    raise TimeoutError('브라우저 응답이 없습니다. 자동화 창이 열려 있는지 확인하세요.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='mode')
    client = sub.add_parser('command')
    client.add_argument('action', choices=['list', 'open', 'read', 'menu', 'back', 'stop'])
    client.add_argument('--subject', default='')
    client.add_argument('--name', default='')
    args = parser.parse_args()
    if args.mode == 'command':
        command(args)
    else:
        serve()
