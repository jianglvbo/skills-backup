---
name: investment-review
description: >
  投资框架审查执行器。执行内容审查（框架一致性、知行合一、我的vs博主冲突、经验验证、跨条目关联备注发现）
  和结构审查（归类正确性、frontmatter完整性、wikilink有效性、标签匹配）。
  触发词：「审查」「review」「健康度」「框架检查」。
  环节词归执行器所有（investment-framework 已让出粗加工/归档/提炼/审查这几组词），用户说「审查」「review」即命中本 skill；
  整链编排走 investment-framework。区别于 investment-refine（提炼）与
  investment-coarse-processor（粗加工）；框架路径/模板/规则以 investment-framework 为准，本 skill 只做审查并落库。
license: MIT
agent_created: true
metadata:
  version: "2.16.0"
  short-description: 投资框架审查执行器（含关联备注发现、言论追踪审计 C10）
compatibility: 通用

---

# 审查执行器

---

## Default Stance

### 核心原则

- **两层审查独立执行**：内容审查和结构审查分步进行，各自产出独立报告
- **只报告不修改**：审查只输出问题清单和关联备注提案，不直接修改文件，由用户决定是否采纳
- **关联备注在审查阶段发现**：内容审查时扫描全 vault 条目，为缺少跨条目关联的条目提议补充脚注。脚注类型、格式和规则详见 `investment-framework/references/footnote-taxonomy.md`（与 tag-taxonomy.md 同级，位于 investment-framework/references/）。关联脚注以脚注形式嵌入正文相关论述处，不用独立 `## 关联` section。提炼阶段可加 `[^data-N]`（数据溯源）和 `[^date-N]`（时效标注），关联脚注仅在审查阶段添加
- **参数全部由编排者传入**：审查范围、目标目录由编排者指定
- **待回收处置例外**：全局规则 #26 的回收执行（真删 + 双向清理）属于用户经 `delete` 字段标记 + 7 天冷静期已授权的既定动作，审查直接执行并出「待回收处置报告」给出理由；其余审查发现（关联备注等）仍仅报告不修改

### 禁止行为

- 绝不直接修改任何框架文件——关联备注提案仅出现在报告中，用户确认后才写入（待回收处置的回收执行除外，属 #26 已授权动作）
- 绝不审查或修改"我的"层——「我的」层由用户自管（规则 #15），审查范围仅限 博主/其他/宏观
- 绝不将结构问题与内容问题混在同一份报告中
- 绝不在关联备注中编撰原文没有的关系——每条关联必须有内容层面的依据

---

## Workflow

### 第零步：复核建议处理（审查首步 · 必做）

按 `investment-framework/references/review-rules.md`「复核建议处理（审查首步）」执行：`console_statement_review(action=list, status=open)` 取全部未处理建议（返回含该言论当前 `contentType/stance/target/viewText` 上下文）→ 逐条按建议用 `blogger_statement(action=update)` 修正归类 → `console_statement_review(action=apply)` 置已处理；判断建议不成立则 `action=delete` 并在报告说明理由。**本步未处理完，不得进入 C/S 维度**；修正结果并入审查报告。

**边界（首步来源按 kind 分流，2026-09-27）**：① `statement_review_sub` 的左滑复核建议（原 ② `refine_review` 已于 2026-09-26 随提炼步骤功能下线）；② `pending_decision` 的**审查类已裁决项**（`kind=审查裁决` 且已答复未内化）——按用户答复执行修复 → `internalize` 写落点，明细入报告「审查裁决执行」节（细则见 review-rules.md「复核建议处理」第 5 点）。提炼类条目（含 `verdict=delete` 删除清单）仍**归 `investment-refine` 第一步「1.1 清待决策队列」，审查不碰**。

### 内容审查（编号 C1-C10）

