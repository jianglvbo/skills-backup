---
name: xueqiu-spyder
description: |
  雪球博主帖子采集（编排+工具一体；2026-09-26 由原抓取工具层与 post-fetch 编排层合并而成）。
  **feed 流式采集为日常增量缺省**：关注流一次会话覆盖全部已关注博主（多博主帖子集、流内展开与计数、
  引用卡结构化为回复内容、流断点书签取代逐博主 cutoff），风控暴露最小；user 逐博主翻页保留给
  首采/深窗口/补漏。产物落 post_history 后交接投资框架 skill 提炼。
  触发词：「xueqiu-spyder」「spyder 抓取」「雪球抓取」「抓取雪球」「采集雪球」「雪球帖子」
  「xq fetch」「雪球动态」「采集帖子」。
  排除条件：含「提炼」「分析」「画像」→ 投资框架 skill（提炼环节）；含「审查」→ 投资框架 skill（审查环节）。
license: MIT
agent_created: true
metadata:
  version: "4.0.0"
  short-description: 雪球帖子采集（feed 流式缺省 + 逐博主兜底，编排工具一体）
compatibility: macOS / Linux
---

# 雪球帖子采集 v4.0（编排 + 工具一体）

## Default Stance

### 核心原则
- **采集层单一职责**：只做抓取、帖子集输出与落库，不做框架动作（控制台同步之外的分析/提炼/画像均不在此层）。
- **模式缺省**：feed 流式（`main.py feed`）＝日常增量缺省（关注流一次会话覆盖全部博主）；user 逐博主（`main.py user`）＝首采/深窗口/补漏兜底。决策表见 `references/execution-guide.md`「模式决策」。
- **ego lite 通道唯一**（2026-09-16 收口）：复用本机已登录雪球的 ego lite（socket 桥 `ego_browser.py`+`ego_bridge.js`）；Chrome 通道代码已整段删除，`XUEQIU_TRANSPORT` 只认 `ego`。
- **防漏采铁律**：只有采集真正完成才推进断点/cutoff——退出码 1（失败）/ 3（窗口起点或书签未翻到）一律禁写；宁可重复采，不可漏采。
- **风控自控**：滑块/安全验证不硬撞——激活 ego 交用户接管、过完自动重试；**静默空页＝风控**（博主主页不可能零帖子，空列表一律按拦截处理，绝不判「无新帖」）；连续失败熔断。
- **纯文本产出**：写入前逐条净化（删 `![[..]]`/`![](url)`/`<img>`/裸图 URL/表情占位；Unicode emoji 保留）；`作者` 值不得含 `发布于|来自|关注`，判不出置 `Unknown`；采集阶段不猜 content_type/view_date（投资框架提炼环节判定）。

### 禁止行为
- 绝不内置或硬编码博主列表（以关注列表↔控制台同步为准）
- 绝不绕过登录态裸调 API（WAF 拦截）；绝不连续翻页硬撞滑块
- 绝不在采集阶段分析或总结帖子内容
- 绝不把置顶帖归入采集窗口（user 模式全部 tab；**新博主首采热门路径例外**——置顶有意保留进正文）
- 绝不跳过格式验收（对照 output-format.md）与入库校验
- 绝不让 ego lite 抢焦点（唯一例外：滑块交接；`XUEQIU_EGO_WAKE=0` 缺省不激活）
- 绝不未经完整性验证标「全文」——user 模式以详情页为准；feed 模式以流内信号为准（无展开控件或展开成功＝全文，2026-09-26 实测 DOM 自带截断控件）

---

## Workflow

### 第一步：环境就绪

```bash
PY=${XUEQIU_PY:-$(cat ~/.config/xueqiu-spyder/python 2>/dev/null || echo python3)}
SPYDER="$(ls -d ~/.zcode/skills/xueqiu-spyder ~/Project/investment-dashboard/.agents/skills/xueqiu-spyder 2>/dev/null | head -1)"
$PY -c "import requests" && ego-browser --help >/dev/null && echo OK   # 路径会迁，先 ls 探活
```

- 前决条件：**ego lite 正在运行且已登录雪球**（工具不负责拉起）；未登录 → 停止，提示用户在 ego lite 里登录
- **采集期间请把 ego lite 留在可见位置**——风控弹窗只在页面上出现，截图（`~/.cache/xueqiu-spyder/shots/<批次>/`）是事后核对用的
- 「用户接管」会中断整轮（正常保护，别抢回）；下一轮自动换新空间继续

### 第二步：前置同步（关注列表 ↔ 看板控制台，每次会话必做）

