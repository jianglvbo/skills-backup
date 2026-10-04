#!/usr/bin/env python3
"""全量批量采集：按 info_cutoff 增量逐博主跑 xueqiu-spyder，并输出**入库清单**。

为什么需要它：单博主采集已脚本化（xueqiu-spyder），但「所有博主」这批要
逐位解析 cutoff → 选页数 → 跑采集 → 判退出码 → 回写 cutoff，还要遵守
「每 10 位停 60 秒」的节流。这段编排以前是人工一条条敲，容易漏位、漏回写。

**职责边界**：本脚本只做采集 + 回写 cutoff + 产出「采集产物清单」，
**不直接入库**（入库走本 skill SKILL.md 第七步：import-post-history.js → 校验 → 清理）。

用法:
  python3 run_fetch_batch.py [--limit N] [--only 博主1,博主2] [--skip 博主1] [--pages-override N]

退出码: 0=全部完成（含"无新帖"）；1=有博主失败（清单里标 FAILED，可重跑补齐）
"""
import argparse
import datetime
import json
import os
import subprocess
import sys
import time
import urllib.request

API = os.environ.get("DASH_API", "https://www.jianglvbo.site:8699")  # 后端只在服务器（2026-10-02 本机不留服务）
# 工具层即本 skill 目录（2026-09-26 编排并入 xueqiu-spyder，本脚本就在 skill 内）；
# 保留按序探活兜底，防止 symlink 断链时静默用失效路径（2026-09-24：首项失效已踩过）
_SPYDER_CANDS = [
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "~/.zcode/skills/xueqiu-spyder",
    "~/Project/investment-dashboard/.agents/skills/xueqiu-spyder",
]
SPYDER_DIR = next((os.path.expanduser(c) for c in _SPYDER_CANDS if os.path.isdir(os.path.expanduser(c))), None)
if not SPYDER_DIR:
    sys.exit("❌ 未找到 xueqiu-spyder 工具层，已探: " + " / ".join(_SPYDER_CANDS)
             + "\n（路径已多次迁移，先用 ls 确认真实位置再改本脚本候选表）")
OUT_DIR = os.path.expanduser("~/.cache/xueqiu-spyder/out")
LIST_PATH = os.path.expanduser("~/.cache/xueqiu-spyder/batch_list.json")


def api(path):
    # 闸门（2026-09-27）：loopback 也要 Bearer mcpToken，凭据从仓库 config.json 读
    hdrs = {}
    try:
        with open(os.path.expanduser("~/Project/investment-dashboard/src/config.json")) as f:
            tok = json.load(f).get("mcpToken", "")
        if tok:
            hdrs["Authorization"] = "Bearer " + tok
    except Exception:
        pass
    req = urllib.request.Request(API + path, headers=hdrs)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def py_bin():
    cfg = os.path.expanduser("~/.config/xueqiu-spyder/python")
    if os.path.exists(cfg):
        with open(cfg) as f:
            p = f.read().strip()
        if p:
            return p
    return sys.executable or "python3"


def pages_for(hours):
    """post-fetch 硬约束：≤24h→3、≤7 天→5、>7 天→10"""
    if hours <= 24:
        return 3
    if hours <= 168:
        return 5
    return 10


