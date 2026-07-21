---
name: muse_knowledge
description: 妙笔 Researcher 先验知识提取工作流指引
---

# 先验知识提取工作流

## 目标
从大纲中提取关键词，匹配 Wiki 知识库中的实体，总结一份完整的先验知识描述，供 Writer 写作参考。

## 流程

### 第一步：解析大纲关键词
- 使用 `check_wiki(text=大纲全文)` 匹配知识库中的实体
- 使用 `keywords_stat(chapters)` 辅助分析高频词（选择最近若干章）

### 第二步：检索实体详情
- 对匹配到的实体，调用 `read_wiki(category, name)` 获取详情
- 对拿不准是否相关的实体，先用 `yaml_only=true` 快速浏览

### 第三步：一跳扩充
- 对已确定相关的实体，调用 `query_relations(name)` 获取关联实体
- 对关联实体重复第二步，但只保留明显相关的

### 第四步：汇总并提交 Workflow
- 将所有实体详情整理成结构化材料
- 注意：你正在整理的是"输入"，不是"输出"
- 使用 `call_knowledge_workflow` 工具提交先验知识生成任务

### 第五步：Workflow 参数要求
- `wiki_only_yaml`：列出只需 frontmatter 的 wiki 词条名（至多 36 个）
- `wiki_full`：列出需全文的 wiki 词条名（至多 18 个）
- `rules`：列出需读取的规则文档名
- **注意**：不要同时将同一个词条放入 wiki_only_yaml 和 wiki_full
- Workflow 会自动校验名称是否存在，不存在会返回错误提示供你修正
