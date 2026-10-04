# 开放规范对齐（agentskills.io）

> Agent Skills 已是事实标准：Anthropic 发起、40+ 客户采纳（Codex / Copilot / Cursor / Gemini CLI / Qwen Code / TRAE / OpenClaw…）。本库设计与开放规范对齐，用户约定从严的地方继续从严。

## frontmatter 字段约束

| 字段 | 必填 | 约束 |
|:---|:---|:---|
| name | 是 | 1–64 字符，小写字母/数字/连字符；不许首尾或连续连字符；**须与目录同名** |
| description | 是 | 1–1024 字符，写「做什么 + 何时用」（细则见 principles.md 原则二） |
| license | 否 | 许可证名或指向内置 LICENSE 文件 |
| compatibility | 否 | ≤500 字符，环境要求（多数 skill 不需要） |
| metadata | 否 | string→string 键值对（作者 / 版本放这） |
| allowed-tools | 否 | 空格分隔的预批准工具（实验性，各端支持不一） |

厂商扩展字段举例（规范要求客户端忽略未知字段，写了不破坏兼容）：Qwen Code `paths`（glob 门控，没碰过匹配文件模型看不见）/ `hooks`（把必须确定性执行的规则写成代码级门禁）/ `priority`；OpenClaw `metadata.openclaw`（requires.bins/env/os 门禁）；Codex `agents/openai.yaml`（UI 元数据与隐式触发开关）。

## 三层渐进披露预算

| 层 | 加载时机 | 行业预算 | 本库约定 |
|:---|:---|:---|:---|
| 元数据（name+description） | 启动常驻 | ~100 tokens | 精炼、领词前置，一个 branch 一个触发条件 |
| SKILL.md 正文 | 触发时全量 | <500 行 / 5000 tokens | **≤200 行（更严，继续执行）** |
| references/ · scripts/ · assets/ | 按需 | 无硬限 | 每个文件写清何时加载 |

## 跨工具目录

| 位置 | 定位 |
|:---|:---|
| `~/.zcode/skills/` | ZCode 个人技能（`.zcode/skills` 项目级同名优先＝覆盖位） |
| `.agents/skills/`（项目）/ `~/.agents/skills/`（用户） | **跨工具公共位置**——Ai 仓库公共 skill 放这里，多 agent 共用 |
| `~/.qwen/skills/` · `.qwen/skills/` | Qwen Code（扩展技能带命名空间 `ext:name`） |
| `.lingma/skills/` | 通义灵码 |
| `.github/skills/` · `~/.copilot/skills/` | GitHub Copilot（另认 `.claude/skills`、`.agents/skills`） |
| `~/.codex/skills` | OpenAI Codex（多级扫描，`.agents/skills` 亦认） |

## 安全

第三方 skill 一律视为**不可信内容**（SKILL.md 可藏 prompt injection、scripts 可藏恶意代码）：启用前先通读；来源不明的 scripts/ 不执行；凭据不进 skill 内容（环境变量或 0600 本地文件）。
