"""共享 subagent 工具定义 — knowledge_task / plot_task / review 三方复用"""


def build_shared_subagent_tools() -> list:
    """构建 subagent 共享的工具定义
    
    包括：read_chapters / wiki_list / read_wiki / new_wiki / edit_wiki /
          edit_doc_text / edit_doc_wikilink /
          read_memory / check_wiki / read_index / query_relations /
          rules_list / read_rule / agent_output
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
                "description": "查看类别下的 wiki 列表（分页，每页 20 个）。注意：必须翻完所有页才能确认某个词条不存在！不要只看第一页就下结论！",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "category": {"type": "string", "description": "类别名"},
                        "page": {"type": "integer", "description": "页码，默认 1。如需查看更多请传入 page=2、page=3 等继续翻页"},
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
        # 统一手术刀式编辑工具
        {
            "type": "function",
            "function": {
                "name": "edit_doc_text",
                "description": "【统一】在正文中精确匹配一段文本并替换。比 edit_wiki(content=新全文) 省 token，只需提供 old_text → new_text。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "doc_type": {"type": "string", "enum": ["wiki", "plot"], "description": "文档类型"},
                        "name": {"type": "string", "description": "文档名"},
                        "old_text": {"type": "string", "description": "要替换的原文"},
                        "new_text": {"type": "string", "description": "替换后的文本"},
                        "category": {"type": "string", "description": "wiki 类别（仅 wiki 需要）"},
                    },
                    "required": ["doc_type", "name", "old_text", "new_text"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "edit_doc_wikilink",
                "description": "【统一】替换正文中所有指向 old_target 的 [[wikilink]]。mode=redirect（默认）重定向目标，mode=unlink 取消链接（[[目标]]→目标/[[目标|别名]]→别名）。当 mode=unlink 且 remember=true 时，会将该目标记入 unlink 黑名单，后续 lint 自动跳过。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "doc_type": {"type": "string", "enum": ["wiki", "plot"], "description": "文档类型"},
                        "name": {"type": "string", "description": "文档名"},
                        "old_target": {"type": "string", "description": "要匹配的 wikilink 目标"},
                        "new_target": {"type": "string", "description": "新目标（mode=redirect 时用，unlink 时忽略）"},
                        "category": {"type": "string", "description": "wiki 类别（仅 wiki 需要）"},
                        "mode": {"type": "string", "enum": ["redirect", "unlink"], "description": "操作模式：redirect（重定向）| unlink（取消链接）"},
                        "remember": {"type": "boolean", "description": "是否将 old_target 记入 unlink 黑名单（仅 mode=unlink 时有效）"},
                    },
                    "required": ["doc_type", "name", "old_target"],
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
                "name": "rules_list",
                "description": "查看规则文档列表（rules/ 目录）。规则文档定义世界观底层规则，如境界体系、等级体系等。注意：创建/更新世界观概念（如修炼境界、物品等级）前，必须先调此工具确认是否已有对应规则文档。",
                "parameters": {
                    "type": "object",
                    "properties": {},
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "read_rule",
                "description": "读取指定规则文档。yaml_only=false 可查看全文。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "规则名（不含 .md 后缀）"},
                        "yaml_only": {"type": "boolean", "description": "True 只返回 frontmatter，False 返回全文（默认 True）"},
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
