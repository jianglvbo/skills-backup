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
import random
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ego_browser import BridgeError, EgoBridge  # noqa: E402

PUA = re.compile(r'[\uE000-\uF8FF]')
# 尾部 UI 伪影（2026-10-02 存量实修复 250 处后的防线）：评论图片展开钮「查看图片」、
# 对话链锚点「查看对话」随 innerText 黏在文本尾部，正则只剥尾部防误伤正文
_TAIL_ARTIFACT = re.compile(r'(?:\s*(?:查看图片|查看对话|查看原图))+\s*$')
CLEAN = lambda s: _TAIL_ARTIFACT.sub('', PUA.sub('', (s or ''))).strip()

# ── 页内脚本 ──
# 读数据走 evaluate；**动作一律不走 el.click()**（那是 JS 合成事件，isTrusted=false）。
# 下面几个带 .click() 的常量（JS_EXPAND / JS_OPEN_DLG / JS_MODAL_CLOSE / JS_NEXT_PAGE）
# 现在只服务 feed.py，本采集器与 xq_profile_collect 已全部换成桥的真点击（_ck / click_tab）。

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
  // 返回 `url\\t时间锚文本`：时间锚供 --stop-before 做时间感知止损（2026-10-05 修复：
  // 此前把 URL 当时间解析，止损永不触发，--follow 回补点失效只会盲翻满页数）
  return own ? 'https://xueqiu.com' + own.getAttribute('href') + '\\t'
    + own.textContent.replace(/\\s+/g, ' ').trim() : null;
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
    // 只认雪球状态页链接（相对 /uid/statusid 或绝对 xueqiu.com/uid/statusid）；
    // 引用卡里的公告 PDF/外链（stockmc.xueqiu.com/…pdf）曾被无锚正则误配，拼出脏 URL
    const a = [...card.querySelectorAll('a[href]')].find(x => {
      const h = x.getAttribute('href') || '';
      return /^\/\d+\/\d+/.test(h) || /^https?:\/\/(www\.)?xueqiu\.com\/\d+\/\d+/.test(h);
    });
    const lines = (card.innerText || '').split('\\n').map(x => x.replace(/[\\uE000-\\uF8FF]/g, '').trim()).filter(Boolean);
    let author = '', meta = '';
    if (lines.length && /^@/.test(lines[0])) author = lines[0].replace(/[：:]\\s*$/, '');
    const mi = lines.findIndex(l => /·\\s*(转发|讨论|赞)/.test(l) && /\\d/.test(l));
    if (mi >= 0) { meta = (lines[mi].split('·')[0] || '').trim(); lines.splice(mi, 1); }   // 只留发帖时间，计数不采（2026-10-04 用户定稿）
    const body = lines.filter(l => !/^(收起|展开)/.test(l)).join('\\n');
    root = { url: a ? (a.getAttribute('href').startsWith('http') ? a.getAttribute('href').split('#')[0]
                                                                  : 'https://xueqiu.com' + a.getAttribute('href').split('#')[0]) : null,
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
  let time = '';
  if (tEl) {
    const raw = (tEl.getAttribute('datetime') || tEl.textContent || '').trim();
    /* datetime 属性是 UTC ISO（2026-10-05 实测坑：直取落库整体 −8h）——转北京时间再出 */
    const m = raw.match(/^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})(?::\d{2})?(?:\.\d+)?(Z|\+00:00)?$/);
    if (m) {
      const d = new Date(raw.replace(' ', 'T') + (m[3] || '+08:00'));
      if (!isNaN(d)) {
        const b = new Date(d.getTime() + (m[3] ? 8 * 3600e3 : 0));
        const p = n => String(n).padStart(2, '0');
        time = b.getFullYear() + '-' + p(b.getMonth() + 1) + '-' + p(b.getDate()) + ' ' + p(b.getHours()) + ':' + p(b.getMinutes());
      }
    }
    if (!time) time = raw.replace(/\s+/g, ' ').slice(0, 40);
  }
  const titleEl = document.querySelector('.article__bd__title, h1.title');
  const title = titleEl ? titleEl.textContent.trim().slice(0, 120) : '';
  return { author, time, title, form: title ? '专栏' : '短文',
           text: art.innerText.replace(/[\uE000-\uF8FF]/g, '').trim(),
           imgs: __imgs(art, art) };
}"""

# 给「当前卡的展开控件」打标记，真点击由 Python 侧发（JS_EXPAND 的 a.click() 是合成事件，已退役）。
# 判据两条：① 排除引用卡 blockquote 里那个（同 JS_EXPAND 口径）；② 文案必须是「展开」——
# 展开成功后同一个控件变「收起」，不加这条会一直判成残留、白重点两次。
JS_MARK_EXPAND = """() => {
  document.querySelectorAll('[data-zc-exp]').forEach(el => el.removeAttribute('data-zc-exp'));
  const t = document.querySelector('[data-zc-dlg]');
  if (!t) return false;
  const card = t.querySelector('blockquote');
  const a = [...t.querySelectorAll('a')].find(x =>
    (x.className || '').toString().includes('timeline__expand__control')
    && x.offsetWidth > 0 && /展开/.test(x.textContent || '')
    && (card ? !card.contains(x) : true));
  if (!a) return false;
  a.setAttribute('data-zc-exp', '1');
  return true;
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

# ── 入站（2026-10-05 去 goto 化）：首页 1 次 goto → 真点「关注 N」→ 按显示名真点博主 → 校验 uid
#   两个采集器共用这一份（xq_profile_collect 本来就 import 本模块，放这里不成环）。
#   **别在两边各写一遍**——入站分叉是最难查的那类漂移（一条会校验 uid、一条不会）。
#   ck 由调用方传（各采集器的 _ck 自带 trail / strip_target 缺省）。
FRIENDS_A = "a[href='/center/#/friends']"
JS_FOLLOW_ROWS = r"""() => {
  /* 从 a[href] 反查「人」的行。两条教训写死在这里：
     ① 不按 .profiles__user 数行——2026-10-05 ZCode 取证：只有首行带那层外壳，按它数只数到 1；
     ② 去重必须按 **href**，不能按 closest 到的容器——每人有头像＋名字两条锚点，
        这两条各自 closest 到不同的 profiles__user* 层，按容器去重会把 20 人数成 40 人（实测踩过）。
     顺带覆盖 vanity href（/investinginchina 这类），修掉「只认数字 uid 会静默漏掉设域名博主」。 */
  const norm = s => (s || '').replace(/[\s\u3000]+/g, ' ').trim();
  const byHref = new Map();
  for (const a of document.querySelectorAll('a[href]')) {
    const href = a.getAttribute('href') || '';
    if (!/^\/(?:\d+|[A-Za-z][\w.-]*)$/.test(href)) continue;
    if (/^\/(?:u|S|k|b|today|hq|center|about|snb|law|edu|verify|check|my|login|register|c)(?:\/|$)/i.test(href)) continue;
    if (byHref.has(href)) continue;
    const row = a.closest('.profiles__user__card') || a.closest('[class*=profiles__user]') || a.parentElement;
    const txt = row ? (row.innerText || '') : '';
    const name = norm(a.innerText) || (txt.split('\n').map(norm).filter(Boolean)[0] || '');
    if (!name) continue;
    byHref.set(href, { i: byHref.size, name: name.slice(0, 30), href: href,
                       uid: /^\/\d+$/.test(href) ? href.slice(1) : null });
  }
  return [...byHref.values()];
}"""

# 关注列表「下一页」的确切形态没实测过，三种写法都找一遍，命中就打标记供真点击
JS_MARK_FOLLOW_NEXT = r"""() => {
  document.querySelectorAll('[data-zc-fnext]').forEach(el => el.removeAttribute('data-zc-fnext'));
  const cand = document.querySelector('a.pagination__next')
    || [...document.querySelectorAll('a,button,span')].find(el =>
         /^(下一页|下页|>)$/.test((el.textContent || '').trim()) && el.offsetWidth > 0);
  if (!cand) return false;
  cand.setAttribute('data-zc-fnext', '1');
  return true;
}"""


def real_click(page, settle_ms=None, **kw):
    """真鼠标点击（桥的 click，走 CDP Input 域，事件 isTrusted=true）；
    元素本来不存在返回 False 而不抛，让调用方按「这个控件没有」分支处理。
    trail 固定 1：实测单段轨迹净 ~0.5s、三段 ~1.9s，批量环节花不起那个差。
    strip_target 缺省**开**——卡片那行时间的 <a> 带 target="_blank"（10-05 实踩：
    不摘的话每次点击都去开新标签、主页面纹丝不动）。只有 click_tab 故意保留 target。"""
    kw.setdefault('trail', 1)
    kw.setdefault('strip_target', True)
    try:
        page.click(settle_ms=settle_ms, **kw)
        return True
    except BridgeError as e:
        if '找不到' in str(e):
            return False
        raise


def url_path(u):
    """'https://xueqiu.com/1553799558/410655234' → '/1553799558/410655234'（DOM 里 href 就是这个形态）"""
    return '/' + re.sub(r'^https?://[^/]+/', '', u or '').split('#')[0].strip('/')


def visit_via_tab(bridge, page, selectors, script, settle_ms=1800):
    """真点击开**新标签** → 在那张页上 evaluate → 关标签；主页面（时间线）全程不动。
    为什么不用「点进去再 back」：实测深页 back 掉页率 5/5（第 3 页进详情，back 回来落在第 1 页），
    一掉页后面每条锚点都找不到。selectors 依次试，返回 (info, err)。"""
    for sel in selectors:
        try:
            r = page.click_tab(selector=sel, settle_ms=settle_ms, trail=1)
        except BridgeError as e:
            msg = str(e)
            if '找不到' in msg:
                continue
            if '没等新到标签' in msg:      # 该链接没带 target → 主页面被就地导航：就地抓再 back 兜
                try:
                    info = page.evaluate(script, None)
                finally:
                    try:
                        page.back(settle_ms=1200)
                    except BridgeError:
                        pass
                return info, None
            raise
        tp = bridge.tab_page(r['label'])
        try:
            info = tp.evaluate(script, None)
        finally:
            tp.close()                     # 随用随关：ego 一个空间只有 8 张页
        return info, None
    return None, 'no-anchor'


def friends_rows(page, want_name=None, max_pages=6, settle_ms=2600):
    """枚举关注列表，**会翻页**：实测每页只渲染 20 人而关注共 44 位，目标常在第二页之后
    （i知否 就是第 1 页找不到、旧代码因此直接判「没这个人」）。
    跨页按 href 去重累加；给了 want_name 找到即返，不浪费后面的页。
    列表是 AJAX 渲染，冷空间首开常只有占位行，所以每页先等再读。"""
    acc = {}
    pg = 0
    for pg in range(max_pages):
        page.wait_for_timeout(settle_ms if pg == 0 else 2200)
        for r in (page.evaluate(JS_FOLLOW_ROWS, None) or []):
            acc.setdefault(r['href'], r)
        rows = list(acc.values())
        for i, r in enumerate(rows):
            r['i'] = i
        if want_name and any(r['name'] == want_name for r in rows):
            return rows
        if not page.evaluate(JS_MARK_FOLLOW_NEXT, None):
            break                                   # 没有「下一页」＝已到末页
        if not real_click(page, selector='[data-zc-fnext]'):
            break
    return list(acc.values())


def enter_profile_by_click(ck, page, uid, blogger, fail=sys.exit):
    """uid 传 None = 只求「解析出落地 uid」并返回（--follow 遇到 vanity 行时先要拿到 uid）。"""
    """进某位博主主页，全程真点击。找不到 / 落地 uid 对不上 → fail() 退出，
    **不静默回落 goto**：回落等于把最大那块风控面留着，而且会静默采错人。
    为什么按显示名：列表里的 href 常是自定义域名（实测 /investinginchina、/forcode、/ericwarn），
    对不上 uid；点进去才 302 到 /u/<uid>，所以落地后必须回头校验 uid。"""
    if not blogger or blogger == str(uid):
        fail('入站需要博主昵称：关注列表里只能按显示名定位（href 常是自定义域名）')
    page.goto('https://www.xueqiu.com/', wait_until='domcontentloaded', timeout=25000)
    page.wait_for_timeout(2200)
    # 入站每场就这两次点击，用 trail=3 换更像真人的位移；批量环节在 _ck 里固定 trail=1
    if not ck(selector=FRIENDS_A, settle_ms=2600, trail=3):
        fail('入站失败：首页找不到「关注 N」入口（登录态掉了或页面改版）；不回落 goto')
    rows = friends_rows(page, want_name=blogger)
    hit = [r for r in rows if r.get('name') == blogger]
    if not hit:
        fail(f'入站失败：关注列表翻页后累计 {len(rows)} 人仍没有「{blogger}」；不回落 goto')
    # 按 href 精确点，不用 nth+外壳选择器：新渲染形态下只有首行有外壳，按序号点会点空
    if not ck(selector=f'a[href="{hit[0]["href"]}"', settle_ms=3000, trail=3):
        fail(f'入站失败：「{blogger}」那一行点不动（href={hit[0]["href"]}）；不回落 goto')
    landed = re.search(r'/u/(\d+)', page.url or '')
    got = landed.group(1) if landed else None
    if uid is None:
        if not got:
            fail(f'入站失败：点「{blogger}」后落地 URL 里没有 uid（{page.url}）')
        return got
    if got != str(uid):
        fail(f'入站失败：点「{blogger}」落到 {page.url}，uid 对不上目标 {uid}（重名或列表错位）；不回落 goto')
    page.wait_for_timeout(1200)
    return got


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
        # 去 goto 化（10-05）：锚定与原帖全量都挪到「每页收尾」做，因为只有那时卡上还有锚点
        self.root_queue = set()      # 已排队的原帖 url（跨页去重）
        self.pending_roots = []      # 本页刚锚出 root.url、等着抓全量的串

    def _expand(self, page):
        """真点击「展开」：标记→真点→残留则 wheel 滚出滚回再点一次（懒挂载兜底）。"""
        clicked = False
        for attempt in (1, 2):
            if attempt == 2:
                page.wheel(dy=-900)
                page.wait_for_timeout(250)
                page.wheel(dy=900, wait_ms=400)
            if not page.evaluate(JS_MARK_EXPAND, None):
                break
            if not self._ck(page, selector='[data-zc-exp]', settle_ms=900):
                break
            clicked = True
            if not page.evaluate(JS_MARK_EXPAND, None):
                break
        return clicked

    def _close_modal(self, page):
        """真点击关弹窗；选择器依次试（同 JS_MODAL_CLOSE 那条兜底链的三种形态）。"""
        for sel in ('.modal.modal__comment .modal__hd [class*=close]',
                    '.modal.modal__comment a[class*=close]',
                    '.modal.modal__comment [class*=modal__close]'):
            if self._ck(page, selector=sel):
                return True
        return False

    def _flush_page(self, page_threads, full_root):
        """本页收尾：锚点还在这页的卡上，先补锚定（会往 pending_roots 追加），再抓原帖全量。
        顺序不能反——锚出来的新原帖也得在这一页被抓掉，出了这页就点不到了。"""
        self.fix_roots(page_threads)
        if full_root:
            self.collect_full_roots(page_threads + self.pending_roots)
        self.pending_roots = []

    def start(self):
        self.bridge.start()
        self._goto_profile()

    def stop(self):
        self.bridge.stop()

    def _ck(self, page, **kw):
        return real_click(page, **kw)

    def _goto_profile(self):
        """入站：首页 1 次 goto → 真点「关注 N」→ 按显示名真点该博主 → 校验落地 uid。
        实现与 xq_profile_collect 共用上面那一份（见 enter_profile_by_click）。"""
        page = self.bridge.main_page
        if not self.uid:
            # vanity 行（自定义域名）枚举时拿不到 uid：先点进去把落地 uid 解析出来再扫
            self.uid = enter_profile_by_click(lambda **kw: self._ck(page, **kw), page, None, self.blogger)
            print(f'[follow] 「{self.blogger}」uid 解析为 {self.uid}', flush=True)
        else:
            enter_profile_by_click(lambda **kw: self._ck(page, **kw), page, self.uid, self.blogger)
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

    def fix_roots(self, threads=None):
        """root 锚定补齐（2026-10-02 串条聚合挂根帖卡的前置）：被回复对象是评论时，
        时间线引用卡里没有 status 链接 → root 空；打开回复帖永久页取上下文锚点
        （a.fake-anchor / a.replay-count → 原帖 status id）。
        2026-10-05 去 goto 化：原来是 goto 回复帖永久页，现在真点触发帖那行时间开新标签。
        ⚠ 所以**必须在锚点还在屏上的那一页收尾时调**（threads 传本页的串），
          等全场扫完再调，卡就点不到了——原来那版正是靠 goto 才敢拖到最后。"""
        need = [t for t in (threads if threads is not None else self.out['threads'])
                if not (t.get('root') or {}).get('url')]
        if not need:
            return
        print(f'[root] 补锚定 {len(need)} 条', flush=True)
        page = self.bridge.main_page
        for t in need:
            path = url_path(t['trigger']['url'])
            try:
                info, err = visit_via_tab(self.bridge, page, (f'a[href="{path}"]',), JS_ROOT_ANCHOR)
                # ⚠ 只锚 URL 不抓正文：回复帖永久页的 .article__bd 是回复帖正文（文不对题），
                # 2026-10-02 实测写过脏 root_content；原帖正文快照需在 root_url 页面抓，另做
                if info and info.get('rootUrl'):
                    t['root'] = {'url': info['rootUrl']}
                    ru = info['rootUrl']
                    if ru not in self.root_queue:
                        self.root_queue.add(ru)
                        self.pending_roots.append(t)
                else:
                    print(f"[root] 无锚点 {t['trigger']['url']} ({err or '页面无锚点'})", flush=True)
            except BridgeError as e:
                print(f'[root] 桥错误: {str(e).splitlines()[0][:90]}', flush=True)
                self.recover(f'{e}')

    def collect_full_roots(self, threads=None):
        """原帖全量：真点引用卡里的被引链接开新标签，抓完整正文（保留段落）+时间·形态+图片
        → root.full；落库侧写 post_history（post_kind_code='origin'）并回填 root_ph_id。
        ⚠ 与 fix_roots 的「只锚 URL」不矛盾：那里在**回复帖页**锚定，这里在**原帖页**抓正文。
        锚点两条形态（10-05 录制实测）：普通帖走「 · 讨论 N」（href 结尾 #comment），
        长文引用卡走标题链接。两条都没有就**留引用卡快照，不回落 goto**。"""
        pool = threads if threads is not None else self.out['threads']
        need, seen = [], set()
        for t in pool:
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
            path = url_path(ru)
            info = None
            try:
                info, err = visit_via_tab(self.bridge, page,
                                          (f'a[href="{path}#comment"]', f'a[href="{path}"]'),
                                          JS_ROOT_FULL)
                if err:
                    print(f'  [full] 卡上取不到原帖页（{err}），留快照 {ru}', flush=True)
            except BridgeError as e:
                # 单页卡住不升级成整桥重启（10-05 教训：recover 里一次 90s 启动超时会带走整批）
                print(f'[full-root] 页面卡住: {str(e).splitlines()[0][:90]}', flush=True)
                continue
            time.sleep(1.2 + random.random() * 0.6)
            if info and info.get('text'):
                full = {'text': info['text'], 'time': info['time'],
                        'form': info['form'], 'title': info.get('title') or '',
                        'imgs': info.get('imgs') or []}
                # 同根兄弟串共享 full（seen 去重只抓一次，落库按 url_hash 幂等同源）
                for t2 in self.out['threads']:
                    if (t2.get('root') or {}).get('url') == ru:
                        t2['root']['full'] = full
                print(f"  [full] {ru[-12:]} {len(info['text'])} 字 · 图{len(full['imgs'])}", flush=True)
            else:
                print(f'  [full] 无正文容器 {ru}', flush=True)

    def extract_thread(self, page, i):
        if not page.evaluate(JS_MARK, {'i': i}):
            return None
        self._expand(page)
        page.wait_for_timeout(900)
        meta = page.evaluate(JS_ITEM_META, {'uid': self.uid})
        return self._extract_chain(page, meta)

    def _extract_chain(self, page, meta):
        if not meta:
            return None
        if not meta['hasDlg']:
            self.out['origs_seen'] += 1
            return {'orig': meta.get('url')}
        # 「查看对话」是 javascript:;（没有 href），但仍是真元素，照样用真鼠标点它
        if not self._ck(page, selector='a.dialogue__btn', within='[data-zc-dlg]', settle_ms=1400):
            return None
        prev, stable = -1, 0
        prev, stable = -1, 0
        while stable < 3:
            n = page.evaluate(JS_MODAL_STABLE, None)
            page.wait_for_timeout(700)
            if n == prev:
                stable += 1
            else:
                stable, prev = 0, n
        nodes = page.evaluate(JS_NODES, None)
        closed = self._close_modal(page)
        page.wait_for_timeout(600)
        if page.evaluate(JS_MODAL_OPEN_Q, None):
            self._close_modal(page)
            page.wait_for_timeout(500)
        for seq, nd in enumerate(nodes):
            nd['seq'] = seq
            nd['parent_seq'] = seq - 1 if seq else None
            # comment_id≠status id（拼接 URL 实测 404），不拼 permalink；comment_id 留作稳定键
        return {'trigger': {'url': meta['url'], 'time': meta['time']},
                'root': meta['root'], 'nodes': nodes, '_modal_close': closed}

    def run(self, pages, stop_before='', full_root=False):
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
                page_threads = []    # 本页采到的串（收尾时它们的锚点还在屏上）
                while i < tops:
                    try:
                        if not page.evaluate(JS_MARK, {'i': i}):
                            break
                        quick = page.evaluate(JS_OWN_URL, {'uid': self.uid})
                        qurl, _, qtime = (quick or '').partition('\t')
                        # 时间感知止损：时间线自上而下由新到旧，本帖已早于目标日期 → 后面只会更旧
                        if stop_before and qtime:
                            if self._older_than(qtime, stop_before):
                                print(f'[time] {qurl} 早于 {stop_before}，时间线已滚过目标窗口，止损', flush=True)
                                self.out['pages'] = pg
                                self._flush_page(page_threads, full_root)   # 止损也要收尾，否则本页锚点白丢
                                return degraded
                        if qurl and qurl in self.done:
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
                            page_threads.append(rec)
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
                self._flush_page(page_threads, full_root)
                out_pages = pg
                if pg < pages:
                    if not real_click(page, selector='a.pagination__next', settle_ms=1000):
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
        page.goto('https://xueqiu.com/', wait_until='domcontentloaded', timeout=25000)
        page.wait_for_timeout(2200)
        # 真点「关注 N」进列表（原来这里也是 JS 合成点击 + 点不动就 goto 兜底，两处都换掉了）
        if not real_click(page, selector=FRIENDS_A, settle_ms=2600, trail=3):
            print('[follow] 首页找不到「关注 N」入口（登录态掉了或页面改版）；不回落 goto', flush=True)
            users = []
        else:
            rows = friends_rows(page)               # 等 AJAX 渲染完（冷空间首开常只有占位行）
            users = [{'uid': r.get('uid') or '', 'name': r['name'], 'href': r['href']} for r in rows]
            n_vanity = sum(1 for u in users if not u['uid'])
            if n_vanity:
                print(f'[follow] 其中 {n_vanity} 位是自定义域名（无数字 uid），'
                      f'跑之前先点进去解析 uid（旧枚举只认 /^\/\d+$/，会静默跳过这些人）', flush=True)
        print(f'[follow] 关注列表 {len(users)} 人', flush=True)
    finally:
        bridge.stop()
    if not users:
        print('[follow] 关注列表枚举为空（登录态或 DOM 变化）', flush=True)
        sys.exit(3)
    outs = []
    for u in users:
        # vanity 行枚举时无 uid，回补点这一轮只能退到 --stop_before 兜底（跑完会回填 u['uid']）
        cutoff = cutoffs.get(u['uid']) or args.stop_before
        path = args.out or os.path.expanduser(
            f"~/.cache/xueqiu-spyder/out/dialog/follow-{u['name'] or u['uid']}-{dt.date.today():%Y%m%d}.json")
        print(f'[follow] → {u["name"]}({u["uid"]}) 回补点={cutoff or "无"}', flush=True)
        c = Collector(u['uid'] or None, u['name'])
        # 锚定与原帖全量已在 run() 里**每页收尾**做完（去 goto 化：出了那一页卡上就没锚点了）
        c.run(args.pages, stop_before=cutoff, full_root=args.full_root)
        u['uid'] = c.uid                     # vanity 行回填解析出来的 uid（供日志与产物名用）
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
    c.run(a.pages, stop_before=a.stop_before, full_root=a.full_root)

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
