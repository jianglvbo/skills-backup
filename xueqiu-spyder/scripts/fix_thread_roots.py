#!/usr/bin/env python3
"""补齐 xq_thread.root_url 缺失的串（2026-10-02 串条聚合挂根帖卡的前置）。

机理：时间线模式采集时，被回复对象是评论（非帖子）的话，时间线引用卡里没有
/uid/statusid 链接 → root_url 落空（库里 268/682）。dialog-flow 定稿的补法＝
打开回复帖永久链接页，取页内上下文锚点（a.fake-anchor / a.replay-count 的
href=/uid/statusid#comment，statusid 即原帖）+ 原帖正文 .article__bd。

产出直接 UPDATE 库：root_url/root_author/root_content/root_meta，
thread_key 从 hash 升级为原帖 url（同 hash 组统一，'同一场对话'聚合键）。
"""
import os
import re
import sys
import json
import time
import argparse
import datetime as dt

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ego_browser import EgoBridge, BridgeError  # noqa: E402

JS_ANCHOR = """() => {
  const a = document.querySelector('a.fake-anchor, a.replay-count');
  const anchor = a ? (a.getAttribute('href') || '') : '';
  const m = anchor.match(/^\\/(\\d+)\\/(\\d+)/);
  const art = document.querySelector('.article__bd');
  const nameEl = document.querySelector('.article__bd blockquote') ? null : null;
  let author = '';
  const nm = document.querySelector('.article .avatar + div a, .detail__user a, a.avatar');
  // 原帖作者：文章头部的用户名（雪球文章页结构），取不到就留空
  const who = document.querySelector('.article__bd__user, .name, .user-name');
  if (who) author = who.textContent.trim().slice(0, 40);
  const metaEl = [...document.querySelectorAll('.article__bd + div, .detail__meta, .time')].map(e => e.textContent).join(' ');
  const meta = (metaEl.match(/\\d{2,4}-\\d{2}-\\d{2}[^·]*·[^·]*讨论[^\\n]*/) || [''])[0].trim();
  return { rootUrl: m ? ('https://xueqiu.com/' + m[1] + '/' + m[2]) : null,
           author, meta,
           content: art ? art.innerText.slice(0, 600) : '' };
}"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=0, help='最多处理多少条（0=全部）')
    ap.add_argument('--dry', action='store_true')
    args = ap.parse_args()

    import pymysql
    import json as _j
    cfg = _j.load(open(os.path.expanduser(
        '/Users/jianglb/Project/investment-dashboard/src/config.json')))
    m = cfg['mysql']
    db = pymysql.connect(host=m['host'], port=m['port'], user=m['user'],
                         password=m['password'], database=m['database'],
                         cursorclass=pymysql.cursors.DictCursor)
    cur = db.cursor()
    cur.execute("""SELECT id, blogger, trigger_url, thread_key FROM xq_thread
                    WHERE root_url IS NULL OR root_url = ''
                    ORDER BY id DESC""" + (f' LIMIT {args.limit}' if args.limit else ''))
    rows = cur.fetchall()
    print(f'待补 root：{len(rows)} 条')

    bridge = EgoBridge()
    bridge.start()
    page = bridge.main_page
    done = fixed = miss = 0
    try:
        for r in rows:
            done += 1
            url = r['trigger_url']
            try:
                page.goto(url, wait_until='domcontentloaded', timeout=20000)
                page.wait_for_timeout(1800)
                info = page.evaluate(JS_ANCHOR, None)
            except BridgeError as e:
                print(f'[{done}/{len(rows)}] 桥错误 {url}: {e}')
                miss += 1
                continue
            if not info or not info.get('rootUrl'):
                miss += 1
                print(f'[{done}/{len(rows)}] 无锚点（真无入口）{url}')
                continue
            root = info['rootUrl']
            old_tk = r['thread_key']
            # ⚠ 只锚 URL，不抓 root_content——在回复帖永久页上抓到的 .article__bd 是
            # 回复帖正文（文不对题），2026-10-02 实测写进 34 行脏数据已清。
            # 原帖正文快照该在 root_url 页面上抓（要多一跳导航），需要时另做。
            if not args.dry:
                cur.execute(
                    """UPDATE xq_thread SET root_url=%s, thread_key=%s WHERE id=%s""",
                    (root, root, r['id']))
                # 同 hash 组的兄弟串沿用同一 root（同一场对话）
                if old_tk and not old_tk.startswith('http'):
                    cur.execute(
                        """UPDATE xq_thread SET root_url=%s, thread_key=%s
                            WHERE thread_key=%s AND (root_url IS NULL OR root_url='')""",
                        (root, root, old_tk))
                db.commit()
            fixed += 1
            print(f'[{done}/{len(rows)}] root={root} ({r["blogger"]})')
            time.sleep(1.2)
    finally:
        bridge.stop()
        cur.close()
        db.close()
    print(f'完成：补上 {fixed}，无锚点 {miss} / 共 {len(rows)}')


if __name__ == '__main__':
    main()
