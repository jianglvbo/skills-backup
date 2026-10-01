---
name: server-ops
description: 云服务器（106.55.14.116，腾讯云轻量应用服务器·广州七区 ap-guangzhou-7，4C/4G/40G 单盘）运维执行器。管理 Nginx HTTPS 入口（www.jianglvbo.site，80/443 反代）、问答网页「问」（8700）、文件库只读站 investment-kb（8701，/kb/ + Basic Auth）、MySQL（investment-dashboard + fitness_plan）管理与备份的部署/状态/重启。投资看板为**双实例并存**（2026-09-28 用户拍板，推翻 2026-09-03「仅本地运行」）：服务器 systemd `investment-dashboard`（127.0.0.1:8698，手机/公网经 8699 走它）+ 本机 launchd（127.0.0.1:8698，经 `ssh -R` 8702 作 nginx upstream 的 backup）；vault=iCloud 绝对基准，写操作仍在 Mac。8699 端口 2026-09-21 随 fitness-console 退役清空、2026-09-28 复用为看板手机入口（fitness 库/unit/目录仍无残留）。触发词：「服务器」「部署到服务器」「服务器状态」「重启服务」「备份 MySQL」「HTTPS 证书」「106.55.14.116」「server-ops」「文件库上线」「只读站」「8699」。排除：本地 Obsidian 操作（走 investment-framework）、看板代码部署脚本走仓库内 `src/scripts/deploy-*.sh`。
license: MIT
agent_created: true
metadata:
  version: "2.5.0"
  short-description: 云服务器运维（Nginx HTTPS + 问答 8700 + 文件库只读站 8701 + 看板服务器实例 8698/8699 + MySQL + Redis）
---

# 服务器运维（server-ops）

> **现状（2026-10-01 复核）**：服务器承载 **Nginx HTTPS（www.jianglvbo.site，80 跳转 + 443 反代 8700 与 /kb/→8701）+ 问答「问」(8700) + 文件库只读站 investment-kb (8701) + 投资看板实例 systemd `investment-dashboard`（127.0.0.1:8698，公网经 8699）+ MySQL（investment-dashboard / fitness_plan）+ Redis 缓存**。
> ⚠ **2026-09-28 用户拍板推翻 2026-09-03 的「看板仅本地运行」**：看板常驻服务器，电脑关机手机照常用。本机 launchd 实例仍在（同一 MySQL 双实例并存），`ssh -R` 隧道出来的 8702 降级为 nginx upstream 的 **backup**（服务器实例挂了且 Mac 开机时才回落）。判据是 8699 那块 `proxy_pass http://investment_dash`，不是「服务器上没有 investment-dashboard」。
> fitness-console（原 8699）2026-09-21 已全删（unit/目录/`fitness` 库无残留）；**8699 这个端口号 2026-09-28 起被投资看板复用**为手机公网入口，看到 8699 别再去查 fitness-console。

## Default stance

### 核心原则
1. **统一账号**：所有操作默认用 `jianglb@106.55.14.116`（免密 sudo）；root 仅本地（SSH 已禁，勿尝试 root 登录）
2. **凭据零明文**：SKILL.md 与命令中一律不出现密码；需要凭据时读 `$HOME/.config/server-ops/credentials.md`（0600，通用位置，各 agent 共用）
3. **只读先于变更**：查状态/验证等只读操作直接执行；重启/同步/改配置等变更操作，先说明影响再执行
4. **脚本优于脑补**：状态检查有现成脚本（scripts/status.sh），执行脚本看输出，不手工拼命令

### 禁止行为
1. 禁止在输出/日志/命令回显中明文打印密码（用 `MYSQL_PWD`/`-p` 交互/`sudo mysql` 免密）
2. 禁止 `git clone` investment-notes 走 GitHub（已知 TLS 中断失败）——统一用本机 rsync 同步 vault（注：vault 推送服务器已随服务器版退役，此条仅限本地↔本机副本场景）
3. 禁止把 MySQL bind-address 从 127.0.0.1 改公网（当前已公网开放，属历史遗留；新部署一律本机 + SSH 隧道）
4. 禁止在服务器上直接编辑生产配置而不先备份（改前 `cp x x.bak-日期`）

## Workflow