**C1**：读取参数 `{ scope_dirs, blogger_console_path }`；**C2**：扫描 scope_dirs 下所有 .md 文件
**C3**：内部一致性检查——交易体系的规则 vs 分析框架的方法论是否矛盾；**C4**：知行合一检查——投资心态中记录的纪律 vs 分析档案中的实际行为
**C5**：我的 vs 博主冲突检查（**可选，仅当用户显式要求时**）——默认不审查「我的」层（规则 #15 用户自管），若用户要求对比「我的」层方法论与「博主」层冲突，临时读取「我的」层执行
**C6**：经验验证检查——投资心得中的教训是否在后续分析档案中被验证
**C7**：关联备注——为缺少跨条目关联的条目补充脚注（脚注类型和格式见 `investment-framework/references/footnote-taxonomy.md`），在正文相关论述处嵌入标记，文末脚注定义（无 ## 脚注 标题、无 --- 分隔线）写 wikilink + 关系类型 + 一句话说明
**C8**：关系依据复核——逐条打开文件中**已存在**的关联脚注，核对目标文件原文是否支撑其关系声明；无依据的一律记为"编撰关系"，在报告中建议删除或降级为 enhance（见 footnote-taxonomy.md「关系依据校验」）
**C9**：输出内容审查报告
**C0（2026-09-12 新增）：别名与规则迭代核对**——审查时对「个股指代」做一次回看：① 言论里出现、但 `stock.aliases` 未登记的称呼 → 补登（`stock_alias`）；①b 无个股关联的言论正文明确点名个股（含 `名称(代码)` 行情标签——#60 门禁（2026-09-19）生效前的存量批次易漏挂）→ 按提及判定规则确认主语后用 `blogger_statement(update, target)` 补挂关联；子串歧义提及（如「中美的经济」含「美的」）与仅作类比/举例的提及不挂（#52 合法 0 关联）；② 被误挂的常用词（如「好美的风景」→ 美的集团）→ 改正关联 + 若缺歧义标记则 `mark-ambiguous` + 把案例写进 `stock-mention-rules.md` 误判清单；③ 报告里单列「本次新增别名 / 新增歧义词 / **新增判定案例** / 规则修订」四项，做到**知识随审查沉淀**；案例用 `stock_alias(action=case-add)` 落 `mention_case`，不要写进 md。

**C10**：言论追踪审计（执行 `scripts/tracks_audit.py --vault <vault路径> [--mysql]`，凭据用环境变量 `DB_PASS`；**带 `--mysql` 时必须用 `python3.11` 跑**——依赖 pymysql，本机默认 `python3` 未装会 `ModuleNotFoundError` 让整段审计静默失败，2026-09-27 实测）——**2026-09-12 重写后**核对 MySQL `statement`（六表视图）数据质量：空正文/缺原文链接/缺 `form`、`content_type` 与 `stance` 码值合法性、P1 三类（trade/predict/research）缺实体关联、复核建议积压（`statement_review_sub`）；**2026-09-27 增三项**：`stance_without_subject`（#59A 全类型存量——服务端 `_validateSignal` 不查关联表存在性，只有这里能抓到）、`signal_without_stance`（#59 后 signal_text 不再渲染、仅正文兜底的遗留值）、`insight_without_wiki`（心得具象化覆盖率）；兼看「insight 帖具象化覆盖率」（该有框架条目的心得帖要有 `wiki_ref`）。**原文留档覆盖率按 180 天窗口判**（`post_history_id` 回指，逾 180 天查不到属正常、不计缺口；2026-09-15 由 30 天放宽）。画像 md 已废弃，不再做 md↔DB 对照。**只报告不修改**，问题并入内容审查报告。

### 结构审查（编号 S1-S8）——审查维度定义见 `investment-framework/references/review-rules.md`，按以下顺序执行。

**辅助 · 预扫（可选）**：① 结构/元数据——`scripts/vault_review.py --vault <vault路径>` 生成 `vault_review_result.json`（归类/frontmatter 含 updateDate/引号/wikilink 含 source/脚注格式/标签 六维 + 言论/买卖/预测三表原文链接（#35；画像 md 已退役，vault_review 的画像文件检查仅作比对期参考）/禁用 `## 来源`/source 形态（#23）/空壳 junk 扩展检查，只报告不修改）。**脚注细则看 `footnote_format` 键**（2026-09-27 补齐：标签白名单、`]]]` 多余闭合、标签前缀与中文关系词一致性、关联脚注必须有 wikilink 目标、data 缺截止日期、date 缺时间锚——此前 S6 声明的这几项脚本里并不存在，"脚注格式 0"是假绿，补上后同批实测 41 条）；孤儿/悬空由 verify-format.py 判，别在这里重复。② 段落布局——`investment-framework/scripts/verify-format.py <vault路径> --scope 其他,博主,宏观`（同行标题/标题间距/段落紧凑/脚注内联孤儿/模板残留，`--fix` 可自动修复）。人工据 JSON 撰写报告时聚焦机器无法判定的部分（段落缺失是否确无内容、标签语义、关联备注提案）。

