---
name: git-ops
description: >
  通用 Git 提交管理：任意 git 仓库的变更检测、暂存、提交、查历史、按阈值推送，不绑定特定仓库。
  触发词：「git 提交」「提交仓库」「commit」「提交管理」「git-ops」「同步仓库」「查看提交历史」。
  agent 自己改完文件后的主动提交同样走本 skill，不必等用户说「提交」（2026-09-15 用户约定）。
  排除条件：Ai 仓库的专属约定（README 强制更新、cp -r 安装、识别合并同步）见 ai-repo-manager，本 skill 只做通用提交动作。
agent_created: true
---

# git-ops · 通用 Git 提交管理

管理任意 git 仓库的提交全流程。目标仓库由调用方传入 `target_repo`，或从 `references/repo-config.md` 已登记仓库中确认。

## Default Stance

### 核心原则

- **改完就提交（2026-09-15 用户约定）**：本会话改过的文件随手提交，不等用户指令；推送阈值见第六步。
- **提交前必检测**：先 `git status` 展示变更清单再决定暂存什么；禁止不看清单直接 `git add -A`。
- **只提交自己的改动（2026-09-11 硬约束）**：工作区可能含并行会话或用户手工修改，把他人未提交改动卷进提交 = 纯覆盖事故。
- **author 必须是当前 agent 模型名（硬门禁）**：提交者身份显式指定，让 `git log` 能辨认每笔提交出自哪个 agent；不依赖 git 全局 user.name——它恒为用户本人，不随 agent 变，起不到区分作用。
- **提交信息**：用户给的优先；未给时按 Conventional Commits 生成（细则见 `references/commit-message.md`）。

### 回退安全点与分支（2026-09-27 用户约定，动手前判）

- **有多种可能，或要做重要变更前 → 先提交一笔检查点**：把当前"已经能跑"的自有改动先落一笔（`WIP`/`chore` 语义即可），再开始下一个方案。
  目的是用户说「效果不好，回退」时有干净的一版可退，而不是靠反向手工改代码。
- **重大变更新起分支，做完再合并**：不在主分支直接铺开。分支名用动词短语（`feat/xxx`、`refactor/xxx`），合并回主分支前在分支上端到端验证。

| 起分支 | 不起分支 |
|:---|:---|
| 多方案试验、跨多文件重构、改 DB 列名/结构、删文件与迁移类改动 | 单点小修、文案、一行级修复——为小事开分支是负担 |

- **检查点只含自己的路径**：`git commit -- <我改的路径>`。**禁止用 `git stash` 腾"干净"工作区**——
  并行会话的未提交改动会被卷走；他人改动就地留在工作区、在汇报里提示即可。
- 推送分支、以及合并后推主分支，属对外可见动作：除非用户本次已授权，先问再推（第六步）。

### 禁止行为

- 绝不 force push / 改写已推送历史
- 绝不跳过变更检测直接提交
- 绝不 `git add -A` / `git add .`（唯一例外：用户明确要求提交全部，且已确认清单中无他人改动、无临时产物）
- **拿不到当前模型名就绝不提交**——停止并询问用户，不得退回全局 user.name 交差
- 绝不提交 .gitignore 覆盖的文件
- 绝不未 fetch 即 push（远程领先时直接 push 会被拒或掩盖冲突）

## Workflow

### 第一步：确认目标仓库

调用方传 `target_repo` → 用之；否则查 `references/repo-config.md` 已登记仓库，向用户确认用哪个；均无 → 询问用户。

```bash
cd <target_repo> && git status   # 确认仓库状态与当前分支
```

### 第二步：检测变更与归属

```bash
git status --short    # 变更清单：?? 新增 / M 修改 / D 删除
git diff --stat       # 已跟踪文件的改动量
```

逐项判定每个文件的来源，再决定暂存范围：

