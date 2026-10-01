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
CLEAN = lambda s: PUA.sub('', (s or '')).strip()

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

JS_ITEM_META = """(arg) => {
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
    if (mi >= 0) { meta = lines[mi]; lines.splice(mi, 1); }
    const body = lines.filter(l => !/^(收起|展开)/.test(l)).join('\\n');
    root = { url: a ? 'https://xueqiu.com' + a.getAttribute('href').split('#')[0] : null,
             author, meta, content: body };
  }
  const timeEl = [...main.querySelectorAll('a[href]')].find(a2 =>
    new RegExp('^/' + arg.uid + '/\\\\d+$').test(a2.getAttribute('href') || ''));
  return { hasDlg: !!(dlg && dlg.offsetWidth),
           url: timeEl ? 'https://xueqiu.com' + timeEl.getAttribute('href') : null,
           time: timeEl ? timeEl.textContent.replace(/\\s+/g, ' ').trim() : '',
           root };
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

JS_NODES = """() => {
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

    def extract_thread(self, page, i):
        if not page.evaluate(JS_MARK, {'i': i}):
            return None
        page.evaluate(JS_EXPAND, None)
        page.wait_for_timeout(900)
        meta = page.evaluate(JS_ITEM_META, {'uid': self.uid})
        return self._extract_chain(page, meta)

    # 详情页补采（点击式主路径，2026-10-01 用户定稿：尽量点击跳转，URL 仅兜底防风控）
    # 主路径：首页搜索框搜关键词 → 结果页点击目标帖（按 status id 匹配锚点）→ 详情页
    # 兜底：搜索未命中（索引延迟/关键词太泛）才 goto 直达，并在输出里标注 fallback
    def search_click(self, page, url, keyword):
        status_id = url.rstrip('/').split('/')[-1]
        page.goto('https://xueqiu.com')
        page.wait_for_timeout(1800)
        ok = page.evaluate("""(kw) => {
          const inp = document.querySelector('input[placeholder*="搜索"], input[type=search]');
          if (!inp) return 'no-input';
          inp.value = kw;
          inp.dispatchEvent(new Event('input', { bubbles: true }));
          inp.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', keyCode: 13, bubbles: true }));
          return 'submitted';
        }""", keyword)
        if ok != 'submitted':
            return 'no-input'
        page.wait_for_timeout(2500)
        hit = page.evaluate("""(sid) => {
          const a = [...document.querySelectorAll('a[href*="' + sid + '"]')]
            .find(x => /xueqiu\.com\/\d+\/\d+/.test(x.href) && x.offsetWidth > 0);
          if (!a) return null;
          a.removeAttribute('target');
          a.scrollIntoView({ block: 'center', behavior: 'instant' });
          a.click();
          return a.href;
        }""", status_id)
        page.wait_for_timeout(2200)
        return hit or 'no-hit'

    def collect_by_url(self, page, url, keyword='', url_fallback=True):
        via = 'search-click'
        if keyword:
            hit = self.search_click(page, url, keyword)
            if not hit or page.url.rstrip('/') == 'https://xueqiu.com/':
                via = 'url-fallback'
                if not url_fallback:
                    return {'search_failed': hit}
                page.goto(url)
                page.wait_for_timeout(2200)
            print(f'  [nav] {via} · {url}', flush=True)
        else:
            page.goto(url)
            page.wait_for_timeout(2200)
        # 详情页：正文 + 「查看对话」按钮 + 引用卡都在主文档；容器打标记复用弹窗流
        marked = page.evaluate("""() => {
          const dlg = document.querySelector('a.dialogue__btn');
          const art = (dlg && dlg.closest('article')) || document.body;
          art.setAttribute('data-zc-dlg', '1');
          const card = art.querySelector('blockquote');
          const a = [...art.querySelectorAll('a')].find(x =>
            (x.className || '').toString().includes('timeline__expand__control') && x.offsetWidth > 0);
          if (a) { a.click(); return { expanded: true }; }
          return { expanded: false };
        }""", None)
        page.wait_for_timeout(1000)
        meta = page.evaluate("""(arg) => {
          const art = document.querySelector('[data-zc-dlg]');
          if (!art) return null;
          const dlg = art.querySelector('a.dialogue__btn');
          const card = art.querySelector('blockquote');
          let root = null;
          if (card) {
            const a = [...card.querySelectorAll('a[href]')].find(x => /\\/\\d+\\/\\d+/.test(x.getAttribute('href') || ''));
            const lines = (card.innerText || '').split('\\n').map(x => x.replace(/[\\uE000-\\uF8FF]/g, '').trim()).filter(Boolean);
            let author = '', meta = '';
            if (lines.length && /^@/.test(lines[0])) author = lines[0].replace(/[：:]\\s*$/, '');
            const mi = lines.findIndex(l => /·\\s*(转发|讨论|赞)/.test(l) && /\\d/.test(l));
            if (mi >= 0) { meta = lines[mi]; lines.splice(mi, 1); }
            const body = lines.filter(l => !/^(收起|展开)/.test(l)).join('\\n');
            root = { url: a ? 'https://xueqiu.com' + a.getAttribute('href').split('#')[0] : null,
                     author, meta, content: body };
          }
          const timeEl = art.querySelector('.time') || art.querySelector('[class*=time]');
          return { hasDlg: !!(dlg && dlg.offsetWidth),
                   url: location.href.split('#')[0],
                   time: timeEl ? timeEl.textContent.replace(/\\s+/g, ' ').trim() : '',
                   root };
        }""", {'uid': self.uid})
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('uid')
    ap.add_argument('--pages', type=int, default=3)
    ap.add_argument('--blogger', default='')
    ap.add_argument('--out', default='')
    ap.add_argument('--urls', default='', help='逗号分隔的回复帖永久链接：逐条进详情页点「查看对话」，不翻时间线（待决策补采用）')
    ap.add_argument('--stop-before', default='', help='YYYY-MM-DD：时间线翻到该日期之前的帖即停（时间感知，防盲翻）')
    ap.add_argument('--keywords', default='', help='与 --urls 对齐的搜索关键词（逗号分隔；搜索点击为主路径，URL goto 兜底）')
    a = ap.parse_args()

    c = Collector(a.uid, a.blogger)
    if a.urls:
        c.start()
        try:
            page = c.bridge.main_page
            kws = [x.strip() for x in a.keywords.split(',') if x.strip()]
            for ui, u in enumerate([x.strip() for x in a.urls.split(',') if x.strip()]):
                if u in c.done:
                    print(f'[url] 已采过，跳过 {u}', flush=True)
                    continue
                kw = kws[ui] if ui < len(kws) else ''
                rec = c.collect_by_url(page, u, keyword=kw)
                if rec is None or 'orig' in rec:
                    print(f'[url] {u} 无对话入口或解析失败', flush=True)
                    continue
                turl = rec['trigger']['url']
                if turl and turl not in c.done:
                    c.out['threads'].append(rec)
                    c.out['replies'] += 1
                    c.done.add(turl)
                    print(f'[url] 串 {len(rec["nodes"])} 节点 · {turl}', flush=True)
        finally:
            c.stop()
    else:
        c.run(a.pages, stop_before=a.stop_before)

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
