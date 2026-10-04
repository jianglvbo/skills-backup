#!/usr/bin/env python3
"""雪球博主主页帖子 UI 采集器（2026-10-05 用户定稿「新方式」，重采/深窗口主力）

用户口径：进博主主页 → **逐条滚动**（每帖 scrollIntoView，页面肉眼可见）→ 点「展开」拿全文
→ 滚到底点「下一页」翻页；回复帖点「查看对话」开弹窗拿整链；转帖引用卡结构化为「回复内容」块；
专栏帖与展开失败帖**点进详情页**拿全文+权威时间。全程页面点击，不做 API 上下文盲拉。

一次翻页同时产出两份产物：
  1. 帖子集 md（`雪球采集-{博主}-{日期}.md`）→ import-post-history.js（图走「图：」行，不进正文）
  2. 对话串 JSON（`雪球对话串-{博主}-{日期}.json`，root.full=原帖全量）→ import-thread.js

与 main.py user（crawler API 上下文翻页，页面不动）的分工：**重采/需肉眼可见操作走本脚本**；
main.py user 保留给日常增量兜底。机制复用 xq_dialog_collect.py 的 JS 常量与弹窗流程。
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
import time

SKILL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SKILL)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ego_browser import BridgeError, EgoBridge  # noqa: E402
import config as xqcfg  # noqa: E402
from feed import clean_quote, derive_time, first_sentence, tidy_article  # noqa: E402
from xq_dialog_collect import (  # noqa: E402
    JS_EXPAND, JS_MARK, JS_MODAL_CLOSE, JS_MODAL_OPEN_Q, JS_MODAL_STABLE,
    JS_NEXT_PAGE, JS_NODES, JS_OPEN_DLG, JS_ROOT_ANCHOR, JS_ROOT_FULL, JS_TOPS, PUA,
)

ANCHOR_MS = int(time.time() * 1000)
DWELL_MS = int(os.environ.get("XUEQIU_ITEM_DWELL_MS", "700"))

# 主页时间线单帖抽取：正文/图/时间/形态信号/引用卡（图滤表情头像、去尺寸档，同 dialog JS_IMG 口径）
JS_POST_META = r"""
(arg) => {
  const junk = u => !u || /emoji|face_regular|badge|medal|identity_icon|xavatar|\/community\/|_logo|icon_|commentlist_tag|_tag-|sprite/i.test(u);
  const norm = u => (u || '').replace(/!\d*x*\d*\.jpg$|!custom\.jpg$|!800\.jpg$/, '');
  const strip = s => (s || '').replace(/[\uE000-\uF8FF]/g, '');
  const t = document.querySelector('[data-zc-dlg]');
  if (!t) return null;
  const main = t.querySelector('.timeline__item__main') || t;
  const content = main.querySelector('.timeline__item__content');
  const scope = content || main;
  const titleEl = t.querySelector('.timeline__item__title');
  const inCard = new Set([...t.querySelectorAll('blockquote a, [class*=forward] a')]);
  const own = [...main.querySelectorAll('a[href]')].find(a =>
    new RegExp('^/' + arg.uid + '/\\d+$').test(a.getAttribute('href') || '') && !inCard.has(a));
  const dlg = main.querySelector('a.dialogue__btn');
  const expA = [...main.querySelectorAll('a')].find(x =>
    (x.className || '').toString().includes('timeline__expand__control') && x.offsetWidth > 0);
  const paraOf = (() => { const blocks = Array.from(scope.children); return n => { let k = 0;
    for (const b of blocks) { if (b === n || b.contains(n)) return k; if ((b.innerText || '').trim()) k++; }
    return -1; }; })();
  const imgs = []; const seen = {};
  for (const nd of scope.querySelectorAll('img, a.co-img-link')) {
    if (nd.closest('blockquote')) continue;
    const raw = nd.tagName === 'IMG' ? nd.src : nd.getAttribute('href');
    if (junk(raw)) continue;
    const u = norm(raw);
    if (!u || !/\.(png|jpe?g|gif|webp|bmp)$/i.test(u) || seen[u]) continue;
    seen[u] = 1;
    imgs.push({ url: u, para: content ? paraOf(nd) : -1 });
  }
  const body = strip(scope.innerText || '').split('\n').map(x => x.trim())
    .filter(l => l && !/^(收起|展开)$/.test(l)).join('\n');
  let quote = null;
  const card = t.querySelector('blockquote');
  if (card) {
    const qa = [...card.querySelectorAll('a[href]')].find(x => /\/\d+\/\d+/.test(x.getAttribute('href') || ''));
    const qtitle = card.querySelector('[class*=title]');
    const qlines = strip(card.innerText || '').split('\n').map(x => x.trim()).filter(Boolean);
    let qauthor = '';
    if (qlines.length && /^@/.test(qlines[0])) { qauthor = qlines[0].replace(/[：:]\s*$/, ''); qlines.splice(0, 1); }
    const mi = qlines.findIndex(l => /·\s*(转发|讨论|赞)/.test(l) && /\d/.test(l));
    let qmeta = '';
    if (mi >= 0) { qmeta = (qlines[mi].split('·')[0] || '').trim(); qlines.splice(mi, 1); }
    quote = {
      url: qa ? 'https://xueqiu.com' + qa.getAttribute('href').split('#')[0] : null,
      author: qauthor, meta: qmeta,
      title: qtitle ? qtitle.textContent.replace(/[\uE000-\uF8FF]/g, '').trim().slice(0, 120) : '',
      lead: qlines.filter(l => !/^(收起|展开)$/.test(l) && l !== (qtitle ? qtitle.textContent.trim() : '\u0000')).join('\n'),
      isColumn: !!qtitle,
    };
  }
  return { url: own ? 'https://xueqiu.com' + own.getAttribute('href') : null,
           timeLabel: own ? strip(own.textContent).replace(/\s+/g, ' ').trim() : '',
           text: body, imgs, isCol: !!titleEl,
           colTitle: titleEl ? titleEl.textContent.replace(/[\uE000-\uF8FF]/g, '').trim().slice(0, 120) : '',
           hasDlg: !!(dlg && dlg.offsetWidth), expandPresent: !!expA, quote };
}
"""


def parse_abs_time(label):
    """绝对写法直解（derive_time 不管 'YYYY-MM-DD HH:MM'，且 MM-DD 段会误吞年份前缀）。"""
    t = PUA.sub('', (label or '')).replace('修改于', '').replace('发布于', '').strip()
    now = dt.datetime.now()
    if '刚刚' in t:
        return now, False
    m = re.search(r'(\d+)\s*天前', t)
    if m:
        return now - dt.timedelta(days=int(m.group(1))), False
    m = re.match(r'^(\d{4})-(\d{1,2})-(\d{1,2})\s+(\d{1,2}):(\d{2})', t)
    if m:
        try:
            return dt.datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                               int(m.group(4)), int(m.group(5))), '修改于' in (label or '')
        except ValueError:
            return None, False
    m = re.match(r'^(\d{1,2})-(\d{1,2})\s+(\d{1,2}):(\d{2})', t)
    if m:
        try:
            d0 = dt.datetime(dt.date.today().year, int(m.group(1)), int(m.group(2)),
                             int(m.group(3)), int(m.group(4)))
            if d0 > dt.datetime.now():
                d0 = d0.replace(year=d0.year - 1)
            return d0, False
        except ValueError:
            return None, False
    return derive_time(t, ANCHOR_MS)


class ProfileCollector:
    def __init__(self, uid, blogger):
        self.uid = str(uid)
        self.blogger = blogger or self.uid
        self.bridge = EgoBridge()
        self.done = set()      # 已采帖 url（翻页异常重扫时跳过）
        self.posts = []        # 每帖 dict（md 导出源）
        self.threads = []      # 对话串（与 xq_dialog_collect 同构：trigger/root/nodes）
        self.origs_seen = 0
        self.pages_done = 0
        self.degraded = False
        self.stop_hit = ''

    # ── 基础 ──────────────────────────────────────────────────────────────
    def start(self):
        self.bridge.start()
        self._goto_profile()

    def stop(self):
        try:
            self.bridge.stop()
        except Exception:
            pass

    def _goto_profile(self):
        page = self.bridge.main_page
        page.goto(f'https://www.xueqiu.com/u/{self.uid}', wait_until='domcontentloaded', timeout=25000)
        page.wait_for_timeout(2500)

    def recover(self, err):
        print(f'  ⚠️ 恢复：{str(err).splitlines()[0][:120]}', flush=True)
        try:
            self.bridge.stop()
        except Exception:
            pass
        self.bridge = EgoBridge()
        self.bridge.start()
        self._goto_profile()

    # ── 对话串（复用 dialog 采集器流程：开弹窗→滚到稳定→收节点→关） ─────────
    def _chain(self, page, meta):
        page.evaluate(JS_OPEN_DLG, None)
        page.wait_for_timeout(1400)
        prev, stable = -1, 0
        while stable < 3:
            n = page.evaluate(JS_MODAL_STABLE, None)
            page.wait_for_timeout(700)
            if n == prev:
                stable += 1
            else:
                stable, prev = 0, n
        nodes = page.evaluate(JS_NODES, None)
        page.evaluate(JS_MODAL_CLOSE, None)
        page.wait_for_timeout(600)
        if page.evaluate(JS_MODAL_OPEN_Q, None):
            page.evaluate(JS_MODAL_CLOSE, None)
            page.wait_for_timeout(500)
        for seq, nd in enumerate(nodes):
            nd['seq'] = seq
            nd['parent_seq'] = seq - 1 if seq else None
        root = meta.get('quote') if meta.get('quote') and meta['quote'].get('url') else None
        return {'trigger': {'url': meta['url'], 'time': meta['timeLabel']},
                'root': ({'url': root['url'], 'author': root['author'], 'meta': root['meta'],
                          'content': root['lead'], 'isColumn': root['isColumn'],
                          'title': root['title']} if root else None),
                'nodes': nodes}

    # ── 主流程：翻页 × 逐条滚动 ───────────────────────────────────────────
    def run(self, pages, stop_before=''):
        self.start()
        page = self.bridge.main_page
        skip = {}
        stop_date = None
        if stop_before:
            try:
                stop_date = dt.date.fromisoformat(stop_before)
            except ValueError:
                pass
        try:
            broken = False
            for pg in range(1, pages + 1):
                page = self.bridge.main_page
                tops = page.evaluate(JS_TOPS, None)
                print(f'[page {pg}] 顶层帖 {tops}', flush=True)
                if tops == 0 and pg > 1:
                    print(f'[page {pg}] 翻页后 0 帖——翻页失效，止损', flush=True)
                    self.degraded = True
                    break
                i = 0
                while i < tops:
                    try:
                        if not page.evaluate(JS_MARK, {'i': i}):
                            break
                        page.wait_for_timeout(DWELL_MS)
                        clicked = page.evaluate(JS_EXPAND, None)
                        if clicked:
                            page.wait_for_timeout(900)
                        meta = page.evaluate(JS_POST_META, {'uid': self.uid})
                        if not meta:
                            i += 1
                            continue
                        tdt, _ed = parse_abs_time(meta['timeLabel'])
                        if stop_date and tdt and tdt.date() < stop_date:
                            self.stop_hit = f'{meta["url"]} {tdt:%Y-%m-%d %H:%M}'
                            print(f'[time] 已滚过窗口起点 {stop_before}（{self.stop_hit}），止损', flush=True)
                            broken = True
                            break
                        url = meta.get('url')
                        if not url or url in self.done:
                            i += 1
                            continue
                        if meta.get('hasDlg'):
                            try:
                                self.threads.append(self._chain(page, meta))
                            except BridgeError as e:
                                print(f'  [dlg] 桥错误：{str(e).splitlines()[0][:90]}', flush=True)
                                self.recover(e)
                                page = self.bridge.main_page
                                tops = page.evaluate(JS_TOPS, None)
                                i = 0
                                continue
                        self.done.add(url)
                        needs_detail = (meta.get('isCol') or (meta.get('expandPresent') and not clicked)
                                        or '展开' in (meta.get('text') or ''))
                        if not meta.get('hasDlg') and not meta.get('isCol'):
                            self.origs_seen += 1
                        self.posts.append({**meta, 'needs_detail': needs_detail})
                        if len(self.posts) % 10 == 0:
                            print(f'  … 已采 {len(self.posts)} 帖（串 {len(self.threads)}）', flush=True)
                        skip.pop(i, None)
                        i += 1
                    except BridgeError as e:
                        msg = str(e)
                        if re.search(r'ReferenceError|TypeError|SyntaxError|is not defined', msg):
                            skip[i] = skip.get(i, 0) + 1
                            print(f'  [{i}] 代码错误（第 {skip[i]} 次）：{msg.splitlines()[0][:90]}', flush=True)
                            if skip[i] >= 2:
                                print(f'  [{i}] 确定性错误，跳过该条目', flush=True)
                                self.degraded = True
                                i += 1
                            continue
                        print(f'  [{i}] 桥错误：{msg.splitlines()[0][:90]}', flush=True)
                        if skip.get(i, 0) >= 2:
                            print(f'  [{i}] 重试超限，跳过该条目', flush=True)
                            self.degraded = True
                            i += 1
                            continue
                        skip[i] = skip.get(i, 0) + 1
                        self.recover(msg)
                        page = self.bridge.main_page
                        tops = page.evaluate(JS_TOPS, None)
                        i = 0
                self.pages_done = pg
                if broken:
                    break
                if pg < pages:
                    if not page.evaluate(JS_NEXT_PAGE, None):
                        print('[next] 无下一页，提前收', flush=True)
                        break
                    page.wait_for_timeout(int(xqcfg.page_delay() * 1000))
        except BridgeError:
            raise
        finally:
            pass  # 桥留给 main() 收尾再关：后置的原帖/详情补全还要用，提前关会白启一轮
        self._detail_fulls()
        self._fix_roots()
        self._thread_full_roots()
        return self

    # ── 后置：详情页补全（专栏帖/展开失败帖；点进详情=用户口径，节流同 config.DETAIL_*） ──
    def _detail_visit(self, url):
        for attempt in (1, 2):
            try:
                page = self.bridge.main_page
                page.goto(url, wait_until='domcontentloaded', timeout=20000)
                page.wait_for_timeout(1800)
                return page.evaluate(JS_ROOT_FULL, None)
            except BridgeError as e:
                print(f'  [detail] 桥错误：{str(e).splitlines()[0][:90]}', flush=True)
                if attempt == 1:
                    self.recover(e)
                else:
                    return None
            except Exception as e2:
                print(f'  [detail] evaluate 失败 {url}: {type(e2).__name__}', flush=True)
                return None
        return None

    def _detail_fulls(self):
        todo = [p for p in self.posts if p.get('needs_detail') and p.get('url')]
        if not todo:
            return
        print(f'[detail] 点进详情页补全 {len(todo)} 帖（专栏/展开失败）', flush=True)
        n_break = 0
        for k, p in enumerate(todo):
            if n_break >= xqcfg.DETAIL_BREAK_N:
                print(f'  [detail] 长歇 {xqcfg.DETAIL_BREAK_S}s', flush=True)
                time.sleep(xqcfg.DETAIL_BREAK_S)
                n_break = 0
            info = self._detail_visit(p['url'])
            time.sleep(xqcfg.page_delay())   # 详情页间隔同样吃 XUEQIU_PAGE_DELAY_RANGE 档
            n_break += 1
            if info and info.get('text'):
                p['full'] = {'text': info['text'], 'time': info['time'],
                             'title': info.get('title') or '', 'imgs': info.get('imgs') or []}
                print(f"  [detail] {p['url'][-12:]} {len(info['text'])} 字 · 图{len(p['full']['imgs'])}", flush=True)
            else:
                p['detail_failed'] = True
                print(f"  [detail] 未取到全文 {p['url']}", flush=True)
                self.degraded = True

    def _fix_roots(self):
        need = [t for t in self.threads if not (t.get('root') or {}).get('url')]
        if not need:
            return
        print(f'[root] 补锚定 {len(need)} 条（回复帖永久页取上下文锚点）', flush=True)
        for t in need:
            try:
                page = self.bridge.main_page
                page.goto(t['trigger']['url'], wait_until='domcontentloaded', timeout=20000)
                page.wait_for_timeout(1600)
                info = page.evaluate(JS_ROOT_ANCHOR, None)
                if info and info.get('rootUrl'):
                    t['root'] = {'url': info['rootUrl']}
                else:
                    print(f"[root] 无锚点 {t['trigger']['url']}", flush=True)
                time.sleep(1.2 + (xqcfg.page_delay() - 1.2) * 0.3)
            except BridgeError as e:
                print(f'[root] 桥错误: {e}', flush=True)
                self.recover(e)

    def _thread_full_roots(self):
        need, seen = [], set()
        for t in self.threads:
            r = (t.get('root') or {})
            u = r.get('url')
            if u and not r.get('full') and u not in seen:
                seen.add(u)
                need.append(t)
        if not need:
            return
        print(f'[full-root] 原帖全量抓取 {len(need)} 条（去重后）', flush=True)
        for t in need:
            ru = t['root']['url']
            info = self._detail_visit(ru)
            time.sleep(xqcfg.page_delay())
            if info and info.get('text'):
                full = {'text': info['text'], 'time': info['time'], 'form': info['form'],
                        'title': info.get('title') or '', 'imgs': info.get('imgs') or []}
                for t2 in self.threads:
                    if (t2.get('root') or {}).get('url') == ru:
                        t2['root']['full'] = full
                print(f"  [full] {ru[-12:]} {len(info['text'])} 字 · 图{len(full['imgs'])}", flush=True)
            else:
                print(f'  [full] 无正文容器 {ru}', flush=True)
                self.degraded = True

    # ── 产物 ──────────────────────────────────────────────────────────────
    def summary(self):
        return {'uid': self.uid, 'blogger': self.blogger,
                'collected_at': dt.datetime.now().isoformat(timespec='seconds'),
                'pages': self.pages_done, 'posts': len(self.posts),
                'replies': len(self.threads), 'origs_seen': self.origs_seen,
                'threads': self.threads, 'stopBefore': self.stop_hit or None,
                'degraded': self.degraded}


def md_body(p):
    """单帖 md 块（feed 同口径）：正文 + 回复内容块 + 发布行 + 图行。"""
    full = p.get('full')
    if p.get('isCol'):
        body = tidy_article(full['text']) if full else tidy_article(p.get('text') or '')
    else:
        body = tidy_article(full['text'] if full else (p.get('text') or ''))
    complete, reason = '全文', ''
    if p.get('isCol') and not full:
        complete, reason = '摘要', '专栏未取到全文'
    elif p.get('detail_failed') or p.get('needs_detail') and not full:
        complete, reason = '摘要', '展开失败未补全'
    q = p.get('quote')
    if q and q.get('url') and not body.startswith('回复@') and '> 回复内容·' not in body:
        qauthor = q.get('author') or '被引作者'
        qdt, _ = parse_abs_time(q.get('meta') or '')
        meta = qdt.strftime('%Y-%m-%d %H:%M') if qdt else (q.get('meta') or '').strip()
        if q.get('isColumn') and q.get('title'):
            head = f"> 回复内容·专栏：{qauthor}《{q['title']}》"
            if meta:
                head += f"（{meta}）"
            block = [head]
            lead = re.sub(r'\s*\n\s*', '', clean_quote(q.get('lead') or '')).strip()
            lead = re.sub(r'\s*\n\s*', '', lead.replace(q['title'], '', 1)).strip()
            if lead:
                block.append(f'> 文章导语（截断）：{lead}')
            block.append(f"> 被引原文：{q['url']}")
        else:
            qtext = re.sub(r'\s*\n\s*', '', clean_quote(q.get('lead') or '')).strip()
            head = f'> 回复内容·原帖：{qauthor}：{qtext}' if qtext else f'> 回复内容·原帖：{qauthor}'
            block = [head, f"> 被引原文：{q['url']}"]
        body = (body + '\n\n' + '\n'.join(block)).strip()
    if not body.strip():
        complete, reason = '摘要', '纯图片帖（无文本正文）'
    tm, tmark, edited = '', '（流内推算）', False
    if full and full.get('time'):
        m = re.search(r'(\d{4})-(\d{2})-(\d{2})[ T](\d{2}:\d{2})', full['time'])
        if m:
            tm = f'{m.group(1)}年{m.group(2)}月{m.group(3)}日 {m.group(4)}'
            tmark = ''
    if not tm:
        tdt, edited = parse_abs_time(p.get('timeLabel') or '')
        if tdt:
            tm = f'{tdt.year}年{tdt.month:02d}月{tdt.day:02d}日 {tdt.strftime("%H:%M")}'
    if not tm:
        complete, reason = '摘要', '时间不可解析'
        tm = '1970年01月01日 00:00'
    if p.get('isCol') and body:
        title = p.get('colTitle') or (full or {}).get('title') or first_sentence(body)
    else:
        title = first_sentence(body)
    is_reply = bool(p.get('hasDlg')) or ('回复@' in body or '//@' in body) or bool(q and q.get('url'))
    form = '专栏' if p.get('isCol') else ('回复' if is_reply else ('短文' if len(body) < 200 else '长文'))
    pub = (f"> 发布：{tm}{tmark}{'（修改于）' if edited else ''} | 形态：{form} | 作者：{p['blogger']}"
           f" | {complete}{' | ' + reason if reason else ''}"
           f" | [原文]({p['url']})")
    lines = [f"## {p['idx']}. {title}", '', body, '', pub]
    imgs = p.get('imgs') or []
    if imgs:
        lines.append('> 图：' + ' | '.join(f"{im['url']} @p{im.get('para', -1)}" for im in imgs))
    return lines, complete


def emit_md(c, outdir, outfile=''):
    now = dt.datetime.now()
    rows = []
    n_full = 0
    for k, p in enumerate(c.posts, 1):
        p = dict(p)
        p['idx'] = k
        p['blogger'] = c.blogger
        lines, complete = md_body(p)
        if complete == '全文':
            n_full += 1
        rows.append('\n'.join(lines))
    status = '待提炼' if n_full == len(rows) else '待提炼-含摘要'
    head = [
        '---',
        f'title: "雪球帖子采集：{c.blogger} {now.year}年{now.month}月{now.day}日"',
        f'source: "https://xueqiu.com/u/{c.uid}"',
        f'author: "{c.blogger}"',
        f'date: "{now.year}年{now.month}月{now.day}日"',
        f'recorded: "{now.year}年{now.month}月{now.day}日"',
        'type: "帖子集"',
        f'status: "{status}"',
        'tags: []',
        '---',
        '',
    ]
    body = []
    for r in rows:
        body += [r, '', '---', '']
    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, outfile or f'雪球采集-{c.blogger}-{now:%Y年%m月%d日}.md')
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(head + body))
    return path, len(rows), n_full


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('uid', help='博主 uid')
    ap.add_argument('--blogger', default='', help='博主昵称（md 归属/文件名用）')
    ap.add_argument('--pages', type=int, default=40)
    ap.add_argument('--stop-before', default='', help='YYYY-MM-DD：滚到该日期之前的帖即停')
    ap.add_argument('--out', default='', help='两份产物的输出目录')
    a = ap.parse_args()
    outdir = a.out or os.path.expanduser('~/.cache/xueqiu-spyder/recrawl')
    c = ProfileCollector(a.uid, a.blogger)
    try:
        c.run(a.pages, stop_before=a.stop_before)
    finally:
        c.stop()
    s = c.summary()
    md_path, n_posts, n_full = emit_md(c, outdir)
    js_path = os.path.join(outdir, f'雪球对话串-{c.blogger or a.uid}-{dt.date.today():%Y%m%d}.json')
    with open(js_path, 'w', encoding='utf-8') as f:
        json.dump(s, f, ensure_ascii=False, indent=1)
    tail = ' [DEGRADED]' if s['degraded'] else ''
    print(f'OUT-MD {md_path}\nOUT-JSON {js_path}\n'
          f'posts={n_posts}(全文 {n_full}/摘要 {n_posts - n_full}) threads={len(s["threads"])} '
          f'pages={s["pages"]} stop={s["stopBefore"]}{tail}', flush=True)
    if s['degraded']:
        sys.exit(3)


if __name__ == '__main__':
    main()
