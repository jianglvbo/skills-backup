#!/usr/bin/env python3
"""雪球对话串采集器（用户定稿流程，细则见 references/dialog-flow.md）

用法:
    python3 scripts/xq_dialog_collect.py <xq_uid> [--pages 3] [--blogger 昵称] [--out FILE]

输出: 结构化 JSON（threads: trigger/root/nodes），供落库脚本消费。
口径（2026-09-30 用户定稿）:
  - 只处理回复帖（dialogue__btn 可见）；原发帖不采（正文由 feed 覆盖，防重复）
  - 链节点按 DOM 字段采（.extend_comment_info=达人赞；acts 行讨论/赞分开），不拆拍平文本
  - 节点带 comment_id(data-id)+author_uid → 构造永久链接；追踪博主正文不落此处
  - 被讨论帖：只留 url+正文快照（落库侧按博主是否追踪决定存快照还是指回）
韧性: 页签丢失（page label not found）→ new_page/重启桥恢复当前页，按 trigger url 去重已采项。
"""
import argparse
import datetime as dt
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ego_browser import BridgeError, EgoBridge  # noqa: E402

PUA = re.compile(r'[\uE000-\uF8FF]')
# 尾部 UI 伪影（2026-10-02 存量实修复 250 处后的防线）：评论图片展开钮「查看图片」、
# 对话链锚点「查看对话」随 innerText 黏在文本尾部，正则只剥尾部防误伤正文
_TAIL_ARTIFACT = re.compile(r'(?:\s*(?:查看图片|查看对话|查看原图))+\s*$')
CLEAN = lambda s: _TAIL_ARTIFACT.sub('', PUA.sub('', (s or ''))).strip()

# ── 页内脚本（全部走 evaluate；点击=el.click() 走 JS handler，符合「点击不裸调」口径）──

JS_TOPS = """() => {
  const tops = [...document.querySelectorAll('[class*="timeline__item"]')]
    .filter(el => !el.parentElement.closest('[class*="timeline__item"]'))
    .filter(t => !/置顶/.test((t.querySelector('.timeline__item__tag, [class*=tag]')?.textContent || '')));
  return tops.length;
}"""

JS_MARK = """(arg) => {
  const tops = [...document.querySelectorAll('[class*="timeline__item"]')]
    .filter(el => !el.parentElement.closest('[class*="timeline__item"]'))
    .filter(t => !/置顶/.test((t.querySelector('.timeline__item__tag, [class*=tag]')?.textContent || '')));
  const t = tops[arg.i];
  if (!t) return null;
  document.querySelectorAll('[data-zc-dlg]').forEach(el => el.removeAttribute('data-zc-dlg'));
  t.setAttribute('data-zc-dlg', '1');
  t.scrollIntoView({ block: 'center', behavior: 'instant' });
  return true;
}"""

JS_OWN_URL = """(arg) => {
  const t = document.querySelector('[data-zc-dlg]');
  if (!t) return null;
  const main = t.querySelector('.timeline__item__main') || t;
  const inCard = new Set([...t.querySelectorAll('blockquote a, [class*=forward] a')]);
  const own = [...main.querySelectorAll('a[href]')].find(a =>
    new RegExp('^/' + arg.uid + '/\\\\d+$').test(a.getAttribute('href') || '') && !inCard.has(a));
  return own ? 'https://xueqiu.com' + own.getAttribute('href') : null;
}"""

JS_EXPAND = """() => {
  const t = document.querySelector('[data-zc-dlg]');
  if (!t) return false;
  const card = t.querySelector('blockquote');
  const a = [...t.querySelectorAll('a')].find(x =>
    (x.className || '').toString().includes('timeline__expand__control')
    && x.offsetWidth > 0 && (card ? !card.contains(x) : true));
  if (!a) return false;
  a.click();
  return true;
}"""

