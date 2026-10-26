"""Chrome 메일 열기와 선택적 PDF 다운로드를 확인하는 대화형 테스트 스크립트.

기능명세:
- 실행: py browser_test.py [--subject 제목] [--list] [--read-pdf].
- 모듈 최상위에서 실행되므로 import도 Chrome 실행과 입력 대기를 유발한다. 라이브러리 import 용도가 아니다.
- chrome-profile을 유지하지만 반복 입력마다 목록 URL로 goto한다. 지속 클릭 제어는 browser_control.py를 사용한다.
- --read-pdf는 첨부 PDF를 downloads/browser에 저장하고 pypdf 텍스트를 콘솔에 출력한다.
- Enter 또는 quit은 종료하며 finally에서 자동화 Chrome을 닫는다. AI/OCR/비교 판정은 수행하지 않는다.
"""

import os
import argparse
from pathlib import Path

from playwright.sync_api import sync_playwright
from pypdf import PdfReader


# 평소 사용하는 Chrome 프로필과 분리
app_dir = Path(os.environ['LOCALAPPDATA']) / 'SaebomCompare'
profile_dir = app_dir / 'chrome-profile'
profile_dir.mkdir(parents=True, exist_ok=True)
parser = argparse.ArgumentParser()
parser.add_argument('--subject', default='')
parser.add_argument('--read-pdf', action='store_true')
parser.add_argument('--list', action='store_true', help='받은 메일함 목록 표시')
args = parser.parse_args()

with sync_playwright() as p:
    context = p.chromium.launch_persistent_context(
        user_data_dir=str(profile_dir),
        channel='chrome',
        headless=False,
        slow_mo=400,
        accept_downloads=True,
        no_viewport=True,
    )

    try:
        page = context.pages[0] if context.pages else context.new_page()
        subject = args.subject
        while True:
            page.goto(
                'https://sbgw.vercel.app/?cases=1#/mail',
                wait_until='domcontentloaded',
            )
            rows = page.get_by_test_id('mail-row')
            if args.list:
                rows.first.wait_for(state='visible')
                print('받은 메일함 목록:\n' + rows.all_inner_texts().__str__(), flush=True)
                subject = input('열 메일 제목을 입력하세요. Enter 또는 quit은 종료: ').strip()
                if not subject or subject == 'quit':
                    break
                args.list = False
            target = rows.filter(has_text=subject).first if subject else rows.first
            target.wait_for(state='visible')
            target.click()
            page.locator('.mail-body').wait_for(state='visible')
            print('메일 제목:', page.locator('#app h2').inner_text(), flush=True)
            print(page.locator('.mail-meta').inner_text(), flush=True)
            print('메일 본문:', page.locator('.mail-body').inner_text(), flush=True)
            print('첨부:', page.locator('.attach-box').inner_text(), flush=True)

            if args.read_pdf:
                pdf_links = page.locator('a[data-testid="attachment-link"][href$=".pdf"]')
                for i in range(pdf_links.count()):
                    with page.expect_download() as info:
                        pdf_links.nth(i).click()
                    download = info.value
                    folder = app_dir / 'downloads' / 'browser'
                    folder.mkdir(parents=True, exist_ok=True)
                    path = folder / Path(download.suggested_filename).name
                    download.save_as(str(path))
                    print('PDF 파일:', path, flush=True)
                    for number, pdf_page in enumerate(PdfReader(path).pages, 1):
                        print(f'PDF {number}쪽:\n{pdf_page.extract_text()}', flush=True)
            subject = input('다음 메일 제목을 입력하세요. Enter 또는 quit은 종료: ').strip()
            if not subject or subject == 'quit':
                break
    finally:
        context.close()