### 第一步：确认连接
- 本机直接 `ssh 106.55.14.116`（**2026-08-31 已配 SSH 密钥免密**：`~/.ssh/config` 已登记 Host 106.55.14.116 / User jianglb / IdentityFile id_rsa，`~/.ssh/id_rsa.pub` 已装入服务器 authorized_keys）
- 需要密码兜底或远程跑命令：`expect scripts/sshrun.exp "<远程命令>"`（自动从 credentials.md ## SSH 段**按行**提取密码，密钥失效时兜底；勿用正则跨段抓密码——会抓到 MySQL 段）
- 服务器本机 MySQL 用 `sudo mysql`（root auth_socket 免密）或 `mysql -u jianglb -p`

### 第二步：查状态（只读，直接执行）
```bash
scripts/status.sh（skill 内相对路径；检查 fitness/qa/mysql + investment-dashboard 行数快检；[--json] 输出结构化）
# 等效 ssh 单行：ssh jianglb@106.55.14.116 "sudo systemctl status fitness-console qa mysql --no-pager | grep -E 'Active|●'"
```
⚠ **该脚本查的 `fitness-console` unit 已于 2026-09-21 删除**（永远 inactive，不是故障），且**没查现役的 `investment-dashboard`**。要一次性看全：
`ssh jianglb@106.55.14.116 "systemctl is-active nginx qa mysql redis-investment investment-kb investment-dashboard"`

### 第三步：备份（定期做）
- MySQL 逻辑备份：
```bash
ssh jianglb@106.55.14.116 "sudo mysqldump investment-dashboard | gzip > /home/jianglb/backup/investment-dashboard-$(date +%Y%m%d).sql.gz"
```
- fitness 数据目录（JSON）：
```bash
ssh jianglb@106.55.14.116 "tar czf /home/jianglb/backup/fitness-data-$(date +%Y%m%d).tgz -C /home/jianglb fitness-console/data"
```
- 建议 cron 每周一执行（可后配）

### 第三步之二：Redis 缓存（2026-09-12 新装；**2026-10-01 实测看板侧 `enabled:false`，实例在跑但不生效**）
- **用途**：投资看板读 `investment-dashboard` 的缓存层。看板跑在本机 macOS，读库要跨公网 RTT，缓存把「N 次查询」压成「1 次 GET」。
- **安装位置**：`/home/jianglb/redis`（源码编译，v8.10.1；`bin/`、`conf/redis.conf`、`data/`、`log/` 全在 home 下，删除 = `rm -rf /home/jianglb/redis`）
- **服务**：systemd 单元 `redis-investment`（User=jianglb，`sudo systemctl {status,restart,stop} redis-investment`，已 enable 开机自启）
- **配置要点**：`bind 0.0.0.0` + `requirepass`（28 位 ≈167 bits）；`maxmemory 256mb` + `allkeys-lru`；**纯缓存不持久化**（`save ""`、`appendonly no`，所以**重启 Redis = 缓存全清**，看板会短暂回落到查库，属预期）
- **命令面加固（2026-09-12，公网可达后追加）**：改名禁用 `CONFIG/DEBUG/MONITOR/REPLICAOF/SLAVEOF/MODULE/SAVE/BGSAVE/BGREWRITEAOF/MIGRATE/CLIENT/ACL/FAILOVER/PSYNC/SYNC/REPLCONF` + 原有的 `FLUSHALL/FLUSHDB/KEYS/SHUTDOWN`。改前备份 `redis.conf.bak-20260912`；改动后重启 Redis 并复验常用命令（PING/GET/SET/SCAN/INFO/DBSIZE 正常）
- **性能实测（2026-09-12）**：单次 1KB GET 往返 **直连 61ms（中位） vs SSH 隧道 114ms** → **直连快约 1.8×**，故看板默认走直连；隧道保留为备用（加密、不依赖出口 IP）
- **迁移/换机提示**：Redis 里**没有任何独立数据**（纯缓存，随时可清空），换机或重装只需照本段重建实例 + 改 `config.json` 的 `redis` 段即可；数据真相始终在 MySQL（`investment-dashboard`）与 vault
- **接入点（2026-09-12 用户放开 6379 后）**：看板**直连 `106.55.14.116:6379`**（安全组入站 TCP:6379 已放行，曾因规则加错安全组导致不通——抓包 0 SYN + 外部节点探测可定位）。**备用通路**：SSH 隧道（launchd `com.investment-redis-tunnel`，本地 6379 → 服务器 127.0.0.1:6379），切回用 `bash ~/Project/investment-dashboard/scripts/redis-endpoint.sh tunnel`
- **两条通路都验证过的命令**：`bash ~/Project/investment-dashboard/scripts/redis-endpoint.sh check`（先探测再改配置）；`direct`/`tunnel` 一键切换并复验看板缓存连接
- **注意**：直连 RTT 实测中位 ~57ms（移动网到腾讯云一跳），与隧道相当；直连省掉 SSH 加密转发但**依赖本机出口 IP 会变**（移动网），隧道更稳
- **凭据**：`~/.config/server-ops/credentials.md` 的 `## Redis` 段（0600）；看板侧写在 `~/Project/investment-dashboard/config.json` 的 `redis` 段（该文件已被 .gitignore）
- **一键体检**：`bash ~/Project/investment-dashboard/scripts/cache-stats.sh`（看板侧进程统计 + 服务器 Redis 内存/键数 + 隧道状态）
- **查看存了什么（2026-09-12 新增，本机直连、不依赖 redis-cli/ssh）**：
  ```bash
  cd ~/Project/investment-dashboard
  python3 scripts/redis-inspect.py keys               # 所有 key：键 / 大小 / 剩余 TTL / 值预览
  python3 scripts/redis-inspect.py keys 'ik:statements:*'  # 按 pattern 过滤
  python3 scripts/redis-inspect.py get '<key>'        # 单键的值（自动解压 gzip + 格式化 JSON）
  python3 scripts/redis-inspect.py raw '<key>'        # 原始值（看 g: 前缀）
  python3 scripts/redis-inspect.py info               # 键数 / epoch / 内存 / 上限 / keyspace
  python3 scripts/redis-inspect.py cmd TTL '<key>'    # 任意命令
  bash scripts/redis.sh                                # 交互式 redis-cli（要原生命令时用）
  ```
  ⚠️ **`KEYS *` 不可用**（已按加固策略改名禁用，阻塞式全库遍历）：取全量用 `SCAN` / `redis-inspect.py keys` / `redis-cli --scan`。
