"""ego lite 通道（2026-09-15 用户拍板：spyder 从 Chrome CDP 迁到 ego lite）。

为什么长这样：ego lite **不对外暴露 CDP 端口**（没有 `--remote-debugging-port` 这类语义），
它的 CDP 能力只经 `ego-browser nodejs` 的 Node 运行时暴露（`page.cdp()` / `page.evaluate()`）。
所以这里起一个 unix socket 服务端、把 `ego_bridge.js` 用 `-e` 交给 `ego-browser nodejs`
执行，桥连回来，双方用 JSON Lines 通信；登录态与反爬特征全部由 ego 进程承载，
本模块不启动任何浏览器。

**通道为什么是 socket（2026-09-15 实测，三个坑都踩过）**：`ego-browser nodejs` 对
stdin 的三种形态都不好用——① 脚本文本走 stdin：CLI 要等 EOF 才执行；
② `-e` 传脚本但 stdin 是管道：CLI 仍把管道当「脚本待读」而挂住；
③ stdin 给伪终端：脚本执行完进程立即退出。只有 stdin=/dev/null 时脚本立刻执行，
但那也意味着不能用 stdin 传协议 —— 所以协议另开 socket。

对外只暴露两个东西（2026-09-16 清理掉历史遗留的 playwright 形状适配层）：
    EgoBridge            —— 桥本身：start() / stop() / call(cmd, ...) / new_page() / main_page
    Page                 —— 一张 ego 标签页：goto() / evaluate() / wait_for_selector() /
                            wait_for_timeout() / text() / cookies() / screenshot() / url / close()
方法名沿用 early crawler 的旧称（goto/evaluate/...），但**与 playwright 无关**——
它们只是对 ego 桥协议（JSON Lines）的封装。
"""

import json
import os
import select
import shutil
import socket
import subprocess
import tempfile
import time

import config


class BridgeError(RuntimeError):
    pass


def ego_focused(app_name="ego lite"):
    """ego lite 现在是不是前台应用（macOS 走 System Events；非 macOS / 无权限返回 None）"""
    import subprocess
    import sys as _sys
    if _sys.platform != "darwin":
        return None
    try:
        r = subprocess.run(
            ["osascript", "-e",
             'tell application "System Events" to get name of first process whose frontmost is true'],
            capture_output=True, text=True, timeout=5)
        front = (r.stdout or "").strip()
        return front == app_name if front else None
    except Exception:
        return None


def activate_ego(app_name="ego lite"):
    """把 ego lite 拉到前台（**只在滑块交接时调用**——用户要求：平时别抢焦点）。

    ego 没开着时 `activate` 无效，退回用 `open -a` 把 App 拉起来（滑块必须让用户看见）。
    """
    import subprocess
    import sys as _sys
    if _sys.platform != "darwin":
        return
    for cmd in (
        ["osascript", "-e", f'tell application "{app_name}" to activate'],
        ["open", "-a", app_name],          # App 没在跑时拉起它
    ):
        try:
            subprocess.run(cmd, capture_output=True, timeout=8)
        except Exception:
            continue


# 这些错误说明**任务空间已经不属于 agent**（用户接管 / 空间结束），重试无意义：
#   "The user has taken control of this task space and ended the task…"
#   "Task space not found."
# 2026-09-16 实测：用户在采集途中点了一下浏览器，后续每条详情页都白撞一遍（32 条刷屏）。
_FATAL_PATTERNS = (
    "taken control",
    "not assigned to the agent",
    "Task space not found",
    "browser commands are paused",
    "hard stop",
)


def _is_fatal(message):
    low = str(message or "").lower()
    return any(p.lower() in low for p in _FATAL_PATTERNS)


def _readline_sock(sock, buf, timeout):
    """从 socket 读一行（带超时，自己按 \\n 切）；返回 (line, 新缓冲)。

    **buf 是 bytearray（bytes），只在拿到完整一行之后才 decode**。按 recv 块逐块
    `chunk.decode("utf-8", "replace")` 会把跨块边界的多字节字符吃掉：一个汉字 3 字节，
    被劈开后落单的字节各自非法 → 每个坏字节换 1 个 U+FFFD。2026-09-28 实测
    post_history 有 50 行正文因此带伤（雪球原文「A股交易」入库成「A股\uFFFD\uFFFD易」），
    且长帖的坏点全挤在字节 ~8060——macOS AF_UNIX 默认缓冲 8KB，recv 一次就那么多；
    短帖坏点随机，因为 recv 返回多少本来就不定。整行 decode 不存在这个问题。
    """
    end = time.time() + timeout
    nl = b"\n"
    while nl not in buf:
        remaining = end - time.time()
        if remaining <= 0:
            return None, buf
        ready, _, _ = select.select([sock], [], [], min(0.5, remaining))
        if not ready:
            continue
        chunk = sock.recv(65536)
        if not chunk:
            raise BridgeError("ego 桥连接已断开")
        buf += chunk
    line, _, rest = buf.partition(nl)
    return line.decode("utf-8", "replace"), rest


