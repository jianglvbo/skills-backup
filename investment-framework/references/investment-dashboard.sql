-- ============================================================
-- investment-dashboard: 投资看板派生数据层
-- 架构原则: 本地 vault 为绝对基准（第一/首要/绝对），本库仅为
--           阅读 + 加工总结的派生数据；一切冲突以 vault 为准。
-- 字符集: utf8mb4 / utf8mb4_unicode_ci
-- 约定: ① 枚举字段一律存码值，逻辑关联统一 dict 码值表（type+code）；主键自增；
--       ② **业务逻辑关联一律不设外键**（dict 码值、言论↔实体 _rel、验证留痕 statement_verify_sub、
--          复核建议 statement_review_sub 全由应用层维护）。**例外**：review_check_sub
--          保留建表期继承的物理外键（纯记录用，不参与业务写入）。
--       ③ 每表每字段均带 COMMENT；
--       ④ **一条帖子只落一张表**：statement_trade/predict/research/view/insight/chat
--          六张之一（唯一例外 post_history 原文表），不得落在第二张表；博主/个股/行业/指数/市场五个维度全靠 _rel 关联表；
--       ⑤ **命名**：表名一律**单数**；布尔 is_/has_ 前缀、时间名跟类型（date → _date、datetime → _datetime）、
--          枚举 _code 后缀、子表 _sub 后缀、关联表 _rel 后缀；
--       ⑥ **可枚举的值表进 dict**（标签、方向、状态等一律 dict，字段注释里写 dict.type 名）；
--       ⑦ **注释写法**：表注释只写「XX表 / XX子表」，字段注释平实直述，不带括号补充说明。
--
-- 变更日志（只记与当前结构有关的口径；历史改名过程见仓库 git 记录）:
--   2026-09-13 库结构规范化（用户拍板）:
--     ① **表名全部单数**：bloggers→blogger、stocks→stock、industries→industry、markets→market、
--        quotes→quote、todos→todo、refine_records→refine_record、review_records→review_record、
--        pending_review→pending_decision；只读 UNION 视图 `statements`→`statement`。
--     ② **列名规范**：布尔 is_/has_（is_read/is_special/is_review_required/is_done/has_hk_connect/is_enabled/is_verify_ok/is_blogger_updated）；
--        时间名跟类型（*_datetime：created/updated/deleted/posted/fetched/resolved/done/refined/saved/info_cutoff）；
--        枚举 _code（stance_code/verdict_code/kind_code/status_code/verify_result_code/source_code/raised_by_code/post_form）；
--        重载列改名（blogger→blogger_name、source→source_batch|source_code）；
--        泛化名加前缀（post_title/post_text/review_*/refine_*/case_*/quote_text/sort_order/todo_content）。
--     ③ **类型**：info_cutoff varchar→datetime；at/saved_at bigint(ms)→datetime；
--        statement_date(date)→statement_datetime(datetime)，用 post_history.posted_datetime 回填精确发帖时间。
--     ④ **接口契约不变**：MCP 入参仍为 blogger/form/stance/source/statementDate/viewDate/fetchedAt/wikiRef，URL 仍为 /api/bloggers/*。
--   2026-09-12 结构（用户拍板）:
--     ① 分层命名：**采集层叫「帖子」→ post_*；提炼后叫「言论」→ statement_***
--        —— 六张言论表 + 只读 UNION 视图 + 序列 statement_id_seq
--        + 子表 statement_verify_sub/statement_review_sub（_sub）＋关联表 _rel。
--        `post_history` 属帖子层，表名与列名保持 post_ 前缀。
--     ② 实体三表取代 prediction_subjects：stock（个股）/ industry（行业）/ market（市场）；
--        宏观/认知/策略类概念不是实体。
--     ③ 关联六表（全部 _rel）：statement_blogger_rel（言论必挂博主）/ statement_stock_rel /
--        statement_industry_rel / statement_market_rel / stock_industry_rel（个股必挂行业）/
--        stock_market_rel。
--     ④ 一条帖子只落一张言论表（优先级命中即止），正文完整保留帖子含义；买卖**不含操作字段**。
--     ⑤ 弃用对象一律**删前备份、然后 DROP**，不留 _del 残表；派生索引（vault 文件/标签）不落库，内存扫描。
--     ⑥ 表注释只写「XX表/XX子表」，字段注释平实直述，码值字段标注 `dict.type`。
--   2026-09-28 重新导出（审计发现快照滞后）:
--     ① 删 refine_chain_step/refine_step/refine_review 三表（2026-09-26 提炼步骤落库下线，framework-rules #54）；
--        refine_target_sub 随之消亡，物理外键例外只剩 review_check_sub。
--     ② 补 2026-09-25 后新增的 stock_watch（我的自选/个股备注）、industry_follow（关注行业）、
--        statement_blogger_rel 等关联表与 console_stat_daily 预聚合表；dict 种子按库内现值重导。
-- ============================================================

CREATE DATABASE IF NOT EXISTS investment-dashboard DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE investment-dashboard;

CREATE TABLE `dict` (
  `type` varchar(32) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '字典类型',
  `code` varchar(32) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '码值',
  `name` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '显示名',
  `sort_order` int NOT NULL DEFAULT '0' COMMENT '排序',
  `is_enabled` tinyint NOT NULL DEFAULT '1' COMMENT '是否启用',
  `remark` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '备注',
  PRIMARY KEY (`type`,`code`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='字典表';

INSERT INTO dict (type, code, name, sort_order, is_enabled, remark) VALUES
  ('ambiguous_word', '小米', '小米', 0, 1, '与常用词同形的个股别名：命中后须过上下文判定'),
  ('ambiguous_word', '美的', '美的', 0, 1, '与常用词同形的个股别名：命中后须过上下文判定'),
  ('category', 'analysis_framework', '分析框架', 1, 1, '方法论/思维框架类条目'),
  ('category', 'trading_system', '交易体系', 2, 1, '交易规则/体系类条目'),
  ('category', 'investment_mentality', '投资心态', 3, 1, '心态/心理类条目'),
  ('category', 'investment_insight', '投资心得', 4, 1, '心得/复盘类条目'),
  ('category', 'stock', '个股', 5, 1, '个股分析条目'),
  ('category', 'industry', '行业', 6, 1, '行业研究条目'),
  ('category', 'macro', '宏观', 7, 1, '宏观分析条目'),
  ('check_status', 'pass', '通过', 1, 1, '检查通过'),
  ('check_status', 'ok', '通过(旧)', 2, 1, '历史数据中的通过标记'),
  ('check_status', 'warn', '警告', 3, 1, '存在问题需关注'),
  ('check_status', 'fail', '失败', 4, 1, '检查未通过'),
  ('console_type', 'stock', '个股', 1, 1, '具体股票+代码'),
  ('console_type', 'industry', '行业', 2, 1, '申万最下级/自定义板块'),
  ('console_type', 'market', '市场', 3, 1, 'A股/港股/美股大盘'),
  ('console_type', 'index', '指数', 4, 1, NULL),
  ('del_flag', '0', '未删除', 0, 1, NULL),
  ('del_flag', '1', '已删除', 1, 1, NULL),
  ('entity_type', 'blogger', '博主', 1, 1, NULL),
  ('entity_type', 'stock', '个股', 2, 1, NULL),
  ('entity_type', 'industry', '行业', 3, 1, NULL),
  ('entity_type', 'market', '市场', 4, 1, NULL),
  ('entity_type', 'market_index', '指数', 4, 1, NULL),
  ('food_homonym', '小米', '小米', 0, 1, '与食物/日用品同名的股名：需证券语境词或产品词才判 link，仅动作词判 doubt'),
  ('food_homonym', '苹果', '苹果', 0, 1, '与食物/日用品同名的股名：需证券语境词或产品词才判 link，仅动作词判 doubt'),
  ('hk_connect_source', 'eastmoney_dlmk0146', '东方财富·港股通板块（沪深港通口径）', 1, 1, NULL),
  ('layer', 'my', '我的', 1, 1, '个人总结/自建框架层'),
  ('layer', 'blogger', '博主', 2, 1, '博主画像及其产出层'),
  ('layer', 'other', '其他', 3, 1, '引用/外部资料层'),
  ('layer', 'macro', '宏观', 4, 1, '宏观分析层'),
  ('layer', 'workspace', '工作区', 5, 1, '工作区文件（粗制品/原始资源/控制台等）'),
  ('layer', 'attachment', '附件', 6, 1, '附件目录（图片等非条目）'),
  ('platform', 'xueqiu', '雪球', 1, 1, '雪球平台'),
  ('platform', 'douyin', '抖音', 2, 1, '抖音平台'),
  ('platform', 'xiaohongshu', '小红书', 3, 1, '小红书平台'),
  ('platform', 'xueqiu_deleted', '雪球已销户', 3, 1, '雪球账号已注销，保留历史言论与留档，不再采集'),
  ('post_content_type', 'research', '研究', 1, 1, '含数据/估值/行业结构的可复用分析；已沉淀框架文件则观点列写见 [[分类/文件名]]；判定优先级见 refine-schema §六（trade>predict>research>insight>view>chat），本 sort_order 仅看板分节顺序'),
  ('post_content_type', 'predict', '预测', 2, 1, '对未来的判断（不强制可验证，2026-09-14 放宽）：①方向（stance 必填）②未来指向（时间窗或事件条件；"下一轮牛市""至少 5 年内"这类粗口径也算）。有目标位/幅度/点位则写进信号列、可进预测控制台闭环；没有不影响归类。可判对错不是门槛；判定优先级见 refine-schema §六（trade>predict>research>insight>view>chat），本 sort_order 仅看板分节顺序'),
  ('post_content_type', 'view', '观点', 3, 1, '对个股/行业/市场/政策的当下判断（无未来指向），须带方向；判定优先级见 refine-schema §六（trade>predict>research>insight>view>chat），本 sort_order 仅看板分节顺序'),
  ('post_content_type', 'insight', '心得', 4, 1, '投资心得、方法论、复盘框架；判定优先级见 refine-schema §六（trade>predict>research>insight>view>chat），本 sort_order 仅看板分节顺序'),
  ('post_content_type', 'chat', '闲聊', 5, 1, '仅当能刻画「擅长与局限/投资心态」时留存，否则舍弃；判定优先级见 refine-schema §六（trade>predict>research>insight>view>chat），本 sort_order 仅看板分节顺序'),
  ('post_content_type', 'trade', '买卖', 6, 1, '明确买卖动作（须当期性：当下/近期动作或当前仓位；历史回顾归 insight）；买卖帖只落 statement_trade 一行（一帖一表），原 blogger_trades 已并入并退役为 blogger_trades_del；判定优先级见 refine-schema §六（trade>predict>research>insight>view>chat），本 sort_order 仅看板分节顺序'),
  ('prediction_status', 'pending', '待验证', 1, 1, '尚未到验证时点'),
  ('prediction_status', 'verifying', '验证中', 2, 1, '已有部分验证证据'),
  ('prediction_status', 'verified_correct', '已验证(正确)', 3, 1, '方向正确（数值偏差进验证备注）'),
  ('prediction_status', 'verified_wrong', '已验证(错误)', 4, 1, '方向相反/关键数值未兑现'),
  ('prediction_status', 'revoked', '已撤销', 5, 1, '博主撤回或判断失效'),
  ('refine_chain', 'statement', '帖子→言论', 1, 1, NULL),
  ('refine_chain', 'wiki', '粗制品→wiki', 2, 1, NULL),
  ('refine_review_state', 'open', '待审查处理', 1, 1, NULL),
  ('refine_review_state', 'internalized', '已内化成规则', 2, 1, NULL),
  ('refine_review_state', 'dismissed', '忽略', 3, 1, NULL),
  ('refine_review_status', 'pending', '待复核', 1, 1, NULL),
  ('refine_review_status', 'confirmed', '已确认', 2, 1, NULL),
  ('refine_review_status', 'rejected', '已打回', 3, 1, NULL),
  ('refine_review_verdict', 'wrong', '这步错了', 1, 1, NULL),
  ('refine_review_verdict', 'confirm', '整帖确认无误', 2, 1, NULL),
  ('refine_source_kind', 'post', '帖子（post_history 原文）', 1, 1, NULL),
  ('refine_source_kind', 'coarse', '粗制品／原始资源', 2, 1, NULL),
  ('refine_source_kind', 'attachment', '附件', 3, 1, NULL),
  ('refine_step', 'worth', '值不值得提炼', 1, 1, '这条内容该不该产出（信息密度门槛／值不值得沉淀成条目）'),
  ('refine_step', 'content_type', '内容类型', 2, 1, '六选一：trade>predict>research>insight>view>chat（wiki 链路不适用）'),
  ('refine_step', 'split', '拆分', 3, 1, '一个源产出几条（一帖一条／多对象必拆／一篇拆成 N 个条目）'),
  ('refine_step', 'attribution', '归属与分类', 4, 1, '归属层（我的/博主/其他/宏观）+ 分类'),
  ('refine_step', 'subjects', '标的关联', 5, 1, '挂哪些个股/行业/市场（可以 0 个）'),
  ('refine_step', 'signal_time', '信号与时间', 6, 1, 'stance + signal + 内容时间 view_date/view_date_source'),
  ('refine_step', 'relation', '关系与落点', 7, 1, '与已有内容的关系（新建/追加/互补/冲突）+ 落到哪张表/哪个路径'),
  ('source_type', 'raw', '原始资源', 1, 1, '直接由原始资源提炼'),
  ('source_type', 'coarse', '粗制品', 2, 1, '由粗制品提炼'),
  ('stance', 'bullish', '看多', 1, 1, '方向：看多/看好/认为便宜'),
  ('stance', 'bearish', '看空', 2, 1, '方向：看空/认为高估/规避'),
  ('stance', 'neutral', '中性', 3, 1, '方向：中性/观望/仅陈述条件'),
  ('stance', 'short_bullish', '短期看多', 4, 1, NULL),
  ('stance', 'short_bearish', '短期看空', 5, 1, NULL),
  ('stance', 'long_bullish', '长期看多', 6, 1, NULL),
  ('stance', 'long_bearish', '长期看空', 7, 1, NULL),
  ('tag', '个股/医药生物', '个股/医药生物', 0, 1, '标签（原 tags 表迁入，2026-09-12）'),
  ('tag', '交易体系/仓位管理', '交易体系/仓位管理', 0, 1, '标签（原 tags 表迁入，2026-09-12）'),
  ('tag', '交易体系/价值投资', '交易体系/价值投资', 0, 1, '标签（原 tags 表迁入，2026-09-12）'),
  ('tag', '交易体系/止损止盈', '交易体系/止损止盈', 0, 1, '标签（原 tags 表迁入，2026-09-12）'),
  ('tag', '交易体系/风险控制', '交易体系/风险控制', 0, 1, '标签（原 tags 表迁入，2026-09-12）'),
  ('tag', '分析框架/估值', '分析框架/估值', 0, 1, '标签（原 tags 表迁入，2026-09-12）'),
  ('tag', '分析框架/估值方法', '分析框架/估值方法', 0, 1, '标签（原 tags 表迁入，2026-09-12）'),
  ('tag', '分析框架/商业模式', '分析框架/商业模式', 0, 1, '标签（原 tags 表迁入，2026-09-12）'),
  ('tag', '分析框架/方法论', '分析框架/方法论', 0, 1, '标签（原 tags 表迁入，2026-09-12）'),
  ('tag', '分析框架/行业分析', '分析框架/行业分析', 0, 1, '标签（原 tags 表迁入，2026-09-12）'),
  ('tag', '市场/A股', '市场/A股', 0, 1, '标签（原 tags 表迁入，2026-09-12）'),
  ('tag', '市场/加密货币', '市场/加密货币', 0, 1, '标签（原 tags 表迁入，2026-09-12）'),
  ('tag', '市场/港股', '市场/港股', 0, 1, '标签（原 tags 表迁入，2026-09-12）'),
  ('tag', '市场/美股', '市场/美股', 0, 1, '标签（原 tags 表迁入，2026-09-12）'),
  ('tag', '投资心得/买卖决策', '投资心得/买卖决策', 0, 1, '标签（原 tags 表迁入，2026-09-12）'),
  ('tag', '投资心得/市场规律', '投资心得/市场规律', 0, 1, '标签（原 tags 表迁入，2026-09-12）'),
  ('tag', '投资心得/市场规律/周期特征', '投资心得/市场规律/周期特征', 0, 1, '标签（原 tags 表迁入，2026-09-12）'),
  ('tag', '投资心得/投资理念', '投资心得/投资理念', 0, 1, '标签（原 tags 表迁入，2026-09-12）'),
  ('tag', '投资心得/投资理念/回撤控制', '投资心得/投资理念/回撤控制', 0, 1, '标签（原 tags 表迁入，2026-09-12）'),
  ('tag', '投资心得/教训复盘', '投资心得/教训复盘', 0, 1, '标签（原 tags 表迁入，2026-09-12）'),
  ('tag', '投资心态/心态修炼/情绪管理', '投资心态/心态修炼/情绪管理', 0, 1, '标签（原 tags 表迁入，2026-09-12）'),
  ('tag', '投资心态/心态修炼/独立思考', '投资心态/心态修炼/独立思考', 0, 1, '标签（原 tags 表迁入，2026-09-12）'),
  ('tag', '投资心态/心态修炼/知行合一', '投资心态/心态修炼/知行合一', 0, 1, '标签（原 tags 表迁入，2026-09-12）'),
  ('tag', '投资心态/心态修炼/空仓心态', '投资心态/心态修炼/空仓心态', 0, 1, '标签（原 tags 表迁入，2026-09-12）'),
  ('tag', '投资心态/心态修炼/纪律', '投资心态/心态修炼/纪律', 0, 1, '标签（原 tags 表迁入，2026-09-12）'),
  ('tag', '投资心态/心态修炼/耐心', '投资心态/心态修炼/耐心', 0, 1, '标签（原 tags 表迁入，2026-09-12）'),
  ('tag', '投资心态/心态修炼/逆向思维', '投资心态/心态修炼/逆向思维', 0, 1, '标签（原 tags 表迁入，2026-09-12）'),
  ('tag', '投资心态/心态修炼/风险意识', '投资心态/心态修炼/风险意识', 0, 1, '标签（原 tags 表迁入，2026-09-12）'),
  ('tag', '投资心态/心理偏误/FOMO', '投资心态/心理偏误/FOMO', 0, 1, '标签（原 tags 表迁入，2026-09-12）'),
  ('tag', '投资心态/心理偏误/叙事谬误', '投资心态/心理偏误/叙事谬误', 0, 1, '标签（原 tags 表迁入，2026-09-12）'),
  ('tag', '投资心态/心理偏误/后视偏差', '投资心态/心理偏误/后视偏差', 0, 1, '标签（原 tags 表迁入，2026-09-12）'),
  ('tag', '投资心态/心理偏误/幸存者偏差', '投资心态/心理偏误/幸存者偏差', 0, 1, '标签（原 tags 表迁入，2026-09-12）'),
  ('tag', '投资心态/心理偏误/心理账户', '投资心态/心理偏误/心理账户', 0, 1, '标签（原 tags 表迁入，2026-09-12）'),
  ('tag', '投资心态/心理偏误/损失厌恶', '投资心态/心理偏误/损失厌恶', 0, 1, '标签（原 tags 表迁入，2026-09-12）'),
  ('tag', '投资心态/心理偏误/沉没成本', '投资心态/心理偏误/沉没成本', 0, 1, '标签（原 tags 表迁入，2026-09-12）'),
  ('tag', '投资心态/心理偏误/确认偏误', '投资心态/心理偏误/确认偏误', 0, 1, '标签（原 tags 表迁入，2026-09-12）'),
  ('tag', '投资心态/心理偏误/路径依赖', '投资心态/心理偏误/路径依赖', 0, 1, '标签（原 tags 表迁入，2026-09-12）'),
  ('tag', '投资心态/心理偏误/过度自信', '投资心态/心理偏误/过度自信', 0, 1, '标签（原 tags 表迁入，2026-09-12）'),
  ('tag', '投资心态/心理偏误/锚定效应', '投资心态/心理偏误/锚定效应', 0, 1, '标签（原 tags 表迁入，2026-09-12）'),
  ('tag', '投资心态/心理陷阱', '投资心态/心理陷阱', 0, 1, '标签（原 tags 表迁入，2026-09-12）'),
  ('tag', '投资心态/自我认知', '投资心态/自我认知', 0, 1, '标签（原 tags 表迁入，2026-09-12）'),
  ('tag', '投资心态/预期管理', '投资心态/预期管理', 0, 1, '标签（原 tags 表迁入，2026-09-12）'),
  ('tag', '行业/AI与算力', '行业/AI与算力', 0, 1, '标签（原 tags 表迁入，2026-09-12）'),
  ('tag', '行业/AI与算力/AI应用', '行业/AI与算力/AI应用', 0, 1, '标签（原 tags 表迁入，2026-09-12）'),
  ('tag', '行业/AI与算力/算力基础设施', '行业/AI与算力/算力基础设施', 0, 1, '标签（原 tags 表迁入，2026-09-12）'),
  ('tag', '行业/互联网', '行业/互联网', 0, 1, '标签（原 tags 表迁入，2026-09-12）'),
  ('tag', '行业/传媒/广告营销', '行业/传媒/广告营销', 0, 1, '标签（原 tags 表迁入，2026-09-12）'),
  ('tag', '行业/农林牧渔/养殖业', '行业/农林牧渔/养殖业', 0, 1, '标签（原 tags 表迁入，2026-09-12）'),
  ('tag', '行业/医药生物/创新药', '行业/医药生物/创新药', 0, 1, '标签（原 tags 表迁入，2026-09-12）'),
  ('tag', '行业/基础化工/农药', '行业/基础化工/农药', 0, 1, '标签（原 tags 表迁入，2026-09-12）'),
  ('tag', '行业/基础化工/化学原料', '行业/基础化工/化学原料', 0, 1, '标签（原 tags 表迁入，2026-09-12）'),
  ('tag', '行业/建筑材料/水泥', '行业/建筑材料/水泥', 0, 1, '标签（原 tags 表迁入，2026-09-12）'),
  ('tag', '行业/房地产/房地产服务', '行业/房地产/房地产服务', 0, 1, '标签（原 tags 表迁入，2026-09-12）'),
  ('tag', '行业/有色金属/工业金属', '行业/有色金属/工业金属', 0, 1, '标签（原 tags 表迁入，2026-09-12）'),
  ('tag', '行业/汽车', '行业/汽车', 0, 1, '标签（原 tags 表迁入，2026-09-12）'),
  ('tag', '行业/电力设备/电池', '行业/电力设备/电池', 0, 1, '标签（原 tags 表迁入，2026-09-12）'),
  ('tag', '行业/电子/半导体', '行业/电子/半导体', 0, 1, '标签（原 tags 表迁入，2026-09-12）'),
  ('tag', '行业/纺织服饰/饰品', '行业/纺织服饰/饰品', 0, 1, '标签（原 tags 表迁入，2026-09-12）'),
  ('tag', '行业/美容护理/个护用品', '行业/美容护理/个护用品', 0, 1, '标签（原 tags 表迁入，2026-09-12）'),
  ('tag', '行业/能源金属/锂', '行业/能源金属/锂', 0, 1, '标签（原 tags 表迁入，2026-09-12）'),
  ('tag', '行业/轻工制造/文娱用品', '行业/轻工制造/文娱用品', 0, 1, '标签（原 tags 表迁入，2026-09-12）'),
  ('tag', '行业/食品饮料', '行业/食品饮料', 0, 1, '标签（原 tags 表迁入，2026-09-12）'),
  ('tag', '行业/食品饮料/饮料乳品', '行业/食品饮料/饮料乳品', 0, 1, '标签（原 tags 表迁入，2026-09-12）'),
  ('target_relation', 'new', '新建', 1, 1, '库内无同类，新建条目'),
  ('target_relation', 'append', '追加', 2, 1, '追加到已有条目'),
  ('target_relation', 'complement', '互补', 3, 1, '与已有条目互补（同主题不同角度）'),
  ('target_relation', 'conflict_check', '矛盾预检', 4, 1, '写前矛盾预警'),
  ('target_relation', 'other', '其他', 5, 1, '无法归入上述主类型'),
  ('target_type', 'wiki', '框架条目', 1, 1, '六大分类 wiki 框架条目'),
  ('target_type', 'blogger', '博主画像', 2, 1, '博主画像/言论追踪'),
  ('target_type', 'macro', '宏观条目', 3, 1, '宏观层条目'),
  ('verify_result', 'correct', '正确', 1, 1, '方向正确即正确'),
  ('verify_result', 'wrong', '错误', 2, 1, '方向相反/关键数值未兑现'),
  ('verify_result', 'revoked', '已撤销', 3, 1, '撤销验证');

CREATE TABLE `blogger` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT COMMENT '自增主键',
  `name` varchar(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '博主名',
  `dir` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT 'vault 目录',
  `alias` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '别名',
  `xueqiu_id` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '雪球用户 id',
  `platform_code` varchar(32) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '平台，字典项 dict.type=platform',
  `is_special` tinyint(1) NOT NULL DEFAULT '0' COMMENT '是否特别关注',
  `summary` text CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci COMMENT '博主简介',
  `strengths` text CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci COMMENT '优势',
  `limitations` text CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci COMMENT '局限',
  `info_cutoff_datetime` datetime DEFAULT NULL COMMENT '信息截止时间',
  `file_count` int unsigned NOT NULL DEFAULT '0' COMMENT '产出文件数',
  `created_datetime` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_datetime` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `avatar` varchar(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '头像地址',
  `deleted_datetime` datetime DEFAULT NULL COMMENT '软删时间',
  `purged_datetime` datetime DEFAULT NULL COMMENT '回收完成时间（7 天冷静期到期、制品已挪到其他层、言论已逻辑删除）；空=未回收',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_name` (`name`),
  KEY `idx_platform` (`platform_code`),
  KEY `idx_special` (`is_special`)
) ENGINE=InnoDB AUTO_INCREMENT=124190 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='博主表';

CREATE TABLE `blogger_recycle_file` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT COMMENT '自增主键',
  `blogger_name` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '博主名',
  `original_rel` varchar(512) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '回收前的 vault 相对路径（博主层）',
  `moved_rel` varchar(512) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '回收后的 vault 相对路径（其他层）',
  `moved_datetime` datetime NOT NULL COMMENT '移动时间',
  `created_datetime` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  PRIMARY KEY (`id`),
  KEY `idx_blogger` (`blogger_name`),
  KEY `idx_moved` (`moved_rel`)
) ENGINE=InnoDB AUTO_INCREMENT=5 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='博主回收留痕表：制品从博主层挪到其他层的路径映射，恢复时据此搬回';

CREATE TABLE `console_stat_daily` (
  `stat_date` date NOT NULL COMMENT '统计日期＝言论内容时间 view_date（缺省回落帖子时间）',
  `dim_code` varchar(16) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '维度：stock/industry/index/market/blogger',
  `subject_id` bigint unsigned NOT NULL DEFAULT '0' COMMENT '主体 id（个股/行业/指数/市场/博主）；0＝该维度合计（按言论去重）',
  `subject_name` varchar(100) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT '' COMMENT '主体名（冗余，免 JOIN；维度合计为空）',
  `stance_group` varchar(8) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '方向档：bull 看多 / bear 看空 / neut 中性 / none 未定向',
  `stmt_count` int unsigned NOT NULL DEFAULT '0' COMMENT '言论条数',
  `blogger_count` int unsigned NOT NULL DEFAULT '0' COMMENT '涉及博主数（当天去重）',
  `trade_count` int unsigned NOT NULL DEFAULT '0' COMMENT '其中买卖条数',
  `created_datetime` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_datetime` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间（重算会刷新）',
  PRIMARY KEY (`stat_date`,`dim_code`,`subject_id`,`stance_group`),
  KEY `idx_dim_date` (`dim_code`,`stat_date`),
  KEY `idx_subject` (`dim_code`,`subject_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='看板统计表：T-1 预聚合，每日 03:10 由看板服务全量重算';

CREATE TABLE `hk_connect_member` (
  `snapshot_id` bigint unsigned NOT NULL COMMENT '所属快照 id，指向港股通名单快照表',
  `stock_code` varchar(16) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '港股代码（5 位数字）',
  `stock_name` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT '' COMMENT '名单里的证券简称',
  `created_datetime` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  PRIMARY KEY (`snapshot_id`,`stock_code`),
  KEY `idx_code` (`stock_code`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='港股通名单成员表：一条=该快照里的一只标的';

CREATE TABLE `hk_connect_snapshot` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT COMMENT '自增主键',
  `source_code` varchar(32) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '名单来源，字典项 dict.type=hk_connect_source',
  `member_count` int NOT NULL COMMENT '本快照成员数（完整性校验用：过小说明抓取被截断）',
  `is_current` tinyint(1) NOT NULL DEFAULT '0' COMMENT '是否当前生效快照：1 是 0 否',
  `fetched_datetime` datetime NOT NULL COMMENT '名单的抓取时间（判断是否过期用这个）',
  `created_datetime` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  PRIMARY KEY (`id`),
  KEY `idx_current` (`is_current`),
  KEY `idx_fetched` (`fetched_datetime`)
) ENGINE=InnoDB AUTO_INCREMENT=2 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='港股通名单快照表：每次抓取一条，保留历史便于比对增减';

CREATE TABLE `index_catalog` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT COMMENT '自增主键',
  `code` varchar(32) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '指数代码（如 000300/930713/HSI），待补时为 NULL',
  `name` varchar(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '指数名称（标准名，如 沪深300/中证人工智能主题指数）',
  `category` varchar(32) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '类别：宽基/行业/主题/策略/风格/属性/债券/跨境',
  `issuer` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '编制/发布机构，如 中证指数公司/国证指数公司/恒生指数公司',
  `publish_date` varchar(32) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '发布日期（源表原文，含「—（2023 年发布）」这类说明）',
  `sample_count` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '成分数量（源表原文，如 300/全部沪市上市证券）',
  `aliases` varchar(512) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '别名，逗号分隔（用于名称匹配，如 创业板指,创指）',
  `remark` varchar(1000) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '说明/关键信息（源表备注原文）',
  `sort_order` int NOT NULL DEFAULT '0' COMMENT '排序（源表出现顺序）',
  `created_datetime` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_datetime` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_name` (`name`),
  KEY `idx_code` (`code`),
  KEY `idx_category` (`category`)
) ENGINE=InnoDB AUTO_INCREMENT=289 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='指数目录表：指数实体必须命中本表（名称/别名/代码），禁止自由文本新增';

CREATE TABLE `industry` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT COMMENT '自增主键',
  `name` varchar(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '行业名称',
  `created_datetime` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_datetime` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `sw_code` varchar(16) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '对应申万行业代码，指向 industry_sw.code；NULL=非申万标准名',
  `sw_level` tinyint DEFAULT NULL COMMENT '申万层级：1=一级 2=二级 3=三级；NULL=未匹配',
  `source` varchar(16) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'legacy' COMMENT '来源：sw=申万标准名，legacy=历史自由文本（已冻结，禁止再新增）',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_name` (`name`)
) ENGINE=InnoDB AUTO_INCREMENT=345 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='行业表';

CREATE TABLE `industry_follow` (
  `industry_id` bigint unsigned NOT NULL COMMENT '行业 id，指向 industry.id',
  `created_datetime` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '关注时间',
  PRIMARY KEY (`industry_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='关注行业（行业维度「关注」筛选的数据源）';

CREATE TABLE `industry_sw` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT COMMENT '自增主键',
  `code` varchar(16) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '申万行业代码；社交/本地生活服务/其他银行 为 NULL（官方未发布对应指数）',
  `name` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '行业名称（保留 Ⅱ/Ⅲ 后缀，与官方口径一致）',
  `short_name` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '规范名：去掉 Ⅱ/Ⅲ 后缀，用于名称匹配与展示',
  `level` tinyint NOT NULL COMMENT '层级：1=一级 2=二级 3=三级',
  `parent_code` varchar(16) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '上级行业代码，指向本表 code；一级为 NULL',
  `parent_name` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '上级行业名称；一级为 NULL',
  `l1_name` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '所属一级行业名；一级行=自身名',
  `l2_name` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '所属二级行业名；仅三级行填写',
  `sort_order` int NOT NULL DEFAULT '0' COMMENT '排序（标准表原始顺序，自上而下）',
  `created_datetime` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_datetime` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_name_level` (`short_name`,`level`),
  KEY `idx_code` (`code`),
  KEY `idx_parent` (`parent_code`),
  KEY `idx_l1` (`l1_name`),
  KEY `idx_l2` (`l2_name`)
) ENGINE=InnoDB AUTO_INCREMENT=2501 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='申万行业分类标准表（2021版）：行业实体必须命中本表，禁止自由文本新增';

CREATE TABLE `market` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT COMMENT '自增主键',
  `code` varchar(16) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '市场码：a/hk/us/kr…',
  `name` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '市场名称：A股/港股/美股/韩股',
  `sort_order` int NOT NULL DEFAULT '0' COMMENT '排序',
  `created_datetime` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_datetime` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_code` (`code`),
  UNIQUE KEY `uq_name` (`name`)
) ENGINE=InnoDB AUTO_INCREMENT=58 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='市场表';

CREATE TABLE `market_index` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT COMMENT '自增主键',
  `name` varchar(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '指数名称，如 创业板/沪深300/恒生科技',
  `code` varchar(32) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '指数代码，如 399006；待补时留空',
  `aliases` varchar(512) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '别名，逗号分隔，如 创业板指,创指',
  `keywords` varchar(512) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '补充关键词，逗号分隔（提及判定的同句共现用）',
  `created_datetime` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_datetime` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `catalog_id` bigint unsigned DEFAULT NULL COMMENT '对应指数目录 id，指向 index_catalog.id；NULL=未匹配',
  `source` varchar(16) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'legacy' COMMENT '来源：catalog=指数目录，legacy=历史自由文本（已冻结，禁止再新增）',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_name` (`name`),
  KEY `idx_code` (`code`)
) ENGINE=InnoDB AUTO_INCREMENT=3 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='指数表';

CREATE TABLE `mention_case` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT COMMENT '自增主键',
  `case_sentence` varchar(512) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '待判定的原文片段（一句/一小句）',
  `case_alias` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '句中出现的称呼/产品词',
  `stock_id` bigint unsigned DEFAULT NULL COMMENT '正确归属的个股 id，指向个股表',
  `stock_name` varchar(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '个股名快照',
  `verdict_code` varchar(16) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '正确判定：link=应建关联 / doubt=存疑 / skip=不是个股',
  `case_reason` varchar(512) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '判定理由（命中/否决了哪条规则）',
  `source_code` varchar(16) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'refine' COMMENT '来源：refine 提炼 / review 审查 / user 用户复核 / seed 初始',
  `created_datetime` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  PRIMARY KEY (`id`),
  KEY `idx_alias` (`case_alias`),
  KEY `idx_verdict` (`verdict_code`)
) ENGINE=InnoDB AUTO_INCREMENT=15 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='提及判定案例表';

CREATE TABLE `pending_decision` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT COMMENT '自增主键',
  `kind_code` varchar(32) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT '其他' COMMENT '问题类型：归类待定/标的名解析不出/称呼歧义/时间存疑/数据缺口/规则待定/系统标记/其他',
  `statement_id` bigint unsigned DEFAULT NULL COMMENT '关联的言论 id（可空：整帖/采集级问题没有单条言论）',
  `blogger_name` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT '' COMMENT '博主名（便于列表归类）',
  `source_url` varchar(512) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT '' COMMENT '原文链接（用户点开核对）',
  `excerpt` text CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci COMMENT '原文片段：用户凭这一段判断，不用去翻原文',
  `question` varchar(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '需要用户裁决的问题（一句话，必须能独立看懂）',
  `options` varchar(512) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT '' COMMENT '候选答案，/ 分隔；空＝自由作答。agent 必须尽量给候选，让用户点一下就行',
  `answer` varchar(1000) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT '' COMMENT '用户答复（选项原文或自由文本）',
  `verdict_code` varchar(16) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT '' COMMENT '用户答复性质：delete=建议删除(必附理由) / fix=修正 / keep=保留 / 空=未分类',
  `status_code` varchar(16) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'open' COMMENT 'open=待裁决 / resolved=已答复 / dismissed=忽略',
  `internalized` varchar(512) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT '' COMMENT '内化落点（规则编号/别名/案例/文件）：非空＝同类帖子以后不用再问人',
  `raised_by_code` varchar(32) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'agent' COMMENT '上报方：agent / system（自动校验）/ user',
  `created_datetime` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `resolved_datetime` datetime DEFAULT NULL COMMENT '答复/忽略时间',
  PRIMARY KEY (`id`),
  KEY `idx_status` (`status_code`),
  KEY `idx_kind` (`kind_code`),
  KEY `idx_stmt` (`statement_id`),
  KEY `idx_created` (`created_datetime`)
) ENGINE=InnoDB AUTO_INCREMENT=70 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='待决策队列：agent 处理不了或用户打回的问题，裁决后内化成规则';

CREATE TABLE `post_history` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT COMMENT '自增主键',
  `blogger_id` bigint unsigned NOT NULL COMMENT '博主 id，指向博主表',
  `blogger_name` varchar(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '博主名',
  `platform_post_id` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '平台内帖子 id',
  `source_url` varchar(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '原文链接',
  `url_hash` char(32) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '原文链接的 md5，唯一键',
  `post_title` varchar(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '标题',
  `post_text` mediumtext CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '采集到的原文全文',
  `post_text_length` int unsigned NOT NULL DEFAULT '0' COMMENT '原文长度',
  `post_form` varchar(10) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '帖子形态',
  `posted_datetime` datetime NOT NULL COMMENT '发帖时间',
  `fetched_datetime` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '采集时间',
  `content_hash` char(32) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '原文内容的 md5，重采时比对',
  `reply_count` int unsigned DEFAULT NULL COMMENT '回复数',
  `retweet_count` int unsigned DEFAULT NULL COMMENT '转发数',
  `like_count` int unsigned DEFAULT NULL COMMENT '点赞数',
  `created_datetime` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_datetime` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `refine_status` tinyint NOT NULL DEFAULT '0' COMMENT '提炼状态 0=待提炼 1=已提炼(处理过，无论是否落言论)',
  `no_value` tinyint NOT NULL DEFAULT '0' COMMENT '提炼后判定无价值、不落言论：0=否/未定 1=无价值(仅 refine_status=1 时有意义)',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_url` (`url_hash`),
  UNIQUE KEY `uk_blogger_post` (`blogger_id`,`platform_post_id`),
  KEY `idx_blogger_time` (`blogger_id`,`posted_datetime`),
  KEY `idx_posted` (`posted_datetime`),
  KEY `idx_ph_refine_status` (`refine_status`,`blogger_name`,`posted_datetime`)
) ENGINE=InnoDB AUTO_INCREMENT=3628 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='帖子原文表';

CREATE TABLE `quote` (
  `id` int NOT NULL AUTO_INCREMENT COMMENT '自增主键',
  `sort_order` int NOT NULL COMMENT '轮播顺序',
  `quote_text` varchar(300) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '语录内容',
  `created_datetime` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `seq` (`sort_order`)
) ENGINE=InnoDB AUTO_INCREMENT=61 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='首页语录表';

CREATE TABLE `refine_item` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT COMMENT '自增主键',
  `chain_code` varchar(16) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '链路，字典项 dict.type=refine_chain',
  `batch_key` varchar(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT '' COMMENT '批次分组键（原 refine_record.from_rel，如 post_history/{博主} {采集日}）',
  `source_kind_code` varchar(16) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '来源种类，字典项 dict.type=refine_source_kind',
  `source_rel` varchar(512) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT '' COMMENT 'vault 相对路径（粗制品/原始资源链路）',
  `post_history_id` bigint unsigned DEFAULT NULL COMMENT '帖子链路：post_history 原文留档 id',
  `blogger_name` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT '' COMMENT '博主名',
  `statement_id` bigint unsigned DEFAULT NULL COMMENT '帖子链路的产物：言论 id',
  `created_datetime` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_statement` (`statement_id`),
  KEY `idx_statement` (`statement_id`),
  KEY `idx_batch` (`batch_key`),
  KEY `idx_created` (`created_datetime`)
) ENGINE=InnoDB AUTO_INCREMENT=26 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='提炼单元表：一个来源走一条链路算一个单元';

CREATE TABLE `review_check_sub` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT COMMENT '自增主键',
  `review_id` bigint unsigned NOT NULL COMMENT '审查记录 id，指向审查记录表',
  `item_name` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '检查项',
  `result` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '检查结果',
  `compare` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '对比说明',
  `status_code` varchar(32) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '检查状态，字典项 dict.type=check_status',
  `created_datetime` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  PRIMARY KEY (`id`),
  KEY `idx_review` (`review_id`),
  KEY `idx_status` (`status_code`),
  CONSTRAINT `fk_rc_review` FOREIGN KEY (`review_id`) REFERENCES `review_record` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB AUTO_INCREMENT=329 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='审查检查项子表';

CREATE TABLE `review_record` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT COMMENT '自增主键',
  `review_date` date NOT NULL COMMENT '审查日期',
  `review_title` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '审查标题',
  `review_method` text CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci COMMENT '审查方式',
  `review_meta` json DEFAULT NULL COMMENT '元信息',
  `review_main_problems` json DEFAULT NULL COMMENT '主要问题',
  `review_groups` json DEFAULT NULL COMMENT '分组结果',
  `review_summary` text CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci COMMENT '审查小结',
  `review_recycle` json DEFAULT NULL COMMENT '回收项',
  `review_actions` json DEFAULT NULL COMMENT '后续动作',
  `saved_datetime` datetime DEFAULT NULL COMMENT '保存时间',
  `created_datetime` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  PRIMARY KEY (`id`),
  KEY `idx_date` (`review_date`)
) ENGINE=InnoDB AUTO_INCREMENT=15 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='审查记录表';

CREATE ALGORITHM=UNDEFINED DEFINER=`jianglb`@`%` SQL SECURITY DEFINER VIEW `statement` AS select `statement_research`.`id` AS `id`,`statement_research`.`blogger_name` AS `blogger_name`,`statement_research`.`statement_datetime` AS `statement_datetime`,`statement_research`.`view_date` AS `view_date`,`statement_research`.`view_date_source` AS `view_date_source`,`statement_research`.`view_date_precision` AS `view_date_precision`,`statement_research`.`view_date_basis` AS `view_date_basis`,`statement_research`.`stance_code` AS `stance_code`,`statement_research`.`view_text` AS `view_text`,`statement_research`.`signal_text` AS `signal_text`,`statement_research`.`source_batch` AS `source_batch`,`statement_research`.`source_url` AS `source_url`,`statement_research`.`blogger_id` AS `blogger_id`,`statement_research`.`post_history_id` AS `post_history_id`,`statement_research`.`is_review_required` AS `is_review_required`,`statement_research`.`dedup_key` AS `dedup_key`,`statement_research`.`created_datetime` AS `created_datetime`,`statement_research`.`updated_datetime` AS `updated_datetime`,`statement_research`.`post_form` AS `post_form`,`statement_research`.`wiki_ref` AS `wiki_ref`,`statement_research`.`reply_to` AS `reply_to`,`statement_research`.`is_read` AS `is_read`,`statement_research`.`is_starred` AS `is_starred`,`statement_research`.`del_flag` AS `del_flag`,`statement_research`.`deleted_datetime` AS `deleted_datetime`,`statement_research`.`fetched_datetime` AS `fetched_datetime`,NULL AS `ref_price`,NULL AS `target_price`,NULL AS `target_date`,NULL AS `date_precision`,NULL AS `verify_date`,NULL AS `verify_result`,NULL AS `status_code`,`statement_research`.`data_refs` AS `data_refs`,NULL AS `price`,NULL AS `market_cap`,NULL AS `trade_date`,NULL AS `trade_note`,'research' AS `content_type` from `statement_research` where (`statement_research`.`del_flag` = 0) union all select `statement_predict`.`id` AS `id`,`statement_predict`.`blogger_name` AS `blogger_name`,`statement_predict`.`statement_datetime` AS `statement_datetime`,`statement_predict`.`view_date` AS `view_date`,`statement_predict`.`view_date_source` AS `view_date_source`,`statement_predict`.`view_date_precision` AS `view_date_precision`,`statement_predict`.`view_date_basis` AS `view_date_basis`,`statement_predict`.`stance_code` AS `stance_code`,`statement_predict`.`view_text` AS `view_text`,`statement_predict`.`signal_text` AS `signal_text`,`statement_predict`.`source_batch` AS `source_batch`,`statement_predict`.`source_url` AS `source_url`,`statement_predict`.`blogger_id` AS `blogger_id`,`statement_predict`.`post_history_id` AS `post_history_id`,`statement_predict`.`is_review_required` AS `is_review_required`,`statement_predict`.`dedup_key` AS `dedup_key`,`statement_predict`.`created_datetime` AS `created_datetime`,`statement_predict`.`updated_datetime` AS `updated_datetime`,`statement_predict`.`post_form` AS `post_form`,`statement_predict`.`wiki_ref` AS `wiki_ref`,`statement_predict`.`reply_to` AS `reply_to`,`statement_predict`.`is_read` AS `is_read`,`statement_predict`.`is_starred` AS `is_starred`,`statement_predict`.`del_flag` AS `del_flag`,`statement_predict`.`deleted_datetime` AS `deleted_datetime`,`statement_predict`.`fetched_datetime` AS `fetched_datetime`,`statement_predict`.`ref_price` AS `ref_price`,`statement_predict`.`target_price` AS `target_price`,`statement_predict`.`target_date` AS `target_date`,`statement_predict`.`date_precision` AS `date_precision`,`statement_predict`.`verify_date` AS `verify_date`,`statement_predict`.`verify_result` AS `verify_result`,`statement_predict`.`status_code` AS `status_code`,NULL AS `data_refs`,NULL AS `price`,NULL AS `market_cap`,NULL AS `trade_date`,NULL AS `trade_note`,'predict' AS `content_type` from `statement_predict` where (`statement_predict`.`del_flag` = 0) union all select `statement_view`.`id` AS `id`,`statement_view`.`blogger_name` AS `blogger_name`,`statement_view`.`statement_datetime` AS `statement_datetime`,`statement_view`.`view_date` AS `view_date`,`statement_view`.`view_date_source` AS `view_date_source`,`statement_view`.`view_date_precision` AS `view_date_precision`,`statement_view`.`view_date_basis` AS `view_date_basis`,`statement_view`.`stance_code` AS `stance_code`,`statement_view`.`view_text` AS `view_text`,`statement_view`.`signal_text` AS `signal_text`,`statement_view`.`source_batch` AS `source_batch`,`statement_view`.`source_url` AS `source_url`,`statement_view`.`blogger_id` AS `blogger_id`,`statement_view`.`post_history_id` AS `post_history_id`,`statement_view`.`is_review_required` AS `is_review_required`,`statement_view`.`dedup_key` AS `dedup_key`,`statement_view`.`created_datetime` AS `created_datetime`,`statement_view`.`updated_datetime` AS `updated_datetime`,`statement_view`.`post_form` AS `post_form`,`statement_view`.`wiki_ref` AS `wiki_ref`,`statement_view`.`reply_to` AS `reply_to`,`statement_view`.`is_read` AS `is_read`,`statement_view`.`is_starred` AS `is_starred`,`statement_view`.`del_flag` AS `del_flag`,`statement_view`.`deleted_datetime` AS `deleted_datetime`,`statement_view`.`fetched_datetime` AS `fetched_datetime`,NULL AS `ref_price`,NULL AS `target_price`,NULL AS `target_date`,NULL AS `date_precision`,NULL AS `verify_date`,NULL AS `verify_result`,NULL AS `status_code`,NULL AS `data_refs`,NULL AS `price`,NULL AS `market_cap`,NULL AS `trade_date`,NULL AS `trade_note`,'view' AS `content_type` from `statement_view` where (`statement_view`.`del_flag` = 0) union all select `statement_insight`.`id` AS `id`,`statement_insight`.`blogger_name` AS `blogger_name`,`statement_insight`.`statement_datetime` AS `statement_datetime`,`statement_insight`.`view_date` AS `view_date`,`statement_insight`.`view_date_source` AS `view_date_source`,`statement_insight`.`view_date_precision` AS `view_date_precision`,`statement_insight`.`view_date_basis` AS `view_date_basis`,`statement_insight`.`stance_code` AS `stance_code`,`statement_insight`.`view_text` AS `view_text`,`statement_insight`.`signal_text` AS `signal_text`,`statement_insight`.`source_batch` AS `source_batch`,`statement_insight`.`source_url` AS `source_url`,`statement_insight`.`blogger_id` AS `blogger_id`,`statement_insight`.`post_history_id` AS `post_history_id`,`statement_insight`.`is_review_required` AS `is_review_required`,`statement_insight`.`dedup_key` AS `dedup_key`,`statement_insight`.`created_datetime` AS `created_datetime`,`statement_insight`.`updated_datetime` AS `updated_datetime`,`statement_insight`.`post_form` AS `post_form`,`statement_insight`.`wiki_ref` AS `wiki_ref`,`statement_insight`.`reply_to` AS `reply_to`,`statement_insight`.`is_read` AS `is_read`,`statement_insight`.`is_starred` AS `is_starred`,`statement_insight`.`del_flag` AS `del_flag`,`statement_insight`.`deleted_datetime` AS `deleted_datetime`,`statement_insight`.`fetched_datetime` AS `fetched_datetime`,NULL AS `ref_price`,NULL AS `target_price`,NULL AS `target_date`,NULL AS `date_precision`,NULL AS `verify_date`,NULL AS `verify_result`,NULL AS `status_code`,NULL AS `data_refs`,NULL AS `price`,NULL AS `market_cap`,NULL AS `trade_date`,NULL AS `trade_note`,'insight' AS `content_type` from `statement_insight` where (`statement_insight`.`del_flag` = 0) union all select `statement_chat`.`id` AS `id`,`statement_chat`.`blogger_name` AS `blogger_name`,`statement_chat`.`statement_datetime` AS `statement_datetime`,`statement_chat`.`view_date` AS `view_date`,`statement_chat`.`view_date_source` AS `view_date_source`,`statement_chat`.`view_date_precision` AS `view_date_precision`,`statement_chat`.`view_date_basis` AS `view_date_basis`,`statement_chat`.`stance_code` AS `stance_code`,`statement_chat`.`view_text` AS `view_text`,`statement_chat`.`signal_text` AS `signal_text`,`statement_chat`.`source_batch` AS `source_batch`,`statement_chat`.`source_url` AS `source_url`,`statement_chat`.`blogger_id` AS `blogger_id`,`statement_chat`.`post_history_id` AS `post_history_id`,`statement_chat`.`is_review_required` AS `is_review_required`,`statement_chat`.`dedup_key` AS `dedup_key`,`statement_chat`.`created_datetime` AS `created_datetime`,`statement_chat`.`updated_datetime` AS `updated_datetime`,`statement_chat`.`post_form` AS `post_form`,`statement_chat`.`wiki_ref` AS `wiki_ref`,`statement_chat`.`reply_to` AS `reply_to`,`statement_chat`.`is_read` AS `is_read`,`statement_chat`.`is_starred` AS `is_starred`,`statement_chat`.`del_flag` AS `del_flag`,`statement_chat`.`deleted_datetime` AS `deleted_datetime`,`statement_chat`.`fetched_datetime` AS `fetched_datetime`,NULL AS `ref_price`,NULL AS `target_price`,NULL AS `target_date`,NULL AS `date_precision`,NULL AS `verify_date`,NULL AS `verify_result`,NULL AS `status_code`,NULL AS `data_refs`,NULL AS `price`,NULL AS `market_cap`,NULL AS `trade_date`,NULL AS `trade_note`,'chat' AS `content_type` from `statement_chat` where (`statement_chat`.`del_flag` = 0) union all select `statement_trade`.`id` AS `id`,`statement_trade`.`blogger_name` AS `blogger_name`,`statement_trade`.`statement_datetime` AS `statement_datetime`,`statement_trade`.`view_date` AS `view_date`,`statement_trade`.`view_date_source` AS `view_date_source`,`statement_trade`.`view_date_precision` AS `view_date_precision`,`statement_trade`.`view_date_basis` AS `view_date_basis`,`statement_trade`.`stance_code` AS `stance_code`,`statement_trade`.`view_text` AS `view_text`,`statement_trade`.`signal_text` AS `signal_text`,`statement_trade`.`source_batch` AS `source_batch`,`statement_trade`.`source_url` AS `source_url`,`statement_trade`.`blogger_id` AS `blogger_id`,`statement_trade`.`post_history_id` AS `post_history_id`,`statement_trade`.`is_review_required` AS `is_review_required`,`statement_trade`.`dedup_key` AS `dedup_key`,`statement_trade`.`created_datetime` AS `created_datetime`,`statement_trade`.`updated_datetime` AS `updated_datetime`,`statement_trade`.`post_form` AS `post_form`,`statement_trade`.`wiki_ref` AS `wiki_ref`,`statement_trade`.`reply_to` AS `reply_to`,`statement_trade`.`is_read` AS `is_read`,`statement_trade`.`is_starred` AS `is_starred`,`statement_trade`.`del_flag` AS `del_flag`,`statement_trade`.`deleted_datetime` AS `deleted_datetime`,`statement_trade`.`fetched_datetime` AS `fetched_datetime`,NULL AS `ref_price`,NULL AS `target_price`,NULL AS `target_date`,NULL AS `date_precision`,NULL AS `verify_date`,NULL AS `verify_result`,NULL AS `status_code`,NULL AS `data_refs`,`statement_trade`.`price` AS `price`,`statement_trade`.`market_cap` AS `market_cap`,`statement_trade`.`trade_date` AS `trade_date`,`statement_trade`.`trade_note` AS `trade_note`,'trade' AS `content_type` from `statement_trade` where (`statement_trade`.`del_flag` = 0);

CREATE TABLE `statement_blogger_rel` (
  `statement_id` bigint unsigned NOT NULL COMMENT '言论 id，指向六张言论表之一',
  `blogger_id` bigint unsigned NOT NULL COMMENT '博主 id，指向博主表',
  `blogger_name` varchar(100) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '博主名快照',
  `created_datetime` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  PRIMARY KEY (`statement_id`,`blogger_id`),
  KEY `idx_blogger` (`blogger_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='言论博主关联表';

CREATE TABLE `statement_chat` (
  `id` bigint unsigned NOT NULL COMMENT '帖子 id，六张帖子表全局唯一',
  `blogger_name` varchar(100) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '博主名，冗余自博主表',
  `statement_datetime` datetime NOT NULL COMMENT '帖子时间（原帖发布时间）',
  `view_date` date DEFAULT NULL COMMENT '观点时间，判断成立时点',
  `view_date_source` varchar(12) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '观点时间来源：as_posted/explicit/derived',
  `view_date_precision` varchar(8) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '观点时间精度：day/month/year',
  `view_date_basis` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '观点时间推算依据，原文摘录',
  `stance_code` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '信号方向，字典项 dict.type=stance',
  `view_text` text CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci COMMENT '正文',
  `signal_text` text CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci COMMENT '信号内容，简短判断',
  `source_batch` varchar(200) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT '' COMMENT '来源批次或平台',
  `source_url` varchar(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT '' COMMENT '原文链接',
  `blogger_id` bigint unsigned DEFAULT NULL COMMENT '博主 id，指向博主表',
  `post_history_id` bigint unsigned DEFAULT NULL COMMENT '指向 post_history.id（帖子层原文留档）',
  `is_review_required` tinyint(1) NOT NULL DEFAULT '0' COMMENT '是否待复核',
  `dedup_key` char(32) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '去重键',
  `created_datetime` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_datetime` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `post_form` varchar(10) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '帖子形态：回复/短文/长文/专栏',
  `wiki_ref` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '已具象化的框架条目文件路径',
  `reply_to` text CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci COMMENT '被回应者的原话（原样照抄，不提炼）',
  `is_read` tinyint NOT NULL DEFAULT '0' COMMENT '是否已读：0=待读（用户在页面划过即置 1）；新言论默认 1',
  `is_starred` tinyint NOT NULL DEFAULT '0' COMMENT '星标/关注（1=已关注，用户手动标记）',
  `del_flag` tinyint(1) NOT NULL DEFAULT '0' COMMENT '删除标记，字典项 dict.type=del_flag：0=未删除 1=已删除（逻辑删除，视图会过滤掉）',
  `deleted_datetime` datetime DEFAULT NULL COMMENT '逻辑删除时间（del_flag=1 时写入；恢复时清空）',
  `fetched_datetime` datetime DEFAULT NULL COMMENT '采集时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_dedup` (`dedup_key`),
  KEY `idx_blogger_date` (`blogger_name`,`view_date`),
  KEY `idx_review` (`is_review_required`),
  KEY `idx_is_read` (`is_read`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='闲聊帖子表';

CREATE TABLE `statement_id_seq` (
  `name` varchar(32) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '序列名',
  `id` bigint unsigned NOT NULL COMMENT '已发放的最大帖子 id',
  PRIMARY KEY (`name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='帖子 id 序列表';

CREATE TABLE `statement_industry_rel` (
  `statement_id` bigint unsigned NOT NULL COMMENT '言论 id，指向六张言论表之一',
  `industry_id` bigint unsigned NOT NULL COMMENT '行业 id，指向行业表',
  `role_code` varchar(16) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'subject' COMMENT '角色：subject 主体，mention 文中提及',
  `created_datetime` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  PRIMARY KEY (`statement_id`,`industry_id`),
  KEY `idx_industry` (`industry_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='言论行业关联表';

CREATE TABLE `statement_insight` (
  `id` bigint unsigned NOT NULL COMMENT '帖子 id，六张帖子表全局唯一',
  `blogger_name` varchar(100) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '博主名，冗余自博主表',
  `statement_datetime` datetime NOT NULL COMMENT '帖子时间（原帖发布时间）',
  `view_date` date DEFAULT NULL COMMENT '观点时间，判断成立时点',
  `view_date_source` varchar(12) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '观点时间来源：as_posted/explicit/derived',
  `view_date_precision` varchar(8) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '观点时间精度：day/month/year',
  `view_date_basis` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '观点时间推算依据，原文摘录',
  `stance_code` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '信号方向，字典项 dict.type=stance',
  `view_text` text CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci COMMENT '正文',
  `signal_text` text CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci COMMENT '信号内容，简短判断',
  `source_batch` varchar(200) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT '' COMMENT '来源批次或平台',
  `source_url` varchar(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT '' COMMENT '原文链接',
  `blogger_id` bigint unsigned DEFAULT NULL COMMENT '博主 id，指向博主表',
  `post_history_id` bigint unsigned DEFAULT NULL COMMENT '指向 post_history.id（帖子层原文留档）',
  `is_review_required` tinyint(1) NOT NULL DEFAULT '0' COMMENT '是否待复核',
  `dedup_key` char(32) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '去重键',
  `created_datetime` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_datetime` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `post_form` varchar(10) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '帖子形态：回复/短文/长文/专栏',
  `wiki_ref` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '已具象化的框架条目文件路径',
  `reply_to` text CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci COMMENT '被回应者的原话（原样照抄，不提炼）',
  `is_read` tinyint NOT NULL DEFAULT '0' COMMENT '是否已读：0=待读（用户在页面划过即置 1）；新言论默认 1',
  `is_starred` tinyint NOT NULL DEFAULT '0' COMMENT '星标/关注（1=已关注，用户手动标记）',
  `del_flag` tinyint(1) NOT NULL DEFAULT '0' COMMENT '删除标记，字典项 dict.type=del_flag：0=未删除 1=已删除（逻辑删除，视图会过滤掉）',
  `deleted_datetime` datetime DEFAULT NULL COMMENT '逻辑删除时间（del_flag=1 时写入；恢复时清空）',
  `fetched_datetime` datetime DEFAULT NULL COMMENT '采集时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_dedup` (`dedup_key`),
  KEY `idx_blogger_date` (`blogger_name`,`view_date`),
  KEY `idx_review` (`is_review_required`),
  KEY `idx_is_read` (`is_read`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='心得帖子表';

CREATE TABLE `statement_market_index_rel` (
  `statement_id` bigint unsigned NOT NULL COMMENT '言论 id，指向六张言论表之一',
  `market_index_id` bigint unsigned NOT NULL COMMENT '指数 id，指向指数表',
  `role_code` varchar(16) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'subject' COMMENT '角色，字典项 dict.type=rel_role：subject 主体，mention 文中提及',
  `created_datetime` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  PRIMARY KEY (`statement_id`,`market_index_id`),
  KEY `idx_market_index` (`market_index_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='言论指数关联表';

CREATE TABLE `statement_market_rel` (
  `statement_id` bigint unsigned NOT NULL COMMENT '言论 id，指向六张言论表之一',
  `market_id` bigint unsigned NOT NULL COMMENT '市场 id，指向市场表',
  `role_code` varchar(16) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'subject' COMMENT '角色：subject 主体，mention 文中提及',
  `created_datetime` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  PRIMARY KEY (`statement_id`,`market_id`),
  KEY `idx_market` (`market_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='言论市场关联表';

CREATE TABLE `statement_predict` (
  `id` bigint unsigned NOT NULL COMMENT '帖子 id，六张帖子表全局唯一',
  `blogger_name` varchar(100) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '博主名，冗余自博主表',
  `statement_datetime` datetime NOT NULL COMMENT '帖子时间（原帖发布时间）',
  `view_date` date DEFAULT NULL COMMENT '观点时间，判断成立时点',
  `view_date_source` varchar(12) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '观点时间来源：as_posted/explicit/derived',
  `view_date_precision` varchar(8) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '观点时间精度：day/month/year',
  `view_date_basis` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '观点时间推算依据，原文摘录',
  `stance_code` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '信号方向，字典项 dict.type=stance',
  `view_text` text CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci COMMENT '正文',
  `signal_text` text CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci COMMENT '信号内容，简短判断',
  `source_batch` varchar(200) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT '' COMMENT '来源批次或平台',
  `source_url` varchar(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT '' COMMENT '原文链接',
  `blogger_id` bigint unsigned DEFAULT NULL COMMENT '博主 id，指向博主表',
  `post_history_id` bigint unsigned DEFAULT NULL COMMENT '指向 post_history.id（帖子层原文留档）',
  `is_review_required` tinyint(1) NOT NULL DEFAULT '0' COMMENT '是否待复核',
  `dedup_key` char(32) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '去重键',
  `created_datetime` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_datetime` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `post_form` varchar(10) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '帖子形态：回复/短文/长文/专栏',
  `ref_price` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '判断时参考价',
  `target_price` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '目标价',
  `target_date` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '目标时间',
  `date_precision` varchar(10) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '目标时间精度：day/month/year',
  `verify_date` date DEFAULT NULL COMMENT '最近验证日期',
  `verify_result` varchar(12) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '最近验证结果，字典项 dict.type=verify_result',
  `wiki_ref` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '已具象化的框架条目文件路径',
  `reply_to` text CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci COMMENT '被回应者的原话（原样照抄，不提炼）',
  `is_read` tinyint NOT NULL DEFAULT '0' COMMENT '是否已读：0=待读（用户在页面划过即置 1）；新言论默认 1',
  `is_starred` tinyint NOT NULL DEFAULT '0' COMMENT '星标/关注（1=已关注，用户手动标记）',
  `del_flag` tinyint(1) NOT NULL DEFAULT '0' COMMENT '删除标记，字典项 dict.type=del_flag：0=未删除 1=已删除（逻辑删除，视图会过滤掉）',
  `deleted_datetime` datetime DEFAULT NULL COMMENT '逻辑删除时间（del_flag=1 时写入；恢复时清空）',
  `fetched_datetime` datetime DEFAULT NULL COMMENT '采集时间',
  `status_code` varchar(32) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '预测状态，字典项 dict.type=prediction_status',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_dedup` (`dedup_key`),
  KEY `idx_blogger_date` (`blogger_name`,`view_date`),
  KEY `idx_review` (`is_review_required`),
  KEY `idx_is_read` (`is_read`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='预测帖子表';

CREATE TABLE `statement_research` (
  `id` bigint unsigned NOT NULL COMMENT '帖子 id，六张帖子表全局唯一',
  `blogger_name` varchar(100) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '博主名，冗余自博主表',
  `statement_datetime` datetime NOT NULL COMMENT '帖子时间（原帖发布时间）',
  `view_date` date DEFAULT NULL COMMENT '观点时间，判断成立时点',
  `view_date_source` varchar(12) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '观点时间来源：as_posted/explicit/derived',
  `view_date_precision` varchar(8) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '观点时间精度：day/month/year',
  `view_date_basis` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '观点时间推算依据，原文摘录',
  `stance_code` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '信号方向，字典项 dict.type=stance',
  `view_text` text CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci COMMENT '正文',
  `signal_text` text CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci COMMENT '信号内容，简短判断',
  `source_batch` varchar(200) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT '' COMMENT '来源批次或平台',
  `source_url` varchar(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT '' COMMENT '原文链接',
  `blogger_id` bigint unsigned DEFAULT NULL COMMENT '博主 id，指向博主表',
  `post_history_id` bigint unsigned DEFAULT NULL COMMENT '指向 post_history.id（帖子层原文留档）',
  `is_review_required` tinyint(1) NOT NULL DEFAULT '0' COMMENT '是否待复核',
  `dedup_key` char(32) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '去重键',
  `created_datetime` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_datetime` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `post_form` varchar(10) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '帖子形态：回复/短文/长文/专栏',
  `data_refs` text CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci COMMENT '数据来源',
  `wiki_ref` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '已具象化的框架条目文件路径',
  `reply_to` text CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci COMMENT '被回应者的原话（原样照抄，不提炼）',
  `is_read` tinyint NOT NULL DEFAULT '0' COMMENT '是否已读：0=待读（用户在页面划过即置 1）；新言论默认 1',
  `is_starred` tinyint NOT NULL DEFAULT '0' COMMENT '星标/关注（1=已关注，用户手动标记）',
  `del_flag` tinyint(1) NOT NULL DEFAULT '0' COMMENT '删除标记，字典项 dict.type=del_flag：0=未删除 1=已删除（逻辑删除，视图会过滤掉）',
  `deleted_datetime` datetime DEFAULT NULL COMMENT '逻辑删除时间（del_flag=1 时写入；恢复时清空）',
  `fetched_datetime` datetime DEFAULT NULL COMMENT '采集时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_dedup` (`dedup_key`),
  KEY `idx_blogger_date` (`blogger_name`,`view_date`),
  KEY `idx_review` (`is_review_required`),
  KEY `idx_is_read` (`is_read`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='研究帖子表';

CREATE TABLE `statement_review_sub` (
  `id` int unsigned NOT NULL AUTO_INCREMENT COMMENT '自增主键',
  `statement_id` bigint unsigned NOT NULL COMMENT '言论 id，指向六张言论表之一',
  `blogger_name` varchar(100) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT '' COMMENT '博主名',
  `suggestion` text CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '复核建议内容',
  `status_code` varchar(16) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'open' COMMENT '处理状态，字典项 dict.type=review_sub_status：open/applied',
  `created_datetime` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_datetime` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_stmt` (`statement_id`),
  KEY `idx_status` (`status_code`)
) ENGINE=InnoDB AUTO_INCREMENT=59 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='帖子复核建议子表';

CREATE TABLE `statement_stock_rel` (
  `statement_id` bigint unsigned NOT NULL COMMENT '言论 id，指向六张言论表之一',
  `stock_id` bigint unsigned NOT NULL COMMENT '个股 id，指向个股表',
  `role_code` varchar(16) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'subject' COMMENT '角色：subject 主体，mention 文中提及',
  `created_datetime` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  PRIMARY KEY (`statement_id`,`stock_id`),
  KEY `idx_stock` (`stock_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='言论个股关联表';

CREATE TABLE `statement_trade` (
  `id` bigint unsigned NOT NULL COMMENT '帖子 id，六张帖子表全局唯一',
  `blogger_name` varchar(100) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '博主名，冗余自博主表',
  `statement_datetime` datetime NOT NULL COMMENT '帖子时间（原帖发布时间）',
  `view_date` date DEFAULT NULL COMMENT '观点时间，判断成立时点',
  `view_date_source` varchar(12) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '观点时间来源：as_posted/explicit/derived',
  `view_date_precision` varchar(8) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '观点时间精度：day/month/year',
  `view_date_basis` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '观点时间推算依据，原文摘录',
  `stance_code` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '信号方向，字典项 dict.type=stance',
  `view_text` text CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci COMMENT '正文',
  `signal_text` text CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci COMMENT '信号内容，简短判断',
  `source_batch` varchar(200) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT '' COMMENT '来源批次或平台',
  `source_url` varchar(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT '' COMMENT '原文链接',
  `blogger_id` bigint unsigned DEFAULT NULL COMMENT '博主 id，指向博主表',
  `post_history_id` bigint unsigned DEFAULT NULL COMMENT '指向 post_history.id（帖子层原文留档）',
  `is_review_required` tinyint(1) NOT NULL DEFAULT '0' COMMENT '是否待复核',
  `dedup_key` char(32) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '去重键',
  `created_datetime` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_datetime` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `post_form` varchar(10) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '帖子形态：回复/短文/长文/专栏',
  `price` decimal(16,4) DEFAULT NULL COMMENT '成交价',
  `market_cap` varchar(32) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '当时市值',
  `trade_date` date DEFAULT NULL COMMENT '操作日期',
  `trade_note` text CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci COMMENT '操作理由',
  `wiki_ref` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '已具象化的框架条目文件路径',
  `reply_to` text CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci COMMENT '被回应者的原话（原样照抄，不提炼）',
  `is_read` tinyint NOT NULL DEFAULT '0' COMMENT '是否已读：0=待读（用户在页面划过即置 1）；新言论默认 1',
  `is_starred` tinyint NOT NULL DEFAULT '0' COMMENT '星标/关注（1=已关注，用户手动标记）',
  `del_flag` tinyint(1) NOT NULL DEFAULT '0' COMMENT '删除标记，字典项 dict.type=del_flag：0=未删除 1=已删除（逻辑删除，视图会过滤掉）',
  `deleted_datetime` datetime DEFAULT NULL COMMENT '逻辑删除时间（del_flag=1 时写入；恢复时清空）',
  `fetched_datetime` datetime DEFAULT NULL COMMENT '采集时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_dedup` (`dedup_key`),
  KEY `idx_blogger_date` (`blogger_name`,`view_date`),
  KEY `idx_review` (`is_review_required`),
  KEY `idx_is_read` (`is_read`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='买卖帖子表';

CREATE TABLE `statement_verify_sub` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT COMMENT '自增主键',
  `statement_id` bigint unsigned NOT NULL COMMENT '言论 id，指向六张言论表之一',
  `verify_date` date DEFAULT NULL COMMENT '验证日期',
  `verifier` varchar(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '验证人',
  `basis` text CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci COMMENT '验证依据',
  `result_code` varchar(32) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '验证结果，字典项 dict.type=verify_result',
  `note` varchar(1024) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '备注',
  `created_datetime` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_prediction` (`statement_id`),
  KEY `idx_result` (`result_code`)
) ENGINE=InnoDB AUTO_INCREMENT=8 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='预测验证子表';

CREATE TABLE `statement_view` (
  `id` bigint unsigned NOT NULL COMMENT '帖子 id，六张帖子表全局唯一',
  `blogger_name` varchar(100) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '博主名，冗余自博主表',
  `statement_datetime` datetime NOT NULL COMMENT '帖子时间（原帖发布时间）',
  `view_date` date DEFAULT NULL COMMENT '观点时间，判断成立时点',
  `view_date_source` varchar(12) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '观点时间来源：as_posted/explicit/derived',
  `view_date_precision` varchar(8) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '观点时间精度：day/month/year',
  `view_date_basis` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '观点时间推算依据，原文摘录',
  `stance_code` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '信号方向，字典项 dict.type=stance',
  `view_text` text CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci COMMENT '正文',
  `signal_text` text CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci COMMENT '信号内容，简短判断',
  `source_batch` varchar(200) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT '' COMMENT '来源批次或平台',
  `source_url` varchar(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT '' COMMENT '原文链接',
  `blogger_id` bigint unsigned DEFAULT NULL COMMENT '博主 id，指向博主表',
  `post_history_id` bigint unsigned DEFAULT NULL COMMENT '指向 post_history.id（帖子层原文留档）',
  `is_review_required` tinyint(1) NOT NULL DEFAULT '0' COMMENT '是否待复核',
  `dedup_key` char(32) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '去重键',
  `created_datetime` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_datetime` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `post_form` varchar(10) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '帖子形态：回复/短文/长文/专栏',
  `wiki_ref` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '已具象化的框架条目文件路径',
  `reply_to` text CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci COMMENT '被回应者的原话（原样照抄，不提炼）',
  `is_read` tinyint NOT NULL DEFAULT '0' COMMENT '是否已读：0=待读（用户在页面划过即置 1）；新言论默认 1',
  `is_starred` tinyint NOT NULL DEFAULT '0' COMMENT '星标/关注（1=已关注，用户手动标记）',
  `del_flag` tinyint(1) NOT NULL DEFAULT '0' COMMENT '删除标记，字典项 dict.type=del_flag：0=未删除 1=已删除（逻辑删除，视图会过滤掉）',
  `deleted_datetime` datetime DEFAULT NULL COMMENT '逻辑删除时间（del_flag=1 时写入；恢复时清空）',
  `fetched_datetime` datetime DEFAULT NULL COMMENT '采集时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_dedup` (`dedup_key`),
  KEY `idx_blogger_date` (`blogger_name`,`view_date`),
  KEY `idx_review` (`is_review_required`),
  KEY `idx_is_read` (`is_read`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='观点帖子表';

CREATE TABLE `stock` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT COMMENT '自增主键',
  `name` varchar(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '个股名称',
  `code` varchar(32) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '股票代码',
  `market_code` varchar(16) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '主市场码，字典项 markets.code',
  `aliases` varchar(512) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '别名，逗号分隔，如 寒王,寒武纪-U',
  `keywords` varchar(512) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '主营/产品关键词，逗号分隔，用于判定「文中该产品词是否指这只股」（F7 同句共现）；通用词只放这里，不放 aliases',
  `has_hk_connect` tinyint(1) DEFAULT NULL COMMENT '是否港股通',
  `created_datetime` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_datetime` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_name` (`name`),
  KEY `idx_code` (`code`),
  KEY `idx_market` (`market_code`)
) ENGINE=InnoDB AUTO_INCREMENT=422 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='个股表';

CREATE TABLE `stock_industry_rel` (
  `stock_id` bigint unsigned NOT NULL COMMENT '个股 id，指向个股表',
  `industry_id` bigint unsigned NOT NULL COMMENT '行业 id，指向行业表',
  `role_code` varchar(16) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'primary' COMMENT '角色：primary 主行业，secondary 次要行业，derived 由言论共现推导',
  `created_datetime` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  PRIMARY KEY (`stock_id`,`industry_id`),
  KEY `idx_industry` (`industry_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='个股行业关联表';

CREATE TABLE `stock_market_rel` (
  `stock_id` bigint unsigned NOT NULL COMMENT '个股 id，指向个股表',
  `market_id` bigint unsigned NOT NULL COMMENT '市场 id，指向市场表',
  `role_code` varchar(16) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'primary' COMMENT '角色：primary 主市场，secondary 次市场',
  `created_datetime` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '创建时间',
  PRIMARY KEY (`stock_id`,`market_id`),
  KEY `idx_market` (`market_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='个股市场关联表';

CREATE TABLE `stock_name_history` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT COMMENT '自增主键',
  `stock_id` bigint unsigned NOT NULL COMMENT '个股 id，指向 stock.id',
  `name` varchar(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '曾用名（官方证券简称，含 G/ST 等技术性名）',
  `valid_from` date DEFAULT NULL COMMENT '该简称启用日（数据源给到才填）',
  `valid_to` date DEFAULT NULL COMMENT '停用日＝下一次改名生效日',
  `source` varchar(32) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'web' COMMENT '来源：eastmoney / manual',
  `source_url` varchar(512) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '来源页（人工补录时尽量给）',
  `created_datetime` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_datetime` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_stock_name` (`stock_id`,`name`),
  KEY `idx_name` (`name`)
) ENGINE=InnoDB AUTO_INCREMENT=678 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='个股曾用名（官方简称变更史，网络导入+人工补录）';

CREATE TABLE `stock_watch` (
  `stock_id` bigint unsigned NOT NULL COMMENT '个股 id，指向 stock.id',
  `buy_price` decimal(18,4) DEFAULT NULL COMMENT '买入价（成本参考，可空）',
  `target_price` decimal(18,4) DEFAULT NULL COMMENT '目标价（可空）',
  `note` text CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci COMMENT '备注（自由文本）',
  `is_watch` tinyint(1) NOT NULL DEFAULT '0' COMMENT '是否加入自选：1=在自选池',
  `watched_datetime` datetime DEFAULT NULL COMMENT '加入自选时间（自选页排序键）',
  `created_datetime` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_datetime` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`stock_id`),
  KEY `idx_watch` (`is_watch`,`watched_datetime`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='我的自选与个股备注（买入价/目标价/备注，一只股一行）';

CREATE TABLE `todo` (
  `id` int NOT NULL AUTO_INCREMENT COMMENT '自增主键',
  `todo_content` varchar(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '待办内容',
  `due_date` date DEFAULT NULL COMMENT '截止日期',
  `is_done` tinyint NOT NULL DEFAULT '0' COMMENT '是否完成',
  `done_datetime` datetime DEFAULT NULL COMMENT '完成时间',
  `sort_order` int NOT NULL DEFAULT '0' COMMENT '排序',
  `created_datetime` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_datetime` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=12 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='首页待办表';
