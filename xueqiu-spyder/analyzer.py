import re
import logging
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class UserInfo:
    user_id: int
    screen_name: str
    followers_count: int
    post_ids: list = field(default_factory=list)


@dataclass
class Opinion:
    post_id: int
    text: str
    reply_count: int
    like_count: int
    retweet_count: int = 0
    created_at: str = ""          # 展示用：YYYY-MM-DD HH:MM
    created_ts: int = 0           # 原始毫秒时间戳（窗口过滤/排序用）
    is_pinned: bool = False       # 置顶帖
    from_hot: bool = False        # 来自热门 tab（新博主首采；置顶帖经此路径保留进正文）
    completeness: str = "全文"    # 全文 / 摘要
    form: str = "短文"            # 形态：回复 / 短文 / 长文 / 专栏
    title: str = ""
    post_url: str = ""
    images: list = field(default_factory=list)   # [{url,seq,para}] 配图，只进元数据行不进正文


def _strip_html(text):
    """去除 HTML 标签，保留纯文本；雪球表情 <img alt="[X]"> 转为 [X] 文本占位（保持原文原则，禁止删除）"""
    if not text:
        return ""
    # 先保留 img 的 alt/title 文本（雪球表情/图片占位）
    text = re.sub(r"<img[^>]*?(?:alt|title)=\"([^\"]*)\"[^>]*/?>", r"\1", text, flags=re.I)
    # <br>/块级闭合标签是段落边界，剥标签前先落成换行（2026-10-01 用户报「原文是有格式的」：
    # 此前 <[^>]+> 一刀切把 <br></p> 抹成空串，整段帖子压成一行，段落结构在入库前就丢了）
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</(?:p|div|blockquote|li|h[1-6])>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"[ \t]+\n", "\n", text)  # 标签删除后残留的行尾空白
    text = re.sub(r"\n{3,}", "\n\n", text)  # 连续空行收敛成「一段一空行」
    text = re.sub(r"\$([^$]+)\$", r"\1", text)  # 去掉 $股票名(代码)$ 格式
    # 去掉详情页自带的来源前缀
    text = re.sub(r"^来源：雪球App，作者：[^）]+）", "", text)
    return text.strip()


# ── 帖子配图 URL 提取（2026-10-01 第①步：只取 URL 进元数据行，正文一律不留图片引用）──
# 为什么单独一层：正文净化（_strip_html）会把 <img>/<a> 整个抹掉，图片 URL 因此在库里归零
# （实测 4,024 帖只剩 4 条）。落地图片的前提是先把它从正文里"救"出来，而不是塞回正文——
# 塞回去会污染 content_hash=md5(body)，三处同键比对（import/check/MCP upsert）会把整批判不一致。
_IMG_URL_RE = re.compile(
    r"https?://[^\"'<>()\s]+[.](?:png|jpe?g|gif|webp|bmp)", re.I)
# 雪球内容图**只走这一个 CDN**：xqimg.imedao.com。头像是 xavatar.imedao.com、表情与界面图标
# 是 assets.imedao.com——2026-10-01 对话串实测里误收过 6 个 `commentlist_tag-….jpeg`（达人标签图标），
# 光靠黑名单堵不完，改用**域名白名单**收口：不在 xqimg 上的一律不当配图。
# ⚠ 雪球哪天换 CDN，这里要一起改，否则表现为「图全丢了」而不是报错——排查时先查这条。
IMG_HOST_RE = re.compile(r"^https?://xqimg\.", re.I)
# 表情/头像/徽标一律不算配图（表情另有 xqEmoji 显示层还原，头像走 profile 接口）
_JUNK_RE = re.compile(
    r"emoji|face_regular|badge|medal|identity_icon|xavatar|/community/|_logo|icon_"
    r"|commentlist_tag|_tag-|sprite"
    r"|!\d+x\d+\.jpg|!240x240|!50x50|!30x30", re.I)
# 雪球 CDN 的尺寸档后缀：同一张图会以 !custom.jpg / !800.jpg 两档出现，不去档会把图数虚报
_SIZE_SUFFIX_RE = re.compile(r"(?:!\d*x*\d*\.jpg|!custom\.jpg|!800\.jpg)$", re.I)


def norm_img_url(url):
    """归一化成「原图 base」：去尺寸档后缀、去 query（imageMogr2 参数由下载端再拼）"""
    url = (url or "").strip().rstrip(").,;'\"")
    url = _SIZE_SUFFIX_RE.sub("", url)
    return url


def _para_index(html, pos):
    """图在原帖第几段之后（0=首段前）。按块级分隔符切段、数非空块——口径是"显示时插回第几段"，
    切得不准只会让图退到相邻段，不影响 URL 本身，故取朴素实现不引 HTML 解析器。"""
    head = html[:pos]
    blocks = re.split(r"</p>|<br\s*/?>\s*<br\s*/?>|\n\s*\n", head)
    n = sum(1 for b in blocks if re.sub(r"<[^>]+>", "", b).strip())
    return max(0, n - 1)


