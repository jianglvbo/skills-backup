#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Obsidian 投资知识库 · 结构审查自动扫描器
========================================
本脚本是 investment-framework 审查环节（references/review.md「结构审查」）的自动化执行器。

覆盖维度（与 SKILL.md 结构审查一一对应）：
  - 归类正确性      → template_for() / unclassified
  - frontmatter 完整性 → REQUIRED / missing_fields（含 updateDate 必填）
  - 引号有效性      → check_quoting()（全局规则 #21）
  - wikilink 有效性 → resolve_link()（正文 + frontmatter `source` 字段）
  - 脚注格式        → legacy_footnote_heading（遗留 ## 脚注 标题，新格式脚注定义放文末无标题，规则 #20）
  - 标签匹配        → tag_issues（博主禁行业标签、标签禁 emoji）
  - 扩展检查        → 禁用 `## 来源` 段（规则 #23）、source 为 URL（应转 wikilink 数组）、空壳 junk 检测
  - 个股代码        → stock_code_missing（规则 #28：个股文件名须含 (代码)）
  - 博主层登记校验  → blogger_not_registered（规则 #12：博主文件夹名须在博主控制台登记）

设计原则
--------
- 只报告不修改：仅输出 JSON + 计数，绝不写盘（符合 skill「绝不直接修改文件」原则）。
- 规则镜像：下方 REQUIRED / EXPECTED_SECTIONS / SCOPE / template_for() 镜像自
  `investment-framework` 的 framework-rules.md 与 9 个模板文件。
  ⚠️ 若框架规则或模板调整（新增必填字段、改段落名），须同步更新本文件对应常量。
- 框架结构驱动：除 VAULT 路径外，不硬编码任何具体博主名 / 文件名 / URL。

用法
----
  python3 vault_review.py --vault "<vault路径>" [--out "<输出目录>"] [--incremental]
  # 不设 --vault 时：优先读环境变量 VAULT，否则退回 iCloud 默认路径
  # 不设 --out  时：结果输出到系统临时目录（/tmp），文件名 vault_review_result.json
  #              ——2026-08-14 优化：原默认输出当前工作目录，会污染 vault 仓库（曾误提交入库）
  # --incremental：增量模式，仅扫描 git 工作区变更的 .md 文件（操作收尾快速定向校验，
  #                2026-08-14 新增，配合「操作门」机制在问题产生当天拦截）

