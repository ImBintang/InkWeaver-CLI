"""共享 subagent 工具定义 — knowledge_task / plot_task / review 三方复用"""


def build_shared_subagent_tools() -> list:
    """构建 subagent 共享的 10 个工具定义
    
    包括：read_chapters / wiki_list / read_wiki / new_wiki / edit_wiki /
          read_memory / check_wiki / read_index / query_relations / agent_output
    """
    return [
        {
            "type": "function",
            "function": {
                "name": "read_chapters",
                "description": "读取指定章节的正文。支持范围表达式如 \"1-3,5,7-9\"。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "chapters": {"type": "string", "description": "章节范围"},
                    },
                    "required": ["chapters"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "wiki_list",
                "description": "查看类别下的 wiki 列表（分页，每页 20 个）。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "category": {"type": "string", "description": "类别名"},
                        "page": {"type": "integer", "description": "页码（默认 1）"},
                    },
                    "required": ["category"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "read_wiki",
                "description": "读取指定 wiki 文档（含 frontmatter）。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "category": {"type": "string", "description": "类别名"},
                        "name": {"type": "string", "description": "词条名"},
                    },
                    "required": ["category", "name"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "new_wiki",
                "description": "新建 wiki 文档。content为必填，否则只有frontmatter无正文。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "category": {"type": "string", "description": "类别名"},
                        "name": {"type": "string", "description": "词条名"},
                        "content": {"type": "string", "description": "正文内容（必须提供）"},
                        "description": {"type": "string", "description": "描述（静态）"},
                        "state": {"type": "string", "description": "状态（动态，可选）"},
                        "tags": {"type": "array", "items": {"type": "string"}, "description": "标签列表"},
                    },
                    "required": ["category", "name", "content"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "edit_wiki",
                "description": "编辑 wiki 文档。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "category": {"type": "string", "description": "类别名"},
                        "name": {"type": "string", "description": "词条名"},
                        "content": {"type": "string", "description": "新正文（None 表示不修改）"},
                        "description": {"type": "string", "description": "新描述（None 表示不修改）"},
                        "state": {"type": "string", "description": "新状态（None 表示不修改）"},
                        "tags": {"type": "array", "items": {"type": "string"}, "description": "新标签（None 表示不修改）"},
                    },
                    "required": ["category", "name"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "read_memory",
                "description": "读取记忆文档。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "记忆名（None 表示读取索引）"},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "check_wiki",
                "description": "检查 wiki 词条在指定章节或文本中是否出现。传入 name+chapters 查章节匹配，传入 text 查文本匹配实体。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "词条名（与 chapters 配合使用）"},
                        "chapters": {"type": "string", "description": "章节范围（与 name 配合使用）"},
                        "text": {"type": "string", "description": "任意文本，自动匹配其中包含的实体名"},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "read_index",
                "description": "读取总 index 或指定类别 index。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "category": {"type": "string", "description": "类别名（None 表示总索引）"},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "query_relations",
                "description": "查询指定词条的所有关联词条。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "词条名"},
                    },
                    "required": ["name"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "agent_output",
                "description": "中间轮输出。调用后直接输出文本，不打断流程。用于输出完整操作摘要。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "要输出的文本"},
                    },
                    "required": ["text"],
                },
            },
        },
    ]
