"""鉴知 Agent — 组装 system prompt、tool defs、工具路由"""

import json
import time
from pathlib import Path

from agent.base import BaseAgent
from agent.todo import TodoManager
from agent.compact import ContextManager, PersistCache
from agent.skill import SkillRegistry
from agent.loop import agent_loop
from agent.permission import PermissionManager
from tools import chapter as chapter_tools
from tools import wiki as wiki_tools
from tools import rules as rules_tools
from tools import relation as relation_tools
from tools import category as category_tools
from tools import memory as memory_tools
from tools import diff as diff_tools
from tools import plot as plot_tools
from tools import editor as editor_tools


TOOL_RESULTS_DIR = Path(".task_outputs") / "tool-results"


DEBT_FILE = "lint-debt.json"


def _read_lint_report(workspace: Path) -> str:
    """读取 lint-debt.json 返回完整债务报告"""
    fp = workspace / DEBT_FILE
    if not fp.exists():
        return "（lint 报告不存在，请先运行 lint 检查）"
    try:
        data = json.loads(fp.read_text(encoding="utf-8"))
        lines = ["## 完整 Lint 债务报告", ""]
        for debt_type, items in data.items():
            if items:
                lines.append(f"### {debt_type}（{len(items)} 项）")
                for item in items:
                    if debt_type == "broken_links":
                        lines.append(f"  ⚠️ {item.get('target', '?')} → {item.get('file', '?')}")
                    elif debt_type == "plot_broken_links":
                        lines.append(f"  ⚠️ {item.get('target', '?')} → {item.get('file', '?')}")
                    elif debt_type == "state_missing":
                        lines.append(f"  ⚠️ {item.get('file', '?')}（{item.get('detail', '')}）")
                    elif debt_type == "state_verbose":
                        lines.append(f"  ⚠️ {item.get('file', '?')}（{item.get('detail', '')}）")
                    elif debt_type == "length_overage":
                        lines.append(f"  ⚠️ {item.get('file', '?')}（{item.get('detail', '')}）")
                    elif debt_type == "desc_verbose":
                        lines.append(f"  ⚠️ {item.get('file', '?')}（{item.get('detail', '')}）")
                    elif debt_type == "file_errors":
                        lines.append(f"  ⚠️ {item.get('file', '?')}（{item.get('detail', '')}）")
                    elif debt_type == "unended_plots":
                        lines.append(f"  ⚠️ {item.get('name', '?')}（{item.get('detail', '')}）")
                    elif debt_type == "appearance":
                        lines.append(f"  {item.get('file', '?')}（{item.get('detail', '')}）")
                    else:
                        lines.append(f"  ⚠️ {json.dumps(item, ensure_ascii=False)}")
                lines.append("")
        return "\n".join(lines) if len(lines) > 1 else "（lint 报告为空，无债务）"
    except Exception as e:
        return f"（读取 lint 报告失败：{e}）"


