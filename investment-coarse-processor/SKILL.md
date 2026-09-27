---
name: investment-coarse-processor
description: >
  投资框架粗加工执行器（非雪球来源）。读取粗制品 → 整理格式、去广告 → 补全 metadata → 移入原始资源。
  不生成提炼预览表，拆分决策由提炼环节负责。
  触发词：「粗加工」「归档」「整理帖子」。
  环节词归执行器所有（investment-framework 已让出粗加工/归档/提炼/审查这几组词），用户直接说即命中本 skill；
  整链编排走 investment-framework。雪球帖子集不走本 skill（#29 原文直取 post_history）；
  区别于 investment-refine（提炼）与 investment-review（审查）。
license: MIT
agent_created: true
metadata:
  version: "2.5.0"
  short-description: 投资框架粗加工执行器（非雪球来源）
compatibility: 通用

---

# 粗加工执行器

---

## Default Stance

### 核心原则

- **只做粗加工，不做提炼**：输出是整理后的原始资源，不创建框架条目，不生成提炼预览表
- **拆分决策留给提炼环节**：粗加工只负责整理格式和补全 metadata，不判断内容应归入哪个分类
- **繁体转简体**：粗制品正文若为繁体中文，整理时必须转为简体中文入库；仅做繁简字形转换，保留原文用词、语气、比喻、案例，不改写内容
- **博主判断依赖看板博主表**：对照**看板 MySQL `blogger` 表**（`GET /api/bloggers/live` 或 MCP `list_bloggers`）判断是否为已登记博主（`工作区/博主控制台.md` 已于 2026-09-07 退役删除，**不要再找该文件**）
- **参数全部由编排者传入**：缺参即报错，不硬编码路径

### 禁止行为

- **绝不修改、精简、重组、删节正文内容**：正文必须原封不动保留，包括图片/附件引用（`![[...]]`）、全文整理/转录稿段落、看似重复的内容、口语化表达。粗加工只做两件事：① 替换/补全 frontmatter；② 去掉文件末尾明显的工具广告（如"AI整理设置点此调整"、积分余额、反馈链接）
  - **作用域例外（2026-09-03 用户规则《博主言论设计》§采集2）**：雪球博主**言论采集**链路必须产出纯文本——删除图片与图片链接（`![[...]]`、`![](url)`、`<img>`、裸图片 URL）及表情（含 `[表情名]` 占位，不留占位），并剥离 `发布于…／来自…／关注` 等页脚噪声。本条"原封不动"对**非言论**链路（视频转录、长文研究、其他来源）继续全额生效。两种链路都不得改写内容语义。
- **绝不给原始资源添加方法论/内容性质标签**（基本面/价值投资/教训复盘/投资理念/交易系统/心态/仓位管理 等）——粗加工产出原始资源时 `tags: []` 留空，标签由提炼环节按 `tag-taxonomy.md` 选定分类标签（2026-08-08 用户纠正：基本面/价值投资/教训复盘/投资理念 等方法论标签属违规）
- **绝不输出模板残留成分**（footnote-taxonomy 禁止行为 #5 · 2026-08-08 用户纠正）：模板为纯结构骨架（字段 + section 标题 + 表格表头，不含任何解释），产出文件不得含 `{...}` 花括号占位、`> 指引 blockquote`（如"原文链接必填真实雪球帖子 URL"、"示例：淘宝闪购单量追上美团"）、frontmatter 行内注释、全空表格占位行——轻创建博主目录时解释成分全部剔除（画像 md 已退役入 blogger 表）；各 section 写作指引见 references/template-guide.md
- **绝不输出过程说明类 blockquote**（2026-08-08 用户纠正）：如"素材依据：已提炼 N 条框架条目"、"2026-08-04 提炼 4 条框架条目"等提炼过程记录**不进入产物正文**——这些是给 Agent 的工作备注，读者不需要；产物只保留内容本身
- 绝不生成提炼预览表或任何提炼方案
- 绝不跳过格式整理直接移动文件
- 绝不修改"我的"层的任何文件
- 绝不给原始资源文件预填框架级标签

---

## Workflow

**第一步**：读取参数 `{ source_path, target_dir, blogger_console_path }`
**第二步**：读取 source_path 文件内容
**第三步**：整理格式——去除多余空行、统一标题层级、修复编码；**若正文为繁体中文，转为简体中文**（仅字形转换，不改用词、语气、比喻、案例）
**第四步**：去广告——移除推广内容、社交媒体分享按钮文本、无关的页脚。特别注意 AI 整理工具（笔记同步助手等）产出的固定模式：
- **尾部广告**：`视频时长 X分X秒 · 消耗 N 积分 · 积分余额 N`、`重复分享 · 本次未扣积分…`、`AI整理设置可以[点此调整](...)`、`内容效果不满意？[点此反馈](...)` 等固定尾部，整段删除
- **只清尾部，防误切正文**：广告标记若在**正文中部**也出现（分段采集的页脚残留），只处理**最后一个**广告块；去完后校验剩余正文中文字符数 ≈ 字节数/3，与去前差值明显不符即说明切多了，回滚重切
**第五步**：补全 frontmatter metadata：

