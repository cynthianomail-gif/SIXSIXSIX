# -*- coding: utf-8 -*-
"""拖拉實測：用 CDP 模擬真實滑鼠拖曳（按下 → 分段移動 → 放開），不是直接丟 DragEvent。
判準：放開後的順序＝放開前畫面上佔位框的位置（看到哪裡就放到哪裡）。
情境：同欄往下／往上排序、拖到隔壁欄、拖到空欄、拖到畫面外（邊緣自動捲動）、依狀態、新卡片。
用法：python -X utf8 tools/e2e_dnd.py
"""
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


def free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    p = s.getsockname()[1]
    s.close()
    return p


port = free_port()
prof = tempfile.mkdtemp(prefix='kanban_dnd_')
proc = subprocess.Popen([CHROME, '--headless=new', '--disable-gpu', '--remote-debugging-port=%d' % port,
                         '--user-data-dir=' + prof, '--window-size=1400,900', 'about:blank'])
results = []
try:
    for _ in range(50):
        try:
            tabs = json.load(urllib.request.urlopen('http://127.0.0.1:%d/json' % port))
            break
        except Exception:
            time.sleep(0.2)
    tab = next(t for t in tabs if t['type'] == 'page')
    ws = websocket.create_connection(tab['webSocketDebuggerUrl'], suppress_origin=True, timeout=30)
    mid = [0]
    events = []

    def cdp(method, **params):
        mid[0] += 1
        ws.send(json.dumps({'id': mid[0], 'method': method, 'params': params}))
        while True:
            m = json.loads(ws.recv())
            if 'method' in m:
                events.append(m)
            if m.get('id') == mid[0]:
                return m.get('result', {})

    def js(expr):
        r = cdp('Runtime.evaluate', expression=expr, awaitPromise=True, returnByValue=True)
        if 'exceptionDetails' in r:
            raise RuntimeError(str(r['exceptionDetails'])[:300])
        return r['result'].get('value')

    def check(name, cond, detail=''):
        results.append(bool(cond))
        print(('PASS ' if cond else 'FAIL ') + name + ('  ' + str(detail) if detail else ''))

    def rect(sel, scroll=False):
        return js("(()=>{const e=document.querySelector(%s); if(!e) return null; %s const r=e.getBoundingClientRect(); return {cx:r.left+r.width/2, cy:r.top+r.height/2, l:r.left, t:r.top, r:r.right, b:r.bottom};})()"
                  % (json.dumps(sel), "e.scrollIntoView({block:'nearest',inline:'nearest'});" if scroll else ''))

    def col_ids(key, with_ph=False):
        return js("[...document.querySelectorAll('.column[data-key=\"%s\"] .col-body > *')].map(e=>e.classList.contains('placeholder')?'PH':e.dataset.id).filter(x=>%s)" % (key, 'x' if with_ph else "x && x!=='PH'"))

    def real_drag(src_sel, target, steps=14, hold_edge=0, col_key=None):
        """target：無參數函式，在來源卡片捲進畫面「之後」才算目標座標。回傳放開前目標欄的畫面順序（含 PH）。"""
        s = rect(src_sel, scroll=True)
        x0, y0 = s['cx'], s['cy']
        x1, y1 = target()
        cdp('Input.setInterceptDrags', enabled=True)
        events.clear()
        cdp('Input.dispatchMouseEvent', type='mousePressed', x=x0, y=y0, button='left', clickCount=1)
        cdp('Input.dispatchMouseEvent', type='mouseMoved', x=x0 + 6, y=y0 + 6, button='left', buttons=1)
        cdp('Input.dispatchMouseEvent', type='mouseMoved', x=x0 + 12, y=y0 + 12, button='left', buttons=1)
        time.sleep(0.15)
        cdp('Runtime.evaluate', expression='1')
        drag = next((e['params']['data'] for e in events if e.get('method') == 'Input.dragIntercepted'), None)
        if not drag:
            cdp('Input.dispatchMouseEvent', type='mouseReleased', x=x0, y=y0, button='left')
            cdp('Input.setInterceptDrags', enabled=False)
            return None
        cdp('Input.dispatchDragEvent', type='dragEnter', x=x0, y=y0, data=drag)
        for i in range(1, steps + 1):
            cdp('Input.dispatchDragEvent', type='dragOver', x=x0 + (x1 - x0) * i / steps, y=y0 + (y1 - y0) * i / steps, data=drag)
            time.sleep(0.02)
        for _ in range(hold_edge):
            cdp('Input.dispatchDragEvent', type='dragOver', x=x1, y=y1, data=drag)
            time.sleep(0.03)
        if hold_edge and col_key:   # 捲過去之後，再移到目標欄裡面
            t = rect('.column[data-key="%s"] .col-body' % col_key)
            x1, y1 = t['cx'], t['t'] + 20
            for i in range(1, 6):
                cdp('Input.dispatchDragEvent', type='dragOver', x=x1, y=y1, data=drag)
                time.sleep(0.02)
        seen = col_ids(col_key, with_ph=True) if col_key else None
        if os.environ.get('DND_DEBUG'):
            print('   [debug] drop at', round(x1), round(y1), 'elementFromPoint:', js("(()=>{const e=document.elementFromPoint(%f,%f); const c=e&&e.closest('.column'); return (e?e.className:'-')+' | col='+(c?c.dataset.key+' '+c.className:'none')+' | board='+document.querySelector('#board').className})()" % (x1, y1)))
        cdp('Input.dispatchDragEvent', type='drop', x=x1, y=y1, data=drag)
        cdp('Input.dispatchMouseEvent', type='mouseReleased', x=x1, y=y1, button='left')
        cdp('Input.setInterceptDrags', enabled=False)
        time.sleep(0.3)
        return seen

    def expect_from(seen, cid):
        return [cid if x == 'PH' else x for x in seen if x != cid]

    cdp('Runtime.enable')
    cdp('Page.enable')
    cdp('Page.navigate', url=PAGE)
    time.sleep(2.5)
    js("document.querySelector('[data-tab=\"board\"]').click()")
    time.sleep(0.4)

    # A. 同欄往下：分工第 1 張拖到第 4 張卡片下半部
    before = col_ids('st_team')
    seen = real_drag('.card[data-id="%s"]' % before[0], lambda: (lambda r: (r['cx'], r['b'] - 6))(rect('.card[data-id="%s"]' % before[3])), col_key='st_team')
    after = col_ids('st_team')
    check('同欄往下排序（放到第 4 張下面）', after.index(before[0]) == 3 and len(after) == len(before), after)

    # B. 同欄往上：分工最後一張拖到第一張的上半部
    before = col_ids('st_team')
    seen = real_drag('.card[data-id="%s"]' % before[-1], lambda: (lambda r: (r['cx'], r['t'] + 4))(rect('.card[data-id="%s"]' % before[0])), col_key='st_team')
    after = col_ids('st_team')
    check('同欄往上排序（放到最上面）', after[0] == before[-1] and len(after) == len(before), after)

    # C. 拖到隔壁欄最下面
    seen = real_drag('.card[data-id="c_est2d"]', lambda: (lambda r: (r['cx'], r['t'] - 20))(rect('.column[data-key="st_vendor"] .col-add')), col_key='st_vendor')
    check('拖到隔壁欄', col_ids('st_vendor') == expect_from(seen, 'c_est2d'), col_ids('st_vendor'))

    # D. 拖到空欄（音樂音效）
    js("document.querySelector('#board').scrollLeft=0")
    real_drag('.card[data-id="c_estfx"]', lambda: (lambda r: (r['r'] - 12, r['t'] + 220))(rect('#board')), hold_edge=40, col_key='st3')
    check('拖到空欄', col_ids('st3') == ['c_estfx'], js("data.projects[0].cards.find(c=>c.id==='c_estfx').stageId"))

    # E. 拖到畫面外的欄位（上架）：停在看板右邊緣等自動捲動，再放進上架欄
    js("document.querySelector('#board').scrollLeft=0")
    vis = js("document.querySelector('.column[data-key=\"st8\"]').getBoundingClientRect().left < innerWidth - 100")
    seen = real_drag('.card[data-id="c_win"]', lambda: (lambda r: (r['r'] - 12, r['t'] + 220))(rect('#board')), hold_edge=80, col_key='st8')
    scrolled = js("document.querySelector('#board').scrollLeft")
    check('拖到右邊緣會自動捲動，放下後不跳回左邊', vis or scrolled > 100, (vis, scrolled))
    check('捲動後放進上架欄', 'c_win' in col_ids('st8'), (seen, col_ids('st8')))

    # F. 依狀態：拖到「進行中」
    js("document.querySelector('#grpSeg [data-g=\"status\"]').click()")
    time.sleep(0.3)
    real_drag('.card[data-id="c_asset"]', lambda: (lambda r: (r['cx'], r['t'] - 20))(rect('.column[data-key="doing"] .col-add')))
    check('依狀態拖到進行中', js("data.projects[0].cards.find(c=>c.id==='c_asset').status") == 'doing')
    check('拖完沒有殘留的佔位框', js("document.querySelectorAll('.placeholder').length") == 0)

    # G. 在空欄用鍵盤新增卡片（真的按鍵），新卡片要立刻出現，之後還能拖
    js("document.querySelector('#grpSeg [data-g=\"stage\"]').click()")
    time.sleep(0.3)
    js("document.querySelector('.column[data-key=\"st4\"]').click()")
    time.sleep(0.2)
    js("document.querySelector('[data-add=\"st4\"]').focus()")
    cdp('Input.insertText', text='翻譯送件')
    cdp('Input.dispatchKeyEvent', type='rawKeyDown', key='Enter', code='Enter', windowsVirtualKeyCode=13)
    cdp('Input.dispatchKeyEvent', type='char', key='Enter', code='Enter', text='\r', windowsVirtualKeyCode=13)
    cdp('Input.dispatchKeyEvent', type='keyUp', key='Enter', code='Enter', windowsVirtualKeyCode=13)
    time.sleep(0.3)
    shown = js("[...document.querySelectorAll('.column[data-key=\"st4\"] .card .ct')].map(e=>e.textContent)")
    check('空欄新增卡片會立刻顯示', shown == ['翻譯送件'], shown)
    nid = js("data.projects[0].cards.find(c=>c.title==='翻譯送件').id")
    real_drag('.card[data-id="%s"]' % nid, lambda: (lambda r: (r['cx'], r['cy']))(rect('.column[data-key="st5"] .col-body', scroll=False)))
    check('新卡片可以拖到別欄', nid in col_ids('st5'), col_ids('st5'))
    n0 = js("data.projects[0].cards.length")
    js("{const i=document.querySelector('[data-add=\"st5\"]'); i.focus(); i.value='選字中'; i.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',isComposing:true,bubbles:true}));}")
    check('輸入法選字的 Enter 不會新增', js("data.projects[0].cards.length") == n0)
    errs = [e['params']['exceptionDetails'].get('exception', {}).get('description', '')[:120] for e in events if e.get('method') == 'Runtime.exceptionThrown']
    check('畫面沒有中斷（JS 錯誤）', not errs, errs[:2])
    ws.close()
finally:
    subprocess.run(['taskkill', '/T', '/F', '/PID', str(proc.pid)], capture_output=True)

print('FAIL 數：', results.count(False))
sys.exit(1 if False in results else 0)
