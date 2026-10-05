#!/usr/bin/env python3
"""真点击探针（2026-10-05 去 goto 化 P0）：**只验证不采集**，不落任何帖子产物。

要回答的三个问题（结论决定 P2/P3 的选型，见 out/qoder/de-goto-plan-v2.md §三 P0）：
  V1'  真鼠标点击（CDP 输入管线，isTrusted=true）能不能正常完成雪球站内导航；
  V2'  翻到第 N 页后「进详情 → back」，回来停在第几页（判据：首张卡的永久链是否还是同一批）；
  V3   真点击/真滚轮的**净操作耗时**（汇总里把故意等的那部分扣掉，剩下的才是输入管线本身的价格）。

用法：
    python3 scripts/xq_click_probe.py [--name 行中衡] [--uid 1553799558] [--pages 2]

前置：ego lite 开着且已登录雪球。全场**只有开头 1 次 goto**（进雪球首页），
之后一律 click / wheel / back。读状态用 evaluate——读不是交互，不产生「假动作」。
"""
import argparse
import json
import os
import re
import sys
import time

SKILL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SKILL)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ego_browser import BridgeError, EgoBridge  # noqa: E402

HOME = 'https://www.xueqiu.com/'
FRIENDS_A = "a[href='/center/#/friends']"

STEPS = []
SETTLE_MS = [0]          # 故意等掉的（页面加载/呼吸位），汇总时从总耗时里扣掉


def settle(ms):
    time.sleep(ms / 1000.0)
    SETTLE_MS[0] += ms


def step(name, fn):
    """跑一步，计时 + 抓异常（一步失败不中断整轮，探针要的是全貌）。"""
    t0 = time.time()
    s0 = SETTLE_MS[0]
    try:
        detail = fn()
        ms = int((time.time() - t0) * 1000)
        STEPS.append((name, ms, ms - (SETTLE_MS[0] - s0), 'ok', detail))
        print(f'[{len(STEPS):02d}] {ms:6d}ms  {name}  {detail}', flush=True)
    except Exception as e:
        ms = int((time.time() - t0) * 1000)
        STEPS.append((name, ms, ms - (SETTLE_MS[0] - s0), 'FAIL', str(e)[:120]))
        print(f'[{len(STEPS):02d}] {ms:6d}ms  {name}  ✗ {type(e).__name__}: {str(e)[:160]}', flush=True)


def uid_of(url):
    m = re.search(r'xueqiu\.com/u/(\d+)', url or '') or re.search(r'xueqiu\.com/(\d+)/\d+', url or '')
    return m.group(1) if m else None


# ── 只读探针（evaluate 读 DOM 状态）──
JS_SNAP = r"""() => {
  const own = c => { const a = [...c.querySelectorAll('a[href]')]
      .find(x => /^\/\d+\/\d+$/.test(x.getAttribute('href') || '')); return a ? a.getAttribute('href') : null; };
  const cards = [...document.querySelectorAll('.timeline__item')];
  return { url: location.href, n: cards.length,
           first: cards.length ? own(cards[0]) : null,
           last: cards.length ? own(cards[cards.length - 1]) : null,
           hasNext: !!document.querySelector('a.pagination__next'),
           expandable: [...document.querySelectorAll('a.timeline__expand__control')]
                        .filter(a => a.offsetWidth > 0).length };
}"""

JS_FOLLOW_ROWS = r"""() => {
  const out = [];
  document.querySelectorAll('.profiles__user').forEach((c, i) => {
    const a = c.querySelector('a.avatar') || c.querySelector('a[href]');
    out.push({ i: i, name: (c.innerText || '').split('\n')[0].trim(),
               href: a ? a.getAttribute('href') : null });
  });
  return out;
}"""

JS_CARD_TEXT = r"""(arg) => {
  const c = [...document.querySelectorAll('.timeline__item')][arg.i];
  if (!c) return null;
  const a = [...c.querySelectorAll('a[href]')].find(x => /^\/\d+\/\d+$/.test(x.getAttribute('href') || ''));
  return { len: (c.innerText || '').length, href: a ? a.getAttribute('href') : null };
}"""


