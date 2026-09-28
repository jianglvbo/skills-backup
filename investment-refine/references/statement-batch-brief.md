# 博主言论分流 · 执行简报（批处理专用，勿再读全量文档）

> 用途：雪球帖子集（粗制品，#29 直提）批量拆言论落库的**唯一必读**。本简报已浓缩 refine-schema §六 决策矩阵、tag-taxonomy、工具契约、收尾规则。
> 出处：refine-schema.md §六（全文仅为兜底复核，正常执行不需要读）。

## 0. MCP 连接（curl POST，非内建工具）

- URL `http://127.0.0.1:8698/mcp`，Header `Authorization: Bearer <token>`（token 读 `~/.config/server-ops/credentials.md` 的 MCP_TOKEN）
- 网络偶发超时 → 重试 3-4 次退避；**禁调研工具文档**（参数见 §4）

## 1. 六分法（落库 contentType 一律英文码值；汇报用中文名）

| 码值 | 显示名 | 一句话判据 | 必填 |
|:---|:---|:---|:---|
| trade | 买卖 | 明确买卖动作（入/加/减/卖/清）→ 走 blogger_trade，不走 statement | op/price 有则填 |
| research | 研究 | 含数据/估值/行业结构的可复用分析 | — |
| predict | 预测 | 对未来的判断：①方向 ②未来指向(时间窗/事件)；**可判对错不是门槛**（2026-09-14 放宽） | stance |
| view | 观点 | 当下判断（底部/可买价位等） | stance |
| insight | 心得 | 心得/方法论/复盘 | — |
| chat | 闲聊 | 仅能刻画擅长/心态才留，否则丢 | — |

**predict 易错**：只有当下状态的判断（"合理价 HKD26.6""白酒处于底部"，无未来指向）→ view，不建 predict；历史复盘不算。**注意**：有没有目标价、能不能精确验证**不是判据**——"下一轮牛市会很差""至少 5 年内没机会"这类粗口径未来判断照归 predict。

## 2. 双时间

- `view_date` 默认 = `statement_datetime`（as_posted）
- 相对表述（"三年前"等）→ derived + `view_date_basis` 存原句，粒度不假装精确
- **帖内无日期 → 两字段留空，不编造**；URL 缺失的帖见 §5

## 3. 主题（subjectId）

- 涉个股/行业/市场言论**必须** subjectId，否则不进言论追踪控制台
- 缺主题先 `console_ensure_subject`：**只能命中标准表已收录的名**（行业命中 `industry_sw` short_name、指数命中 `index_catalog`）；标准表里没有 ≠ 自造名——要新增标准行业走 `data/industry_sw.json` + 重跑 `scripts/seed_industry_index.js`（幂等），不在控制台硬建（AGENTS.md 2026-09-20 重申）。行业按申万标准名（禁自创）；个股带 code+market(+hkConnect 港股通)
- 标的使用代称时还原（寒王→寒武纪），原词存 targetAlias
- **主题只在该言论确认落库的当刻 ensure——禁止为原文里提到的标的批量预建空壳主题**（教训：2026-09-06 六博主批一次性 ensure 了原文出现的所有股票，被舍弃的帖留下 10 个零引用空壳，已删）。流程：先判定某帖言论够格落库 → ensure 其主题 → 落该言论；被舍弃的帖不得 ensure

## 4. 落库调用骨架（参数名严格一致）

```json
console_ensure_subject { "consoleType":"stock|industry|market", "name":"", "code":"", "market":"A股|港股|美股", "hkConnect":true }
blogger_statement { "action":"add", "blogger":"", "contentType":"research|predict|view|insight|chat", "stance":"bullish|bearish|neutral"(predict/view 必填), "target":"", "view":"精炼摘要(含关键数据/判断，不存整篇原文)", "viewDate":"YYYY-MM-DD", "statementDate":"YYYY-MM-DD", "subjectId":N, "source":"雪球", "sourceUrl":"https://xueqiu.com/...", "form":"回复|短文|长文|专栏"(必填), "replyTo":"被回应者原话" }
blogger_trade   { "action":"add", "blogger":"", "targetName":"", "targetAlias":"", "price":"", "stance":"", "tradeDate":"", "statementDate":"", "subjectId":N, "industryName":"", "sourceUrl":"", }
console_add_prediction  // predict 言论落库后调用，回填 origin（参数见工具，勿臆造）
```

**回复帖切分（refine-schema §七⑦的浓缩，每条回复帖都过）**：原文 `第一个 //@` 之前＝博主的话 → `view`；之后＝被回应者原话 → `replyTo`（剥掉 `//@昵称：` 与嵌套 `回复@昵称:` 包装，只留对方说的话，一字不改；**`//@` 段常在长文末尾，先扫到再切，别拿前半句就收工**；原文无 `//@` 段则不传）。

**写前查重**：同博主同段文字已存在 → update 合并，不新增第二份。

## 5. 铁律

- **原文链接**：无 sourceUrl 不建行——高价值言论列入「缺URL挂起」汇报（附原文前 80 字），禁编造 URL
- **收尾状态（#29）**：落言论成功 → 服务端自动按 sourceUrl 置 `post_history.refine_status=1`，无需手工处理；**只有判无价值的帖**才调 `post_history {action:'mark', id, refineStatus:1, noValue:1}`；`fetchedAt` 从 `post_history.fetched_datetime` 带出（导出批次时 SELECT 一起取），勿默认今日
- **signal 硬上限 25 字（含标点）**：服务端拒写（26 字都过不了）；写完先数一遍，超了用 `/` 合并并列要素或删修饰词
- **内容精炼**：落库 view 用一句话摘要含关键数字，**不存整篇原文**
- **能成 wiki**：内容提供可脱离语境复用的判断逻辑/框架 → 才建（博主层模板 + verify-format）；不硬造
- **⚠待确认**：分类不明/价值高去向不明 → 不写不丢，汇报「原文+原因+候选」
- **#29 收尾**：帖子集路径无源文件（原文在 post_history 库内），不存在批次 md 移废纸篓的动作
- 博主画像 md 已废弃（2026-09-12 用户决定）：内容只在看板（`blogger` 表 + `statement` 视图），**不写、不同步、不手改画像 md**

## 6. 汇报格式（极简，一次性）

按批次一行：`博主 | 帖数→言论数(类型分布) | post/trade id | predict 关联 | wiki | ⚠/缺URL条数`
末尾汇总：⚠清单原文、缺URL清单原文（各≤80字）、批次去向。失败明确列原因。

## 7. 防烧预算

- 只读本简报；文档/工具参数**勿再翻**（本简报即权威浓缩）
- 卡住超 2 个文件未决 → 处理能处理的，未决项进 ⚠/挂起清单照常收尾，**不得空转重试**