def cutoff_iso(blogger):
    """把看板返回的 infoCutoff 转成 spyder 的 `--from`（**本地时间**字符串）。

    ⚠️ 时区语义（2026-09-16 审计实测，两轮才对）：
      · 库里 `info_cutoff_datetime` 列存的是**本地时间**（如 20:41）；
      · mysql2 读出来是 Date，再由看板 API 序列化成 **UTC ISO**（→ `12:41:00.000Z`）；
      · 所以从 API 拿到之后要 **按 UTC 解析、再转成本地**（+8h），才能还原库里的 20:41。
      · 直接截前 19 位当本地时间用会**少算 8 小时** → 每次都多采 8 小时（重复记录 + 多余请求）。
    实测对照：晚舟夕照 库里 20:41 / API 12:41Z / +8h=20:41 ✅
    """
    raw = (blogger.get("infoCutoff") or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.datetime.strptime(raw[:19], "%Y-%m-%dT%H:%M:%S")
        if raw.endswith("Z"):
            # 带 Z = UTC（看板 API 的序列化口径）→ 按本机时区偏移转本地
            dt = dt + (datetime.datetime.now().astimezone().utcoffset() or datetime.timedelta(0))
        return dt.strftime("%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return raw[:19].replace(" ", "T")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 位（0=全部）")
    ap.add_argument("--only", default="", help="只跑这些博主（逗号分隔）")
    ap.add_argument("--skip", default="", help="跳过这些博主（逗号分隔）")
    ap.add_argument("--pages-override", type=int, default=0, help="强制页数（默认按窗口算）")
    ap.add_argument("--since", default="", help="统一窗口起点 ISO（覆盖各博主自身 cutoff，用于补采/回溯）")
    args = ap.parse_args()

    data = api("/api/bloggers/live")
    rows = data.get("data", {}).get("bloggers", [])
    # 采集对象 = 有雪球ID 且**不是已销户**（2026-09-16 用户拍板：
    # 「雪球已销户」保留历史言论与留档，但不再加入后续言论采集；先例：山湖水）
    targets = [b for b in rows
               if (b.get("xueqiuId") or "").strip()
               and b.get("platform") != "雪球已销户"]
    only = {s.strip() for s in args.only.split(",") if s.strip()}
    skip = {s.strip() for s in args.skip.split(",") if s.strip()}
    if only:
        targets = [b for b in targets if b["name"] in only]
    if skip:
        targets = [b for b in targets if b["name"] not in skip]
    targets.sort(key=lambda b: b.get("infoCutoff") or "")
    if args.limit:
        targets = targets[: args.limit]

    os.makedirs(OUT_DIR, exist_ok=True)
    today = datetime.date.today().strftime("%Y年%m月%d日")
    py = py_bin()
    now = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    print(f"批量采集：{len(targets)} 位博主 ｜ 工具层 {py}", flush=True)
    manifest = []
    failed = []
    for i, b in enumerate(targets, 1):
        name = b["name"]
        xq = str(b["xueqiuId"]).strip()
        cut = (args.since or "").strip() or cutoff_iso(b)
        if not cut:
            cut = (datetime.date.today() - datetime.timedelta(days=182)).strftime(
                "%Y-%m-%dT17:50:00")
        try:
            hrs = (datetime.datetime.now()
                   - datetime.datetime.strptime(cut, "%Y-%m-%dT%H:%M:%S")).total_seconds() / 3600
        except ValueError:
            hrs = 999
        pages = args.pages_override or pages_for(hrs)

        outfile = f"雪球采集-{name}-{today}.md"
        path = os.path.join(OUT_DIR, outfile)
        cmd = [py, "main.py", "user", xq, "--from", cut, "--to", now,
               "--max-pages", str(pages), "--outfile", outfile, "--output", OUT_DIR]
        t0 = time.time()
        print(f"[{i}/{len(targets)}] {name} 窗口 {cut} → {now}（{hrs:.0f}h，{pages} 页）", flush=True)
        # 浏览器一律 ego lite（2026-09-16 用户拍板，Chrome 路径已从工具层删除）。
        env = dict(os.environ)
        env["XUEQIU_TRANSPORT"] = "ego"
        # 不抢焦点（用户口径：「不要让 ego lite 一直跳到我前面」，滑块时才激活）。
        env["XUEQIU_EGO_WAKE"] = "0"
        r = subprocess.run(cmd, cwd=SPYDER_DIR, capture_output=True, text=True,
                           timeout=1800, env=env)
        dt = time.time() - t0
        code = r.returncode
        tail = (r.stderr or r.stdout or "").strip().splitlines()
        note = tail[-1][:80] if tail else ""
        rec = {"name": name, "xq": xq, "cutoff": cut, "pages": pages,
               "exit": code, "file": path if code == 0 and os.path.exists(path) else None,
               "seconds": round(dt, 1)}
        if code == 0:
            manifest.append(rec)
            print(f"    ✅ 采集完成 {dt:.0f}s → {os.path.basename(path)}", flush=True)
        elif code == 2:
            rec["file"] = None
            manifest.append(rec)
            print(f"    ⚪ 窗口内无新帖 {dt:.0f}s（cutoff 可推进）", flush=True)
        else:
            failed.append(rec)
            print(f"    ❌ 退出码 {code}（{dt:.0f}s）{note}", flush=True)

        if i % 10 == 0 and i < len(targets):
            print("    ⏸ 节流：停 60s（每 10 位）", flush=True)
            time.sleep(60)

    with open(LIST_PATH, "w", encoding="utf-8") as f:
        json.dump({"generated": now, "manifest": manifest, "failed": failed}, f,
                  ensure_ascii=False, indent=1)
    ok_new = sum(1 for m in manifest if m["file"])
    print(f"\n完成：{len(manifest)} 位（其中 {ok_new} 位有新帖）｜失败 {len(failed)} 位",
          flush=True)
    if failed:
        print("失败清单: " + ", ".join(f"{x['name']}({x['exit']})" for x in failed), flush=True)
    print(f"清单已写: {LIST_PATH}", flush=True)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
