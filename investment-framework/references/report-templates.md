# 审查落库模板（data/review.json · schema）

> 由 审查环节 SKILL.md 的 Output Format 引用。**2026-08-16 起审查不再产出 md 报告文件**，改为直落库：审查完成后按本模板规定的结构化字段，`MCP 工具 `review_record`（REST POST /api/review/record 兼容，连接见 references/console-mcp.md）` 写入投资看板本地数据 `data/review.json`（看板唯一数据源）。
> 历史 md 报告（2026-08-09/14）不迁移、不回退依赖；看板仅在无落库记录时回退解析旧 md 兼容展示。

---

## 一、落库对象结构（单次审查 = 一条 record）

```json
{
  "date": "YYYY-MM-DD",
  "title": "审查报告 YYYY-MM-DD",
  "meta": {
    "审查范围": "博主/、其他/、宏观/（「我的」层按规则 #15 不审查不触碰）",
    "扫描文件数": "386 个（全库）",
    "工具": "vault_review.py 六维自动预扫 + verify-format.py 段落布局预扫 + 子代理深度内容审查",
    "对比基线": "2026-08-09",
    "原则": "只报告不修改（除 #26 授权的回收执行）"
  },
  "method": "审查方法简述（结构 S1-S8 + 内容 C3-C8 判定口径）",
  "mainProblems": "总体结论：结构强项 + 主要问题清单",
  "checks": [
    { "item": "wikilink_issues", "result": "13", "compare": "8/9 为 0（新增）", "status": "fail" },
    { "item": "no_fm", "result": "0", "compare": "—", "status": "pass" }
  ],
  "groups": [
    {
      "title": "归类错误（S2）",
      "severity": "fail",
      "tag": "S2",
      "headers": ["文件", "建议"],
      "rows": [["博主/景风长赢/景风长赢.md", "迁 `其他/` 层"]],
      "text": "叙述性补充（核心问题说明、决策依据），无表格时的兜底"
    }
  ],
  "recycle": {
    "done": "0", "cooling": "5", "doneHist": "1",
    "rows": [["其他/交易体系/马丁与反马丁策略", "2026-08-09", "5", "待回收（剩 2 天）", "冷静期内保留；1 处入链"]]
  },
  "actions": [
    { "num": "1", "text": "裁决 9 处框架内部矛盾", "status": "待用户裁决" }
  ],
  "summary": "总结与建议全文（含强项、建议动作说明）"
}
```

---

## 二、字段规范（必须遵守）

| 字段 | 必填 | 类型 | 说明 |
|:---|:---|:---|:---|
| `date` | 必填 | string | 审查日期 `YYYY-MM-DD`；**幂等覆盖**——同日期重复审查覆盖旧记录 |
| `title` | 必填 | string | 报告标题 `审查报告 {YYYY-MM-DD}` |
| `meta` | 建议 | object | 审查范围/扫描文件数/工具/对比基线/原则（key-value） |
| `method` | 建议 | string | 审查方法简述 |
| `mainProblems` | 建议 | string | 总体结论（结构强项 + 主要问题） |
| `checks` | 必填 | array | **脚本指标检查项**（vault_review.py 输出表逐行）：每项 `{item, result, compare, status}` |
| `groups` | 必填 | array | **通用审查分组**：结构问题 S 小节 + 内容审查 C 小节 + **未来任意新审查项**。每项 `{title, severity, tag, headers, rows, text}` |
| `recycle` | 建议 | object | 待回收处置：`{done, cooling, doneHist, rows[]}`（rows = 条目/标记日期/冷静天数/处置/理由） |
| `actions` | 建议 | array | 建议动作表：`{num, text, status}` |
| `summary` | 建议 | string | 总结与建议 |

---

## 三、`status` / `severity` 取值规范

| 值 | 含义 | 看板徽章 |
|:---|:---|:---|
| `pass` | 合规（result 存纯数值如 `0`，勿带徽章） | PASS 绿 |
| `warn` | 注意（需关注，可修复） | WARN 黄 |
| `fail` | 严重（必须处理，可 AI 修复） | FAIL 红 |
| ~~`info`~~ | **非法**：dict_check_status 无此码，落库会外键报错 | — |

