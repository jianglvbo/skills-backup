#!/usr/bin/env python3
"""雪球关注列表同步 → 看板博主控制台对比（前置步骤脚本化，2026-08-19 新增；2026-09-07 改读看板 API）

博主控制台权威 = 看板 MySQL bloggers 表（vault 博主控制台.md 已退役删除）。一键输出：
  1. 新增博主（关注中但看板未登记）
  2. 取关博主（看板登记但已不关注）
  3. ID 不一致（看板 ID 与关注列表不符）
  4. 博主层残留（看板无登记但 博主/<名>/ 存在文件夹）
  5. 待采集清单（雪球ID非空博主）

用法: python3 xq_sync_console.py [--dry-run|--apply]
  --dry-run（默认）只输出对比报告；Agent 向用户确认后
  --apply 实际落地变更：新增 → POST /api/bloggers 登记；取关 → 仅报告（须用户到看板确认后手工删除，涉及目录回收不自动执行）
  --half-year 新博主的「信息截止」基准（默认：半年前今天 17:50:00）

依赖：**ego lite**（已打开且已登录雪球；走 xueqiu-spyder 的 ego 通道）+ 看板服务（127.0.0.1:8698）

2026-09-16 迁移：浏览器层从 browser-act 换成 ego lite（用户口径「以后别用 chrome 了，用 ego lite」）。
ego lite 不暴露 CDP 端口，所以复用 xueqiu-spyder 的 ego 桥（ego_browser.EgoBridge）：
`page.goto(接口 URL)` → `page.evaluate(fetch)` → 解析 JSON。桥退出时自动关掉自己开的页签。
"""
import re, sys, os, json, datetime, argparse, urllib.request

# 复用统一接入层（ego lite；不再依赖 browser-act / Chrome）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from xq_ego import ego_session   # noqa: E402

VAULT = '/Users/jianglb/Library/Mobile Documents/iCloud~md~obsidian/Documents/投资知识库'
BLOGGER_DIR = os.path.join(VAULT, '博主')
API = 'http://127.0.0.1:8698'

def _auth():
    """看板登录闸门（2026-09-27）对 loopback 同样生效：脚本必须带 Bearer mcpToken，
    凭据从仓库 config.json 读（不进命令行、不进 git），缺失时返回空 dict（表现为 401）。"""
    try:
        with open(os.path.expanduser('~/Project/investment-dashboard/src/config.json')) as f:
            tok = json.load(f).get('mcpToken', '')
        return {'Authorization': 'Bearer ' + tok} if tok else {}
    except Exception:
        return {}

def api(path, payload=None):
    if payload is None:
        req = urllib.request.Request(API + path, headers=_auth())
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.load(r)
    req = urllib.request.Request(API + path, data=json.dumps(payload).encode(),
        headers={'Content-Type': 'application/json', **_auth()}, method='POST')
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)

def fetch_following():
    """分页拉取关注列表，返回 {screen_name: id}（走 ego 通道）

    与旧 browser-act 版的差别：不再 `navigate + get markdown` 再正则抠 JSON，
    而是直接在 ego 页面里 fetch 接口、拿原始 JSON 文本（同一登录态）。
    桥在脚本结束时统一退出，ego 里不留页签。
    """
    import time

    following, p = {}, 1
    with ego_session() as page:
        while True:
            url = (f"https://xueqiu.com/friendships/groups/members.json"
                   f"?gid=0&page={p}&count=50")
            page.goto(url)
            time.sleep(1.0)                       # 与旧实现同等的节流
            txt = page.text()
            if re.search(r"用户未登录|请先登录|访问验证|安全验证", txt):
                sys.exit("❌ 雪球未登录（或命中风控），无法同步关注列表——请先在 ego lite 里登录雪球")
            m = re.search(r"(\{.*\})", txt, re.S)
            if not m:
                break
            try:
                data = json.loads(m.group(1))
            except json.JSONDecodeError:
                break
            users = data.get("users", [])
            if not users:
                break
            for u in users:
                following[u["screen_name"]] = u["id"]
            if p >= data.get("maxPage", 1):
                break
            p += 1
        return following

