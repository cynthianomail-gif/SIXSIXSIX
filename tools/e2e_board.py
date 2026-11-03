# -*- coding: utf-8 -*-
"""看板 E2E：headless Chrome + CDP 實際操作（總覽、拖拉、編輯、回推套用、重新整理資料還在、舊資料搬移），並截各分頁圖。
用法：python -X utf8 tools/e2e_board.py <截圖輸出資料夾>
"""
import base64
import json
import os
import shutil
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
OLD = os.path.join(ROOT, '專案看板.html.bak-20261103')
OUT = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix='kanban_e2e_')
os.makedirs(OUT, exist_ok=True)
LS = "data"  # 頁面上的全域資料（新開的頁面在第一次修改前不會寫 localStorage）


def free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    p = s.getsockname()[1]
    s.close()
    return p


port = free_port()
prof = tempfile.mkdtemp(prefix='kanban_e2e_prof_')
proc = subprocess.Popen([CHROME, '--headless=new', '--disable-gpu', '--remote-debugging-port=%d' % port,
                         '--user-data-dir=' + prof, '--window-size=1500,950', 'about:blank'])
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
    errors = []

    def cdp(method, **params):
        mid[0] += 1
        ws.send(json.dumps({'id': mid[0], 'method': method, 'params': params}))
        while True:
            m = json.loads(ws.recv())
            if m.get('method') == 'Runtime.exceptionThrown':
                errors.append(m['params']['exceptionDetails'].get('exception', {}).get('description', str(m['params'])))
            if m.get('id') == mid[0]:
                return m.get('result', {})

    def js(expr):
        r = cdp('Runtime.evaluate', expression=expr, awaitPromise=True, returnByValue=True)
        if 'exceptionDetails' in r:
            raise RuntimeError(r['exceptionDetails'].get('exception', {}).get('description'))
        return r['result'].get('value')

    def check(name, cond, detail=''):
        results.append(bool(cond))
        print(('PASS ' if cond else 'FAIL ') + name + ('  ' + str(detail) if detail else ''))

    def shot(name, full=False):
        time.sleep(1.4)
        params = {'format': 'png'}
        if full:
            h = js("document.documentElement.scrollHeight")
            params.update(captureBeyondViewport=True, clip={'x': 0, 'y': 0, 'width': 1500, 'height': h, 'scale': 1})
        r = cdp('Page.captureScreenshot', **params)
        with open(os.path.join(OUT, name + '.png'), 'wb') as f:
            f.write(base64.b64decode(r['data']))

    def tab_(name):
        js("document.querySelector('[data-tab=\"%s\"]').click()" % name)

    def drag(card_id, col_key, top=True):
        js("""{
          const card = document.querySelector('.card[data-id="%s"]');
          const col = document.querySelector('.column[data-key="%s"]');
          const first = col.querySelector('.card');
          const dt = new DataTransfer();
          card.dispatchEvent(new DragEvent('dragstart', {bubbles:true, dataTransfer:dt}));
          const y = first && %s ? first.getBoundingClientRect().top + 2 : 99999;
          col.dispatchEvent(new DragEvent('dragover', {bubbles:true, cancelable:true, dataTransfer:dt, clientX:10, clientY:y}));
          col.dispatchEvent(new DragEvent('drop', {bubbles:true, cancelable:true, dataTransfer:dt, clientX:10, clientY:y}));
          card.dispatchEvent(new DragEvent('dragend', {bubbles:true, dataTransfer:dt}));
        }""" % (card_id, col_key, 'true' if top else 'false'))

    cdp('Runtime.enable')
    cdp('Page.enable')
    cdp('Page.navigate', url=PAGE)
    time.sleep(2.5)

    # 1. 總覽（預設分頁）
    check('預設開總覽', js("!document.querySelector('#tab-overview').classList.contains('hidden')"))
    check('完成率圓環 4%（1/24）', js("document.querySelector('.ring .pct').textContent") == '4%', js("document.querySelector('.ring .cap').textContent"))
    check('外包準備 3 張階段小卡', js("document.querySelectorAll('.sgrid')[0].querySelectorAll('.scard').length") == 3)
    check('製作流程管線 8 個節點', js("document.querySelectorAll('.pnode').length") == 8)
    check('兩週行事曆 10 個工作天', js("document.querySelectorAll('.cday:not(.over)').length") == 10)
    titles = js("[...document.querySelectorAll('.scard .nm')].map(e=>e.textContent)")
    check('分類＝庶務／分工／廠商', titles[:3] == ['庶務', '分工', '廠商'], titles[:3])
    longest = js("Math.max(..." + LS + ".projects[0].cards.map(c=>c.title.length))")
    check('卡片標題都在 10 字內', longest <= 10, longest)
    check('沒有 US$1,000 卡片或清單', 'US$1,000' not in js("JSON.stringify(" + LS + ")") if js("localStorage.getItem('project-kanban-v1')") else 'US$1,000' not in js("document.body.innerText"))
    shot('1_總覽', full=True)

    # 2. 點階段小卡 → 跳到看板該欄
    js("document.querySelector('.scard[data-stage=\"st_team\"]').click()")
    check('點小卡跳到看板', js("!document.querySelector('#tab-board').classList.contains('hidden')"))
    check('看板 11 欄（空欄縮成細條）', js("document.querySelectorAll('.column').length") == 11, js("document.querySelectorAll('.column.slim').length"))
    check('初始 24 張卡', js("document.querySelectorAll('.card').length") == 24)
    one_line = "Math.max(...[...document.querySelectorAll('.card .cm')].map(e=>e.getBoundingClientRect().height))"
    check('卡片底列都是一行（依階段）', js(one_line) <= 28, js(one_line))
    shot('2_看板_依階段')

    # 3. 拖拉：提供會議時段 庶務 → Excel 最上面
    drag('c_slots', 'st1', top=True)
    stage = js(LS + ".projects[0].cards.find(c=>c.id==='c_slots').stageId")
    check('拖拉換階段並存檔', stage == 'st1', stage)
    check('拖到欄位最上面', js("document.querySelector('.column[data-key=\"st1\"] .card').dataset.id") == 'c_slots')
    check('庶務剩 1 張', js(LS + ".projects[0].cards.filter(c=>c.stageId==='st_misc').length") == 1)
    # 拖進縮起來的空欄
    drag('c_rvrd', 'st6', top=False)
    check('可拖進空的階段', js(LS + ".projects[0].cards.find(c=>c.id==='c_rvrd').stageId") == 'st6')

    # 4. 依狀態，拖到完成
    js("document.querySelector('#grpSeg [data-g=\"status\"]').click()")
    check('依狀態 5 欄', js("document.querySelectorAll('.column').length") == 5)
    check('卡片底列都是一行（依狀態）', js(one_line) <= 28, js(one_line))
    js("{const s=document.createElement('style'); s.id='narrow'; s.textContent='.column{flex-basis:190px!important}'; document.head.appendChild(s)}")
    check('欄位壓到 190px 仍是一行', js(one_line) <= 28, js(one_line))
    shot('3b_窄欄')
    js("document.getElementById('narrow').remove()")
    drag('c_q', 'done', top=False)
    c = js(LS + ".projects[0].cards.find(c=>c.id==='c_q')")
    check('拖到完成：狀態＝done、留紀錄', c['status'] == 'done' and '進行中 → 完成' in c['log'][0]['text'], c['log'][0]['text'])
    shot('3_看板_依狀態')

    # 5. 編輯卡片
    js("document.querySelector('.card[data-id=\"c_est2d\"]').click()")
    js("document.querySelector('[data-picker=\"owners\"] [data-pid=\"p_laura\"]').click()")
    js("document.querySelector('#fDue').value='2026-11-06'")
    js("{document.querySelector('#fAddCheck').click(); const a=[...document.querySelectorAll('[data-ct]')]; a[a.length-1].value='2D 工時表'; a[a.length-1].dispatchEvent(new Event('input'))}")
    # 在負責人欄直接新增一個人
    js("document.querySelector('[data-addp=\"owners\"]').click()")
    js("{const i=document.querySelector('.add-p-in'); i.value='小明'; i.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}))}")
    on = js("[...document.querySelectorAll('[data-picker=\"owners\"] button.on')].map(b=>b.textContent)")
    rev = js("[...document.querySelectorAll('[data-picker=\"reviewers\"] button[data-pid]')].map(b=>b.textContent+(b.classList.contains('on')?'*':''))")
    check('負責人欄直接新增：新人已勾選', '小明小' in ''.join(on) or any('小明' in t for t in on), on)
    check('驗收欄也出現新人但沒勾', any(t.endswith('小明') for t in rev), rev)
    check('新人放進企劃那一行、＋仍在行尾', js("document.querySelector('[data-picker=\"owners\"] .pk-row[data-dept=\"企劃\"] .pk-chips').lastElementChild.dataset.addp") == 'owners' and '小明' in js("document.querySelector('[data-picker=\"owners\"] .pk-row[data-dept=\"企劃\"]').textContent"))
    js("document.querySelector('[data-addp=\"owners\"]').click()")
    js("{const i=document.querySelector('.add-p-in'); i.value='小明'; i.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}))}")
    check('同名不重複新增', js(LS + ".people.filter(p=>p.name==='小明').length") == 1)
    groups = js("[...document.querySelectorAll('[data-picker=\"owners\"] .pk-g')].map(e=>e.textContent)")
    check('負責人選單一類一行（含空的 RD）', groups == ['企劃', '美術', 'RD', '營運', '群組'], groups)
    # 自訂標籤
    js("document.querySelector('#fAddTag').click()")
    js("{const i=document.querySelector('#fTags .add-p-in'); i.value='急件'; i.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}))}")
    check('新增標籤後已勾選', js("[...document.querySelectorAll('#fTags button.on')].map(b=>b.dataset.tag)") == ['急件'])
    shot('4_卡片編輯')
    js("document.querySelector('#fSave').click()")
    check('存檔後卡片有標籤', js(LS + ".projects[0].cards.find(c=>c.id==='c_est2d').tags") == ['急件'])
    check('卡片上顯示標籤', js("document.querySelector('.card[data-id=\"c_est2d\"] .tag').textContent") == '急件')
    js("{const s=document.querySelector('#tagSel'); s.value='急件'; s.dispatchEvent(new Event('change'))}")
    check('標籤篩選只剩 1 張', js("document.querySelectorAll('.card').length") == 1)
    js("{const s=document.querySelector('#tagSel'); s.value=''; s.dispatchEvent(new Event('change'))}")
    check('等前置顯示為「等待」', js("document.querySelector('.card[data-id=\"c_estall\"] .flag.dep').textContent") == '等待')
    check('卡片不再顯示對外', js("document.querySelectorAll('.card .flag.ext, .card svg').length") == 0)
    js("document.querySelector('.card[data-id=\"c_est2d\"]').click()")
    js("document.querySelector('#fSave').click()")
    c2 = js(LS + ".projects[0].cards.find(c=>c.id==='c_est2d')")
    xm = js(LS + ".people.find(p=>p.name==='小明').id")
    check('存檔後卡片負責人含新人', xm in c2['owners'], c2['owners'])
    # 移除測試用的新人，讓後面的檢查維持原本的成員數
    js("{const id=" + LS + ".people.find(p=>p.name==='小明').id; " + LS + ".people=" + LS + ".people.filter(p=>p.id!==id); const c=" + LS + ".projects[0].cards.find(c=>c.id==='c_est2d'); c.owners=c.owners.filter(i=>i!==id); save();}")
    js("document.querySelector('.card[data-id=\"c_est2d\"]').click()")
    js("document.querySelector('#fSave').click()")
    c = js(LS + ".projects[0].cards.find(c=>c.id==='c_est2d')")
    check('編輯存檔：負責人／截止日／清單', c['owners'] == ['p_laura'] and c['due'] == '2026-11-06' and c['checklist'][0]['t'] == '2D 工時表', (c['owners'], c['due']))

    # 6. 時程回推
    tab_('schedule')
    js("{const a=document.querySelector('#anchorIn'); a.value='2027-06-30'; a.dispatchEvent(new Event('change'))}")
    rows = js("document.querySelectorAll('.tl-row').length")
    check('星城 2027 回推 23 列', rows == 23, rows)
    shot('5_時程', full=True)
    js("document.querySelector('#applyTpl').click()")
    d = js(LS + ".projects[0]")
    rd = next((x for x in d['cards'] if x['title'] == 'RD初版'), None)
    check('套用後新增 23 張（共 47）', len(d['cards']) == 47, len(d['cards']))
    check('RD初版 進 RD 階段且有日期', rd and rd['stageId'] == 'st6' and rd['due'] == '2027-05-18', rd and rd['due'])
    js("document.querySelector('#applyTpl').click()")
    check('再套用一次不重複新增', js(LS + ".projects[0].cards.length") == 47)

    # 7. 總覽在有日期之後
    tab_('overview')
    check('時程圖有階段列', js("document.querySelectorAll('.g-row').length") > 3)
    shot('6_總覽_套用回推後', full=True)

    # 8. 成員
    tab_('people')
    check('成員 4 張卡', js("document.querySelectorAll('.pcard').length") == 4)
    heads = js("[...document.querySelectorAll('.dept-h b')].map(e=>e.textContent)")
    check('成員分區：企劃／美術／RD／營運／群組', heads == ['企劃', '美術', 'RD', '營運', '群組'], heads)
    js("document.querySelector('[data-adddept=\"RD\"]').click()")
    newp = js(LS + ".people[" + LS + ".people.length-1]")
    check('在 RD 區直接新增成員', newp['dept'] == 'RD' and js("document.querySelectorAll('.pcard').length") == 5, newp.get('dept'))
    js("{const s=document.querySelector('.pcard[data-pid=\"" + newp['id'] + "\"] [data-f=\"dept\"]'); s.value='美術'; s.dispatchEvent(new Event('change'))}")
    check('改類別後移到美術區', js(LS + ".people.filter(p=>p.dept==='美術').length") == 2)
    js("{const id='" + newp['id'] + "'; data.people=data.people.filter(p=>p.id!==id); save(); renderPeople();}")
    js("{const i=document.querySelector('.pcard[data-pid=\"p_melodie\"] [data-f=\"name\"]'); i.value='蕭妍安(Melodie)'; i.dispatchEvent(new Event('change'))}")
    check('成員頁沒有 Teams 帳號欄與提醒設定', js("document.querySelectorAll('[data-f=\"teams\"],[data-f=\"webhook\"],#sFlow,#pvBtn,[data-test]').length") == 0)
    check('網頁裡沒有信箱', '@wanin.tw' not in js("document.documentElement.outerHTML"))
    shot('7_成員')

    # 9. 重新整理後資料還在
    cdp('Page.reload')
    time.sleep(2)
    n = js(LS + ".projects[0].cards.length")
    mel = js(LS + ".people.find(p=>p.id==='p_melodie').name")
    check('重新整理後保留 47 張與成員設定', n == 47 and mel == '蕭妍安(Melodie)', (n, mel))

    # 10. 舊版資料（第 1 版初始資料＋使用者改動）自動搬到新格式
    js("localStorage.clear()")
    old_html = os.path.join(os.path.dirname(PAGE.replace('file:///', '').split('#')[0]), '_e2e_old.html')
    shutil.copyfile(OLD, old_html)  # 備份檔副檔名不是 .html，瀏覽器會當純文字，所以複製一份來開（同資料夾＝同 localStorage）
    cdp('Page.navigate', url='file:///' + old_html.replace('\\', '/'))
    time.sleep(2)
    js("document.querySelector('[data-tab=\"people\"]').click()")
    js("{const i=document.querySelector('#sAhead'); i.value='3'; i.dispatchEvent(new Event('change'))}")  # 讓舊版寫入 localStorage
    js("{const d=" + LS + "; const c=d.projects[0].cards.find(c=>c.id==='c_nda'); c.status='done'; c.owners=['p_laura']; c.log.unshift({at:'x',text:'改過'}); localStorage.setItem('project-kanban-v1', JSON.stringify(d));}")
    os.remove(old_html)
    cdp('Page.navigate', url=PAGE)
    time.sleep(2.5)
    # 觸發一次存檔以寫回搬移結果
    js("save()")
    d = js(LS)
    p = d['projects'][0]
    nda = next(c for c in p['cards'] if c['id'] == 'c_nda')
    check('舊資料：短標題＋新分類', nda['title'] == 'NDA 簽署' and nda['stageId'] == 'st_vendor', (nda['title'], nda['stageId']))
    check('舊資料：保留使用者改的狀態與負責人', nda['status'] == 'done' and nda['owners'] == ['p_laura'], (nda['status'], nda['owners']))
    check('舊資料：刪掉 US$1,000 卡片', not any(c['id'] == 'c_credit' for c in p['cards']) and 'US$1,000' not in json.dumps(p, ensure_ascii=False))
    check('舊資料：24 張、沒有 st0', len(p['cards']) == 24 and not any(s['id'] == 'st0' for s in p['stages']), len(p['cards']))
    check('舊資料：假日設定保留、Teams 設定清掉', '2026-12-25' in d['settings']['holidays'] and 'remindDaysAhead' not in d['settings'] and d.get('seedRev') == 2)

    # 11. 手機寬度
    cdp('Emulation.setDeviceMetricsOverride', width=390, height=800, deviceScaleFactor=1, mobile=True)
    js("document.querySelector('[data-tab=\"overview\"]').click()")
    time.sleep(0.6)
    sw = js("document.documentElement.scrollWidth")
    check('390px 寬總覽不橫向溢出', sw <= 392, sw)
    shot('8_手機_總覽')

    check('頭像取姓', js("initial('初儀君')+initial('蕭妍安(Melodie)')+initial('Melodie 蕭妍安')") == '初蕭M')
    check('沒有 JS 錯誤', not errors, errors[:3])
    ws.close()
finally:
    subprocess.run(['taskkill', '/T', '/F', '/PID', str(proc.pid)], capture_output=True)

print('截圖：', OUT)
print('FAIL 數：', results.count(False))
sys.exit(1 if False in results else 0)
