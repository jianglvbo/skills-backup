#!/usr/bin/env node
/*
 * ego 通道桥（2026-09-15 用户拍板：spyder 从 Chrome CDP 迁到 ego lite）。
 *
 * 为什么是这样一个文件：ego lite **不对外暴露 CDP 端口**（没有 `--remote-debugging-port`
 * 这类语义），它的 CDP 能力只经 `ego-browser nodejs` 的 Node 运行时暴露
 * （`page.cdp()` / `page.evaluate()`）。所以由 Python 侧起一个 unix socket 服务端，
 * 本脚本经 `-e` 传入、连接回来，之后两边用 **JSON Lines over socket** 通信；
 * 登录态与反爬特征全部由 ego 进程承载，本脚本不启动任何浏览器。
 *
 * 为什么不用 stdin 传协议（2026-09-15 实测，三个坑）：
 *   ① 脚本文本走 stdin → CLI 要等 EOF 才执行；
 *   ② 用 `-e` 传脚本但 stdin 是管道 → CLI 仍把管道当「脚本待从 stdin 读」而挂住；
 *   ③ stdin 给伪终端 → 脚本执行完进程立刻退出（PTY 上 events 不续命）。
 *   → 结论：stdin 一律给 /dev/null，协议另开 socket（与 Python 的父子关系无关，最稳）。
 *
 * 协议（每行一个 JSON 请求，回一行 JSON 响应）：
 *   {"id":1,"cmd":"goto","page":"p1","url":"https://xueqiu.com/"}
 *   {"id":2,"cmd":"evaluate","page":"p1","fn":"async (a) => ({...})","arg":{...}}
 *   {"id":3,"cmd":"waitForSelector","page":"p1","selector":"div","timeoutMs":8000}
 *   {"id":4,"cmd":"newPage"}                        // 新建 ego 标签页，回 {"label":"p2"}
 *   {"id":5,"cmd":"url","page":"p2"}
 *   {"id":6,"cmd":"close","page":"p2"}              // 只关该页（默认保留页签，见下）
 *   {"id":7,"cmd":"shutdown"}                       // 桥自行退出
 *   {"id":8,"cmd":"screenshot","page":"p1","path":"/abs/out.png"}   // 落图，供用户事后核对风控
 *   {"id":9,"cmd":"text","page":"p1"}               // 读页面可见文本（`document.body.innerText`）
 *   {"id":10,"cmd":"cookies"}                       // 读该会话 Cookie 串（给 requests 复用同一登录态）
 *   {"id":11,"cmd":"handoff","waitMs":900000}       // 把任务空间交给用户（过滑块），等用户交还后回执
 *
 *   真输入命令（2026-10-05 去 goto 化新增；走 CDP Input 域，事件 isTrusted=true）：
 *   {"id":12,"cmd":"click","page":"p2","selector":"a[href='/center/#/friends']","stripTarget":true,"settleMs":2000}
 *   {"id":13,"cmd":"click","page":"p2","text":"展开","scope":".timeline__item","nth":0}
 *   {"id":14,"cmd":"wheel","page":"p2","dy":600,"waitForMs":400}
 *   {"id":15,"cmd":"back","page":"p2"}              // 走历史栈；回执 {from,to,moved,index,depth,targetUrl}
 *
 *   **页签策略（2026-09-16 用户口径：随用随关）**：
 *   ① 页面确实开在 ego 里且可见（用户要盯风控），但**不抢焦点**；
 *   ② **工作页一进一出**：临时页用完立刻真关（`close` 直接 `page.close()`），
 *      下一次 `newPage` 再开一张——ego 任务空间有 8 个标签页上限（实测
 *      `Page budget reached (8/8)`），同一时刻只留一张工作页就不会撞顶；
 *   ③ 桥退出（shutdown / SIGTERM / SIGINT）时调 **`task.finish({keep: []})`**：
 *      agent 页签全关、**空间被释放**（回执 `closedSpace: true`）——"用完回收"的正解；
 *      只关页签会留下空空间，越攒越多（2026-09-16 用户指出）。
 *      例外：因「交给用户接管」而中断时不 finish（ego 要求：用户控制中不要 finish）。
 *
 *   fn 既接受函数表达式（`async (a) => {...}` / `() => {...}`），也接受裸表达式
 *   （`document.title`）；裸表达式会被包成 `async (a) => (expr)`，这样 Python 侧
 *   两种 Playwright 写法都能原样传来。
 *
 * 配置从哪来（2026-09-15 实测）：ego 的 Node 运行时**不继承父进程环境变量**
 * （`process.env.XUEQIU_EGO_SOCK` 是 undefined），所以配置由 Python 侧在启动时
 * 注入到脚本头部（见 ego_browser.py 的 `_bake_config`）：本文件第二行期望一个
 * `const __EGO_CFG = {...}`。字段：
 *   sock       必填：Python 侧 socket 路径
 *   space      复用已存在的任务空间 id（多轮采集沿用同一个，不新建）
 *   spaceName  任务空间名（仅新建时用到）
 *   url        启动时若当前页不在该域，先导航过去（默认雪球首页）
 *   （无其它可调项：会话内固定一张工作页，退出时统一关）
 */

