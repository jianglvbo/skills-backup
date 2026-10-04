# 服务器布局详表（106.55.14.116）

## 主机信息（2026-10-01 只读复核）
- 机型: **腾讯云轻量应用服务器**（非 CVM——CVM 专属 metadata 路径 `placement/group-id`、`instance/renew-flag` 全 404）；instance-id `ins-r8dyzkdm`，**zone `ap-guangzhou-7`（广州七区，不是上海）**
- 规格: Ubuntu 22.04.5 LTS / 4C（Xeon Platinum 8255C @2.50GHz）/ **Mem 3.6 GiB**（1.7 GiB 可用，swap 2 GiB 已用 330 Mi）/ **40 G SSD 单盘 `/dev/vda2`，无第二块数据盘**（要扩只能整机升配）
- 磁盘实测: 用 12 G / **剩 26.9 G（30%）**；inode 2,621,440 个，用 6%（海量小图不是瓶颈）
- 网络: hostname `VM-0-11-ubuntu`，eth0 `10.1.0.11/22`；内网 IP 即 nginx 的 `listen 10.1.0.11:8699` 那个地址
- SSH: `jianglb@106.55.14.116:22`（免密 sudo；ubuntu 备用；root 仅本地）
- 防火墙: ufw inactive（仅云安全组控制公网入口）
- 公网端口: 22 / 8700 / 3306（历史遗留，建议改隧道）/ **80+443 = Nginx HTTPS** / **8699 = 投资看板公网入口（2026-09-28 起，腾讯云防火墙早已放行；2026-10-02 起它同时是本机浏览器唯一入口）**；6379 **已于 2026-10-02 收到 `bind 127.0.0.1`**（实测公网 `nc -z` 不可连，安全组那条放行可以收回），8698 实测公网也不可达（000，被云防火墙挡；node 本身绑的是 0.0.0.0，别把"防火墙挡着"当成设计）

## 服务与端口
| 服务 | 端口 | systemd unit | 目录 |
|:---|:---|:---|:---|
| Nginx HTTPS 入口（www.jianglvbo.site） | 80/443 + **8699/8443** | nginx.service | **生效配置是 `/etc/nginx/sites-enabled/jianglvbo.site`（实体文件，与 sites-available 已分叉）**；证书 /etc/nginx/ssl（**2026-12-20 到期**） |
| **投资看板后端（唯一一套）** | 监听 `0.0.0.0:`**8698**（公网实测不可达） | **investment-dashboard.service**（active） | `/home/jianglb/investment-dashboard/{frontend,dashboard-server,vault,config,logs}`；`IKB_CONFIG=/home/jianglb/investment-dashboard/config/config.json`（600、不随 rsync），`dashboard-server/web -> ../frontend` 软链（**手动重建后端目录时忘了建它，`/login` 直接 404**）。入口：手机与 Mac 浏览器都走 8699 → nginx（静态 `root` 直出 + `auth_request` 闸门）→ upstream `investment_dash`。**upstream 里那条 `127.0.0.1:8702 backup`（`ssh -R` 回落本机）已于 2026-10-02 删除**——本机不再有后端，也就是**没有回落**（改前快照 `~/archive/2026-10-02/nginx-before-drop-8702`） |
| ~~文件库只读站 investment-kb~~ | ~~127.0.0.1:8701~~ | 2026-10-02 **整体退役**（unit 删除、`/home/jianglb/investment-kb` 归档、nginx `location /kb/` 在 **443 与 8699 两块一起摘**）。依据：10 个 access.log 档共 53 次命中，当天仅剩验收脚本自己的 curl。`deploy-kb.sh` → **`deploy-vault.sh`** |
| MySQL | 3306 | mysql.service | 数据 **/home/jianglb/mysql**（`/var/lib/mysql` 只是空壳，datadir 由 my.cnf 指定） |
| Redis（看板 L2 缓存） | `127.0.0.1:`6379 | redis-investment.service | /home/jianglb/redis —— **2026-10-02 起 `enabled:true` 且 `bind` 从 0.0.0.0 收到 127.0.0.1**（公网实测不可连）；`maxmemory 256mb` + `allkeys-lru`、无持久化、**ttl=60s**（`--config` 模板里显式写 host/port/ttl：`src/config.json` 根本没有 redis 段，顺着 `local.redis` 带只会得到 `{}`） |
| 另一半问答（问） | 8700 | qa.service | /home/jianglb/qa |
| ~~投资控制台（旧版）~~ | ~~8698 旧称~~ | 2026-09-03 删 → **2026-09-28 以 `investment-dashboard` 之名重建回服务器** | 统一叫 investment-dashboard，别再写「仅本地运行」 |
| ~~减脂塑形控制台~~ | ~~8699~~ | fitness-console 退役全删（2026-09-21 用户拍板：unit/目录/fitness 库无残留） | — 但 **8699 这个端口号 2026-09-28 起被投资看板复用**，见上面第二行 |

