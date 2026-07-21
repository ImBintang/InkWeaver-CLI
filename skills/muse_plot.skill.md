---
name: muse_plot
description: 妙笔 Plot Summary 前情提要提取工作流指引
---

# 前情提要提取工作流

## 目标
基于剧情卡片和最近的章节正文，按时间顺序梳理前情提要，供 Writer 写作参考。

## 流程

### 第一步：定位关键章节
- 使用 `keywords_stat(chapters)` 分析近 20 章的高频词，定位关键章节

### 第二步：查询剧情卡片
- 使用 `query_plot_by_chapters(chapters)` 查询关键章节区间覆盖的剧情卡片
- 使用 `plot_list(ended="false")` 获取所有未结束的剧情卡片
- 使用 `read_plot(name)` 读取相关卡片详情

### 第三步：阅读最近章节
- 调用 `read_chapters(chapters)` 读取最近 5 章的正文
- 关注章节结尾的状态：人物位置、情感状态、未解决的问题

### 第四步：整理并提交 Workflow
- 将所有信息整理成结构化的 markdown
- 强调：与大纲相关性强的剧情优先
- 强调：章节更近的剧情优先
- 使用 `call_plot_workflow` 工具提交前情提要生成任务

### 第五步：Workflow 参数要求
- `plot_only_yaml`：列出只需 frontmatter 的剧情卡片名（至多 24 个）
- `plot_full`：列出需全文的剧情卡片名（至多 12 个）
- `chapters`：列出需读取的章节编号
- **注意**：不要同时将同一个卡片放入 plot_only_yaml 和 plot_full
- Workflow 会自动校验名称和章节号是否存在，不存在会返回错误提示供你修正
