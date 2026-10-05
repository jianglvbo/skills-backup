#!/usr/bin/env python3
"""雪球博主主页帖子 UI 采集器（2026-10-05 用户定稿「新方式」，重采/深窗口主力）

用户口径：进博主主页 → **逐条滚动**（真滚轮，页面肉眼可见）→ 真点击「展开」拿全文
→ 滚到底真点击「下一页」翻页；回复帖点「查看对话」开弹窗拿整链；转帖引用卡结构化为「回复内容」块；
专栏帖与展开失败帖**在卡片上点那行时间进详情页**拿全文+权威时间，再**点返回**回本页同一位置。

**输入一律是真的**（2026-10-05 去 goto 化）：走桥的 `click / clickTab / wheel`（CDP Input 域，
`isTrusted=true`），不用 `page.goto`、也不用 evaluate 里的 `el.click()`（JS 合成事件比 goto 更好认）。
详情/原帖/锚定一律**真点击开新标签、抓完就关**——不用「点进去再 back」：实测深页 back
掉页率 5/5（第 3 页进详情，back 回来落在第 1 页），一掉页后面每条锚点都找不到。
全场只剩开头进雪球首页那一次 goto。全程页面点击，不做 API 上下文盲拉。

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
    JS_MARK, JS_MODAL_OPEN_Q, JS_MODAL_STABLE,
    JS_NODES, JS_ROOT_ANCHOR, JS_ROOT_FULL, JS_TOPS, PUA,
)

ANCHOR_MS = int(time.time() * 1000)
DWELL_MS = int(os.environ.get("XUEQIU_ITEM_DWELL_MS", "700"))

_BARK = os.path.join(os.path.expanduser("~"), "Project/investment-dashboard/.agents/skills/bark/scripts/notify.py")


def waf_bark(msg):
    """疑似风控/滑块拦截的突发提醒（2026-10-05 用户要求：需要人过的验证 Bark 通知）。"""
    try:
        import subprocess
        subprocess.Popen(
            ["python3", _BARK, "--group", "investment-dashboard", "--level", "timeSensitive",
             "--id", "xueqiu-slider", "雪球疑似风控拦截", msg],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass

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
    const qa = [...card.querySelectorAll('a[href]')].find(x => {
      const h = x.getAttribute('href') || '';
      return /^\/\d+\/\d+/.test(h) || /^https?:\/\/(www\.)?xueqiu\.com\/\d+\/\d+/.test(h);
    });
    const qtitle = card.querySelector('[class*=title]');
    const qlines = strip(card.innerText || '').split('\n').map(x => x.trim()).filter(Boolean);
    let qauthor = '';
    if (qlines.length && /^@/.test(qlines[0])) { qauthor = qlines[0].replace(/[：:]\s*$/, ''); qlines.splice(0, 1); }
    const mi = qlines.findIndex(l => /·\s*(转发|讨论|赞)/.test(l) && /\d/.test(l));
    let qmeta = '';
    if (mi >= 0) { qmeta = (qlines[mi].split('·')[0] || '').trim(); qlines.splice(mi, 1); }
    quote = {
      url: qa ? (qa.getAttribute('href').startsWith('http') ? qa.getAttribute('href').split('#')[0]
                                                            : 'https://xueqiu.com' + qa.getAttribute('href').split('#')[0]) : null,
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


# 入站（G1）用：关注列表里「显示名 + href」。为什么非按名字不可——列表里的 href 常是
# 自定义域名（实测 /investinginchina、/forcode、/ericwarn），对不上 uid；
# 顺带说明 xq_dialog_collect.JS_FOLLOW_LIST 那条 `^/\d+$` 判据会**静默漏掉这些博主**（P5 修）。
FRIENDS_A = "a[href='/center/#/friends']"
JS_FOLLOW_ROWS = r"""() => {
  const out = [];
  document.querySelectorAll('.profiles__user').forEach((c, i) => {
    const a = c.querySelector('a.avatar') || c.querySelector('a[href]');
    out.push({ i: i, name: (c.innerText || '').split('\n')[0].trim(),
               href: a ? a.getAttribute('href') : null });
  });
  return out;
}"""

# 给「当前卡的展开控件」打标记，真点击由 Python 侧发（合成点击 a.click() 已退役，见 _ck）。
# 判据两条：① 排除引用卡 blockquote 里那个（沿用 JS_EXPAND 口径）；② 文案必须是「展开」——
# 展开成功后同一个控件会变成「收起」，不加这条就会一直判定成残留、白重两次点。
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


class ProfileCollector:
    def __init__(self, uid, blogger):
        self.uid = str(uid)
        self.blogger = blogger or self.uid
        self.bridge = EgoBridge()
        self.done = set()      # 已采帖 url（翻页异常重扫时跳过）
        self.posts = []        # 每帖 dict（md 导出源）
        self.threads = []      # 对话串（与 xq_dialog_collect 同构：trigger/root/nodes）
        self.origs_seen = 0
        self.roots_seen = set()   # 已排上原帖全量的 root url（跨页去重）
        self.last_visit_errored = False   # 上一次访问是「页面卡住/桥错」而非「没取到正文」
        self.pages_done = 0
        self.degraded = False
        self.expand_present = 0     # §3.5 口径：有展开控件的帖数
        self.expand_failed = 0      # 其中没点开的＝掉进 G2 详情补全的量（v1 基线 108/797＝14%）
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
        """入站（G1 点击化）：首页 1 次 goto（全场唯一一次）→ 真点「关注 N」→
        关注列表里按显示名真点该博主 → 落地校验 uid。
        找不到 / 对不上 uid 一律**明确失败退出，不静默回落 goto**——
        回落等于把最大那块风控面留着，而且会静默采错人。"""
        page = self.bridge.main_page
        if not self.blogger or self.blogger == self.uid:
            sys.exit('入站需要 --blogger 昵称：关注列表里只能按显示名定位（href 常是自定义域名）')
        page.goto('https://www.xueqiu.com/', wait_until='domcontentloaded', timeout=25000)
        page.wait_for_timeout(2200)
        # 入站每场只有这两次点击，用 trail=3 换更像真人的位移；批量环节在 _ck 里固定 trail=1
        if not self._ck(page, selector=FRIENDS_A, settle_ms=2600, trail=3):
            sys.exit('入站失败：首页找不到「关注 N」入口（登录态掉了或页面改版）；不回落 goto')
        rows = page.evaluate(JS_FOLLOW_ROWS, None) or []
        hit = [r for r in rows if r.get('name') == self.blogger]
        if not hit:
            sys.exit(f'入站失败：关注列表 {len(rows)} 行里没有「{self.blogger}」；不回落 goto')
        if not self._ck(page, selector='a.avatar', within='.profiles__user',
                        nth=hit[0]['i'], settle_ms=3000, trail=3):
            sys.exit(f'入站失败：「{self.blogger}」那一行点不动（href={hit[0]["href"]}）；不回落 goto')
        landed = re.search(r'/u/(\d+)', page.url or '')
        if not landed or landed.group(1) != self.uid:
            sys.exit(f'入站失败：点「{self.blogger}」落到 {page.url}，'
                     f'uid 对不上目标 {self.uid}（重名或列表错位）；不回落 goto')
        page.wait_for_timeout(1200)

    def recover(self, err):
        print(f'  ⚠️ 恢复：{str(err).splitlines()[0][:120]}', flush=True)
        try:
            self.bridge.stop()
        except Exception:
            pass
        self.bridge = EgoBridge()
        self.bridge.start()
        self._goto_profile()

    # ── 真输入（2026-10-05 去 goto 化）────────────────────────────────────────
    # 一律走桥的 click / wheel / back（CDP Input 域，事件 isTrusted=true）。
    # 不用 evaluate 里的 el.click()：那是 JS 合成事件（无 mousemove/hover/pointer 序列），
    # 拿它替 goto 只是换一种假动作，风控面上更显眼。
    def _ck(self, page, settle_ms=None, **kw):
        """真点击；元素本来不存在时返回 False（不抛），调用方按「这个控件没有」分支处理。
        trail 固定 1：实测单段轨迹净 513ms、三段 1896ms，批量环节花不起那个差。
        **strip_target 缺省打开**——卡片那行时间的 `<a>` 实测带 target="_blank"
        （10-05 诊断：`A.date-and-source` 的 target 就是 _blank），不摘就等于每点一次
        开一张新标签，主页面纹丝不动，还很快撞满 ego 的 8 页上限。"""
        kw.setdefault('trail', 1)
        kw.setdefault('strip_target', True)
        try:
            page.click(settle_ms=settle_ms, **kw)
            return True
        except BridgeError as e:
            if '找不到' in str(e):
                return False
            raise

    def _visit_tab(self, selectors, script=None, settle_ms=1800):
        """真点击开**新标签** → 在那张页上抓 → 关掉。时间线那张页全程不动。

        为什么不用「点进详情 + back」：10-05 实测深页 back 掉页率 5/5
        （第 3 页进详情，back 回来落在第 1 页），一掉页后面每条锚点都找不到；
        而雪球卡片那行时间的 `<a>` 本来就带 target="_blank"，真人也是这么开新标签看帖的。

        `selectors` 依次试（原帖锚点有两种形态：带 `#comment` 的「 · 讨论 N」与裸链接）。
        返回 `(info, err)`：err ∈ {None, 'no-anchor', 'hang'}。
        """
        page = self.bridge.main_page
        self.last_visit_errored = False
        for sel in selectors:
            try:
                r = page.click_tab(selector=sel, settle_ms=settle_ms, trail=1)
            except BridgeError as e:
                msg = str(e)
                if '找不到' in msg:
                    continue                       # 这条锚点形态不在卡上，换下一条
                if '没等新到标签' in msg:
                    # 该链接没带 target=_blank → 主页面被就地导航了：就地抓，再 back 回去
                    try:
                        info = page.evaluate(script or JS_ROOT_FULL, None)
                    finally:
                        try:
                            page.back(settle_ms=1200)
                        except BridgeError:
                            pass
                    return info, None
                raise
            if not r or not r.get('label'):
                return None, 'no-anchor'
            tp = self.bridge.tab_page(r['label'])
            try:
                info = tp.evaluate(script or JS_ROOT_FULL, None)
            finally:
                tp.close()                         # 随用随关：ego 一个空间只有 8 张页
            return info, None
        return None, 'no-anchor'

    def _expand(self, page):
        """真点击「展开」，带懒挂载兜底与残留重点（v1 §3.5：展开失败率决定 G2 的量级）。"""
        clicked = False
        for attempt in (1, 2):
            if attempt == 2:
                # 滚出去再滚回来，强制这张卡重新布局一次（懒挂载没等到时的兜底）
                page.wheel(dy=-900)
                page.wait_for_timeout(250)
                page.wheel(dy=900, wait_ms=400)
            if not page.evaluate(JS_MARK_EXPAND, None):
                break
            if not self._ck(page, selector='[data-zc-exp]', settle_ms=900):
                break
            clicked = True
            if not page.evaluate(JS_MARK_EXPAND, None):
                break                       # 「展开」没了 = 展开成功
            print('    [expand] 展开控件还在，滚出滚回再点一次', flush=True)
        return clicked

    # ── 对话串（复用 dialog 采集器流程：开弹窗→滚到稳定→收节点→关） ─────────
    def _close_modal(self, page):
        """真点击关弹窗；选择器依次试（同 JS_MODAL_CLOSE 那条兜底链的三种形态）。"""
        for sel in ('.modal.modal__comment .modal__hd [class*=close]',
                    '.modal.modal__comment a[class*=close]',
                    '.modal.modal__comment [class*=modal__close]'):
            if self._ck(page, selector=sel):
                return True
        return False

    def _chain(self, page, meta):
        # 「查看对话」是 `javascript:;`（没有 href），但仍是真元素，照样用真鼠标点它
        if not self._ck(page, selector='a.dialogue__btn', within='[data-zc-dlg]', settle_ms=1400):
            return None
        prev, stable = -1, 0
        while stable < 3:
            n = page.evaluate(JS_MODAL_STABLE, None)
            page.wait_for_timeout(700)
            if n == prev:
                stable += 1
            else:
                stable, prev = 0, n
        nodes = page.evaluate(JS_NODES, None)
        self._close_modal(page)
        page.wait_for_timeout(600)
        if page.evaluate(JS_MODAL_OPEN_Q, None):
            self._close_modal(page)
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
                page_todo = []      # 本页需要进详情的帖（开新标签抓，见 _visit_details）
                page_roots = []     # 本页需要原帖全量的串（锚点在本页引用卡上，见 _visit_roots）
                page_anchor = []    # 本页引用卡没给链接的串（进触发帖永久页取锚点，见 _anchor_roots）
                while i < tops:
                    try:
                        if not page.evaluate(JS_MARK, {'i': i}):
                            break
                        page.wait_for_timeout(DWELL_MS)
                        clicked = self._expand(page)
                        if clicked:
                            page.wait_for_timeout(900)
                        meta = page.evaluate(JS_POST_META, {'uid': self.uid})
                        if not meta:
                            i += 1
                            continue
                        if meta.get('expandPresent'):
                            # 与 v1 同口径（108/797＝14% 那个数）：有展开控件却没点开的才算失败
                            self.expand_present += 1
                            if not clicked:
                                self.expand_failed += 1
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
                                t = self._chain(page, meta)
                                # 返回 None＝「查看对话」没点到（控件不在或已失效）：
                                # 帖子本身照样入集，只是这一条串这次没拿到——别 continue，
                                # 那会连 self.done / posts.append / i += 1 一起跳过（死循环 + 丢帖）。
                                if t:
                                    self.threads.append(t)
                                    ru = (t.get('root') or {}).get('url')
                                    if not ru:
                                        page_anchor.append(t)   # 引用卡没给链接 → 进触发帖永久页取锚点（G3）
                                    elif ru not in self.roots_seen:
                                        # 原帖全量攒到本页收尾统一做（出了这一页，卡上的锚点就点不到了）
                                        self.roots_seen.add(ru)
                                        page_roots.append(t)
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
                        if needs_detail:
                            page_todo.append(self.posts[-1])
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
                print(f'[expand] 累计：带展开控件 {self.expand_present}，没点开 {self.expand_failed}'
                      f'（{round(100 * self.expand_failed / max(1, self.expand_present))}%，'
                      f'v1 基线 14%、§3.5 目标 <5%）', flush=True)
                # 本页采完，**趁时间线还停在本页**把需要详情的帖子补掉：
                # 现场点进详情 → 取全文/权威时间 → 关新标签（时间线那张页不动）。
                # 必须在翻页之前做完：出了这一页，卡上的锚点就再也点不到了。
                self._visit_details(page_todo, pg)
                # 先补锚定（会往 page_roots 追加新原帖），再统一去原帖页取全量
                self._anchor_roots(page_anchor, pg, page_roots)
                self._visit_roots(page_roots, pg)
                self.pages_done = pg
                if broken:
                    break
                if pg < pages:
                    if not self._ck(page, selector='a.pagination__next', settle_ms=1000):
                        print('[next] 无下一页，提前收', flush=True)
                        break
                    page.wait_for_timeout(int(xqcfg.page_delay() * 1000))
        except BridgeError:
            raise
        finally:
            pass  # 桥留给 main() 收尾再关：后置的原帖/详情补全还要用，提前关会白启一轮
        if self.roots_seen:
            got = sum(1 for t in self.threads if (t.get('root') or {}).get('full'))
            print(f'[full-root] 原帖全量：排上 {len(self.roots_seen)} 个，取到 {got} 条；'
                  f'其余留引用卡快照（锚点不在屏上就不回落 goto）', flush=True)
        return self

    # ── 原帖全量（G4）：真点击引用卡里的被引链接，开新标签抓完就关 ──────────────
    def _visit_roots(self, todo, pg):
        """进原帖页取全文+配图（新标签，时间线不动）。锚点两条（R2 录制实测）：
        普通帖走引用卡的「 · 讨论 N」（href 结尾 `#comment`），长文引用卡走标题链接
        （href 就是 `/root_uid/root_pid`）。
        两个都找不到就**留引用卡快照，不回落 goto**：原帖文字在点「展开」时已经连带到手，
        进原帖页只为取图与权威时间，取不到就是少一张图，不值得为它保留最大那块风控面。"""
        if not todo:
            return
        print(f'[full-root] 第 {pg} 页现场补原帖 {len(todo)} 条（点引用卡）', flush=True)
        snap_only = 0
        err_streak = 0
        for t in todo:
            ru = t['root']['url']
            path = '/' + re.sub(r'^https?://[^/]+/', '', ru).split('#')[0].strip('/')
            try:
                info, _err = self._visit_tab((f'a[href="{path}#comment"]', f'a[href="{path}"]'))
            except BridgeError as e:
                # 单页卡住不升级成整桥重启（10-05 教训：recover() 里一次 90s 启动超时会带走整批）
                print(f'  [full] 页面卡住：{str(e).splitlines()[0][:90]}', flush=True)
                self.last_visit_errored = True
                info = None
            err_streak = err_streak + 1 if self.last_visit_errored else 0
            if err_streak >= 2:
                print(f'  [full] 连续 {err_streak} 次页面卡住，第 {pg} 页原帖补全中止', flush=True)
                self.degraded = True
                break
            time.sleep(xqcfg.page_delay())
            if info and info.get('text'):
                full = {'text': info['text'], 'time': info['time'], 'form': info['form'],
                        'title': info.get('title') or '', 'imgs': info.get('imgs') or []}
                for t2 in self.threads:            # 同一原帖可能被多条串引用，一次抓全填回去
                    if (t2.get('root') or {}).get('url') == ru:
                        t2['root']['full'] = full
                print(f"  [full] {ru[-12:]} {len(info['text'])} 字 · 图{len(full['imgs'])}", flush=True)
            else:
                snap_only += 1
                print(f'  [full] 卡上取不到原帖页，留快照 {ru}', flush=True)
        if snap_only:
            print(f'  [full] 第 {pg} 页 {snap_only} 条只留快照', flush=True)

    def _detail_visit(self, p):
        """开**新标签**进详情页 → 取全文+权威时间 → 关标签（时间线那张页不动）。

        锚点判据（R2 录制 + P0 探针实测）：每张卡自带 `a[href="/uid/pid"]`（就是那行时间，
        带「修改于」前缀同理），**长文帖也是这一条**，不必去找标题链接；
        卡上的「转发/讨论/赞/收藏」与主页 tab 的 href 全是当前页，别误用。
        找不到锚点就标 detail_failed 留给下次，**不静默回落 goto**——回落等于把最大那块风控面留着。
        """
        path = '/' + re.sub(r'^https?://[^/]+/', '', p['url']).split('#')[0].strip('/')
        try:
            info, err = self._visit_tab([f'a[href="{path}"]'])
        except BridgeError as e:
            # 单页卡住（实测 evaluate 15s 超时「Page is still unresponsive」）不值得整桥重启：
            # 10-05 烟测就是栽在 recover() 里那次 90s 启动超时——一次页面抖动带走整批。
            print(f'  [detail] 页面卡住：{str(e).splitlines()[0][:90]}', flush=True)
            self.last_visit_errored = True
            return None
        if err == 'no-anchor':
            print(f'  [detail] 卡上找不到锚点 {path}，标摘要留给下次', flush=True)
        return info

    def _visit_details(self, todo, pg):
        """一页采完、点「下一页」之前统一补详情。
        取文（展开）必须在进详情之前做完——新标签那条路不动时间线，
        但**展开态是页面状态**，翻页/重渲染后会复位，所以本页的展开先做完再回访。"""
        todo = [p for p in todo if p.get('needs_detail') and p.get('url')]
        if not todo:
            return
        print(f'[detail] 第 {pg} 页现场补全 {len(todo)} 帖（专栏/展开失败）', flush=True)
        n_break = 0
        miss_streak = 0
        err_streak = 0
        waf_barked = False
        for p in todo:
            if n_break >= xqcfg.DETAIL_BREAK_N:
                print(f'  [detail] 长歇 {xqcfg.DETAIL_BREAK_S}s', flush=True)
                time.sleep(xqcfg.DETAIL_BREAK_S)
                n_break = 0
            info = self._detail_visit(p)
            time.sleep(xqcfg.page_delay())   # 详情页间隔同样吃 XUEQIU_PAGE_DELAY_RANGE 档
            n_break += 1
            err_streak = err_streak + 1 if self.last_visit_errored else 0
            if err_streak >= 2:
                # 连着两次「页面卡住」＝时间线/渲染器已经不可用了，继续点只会每次白等 15s
                print(f'  [detail] 连续 {err_streak} 次页面卡住，第 {pg} 页详情补全中止', flush=True)
                self.degraded = True
                break
            if info and info.get('text'):
                p['full'] = {'text': info['text'], 'time': info['time'],
                             'title': info.get('title') or '', 'imgs': info.get('imgs') or []}
                miss_streak = 0
                print(f"  [detail] {p['url'][-12:]} {len(info['text'])} 字 · 图{len(p['full']['imgs'])}", flush=True)
            else:
                p['detail_failed'] = True
                print(f"  [detail] 未取到全文 {p['url']}", flush=True)
                self.degraded = True
                miss_streak += 1
                if miss_streak >= 4 and not waf_barked:
                    waf_barked = True   # 连续取不到＝疑似滑块/风控页，Bark 提醒人看一眼（不停车）
                    waf_bark(f'详情页连续 {miss_streak} 帖取不到正文（{self.blogger}），'
                             f'疑似滑块/风控页——如果 ego lite 里有验证请你过一下，本批继续跑、过不去的标摘要留给下次')

    def _anchor_roots(self, todo, pg, page_roots):
        """G3 回复帖锚定：真点触发帖那行时间开新标签 → `JS_ROOT_ANCHOR` 取「被讨论帖」→ 关标签。
        锚到的新原帖若没排过队，追加进 `page_roots`，让本页收尾一起去原帖页。
        （原来这里是 goto 触发帖永久页——永久页 URL 就在卡上，没有必须直达的理由。）"""
        if not todo:
            return
        print(f'[root] 第 {pg} 页补锚定 {len(todo)} 条（点触发帖开新标签取锚点）', flush=True)
        for t in todo:
            tu = t.get('trigger', {}).get('url') or ''
            path = '/' + re.sub(r'^https?://[^/]+/', '', tu).split('#')[0].strip('/')
            if not path or path == '/':
                print(f'  [root] 触发帖没有永久链 {tu}', flush=True)
                continue
            err = None
            try:
                info, err = self._visit_tab((f'a[href="{path}"]',), script=JS_ROOT_ANCHOR)
            except BridgeError as e:
                print(f'  [root] 页面卡住：{str(e).splitlines()[0][:90]}', flush=True)
                info = None
            if err == 'no-anchor':
                print(f'  [root] 卡上找不到触发帖锚点 {path}', flush=True)
            time.sleep(1.2 + (xqcfg.page_delay() - 1.2) * 0.3)
            if info and info.get('rootUrl'):
                ru = info['rootUrl']
                t['root'] = {'url': ru}
                if ru not in self.roots_seen:
                    self.roots_seen.add(ru)
                    page_roots.append(t)
            elif err != 'no-anchor':
                print(f'  [root] 无锚点 {tu}', flush=True)

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
        m = re.search(r'(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})', full['time'])
        if m:
            y, mo, d, hh, mm = (int(m.group(i)) for i in range(1, 6))
            tdt2 = dt.datetime(y, mo, d, hh, mm)
            if 'T' in full['time']:   # datetime 属性 UTC 直漏（兜底，JS_ROOT_FULL 已在源头转北京时间）
                tdt2 += dt.timedelta(hours=8)
            tm = f'{tdt2.year}年{tdt2.month:02d}月{tdt2.day:02d}日 {tdt2.strftime("%H:%M")}'
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
