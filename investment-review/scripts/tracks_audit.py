#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
投资知识库 · 言论追踪专项审计（C10 自动化执行器）
===================================================
本脚本是 `investment-review` skill 内容审查「C10 言论追踪审计」的执行器。

vault 侧检查（默认，无需凭据；**仅面向尚未删除的历史画像 md**）：
  1. section 缺失    → 历史画像无 `## 言论追踪` 段
  2. section 空表    → 有段但无任何数据行
  3. 标的占位        → 标的列出现 — / 持仓 / 暂无 / 空
  4. 缺原文链接      → 历史 md 里的言论行无链接（规则 #35）
  5. 表格列异常      → 观点/具象化表 <6 列、信号表 <5 列
  ⚠️ 画像 md 于 2026-09-12 退役，本侧**不是数据缺口**（见 vault_scan docstring）。

MySQL 侧检查（可选，--mysql + 环境变量 DB_PASS；**权威口径**）：
  6. 言论数据质量   → `statement` 视图：空正文 / 缺原文链接 / 缺 `post_form` /
                      `content_type`、`stance_code` 码值合法性
  7. 关联与留档     → P1 三类（trade/predict/research）缺实体关联、
                      `post_history_id` 回指覆盖率（按 180 天窗口判）
  8. 复核建议积压   → `statement_review_sub` 未处理条数

用法:
  python3 scripts/tracks_audit.py --vault <vault路径>            # 仅 vault 侧
  DB_PASS=xxx python3 scripts/tracks_audit.py --vault <路径> --mysql   # vault + MySQL
设计原则：
  - 只报告不修改：仅输出 JSON，绝不写盘。
  - 不硬编码凭据：MySQL 凭据只从环境变量 DB_PASS 读取（Ai 仓库公共化约定）。
