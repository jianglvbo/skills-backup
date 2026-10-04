---
name: server-ops
description: 云服务器（106.55.14.116，腾讯云轻量应用服务器·广州七区 ap-guangzhou-7，4C/4G/40G 单盘）运维执行器。管理 Nginx HTTPS 入口（www.jianglvbo.site，80/443 反代 + 8699 看板公网入口）、问答网页「问」（8700）、投资看板服务器实例（systemd `investment-dashboard`，127.0.0.1:8698，nginx 直出静态 + auth_request 闸门）、MySQL（investment-dashboard + fitness_plan）与 Redis 缓存的部署/状态/重启。**投资看板后端只有一套、就在服务器上**（2026-10-02 用户定稿「mac 只留前端，本机不留任何常驻服务」：本机 launchd 三台作业与 `ssh -R` 8702 反隧道已删，nginx upstream 里那条 backup 也一并删）；vault 真相仍在 Mac/iCloud，靠 `deploy-vault.sh` 单向推快照给服务器。8699 端口 2026-09-21 随 fitness-console 退役清空、2026-09-28 复用为看板入口（fitness 库/unit/目录无残留）。触发词：「服务器」「部署到服务器」「服务器状态」「重启服务」「备份 MySQL」「HTTPS 证书」「106.55.14.116」「server-ops」「8699」。排除：本地 Obsidian 操作（走 investment-framework）、看板代码部署脚本走仓库内 `src/scripts/deploy-*.sh`。
license: MIT
agent_created: true
metadata:
  version: "2.6.0"
  short-description: 云服务器运维（Nginx HTTPS + 问答 8700 + 看板单实例 8698/8699 + MySQL + Redis）
---

# 服务器运维（server-ops）

> **现状（2026-10-02 复核）**：服务器承载 **Nginx（80 跳转 + 443 反代 8700 问答；8699 看板公网入口，静态由 root 直出、`auth_request` 走 node `/api/config`）+ 问答「问」(8700) + 投资看板后端唯一实例 systemd `investment-dashboard`（127.0.0.1:8698）+ MySQL（investment-dashboard / fitness_plan）+ Redis（bind 127.0.0.1，看板实例在用，ttl=60）**。
> ⚠ **两处已退役**：① 文件库只读站 investment-kb（8701 + nginx `/kb/`，443 与 8699 两块一起摘）2026-10-02 退役，`deploy-kb.sh` 换成了 `deploy-vault.sh`；② 本机（Mac）那套 launchd 常驻（node @8698、`ssh -R` 反隧道、vault 镜像定时器）同日全删——**服务器实例挂了我们不再有"回落 Mac"这层兜底**，别再提议用隧道补回来。
> fitness-console（原 8699）2026-09-21 已全删；**8699 这个端口号 2026-09-28 起被投资看板复用**为公网入口，看到 8699 别再去查 fitness-console。

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
⚠ **该脚本查的 `fitness-console` unit 已于 2026-09-21 删除**（永远 inactive，不是故障），且**没查现役的 `investment-dashboard`**。要一次性看全（`investment-kb` 已于 2026-10-02 退役，别再列进来——查一个不存在的 unit 只会得到一个假的 inactive）：
`ssh jianglb@106.55.14.116 "systemctl is-active nginx qa mysql redis-investment investment-dashboard"`

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

### 第三步之二：Redis 缓存（2026-09-12 新装；**2026-10-02 起：只有服务器上那一个看板实例在用，且只听回环**）
- **用途**：看板读接口的 L2 缓存（L1 是进程内 30s）。
- ⚠ **`bind` 已从 `0.0.0.0` 收到 `127.0.0.1`**（2026-10-02；实测 `nc -z 106.55.14.116 6379` 不可连）。
  所以**本机直连 6379 那条通路没有了**：`src/scripts/redis-inspect.py` 因此删除（它读 `src/config.json` 的 `redis` 段直连，
  而本机 config 里根本没有 `redis` 段——那文件是"后端在本机"时代的产物）。查缓存改走 **SSH** 的两件：
  `bash src/scripts/redis.sh`（SCAN / GET+解压 / 交互式 redis-cli）与 `bash src/scripts/cache-stats.sh`
  （实例进程统计 + 服务器内存/命中/键数）。被删的那个脚本要找回看 git 历史。