const CFG = typeof __EGO_CFG !== "undefined" ? __EGO_CFG : {};
const SOCK = CFG.sock;
const SPACE = CFG.space;
const SPACE_NAME = CFG.spaceName || "xueqiu-spyder";
const HOME = CFG.url || "https://xueqiu.com/";

const pages = new Map();
let handedOff = false;      // 是否正处于「交给用户接管」状态（决定退出时要不要回收）
let task = null;
let workPage = null;                    // 会话内唯一的工作页（用完放回，下次复用）

const net = require("net");

function pageOf(label) {
  const page = pages.get(label || "p1");
  if (!page) throw new Error("unknown page label: " + label);
  return page;
}

async function boot() {
  // SPACE 既可能是数字 id（复用某空间），也可能是名字（新建/按名找）。
  // 2026-09-16 踩坑：一律 Number() 会把名字转成 NaN → "task space not found: NaN"。
  const spaceArg = SPACE == null || SPACE === ""
    ? SPACE_NAME
    : (String(SPACE).trim() !== "" && !isNaN(Number(SPACE)) ? Number(SPACE) : String(SPACE));
  task = await taskSpace(spaceArg);
  // 注册表只装本会话真正在用的页签：ego 的页签编号是**整个浏览器单调递增**的
  // （本次实测跨进程递增到 p15），跨会话残留的旧条目会让 pageOf() 指到别的 space 的页。
  pages.clear();
  workPage = null;
  // 取主页面：**不能假定 p1 一定存在或可用**——2026-09-16 踩坑：`task.page("p1")` 对
  // 一个已被关掉的页**不抛错**，只返回死引用，直到后面 `.url()` 才炸
  //（现象：桥崩在 "page p1 was closed"，Python 侧只看到"启动超时"）。
  // 所以这里用一次轻量探针验活，坏了就新开一张。
  let first = null;
  try {
    const candidate = task.page("p1");
    await candidate.url();            // 探针：死引用在这里就会抛
    first = candidate;
  } catch (e) {
    first = await task.newPage();     // 没有可用 p1 → 自己开一张
    process.stderr.write("[bridge] 空间内没有可用 p1，已新建标签页 " + first.label + "\n");
  }
  pages.set("p1", first);
  const current = await first.url();
  if (!/xueqiu\.com/.test(current)) {
    await first.goto(HOME);
    await first.waitForLoadState("domcontentloaded");
  }
  // 用完收尾：桥退出（正常 shutdown / 被杀）时把桥自己开的页签全关
  process.on("exit", () => { cleanupOwnPages(); });
  process.on("SIGTERM", () => { cleanupOwnPages(); process.exit(0); });
  process.on("SIGINT", () => { cleanupOwnPages(); process.exit(0); });
}

/* 用完回收：**关页签 + 释放空间**。
   用户口径（2026-09-16）：「标签、空间都是的，用完要回收」。
   正确姿势是 ego 的 `task.finish({ keep: [] })`——实测回执
   `{closedSpace: true, closedManagedLabels: [...]}`：agent 页签全关、空间被释放。
   只逐个 close 页签会留下空空间，越攒越多（这正是用户指出来的问题）。
   注意：必须 await（process.exit 不等异步）；交接给用户的途中不回收。 */
async function cleanupOwnPages() {
  if (handedOff) {
    process.stderr.write("[bridge] 处于用户接管中，跳过回收（空间留给用户收尾）\n");
    return;
  }
  try {
    const receipt = await task.finish({ keep: [] });
    process.stderr.write("[bridge] 回收完成: " + JSON.stringify(receipt) + "\n");
  } catch (e) {
    process.stderr.write("[bridge] finish 失败（" + String(e).slice(0, 80) + "），退回逐个关页签\n");
    for (const [label, p] of Array.from(pages.entries())) {
      try { await p.close(); pages.delete(label); } catch (e2) {}
    }
  }
  workPage = null;
}