# 配图提取（2026-10-01）：对话串全程 innerText，链节点与触发帖的图此前一律丢。
# 图在 DOM 里有两种载体——已渲染的 <img src>（带 !800.jpg/!custom.jpg 尺寸档）与被折叠成
# <a class="co-img-link" href="…jpg">查看图片</a>；读属性即可，**不需要点开**。
# 用 raw string 写：JS 正则里的 \d \/ 直接就是字面量，不必再双层转义。
JS_IMG = r"""
  const __junk = u => !u || /emoji|face_regular|badge|medal|identity_icon|xavatar|\/community\/|_logo|icon_|commentlist_tag|_tag-|sprite/i.test(u);
  const __norm = u => (u || '').replace(/!\d*x*\d*\.jpg$|!custom\.jpg$|!800\.jpg$/, '');
  const __imgs = (root, scope) => {
    const out = [], seen = {};
    if (!root) return out;
    const blocks = scope ? Array.from(scope.children) : [];
    const paraOf = n => {
      let k = 0;
      for (const b of blocks) {
        if (b === n || b.contains(n)) return k;
        if ((b.innerText || '').trim()) k++;
      }
      return -1;
    };
    for (const nd of root.querySelectorAll('img, a.co-img-link, a[href]')) {
      if (nd.closest('blockquote')) continue;          // 引用卡的图归 root，不在节点里重复计
      const raw = nd.tagName === 'IMG' ? nd.src : nd.getAttribute('href');
      if (__junk(raw)) continue;
      const u = __norm(raw);
      if (!u || !/\.(png|jpe?g|gif|webp|bmp)$/i.test(u) || seen[u]) continue;
      seen[u] = 1;
      out.push({url: u, para: scope ? paraOf(nd) : -1});
    }
    return out;
  };
"""

JS_ITEM_META = "(arg) => {" + JS_IMG + """
  const t = document.querySelector('[data-zc-dlg]');
  if (!t) return null;
  const main = t.querySelector('.timeline__item__main') || t;
  const dlg = main.querySelector('a.dialogue__btn');
  const card = t.querySelector('blockquote');
  let root = null;
  if (card) {
    const a = [...card.querySelectorAll('a[href]')].find(x => /\\/\\d+\\/\\d+/.test(x.getAttribute('href') || ''));
    const lines = (card.innerText || '').split('\\n').map(x => x.replace(/[\\uE000-\\uF8FF]/g, '').trim()).filter(Boolean);
    let author = '', meta = '';
    if (lines.length && /^@/.test(lines[0])) author = lines[0].replace(/[：:]\\s*$/, '');
    const mi = lines.findIndex(l => /·\\s*(转发|讨论|赞)/.test(l) && /\\d/.test(l));
    if (mi >= 0) { meta = (lines[mi].split('·')[0] || '').trim(); lines.splice(mi, 1); }   // 只留发帖时间，计数不采（2026-10-04 用户定稿）
    const body = lines.filter(l => !/^(收起|展开)/.test(l)).join('\\n');
    root = { url: a ? 'https://xueqiu.com' + a.getAttribute('href').split('#')[0] : null,
             author, meta, content: body,
             imgs: __imgs(card, null) };
  }
  const timeEl = [...main.querySelectorAll('a[href]')].find(a2 =>
    new RegExp('^/' + arg.uid + '/\\\\d+$').test(a2.getAttribute('href') || ''));
  return { hasDlg: !!(dlg && dlg.offsetWidth),
           url: timeEl ? 'https://xueqiu.com' + timeEl.getAttribute('href') : null,
           time: timeEl ? timeEl.textContent.replace(/\\s+/g, ' ').trim() : '',
           imgs: __imgs(main, main.querySelector('.timeline__item__content') || main),
           root };
}"""

JS_ROOT_ANCHOR = """() => {
  const a = document.querySelector('a.fake-anchor, a.replay-count');
  const anchor = a ? (a.getAttribute('href') || '') : '';
  const m = anchor.match(/^\\/(\\d+)\\/(\\d+)/);
  const art = document.querySelector('.article__bd');
  let author = '';
  const who = document.querySelector('.article__bd__user, .name, .user-name');
  if (who) author = who.textContent.trim().slice(0, 40);
  const metaEl = [...document.querySelectorAll('.article__bd + div, .detail__meta, .time')].map(e => e.textContent).join(' ');
  const meta = (metaEl.match(/\\d{2,4}-\\d{2}-\\d{2}[^·]*·[^·]*讨论[^\\n]*/) || [''])[0].trim();
  return { rootUrl: m ? ('https://xueqiu.com/' + m[1] + '/' + m[2]) : null,
           author, meta,
           content: art ? art.innerText.slice(0, 600) : '' };
}"""

