import argparse
import datetime
import json
import logging
import re
import sys
import time

import config
from crawler import XueqiuCrawler, CrawlerError
import feed as feed_mod

# 哨兵：采集成功但时间窗内无新帖（与「采集失败」区分——2026-09-09 固化）
# 退出码约定：0=有产出 / 2=窗口内无新帖（采集完成）/ 1=失败（WAF/登录/异常）
# 编排层据此决定是否更新 info_cutoff（失败时禁止更新，保证不漏采）
NO_NEW_POSTS = "__NO_NEW_POSTS__"
# 哨兵：窗口起点没被翻到（页数不足），**不等于**窗口内无帖——2026-09-12 实测：
# 雪月霜 09-08 窗口用 4 页（80 条）只翻到 09-09 之后，过滤器判定"无帖"返回 2，
# 编排层若据此推进 info_cutoff 就会永久漏采。故单列退出码 3。
NO_WINDOW_REACHED = "__NO_WINDOW_REACHED__"
from analyzer import filter_big_v, extract_opinions, summarize_opinions, posts_to_opinions
from report import generate_report, generate_user_report

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def _parse_window(s):
    """解析时间窗参数：YYYY-MM-DD 或 YYYY-MM-DDTHH:MM:SS → 毫秒时间戳"""
    s = s.strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        s += "T00:00:00"
    try:
        dt = datetime.datetime.strptime(s, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        raise SystemExit(f"时间格式错误: {s}（支持 YYYY-MM-DD 或 YYYY-MM-DDTHH:MM:SS）")
    return int(dt.timestamp() * 1000)


def run(symbol, min_reply_count=None, max_pages=None, output_dir=None):
    """主流程：爬取 -> 筛选大V -> 提取观点 -> 生成报告"""
    if min_reply_count is None:
        min_reply_count = config.MIN_REPLY_COUNT
    if max_pages is None:
        max_pages = config.MAX_PAGES
    if output_dir is None:
        output_dir = config.DEFAULT_OUTPUT_DIR

    logger.info(f"开始爬取 {symbol} 的讨论帖...")
    crawler = XueqiuCrawler()

    try:
        all_posts = []
        for page in range(1, max_pages + 1):
            logger.info(f"爬取第 {page}/{max_pages} 页...")
            try:
                posts = crawler.get_stock_posts(symbol, sort="reply", page=page)
            except CrawlerError as e:
                logger.error(f"第 {page} 页爬取失败: {e}")
                break
            if not posts:
                logger.info("没有更多帖子了")
                break
            all_posts.extend(posts)

        logger.info(f"共获取 {len(all_posts)} 条帖子")

        big_v_map = filter_big_v(all_posts, min_reply_count)
        if not big_v_map:
            logger.warning("未找到符合条件的大V")
            return None

        user_posts_map = {}
        for post in all_posts:
            uid = post.get("user_id") or (post.get("user", {}) or {}).get("id")
            if uid and uid in big_v_map:
                user_posts_map.setdefault(uid, []).append(post)

        users_opinions = []
        for uid, user_info in big_v_map.items():
            posts_for_user = user_posts_map.get(uid, [])
            opinions = extract_opinions(posts_for_user, symbol)
            opinions = summarize_opinions(opinions)
            if opinions:
                users_opinions.append((user_info, opinions))
                logger.info(f"大V [{user_info.screen_name}] 提取到 {len(opinions)} 条观点")

        if not users_opinions:
            logger.warning("所有大V均无相关观点")
            return None

        users_opinions.sort(key=lambda x: x[0].followers_count, reverse=True)
        filepath = generate_report(symbol, users_opinions, output_dir)
        logger.info(f"报告已生成: {filepath}")
        return filepath
    finally:
        crawler.close()


def run_user(user_id, max_pages=10, output_dir=None, days=None, column_only=False,
             from_time=None, to_time=None, outfile=None, hot_pages=0):
    """爬取指定用户的帖子并生成帖子集文件。user_id 可以是数字ID或用户名。
    时间窗：优先 --from/--to 精确窗口；否则 --days 相对窗口（兼容）。
    hot_pages（2026-09-28 新博主首采）：热门 tab（type=9）前 N 页——窗口过滤之后
    按 URL 去重并入，不受时间窗裁剪；热门页 1 的置顶帖有意保留。"""
    if output_dir is None:
        output_dir = config.DEFAULT_OUTPUT_DIR

    crawler = XueqiuCrawler()

    try:
        # 如果不是纯数字，按用户名搜索解析
        if not str(user_id).isdigit():
            logger.info(f"搜索用户: {user_id}")
            user_id, resolved_name = crawler.find_user_id(user_id)
            logger.info(f"已解析: {resolved_name} -> {user_id}")

        logger.info(f"开始爬取用户 {user_id} 的帖子...")

        # 窗口起点先算好传给翻页器：翻到边界自动停（翻页器内部排除置顶帖再判）
        lo = _parse_window(from_time) if from_time else (
            int((time.time() - days * 86400) * 1000) if days else None)
        hi = _parse_window(to_time) if to_time else None

        # 一次导航同时获取用户名和帖子
        screen_name, all_posts = crawler.get_user_all_posts_with_info(
            user_id, max_pages=max_pages, window_lo_ms=lo)
        logger.info(f"用户: {screen_name}，共获取 {len(all_posts)} 条帖子")

        if not all_posts:
            logger.warning("未获取到任何帖子")
            return None

        # 时间窗过滤：置顶帖排除（时间旧且非窗口内容）+ created_at 范围
        if lo is not None or hi is not None:
            before = len(all_posts)
            # 窗口覆盖门槛用「非置顶」的最旧帖：置顶帖是几年前的旧帖、常年挂第 1 页，
            # 把它算进 oldest 会让门槛永远不触发（exit 3 失灵 → 断点误推进 → 漏采）
            live_ts = [p.get("created_at") or 0 for p in all_posts
                       if not (p.get("mark") == 1 or p.get("pinned"))]
            oldest = min(live_ts) if live_ts else 0
            all_posts = [
                p for p in all_posts
                if not (p.get("mark") == 1 or p.get("pinned"))  # 置顶帖不纳入窗口
                and p.get("created_at", 0) >= (lo or 0)
                and (hi is None or p.get("created_at", 0) <= hi)
            ]
            window_desc = f"{from_time or ('最近%d天' % days if days else '起')} ~ {to_time or 'now'}"
            logger.info(f"时间窗过滤[{window_desc}]: {before} -> {len(all_posts)} 条（置顶帖已排除）")
            # 窗口起点覆盖门禁（2026-09-24 补齐）：原先只在「过滤后全空」时检查页数是否够，
            # 漏洞是——被 WAF 中途截断但已捞到几条时，会静默判成功并推进 cutoff，
            # 中间那段永久漏采。现在：**只要页数用满且最旧帖仍新于窗口起点，就判页数不足**。
            # 「页数用满」的判据：累计条数 ≥ max_pages × 每页条数（服务端固定 20；
            # 2026-09-28 起请求不再拼 count，与 UI 一致）。翻页器提前触边界自动停时
            # before < max_pages×20，门槛不会误报。
            page_size = getattr(crawler, "_timeline_count", 20) or 20
            if lo and oldest and oldest > lo and before >= max_pages * page_size:
                logger.error(
                    "窗口起点 %s 未被翻到：本轮最旧帖为 %s（页数已用满 max-pages=%d × %d 条）——"
                    "不可推进 cutoff，须加大 --max-pages 重跑",
                    from_time, time.strftime('%Y-%m-%d %H:%M', time.localtime(oldest / 1000)),
                    max_pages, page_size,
                )
                return NO_WINDOW_REACHED
            if not all_posts:
                logger.warning("过滤后无帖子（窗口内无新帖，采集完成）")
                return NO_NEW_POSTS

        # 热门 tab 前 N 页（新博主首采第 3 步）：必须在窗口过滤**之后**合并——
        # 热门里会带回超出窗口的高赞老帖，先合并会被时间窗裁掉
        if hot_pages > 0:
            hot = crawler.get_user_hot_posts(user_id, pages=hot_pages)
            seen = set()
            for p in all_posts:
                seen.add(p.get("id"))
                if p.get("target"):
                    seen.add(p.get("target"))
            added = 0
            for p in hot:
                if p.get("id") in seen or (p.get("target") and p.get("target") in seen):
                    continue
                p["from_hot"] = True
                all_posts.append(p)
                seen.add(p.get("id"))
                if p.get("target"):
                    seen.add(p.get("target"))
                added += 1
            logger.info(f"热门 tab 前 {hot_pages} 页并入 {added} 条（与全部 tab 去重后）")

        # 仅保留专栏文章
        if column_only:
            before = len(all_posts)
            all_posts = [p for p in all_posts if p.get("is_column")]
            logger.info(f"专栏过滤: {before} -> {len(all_posts)} 条")
            if not all_posts:
                logger.warning("过滤后无专栏文章（窗口内无新帖，采集完成）")
                return NO_NEW_POSTS

        # 补全被截断的帖子全文（含详情页精确时间覆盖）
        logger.info("正在获取帖子全文...")
        crawler.enrich_posts_full_text(all_posts)

        # 转为 Opinion 并按时间倒序排列
        opinions = posts_to_opinions(all_posts)
        opinions.sort(key=lambda o: o.created_ts, reverse=True)
        logger.info(f"有效帖子: {len(opinions)} 条")

        filepath = generate_user_report(
            screen_name, user_id, opinions, output_dir, outfile=outfile
        )
        logger.info(f"帖子集已生成: {filepath}")
        return filepath
    finally:
        crawler.close()


def run_search(keyword):
    """搜索雪球用户并打印结果"""
    logger.info(f"搜索用户: {keyword}")
    crawler = XueqiuCrawler()
    try:
        users = crawler.search_user(keyword)
        if not users:
            print("未找到匹配用户")
            return
        print(f"\n找到 {len(users)} 个用户:\n")
        for i, u in enumerate(users, 1):
            uid_str = u.get("uid") or "需解析"
            desc = u.get("desc", "")
            print(f"  {i}. {u['name']}  (ID: {uid_str})  {desc}")
        print()
    finally:
        crawler.close()


def run_profile(user_id):
    """新博主首采第 1 步：开主页抓档案（uid/昵称/头像/简介/粉丝/总帖数），打印 JSON。
    调用方（agent）据此走看板 add_blogger 落库（含 avatar 字段，2026-09-28 服务端已支持）。"""
    crawler = XueqiuCrawler()
    try:
        if not str(user_id).isdigit():
            logger.info(f"搜索用户: {user_id}")
            user_id, resolved_name = crawler.find_user_id(user_id)
            logger.info(f"已解析: {resolved_name} -> {user_id}")
        profile = crawler.get_user_profile(user_id)
        if not profile.get("screen_name"):
            raise CrawlerError(f"档案抓取失败：昵称为空（uid={profile.get('uid')}）——"
                               f"检查登录态/风控（空页一律按拦截处理）")
        print(json.dumps(profile, ensure_ascii=False, indent=1))
        return profile
    finally:
        crawler.close()


def main():
    # 兼容旧用法：如果第一个参数不是已知子命令，自动当作 stock 子命令
    if len(sys.argv) > 1 and sys.argv[1] not in ("stock", "user", "search", "feed", "profile", "-h", "--help"):
        sys.argv.insert(1, "stock")

    parser = argparse.ArgumentParser(description="雪球爬虫工具")
    subparsers = parser.add_subparsers(dest="command", help="子命令")

    # stock 子命令
    sp_stock = subparsers.add_parser("stock", help="爬取股票大V观点")
    sp_stock.add_argument("symbol", help="股票代码，如 SH600519、SZ002738")
    sp_stock.add_argument("--min-reply", type=int, default=config.MIN_REPLY_COUNT)
    sp_stock.add_argument("--max-pages", type=int, default=config.MAX_PAGES)
    sp_stock.add_argument("--output", default=config.DEFAULT_OUTPUT_DIR)

    # user 子命令
    sp_user = subparsers.add_parser("user", help="爬取指定用户帖子并生成帖子集")
    sp_user.add_argument("user_id", help="用户ID或用户名（用户名会自动搜索解析）")
    sp_user.add_argument("--max-pages", type=int, default=10)
    sp_user.add_argument("--days", type=int, default=None, help="只保留最近N天的帖子（与 --from 互斥，优先 --from）")
    sp_user.add_argument("--from", dest="from_time", default=None, help="起始时间 YYYY-MM-DD 或 YYYY-MM-DDTHH:MM:SS（对齐 info_cutoff 增量窗口）")
    sp_user.add_argument("--to", dest="to_time", default=None, help="截止时间（默认当前）")
    sp_user.add_argument("--column", action="store_true", help="仅抓取专栏文章")
    sp_user.add_argument("--output", default=config.DEFAULT_OUTPUT_DIR)
    sp_user.add_argument("--outfile", default=None, help="输出文件名（默认 雪球采集-{昵称}-{日期}.md）")
    sp_user.add_argument("--hot-pages", type=int, default=0,
                         help="热门 tab（type=9）前 N 页并入帖子集（新博主首采用 5；日常增量不传）")

    # search 子命令
    sp_search = subparsers.add_parser("search", help="搜索雪球用户")
    sp_search.add_argument("keyword", help="搜索关键词（用户名）")

    # profile 子命令（新博主首采第 1 步：主页档案 → 落库登记）
    sp_profile = subparsers.add_parser("profile", help="抓取用户主页档案（uid/昵称/头像/简介/粉丝），打印 JSON")
    sp_profile.add_argument("user_id", help="用户ID或用户名（用户名会自动搜索解析）")

    # feed 子命令（流式采集——日常增量缺省路径，2026-09-26 实测定型）
    sp_feed = subparsers.add_parser("feed", help="流式采集关注/热门时间线（多博主帖子集）")
    sp_feed.add_argument("--tab", choices=["follow", "hot"], default="follow",
                         help="follow=关注流（缺省，增量日常采集）；hot=热门流（探索用）")
    sp_feed.add_argument("--limit", type=int, default=50, help="最多采集条数（默认 50）")
    sp_feed.add_argument("--since", default=None,
                         help="窗口起点 ISO；缺省=上次流断点书签（follow），无书签则按 limit 采")
    sp_feed.add_argument("--no-since", action="store_true", help="忽略断点书签，按 limit 采")
    sp_feed.add_argument("--filter-tracked", choices=["auto", "on", "off"], default="auto",
                         help="看板博主过滤（auto=关注流开/热门流关；被过滤帖不入产物，未建档帖入库侧也会跳过）")
    sp_feed.add_argument("--output", default=None, help="输出目录（默认 ~/.cache/xueqiu-spyder/out）")
    sp_feed.add_argument("--outfile", default=None, help="输出文件名（默认 雪球采集-{流名}-{日期}.md）")
    sp_feed.add_argument("--no-threads", dest="threads", action="store_false",
                         help="关掉「同一趟顺带采对话串」（缺省开：流内每条有对话入口的回复帖就地开弹窗采整条问答链，"
                              "旁挂一份 import-thread.js 能直接吃的 JSON）")
    sp_feed.set_defaults(threads=True)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    try:
        if args.command == "stock":
            result = run(args.symbol, args.min_reply, args.max_pages, args.output)
        elif args.command == "user":
            result = run_user(args.user_id, args.max_pages, args.output,
                              getattr(args, 'days', None), getattr(args, 'column', False),
                              getattr(args, 'from_time', None), getattr(args, 'to_time', None),
                              getattr(args, 'outfile', None),
                              getattr(args, 'hot_pages', 0) or 0)
        elif args.command == "profile":
            run_profile(args.user_id)
            return
        elif args.command == "search":
            run_search(args.keyword)
            return
        elif args.command == "feed":
            result = feed_mod.run_feed(
                args.tab, args.limit,
                args.since, args.output, args.outfile, args.filter_tracked,
                use_bookmark=not args.no_since, threads=args.threads,
            )
        else:
            parser.print_help()
            sys.exit(1)

        if result == NO_WINDOW_REACHED:
            print("\n窗口起点未被翻到（页数不足）：不可视为『无新帖』，须加大 --max-pages 重跑")
            sys.exit(3)
        elif result == NO_NEW_POSTS:
            print("\n窗口内无新帖（采集完成，无新增内容）")
            sys.exit(2)
        elif result:
            print(f"\n报告已保存到: {result}")
        else:
            print("\n未生成报告")
            sys.exit(1)
    except CrawlerError as e:
        logger.error(f"爬取失败: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