- **安装位置**：`/home/jianglb/redis`（源码编译，v8.10.1；`bin/`、`conf/redis.conf`、`data/`、`log/` 全在 home 下，删除 = `rm -rf /home/jianglb/redis`）
- **服务**：systemd 单元 `redis-investment`（User=jianglb，`sudo systemctl {status,restart,stop} redis-investment`，已 enable 开机自启）
- **配置要点**：`bind 127.0.0.1` + `requirepass`（口令在服务器 `config/config.json` 的 redis 段，**别贴命令行**，会被脱敏成 `***`）；
  `maxmemory 256mb` + `allkeys-lru`；**纯缓存不持久化**（`save ""`、`appendonly no`，所以**重启 Redis = 缓存全清**，看板会短暂回落到查库，属预期）
- **命令面加固（2026-09-12）**：改名禁用 `CONFIG/DEBUG/MONITOR/REPLICAOF/SLAVEOF/MODULE/SAVE/BGSAVE/BGREWRITEAOF/MIGRATE/CLIENT/ACL/FAILOVER/PSYNC/SYNC/REPLCONF` + `FLUSHALL/FLUSHDB/KEYS/SHUTDOWN`。改前备份 `redis.conf.bak-20260912`
- **收益要说准**（2026-10-02 服务器上实测）：后端与 MySQL 同机，冷重建 53ms / Redis 命中 17ms / 关 Redis 回源 38ms——
  **只差 20ms 量级，它不是手机端卡顿的解药**（卡顿在前端渲染，已由「首屏 60 条 + 不自动渲染」解决）。
  旧条目里「直连 61ms vs SSH 隧道 114ms」是**本机跨公网**量的数，跟服务器上的用法无关，别再引用。
- **TTL = 60s**（写在服务器 config，`deploy-dashboard.sh --config` 模板里显式给 `host/port/ttl`）。理由见
  `investment-dashboard/AGENTS.md`：现在只有一套后端、agent 的写也打在这套上，`cacheBump` 同进程同 Redis，
  跨实例失效问题不存在了；60s 只当「直连 SQL 改库忘了 POST /api/cache/clear」的兜底。**只有 `ik:epoch` 键无 TTL。**
- **迁移/换机提示**：Redis 里**没有任何独立数据**（纯缓存，随时可清空），换机只需照本段重建实例 + 改 config 的 `redis` 段；数据真相始终在 MySQL（`investment-dashboard`）与 vault
- **缓存内容是什么**：全部是看板读接口的结果（`ik:statements|subject|subjects|trades|bloggerProfile|bloggerCounts:v<epoch>:<hash>`）+ 一个 `ik:epoch` 版本号键；键名里的 `<epoch>` 取自 `auth.epoch`，**两端同 epoch 才互认**。
  ⚠️ **`KEYS *` 不可用**（已按加固策略改名禁用，阻塞式全库遍历）：取全量用 `bash src/scripts/redis.sh`（内部走 `redis-cli --scan`）。
- **客户端健壮性（2026-09-12，仍然有效的三个坑）**：① **空闲连接会被 NAT 静默掐死** → 命令超时即判定连接已死并重连 + **15s 心跳保活**；② 失败退避为**指数**（1s→15s，成功后归零）；③ `ik:epoch` 键若被 LRU 淘汰，**读取端不回落成固定值**（那会读到本该失效的老键）而是生成随机 epoch + `SET NX`，`cacheBump` 用 `SET` 而非 `INCR`。
- **安全**：收到 `bind 127.0.0.1` 之后，公网侧靠"不可达"这一层就够，`requirepass` 退化为第二层（防同机其他用户/进程）；
  腾讯云安全组里那条 6379 入站放行**已经不需要了**，可以收回（收回前确认没有别的东西在直连它）。

### 第四步：investment-dashboard 数据链路（**2026-10-02 起只有一套，就在服务器上**）
- **架构**：systemd `investment-dashboard`（node，监听 `0.0.0.0:8698`，实际只有 nginx 与本机用；公网 `http://106.55.14.116:8698` 实测被云防火墙挡 → 000），
  config 在 `/home/jianglb/investment-dashboard/config/config.json`（600 权限、不随 rsync），前端静态由 nginx `root` 直出
  `/home/jianglb/investment-dashboard/frontend/`，`/api` `/mcp` `/login` 反代回 node，闸门用 `auth_request` 子请求 node 的 `/api/config`。
  **本机（Mac）不再有第二实例**：node @127.0.0.1:8698 那台 launchd 服务与 `ssh -R` 8702 反隧道 2026-10-02 已删，
  nginx upstream 里那条 `server 127.0.0.1:8702 backup;` 也删了 —— **服务器实例挂了就是没有看板**，没有回落。
  代码改动：`bash ~/Project/investment-dashboard/src/scripts/deploy-dashboard.sh`（不带 `--config` 时不碰服务器 config）。
