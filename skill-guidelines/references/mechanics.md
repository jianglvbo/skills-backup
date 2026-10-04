# Skill 专属机制

> `SKILL.md` 里那套写法（指针、两种负载、阶梯、领词、空转、沉淀）对所有 agent 文档通用。这份只讲**当文档是一份 skill 时**多出来的三个决策：frontmatter 怎么填、调用方式怎么选、什么时候拆出路由 skill。

## 调用方式：两种负载之间的取舍

| | model-invoked | user-invoked |
|:---|:---|:---|
| frontmatter | 省略 `disable-model-invocation` | 设 `disable-model-invocation: true` |
| 谁能触发 | agent 自主触发 + 其他 skill 能到达 + **人仍然能打名字** | 只有打名字的人；其他 skill 到不了 |
| description 的角色 | 面向模型的顶层**指针**，常驻上下文，承载触发 branch | 变成面向人的一句话摘要，触发词列表删掉 |
| 付什么 | 永久上下文负载，换可发现性 | 零上下文负载，花心智负载——人是那个得记住它存在的索引 |

关键澄清：model-invocation **总是包含**人的可达性。写 description 只会增加 agent 的发现能力，绝不会拿走人的。所以"给它写 description"不等于"不许人手动调"。

**选择规则**：只有当 agent 必须自己到达它、或另一个 skill 必须到达它时，才用 model-invoked。只会靠手点的，做成 user-invoked，一分上下文负载都不付。

**副作用**：一个内容全是 reference 的 model-invoked skill，同时是共享参考的**一个家**——别的 skill 能调用它，所以多份 skill 都要用的参考放在一处即可。

**反例场景**：两个 user-invoked skill 都需要的一份共享参考，哪个 skill 里都放不下——没有 description 就无法互相触发。把它推到 skill 系统之外的一个普通文件里，任何 skill 都能指过去。

## 按调用方式拆分

`SKILL.md` 的「按 sequence 拆分」管的是抢跑问题；这里管的是**要不要让一个东西独立可被触发**。

拆出一个 model-invoked skill 的时机，二选一：

- 你有一个真正独立的**领词**，它应该自己触发它——即你提示词里真的会用的那个触发词。
- 另一个 skill 必须到达它。

代价是新增一条常驻上下文的 description，所以这份独立可达性得值这个价。

## 路由 skill

user-invoked skill 多到你记不住时，堆起来的心智负载靠**路由 skill** 治：一个 user-invoked skill，列出其余那些以及各自何时取用，于是人只需记一个而不是记一堆。

它的固有限制：路由 skill 只能**提示**，不能**触发**它们——被指向的那些是 user-invoked、没有 description，所以除了人自己伸手，没有任何东西能到达它们。别把路由 skill 当成调度器，它是给人看的名录。
