"""流式采集（关注/热门时间线）——feed 模式，2026-09-26 实测定型。

与 user 模式（逐博主主页翻页）的关系：feed 是**日常增量缺省路径**（一次会话覆盖全部
已关注博主，风控暴露最小），user 模式保留给首采/深窗口/补漏。断点不是逐博主
info_cutoff，而是**流位置书签**（~/.cache/xueqiu-spyder/feed-state.json）。

机制要点（2026-09-26 实测，改前先读）：
- **切 tab**：桥新开会话默认落「热门」，关注流必须显式点「关注」tab。
- **展开控件**：`a.timeline__expand__control`——textContent 带不可见 iconfont 图形字符，
  按 textContent==='展开' 匹配永远 0 命中，须按类名或 innerText 匹配。
- **展开要 dwell**：handler 进视口才惰性挂载，scrollIntoView 后停 ~1s 再点才生效；
  380-450ms 就点全部落空。点完回读正文验证，失败换候选重试一次。
- **计数在流内**：`.timeline__item__ft` 三个 a.timeline__item__control 依次
  转发/讨论/赞——数字>0 显示数字，=0 显示标签。
- **引用卡**：`.timeline__item__forward__content` 是正文块的**兄弟节点**（转帖引用），
  回复帖同名元素装的是对话预览——回复帖（正文以「回复@」开头）不得拼引用卡。
- **时间**：流内相对时间锚定采集瞬间换算绝对时间；「修改于」= 编辑时间非首发时间，
  标记（修改于·推算）。例外帖（自身专栏/展开失败）走详情页拿权威时间与全文。
- **风控**：例外 URL 节流 2.6-3.4s、连续 3 次失败熔断；流会话本身是自然浏览形态。

退出码：0=产出 / 2=窗口内无帖 / 3=书签未翻到（滚动地板/上限，禁止写断点）/ 1=失败。
"""
import json
import os
import random
import re
import time
import urllib.request
from datetime import datetime, timedelta

import ego_browser
from analyzer import extract_images

N_TARGET_DEFAULT = 50
PACE = (2.6, 3.4)          # 例外详情页节流
BREAK_N = 3
URL_BUDGET = 50            # 单次采集例外详情页主动预算（防 WAF 连击；超出即停、余下标摘要）
MAX_SCROLL = 120           # 滚动步数硬上限（含点「加载更多」，≈1000 条），超出按「书签未翻到」处理
STATE_PATH = os.path.expanduser("~/.cache/xueqiu-spyder/feed-state.json")
DASHBOARD = "http://127.0.0.1:8698"

