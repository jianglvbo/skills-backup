# CLI reference — per-command details

Loaded on demand: read the section for the command group you are about to run.
`$SM` is the CLI path printed by the resolve step in SKILL.md — substitute it into every
command; each command runs in a new shell, so the variable never carries over.

## skills install

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

# Straight into a folder, inheriting that folder's agents (skips the two-step)
"$SM" skills install <ref> --folder "投资分析框架"
```

`--local` / `--git` / `--skillssh` force a source type, `--name` renames on arrival.
There is no `--sync` / `--sync-preset` flag — deployment is explicit (`skills deploy`,
or `folders targets` for a whole folder).

**Ref resolution** is deterministic, no path-existence guessing:
1. Starts with `./`, `../`, `/`, or `~/` → local path
2. Contains `://`, ends in `.git`, or starts with `git@` → git URL
3. Matches `owner/repo`, `owner/repo/skill`, or `owner/repo@skill` → skillssh
4. Otherwise → error; pass `--local` / `--git` / `--skillssh` to disambiguate

**Default is library-only** — the skill enters the DB but appears in no agent. Prefer an
explicit follow-up deployment so scope is unambiguous:

```bash
"$SM" skills deploy <skill> --agent claude_code --agent codex
```

**Always verify after install** with `skills show <name>` (or `skills status <name>`) so you
can report the folder it landed in and which agents it actually reaches.

## skills search

```bash
"$SM" --json skills search "react performance" --limit 5
```

Each result has `install_ref` (paste straight into `skills install`), `installs` (popularity
proxy), and `skills_sh_url`. Show the top 1–3 with install counts before installing — 10K+
installs is battle-tested; under 100 needs a careful look at the source repo.

## skills update / check

```bash
"$SM" skills update <skill-name-or-id>   # re-fetch one (git/skillssh re-clone; local/import re-import)
"$SM" skills update --all
"$SM" skills check --all                 # probe remote revisions only, touch nothing
```

`check` is the dry-run partner of `update`. Local-only skills are reported `skipped: true`.

**An update replaces the skill's directory wholesale**, so anything written inside it that the
new version does not have would be destroyed. When the CLI detects that, it applies nothing and
reports the paths instead:

```jsonc
{ "name": "ppt-master", "refreshed": false,
  "held_back_removals": ["library: templates/mine.pptx"] }
```

The field is omitted entirely when nothing is held back — test for its presence, not for an
empty array. `refreshed: false` *with* `held_back_removals` is **not a failure and not something
to retry**: the skill is untouched and still on its old version. Show the user the listed paths
and ask. There is no CLI flag to override; only the desktop app can confirm, because only a
person can say those files are expendable. Paths are prefixed with where they live (`library`,
or an agent key for a deployed copy).

What this does *not* cover: a file the user edited that the new version also ships survives by
path — the update overwrites their edits silently. Warn anyone keeping local modifications
inside a skill folder.

## skills remove

```bash
"$SM" skills remove <skill> --dry-run    # always preview when removing more than one
"$SM" skills remove <skill> --yes        # --yes required; --json mode does NOT auto-confirm
```

Remove deletes the central-library copy, all synced targets across agents, and the DB row.
Not reversible without re-installing.

## skills deploy / undeploy / status

```bash
"$SM" skills deploy <skill> --agent claude_code
"$SM" skills undeploy <skill> --agent codex
"$SM" skills deploy <skill-a> <skill-b> --agent codex --dry-run
"$SM" skills deploy <skill> --agent claude_code --agent codex
"$SM" --json skills status <skill>
```

These change real managed deployments without deleting the central-library copy or changing
folder membership. `skills enable/disable` are deprecated compatibility commands and do not
change deployment — never use them.

`deploy`/`undeploy` always require at least one explicit `--agent`, whether naming one skill or
several. `skills status` also reports target rows left by a custom agent that is no longer
registered, so stale deployments stay visible and can be cleaned with an explicit `undeploy`
while the row exists.

