---
name: manage-skills
description: Manage the user's shared agent-skill library via skills-manager-cli — install, update, remove, deploy or undeploy skills per agent, organize them into folders (folders replaced the old presets), search, adopt, and back the library up via git. Use this whenever the user wants Claude Code, Codex, Cursor, Qoder, or another agent to gain or lose a skill, wants to organize the central library, or asks what is installed or deployed. Prefer this over direct agent-folder installs because Skills Manager preserves source metadata, folder membership, updates, and cross-agent deployment state. 触发词：「skillmanager」「skills-manager」「管理 skill 库」「技能库管理」「哪个 agent 装了哪些 skill」「收编 skill」「断开更新」。排除条件：本 skill 只管中心库与各 agent 部署；设计准则交 skill-guidelines，新建业务 skill 交 skill-creator，采集/提炼类业务 skill 不归本 skill。强制闸门：任何「装个 skill」的指令（含 git clone / skills.sh / 本地目录）先扫三处查重——中心库、目标 agent 自己目录（含未纳管的实体目录，`ls` 才看得见）、该 agent 的插件与内置——再出对比表并停下等用户确认，未确认不得 install；agent 内部安装器产物（Qoder 插件市场、千问办公自带 dingtalk-*/docx/pptx 那类）一律不 adopt 不入库，由该 agent 自管。本机实测坑：要把技能改成 local（断开上游、让 update 跳过它）用 skills set-source <skill> --local，它会连元数据文件一起改；直接 UPDATE 数据库会被运行中的桌面 app 写回原值。
---

## Before doing anything

1. **Resolve the CLI first, then use the path it prints.** Run this once (POSIX
   shell):

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

   **Substitute the printed path into every command below**, wherever the
   examples write `$SM`. Do not carry `$SM` as a shell variable: each command
   you run is a new shell, so an assignment made here is gone by the next one.

   The three outcomes:

   - **A path under `~/.skills-manager/bin`** — the desktop app published this
     copy, and the `.version` stamp appears only after it has been verified, so
     it always matches the app the user is running. Use it.
   - **`BRIDGE_BROKEN`** — something the app left behind is here but does not
     add up: an unstamped binary, or a stamp with no binary beside it. Either is
     what a copy that failed half-way leaves. **Stop.** Do not go looking
     for another CLI: that binary may predate a safety fix, and the machine has
     a desktop app whose version nothing here can match. Ask the user to open
     the Skills Manager app once, which republishes it.
   - **A path from PATH** — nothing was ever published here, so there is no
     stale copy to worry about: this is a CLI-only machine (a server install, a
     standalone download, a hand-built binary). Use it, but note it can be
     older than a desktop app if one is also installed.

   If nothing is printed at all, this skill doesn't apply — fall back to
   find-skills, or tell the user to install Skills Manager.
2. **Always pass `--json` when you parse output yourself.** Pretty-printed output is for the user; JSON is for you. Errors include `ok=false`, a stable `code`, and `message` on stderr with a non-zero exit code.

```bash
"$SM" --json skills list
```

### When a deployment is refused

A deploy that would overwrite something that is not ours is refused outright —
nothing at those paths is deleted, and nothing else in the batch is applied.
That failure is machine-readable, so report the actual paths rather than the
sentence:

```json
{"ok": false, "code": "TARGET_CONFLICT", "kind": "target_conflict",
 "message": "Refusing to deploy: 1 of 2 target(s) …",
 "details": {"conflicts": [{"path": "/Users/me/.claude/skills/db",
                            "reason": "is not a managed deployment"}]}}
```

Tell the user which path is in the way, that its contents are untouched, and
offer the two ways out: adopt it into the library (`skills adopt`), or move it
aside and retry. Never delete it for them.

## Mental model

There's **one central library** at `~/.skills-manager/skills/` that all agents share. Each skill has source metadata, **one** folder membership, and zero or more real deployments in agent directories. A **folder** is a group in a tree (it may have a parent), and it carries the set of agents it deploys to.

Keep these three states separate:
- **Library**: `skills install/remove` controls whether Skills Manager owns the skill.
- **Folder membership**: `folders add` / `folders move` organize the library only. A skill belongs to exactly one folder, so adding it to a second folder *moves* it out of the first.
- **Deployment**: `folders targets` / `folders deploy|undeploy` and `skills deploy|undeploy` control what an agent can actually see.