# ── 注入页面的 JS（均已实测）─────────────────────────────────────────
CLICK_TAB_JS = r"""
(name) => {
  for (const bar of [...document.querySelectorAll('div,nav,ul')]) {
    const f = [...bar.querySelectorAll('*')].find(c => c.textContent.trim() === name && c.children.length === 0);
    if (f && [...bar.parentElement.querySelectorAll('*')].some(c => c.textContent.trim() === '7x24' || c.textContent.trim() === '自选')) {
      f.click(); return true;
    }
  }
  return false;
}
"""
ITEM_COUNT_JS = "() => document.querySelectorAll('.timeline__item').length"
SCROLL_JS = "() => { const y0 = window.scrollY; window.scrollBy(0, 2600); return { y0, y1: window.scrollY }; }"
# 关注流不是纯无限滚动：每加载一批底部出现「加载更多」按钮（a.home__timeline__more），
# 不点它滚轮/scrollBy 都停在 95 条（2026-09-26 实测——用户手动滑动时其实点过它）。
LOAD_MORE_JS = r"""
() => {
  window.scrollTo(0, document.body.scrollHeight);
  const btn = [...document.querySelectorAll('a.home__timeline__more')].find(e => e.offsetWidth || e.offsetHeight);
  if (btn) { btn.click(); return true; }
  return false;
}
"""
OLDEST_LABEL_JS = r"""
() => {
  const items = [...document.querySelectorAll('.timeline__item')];
  if (!items.length) return null;
  const t = (items[items.length - 1].querySelector('.date-and-source') || {}).innerText || '';
  return t.trim();
}
"""
LIST_EXPAND_JS = r"""
() => [...document.querySelectorAll('.timeline__item')]
  .filter(it => { const c = it.querySelector('.timeline__item__content'); return c && (c.innerText || '').includes('展开'); })
  .map(it => { const a = it.querySelector('a[href].date-and-source'); return a ? a.getAttribute('href') : null; })
  .filter(Boolean)
"""
SCROLL_ONE_JS = r"""
(href) => {
  const a = document.querySelector('.timeline__item a[href].date-and-source[href="' + href + '"]');
  if (!a) return { gone: true };
  const btn = a.closest('.timeline__item').querySelector('a.timeline__expand__control');
  if (!btn) return { gone: false, nobtn: true };
  btn.scrollIntoView({ block: 'center' });
  return { ok: true };
}
"""
CLICK_ONE_JS = r"""
(href) => {
  const a = document.querySelector('.timeline__item a[href].date-and-source[href="' + href + '"]');
  if (!a) return { gone: true };
  const it = a.closest('.timeline__item');
  const content = it.querySelector('.timeline__item__content');
  if (!content || !content.innerText.includes('展开')) return { ok: true, done: true };
  const btn = it.querySelector('a.timeline__expand__control');
  if (!btn) return { ok: false, nobtn: true };
  btn.click();
  return { ok: true, done: !content.innerText.includes('展开') };
}
"""
FEED_JS = r"""
() => {
  const num = (t) => /^\d+$/.test(t) ? parseInt(t, 10) : 0;
  // 配图：流式卡里图有两种载体——已渲染的 <img src>（带 !800.jpg/!custom.jpg 尺寸档）与被折叠成
  // <a class="co-img-link" href="…jpg">查看图片</a>。读属性即可，**不需要点开**（2026-10-01 实测）。
  // ⚠ 归属只认**本帖自己**：回复/转帖卡会把「被回复帖」的图渲染进同一张卡，整卡无差别扫描就把父帖
  //   的图算到回复头上（2026-10-01 实测：post_image 45 行只对应 24 个不同 URL，「斯宾诺莎的世界」
  //   9 条回复共用 1 张图，含图率被虚高到 38.6%）。用户拍板「图归真正拥有它的帖」。
  //   判据＝只跳过 [class*=forward] 子树。两条实测出来的边界，别再改：
  //   ① 不能把范围收到 .timeline__item__content——本帖自己的图长在 div.content__addition.pic__thumb
  //      （正文块的**兄弟**、挂在卡尾），正文块里只有折叠态的 a.co-img-link，按正文块扫真图全丢。
  //   ② 不能顺手排除 <blockquote>——雪球把**本帖自己的九宫格**渲染成
  //      blockquote.status__images.status__images--timeline（实测 CSCK 411157111 的 3 张自拍图），
  //      带 blockquote 判据会把它们一起删掉；被引帖的图外层是 blockquote.timeline__item__forward，
  //      [class*=forward] 一条就够（实测 411156409：自己的 a.co-img-link 留下、父帖两处图都挡掉）。
  const junk = u => !u || /emoji|face_regular|badge|medal|identity_icon|xavatar|\/community\/|_logo|icon_|commentlist_tag|_tag-|sprite/i.test(u);
  const norm = u => (u || '').replace(/!\d*x*\d*\.jpg$|!custom\.jpg$|!800\.jpg$/, '');
  const collectImgs = (card, content) => {
    const out = [], seen = {};
    const blocks = content ? Array.from(content.children) : [];
    const paraOf = n => {
      let k = 0;
      for (const b of blocks) {
        if (b === n || b.contains(n)) return k;
        if ((b.innerText || '').trim()) k++;
      }
      return -1;                       // 图不在正文块内（如折叠锚在卡尾）→ -1，渲染端退卡尾
    };
    for (const nd of card.querySelectorAll('img, a.co-img-link, a[href]')) {
      if (nd.closest('[class*=forward]')) continue;   // 被引/父帖的图不归本帖（自己的九宫格也是 blockquote，不能按 blockquote 判）
      const raw = nd.tagName === 'IMG' ? nd.src : nd.getAttribute('href');
      if (junk(raw)) continue;
      const u = norm(raw);
      if (!u || !/\.(png|jpe?g|gif|webp|bmp)$/i.test(u) || seen[u]) continue;
      seen[u] = 1;
      out.push({url: u, para: content ? paraOf(nd) : -1});
    }
    return out;
  };
  const rows = [];
  for (const it of [...document.querySelectorAll('.timeline__item')]) {
    const nameA = it.querySelector('.user-name');
    const content = it.querySelector('.timeline__item__content');
    const permA = it.querySelector('a[href].date-and-source');
    const timeA = it.querySelector('.date-and-source');
    if (!permA) continue;
    const controls = [...it.querySelectorAll('.timeline__item__ft a.timeline__item__control')]
      .slice(0, 3).map(a => (a.querySelector('span:last-child') || a).innerText.trim());
    const ctext = (content ? content.innerText : '');
    let quoted = null;
    const fwd = it.querySelector('.timeline__item__forward__content');
    if (fwd) {
      const anchors = [...fwd.querySelectorAll('a[href]')];
      const qLink = anchors.map(a => a.getAttribute('href')).find(h => /^\/(\d+|[\w-]+)\/\d+$/.test(h || ''));
      const wrap = fwd.parentElement && fwd.parentElement !== it ? fwd.parentElement : fwd;
      const wrapTxt = wrap.innerText || '';
      const lines = wrapTxt.split('\n').map(s => s.trim()).filter(Boolean);
      const qAuthor = (lines.find(l => /^@.+[:：]$/.test(l)) || lines.find(l => /^@/.test(l)) || '').replace(/[:：]\s*$/, '');
      const titleA = anchors.find(a => a.getAttribute('href') === qLink && (a.innerText || '').trim().length >= 6);
      const footLine = [...lines].reverse().find(l => /·\s*转发\s*\d+/.test(l)) || '';
      const fm = /((?:昨天|今天)?\s*\d+\s*(?:分钟|小时|天)前|\d{2}-\d{2}\s*\d{2}:\d{2})\s*·\s*转发\s*(\d+)\s*·\s*讨论\s*(\d+)\s*·\s*赞\s*(\d+)/.exec(footLine) || [];
      quoted = {
        url: qLink || null,
        author: qAuthor || null,
        title: titleA ? titleA.innerText.trim() : null,
        isColumn: wrapTxt.includes('专栏'),
        time: fm[1] || '',
        counts: [parseInt(fm[2] || 0, 10), parseInt(fm[3] || 0, 10), parseInt(fm[4] || 0, 10)],
        lead: lines.filter(l => l !== footLine && l !== qAuthor && l !== (titleA ? titleA.innerText.trim() : '\u0000'))
                   .join('\n').slice(0, 1200),
      };
    }
    rows.push({
      href: permA.getAttribute('href'),
      author: nameA ? nameA.innerText.trim() : null,
      timeLabel: timeA ? timeA.innerText.trim() : null,
      column: !!it.querySelector('.timeline__item__title'),
      trunc: /展开/.test(ctext),
      counts: [num(controls[0] || ''), num(controls[1] || ''), num(controls[2] || '')],
      quoted,
      text: ctext.slice(0, 20000),
      imgs: collectImgs(it, content),
    });
  }
  return { now: Date.now(), rows };
}
"""
DETAIL_JS = r"""
() => {
  const art = document.querySelector('.article__bd');
  const bodyTxt = document.body.innerText || '';
  const blocked = /滑动|安全验证|captcha|访问验证/i.test(bodyTxt.slice(0, 3000));
  const head = (bodyTxt.match(/(发布于|修改于)\s*(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})/) || []);
  return {
    blocked,
    timeRaw: head.length ? head[2] + ' ' + head[3] : '',
    edit: head.length ? head[1] : null,
    content: art ? (art.innerText || '').slice(0, 20000) : null,
  };
}
"""

