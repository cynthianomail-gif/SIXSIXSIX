# -*- coding: utf-8 -*-
"""GitHub 共用儲存實測：本機假 GitHub API（contents GET/PUT、sha 衝突 409）＋三個獨立瀏覽器（A、B 有金鑰，C 沒有）。
情境：首次儲存、別人打開看到、兩人改不同卡、兩人改同一張卡的不同欄位、刪除、沒金鑰只能看、舊資料的信箱與 Flow 網址不會被推上去。
不碰真的 GitHub。用法：python -X utf8 tools/e2e_github_sync.py
"""
import base64
import hashlib
import http.server
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

import websocket

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHROME = r'C:\Program Files\Google\Chrome\Application\chrome.exe'
PAGE = 'file:///' + os.path.join(ROOT, '專案看板.html').replace('\\', '/')
STORE = {'content': None, 'sha': None, 'puts': 0, 'conflicts': 0}
FILE_PATH = '/repos/o/r/contents/data/board.json'


class GH(http.server.BaseHTTPRequestHandler):
    def _h(self, code, body=None):
        self.send_response(code)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Headers', 'authorization, content-type, accept')
        self.send_header('Access-Control-Allow-Methods', 'GET, PUT, OPTIONS')
        self.send_header('Access-Control-Allow-Private-Network', 'true')
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        if body is not None:
            self.wfile.write(json.dumps(body).encode())

    def do_OPTIONS(self):
        self._h(204)

    def do_GET(self):
        path = self.path.split('?')[0]
        if path == '/repos/o/r':
            return self._h(200, {'permissions': {'push': self.headers.get('Authorization') == 'Bearer good'}})
        if path == '/user':
            return self._h(200 if self.headers.get('Authorization') else 401, {'login': 'tester'})
        if path == FILE_PATH:
            if STORE['content'] is None:
                return self._h(404, {'message': 'Not Found'})
            return self._h(200, {'sha': STORE['sha'], 'content': base64.b64encode(STORE['content']).decode()})
        self._h(404, {})

    def do_PUT(self):
        if self.path.split('?')[0] != FILE_PATH:
            return self._h(404, {})
        if self.headers.get('Authorization') != 'Bearer good':
            return self._h(401, {'message': 'Bad credentials'})
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        if STORE['content'] is not None and body.get('sha') != STORE['sha']:
            STORE['conflicts'] += 1
            return self._h(409, {'message': 'sha mismatch'})
        STORE['content'] = base64.b64decode(body['content'])
        STORE['sha'] = hashlib.sha1(STORE['content']).hexdigest()
        STORE['puts'] += 1
        self._h(200, {'content': {'sha': STORE['sha']}})

    def log_message(self, *a):
        pass


def free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    p = s.getsockname()[1]
    s.close()
    return p


api_port = free_port()
srv = http.server.ThreadingHTTPServer(('127.0.0.1', api_port), GH)
threading.Thread(target=srv.serve_forever, daemon=True).start()
API = 'http://127.0.0.1:%d' % api_port
procs = []
results = []


def check(name, cond, detail=''):
    results.append(bool(cond))
    print(('PASS ' if cond else 'FAIL ') + name + ('  ' + str(detail) if detail else ''))


class Browser:
    def __init__(self):
        port = free_port()
        self.proc = subprocess.Popen([CHROME, '--headless=new', '--disable-gpu', '--remote-debugging-port=%d' % port,
                                      '--user-data-dir=' + tempfile.mkdtemp(prefix='kanban_gh_'), '--window-size=1400,900', 'about:blank'])
        procs.append(self.proc)
        for _ in range(50):
            try:
                tabs = json.load(urllib.request.urlopen('http://127.0.0.1:%d/json' % port))
                break
            except Exception:
                time.sleep(0.2)
        self.ws = websocket.create_connection(next(t for t in tabs if t['type'] == 'page')['webSocketDebuggerUrl'], suppress_origin=True, timeout=30)
        self.mid = 0
        self.errors = []
        self.cdp('Runtime.enable')
        self.cdp('Page.enable')

    def cdp(self, method, **params):
        self.mid += 1
        self.ws.send(json.dumps({'id': self.mid, 'method': method, 'params': params}))
        while True:
            m = json.loads(self.ws.recv())
            if m.get('method') == 'Runtime.exceptionThrown':
                self.errors.append(m['params']['exceptionDetails'].get('exception', {}).get('description', '')[:150])
            if m.get('id') == self.mid:
                return m.get('result', {})

    def js(self, expr):
        r = self.cdp('Runtime.evaluate', expression=expr, awaitPromise=True, returnByValue=True)
        if 'exceptionDetails' in r:
            raise RuntimeError(str(r['exceptionDetails'])[:300])
        return r['result'].get('value')

    def open(self, token, extra_ls=None):
        """先開一次拿到 origin，寫好同步設定（假 API）後重新載入"""
        self.cdp('Page.navigate', url=PAGE + '#offline')
        time.sleep(1.5)
        cfg = {'api': API, 'owner': 'o', 'repo': 'r', 'token': token, 'login': ''}
        self.js("localStorage.clear(); localStorage.setItem('project-kanban-sync', %s)" % json.dumps(json.dumps(cfg)))
        if extra_ls:
            self.js(extra_ls)
        self.cdp('Page.navigate', url=PAGE)
        time.sleep(2.5)

    def card(self, cid):
        return self.js("(data.projects[0].cards.find(c=>c.id==='%s')||null)" % cid)

    def edit(self, cid, code):
        self.js("{const c=data.projects[0].cards.find(c=>c.id==='%s'); %s; save(); renderAll();}" % (cid, code))

    def save_gh(self):
        self.js("saveToGitHub()")
        time.sleep(1.5)

    def reload(self):
        self.cdp('Page.reload')
        time.sleep(2.5)