- **缓存内容是什么**：全部是看板读接口的结果（`ik:statements|subject|subjects|trades|bloggerProfile|bloggerCounts:v<epoch>:<hash>`）+ 一个 `ik:epoch` 版本号键；
  **无持久化 → 重启 Redis 即全空**；键都带 300s TTL（仅 `ik:epoch` 无 TTL）。
- **客户端健壮性（2026-09-12，公网链路的三个坑）**：① **空闲连接会被 NAT 静默掐死** → 命令超时即判定连接已死并重连 + **15s 心跳保活**（实测空闲 45s 后首次读仍命中）；② 失败退避为**指数**（1s→15s，成功后归零）；③ `ik:epoch` 键若被 LRU 淘汰，**读取端不回落成固定值**（那会读到本该失效的老键）而是生成随机 epoch + `SET NX`，`cacheBump` 用 `SET` 而非 `INCR`。
- **安全提醒**：Redis 监听 0.0.0.0 时全靠 `requirepass` 兜底；若要收紧，把安全组规则限定到本机出口 IP，或维持 loopback + SSH 隧道（本 skill 的推荐姿势）

### 第四步：investment-dashboard 数据链路（2026-09-28 起双实例并存）
- **架构**：本机看板（`~/Project/investment-dashboard`，launchd 8698，vault=iCloud 绝对基准，**唯一写方**）与服务器看板（systemd `investment-dashboard`，127.0.0.1:8698，config 在 `/home/jianglb/investment-dashboard/config.json`）**连同一个 MySQL**；手机/公网走服务器实例，本机浏览器走 Mac 实例。代码改动跟 `master` 走 `bash ~/Project/investment-dashboard/src/scripts/deploy-dashboard.sh`（不带 `--config` 时不碰服务器 config.json，该文件 600 权限、不随 rsync）。
- **两实例各自跑夜间统计重建**（幂等无冲突）；Redis 侧服务器实例 `enabled:false`（两实例各自 L1，无常间失效问题）。
- vault 侧：服务器看板实例的 vaultRoot 指向 KB 站那份镜像 `/home/jianglb/investment-kb/vault`（与只读站共用，不重复占 135M）＝**KB 部署时的快照**；改内容一律在 Mac/iCloud 做，`deploy-kb.sh --vault` 推过去之后服务器侧才更新，**直接在服务器上改会被下次推送覆盖**。
- 同步范围：只动 blogger 表（files/tags 已退役，改为内存索引）；运营表（refine/review/coarse/trash/prediction 域）一律不碰
- **两实例的 Redis 缓存当前都是关的**：`src/config.json` 本机 `redis.enabled=false`，`deploy-dashboard.sh` 重写服务器 config 时硬写 `enabled:false`（注释口径「本机 MySQL 够快，L1 即可」）——**Redis 实例本身还在服务器上跑着**（见第三步之二），只是看板不读它。别看到 6379 有监听就以为缓存在生效。
- 强制重建缓存：`POST http://127.0.0.1:8698/api/index/rebuild`（内存索引）；直连 SQL 改过 statement_* 或关联表还要 `POST /api/cache/clear` + `POST /api/stats/rebuild`（MCP 工具改库会自动去抖补算，无需手工）
- 库表 DDL 权威：`investment-framework/references/investment-dashboard.sql`（**2026-10-01 实测 46 表**、全库 DATA+INDEX 15.45 MB：六张 `statement_*` 类型表 + `statement` UNION 视图 + 关联表 + `post_history` + 2026-10-01 新增 `xq_thread*` 四表 + `console_stat_daily` 等；单 `dict` 表承载全部码值。表名/列名 2026-09-13 规范化后由 `src/scripts/export-schema.js`（注意已在 `src/` 下）从实库生成）
- **本机服务管理**：launchd 单元 `com.investment-dashboard`（`launchctl kickstart -k gui/501/com.investment-dashboard` 重启）；启动前置：config.json + vault 可达 + node_modules 含 mysql2。**服务器侧服务管理**：`sudo systemctl {status,restart} investment-dashboard`（本 skill 范围内）
- MCP 端点：**两个实例都有**，同一份 `mcpToken` 过闸门 —— 本机 `http://127.0.0.1:8698/mcp`、服务器同路径（公网经 8699）。轮换 token 要同步所有客户端（ZCode `~/.zcode/cli/config.json` 的 `mcp.servers` + Qoder `~/.qoder-cn/settings.json`），否则表现为「连不上」而服务端其实好好的。