# 原帖页全量抓取（2026-10-03 卡片重构，用户拍板「原帖无论谁发都采」）：
#   goto root_url 后取 .article__bd 完整正文（保留段落，不截断）+ 时间·形态 + 图片。
#   与 root_content 脏写事故的区别：本脚本 goto 的是**原帖页**（上一次错在回复帖永久页
#   把回复正文当原帖抓——语义已由 goto 目标保证）。
JS_ROOT_FULL = "() => {" + JS_IMG + r"""
  const art = document.querySelector('.article__bd');
  if (!art) return null;
  const who = document.querySelector('.article__bd__user, .name, .user-name');
  const author = who ? who.textContent.trim().slice(0, 40) : '';
  const tEl = document.querySelector('.article__author time, .article__author a.edit-time');
  const time = tEl ? ((tEl.getAttribute('datetime') || tEl.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 40)) : '';
  const titleEl = document.querySelector('.article__bd__title, h1.title');
  const title = titleEl ? titleEl.textContent.trim().slice(0, 120) : '';
  return { author, time, title, form: title ? '专栏' : '短文',
           text: art.innerText.replace(/[\uE000-\uF8FF]/g, '').trim(),
           imgs: __imgs(art, art) };
}"""

JS_OPEN_DLG = """() => {
  const t = document.querySelector('[data-zc-dlg]');
  const b = t && t.querySelector('a.dialogue__btn');
  if (!b || !b.offsetWidth) return false;
  b.click();
  return true;
}"""

JS_MODAL_STABLE = """() => {
  const m = document.querySelector('.modal.modal__comment');
  if (!m) return 0;
  const sc = [...m.querySelectorAll('*')].find(e => e.scrollHeight > e.clientHeight + 30);
  if (sc) sc.scrollTop = sc.scrollHeight;
  return m.querySelectorAll('.comment__item').length;
}"""

JS_MODAL_OPEN_Q = "() => !!document.querySelector('.modal.modal__comment')"

JS_MODAL_CLOSE = """() => {
  const m = document.querySelector('.modal.modal__comment');
  if (!m) return true;
  const x = m.querySelector('.modal__hd [class*=close]') || m.querySelector('a[class*=close]') || m.querySelector('[class*=modal__close]');
  if (x) { x.click(); return true; }
  return false;
}"""

JS_NEXT_PAGE = """() => {
  const n = document.querySelector('a.pagination__next');
  if (!n) return false;
  n.scrollIntoView({ block: 'center', behavior: 'instant' });
  n.click();
  return true;
}"""

# 关注列表枚举（2026-10-03 关注提炼，用户指定入口=雪球关注列表）：
#   首页左栏「关注 N」→ /center/#/friends；列表项=.profiles__user（a.avatar[href=/uid]）
JS_FOLLOW_LIST = """() => {
  const out = [];
  for (const c of document.querySelectorAll('.profiles__user')) {
    const a = c.querySelector('a.avatar');
    const href = a ? (a.getAttribute('href') || '') : '';
    const m = href.match(/^\\/(\\d+)$/);
    if (!m) continue;
    const lines = (c.innerText || '').split('\\n').map(s => s.trim()).filter(Boolean);
    out.push({ uid: m[1], name: (lines[0] || '').slice(0, 30) });
  }
  return out;
}"""