Two things the old docs got wrong, and are now confirmed against the shipped CLI: **`presets` no longer exists** (it is `folders`, a top-level command group), and **there are no tags at all** — `skills tag …` is not a deprecated command, it is an unrecognized one. Neither `list` nor `show` returns a `tags` or `presets` field. Don't write commands for either.

The library directory is itself a git repo with a backup remote, auto-committed by the app. Use `git versions` / `git restore <tag>` to undo a bad batch rather than hand-rolling `git` inside it.

## Install

**Nothing gets installed before a duplicate check.** When the user asks for an install in a session
(`帮我从 github 装 archify`), scan all three places a skill can already live, report a comparison table, and
**stop for confirmation** — `remove` drops the skill id with its folder membership and every per-agent
deployment, so a wrong install is expensive to undo.

```bash
"$SM" --json agents list                              # each agent's skills_dir
"$SM" --json skills list --query <name>                # 1. central library
ls <skills_dir>                                        # 2. what that agent can already load
ls ~/.qoder-cn/plugins/cache/*/*/skills 2>/dev/null    # 3. plugin-supplied skills (Qoder; other agents differ)
```

Step 2 has **no CLI support**: `skills list --deployed-to <agent>` reports only what the library owns, so
unmanaged bodies sitting in an agent's own directory are invisible to it. `ls` is the only way to see them —
never conclude "no duplicates" from `--deployed-to` alone.

Then present the table, and only the table, before running anything:

| 候选 | 已有同名/同能力项 | 所在层 | 纳管? | 上游 / 可否 `update` | 触发词是否撞车 | 结论 |
|---|---|---|---|---|---|---|

`结论` must be one of four, chosen from the evidence: **直接装** (nothing in any of the three layers) /
**库里已有，缺的是补链** (same name in the library but `source_type` is `local` or `import` with no
`source_ref` — the fix is `skills set-source --git-url`, not a second install) / **重复** (the library or the
agent already has something that does this) / **撞在不同层，库管不了** (the overlap lives in a plugin or the
agent's own built-ins, which never enter the library — say so plainly and propose trigger-word division of
labour instead of an install).

```bash
# From skills.sh marketplace
"$SM" skills install vercel-labs/agent-skills@react-best-practices

# Any git URL (use /tree/branch/subpath form when the skill lives in a sub-directory)
"$SM" skills install https://github.com/anthropics/skills.git
"$SM" skills install https://github.com/foo/bar/tree/main/skills/baz

# Local folder
"$SM" skills install ./my-skill

# Force a source type when the ref is ambiguous
"$SM" skills install foo/bar --skillssh
"$SM" skills install ./looks-like/owner-repo --local
```

**Default is library-only** — the skill enters the DB but doesn't appear in any agent yet. Prefer an explicit follow-up deployment so scope is unambiguous:

```bash
"$SM" skills deploy <skill> --agent claude_code --agent codex
```

To install straight into a folder and inherit that folder's agents, skip the two-step and use
`"$SM" skills install <ref> --folder "投资分析框架"` — it lands where the rest of the folder already is.
`--local` / `--git` / `--skillssh` force a source type when the ref is ambiguous, `--name` renames on arrival.

There is no `--sync` / `--sync-preset` flag on `install` — deployment is explicit (`skills deploy`, or
`folders targets` for a whole folder).

**Ref resolution** is deterministic, no path-existence guessing:
1. Starts with `./`, `../`, `/`, or `~/` → local path
2. Contains `://`, ends in `.git`, or starts with `git@` → git URL
3. Matches `owner/repo`, `owner/repo/skill`, or `owner/repo@skill` → skillssh
4. Otherwise → error; pass `--local` / `--git` / `--skillssh` to disambiguate

**Always verify after install** with `skills show <name>` (or `skills status <name>`) so you can report the
folder it landed in and which agents it actually reaches.

## Search

```bash
"$SM" --json skills search "react performance" --limit 5
```

Each result has `install_ref` (paste straight into `skills install`), `installs` (popularity proxy), and `skills_sh_url`. Show the top 1–3 with install counts before installing — anything with 10K+ installs is battle-tested; anything under 100 needs a careful look at the source repo.

## Update / Check

```bash
# Re-fetch one skill (git/skillssh re-clones, local/import re-imports source dir)
"$SM" skills update <skill-name-or-id>

# Re-fetch all eligible skills
"$SM" skills update --all

# Just probe remote revisions, don't touch files
"$SM" skills check --all
```