### 第五步之二：investment-kb 只读站（8701，2026-09-27 上线）
- **是什么**：投资看板「文件库」那一页的**线上只读版**（Obsidian 式目录树 + 笔记）。不连 MySQL、不接 MCP、无任何写入口（非 GET 一律 405），只听 `127.0.0.1:8701`。它与服务器上的**看板实例**（8698，见第四步）是两套东西、两个进程，别把端口搞混。
- **地址**：`https://www.jianglvbo.site/kb/`，公网唯一入口是 nginx `location /kb/` + Basic Auth（用户名 `jianglb`，密码见 `credentials.md` 的「## 文件库只读站 Basic Auth」段；哈希存 `/etc/nginx/.htpasswd-kb`，root:www-data 640，`openssl passwd -apr1` 生成）
- **目录**：`~/investment-kb/{app,vault,logs}` —— `app/` 是代码（kb-site.js + lib/vault.js + web/，rsync --delete 跟仓库走），`vault/` 是内容镜像 137M/624 篇 md
- **服务**：systemd `investment-kb`（`sudo systemctl {status,restart} investment-kb`，已 enable）；env 在 unit 里（KB_ROOT/KB_APP/KB_PORT/KB_HOST/KB_BASE）
- **更新内容 = 本地跑** `bash ~/Project/investment-dashboard/src/scripts/deploy-kb.sh`（刷镜像→推代码→推 vault→restart）；只改代码加 `--app`（省掉 137M）。**服务器读不到 iCloud**，镜像必须先在本机生成（`vault-mirror.sh`，由本地看板进程每 10 分钟自动跑一次）
- **回退**：`sudo systemctl disable --now investment-kb` + 从 sites-enabled 配置删掉 `location /kb/` 块（备份在 `/etc/nginx/jianglvbo.site.sites-enabled.bak-20260927`）+ `sudo nginx -t && sudo systemctl reload nginx`；`rm -rf ~/investment-kb` 清数据

