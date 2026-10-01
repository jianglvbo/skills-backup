# 服务器布局详表（106.55.14.116）

## 主机信息（2026-10-01 只读复核）
- 机型: **腾讯云轻量应用服务器**（非 CVM——CVM 专属 metadata 路径 `placement/group-id`、`instance/renew-flag` 全 404）；instance-id `ins-r8dyzkdm`，**zone `ap-guangzhou-7`（广州七区，不是上海）**
- 规格: Ubuntu 22.04.5 LTS / 4C（Xeon Platinum 8255C @2.50GHz）/ **Mem 3.6 GiB**（1.7 GiB 可用，swap 2 GiB 已用 330 Mi）/ **40 G SSD 单盘 `/dev/vda2`，无第二块数据盘**（要扩只能整机升配）
- 磁盘实测: 用 12 G / **剩 26.9 G（30%）**；inode 2,621,440 个，用 6%（海量小图不是瓶颈）
- 网络: hostname `VM-0-11-ubuntu`，eth0 `10.1.0.11/22`；内网 IP 即 nginx 的 `listen 10.1.0.11:8699` 那个地址
- SSH: `jianglb@106.55.14.116:22`（免密 sudo；ubuntu 备用；root 仅本地）
- 防火墙: ufw inactive（仅云安全组控制公网入口）
- 公网端口: 22 / 8700 / 3306（历史遗留，建议改隧道）/ **80+443 = Nginx HTTPS** / **8699 = 投资看板手机公网入口（2026-09-28 起，腾讯云防火墙早已放行）**；6379 曾放行但看板侧现已 `enabled:false`

## 服务与端口
| 服务 | 端口 | systemd unit | 目录 |
|:---|:---|:---|:---|
| Nginx HTTPS 入口（www.jianglvbo.site） | 80/443 + **8699/8443** | nginx.service | **生效配置是 `/etc/nginx/sites-enabled/jianglvbo.site`（实体文件，与 sites-available 已分叉）**；证书 /etc/nginx/ssl（**2026-12-20 到期**） |
| **投资看板（服务器实例）** | 127.0.0.1:**8698** | **investment-dashboard.service**（active，2026-09-28 起现役） | `/home/jianglb/investment-dashboard`（8.3 M；config.json 600 权限、不随 rsync，`IKB_CONFIG` 指过去）；**手机/公网经 8699 → nginx upstream `investment_dash`**，upstream 里 `127.0.0.1:8702` 是 `ssh -R` 隧道到本机 launchd 实例的 **backup** |
| 文件库只读站 investment-kb | 127.0.0.1:8701 | investment-kb.service | /home/jianglb/investment-kb（`app/` + `vault/` 镜像 137 M + `logs/`），公网经 nginx `location /kb/` + Basic Auth（**443 与 8699 两块里各有一份，改要一起改**） |
| MySQL | 3306 | mysql.service | 数据 **/home/jianglb/mysql**（`/var/lib/mysql` 只是空壳，datadir 由 my.cnf 指定） |
| Redis（看板缓存） | 6379 | redis-investment.service | /home/jianglb/redis —— ⚠ **看板侧 config `enabled:false`，实例在跑但不生效**（2026-10-01 实测本机 config + `deploy-dashboard.sh` 硬写 false） |
| 另一半问答（问） | 8700 | qa.service | /home/jianglb/qa |
| ~~投资控制台（旧版）~~ | ~~8698 旧称~~ | 2026-09-03 删 → **2026-09-28 以 `investment-dashboard` 之名重建回服务器** | 统一叫 investment-dashboard，别再写「仅本地运行」 |
| ~~减脂塑形控制台~~ | ~~8699~~ | fitness-console 退役全删（2026-09-21 用户拍板：unit/目录/fitness 库无残留） | — 但 **8699 这个端口号 2026-09-28 起被投资看板复用**，见上面第二行 |