- **vault 侧**：实例的 `vaultRoot=/home/jianglb/investment-dashboard/vault`＝**Mac/iCloud 的单向快照**（实测 1570 文件 / 650 md / 135M）。
  改内容一律在 Mac/iCloud 做，然后 `bash src/scripts/deploy-vault.sh`（第一步刷镜像、第二步推）；
  **服务器上直接改 md 会被下次 `--delete` 抹掉**。旧路径 `/home/jianglb/investment-kb/vault` 随只读站退役已归档。
- **缓存失效**：`POST /api/cache/clear`、`POST /api/stats/rebuild`、`POST /api/index/rebuild` 现在一律打
  **`https://www.jianglvbo.site:8699`** 且**必须带 `Authorization: Bearer <mcpToken>`**（闸门对 loopback 同样生效，
  无凭据 401，而很多脚本把 401 吞成"服务没起"→ 静默没清缓存，2026-10-02 已修两处这类假成功）。
- 同步范围：只动 blogger 表（files/tags 已退役，改为内存索引）；运营表（refine/review/coarse/trash/prediction 域）一律不碰
- 夜间统计只有这一台跑（旧「两实例各自重算、幂等无冲突」作废）；**改了统计表结构/口径必须马上 deploy**，
  否则这台跑旧代码的实例 03:10 按旧列清单 INSERT 会把新列整列清空（2026-09-29 实踩 `blogger`，2026-10-02 新加的 `ev_count` 同理）
- 库表 DDL 权威：`investment-framework/references/investment-dashboard.sql`（**2026-10-01 实测 46 基表 + 1 视图**、全库 DATA+INDEX 15.45 MB：六张 `statement_*` 类型表 + `statement` **VIEW（不是基表，`SHOW INDEX` 返回 0 行）** + 关联表 + `post_history` + `xq_thread*` 四表 + `console_stat_daily` 等；表名/列名规范化后由 `src/scripts/export-schema.js` 从实库生成）
- **服务管理**：只有服务器侧 `sudo systemctl {status,restart} investment-dashboard`（本 skill 范围内）。
  本机 launchd 单元已不存在，`launchctl kickstart … com.investment-dashboard` 这条命令现在只会报错。
- MCP 端点：**只剩一个**，`https://www.jianglvbo.site:8699/mcp`（同一份 `mcpToken` 过闸门；证书 TrustAsia 正规签发，node/curl 免 `-k`）。
  客户端配置：ZCode `~/.zcode/cli/config.json` 的 `mcp.servers` + Qoder `~/.qoder-cn/settings.json`，**两处 2026-10-02 已同时改指 8699**；
  轮换 token 要同步所有客户端，否则表现为「连不上」而服务端其实好好的。**代价**：单次工具调用从本机 4–11ms 变公网 0.35–2.0s（实测）。

### ~~第五步之二：investment-kb 只读站（8701）~~ —— **2026-10-02 已整体退役，别再重建**
- 拆掉的东西：`src/kb-site.js`（只听 127.0.0.1:8701 的只读站）、`src/scripts/deploy-kb.sh`（换成 **`deploy-vault.sh`**）、
  `src/scripts/kb-entry-8699.sh`，以及 nginx **443 与 8699 两块里的 `location /kb/`**（**两块必须一起摘**，
  只摘一块就是把内容留在公网）。依据是 access.log：10 个档共 53 次命中，当天仅剩的 2 次是验收脚本自己的 `curl/7.81.0`。
- 服务器上 `~/investment-kb/` 目录已停用并归档，那份 vault 镜像搬到 `/home/jianglb/investment-dashboard/vault`
  （1569→1570 文件两边核对一致），看板 `vaultRoot` 同步改指。
- ⚠ 前端里 `window.__KB` / `kbUrl()` 那套代码**还在** `app.js` / `app-m.js`（各 7 处），现在没人注入 `__KB` 所以是休眠态。
  别照着它再搭一遍只读站，也不必为退役去动前端（并行会话在改）。整条链路的实现要找回看 git 历史（删于 `chore/server-layout-cleanup`）。

### 第五步：Nginx HTTPS 入口（www.jianglvbo.site，2026-09-21 上线 / 2026-10-02 加 8699 分离）
- **架构**：Nginx 监听 80/443（default_server）——443 按 SNI 反代本机 `127.0.0.1:8700`（问答「问」），80 一律 `301` → `https://www.jianglvbo.site`；default 站点已删。
  **看板的公网入口不在 443**：是 `listen 10.1.0.11:8699 ssl http2` 那块（**注意不是 `0.0.0.0:8699`**，验证必须打 `https://10.1.0.11:8699` + `Host: www.jianglvbo.site`；
  打 `127.0.0.1:8699` 连的是另一台 java 进程，curl 全回 000，会把好服务判成挂掉）。443 上不再有任何看板相关 location。
