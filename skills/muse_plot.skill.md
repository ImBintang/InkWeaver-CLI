---
name: muse_plot
description: 妙笔 Plot Summary 前情提要提取工作流指引
---

# 前情提要提取工作流

## 目标
基于剧情卡片，按时间顺序梳理前情提要，供 Writer 写作参考。

## 核心约束
- **用户提供大纲后立即执行完整流程，不得中途询问用户，不得输出分析性文本**。
- **你的最终输出必须是 `call_plot_workflow` 的结果**，不是你自己写的总结。
- 如果 `call_plot_workflow` 返回错误，修正参数后重试，不要问用户。

## 流程

### 第一步：查询剧情卡片
- 使用 `query_plot_by_chapters(chapters)` 查询关键章节区间覆盖的剧情卡片
- 使用 `plot_list(ended="false")` 获取所有未结束的剧情卡片
- 使用 `read_plot(name)` 读取相关卡片详情

### 第二步：调用 Workflow
- 将所有信息整理后，**立即调用 `call_plot_workflow` 提交前情提要生成任务**
- 不要输出自己的分析或总结，workflow 的结果即为最终输出
- 与大纲相关性强的剧情优先、章节更近的剧情优先

### 第三步：Workflow 参数要求
- `plot_only_yaml`：列出只需 frontmatter 的剧情卡片名（至多 24 个）
- `plot_full`：列出需全文的剧情卡片名（至多 12 个）
- **注意**：不要同时将同一个卡片放入 plot_only_yaml 和 plot_full
- Workflow 会自动校验名称是否存在，不存在会返回错误提示供你修正

## 输出格式

Workflow 生成的前情提要应遵循以下结构：

```
# 当前事件
（当前正在发生的核心剧情事件、冲突）

# 背景事件
（近期已发生的铺垫性事件、伏笔状态）
```