function forgetAndClose(page) {
  for (const [label, p] of Array.from(pages.entries())) {
    if (p === page) pages.delete(label);
  }
  try { page.close(); } catch (e) { /* 已关或连接已断，忽略 */ }
}

/* ── 真输入的两个底座（2026-10-05 去 goto 化）───────────────────────────────────
   为什么不用 evaluate 里的 el.click()：那是 **JS 合成事件**（isTrusted=false，
   没有 mousemove / hover / pointerdown 序列），拿它替 goto 只是换一种假动作。
   为什么不用 page.click(selector)：ego 的 Page 不是 Playwright Page
   （实测没有 locator / goBack，click 的 options 还要过 validatePublicApiOptions 严格校验），
   而 CDP `Input.dispatchMouseEvent` 实测直接可用（10-05 pageApi probe 回执 {}），
   自己发事件才能控制「先悬停、再分段移动过去」的真人轨迹。 */
async function locateForClick(page, q) {
  // 找元素 + 滚到视口中间 + 报中心点坐标（CDP 输入用的就是视口 CSS px）
  return await page.evaluate((a) => {
    const norm = s => (s || "").replace(/[\s　]+/g, " ").trim();
    let list = [];
    if (a.selector) {
      // within：先按 nth 选中「行」（如 .profiles__user），再在行内找子元素。
      // 不这么做的后果实测过：行索引与全页 a.avatar 索引不对齐 → 点到别人头上。
      let scope = document;
      if (a.within) {
        const rows = [...document.querySelectorAll(a.within)];
        const row = rows[a.nth || 0];
        if (!row) return { ok: false, total: 0, reason: "within 没有第 " + a.nth + " 行（共 " + rows.length + " 行）" };
        scope = row;
      }
      list = [...scope.querySelectorAll(a.selector)];
    } else {
      const root = a.scope ? (document.querySelector(a.scope) || document) : document;
      const want = norm(a.text);
      list = [...root.querySelectorAll("a,button,span,div,li")]
        .map(el => ({ el, t: norm(el.innerText || el.textContent) }))
        .filter(o => a.exact ? o.t === want : o.t.includes(want))
        .sort((x, y) => x.t.length - y.t.length)      // 取文案最短的那个，避免命中包住它的大容器
        .map(o => o.el);
    }
    const el = list.filter(n => n.offsetWidth || n.offsetHeight)[a.childNth || 0];   // 隐藏的先剔掉（display:none 的点了没反应）
    if (!el) return { ok: false, total: list.length };
    if (a.stripTarget) el.removeAttribute("target");  // 带 target=_blank 的点了会开新标签，撞 ego 8 页上限
    el.scrollIntoView({ block: "center", behavior: "instant" });
    const r = el.getBoundingClientRect();
    return { ok: true, total: list.length, tag: el.tagName, text: norm(el.innerText).slice(0, 30),
             x: r.left + r.width / 2, y: r.top + r.height / 2 };
  }, q);
}

async function cdpClick(page, x, y, opts) {
  // 每次 page.cdp() 都是一个公网级往返（实测单趟数百毫秒），所以**轨迹点数就是成本**：
  // trail=1 只发一次 mouseMoved（最省），trail=3 走三段位移更像真人。
  const holdMs = Number((opts && opts.holdMs) || 40);
  const trail = Math.max(1, Number((opts && opts.trail) || 3));
  const px = Math.round(x), py = Math.round(y);
  const t0 = Date.now();
  for (let i = 0; i < trail; i++) {
    const f = (i + 1) / trail;
    await page.cdp("Input.dispatchMouseEvent", {
      type: "mouseMoved", x: Math.round(px - 26 + 26 * f), y: Math.round(py - 20 + 20 * f),
    });
  }
  const movedMs = Date.now() - t0;
  const t1 = Date.now();
  await page.cdp("Input.dispatchMouseEvent", { type: "mousePressed", x: px, y: py, button: "left", clickCount: 1, buttons: 1 });
  await page.waitForTimeout(holdMs);
  await page.cdp("Input.dispatchMouseEvent", { type: "mouseReleased", x: px, y: py, button: "left", clickCount: 1, buttons: 0 });
  return { moves: trail, movedMs, pressMs: Date.now() - t1 };
}