- **8699 那块的前后端分离（2026-10-02）**：静态由 `include /etc/nginx/snippets/dashboard-static.conf` 提供
  （root 指 `investment-dashboard/frontend/` + gzip + `?v=` 一年强缓存 + UA 分流 home + `auth_request`），
  `map` 必须留在 http 级 → `/etc/nginx/conf.d/map-dashboard-home.conf`。**属于 nginx 的配置就待在 /etc/nginx**，别塞回项目目录。
  闸门是 `auth_request` 子请求 node 已有的 `/api/config`（401/200 正是它要的语义）→ 闸门逻辑只有 node 一份实现，**静态也过闸门**（无凭据 302）。
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
| 服务 | string | nginx / qa / mysql / redis-investment / **investment-dashboard（看板后端唯一实例 8698，公网经 8699）**；~~investment-kb（8701 只读站）~~ 2026-10-02 退役、~~本机 launchd 那份~~ 同日删除——**本 skill 不再管任何 Mac 侧进程** |
| 状态 | string | active / failed / inactive |
| HTTP 码 | int | 8700 在服务器上本地 curl（8701 只读站已于 2026-10-02 退役，别再拿它当判据）；443 用**服务器侧** `curl --resolve www.jianglvbo.site:443:127.0.0.1` 验证（本机 Mac 到 443 的 TLS 被中途重置，测不出结果）；**看板 8699 必须打 `https://10.1.0.11:8699` + `Host: www.jianglvbo.site`**（那块 listen 的是内网 IP 不是 0.0.0.0，打 `127.0.0.1:8699` 连的是另一台 java 进程、curl 全回 000，会把好服务判成挂掉）；**闸门对 loopback 同样生效**，无凭据 302/401 不是故障，带凭据用 `Authorization: Bearer <mcpToken>` |
| 数据校验 | string | investment-dashboard 行数快检 / overview API 与预期对比 |
| 影响说明 | string | 变更操作前必须给出 |

## Relative files

| 场景 | 加载文件 | 方式 |
|:---|:---|:---|
| 服务器布局/端口/目录/数据库连接详表 | `references/layout.md` | 读取 |
| 需要凭据（密码等） | `$HOME/.config/server-ops/credentials.md` | 读取（注意不输出到对话） |
| 查服务器各站 + MySQL 状态 | `scripts/status.sh` | 执行 |
| 远程执行命令（密码兜底） | `scripts/sshrun.exp "<远程命令>"` | 执行（expect；密钥失效时自动兜底） |
| ~~vault_sync.sh / rsyncrun.exp~~ | 已退役（本地 .retired-20260903 留档；仓库版保留为历史参考） | 2026-09-28 看板回服务器后**也没有复活这两个**：代码走 `src/scripts/deploy-dashboard.sh`、vault 快照走 `src/scripts/deploy-vault.sh`（`deploy-kb.sh` 随只读站一起删于 2026-10-02；本 skill 的 `--delete` 语义警告见第四步） |

## Source hierarchy

| 优先级 | 来源 |
|:---|:---|
| 1 | 用户显式约定（默认 jianglb、MySQL 数据目录 /home/jianglb/mysql、vault 用 rsync） |
| 2 | 部署实测经验（2026-08-30：AppArmor/注释行 datadir/GitHub clone 失败） |
| 3 | 通用运维最佳实践（变更前备份、只读先于变更、凭据隔离） |

## 自检

- [ ] 连接是否用 jianglb 且无明文密码出现在命令/输出？
- [ ] 变更操作（重启/改配置/删数据）前是否备份并说明影响？
- [ ] investment-dashboard **看板**是否按「**后端只有一套（就在服务器上）**」处理？（2026-10-02 用户定稿：本机 launchd 那台已删、nginx 里 8702 backup 已摘，**不要再试图用隧道回落到 Mac**；改统计表结构/口径后必须 `deploy-dashboard.sh`，因为只有这一台会跑 03:10 重算。**vault 与内容写入的唯一真相源仍是 Mac/iCloud**，别在服务器上改内容——会被下次 `deploy-vault.sh` 的 `--delete` 抹掉）
- [ ] 改 nginx 是否改的 `sites-enabled/jianglvbo.site`（实体文件、与 sites-available 已分叉）？备份是否放在了 sites-enabled **外面**？
- [ ] fitness-console 相关引用能识别为已退役（2026-09-21 全删），不尝试重启/部署？看到 **8699 别当 fitness-console**——该端口 2026-09-28 起归投资看板手机入口。
- [ ] 新端口公网访问是否提醒安全组放行（服务器防火墙不挡端口）？
- [ ] 改 Nginx 后是否 `nginx -t` 再 reload？证书是否在有效期内（到期 2026-12-20）？

