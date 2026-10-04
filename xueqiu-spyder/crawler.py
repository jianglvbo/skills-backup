import time
import re
import sys
import random
import logging
import subprocess
import os
import urllib.parse

import config
import ego_browser

logger = logging.getLogger(__name__)



# ── 浏览器通道（2026-09-15 拍板「以后别用 chrome 了，用 ego lite」；2026-09-16 收口）
# 只认 ego lite（ego_browser.EgoBridge）。曾经存在的 Chrome CDP 回落路径已整段删除：
# `auto` 在 ego 桥超时时静默拉起过 Google Chrome，被用户当场抓到。


class CrawlerError(Exception):
    pass


class TaskSpaceLost(CrawlerError):
    """ego 任务空间已不属于 agent（用户接管 / 空间结束）。

    2026-09-16 实测：用户碰了一下浏览器，ego 就把空间交还给用户，之后每条请求都返回
    "The user has taken control of this task space…"。这种错误**重试没有意义**，
    必须立刻停手、如实汇报，等用户明确说继续再 claim 空间（ego 的硬约束）。
    """


class XueqiuCrawler:
    """复用真实浏览器环境绕过 WAF：**只走 ego lite**（2026-09-16 起 Chrome 路径已删除）"""

    # 详情页补全连续失败多少条就停（多半是任务空间被接管/结束，继续硬撞只是刷日志）
    _ENRICH_FAIL_LIMIT = 3

    def __init__(self):
        self._ego = None
        self._page = None
        self._page_override = None
        # timeline 端点状态：v4 被 WAF 405 时自动降级到旧版路径（2026-09-09 固化）
        self._timeline_url = config.USER_TIMELINE_URL
        # 每页条数固定 20：请求不再拼 count（UI 不发，服务端默认 20）；此值只供窗口门槛
        # 的「页数用满」数学使用，改大会让门槛失灵（漏判 exit 3 → 断点误推进 → 漏采）
        self._timeline_count = 20
        self._degraded = False
        self._shot_tag = time.strftime("%Y%m%d-%H%M%S")   # 本次采集的截图批次号
        self._detail_seen = 0
        self._connect_browser()

    @property
    def _main_page(self):
        """主页面句柄（ego 桥的主页）；需要临时换页时设 _page_override，其余流程一律读这里"""
        return self._page_override or self._page

    def _connect_browser(self):
        """连接浏览器：**只允许 ego lite**。

        2026-09-16 用户实锤：`auto` 模式在 ego 桥启动超时（任务空间被交接卡住）时
        静默回落，真的拉起了 Google Chrome（`--remote-debugging-port=9222
        --user-data-dir=…/xueqiu-spyder/chrome-profile`）。用户口径是"别用 Chrome"，
        所以自动回落路径整段删除：ego 不可用就直接失败并说清怎么修。
        """
        mode = config.BROWSER_TRANSPORT
        if mode != "ego":
            raise CrawlerError(
                f"XUEQIU_TRANSPORT={mode!r} 已停用：本工具只走 ego lite"
                f"（用户 2026-09-15/16 拍板）。去掉该环境变量即可（默认 ego）。")
        try:
            self._connect_ego()
            self._wake_ego()          # 默认不抢焦点（EGO_WAKE=0）；滑块时另有激活
        except Exception as e:
            raise CrawlerError(
                f"ego lite 通道不可用：{e}\n"
                f"（请先打开 ego lite 并登录雪球，确认 `ego-browser --help` 可用；"
                f"本工具不会回落到 Chrome）")

    def _connect_ego(self):
        """连接 ego lite：不启动任何浏览器，只把动作转发给它（见 ego_browser.py）

        2026-09-15 迁移：原来这里连 Chrome 的 CDP 调试端口（并在没有调试端口时自己
        拉起一个带调试端口的 Chrome）。ego lite 不对外暴露 CDP 端口，故改走
        ego-browser 的 Node 运行时；登录态直接复用 ego 里已登录的雪球会话，
        不再需要单独的采集 profile（~/.cache/xueqiu-spyder/chrome-profile 随之退役）。
        """
        bridge = ego_browser.EgoBridge()
        hello = bridge.start()
        self._ego = bridge                 # 桥即"浏览器"：new_page() / stop() 都在它身上
        self._page = bridge.main_page
        logger.info("已连接 ego lite（桥进程 pid=%s）", hello.get("pid"))

    # ── 现场可见性（2026-09-16 用户要求）──────────────────────────────
    def _handle_slider(self, where=""):
        """命中滑块/安全验证 → **把 ego lite 交给用户接管**，等其过完再继续。

        2026-09-16 用户要求：「如果遇到了滑块，记得把 ego lite 让我接管」。
        这不是可选的等待：ego 的硬约束是用户一旦接管，agent 侧命令全部暂停，
        所以正确姿势是 handOff → 等控制权回来 → 重试本页。
        返回 True=已过验证（重试）；False=超时或交接失败（按 WAF 处理）。
        """
        wait_min = int(config.EGO_SLIDER_WAIT_MS / 60000)
        logger.warning(
            "🧩 命中滑块/安全验证（%s）→ 已把 ego lite 交给你接管："
            "请在浏览器里完成验证，完成后自动继续（最多等 %d 分钟）",
            where or "未标注位置", wait_min,
        )
        self._shot("slider", page=self._ego.main_page if self._ego else None)
        if not self._ego:
            return False
        ego_browser.activate_ego()
        try:
            if self._ego.handoff(wait_ms=config.EGO_SLIDER_WAIT_MS):
                logger.warning("✅ 已拿回控制权（%s），重试本页", where or "")
                self._shot("slider-cleared", page=self._ego.main_page)
                return True
            logger.error("⏳ 等待超时（%s）：滑块仍未处理，按 WAF 中止本轮", where or "")
        except Exception as e:
            logger.error("滑块交接失败（%s）：%s", where or "", e)
        return False

    def _wake_ego(self):
        """把 ego lite 窗口拉到前台（**默认不做**）。

        2026-09-16 用户口径：「不要让 ego lite 一直跳到我前面，但是如果遇到滑块请激活
        ego lite，让我注意到」。所以：
          · 默认 `EGO_WAKE=0`，采集开始/结束都**不**抢焦点；
          · 只有命中滑块时由 `_handle_slider()` 激活一次（那条独立于本开关）。
        想让采集时也置前：设 `XUEQIU_EGO_WAKE=1`。
        """
        if not config.EGO_WAKE or sys.platform != "darwin":
            return
        try:
            subprocess.run(
                ["osascript", "-e", 'tell application "ego lite" to activate'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5,
            )
            logger.info("已把 ego lite 窗口拉到前台一次（XUEQIU_EGO_WAKE=1 才会这样）")
        except Exception as e:
            logger.debug("激活 ego 窗口失败（不影响采集）：%s", e)

    def _shot(self, tag, page=None, force=False):
        """落一张现场截图留证（风控/异常时自动调用，也可按间隔抽帧）。

        ego 里页面是可见的，但风控提示往往一闪而过；截图让用户事后能核对
        「当时页面上到底是什么」。落图目录 `XUEQIU_EGO_SHOT_DIR`
        （默认 `~/.cache/xueqiu-spyder/shots`），失败只记日志、不影响采集。
        """
        if not config.EGO_SHOT_DIR:
            return None
        target = page if page is not None else self._main_page
        if target is None:
            return None
        try:
            os.makedirs(config.EGO_SHOT_DIR, exist_ok=True)
            stamp = time.strftime("%H%M%S")
            path = os.path.join(config.EGO_SHOT_DIR, self._shot_tag, f"{stamp}-{tag}.png")
            target.screenshot(path)
            logger.warning("现场截图已保存：%s", path)
            return path
        except Exception as e:
            if force:
                logger.warning("现场截图失败（不影响采集）：%s", e)
            return None

    def _fetch_timeline_page(self, user_page, user_id, page_num, type_code=None):
        """timeline 单页抓取。请求形态贴 UI（2026-09-28 实测）：`page&user_id[&type]&_时间戳`——
        全部 tab 不带 type（**带 type=0 会被 WAF 直接挑战**），热门 tab 才带 type=9；
        count 参数 UI 不发（服务端默认 20），别再拼上去。
        fetch 一律用 **相对路径**：2026-09-21 起雪球把 apex 域
        (xueqiu.com) 302 到 www.xueqiu.com，页内绝对 URL fetch 会跨域跳转，
        带 cookie 的跨源重定向被浏览器直接掐掉（Failed to fetch，连状态码都没有）；
        相对路径随页面 origin（www）同源请求则正常返回。"""
        return user_page.evaluate(
            """async (args) => {
                try {
                    const path = new URL(args.url).pathname;
                    let q = `?page=${args.page}&user_id=${args.uid}`;
                    if (args.type) q += `&type=${args.type}`;
                    q += `&_=${args.ts}`;
                    const resp = await fetch(path + q);
                    const ct = resp.headers.get('content-type') || '';
                    if (!ct.includes('json')) return {ok: false, error: 'not json'};
                    const data = await resp.json();
                    if (data.error_code) return {ok: false, error: data.error_description};
                    return {ok: true, statuses: data.statuses || [], count: data.count};
                } catch(e) { return {ok: false, error: e.message}; }
            }""",
            {"uid": user_id, "page": page_num, "url": self._timeline_url,
             "type": type_code, "ts": int(time.time() * 1000)},
        )

    def _degrade_timeline(self):
        """v4 timeline 端点被 WAF 拦截时，自动切到旧版路径（2026-09-09 固化；
        2026-09-28 起请求不再拼 count 参数——UI 不发，服务端默认 20，与旧版上限一致）"""
        if self._degraded:
            return False
        self._timeline_url = config.USER_TIMELINE_URL_FALLBACK
        self._degraded = True
        logger.warning(
            "timeline 端点失败，自动降级到旧版路径重试: %s (count=20)",
            self._timeline_url,
        )
        return True

    def _fetch_json(self, url, params=None):
        """在浏览器内 fetch API，返回 JSON；检测 WAF/滑块验证页"""
        query = "&".join(f"{k}={v}" for k, v in (params or {}).items())
        full_url = f"{url}?{query}" if query else url

        slider_retry_left = 1          # 滑块交接后允许重试本页 1 次（防死循环）
        for attempt in range(config.MAX_RETRIES):
            time.sleep(config.REQUEST_DELAY)
            try:
                result = self._main_page.evaluate(
                    """async (url) => {
                        try {
                            const resp = await fetch(url);
                            const ct = resp.headers.get('content-type') || '';
                            if (!ct.includes('json')) {
                                const snippet = (await resp.text()).slice(0, 300);
                                return {ok: false, error: 'not json', snippet: snippet};
                            }
                            return {ok: true, data: await resp.json()};
                        } catch(e) {
                            return {ok: false, error: e.message};
                        }
                    }""",
                    full_url,
                )

                if result.get("ok"):
                    return result["data"]

                # 任务空间被接管/结束：立刻停手，不重试（重试只会刷屏）
                blob_early = f"{result.get('error') or ''} {result.get('snippet') or ''}"
                if re.search(r"taken control|not assigned to the agent|Task space not found|commands are paused",
                             blob_early, re.I):
                    self._shot("space-lost", force=False)
                    raise TaskSpaceLost(
                        "ego 任务空间已被接管或结束——采集停止。"
                        "（要接着跑：在 ego 里把任务空间交回 agent，或让用户明确说「继续」后重新 claim）"
                    )

                # WAF / 滑块 / 安全验证检测（雪球阿里云防护特征）
                snippet = (result.get("snippet") or "") + (result.get("error") or "")
                if re.search(r"滑动|安全验证|captcha|无感验证|请完成验证|访问验证", snippet, re.I):
                    if slider_retry_left > 0 and self._handle_slider("timeline 接口"):
                        slider_retry_left -= 1
                        logger.warning("已由用户完成验证 → 重试本页")
                        continue
                    self._shot("waf-timeline")
                    raise CrawlerError(
                        f"疑似触发 WAF/滑块验证 ({full_url}) —— 停止采集，等待数分钟或人工在浏览器完成验证后重试"
                    )
                logger.warning(f"API 请求失败: {result.get('error')} (attempt {attempt + 1})")
            except CrawlerError:
                raise
            except Exception as e:
                if attempt == config.MAX_RETRIES - 1:
                    raise CrawlerError(f"请求失败 ({full_url}): {e}")
                time.sleep(2 ** attempt)

        raise CrawlerError(f"超过最大重试次数: {full_url}")

    def get_stock_posts(self, symbol, sort="reply", page=1, count=None):
        """获取某只股票的讨论帖"""
        if count is None:
            count = config.POSTS_PER_PAGE
        params = {
            "symbol": symbol,
            "sort": sort,
            "source": "all",
            "count": count,
            "page": page,
        }
        data = self._fetch_json(config.SEARCH_STATUS_URL, params)
        return data.get("list", [])

    def get_post_full_text(self, target, _slider_retried=False):
        """访问帖子详情页获取完整内容 + 精确发布时间 + 配图清单
        返回 (full_text, published_ms or None, imgs or None)；target 如 /5243796549/376934652
        imgs = [{url, para}]：图在原帖第几段之后。**零点击**——雪球把折叠的配图渲染成
        <a class="co-img-link" href="…jpg">查看图片</a>，读 href 即可，不必点开（2026-10-01 实测）。
        `_slider_retried`：滑块交接后只重试一次，防"验证页反复出现"死循环"""
        detail_page = self._ego.new_page()
        try:
            detail_page.goto(
                f"https://xueqiu.com{target}",
                wait_until="domcontentloaded",
                timeout=15000,
            )
            try:
                detail_page.wait_for_selector(".article__bd__detail", timeout=5000)
            except Exception:
                pass
            result = detail_page.evaluate("""() => {
                const el = document.querySelector('.article__bd__detail');
                const text = el ? el.textContent.trim() : '';
                let published = '';
                const meta = document.querySelector('meta[property="article:published_time"]');
                if (meta && meta.content) {
                    published = meta.content;
                } else {
                    const m = document.body?.innerText?.match(/发布于\\s*(\\d{4}-\\d{2}-\\d{2}\\s*\\d{2}:\\d{2})/);
                    if (m) published = m[1];
                }
                // 配图：img[src] 与折叠用的 a.co-img-link[href] 一起收，按文档顺序去重
                let imgs = [];
                if (el) {
                    const junk = u => !u || /emoji|face_regular|badge|medal|identity_icon|xavatar|\\/community\\/|_logo|icon_/i.test(u);
                    const norm = u => (u || '').replace(/!\\d*x*\\d*\\.jpg$|!custom\\.jpg$|!800\\.jpg$/, '');
                    const nodes = Array.from(el.querySelectorAll('img, a.co-img-link, a[href]'));
                    const blocks = Array.from(el.children);
                    const paraOf = node => {
                        let n = 0;
                        for (const b of blocks) {
                            if (b === node || b.contains(node)) return n;
                            if ((b.innerText || '').trim()) n++;
                        }
                        return n;
                    };
                    const seen = {};
                    for (const nd of nodes) {
                        const raw = nd.tagName === 'IMG' ? nd.src : nd.getAttribute('href');
                        if (junk(raw)) continue;
                        const u = norm(raw);
                        if (!u || !/\\.(png|jpe?g|gif|webp|bmp)$/i.test(u) || seen[u]) continue;
                        seen[u] = 1;
                        imgs.push({url: u, para: paraOf(nd)});
                    }
                }
                return {text: text, published: published, imgs: imgs,
                        title: document.title || '',
                        snippet: (document.body?.innerText || '').slice(0, 300)};
            }""")
            # 2026-09-12：详情页可能被 WAF 拦成 405/验证页——此前静默返回空正文，
            # 整批继续硬撞直到封禁加重（09-08 批 69 条摘要帖就是这么来的）。
            # 这里改为显式抛错中止本轮，交由编排层等待冷却后重跑。
            blob = f"{result.get('title', '')} {result.get('snippet', '')}"
            if re.search(r"(?<!\d)405(?!\d)|滑动|安全验证|访问验证|请完成验证|captcha", blob, re.I):
                if not _slider_retried and self._handle_slider(f"详情页 {target}"):
                    return self.get_post_full_text(target, _slider_retried=True)
                self._shot(f"waf-detail-{target.strip('/').replace('/', '_')}", page=detail_page)
                raise CrawlerError(
                    f"详情页命中 WAF/405（{target}）—— 中止本轮采集，等待冷却后重跑；"
                    f"页面特征: {blob.strip()[:80]}"
                )
            return result.get("text", ""), result.get("published") or None, result.get("imgs") or None
        except CrawlerError:
            raise
        except Exception as e:
            if "任务空间已不属于 agent" in str(e):
                logger.error(
                    "获取帖子详情失败 %s：ego 任务空间已被接管/结束——停止补全，"
                    "剩余帖子按 API 摘要处理", target,
                )
            else:
                logger.warning(f"获取帖子详情失败 {target}: {e}")
            self._shot(f"detail-error-{target.strip('/').replace('/', '_')}", page=detail_page)
            return "", None, None
        finally:
            detail_page.close()

    def enrich_posts_full_text(self, posts):
        """对 description 被截断的帖子，访问详情页补全内容；并用详情页精确时间覆盖 API created_at"""
        import datetime as _dt
        failed = 0
        for post in posts:
            desc = post.get("description", "") or ""
            text = post.get("text", "") or ""
            target = post.get("target", "")
            # 配图要留一份 API 原始 HTML：下面补全成功会把 post["text"] 覆盖成详情页纯文本，
            # 那时 <img>/<a href> 就没了（analyzer 只认 HTML 里的 URL）。setdefault 不动正文口径。
            if "<" in text:
                post.setdefault("text_html", text)
            # text 为空，或 description 以 ... 结尾（摘要截断），说明正文可能被截断 → 详情页补全
            # 需要补全的判据：详情页目标存在，且正文缺失/明显是 API 摘要
            #   ① 没有正文；② 描述被截断（...）；③ 正文短于截断描述（半截内容）
            # 只按 desc.endswith("...") 判断会漏掉"text 本身就不完整但不带省略号"的帖
            # （2026-09-16 实测：description 截断、text 半截，两者都不长 → 旧判据直接跳过）
            looks_truncated = not text or desc.endswith("...") or len(text) < len(desc)
            if target and looks_truncated:
                # 节流按 execution-guide「单帖接口限流」的安全速率：≥1.2s + 抖动 + 每 N 次长歇
                # （2026-10-01 实测：1.0s 无长歇 → 16 次跳转即命中滑块，白烧一轮）
                time.sleep(random.uniform(*config.DETAIL_PACE))
                if (config.DETAIL_BREAK_N
                        and self._detail_seen and self._detail_seen % config.DETAIL_BREAK_N == 0):
                    logger.info("  详情页已访问 %d 次，长歇 %.0f 秒（WAF 安全速率）",
                                self._detail_seen, config.DETAIL_BREAK_S)
                    time.sleep(config.DETAIL_BREAK_S)
                self._detail_seen += 1
                # 抽帧留证：详情页是风控最常出现的地方，按间隔落图便于回看
                if config.EGO_SHOT_EVERY and self._detail_seen % config.EGO_SHOT_EVERY == 0:
                    self._shot(f"progress-{self._detail_seen}")
                post["needs_full"] = True     # 待补全：失败时据此标「摘要」而不是「全文」
                full, published, dom_imgs = self.get_post_full_text(target)
                if dom_imgs:
                    # 详情页是比 API 更全的配图来源（长文的图常常不在 API 摘要 HTML 里）
                    post["dom_imgs"] = dom_imgs
                if full:
                    # 详情页是权威来源：**只要抓到就采用**，不再要求"比 API 的 text 长"
                    # （2026-09-16 修：此前把"长度相当但内容更全"的帖误判为失败，
                    #   连撞三次触发熔断，导致后面几十条全部跳过）
                    changed = len(full) != len(text or "")
                    post["text"] = full
                    post["needs_full"] = False
                    logger.info(
                        f"  详情页正文{'覆盖' if changed else '确认'} {target} ({len(full)} 字)"
                    )
                else:
                    failed += 1
                    if failed >= self._ENRICH_FAIL_LIMIT:
                        logger.error(
                            "详情页补全连续失败 %d 条（常见原因：用户接管了任务空间 / 任务空间已结束）"
                            "——停止补全；剩余帖子按 API 摘要处理并标「摘要」，不会误标「全文」",
                            failed,
                        )
                        break
                # 详情页精确时间覆盖 API 时间（详情页为权威来源）
                if published:
                    try:
                        pub = published.strip().replace("T", " ")[:16]
                        dt = _dt.datetime.strptime(pub, "%Y-%m-%d %H:%M")
                        new_ms = int(dt.timestamp() * 1000)
                        old_ms = post.get("created_at") or 0
                        if new_ms != old_ms:
                            post["created_at"] = new_ms
                            logger.info(f"  详情页时间覆盖 {target}: {published}")
                    except ValueError:
                        pass
        return posts

    def search_user(self, keyword):
        """搜索用户，返回 [{name, href, uid}] 列表"""
        encoded = urllib.parse.quote(keyword)
        url = f"https://xueqiu.com/k?q={encoded}&forceRedirect=1&page=1&type=user"
        search_page = self._ego.new_page()
        try:
            search_page.goto(url, wait_until="domcontentloaded", timeout=15000)
            try:
                search_page.wait_for_selector(
                    ".search__user__card__content, a.user-name", timeout=5000
                )
            except Exception:
                pass
            users = search_page.evaluate("""() => {
                const cards = document.querySelectorAll('.search__user__card__content');
                const result = [];
                for (const card of cards) {
                    const nameEl = card.querySelector('.user-name');
                    const descEl = card.querySelector('p');
                    result.push({
                        name: nameEl ? nameEl.textContent.trim() : '',
                        href: nameEl ? nameEl.getAttribute('href') : '',
                        desc: descEl ? descEl.textContent.trim().substring(0, 80) : ''
                    });
                }
                if (!result.length) {
                    const anchors = document.querySelectorAll('a.user-name');
                    for (const a of anchors) {
                        result.push({
                            name: a.textContent.trim(),
                            href: a.getAttribute('href') || ''
                        });
                    }
                }
                return result;
            }""")
            # 解析 href 中的 uid
            for u in users:
                u["uid"] = self._extract_uid_from_href(u.get("href", ""))
            return users
        finally:
            search_page.close()

    def _extract_uid_from_href(self, href):
        """从 href 中提取数字 uid，如 /u/1234 -> '1234'"""
        if not href:
            return ""
        m = re.match(r"/u/(\d+)", href)
        if m:
            return m.group(1)
        # 虚荣路径如 /zzyandsnow，需要 resolve
        return ""

    def resolve_user_id(self, vanity_path):
        """将虚荣路径（如 /zzyandsnow）解析为数字 user_id"""
        resolve_page = self._ego.new_page()
        try:
            resolve_page.goto(
                f"https://xueqiu.com{vanity_path}",
                wait_until="domcontentloaded",
                timeout=15000,
            )
            try:
                resolve_page.wait_for_selector(".user-name", timeout=5000)
            except Exception:
                pass
            # 从跳转后的 URL 或页面内容提取数字 ID
            final_url = resolve_page.url
            m = re.search(r"/u/(\d+)", final_url)
            if m:
                return m.group(1)
            # 从页面 JS 变量中提取
            uid = resolve_page.evaluate("""() => {
                const m = document.body?.innerHTML?.match(/"id":(\\d{5,})/);
                return m ? m[1] : '';
            }""")
            return uid or ""
        finally:
            resolve_page.close()

    def find_user_id(self, keyword):
        """搜索用户名并返回第一个精确匹配的数字 uid"""
        users = self.search_user(keyword)
        if not users:
            raise CrawlerError(f"未找到用户: {keyword}")
        # 优先精确匹配
        target = None
        for u in users:
            if u["name"] == keyword:
                target = u
                break
        if not target:
            target = users[0]
            logger.info(f"未精确匹配，使用第一个结果: {target['name']}")
        # 如果已有数字 uid 直接返回
        if target.get("uid"):
            return target["uid"], target["name"]
        # 否则解析虚荣路径
        href = target.get("href", "")
        if href:
            uid = self.resolve_user_id(href)
            if uid:
                return uid, target["name"]
        raise CrawlerError(f"无法解析用户ID: {target}")

    def get_user_all_posts_with_info(self, user_id, max_pages=10, window_lo_ms=None):
        """在同一个页面中获取用户信息和所有帖子，避免重复导航

        昵称来源（2026-09-24 定论，别再改回 DOM）：**取 timeline API 首条帖子的
        `user.screen_name`**——API 自带该字段（实测 `/statuses/user_timeline.json`
        返回 `user:{screen_name, id}`），与帖子数据同源、无渲染竞态。
        历史两种 DOM 取法都踩过坑：`.user-name` 抢在时间轴渲染前会命中侧栏
        「用户推荐」（曾把 10 个博主的 author 写成「大道无形我有型」）；改
        `document.title` 只是缓解，页面加载中 title 未成形时仍会回落到侧栏。
        DOM 仅作末位兜底（拿不到 API 数据时才用）。

        window_lo_ms（2026-09-28 新博主首采流）：给了窗口起点就**翻到边界自动停**——
        每页取完，排除置顶后最旧帖 ≤ 窗口起点即止。置顶帖常年挂第 1 页首位
        （可能是几年前的旧帖），不排除会把边界误判在第 1 页、整轮只采到 20 条。
        主页不是无限滚动而是底部分页控件（实测 20 条/页），这里的 API 翻页与
        用户在页面上点「第 N 页」是同一个请求。
        """
        user_page = self._ego.new_page()
        all_statuses = []
        screen_name = str(user_id)
        try:
            user_page.goto(
                f"https://www.xueqiu.com/u/{user_id}",
                wait_until="domcontentloaded",
                timeout=15000,
            )

            # 在同一页面分页获取帖子
            for page_num in range(1, max_pages + 1):
                time.sleep(config.page_delay())
                result = self._fetch_timeline_page(
                    user_page, user_id, page_num)
                if not result.get("ok") and self._degrade_timeline():
                    # 首次失败 → 自动降级端点后**同页重试**（只降级一次）；
                    # 不能 continue 跳下一页——降级后该页数据还没拿到，跳过=漏采最新一页
                    result = self._fetch_timeline_page(
                        user_page, user_id, page_num)
                if not result.get("ok"):
                    logger.warning(f"用户 {user_id} 第 {page_num} 页失败: {result.get('error')}")
                    self._shot(f"timeline-fail-page{page_num}", page=user_page)
                    break
                statuses = result.get("statuses", [])
                if not statuses:
                    break
                all_statuses.extend(statuses)
                logger.info(f"  第 {page_num} 页获取 {len(statuses)} 条 (共 {len(all_statuses)})")
                if window_lo_ms:
                    live = [s for s in statuses
                            if not (s.get("mark") == 1 or s.get("pinned"))]
                    oldest_live = min((s.get("created_at") or 0) for s in live) if live else 0
                    if oldest_live and oldest_live <= window_lo_ms:
                        logger.info(
                            "  已翻到窗口起点（%s），停止翻页",
                            time.strftime('%Y-%m-%d %H:%M', time.localtime(oldest_live / 1000)),
                        )
                        break

            # 昵称：API 首条帖子的 user.screen_name（与帖子同源，无渲染竞态）。
            # 本轮无帖（窗口内无新帖）时拿不到 → 回落到 DOM，再不行用传入的 user_id。
            for st in all_statuses:
                sn = (st.get("user") or {}).get("screen_name")
                if sn:
                    screen_name = str(sn).strip()
                    break
            else:
                dom_name = user_page.evaluate("""() => {
                    const t = document.title || '';
                    if (t.includes(' - 雪球')) return t.replace(' - 雪球', '').trim();
                    return '';
                }""")
                if dom_name:
                    screen_name = dom_name
        finally:
            user_page.close()
        return screen_name, all_statuses

    def get_user_hot_posts(self, user_id, pages=5):
        """热门 tab 前 N 页（2026-09-28 新增，新博主首采第 3 步）。

        请求形态＝UI 点「热门」标签：同一 timeline 端点 + `type=9`（实测可翻页，
        该维度总量约 200 条、每页 20）。**不做端点降级**——旧版路径 + type=9 的
        排序行为未验证，降级后若静默返回「全部」排序，热门数据就被换成时间线，
        宁缺毋滥：单页失败重试一次，再失败就停手保留已取页数。
        热门页 1 含置顶帖（mark=1），由上层有意保留（首采一次性，无重复采集风险）。
        """
        hot = []
        user_page = self._ego.new_page()
        try:
            user_page.goto(
                f"https://www.xueqiu.com/u/{user_id}",
                wait_until="domcontentloaded",
                timeout=15000,
            )
            for page_num in range(1, pages + 1):
                time.sleep(config.page_delay())
                result = self._fetch_timeline_page(user_page, user_id, page_num, type_code=9)
                if not result.get("ok"):
                    time.sleep(5)
                    result = self._fetch_timeline_page(user_page, user_id, page_num, type_code=9)
                if not result.get("ok"):
                    logger.warning(
                        "热门第 %d 页失败（%s）——保留已取的 %d 条，余下放弃",
                        page_num, result.get("error"), len(hot))
                    self._shot(f"hot-fail-page{page_num}", page=user_page)
                    break
                statuses = result.get("statuses", [])
                if not statuses:
                    break
                hot.extend(statuses)
                logger.info(f"  热门第 {page_num} 页获取 {len(statuses)} 条 (共 {len(hot)})")
        finally:
            user_page.close()
        return hot

    def get_user_profile(self, user_id):
        """主页档案（2026-09-28 新增，新博主首采第 1 步）：uid/昵称/头像/简介/粉丝/总帖数。

        头像取 DOM（xavatar 大图，页面上就是 240x240）；API 的 `profile_image_url`
        是逗号拼接的多尺寸串，仅作兜底（取首段补域名）。
        昵称/简介/粉丝数取 timeline API 首条帖的 `user` 对象——与帖子数据同源、
        无 DOM 渲染竞态（侧栏「用户推荐」顶替昵称的坑见 get_user_all_posts_with_info）。
        """
        profile = {"uid": str(user_id), "screen_name": "", "avatar": "", "description": "",
                   "followers_count": 0, "status_count": 0}
        user_page = self._ego.new_page()
        try:
            user_page.goto(
                f"https://www.xueqiu.com/u/{user_id}",
                wait_until="domcontentloaded",
                timeout=15000,
            )
            try:
                user_page.wait_for_selector("img", timeout=5000)
            except Exception:
                pass
            dom = user_page.evaluate("""() => {
                const uid = (location.pathname.match(/u\\/(\\d+)/) || [])[1] || '';
                let avatar = '';
                for (const img of document.querySelectorAll('img')) {
                    const cls = (img.className || '') + ' ' +
                        ((img.parentElement && img.parentElement.className) || '');
                    const src = img.currentSrc || img.src || '';
                    if (/avatar|profile/i.test(cls) && /xavatar\\.imedao\\.com/.test(src)) {
                        avatar = src; break;
                    }
                }
                if (!avatar) {
                    for (const img of document.querySelectorAll('img')) {
                        const src = img.currentSrc || img.src || '';
                        if (/xavatar\\.imedao\\.com/.test(src) &&
                            !/!50x50|!30x30|badge|medal|emoji/.test(src)) { avatar = src; break; }
                    }
                }
                return { uid, avatar };
            }""")
            if dom.get("uid"):
                profile["uid"] = dom["uid"]
            profile["avatar"] = dom.get("avatar") or ""
            time.sleep(config.REQUEST_DELAY)
            uid = profile["uid"] or user_id
            result = self._fetch_timeline_page(user_page, uid, 1)
            if not result.get("ok") and self._degrade_timeline():
                result = self._fetch_timeline_page(user_page, uid, 1)
            if result.get("ok"):
                statuses = result.get("statuses") or []
                u = (statuses[0].get("user") or {}) if statuses else {}
                profile["screen_name"] = str(u.get("screen_name") or "").strip()
                profile["description"] = str(u.get("description") or "").strip()
                profile["followers_count"] = int(u.get("followers_count") or 0)
                profile["status_count"] = int(u.get("status_count") or 0)
                if not profile["avatar"]:
                    first = str(u.get("profile_image_url") or "").split(",")[0].strip()
                    if first:
                        profile["avatar"] = first if first.startswith("http") \
                            else f"https://xavatar.imedao.com/{first}"
            else:
                logger.warning("档案 API 取失败（%s）——仅 DOM 侧 uid/头像可用", result.get("error"))
        finally:
            user_page.close()
        return profile

    def close(self):
        """断开浏览器连接

        2026-09-15：ego 通道下 **只关桥进程，不关 ego 本身**——ego 是用户自己的浏览器，
        它的标签页与登录态要留给用户（桥进程退出时会把写入流关掉，ego 里的标签页保留）。
        """
        try:
            if self._ego:
                self._ego.stop()           # 关桥＝关标签 + 释放空间（finish({keep:[]})）
        except Exception:
            pass
        finally:
            self._ego = None
            # 结束**不再**激活窗口：2026-09-16 用户反馈批量采集时 ego 一直被拉到最前，
            # 干扰正常用电脑。要看现场让用户自己切（或单跑时看开头那次激活）。
