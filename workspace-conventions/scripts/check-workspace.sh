#!/usr/bin/env bash
# 工作区协作规范自检：纯只读，不改任何文件。有 FAIL 则退出码 1。
# 用法：check-workspace.sh [工作区根目录]     （缺省＝当前目录）
#
# 两份名单的权威来源是工作区 AGENTS.md §目录 里的注释锚点——脚本读它，不复制名单：
#   <!-- agent-cells: qoder-cn zcode qwenworkcn claude -->
#   <!-- root-entries: AGENTS.md README.md src out .gitignore .agents -->
# out/ 分格缺锚点时退回环境变量 WORKSPACE_AGENT_DIRS；根目录名单缺锚点只 WARN。
set -u

ROOT="${1:-$(pwd)}"
cd "$ROOT" 2>/dev/null || { echo "FAIL root_not_found $ROOT"; exit 1; }

FALLBACK_CELLS="${WORKSPACE_AGENT_DIRS:-qoder-cn zcode qwenworkcn claude}"
fails=0
warns=0

pass() { echo "PASS $1"; }
fail() { echo "FAIL $1"; fails=$((fails + 1)); }
warn() { echo "WARN $1"; warns=$((warns + 1)); }

# 从 AGENTS.md 取一行注释锚点的内容；取不到输出空
anchor() {
  [ -f AGENTS.md ] || return 0
  awk -v k="$1" '
    { i = index($0, "<!-- " k ": ")
      if (i > 0) {
        s = substr($0, i + length("<!-- " k ": ")); j = index(s, " -->")
        if (j > 0) { print substr(s, 1, j - 1); exit }
      } }' AGENTS.md
}

# --- 1. 三份文档分工 ---
[ -f AGENTS.md ] && pass agents_md_present || fail agents_md_missing
[ -f README.md ] && pass readme_present || warn readme_missing_agent_facing_docs

if [ -f AGENTS.md ]; then
  grep -q "开工与收工" AGENTS.md && pass sec_handoff || fail sec_handoff_missing
  grep -q "一处一义" AGENTS.md && pass sec_ownership || fail sec_ownership_missing
  grep -q "产物落点" AGENTS.md && pass sec_artifacts || fail sec_artifacts_missing
  grep -q "分支命名" AGENTS.md && pass sec_branch_naming || warn sec_branch_naming_missing_2026_10_01_rule
fi

# --- 2. .gitignore：产物与 skill 镜像层不进 git ---
if [ -f .gitignore ]; then
  grep -qE '^/?out/?$' .gitignore && pass gitignore_out || fail gitignore_out_missing
  grep -qE '^\.agents/?$' .gitignore && pass gitignore_agents || fail gitignore_agents_missing
else
  fail gitignore_missing
fi

# --- 3. 账实相符：被忽略的东西不能同时在 git 索引里 ---
if command -v git >/dev/null 2>&1 && git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  tracked=$(git ls-files -- out .agents 2>/dev/null | head -5)
  if [ -z "$tracked" ]; then
    pass not_tracked
  else
    echo "$tracked" | while IFS= read -r f; do echo "FAIL tracked_in_git $f"; done
    fail tracked_in_git_see_lines_above
  fi
  # --- 3b. 当前分支名（规范 #7；主线不查，WARN 不拦）---
  cur=$(git branch --show-current 2>/dev/null)
  case "$cur" in
    "" | master | main | release | develop | trunk) ;;
    *)
      if printf '%s' "$cur" | grep -qE '^(feat|fix|chore|docs)(/[a-z0-9][a-z0-9-]*)+$'; then
        pass "branch_name_ok $cur"
      else
        warn "branch_name_off_convention $cur（期望 <feat|fix|chore|docs>/<scope->英文slug，见 workspace-conventions 规范 #7）"
      fi
      ;;
  esac
else
  warn git_repo_absent_skipped_tracked_check
fi

# --- 4. out/ 分格命名（FAIL：分格名是硬约束）---
cells=$(anchor agent-cells)
if [ -n "$cells" ]; then
  pass agent_cells_from_anchor
else
  cells="$FALLBACK_CELLS"
  warn agent_cells_anchor_missing_using_fallback
fi
if [ -d out ]; then
  found=0
  for d in ./out/*/; do
    [ -d "$d" ] || continue
    found=1
    name=${d#./out/}
    name=${name%/}
    ok=0
    for a in $cells; do
      [ "$name" = "$a" ] && ok=1
    done
    [ "$ok" = 1 ] && pass "out_cell_known $name" || fail "out_cell_unlisted $name"
  done
  [ "$found" = 0 ] && warn out_dir_empty
else
  warn out_dir_absent_create_on_first_artifact
fi

# --- 5. 根目录固定名单（WARN：散落只提示不拦）---
root_list=$(anchor root-entries)
if [ -n "$root_list" ]; then
  pass root_entries_from_anchor
  for e in ./* ./.[!.]*; do
    [ -e "$e" ] || continue
    base=${e#./}
    case "$base" in
      .git | .DS_Store | *.swp | *~) continue ;;
    esac
    hit=0
    for r in $root_list; do
      [ "$base" = "$r" ] && hit=1
    done
    [ "$hit" = 1 ] || warn "root_entry_unlisted $base"
  done
else
  warn root_entries_anchor_missing
fi

# --- 6. 悬空软链：静默失效，必须显式测 ---
check_links() {
  layer="$1"
  [ -d "$layer" ] || return 0
  dead_found=0
  while IFS= read -r l; do
    warn "dangling_link $l"
    dead_found=1
  done < <(find "$layer" -maxdepth 1 -type l ! -exec test -e {} \; -print 2>/dev/null)
  [ "$dead_found" = 1 ] || pass "links_ok $layer"
}
check_links ".agents/skills"
check_links "$HOME/.agents/skills"

echo "SUMMARY fails=$fails warns=$warns"
[ "$fails" -eq 0 ] || exit 1
exit 0