JS_NODES = "() => {" + JS_IMG + """
  const out = [];
  for (const it of document.querySelectorAll('.modal.modal__comment .comment__item')) {
    const hd = it.querySelector('.comment__item__main__hd');
    const nameEl = hd && hd.querySelector('a.user-name');
    const name = nameEl ? nameEl.textContent.replace(/[\\uE000-\\uF8FF]/g, '').trim() : '';
    const href = nameEl ? (nameEl.getAttribute('href') || '') : '';
    const uid = /^\\/(\\d+)$/.test(href) ? href.slice(1) : null;
    const hdTxt = (hd ? hd.innerText : '').replace(/[\\uE000-\\uF8FF]/g, ' ').replace(/\\s+/g, ' ').trim();
    let badge = hdTxt.replace(name, '').trim();
    badge = badge.replace(/\\d{4}-\\d{2}-\\d{2}.*$/, '').replace(/\\d{2}-\\d{2}.*$/, '')
                 .replace(/(修改于|昨天|今天).*$/, '').trim();
    const time = hd ? (hd.querySelector('.time')?.textContent.replace(/\\s+/g, ' ').trim() || '') : '';
    const main = it.querySelector('.comment__item__main');
    let body = '';
    if (main) {
      const clone = main.cloneNode(true);
      clone.querySelectorAll('.comment__item__main__hd, .comment__item__ft, blockquote, .comment__item__tags')
        .forEach(e => e.remove());
      body = (clone.innerText || '').replace(/[\\uE000-\\uF8FF]/g, '').trim();
    }
    const darenEl = it.querySelector('.extend_comment_info');
    /* 讨论/赞数字不采（用户 09-30 定稿）：只保留「N位达人赞过/作者赞过」标识 */
    out.push({ name, author_uid: uid, badge: badge || null, time, text: body,
               daren: darenEl ? darenEl.textContent.replace(/[\\uE000-\\uF8FF]/g, '').trim() : null,
               imgs: __imgs(main, main),
               comment_id: it.getAttribute('data-id') || null });
  }
  return out;
}"""


def num(v):
    if v is None:
        return None
    s = str(v)
    try:
        return int(float(s.replace('万', '')) * (10000 if '万' in s else 1))
    except ValueError:
        return None


