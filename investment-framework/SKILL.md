---
name: investment-framework
description: >
  投资知识框架全局编排者。管理三大归属层（我的/博主/其他）+ 六大分类（分析框架/交易体系/投资心态/投资心得/个股/行业）+ 宏观。
  定义流水线（粗制品→粗加工→原始资源→提炼→审查）、模板表、路径表、全局规则、审查机制。
  触发词：「投资框架」「框架全貌」「pipeline」「编排」。
  排除条件（业务环节词交执行器，本 skill 不抢）：「粗加工」「归档」「整理帖子」→ coarse-processor；「提炼」→ refine；「审查」「review」「健康度」→ review；「采集」「抓取」→ xueqiu-spyder；「预测」「预测控制台」「记录预测」→ prediction-console。
  区别于 xueqiu-spyder（帖子采集，编排+工具一体）：本 skill 是路径和模板的唯一持有者，负责串联全部加工模块。
license: MIT
agent_created: true
metadata:
  version: "3.0.0"
  short-description: 投资知识框架全局编排者
compatibility: 通用
---

# 投资知识框架 · 全局编排者

唯一持有路径、模板、流水线规则的地方。

---

## Default Stance

### 核心原则

- **单点配置**：所有路径、模板、规则只在此定义。变更 vault 目录时只改 VAULT_ROOT 一行。
- **触发词仲裁（用户拍板）**：`粗加工`/`提炼`/`归档`/`审查`/`review` 这些词**同时出现在本编排者与三个执行器（investment-coarse-processor / investment-refine / investment-review）的 description 里**，是有意为之。仲裁规则：**命中同义触发词时先由本编排者判路由，再交对应执行器执行**（见下方「路由表」）；执行器被单独加载时不自行串联其他环节（三者 description 均写明「由 investment-framework 编排调用，不独立触发」）。跨环节的动作（如 `investment-refine` 开工前置的「清待决策队列」）也必须由编排者串起来。
- **三层归属 + 宏观双层**：我的（用户自管）/ 博主（已登记）/ 其他（未登记投资人）。宏观分两层：顶层 `宏观/` 存通用框架，归属层下 `宏观/` 存该来源的具体分析。
- **提炼直接执行**：提炼 skill 读取原文后直接分析、创建条目、汇报结果，不需用户逐步确认。一篇帖子可拆为多条框架条目。
- **跨层靠标签**：同一标的在三层都有时，通过 frontmatter 标签检索，不靠文件结构。
- **按需创建**：任何层级目录都不预建空文件夹，有内容写入时才创建；发现空文件夹应清理（「我的」层由用户自管，不主动删）。

### 禁止行为

- 绝不修改 Obsidian vault 外的文件
- 绝不预建或保留任何层级的空文件夹——有内容写入时再建
- 绝不将非投资相关内容放入"其他"层——直接丢弃
- 绝不在"我的"层创建或修改文件——由用户自己管理
- 绝不丢弃有借鉴意义的内容
- 绝不在提炼时遗漏有借鉴意义的内容

---

## Workflow

### 路由表

| 用户意图 | 触发词 | 调用链 |
|:---|:---|:---|
| **雪球帖子集提炼（主路径）** | 提炼、帖子集提炼、采集后提炼 | investment-refine（原文直取 `post_history` 库内，#29，不经粗加工） |
| 截图/链接直投 | （用户发送雪球截图+链接） | #30 直投路径：粗制品(临时) → 提炼 → 删源文件 |
| 非雪球来源全流程 | 粗加工、归档、全流程 | investment-coarse-processor → investment-refine |
| 仅粗加工（非雪球来源） | 粗加工、归档 | investment-coarse-processor |
| 仅提炼（vault 常规路径） | 提炼 | investment-refine（前置：原始资源已存在该文档且 `status=待提炼`；否则先从粗制品粗加工） |
| 审查 | 审查、review、健康度 | investment-review |
| 预测控制台 | 预测、预测记录、预测控制台 | prediction-console（独立 skill，不经本编排者串联） |
| 查看全貌 | 投资框架、框架全貌、pipeline | 输出框架说明 |

### 提炼输入锚点判定

提炼的输入按序判定锚点，**禁止直接在 `工作区/粗制品/` 上提炼**：