`check` is the dry-run partner of `update`. Local-only skills (no git source) are reported as `skipped: true`.

**An update replaces the skill's directory wholesale**, so anything written inside it that the new version does not have would be destroyed. When the CLI detects that, it applies nothing and reports the paths instead:

```jsonc
{ "name": "ppt-master", "refreshed": false,
  "held_back_removals": ["library: templates/mine.pptx"] }
```

The field is omitted entirely when nothing is held back, so test for its presence rather than for an empty array. `refreshed: false` *with* `held_back_removals` is **not a failure and not something to retry** — the skill is untouched and still on its old version. Show the user the listed paths and ask. There is no CLI flag to override this; only the desktop app can confirm and proceed, because only a person can say those files are expendable. The paths are prefixed with where they live (`library`, or an agent key for a deployed copy).

Note what this does *not* cover: a file the user edited that the new version also ships is reported as surviving, because its path survives — the update overwrites their edits silently. Warn anyone keeping local modifications inside a skill folder.

## Remove

```bash
# Always preview first when removing more than one
"$SM" skills remove <skill> --dry-run

# --yes is required for the actual delete; --json mode does NOT auto-confirm
"$SM" skills remove <skill> --yes
```

Remove deletes the central-library copy, all synced targets across agents, and the DB row. It's not reversible without re-installing.

## Deploy / Undeploy

```bash
"$SM" skills deploy <skill> --agent claude_code
"$SM" skills undeploy <skill> --agent codex
"$SM" skills deploy <skill-a> <skill-b> --agent codex --dry-run
"$SM" skills deploy <skill> --agent claude_code --agent codex
"$SM" --json skills status <skill>
```

These commands change real managed deployments without deleting the central-library copy or changing folder
membership. `skills enable/disable` are deprecated compatibility commands and do not change deployment;
never use them.

`skills deploy` and `skills undeploy` always require at least one explicit `--agent`, whether the command names one skill or several. `skills status` also reports target rows left by a custom agent that is no longer registered, so stale deployments stay visible and can be cleaned with an explicit undeploy while the row exists.

## Re-asserting deployments (`skills sync`)

`skills sync` re-applies **every folder's** claimed deployment — the repair command when an agent's
directory has drifted from what the library says it should contain. It takes no preset argument.

```bash
# Preview — safe, no writes
"$SM" skills sync --dry-run

# Reconcile all folders across the agents they claim
"$SM" skills sync

# One folder
"$SM" skills sync --folder "投资分析框架"

# Pin to a single agent (when only that agent's directory drifted)
"$SM" skills sync --tool claude_code
```

## Adopt skills installed elsewhere

**What adopt is for:** a directory the user themselves wrote, cloned, or dropped in place, and now wants the
library to own. It is **not** for skills an agent installed through its own internal mechanism — plugins from
a marketplace, or an agent's shipped built-ins. Those stay out of the library permanently and the agent
manages their lifecycle; adopting them would put update, undeploy, and version churn in two places at once.

On this machine that exclusion covers, and only covers:

- `~/.qoder-cn/plugins/cache/**` — Qoder plugin skills (they load from the plugin, not from a skills dir, so
  there is nothing to adopt anyway).
- 千问办公 (`qwen_work`) shipped skills: `dingtalk-*`, `docx` / `pptx` / `xlsx` / `pdf`, `create-skill`,
  `plugin-creator`, `media-generation`, `qw-pages*`. They arrive with the agent and reappear after every
  agent update, so an adoption is silently reverted — they are already recorded in `ignored_skills`.

Everything else unmanaged in an agent directory **is** a candidate — pull it in:

```bash
# Dry-run scan first — lists candidates without writing
"$SM" skills adopt "$AGENT_SKILLS_DIR" --dry-run

# Adopt everything found — each becomes source_type=local (can't auto-update from git)
"$SM" skills adopt "$AGENT_SKILLS_DIR"

# Adopt a single skill and pin it to a git source so `update` works later
"$SM" skills adopt "$AGENT_SKILLS_DIR/some-skill" \
  --git-url https://github.com/owner/agent-skills/tree/main/some-skill

# Or pass --git-subpath explicitly when the URL is just the repo root
"$SM" skills adopt "$AGENT_SKILLS_DIR/some-skill" \
  --git-url https://github.com/owner/agent-skills \
  --git-subpath some-skill

# Skill lives at the repo root? Pass an empty subpath
"$SM" skills adopt "$AGENT_SKILLS_DIR/my-skill" \
  --git-url https://github.com/me/my-skill --git-subpath ""
```