class Collector:
    def __init__(self, uid, blogger):
        self.uid = uid
        self.blogger = blogger
        self.bridge = EgoBridge()
        self.done = set()          # 已采 trigger url（恢复重扫时跳过）
        self.out = {'uid': uid, 'blogger': blogger,
                    'collected_at': dt.datetime.now().isoformat(timespec='seconds'),
                    'pages': 0, 'replies': 0, 'origs_seen': 0, 'threads': []}

    def start(self):
        self.bridge.start()
        self._goto_profile()

    def stop(self):
        self.bridge.stop()

    def _goto_profile(self):
        page = self.bridge.main_page
        page.goto(f'https://www.xueqiu.com/u/{self.uid}')
        page.wait_for_timeout(2200)
        return page

    @staticmethod
    def _older_than(time_raw, stop_before):
        """time_raw 如 '09-25 13:05· 来自Android' / '2025-05-02 …' / '昨天 …' → True=早于 stop_before"""
        t = PUA.sub('', (time_raw or '')).strip()
        base = dt.date.today()
        m = re.match(r'^(\d{4})-(\d{1,2})-(\d{1,2})', t)
        if m:
            d = dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        else:
            m = re.match(r'^(\d{1,2})-(\d{1,2})', t)
            if m:
                d = dt.date(base.year, int(m.group(1)), int(m.group(2)))
            else:
                return False   # 今天/昨天/相对时间：仍在窗口内
        try:
            stop = dt.date.fromisoformat(stop_before)
        except ValueError:
            return False
        return d < stop

    def recover(self, err):
        """页签丢失/桥故障恢复：先试 new_page，不行重启桥（换空间）"""
        print(f'  ⚠️ 恢复：{err}', flush=True)
        try:
            self.bridge.stop()
        except Exception:
            pass
        self.bridge = EgoBridge()
        self.bridge.start()
        self._goto_profile()

    def fix_roots(self):
        """root 锚定补齐（2026-10-02 串条聚合挂根帖卡的前置）：被回复对象是评论时，
        时间线引用卡里没有 status 链接 → root 空；按 dialog-flow 定稿打开回复帖
        永久页取上下文锚点（a.fake-anchor / a.replay-count → 原帖 status id）。"""
        need = [t for t in self.out['threads'] if not (t.get('root') or {}).get('url')]
        if not need:
            return
        print(f'[root] 补锚定 {len(need)} 条', flush=True)
        page = self.bridge.main_page
        for t in need:
            try:
                page.goto(t['trigger']['url'], wait_until='domcontentloaded', timeout=20000)
                page.wait_for_timeout(1600)
                info = page.evaluate(JS_ROOT_ANCHOR, None)
                # ⚠ 只锚 URL 不抓正文：回复帖永久页的 .article__bd 是回复帖正文（文不对题），
                # 2026-10-02 实测写过脏 root_content；原帖正文快照需在 root_url 页面抓，另做
                if info and info.get('rootUrl'):
                    t['root'] = {'url': info['rootUrl']}
                else:
                    print(f"[root] 无锚点 {t['trigger']['url']}", flush=True)
            except BridgeError as e:
                print(f'[root] 桥错误: {e}', flush=True)
                self.recover(f'{e}')

    def collect_full_roots(self):
        """原帖全量抓取（2026-10-03 卡片重构）：对每条有 root.url 的串 goto 原帖页，
        抓完整正文（保留段落）+时间·形态+图片 → root.full；落库侧写 post_history
        （post_kind_code='origin'，无论原帖作者是不是追踪博主）并回填 root_ph_id。
        ⚠ 与 fix_roots 的「只锚 URL」不矛盾：那里在**回复帖页**锚定，这里在**原帖页**抓正文。"""
        need = []
        seen = set()
        for t in self.out['threads']:
            r = (t.get('root') or {})
            u = r.get('url')
            if u and not r.get('full') and u not in seen:
                seen.add(u)
                need.append(t)
        if not need:
            return
        print(f'[full-root] 原帖全量抓取 {len(need)} 条（去重后）', flush=True)
        page = self.bridge.main_page
        for t in need:
            ru = t['root']['url']
            info = None
            for attempt in (1, 2):   # 桥断→恢复→重试一次
                try:
                    page = self.bridge.main_page
                    page.goto(ru, wait_until='domcontentloaded', timeout=20000)
                    page.wait_for_timeout(1800)
                    info = page.evaluate(JS_ROOT_FULL, None)
                    break
                except BridgeError as e:
                    print(f'[full-root] 桥错误: {e}', flush=True)
                    if attempt == 1:
                        self.recover(f'{e}')
                    else:
                        break
                except Exception as e2:
                    print(f'  [full] evaluate 失败 {ru}: {type(e2).__name__} {str(e2).splitlines()[0][:120]}', flush=True)
                    break
            if info and info.get('text'):
                full = {'text': info['text'], 'time': info['time'],
                        'form': info['form'], 'title': info.get('title') or '',
                        'imgs': info.get('imgs') or []}
                # 同根兄弟串共享 full（seen 去重只抓一次，落库按 url_hash 幂等同源）
                for t2 in self.out['threads']:
                    if (t2.get('root') or {}).get('url') == ru:
                        t2['root']['full'] = full
                print(f"  [full] {ru[-12:]} {len(info['text'])} 字 · 图{len(full['imgs'])} · time={full['time'][:16]}", flush=True)
            elif info is not None:
                print(f'  [full] 无正文容器 {ru}', flush=True)

    def extract_thread(self, page, i):
        if not page.evaluate(JS_MARK, {'i': i}):
            return None
        page.evaluate(JS_EXPAND, None)
        page.wait_for_timeout(900)
        meta = page.evaluate(JS_ITEM_META, {'uid': self.uid})
        return self._extract_chain(page, meta)

    def _extract_chain(self, page, meta):
        if not meta:
            return None
        if not meta['hasDlg']:
            self.out['origs_seen'] += 1
            return {'orig': meta.get('url')}
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
        closed = page.evaluate(JS_MODAL_CLOSE, None)
        page.wait_for_timeout(600)
        if page.evaluate(JS_MODAL_OPEN_Q, None):
            page.evaluate(JS_MODAL_CLOSE, None)
            page.wait_for_timeout(500)
        for seq, nd in enumerate(nodes):
            nd['seq'] = seq
            nd['parent_seq'] = seq - 1 if seq else None
            # comment_id≠status id（拼接 URL 实测 404），不拼 permalink；comment_id 留作稳定键
        return {'trigger': {'url': meta['url'], 'time': meta['time']},
                'root': meta['root'], 'nodes': nodes, '_modal_close': closed}

    def run(self, pages, stop_before=''):
        self.start()
        skip = {}          # idx -> 已重试次数（确定性错误不重试）
        degraded = False   # 有条目因反复失败被跳过 → 退出码 3
        try:
            for pg in range(1, pages + 1):
                page = self.bridge.main_page
                tops = page.evaluate(JS_TOPS, None)
                print(f'[page {pg}] 顶层帖 {tops}', flush=True)
                if tops == 0 and pg > 1:
                    print(f'[page {pg}] 翻页后 0 帖——翻页失效，止损退出', flush=True)
                    degraded = True
                    break
                i = 0
                while i < tops:
                    try:
                        if not page.evaluate(JS_MARK, {'i': i}):
                            break
                        quick = page.evaluate(JS_OWN_URL, {'uid': self.uid})
                        # 时间感知止损：时间线自上而下由新到旧，本帖已早于目标日期 → 后面只会更旧
                        if stop_before and quick:
                            if self._older_than(quick, stop_before):
                                print(f'[time] {quick} 早于 {stop_before}，时间线已滚过目标窗口，止损', flush=True)
                                self.out['pages'] = pg
                                return degraded
                        if quick and quick in self.done:
                            i += 1
                            continue
                        rec = self.extract_thread(page, i)
                        if rec is None:
                            i += 1
                            continue
                        if 'orig' in rec:
                            self.out['origs_seen'] += 1
                            i += 1
                            continue
                        turl = rec['trigger']['url']
                        if turl and turl not in self.done:
                            rec.pop('_modal_close', None)
                            self.out['threads'].append(rec)
                            self.out['replies'] += 1
                            self.done.add(turl)
                            print(f'  [{i}] 串 {len(rec["nodes"])} 节点 · {turl}', flush=True)
                        skip.pop(i, None)
                        i += 1
                    except BridgeError as e:
                        msg = str(e)
                        # 坑2：确定性代码错误（ReferenceError/TypeError/SyntaxError）= 采集器自身
                        # JS bug，重启桥/恢复都无效——重试上限 2 次后跳过该条目，不烧桥
                        if re.search(r'ReferenceError|TypeError|SyntaxError|is not defined', msg):
                            skip[i] = skip.get(i, 0) + 1
                            print(f'  [{i}] 代码错误（第 {skip[i]} 次）：{msg.splitlines()[0][:90]}', flush=True)
                            if skip[i] >= 2:
                                print(f'  [{i}] 确定性错误，跳过该条目继续', flush=True)
                                degraded = True
                                i += 1
                            continue
                        print(f'  [{i}] 桥错误：{msg.splitlines()[0][:90]}', flush=True)
                        if skip.get(i, 0) >= 2:
                            print(f'  [{i}] 重试超限，跳过该条目继续', flush=True)
                            degraded = True
                            i += 1
                            continue
                        skip[i] = skip.get(i, 0) + 1
                        self.recover(msg)
                        page = self.bridge.main_page
                        tops = page.evaluate(JS_TOPS, None)
                        i = 0          # 本页重扫，done 集合跳过已采
                out_pages = pg
                if pg < pages:
                    if not page.evaluate(JS_NEXT_PAGE, None):
                        print('[next] 无下一页，提前收', flush=True)
                        break
                    page.wait_for_timeout(2400)
            self.out['pages'] = pages
            self.out['degraded'] = degraded
            self.out['stopBefore'] = stop_before
        finally:
            self.stop()
        return degraded