1. **雪球帖子集 / 用户直投截图+链接** → **#29/#30 直提**：原文取 `post_history` 库内（#29）或临时粗制品（#30，提炼后删源文件），跳过粗加工与原始资源，不读不产生 vault 文件；言论/买卖/预测 → `blogger_statement`/`blogger_trade` 落库（画像单轨，不写画像 md），有框架价值 → 同时产出 wiki 条目，二者可兼得。
2. **原始资源已存在该文档且 `status=待提炼`** → 直接 investment-refine。
3. **只在 `工作区/粗制品/`（非雪球来源）** → 先 investment-coarse-processor 粗加工（移入原始资源并置 `status=待提炼`），再提炼——**不要跳过粗加工**。
4. 用户说「粗加工+提炼」「全流程」「归档」→ 自然走「粗加工 → 提炼」串联，无需判断。

### 粗加工 → investment-coarse-processor

调用 `investment-coarse-processor`，传入 `{ source_path, target_dir, blogger_console_path }`。该 skill 负责整理格式、去广告、补全 metadata 并移入原始资源目录。**硬约束：绝不修改/精简/重组正文内容**——正文（含图片引用、转录稿段落、重复内容）原封不动保留，只动 frontmatter 和末尾工具广告。

### 提炼 → investment-refine（直接执行）

**开工前置**：清待决策队列（refine 第一步 1.1）→ 取原文（refine 第一步 1.2）。
**分析**：读取原文全文（常规：原始资源文件；**帖子集：`post_history` 库内原文**，MCP `post_history` `action=get`/`check`），判断归属层、分类、标签、库内关系（refine 第二步）。
**创建条目**：按分析结果直接创建框架条目文件；涉及已登记博主 → 言论按分流矩阵落库 `statement` 六表（画像单轨）；涉及宏观事件 → 创建/更新宏观文件（refine 第三步）。
**汇报 + 收尾**：向用户报告产出条目；常规路径把源文件 status 改为 `已提炼`（帖子集 #29 无源文件，以 `statement.source_url` 反查确认）；提炼产物落各自存储（言论六表 / wiki 条目），**提炼步骤落库已下线**（#54，用户异议走待决策队列）。

### 审查 → investment-review

**第零步（复核首步 · 必做）**：处理用户在「言论追踪」左滑写入的复核建议——见 review-rules.md「复核建议处理（审查首步）」（list open → blogger_statement 修正 → apply/delete）
**第一步**：确定审查范围（内容审查 or 结构审查，见 references/review-rules.md）
**第二步**：内容审查——检查框架一致性、知行合一、我的 vs 博主冲突、经验验证
**第三步**：结构审查——检查归类正确性、frontmatter 完整性、wikilink 有效性、标签匹配
**第四步**：组装结构化落库数据（按 investment-review/references/report-templates.md 的 schema），`MCP 工具 `review_record`（REST POST /api/review/record 兼容，连接见 investment-framework/references/console-mcp.md）` 写入投资看板（2026-08-16 起不再产出 md 审查报告）
**第五步（待回收处置 · 默认执行）**：审查扫描全部内容型条目的 `delete` 字段（见 framework-rules #26），按 7 天冷静期处置超期条目（真删 + 双向清理）并出「待回收处置」数据（入落库 recycle 字段）给出理由；未到期条目在数据中提示剩余天数
**第六步（规律固化 · 收尾必做）**：按 review-rules.md「规律固化（审查收尾步）」把全部发现二分一次性/可泛化，可泛化的当场回写对应规范（含操作门脚本加校验项），报告写明固化清单——未执行不得结束审查

### 操作门（事前校验）

原则：**问题在产生当天拦截，不等每周审查**——每个流水线操作在出口必须过校验门，审查降级为兜底网（教训：悬空引用/info_cutoff/模板段落缺失全部机器可检，却积压 7 周至审查才暴露）。

| 操作 | 出口校验门 | 工具 |
|:---|:---|:---|
| 采集/同步后（xueqiu-spyder 前置步骤） | 控制台-画像 info_cutoff 一致 + 博主层残留检测 | execution-guide「前置步骤」 |
| 提炼后（refine 第三步收尾） | 段落布局/模板段落完整 0 问题 | scripts/verify-format.py |
| **删除/回收/移动前**（#25/#26） | inbound 引用反查，清理完才允许删 | scripts/check_inbound.py |
| 任意批量操作后 / 提交前 | 增量扫描 git 变更文件（秒级） | investment-review/scripts/vault_review.py --incremental |
| **改 framework-rules 条目号 / 删改全局规则后** | 全家 `framework-rules #N` 引用有效性（悬空即拦） | scripts/check-rule-refs.py |