# ── 文本清洗 ─────────────────────────────────────────────────────────

def clean_feed(t):
    t = (t or "").replace("\u00a0", " ").strip()
    for _ in range(3):
        t = re.sub(r"(收起|展开|查看对话|查看图片)\s*$", "", t).strip()
    return t


def tidy_article(t):
    t = (t or "").strip()
    t = re.sub(r"(收起|展开|查看对话|查看图片)\s*$", "", t).strip()
    t = re.sub(r"\s*·\s*转发\s*\d+\s*·\s*讨论\s*\d+\s*·\s*赞\s*\d+\s*$", "", t).strip()
    # 保留段落分段（2026-09-28）：innerText 的 \n 只出现在块边界/<br>，是真实换行不是视觉折行
    # （库内 80 条样本实测：\n 前多为。？！，后为新段落开头）。只合并两类明显半句断行——
    # 下一行以标点开头（段落不会以标点起头）、本行以开括号收尾；不再按行尾标点全量压行，
    # 否则「谁能笑到最后？\n汇丰在1月7日…」这类真分段会被合并成一行。
    t = re.sub(r"\n([，。、；：）」！？])", r"\1", t)
    t = re.sub(r"([（「])\n", r"\1", t)
    return t


def clean_quote(t):
    t = tidy_article(t)
    # 引用卡尾注（转发项可能缺省）：「4小时前 · 讨论 70 · 赞 19」「09-24 17:55 · 转发 1 · 讨论 2 · 赞 3」
    t = re.sub(r"\s*(?:(?:昨天|今天)?\s*\d{1,2}:\d{2}|\d{1,2}-\d{1,2}\s*\d{1,2}:\d{2}|\d+\s*(?:分钟|小时|天)前)"
               r"\s*(?:·\s*(?:转发|讨论|评论|赞)\s*\d+)*\s*$", "", t).strip()
    # 孤立标签行（专栏标记由样式承载）与残留冒号
    t = re.sub(r"^\s*专栏\s*$", "", t, flags=re.M).strip()
    t = re.sub(r"^[:：]+\s*", "", t).strip()
    for _ in range(2):
        t = re.sub(r"(收起|展开|查看对话|查看图片)\s*$", "", t).strip()
    return t