## 数据与目录
| 路径 | 内容 |
|:---|:---|
| /etc/nginx/ssl | jianglvbo.site_bundle.pem（644）+ .key（600）；TrustAsia DV，SAN=裸域+www，2026-12-20 到期 |
| /home/jianglb/mysql | MySQL 数据目录 692 M（jianglb 用户运行）——**其中 binlog 356 M＝业务库的 23 倍**（`binlog_format=ROW`、`binlog_expire_logs_seconds=2592000` 30 天）；`innodb_file_per_table=ON`、`innodb_buffer_pool_size` 仍是默认 **128 M** 未调优 |
| /home/jianglb/investment-dashboard | 看板**唯一**后端：`dashboard-server/`（server.js + lib/ + node_modules，`deploy-dashboard.sh` 推）+ `frontend/`（nginx root 直出的那份静态，同一脚本一起刷）+ `config/` + `vault/` + `logs/` |
| /home/jianglb/investment-dashboard/vault | Mac/iCloud 的**单向只读快照**（135 M / 1570 文件 / 650 md），`deploy-vault.sh` 推，**rsync `--delete`**：服务器本地新增文件会被下次推送抹掉。旧目录 `/home/jianglb/investment-kb` 已随只读站退役归档 |
| /home/jianglb/archive/2026-10-02 | 本轮全部回退锚：`nginx-before-sep`（分离前的整份 sites-enabled）、`nginx-before-drop-8702`、撤掉的 Java 闸门单元与 jar、退役的 investment-kb |
| /home/jianglb/fitness-server | 健身动作 GIF 静态站 131 M / 2,648 文件，nginx `location /exercise-media/ { alias …; expires 30d; access_log off; }` **直出、无鉴权**（是「静态图怎么服务」的现成先例，但帖子图不能照抄这条裸出） |
| /home/jianglb/backup | 备份目录 524 K、5 个手工件、**最新一份 2026-09-14**（crontab 与 systemd timers 均无备份项 → 实际零自动备份） |
| /home/jianglb/qa | 问答网页「问」静态站（index.html + server.js，纯静态 node 托管，无数据库；本地源 ~/WorkBuddy/2026-08-26-23-11-26/另一半问答.html） |
| 其他占盘 | `/snap` 2.0 G、docker 镜像 503 M + build cache 432 M、`/var/log` 579 M（journal 537 M sparse 实占 80 M、`btmp.1` 18 M + auth.log 11 M/6.7 M 全是 SSH 爆破噪声）——**要腾地方的第一站** |
| nginx 流量 | 2026-09-30 全天 1,005 请求 / `bytes_sent` 14.1 MB / 均值 14.7 KB；状态码 304=374 200=330 404=209 401=3；UA 以 iPhone Safari 479 条为主（**手机是真用途**）。`client_max_body_size` 全份配置未设（默认 1 M），gzip 只压 text/html，无 CDN（域名直解本机） |

## 数据库连接
- host: 106.55.14.116:3306（**建议**改回 127.0.0.1 + 本机 SSH 隧道：`ssh -L 3306:127.0.0.1:3306 jianglb@106.55.14.116`）
- ⚠ **服务器看板实例连的是 `106.55.14.116`（走 jianglb@'%'）**，不是 127.0.0.1——`jianglb@localhost` 是**另一套口令**，拿服务器 config 里那份去连本机 socket 会 Access denied（实测）
- **业务库 investment-dashboard**：**2026-10-01 实测 46 表、DATA 11.88 MB + INDEX 3.58 MB = 15.45 MB、约 2.07 万行**（表名一律单数；单 dict 表存全部码值；2026-08-31 dict 14→1 整合，2026-09-13 表名/列名规范化；2026-09-23 后陆续新增 `stock_watch`/`console_stat_daily`/`note_item`/`pending_decision` 等，2026-10-01 又加 `xq_thread*` 四表），DDL 权威在 `investment-framework/references/investment-dashboard.sql`（由 `~/Project/investment-dashboard/src/scripts/export-schema.js` 从实库生成——**脚本已移到 `src/scripts/` 下**，可跑 `verify-schema-replay.js` 校验一致性）
- **数据链路（2026-10-02 起只有一套后端）**：Mac/iCloud 是**内容与 vault 的唯一写方**（真相），但**后端进程只有服务器那一个**——
  agent 的 MCP、手机、本机浏览器全都打它，MySQL 也在这台机上闭环。服务器的 `vaultRoot` 指快照，
  在服务器上改内容会被下次 `deploy-vault.sh` 的 `--delete` 抹掉。夜间统计重算只有这一台跑（旧「两实例各自重算、幂等无冲突」作废）；
  **改了统计表结构/口径必须马上 `deploy-dashboard.sh`**——只有这一台跑 03:10 重算，它要是跑旧代码，按旧列清单 INSERT 就把新列整列清空
  （2026-09-29 实踩 `blogger`；2026-10-02 新加的 `ev_count` 同理，那条还没验收）