每周审查仍保留：内容层（C3 一致性 / C4 知行合一 / C6 经验验证 / C7 关联备注）+ 待回收处置，是操作门覆盖不到的兜底网。

### 看板联动（investment-dashboard）

完整清单见 `references/console-guide.md` §9（`review_record` 审查落库、`pending_decision` 待决策、`console_statement_review` 言论复核建议、`post_history` 原文库、`console_*` 预测控制台等落库、产物展示与失败处理；**提炼链路 `refine_trace`/`refine_review` 已于 2026-09-26 随步骤落库下线删除**，#54）。提炼/审查执行器各自负责落库调用，编排者只在汇报中核对「看板数据未写入」提示。

---

## 动态上下文（运行时注入）

执行任何流水线步骤前，编排者**必须**先获取以下环境信息（不靠记忆、不靠脑补）：

| 信息 | 获取方式 | 用途 |
|:---|:---|:---|
| 当前日期 | `date "+%Y-%m-%d"` | 框架条目 `updateDate`、审查冷静天数计算（review R2）、看板 blogger「信息截止」更新（xueqiu-spyder 第八步） |
| 当前时间 | `date "+%Y-%m-%d %H:%M"` | 雪球采集时间窗口基准（xueqiu-spyder 第五步） |
| 待提炼文档状态 | 查询原始资源 frontmatter `status` | 判定走粗加工 or 直接提炼（提炼输入锚点判定） |

> 各执行 skill 在需要时自行获取（如 refine 写 `updateDate` 前、review 算冷静天数前、xueqiu-spyder 时间窗口前），编排者不代为传递时间戳。

---

## 路径表

| 常量 | 值 | 说明 |
|:---|:---|:---|
| VAULT_ROOT | /Users/jianglb/Library/Mobile Documents/iCloud~md~obsidian/Documents/投资知识库 | vault 根目录 |
| MY_DIR | {VAULT_ROOT}/我的 | 用户自管层 |
| BLOGGER_DIR | {VAULT_ROOT}/博主 | 已登记博主层 |
| OTHER_DIR | {VAULT_ROOT}/其他 | 未登记投资人层 |
| MACRO_DIR | {VAULT_ROOT}/宏观 | 通用宏观框架与分析工具 |
| MACRO_BLOGGER | {BLOGGER_DIR}/{博主名}/宏观 | 该博主对具体宏观事件的分析 |
| MACRO_OTHER | {OTHER_DIR}/宏观 | 未登记投资人对具体宏观事件的分析 |
| ROUGH_DIR | {VAULT_ROOT}/工作区/粗制品 | 粗制品暂存（雪球帖子集**不**落这里：采集直落 post_history，见 #29/#41；非雪球来源暂存于此） |
| RAW_DIR | {VAULT_ROOT}/工作区/原始资源 | 粗加工后原始资源 |
| 原文库 post_history | 看板 MySQL `post_history` 表（**采集落点 + 提炼前原文；只存帖子必要信息，不存提炼产物**）。写：`~/Project/investment-dashboard/src/scripts/import-post-history.js [--rm] <采集产物.md>`（批量；--rm 落库后清临时产物）或 `MCP post_history action=upsert`；读：`MCP post_history action=get/check` | 提炼的原文来源、回顾/重新提炼先查这里（规则 #41） |
| 本地看板启动器 | `~/Project/investment-dashboard/src/scripts/run-server.sh`（launchd `com.investment-dashboard` 的 ProgramArguments 指向它；自愈 node 路径） | 看板 8698 启动/排障（详见 references/console-guide.md §8.5） |
| 博主控制台 | 看板 MySQL `blogger` 表（读 GET /api/bloggers/live、写 POST /api/bloggers 与 /api/bloggers/update；vault 工作区/博主控制台.md 已退役删除） | 博主注册权威（编号/别名/雪球ID/平台/特别关注/信息截止） |

---

## 模板路径表