```yaml
---
title: "{帖子标题}"
source: "{见下方 source 字段规则：优先提取 url 字段真实链接}"
author: "{作者名}"
date: {YYYY-MM-DD}
type: "帖子/长文/链接/视频整理"
status: "待提炼"
tags: []
---
```

**第五步B（source 字段规则 · 重要）**：粗加工产出的**原始资源**的 source 必须携带**真实来源链接**，禁止用渠道名/批次名占位（framework-rules #23）：
- **优先提取粗制品 frontmatter 的 `url` 字段**（笔记同步助手/插件写入的原始链接，如 `url: https://v.douyin.com/xxx/`、`url: https://xueqiu.com/xxx`）——**这是本步的默认动作**，粗加工时先检查粗制品 frontmatter 是否有 `url` 字段，有则提取为 source；
- 外部来源（抖音/雪球/公众号等）→ markdown 链接格式：`"[{标题}（{作者} {YYYY-MM-DD}）]({真实URL})"`；
- 粗制品 `url` 字段缺失时：尽量从正文/尾部广告残留中提取真实链接；仍无 → source 留空数组 `[]`，并在汇报中标注「来源链接缺失待补」；
- **禁止**：`source: "AI整理 - 抖音"`、`"雪球长文"`、`"AI整理 - 小红书"` 等渠道名/批次名占位（2026-08-08 修正：此前粗加工丢弃了 url 字段导致 6 处 source 非法，根因即本条）

> **source 链式引用约定（2026-08-08 用户确认）**：wiki 文件（框架条目）的 `source` 指向原始资源 wikilink `[[工作区/原始资源/{文件名}]]`；原始资源的 `source` 再指向真实 URL。两级链式溯源，wiki 层不直接写 URL。**唯一例外**：博主言论直接提炼（#29 帖子集路径，无原始资源中间层）时，框架条目 `source` 可直接指向 URL。此约定由提炼环节执行，粗加工只负责把原始资源这级写对。

**第六步**：对照 blogger_console_path，判断 author 是否为已登记博主
**第七步**：将文件从 source_path 移动到 target_dir
**第八步**：自检

---

## Output Format

| 字段 | 类型 | 说明 |
|:---|:---|:---|
| output_path | string | 移动后的文件路径 |
| has_blogger | boolean | 是否涉及已登记博主 |
| blogger_name | string/null | 涉及的博主名 |

---

## Relative Files

| 场景 | 加载文件 | 内容 | 方式 |
|:---|:---|:---|:---|
| 补全 frontmatter 时 | references/frontmatter-rules.md | 7 字段规范、引号嵌套规则、tags block list 格式 | 读取 |

其余路径和规则由编排者传入。

---

## Source Hierarchy

| 优先级 | 来源 |
|:---|:---|
| 1 | 编排者传入的参数（路径、控制台） |
| 2 | 用户约定（frontmatter 字段规范） |
| 3 | Obsidian frontmatter 规范 |

---

## YAML Frontmatter 规范

原始资源 frontmatter **仅 7 个字段**：title、source、author、date、type、status、tags（字段顺序 `title → source → author → date → type → status → tags`），不添加 layer/category 等字段。**`id` 字段豁免**：若粗制品/原始资源 frontmatter 含 `id`（如 `id: docid_xxx_e`，Visit History 等 Obsidian 插件写入的追踪 ID），**保留不动、不删除、不移动、不报错**——它不属于框架字段集（见 framework-rules #27「id 字段豁免」）。引号嵌套规则（含双引号用单引号包裹等）、tags block list 格式等完整规范见 `references/frontmatter-rules.md`（权威源 framework-rules #27/#21）——执行时**必须加载**，不自造格式。

---

## 自检

- [ ] 若源文为繁体中文，正文是否已转为简体中文？
- [ ] frontmatter 仅含 7 个标准字段（title/source/author/date/type/status/tags）？
- [ ] 引号嵌套是否正确处理（双引号值用单引号包裹）？
- [ ] tags 是否为空或格式正确的 block list？
- [ ] 文件是否已移动到 target_dir？
- [ ] 是否未生成提炼预览表？
- [ ] 未添加 layer/category 等非原始资源字段？
- [ ] 是否未将未登记作者「补登」进看板 blogger 表（仅对照判断，绝不自动新增；未登记作者其条目归「其他」层，见 framework-rules #12）？
- [ ] date 是否为 `yyyy-MM-dd` 裸写无引号（见全局规则 #1）？若带引号或中文格式（如 YYYY年M月D日）即违规
- [ ] source 是否为真实来源链接（优先提取粗制品 frontmatter `url` 字段）？外部→markdown 链接、内部→wikilink，**禁止渠道名/批次名占位**（如"AI整理 - 抖音"、"雪球长文"）？若 url 缺失且无法提取 → source 留空数组 `[]` 并标注待补？
