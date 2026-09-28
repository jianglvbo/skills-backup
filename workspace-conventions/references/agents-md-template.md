# AGENTS.md 骨架（复制后替换 `<>` 与 `【】`）

> 三份文档分工，别混：`README.md` 给下一个开发者（是什么/怎么跑/怎么开发/怎么用；
> 不写 agent 纪律与内部历史）、`AGENTS.md` 给 agent（改这个仓库不能踩什么）、workspace-conventions skill 给跨项目通用规范。
> 校验字符串 `开工与收工` / `一处一义` / `产物落点` 是 `check-workspace.sh` 的判据，改了就 FAIL；
> 两份名单走 `<!-- agent-cells: … -->` / `<!-- root-entries: … -->` 注释锚点，脚本读它们（渲染时不可见）。

```markdown
# <仓库名> — agent 工作约定

## 开工与收工（跨会话交接）

- **开工**：`git log --oneline -15` 看最近做了什么，`git status` + `git diff` 判断未提交改动——
  **那可能是上个会话的进行中工作，先读懂意图，别当垃圾回滚或扫进无关提交**。
- **收工**：交接写进 commit message——决策、原因、遗留问题在 message 里说清；
  需要长期生效的规则沉淀进本文。
- **分工（一处一义）**：`README.md` 给下一个开发者看（怎么跑/怎么开发/怎么用）；本文件只管**本工作区工程纪律**；
  流程编排与工具用法归各 skill，skill 内不得重述本文件的纪律（只准指回）。
  跨工作的通用规范在 workspace-conventions skill，别往本文件里抄。

## 目录

<!-- agent-cells: qoder-cn zcode qwenworkcn claude -->
<!-- root-entries: AGENTS.md README.md package.json src out .gitignore .agents -->

**两份固定名单**（`check-workspace.sh` 直接读上面两行注释锚点，改这里即生效、别处不再列）：
`root-entries` ＝ 允许出现在仓库一级的条目；`agent-cells` ＝ 允许出现的 `out/` 分格名。
要加新目录或新分格，**先改锚点行再建目录**（脚本对根外条目报 WARN、对 `out/` 名单外分格报 FAIL）。

【本项目目录树；每行一句「这里放什么、为什么在这」；点目录与实际 skill 层要写准】

## 产物落点：`out/<agent>/`

- agent 产出只落 `out/<agent>/`，`<agent>` 只取 §目录 `agent-cells` 那份名单（**唯一来源**，
  不靠目录名推、这里不再列一遍）。截图、报告、导出数据、一次性脚本全算产物；
  格子不存在就 `mkdir -p`（名单里没有就先扩名单）。
- **不碰别人家的格子**；产物不散落仓库根或源码目录。
- `out/` 不进 git——要跨会话生效的结论写进 commit message 或本文，别指望别人翻图。

## Skill 可见性【本仓库无 skill 层需求则整节删掉，并同步删 .gitignore 的 .agents 行】

- 本项目哪些 skill 只靠工作区 `.agents/skills/` 才可见：【逐个列名 + 说明它缺席哪些 agent 的全局目录】
- 这一层由谁维护：【桌面 app 的 workspace tag / 手工 ln -s】，
  以及「不在 <中心库> 账上、sync 不会自动修」的实测结论
- 改动后跑：`find .agents/skills -maxdepth 1 -type l ! -exec test -e {} \; -print`
  （非空＝有断链；悬空软链会静默消失，agent 不报错）
- 【若曾有过相反决定：写 sha + 翻案理由，免得下个会话再删一遍】

## <项目自有纪律 1：如「改结构＝读写一起改 + 端到端冒烟」>

【只写本项目特有的。带证据：哪个文件哪一行、什么命令、什么历史事故。
禁令要写「别改成 X（会怎样）」，比单纯规定更好执行】

## <项目自有纪律 2 …>
```

## 写作要求

- 每条规则要么有实测证据，要么标 `[待验证]`；不确定的写成问题，不写成事实。
- 优先**改写既有条目**，新增须确认无条目可承载；一个语义只存一处。
- 历史事故写清「什么时候、错在哪、代价」——比抽象规定更能防重犯。