def extract_images(*sources):
    """从若干 HTML 片段里抽出配图 URL 清单（表情/头像/徽标已滤、尺寸档已归一、按出现顺序去重）。

    sources 里每一项可以是 str（HTML 原文）或 list[dict]（详情页 DOM 直接给的 [{url, para}]）。
    返回 [{"url":…, "seq":0.., "para":…}]，para 未知时为 -1（渲染端据此退回卡尾，不静默丢图）。
    """
    out, seen = [], set()
    for src in sources:
        if not src:
            continue
        if isinstance(src, (list, tuple)):
            for it in src:
                raw = (it or {}).get("url", "")
                # 列表来源（详情页/流式 DOM 直接给的 URL）也在 Python 侧再过一次垃圾过滤：
                # JS 里的正则与这里漂移过一次就会把表情/头像当配图落库，双道比单道便宜
                if _JUNK_RE.search(raw) or not IMG_HOST_RE.match(norm_img_url(raw)):
                    continue
                u = norm_img_url(raw)
                if not u or u in seen:
                    continue
                seen.add(u)
                out.append({"url": u, "seq": len(out),
                            "para": int((it or {}).get("para", -1))})
            continue
        for m in _IMG_URL_RE.finditer(src):
            raw = m.group(0)
            if _JUNK_RE.search(raw) or not IMG_HOST_RE.match(norm_img_url(raw)):
                continue
            u = norm_img_url(raw)
            if not u or u in seen:
                continue
            seen.add(u)
            out.append({"url": u, "seq": len(out), "para": _para_index(src, m.start())})
    return out


def infer_form(text, is_column=False):
    """帖子形态判定（对齐框架粗制品形态值域）：
    回复=以「回复@」开头；专栏=is_column；长文=原创≥300字；其余=短文"""
    text = text.strip()
    if is_column:
        return "专栏"
    if text.startswith("回复@"):
        return "回复"
    if len(text) >= 300:
        return "长文"
    return "短文"


def _parse_timestamp(ts):
    """将毫秒时间戳转为可读字符串"""
    if not ts:
        return ""
    try:
        return datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d %H:%M")
    except (ValueError, OSError):
        return str(ts)


def filter_big_v(posts, min_reply_count):
    """从帖子列表中筛选大V用户（评论数超过阈值）"""
    big_v_map = {}
    for post in posts:
        reply_count = post.get("reply_count", 0)
        if reply_count < min_reply_count:
            continue
        user = post.get("user", {})
        uid = user.get("id") or post.get("user_id")
        if not uid:
            continue
        if uid not in big_v_map:
            big_v_map[uid] = UserInfo(
                user_id=uid,
                screen_name=user.get("screen_name", "未知"),
                followers_count=user.get("followers_count", 0),
                post_ids=[post["id"]],
            )
        else:
            big_v_map[uid].post_ids.append(post["id"])

    logger.info(f"筛选出 {len(big_v_map)} 位大V")
    return big_v_map


def extract_opinions(posts, symbol):
    """从用户帖子中提取与目标股票相关的观点"""
    opinions = []
    symbol_upper = symbol.upper()
    for post in posts:
        # 检查帖子是否与目标股票相关（在原始 HTML 内容中搜索）
        text = post.get("text", "") or ""
        desc = post.get("description", "") or ""
        title = post.get("title", "") or ""
        raw_content = f"{text} {desc} {title}".upper()
        if symbol_upper not in raw_content:
            continue

        clean_text = _strip_html(text)
        if not clean_text:
            clean_text = _strip_html(desc)
        if not clean_text:
            continue

        opinions.append(Opinion(
            post_id=post.get("id", 0),
            text=clean_text,
            reply_count=post.get("reply_count", 0),
            like_count=post.get("like_count", 0),
            created_at=_parse_timestamp(post.get("created_at")),
        ))

    return opinions


def summarize_opinions(opinions, top_n=5):
    """按互动量排序，取 Top N 条作为核心观点摘要"""
    if not opinions:
        return []
    ranked = sorted(
        opinions,
        key=lambda o: o.reply_count * 2 + o.like_count,
        reverse=True,
    )
    return ranked[:top_n]


def posts_to_opinions(posts):
    """将原始帖子列表转为 Opinion 列表（不做股票过滤）；须在 enrich 补全全文后调用（形态判定依赖完整文本）"""
    opinions = []
    for post in posts:
        text = post.get("text", "") or ""
        desc = post.get("description", "") or ""
        # 配图先取：正文已被 _strip_html 抹成纯文本，只能从"另存的两份来源"拿，绝不回写 body。
        # 优先 API 原始 HTML（enrich 前留存于 text_html），其次详情页 DOM 抓到的 dom_imgs。
        imgs = extract_images(post.get("text_html") or text, post.get("dom_imgs"))
        clean_text = _strip_html(text)
        if not clean_text:
            clean_text = _strip_html(desc)
        if not clean_text:
            # 有图无文＝雪球「纯图片帖」，现行口径仍按「摘要」不入库（改这条属第⑦步的口径决策）。
            # 这里显式留一行计数，好让"落地图片能救回多少帖"有实测数而不是估的。
            if imgs:
                logger.warning("IMG-ONLY-SKIP %s 配图 %d 张、正文为空（现行口径不入库）",
                               post.get("id", 0), len(imgs))
            continue
        pid = post.get("id", 0)
        created_ts = post.get("created_at") or 0
        is_pinned = bool(post.get("mark") == 1 or post.get("pinned"))
        target = post.get("target", "") or ""
        opinions.append(Opinion(
            post_id=pid,
            text=clean_text,
            reply_count=post.get("reply_count", 0) or 0,
            like_count=post.get("like_count", 0) or 0,
            retweet_count=post.get("retweet_count", 0) or 0,
            created_at=_parse_timestamp(created_ts),
            created_ts=created_ts,
            is_pinned=is_pinned,
            from_hot=bool(post.get("from_hot")),
            form=infer_form(clean_text, bool(post.get("is_column"))),
            title=(post.get("title") or "").strip(),
            post_url=f"https://xueqiu.com/{target}" if target else "",
            images=imgs,
            # 完整性：正文仍与被截断的 API 摘要一致 → 说明详情页补全没成功，标「摘要」
            # （2026-09-16 修：此前补全失败的截断帖也会被标成「全文」，属误标）
            completeness=(
                "摘要"
                if post.get("needs_full") and clean_text == _strip_html(desc)
                else "全文"
            ),
        ))
    return opinions
