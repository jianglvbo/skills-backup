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


def _strip_html(text):
    """去除 HTML 标签，保留纯文本；雪球表情 <img alt="[X]"> 转为 [X] 文本占位（保持原文原则，禁止删除）"""
    if not text:
        return ""
    # 先保留 img 的 alt/title 文本（雪球表情/图片占位）
    text = re.sub(r"<img[^>]*?(?:alt|title)=\"([^\"]*)\"[^>]*/?>", r"\1", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\$([^$]+)\$", r"\1", text)  # 去掉 $股票名(代码)$ 格式
    # 去掉详情页自带的来源前缀
    text = re.sub(r"^来源：雪球App，作者：[^）]+）", "", text)
    return text.strip()


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
        clean_text = _strip_html(text)
        if not clean_text:
            clean_text = _strip_html(desc)
        if not clean_text:
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
            # 完整性：正文仍与被截断的 API 摘要一致 → 说明详情页补全没成功，标「摘要」
            # （2026-09-16 修：此前补全失败的截断帖也会被标成「全文」，属误标）
            completeness=(
                "摘要"
                if post.get("needs_full") and clean_text == _strip_html(desc)
                else "全文"
            ),
        ))
    return opinions