- user: jianglb（@'%' 远程 + @localhost 本机），密码见 credentials/server.md
- root: 仅 localhost，auth_socket 免密（sudo mysql）
- 现役库：investment-dashboard（投资看板，15.45 MB）+ fitness_plan（中文健身动作数据集，31.31 MB，属本地 ~/Project/fitness-plan 仓库 db/schema.sql；`fitness`/`fitness_dev` 已随 fitness-console 退役删除）
- 注意：investment-dashboard **重度使用 MySQL**（博主表 blogger + 运营表 refine/review/coarse/prediction 等）
- **绝对不要把图片/文件当 BLOB 写进这张表**：`binlog_format=ROW` + 30 天过期，实测 15.45 MB 业务数据已产出 356 MB binlog（23 倍）；GB 级图写进去几天就顶穿 40 G 单盘

## MCP 端点（investment-dashboard）
- **只有一个端点**：`https://www.jianglvbo.site:8699/mcp`（2026-10-02 起本机 `http://127.0.0.1:8698/mcp` 那台随 launchd 服务一起消失；`run-tunnel.sh` 与 `com.investment-tunnel.plist` 也已从仓库删）。server.js 内置 handleMcpHttp/handleMcpMessage；POST，JSON 单响应，无 SSE。**实测代价**：同一次工具调用本机 4–11ms → 公网 0.35–2.0s（TLS + RTT 是下界，约 0.33s），批量环节按每次 +0.33s 估。
- 鉴权：Bearer Token = config 的 `mcpToken`（无 token 401 / 带 token 200）。服务器那份由 `deploy-dashboard.sh --config` 从**本机 `src/config.json` 原样复制**（同口令同 secret/epoch → 手机与本机浏览器会话互通）。**轮换 token 必须同步所有客户端**（ZCode `~/.zcode/cli/config.json` 的 `mcp.servers` + Qoder `~/.qoder-cn/settings.json`，两处 2026-10-02 已同时改指 8699；改完要重启客户端才重连），否则表现为 MCP「连不上」而服务端其实好好的
- **闸门对 loopback 同样生效**（`_dispatch` 顶部，无 IP 例外）：服务器上 curl 8698 拿 401 是**正常**，不是故障；脚本要打 `/api/*` 就带 Bearer。**反面教训**：`vault_review.py` 因为一直没带 token，博主层登记校验长期 401 空跑而没人发现（2026-10-02 修）——脚本把 401 吞成"服务没起"是最难查的一类假成功
- 工具面：2026-10-01 实测 **43 个**（overview / vault 文件 CRUD / blogger CRUD / tags / logs / git_* / console_* / statement_* / post_history / thread_chain / note_item / pending_decision / review_record 等）——早先记的「22 个」是 2026-09-03 的数

## 已知坑（2026-08-30 实测）
1. **GitHub clone investment-notes 必失败**（GnuTLS TLS 中断）→ 一律本机 rsync
2. **Ubuntu mysqld.cnf 的 datadir 是注释行**（`# datadir`）→ 改需 sed 替换注释行本身
3. **改 MySQL datadir 必须同步 AppArmor**（/etc/apparmor.d/local/usr.sbin.mysqld）
4. **investment server.js 原监听 127.0.0.1** → 公网版已改 0.0.0.0（部署时已做）
5. ~~服务器版为只读展示：无 Obsidian/DeepSeek/skill，"打开 Obsidian/评分/加工/审查"按钮不可用~~（**2026-09-03 那版服务器实例的描述，已随 2026-09-28 重建作废**；现役服务器实例是完整 server.js，只是 vaultRoot 指向部署快照、写内容仍要在 Mac 做）
6. 云安全组入口（云控制台）：22/8698/8699/8700/3306/6379 已放行。**新端口一律须在云控制台安全组手动放行**（服务器上无 tccli、无云 API 凭据，自动加不了，只能用户去控制台点）——服务器内 ufw/firewalld 均 inactive、iptables 仅有 YJ-FIREWALL-INPUT（只拉黑特定 IP，不挡端口）。**新静态/新服务部署完公网 curl 超时（HTTP 000）时，99% 是安全组没放行**，别去折腾服务器防火墙。⚠ 反向的坑也存在：**实测 8698 与 6379 从公网都连不上（000 / nc 失败），说明"已放行"这条记录本身不可信**——判据要用实测，别拿文档当防火墙现状
7. ⚠ **8699 不能再关**（2026-10-02 起它是**唯一**入口：手机 + 本机浏览器 + agent 的 MCP 全走它，本机后端已删）；6379 已 `bind 127.0.0.1`，安全组那条放行**可以收回**（收回前确认没别的东西在直连）

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
