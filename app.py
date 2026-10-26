"""이전 버전의 Tkinter 데스크톱 UI와 배치 비교 실행기.

기능명세:
- 실행: py app.py 또는 py app.py --batch [--basic]. 현재 EXE 기본 진입점은 web_app.py다.
- compare_core를 호출해 목록을 가져오고 선택/전체 PDF 메일의 비교 및 CSV/JSON 저장을 수행한다.
- Tkinter 위젯은 메인 스레드에서만 수정하고 작업 스레드는 Queue로 이벤트를 전달한다.
- 이전 UI의 비교는 offline=True를 지정하지 않아 필요 시 다운로드할 수 있다.
"""
import argparse
import json
import os
import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from compare_core import ROOT, compare_mail, load_catalog, save_reports


class App(tk.Tk):
    """이전 Tkinter UI의 창·상태·작업 이벤트를 관리하는 클래스.
    
    입력: Tk 기본 생성자 호출. 세부 초기화는 __init__ 참조.
    반환: App 인스턴스.
    처리 규칙: GUI 갱신은 메인 스레드, 업무 실행은 launch/Queue로 분리한다.
    예외/부작용: 창과 주기 타이머를 만들며 종료는 Tk의 창 생명주기를 따른다.
    """
    def __init__(self):
        """이전 Tkinter UI의 위젯과 이벤트 상태를 구성한다.
        
        입력: self: Tk 인스턴스.
        반환: None.
        처리 규칙: 목록/상세/버튼을 만들고 Queue, results, catalog, busy를 초기화한다. after로 poll과 최초 온라인 조회를 예약한다.
        예외/부작용: 실제 데스크톱 창을 만들며 예약된 refresh가 네트워크를 사용할 수 있다.
        """
        super().__init__()
        self.title('새봄 · 세금계산서와 계약서 비교')
        self.geometry('1180x780')
        self.minsize(900, 600)
        self.catalog = None
        self.results = {}
        self.report_folder = ROOT / 'reports'
        self.events = queue.Queue()
        self.busy = False
        self.expanded = tk.BooleanVar(value=True)
        style = ttk.Style(self)
        style.theme_use('clam')
        style.configure('Treeview', rowheight=30, font=('맑은 고딕', 10))
        style.configure('TButton', padding=7)
        top = ttk.Frame(self, padding=16)
        top.pack(fill='x')
        ttk.Label(top, text='세금계산서 × 계약서', font=('맑은 고딕', 20, 'bold')).pack(anchor='w')
        ttk.Label(top, text='사이트에서 원본 PDF를 다운로드하고 이 컴퓨터에서 내용을 대조합니다.').pack(anchor='w', pady=(4, 10))
        bar = ttk.Frame(top)
        bar.pack(fill='x')
        self.toggle = ttk.Checkbutton(bar, text='확장 케이스 포함', variable=self.expanded)
        self.toggle.pack(side='left')
        self.buttons = []
        for title, command in [('메일 목록 새로고침', self.refresh), ('선택 메일 비교', self.selected), ('전체 PDF 메일 비교', self.all_mails)]:
            button = ttk.Button(bar, text=title, command=command)
            button.pack(side='left', padx=5)
            self.buttons.append(button)
        ttk.Button(bar, text='보고서 폴더', command=self.open_report).pack(side='right')
        panes = ttk.Panedwindow(self, orient='vertical')
        panes.pack(fill='both', expand=True, padx=16)
        table = ttk.Frame(panes)
        self.tree = ttk.Treeview(table, columns=('date', 'sender', 'subject', 'status'), show='headings', selectmode='browse')
        for key, title, width in [('date', '받은 시각', 145), ('sender', '보낸 사람', 190), ('subject', '메일 제목', 580), ('status', '비교 결과', 100)]:
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, stretch=key == 'subject')
        scroll = ttk.Scrollbar(table, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side='left', fill='both', expand=True)
        scroll.pack(side='right', fill='y')
        self.tree.tag_configure('불일치', foreground='#b42318')
        self.tree.tag_configure('일치', foreground='#067647')
        self.tree.tag_configure('확인 필요', foreground='#985b00')
        self.tree.bind('<<TreeviewSelect>>', self.show_detail)
        panes.add(table, weight=3)
        detail = ttk.Frame(panes, padding=(0, 10))
        links = ttk.Frame(detail)
        links.pack(fill='x')
        ttk.Label(links, text='메일 및 비교 근거', font=('맑은 고딕', 12, 'bold')).pack(side='left')
        # 콜백/정규화 함수: 아래 lambda는 해당 표현식의 인자를 받아 즉시 변환하거나 상위 함수의 작업을 위임한다.
        ttk.Button(links, text='세금계산서 PDF 열기', command=lambda: self.open_pdf('invoice_path')).pack(side='right')
        # 콜백/정규화 함수: 아래 lambda는 해당 표현식의 인자를 받아 즉시 변환하거나 상위 함수의 작업을 위임한다.
        ttk.Button(links, text='계약서 PDF 열기', command=lambda: self.open_pdf('contract_path')).pack(side='right', padx=6)
        self.detail = tk.Text(detail, wrap='word', font=('맑은 고딕', 10), state='disabled')
        ds = ttk.Scrollbar(detail, command=self.detail.yview)
        self.detail.configure(yscrollcommand=ds.set)
        self.detail.pack(side='left', fill='both', expand=True, pady=8)
        ds.pack(side='right', fill='y', pady=8)
        panes.add(detail, weight=2)
        self.status = tk.StringVar(value='메일 목록을 불러오세요.')
        ttk.Label(self, textvariable=self.status, padding=12).pack(fill='x')
        self.after(100, self.poll)
        self.after(200, self.refresh)

    def launch(self, job):
        """작업 중복을 막고 백그라운드 작업을 시작한다.
        
        입력: job: 인자 없이 실행할 callable.
        반환: None. 이미 busy이면 즉시 반환.
        처리 규칙: 버튼을 비활성화한 뒤 run 데몬 스레드를 시작한다.
        예외/부작용: Tk 상태와 busy를 바꾼다. 결과/오류는 Queue로 전달하여 UI를 작업 스레드에서 직접 만지지 않는다.
        """
        if self.busy:
            return
        self.busy = True
        for button in self.buttons:
            button.configure(state='disabled')
        self.toggle.configure(state='disabled')
        def run():
            """백그라운드 job을 실행하고 완료 이벤트를 보낸다.
            
            입력: 명시 인자 없음. job과 self 클로저 사용.
            반환: None.
            처리 규칙: job 호출, 예외 시 error 큐 추가, finally에서 done 추가.
            예외/부작용: job의 외부 부작용 및 Queue 변경이 발생한다. job 예외를 UI 오류 이벤트로 바꾼다.
            """
            try:
                job()
            except Exception as exc:
                self.events.put(('error', str(exc)))
            finally:
                self.events.put(('done', None))
        threading.Thread(target=run, daemon=True).start()

    def refresh(self):
        """온라인 메일/계약 목록 조회를 예약한다.
        
        입력: self.expanded의 체크 상태.
        반환: None.
        처리 규칙: 진행 문구를 바꾸고 load_catalog 결과를 catalog 이벤트로 전달하는 작업을 launch한다.
        예외/부작용: UI 상태 변경 및 백그라운드 네트워크/캐시 저장이 발생한다.
        """
        expanded = self.expanded.get()
        self.status.set('사이트에서 메일·계약 목록을 가져오는 중…')
        # 콜백/정규화 함수: 아래 lambda는 해당 표현식의 인자를 받아 즉시 변환하거나 상위 함수의 작업을 위임한다.
        self.launch(lambda: self.events.put(('catalog', load_catalog(expanded))))

    def selected(self):
        """현재 선택한 한 메일의 비교를 요청한다.
        
        입력: Treeview의 선택 행.
        반환: None.
        처리 규칙: 선택이 없으면 안내창을 표시하고, 선택 ID와 일치하는 메일을 compare에 전달한다.
        예외/부작용: 안내 대화상자 또는 비교 작업 시작이 발생한다. catalog가 준비된 상태에서 사용한다.
        """
        ids = self.tree.selection()
        if not ids:
            messagebox.showinfo('메일 선택', '목록에서 비교할 메일을 선택하세요.')
            return
        self.compare([m for m in self.catalog['mails'] if m['id'] == ids[0]])

    def all_mails(self):
        """목록에서 PDF 첨부가 있는 메일을 모두 비교한다.
        
        입력: self.catalog.
        반환: None.
        처리 규칙: 목록이 있으면 첨부 이름이 .pdf로 끝나는 메일만 필터링한다.
        예외/부작용: compare를 통해 다운로드/비교/보고서 저장이 발생할 수 있다.
        """
        if self.catalog:
            self.compare([m for m in self.catalog['mails'] if any(a['name'].lower().endswith('.pdf') for a in m['attachments'])])

    def compare(self, mails):
        """지정 메일의 비교 및 보고서 저장을 작업 스레드에 예약한다.
        
        입력: mails: 선택한 메일 dict 목록.
        반환: None.
        처리 규칙: 현재 계약 목록을 캡처하고 job을 launch한다.
        예외/부작용: 비교/저장 이벤트를 Queue로 전달한다. 이전 UI이므로 compare_mail의 온라인 기본값을 사용한다.
        """
        contracts = self.catalog['contracts']
        def job():
            """메일들을 순서대로 비교하고 실행 보고서를 저장한다.
            
            입력: 명시 인자 없음. mails/contracts/self 클로저.
            반환: None.
            처리 규칙: 메일별 진행/결과 이벤트를 전달한다. 개별 실패는 확인 필요 결과로 변환하고 마지막에 save_reports를 호출한다.
            예외/부작용: PDF 읽기/다운로드/보고서 저장과 Queue 변경이 발생한다. 보고서 저장 실패는 상위 run이 처리한다.
            """
            results = []
            for i, mail in enumerate(mails, 1):
                self.events.put(('status', f'{i}/{len(mails)} 다운로드·대조 중: {mail["subject"]}'))
                try:
                    result = compare_mail(mail, contracts)
                except Exception as exc:
                    result = {'mail_id': mail['id'], 'subject': mail['subject'], 'status': '확인 필요', 'contract_id': '', 'invoice_path': '', 'contract_path': '',
                              'checks': [{'item': '처리 오류', 'status': '확인 필요', 'detail': str(exc)}]}
                results.append(result)
                self.events.put(('result', result))
            folder = save_reports(results)
            self.events.put(('report', (folder, len(results))))
        self.launch(job)

    def poll(self):
        """작업 스레드의 이벤트를 메인 스레드 UI에 반영한다.
        
        입력: self.events 큐.
        반환: None.
        처리 규칙: catalog/result/status/report/error/done을 처리하고 100ms 뒤 자신을 다시 예약한다.
        예외/부작용: 목록/상세/버튼/상태/오류창을 변경한다. 빈 큐는 정상 종료 조건으로 처리한다.
        """
        try:
            while True:
                kind, data = self.events.get_nowait()
                if kind == 'catalog':
                    self.catalog, self.results = data, {}
                    self.tree.delete(*self.tree.get_children())
                    for m in data['mails']:
                        self.tree.insert('', 'end', iid=m['id'], values=(m['received_at'], m['from_name'], m['subject'], '미실행'))
                    self.status.set(f'메일 {len(data["mails"])}건 · 계약 {len(data["contracts"])}건')
                    if data['mails']:
                        self.tree.selection_set(data['mails'][0]['id'])
                elif kind == 'result':
                    self.results[data['mail_id']] = data
                    self.tree.set(data['mail_id'], 'status', data['status'])
                    self.tree.item(data['mail_id'], tags=(data['status'],))
                    self.show_detail()
                elif kind == 'status':
                    self.status.set(data)
                elif kind == 'report':
                    self.report_folder = data[0]
                    self.status.set(f'{data[1]}건 비교 완료 · CSV/JSON 보고서 저장: {data[0]}')
                elif kind == 'error':
                    self.status.set('처리에 실패했습니다. 다시 시도하세요.')
                    messagebox.showerror('처리 오류', data)
                elif kind == 'done':
                    self.busy = False
                    for button in self.buttons:
                        button.configure(state='normal')
                    self.toggle.configure(state='normal')
        except queue.Empty:
            pass
        self.after(100, self.poll)

    def show_detail(self, event=None):
        """선택한 메일 본문과 비교 근거를 상세 영역에 표시한다.
        
        입력: event=None: Treeview 선택 이벤트(본문에서는 미사용).
        반환: None.
        처리 규칙: 선택과 catalog가 없으면 반환한다. 결과가 있으면 항목별 상태/근거를 문자열로 만든다.
        예외/부작용: 읽기 전용 Text 위젯 내용을 교체한다. 파일/네트워크 접근은 없다.
        """
        ids = self.tree.selection()
        if not ids or not self.catalog:
            return
        mail = next(m for m in self.catalog['mails'] if m['id'] == ids[0])
        text = f"{mail['subject']}\n보낸 사람: {mail['from_name']} <{mail['from_email']}>\n받는 사람: {mail['to']}\n받은 시각: {mail['received_at']}\n\n{mail['body']}\n\n"
        result = self.results.get(ids[0])
        if result:
            text += f"비교 결과: {result['status']}\n계약번호: {result.get('contract_id', '')}\n\n"
            text += '\n\n'.join(f"[{c['status']}] {c['item']}\n{c['detail']}" for c in result['checks'])
        else:
            text += '선택 메일 비교를 누르면 PDF 원본을 내려받아 비교합니다.'
        self.detail.configure(state='normal')
        self.detail.delete('1.0', 'end')
        self.detail.insert('1.0', text)
        self.detail.configure(state='disabled')

    def open_pdf(self, key):
        """선택 결과의 원본 PDF를 Windows 기본 연결 앱으로 연다.
        
        입력: key: invoice_path 또는 contract_path.
        반환: None.
        처리 규칙: 선택 결과에서 경로를 읽고 os.startfile을 호출한다. 경로가 없으면 안내창을 표시한다.
        예외/부작용: 외부 PDF 앱 실행 또는 대화상자가 발생한다. OS 실행 오류는 전달될 수 있다.
        """
        ids = self.tree.selection()
        path = self.results.get(ids[0], {}).get(key) if ids else None
        if path:
            os.startfile(path)
        else:
            messagebox.showinfo('PDF', '먼저 비교를 실행하세요. 원본이 없으면 열 수 없습니다.')

    def open_report(self):
        """보고서 폴더를 파일 탐색기로 연다.
        
        입력: self.report_folder.
        반환: None.
        처리 규칙: 폴더를 만들고 os.startfile로 연다.
        예외/부작용: 폴더 생성과 Windows 탐색기 실행이 발생한다. OS/파일 권한 오류는 전달한다.
        """
        self.report_folder.mkdir(parents=True, exist_ok=True)
        os.startfile(str(self.report_folder))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--batch', action='store_true', help='화면 없이 모든 PDF 메일 비교')
    parser.add_argument('--basic', action='store_true', help='확장 케이스 제외')
    args = parser.parse_args()
    if args.batch:
        data = load_catalog(not args.basic)
        results = []
        for mail in data['mails']:
            if any(a['name'].lower().endswith('.pdf') for a in mail['attachments']):
                result = compare_mail(mail, data['contracts'])
                results.append(result)
                print(mail['id'], result['status'])
        print(save_reports(results))
    else:
        App().mainloop()
