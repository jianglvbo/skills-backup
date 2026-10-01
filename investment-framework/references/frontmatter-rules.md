# 原始资源 YAML Frontmatter 规范

> 粗加工执行细节（低频规则，从 SKILL.md 下沉）。权威规则：`framework-rules.md` #27（层间 frontmatter 字段集边界）、#21（引号规则）。
> 与 SKILL.md 冲突时以本文件 + framework-rules.md 为准。

## 字段范围

原始资源 frontmatter **仅 7 个字段**：title、source、author、date、type、status、tags。不添加 layer、category 或其他字段——归属层和分类由提炼环节决定。

字段顺序建议（防漂移，framework-rules #27）：`title → source → author → date → type → status → tags`。

## 引号规则

frontmatter 值中的引号必须正确处理，否则 Obsidian Properties 面板解析异常：

| 值的内容 | 包裹方式 | 示例 |
|:---|:---|:---|
| 不含引号 | 双引号 | `title: "珀莱雅深度拆解"` |
| 含双引号 | 单引号包裹 | `title: '理解泡泡玛特的四本"书"'` |
| 含单引号 | 双引号包裹 | `title: "错过'时代'是最大的风险"` |
| 含双引号+单引号 | 双引号包裹+转义 | `title: "他说\"好\"了"` |

**禁止**：双引号包裹的值内部再出现未转义的双引号（如 `title: "理解"书""`），这会导致 YAML 解析错误。

## tags 格式

多值 tags 必须使用 YAML block list 格式，每项独占一行并缩进：

```yaml
tags:
  - "投资心得/心得主题/估值经验"
  - "市场/A股"
```

**禁止**：将 block list 项写在 `tags:` 同一行（如 `tags: - item1`），这会导致解析失败。空 tags 使用 `tags: []`。