**A deploy that would overwrite something that is not ours is refused outright** — nothing at
those paths is deleted, nothing else in the batch is applied. The failure is machine-readable:

```json
{"ok": false, "code": "TARGET_CONFLICT", "kind": "target_conflict",
 "message": "Refusing to deploy: 1 of 2 target(s) …",
 "details": {"conflicts": [{"path": "/Users/me/.claude/skills/db",
                            "reason": "is not a managed deployment"}]}}
```

Tell the user which path is in the way, that its contents are untouched, and offer the two ways
out: adopt it into the library (`skills adopt`), or move it aside and retry. Never delete it
for them.

## skills sync

`skills sync` re-applies **every folder's** claimed deployment — the repair command when an
agent's directory has drifted from what the library says it should contain.

```bash
"$SM" skills sync --dry-run              # preview — safe, no writes
"$SM" skills sync                        # reconcile all folders across the agents they claim
"$SM" skills sync --folder "投资分析框架"   # one folder
"$SM" skills sync --tool claude_code     # pin to a single agent
```

## skills adopt

**What adopt is for:** a directory the user themselves wrote, cloned, or dropped in place, and
now wants the library to own. It is **not** for skills an agent installed through its own
internal mechanism — plugins from a marketplace, or an agent's shipped built-ins. Those stay
out of the library permanently and the agent manages their lifecycle; adopting them would put
update, undeploy, and version churn in two places at once.

On this machine that exclusion covers, and only covers:

- `~/.qoder-cn/plugins/cache/**` — Qoder plugin skills (they load from the plugin, not from a
  skills dir, so there is nothing to adopt anyway).
- 千问办公 (`qwen_work`) shipped skills: `dingtalk-*`, `docx` / `pptx` / `xlsx` / `pdf`,
  `create-skill`, `plugin-creator`, `media-generation`, `qw-pages*`. They arrive with the agent
  and reappear after every agent update, so an adoption is silently reverted — they are already
  recorded in `ignored_skills`.

Everything else unmanaged in an agent directory **is** a candidate — pull it in:

```bash
"$SM" skills adopt "$AGENT_SKILLS_DIR" --dry-run        # lists candidates without writing
"$SM" skills adopt "$AGENT_SKILLS_DIR"                  # each becomes source_type=local
"$SM" skills adopt "$AGENT_SKILLS_DIR/some-skill" \
  --git-url https://github.com/owner/agent-skills/tree/main/some-skill
"$SM" skills adopt "$AGENT_SKILLS_DIR/some-skill" \
  --git-url https://github.com/owner/agent-skills --git-subpath some-skill
"$SM" skills adopt "$AGENT_SKILLS_DIR/my-skill" \
  --git-url https://github.com/me/my-skill --git-subpath ""   # skill at repo root
```

`$AGENT_SKILLS_DIR` comes from `"$SM" --json agents list` (`skills_dir` per agent) — don't
hardcode one agent's private path into a command.

`adopt` auto-excludes anything already in the DB or already a sync target, so it's safe to
re-run — and that is why you can **never** re-type an existing library skill by adopting it: it
gets skipped. `--git-url` requires either a URL with a subpath (`/tree/branch/path`) or an
explicit `--git-subpath` — without that, future `update` would re-clone the wrong directory, so
the CLI refuses to guess. `--git-url` only applies at the moment of adoption; once in the
library, use `set-source`.

**Adopt copies**: it never deletes the source and writes no `skill_targets` row, so
`skills status` shows 0 deployments afterwards. Deploy explicitly, then remove the source body
yourself.

## skills set-source

Re-point a skill at a source, in place — the skill id survives, and its folder and per-agent
deployments keyed to it all stay intact.

```bash
# Preview: resolves the source, reports whether content differs. Clones to a temp dir,
# writes nothing to the library or the DB.
"$SM" --json skills set-source <skill> --git-url you/skills --subpath my-skill --dry-run

# A GitHub /tree/ URL carries the branch and subpath already
"$SM" skills set-source <skill> --git-url https://github.com/you/skills/tree/main/my-skill

# Drop the upstream link entirely (see "Stop tracking upstream" in SKILL.md)
"$SM" skills set-source <skill> --local
```

