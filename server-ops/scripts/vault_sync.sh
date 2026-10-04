#!/bin/bash
# server-ops: 本机 vault → 服务器同步（md 内容，排除附件/插件数据）
# 用法: 本机执行 scripts/vault_sync.sh [--dry-run]
#
# ⛔ **本脚本已停用（2026-10-02）**：它推的 `DEST=/home/jianglb/vault` 服务器上看板实例根本不读
#   （`vaultRoot=/home/jianglb/investment-dashboard/vault`，实测那个旧目录在服务器上不存在=以前推进去的孤儿路径已被归档清掉）。
#   而且它**没有** `--delete` 删除量守卫，跑一次就等于往服务器塞一份没人用的第二副本。
#   **正确入口只有一个**：`bash ~/Project/investment-dashboard/src/scripts/deploy-vault.sh`
#   （第一步刷 iCloud→仓库镜像、第二步干跑数删除量、超 200 中止、再推给看板）。下面 3 行是拒绝执行，不是待办。
if [ "${VAULT_SYNC_FORCE:-}" != "1" ]; then
  echo "已停用：请改用 ~/Project/investment-dashboard/src/scripts/deploy-vault.sh（本脚本推的是看板不读的 /home/jianglb/vault）" >&2
  exit 1
fi
set -e

VAULT="/Users/jianglb/Library/Mobile Documents/iCloud~md~obsidian/Documents/投资知识库"
HOST="${SERVER_HOST:-106.55.14.116}"
USER="${SERVER_USER:-jianglb}"
DEST="/home/jianglb/vault"

EXCLUDES=(
  --exclude ".obsidian"
  --exclude ".trash"
  --exclude ".plugin_data"
  --exclude ".space"
  --exclude ".DS_Store"
  --exclude "附件"
  --exclude "__visit_history"
)

EXTRA=()
[ "$1" = "--dry-run" ] && EXTRA=(--dry-run)

echo "▶ 同步 vault → ${USER}@${HOST}:${DEST}"
rsync -az "${EXTRA[@]}" "${EXCLUDES[@]}" \
  -e "ssh -o ConnectTimeout=10 -p 22" \
  "$VAULT/" "${USER}@${HOST}:${DEST}/"

if [ "$1" != "--dry-run" ]; then
  # 2026-10-02 更正此处的假话：服务器上**有**投资看板服务（systemd `investment-dashboard`，唯一后端），
  # 但它读的 vault 在 investment-dashboard/vault，不是这里的 /home/jianglb/vault → 推完也不需要重启
  # （看板每次请求现读文件）。fitness-console 早在 2026-09-21 就全删了，8699 现在是看板的公网入口。
  echo "✅ 已同步到 ${USER}@${HOST}:${DEST}（注意：这个路径看板实例**不读**，正常请走 deploy-vault.sh）"
fi