**S1**：读取参数 `{ scope_dirs }`
**S2**：归类正确性——含博主层条目其作者是否均在博主控制台（看板 MySQL blogger 表）登记，未登记者误挂博主层须标记迁移至其他层（见 framework-rules #12）
**S3**：frontmatter 完整性——必填字段 title/createDate/updateDate/**author**/tags/**source** 齐全（author/source 缺失即标记）；字段顺序须按所属分类模板 canonical 排列（标准 8 字段 `title→createDate→updateDate→author→star→delete→tags→source`，分析档案/宏观事件型见 framework-rules #27）；**禁止出现 `date` 字段**（层间边界硬约束，见 framework-rules #27）；日期字段裸写无引号
**S4**：引号有效性（全局规则 #21）
**S5**：wikilink 有效性——扫描**正文与 frontmatter `source` 字段**中的所有 wikilink，目标不存在即标记
**S6**：脚注格式——检查每条脚注定义的 wikilink 是否以单 `]]` 闭合（禁止 `]]]`/多余 `]]`），格式是否为 `[[target]] — 关系：说明`（见 footnote-taxonomy.md「格式校验」）。**额外必查**：(1) 标签前缀是否在白名单内（enhance/supplement/conflict/complement/opposite/data/date），非法标签如 `关联`/`ref` 一律标记；(2) 标签前缀与描述中中文关系词是否一致（enhance=增强、supplement=补充、conflict=冲突、complement=互补、opposite=对立、opposite=对立）；(3) 孤儿/悬空检查——每条定义必须有对应内联标记，每条内联标记必须有对应定义
**S7**：标签匹配；**S8**：输出结构审查报告

### 待回收处置（审查时执行，编号 R1-R5）

按全局规则 #26（delete 字段机制）处置待回收条目——**不再解析表格**，改为扫描内容型条目的 `delete` 字段。无独立回收控制台（历史由系统废纸篓兜底）。

**R1**：扫描 scope_dirs 下所有内容型条目的 frontmatter，收集含 `delete` 字段的条目，提取标记日期 `delete`（YYYY-MM-DD）
**R2**：对每个标记条目计算 `冷静天数 = today - delete`
**R3**：分支判定
- 若 `冷静天数 > 7` → 标记「本次将回收」，执行 R4
- 若 `冷静天数 ≤ 7` → 跳过，报告中提示剩余天数（`待回收（剩 N 天）`）
**R4（回收执行 · 真删 + 双向清理）**：对超期条目
- 反查 vault 中所有**指向该条目**的 wikilink 与关联脚注（含博主档案、其他条目、frontmatter `source` 字段），列出清单
- 清理这些 inbound 引用（移除脚注定义 + 正文标记，或档案 wikilink 行），保持无悬空链接
- 将条目文件移出 vault（移至系统废纸篓，完成真删），记录清理了 M 处引用
**R5**：输出「待回收处置报告」，**必须给出每条回收的理由**（超 7 天冷静期 + 标记日期 + 清理引用数）；未到期者提示剩余天数
**R6（收尾 · 必做）**：**规律固化**——按 `review-rules.md`「规律固化（审查收尾步）」把本次全部发现（C 维度 + S 维度 + 复核建议处理）强制二分为「一次性数据问题 / 可泛化规律」；可泛化的必须当场回写对应规范（`framework-rules.md` 对应编号 / `investment-refine/SKILL.md` 判据 / 操作门脚本加校验项），并在落库 record 的 summary 与汇报中写明「本次固化了什么（文件+条目）、哪些判定一次性、未固化理由」。**未执行 R6 不得结束审查**。
**R7（收尾 · 上报待决策，2026-09-27 新增）**：按 `review-rules.md`「审查发现上报待决策」把无法自行处置、需用户拍板的发现（矛盾边界/删除保留/迁移去留/归属判定）上报 `pending_decision(kind=审查裁决)`，由下轮审查首步闭环执行；机械可修的不上报。**拍板后还要用户自己做动作的**（补数据/改文件/线下核对），改按 assign-tasks skill 下发任务（`note_item type=task source=agent`，带 contextRef=`review:<日期>`）——问句走队列、动作走任务。

---

## Output Format

> **2026-08-16 起：审查不再产出 md 报告文件，直接落库投资看板**。审查完成后按 `references/report-templates.md`（落库 schema 模板）组装结构化 record，`MCP 工具 `review_record`（REST POST /api/review/record 兼容，连接见 investment-framework/references/console-mcp.md）` 写入投资看板（幂等：同 date 覆盖）。落库失败不阻断主流程，但汇报中明确提示「审查数据未落入看板，需补录」。

### 落库字段（完整明细见 `references/report-templates.md` §八）

| 字段 | 说明 |
|:---|:---|
| `c_groups` / `s_groups` | 内容审查（C3/C4/C6/C7/C8/C10）与结构审查（S 维度）分组；每项 `{title, severity, tag, headers, rows, text}` |
| `rows` | 推荐对象数组 `{列名: 值, 状态: pass\|fail\|warn}`（带状态列的表格必须显式给状态，看板据此渲染徽章） |
| `checks` | 脚本指标表；`recycle` 待回收处置（done/cooling/doneHist/rows）；`actions` 建议动作（num/text/status） |
| `meta` / `method` / `summary` / `mainProblems` | 范围与基线 / 方法 / 总结 / 主要问题（markdown 结构化，看板渲染小节与列表） |

> **修复动作不落库**：用户授权修复后另写 `修复记录-{YYYY-MM-DD}.md`（vault 执行日志），由审查 date 文字引用。

## Relative Files

| 场景 | 加载文件 | 内容 | 方式 |
|:---|:---|:---|:---|
| 审查时 | investment-framework/references/review-rules.md（由 investment-framework 编排者传入） | 审查维度和检查清单 | 读取 |
| 审查时 | investment-framework/references/footnote-taxonomy.md | 脚注类型定义、格式规范、添加阶段 | 读取 |
| 审查时 | references/report-templates.md | 审查落库 schema 模板（字段规范 + status 取值 + 落库调用规范 + 扩展机制） | 读取 |
| 结构审查预扫 | scripts/vault_review.py | 自动扫描脚本，输出 vault_review_result.json（只报告不修改） | **执行** |
| 段落布局预扫 | investment-framework/scripts/verify-format.py | 段落布局/脚注内联/模板废话/模板成分残留扫描（可 --fix 自动修复） | **执行** |
| 言论追踪审计（C10） | scripts/tracks_audit.py | 言论追踪专项审计：MySQL `statement` 视图数据质量（空正文/缺链接/缺 form/码值合法/P1 关联/留档覆盖率/复核积压）为准；vault 侧只扫**未删的历史画像 md**，不代表缺口（--mysql 需 DB_PASS 环境变量） | **执行** |

---

## Source Hierarchy

| 优先级 | 来源 |
|:---|:---|
| 1 | 编排者传入的参数 |
| 2 | 用户约定（审查维度定义） |
| 3 | Obsidian frontmatter 规范 |

---

## 自检

> **只列"无法自动化"的语义红线**。C1-C10 / S1-S8 / R1-R5 各步骤按上文流程执行即可，不必在此重述；frontmatter 字段/顺序/引号/wikilink/脚注格式/标签白名单/空原文链接等由 `investment-review/scripts/vault_review.py` 与 `investment-framework/scripts/verify-format.py` 作为门禁自动判定（改格式规则改脚本，不加人工清单）。

- [ ] **待决策按 kind 分流不越界（2026-09-27）**：提炼类条目（含 `verdict=delete` 清单）归 investment-refine，审查不碰——误清需退回并在报告说明；`kind=审查裁决` 的已裁决项由首步执行修复并 internalize，收尾把未决发现带 options/excerpt 上报（R7）。
- [ ] **只出报告、不改文件**；关联备注提案每条都有**内容层面的依据**（非编撰关系，承材料收集师"不造假"）。
- [ ] 归类正确性覆盖"博主层条目作者是否均在博主控制台登记、未登记者误挂需迁其他层"（#12/#38 反向校验，机器判之外的人工确认）。