### 第五步：Nginx HTTPS 入口（www.jianglvbo.site，2026-09-21 上线）
- **架构**：Nginx 监听 80/443（default_server）——443 按 SNI 反代本机 `127.0.0.1:8700`（问答「问」）与 `127.0.0.1:8701`（`/kb/` 只读站），80 一律 `301` → `https://www.jianglvbo.site`；default 站点已删
- ⚠ **实际生效的文件是 `/etc/nginx/sites-enabled/jianglvbo.site`，它是实体文件、不是软链**（2026-09-27 实测翻案，本 skill 旧版写「软链 sites-enabled」是错的）。`sites-available/jianglvbo.site` 那份**没有** `/exercise-media/` 块，两边已分叉——**改配置改 sites-enabled 那份**，改 sites-available 不生效。
- ⚠ **`include /etc/nginx/sites-enabled/*` 会吃掉该目录下所有文件**：备份留在里面直接 `nginx -t` 报 `duplicate default server for 0.0.0.0:80`（2026-09-27 踩过）。备份命名成 `/etc/nginx/jianglvbo.site.sites-enabled.bak-日期`（放 sites-enabled 外面）。
- **证书**：`/etc/nginx/ssl/jianglvbo.site_bundle.pem`（644）+ `.key`（600），腾讯云 TrustAsia DV，SAN = jianglvbo.site + www.jianglvbo.site，**2026-12-20 到期**；续期 = 腾讯云控制台重新申请 → 下载 Nginx 版 → 覆盖 ssl 目录两个文件 → `sudo nginx -t && sudo systemctl reload nginx`
- **坑：服务器 Nginx 1.18 不支持 `http2 on;` 独立指令**（1.25+ 语法），必须写 `listen 443 ssl http2;`
- **公网访问 80/443 须腾讯云安全组放行**（只能用户在控制台点；服务器 ufw/firewalld 均 inactive）
- 改配置流程：`sudo cp -a x <备份到 sites-enabled 之外>` → 改 → `sudo nginx -t` → `sudo systemctl reload nginx`
- **本机（Mac）到 443 的 TLS 会被中途重置**（2026-09-27 实测：TCP 连得上、握手 reset，主站 `/` 同样打不开，与本次改动无关）；验证公网效果用服务器侧 `curl --resolve www.jianglvbo.site:443:127.0.0.1`，或换手机流量网络。


### 第六步：MySQL 管理
- **现役库**：`investment-dashboard`（投资看板远端唯一共享库）+ `fitness_plan`（中文健身动作数据集，属本地 `~/Project/fitness-plan` 仓库 db/schema.sql）；`fitness`/`fitness_dev` 已随 fitness-console 退役删除（2026-09-21 确认不存在）
- 本机远程连：先建隧道 `ssh -L 3306:127.0.0.1:3306 jianglb@106.55.14.116`（另开终端），再连 `127.0.0.1:3306`；或直连公网 3306（pymysql/mysql 客户端）
- 或服务器本机：`sudo mysql`（root 免密）/ `mysql -u jianglb -p`
- 建库：`CREATE DATABASE IF NOT EXISTS xxx DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;`

## Output format

| 字段 | 类型 | 说明 |
|:---|:---|:---|
| 服务 | string | nginx / qa / mysql / redis-investment / investment-kb（只读站 8701）/ **investment-dashboard（看板服务器实例 8698，2026-09-28 起现役）**；本机 launchd 那份不在本 skill 范围 |
| 状态 | string | active / failed / inactive |
| HTTP 码 | int | 8700 与 8701 本地 curl；443 用**服务器侧** `curl --resolve www.jianglvbo.site:443:127.0.0.1` 验证（本机 Mac 到 443 的 TLS 被中途重置，测不出结果）；服务器 8698 在服务器上 curl（**闸门对 loopback 同样生效**，无凭据必 401，那不是故障），带凭据用 `Authorization: Bearer <mcpToken>` |
| 数据校验 | string | investment-dashboard 行数快检 / overview API 与预期对比 |
| 影响说明 | string | 变更操作前必须给出 |

## Relative files

| 场景 | 加载文件 | 方式 |
|:---|:---|:---|
| 服务器布局/端口/目录/数据库连接详表 | `references/layout.md` | 读取 |
| 需要凭据（密码等） | `$HOME/.config/server-ops/credentials.md` | 读取（注意不输出到对话） |
| 查服务器各站 + MySQL 状态 | `scripts/status.sh` | 执行 |
| 远程执行命令（密码兜底） | `scripts/sshrun.exp "<远程命令>"` | 执行（expect；密钥失效时自动兜底） |
| ~~vault_sync.sh / rsyncrun.exp~~ | 已退役（本地 .retired-20260903 留档；仓库版保留为历史参考） | 2026-09-28 看板回服务器后**也没有复活这两个**：代码走 `src/scripts/deploy-dashboard.sh`、vault 镜像走 `src/scripts/deploy-kb.sh`（本 skill 的 `--delete` 语义警告见第四步） |