9 个模板（分析框架-方法论 / 分析框架-分析档案 / 交易体系 / 投资心态 / 投资心得 / 宏观 / 行业 / 个股 / 博主）的 assets 路径与用途见 `references/template-guide.md`「模板路径表」。模板=纯结构骨架（字段+section 标题+表头）不含解释；各 section 写作指引同在 template-guide.md，产出文件可保留空结构但不得有模板解释残留（verify-format.py 检测）。

---

## Output Format

编排者自身不产出文件，输出为对下游模块的调度结果与汇报：

| 字段 | 类型 | 说明 |
|:---|:---|:---|
| routed_skill | string | 本次调度的下游 skill（coarse-processor / refine / review） |
| output_summary | string | 下游执行结果摘要（产出条目数、更新档案等） |
| next_action | string | 后续动作提示（如"源文件待提炼"） |

下游具体产出物（粗加工原始帖子、框架条目、审查落库数据）由各 skill 的 Output Format 定义，位置见下方路径表。

---

## Relative Files

| 场景 | 加载文件 | 内容 | 方式 |
|:---|:---|:---|:---|
| 始终 | references/framework-rules.md | 框架边界规则、全局规则 | 读取 |
| 提炼 | references/template-guide.md | 各模板 section 写作指引（模板为纯结构骨架，写作要求统一在此） | 读取 |
| 提炼 | references/tag-taxonomy.md | 标签分类体系 + 编排派发规则 | 读取 |
| 提炼/审查 | references/footnote-taxonomy.md | 脚注类型定义、格式规范、添加阶段 | 读取 |
| 提炼 | assets/{模板名}.md | 对应分类的模板（纯结构骨架） | 读取 |
| 审查 | references/review-rules.md | 审查维度和检查清单 | 读取 |
| 看板联动 | references/console-guide.md | 看板数据契约（`review_record`/`pending_decision`/`post_history` 等落库）、产物展示约定、前端设计铁律（§3/§4 是已退役的 `refine_record`/决策链路图 v2，仅作历史查阅） | 读取 |
| 审查（段落布局） | scripts/verify-format.py | 段落布局/脚注内联/模板废话/模板成分残留（空表格行/来源blockquote/frontmatter注释/花括号占位）扫描（可 --fix 自动修复）。**纯标准库无第三方依赖** | **执行** |
| 删除/回收/移动前（#25/#26） | scripts/check_inbound.py | inbound 引用反查（wikilink/脚注/source 字段），双向清理范围确认工具 | **执行** |
| 改 framework-rules 编号后 | scripts/check-rule-refs.py | 全家 SKILL.md/references 的 `framework-rules #N` 引用校验（悬空即报错，退出码 1） | **执行** |

---

## Source Hierarchy

| 优先级 | 来源 |
|:---|:---|
| 1 | 用户显式约定（三层归属、六大分类、博主控制台范围、标签检索） |
| 2 | Obsidian 规范（wikilink 完整路径、frontmatter 字段类型） |
| 3 | 投资研究最佳实践（方法论+分析档案分离、结果跟踪闭环） |
| 4 | 工程实践验证（单点配置、按需创建、直接执行） |

---

## 自检

- [ ] 用户意图是否命中路由表、走对执行 skill？（含主路径 #29 帖子集、例外 #30 截图直投）
- [ ] 所有路径是否来自路径表（非硬编码）？
- [ ] 待提炼文档是否满足前置条件？（按「提炼输入锚点判定」走；**帖子集例外：原文取自 `post_history` 库内**，不读 vault 文件）
- [ ] **操作门是否已过**？——删除/回收/移动前已运行 `check_inbound.py` 反查并清理引用；批量操作后已运行 `vault_review.py --incremental` 增量校验
- [ ] 待回收处置是否严格按 #26（用户加 `delete` 字段标记、7 天冷静期、超期真删+双向清理+理由报告），Agent 不替用户标记、不 shortcut？**粗制品例外**：跳过冷静期即时可回收
- [ ] 看板是否已联动（审查 `review_record`；提炼产物落各自存储，#54 后无步骤落库）？API 失败时是否汇报「看板数据未写入」？（契约见 references/console-guide.md §9）

> 提炼/粗加工/审查的**执行层**自检（标签体系、归属层、模板完整、字段规范等）在各执行 skill 的自检节，编排者不重复。