`$AGENT_SKILLS_DIR` is the agent's own skills directory — get it from `"$SM" --json agents list`
(`skills_dir` per agent) instead of hardcoding one agent's private path into a command.

`adopt` auto-excludes anything already in the DB or already a sync target, so it's safe to re-run — and that is also why you can **never** re-type an existing library skill by adopting it: it gets skipped. `--git-url` requires either a URL with a subpath (`/tree/branch/path`) or an explicit `--git-subpath` — without that, future `update` would re-clone the wrong directory, so the CLI refuses to guess.

`--git-url` only applies at the moment of adoption, while the directory is still unmanaged. Once a skill is in the library, use `set-source` below.

## Re-point a skill at a git source

```bash
# Preview: resolves the source and reports whether content differs. It clones to
# a temp dir, but writes nothing to the library or the DB.
"$SM" --json skills set-source <skill> --git-url you/skills --subpath my-skill --dry-run

# A GitHub /tree/ URL carries the branch and subpath already
"$SM" skills set-source <skill> --git-url https://github.com/you/skills/tree/main/my-skill
```

This is how a `local` skill becomes git-backed so `update` works, and how a
skill pointed at the wrong repo gets corrected. It updates the row **in place**,
so the skill id survives and its folder and per-agent
deployments keyed to it all stay intact.

- The flag is `--subpath` here, not `--git-subpath` — that one belongs to `adopt`. Pass `--subpath ""` when the skill is at the repo root, which must itself hold a `SKILL.md`.
- `--branch` overrides a branch encoded in the URL.
- The report carries `content_changed` — a single boolean, **not** a file list. It compares the new source against the hash currently recorded for the library copy, not a fresh hash of the directory on disk, so edits made inside the central copy afterwards do not register as a difference. When it is `false` the library copy is left untouched and those edits survive; copy-mode deployments are re-synced either way.