## Source hierarchy

| 优先级 | 来源 |
|:---|:---|
| 1 | 用户显式约定（默认 jianglb、MySQL 数据目录 /home/jianglb/mysql、vault 用 rsync） |
| 2 | 部署实测经验（2026-08-30：AppArmor/注释行 datadir/GitHub clone 失败） |
| 3 | 通用运维最佳实践（变更前备份、只读先于变更、凭据隔离） |

## 自检

- [ ] 连接是否用 jianglb 且无明文密码出现在命令/输出？
- [ ] 变更操作（重启/改配置/删数据）前是否备份并说明影响？
- [ ] investment-dashboard **看板**是否按「**双实例并存**」处理？（2026-09-28 起服务器 systemd `investment-dashboard` 是现役、手机经 8699 走它；本机 launchd 那份是第二实例。**vault 与内容写入的唯一真相源仍是 Mac/iCloud**，别在服务器上改内容——会被下次 `deploy-kb.sh --vault` 覆盖）
- [ ] 改 nginx 是否改的 `sites-enabled/jianglvbo.site`（实体文件、与 sites-available 已分叉）？备份是否放在了 sites-enabled **外面**？
- [ ] fitness-console 相关引用能识别为已退役（2026-09-21 全删），不尝试重启/部署？看到 **8699 别当 fitness-console**——该端口 2026-09-28 起归投资看板手机入口。
- [ ] 新端口公网访问是否提醒安全组放行（服务器防火墙不挡端口）？
- [ ] 改 Nginx 后是否 `nginx -t` 再 reload？证书是否在有效期内（到期 2026-12-20）？

## 变更记录

- **2026-10-01 只读复核（SSH 勘察，未改任何线上状态），清掉本 skill 攒下的六处失真**：① 全文「看板本体仅本地运行」作废——服务器 systemd `investment-dashboard`（127.0.0.1:8698）**现役 active**，8699 是手机公网入口，`ssh -R` 出来的 8702 只是 upstream backup（2026-09-28 用户拍板，本 skill 一直没跟上，连 description 路由层都是错的）；② 机房 **不是上海**，metadata 实测 `ap-guangzhou-7`，机型是**轻量应用服务器**（CVM 专属 metadata 路径全 404），4C/4G/40G **单盘无第二块**；③ 表数 38 → **实测 46 表 / 15.45 MB**，`export-schema.js` 等脚本路径已在 `src/scripts/` 下；④ **Redis 看板侧 `enabled:false`**（本机 config 实测 + `deploy-dashboard.sh` 硬写），实例在跑但不生效，那段「直连快 1.8×」的性能结论描述的是当时状态；⑤ 8699 端口号被复用，`status.sh` 里查 `fitness-console` unit 的那条已无意义；⑥ 新增实测约束：`/var/lib/mysql` 是空壳（真 datadir `/home/jianglb/mysql`）、**binlog 已 356 MB（业务库的 23 倍，`binlog_format=ROW` + 30 天过期）**、`client_max_body_size` 全份配置未设（默认 1m）、`/kb/` 的 `location` 在 443 与 8699 两块里各有一份、**零自动备份**（crontab/timer 都无，`/home/jianglb/backup` 最新一份停在 2026-09-14）。
- **2026-09-27 上线 investment-kb 只读站**：新 unit + `/etc/nginx/.htpasswd-kb` + sites-enabled 配置加 `location /kb/`（备份 `/etc/nginx/jianglvbo.site.sites-enabled.bak-20260927`）。同批纠正本 skill 两处失真：sites-enabled 不是软链、vault 镜像确实有一份推上来了（只读站数据源）。
- **2026-09-27 服务器清理（用户拍板）**：删 `~/src`（502M＝redis-stable 编译树 + redis.tar.gz）与 `investment-dashboard-dump-pre-rename-20260925.sql`（5.8M），释放约 508M。**Redis 本体不受影响**：可执行文件在 `~/redis/bin`（`/proc/<pid>/exe` 已核实），版本 v8.10.1 jemalloc-5.3.0，删后 `redis-investment` 仍 active、PING 通；要重建就照本 skill Redis 段重装。
- 服务器 home 现存：`backup fitness-server investment-dashboard investment-kb jianglvbo.site.conf mysql qa redis`（`src` 已没了，别再去找）。
