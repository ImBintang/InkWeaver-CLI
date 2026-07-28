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
    """重新运行 lint 检查（刷新 lint-debt.json）并返回完整债务报告"""
    # 先重跑 lint 以反映当前缓存/DB 的最新状态
    try:
        from tools.lint import run_lint
        run_lint(workspace)
    except Exception:
        pass  # 即使 run_lint 失败，仍尝试读取旧报告
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

        # v5.0：初始化 DB 服务
        from tools.db.service import SQLiteService
        from tools.db.proxy import ProxyService
        from tools.editor import register_proxy
        self._db_service = SQLiteService(workspace / "wiki.db")
        self._proxy = ProxyService(self._db_service)
        register_proxy(workspace, self._proxy)

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
            "- 使用 memory_query 检索记忆（支持类别/关键词过滤）",
            "- 使用 memory_write 写入新记忆（用户表达偏好/纠正错误/观察到模式时静默写入）",
            "- 使用 memory_forget 删除记忆",
            "- 使用 chapter_list 查看章节列表（含 [已处理]/[未处理] 标记）",
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
            "### 重新提取模式（re-extract）",
            "当用户要求「重新提取 X-Y 章」「重新更新某几个章节的知识」时，使用重新提取模式：",
            "- 规划阶段：自由读取所有版本（read_wiki(version=N) 可查看历史版本）",
            "- submit_plan 时传 mode: \"re-extract\" + scope",
            "- 执行阶段：系统自动加载基础版本（≤ scope 最大章节的最近版本），无需手动指定",
            "- flush 时系统自动决定插入新版本或覆盖同章节版本",
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
            "### ✍️ 写作质量要求（执行阶段必须遵守）",
            "**先调用 read_index(类别名) 获取该类别的 writing_guide，按规范结构撰写正文。**",
            "| 字段 | 最低要求 | 说明 |",
            "|------|---------|------|",
            "| description | 30-80字 | 一句话概括词条核心身份，禁止只写名字或职位 |",
            "| state | 20-100字 | 当前状态快照（境界/位置/关系/动态），state_required 类别必填 |",
            "| content | ≥300字 | 按类别 writing_guide 分段撰写，使用 [[wikilink]] 交叉引用 |",
            "",
            "**禁止行为**：",
            "- ❌ description 只写「叶家少主」「赤云城家族」等 ≤15字的标签式描述",
            "- ❌ state 只写「肉仙五重」「存在」等 ≤10字的片段",
            "- ❌ content 不参考 writing_guide 结构，只写 2-3 句流水账",
            "- ❌ 跳过 read_index 直接凭记忆写作",
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

        # v5.3: 注入记忆上下文（preference + correction，限 10 条）
        try:
            memory_block = memory_tools.get_memories_for_prompt(
                self.workspace, ["preference", "correction"], limit=10)
            if memory_block:
                parts.append("")
                parts.append(memory_block)
        except Exception:
            pass  # DB 未就绪时静默跳过

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
                    "name": "read_index",
                    "description": "读取指定类别的 index（含 writing_guide 写作规范）。不传 category 则查看所有类别概览。写词条前必须先调用此工具获取写作规范。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "category": {"type": "string", "description": "类别名（不传则查看所有类别）"},
                        },
                    },
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
                    "description": "读取指定 wiki 词条。默认只返回 frontmatter（yaml_only=true），设置 yaml_only=false 可查看全文。先查 category_list 得到类别名再调用。可选传 version 参数读取历史版本。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "category": {"type": "string", "description": "类别名"},
                            "name": {"type": "string", "description": "词条名"},
                            "yaml_only": {"type": "boolean", "description": "是否只返回 frontmatter（默认 true，false 返回全文）"},
                            "version": {"type": "integer", "description": "可选，指定版本的 updated_chapter 值。不传则读取当前版本。"},
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
                    "description": "读取指定剧情卡片。yaml_only=true 只返回 frontmatter。可选传 version 参数读取历史版本。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "剧情卡片名"},
                            "yaml_only": {"type": "boolean", "description": "是否只读 frontmatter（默认 true）"},
                            "version": {"type": "integer", "description": "可选，指定版本的 updated_chapter 值。不传则读取当前版本。"},
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
                    "name": "memory_query",
                    "description": "检索记忆（支持类别过滤 + 关键词模糊匹配）。类别：preference/observation/correction/style",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "category": {"type": "string", "description": "按类别过滤（可选）"},
                            "keyword": {"type": "string", "description": "关键词模糊匹配（可选）"},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "memory_write",
                    "description": "写入新记忆（静默，无需用户确认）。当用户表达偏好、纠正错误、或你观察到跨会话有价值的模式时调用。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "category": {
                                "type": "string",
                                "enum": ["preference", "observation", "correction", "style"],
                                "description": "记忆类别",
                            },
                            "content": {"type": "string", "description": "记忆正文（自然语言）"},
                        },
                        "required": ["category", "content"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "memory_update",
                    "description": "更新已有记忆的内容",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "integer", "description": "记忆 ID"},
                            "content": {"type": "string", "description": "新内容"},
                        },
                        "required": ["id", "content"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "memory_forget",
                    "description": "删除记忆（软删除）",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "integer", "description": "记忆 ID"},
                        },
                        "required": ["id"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "doc_diff",
                    "description": "[DEPRECATED] 已废弃，请使用 chapter_list 替代。查看章节列表（含处理状态）。",
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
                            "content": {"type": "string", "description": "正文内容（按类别 writing_guide 结构撰写，≥300字，使用 [[wikilink]] 交叉引用）"},
                            "description": {"type": "string", "description": "一句话概括词条核心身份（30-80字，如：叶家少主，丹田被废后获寒叔传承走上重修之路）"},
                            "state": {"type": "string", "description": "当前状态快照（20-100字，如：肉仙五重，隐居叶家修炼铁打功，与秦家退婚事件后关系紧张）"},
                            "keywords": {"type": "string", "description": "关键词（逗号分隔）"},
                            "tags": {"type": "array", "items": {"type": "string"}, "description": "标签"},
                        },
                        "required": ["category", "name", "content", "description"],
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
                            "content": {"type": "string", "description": "新正文（保持 ≥300字，按类别 writing_guide 结构）"},
                            "description": {"type": "string", "description": "新描述（30-80字，概括核心身份）"},
                            "state": {"type": "string", "description": "新状态（20-100字，当前快照）"},
                            "keywords": {"type": "string", "description": "新关键词（逗号分隔）"},
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
                            "keywords": {"type": "string", "description": "关键词（逗号分隔）"},
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
                            "keywords": {"type": "string", "description": "新关键词（逗号分隔）"},
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
                    "description": "新建剧情卡片。keywords 为必填项，用于后续检索与写作参考。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "剧情卡片名"},
                            "content": {"type": "string", "description": "正文（需包含 [[wikilink]]）"},
                            "chapters": {"type": "string", "description": "覆盖章节范围，如 \"1-5,7-10\""},
                            "description": {"type": "string", "description": "描述"},
                            "state": {"type": "string", "description": "状态"},
                            "keywords": {"type": "string", "description": "关键词（逗号分隔，必填，如：叶匀,狼山,突破）"},
                        },
                        "required": ["name", "content", "chapters", "keywords"],
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
                            "keywords": {"type": "string", "description": "新关键词（逗号分隔）"},
                        },
                        "required": ["name"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "end_plot",
                    "description": "结束剧情卡片（设置 ended=true），并记录收尾语。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "剧情卡片名"},
                            "end_notes": {"type": "string", "description": "收尾语（简述该剧情线如何完结，如：叶匀击败狼王后离开狼山）"},
                        },
                        "required": ["name", "end_notes"],
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
                            "keywords": {"type": "string", "description": "关键词（逗号分隔）"},
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
                            "keywords": {"type": "string", "description": "新关键词（逗号分隔）"},
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
                                "计划 JSON 包括：scope（提取范围）, mode（可选，\"extract\" 或 \"re-extract\"）, "
                                "new_category, new_wiki, edit_wiki, new_rule, edit_rule, new_plot, edit_plot。"
                                "重新提取时 mode=\"re-extract\"，系统自动加载基础版本。"
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
                                    "content": {"type": "string", "description": "正文内容（按类别 writing_guide 结构撰写，≥300字，使用 [[wikilink]] 交叉引用）"},
                                    "description": {"type": "string", "description": "一句话概括核心身份（30-80字）"},
                                    "state": {"type": "string", "description": "当前状态快照（20-100字，state_required 类别必填）"},
                                    "keywords": {"type": "string", "description": "关键词，逗号分隔"},
                                    "tags": {"type": "array", "items": {"type": "string"}, "description": "标签（可选）"},
                                },
                                "required": ["category", "name", "content", "description"],
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
                                    "content": {"type": "string", "description": "正文内容（保持 ≥300字，按类别 writing_guide 结构）"},
                                    "description": {"type": "string", "description": "描述（30-80字，概括核心身份）"},
                                    "state": {"type": "string", "description": "状态（20-100字，当前快照）"},
                                    "keywords": {"type": "string", "description": "关键词，逗号分隔"},
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
            # 解析 JSON，非 pending_review 状态直接透传（如校验错误）
            try:
                result_data = json.loads(result)
            except json.JSONDecodeError:
                return result

            if result_data.get("status") == "pending_review":
                self.cli.print_plan(result_data)
                confirmed = self.cli.confirm("是否执行此计划？(y/n)")
                if confirmed:
                    plan = result_data.get("plan", {})
                    if self.permission.mode == "review":
                        # 审核阶段：合并到现有白名单，不重置
                        return self.permission.submit_review_plan(plan)
                    else:
                        try:
                            self.permission.submit_plan(plan)
                            # v5.0：加载白名单条目到缓存
                            if hasattr(self, '_proxy') and self._proxy is not None:
                                self._proxy.load_whitelist(plan)
                        except Exception as e:
                            err_msg = f"计划批准流程异常：{e}。请重试或联系管理员。"
                            self.cli.print_info(err_msg)
                            if self.cli.logger:
                                self.cli.logger.write("ERROR", f"submit_plan 异常：{e}")
                            return json.dumps({
                                "status": "error",
                                "message": err_msg,
                            }, ensure_ascii=False)
                        return json.dumps({
                            "status": "approved",
                            "message": "计划已通过，写权限已开放，可以开始执行。",
                            "writing_reminder": (
                                "写作质量要求："
                                "1) 先调用 read_index(类别名) 获取 writing_guide；"
                                "2) description 30-80字概括核心身份；"
                                "3) state 20-100字当前状态快照（state_required类别必填）；"
                                "4) content ≥300字，按 writing_guide 结构分段，使用[[wikilink]]交叉引用。"
                            )
                        }, ensure_ascii=False)
                else:
                    self.cli.print_info("请输入打回理由：")
                    reason = self.cli.read_line() or ""
                    return json.dumps({
                        "status": "rejected",
                        "reason": reason,
                        "message": f"计划被打回，理由：{reason}。请根据理由修改后重新提交。",
                        "memory_hint": (
                            "重要：用户的打回理由可能包含跨会话有价值的纠正信息。"
                            "请判断是否值得调用 memory_write(category='correction', content=...) "
                            "将用户的纠正意见记录为记忆，以便后续会话不再犯同样的错误。"
                        )
                    }, ensure_ascii=False)

            return result

        if name == "review_workflow":
            from tools.workflow import review_workflow as _review_workflow
            self._archive_context()
            self.permission.switch_review()
            # v5.0：进入审核前暂存缓存
            if hasattr(self, '_proxy') and self._proxy is not None and self._proxy.is_cache_loaded():
                snapshot_dir = self.workspace / "session"
                snapshot_dir.mkdir(parents=True, exist_ok=True)
                snapshot_path = snapshot_dir / "cache_snapshot.json"
                self._proxy.snapshot(snapshot_path)
            # 自动运行 lint（任务内：只检查白名单内文档）
            try:
                from tools.lint import run_lint
                lint_whitelist = None
                if hasattr(self, '_proxy') and self._proxy is not None and self._proxy.is_cache_loaded():
                    lint_whitelist = [
                        (doc.doc_type, doc.name)
                        for (_, _), doc in self._proxy._cache.items()
                        if not doc.is_deleted
                    ]
                chapter_scope = sorted(self.permission.whitelist.read_chapters or [])
                lint_result = run_lint(self.workspace, whitelist=lint_whitelist,
                                       chapter_scope=chapter_scope or None)
            except Exception:
                lint_result = "（lint 检查异常）"
            # v5.4.2：断链重要性等级3审核节点
            forced_debts_approved = []
            forced = self._extract_forced_debts()
            if forced:
                approved, rejected = self._audit_forced_debts(forced)
                if rejected:
                    self._unlink_rejected(rejected)
                forced_debts_approved = approved
            # 标记等待 chat() 清理上下文（不清 self.messages，避免 agent_loop 的 tool result 链断裂）
            self._review_pending = {"lint_result": lint_result, "forced_debts": forced_debts_approved}
            self._stop_agent_loop = True
            return _review_workflow(self.workspace)

        if name == "finish_task":
            from tools.workflow import finish_task as _wf_finish
            from tools.diff import finish_task as _diff_finish
            # v5.4.2：检查强制债务是否已解决
            forced_unresolved = self._check_forced_debt_resolved()
            if forced_unresolved:
                return json.dumps({
                    "status": "error",
                    "message": (
                        f"强制债务未解决：{', '.join(forced_unresolved)}。"
                        f"请先通过 submit_plan 申请白名单扩展并创建对应词条。"
                    )
                }, ensure_ascii=False)
            # 沿用旧 finish_task 逻辑：校验存在性 + 记录 log.json + 构建关系图
            chapter_range = sorted(self.permission.whitelist.read_chapters or [])
            # 将章节列表转为合法的 spec 字符串（如 "1,2,3,4,5"）
            scope = ",".join(str(ch) for ch in chapter_range) if chapter_range else "0"
            # 从 proxy 缓存中收集新建/更新的条目名称
            new_wiki, updated_wiki = [], []
            new_rules, updated_rules = [], []
            new_plots, updated_plots = [], []
            if hasattr(self, '_proxy') and self._proxy is not None:
                for (doc_type, _), doc in self._proxy._cache.items():
                    if doc.is_deleted:
                        continue
                    if doc_type == "wiki":
                        (new_wiki if doc.is_new else updated_wiki).append(doc.name)
                    elif doc_type == "rule":
                        (new_rules if doc.is_new else updated_rules).append(doc.name)
                    elif doc_type == "plot":
                        (new_plots if doc.is_new else updated_plots).append(doc.name)
            # 仅在有合法章节范围时记录日志和 flush（防止空 chapters 的伪调用清空缓存）
            if chapter_range:
                _diff_finish(self.workspace, scope,
                             new_wiki=new_wiki, updated_wiki=updated_wiki,
                             new_rules=new_rules, updated_rules=updated_rules,
                             new_plots=new_plots, updated_plots=updated_plots)
                # v5.0：flush 缓存到 DB
                if hasattr(self, '_proxy') and self._proxy is not None and self._proxy.is_cache_loaded():
                    scope_chapter = chapter_range[-1]
                    try:
                        self._proxy.flush(scope_chapter=scope_chapter)
                    except Exception as e:
                        self.cli.print_info(f"DB flush 失败：{e}")
                        return json.dumps({
                            "status": "error",
                            "message": f"DB 写入失败：{e}。缓存已保留，可重试。"
                        }, ensure_ascii=False)
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
            "read_index": lambda **kw: category_tools.read_index(self.workspace, **kw),
            "wiki_list": lambda **kw: wiki_tools.wiki_list(self.workspace, **kw),
            "read_wiki": self._handle_read_wiki,
            "check_wiki": lambda **kw: wiki_tools.check_wiki(self.workspace, **kw),
            "query_relations": lambda **kw: relation_tools.query_relations(self.workspace, **kw),
            "read_plot": self._handle_read_plot,
            "plot_list": lambda **kw: plot_tools.plot_list(self.workspace, **kw),
            "query_plot_by_chapters": lambda **kw: plot_tools.query_plot_by_chapters(self.workspace, **kw),
            "rules_list": lambda **kw: rules_tools.rules_list(self.workspace),
            "read_rule": lambda **kw: rules_tools.read_rule(self.workspace, **kw),
            "read_memory": lambda **kw: memory_tools.read_memory(self.workspace, **kw),
            "memory_query": lambda **kw: memory_tools.memory_query(self.workspace, **kw),
            "memory_write": lambda **kw: memory_tools.memory_write(
                self.workspace, source="chat", **kw),
            "memory_update": lambda **kw: memory_tools.memory_update(self.workspace, **kw),
            "memory_forget": lambda **kw: memory_tools.memory_forget(self.workspace, **kw),
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
            ch = self._db_service.chapter_get(n)
            if ch and ch["title"]:
                titles.append(f"第{n}章 {ch['title']}")
            else:
                titles.append(f"第{n}章")
        self.context.track_chapter(
            [str(n) for n in nums],
            titles,
        )
        return result

    def _handle_keywords_stat(self, chapters: str, keywords: list) -> str:
        return chapter_tools.keywords_stat(self.workspace, chapters, keywords)

    def _handle_chapter_list(self) -> str:
        return chapter_tools.chapter_list(self.workspace)

    def _handle_read_wiki(self, category: str = "", name: str = "",
                          yaml_only: bool = True, version: int = None, **kw) -> str:
        """读取 wiki（re-extract 执行阶段忽略 version 参数，返回缓存中的基础版本）"""
        if (version is not None
                and self.permission.mode == "executing"
                and hasattr(self, '_proxy') and self._proxy is not None):
            # 重新提取执行阶段：白名单内词条忽略 version，走缓存
            cached = self._proxy._find_in_cache("wiki", name)
            if cached and cached.base_version_id is not None:
                version = None  # 强制走缓存路径
        return wiki_tools.read_wiki(self.workspace, category=category,
                                    name=name, yaml_only=yaml_only, version=version)

    def _handle_read_plot(self, name: str = "",
                          yaml_only: bool = True, version: int = None, **kw) -> str:
        """读取 plot（re-extract 执行阶段忽略 version 参数，返回缓存中的基础版本）"""
        if (version is not None
                and self.permission.mode == "executing"
                and hasattr(self, '_proxy') and self._proxy is not None):
            cached = self._proxy._find_in_cache("plot", name)
            if cached and cached.base_version_id is not None:
                version = None
        return plot_tools.read_plot(self.workspace, name=name,
                                    yaml_only=yaml_only, version=version)

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

    # ---- v5.4.2 断链重要性审核 ----

    def _extract_forced_debts(self) -> list[dict]:
        """从 lint-debt.json 读取重要性等级≥2的断链条目"""
        fp = self.workspace / DEBT_FILE
        if not fp.exists():
            return []
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
            scores = data.get("importance_scores", {})
            forced = []
            for target, info in scores.items():
                if info.get("level", 0) >= 2:
                    forced.append({"target": target, **info})
            # 按等级降序、词频降序排列
            forced.sort(key=lambda x: (-x.get("level", 0), -x.get("frequency", 0)))
            return forced
        except Exception:
            return []

    def _audit_forced_debts(self, forced: list[dict]) -> tuple[list[dict], list[dict]]:
        """阻塞式用户审核：返回 (approved, rejected)"""
        self.cli.print_info("")
        self.cli.print_info("⚠️ 以下断链实体重要性等级≥2，进入强制债务审核：")
        for i, item in enumerate(forced, 1):
            self.cli.print_info(
                f"  [{i}] {item['target']}"
                f"（等级{item.get('level', '?')} / "
                f"{item['mention_count']}条目提及 / "
                f"词频{item['frequency']} / "
                f"覆盖{item['chapter_count']}章）"
            )
        self.cli.print_info("")
        self.cli.print_info('回车=全部通过，输入拒绝编号（如 "2" 或 "1,2"）：')

        response = self.cli.read_line()
        # None (Ctrl+C) 或空字符串（回车）→ 全部通过
        if response is None or response.strip() == "":
            return forced, []

        # 解析拒绝编号
        try:
            reject_ids = {int(x.strip()) for x in response.split(",") if x.strip()}
        except ValueError:
            # 无法解析→全部通过
            return forced, []

        approved = []
        rejected = []
        for i, item in enumerate(forced, 1):
            if i in reject_ids:
                rejected.append(item)
            else:
                approved.append(item)

        if rejected:
            names = ", ".join(item["target"] for item in rejected)
            self.cli.print_info(f"已拒绝：{names}，将自动取消链接。")

        return approved, rejected

    def _unlink_rejected(self, rejected: list[dict]):
        """对被拒绝的断链目标执行 unlink（遍历 proxy 缓存中所有文档）"""
        import re as _re
        if not hasattr(self, '_proxy') or self._proxy is None:
            return

        targets = {item["target"] for item in rejected}

        for (doc_type, _), doc in list(self._proxy._cache.items()):
            if doc.is_deleted or not doc.content:
                continue
            # 检查是否包含需要 unlink 的目标
            has_target = any(t in doc.content for t in targets)
            if not has_target:
                continue

            new_content = doc.content
            for target in targets:
                target_lower = target.strip().lower()

                def _make_unlinker(t_lower):
                    def _replace(match):
                        raw_target = match.group(1)
                        raw_alias = match.group(2) or ""
                        if raw_target.strip().lower() == t_lower:
                            return raw_alias.lstrip("|") if raw_alias else raw_target
                        return match.group(0)
                    return _replace

                new_content = _re.sub(
                    r"\[\[([^\]|]+?)(\|[^\]]+?)?\]\]",
                    _make_unlinker(target_lower), new_content,
                )

            if new_content != doc.content:
                self._proxy.update_doc(
                    doc_type=doc_type, name=doc.name,
                    category=doc.category or None,
                    content=new_content, chapter=0
                )

    def _check_forced_debt_resolved(self) -> list[str]:
        """检查强制债务是否已解决，返回未解决的 target 列表"""
        forced_debts = []
        if hasattr(self, '_review_pending') and self._review_pending:
            forced_debts = self._review_pending.get("forced_debts", [])
        if not forced_debts:
            return []

        unresolved = []
        for item in forced_debts:
            target = item["target"] if isinstance(item, dict) else item
            # 检查 proxy 中是否已存在该 wiki 词条
            if hasattr(self, '_proxy') and self._proxy is not None:
                if self._proxy.find_doc("wiki", target) is None:
                    unresolved.append(target)
            else:
                unresolved.append(target)
        return unresolved

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

        # v5.4.2：注入强制创建清单
        forced_debts = []
        if hasattr(self, '_review_pending') and self._review_pending:
            forced_debts = self._review_pending.get("forced_debts", [])
        if forced_debts:
            parts.append("### 🚨🚨🚨 强制债务（最高优先级 — 必须首先处理）")
            parts.append("")
            parts.append("**以下实体已经用户审核确认，必须创建词条。这是不可跳过的硬性要求。**")
            parts.append("**你必须在处理其他任何债务之前，先完成以下所有实体的创建。**")
            parts.append("**不得忽略、跳过、或以任何理由拒绝创建。不得对这些实体执行 unlink。**")
            parts.append("")
            for item in forced_debts:
                t = item["target"] if isinstance(item, dict) else item
                if isinstance(item, dict):
                    parts.append(
                        f"- **{t}**（等级{item.get('level', '?')} / "
                        f"{item.get('mention_count', '?')}条目提及 / "
                        f"词频{item.get('frequency', '?')} / "
                        f"覆盖{item.get('chapter_count', '?')}章）"
                    )
                else:
                    parts.append(f"- **{t}**")
            parts.append("")
            parts.append("**执行步骤（每个实体都必须完成）：**")
            parts.append("1. 调用 `submit_plan` 提交包含所有强制实体的 new_wiki 计划（必要时含 new_category）")
            parts.append("2. 用户确认后，逐个调用 `new_wiki` 创建词条（内容≥300字）")
            parts.append("3. 创建完成后才可继续处理其他债务")
            parts.append("")
            parts.append("❗❗ finish_task 会校验上述实体是否已全部创建。任何一个未创建都将阻塞任务结束。")
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
        if forced_debts:
            parts.append("**禁止**：上方「强制债务」清单中的实体不适用④，不得 unlink，必须走③流程创建。违反将导致 finish_task 永久阻塞。")
            parts.append("")
        parts.append("### 未结束剧情卡片处理规则")
        parts.append("lint 报告的 unended_plots 表示剧情卡片章节范围已结束但未标记收尾。")
        parts.append("对于这些卡片，调用 `end_plot(name=\"...\", end_notes=\"...\")` 结束，end_notes 必须填写收尾语。")
        parts.append("判断标准：卡片的最大章节号 ≤ 最新章节号 - 10，且卡片故事内容已自然完结。")
        parts.append("")
        parts.append("### 剧情卡片 keywords 检查")
        parts.append("审核时检查所有剧情卡片的 keywords 是否为空。若为空，用 `edit_plot(name=\"...\", keywords=\"...\")` 补充。")
        parts.append("keywords 应包含该卡片涉及的核心人物、地点、事件关键词，逗号分隔。")
        parts.append("")
        parts.append("### 词条字段质量检查（重要）")
        parts.append("审核时用 `read_wiki` 抽查词条，检查以下字段质量：")
        parts.append("| 字段 | 最低要求 | 不合格示例 |")
        parts.append("|------|---------|-----------|")
        parts.append("| description | 30-80字 | 「叶家少主」「赤云城家族」等 ≤15字标签 |")
        parts.append("| state | 20-100字 | 「肉仙五重」「存在」等 ≤10字片段 |")
        parts.append("| content | ≥300字 | 只有 2-3 句话的流水账 |")
        parts.append("")
        parts.append("不合格时用 `edit_wiki` 或 `batch_edit_wiki` 补充完善。")
        parts.append("description 应概括词条核心身份与特征，state 应反映当前境界/位置/关系/动态，")
        parts.append("content 应按类别 writing_guide 结构分段撰写（先调用 read_index 获取规范）。")

        self.messages.append({
            "role": "system",
            "content": "\n".join(parts)
        })