- The flag is `--subpath` here, not `--git-subpath` — that one belongs to `adopt`. Pass
  `--subpath ""` when the skill is at the repo root, which must itself hold a `SKILL.md`.
- `--branch` overrides a branch encoded in the URL.
- `--git-url` and `--local` are mutually exclusive.
- The report carries `content_changed` — a single boolean, **not** a file list. It compares the
  new source against the hash currently recorded for the library copy, not a fresh hash of the
  directory on disk, so edits made inside the central copy afterwards do not register as a
  difference. When it is `false` the library copy is left untouched and those edits survive;
  copy-mode deployments are re-synced either way.

**`--force` is destructive, and nothing stands between it and the user's files.**
A content difference is refused without it. With it, the whole skill directory is replaced —
staged, swapped in, and the old copy deleted — so anything in the library copy that the new
source does not ship is gone. Unlike `skills update`, this path has **no** `held_back_removals`
check: nothing is withheld, and nothing asks. `--dry-run` cannot tell you which files are at
stake, only that something differs. Never pass `--force` on the user's behalf — report
`content_changed: true`, say that proceeding overwrites the library copy wholesale, and let
them decide.

## folders

Folders replace presets. They form a tree (`--parent`), each carries a set of target agents,
and a skill belongs to exactly one of them. There is no `presets` command group and no tag
command — verify with `"$SM" folders --help` before writing any command.

```bash
"$SM" --json folders list                       # id, name, parent, skill_count, agents, deployed/total pairs
"$SM" --json folders show "投资分析框架"          # its skills, its agents, claimed vs on-disk counts
"$SM" folders create "新分组" --parent "Agent 规范"
"$SM" folders rename <ref> <name>
"$SM" folders move <ref> --parent <ref>         # omit --parent to move it to the library root
"$SM" folders delete <ref> --dry-run            # org-only; add --undeploy to also take its skills off agents
"$SM" folders delete <ref> --yes
```

Membership is a move, not an add: a skill can sit in only one folder, so `folders add` pulls it
out of whatever folder held it before. There is no `remove-skill` — to unfile a skill, move it
to another folder or leave it unfiled (`skills list --unfiled` finds those).

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

The no-`--agent` defaults intentionally differ: `deploy` targets all installed, enabled coding
agents; `undeploy` discovers the folder's actual target rows and removes them even when an agent
is now disabled, uninstalled, or no longer registered. Use the no-agent undeploy for "turn this
folder off everywhere."

`folders create/rename/move/delete` and `folders add` are organization-only: they never deploy
or undeploy agent files implicitly (only `delete --undeploy` and the explicit
`deploy`/`undeploy`/`targets` do).

## Library backup (git)

`~/.skills-manager/skills/` is itself a git repo with a backup remote, auto-committed by the app.

```bash
"$SM" --json git status              # is_repo, remote, branch, has_changes, changed_skill_count, ahead/behind
"$SM" git commit -m "why"            # -m is required
"$SM" --json git versions --limit 20 # restorable snapshot tags
"$SM" git restore <tag>              # roll the whole library back — the undo for a bad batch
```

Prefer `git restore <tag>` over hand-running `git` inside the library: the app's own sync refs
live there, and `git prune-sync-refs` exists specifically to clean up refs that a
`--mirror`/`--all` push leaked.

## Health check

```bash
"$SM" --json repo status    # base dir, skills_dir, db_path, skill_count, folder_count
"$SM" --json agents list    # every agent: installed / enabled / skills_dir / category
"$SM" --json git status     # is the library repo healthy, any uncommitted drift
"$SM" agents enable codex
"$SM" agents disable claude_code
```