依赖：Python 3.8+，仅标准库（os/re/json/argparse）。
"""
import os, re, json, argparse, datetime, tempfile, sys

DEFAULT_VAULT = os.path.expanduser(
    "~/Library/Mobile Documents/iCloud~md~obsidian/Documents/投资知识库"
)
ap = argparse.ArgumentParser(description="Obsidian 投资知识库结构审查扫描器")
ap.add_argument("--vault", default=os.environ.get("VAULT", DEFAULT_VAULT),
                help="vault 根目录路径（默认环境变量 VAULT 或 iCloud 默认路径）")
ap.add_argument("--out", default=tempfile.gettempdir(),
                help="结果 JSON 输出目录（默认系统临时目录，避免污染 vault 仓库；需留存可传 --out 指定目录）")
ap.add_argument("--incremental", action="store_true",
                help="增量模式：仅扫描 git 工作区变更的 .md 文件（流水线操作收尾快速定向校验）")
_args = ap.parse_args()
VAULT = os.path.expanduser(_args.vault)
OUT = _args.out
if not os.path.isdir(VAULT):
    ap.error(f"vault 路径不存在: {VAULT}")
os.makedirs(OUT, exist_ok=True)

# 框架归属层（镜像 investment-framework 路径表；改框架时同步）
# 「我的」层由用户自管（规则 #15），Agent 不审查、不维护——扫描范围仅 博主/其他/宏观
SCOPE = ["博主", "其他", "宏观"]

# 博主控制台登记名 = 看板 MySQL bloggers 表（2026-09-07 起 vault 工作区/博主控制台.md 退役；
# 镜像 framework-rules #12）。经看板 API 读取；API 不可用时登记校验降级跳过（stderr 提示）
def load_blogger_console():
    names = set()
    try:
        import json as _json, urllib.request
        with urllib.request.urlopen("http://127.0.0.1:8698/api/bloggers/live", timeout=10) as r:
            data = _json.load(r)
        for b in data["data"]["bloggers"]:
            # 2026-09-14：**已删除的博主不算「已登记」**——他们走回收流程（制品会挪到「其他」层、
            # 言论逻辑删除），若不排除，规则 #12 的 other_author_registered 会一直把回收后的制品误报成「误挂」。
            if b.get("registered") and not b.get("deleted"):
                names.add(b["name"])
    except Exception as e:
        print(f"[vault_review] 看板博主控制台不可读（{e}），博主层登记校验跳过", file=sys.stderr)
    return names
BLOGGERS = load_blogger_console()

# emoji：保留彩色表情块，排除箭头/符号箭头（→ ← ↑ ↓ ➡ 等，属合法标题字符）
EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF"
    "\U00002600-\U000026FF"   # ☀⚠✅ 等杂项符号（保留）
    "\U0001F1E6-\U0001F1FF"   # 区域指示符
    "\U0001F900-\U0001F9FF"
    "\U00002700-\U000027BF]+" # 装饰符号 ✅❌（保留）
)
def has_emoji(s):
    return bool(EMOJI_RE.search(s))
def strip_emoji(s):
    return EMOJI_RE.sub("", s)
def norm_path(p):
    return strip_emoji(p).replace('"', "").replace("'", "").strip()

# ---------- 索引 ----------
index=set(); index_norm={}; index_lower={}; basename_map={}
for root,dirs,files in os.walk(VAULT):
    rel_root=os.path.relpath(root,VAULT); parts=rel_root.split("/")
    if parts[0] in (".obsidian",".trash",".space",".smart-env",".makemd","附件"):
        dirs[:]=[]; continue
    for f in files:
        if not f.endswith(".md"): continue
        link=os.path.relpath(os.path.join(root,f),VAULT)[:-3]
        index.add(link)
        index_norm.setdefault(norm_path(link),set()).add(link)
        index_lower.setdefault(link.lower(),set()).add(link)
        basename_map.setdefault(os.path.basename(link),set()).add(link)

def resolve_link(target):
    t=target.split("|")[0].split("#")[0].strip()
    if t in index: return ("ok","")
    if norm_path(t) in index_norm:
        return ("emoji_or_quote","实际: "+", ".join(sorted(index_norm[norm_path(t)])))
    if t.lower() in index_lower:
        return ("case","实际: "+", ".join(sorted(index_lower[t.lower()])))
    bn=t.split("/")[-1]
    if bn in basename_map:
        return ("short_path","实际: "+", ".join(sorted(basename_map[bn])))
    return ("broken","")

# ---------- frontmatter ----------
def parse_frontmatter(text):
    if not text.startswith("---"): return ({},"",False,None)
    m=re.match(r"^---\s*\n(.*?)\n---\s*\n?",text,re.DOTALL)
    if not m: return ({},"",False,"frontmatter 未闭合")
    raw=m.group(1); fm={}; lines=raw.split("\n"); i=0; n=len(lines)
    while i<n:
        line=lines[i]
        if not line.strip() or line.strip().startswith("#"): i+=1; continue
        m2=re.match(r"^([A-Za-z_\u4e00-\u9fff][\w\u4e00-\u9fff]*):\s*(.*)$",line)
        if not m2: i+=1; continue
        key=m2.group(1); val=m2.group(2).strip()
        # 行内注释处理（YAML 标准语义）：
        # - val 以 # 开头（如 `key: # comment`）→ 整行为注释，值为空
        # - val 中 "空格+#"（如 `key: value # comment`）→ 截断注释；引号包裹的值不截断（如 title: "a # b"）
        if val.startswith("#"):
            val=""
        elif val and val[0] not in ('"',"'"):
            val=re.sub(r"\s+#.*$","",val).strip()
        if val=="":
            items=[]; j=i+1
            while j<n and re.match(r"^\s*-\s+",lines[j]):
                items.append(re.match(r"^\s*-\s+(.*)$",lines[j]).group(1).strip()); j+=1
            fm[key]=items if items else ""
            i=j; continue
        if val.startswith("[") and val.endswith("]"):
            inner=val[1:-1].strip()
            fm[key]=[] if inner=="" else [x.strip().strip('"').strip("'") for x in inner.split(",")]
            i+=1; continue
        fm[key]=val; i+=1
    return (fm,raw,True,None)

# 模板推断（镜像 investment-framework 路径表 + 六大分类）
# 2026-09-07 修复：改为「目录段」匹配——仅匹配分类目录段，不再对整个 rel 子串匹配，
# 消除文件名含分类词（如《周期行业去产能非线性出清框架》含「行业」）导致的模板误判（审查发现）
def template_for(rel,fm):
    parts=rel.split("/")
    # 目录段 = 去掉 归属层/{名称?} 与文件名后的中间目录（不含 .md 文件名参与匹配）
    if parts[0]=="博主":
        sub=parts[2:-1] if len(parts)>2 else []   # 博主/{name}/分类/.../ 中的分类段（含 分析框架/方法论 等）
    elif parts[0] in ("我的","其他","宏观"):
        sub=parts[1:-1] if len(parts)>1 else []   # 归属层/分类/... 中的分类段
        # 顶层宏观文件直接是 宏观/{名称}.md（无子目录）→ 按 frontmatter 判事件型/通用型
        if parts[0]=="宏观" and not sub:
            return "宏观" if ("event" in fm or "时效状态" in fm or "时间范围" in fm) else "宏观通用"
    else:
        return "未知"
    # 1) 分析框架 子目录：方法论 / 分析档案（精确段，可跨多级目录）
    if "方法论" in sub and "分析框架" in sub: return "方法论"
    if "分析档案" in sub and "分析框架" in sub: return "分析档案"
    # 2) 其他分类目录段（按序匹配首个命中）
    for seg in sub:
        tp={"交易体系":"交易体系","投资心态":"投资心态","投资心得":"投资心得",
            "行业":"行业","个股":"个股","宏观":"宏观"}.get(seg)
        if tp: return tp
    # 3) 分析框架 直接兜底（博主/*/分析框架/{方法论/分析档案/...}.md 或 归属层/分析框架/...）：
    #    无 方法论/分析档案 子目录时按 frontmatter 判别
    if "分析框架" in sub:
        return "分析档案" if "标的" in fm else "方法论"
    # 博主画像 md 已废弃（framework-rules #36）：不再识别「博主画像」，
    # 落在 博主/{name}/ 根、非分类子目录的文件 → "未知" → unclassified（暴露流浪画像文件，不校验它）
    return "未知"

# 必填字段（镜像模板 frontmatter 硬约束；改模板时同步）
# star 为内容型通用字段（缺省 false，模板含之）；博主画像 md 已废弃，不再纳入模板
REQUIRED={
 "宏观":["title","event","时效状态","时间范围","createDate","updateDate","author","star","tags","source"],
 "分析档案":["title","标的","createDate","updateDate","author","star","status","tags","source"],
}
for t in ["方法论","交易体系","投资心态","投资心得","行业","个股"]:
    REQUIRED[t]=["title","createDate","updateDate","author","star","tags","source"]
REQUIRED["宏观通用"]=["title","createDate","updateDate","author","star","tags","source"]  # 顶层宏观通用框架，无 event 三字段

# canonical 字段顺序（镜像 framework-rules #27；改模板时同步）
# 用于检测字段顺序漂移——仅比对 canonical 中实际存在的字段
# delete 为常驻可选字段（缺省空=未标记），仅进 CANON 不进 REQUIRED（空值合法，不应报缺失）
CANON={
 "方法论":["title","createDate","updateDate","author","star","delete","tags","source"],
 "交易体系":["title","createDate","updateDate","author","star","delete","tags","source"],
 "投资心态":["title","createDate","updateDate","author","star","delete","tags","source"],
 "投资心得":["title","createDate","updateDate","author","star","delete","tags","source"],
 "行业":["title","createDate","updateDate","author","star","delete","tags","source"],
 "个股":["title","createDate","updateDate","author","star","delete","tags","source"],
 "分析档案":["title","标的","createDate","updateDate","author","star","delete","status","tags","source"],
 "宏观":["title","event","时效状态","时间范围","createDate","updateDate","author","star","delete","tags","source"],
 "宏观通用":["title","createDate","updateDate","author","star","delete","tags","source"],
}

# 期望段落（镜像模板 body 最小必要结构；改模板时同步）
EXPECTED_SECTIONS={
 "方法论":["适用场景","方法步骤","关键指标","案例"],
 "分析档案":["使用的方法论","核心结论","分析过程","估值判断","决策","结果跟踪"],
 "交易体系":["规则","适用条件"],
 "投资心态":["场景","问题","根因","应对策略"],
 "投资心得":["背景","经验教训","借鉴意义"],
 "行业":["行业概况","关键数据"],
 "个股":["基本信息","分析汇总"],
 "宏观":["事件概述","影响分析","传导路径"],
}
WL_RE=re.compile(r"(!?)\[\[([^\]]+)\]\]")
def extract_wikilinks(body): return [m.group(2) for m in WL_RE.finditer(body)]
def check_quoting(raw):
    issues=[]
    for line in raw.split("\n"):
        m=re.match(r'^([\w\u4e00-\u9fff]+):\s*"(.*)"\s*$',line)
        if m and re.search(r'(?<!\\)"',m.group(2)): issues.append((m.group(1),line.strip()))
        m2=re.match(r"^([\w\u4e00-\u9fff]+):\s*'(.*)'\s*$",line)
        if m2 and re.search(r"(?<!\\)'",m2.group(2)): issues.append((m2.group(1),line.strip()))
    return issues

# ---------- 扫描 ----------
F={"no_fm":[],"fm_error":[],"missing_fields":[],"quoting":[],"tag_issues":[],
   "legacy_footnote_heading":[],"forbidden_source_section":[],
   "missing_core_sections":[],"wikilink_issues":[],"unclassified":[],"macro_template_mismatch":[],
   "source_as_invalid":[],"stray_date":[],"field_order":[],"junk_files":[],
   "stock_code_missing":[],"blogger_not_registered":[],
   "other_author_registered":[],
   "recycle_expired":[],"recycle_pending":[],"recycle_invalid":[],
   "footnote_links_workspace":[],"footnote_format":[]}
summary={"total":0,"by_template":{}}

files=[]
for scope in SCOPE:
    for root,dirs,fs in os.walk(os.path.join(VAULT,scope)):
        rr=os.path.relpath(root,VAULT)
        # 排除隐藏/系统目录（任意层级，含子层 .space/templates 等）
        dirs[:]=[d for d in dirs if d not in (".trash",".space",".smart-env",".makemd")]
        if rr.split("/")[0] in (".trash",".space",".smart-env",".makemd"): continue
        for f in fs:
            if f.endswith(".md"): files.append(os.path.relpath(os.path.join(root,f),VAULT))

# 增量模式（2026-08-14 新增）：仅扫描 git 工作区变更的 .md 文件——
# 供流水线操作收尾（采集/提炼/删除后）快速定向校验，问题在产生当天拦截，不等每周审查
if _args.incremental:
    import subprocess
    r = subprocess.run(["git", "-C", VAULT, "-c", "core.quotepath=false", "status", "--short"],
                       capture_output=True, text=True)
    changed = set()
    for line in r.stdout.splitlines():
        p = line[3:].strip()
        if " -> " in p:  # 重命名：取新路径
            p = p.split(" -> ")[-1]
        if p.endswith(".md") and not p.startswith(".") and "/" in p:
            changed.add(p)
    files = [f for f in files if f in changed]
    print(f"[增量模式] git 变更 .md 文件 {len(files)} 个")

for rel in sorted(files):
    summary["total"]+=1
    full=os.path.join(VAULT,rel)
    text=open(full,encoding="utf-8").read()
    fm,raw_fm,has_fm,err=parse_frontmatter(text)
    if not has_fm: F["no_fm"].append(rel); continue
    if err: F["fm_error"].append((rel,err))
    tpl=template_for(rel,fm)
    summary["by_template"][tpl]=summary["by_template"].get(tpl,0)+1
    if tpl=="未知":
        F["unclassified"].append(rel); continue

    # 未归类（不在六大分类文件夹内）
    parts=rel.split("/")
    if (rel.startswith("我的/") or rel.startswith("其他/")) and len(parts)<=2:
        F["unclassified"].append(rel)

    # 必填字段
    miss=[k for k in REQUIRED[tpl] if k not in fm or fm[k] in ("",None,[])]
    if miss:
        if tpl=="宏观" and set(miss)=={"event","时效状态","时间范围"}:
            F["macro_template_mismatch"].append((rel,"通用宏观框架使用了宏观事件模板（缺 event/时效状态/时间范围）"))
        else:
            F["missing_fields"].append((rel,tpl,miss))

    # 流浪 date（层间边界硬约束 framework-rules #27：条目层禁止 date）
    if "date" in fm:
        F["stray_date"].append((rel,"条目层含禁止字段 date（应为原始资源层发布日）"))

    # delete 字段（待删除标记，framework-rules #26）：校验取值 + 计算冷静期状态
    # 2026-08-05 起 delete 为全条目常驻字段（缺省空值 = 未标记），仅非空值参与回收判定
    if "delete" in fm:
        dv=fm["delete"]
        if dv in (None,""):
            pass  # 缺省空 = 未标记，忽略
        elif isinstance(dv,str) and re.match(r"^\d{4}-\d{2}-\d{2}$",dv):
            try:
                d_mark=datetime.date.fromisoformat(dv)
                days=(datetime.date.today()-d_mark).days
                if days>7:
                    F["recycle_expired"].append((rel,dv,days))
                else:
                    F["recycle_pending"].append((rel,dv,days))
            except ValueError:
                F["recycle_invalid"].append((rel,dv))
        else:
            F["recycle_invalid"].append((rel,dv))

    # 字段顺序 canonical（framework-rules #27）
    # id 为外部插件字段（Visit History 等，见 framework-rules #27「id 字段豁免」），
    # 不在 CANON 中，天然被下列过滤忽略；此处显式防御，防止未来误加。
    canon=CANON.get(tpl)
    if canon:
        expected=[k for k in canon if k in fm and k != "id"]
        actual=[k for k in fm.keys() if k in canon and k != "id"]
        if actual!=expected:
            F["field_order"].append((rel,tpl,actual))

    # 引号
    for k,l in check_quoting(raw_fm): F["quoting"].append((rel,k,l))

    # 标签
    tags=fm.get("tags",[])
    if isinstance(tags,list):
        if len(tags)==0:
            F["tag_issues"].append((rel,"tags 为空"))
        # 方法论/内容性质标签违规（framework-rules #17 · 2026-08-08 用户纠正）
        METHOD_BANNED = {"基本面","价值投资","教训复盘","投资理念","交易系统","心态","仓位管理",
                          "交易策略","投资策略","投资框架","风控","止损","止盈","复盘","抄作业",
                          "趋势","波段","短线","长线"}
        for tg in tags:
            if str(tg) in METHOD_BANNED:
                F["tag_issues"].append((rel,"方法论/内容性质标签(违规 #17): "+str(tg)))
        # 裸标签违规（tag-taxonomy 层级硬约束：最少二级、须挂前缀；日期标签例外）
        for tg in tags:
            s = str(tg)
            is_date = bool(re.match(r'^\d{4}-\d{2}-\d{2}$', s))
            if '/' not in s and not is_date:
                F["tag_issues"].append((rel,"裸标签(违规，须带一级前缀): "+s))
        for tg in tags:
            if has_emoji(str(tg)): F["tag_issues"].append((rel,"标签含emoji: "+str(tg)))

    # 段落
    h2=[h.strip() for h in re.findall(r"^##\s+(.+)$",text,re.MULTILINE)]
    # 遗留 ## 脚注 标题（规则#20：新格式脚注定义放文末，无 ## 脚注 标题）
    if "脚注" in h2:
        F["legacy_footnote_heading"].append(rel)
    # 禁止 ## 来源（精确匹配标题，避免误伤"## 数据来源"等合法标题）
    if re.search(r"^##\s+来源\s*$", text, re.MULTILINE):
        F["forbidden_source_section"].append((rel,h2))
    # 缺核心段
    miss_sec=[s for s in EXPECTED_SECTIONS.get(tpl,[]) if s not in h2]
    if miss_sec:
        F["missing_core_sections"].append((rel,tpl,miss_sec))

    # source 形态校验（framework-rules #23：内部→wikilink，外部→[标题](URL)，二选一）
    src=fm.get("source")
    def source_shape_invalid(item):
        """返回 None=合法；否则返回原因字符串"""
        s=str(item).strip()
        # 剥掉 YAML 引号（单双引号）
        if len(s)>=2 and s[0] in ('"',"'") and s[-1]==s[0]:
            s=s[1:-1].strip()
        # 合法形态1：内部 wikilink
        if s.startswith("[[") and s.endswith("]]"):
            return None
        # 合法形态2：外部 markdown 链接 [标题](URL)
        if re.match(r'^\[[^\]\n]+\]\(https?://[^)\s]+\)$', s):
            return None
        # 裸 URL（无标题）→ 违规
        if re.match(r'^https?://', s):
            return "裸URL（应改为 [标题](URL) 或 wikilink，见 #23）"
        # 其他形态
        return f"非法source形态（见 #23）：{s[:40]}"
    if isinstance(src,list):
        for s in src:
            reason=source_shape_invalid(s)
            if reason: F["source_as_invalid"].append((rel,str(s)[:60],reason))
    elif isinstance(src,str):
        reason=source_shape_invalid(src)
        if reason: F["source_as_invalid"].append((rel,src[:60],reason))
    # wikilink 可追溯性
    wl_sources=[]
    if isinstance(src,list):
        for s in src: wl_sources.extend(extract_wikilinks(str(s)))
    elif isinstance(src,str): wl_sources.extend(extract_wikilinks(src))
    for wl in extract_wikilinks(text)+wl_sources:
        target=wl.split("|")[0].split("#")[0].strip()
        if not target: continue
        if has_emoji(target):
            F["wikilink_issues"].append((rel,wl,"wikilink含emoji","")); continue
        st,sug=resolve_link(target)
        if st=="broken": F["wikilink_issues"].append((rel,wl,"失效链接",sug))
        elif st=="emoji_or_quote": F["wikilink_issues"].append((rel,wl,"路径含emoji/引号不匹配",sug))
        elif st=="case": F["wikilink_issues"].append((rel,wl,"大小写不匹配",sug))
        elif st=="short_path": F["wikilink_issues"].append((rel,wl,"路径不完整(仅basename)",sug))

    # 脚注 wikilink 目标仅限 wiki 产物（footnote-taxonomy 禁止行为 #6）：仅检查脚注定义行
    # （[^x-N]: ... 格式，位于文末脚注区）中的 wikilink，禁止指向 工作区/ 下任何文件
    for line in text.splitlines():
        if re.match(r"^\[\^[a-z]*-[0-9]+\]:", line.strip()):
            for wl in extract_wikilinks(line):
                target=wl.split("|")[0].split("#")[0].strip()
                if target.startswith("工作区/"):
                    F["footnote_links_workspace"].append((rel, wl, "脚注指向工作区文件（仅限wiki产物间引用，见 footnote-taxonomy 禁止行为#6）"))

    # 脚注格式细则（2026-09-27 审查固化）。此前 SKILL 声明的 S6 只实现了一半：
    #   标签白名单、标签与中文关系词一致性、关联脚注必须有 wikilink 目标、data/date 时间锚
    #   四项都没有检查项，所以"脚注格式 0 问题"是假绿（当次实测全库 41 条不合规）。
    #   孤儿/悬空脚注已由 verify-format.py 覆盖，此处不重复。
    REL_CN = {"enhance":"增强","supplement":"补充","conflict":"冲突","complement":"互补","opposite":"对立"}
    for line in text.splitlines():
        ls = line.strip()
        m = re.match(r"^\[\^([^\]]+)\]:\s*(.*)$", ls)
        if not m: continue
        key, body = m.group(1), m.group(2)
        prefix = key.rsplit("-", 1)[0]
        where = (rel, "[^"+key+"]")
        if not re.match(r"^(enhance|supplement|conflict|complement|opposite|data|date)-\d+$", key):
            F["footnote_format"].append((*where, "非法标签名（白名单=enhance/supplement/conflict/complement/opposite/data/date，见禁止行为#1）"))
        if "]]]" in body or body.count("]]") > len(extract_wikilinks(body)):
            F["footnote_format"].append((*where, "wikilink 多余闭合（]]] 或多出一个 ]]）"))
        parts_em = re.split(r"\s+—\s+", body, maxsplit=1)
        tail = parts_em[1].strip() if len(parts_em) > 1 else ""
        if prefix in REL_CN:
            if not extract_wikilinks(body):
                F["footnote_format"].append((*where, "关联脚注无 wikilink 目标（指向看板/画像 md 的引用已失效，画像自 #36 退役）"))
            mc = re.match(r"^(增强|补充|冲突|互补|对立|数据|时效|关联|参考)\s*[:：]", tail)
            cn = mc.group(1) if mc else ""
            if cn in ("关联", "参考"):
                F["footnote_format"].append((*where, f"描述用了非标准关系词「{cn}」"))
            elif cn and cn != REL_CN[prefix]:
                F["footnote_format"].append((*where, f"标签 {prefix} 与中文关系词「{cn}」不一致（禁止行为#2）"))
            elif not cn:
                F["footnote_format"].append((*where, f"关联脚注缺中文关系词「{REL_CN[prefix]}：」"))
        elif prefix == "data" and not re.search(r"截至|截止", body):
            F["footnote_format"].append((*where, "data 脚注缺截止日期（规范：来源 — 描述，截至YYYY年M月）"))
        elif prefix == "date" and not re.search(r"截至\s*\d{4}\s*年", body):
            F["footnote_format"].append((*where, "date 脚注缺「截至YYYY年M月」时间锚"))

    # 个股代码（framework-rules #28）：文件名须含 {名称}({代码})，代码后可附加描述后缀
    # 代码位数：A股6位/港股4-5位/美股1-5位/澳交所等3位——统一放宽为 2-6 位字母数字（含 .AX 等市场后缀形式）
    if tpl=="个股":
        bn=os.path.basename(rel)[:-3]
        if not re.search(r"\([A-Za-z0-9\.]{2,6}\)",bn):
            F["stock_code_missing"].append((rel,"个股文件名缺股票代码，应为 {名称}({代码})，见规则#28"))

    # 博主层作者登记校验（framework-rules #12）：博主文件夹名须在博主控制台登记
    # 豁免名单（用户 2026-08-05 决定）：以下博主保持博主层不迁移，即使未在控制台登记也不报错
    BLOGGER_EXEMPT = {"段永平", "七彩云龙", "梁宏"}
    if rel.startswith("博主/") and BLOGGERS:
        bname=parts[1]
        if bname not in BLOGGERS and bname not in BLOGGER_EXEMPT:
            F["blogger_not_registered"].append((rel,bname))
    # 反向校验（2026-09-07 审查新增 · 规则 #12 反例）：其他层条目 author 若为已登记博主，
    # 提示内容应挂博主层（登记后未回迁的典型场景）；「其他/宏观/」为通用宏观不受此限
    # 注意：此为提示级（供人工裁决），不自动 fail——「解读/第三方整理」类内容 author 语义需人工甄别
    if rel.startswith("其他/") and "宏观" not in rel and BLOGGERS:
        aus=fm.get("author","")
        for a in re.split(r"[、,/与和]",str(aus)):
            a=a.strip().strip('"').strip("'")
            if a and a in BLOGGERS:
                F["other_author_registered"].append((rel,a,"author 为已登记博主，疑似误挂其他层（规则 #12），建议迁移 博主/{a}/ 或人工甄别解读属性"))

# junk：缺全部字段的空壳
for rel,tpl,miss in F["missing_fields"][:]:
    if set(miss)=={"title","event","时效状态","时间范围","createDate","updateDate","tags","source"}:
        F["junk_files"].append(rel)

out_path=os.path.join(OUT,"vault_review_result.json")
json.dump({"summary":summary,"findings":F},open(out_path,"w",encoding="utf-8"),ensure_ascii=False,indent=2)
print(f"vault: {VAULT}")
print(f"结果已写入: {out_path}")
print("总文件:",summary["total"],"| 模板分布:",json.dumps(summary["by_template"],ensure_ascii=False))
print("\n=== 问题计数 ===")
for k,v in F.items(): print(f"{k}: {len(v)}")