`groups[].severity` 独立于 `checks[].status`：分组级别用 `fail/warn/info`（从分组标题/严重度推断）；**检查项级别只能传 `pass/warn/fail`**（字典 dict_check_status 合法码，2026-08-31 实测；`ok` 为旧数据遗留、新写入不用）。落库报外键错误时按 refine-schema.md 二B 字典表核对码值。

---

## 四、落库调用规范

1. **端点**：`MCP 工具 `review_record`（REST POST /api/review/record 兼容，连接见 references/console-mcp.md）`，`Content-Type: application/json`
2. **请求体**：上述完整 record 对象
3. **校验**：服务端校验 `date/title/checks/groups` 必填；失败返回 `{ok:false, error}`，**必须补全后重试**，禁止跳过落库
4. **幂等**：同 `date` 覆盖写入（自动处理）
5. **失败处理**：看板未启动/网络异常 → 落库失败不阻断审查主流程，但汇报中明确提示「审查数据未落入看板，需补录」
6. **写入时机**：审查全部完成（含回收处置计算）后一次性提交，不中途分批

---

## 五、审查项扩展机制（未来新审查项）

- **任何新的审查维度** = `groups` 数组新增一条：`{title, severity, tag, headers, rows, text}`
- **看板零代码适配**：前端按 `groups` 通用渲染（有 `headers+rows` 出表格、有 `text` 出文本卡），新增分组自动展示
- **检查项新增** = `checks` 数组新增一行（vault_review.py 新指标自动纳入）
- 禁止：修改 schema 结构（新增字段需与看板协商）、跳过落库、用 md 替代

---

## 六、`groups[].tag` 分区硬约束（2026-09-07 实测补录）

看板前端按 `tag` 把分组拆成「结构问题 / 内容审查」两个大区：`isStructG = g.tag === 'S' || 标题含「结构」`——**精确匹配单字母 `S`**。

- **结构类分组**（归类错误 / 段落缺失 / 脚注格式 / 空链接行 / 重复标题等）→ `tag: "S"`（子维度编号如 S2/S6 只写进 `title`，如 `归类错误（S2）`）
- **内容类分组**（C3 一致性 / C4 知行合一 / C6 经验验证 / C7 关联 / C8 复核等）→ `tag: "C"`
- ⚠️ **禁止**在 `tag` 落 `"S2"`/`"C6"` 等带编号的值——看板 `isStructG` 只认 `tag === 'S'`，落 `"S2"` 会被 `!isStructG` 误分进「内容审查」区（结构问题混入内容区，2026-09-07 落库实测踩坑，已修正后重落）
- 分组 `title` 内可保留子编号便于人读，但**分区判定只看 tag**，两处互不替代

> 连带约束：`rows` 用对象数组时，需显式 `状态` 列（`pass/fail/warn`）才会渲染徽章；C7 关系列固定最右（看板 `isC7` 检测到「关系」+「说明」列自动把关系列移到最右，`headers` 顺序建议直接按 `条目/关联目标/说明/关系` 排好）。

---

## 七、修复记录（仍为 md 执行日志）

**修复动作不落库**：用户授权修复后仍写 `修复记录-{YYYY-MM-DD}.md`（执行日志，vault `工作区/审查报告/` 目录），记录修复动作与结果。对应审查通过 `date` 关联（不再有审查报告 md 可 wikilink，改用「对应审查：2026-08-14」文字引用）。

---

## 八、审查落库字段明细（自 SKILL.md Output Format 下沉 · 2026-09-12）

### 内容审查（C 维度）

| 字段 | 类型 | 说明 |
|:---|:---|:---|
| c_groups | array | 内容审查分组（C3 内部一致性 / C4 知行合一 / C6 经验验证 / C8 编撰脚注 / C7 关联提案 / C10 言论追踪审计），每项 `{title, severity, tag, headers, rows, text}` 入 `groups` 数组 |