async function handle(req) {
  const id = req && req.id;
  try {
    switch (req.cmd) {
      case "newPage": {
        // 工作页可能已被上一轮 close 关掉（workPage 仍指向它）——探针验活，死了就重开。
        // 2026-09-16 踩坑：close 后没清 workPage，下一次复用了已关闭的 p2 →
        // 运行时报 "unknown page label: p2"，整批详情页补全失败。
        if (workPage) {
          try {
            await workPage.url();          // 死引用在这里会抛
          } catch (e) {
            pages.delete(workPage.label);
            workPage = null;
          }
        }
        if (!workPage) {
          // 随用随关：这里只负责"要用时开一张"，用完由 close 立刻关（避开 8 页上限）
          workPage = await task.newPage();
          pages.set(workPage.label, workPage);
        }
        return { id, ok: true, result: { label: workPage.label, reused: true } };
      }
      case "goto": {
        const page = pageOf(req.page);
        // 主页面被驱动时也置前（例如同步脚本让主页面跑接口页）
        if (req.page && req.page !== "p1") { try { await page.bringToFront(); } catch (e) {} }
        await page.goto(req.url);
        await page.waitForLoadState("domcontentloaded");
        return { id, ok: true, result: { url: await page.url() } };
      }
      /* ── 真输入命令（2026-10-05 去 goto 化，底座见上方 locateForClick / cdpClick）──
         默认驱动**工作页**（与 screenshot 同口径：正在被驱动的那张，不是 p1）。 */
      case "click": {
        // selector 与 text 二选一：text 给「展开 / 查看对话 / 下一页」这类没稳定 class 的控件。
        // ⚠ 雪球的「展开」尾部带空格，所以 text 匹配走**包含 + 归一化空白**，别求全等。
        const page = pageOf(req.page || (workPage ? workPage.label : "p1"));
        if (!req.selector && !req.text) return { id, ok: false, error: "click 需要 selector 或 text" };
        const t0 = Date.now();
        const loc = await locateForClick(page, {
          selector: req.selector || null, text: req.text || null, scope: req.scope || null,
          within: req.within || null, nth: Number(req.nth || 0), childNth: Number(req.childNth || 0),
          exact: !!req.exact, stripTarget: !!req.stripTarget,
        });
        const locateMs = Date.now() - t0;
        if (!loc.ok) {
          return { id, ok: false,
                   error: "click 找不到：" + (req.selector ? req.selector + (req.within ? " ∈ " + req.within + "[" + req.nth + "]" : "")
                                                          : ("文案「" + req.text + "」")) +
                          "（候选 " + loc.total + " 个" + (loc.reason ? "，" + loc.reason : "") + "）" };
        }
        const clk = await cdpClick(page, loc.x, loc.y, { holdMs: req.holdMs, trail: req.trail });
        if (req.settleMs) await page.waitForTimeout(Number(req.settleMs));
        // 回报每段耗时：一次真点击的净成本几乎全是 CDP 往返数，采集器估时按这个来
        return { id, ok: true,
                 result: { at: [Math.round(loc.x), Math.round(loc.y)], tag: loc.tag,
                           text: loc.text, candidates: loc.total,
                           ms: Object.assign({ locate: locateMs }, clk), url: await page.url() } };
      }
      case "wheel": {
        // 真滚轮：JS 的 scrollIntoView 是瞬移、不产生 wheel 事件，
        // 懒挂载节奏和真人差很远（也是采集器「逐条滚动」现在的形态）。
        const page = pageOf(req.page || (workPage ? workPage.label : "p1"));
        if (req.x != null && req.y != null) await page.mouse.move(Number(req.x), Number(req.y));
        const dy = Number(req.dy || 0);
        await page.mouse.wheel(Number(req.dx || 0), dy);
        if (req.waitForMs) await page.waitForTimeout(Number(req.waitForMs));
        return { id, ok: true, result: { dy: dy, url: await page.url() } };
      }
      case "back": {
        // 真返回：走浏览器历史栈（等价于点左上角那个箭头），不是 evaluate('history.back()')。
        // 顺带回报栈深，采集器据此判断「回到哪了」。
        const page = pageOf(req.page || (workPage ? workPage.label : "p1"));
        const from = await page.url();
        const hist = await page.cdp("Page.getNavigationHistory", {});
        const i = hist.currentIndex;
        if (i <= 0) {
          return { id, ok: false, error: "历史栈到头了（currentIndex=" + i + "，深 " + hist.entries.length + "），没有可返回的页" };
        }
        const target = hist.entries[i - 1];
        await page.cdp("Page.navigateToHistoryEntry", { entryId: target.id });
        try { await page.waitForLoadState("domcontentloaded"); } catch (e) { /* 同文档/已就绪 */ }
        if (req.settleMs) await page.waitForTimeout(Number(req.settleMs));
        const to = await page.url();
        const after = await page.cdp("Page.getNavigationHistory", {});
        return { id, ok: true,
                 result: { from: from, to: to, moved: from !== to,
                           index: after.currentIndex, depth: after.entries.length,
                           targetUrl: target.url } };
      }
      case "evaluate": {
        const page = pageOf(req.page);
        const raw = String(req.fn || "").trim().replace(/;\s*$/, "");
        const looksLikeFn = /^(async\s*)?(function\b|\()/.test(raw) || /=>/.test(raw);
        const fn = eval("(" + (looksLikeFn ? raw : "async (a) => (" + raw + ")") + ")");
        const result = await page.evaluate(fn, req.arg);
        return { id, ok: true, result: result === undefined ? null : result };
      }
      case "waitForSelector": {
        await pageOf(req.page).waitForSelector(req.selector, {
          timeout: req.timeoutMs || 8000,
        });
        return { id, ok: true, result: true };
      }
      case "url": {
        return { id, ok: true, result: await pageOf(req.page).url() };
      }
      case "pageApi": {
        // 调试用（2026-10-05 加）：ego 的 page 对象**不是** Playwright Page
        // （实测没有 locator / goBack，click 的 options 走 validatePublicApiOptions 严格校验，
        //  cdp 有方法白名单）。写真输入命令前先跑它拿现状，别照 Playwright 文档猜。
        const page = pageOf(req.page || (workPage ? workPage.label : "p1"));
        const out = { ctor: (page.constructor && page.constructor.name) || "?", fns: [], props: [], probe: {} };
        const names = new Set();
        let o = page;
        while (o && o !== Object.prototype) {
          for (const k of Object.getOwnPropertyNames(o)) names.add(k);
          o = Object.getPrototypeOf(o);
        }
        for (const k of names) {
          try {
            if (typeof page[k] === "function") out.fns.push(k);
            else if (page[k] != null) out.props.push(k + ":" + typeof page[k]);
          } catch (e) { out.props.push(k + ":<throws>"); }   // getter 会抛的也记下来
        }
        out.fns.sort(); out.props.sort();
        if (req.probe) {
          const t = async (name, fn) => {
            try { const r = await fn(); out.probe[name] = { ok: true, r: r === undefined ? null : r }; }
            catch (e) { out.probe[name] = { err: String(e && e.message || e).slice(0, 300) }; }
          };
          // 选项校验：故意传一个不存在的 key，报错文本里通常带允许列表
          await t("clickBadOption", () => page.click("a[href='/']", { zzzNope: 1 }));
          await t("cdpHistory", () => page.cdp("Page.getNavigationHistory", {}));
          await t("cdpDispatchMouse", () => page.cdp("Input.dispatchMouseEvent",
            { type: "mouseMoved", x: 5, y: 5 }));
          await t("mouseWheel0", () => page.mouse.wheel(0, 0));
          await t("keyboardProps", async () => {
            const ks = [];
            let ko = page.keyboard;
            while (ko && ko !== Object.prototype) {
              for (const k of Object.getOwnPropertyNames(ko)) if (typeof ko[k] === "function") ks.push(k);
              ko = Object.getPrototypeOf(ko);
            }
            return { ctor: (page.keyboard && page.keyboard.constructor && page.keyboard.constructor.name) || "?",
                     fns: [...new Set(ks)].sort() };
          });
        }
        return { id, ok: true, result: out };
      }
      case "close": {
        const label = req.page || "p1";
        const page = pageOf(label);
        if (label === "p1" || req.force) {
          // 主页面是用户的页面，桥不关；显式 force 才真关
          if (label === "p1") return { id, ok: true, result: { kept: true, label } };
          pages.delete(label);
          await page.close();
          return { id, ok: true, result: { closed: true, label } };
        }
        // 用完放回：页签由桥在退出时统一关（会话内复用同一张，不再新开）
        // 2026-09-16 用户口径：「标签最好是随用随关」——所以放回时**立刻真关**，
        // 下次要用再开一张（工作页一进一出，ego 里不留悬挂标签）。
        if (label !== "p1") {
          try { await page.close(); } catch (e) {}
          pages.delete(label);
          if (workPage && workPage.label === label) workPage = null;   // 关掉的是工作页 → 清引用
          return { id, ok: true, result: { closed: true, label } };
        }
        return { id, ok: true, result: { released: true, label } };
      }
      case "screenshot": {
        // 未指定页时拍**工作页**（正在被驱动的页），而不是主页面——2026-09-16 实测：
        // 主页面停在某个不动的 URL 上，进度截图拍它会拍到"上一次的样子"。
        const target = pageOf(req.page || (workPage ? workPage.label : "p1"));
        await target.screenshot({ path: req.path });
        return { id, ok: true, result: { path: req.path, page: req.page || (workPage ? workPage.label : "p1") } };
      }
      case "text": {
        // 页面可见文本：脚本侧要"看"页面内容时用（如关注列表接口页的 JSON 文本）
        const text = await pageOf(req.page).evaluate(() => document.body.innerText || "");
        return { id, ok: true, result: text };
      }
      case "cookies": {
        const jar = await pageOf(req.page).evaluate(() => document.cookie || "");
        return { id, ok: true, result: jar };
      }
      case "handoff": {
        // 把任务空间交给用户（滑块/验证要人工过），然后**盯着控制权**：
        // 用户过完验证、控制权回到 agent → 回执 ok，采集方据此重试本页。
        const waitMs = Number(req.waitMs || 900000);
        handedOff = true;
        await task.handOff();
        const deadline = Date.now() + waitMs;
        while (Date.now() < deadline) {
          try {
            await task.waitForControl({ timeout: 5000, interval: 1000 });
            // 控制权回来了：显式认领一次，避免"控制权已回但 space 仍标记为用户所有"
            let reclaimed = false;
            try {
              const again = await claimTaskSpace(task.spaceId);
              reclaimed = true;
              task = again;
            } catch (e) { /* 已经在 agent 名下时 claim 会失败，属正常 */ }
            handedOff = false;      // 控制权已收回，恢复正常回收行为
            return {
              id, ok: true,
              result: { regained: true, reclaimed,
                        waitedMs: waitMs - (deadline - Date.now()) },
            };
          } catch (e) {
            // waitForControl 超时（用户还没好，或**仍由用户持有**）：继续等
          }
        }
        return { id, ok: false,
                 error: "handoff 超时：用户未在 " + Math.round(waitMs / 1000) + " 秒内交还控制权" };
      }
      case "shutdown": {
        // 用完回收：关页签 + 释放空间（用户 2026-09-16 要求）
        await cleanupOwnPages();
        setTimeout(() => process.exit(0), 50);
        return { id, ok: true, result: { cleaned: true } };
      }
      default:
        return { id, ok: false, error: "unknown cmd: " + (req && req.cmd) };
    }
  } catch (e) {
    const detail = String((e && e.stack) || (e && e.message) || e).slice(0, 900);
    process.stderr.write("[bridge] " + req.cmd + " failed: " + detail + "\n");
    return { id, ok: false, error: String((e && e.message) || e).slice(0, 600) };
  }
}

async function main() {
  if (!SOCK) {
    process.stderr.write("XUEQIU_EGO_SOCK 未设置\n");
    process.exit(2);
  }
  await boot();

  const conn = net.connect(SOCK);
  conn.setEncoding("utf8");
  conn.on("connect", () => {
    conn.write(JSON.stringify({ hello: true, pid: process.pid, space: task.spaceId }) + "\n");
  });
  conn.on("error", (e) => {
    process.stderr.write("socket error: " + String(e).slice(0, 200) + "\n");
    process.exit(2);
  });

  let buffer = "";
  let queue = Promise.resolve();
  conn.on("data", (chunk) => {
    buffer += chunk;
    let idx;
    while ((idx = buffer.indexOf("\n")) >= 0) {
      const line = buffer.slice(0, idx).trim();
      buffer = buffer.slice(idx + 1);
      if (!line) continue;
      let req = null;
      try {
        req = JSON.parse(line);
      } catch (e) {
        conn.write(JSON.stringify({ id: null, ok: false, error: "bad json" }) + "\n");
        continue;
      }
      // 串行执行，保证同一页上的动作不会互相插队
      queue = queue.then(async () => {
        const resp = await handle(req);
        if (conn.writable) conn.write(JSON.stringify(resp) + "\n");
      });
    }
  });
}

main().catch((e) => {
  process.stderr.write("bridge fatal: " + String((e && e.message) || e).slice(0, 300) + "\n");
  process.exit(1);
});
