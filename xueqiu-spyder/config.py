import os
import random

# API endpoints
SEARCH_STATUS_URL = "https://xueqiu.com/query/v1/symbol/search/status.json"
# v4 路径被 WAF 405 封禁时，可经 XUEQIU_TIMELINE_URL 切到旧版路径
# （旧版数据结构一致 {"count":N,"statuses":[...]}，但 count 上限 20）
USER_TIMELINE_URL = os.environ.get(
    "XUEQIU_TIMELINE_URL", "https://xueqiu.com/v4/statuses/user_timeline.json"
)
# 自动降级目标：v4 路径 405/非 JSON 时 crawler 自动切到此端点重试（无需人工干预）
USER_TIMELINE_URL_FALLBACK = os.environ.get(
    "XUEQIU_TIMELINE_URL_FALLBACK", "https://xueqiu.com/statuses/user_timeline.json"
)
# 旧版路径每页上限 20（v4 支持 50），降级后自动下调
FALLBACK_POSTS_COUNT = 20

# Crawling parameters
MIN_REPLY_COUNT = 20
MAX_PAGES = 20
POSTS_PER_PAGE = 50
# 用户页翻页每页条数：默认 20（v4 与旧版路径都支持；旧版上限即 20）
USER_POSTS_COUNT = int(os.environ.get("XUEQIU_POSTS_COUNT", "20"))

# Rate limiting
REQUEST_DELAY = float(os.environ.get("XUEQIU_REQUEST_DELAY", "1.0"))
MAX_RETRIES = 3

# 翻页间隔（user 模式逐博主主页时间线 / 热门 tab）：XUEQIU_PAGE_DELAY_RANGE="min,max"
# 随机档，拉长防风控（缺省 1.0,1.0＝沿用 REQUEST_DELAY 的旧行为）。
# 只作用于翻页循环——内层每次请求仍按 REQUEST_DELAY，避免间隔叠乘把整轮拖垮。
_pg = os.environ.get("XUEQIU_PAGE_DELAY_RANGE", "1.0,1.0").split(",")
PAGE_DELAY_RANGE = (float(_pg[0].strip()),
                   float(_pg[1].strip()) if len(_pg) > 1 else float(_pg[0].strip()))


def page_delay():
    """下一页前的间隔（秒），在 PAGE_DELAY_RANGE 档内随机取值。"""
    lo, hi = PAGE_DELAY_RANGE
    return random.uniform(lo, max(lo, hi))

# 详情页逐帖补全的节流（2026-10-01 对齐 references/execution-guide.md「单帖接口限流」硬约束：
# 安全速率 sleep ≥1.2s + 每 50 次停 45s ≈0.7 req/s；实测 1.1 req/s 连约 200 次即 405）。
# 原先这里沿用 REQUEST_DELAY=1.0s 且无长歇，实测 16 次跳转压在 ~21 次/分钟上直接弹滑块。
DETAIL_PACE = tuple(float(x) for x in
                    os.environ.get("XUEQIU_DETAIL_PACE", "1.2,1.9").split(","))
DETAIL_BREAK_N = int(os.environ.get("XUEQIU_DETAIL_BREAK_N", "50"))     # 每多少次详情页长歇一次
DETAIL_BREAK_S = float(os.environ.get("XUEQIU_DETAIL_BREAK_S", "45"))   # 长歇秒数（文档口径 45s）


# Output
DEFAULT_OUTPUT_DIR = "./output"

# ── 浏览器通道：**只有 ego lite**（2026-09-16 用户拍板）────────────────────
# 用户原话：「采集帖子不要用 chrome 浏览器，记得把有关的都去掉，全部改用 ego lite」。
# 曾经存在的 Chrome CDP 通道（含 auto 回落）已于 2026-09-16 从代码里整段删除：
# 它曾在 ego 桥超时时静默拉起过 Google Chrome。现在只有 ego 一条路——
# `XUEQIU_TRANSPORT` 留作显式开关，取值不是 `ego` 直接报错。
BROWSER_TRANSPORT = os.environ.get("XUEQIU_TRANSPORT", "ego").strip().lower()
# 桥接脚本：同目录的 ego_bridge.js，可用环境变量指到别处
EGO_BRIDGE_JS = os.environ.get(
    "XUEQIU_EGO_BRIDGE",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "ego_bridge.js"),
)
# 复用的 ego 任务空间 id（同一批采集沿用；空=让桥按名字找/新建）
EGO_SPACE = os.environ.get("XUEQIU_EGO_SPACE", "")
# 注：空间名由 ego_browser._bake_config 直接读 XUEQIU_EGO_SPACE_NAME，不在此定义
# ego 桥启动握手超时（秒）
EGO_BOOT_TIMEOUT = float(os.environ.get("XUEQIU_EGO_BOOT_TIMEOUT", "90"))

# ── 现场可见性（2026-09-16 用户要求）──────────────────────────────────
# 用户原话：「采集博主言论的时候，我需要 ego lite 的页面在前端，我才能知道有没有触发风控」。
# EGO_WAKE：是否在采集开始/结束时把 ego lite 窗口拉到前台。
# **2026-09-16 用户口径：默认不抢焦点**（「不要让 ego lite 一直跳到我前面」）；
# 只有命中滑块需要用户处理时才激活（见 crawler._handle_slider，那条不受此开关控制）。
EGO_WAKE = os.environ.get("XUEQIU_EGO_WAKE", "0") not in ("0", "false", "no")
# EGO_SHOT_DIR：命中风控/异常时自动落图留证的目录（空＝不落图）
EGO_SHOT_DIR = os.environ.get(
    "XUEQIU_EGO_SHOT_DIR",
    os.path.join(os.path.expanduser("~"), ".cache", "xueqiu-spyder", "shots"),
)
# 详情页补全过程中每隔 N 条落一张图（0＝关；默认每 5 条，便于回看进度与风控弹窗）
EGO_SHOT_EVERY = int(os.environ.get("XUEQIU_EGO_SHOT_EVERY", "5"))
# 命中滑块后把 ego 交给用户接管、等用户过完验证的最长时间（毫秒；默认 15 分钟）
# 用户要求：「如果遇到了滑块，记得把 ego lite 让我接管」
EGO_SLIDER_WAIT_MS = int(os.environ.get("XUEQIU_EGO_SLIDER_WAIT_MS", str(15 * 60 * 1000)))
