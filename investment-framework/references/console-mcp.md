# Investment Console MCP（投资看板）

投资看板（investment-dashboard）的 MCP 服务，供任何 agent（WorkBuddy / 其他 agent）接入读写投资知识库派生数据。

## 连接信息

| 项 | 值 |
|---|---|
| 类型 | MCP **HTTP**（Streamable HTTP，JSON-RPC 2.0） |
| 端点 | `http://127.0.0.1:8698/mcp` |
| 鉴权 | Header `Authorization: Bearer <token>`（token 由服务器 owner 提供） |
| 请求 | `POST /mcp`，`Content-Type: application/json`，body = JSON-RPC 消息 |

## 前提

- **看板本地运行**：`investment-dashboard` 服务在本机（端口 8698，launchd: `com.investment-dashboard`），读本地 iCloud vault；**MySQL 仍在远程服务器** `106.55.14.116:3306`（`investment-dashboard`）
- 数据源：Obsidian vault 派生 MySQL（`investment-dashboard` 库）——**vault 为绝对基准**，MySQL 为派生数据
- 本地 config.json 含 `mcpToken`（与服务端鉴权一致）

## 工具清单（41 个，2026-09-28 对齐服务端 tools/list）

| 类别 | 工具 |
|---|---|
| 知识库统计 | `overview`（全库统计：fileCount/bloggerCount/tags/files 树） |
| 文件操作 | `list_files`、`read_file`、`write_file`、`delete_file`、`unmark_delete`、`list_pending_delete`、`purge_pending_delete` |
| 博主 | `list_bloggers`、`get_blogger`、`add_blogger`（支持 avatar）、`update_blogger`、`remove_blogger`、`restore_blogger`、`purge_blogger`、`list_recycle_bloggers` |
| 标签 | `list_tags` |
| 言论追踪 | `blogger_statement`（六分法 `contentType` 落库：research/predict/view/insight/chat，trade 走 `blogger_trade`）、`blogger_trade`（买卖记录）、`console_statement_review`（言论卡复核建议闭环）、`statement_read`（待读/已读）、`statement_star`（星标） |
| 原文库 | `post_history`（采集落点 + 提炼前原文：check/get/upsert/stats/mark） |
| 实体与别名 | `stock_alias`（别名/主营词）、`stock_former_name`（曾用名）、`stock_watch`（自选/备注）、`console_ensure_subject`（建主题，行业/指数过标准表门禁）、`industry_sw_list`（申万标准表）、`index_catalog_list`（指数目录）、`industry_follow`（关注行业） |
| 预测控制台 | `console_list_subjects`、`console_get_subject`、`console_add_prediction`、`console_update_status`、`console_stats`（方向统计查询/重算） |
| 待决策 | `pending_decision`（agent 上报 → 用户裁决 → 内化闭环） |
| 审查落库 | `review_record`（结构化审查）。已下线：`refine_record`（2026-09-14）、提炼链路 `refine_trace`/`refine_review`（2026-09-26 随步骤落库下线删除，framework-rules #54） |
| 日志与 git | `get_logs`、`git_log`、`git_status`、`git_commit` |

预测控制台域（方案 A：MySQL 唯一存储，vault 不再存控制台 Markdown）：`console_add_prediction` 幂等去重（主题+日期+预测人+内容唯一键）；`console_update_status` 改已验证/已撤销时 `verify.result`+`verify.basis` 必填（服务端强制验证留痕）。库表（2026-09-13 规范化为单数表名）：实体四表 `stock`/`industry`/`market_index`/`market`，预测即言论行 `statement_predict` + 验证留痕子表 `statement_verify_sub`，言论统一只读视图 `statement`（字典为 `dict` 单表 type=console_type/prediction_status/verify_result）。vault 文件派生索引在内存扫描（files/tags 表已退役），本机 migrate_to_mysql.py 已退役；存量迁移已完成，一次性脚本已删除。 `console_add_prediction` 支持 subjectMarket（sh/sz/hk/kr/us）与 subjectHkConnect（1/0），只补空不覆盖；个股代码显示权威为 `stock.code`/`stock.market_code`/`stock.has_hk_connect`。完整库结构见本 skill `references/investment-dashboard.sql`（2026-09-28 起与库对齐重导）。

## ⚠️ 枚举码硬约束（落库避坑 · 2026-08-31 实测）

`review_record` 的 `checks[].status` 等枚举 **优先传 MySQL 字典英文码，传中文会外键报错**（`foreign key constraint fails ... dict_*`）。全量对照见投资框架 skill 的 `investment-refine/references/refine-schema.md`「二B 字典码对照表」，要点：

- `layer`：`my`/`blogger`/`other`/`macro`/`workspace`（**2026-08-31 起兼容中文**：我的/博主/其他/宏观，服务端自动映射；`category`/`relation` 同样兼容中文）
- `category`：`analysis_framework`/`trading_system`/`investment_mentality`/`investment_insight`/`stock`/`industry`/`macro`
- `relation`：`new`/`append`/`complement`/`conflict_check`/`other`
- `type`：`wiki`/`blogger`/`macro`；`sourceType`：`raw`/`coarse`
- 审查 `checks[].status`：仅 `pass`/`warn`/`fail`（无 `info`）

**提炼链路落库已下线（2026-09-26 用户拍板，framework-rules #54）**：`refine_trace`/`refine_review` 工具与 `refine_step`/`refine_review`/`refine_chain_step` 三表已删除，不再逐步骤落库与复核。提炼产物照常落各自存储（言论六表 / wiki 条目，各自落库动作不变）；用户对提炼结果的异议走 `pending_decision`（待决策队列）与 `console_statement_review`（言论卡左滑复核建议）。

## 调用示例（JSON-RPC）

```bash
# initialize
curl -X POST -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' \
  http://127.0.0.1:8698/mcp

# tools/list
curl -X POST -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' \
  http://127.0.0.1:8698/mcp

# tools/call（例：overview）
curl -X POST -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"overview","arguments":{}}}' \
  http://127.0.0.1:8698/mcp
```

## 接入配置（各 agent 的 MCP 客户端）

```
mcpServers:
  investment-dashboard:
    type: http
    url: http://127.0.0.1:8698/mcp
    headers:
      Authorization: Bearer <token>
```

## 职责边界

- 本 MCP = 控制台域（读 MySQL 派生数据 + vault 文件操作 + 审查落库）
- 知识库流水线（提炼/审查/粗加工的执行逻辑）走投资框架 skill（`investment-refine` / `investment-review`），审查落库调用本 MCP 的 `review_record`（REST POST /api/review/record 兼容）；提炼链路落库已下线（见上），`/api/refine/*` 端点已随工具一起删除