```bash
python3 "$SPYDER/scripts/xq_sync_console.py"            # dry-run（默认）
python3 "$SPYDER/scripts/xq_sync_console.py" --apply    # 确认后落地
```

新增→报告后登记；取关→报告由用户看板手工删；残留检测 + info_cutoff 一致性核对 → 细节见 `references/execution-guide.md`「前置步骤」。

### 第三步：模式决策

| 场景 | 模式 |
|:---|:---|
| 日常增量（全部已关注博主，隔 ≤3 天） | **feed**（**自带配图与对话串**，2026-10-02 用户定稿：每轮采集都含这两样，落库时帖子走 import-post-history.js、对话串 JSON 走 import-thread.js，两者都做完才算采集完成） |
| 首采 / 深窗口（>3 天）/ 跨周补采 / 书签未翻到 | user |
| **新博主首采**（半年回溯 + 热门 5 页，2026-09-28 用户定） | user `--hot-pages 5`（先走 profile 档案落库） |
| feed 完成后的缺帖抽查 | user（轮转抽 1/5~1/7 未露面博主翻一页核对） |

### 第四步：feed 模式采集（缺省）

```bash
$PY "$SPYDER/main.py" feed --tab follow --limit 50 \
  --outfile "雪球采集-关注流-{YYYY年M月D日}.md" --output ~/.cache/xueqiu-spyder/out
```

- 窗口＝**流断点书签**（`~/.cache/xueqiu-spyder/feed-state.json`）→ 当前；无书签按 limit 采；**`--limit` 按窗口积压估**（≈天数 × 日均流帖量 × 1.3，活跃关注 150+/天），limit 太小会把窗口截断在旧帖前
- 退出码：0=产出（写书签）/ 2=窗口内无帖（写书签）/ **3=书签未翻到（禁写书签，已采部分仍产出、缺口走 user）** / 1=失败（禁写）
- 关注流缺省按看板博主过滤（服务不可用时不过滤，未建档帖入库侧跳过）；流内相对时间锚定采集瞬间换算绝对时间（`（流内推算）`/`（修改于·推算）`标记）；例外帖（自身专栏/展开失败）走详情页（节流 2.6~3.4s、**主动预算 50 次/轮**＋连败 3 次熔断，防 WAF 连击）
- cutoff 回写：仅对**本轮实际采到帖**的博主回写（值=书签）；未露面博主不动
- 机制要点与全部细节 → `references/execution-guide.md`「feed 模式」

### 第五步：user 模式采集（兜底）

```bash
NOW=$(date "+%Y-%m-%dT%H:%M:%S")
$PY "$SPYDER/main.py" user {xq_id} --from "{info_cutoff}" --to "$NOW" \
  --max-pages {N} --outfile "雪球采集-{昵称}-{YYYY年M月D日}.md" --output ~/.cache/xueqiu-spyder/out
```

- **`--max-pages` 按窗口长度取**（硬约束）：≤24h→3、≤7 天→5、>7 天→10；批量每 10 位停 60 秒
- **时区陷阱**：看板 API 把本地时间序列化成 UTC ISO，做 `--from` 必须 +8h 还原（批处理脚本已内置）
- 流程：翻页 → 置顶排除+时间窗过滤 → 截断帖详情页补全（含精确时间覆盖）→ 帖子集输出；v4 端点 405 自动降级旧端点
- `--hot-pages N`（新博主首采用 5）：热门 tab（type=9）前 N 页去重并入帖子集，不受时间窗裁剪；窗口起点自动停（排除置顶后最旧帖 ≤ `--from` 即止）
- 批量跑全部博主用 `scripts/run_fetch_batch.py`；细节 → `references/execution-guide.md`「user 模式」「新博主首采」

### 第六步：格式验收（强制，对照 `references/output-format.md`）

frontmatter 齐全（type=帖子集；status=待提炼 / 待提炼-含摘要）→ 每帖三件套（标题+正文+发布行，发布行含 `形态`、`作者`、`全文/摘要`、`[原文]`）→ 纯文本净化 → 归属正确（`作者` ≠ 全员同名/知名大V名＝抓到推荐位）。

### 第七步：落库 + 校验 + 清理（强制）

```bash
node ~/Project/investment-dashboard/src/scripts/import-post-history.js "<采集产物.md>"        # 幂等落库
node "$SPYDER/scripts/check-post-history-covered.js" "<采集产物.md>"                          # 入库校验
node ~/Project/investment-dashboard/src/scripts/import-post-history.js --rm "<采集产物.md>"   # 过校验后才清理
```