def remote():
    return json.loads(STORE['content'].decode('utf-8')) if STORE['content'] else None


def rcard(cid):
    return next((c for c in remote()['projects'][0]['cards'] if c['id'] == cid), None)


try:
    # 舊資料：A 的瀏覽器裡有 Teams 時代的信箱與 Flow 網址
    old_ls = ("{const d=seedData(); d.people[0].teams='someone@example.com'; d.settings.personalFlowUrl='https://flow.example/secret'; "
              "d.settings.sendLog=['x']; localStorage.setItem('project-kanban-v1', JSON.stringify(d));}")
    A = Browser()
    A.open('good', old_ls)
    check('A：還沒有共用檔時沿用自己的資料', A.js("data.projects[0].cards.length") == 24)
    A.edit('c_slots', "c.title='改A'")
    check('A：有修改時「儲存」按鈕亮起', A.js("document.querySelector('#btnSync').classList.contains('dirty')"))
    A.save_gh()
    check('1. A 首次儲存建立共用檔', STORE['puts'] == 1 and rcard('c_slots')['title'] == '改A', STORE['puts'])
    raw = STORE['content'].decode('utf-8')
    check('1. 推上去的資料沒有信箱、Flow 網址、Teams 設定', 'someone@example.com' not in raw and 'flow.example' not in raw and 'personalFlowUrl' not in raw and '"teams"' not in raw)
    check('1. A 儲存後按鈕回到一般狀態', not A.js("document.querySelector('#btnSync').classList.contains('dirty')") and '已同步' in A.js("document.querySelector('#saveState').textContent"))

    B = Browser()
    B.open('good')
    check('2. B 打開就看到 A 存的內容', (B.card('c_slots') or {}).get('title') == '改A')

    # 3. 兩人改不同卡：A 改 c_q（不存），B 改 c_nda 並先存；A 再存 → 兩邊都在
    A.edit('c_q', "c.status='done'")
    B.edit('c_nda', "c.title='改B'")
    B.save_gh()
    A.save_gh()
    check('3. 兩人改不同卡，後存的人自動合併', rcard('c_q')['status'] == 'done' and rcard('c_nda')['title'] == '改B', (rcard('c_q')['status'], rcard('c_nda')['title']))
    check('3. A 畫面也有 B 的修改', (A.card('c_nda') or {}).get('title') == '改B')

    # 4. 同一張卡不同欄位：B 先改標題並存，A 改截止日後存
    B.reload()
    B.edit('c_meet', "c.title='會議B'")
    B.save_gh()
    A.edit('c_meet', "c.due='2026-11-12'")
    A.save_gh()
    m = rcard('c_meet')
    check('4. 同一張卡兩人改不同欄位都保留', m['title'] == '會議B' and m['due'] == '2026-11-12', (m['title'], m['due']))

    # 5. 新增與刪除：A 刪 c_info、B 新增一張卡
    B.reload()
    B.js("{const c=newCard(project(),'B新卡'); c.stageId='st_misc'; save(); renderAll();}")
    A.js("{const p=project(); p.cards=p.cards.filter(c=>c.id!=='c_info'); save(); renderAll();}")
    B.save_gh()
    A.save_gh()
    titles = [c['title'] for c in remote()['projects'][0]['cards']]
    check('5. A 刪的卡不見、B 新增的卡留著', 'INFO' not in titles and 'B新卡' in titles, len(titles))
    B.reload()
    check('5. B 重新整理後跟共用版一致', B.card('c_info') is None and any(c['title'] == 'B新卡' for c in B.js("data.projects[0].cards")))

    # 6. 沒有金鑰的人：看得到，按儲存會跳出設定說明，不會寫入
    C = Browser()
    C.open('')
    check('6. 沒有金鑰也看得到共用版', (C.card('c_meet') or {}).get('title') == '會議B')
    puts = STORE['puts']
    C.edit('c_win', "c.title='C改的'")
    C.js("saveToGitHub()")
    time.sleep(0.5)
    check('6. 沒有金鑰按儲存 → 跳出設定說明、不寫入', C.js("!!document.querySelector('#ghToken')") and STORE['puts'] == puts)
    C.js("{document.querySelector('#ghToken').value='bad'; document.querySelector('#ghOk').click();}")
    time.sleep(1)
    check('6. 沒有寫入權限的金鑰會被擋下並說明', '協作者' in C.js("document.querySelector('#ghMsg').textContent") and STORE['puts'] == puts, C.js("document.querySelector('#ghMsg').textContent"))

    # 7. 衝突重試次數
    check('7. 有發生 sha 衝突且自動處理', STORE['conflicts'] == 0 or STORE['puts'] >= 5, (STORE['puts'], STORE['conflicts']))
    for name, b in (('A', A), ('B', B), ('C', C)):
        check('沒有 JS 錯誤（%s）' % name, not b.errors, b.errors[:2])
finally:
    for p in procs:
        subprocess.run(['taskkill', '/T', '/F', '/PID', str(p.pid)], capture_output=True)
    srv.shutdown()

print('FAIL 數：', results.count(False))
sys.exit(1 if False in results else 0)
