---
name: assign-tasks
description: >
  向用户下发任务（看板「便签」弹窗的任务类型，MySQL note_item）。当 agent 在采集/粗加工/提炼/审查等环节
  遇到**需要用户做动作**才能继续的事（改数据、补资料、拍板后要执行一串操作、线下要办的事），
  用 MCP `note_item` 建一条 type=task 的任务下发给用户，用户在看板处理并流转状态。
  触发词：「下发任务」「建任务」「给用户派个活」「这条需要用户处理」。
  排除条件：一句话能答完的裁决/歧义 → 走 pending_decision 待决策队列（投资框架 skill 提炼/审查环节已内建），
  不下发任务；用户自己记的待办/随心记走看板便签，不经本 skill。
  区别于 pending_decision（问句、答完即闭环）：任务是**祈使句 + 可执行步骤 + 状态流转**（open→doing→done/cancelled）。
license: MIT
agent_created: true
metadata:
  version: "1.0.0"
  short-description: agent 向用户下发任务（看板便签任务）
compatibility: 需 investment-dashboard MCP（note_item 工具）
---

# Default stance

## 核心认知

任务 = 把「agent 干不了、必须用户出手」的事，变成看板里一条**能看懂、能动手、能销账**的记录。
用户在看板「便签」弹窗里看到它：高优先排最前、agent 徽标标明来源、点行展开 MD 详情、按钮流转状态。

## 核心原则

- **分流先行（写任务前必判）**：一句话能答完的 → `pending_decision`（别决策队列，选项点一下就完）；
  需要用户**做动作**（跑脚本/改数据/补资料/线下办事，做完可能还要 agent 收尾）→ 本 skill 下发任务。
  判不准时问：「用户的回复本身能不能终结这件事？」能 → 待决策；不能 → 任务。
- **一条任务只让用户做一件事**：多件事拆多条；同一件事的多个步骤可以写在一条的「要你做的」里。
- **依据必须可回溯**：`contextRef` 必填指针（`stmt:<言论id>` / `pending:<待决策id>` /
  `post:<帖子url>` / `review:<审查日期>` / `file:<vault路径>`），用户点开就知道「为什么是我、为什么是这事」。
- **raisedBy 写自己**：填当前 agent 模型名（如 `qwen3.8-flash`），看板显示「agent 下发 · <模型名>」，出问题找得到人。
- **不重复下发**：建之前先 `note_item action=list type=task status=open` 扫一遍，同一 contextRef + 同一件事
  已有未完成任务就**不新建**（可在原任务 update 补充），发重复任务等于给用户制造噪音。
- **高优先要克制**：priority=high 只用于「这条不处理，流水线就卡住」；一般今天内处理即可的用 normal。

## 禁止行为

- 绝不把「agent 自己能做的」下发给用户（那是偷懒，不是任务）
- 绝不下发没有依据指针的任务（用户无法判断来龙去脉，只能回来问你）
- 绝不用任务替代待决策（问句塞进任务，用户答了也没地方闭环）
- 绝不改用户已流转为 done/cancelled 的任务（那是用户的结论）

---

# Workflow

## 第一步：判定分流（执行路径内置，不可跳过）

三问：① 需要用户做动作吗？（只回答不算）② 动作完成后需要 agent 或后续批次收尾吗？③ 值得占用户一条待办清单吗？
三问皆是 → 下发任务；任一为否 → 待决策队列或仅汇报标注。
**例外（2026-09-30 用户拍板）**：投资框架 skill 提炼环节第三步第 9 条的「学习推荐」任务不受 ② 限制——用户明确要求提炼发现值得学习的内容时下发任务告知（用户动作＝去读，无需收尾），照常下发。

## 第二步：查重

`MCP note_item action=list type=task status=open`，按 contextRef 与事项比对；有未完成任务 → 走 update 补充，不新建。

## 第三步：写任务（格式硬规范，正反例见 `references/task-format.md`）

```
MCP note_item action=add
  type=task  source=agent  raisedBy=<当前模型名>
  title=<一句话祈使句，≤30字，动词开头>
  priority=<high|normal|low>
  dueDate=<可选 yyyy-MM-dd>
  contextRef=<stmt:|pending:|post:|review:|file: 指针>
  content=MD 四段（缺「背景」「要你做的」不合格）：
    ## 背景
    <哪个环节、遇到什么问题，2-3 句>
    ## 要你做的
    1. <可执行步骤>
    2. ...
    ## 依据
    - <言论/帖子/审查记录的链接或 id，用户可点开的放 url>
    ## 期限
    <可选；写「阻塞 XX 流程，处理完当批继续」这类后果，不写空洞的尽快>
```

## 第四步：汇报

下发后在本次执行汇报里列「本次下发任务 N 条（id 与 title）」；high 优先的任务单独点名。

## 任务生命周期（用户侧动作，agent 只读不代改）

- 用户点「开始」→ status=doing；点「完成」→ done；点「取消」→ cancelled（用户判定这事不做了，是结论不是遗漏）。
- agent 后续批次可 `list type=task status=done` 回收已完成任务，按「要你做的」内容继续收尾（如用户批准了某次重采，done 即授权信号）。

---

# Output format

| 字段 | 类型 | 说明 |
|:---|:---|:---|
| dispatched | list[{id,title,priority}] | 本次新建任务 |
| merged | list[{id}] | 并入既有任务的补充（未新建） |
| skipped_reason | string | 未下发时说明走了哪条分流 |

# Relative files

| 文件 | 何时加载 |
|:---|:---|
| `references/task-format.md` | 写 content 前读一次：四段模板全文 + 3 组正反例（提炼歧义/审查修复/数据补录） |

# Source hierarchy

1. 用户约定（2026-09-30：任务由 agent 下发、样式内容本 skill 定稿）
2. 看板 note_item 表口径（investment-dashboard `src/server.js` 的 NOTE_ITEM_DDL 注释）
3. framework-rules #49（待决策队列分流，本 skill 与它互补）

# 自检

- [ ] 三问分流判过了？（答完即终结的事没被塞进任务）
- [ ] title 是祈使句且 ≤30 字、content 四段齐（背景/要你做的至少）？
- [ ] contextRef 带了指针、raisedBy 写了模型名、查重没发重复任务？
- [ ] 汇报里列了「本次下发任务 N 条」？