def first_sentence(t):
    t = (t or "").strip()
    m = re.match(r"^(.*?[。！？])", t, re.S)
    # 标题必须单行：正文分段后首句可能跨段，\n 进标题会撑破帖子集 md 的「## 标题」结构
    return re.sub(r"\s+", " ", m.group(1)).strip() if (m and m.group(1).strip()) else re.sub(r"\s+", " ", t[:30]).strip()


def derive_time(label, anchor_ms):
    """流内相对时间 → 绝对 datetime（锚定采集瞬间）。返回 (datetime, edited:bool) 或 (None, False)。"""
    if not label:
        return None, False
    edited = "修改于" in label
    label = label.replace("修改于", "").strip()
    now = datetime.fromtimestamp(anchor_ms / 1000)
    m = re.search(r"(\d+)\s*分钟前", label)
    if m:
        return now - timedelta(minutes=int(m.group(1))), edited
    m = re.search(r"(\d+)\s*小时前", label)
    if m:
        return now - timedelta(hours=int(m.group(1))), edited
    m = re.search(r"(昨天|今天)\s*(\d{1,2}):(\d{2})", label)
    if m:
        d = now.date() - timedelta(days=1 if m.group(1) == "昨天" else 0)
        return datetime(d.year, d.month, d.day, int(m.group(2)), int(m.group(3))), edited
    m = re.search(r"(\d{1,2})-(\d{1,2})\s+(\d{1,2}):(\d{2})", label)
    if m:
        dt = datetime(now.year, int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4)))
        if dt > now:                      # 跨年：12-31 的帖在 1 月看到
            dt = dt.replace(year=now.year - 1)
        return dt, edited
    return None, False


def label_older_than(label, anchor_ms, since_dt):
    dt, _ = derive_time(label, anchor_ms)
    return dt is not None and dt <= since_dt