class JianzhiAgent(BaseAgent):
    """写作智能体 — 组装各模块并提供统一入口"""

    def __init__(self, config: dict, workspace: Path, skills_dir: Path, cli):
        super().__init__(config, workspace, cli)
        self.todo = TodoManager()
        self.context = ContextManager()
        self.skills = SkillRegistry(skills_dir)
        self.permission = PermissionManager()
        self._persist_cache = PersistCache(workspace)
        self._last_tool_call_id = ""
        self.review_session = None  # 妙笔审阅会话（由 MuseWorkflow 设置）

        self.system_prompt = self.build_system_prompt()
        self.tool_defs = self.build_tool_defs()

    def build_system_prompt(self) -> str:
        """组装 system prompt"""
        parts = [
            f"你是鉴知（Jianzhi），一个专业的写作智能体。",
            f"当前工作区：{self.workspace.name}",
            f"当前目录：{self.workspace}",
            "",
            "# 可用技能（渐进式披露）",
            "技能是封装了完整工作流程的知识包。调用技能时，系统会加载该技能的详细步骤说明到上下文中。",
            "**何时调用技能**：遇到以下关键词或场景时，调用对应的技能而非自行推断步骤：\n",
            "- 触发词「提取知识、更新wiki、/update、新章节」→ 调用 `knowledge_extract` 技能\n",
            "- 触发词「创建类别、新建分类、类别规范、设计类别、首次提取」→ 调用 `category_design` 技能\n"
            "- 知识提取流程中，首次创建类别前**必须先调用** `category_design` 技能获取规范，不能直接用 new_category\n",
            "调用方式：直接在响应中使用工具名，传入 query 参数说明意图。\n",
            "",
            "当前可用技能：",
            self.skills.describe_available(),
            "",
            "# 工具使用指南",
            "- 使用 read_chapters 读取章节正文",
            "- 使用 chapter_list 查看章节列表",
            "- 使用 keywords_stat 统计关键词词频",
            "- 使用 update_todo 管理当前任务计划",
            "- 使用 agent_output 进行中间轮输出",
            "- 使用 tools_log_check 查询被压缩的工具调用记录",
            "- 使用 submit_plan 提交知识提取/修改计划（**禁止直接文本输出计划**，必须通过此工具提交）",
            "",
            "# 知识库（只读）",
            "工作区可能包含知识库（wiki/目录 和 plot/目录），其中存储了已提取的结构化知识和剧情事件。",
            "当用户询问故事相关的问题时，**优先使用知识库进行检索**，而不是直接翻原文。",
            "",
            "### 知识库查询工具",
            "- 使用 category_list 查看 wiki 有哪些类别",
            "- 使用 wiki_list <类别> 查看某类别下的所有词条。**注意：结果可能有多页**（如显示「第 1/3 页」），必须逐页翻完再判断某词条是否存在",
            "- 使用 read_wiki <类别> <词条名> 读取词条完整内容（含 frontmatter）",
            "- 使用 read_plot <名称> 读取剧情卡片（了解故事事件）",
            "- 使用 plot_list 浏览剧情卡片列表（支持 ended 参数过滤）",
            "- 使用 check_wiki <词条名> <章节> 检查词条在章节中是否出现",
            "- 使用 query_relations <词条名> 查看词条关联关系",
            "- 使用 rules_list 查看规则文档列表",
            "- 使用 read_rule <规则名> 读取规则文档",
            "- 使用 read_memory 读取记忆索引/<name> 读取指定记忆",
            "- 使用 doc_diff 查看新增/修改的章节",
            "- 使用 context_query 查询当前上下文中已引用的 wiki/规则/剧情卡片列表",
            "",
            "### 知识库优先 RAG 原则（重要）",
            "**核心原则**：面对已有知识库的知识检索，必须先用知识库进行 RAG，而不是直接翻原文。",
            "",
            "正确的检索顺序：",
            "1. plot_list / read_plot → 先查剧情卡片，快速定位事件",
            "2. category_list → 查看有哪些 wiki 类别",
            "3. wiki_list <类别> → 查看该类别下有哪些词条",
            "4. read_wiki <类别> <词条名> → 读取相关词条内容",
            "5. check_wiki <词条名> <章节> → 检查词条在章节中是否出现",
            "6. 只有以上无法满足需求时，才用 read_chapters 读取章节原文",
            "",
            "**禁止行为**：",
            "- ❌ 跳过知识库直接 read_chapters 全文阅读",
            "- ❌ 已有知识库词条的情况下，不查知识库就去翻原文",
            "- ❌ 把知识库能解答的问题变成大段章节阅读",
            "",
            "# 工作流模式",
            "本 Agent 使用计划驱动的工作流模式，分为两个阶段：",
            "",
            "### 阶段一：规划阶段（planning）— 默认状态",
            "- ✅ 允许：所有只读工具（read_chapters / wiki_list / read_wiki / 等）",
            "- ❌ 禁止：所有写工具（new_wiki / edit_wiki / batch_create_wiki / 等）",
            "- 请先阅读章节和已有知识库，制定提取计划",
            "- 通过 submit_plan 提交计划，等待用户审阅",
            "",
            "### 🔴 计划提交强制规则（重要）",
            "当你完成分析、准备好知识提取计划后，**必须**调用 submit_plan 工具提交计划（传入 plan_json 字符串），",
            "**禁止**将计划内容以文本形式直接输出。",
            "直接输出文本会导致工作流断裂——用户无法通过系统流程审阅和确认计划，权限也无法切换到执行阶段。",
            "",
            "### 阶段二：执行阶段（executing）— 用户确认后",
            "- 所有写工具放行，但仅限计划白名单内的操作",
            "- 白名单外的写操作会被拦截",
            "- 使用 batch_create_wiki / batch_edit_wiki 批量操作",
            "- 完成后调用 review_workflow 进入审核",
            "",
            "### Review 审核模式（review_workflow）",
            "- 进入后上下文清空，只能读取计划内的章节/wiki/plot",
            "- 先运行自动 lint 获取债务清单（摘要），使用 lint_report 查看断链等债务的全量明细",
            "- 检查语义问题，制定修改计划",
            "- 修改完成后调用 finish_task 结束",
            "",
            "# 规则",
            "- 需要多步骤工作时，先用 update_todo 制定计划",
            "- 每次只处理一个 in_progress 步骤",
            "- 完成后标记为 completed 并推进下一步",
        ]
        return "\n".join(parts)

    def build_tool_defs(self) -> list:
        """组装 OpenAI 格式 tool definitions"""
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "update_todo",
                    "description": "更新会话计划列表。多步骤工作前先制定计划。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "items": {
                                "type": "array",
                                "description": "计划项列表",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "content": {"type": "string", "description": "计划内容"},
                                        "status": {
                                            "type": "string",
                                            "enum": ["pending", "in_progress", "completed"],
                                            "description": "pending=待办, in_progress=进行中, completed=已完成",
                                        },
                                        "activeForm": {"type": "string", "description": "当前步骤的具体操作描述"},
                                    },
                                    "required": ["content", "status"],
                                },
                            }
                        },
                        "required": ["items"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "tools_log_check",
                    "description": "查询被压缩/缓存的历史工具调用记录。传入工具名（如 batch_create_wiki）可查看最近一次该工具的完整执行结果。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "tool_use_id": {
                                "type": "string",
                                "description": "工具名（如 batch_create_wiki）或工具调用 ID（如 call_xxx）",
                            }
                        },
                        "required": ["tool_use_id"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "agent_output",
                    "description": "中间轮输出。调用后直接输出文本，不打断会话流程。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string", "description": "要输出的文本"},
                        },
                        "required": ["text"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "read_chapters",
                    "description": "读取指定章节的正文。支持范围表达式如 \"1-3,5,7-9\"。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "chapters": {
                                "type": "string",
                                "description": "章节范围，如 \"1-3,5,7-9\"",
                            }
                        },
                        "required": ["chapters"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "keywords_stat",
                    "description": "分章节统计指定关键词的词频",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "chapters": {"type": "string", "description": "章节范围"},
                            "keywords": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "关键词列表",
                            },
                        },
                        "required": ["chapters", "keywords"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "chapter_list",
                    "description": "获取当前工作区的章节列表（含标题）",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                    },
                },
            },
            # Wiki 只读工具（通用模式可用）
            {
                "type": "function",
                "function": {
                    "name": "category_list",
                    "description": "查看 wiki 类别列表（如人物、势力、地点、功法、宝物等）",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "wiki_list",
                    "description": "查看指定类别下的 wiki 词条列表（分页，每页 20 个）。注意：必须翻完所有页才能确认某个词条不存在！不要只看第一页就下结论！",
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
                    "description": "读取指定 wiki 词条。默认只返回 frontmatter（yaml_only=true），设置 yaml_only=false 可查看全文。先查 category_list 得到类别名再调用。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "category": {"type": "string", "description": "类别名"},
                            "name": {"type": "string", "description": "词条名"},
                            "yaml_only": {"type": "boolean", "description": "是否只返回 frontmatter（默认 true，false 返回全文）"},
                        },
                        "required": ["category", "name"],
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
                    "name": "query_relations",
                    "description": "查询指定词条的所有关联词条",
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
                    "name": "read_plot",
                    "description": "读取指定剧情卡片。yaml_only=true 只返回 frontmatter。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "剧情卡片名"},
                            "yaml_only": {"type": "boolean", "description": "是否只读 frontmatter（默认 true）"},
                        },
                        "required": ["name"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "plot_list",
                    "description": "列出剧情卡片（支持 ended 过滤）。ended=\"false\" 只看未结束，\"true\" 只看已结束，\"all\" 看全部。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "ended": {"type": "string", "description": "过滤：true/false/all（默认 false）"},
                            "page": {"type": "integer", "description": "页码（默认 1）"},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "rules_list",
                    "description": "查看规则文档列表（rules/ 目录下的世界观规则，如境界体系）",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "read_rule",
                    "description": "读取指定规则文档。默认只返回 frontmatter（yaml_only=true），设置 yaml_only=false 可查看全文。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "规则名"},
                            "yaml_only": {"type": "boolean", "description": "是否只返回 frontmatter（默认 true，false 返回全文）"},
                        },
                        "required": ["name"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "read_memory",
                    "description": "读取记忆文档（name=None 时读取索引 MEMORY.md）",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "记忆名（None 表示索引）"},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "doc_diff",
                    "description": "对比文档哈希，查看新增/修改的章节",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "context_query",
                    "description": "查询当前对话上下文中已引用的 wiki 词条、规则文档、剧情卡片列表。entity_type=\"all\" 查看全部，\"wiki\"/\"rules\"/\"plots\" 查看单项。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "entity_type": {
                                "type": "string",
                                "description": "查询范围：\"all\" / \"wiki\" / \"rules\" / \"plots\"（默认 \"all\"）",
                            },
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "lint_report",
                    "description": "返回完整的 lint 检查债务报告（从 lint-debt.json 读取），包含全部 broken_links 等债务明细。查看具体断链的完整列表。",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            # 遗留写工具（v4 统一管理，保留向后兼容）
            {
                "type": "function",
                "function": {
                    "name": "new_wiki",
                    "description": "新建 wiki 词条（规则文档请用 new_rule）。规划阶段被权限系统拦截。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "category": {"type": "string", "description": "类别名"},
                            "name": {"type": "string", "description": "词条名"},
                            "content": {"type": "string", "description": "正文内容"},
                            "description": {"type": "string", "description": "描述"},
                            "state": {"type": "string", "description": "状态"},
                            "tags": {"type": "array", "items": {"type": "string"}, "description": "标签"},
                        },
                        "required": ["category", "name", "content"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "edit_wiki",
                    "description": "编辑 wiki 词条。也可使用 edit_doc(doc_type=\"wiki\")。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "category": {"type": "string", "description": "类别名"},
                            "name": {"type": "string", "description": "词条名"},
                            "content": {"type": "string", "description": "新正文"},
                            "description": {"type": "string", "description": "新描述"},
                            "state": {"type": "string", "description": "新状态"},
                            "tags": {"type": "array", "items": {"type": "string"}, "description": "新标签"},
                        },
                        "required": ["category", "name"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "delete_wiki",
                    "description": "删除 wiki 词条。",
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
                    "name": "new_rule",
                    "description": "新建规则文档（rules/ 目录，不参与关系系统）。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "规则名"},
                            "content": {"type": "string", "description": "文档全文"},
                        },
                        "required": ["name", "content"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "edit_rule",
                    "description": "编辑规则文档。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "规则名"},
                            "content": {"type": "string", "description": "新全文"},
                        },
                        "required": ["name", "content"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "delete_rule",
                    "description": "删除规则文档。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "规则名"},
                        },
                        "required": ["name"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "new_plot",
                    "description": "新建剧情卡片。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "剧情卡片名"},
                            "content": {"type": "string", "description": "正文（需包含 [[wikilink]]）"},
                            "chapters": {"type": "string", "description": "覆盖章节范围，如 \"1-5,7-10\""},
                            "description": {"type": "string", "description": "描述"},
                            "state": {"type": "string", "description": "状态"},
                        },
                        "required": ["name", "content", "chapters"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "edit_plot",
                    "description": "编辑剧情卡片。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "剧情卡片名"},
                            "content": {"type": "string", "description": "新正文"},
                            "chapters": {"type": "string", "description": "新章节范围"},
                            "description": {"type": "string", "description": "新描述"},
                            "state": {"type": "string", "description": "新状态"},
                        },
                        "required": ["name"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "end_plot",
                    "description": "结束剧情卡片（设置 ended=true）。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "剧情卡片名"},
                        },
                        "required": ["name"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "delete_plot",
                    "description": "删除剧情卡片。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "剧情卡片名"},
                        },
                        "required": ["name"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "new_category",
                    "description": "创建新类别。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "类别名"},
                            "description": {"type": "string", "description": "类别描述"},
                            "writing_guide": {"type": "string", "description": "写作规范"},
                            "has_state": {"type": "boolean", "description": "是否需要 state 字段"},
                        },
                        "required": ["name"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "edit_category",
                    "description": "编辑类别。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "类别名"},
                            "description": {"type": "string", "description": "新描述"},
                            "writing_guide": {"type": "string", "description": "新写作规范"},
                            "has_state": {"type": "boolean", "description": "新 state 配置"},
                        },
                        "required": ["name"],
                    },
                },
            },
            # 统一文档管理工具（通过 doc_type 参数指定类型：wiki / plot / rule）
            {
                "type": "function",
                "function": {
                    "name": "create_doc",
                    "description": "【统一】新建文档。通过 doc_type 指定类型：\"wiki\"（需 category）/ \"plot\"（需 chapters）/ \"rule\"。支持 frontmatter 字段（description/state/tags）。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "doc_type": {"type": "string", "enum": ["wiki", "plot", "rule"], "description": "文档类型"},
                            "name": {"type": "string", "description": "文档名"},
                            "content": {"type": "string", "description": "正文内容"},
                            "category": {"type": "string", "description": "wiki 类别（仅 doc_type=wiki 时需要）"},
                            "description": {"type": "string", "description": "描述（wiki/plot）"},
                            "state": {"type": "string", "description": "状态（wiki/plot 动态信息）"},
                            "tags": {"type": "array", "items": {"type": "string"}, "description": "标签"},
                            "chapters": {"type": "string", "description": "覆盖章节（仅 doc_type=plot 时需要），如 \"1-5,7-10\""},
                        },
                        "required": ["doc_type", "name"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "edit_doc",
                    "description": "【统一】编辑文档字段（frontmatter 级）。通过 doc_type 指定类型。所有参数可选，None 表示不修改。如需修改正文中的少量文本，优先使用 edit_doc_text（更省 token）。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "doc_type": {"type": "string", "enum": ["wiki", "plot", "rule"], "description": "文档类型"},
                            "name": {"type": "string", "description": "文档名"},
                            "content": {"type": "string", "description": "新正文（None 表示不修改）"},
                            "category": {"type": "string", "description": "wiki 类别（仅 wiki 需要）"},
                            "description": {"type": "string", "description": "新描述"},
                            "state": {"type": "string", "description": "新状态（空字符串删除此字段）"},
                            "tags": {"type": "array", "items": {"type": "string"}, "description": "新标签"},
                            "chapters": {"type": "string", "description": "新章节范围（仅 plot）"},
                        },
                        "required": ["doc_type", "name"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "edit_doc_text",
                    "description": "【统一·手术刀式】在正文中精确匹配一段文本并替换（不涉及 frontmatter）。比 edit_doc(content=新全文) 更省 token，只需提供 \"把哪句话改成什么\"。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "doc_type": {"type": "string", "enum": ["wiki", "plot", "rule"], "description": "文档类型"},
                            "name": {"type": "string", "description": "文档名"},
                            "old_text": {"type": "string", "description": "要替换的原文（必须精确匹配）"},
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
                    "description": "【统一·wikilink 定向替换】替换正文中所有指向 old_target 的 [[wikilink]]。支持两种模式：redirect（重定向，默认）和 unlink（取消链接）。mode=unlink 时 [[目标]] → 目标 / [[目标|别名]] → 别名（new_target 忽略）。当 mode=unlink 且 remember=true 时，会将该目标记入 unlink 黑名单，后续 lint 自动跳过。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "doc_type": {"type": "string", "enum": ["wiki", "plot", "rule"], "description": "文档类型"},
                            "name": {"type": "string", "description": "文档名"},
                            "old_target": {"type": "string", "description": "要匹配的 wikilink 目标名"},
                            "new_target": {"type": "string", "description": "新目标（mode=redirect 时必填，mode=unlink 时忽略）"},
                            "category": {"type": "string", "description": "wiki 类别（仅 wiki 需要）"},
                            "mode": {"type": "string", "enum": ["redirect", "unlink"], "description": "操作模式：redirect（重定向，默认）| unlink（取消链接）"},
                            "remember": {"type": "boolean", "description": "是否将 old_target 记入 unlink 黑名单（仅 mode=unlink 时有效）。黑名单内的断链会被 lint 自动跳过。"},
                        },
                        "required": ["doc_type", "name", "old_target"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "delete_doc",
                    "description": "【统一】删除文档。通过 doc_type 指定类型（wiki / plot / rule）。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "doc_type": {"type": "string", "enum": ["wiki", "plot", "rule"], "description": "文档类型"},
                            "name": {"type": "string", "description": "文档名"},
                            "category": {"type": "string", "description": "wiki 类别（仅 wiki 需要）"},
                        },
                        "required": ["doc_type", "name"],
                    },
                },
            },
        ]

        # 妙笔审阅工具（只在 review_session 激活时可用）
        tools.append({
            "type": "function",
            "function": {
                "name": "report_issue",
                "description": "【妙笔审阅专用】报告一条审阅问题。每发现一条问题调用一次。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "level": {
                            "type": "integer",
                            "description": "问题等级：0-严重(-20分) / 1-重要(-10分) / 2-一般(-5分) / 3-可优化(-3分)",
                            "enum": [0, 1, 2, 3],
                        },
                        "quote": {"type": "string", "description": "原文引用"},
                        "description": {"type": "string", "description": "问题描述"},
                        "suggestion": {"type": "string", "description": "优化建议"},
                    },
                    "required": ["level", "quote", "description", "suggestion"],
                },
            },
        })
        tools.append({
            "type": "function",
            "function": {
                "name": "review_done",
                "description": "【妙笔审阅专用】所有问题报告完毕，结束审阅，触发后端自动算分。",
                "parameters": {
                    "type": "object",
                    "properties": {},
                },
            },
        })

        # === v4 工作流工具 ===
        # submit_plan — 提交计划
        tools.append({
            "type": "function",
            "function": {
                "name": "submit_plan",
                "description": "提交知识提取/修改计划。调用后 Agent 暂停，用户审阅计划。"
                                "计划通过后写权限开放，白名单外的写操作会被拦截。"
                                "单次提取不得超过 20 章。若用户未指定，默认 10 章。"
                                "计划 JSON 包括：scope（提取范围）, new_category, new_wiki, "
                                "edit_wiki, new_rule, edit_rule, new_plot, edit_plot。"
                                "每项字段说明："
                                "new_wiki/edit_wiki 每项需含 category（类别）, name（词条名）, chapters（章节号）, reason（理由）；"
                                "new_rule/edit_rule 每项需含 name（规则名）, reason（规则说明）；"
                                "new_plot/edit_plot 每项需含 name（卡片名）, chapters（章节范围）, reason（理由）。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "plan_json": {
                            "type": "string",
                            "description": "完整的计划 JSON 字符串",
                        }
                    },
                    "required": ["plan_json"],
                },
            },
        })
        # batch_create_wiki — 批量创建 wiki 词条
        tools.append({
            "type": "function",
            "function": {
                "name": "batch_create_wiki",
                "description": "批量创建 wiki 词条（部分成功模式）。"
                                "每个词条需指定类别、名称、正文。"
                                "返回成功/失败统计。适用于执行阶段批量操作。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "items": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "category": {"type": "string", "description": "类别名"},
                                    "name": {"type": "string", "description": "词条名"},
                                    "content": {"type": "string", "description": "正文内容"},
                                    "description": {"type": "string", "description": "描述（可选）"},
                                    "state": {"type": "string", "description": "状态（可选）"},
                                    "tags": {"type": "array", "items": {"type": "string"}, "description": "标签（可选）"},
                                },
                                "required": ["category", "name", "content"],
                            },
                        }
                    },
                    "required": ["items"],
                },
            },
        })
        # batch_edit_wiki — 批量编辑 wiki 词条
        tools.append({
            "type": "function",
            "function": {
                "name": "batch_edit_wiki",
                "description": "批量编辑 wiki 词条（部分成功模式）。"
                                "只传需要修改的字段，category+name 为必填，content 可选。"
                                "返回成功/失败统计。适用于执行阶段批量操作。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "items": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "category": {"type": "string", "description": "类别名"},
                                    "name": {"type": "string", "description": "词条名"},
                                    "content": {"type": "string", "description": "正文内容（可选）"},
                                    "description": {"type": "string", "description": "描述（可选）"},
                                    "state": {"type": "string", "description": "状态（可选）"},
                                    "tags": {"type": "array", "items": {"type": "string"}, "description": "标签（可选）"},
                                },
                                "required": ["category", "name"],
                            },
                        }
                    },
                    "required": ["items"],
                },
            },
        })
        # review_workflow — 切换到审核工作流
        tools.append({
            "type": "function",
            "function": {
                "name": "review_workflow",
                "description": "切换到 Review 审核工作流。调用后当前上下文将存档并清空，"
                                "进入审核阶段。审核阶段只能读取计划内的章节/wiki/plot。"
                                "知识提取完成后必须调用此工具。",
                "parameters": {"type": "object", "properties": {}},
            },
        })
        # finish_task — 完成任务
        tools.append({
            "type": "function",
            "function": {
                "name": "finish_task",
                "description": "完成任务。自动校验、记录日志，然后清空上下文返回初始状态。"
                                "Review 修改完成后调用。",
                "parameters": {"type": "object", "properties": {}},
            },
        })

        # 为每个 skill 生成一个工具（渐进式披露：只暴露 name+description，调用时加载全文）
        for skill_name in self.skills.skill_names():
            doc = self.skills.documents.get(skill_name)
            desc = doc.manifest.description if doc else "（无描述）"
            tools.append({
                "type": "function",
                "function": {
                    "name": skill_name,
                    "description": f"加载并执行技能「{skill_name}」— {desc}",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "调用技能的原因或具体问题（可选）",
                            },
                        },
                    },
                },
            })

        return tools

    def dispatch_tool(self, name: str, args: dict) -> str:
        """工具分发路由"""
        # 统一权限检查（v4 pipeline: deny → whitelist → allow）
        result = self.permission.check(name, args)
        if result is not None:
            return result

        # skill 调用
        if name in self.skills.skill_names():
            self.context.track_skill(name)
            return self.skills.load_full_text(name)

        # 妙笔审阅工具
        if name == "report_issue":
            if self.review_session is None:
                return "错误：report_issue 仅在妙笔审阅模式下可用"
            return self.review_session.report_issue(**args)
        if name == "review_done":
            if self.review_session is None:
                return "错误：review_done 仅在妙笔审阅模式下可用"
            result = self.review_session.review_done()
            return json.dumps(result, ensure_ascii=False)

        # === v4 工作流工具 ===
        if name == "submit_plan":
            from tools.workflow import submit_plan as _submit_plan
            plan_str = args.get("plan_json", "{}")
            result = _submit_plan(self.workspace, plan_str)
            try:
                result_data = json.loads(result)
                if result_data.get("status") == "pending_review":
                    self.cli.print_plan(result_data)
                    self.cli.print_info("请确认是否执行此计划 (y/n)：")
                    confirm = input().strip().lower()
                    if confirm == "y":
                        plan = result_data.get("plan", {})
                        if self.permission.mode == "review":
                            # 审核阶段：合并到现有白名单，不重置
                            return self.permission.submit_review_plan(plan)
                        else:
                            self.permission.submit_plan(plan)
                            return json.dumps({
                                "status": "approved",
                                "message": "计划已通过，写权限已开放，可以开始执行。"
                            }, ensure_ascii=False)
                    else:
                        self.cli.print_info("请输入打回理由：")
                        reason = input().strip()
                        return json.dumps({
                            "status": "rejected",
                            "reason": reason,
                            "message": f"计划被打回，理由：{reason}。请根据理由修改后重新提交。"
                        }, ensure_ascii=False)
            except (json.JSONDecodeError, Exception):
                pass
            return result

        if name == "review_workflow":
            from tools.workflow import review_workflow as _review_workflow
            self._archive_context()
            self.permission.switch_review()
            # 自动运行 lint
            try:
                from tools.lint import run_lint
                lint_result = run_lint(self.workspace)
            except Exception:
                lint_result = "（lint 检查异常）"
            # 标记等待 chat() 清理上下文（不清 self.messages，避免 agent_loop 的 tool result 链断裂）
            self._review_pending = {"lint_result": lint_result}
            self._stop_agent_loop = True
            return _review_workflow(self.workspace)

        if name == "finish_task":
            from tools.workflow import finish_task as _wf_finish
            from tools.diff import finish_task as _diff_finish
            # 沿用旧 finish_task 逻辑：校验存在性 + 记录 log.json + 构建关系图
            scope = str(sorted(self.permission.whitelist.read_chapters or [0]))
            _diff_finish(self.workspace, scope)
            self.permission.reset()
            # 标记等待 chat() 清理上下文
            self._finish_pending = True
            self._stop_agent_loop = True
            return _wf_finish(self.workspace)

        dispatch = {
            "update_todo": self._handle_todo,
            "tools_log_check": self._handle_tools_log_check,
            "agent_output": lambda **kw: "(已输出)",
            "read_chapters": self._handle_read_chapters,
            "keywords_stat": self._handle_keywords_stat,
            "chapter_list": self._handle_chapter_list,
            # Wiki 只读工具
            "category_list": lambda **kw: category_tools.category_list(self.workspace),
            "wiki_list": lambda **kw: wiki_tools.wiki_list(self.workspace, **kw),
            "read_wiki": lambda **kw: wiki_tools.read_wiki(self.workspace, **kw),
            "check_wiki": lambda **kw: wiki_tools.check_wiki(self.workspace, **kw),
            "query_relations": lambda **kw: relation_tools.query_relations(self.workspace, **kw),
            "read_plot": lambda **kw: plot_tools.read_plot(self.workspace, **kw),
            "plot_list": lambda **kw: plot_tools.plot_list(self.workspace, **kw),
            "query_plot_by_chapters": lambda **kw: plot_tools.query_plot_by_chapters(self.workspace, **kw),
            "rules_list": lambda **kw: rules_tools.rules_list(self.workspace),
            "read_rule": lambda **kw: rules_tools.read_rule(self.workspace, **kw),
            "read_memory": lambda **kw: memory_tools.read_memory(self.workspace, **kw),
            "doc_diff": lambda **kw: diff_tools.doc_diff(self.workspace),
            "context_query": lambda **kw: self.context.query_context(**kw),
            "lint_report": lambda **kw: _read_lint_report(self.workspace),
            # 统一文档管理工具
            "create_doc": lambda **kw: editor_tools.create_doc(self.workspace, **kw),
            "edit_doc": lambda **kw: editor_tools.edit_doc(self.workspace, **kw),
            "edit_doc_text": lambda **kw: editor_tools.edit_doc_text(self.workspace, **kw),
            "edit_doc_wikilink": lambda **kw: editor_tools.edit_doc_wikilink(self.workspace, **kw),
            "delete_doc": lambda **kw: editor_tools.delete_doc(self.workspace, **kw),
            # v4 批量工具
            "batch_create_wiki": lambda **kw: wiki_tools.batch_create_wiki(self.workspace, kw.get("items", [])),
            "batch_edit_wiki": lambda **kw: wiki_tools.batch_edit_wiki(self.workspace, kw.get("items", [])),
            # 遗留写工具（代理到具体模块）
            "new_wiki": lambda **kw: wiki_tools.new_wiki(self.workspace, **kw),
            "edit_wiki": lambda **kw: wiki_tools.edit_wiki(self.workspace, **kw),
            "delete_wiki": lambda **kw: wiki_tools.delete_wiki(self.workspace, **kw),
            "new_rule": lambda **kw: rules_tools.new_rule(self.workspace, **kw),
            "edit_rule": lambda **kw: rules_tools.edit_rule(self.workspace, **kw),
            "delete_rule": lambda **kw: rules_tools.delete_rule(self.workspace, **kw),
            "new_plot": lambda **kw: plot_tools.new_plot(self.workspace, **kw),
            "edit_plot": lambda **kw: plot_tools.edit_plot(self.workspace, **kw),
            "end_plot": lambda **kw: plot_tools.end_plot(self.workspace, **kw),
            "delete_plot": lambda **kw: plot_tools.delete_plot(self.workspace, **kw),
            "new_category": lambda **kw: category_tools.new_category(self.workspace, **kw),
            "edit_category": lambda **kw: category_tools.edit_category(self.workspace, **kw),
        }

        handler = dispatch.get(name)
        if handler is None:
            return f"错误：未知工具「{name}」"

        try:
            result = handler(**args)
            # 追踪已引用的实体
            if name == "read_wiki":
                name_val = args.get("name", "")
                if name_val and not result.startswith("错误"):
                    self.context.track_entity("wiki", [name_val])
            elif name == "read_rule":
                name_val = args.get("name", "")
                if name_val and not result.startswith("错误"):
                    self.context.track_entity("rules", [name_val])
            elif name == "read_plot":
                name_val = args.get("name", "")
                if name_val and not result.startswith("错误"):
                    self.context.track_entity("plot", [name_val])
            # PersistCache：大输出/写工具结果持久化
            if hasattr(self, '_persist_cache') and self._persist_cache.should_persist(name, result):
                tc_id = self._last_tool_call_id or f"{name}_{int(time.time())}"
                result = self._persist_cache.persist_result(name, args, result, tc_id)
            return result
        except Exception as e:
            return f"错误：{e}"

    # ---- 工具 handlers ----
    def _handle_todo(self, items: list) -> str:
        return self.todo.update(items)

    def _handle_tools_log_check(self, tool_use_id: str) -> str:
        # 先查 TOOL_RESULTS_DIR（精确 tool_call_id 匹配）
        path = TOOL_RESULTS_DIR / f"{tool_use_id}.txt"
        if path.exists():
            text = path.read_text(encoding="utf-8")
            return text[:30000]
        # 再查 PersistCache（按工具名搜最近一次）
        if hasattr(self, '_persist_cache'):
            try:
                cache = json.loads(self._persist_cache.cache_path.read_text(encoding="utf-8"))
                # 按 tool name 匹配，取最新的
                matches = [(k, v) for k, v in cache.items()
                           if isinstance(v, dict) and v.get("tool") == tool_use_id]
                if matches:
                    # 取最后一条（最新的）
                    _, data = matches[-1]
                    full = data.get("full_output", "")
                    if full:
                        return full[:30000]
                    return data.get("result_preview", "(缓存无完整输出)")
            except Exception:
                pass
        return f"未找到工具调用记录：{tool_use_id}"

    def _handle_read_chapters(self, chapters: str) -> str:
        result = chapter_tools.read_chapters(self.workspace, chapters)
        # 追踪已读章节
        nums = chapter_tools.parse_chapter_spec(chapters)
        titles = []
        for n in nums:
            t, _ = chapter_tools._read_chapter_file(self.workspace / "document", n)
            titles.append(t or f"第{n}章")
        self.context.track_chapter(
            [str(n) for n in nums],
            titles,
        )
        return result

    def _handle_keywords_stat(self, chapters: str, keywords: list) -> str:
        return chapter_tools.keywords_stat(self.workspace, chapters, keywords)

    def _handle_chapter_list(self) -> str:
        return chapter_tools.chapter_list(self.workspace)

    def chat(self, user_input: str):
        """处理一条用户输入

        Returns:
            bool: 始终返回 False（v4 不再有 handoff 机制）
        """
        self.messages.append({"role": "user", "content": user_input})
        self.messages = agent_loop(self, self.messages)

        # review_workflow / finish_task 过渡：agent_loop 结束后清理上下文
        if hasattr(self, '_review_pending') and self._review_pending:
            lint_result = self._review_pending.get("lint_result", "")
            self.messages.clear()
            self._inject_review_context(lint_result)
            del self._review_pending
            # 清除上一轮 dispatch 设置的停止标记，否则自动审核也跑不起来
            self._stop_agent_loop = False
            # 注入用户提示引导 LLM 主动执行审核，避免只有 system 消息导致 LLM 只输出文本
            self.messages.append({
                "role": "user",
                "content": "请根据以上信息逐项检查所有新建词条、剧情卡片、规则文档的语义质量。"
                           "对于 broken_link，严格按「断链处理规则」的四个分类处理："
                           "计划内漏建（①）直接 new_wiki 补建；"
                           "规则概念（②）用 edit_doc_wikilink(mode='unlink', remember=true) 取消链接并记黑名单；"
                           "计划外重要遗漏（③）先用 submit_plan 提交补充计划，用户确认后再 new_wiki 补建；"
                           "次要实体（④）用 edit_doc_wikilink(mode='unlink', remember=false) 取消链接不加黑名单。"
                           "对于 unended_plots，用 end_plot 结束已完结的剧情卡片。"
                           "完成后调用 finish_task 结束。"
            })
            self.messages = agent_loop(self, self.messages)
        elif hasattr(self, '_finish_pending') and self._finish_pending:
            self.messages.clear()
            del self._finish_pending

        # 打印最终输出（取最后一条 assistant 的文本回复）
        for msg in reversed(self.messages):
            if msg.get("role") == "assistant" and msg.get("content"):
                text = msg["content"].strip()
                if text:
                    self.cli.print_output(text)
                break

        return False

    def context_report(self) -> str:
        """/context 指令"""
        return self.context.context_report(self.messages)

    def compact_history(self):
        """主动压缩（/compact 指令）"""
        self.context.mark_compacted()
        self.messages = [{
            "role": "user",
            "content": "（上下文已压缩，继续当前工作）"
        }]
        self.cli.print_info("上下文已压缩。")

    # ---- v4 工作流辅助方法 ----

    def _archive_context(self):
        """存档当前上下文到 session/transcript_{timestamp}.jsonl"""
        import datetime
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        archive_dir = self.workspace / "session"
        archive_dir.mkdir(parents=True, exist_ok=True)
        path = archive_dir / f"transcript_{ts}.jsonl"
        with open(path, "w", encoding="utf-8") as f:
            for msg in self.messages:
                f.write(json.dumps(msg, ensure_ascii=False) + "\n")
        self.cli.print_info(f"上下文已存档至：{path}")

    def _inject_review_context(self, lint_result: str = ""):
        """注入 review 首轮信息：计划JSON + lint 结果 + 已创建条目快照"""
        parts = ["【系统】你已进入 Review 审核工作流。"]
        parts.append("")
        parts.append("以下是本次知识提取计划所涉及的章节和条目信息：")
        parts.append("")

        # 注入已创建的 wiki 条目列表
        wiki_entries = []
        for cat, name in sorted(self.permission.whitelist.new_wiki):
            wiki_entries.append(f"[{cat}] {name}（新建）")
        for cat, name in sorted(self.permission.whitelist.edit_wiki):
            wiki_entries.append(f"[{cat}] {name}（修改）")

        if wiki_entries:
            parts.append("### 计划内的知识条目")
            parts.extend(f"- {e}" for e in wiki_entries)

        # 注入 lint 结果
        if lint_result and lint_result != "（lint 检查异常）":
            parts.append("")
            parts.append("### 自动 Lint 检查结果")
            parts.append(lint_result[:8000])

        parts.append("")
        parts.append("### 注意事项")
        parts.append("- 你只能读取计划范围内的章节、wiki 和剧情卡片")
        parts.append("- 使用 wiki_list / plot_list / chapter_list 可查看存在性但不可读内容")
        parts.append("- 以上是自动 lint 的债务清单，请据此进行语义审查和修复")
        parts.append("")
        parts.append("### 断链（broken_link）处理规则")
        parts.append("lint 报告的 broken_link 需要按以下规则区分处理：")
        parts.append("")
        parts.append("**① 计划内漏建词条** — 该实体在本轮提取计划（上方「计划内的知识条目」）的 new_wiki 列表中，")
        parts.append("   但实际未创建（词条文件不存在）。→ 直接使用 `new_wiki` 补建（白名单已存在）。")
        parts.append("")
        parts.append("**② 规则文档已覆盖的概念** — 如通用境界名、通用物品、世界观底层设定等，规则文档已定义无需重复建词条。")
        parts.append("   → 使用 `edit_doc_wikilink(mode=\"unlink\", remember=true)` 取消链接并记入 unlink 黑名单。")
        parts.append("")
        parts.append("**③ 计划外的重要遗漏词条** — 不属于以上两类，但实体本身值得建词条（如重要人物、独特宝物、核心势力等）。")
        parts.append("   → 先通过 `submit_plan` 提交补充计划申请白名单扩展（只需包含该词条的 new_wiki），")
        parts.append("    用户确认后白名单自动扩展，再使用 `new_wiki` 创建。")
        parts.append("")
        parts.append("**④ 次要实体** — 一次性配角、普通妖兽、泛称物品等，不值得独立建词条。")
        parts.append("   → 使用 `edit_doc_wikilink(mode=\"unlink\", remember=false)` 取消链接，**不**记入黑名单。")
        parts.append("")
        parts.append("### 未结束剧情卡片处理规则")
        parts.append("lint 报告的 unended_plots 表示剧情卡片章节范围已结束但未标记收尾。")
        parts.append("对于这些卡片，调用 `end_plot(name=\"...\", end_notes=\"...\")` 结束。")
        parts.append("判断标准：卡片的最大章节号 ≤ 最新章节号 - 10，且卡片故事内容已自然完结。")

        self.messages.append({
            "role": "system",
            "content": "\n".join(parts)
        })
