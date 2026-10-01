---
name: wechat-article
description: >
  提取微信公众号文章正文并转为 Markdown。当用户发送 mp.weixin.qq.com 链接，或提到"公众号文章"、"微信文章"、"提取公众号"、"抓取公众号"时使用。
  支持标题、作者、公众号名称、发布日期和正文的完整提取，可直接写入 Obsidian 粗制品目录。
  触发词：「公众号文章」「微信文章」「提取公众号」「抓取公众号」「mp.weixin.qq.com」。
  排除条件：非微信来源（雪球/抖音/得到等）走对应 skill；文章解析后需提炼时交投资框架 skill（提炼环节）。
version: 1.1.1
agent_created: true
---

# 微信公众号文章提取

## Default Stance

### 核心原则

- **微信 UA 反爬**：微信服务端对非微信客户端 UA 返回反爬页——必须伪装 MicroMessenger UA 才能拿到完整 HTML
- **正文取自 `#js_content`**：用 BeautifulSoup 提取该 div 的正文，图片 URL 在 `data-src` 属性
- **输出 JSON 或 Markdown**：默认 JSON（结构化字段），`--markdown` 输出纯 Markdown（含 frontmatter，适合写入文件）
- **写入粗制品**：文章落 `工作区/粗制品/`（investment-framework 路径表 ROUGH_DIR），供后续提炼

### 禁止行为

- 绝不直接抓取非微信来源（走对应 skill）
- 绝不把图片 URL 当已下载资源使用（默认只提取不下载，需 `--with-images` 才内联）
- 绝不跳过反爬处理直接裸抓（会拿到反爬页而非正文）
- 绝不把粗制品当最终产物——写入后状态应为 `待提炼`

---

## Workflow

### 第一步：提取文章

```bash
/opt/homebrew/bin/python3 ~/.skills-manager/skills/wechat-article/scripts/wechat_extract.py "<URL>" --markdown
```

- 默认输出 JSON（title/author/account/date/url/markdown/images）
- `--markdown` 直接输出纯 Markdown（含 frontmatter）

### 第二步：写入 Obsidian 粗制品

保存到 `工作区/粗制品/{标题}.md`（文件名不含日期前缀，日期通过 frontmatter 记录）：

```yaml
---
title: 文章标题
source: "[文章标题](https://mp.weixin.qq.com/s/xxx)"
author: 作者名
account: 公众号名称
date: YYYY-MM-DD
url: https://mp.weixin.qq.com/s/xxx
recorded: YYYY-MM-DD
type: 长文
status: 待提炼
tags: []
---
```
> 字段规范（对齐 framework #1/#23 + 粗加工 7 字段）：`source` 填**真实原文链接**的 markdown `[标题](url)`，**禁止渠道名占位**（如"微信公众号"）；`date`/`recorded` 裸写 `yyyy-MM-dd` 无引号、不用中文年月日；`tags` 留空 `[]` 由提炼阶段按标签体系补。

### 第三步：回复用户

写入后仅回复：`收到，纳入分析`

---

## Output Format

| 字段 | 类型 | 说明 |
|:---|:---|:---|
| title | string | 文章标题 |
| author | string | 作者 |
| account | string | 公众号名称 |
| date | string | 发布日期（YYYY年M月D日） |
| url | string | 原始链接 |
| markdown | string | 正文 Markdown |
| images | list[string] | 图片 URL 列表（默认不下载） |
| written_to | string | 粗制品保存路径（若写入） |

---

## Relative Files

| 场景 | 加载文件 | 内容 | 方式 |
|:---|:---|:---|:---|
| 提取正文 | scripts/wechat_extract.py | 微信文章抓取脚本 | **执行** |
| 确认粗制品路径 | investment-framework SKILL.md | ROUGH_DIR 路径表 | 读取 |

---

## Source Hierarchy

| 优先级 | 来源 |
|:---|:---|
| 1 | 脚本输出（真实抓取结果） |
| 2 | 用户提供的 URL |
| 3 | investment-framework 路径表（粗制品目录） |

---

## 自检

- [ ] URL 是否为 mp.weixin.qq.com（非微信来源不硬抓）？
- [ ] 提取结果是否含正文（未拿到反爬页）？
- [ ] 图片是否按需处理（默认只提取不下载）？
- [ ] 写入粗制品时 frontmatter 是否完整（status=待提炼）？

---

## 技术备注

- **反爬**：伪装 MicroMessenger UA 获取完整 HTML，BeautifulSoup 从 `#js_content` 提取正文
- **图片**：URL 在 `data-src`；`--with-images` 转 `![](url)` 内联
- **失败**：文章删除/封禁/权限限制时脚本给明确提示；微信反爬策略更新则需更新 UA 字符串
- **依赖**：Python 3.11+（`/opt/homebrew/bin/python3`）、requests、beautifulsoup4、lxml