`repo status`, `agents list` and `git status` are read-only and are the first checks for "why
isn't this skill showing up in Cursor" questions. `agents disable` is a real mutation: it
removes every managed deployment for that agent. `agents enable` makes the agent available
again and re-asserts the folders that claim it; use an explicit `folders targets` /
`skills deploy` afterward when the requested state is additive.

Use `agents disable <agent>` when the user wants the whole Agent integration turned off or
wants every managed skill removed from it. If they only want one skill or one folder taken off
while keeping the agent available, use `skills undeploy` or `folders undeploy` instead.

`agents list` shows which agents are actually installed here. Two of them share a destination
(`~/.agents/skills`), so a skill deployed to either appears in the same directory — don't read
one copy as two deployments. That directory does not exist on this machine since 2026-09-28 (it
was the other system's state home — see Pitfalls in SKILL.md); deploying to `cline` or `warp`
will create it again.

## Useful queries

```bash
"$SM" --json skills list --unfiled                # skills in no folder
"$SM" --json skills list --folder "搜索"           # includes subfolders' skills
"$SM" --json skills list --deployed-to codex
"$SM" --json skills list --source local
"$SM" --json skills list --query qmd
```

`list`/`show` return `folder`, `folder_id`, `deployed_to`, `source_type`, `update_status`.
There is no `tags` or `presets` field. `enabled` is not deployment state — read `deployed_to`
or `skills status <name>` for that.

To answer "what can agent X actually see", prefer `--deployed-to <agent>` and, when the folder
layout matters, `folders show <folder>` (claimed vs on-disk counts).

## Pitfalls (long tail)

- **A real skill directory sits inside an agent's skills dir instead of a symlink** → the only
  legitimate library on this machine is `~/.skills-manager/skills/`; every agent directory
  (including `~/.agents/skills`, which is `cline`/`warp`'s `skills_dir`) may hold symlinks
  only. Self-check: `find ~/.qoder-cn/skills ~/.zcode/skills ~/.agents/skills -maxdepth 1 -type d ! -name skills` — anything printed is a body that never got moved into the library.
- **Someone ran `npx skills …`** → that's a second, structurally identical system with its own
  library. It treats `~/.agents` as its state home (`skills/` holds the bodies,
  `.skill-lock.json` is its ledger, version 3) and deploys **relative** symlinks
  (`../../.agents/skills/<name>`) into agent dirs, so `skills-manager` neither sees nor repairs
  them. Both ledgers were reconciled on 2026-09-28: the extra library and its lock file were
  deleted, installs go through `$SM` only. Dangling links are silent — a skill just vanishes
  with no error.
- **Tried to make a skill `local` by editing the DB** → the running app writes it back. Use
  `skills set-source <skill> --local`, which also rewrites the metadata file. If you touch the
  DB for any reason, take a consistent copy first — the WAL is live, so `cp` misses data:
  `sqlite3 skills-manager.db ".backup backup-<purpose>-<timestamp>.db"`.
- **`adopt --git-url` on an already-managed skill fails late** → adopt only ever creates new
  library entries; `--dry-run` returns `ok: true` with the skill sitting in `skipped`, and only
  the real run errors with `--git-url requires exactly one adoptable skill, found 0`. Do **not**
  remove-then-reinstall either — that drops the skill id, and with it its folder and every
  per-agent deployment; the fix is `skills set-source`.
- **Joining a folder pushed a skill to an unexpected agent** → folders claim agents
  (`folder_targets`), so membership alone subscribes every member to that folder's deployment
  set. Check `folders show <ref>` (claimed vs on-disk) before adding, and
  `folders targets <ref> --agent …` to change the claim rather than the membership.
- **The user asks to install a skill the library already holds** → don't. `install` either
  refuses on `already exists` or, if it gets through, replaces the whole directory. Run
  `skills show <name>` first: `source_type: local` / `import` with `source_ref: null` means
  it's in the library but unlinked upstream, and the fix is
  `skills set-source --git-url … --subpath …`, which keeps the id, folder, and deployments.

Use `--dry-run` before bulk remove, folder delete, deploy, or undeploy operations. Use `check`
before `update`.