def load_tracked():
    """看板博主清单（uid → 昵称）；服务不可用返回 None（不过滤，交给入库侧跳过）。
    闸门（2026-09-27）：loopback 也要 Bearer mcpToken，凭据从仓库 config.json 读。"""
    try:
        req = urllib.request.Request(DASHBOARD + "/api/bloggers/live")
        try:
            with open(os.path.expanduser("~/Project/investment-dashboard/src/config.json")) as f:
                tok = json.load(f).get("mcpToken", "")
            if tok:
                req.add_header("Authorization", "Bearer " + tok)
        except Exception:
            pass
        with urllib.request.urlopen(req, timeout=3) as r:
            data = json.loads(r.read().decode("utf-8"))
        bloggers = data.get("data") if isinstance(data, dict) else data
        m = {}
        for b in bloggers or []:
            xid = str(b.get("xueqiuId") or "").strip()
            if xid:
                m[xid] = b.get("name")
        return m or None
    except Exception:
        return None


def load_bookmark(tab):
    try:
        with open(STATE_PATH, encoding="utf-8") as fh:
            return json.load(fh).get(tab)
    except Exception:
        return None


def save_bookmark(tab, iso):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    state = {}
    try:
        with open(STATE_PATH, encoding="utf-8") as fh:
            state = json.load(fh)
    except Exception:
        pass
    state[tab] = iso
    with open(STATE_PATH, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=1)


# ── 主流程 ───────────────────────────────────────────────────────────