**rows 推荐对象数组（看板 v0.12.61+ 支持，直观性最佳）**：每行 `{列名: 值, 状态: pass|fail|warn}`，列名与 headers 对应；`状态` 显式给出（C3 内部一致性、C4 知行合一、C6 经验验证等需状态列的表格必须带），供看板直接渲染状态徽章，不再靠结果文本推断：
```json
{ "title": "C3 内部一致性抽查", "severity": "ok", "tag": "C3",
  "headers": ["检查项", "结果"],
  "rows": [
    { "检查项": "HIS1963 新建4条 vs 既有", "结果": "认知演进自洽，非矛盾", "状态": "pass" },
    { "检查项": "永不补仓 vs 马丁反马丁", "结果": "跨作者相反指令，需裁决", "状态": "fail" }
  ] }
```
关联备注（C7）关系列值用统一词：`对立/冲突/互补/补充/增强/数据`（看板按词着色：对立粉紫、冲突红、互补绿、补充/增强蓝、数据灰）。headers 可由对象 keys 推导（可省略，但建议保留便于列序）。

**rows 中 wikilink 字段值**（含"条目/关联目标/博主/条目A/条目B/目标/来源"等列名的值）应为**真实存在的完整 vault 相对路径**（如 `博主/逻辑拐点/逻辑拐点.md`），含 `.md` 后缀、含目录前缀；**禁止描述性命名**（如"逻辑拐点画像·黄酒观点"——这种不可点击、不可验证，是 v0.12.65 前的旧妥协，看板 v0.12.69+ 已通过 linkReal 兜底但根因是审查方送的数据不精确）。去除 `[[]]` 包裹。

`/api/review/list` 会自动扫描每条 wikilink 在 vault 的存在性并注入 `linkExists: {path: true|false}` + `linkReal: {v: realRel}` 两份映射（v0.12.68/69 智能解析：①精确路径 ②补 .md ③index basename ④「博主名画像·主题」→博主画像 ⑤「博主名《标题》」→博主名下同名文件）。看板按 linkReal 优先展示《原文件名.md》+ 跳真实文件，缺失（linkReal==null）才走 linkExists 删除线+tooltip"待生成，建议由博主画像 skill 补全"。

**server 扫描必须兼容两种 rows 形态**（v0.12.73 教训：C6 经验验证用对象数组 rows 时被 `Array.isArray(row)` 过滤跳过，linkReal 无映射 → 看板误判"已删除"）：`Array.isArray(row) ? row[ci] : row[headers[ci]]` 取值。凡改 server 扫描逻辑，必须用「字符串数组 + 对象数组」两种 rows 各测一遍。

**"状态"列渲染契约**（v0.12.74）：rows 显式 `状态` 字段（headers 自带"状态"列或对象键 `status`）看板渲染为徽章（PASS 绿/FAIL 红/WARN 橙），**不允许落库纯文本 `warn` 之外的变体**；状态值统一 `pass/fail/warn`。C6 经验验证"待跟踪"预测语义 = warn（未验证/未证伪），待后续数据验证后更新 pass/fail。

**C7 headers 顺序固定**：`["条目", "关联目标", "说明", "关系"]`——关系列固定最右（与检查清单"状态"列右对齐，视觉统一），说明列位于关联目标与关系之间、宽度最大、承载完整理由。

**"说明"列必须写完整理由**（v0.12.69 用户反馈："显示尽可能完整"）：不少于一句完整话，包含**双方观点对比**（谁主张什么）与**关系判定**（为什么是对立/互补），便于后人无需读原文也能判断关联合理性。示例（合格）：`"逻辑拐点认为黄酒无高端土壤、炒炒别当真，与metalslime看多对立"`。反例（过短，不合格）：`"阿兰模型层视角，metalslime硬件层视角"`——只描述视角不说冲突点、无法裁决。

**审查脚本生成关联备注前必须校验目标文件已存在**：用 `getIndex().files` 查真实 rel；若未生成则**降级为"待补全关联"分组**（tag=`C7-pending`）或在说明里加 `[待生成]` 标记，**避免看板出现"建议建关系但目标不存在"的违和感**。