# 关注采集（2026-10-03 关注提炼，用户指定入口=雪球关注列表逐博主）：
#   枚举 /center/#/friends → 每位博主跑现有链路（时间线→对话链→root 锚定→原帖全量）；
#   回补点=cutoffs[uid]（JSON 文件 {uid: YYYY-MM-DD}，由调用方从 post_history 最早已采日期生成）
#   或 --stop-before 统一兜底；每位博主独立产物 JSON（import-thread.js 按博主消费）。
def follow_mode(args):
    import json as _json
    cutoffs = {}
    if args.cutoffs and os.path.exists(args.cutoffs):
        cutoffs = _json.load(open(args.cutoffs, encoding='utf-8'))
    bridge = EgoBridge()
    bridge.start()
    try:
        page = bridge.main_page
        page.goto('https://xueqiu.com', wait_until='domcontentloaded', timeout=25000)
        page.wait_for_timeout(2400)
        entered = page.evaluate("""() => {
          const a = document.querySelector("a[href='/center/#/friends']");
          if (!a) return false;
          a.click();
          return true;
        }""", None)
        if not entered:
            page.goto('https://xueqiu.com/center/#/friends', wait_until='domcontentloaded', timeout=25000)
        page.wait_for_timeout(3200)
        users = page.evaluate(JS_FOLLOW_LIST, None)
        print(f'[follow] 关注列表 {len(users)} 人', flush=True)
    finally:
        bridge.stop()
    if not users:
        print('[follow] 关注列表枚举为空（登录态或 DOM 变化）', flush=True)
        sys.exit(3)
    outs = []
    for u in users:
        cutoff = cutoffs.get(u['uid']) or args.stop_before
        path = args.out or os.path.expanduser(
            f"~/.cache/xueqiu-spyder/out/dialog/follow-{u['name'] or u['uid']}-{dt.date.today():%Y%m%d}.json")
        print(f'[follow] → {u["name"]}({u["uid"]}) 回补点={cutoff or "无"}', flush=True)
        c = Collector(u['uid'], u['name'])
        degraded = c.run(args.pages, stop_before=cutoff)
        c.fix_roots()
        if args.full_root:
            c.collect_full_roots()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            _json.dump(c.out, f, ensure_ascii=False, indent=1)
        outs.append({'blogger': u['name'], 'uid': u['uid'], 'threads': len(c.out['threads']),
                     'json': path, 'degraded': bool(c.out.get('degraded')) or bool(degraded)})
        print(f"[follow] ✓ {u['name']} threads={len(c.out['threads'])} → {path}", flush=True)
    print('FOLLOW-SUMMARY ' + _json.dumps(outs, ensure_ascii=False), flush=True)
    if any(o['degraded'] for o in outs):
        sys.exit(3)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('uid', nargs='?', default='', help='博主 uid（--follow 时可省略）')
    ap.add_argument('--pages', type=int, default=3)
    ap.add_argument('--blogger', default='')
    ap.add_argument('--out', default='')
    ap.add_argument('--stop-before', default='', help='YYYY-MM-DD：时间线翻到该日期之前的帖即停（时间感知，防盲翻）')
    ap.add_argument('--full-root', action='store_true', help='原帖全量抓取：goto 原帖页抓完整正文+图片（root.full，落库侧写 origin 留档）')
    ap.add_argument('--follow', action='store_true', help='关注采集：从雪球关注列表逐博主跑到各自回补点')
    ap.add_argument('--cutoffs', default='', help='JSON 文件 {uid: YYYY-MM-DD}：每博主的回补点（--follow 用）')
    a = ap.parse_args()
    if a.follow:
        follow_mode(a)
        return
    if not a.uid:
        ap.error('需要 uid（或 --follow）')

    c = Collector(a.uid, a.blogger)
    c.run(a.pages, stop_before=a.stop_before)
    c.fix_roots()
    if a.full_root:
        c.collect_full_roots()

    path = a.out or os.path.expanduser(
        f"~/.cache/xueqiu-spyder/out/dialog/雪球对话串-{a.blogger or a.uid}-{dt.date.today():%Y%m%d}.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(c.out, f, ensure_ascii=False, indent=1)
    tail = ' [DEGRADED]' if c.out.get('degraded') else ''
    print(f'OUT {path}\nthreads={len(c.out["threads"])} replies={c.out["replies"]} origs_seen={c.out["origs_seen"]}{tail}', flush=True)
    if c.out.get('degraded'):
        sys.exit(3)


if __name__ == '__main__':
    main()
