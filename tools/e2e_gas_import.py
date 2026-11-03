# -*- coding: utf-8 -*-
"""驗證「匯入時程統籌看板 CSV」：筆數、抽樣對照 CSV、重匯不重複、日期變動會更新、使用者改的負責人與狀態不被蓋掉。
CSV 含同事姓名，不放進 repo；用法：python -X utf8 tools/e2e_gas_import.py <倒推檢核表.csv>
"""
import csv
import re
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

import websocket

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHROME = r'C:\Program Files\Google\Chrome\Application\chrome.exe'
PAGE = 'file:///' + os.path.join(ROOT, '專案看板.html').replace('\\', '/') + '#offline'
CSV_PATH = sys.argv[1]
TEXT = open(CSV_PATH, encoding='utf-8-sig').read()
ROWS = list(csv.reader(open(CSV_PATH, encoding='utf-8-sig')))
HEAD = ROWS[0]


def free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    p = s.getsockname()[1]
    s.close()
    return p


port = free_port()
proc = subprocess.Popen([CHROME, '--headless=new', '--disable-gpu', '--remote-debugging-port=%d' % port,
                         '--user-data-dir=' + tempfile.mkdtemp(prefix='kanban_gas_'), '--window-size=1400,900', 'about:blank'])
results = []
try:
    for _ in range(50):
        try:
            tabs = json.load(urllib.request.urlopen('http://127.0.0.1:%d/json' % port))
            break
        except Exception:
            time.sleep(0.2)
    ws = websocket.create_connection(next(t for t in tabs if t['type'] == 'page')['webSocketDebuggerUrl'], suppress_origin=True, timeout=60)
    mid = [0]
    errors = []

    def cdp(m, **p):
        mid[0] += 1
        ws.send(json.dumps({'id': mid[0], 'method': m, 'params': p}))
        while True:
            r = json.loads(ws.recv())
            if r.get('method') == 'Runtime.exceptionThrown':
                errors.append(r['params']['exceptionDetails'].get('exception', {}).get('description', '')[:150])
            if r.get('id') == mid[0]:
                return r.get('result', {})

    def js(e):
        r = cdp('Runtime.evaluate', expression=e, awaitPromise=True, returnByValue=True)
        if 'exceptionDetails' in r:
            raise RuntimeError(str(r['exceptionDetails'])[:300])
        return r['result'].get('value')

    def check(name, cond, detail=''):
        results.append(bool(cond))
        print(('PASS ' if cond else 'FAIL ') + name + ('  ' + str(detail) if detail else ''))

    cdp('Runtime.enable')
    cdp('Page.enable')
    cdp('Page.navigate', url=PAGE)
    time.sleep(2.5)
    n_games = sum(1 for r in ROWS[1:] if r[0].strip() and r[1].strip())

    s1 = js("(()=>{const s=importGasCsv(%s); save(); renderAll(); return s;})()" % json.dumps(TEXT))
    check('匯入：每款遊戲一個專案', s1['projects'] == n_games and s1['newProjects'] == n_games, s1)
    check('原本的 SIXSIXSIX 還在', js("data.projects.some(p=>p.id==='pj_666')"))

    # 期望卡片數：每款遊戲有值的節點數（應交／實際／狀態任一有值）
    groups = {}
    for i, h in enumerate(HEAD[5:], start=5):
        base = h.rsplit(' ', 1)[0]
        groups.setdefault(base, []).append(i)
    exp_cards = sum(1 for r in ROWS[1:] if r[1].strip() for b, idx in groups.items() if any(r[i].strip() for i in idx))
    check('卡片數＝CSV 有值的節點數', s1['newCards'] == exp_cards, (s1['newCards'], exp_cards))

    # 抽樣 3 款：每個節點的截止日＝應交、有實際就完成
    bad = []
    for r in [ROWS[1], ROWS[len(ROWS) // 2], ROWS[-1]]:
        code = r[1]
        cards = js("(()=>{const p=data.projects.find(p=>p.extId===%s); return p.cards.map(c=>({k:c.gasKey,due:c.due,st:c.status}))})()" % json.dumps(code))
        bykey = {c['k']: c for c in cards}
        for b, idx in groups.items():
            vals = {HEAD[i].rsplit(' ', 1)[1]: r[i].strip() for i in idx}
            if not any(vals.values()):
                continue
            key = b.split('[')[0].strip()
            c = bykey.get(key)
            if not c:
                bad.append((code, key, '沒有卡片'))
                continue
            if vals.get('應交', '') and c['due'] != vals['應交']:
                bad.append((code, key, c['due'], vals['應交']))
            want_done = bool(vals.get('實際')) or '已完成' in vals.get('狀態', '') or bool(re.search('製作完成|上線', r[2]))
            if want_done != (c['st'] == 'done'):
                bad.append((code, key, c['st'], vals))
    check('抽樣 3 款逐節點對照 CSV（截止日、完成）', not bad, bad[:3])

    lead_rows = [r for r in ROWS[1:] if r[3].strip()]
    if lead_rows:
        r = lead_rows[0]
        owners = js("(()=>{const p=data.projects.find(p=>p.extId===%s); const lead=data.people.find(x=>x.name===%s); const c=p.cards.find(c=>/開案組/.test(c.tags.join())); return [!!lead, lead&&lead.dept, c? c.owners.includes(lead.id):null]})()" % (json.dumps(r[1]), json.dumps(r[3].strip())))
        check('主企劃變成企劃成員、企劃類節點的負責人', owners[0] and owners[1] == '企劃' and owners[2] is not False, owners)

    # 重匯：不重複
    n_before = js("data.projects.reduce((n,p)=>n+p.cards.length,0)")
    s2 = js("(()=>{const s=importGasCsv(%s); save(); renderAll(); return s;})()" % json.dumps(TEXT))
    check('重匯同一份：不新增、不更新', s2['newProjects'] == 0 and s2['newCards'] == 0 and s2['updated'] == 0 and js("data.projects.reduce((n,p)=>n+p.cards.length,0)") == n_before, s2)

    # 使用者在看板上改負責人、狀態，再匯入一份「日期改過」的 CSV
    r0 = next(r for r in ROWS[1:] if r[1].strip())
    code = r0[1]
    col = next(i for i in range(5, len(HEAD)) if HEAD[i].endswith('應交') and r0[i].strip() and not r0[i + 1].strip())
    key = HEAD[col].rsplit(' ', 1)[0].split('[')[0].strip()
    js("(()=>{const p=data.projects.find(p=>p.extId===%s); const c=p.cards.find(c=>c.gasKey===%s); c.owners=['p_laura']; c.status='doing'; c.desc='我加的說明';})()" % (json.dumps(code), json.dumps(key)))
    rows2 = [list(r) for r in ROWS]
    i0 = next(i for i, r in enumerate(rows2) if r[1] == code)
    rows2[i0][col] = '2027-12-31'
    import io
    buf = io.StringIO()
    csv.writer(buf, lineterminator='\n').writerows(rows2)
    s3 = js("(()=>{const s=importGasCsv(%s); save(); renderAll(); return s;})()" % json.dumps(buf.getvalue()))
    c = js("(()=>{const p=data.projects.find(p=>p.extId===%s); return p.cards.find(c=>c.gasKey===%s)})()" % (json.dumps(code), json.dumps(key)))
    check('重匯：日期改過的卡更新截止日', s3['updated'] == 1 and c['due'] == '2027-12-31', (s3, c['due']))
    check('重匯：不蓋掉我改的負責人、狀態、說明', c['owners'] == ['p_laura'] and c['status'] == 'doing' and c['desc'] == '我加的說明', (c['owners'], c['status']))

    # 畫面：專案選單分組、切到匯入專案的總覽不報錯
    groups_lbl = js("[...document.querySelectorAll('#projSel optgroup')].map(g=>g.label+':'+g.children.length)")
    check('專案選單依 H5／星城分組', len(groups_lbl) >= 1, groups_lbl)
    js("{const s=document.querySelector('#projSel'); s.value=data.projects.find(p=>p.extId===%s).id; s.dispatchEvent(new Event('change'));}" % json.dumps(code))
    check('切到匯入的專案：總覽有內容', '上架' in js("document.querySelector('.hero .eyebrow').textContent") and js("document.querySelectorAll('.scard').length") > 0)
    js("document.querySelector('[data-tab=\"board\"]').click()")
    check('看板顯示該專案卡片', js("document.querySelectorAll('.card').length") == js("project().cards.length"))
    cdp('Page.reload')
    time.sleep(2)
    check('重新整理後記得上次看的專案', js("project().extId") == code)
    check('沒有 JS 錯誤', not errors, errors[:2])
    ws.close()
finally:
    subprocess.run(['taskkill', '/T', '/F', '/PID', str(proc.pid)], capture_output=True)

print('FAIL 數：', results.count(False))
sys.exit(1 if False in results else 0)
