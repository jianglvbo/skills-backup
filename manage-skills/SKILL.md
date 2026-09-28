---
name: manage-skills
description: Manage the shared agent-skill library via skills-manager-cli — install, update, remove, deploy/undeploy per agent, organize folders, search, adopt, git backup. Use when the user wants any agent to gain or lose a skill, to organize the central library, or asks what is installed or deployed. 触发词：「skillmanager」「skills-manager」「管理 skill 库」「技能库管理」「哪个 agent 装了哪些 skill」「收编 skill」「断开更新」。排除条件：本 skill 只管中心库与各 agent 部署；设计准则交 skill-guidelines，新建业务 skill 交 skill-creator，采集/提炼类业务 skill 不归本 skill。强制闸门：任何「装个 skill」的指令（含 git clone / skills.sh / 本地目录）先扫三处查重——中心库、目标 agent 自己目录（含未纳管实体目录，ls 才看得见）、该 agent 的插件与内置——出对比表并停下等用户确认，未确认不得 install；agent 内部安装器产物（Qoder 插件市场、千问办公自带 dingtalk-*/docx/pptx 等）不 adopt 不入库。本机实测坑：把技能改成 local（断开上游、update 跳过）用 skills set-source <skill> --local；直接 UPDATE 数据库会被运行中的 app 写回。
---

# Default stance

## 核心原则

- **装前必查重，未确认不 install**：用户任何形式的安装请求（marketplace / git / 本地目录 / git clone）都先走 Workflow 第三步的三处扫描，出对比表，**停下等用户选**。`remove` 会连 skill id、folder 隶属和全部部署一起删，装错撤销成本高。
- **CLI 不保护的整目录替换，直报不代决**：`set-source --force`（无 held-back 检查）、`update` 撞上 `held_back_removals`（说明库里多了上游没有的文件）——报告涉及路径，让用户决定。
- **agent 内部安装器产物永不入库**：Qoder 插件市场（`~/.qoder-cn/plugins/cache/**`）与千问办公自带（`dingtalk-*`、`docx`/`pptx`/`xlsx`/`pdf` 等）由该 agent 自管，不 adopt。

## 禁止行为

- 绝不绕过 CLI：不手写 `git` 操作库（app 的 sync refs 在里面）、不直改 `skills-manager.db`（运行中的 app 会写回）。
- 绝不省安全步：批量 remove / folder delete / deploy / undeploy 先 `--dry-run`，update 先 `check`。
- 绝不把示例里的 `$SM` 当跨 shell 变量：每次执行都从 Workflow 第一步重新解析 CLI 路径并替换。

# Workflow

## 第一步：Resolve the CLI

Run once per session, then substitute the printed path into every command:

```bash
D="$HOME/.skills-manager/bin"
B="$D/skills-manager-cli"; [ -e "$B" ] || B="$B.exe"   # .exe on Windows
if [ -s "$D/.version" ] && [ -x "$B" ]; then
  echo "$B"
elif [ -s "$D/.version" ] || [ -e "$B" ]; then
  echo BRIDGE_BROKEN
else
  P="$(command -v skills-manager-cli 2>/dev/null || true)"
  [ -x "$P" ] && echo "$P"
fi
```

- **Path under `~/.skills-manager/bin`** — the desktop app published and verified this copy. Use it.
- **`BRIDGE_BROKEN`** — a half-failed copy: unstamped binary, or stamp with no binary. **Stop**; do not hunt for another CLI (it may predate a safety fix). Ask the user to open the Skills Manager app once, which republishes it.
- **Path from PATH** — a CLI-only machine; use it, noting it may be older than a desktop app if one exists.
- Nothing printed → this skill doesn't apply; fall back to find-skills or tell the user to install Skills Manager.

**Always pass `--json` when you parse output yourself.** Errors carry `ok=false`, a stable `code`, and `message` on stderr with non-zero exit.

## 第二步：Mental model

One central library at `~/.skills-manager/skills/`, shared by all agents. Three states, three command families — never conflate them:

| 状态 | 控制命令 | 关键语义 |
|:---|:---|:---|
| **Library**（是否纳管） | `skills install/remove` | remove 删库内副本 + 全部部署 + DB 行 |
| **Folder membership**（归类） | `folders add/move` | 一个技能只属一个 folder，`add` 是从旧 folder **搬走** |
| **Deployment**（agent 可见） | `skills deploy/undeploy`、`folders targets/deploy/undeploy` | `folders targets` 是整集合替换，按差量部署/回收 |

The library directory is a git repo with a backup remote, auto-committed by the app — undo a bad batch with `git versions` / `git restore <tag>` (cli.md §Library backup), not hand-rolled `git`.

**`presets` no longer exists** (it is `folders`), and **there are no tags** — `skills tag …` is not deprecated, it is unrecognized. `list`/`show` return no `tags`/`presets` fields. Don't write commands for either.

## 第三步：装前查重闸门（install 的前置，不可跳）

User asks for any install → scan all three places a skill can already live, present the comparison table, and **stop for confirmation**:

```bash
"$SM" --json agents list                              # each agent's skills_dir
"$SM" --json skills list --query <name>                # 1. central library
ls <skills_dir>                                        # 2. what that agent can already load
ls ~/.qoder-cn/plugins/cache/*/*/skills 2>/dev/null    # 3. plugin-supplied skills (Qoder; other agents differ)
```

Step 2 has **no CLI support**: `skills list --deployed-to <agent>` reports only library-owned
deployments, so unmanaged bodies in an agent's own dir are invisible to it — `ls` is the only
way to see them. Never conclude "no duplicates" from `--deployed-to` alone.

Present the table, and only the table, before running anything:

