import os
import re
from datetime import datetime

import config


def generate_report(symbol, users_opinions, output_dir=None):
    """生成 Markdown 格式的大V观点汇总报告"""
    if output_dir is None:
        output_dir = config.DEFAULT_OUTPUT_DIR
    os.makedirs(output_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        f"# {symbol} 大V观点汇总",
        "",
        f"> 生成时间: {timestamp}  ",
        f"> 筛选条件: 评论数 > {config.MIN_REPLY_COUNT}  ",
        f"> 共发现 {len(users_opinions)} 位大V",
        "",
        "---",
        "",
    ]

    for idx, (user_info, opinions) in enumerate(users_opinions, 1):
        lines.append(f"## {idx}. {user_info.screen_name}")
        lines.append("")
        lines.append(f"- 粉丝数: {user_info.followers_count}")
        lines.append(f"- 相关帖子数: {len(opinions)}")
        lines.append("")

        if opinions:
            lines.append("### 核心观点")
            lines.append("")
            for op in opinions[:3]:
                text = op.text[:200] + "..." if len(op.text) > 200 else op.text
                lines.append(f"- {text}")
            lines.append("")

            lines.append("### 热门帖子")
            lines.append("")
            lines.append("| 时间 | 内容摘要 | 评论 | 点赞 |")
            lines.append("|------|----------|------|------|")
            for op in opinions:
                short = op.text[:80].replace("|", "/").replace("\n", " ")
                if len(op.text) > 80:
                    short += "..."
                lines.append(f"| {op.created_at} | {short} | {op.reply_count} | {op.like_count} |")
            lines.append("")

        lines.append("---")
        lines.append("")

    filename = f"{symbol}_大V观点_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    filepath = os.path.join(output_dir, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return filepath


def _fmt_title(opinion):
    """帖子标题：优先 title 字段，否则取正文首句（完整句子，不硬切字数）"""
    if opinion.title:
        return opinion.title[:60]
    text = opinion.text.strip()
    m = re.match(r"^([^。！？!?\n]+[。！？!?])", text)
    if m:
        return m.group(1)
    return text[:30]


def generate_user_report(screen_name, user_id, opinions, output_dir=None, outfile=None):
    """生成对齐投资框架粗制品规范的帖子集文件（frontmatter + 三件套 + 发布行）
    发布行：> 发布：{YYYY年M月D日 HH:MM} | 形态：X | 转发 n | 回复 n | 点赞 n | 全文/摘要 | [原文](url)
    """
    import re as _re
    from datetime import datetime as _dt
    if output_dir is None:
        output_dir = config.DEFAULT_OUTPUT_DIR
    os.makedirs(output_dir, exist_ok=True)

    now = _dt.now()
    date_cn = now.strftime("%Y年%m月%d日")
    # 置顶帖：时间窗路径不纳入正文（置顶时间旧且非本次窗口内容），仅附录一行供追溯；
    # 热门路径（from_hot，新博主首采 2026-09-28）的置顶帖**有意纳入正文**——首采一次性
    # 无重复采集风险，且置顶通常是博主最重要的方法论帖（如五年实盘复盘）
    body_ops = [o for o in opinions if not o.is_pinned or o.from_hot]
    pinned_ops = [o for o in opinions if o.is_pinned and not o.from_hot]

    lines = [
        "---",
        f'title: "雪球帖子采集：{screen_name} {date_cn}"',
        f'source: "https://xueqiu.com/u/{user_id}"',
        f'author: "{screen_name}"',
        f'date: "{date_cn}"',
        f'recorded: "{date_cn}"',
        'type: "帖子集"',
        'status: "待提炼"',
        "tags: []",
        "---",
        "",
    ]

    for idx, op in enumerate(body_ops, 1):
        ts_cn = _re.sub(r"(\d{4})-(\d{2})-(\d{2}) (\d{2}:\d{2})", r"\1年\2月\3日 \4", op.created_at)
        lines.append(f"## {idx}. {_fmt_title(op)}")
        lines.append("")
        lines.append(op.text)
        lines.append("")
        lines.append(
            f"> 发布：{ts_cn} | 形态：{op.form} | 转发 {op.retweet_count} | "
            f"回复 {op.reply_count} | 点赞 {op.like_count} | {op.completeness} | "
            f"[原文](https://xueqiu.com/{user_id}/{op.post_id})"
        )
        lines.append("")
        lines.append("---")
        lines.append("")

    if pinned_ops:
        lines.append("## 附：置顶帖（不纳入本次窗口）")
        lines.append("")
        for op in pinned_ops:
            lines.append(f"- [{op.created_at}] {_fmt_title(op)} [原文](https://xueqiu.com/{user_id}/{op.post_id})")
        lines.append("")

    if outfile is None:
        filename = f"雪球采集-{screen_name}-{date_cn}.md"
    else:
        filename = outfile if outfile.endswith(".md") else outfile + ".md"
    filepath = os.path.join(output_dir, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write("\n".join(lines).rstrip() + "\n")
    return filepath