## 数据与目录
| 路径 | 内容 |
|:---|:---|
| /etc/nginx/ssl | jianglvbo.site_bundle.pem（644）+ .key（600）；TrustAsia DV，SAN=裸域+www，2026-12-20 到期 |
| /home/jianglb/mysql | MySQL 数据目录 692 M（jianglb 用户运行）——**其中 binlog 356 M＝业务库的 23 倍**（`binlog_format=ROW`、`binlog_expire_logs_seconds=2592000` 30 天）；`innodb_file_per_table=ON`、`innodb_buffer_pool_size` 仍是默认 **128 M** 未调优 |
| /home/jianglb/investment-dashboard | 看板服务器实例（server.js + lib/ + web/ + node_modules，`deploy-dashboard.sh` 推） |
| /home/jianglb/investment-kb | 只读站 app/ + vault 镜像（`deploy-kb.sh --vault` 推，**rsync `--delete`**：服务器本地新增文件会被下次推送抹掉） |
| /home/jianglb/fitness-server | 健身动作 GIF 静态站 131 M / 2,648 文件，nginx `location /exercise-media/ { alias …; expires 30d; access_log off; }` **直出、无鉴权**（是「静态图怎么服务」的现成先例，但帖子图不能照抄这条裸出） |
| /home/jianglb/backup | 备份目录 524 K、5 个手工件、**最新一份 2026-09-14**（crontab 与 systemd timers 均无备份项 → 实际零自动备份） |
| /home/jianglb/qa | 问答网页「问」静态站（index.html + server.js，纯静态 node 托管，无数据库；本地源 ~/WorkBuddy/2026-08-26-23-11-26/另一半问答.html） |
| 其他占盘 | `/snap` 2.0 G、docker 镜像 503 M + build cache 432 M、`/var/log` 579 M（journal 537 M sparse 实占 80 M、`btmp.1` 18 M + auth.log 11 M/6.7 M 全是 SSH 爆破噪声）——**要腾地方的第一站** |
| nginx 流量 | 2026-09-30 全天 1,005 请求 / `bytes_sent` 14.1 MB / 均值 14.7 KB；状态码 304=374 200=330 404=209 401=3；UA 以 iPhone Safari 479 条为主（**手机是真用途**）。`client_max_body_size` 全份配置未设（默认 1 M），gzip 只压 text/html，无 CDN（域名直解本机） |

## 数据库连接
- host: 106.55.14.116:3306（**建议**改回 127.0.0.1 + 本机 SSH 隧道：`ssh -L 3306:127.0.0.1:3306 jianglb@106.55.14.116`）
- ⚠ **服务器看板实例连的是 `106.55.14.116`（走 jianglb@'%'）**，不是 127.0.0.1——`jianglb@localhost` 是**另一套口令**，拿服务器 config 里那份去连本机 socket 会 Access denied（实测）
- **业务库 investment-dashboard**：**2026-10-01 实测 46 表、DATA 11.88 MB + INDEX 3.58 MB = 15.45 MB、约 2.07 万行**（表名一律单数；单 dict 表存全部码值；2026-08-31 dict 14→1 整合，2026-09-13 表名/列名规范化；2026-09-23 后陆续新增 `stock_watch`/`console_stat_daily`/`note_item`/`pending_decision` 等，2026-10-01 又加 `xq_thread*` 四表），DDL 权威在 `investment-framework/references/investment-dashboard.sql`（由 `~/Project/investment-dashboard/src/scripts/export-schema.js` 从实库生成——**脚本已移到 `src/scripts/` 下**，可跑 `verify-schema-replay.js` 校验一致性）
- **数据链路（2026-09-28 起双实例并存）**：Mac/iCloud 是**唯一写方**（vault 绝对基准 + 内容编辑），服务器实例与本机实例读同一个 MySQL；服务器实例的 vaultRoot 指 KB 站那份镜像（＝部署快照，在服务器上改内容会被下次 `deploy-kb.sh --vault` 覆盖）。两实例各自跑 03:10 夜间统计重建（幂等无冲突）——**但改了统计表结构/口径必须两边一起部署**，否则旧实例按旧列清单 INSERT 会把新列整列清空（2026-09-29 实踩）
- user: jianglb（@'%' 远程 + @localhost 本机），密码见 credentials/server.md
- root: 仅 localhost，auth_socket 免密（sudo mysql）
- 现役库：investment-dashboard（投资看板，15.45 MB）+ fitness_plan（中文健身动作数据集，31.31 MB，属本地 ~/Project/fitness-plan 仓库 db/schema.sql；`fitness`/`fitness_dev` 已随 fitness-console 退役删除）
- 注意：investment-dashboard **重度使用 MySQL**（博主表 blogger + 运营表 refine/review/coarse/prediction 等）
- **绝对不要把图片/文件当 BLOB 写进这张表**：`binlog_format=ROW` + 30 天过期，实测 15.45 MB 业务数据已产出 356 MB binlog（23 倍）；GB 级图写进去几天就顶穿 40 G 单盘

## MCP 端点（investment-dashboard）
- **两个实例都提供**：本机 `http://127.0.0.1:8698/mcp`、服务器同路径（公网经 8699）。server.js 内置 handleMcpHttp/handleMcpMessage；POST，JSON 单响应，无 SSE
- 鉴权：Bearer Token = 各自 config.json 的 `mcpToken`（无 token 401 / 带 token 200）。**服务器那份由 `deploy-dashboard.sh --config` 从本地 config 原样复制**（同口令同 secret/epoch → 两端会话互通）。**轮换 token 必须同步所有客户端**（ZCode `~/.zcode/cli/config.json` 的 `mcp.servers` + Qoder `~/.qoder-cn/settings.json`），否则表现为 MCP「连不上」而服务端其实好好的
- **闸门对 loopback 同样生效**（`_dispatch` 顶部，无 IP 例外）：服务器上 curl 8698 拿 401 是**正常**，不是故障；脚本要打 `/api/*` 就带 Bearer
- 工具面：2026-10-01 实测 **43 个**（overview / vault 文件 CRUD / blogger CRUD / tags / logs / git_* / console_* / statement_* / post_history / thread_chain / note_item / pending_decision / review_record 等）——早先记的「22 个」是 2026-09-03 的数

