#!/usr/bin/env python3
"""更新 info_cutoff：博主画像 frontmatter + 看板 bloggers 表（2026-09-07 起 vault 博主控制台.md 退役）

用法: python3 xq_update_cutoff.py <nickname> <ISO时间>
  nickname  博主名
  ISO时间   格式 YYYY-MM-DDTHH:mm:ss（通常为采集完成时间）

行为：① 画像 博主/<名>/<名>.md 的 info_cutoff + updateDate
      ② 看板 MySQL bloggers.info_cutoff（POST /api/bloggers/update）
"""
import re, sys, os, json, urllib.request

VAULT = '/Users/jianglb/Library/Mobile Documents/iCloud~md~obsidian/Documents/投资知识库'
nickname, new_cutoff = sys.argv[1], sys.argv[2]

changed = []

# 1. 博主画像
profile = os.path.join(VAULT, '博主', nickname, f'{nickname}.md')
if os.path.exists(profile):
    with open(profile) as f:
        content = f.read()
    updated = False
    if re.search(r'^info_cutoff:.*$', content, re.M):
        content = re.sub(r'^info_cutoff:.*$', f'info_cutoff: {new_cutoff}', content, count=1, flags=re.M)
        updated = True
    if re.search(r'^updateDate:.*$', content, re.M):
        content = re.sub(r'^updateDate:.*$', f'updateDate: {new_cutoff[:10]}', content, count=1, flags=re.M)
    if updated:
        with open(profile, 'w') as f:
            f.write(content)
        changed.append(f'画像 {profile}')
else:
    changed.append(f'画像不存在（跳过）：博主/{nickname}/{nickname}.md')

# 2. 看板博主控制台（MySQL bloggers 表权威；vault 博主控制台.md 已退役）
# 闸门（2026-09-27）：loopback 也要 Bearer mcpToken，凭据从仓库 config.json 读
def _auth():
    try:
        with open(os.path.expanduser('~/Project/investment-dashboard/src/config.json')) as f:
            tok = json.load(f).get('mcpToken', '')
        return {'Authorization': 'Bearer ' + tok} if tok else {}
    except Exception:
        return {}

try:
    req = urllib.request.Request('http://127.0.0.1:8698/api/bloggers/update',
        data=json.dumps({'name': nickname, 'infoCutoff': new_cutoff}).encode(),
        headers={'Content-Type': 'application/json', **_auth()}, method='POST')
    with urllib.request.urlopen(req, timeout=15) as resp:
        r = json.load(resp)
    changed.append('看板 bloggers.info_cutoff ✓' if r.get('ok') else f'看板更新失败: {r.get("error")}')
except Exception as e:
    changed.append(f'看板更新异常（画像已更新，稍后可重试）: {e}')

print('\n'.join(changed))