| 来源 | 判据 | 处理 |
|:---|:---|:---|
| 我的改动 | 本会话工具调用中编辑过 | ✅ 暂存 |
| 他人未提交改动 | 清单有、但我未编辑过 | ⛔ 不暂存，汇报中提示用户（可能并行会话/手工改动） |
| 未跟踪产物 | outputs/、临时脚本、日志等 | ⛔ 默认不提交（用户明确要求除外） |

> 改文件前必须先 read；若读取后被他人改过，edit 工具会报 `file changed since it was read` 并拒绝写入——重新读取、确认差异方向后再改，不得绕过。

### 第三步：暂存

- 逐个 `git add <我改动的文件>`（显式列出路径）
- 暂存后 `git status --short` 复核：暂存区只含预期文件，多一个都要查清来源

### 第四步：提交（author 硬门禁）

**先过门禁再提交**——模型名取自 harness 会话信息（系统提示中的 model 标识短名，如 `GLM-5.3-Flash`）；环境未提供模型标识 → **停止提交，询问用户**。

| | 写法 |
|:--|:--|
| ❌ 错误 | `git commit -m "..."` —— author 落到全局 user.name，`git log` 无法辨认是哪个 agent 提交的 |
| ✅ 正确 | `git commit --author="GLM-5.3-Flash <仓库邮箱>" -m "..."` |

- **邮箱**：仓库配置（`git config user.email`）或 repo-config 登记值；两者都无 → 询问用户
- **提交信息**：用户自定义优先；否则按 Conventional Commits 生成（完整规范与正反例见 `references/commit-message.md`）
- 提交后自检：`git log -1 --format="%h %an %s"` —— **`%an` 不是模型名 = 门禁失守，立即 `git commit --amend --author=...` 修正**

### 第五步：查看提交历史（按需）

```bash
git log --oneline -10
git log --format="%h %ad %an %s" --date=short -5   # 带日期作者
git show --stat HEAD                                # 最近提交变更统计
```

### 第六步：推送（按约定）

**推送前冲突检查**——先 fetch 再决定：

```bash
git fetch origin
git log --oneline HEAD..origin/<branch> | wc -l    # 远程领先数
git log --oneline origin/<branch>..HEAD | wc -l    # 本地领先数
```

| 远程领先 | 处理 |
|:---|:---|
| 0 | ✅ 可安全 push |
| >0 | ⛔ 不得直接 push：先 `git pull --rebase` 合并；冲突无法自动解决 → 上报用户决策，绝不 force push |

- 本地未推送 commit ≥ 5 → 主动推送一次（阈值与全局 AGENTS.md「git 提交」节一致）
- 用户明确要求立即推 / 不推 → 遵循用户指示

## Output Format

| 字段 | 类型 | 说明 |
|:---|:---|:---|
| repo | string | 目标仓库路径 |
| changes_detected | list[string] | 变更文件清单（含归属判定结果） |
| committed | boolean | 是否完成提交 |
| commit_hash | string | 本次提交 hash（若提交） |
| author | string | 实际写入的 author（若提交） |
| history | list[string] | 提交历史（若查看） |
| pushed | boolean | 是否推送 |

## Relative Files

| 场景 | 加载文件 | 内容 | 方式 |
|:---|:---|:---|:---|
| 确认目标仓库 / 邮箱 | references/repo-config.md | 已登记仓库的路径/邮箱/忽略约定 | 读取（按仓库选小节） |
| 生成提交信息 | references/commit-message.md | Conventional Commits 完整规范与正反例 | 读取 |

## Source Hierarchy

1. 调用方传入的 target_repo 参数
2. 用户显式约定（author=模型名、改完就提交、push 阈值 ≥5）
3. 仓库配置（references/repo-config.md）
4. Conventional Commits 规范

## 自检

- [ ] 暂存区只含本会话改动文件？清单中他人改动已在汇报中提示用户？
- [ ] author 为当前模型名（`git log -1 %an` 复核过）？
- [ ] 提交信息为用户自定义或 Conventional Commits 格式？
- [ ] 未 force push，push 前已 fetch 检查远程领先？