## 已知坑（2026-08-30 实测）
1. **GitHub clone investment-notes 必失败**（GnuTLS TLS 中断）→ 一律本机 rsync
2. **Ubuntu mysqld.cnf 的 datadir 是注释行**（`# datadir`）→ 改需 sed 替换注释行本身
3. **改 MySQL datadir 必须同步 AppArmor**（/etc/apparmor.d/local/usr.sbin.mysqld）
4. **investment server.js 原监听 127.0.0.1** → 公网版已改 0.0.0.0（部署时已做）
5. ~~服务器版为只读展示：无 Obsidian/DeepSeek/skill，"打开 Obsidian/评分/加工/审查"按钮不可用~~（**2026-09-03 那版服务器实例的描述，已随 2026-09-28 重建作废**；现役服务器实例是完整 server.js，只是 vaultRoot 指向部署快照、写内容仍要在 Mac 做）
6. 云安全组入口（云控制台）：22/8698/8699/8700/3306/6379 已放行。**新端口一律须在云控制台安全组手动放行**（服务器上无 tccli、无云 API 凭据，自动加不了，只能用户去控制台点）——服务器内 ufw/firewalld 均 inactive、iptables 仅有 YJ-FIREWALL-INPUT（只拉黑特定 IP，不挡端口）。**新静态/新服务部署完公网 curl 超时（HTTP 000）时，99% 是安全组没放行**，别去折腾服务器防火墙
7. ⚠ **8699 不能再关**（2026-09-28 起它是投资看板手机公网入口）；6379 目前看板侧 `enabled:false`，收紧安全组时可以先关它

## 已知坑（2026-09-03 重建实测，当日服务器版已下线，留档防复发）
1. **investment-dashboard 启动前置条件缺一即循环崩溃退出**（exit 1 + Restart=always 刷屏）：① config.json 存在且 vaultRoot 指向的 vault 已同步；② node_modules 含 mysql2（`npm install mysql2`）；③ web/ 子目录布局（WEB_DIR=ROOT/web，app.js/index.html/style.css/assets 必须在 web/ 下）——此坑同样适用于本地 launchd 部署
2. ~~服务器重建配方~~（2026-09-03 晚曾拍板仅本地运行、unit/目录/vault 全删，备份 `/home/jianglb/backup/investment-dashboard-data-20260903.tgz`）→ **2026-09-28 已按新口径重建回服务器**（`deploy-dashboard.sh` 部署、config 走 `--config` 重写、systemd 名 `investment-dashboard`）；当日那份旧配方已不适用，别再照它配
3. **vault→MySQL 同步已批量化**（2026-09-03）：多行 upsert ~20 查询（原逐行 ~1500 次串行往返曾占满连接池导致全站接口 3-7 分钟超时）；若日后改回逐行逻辑务必重新评估连接池压力
4. 服务器 web/ 为子目录布局（与本地一致）；rsync 多源时源写 `web/`（带斜杠）= 拷贝目录**内容**到目标根

## 已知坑（2026-09-21 Nginx 实测）
1. **Nginx 1.18 不支持 `http2 on;` 独立指令**（1.25+ 语法，nginx -t 报 unknown directive）→ 用 `listen 443 ssl http2;`
2. `systemctl reload nginx` 偶现旧 worker 未退（配置已新、行为仍旧）→ 疑似不生效时 `sudo systemctl restart nginx` 兜底

## 待办
- [ ] **备份 cron**（2026-10-01 复核：crontab 与 systemd timers **仍然零自动备份**，`/home/jianglb/backup` 最新一份停在 2026-09-14；binlog 30 天是唯一可恢复窗口）。原口径「每周 mysqldump」偏弱——若要撑图片/原文永久留档，至少每日
- [ ] 用户在云控制台安全组放行 80/443（HTTPS 公网生效前置）。⚠ 443 目前被链路按 SNI 注 RST（疑备案未接入本节点），手机能用的只有 **8699**；备案下来后把 8699/8443 的 `location` 挪回 443 并删掉这两个口。**8699 在备案前绝不能关**
- [ ] 2026-12-20 证书到期前续期（腾讯云重申请 → 覆盖 /etc/nginx/ssl → nginx -t + reload）
- [ ] SSH 密钥登录（禁密码）
- [ ] MySQL 3306 改回本机 + 隧道（或独立强密码）
- [ ] 密码分离（SSH/MySQL）
- [ ] 腾讯云轻量**流量包配额**：机器上查不到（无 CDN、无云 API 凭据），要跨控制台核对；当前出站仅 14 MB/日，量级无感
- [x] 数据落地 MySQL（2026-08-30 完成：investment-dashboard 21 表 + 迁移脚本 + server.js MySQL 化；**2026-10-01 已长到 46 表**）
- [x] 看板回服务器（2026-09-28 用户拍板，推翻 2026-09-03「仅本地运行」；systemd `investment-dashboard` active，8699 手机入口已通）
