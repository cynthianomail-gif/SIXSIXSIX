# -*- coding: utf-8 -*-
"""驗證看板的時程回推與原 Excel 快取值一致（期望值由子代理從 xlsx 快取值讀出）。
用法：python -X utf8 tools/verify_schedule.py
"""
import html
import json
import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHROME = r'C:\Program Files\Google\Chrome\Application\chrome.exe'
PAGE = os.path.join(ROOT, '專案看板.html')

CASES = [
    # 【星城】2026遊戲館回推 第 5 列 Game283，完成時間 2026-09-30
    ('xc2026:2026-09-30', {'F': '2025-11-30', 'H': '2026-03-25', 'I': '2026-04-17', 'K': '2026-06-01', 'L': '2026-06-23',
                           'N': '2026-07-08', 'O': '2026-07-27', 'P': '2026-08-17', 'Q': '2026-08-21', 'S': '2026-05-20',
                           'U': '2026-09-08', 'W': '2026-09-16', 'X': '2026-09-21'}),
    # 【H5】時程回推 game032（企劃 2XL、RD XL），完成時間 2026-06-26
    # 只比 2026 範圍內的日期：H5「假日清單」從 2026-01-01 才開始，原檔的 2D預計完成日(2025-07-14)、開案組Excel(2024-12-24)
    # 沒扣 2025 以前的假日；看板用星城國定假日表有扣，所以會早幾天（已用 H5 假日清單重算確認規則本身一致）
    # CC 是原檔快取值；CL（−21）、N（−10）是手算（6/19 端午扣掉）
    ('h5_2026:2026-06-26:2XL:XL', {'CC': '2026-04-20', 'CL': '2026-05-27', 'N': '2026-06-11'}),
]


def run(hash_):
    prof = tempfile.mkdtemp(prefix='kanban_sched_')
    url = 'file:///' + PAGE.replace('\\', '/') + '#sched=' + hash_
    out = subprocess.run([CHROME, '--headless=new', '--disable-gpu', '--user-data-dir=' + prof,
                          '--virtual-time-budget=3000', '--dump-dom', url], capture_output=True, timeout=90)
    dom = out.stdout.decode('utf-8', 'replace')
    m = re.search(r'<pre id="testOut"[^>]*>(.*?)</pre>', dom, re.S)
    if not m or not m.group(1).strip():
        raise SystemExit('取不到 #testOut：' + out.stderr.decode('utf-8', 'replace')[-400:])
    return {r[0]: (r[1], r[2]) for r in json.loads(html.unescape(m.group(1)))}


fails = 0
for h, exp in CASES:
    got = run(h)
    print('==', h)
    for k, v in exp.items():
        g = got.get(k, ('?', ''))
        ok = g[1] == v
        fails += not ok
        print('  %-4s %-28s 期望 %s  看板 %s  %s' % (k, g[0][:28], v, g[1], 'PASS' if ok else 'FAIL'))
print('FAIL 數：', fails)
sys.exit(1 if fails else 0)