**`--force` is destructive, and nothing stands between it and the user's files.**
A content difference is refused without it. With it, the whole skill directory is
replaced — staged, swapped in, and the old copy deleted — so anything in the
library copy that the new source does not ship is gone. Unlike `skills update`,
this path has **no** `held_back_removals` check: nothing is withheld, and nothing
asks. `--dry-run` cannot tell you which files are at stake, only that something
differs. Never pass `--force` on the user's behalf — report `content_changed:
true`, say that proceeding overwrites the library copy wholesale, and let them
decide.

## Folders

Folders replace presets. They form a tree (`--parent`), each carries a set of target agents, and a skill
belongs to exactly one of them. There is no `presets` command group and no tag command — verify with
`"$SM" folders --help` before writing any command here.

```bash
"$SM" --json folders list                       # id, name, parent, skill_count, agents, deployed/total pairs
"$SM" --json folders show "投资分析框架"          # its skills, its agents, and how much is actually on disk
"$SM" folders create "新分组" --parent "Agent 规范"
"$SM" folders rename <ref> <name>
"$SM" folders move <ref> --parent <ref>         # omit --parent to move it to the library root
"$SM" folders delete <ref> --dry-run            # org-only; add --undeploy to also take its skills off agents
"$SM" folders delete <ref> --yes
```

Membership is a move, not an add: a skill can sit in only one folder, so `folders add` pulls it out of
whatever folder held it before. There is no `remove-skill` — to unfile a skill, move it to another folder
or leave it unfiled (`skills list --unfiled` finds those).

```bash
"$SM" folders add <folder> <skill>...
```

Deployment lives on the folder, not the skill:

```bash
"$SM" --json folders targets "投资分析框架"                          # read the agent set
"$SM" folders targets "投资分析框架" --agent qwen_work --agent qoder   # replace it; diff is deployed/taken back
"$SM" folders deploy "投资分析框架"                                    # to all installed, enabled coding agents
"$SM" folders deploy "投资分析框架" --agent codex
"$SM" folders undeploy "投资分析框架" --agent claude_code
"$SM" folders undeploy "投资分析框架"                                  # every agent holding target rows
```

The no-`--agent` defaults intentionally differ: deploy targets all installed, enabled coding agents;
undeploy discovers the folder's actual target rows and removes them even when an agent is now disabled,
uninstalled, or no longer registered. Use the no-agent undeploy for "turn this folder off everywhere."

`folders create/rename/move/delete` and `folders add` are organization-only: they never deploy or undeploy
agent files implicitly (only `delete --undeploy` and the explicit `deploy`/`undeploy`/`targets` do).

Useful queries:

```bash
"$SM" --json skills list --unfiled                # skills in no folder
"$SM" --json skills list --folder "搜索"           # includes subfolders' skills
"$SM" --json skills list --deployed-to codex
"$SM" --json skills list --source local
"$SM" --json skills list --query qmd
```

## Library backup (git)

`~/.skills-manager/skills/` is itself a git repo with a backup remote, auto-committed by the app.

```bash
"$SM" --json git status              # is_repo, remote, branch, has_changes, changed_skill_count, ahead/behind
"$SM" git commit -m "why"            # -m is required
"$SM" --json git versions --limit 20 # restorable snapshot tags
"$SM" git restore <tag>              # roll the whole library back — the undo for a bad batch
```

Prefer `git restore <tag>` over hand-running `git` inside the library: the app's own sync refs live there,
and `git prune-sync-refs` exists specifically to clean up refs that a `--mirror`/`--all` push leaked.

## Stop tracking upstream (make a skill `local`)

`update` and the reinstall path replace the **whole** skill directory, so any edit made inside the library
(a rewritten description, a patched script) is lost on the next run. To keep the copy and drop the upstream
link:

```bash
"$SM" --json skills set-source <skill> --local --dry-run   # reports was/now without writing
"$SM" skills set-source <skill> --local                    # source_type -> local, update_status -> local_only
```

After that `skills check <skill>` returns `skipped: true`, and `--git-url` and `--local` are mutually
exclusive. Re-pointing at a different repo is the same command with `--git-url`.

Do **not** try to get there by writing `~/.skills-manager/skills-manager.db` directly: the running desktop
app holds the list in memory and periodically runs `reindex sync metadata`, which restores the old value
within seconds. `set-source --local` goes through the repo lock and rewrites both the row and the
`skills/<id>.json` metadata file, which is what makes it stick.

If you do touch the DB for any reason, take a consistent copy first — the WAL is live, so `cp` misses data:
`sqlite3 skills-manager.db ".backup backup-<purpose>-<timestamp>.db"`.

## Health check

When sync misbehaves or a command errors in a confusing way:

```bash
"$SM" --json repo status    # base dir, skills_dir, db_path, skill_count, folder_count
"$SM" --json agents list    # every agent: installed / enabled / skills_dir / category
"$SM" --json git status     # is the library repo healthy, any uncommitted drift
"$SM" agents enable codex
"$SM" agents disable claude_code
```

`repo status`, `agents list` and `git status` are read-only and are the first checks for "why isn't this
skill showing up in Cursor" questions. `agents disable` is a real mutation: it removes every managed
deployment for that agent. `agents enable` makes the agent available again and re-asserts the folders that
claim it; use an explicit `folders targets` / `skills deploy` afterward when the requested state is additive.

Use `agents disable <agent>` when the user wants the whole Agent integration turned off or wants every
managed skill removed from it. If they only want one skill or one folder taken off while keeping the agent
available for future deployments, use `skills undeploy` or `folders undeploy` instead.

`agents list` shows which agents are actually installed here. Two of them share a destination (`~/.agents/skills`),
so a skill deployed to either appears in the same directory — don't read one copy as two deployments. That
directory does not exist on this machine since 2026-09-28 (it was the other system's state home — see Pitfalls);
deploying to `cline` or `warp` will create it again.

## Typical workflows

### "Find me a skill for X" / "Install a skill that does X"

1. `skills search "X" --limit 5` — show the top 1–3 hits with install counts and source.
2. Run the three-layer duplicate scan from [Install](#install) and show the comparison table. `installs` is a
   popularity proxy, not evidence that nothing on this machine already does the job — the scan is what decides.
3. **Stop and wait for the user to pick.** Do not install the clear winner on their behalf; "clear" is exactly
   where a redundant skill gets added to every future session.
4. On confirmation: `skills install <install_ref>`.
5. Deploy it to the agent(s) the user requested with `skills deploy`.
6. `skills status <name>` to confirm the library and deployment state.

### "What skills do I have?"

```bash
"$SM" --json skills list
```

The `folder`, `folder_id`, `deployed_to`, `source_type`, and `update_status` fields are the informative
ones. There is no `tags` or `presets` field. The `enabled` field is not deployment state — read
`deployed_to` or `skills status <name>` for that.

To answer "what can agent X actually see", prefer `"$SM" --json skills list --deployed-to <agent>` and, when
the library folder layout matters, `"$SM" --json folders show <folder>` (it reports claimed vs on-disk counts).

### "Pull in the skills already installed in my agent directories"

1. Get the directory from `"$SM" --json agents list` (`skills_dir`) — don't hardcode an agent's private path.
2. `skills adopt <dir> --dry-run` (and any other agent dirs the user mentions) — show the candidate list.
3. After user confirms: `skills adopt <dir>`.
4. For any adopted skill where the user knows the original repo, restore the update link with `skills set-source <skill> --git-url ... --subpath ...`.

### "Update everything"

```bash
"$SM" skills check --all     # see what has upstream changes
"$SM" skills update --all    # apply
```

Report which skills actually refreshed (`refreshed: true` in the JSON) vs which were already up-to-date.

## Pitfalls

- **Install succeeded but skill doesn't appear in the agent** → install defaults to library-only. Use `skills deploy <skill> --agent <key>`, or install with `--folder <ref>` to inherit that folder's agents.
- **Folder membership changed but agent files did not** → membership is organization only. Follow with `folders targets` / `folders deploy` or `skills deploy` when the user also asked to make it visible.
- **`folders add` moved the skill out of its old folder** → expected: a skill belongs to exactly one folder. Check the previous owner with `skills status <name>` before moving.
- **`folders delete` left the skills deployed** → deleting a folder is organization-only unless you pass `--undeploy`.
- **`presets …` and `skills tag …` don't exist** → these are not deprecated flags but unknown subcommands; if a recollection of them surfaces, re-derive the command from `"$SM" folders --help` / `"$SM" skills --help`.
- **Adopted skills can't be `update`d from git** → `npx skills add` and manual `git clone` don't leave source metadata, so adopt has to treat them as `local`. Re-point them with `skills set-source`. Do **not** reach for `adopt --git-url` here: adopt only ever creates new library entries, and it fails *late* — `--dry-run` returns `ok: true` with the skill sitting in `skipped`, and only the real run errors with `--git-url requires exactly one adoptable skill, found 0`. Do **not** remove-then-reinstall either — that drops the skill id, and with it its folder and every per-agent deployment.
- **Tried to make a skill `local` by editing the DB** → the running app writes it back. Use `skills set-source <skill> --local`, which also rewrites the metadata file. See "Stop tracking upstream".
- **A real skill directory sits inside an agent's skills dir instead of a symlink** → the only legitimate library on this machine is `~/.skills-manager/skills/`; every agent directory (including `~/.agents/skills`, which is `cline`/`warp`'s `skills_dir`) may hold symlinks only. Self-check: `find ~/.qoder-cn/skills ~/.zcode/skills ~/.agents/skills -maxdepth 1 -type d ! -name skills` — anything printed is a body that never got moved into the library.
- **Someone ran `npx skills …`** → that's a second, structurally identical system with its own library. It treats `~/.agents` as its state home (`skills/` holds the bodies, `.skill-lock.json` is its ledger, version 3) and deploys **relative** symlinks (`../../.agents/skills/<name>`) into agent dirs, so `skills-manager` neither sees nor repairs them. Both ledgers were reconciled on 2026-09-28: the extra library and its lock file were deleted, installs go through `$SM` only. Dangling links are silent — a skill just vanishes with no error.
- **`adopt` left the source directory and created no deployment** → adopt **copies** into the library: it never deletes the source and writes no `skill_targets` row, so `skills status` shows 0 deployments afterwards. Deploy explicitly with `skills deploy`, then remove the source body yourself.
- **Joining a folder pushed a skill to an unexpected agent** → folders claim agents (`folder_targets`), so membership alone subscribes every member to that folder's deployment set. Check `folders show <ref>` (claimed vs on-disk) before adding, and `folders targets <ref> --agent …` to change the claim rather than the membership.
- **The user asks to install a skill the library already holds** → don't. `install` either refuses on `already exists` or, if it gets through, replaces the whole directory. Run `skills show <name>` first: `source_type: local` / `import` with `source_ref: null` means it's in the library but unlinked upstream, and the fix is `skills set-source --git-url … --subpath …`, which keeps the id, folder, and deployments.
- Use `--dry-run` before bulk remove, folder delete, deploy, or undeploy operations. Use `check` before `update`. Take a `sqlite3 ".backup"` copy before any DB touch.