def run_feed(tab="follow", limit=N_TARGET_DEFAULT, since=None, output_dir=None,
             outfile=None, filter_mode="auto", use_bookmark=True):
    import logging
    logger = logging.getLogger(__name__)
    if output_dir is None:
        output_dir = os.path.expanduser("~/.cache/xueqiu-spyder/out")
    since_dt = None
    if since:
        since_dt = datetime.strptime(since.replace("T", " ")[:19], "%Y-%m-%d %H:%M:%S")
    elif tab == "follow" and use_bookmark:
        bm = load_bookmark(tab)
        if bm:
            since_dt = datetime.strptime(bm.replace("T", " ")[:19], "%Y-%m-%d %H:%M:%S")
            logger.info("流断点书签: %s（上次采集起点之后的内容为本轮窗口）", bm)

    tracked = None
    if filter_mode == "on" or (filter_mode == "auto" and tab == "follow"):
        tracked = load_tracked()
        logger.info("看板博主过滤: %s", f"{len(tracked)} 位" if tracked else "不可用（不过滤，未建档帖由入库侧跳过）")

    b = ego_browser.EgoBridge()
    b.start()
    anchor_ms, raw = 0, []
    try:
        p = b.main_page
        try:
            p.wait_for_selector(".timeline__item", timeout=10000)
        except Exception:
            time.sleep(4)
        tab_name = {"follow": "关注", "hot": "热门"}.get(tab, tab)
        p.evaluate(CLICK_TAB_JS, tab_name)
        time.sleep(2.5)
        # 滚动：凑 limit 且（有书签时）翻到书签；stale×3 或步数上限 → 可能书签未翻到
        stale, steps, reached = 0, 0, since_dt is None
        while steps < MAX_SCROLL:
            n = p.evaluate(ITEM_COUNT_JS, None)
            if n >= limit and (reached or since_dt is None):
                break
            if since_dt is not None and n > 0:
                oldest = p.evaluate(OLDEST_LABEL_JS, None)
                if label_older_than(oldest, anchor_ms if anchor_ms else time.time() * 1000, since_dt):
                    reached = True
                    if n >= limit:
                        break
            sc = p.evaluate(SCROLL_JS, None) or {}
            steps += 1
            time.sleep(1.2)
            # 停滞判定＝滚动位置不再推进（条数与最旧标签在同分组内都会停滞，2026-09-26 实测：
            # 96 条处最旧标签卡住但流仍在加载，按它们判 stale 会提前误停）
            if not sc.get("y1") or (sc.get("y1", 0) - sc.get("y0", 0)) < 100:
                # 滚到底仍不动 → 点「加载更多」续流（关注流分批加载，不点永远停在 95 条）
                if p.evaluate(LOAD_MORE_JS, None):
                    time.sleep(2.2)
                    stale = 0
                    continue
                stale += 1
                if stale >= 3:
                    break
            else:
                stale = 0
        if since_dt is not None and not reached:
            oldest = p.evaluate(OLDEST_LABEL_JS, None)
            reached = label_older_than(oldest, time.time() * 1000, since_dt)

        # 流内展开：节奏在 Python 侧（ego 后台页会节流页内 setTimeout，页内长循环必超时；
        # 且 ego evaluate 有 15s 页内上限——页内只做瞬时动作，dwell 全在桥外）
        exp_ok, exp_remaining = 0, 0
        for _round in range(3):
            hrefs = p.evaluate(LIST_EXPAND_JS, None) or []
            if not hrefs:
                break
            logger.info("展开第 %s 轮：%s 条待展开", _round + 1, len(hrefs))
            for href in hrefs:
                for dwell in (1.0, 1.3):
                    p.evaluate(SCROLL_ONE_JS, href)
                    time.sleep(dwell)
                    res = p.evaluate(CLICK_ONE_JS, href) or {}
                    if res.get("gone") or res.get("nobtn"):
                        break
                    if res.get("done"):
                        exp_ok += 1
                        break
            exp_remaining = len(p.evaluate(LIST_EXPAND_JS, None) or [])
            if not exp_remaining:
                break
        exp = {"ok": exp_ok, "remaining": exp_remaining}
        time.sleep(1.0)
        res = p.evaluate(FEED_JS, None)
        anchor_ms, raw = res["now"], res["rows"]
        logger.info("载入 %s 条，流内展开 %s 条，滚动 %s 步，书签已翻到: %s",
                    len(raw), exp.get("ok", 0), steps, reached if since_dt else "n/a")
    finally:
        b.stop()

    # 去重 + 窗口过滤（书签 30 分钟重叠容差）+ 博主过滤
    seen, rows = set(), []
    dropped_untracked = 0
    for r in raw:
        if r["href"] in seen:
            continue
        seen.add(r["href"])
        if since_dt:
            dt, _ = derive_time(r["timeLabel"], anchor_ms)
            if dt and dt < since_dt - timedelta(minutes=30):
                continue
        if tracked is not None:
            uid = (re.match(r"^/(\d+)/", r["href"]) or [None, None])[1]
            if uid and uid not in tracked:
                dropped_untracked += 1
                continue
        rows.append(r)
    rows = rows[:limit]
    window_incomplete = since_dt is not None and not reached
    if not rows:
        if window_incomplete:
            print("流书签未翻到且窗口内无帖：不可当无新帖，须走 user 兜底")
            return "__NO_WINDOW_REACHED__"
        print("窗口内无新帖（流内未见到书签之后的帖子）")
        return "__NO_NEW_POSTS__"
    if window_incomplete:
        print("⚠ 流书签未翻到（滚动地板）：已采部分仍产出（断点不写），窗口缺口须走 user 兜底")

    # 例外帖：自身专栏 / 展开失败 → 详情页
    todo = [r for r in rows if r["column"] or r["trunc"]]
    n_col = sum(1 for r in todo if r["column"])
    url_visited, url_ok = 0, 0
    logger.info("例外帖 %s 条（专栏 %s / 展开失败 %s）→ 详情页补全（预算 %s）",
                len(todo), n_col, len(todo) - n_col, URL_BUDGET)
    if todo:
        b = ego_browser.EgoBridge()
        b.start()
        p2 = b.new_page()
        fails = 0
        try:
            for r in todo:
                if url_visited >= URL_BUDGET:
                    logger.error("例外 URL 达主动预算 %s，余下 %s 条按「摘要」处理",
                                 URL_BUDGET, len(todo) - todo.index(r))
                    break
                url_visited += 1
                try:
                    p2.goto("https://xueqiu.com" + r["href"])
                    time.sleep(1.2 + random.random() * 0.5)
                    d = p2.evaluate(DETAIL_JS, None)
                    if d.get("blocked"):
                        raise RuntimeError("WAF 滑块/安全验证")
                    if not d.get("content"):
                        time.sleep(2.5)
                        d = p2.evaluate(DETAIL_JS, None)
                    if not d.get("content"):
                        raise RuntimeError("详情页无正文（纯图片帖或渲染缺）")
                    r["detail"] = d
                    url_ok += 1
                    fails = 0
                except Exception as e:
                    r["detail_err"] = str(e)[:120]
                    fails += 1
                    logger.warning("例外帖失败 %s %s", r["href"], r["detail_err"])
                    if fails >= BREAK_N:
                        logger.error("连续 %s 次失败熔断，余下按「摘要」处理", fails)
                        break
                time.sleep(random.uniform(*PACE))
        finally:
            p2.close()
            b.stop()

    # 组装帖子集（多博主格式，规范见本 skill references/output-format.md）
    now_dt = datetime.fromtimestamp(anchor_ms / 1000)
    out_rows, n_full = [], 0
    for i, r in enumerate(rows, 1):
        d = r.get("detail")
        own = clean_feed(r["text"])
        body, complete, reason = own, "全文", ""
        if r["column"]:
            if d and d.get("content"):
                body = tidy_article(d["content"])
            else:
                complete, reason = "摘要", "专栏未取到全文"
        elif r["trunc"]:
            if d and d.get("content"):
                body = tidy_article(d["content"])
            else:
                complete, reason = "摘要", "流内展开失败"
        # 时间：详情页权威 > 流内推算
        tm, tmark, edited = "", "", False
        if d and d.get("timeRaw"):
            m = re.search(r"(\d{4})-(\d{2})-(\d{2})\s+(\d{2}:\d{2})", d["timeRaw"])
            if m:
                tm = f"{m.group(1)}年{m.group(2)}月{m.group(3)}日 {m.group(4)}"
                edited = d.get("edit") == "修改于"
        if not tm:
            dt, ed = derive_time(r["timeLabel"], anchor_ms)
            if dt:
                tm = f"{dt.year}年{dt.month:02d}月{dt.day:02d}日 {dt.strftime('%H:%M')}"
                tmark = "（修改于·推算）" if ed else "（流内推算）"
        if not tm:
            complete, reason = "摘要", "时间不可解析"
            tm = "1970年01月01日 00:00"
        # ── 引用卡 → 「回复内容」块：专栏/原帖两种样式（唯一拼接路径）。
        # 详情页来源的正文已内联引用（d 存在）与回复帖（//@ 链承载）不追加，防重复。
        q = r.get("quoted")
        if q and q.get("url") and not d and not own.startswith("回复@"):
            qauthor = q.get("author") or "被引作者"
            c = q.get("counts") or [0, 0, 0]
            meta_parts = ([q.get("time")] if q.get("time") else []) + \
                [f"转发 {c[0]}", f"讨论 {c[1]}", f"赞 {c[2]}"]
            meta = " · ".join(p for p in meta_parts if p)
            block = None
            if q.get("isColumn") and q.get("title"):
                head = f"> 回复内容·专栏：{qauthor}《{q['title']}》"
                if meta:
                    head += f"（{meta}）"
                block = [head]
                lead = clean_quote(q.get("lead") or "")
                lead = re.sub(r"\s*\n\s*", "", lead.replace(q["title"], "", 1)).strip()
                if lead:
                    block.append(f"> 文章导语（截断）：{lead}")
                block.append(f"> 被引原文：https://xueqiu.com{q['url']}")
            elif not q.get("isColumn"):
                qtext = re.sub(r"\s*\n\s*", "", clean_quote(q.get("lead") or "")).strip()
                head = f"> 回复内容·原帖：{qauthor}：{qtext}" if qtext else f"> 回复内容·原帖：{qauthor}"
                block = [head, f"> 被引原文：https://xueqiu.com{q['url']}"]
            if block:
                body = (body + "\n\n" + "\n".join(block)).strip()
        # 空正文＝纯图片帖（控制字剥离后无文本）→ 按「摘要」处理（import 按设计不入库，
        # 提炼本就会丢零信息量帖；不标全文防误导，2026-09-26 实测 #242）
        if not body.strip():
            complete, reason = "摘要", "纯图片帖（无文本正文）"
        title = first_sentence(body)
        if r["column"] and body:
            m = re.match(r"^([^\n，。]{4,60})", body)
            title = m.group(1) if m else title
        is_reply = ("回复@" in body or "//@" in body) or bool(q and q.get("url"))
        form = "专栏" if r["column"] else ("回复" if is_reply else ("短文" if len(body) < 200 else "长文"))
        if complete == "全文":
            n_full += 1
        c = r["counts"]
        author = r.get("author") or "Unknown"
        pub = (f"> 发布：{tm}{tmark}{'（修改于）' if edited else ''} | 形态：{form} | 作者：{author}"
               f" | 转发 {c[0]} | 回复 {c[1]} | 点赞 {c[2]}"
               f" | {complete}{' | ' + reason if reason else ''}"
               f" | [原文](https://xueqiu.com{r['href']})")
        # 配图：只取本帖正文块里的图（引用卡/被回复块的图归被引帖，见 FEED_JS collectImgs 的 ⚠ 段）。
        # 过一遍归一化——去尺寸档、滤表情/头像、按出现顺序去重。
        # 只写进元数据行，正文一律不留图片引用：进正文会污染 content_hash=md5(body)，
        # 三处同键比对（import/check/MCP upsert）会把整批存量帖判「不一致」。
        imgs = extract_images(r.get("imgs"))
        out_rows.append((i, title, body, pub, author, imgs))

    status = "待提炼" if n_full == len(out_rows) else "待提炼-含摘要"
    n_author = len({x[4] for x in out_rows})
    tab_desc = {"follow": "关注流", "hot": "热门流"}.get(tab, tab)
    lines = [
        "---",
        f'title: "雪球帖子采集：{tab_desc}（多博主） {now_dt.year}年{now_dt.month}月{now_dt.day}日"',
        'source: "https://xueqiu.com/"',
        f'author: "{tab_desc} {n_author} 位博主"',
        f'date: "{now_dt.year}年{now_dt.month}月{now_dt.day}日"',
        f'recorded: "{now_dt.year}年{now_dt.month}月{now_dt.day}日"',
        'type: "帖子集"',
        f'status: "{status}"',
        "tags: []",
        "---",
        "",
    ]
    for i, title, body, pub, author, imgs in out_rows:
        lines += [f"## {i}. {title}", "", body, "", pub]
        if imgs:
            # 与 report.py（user 模式）同一行式：`> 图：<url> @p<段落号> | …`，入库侧只解析一种格式
            lines.append("> 图：" + " | ".join(
                f"{im['url']} @p{im.get('para', -1)}" for im in imgs))
        lines += ["", "---", ""]
    os.makedirs(output_dir, exist_ok=True)
    outfile = outfile or f"雪球采集-{tab_desc}-{now_dt:%Y年%m月%d日}.md"
    path = os.path.join(output_dir, outfile)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    print(f"帖子集已生成: {path}")
    print(f"统计: {len(out_rows)} 帖（全文 {n_full} / 摘要 {len(out_rows) - n_full}），博主 {n_author} 位"
          + (f"，非看板博主过滤 {dropped_untracked} 条" if dropped_untracked else ""))
    print(f"[URL] 例外帖详情页访问 {url_visited} 次（成功 {url_ok}）：专栏 {n_col} / 展开失败 {len(todo) - n_col}"
          + ("，熔断余下按摘要" if url_visited < len(todo) else ""))
    print(f"[展开] 遇到需展开 {exp.get('ok', 0) + max(exp.get('remaining', 0), 0)} 条"
          f"（流内展开成功 {exp.get('ok', 0)}，未成功转详情页 {max(exp.get('remaining', 0), 0)}）")
    # 成功产出 → 写流断点书签（本轮起点）；窗口未翻到时**禁写**（防漏采固化）
    if not window_incomplete:
        save_bookmark(tab, datetime.fromtimestamp(anchor_ms / 1000).strftime("%Y-%m-%dT%H:%M:%S"))
    return path