| 候选 | 已有同名/同能力项 | 所在层 | 纳管? | 上游 / 可否 `update` | 触发词是否撞车 | 结论 |
|---|---|---|---|---|---|---|

`结论` is one of four, chosen from evidence: **直接装** / **库里已有，缺的是补链**（同名列已在库、`source_type` 是 `local` 或无 `source_ref` 的 `import`——修复是 `skills set-source --git-url`，不是二次 install）/ **重复**（库或该 agent 已有同能力项）/ **撞在不同层，库管不了**（重叠在插件或 agent 内置——明说，提议触发词分工而非安装）。

On confirmation: install per cli.md §skills install (syntax, `--folder` shortcut, ref
resolution), then **verify** with `skills show <name>` and report folder + reached agents.
For "find me a skill" requests, run `skills search` (cli.md) first and show top 1–3 with
install counts — but the duplicate scan, not popularity, decides.

## 第四步：按任务执行（细节在 references/cli.md）

| 任务 | 走 cli.md 哪节 | 红线 |
|:---|:---|:---|
| 装 / 搜 | §skills install / §skills search | 第三步闸门先行 |
| 更新 | §skills update / check | `check` 先行；`held_back_removals` 出现即停、报用户 |
| 删 | §skills remove | 不可逆；批量先 `--dry-run` |
| 部署 / 收回 | §skills deploy / undeploy / status | 必须显式 `--agent`；`enable/disable` 已废弃不用 |
| 修目录漂移 | §skills sync | 无参 = 全 folder 重放部署 |
| 收编已有目录 | §skills adopt | 排除清单（Default stance 第 4 条）；adopt 是**复制**，原体自删、需显式 deploy |
| 改上游 / 断链 | §skills set-source + 下节 | `--force` 永不代传 |
| 组织 folder | §folders | `add` 是搬走；`delete` 不加 `--undeploy` 只动组织 |
| 排障 | §Health check | `repo status` / `agents list` / `git status` 是只读第一查 |

### 断开更新链（make a skill local）

`update` 整目录替换会毁掉库内改动。保住副本并断开上游：

```bash
"$SM" --json skills set-source <skill> --local --dry-run   # reports was/now without writing
"$SM" skills set-source <skill> --local                    # source_type -> local, update_status -> local_only
```

Afterwards `skills check <skill>` returns `skipped: true`. **Do not** try to get there by
writing the DB directly: the running app holds the list in memory and periodically runs
`reindex sync metadata`, restoring the old value within seconds. `set-source --local` goes
through the repo lock and rewrites both the row and the `skills/<id>.json` metadata file,
which is what makes it stick.

## 第五步：汇报

Report per action: skill name, folder, `deployed_to`, `source_type`/`update_status`, and any
paths the CLI refused to touch (see §Output format).

**When a deployment is refused** (`TARGET_CONFLICT`): nothing was deleted, nothing else in the
batch was applied. Tell the user which path is in the way, that its contents are untouched, and
offer the two ways out — adopt it into the library, or move it aside and retry. Never delete it
for them.

**When update holds back** (`refreshed: false` with `held_back_removals`): not a failure, do
not retry. Show the listed paths and ask; only the desktop app can force it through.

# Output format

| 字段 | 说明 |
|:---|:---|
| `action` | install / update / remove / deploy / undeploy / adopt / set-source / folder op |
| `skill` | 名称 + 涉及的 agent 与 folder |
| `state` | 执行后 `skills status`/`show` 的 `source_type`、`update_status`、`deployed_to` |
| `refusals` | CLI 拒绝的路径与原因（`TARGET_CONFLICT` / `held_back_removals`），无则省略 |
| `next` | 需要用户决定的点（确认装 / 处理 held-back / 选 adopt 或挪开），无则省略 |

# Relative files

| 场景 | 加载文件 | 内容 | 方式 |
|:---|:---|:---|:---|
| 执行任一命令组前 | `references/cli.md` | 按命令组的完整语法、语义边界、长尾坑 | 读取（按节精准读） |

# Source hierarchy

1. CLI `--help` 实测（版本会变，命令以它为准）
2. 本 SKILL.md 与 `references/cli.md`
3. 上游 skills-manager 项目的旧版文档（`presets`/tags 时代）——仅用于识别「过时说法」，不用于执行

# 自检

- [ ] install 前跑过三处扫描 + 对比表 + 停下等确认？
- [ ] 破坏性路径（`--force`、`held_back_removals`、批量 remove）都报给了用户而不是代决？
- [ ] 没有 `--dry-run` 漏跑、没有 update 前漏 `check`？
- [ ] 三个状态（library / folder / deployment）用的命令族没混？
- [ ] install / adopt 之后跑过 `skills show`/`status` 验证并汇报了 folder 与 deployed_to？

# Pitfalls (high frequency)

- **Install succeeded but skill doesn't appear in the agent** → install defaults to
  library-only. `skills deploy <skill> --agent <key>`, or install with `--folder <ref>`.
- **Folder membership changed but agent files did not** → membership is organization only;
  follow with an explicit deploy. `folders delete` without `--undeploy` likewise leaves
  skills deployed.
- **`folders add` moved the skill out of its old folder** → expected (one folder per skill).
  Check the previous owner with `skills status <name>` before moving.
- **Adopted skills can't `update` from git** → `npx skills add` and manual `git clone` leave
  no source metadata; re-point with `skills set-source`. Never remove-then-reinstall (drops
  the id, folder, and deployments).
- **`presets …` / `skills tag …` don't exist** → unknown subcommands, not deprecated ones.
  Re-derive from `"$SM" folders --help` / `"$SM" skills --help`.
- **Long tail** (symlink hygiene, `npx skills` second ledger, DB edits written back, adopt
  `--git-url` late failure, folder agent claims) → `references/cli.md` §Pitfalls.