JS_FIRST_EXPAND = r"""() => {
  // 第一张「有可见展开控件」的卡片是第几张——展开必须点它自己那张，
  // 否则点的是别的卡，正文长度当然不变（上一轮探针就是这么误判的）。
  const cards = [...document.querySelectorAll('.timeline__item')];
  for (let i = 0; i < cards.length; i++) {
    const a = [...cards[i].querySelectorAll('a.timeline__expand__control')].find(x => x.offsetWidth || x.offsetHeight);
    if (a) return { i: i, len: (cards[i].innerText || '').length };
  }
  return null;
}"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--name', default='行中衡', help='关注列表里按显示名定位（列表 href 可能是自定义域名，认不了 uid）')
    ap.add_argument('--uid', default='', help='落地后校验用；留空则只报告不校验')
    ap.add_argument('--pages', type=int, default=2, help='V2prime：进详情前先翻几页（2 = 第 3 页）')
    ap.add_argument('--trail', type=int, default=3, help='鼠标轨迹分段数（1=最省，3=更像真人）；实测成本几乎全在 mouseMoved 这一趟')
    args = ap.parse_args()

    bridge = EgoBridge()
    bridge.start()
    page = bridge.main_page
    verdict = {}

    try:
        print(f'== 探针开始 {time.strftime("%H:%M:%S")}  当前页: {page.url}', flush=True)
        CLICK_MS = []

        def ck(**kw):
            """真点击 + 记下桥回报的分段耗时（V3 要的就是这个）。"""
            kw.setdefault('trail', args.trail)
            r = page.click(**kw)
            if isinstance(r, dict) and r.get('ms'):
                CLICK_MS.append(r['ms'])
            return r
        verdict['clicks'] = CLICK_MS

        # S1 全场唯一一次 goto
        def s1():
            page.goto(HOME, timeout=25000)
            settle(2200)
            return page.url
        step('goto 雪球首页（全场唯一一次）', s1)

        # S2 真点击「关注 N」→ 关注列表
        def s2():
            ck(selector=FRIENDS_A, strip_target=True)
            settle(2600)
            return f'→ {page.url}'
        step('click 关注列表入口', s2)

        # S3 列表里按名字找博主 → 真点击进主页
        #     必须走共用的 friends_rows（含 AJAX 轮询）与按 href 点击——
        #     10-05 ZCode 撞坑：探针这里自己单次枚举 + 按外壳 nth 点，比采集器先挂
        def s3():
            from xq_dialog_collect import friends_rows
            rows = friends_rows(page, want_name=args.name)
            hit = [r for r in rows if r.get('name') == args.name]
            if not hit:
                raise RuntimeError(f'关注列表 {len(rows)} 行里没有「{args.name}」；样本={[r["name"] for r in rows[:8]]}')
            href = hit[0].get('href') or ''
            numeric = bool(re.match(r'^/(\d+)$', href))
            verdict['follow_href'] = (f'{href} → ' + ('数字 uid，现有 JS_FOLLOW_LIST 能收'
                                     if numeric else '自定义域名，**现有 JS_FOLLOW_LIST 会静默漏掉这一行**'))
            # within 必给：nth 是「第几行」，不写 within 就变成全页第 n 个 a.avatar（实测点错人）
            ck(selector='a.avatar', within='.profiles__user', nth=hit[0]['i'], strip_target=True)
            settle(3000)
            u = page.url
            landed = uid_of(u)
            if args.uid and landed != args.uid:
                raise RuntimeError(f'点错人：落地 uid={landed} != 目标 {args.uid}')
            return f'→ {u}  uid={landed}  href={href}'
        step(f'click 博主「{args.name}」进主页', s3)

        target_uid = uid_of(page.url) or (args.uid or None)
        own_a = f'a[href^="/{target_uid}/"]'

        # S4 展开：真点击 a.timeline__expand__control，量正文长度变化
        def s4():
            tgt = page.evaluate(JS_FIRST_EXPAND, None)
            if not tgt:
                raise RuntimeError('这一屏没有可见的展开控件（都是未截断的短帖），换博主或先滚一屏')
            ck(selector='a.timeline__expand__control', within='.timeline__item',
               nth=tgt['i'], strip_target=True)
            settle(900)
            after = page.evaluate(JS_CARD_TEXT, {'i': tgt['i']})
            verdict['expand'] = (f"第 {tgt['i']} 张卡正文 {tgt['len']} → {after and after['len']} 字"
                                 f"（{'展开生效' if after and after['len'] > tgt['len'] else '没变，展开没生效'}）")
            return verdict['expand']
        step('click 展开（真鼠标）', s4)

        # S5 进详情：真点击卡片那行时间的永久链
        def s5():
            snap0 = page.evaluate(JS_SNAP, None)
            ck(selector=own_a, nth=0, strip_target=True)
            settle(2600)
            u = page.url
            verdict['detail'] = u
            return f'→ {u}  (卡片数 {snap0["n"]}, 首卡 {snap0["first"]})'
        step('click 时间链接进详情', s5)

        # S6 back 回时间线：量历史栈与页位
        def s6():
            r = page.back()
            settle(1200)
            s = page.evaluate(JS_SNAP, None)
            verdict['back1'] = f"moved={r['moved']} 栈 {r['index']}/{r['depth']} → {s['url']}"
            return f"{verdict['back1']}  卡片数 {s['n']}, 展开控件 {s['expandable']}"
        step('back 回时间线', s6)

        # S7 真滚轮 + 真点击翻页到第 N 页
        def s7():
            for _ in range(args.pages):
                page.wheel(dy=2400, wait_ms=500)
                settle(200)
                ck(selector='a.pagination__next', nth=0, strip_target=True)
                settle(2600)
            page.wheel(dy=-3000, wait_ms=400)
            s = page.evaluate(JS_SNAP, None)
            verdict['paged'] = f'第 {args.pages + 1} 页：卡片数 {s["n"]}, 首卡 {s["first"]}, 末卡 {s["last"]}'
            return verdict['paged']
        step(f'wheel + click 下一页 ×{args.pages}', s7)

        # S8 V2' 主问题：第 N 页进详情 → back，看回来还在不在第 N 页
        def s8():
            before = page.evaluate(JS_SNAP, None)
            ck(selector=own_a, nth=0, strip_target=True)
            settle(2600)
            detail = page.url
            r = page.back()
            settle(1500)
            after = page.evaluate(JS_SNAP, None)
            same = after['first'] == before['first']
            verdict['v2'] = ('保留页位 ⇒ 现场点方案的重扫成本≈0' if same else
                             f"掉页：首卡 {before['first']} → {after['first']}，卡片数 {before['n']}→{after['n']}"
                             f' ⇒ 每进一次详情要重扫 {args.pages} 页')
            return f"栈 {r['index']}/{r['depth']} 详情 …{detail[-20:]} 回来卡片数={after['n']} ⇒ {verdict['v2']}"
        step('V2prime 第 N 页进详情后 back 落点', s8)

        # S8b 连续多次「进详情 → back」的**掉页率**。必须自己先翻回深页：
        #      在第 1 页测是假阳性——掉回第 1 页和保留第 1 页看起来一模一样（10-05 第一版就栽在这）。
        def s8b():
            for _ in range(args.pages):
                page.wheel(dy=2400, wait_ms=400)
                settle(150)
                page.click(selector='a.pagination__next', nth=0, strip_target=True)
                settle(2400)
            base = page.evaluate(JS_SNAP, None)
            lines = []
            for k in range(5):
                snap_before = page.evaluate(JS_SNAP, None)
                ck(selector=own_a, nth=k % 3, strip_target=True)
                settle(2200)
                on_detail = f'/u/{target_uid}' not in page.url
                page.back(settle_ms=1500)
                after = page.evaluate(JS_SNAP, None)
                kept = after['first'] == snap_before['first'] and after['n'] == snap_before['n']
                same_first = '是' if after['first'] == snap_before['first'] else '否'
                lines.append(f'#{k + 1} 进详情={on_detail} 卡片数 {snap_before["n"]}→{after["n"]} '
                             f'首卡同={same_first} ⇒ {"保页位" if kept else "掉页"}')
                if not kept:      # 掉页后重新翻回深页，下一轮才是同条件对比
                    for _ in range(args.pages):
                        page.wheel(dy=2400, wait_ms=400)
                        settle(150)
                        page.click(selector='a.pagination__next', nth=0, strip_target=True)
                        settle(2400)
            verdict['repeat_back'] = lines
            verdict['repeat_back_kept'] = sum(1 for l in lines if '保页位' in l)
            verdict['repeat_back_total'] = len(lines)
            return f'基准：第 {args.pages + 1} 页 卡片数 {base["n"]} 首卡 {base["first"]} | ' \
                   f'保页位 {verdict["repeat_back_kept"]}/{len(lines)}\n      ' + '\n      '.join(lines)
        step('S8b 深页连续 5 次进详情+back 的掉页率', s8b)

        # S9 引用卡 → 原帖（G4 的锚点）
        def s9():
            page.wheel(dy=1200, wait_ms=400)
            ck(selector="a[href$='#comment']", nth=0, strip_target=True)
            settle(2600)
            u = page.url
            verdict['root_link'] = u
            r = page.back()
            settle(1200)
            return f'→ {u}  back 栈 {r["index"]}/{r["depth"]}'
        step("click 引用卡「· 讨论 N」进原帖 + back", s9)

    except BridgeError as e:
        print(f'⚠ 桥中断：{e}', flush=True)
    finally:
        total = sum(s[1] for s in STEPS)
        net = sum(s[2] for s in STEPS)
        print('\n===== 汇总 =====', flush=True)
        for name, ms, nms, st, detail in STEPS:
            print(f'  {st:4s} 总{ms:6d}ms 净{nms:6d}ms  {name}', flush=True)
        print(f'  {len(STEPS)} 步：总 {total}ms，扣掉故意等的 {SETTLE_MS[0]}ms 后**净操作 {net}ms**', flush=True)
        if CLICK_MS:
            loc = [c['locate'] for c in CLICK_MS]
            mv = [c['movedMs'] for c in CLICK_MS]
            pr = [c['pressMs'] for c in CLICK_MS]
            print(f'  真点击 {len(CLICK_MS)} 次：定位 avg {sum(loc)//len(loc)}ms / max {max(loc)}ms，'
                  f'轨迹 avg {sum(mv)//len(mv)}ms，按下+抬起 avg {sum(pr)//len(pr)}ms，'
                  f'单次合计 avg {sum(loc + mv + pr)//len(loc)}ms', flush=True)
        print(json.dumps(verdict, ensure_ascii=False, indent=2), flush=True)
        print('\n（bridge.stop() 会 finish 任务空间——按仓库规矩「用完回收」）', flush=True)
        bridge.stop()


if __name__ == '__main__':
    main()
