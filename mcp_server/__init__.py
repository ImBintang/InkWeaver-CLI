# mcp_server package — v7.0.0 MCP 协议外壳
#
# 设计原则（与 v6 解耦规范一致）：
# - 本包是 CLI/GUI 之外的第三条接入通路，不改动 agent/tools/muse 任何核心逻辑
# - 只读原子工具同步返回；Agent 驱动的工作流（鉴知问答/知识提取/妙笔写作）
#   统一走 TaskManager 异步任务模型（start → status/wait → confirm → result）
# - stdio 传输下 stdout 被协议占用，入口处必须重定向到 stderr