- 摘要帖与无链接帖按设计不入库；产物是临时文件，**不进 vault**（`~/.cache/xueqiu-spyder/out`）
- **对话串 JSON 同步落库（2026-10-02 用户定稿，每轮必做）**：feed 会为流内回复帖自动采链（`[对话串] N 位博主、M 条链 → …-对话串-<博主>.json`），逐个文件 `node ~/Project/investment-dashboard/src/scripts/import-thread.js <json>`——只落帖子不落串＝采集没做完
- 保留期清理：`node ~/Project/investment-dashboard/src/scripts/purge-post-history.js --dry`（180 天滚动窗口）

### 第八步：断点与 cutoff 回写

- **feed**：书签在采集器内自动维护（成功才写）；cutoff 按「本轮见到帖」的博主回写（`scripts/xq_update_cutoff.py`）
- **user**：退出码 0/2 → 回写该博主 info_cutoff 为实际完成时间；1/3 → **cutoff 不动**、列入待重试清单报告用户

### 第九步：报告

条数、时间范围、全文/摘要数、博主数；出过风控必须附截图路径；末尾附进度条 `node ~/Project/investment-dashboard/src/scripts/fetch-progress.js`（完成口径：有留档/已注销/确认无新帖）。

### stock / search / profile 子命令（独立能力，不经提炼流水线）

```bash
$PY "$SPYDER/main.py" stock SZ002738 --min-reply 20 --max-pages 10   # 个股大V观点报告
$PY "$SPYDER/main.py" search 治雨                                     # 搜用户 ID
$PY "$SPYDER/main.py" profile 4026867340                             # 主页档案 JSON（uid/昵称/头像/简介/粉丝），新博主登记用
```

---

## Output Format

帖子集 markdown，规范唯一权威在 `references/output-format.md`（frontmatter 七字段 + 三件套 + 发布行 `形态/作者/全文|摘要/[原文]` + 回复内容块 + 多博主规则）。feed 多博主帖集的归属以发布行 `作者：` 为权威、URL uid 仲裁兜底。

## Relative Files

| 场景 | 文件 | 方式 |
|:---|:---|:---|
| 执行细节/前置同步/风控/模式决策/feed 与 user 模式细则 | `references/execution-guide.md` | 读取 |
| 对话串采集流程（查看对话四步/分页/分类判据/清洗规则；采集器 `scripts/xq_dialog_collect.py` 时间线单模式——URL 直达补采已删、落库 `import-thread.js` 口径） | `references/dialog-flow.md` | 读取 |
| 帖子集格式/铁律/回复内容块 | `references/output-format.md` | 读取 |
| 环境变量/退出码语义 | `references/env-vars.md` | 读取 |
| 流式采集核心 | `feed.py`（`main.py feed` 入口） | 执行 |
| 逐博主/个股/搜索 | `main.py` `crawler.py` `analyzer.py` `report.py` `config.py` | 执行 |
| ego 通道 | `ego_browser.py` `ego_bridge.js` | 执行 |
| 编排脚本（同步/cutoff/批量/摘要补全/落库校验） | `scripts/xq_sync_console.py` `scripts/xq_update_cutoff.py` `scripts/run_fetch_batch.py` `scripts/xq_refetch_summary.py` `scripts/check-post-history-covered.js` `scripts/xq_ego.py` `scripts/clean_legacy_batches.py` | 执行 |

## Source Hierarchy

1. 用户显式参数 > 2. 看板控制台（MySQL blogger 表） > 3. `references/*.md` 规范 > 4. 雪球页面/API 实际结构（由工具层解析）

---

## 自检

- [ ] ego lite 已打开且已登录雪球？日志确认走 ego 通道（不应出现任何 Chrome 字样）？
- [ ] 前置同步已执行（关注列表↔控制台，含残留与 info_cutoff 一致性）？
- [ ] 模式选择符合决策表？feed 采集后未露面博主是否安排了轮转抽查？
- [ ] **退出码语义正确**：1/3 未推进断点/cutoff、已列入待重试？未把「书签未翻到」误当「无新帖」？
- [ ] 产物对照 output-format.md 验收：三件套/发布行 `作者`/全文摘要标记/`[原文]` 链接/纯文本净化？
- [ ] 报「无新帖」的博主（user 模式）经同源 API 复核过？空列表不得当无新帖？
- [ ] 产物已落 post_history 且入库校验通过后才清理临时文件？
- [ ] 全程没抢焦点（滑块交接除外）；跑完无残留页签/空间；风控截图已归档并附在汇报里？
- [ ] `作者` 校验：多博主帖集按发布行作者归属、uid 仲裁无冲突？全员同名＝推荐位误抓？
- [ ] 新博主首采走了完整三步（profile 档案落库含头像 → 半年窗口 → 热门 5 页）？热门/置顶处理符合「新博主首采」节？
