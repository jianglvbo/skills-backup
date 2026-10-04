#!/bin/bash
# server-ops: 查服务器各站 + MySQL 状态（只读）
# 用法: scripts/status.sh [--json]
# 2026-09-21: fitness-console 退役删除；新增 nginx 服务与 443 HTTPS 本机探测
set -e
HOST="${SERVER_HOST:-106.55.14.116}"
USER="${SERVER_USER:-jianglb}"

if [ "$1" = "--json" ]; then
  ssh -o ConnectTimeout=10 "$USER@$HOST" bash -s <<'REMOTE'
echo "{"
echo "  \"nginx\": \"$(sudo systemctl is-active nginx)\","
echo "  \"qa\": \"$(sudo systemctl is-active qa)\","
echo "  \"mysql\": \"$(sudo systemctl is-active mysql)\","
echo "  \"redis\": \"$(sudo systemctl is-active redis-investment)\","
echo "  \"qa_http\": \"$(curl -s -o /dev/null -w %{http_code} http://127.0.0.1:8700/)\","
echo "  \"site_https\": \"$(curl -sk -o /dev/null -w %{http_code} --resolve www.jianglvbo.site:443:127.0.0.1 https://www.jianglvbo.site/)\","
cnt=$(sudo mysql -N -e "SELECT CONCAT('bloggers=',(SELECT COUNT(*) FROM investment-dashboard.blogger),' statements=',(SELECT COUNT(*) FROM investment-dashboard.statement),' post_history=',(SELECT COUNT(*) FROM investment-dashboard.post_history),' stocks=',(SELECT COUNT(*) FROM investment-dashboard.stock))")
echo "  \"investment-dashboard_counts\": \"$cnt\""
echo "}"
REMOTE
else
  ssh -o ConnectTimeout=10 "$USER@$HOST" bash -s <<'REMOTE'
echo "== 服务状态 =="
# investment-dashboard 2026-10-02 起是看板后端**唯一**实例，必须查（旧版这里漏了它，只查已退役的 fitness-console）
for s in nginx qa mysql redis-investment investment-dashboard; do
  printf "%-22s %s\n" "$s" "$(sudo systemctl is-active $s)"
done
echo "== HTTP/HTTPS =="
curl -s -o /dev/null -w "qa(8700): %{http_code}\n" http://127.0.0.1:8700/
curl -sk -o /dev/null -w "www.jianglvbo.site(443): %{http_code}\n" --resolve www.jianglvbo.site:443:127.0.0.1 https://www.jianglvbo.site/
echo "== 证书有效期 =="
echo | openssl s_client -connect 127.0.0.1:443 -servername www.jianglvbo.site 2>/dev/null | openssl x509 -noout -dates
echo "== investment-dashboard 行数快检 =="
sudo mysql -N -e "SELECT CONCAT('bloggers=', (SELECT COUNT(*) FROM investment-dashboard.blogger), ' statements=', (SELECT COUNT(*) FROM investment-dashboard.statement), ' post_history=', (SELECT COUNT(*) FROM investment-dashboard.post_history), ' stocks=', (SELECT COUNT(*) FROM investment-dashboard.stock))"
REMOTE
fi