## 变更记录

- **2026-10-02 二次复核 + 落地（本轮真的改了线上）**：把上一轮的三处"当时正确、现在已废"清掉——
  ① **「双实例并存」作废**：Mac 侧 `com.investment-dashboard` / `com.investment-tunnel` / `com.investment-vault-mirror`
  三个 LaunchAgent 全部 `bootout` 并移出 plist（归档 `~/archive/2026-10-02-mac/`；顺带发现那个镜像定时器
  **日志里成功过 0 次**，launchd 直起 /bin/bash 读 iCloud 一律被 TCC 拒，十年如一日只在制造失败行）；
  ② **nginx upstream 的 `server 127.0.0.1:8702 backup;` 已删**（改前快照 `~/archive/2026-10-02/nginx-before-drop-8702`，
  `nginx -t` 过才 reload），后端真正确认只有一套；③ **Redis 从 `bind 0.0.0.0`+`requirepass` 收到 `bind 127.0.0.1`**
  （实测公网 `nc -z 106.55.14.116 6379` 不可连），于是"本机直连 6379"整条通路消失，`redis-inspect.py` 随之删除。
  同轮实测并留档：8698 公网不可连（000，云防火墙挡）/ node 实绑 `0.0.0.0:8698`（旧文档写"只听 127.0.0.1"是错的）/
  单次 MCP 调用本机 4–11ms → 公网 0.35–2.0s / vault 两侧 650 md 集合对拍零差集。
- **2026-10-01 只读复核（SSH 勘察，未改任何线上状态），清掉本 skill 攒下的六处失真**：① 全文「看板本体仅本地运行」作废——服务器 systemd `investment-dashboard`（127.0.0.1:8698）**现役 active**，8699 是手机公网入口，`ssh -R` 出来的 8702 只是 upstream backup（2026-09-28 用户拍板，本 skill 一直没跟上，连 description 路由层都是错的）；② 机房 **不是上海**，metadata 实测 `ap-guangzhou-7`，机型是**轻量应用服务器**（CVM 专属 metadata 路径全 404），4C/4G/40G **单盘无第二块**；③ 表数 38 → **实测 46 表 / 15.45 MB**，`export-schema.js` 等脚本路径已在 `src/scripts/` 下；④ ~~**Redis 看板侧 `enabled:false`**~~（**2026-10-02 已开**：`enabled:true`、ttl 60s）；⑤ 8699 端口号被复用，`status.sh` 里查 `fitness-console` unit 的那条已无意义；⑥ 新增实测约束：`/var/lib/mysql` 是空壳（真 datadir `/home/jianglb/mysql`）、**binlog 已 356 MB（业务库的 23 倍，`binlog_format=ROW` + 30 天过期）**、`client_max_body_size` 全份配置未设（默认 1m）、~~`/kb/` 的 `location` 在 443 与 8699 两块里各有一份~~（2026-10-02 两块一起摘）、**零自动备份**（crontab/timer 都无，`/home/jianglb/backup` 最新一份停在 2026-09-14）。
- **2026-09-27 上线 investment-kb 只读站**：新 unit + `/etc/nginx/.htpasswd-kb` + sites-enabled 配置加 `location /kb/`（备份 `/etc/nginx/jianglvbo.site.sites-enabled.bak-20260927`）。同批纠正本 skill 两处失真：sites-enabled 不是软链、vault 镜像确实有一份推上来了（只读站数据源）。
- **2026-09-27 服务器清理（用户拍板）**：删 `~/src`（502M＝redis-stable 编译树 + redis.tar.gz）与 `investment-dashboard-dump-pre-rename-20260925.sql`（5.8M），释放约 508M。**Redis 本体不受影响**：可执行文件在 `~/redis/bin`（`/proc/<pid>/exe` 已核实），版本 v8.10.1 jemalloc-5.3.0，删后 `redis-investment` 仍 active、PING 通；要重建就照本 skill Redis 段重装。
- 服务器 home 现存：`backup fitness-server investment-dashboard investment-kb jianglvbo.site.conf mysql qa redis`（`src` 已没了，别再去找）。