"""
import os, re, sys, json, glob
from pathlib import Path
from collections import Counter

SECTIONS_REQUIRED = ["擅长与局限", "言论追踪", "个股买卖", "预测"]
PLACEHOLDER_TARGETS = {"", "—", "-", "/", "暂无", "持仓", "未点名", "无"}


def vault_scan(vault_root: str) -> dict:
    """vault 侧扫描：**仅针对未清理的历史画像 md**（2026-09-12 起画像 md 已废弃、言论只落 MySQL）。

    因此本函数的结果**不是数据缺口**：vault 里没有「## 言论追踪」= 正常（md 已退役），
    MySQL 侧（mysql_scan）才是权威。保留本扫描只为兼容尚未删除的历史 md 文件。
    """
    findings = []
    blogger_dir = Path(vault_root) / "博主"
    files = sorted(glob.glob(str(blogger_dir / "*" / "*.md")))
    stats = Counter()
    track_total = 0
    for f in files:
        blogger = Path(f).parent.name
        txt = Path(f).read_text(encoding="utf-8")
        # 1) section 缺失/重复标题兼容（2026-09-07：镜像 bug 导致 43 画像 `## 言论追踪`
        #    标题重复——锚点 begin 后又写了一次标题。取「含数据行最多的区段」，
        #    对无重复（修复后）与有重复（现状）均正确）
        segs = [m.group(1) for m in re.finditer(r'^## 言论追踪[ \t]*\n(.*?)(?=\n## |\Z)', txt, re.M | re.S)]
        if not segs:
            findings.append({"file": str(f), "blogger": blogger, "type": "section_missing",
                             "detail": "博主画像缺少「## 言论追踪」section"})
            stats["section_missing"] += 1
            continue
        def _row_count(seg):
            n = 0
            for line in seg.splitlines():
                line = line.strip()
                if not line.startswith("|"): continue
                cells = [c.strip() for c in line.strip("|").split("|")]
                if cells and re.match(r'^20\d{2}-\d{2}-\d{2}$', cells[0]): n += 1
            return n
        sec = max(segs, key=_row_count)
        rows = []
        header_idx = None
        for line in sec.splitlines():
            line = line.strip()
            if not line.startswith("|"): continue
            cells = [c.strip() for c in line.strip("|").split("|")]
            if not cells: continue
            # 表头定位：含「标的」的列即标的列（新 9 列在 idx3，旧 6 列在 idx1）
            if header_idx is None and any("标的" in c for c in cells):
                for i, c in enumerate(cells):
                    if "标的" in c:
                        header_idx = i
                        break
                continue
            if not re.match(r'^20\d{2}-\d{2}-\d{2}$', cells[0]): continue
            rows.append(cells)
        track_total += len(rows)
        # 2) 空表（无任何数据行）
        if not rows:
            findings.append({"file": str(f), "blogger": blogger, "type": "section_empty",
                             "detail": "言论追踪 section 无数据行"})
            stats["section_empty"] += 1
            continue
        # 3/4/5) 行级检查
        for cells in rows:
            ti = header_idx if header_idx is not None else (3 if len(cells) >= 9 else 1)
            target = cells[ti] if len(cells) > ti else ""
            ncols = len(cells)
            has_link = any("http" in c or "](http" in c for c in cells)
            # 3) 标的占位
            if target in PLACEHOLDER_TARGETS:
                findings.append({"file": str(f), "blogger": blogger, "type": "target_placeholder",
                                 "date": cells[0], "detail": f"标的为占位符: {target!r}"})
                stats["target_placeholder"] += 1
            # 4) 缺原文链接（外部来源行）——按列数判断：6 列表的 link 列（末列）为空
            if ncols >= 6 and not has_link:
                findings.append({"file": str(f), "blogger": blogger, "type": "missing_link",
                                 "date": cells[0], "target": target,
                                 "detail": "外部言论行缺原文链接（规则 #35）"})
                stats["missing_link"] += 1
            # 5) 列数异常
            if ncols < 4:
                findings.append({"file": str(f), "blogger": blogger, "type": "table_cols_abnormal",
                                 "date": cells[0], "detail": f"表格列数异常: {ncols} 列"})
                stats["table_cols_abnormal"] += 1
        stats["blogger_with_rows"] += 1
    return {"vault_root": str(vault_root), "blogger_files": len(files),
            "stats": dict(stats), "track_rows_total": track_total, "findings": findings,
            "note": "画像 md 已废弃（2026-09-12）——本侧结果仅代表历史 md 现状，不构成数据缺口；权威在 MySQL statement 视图"}


def mysql_scan() -> dict:
    """只读扫描言论库数据质量（2026-09-12 重写：对齐当日 schema）

    旧实现查的 blogger_statements / statement_reviews / prediction_tracks **三张表都已不存在**
    （言论收敛为 statement 视图 + 六张 statement_* 物理表；复核建议在 statement_review_sub；
    预测独立表与跟踪表均已退役）→ 整段审计曾经静默失效。
    """
    import pymysql
    conn = pymysql.connect(host="106.55.14.116", port=3306, user="jianglb",
                           password=os.environ.get("DB_PASS", ""), database="investment-dashboard",
                           connect_timeout=8)
    cur = conn.cursor()
    out = {}
    # ① 言论总量与内容完整性（权威＝statement 视图，UNION 六张类型表；2026-09-13 起表名单数）
    cur.execute("SELECT COUNT(*) FROM statement")
    out["statements_total"] = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM statement WHERE view_text IS NULL OR TRIM(view_text)=''")
    raw_view_empty = cur.fetchone()[0]
    # trade-only 行（view 空、内容在 trade_note）属合法形态（2026-09-21 审查确认），不算缺正文
    cur.execute("""SELECT COUNT(*) FROM statement_trade
                   WHERE (view_text IS NULL OR TRIM(view_text)='')
                     AND trade_note IS NOT NULL AND TRIM(trade_note)<>''""")
    trade_note_only = cur.fetchone()[0]
    out["view_text_empty"] = raw_view_empty - trade_note_only
    out["trade_note_only_rows"] = trade_note_only
    cur.execute("SELECT COUNT(*) FROM statement WHERE source_url IS NULL OR TRIM(source_url)=''")
    out["source_url_empty"] = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM statement WHERE post_form IS NULL OR TRIM(post_form)=''")
    out["form_empty"] = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM statement WHERE reply_to IS NOT NULL AND TRIM(reply_to)<>''")
    out["reply_to_filled"] = cur.fetchone()[0]
    # ② 回指原文留档（2026-09-12 新增；留档只保 180 天〔2026-09-15 由 30 天放宽〕，取不到属正常）
    cur.execute("SELECT COUNT(*) FROM statement WHERE post_history_id IS NOT NULL")
    out["post_history_linked"] = cur.fetchone()[0]
    # ③ 分型分布与复核标记
    cur.execute("SELECT content_type, COUNT(*) FROM statement GROUP BY content_type")
    out["by_content_type"] = {r[0]: r[1] for r in cur.fetchall()}
    cur.execute("SELECT COUNT(*) FROM statement WHERE is_review_required=1")
    out["review_required"] = cur.fetchone()[0]
    # ④ 码值合法性（字典域：post_content_type / stance —— 旧写的 stmt_content_type 不存在）
    cur.execute("""SELECT s.content_type, COUNT(*) FROM statement s
                   LEFT JOIN dict d ON d.type='post_content_type' AND d.code=s.content_type
                   WHERE d.code IS NULL GROUP BY s.content_type""")
    out["bad_content_type"] = [list(r) for r in cur.fetchall()]
    cur.execute("""SELECT s.stance_code, COUNT(*) FROM statement s
                   LEFT JOIN dict d ON d.type='stance' AND d.code=s.stance_code
                   WHERE s.stance_code IS NOT NULL AND d.code IS NULL GROUP BY s.stance_code""")
    out["bad_stance"] = [list(r) for r in cur.fetchall()]
    # ⑤ 实体关联覆盖率（个股/行业主题靠关联表承载，target 列已删）
    # 注：按物理表分别统计（P1 三类：trade/predict/research）；市场级言论挂 statement_market_rel，
    # 只查个股+行业会把「美股/A股大盘」类误判为缺关联（2026-09-21 审查修正，含市场口径）
    miss = 0
    per_tbl = {}
    for tbl in ("statement_trade", "statement_predict", "statement_research"):
        cur.execute(f"""SELECT COUNT(*) FROM {tbl} t
                        WHERE NOT EXISTS (SELECT 1 FROM statement_stock_rel k WHERE k.statement_id=t.id)
                          AND NOT EXISTS (SELECT 1 FROM statement_industry_rel i WHERE i.statement_id=t.id)
                          AND NOT EXISTS (SELECT 1 FROM statement_market_rel m WHERE m.statement_id=t.id)""")
        n = cur.fetchone()[0]
        per_tbl[tbl] = n
        miss += n
    out["key_types_without_subject"] = miss
    out["key_types_without_subject_by_table"] = per_tbl
    # ⑤b 规则 #59A「信号必须挂在标的上」的全类型存量核查（2026-09-27 审查固化）：
    #     上面只数 P1 三类缺关联，#59A 管的是"给了 stance 就必须至少挂一条实体"，
    #     且服务端 `_validateSignal` 并不查关联表存在性 → 只能靠本审计兜底。
    cur.execute("""SELECT s.content_type, COUNT(*) FROM statement s
                   WHERE s.stance_code IS NOT NULL AND TRIM(s.stance_code)<>''
                     AND NOT EXISTS (SELECT 1 FROM statement_stock_rel k WHERE k.statement_id=s.id)
                     AND NOT EXISTS (SELECT 1 FROM statement_industry_rel i WHERE i.statement_id=s.id)
                     AND NOT EXISTS (SELECT 1 FROM statement_market_rel m WHERE m.statement_id=s.id)
                   GROUP BY s.content_type""")
    out["stance_without_subject"] = {r[0]: r[1] for r in cur.fetchall()}
    cur.execute("""SELECT COUNT(*) FROM statement WHERE (stance_code IS NULL OR TRIM(stance_code)='')
                     AND signal_text IS NOT NULL AND TRIM(signal_text)<>''""")
    out["signal_without_stance"] = cur.fetchone()[0]
    # ⑤c 心得具象化覆盖率（insight 应沉淀成框架条目才有 wiki_ref）
    cur.execute("""SELECT SUM(wiki_ref IS NULL OR TRIM(wiki_ref)=''), COUNT(*) FROM statement_insight""")
    n_no_wiki, n_ins = cur.fetchone()
    out["insight_without_wiki"] = int(n_no_wiki or 0)
    out["insight_total"] = int(n_ins or 0)
    # ⑥ 复核建议积压（审查首步数据源）
    cur.execute("SELECT status_code, COUNT(*) FROM statement_review_sub GROUP BY status_code")
    out["statement_review_sub"] = {r[0]: r[1] for r in cur.fetchall()}
    # ⑦ 退役表残留检查（存在才读数；均已退役为 _del 或删除，此处仅提示）
    for legacy in ("predictions", "prediction_tracks", "blogger_statements", "statement_reviews"):
        try:
            cur.execute(f"SELECT COUNT(*) FROM {legacy}")
            out.setdefault("legacy_tables", {})[legacy] = cur.fetchone()[0]
        except Exception:
            out.setdefault("legacy_tables_absent", []).append(legacy)
    conn.close()
    return out


def main():
    args = sys.argv[1:]
    vault = None
    do_mysql = False
    i = 0
    while i < len(args):
        if args[i] == "--vault" and i + 1 < len(args): vault = args[i + 1]; i += 2
        elif args[i] == "--mysql": do_mysql = True; i += 1
        else: i += 1
    if not vault:
        print(json.dumps({"ok": False, "error": "缺少 --vault 参数"}, ensure_ascii=False))
        sys.exit(1)
    report = vault_scan(vault)
    if do_mysql:
        report["mysql"] = mysql_scan()
        m, v = report["mysql"]["statements_total"], report["track_rows_total"]
        report["sync"] = {
            "vault_track_rows": v,
            "mysql_statements": m,
            "delta_mysql_minus_vault": m - v,
            "note": "delta 正值=库比 vault 多（增量采集/他源导入）；负值=vault 言论未入库（占位/无效行跳过）。注意：vault 统计口径为「言论追踪」section 数据行，与 MySQL statements 全量（含已删除标记行等）存在正常口径差"
        }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