### 结构审查（S 维度）

| 字段 | 类型 | 说明 |
|:---|:---|:---|
| s_groups | array | 结构审查分组（S2 归类错误 / S5 失效 Wikilink / S6 脚注格式 / S7 标签 / 模板段落缺失 / 其他结构问题），每项 `{title, severity, tag, headers, rows, text}` 入 `groups` 数组 |
| checks | array | 脚本指标（vault_review.py 输出表逐行）：`{item, result, compare, status}`，**status 统一落 `pass/fail/warn`**（勿落 ok/good/error/warning 等变体；看板接口已做兼容归一，但源头统一最干净）。**`item` 落库用中文**（v0.12.71 用户要求：检查项显示中文；审查脚本英文键 → 落库前按下方对照表映射中文），看板 CHECK_LABELS 亦维护全量英文键→中文映射兜底历史数据。**新增检查项时**：①脚本英文键同步进看板 CHECK_LABELS；②落库 item 用中文。**result/compare 只存纯数据，禁 emoji 徽章**（对勾/警示/叉号等符号）：result 只落数值/文本（如 `0`、`5`），状态图标由看板按 status_code 用 SVG 渲染，禁止把展示样式写进数据（2026-08-31 用户要求，违者视为数据污染）。 |

**检查项中文对照表**（英文键 → 落库中文）：

| 英文键 | 中文 | 英文键 | 中文 |
|:---|:---|:---|:---|
| `no_fm / fm_error` | frontmatter 缺失/格式错误 | `recycle_invalid` | 回收标记无效 |
| `missing_fields` | frontmatter 字段缺失 | `quoting` | 引号规范 |
| `field_order` | frontmatter 字段顺序 | `footnote_links_workspace` | 脚注链接指向工作区 |
| `missing_core_sections` | 模板核心段落缺失 | `legacy_footnote_heading` | 旧式脚注标题 |
| `wikilink_issues` | wikilink 失效 | `forbidden_source_section` | 禁用 ## 来源 段 |
| `tag_issues` | 标签不匹配 | `source_as_invalid` | source 形态非法 |
| `blogger_not_registered` | 博主未登记 | `stray_date` | 游离日期字段 |
| `stock_code_missing` | 股票代码缺失 | `unclassified` | 未归类文件 |
| `recycle_pending` | 待回收条目 | `junk_files` | 空壳文件 |
| `recycle_expired` | 回收过期条目 | `macro_template_mismatch` | 宏观模板不匹配 |
| `verify-format 段落布局` | 段落布局 | | |

> 画像 md 已废弃（#36），原 `blogger_has_source`/`blogger_has_platform_id`/`blogger_empty_link_row`/`blogger_table_no_link_col`/`info_cutoff_mismatch` 五项画像副本检查已随 `vault_review.py` 下线。

### 总结与主要问题（结构化，便于看板直观展示）

`summary` 与 `mainProblems` 建议用结构化 markdown（看板渲染为小节标题 + 圆点列表 + 段落）：
- `###` 分小节（看板渲染为主题色小节标题）
- `- ` 无序列表 / `1. ` 有序列表（看板渲染为圆点列表）
- 普通段落一行一句，避免超长无断句段落

### 落库 record（组装规则）

| 字段 | 来源 |
|:---|:---|
| date / title | 审查日期与标题 |
| meta | 审查范围/扫描文件数/工具/对比基线/原则 |
| method | 审查方法简述 |
| mainProblems | 总体结论 |
| checks | 脚本指标表 |
| groups | **s_groups + c_groups 合并**（通用分组，看板自动渲染） |
| recycle | 待回收处置（done/cooling/doneHist/rows） |
| actions | 建议动作表（num/text/status） |
| summary | 总结与建议 |

> 落库 schema 见 `references/report-templates.md`。**修复动作不落库**：用户授权修复后另写 `修复记录-{YYYY-MM-DD}.md`（vault 执行日志），对应审查用 date 文字引用。