def parse_console():
    """读看板博主控制台（MySQL bloggers 表），返回 {name: {id, is_xq, special, cutoff}} 与最大编号"""
    data = api('/api/bloggers/live')
    console = {}
    max_no = 0
    for b in data['data']['bloggers']:
        try: max_no = max(max_no, int(b.get('name') and 0 or 0) or 0)
        except Exception: pass
        console[b['name']] = {'id': b.get('xueqiuId') or '', 'is_xq': (b.get('platform') or '') == '雪球',
                              'platform': b.get('platform') or '',
                              'special': bool(b.get('special')), 'cutoff': b.get('infoCutoff') or '',
                              'registered': bool(b.get('registered'))}
    return console, max_no

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true', help='实际落地变更（默认 dry-run 只报告）')
    ap.add_argument('--half-year', default=None, help='新博主信息截止基准（默认半年前今天 17:50:00）')
    args = ap.parse_args()

    # 拉取关注列表（ego 通道；前置条件＝ego lite 已打开且已登录雪球）
    print('拉取关注列表（ego lite 通道）...', file=sys.stderr)
    following = fetch_following()

    console, max_no = parse_console()
    # 待采集 = 有雪球ID；「雪球已销户」只保留历史、不再采集（2026-09-16 用户拍板）
    xq_console = {n: v for n, v in console.items()
                  if v['id'] and v.get('platform') != '雪球已销户'}

    new = {n: i for n, i in following.items() if n not in console}
    unfollow = {n: v for n, v in xq_console.items() if n not in following}
    id_mismatch = {n: (v['id'], following[n]) for n, v in xq_console.items()
                   if n in following and str(following[n]) != v['id']}
    dirs = [d for d in os.listdir(BLOGGER_DIR) if os.path.isdir(os.path.join(BLOGGER_DIR, d)) and not d.startswith('.')]
    residual = [d for d in dirs if d not in console]

    print(f"\n===== 同步报告（{'dry-run，未改动' if not args.apply else '已落地'}）=====")
    print(f"关注列表: {len(following)} 人 | 控制台: {len(console)} 行 | 雪球ID非空: {len(xq_console)}")
    print(f"\n【新增】关注中但控制台未登记: {len(new)}")
    for n, i in new.items():
        print(f"  + {n} ({i})")
    print(f"\n【取关】控制台登记但已不关注: {len(unfollow)}")
    for n, v in unfollow.items():
        print(f"  - {n} ({v['id']})")
    print(f"\n【ID 不一致】: {len(id_mismatch)}")
    for n, (cid, fid) in id_mismatch.items():
        print(f"  ! {n}: 控制台={cid} vs 关注={fid}")
    print(f"\n【博主层残留】控制台无登记但文件夹存在: {len(residual)}")
    for d in residual:
        print(f"  ! {d}")

    # ---- apply 落地 ----
    if args.apply:
        half = args.half_year or (datetime.date.today() - datetime.timedelta(days=182)).strftime('%Y-%m-%dT17:50:00')
        added = 0
        for name, xid in sorted(new.items(), key=lambda x: x[1]):
            try:
                r = api('/api/bloggers', {'name': name, 'xueqiuId': str(xid), 'platform': '雪球', 'infoCutoff': half})
                print(f"  + 登记 {name} ({xid}): {'ok' if r.get('ok') else r.get('error')}")
                if r.get('ok'): added += 1
            except Exception as e:
                print(f"  + 登记 {name} 失败: {e}")
        if unfollow:
            print(f"\n⚠️ 取关 {len(unfollow)} 人需到看板博主控制台手工删除（涉及目录回收，脚本不自动执行）: {', '.join(sorted(unfollow))}")
        print(f"\n✅ 已落地：新增登记 {added} 人")
    else:
        print("\nℹ️ 使用 --apply 落地变更（新增走看板 API 登记；取关须看板手工处理）")

    # 待采集清单
    print(f"\n【待采集】控制台雪球ID非空博主: {len(xq_console)} 位（含新增需确认）")
    for n in sorted(xq_console, key=lambda x: x):
        mark = ' [新增?]' if n in new else ''
        print(f"  {n}\t{console[n]['id']}\t截止:{console[n].get('cutoff') or '(空)'}{mark}")

if __name__ == '__main__':
    main()