def _kill_process_group(proc):
    """杀掉子进程及其整个进程组（ego CLI 是包装进程，普通 kill 杀不干净）"""
    import signal
    if not proc or proc.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


class EgoBridge:
    """一个进程 = 一个 ego 任务空间 = 一次采集会话"""

    def __init__(self, cli=None, space=None, boot_timeout=None):
        self.cli = cli or shutil.which("ego-browser") or ""
        self.space = space if space is not None else config.EGO_SPACE
        self.boot_timeout = boot_timeout or config.EGO_BOOT_TIMEOUT
        self._seq = 0
        self.space = self.space or None
        self._is_first_attempt = True
        self._space = self.space
        self._srv = None
        self._conn = None
        self._buf = bytearray()
        self._log_fh = None
        self._sock_path = None
        self.proc = None
        self.main_page = None

    # ── 生命周期 ──────────────────────────────────────────────
    def start(self):
        """启动桥。**两段式**：先试原任务空间，若它已不可用（被交接成"用户所有"、
        或用户接管后命令被 pause），**换新空间重试**——避免整轮采集被一个失效空间卡死。

        2026-09-16 实测：滑块交接后用户没交还，该空间对新连接变成"永远等"，90s 超时。
        2026-09-24 补：空间已被接管时不一定报"超时"——后续调用会直接回
        `task.listTabs: The user has taken control…`（hard stop）。原先只认"超时"，
        导致同一轮内第二次调用直接抛错退出（当天 i知否 两次失败即此因）；
        现按 `_is_fatal()` 判定：**任何「空间不属于 agent」类错误都换新空间续跑**。
        """
        try:
            return self._start_once(self.space)
        except BridgeError as e:
            if "超时" not in str(e) and not _is_fatal(str(e)):
                raise
            fresh = "xueqiu-spyder-" + time.strftime("%H%M%S")
            self._is_first_attempt = False
            self._teardown()
            print(f"⚠️ 原任务空间不可用（{e}）—— 换用新空间 {fresh} 重试", flush=True)
            return self._start_once(fresh)

    def _start_once(self, space):
        if not self.cli:
            raise BridgeError(
                "未找到 ego-browser CLI —— 请先装好 ego lite 的命令行工具"
                "（用户 2026-09-15 已要求不再用 Chrome）"
            )
        if not os.path.exists(config.EGO_BRIDGE_JS):
            raise BridgeError(f"桥接脚本不存在: {config.EGO_BRIDGE_JS}")

        with open(config.EGO_BRIDGE_JS, "r", encoding="utf-8") as fh:
            script = fh.read()

        self._space = space
        self._sock_path = os.path.join(
            tempfile.mkdtemp(prefix="xueqiu-ego-"), "bridge.sock")
        self._srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._srv.bind(self._sock_path)
        self._srv.listen(1)
        # 第一段等待给短一点（20s）：空间被用户持有时，连接会一直挂着，
        # 早失败早换空间；第二段（新空间）用完整 boot_timeout。
        wait_budget = 20 if self._is_first_attempt else self.boot_timeout
        self._srv.settimeout(wait_budget)

        # 注意顺序：先定 socket 路径，再注入配置（配置里带 socket 路径）
        script = self._bake_config(script)

        env = dict(os.environ)

        # stdin=/dev/null 是唯一能让 `-e` 脚本立刻执行的形态（见模块头注释）
        self.proc = subprocess.Popen(
            [self.cli, "nodejs", "-e", script],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            start_new_session=True,     # 独立进程组：超时时能整组杀掉，不留残留
        )
        self._spawn_stderr_drain()

        try:
            conn, _ = self._srv.accept()
        except socket.timeout:
            self._teardown()
            raise BridgeError(
                f"ego 桥启动超时（{wait_budget:g}s）—— "
                f"确认 ego lite 已打开并已登录雪球"
                + (f"；若是因为任务空间被交接给用户，会自动改用新空间" if self._is_first_attempt else ""))
        conn.settimeout(None)
        self._conn = conn
        self._buf = bytearray()

        line, self._buf = _readline_sock(self._conn, self._buf, min(30, self.boot_timeout))
        if line is None:
            self.stop()
            raise BridgeError("ego 桥握手超时（连上了但没发 hello）")
        try:
            hello = json.loads(line)
        except ValueError:
            self.stop()
            raise BridgeError(f"ego 桥握手失败，首行不是 JSON: {line[:120]}")
        if not hello.get("hello"):
            self.stop()
            raise BridgeError(f"ego 桥启动失败: {hello.get('error', 'unknown')}")

        self.main_page = Page(self, "p1")
        # 空间可用性探活（2026-09-24）：桥握手成功 ≠ 空间属于 agent——若原空间已被用户
        # 接管，握手照样通过，直到第一条真命令才报 `The user has taken control…`。
        # 在这里主动探一次（用最轻的 url 命令），让 start() 的两段式重试能真正生效。
        try:
            _ = self.main_page.url          # property：读主页面 URL 即探活
        except BridgeError as e:
            self.stop()
            raise e
        return hello

    def _bake_config(self, script):
        """配置注入：ego 的 Node 运行时拿不到父进程环境变量（实测），
        所以把 socket 路径等直接拼进脚本头部，由桥读 `__EGO_CFG`。
        注意：shebang 必须留在**第 1 行**（否则 VM 报 SyntaxError），故先摘后拼。"""
        cfg = {
            "sock": self._sock_path,
            "space": (self._space or "").strip() or None,
            "spaceName": os.environ.get("XUEQIU_EGO_SPACE_NAME", "xueqiu-spyder"),
            "url": os.environ.get("XUEQIU_EGO_URL", "https://xueqiu.com/"),
            # 会话内只维护一张工作页（用完放回、下次复用），退出时由桥统一关
        }
        shebang = ""
        if script.startswith("#!"):
            first_nl = script.find("\n")
            if first_nl > 0:
                shebang = script[:first_nl + 1]
                script = script[first_nl + 1:]
        header = "const __EGO_CFG = " + json.dumps(cfg, ensure_ascii=False) + ";\n"
        return shebang + header + script

    def _spawn_stderr_drain(self):
        """把桥的 stderr（致命错误/杂音）排到日志文件或 devnull，避免管道塞满卡死"""
        import threading

        def drain():
            try:
                for line in self.proc.stderr:
                    if self._log_fh:
                        self._log_fh.write(line)
                        self._log_fh.flush()
            except Exception:
                pass

        log_path = os.environ.get("XUEQIU_EGO_LOG", "")
        if log_path:
            try:
                self._log_fh = open(log_path, "a", encoding="utf-8")
            except OSError:
                self._log_fh = None
        threading.Thread(target=drain, daemon=True).start()

    def stop(self):
        """退出桥：请它自己关（保住 ego 的标签页），超时再杀"""
        try:
            if self._conn:
                self._conn.sendall((json.dumps({"id": -1, "cmd": "shutdown"}) + "\n").encode("utf-8"))
        except Exception:
            pass
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.wait(timeout=5)
            except Exception:
                _kill_process_group(self.proc)
        self.proc = None
        for closeable in (self._conn, self._srv):
            try:
                if closeable:
                    closeable.close()
            except Exception:
                pass
        self._conn = self._srv = None
        self._buf = bytearray()
        if self._log_fh:
            try:
                self._log_fh.close()
            except Exception:
                pass
            self._log_fh = None

    def _teardown(self):
        """强制清理一个失败的尝试（进程组 + socket + 状态位），供两段式启动复用"""
        _kill_process_group(self.proc)
        self.proc = None
        for closeable in (self._conn, self._srv):
            try:
                if closeable:
                    closeable.close()
            except Exception:
                pass
        self._conn = self._srv = None
        self._buf = bytearray()
        if self._log_fh:
            try:
                self._log_fh.close()
            except Exception:
                pass
            self._log_fh = None

    def new_page(self):
        """新开一张 ego 标签页（用完记得 close——用户口径：标签随用随关）"""
        res = self.call("newPage", timeout=60)
        return Page(self, (res or {}).get("label") or "p1")

    def tab_page(self, label):
        """把 `Page.click_tab()` 接住的标签包成可操作的 Page（桥已登记该 label，
        可直接 evaluate/close）。别拿它开新页——开新页用 new_page()。"""
        return Page(self, label)

    # ── 请求 ──────────────────────────────────────────────────
    def handoff(self, wait_ms=900000):
        """把任务空间交给用户（滑块/验证要人工过），阻塞等待用户交还控制权。

        ego 的硬约束：用户一旦接管，agent 侧所有命令都会被暂停——所以这里不是
        可选项，是**唯一正确的姿势**：交接 → 等 → 拿回控制权 → 采集方重试本页。
        返回 True=控制权已拿回；False/异常=超时或失败（调用方按 WAF 处理）。
        """
        # 用户可能不在屏幕前：交接即 Bark（2026-10-05 用户要求「遇到需要我过滑块的情况 Bark 通知」）
        try:
            import subprocess
            subprocess.Popen(
                ["python3", "/Users/jianglb/Project/investment-dashboard/.agents/skills/bark/scripts/notify.py",
                 "--group", "investment-dashboard", "--level", "timeSensitive", "--id", "xueqiu-slider",
                 "雪球滑块验证", "需要你在 ego lite 过一下滑块（等 15 分钟），过完自动续跑"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass
        res = self.call("handoff", timeout=wait_ms / 1000 + 30, waitMs=wait_ms)
        return bool(res and res.get("regained"))

    def call(self, cmd, timeout=90, **payload):
        if not self._conn:
            raise BridgeError("ego 桥未运行")
        self._seq += 1
        req = {"id": self._seq, "cmd": cmd}
        req.update({k: v for k, v in payload.items() if v is not None})
        if cmd == "evaluate":
            # 显式带回 arg（None → null）：ego 的 page.evaluate 对「没有第二个参数」
            # 会报 must be JSON-serializable，必须传 null（2026-09-15 实测）
            req["arg"] = payload.get("arg")
        self._conn.sendall((json.dumps(req, ensure_ascii=False) + "\n").encode("utf-8"))

        deadline = time.time() + timeout
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                raise BridgeError(f"ego 请求超时（{cmd}，{timeout:g}s）")
            line, self._buf = _readline_sock(self._conn, self._buf, remaining)
            if line is None:
                raise BridgeError(f"ego 请求超时（{cmd}，{timeout:g}s）")
            line = line.strip()
            if not line:
                continue
            try:
                resp = json.loads(line)
            except ValueError:
                continue
            if resp.get("id") != self._seq:
                continue  # 迟到的旧响应：丢弃
            if resp.get("ok"):
                return resp.get("result")
            err = resp.get("error") or f"{cmd} 失败"
            if _is_fatal(err):
                # 用户接管 = 正常保护（多发生在过滑块时）：**当轮就此停下，不夺回控制权**，
                # 本博主按失败处理、cutoff 不推进，下一轮采集会换新空间续跑。
                # 别在这里自动换空间硬续——那会跟用户正在操作的页面抢控制权。
                raise BridgeError(
                    f"任务空间已不属于 agent（用户接管或空间已结束），{cmd} 停止：{err[:200]}"
                )
            raise BridgeError(err)


class Page:
    """一张 ego 标签页（ego 里真实的 tab；label 由桥分配，p1 = 主页面）"""

    def __init__(self, bridge, label):
        self._bridge = bridge
        self.label = label

    def goto(self, url, wait_until=None, timeout=None):
        budget = max(30, int((timeout or 15000) / 1000) + 20)
        return self._bridge.call("goto", timeout=budget, page=self.label, url=url)

    # ── 真输入（2026-10-05 去 goto 化）：走 CDP Input 域，事件 isTrusted=true。
    #    evaluate 里的 el.click() 是 JS 合成事件（无 mousemove/hover/pointer 序列），
    #    拿它替 goto 只是换一种假动作，别混用。
    #    ⚠ ego 的 Page 不是 Playwright Page（没有 locator / goBack），实现细节在桥里。
    def click(self, selector=None, text=None, scope=None, within=None, nth=0, child_nth=0,
              strip_target=None, settle_ms=None, trail=None, timeout=40):
        """真鼠标点击（CDP Input 域，事件 isTrusted=true）。
        `selector` 与 `text` 二选一：text 给「展开 / 查看对话 / 下一页」这类没有稳定
        class 的控件（按可见文案找，取文案最短的那个元素）。
        `within` + `nth`：**先按 nth 选中行**（如 `.profiles__user`），再在行内找 selector
        ——不用它的话 nth 按全页计数，与行索引不对齐（实测会点到别人头上）。
        回执 `ms` 是分段时间（locate / moved / press）；一次点击的净成本≈CDP 往返数，
        `trail` 是轨迹点数（默认 3，越大越像真人、越慢）。"""
        if not selector and not text:
            raise BridgeError("click 需要 selector 或 text")
        return self._bridge.call("click", timeout=timeout, page=self.label,
                                 selector=selector, text=text, scope=scope, within=within,
                                 nth=nth, childNth=child_nth, stripTarget=strip_target,
                                 settleMs=settle_ms, trail=trail)

    def wheel(self, dy=0, dx=0, at=None, wait_ms=None):
        """真滚轮。`at=(x, y)` 先把鼠标移到该处再滚（滚轮落在指针下的滚动容器里）。
        别再用 scrollIntoView 替它——那是瞬移、不产生 wheel 事件。"""
        x, y = at if at else (None, None)
        return self._bridge.call("wheel", timeout=30, page=self.label,
                                 dx=dx, dy=dy, x=x, y=y, waitForMs=wait_ms)

    def back(self, settle_ms=None, timeout=40):
        """真返回（走浏览器历史栈，等价于点左上角箭头；不是 evaluate('history.back()')）。
        回执 {from, to, moved, index, depth, targetUrl}；到头了会抛 BridgeError。
        ⚠ 别拿它做「进详情→回时间线」的循环：10-05 实测深页 back 掉页率 5/5
        （第 3 页进详情，back 回来落在第 1 页），那种场景改用 click_tab() 开新标签、抓完关。"""
        return self._bridge.call("back", timeout=timeout, page=self.label,
                                 settleMs=settle_ms)

    def click_tab(self, selector=None, text=None, scope=None, within=None, nth=0,
                  settle_ms=None, wait_ms=None, trail=None, timeout=60):
        """真点击 `target=_blank` 的链接，把开出来的**新标签**接住。
        回执 `{label, url, at, text}`；用 `self._bridge.tab_page(label)` 变成可操作的 Page，
        在它上面 evaluate 抓数据，**用完必须 .close()**（ego 一个空间只有 8 张页上限）。
        主页面全程不动 —— 这才是替代「点进详情 + back」的正确形态（见 back 的警告）。"""
        if not selector and not text:
            raise BridgeError("click_tab 需要 selector 或 text")
        return self._bridge.call("clickTab", timeout=timeout, page=self.label,
                                 selector=selector, text=text, scope=scope, within=within,
                                 nth=nth, settleMs=settle_ms, waitMs=wait_ms, trail=trail)

    def evaluate(self, fn_or_expr, arg=None):
        # arg 必须显式给（不能是「缺字段」）：ego 的 page.evaluate 对「没有第二个
        # 参数」和「第二个参数为 null」处理不同，缺字段会报
        # "page.evaluate argument must be JSON-serializable"（2026-09-15 实测）
        return self._bridge.call("evaluate", timeout=90, page=self.label,
                                 fn=fn_or_expr, arg=arg if arg is not None else None)

    def wait_for_selector(self, selector, timeout=8000):
        return self._bridge.call("waitForSelector", timeout=timeout / 1000 + 20,
                                 page=self.label, selector=selector, timeoutMs=timeout)

    def wait_for_timeout(self, ms):
        time.sleep(ms / 1000.0)

    def screenshot(self, path, timeout=60):
        """把该页当前画面存成 PNG（风控留证用；ego 页面本来就是可见的）"""
        return self._bridge.call("screenshot", timeout=timeout, page=self.label, path=path)

    def text(self):
        """页面可见文本（`document.body.innerText`）"""
        return self._bridge.call("text", timeout=60, page=self.label) or ""

    def cookies(self):
        """该会话的 Cookie 串（供 requests 复用同一登录态；拿不到就返回空串）"""
        return self._bridge.call("cookies", timeout=30, page=self.label) or ""

    @property
    def url(self):
        return self._bridge.call("url", timeout=30, page=self.label) or ""

    def close(self):
        try:
            self._bridge.call("close", timeout=30, page=self.label)
        except BridgeError:
            pass
