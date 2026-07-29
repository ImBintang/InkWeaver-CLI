"""chat 命令 — 鉴知对话 REPL"""

import threading
import typer

from commands.common import (
    load_config, save_config, get_workspaces_dir, resolve_workspace,
    require_workspace, make_io, SKILLS_DIR
)
from core.output import OutputFormatter
from core.events import EventBus, EventType


_HELP_TEXT = """可用指令：

  会话控制：
    /exit              退出
    /help              显示本帮助
    /clear             清空对话上下文
    /compact           主动压缩上下文
    /context           查看上下文占用与组成
    /token             查询本会话累计 token 用量

  快速查询：
    /chapters [-n]     列出最新N章（默认50）
    /show <num>        展示指定章节内容
    /status            章节处理状态
    /wiki <name>       查看指定词条
    /rule [name]       查看规则（无参数=列表）
    /relation <name>   查询词条关联
    /memory            查看记忆列表

  记忆：
    /remember <text>   写入一条记忆（默认 preference 类）
    /forget <id>       删除指定记忆

  操作：
    /extract           触发知识提取
"""


def chat(
    workspace: str = typer.Option("", "--workspace", "-w", help="工作区名"),
):
    """进入鉴知对话 REPL"""
    config = load_config()
    ws = resolve_workspace(config, workspace)

    if ws is None:
        print("尚无工作区，请先使用 inkweaver workspace create <名称> 创建。")
        raise typer.Exit(1)

    # 更新 config 中的 last
    config.setdefault("workspace", {})["last"] = ws.name
    save_config(config)

    # 创建 I/O 通道
    io = make_io(json_mode=False, workspace=ws, mode="chat")
    io.print_info(f"进入工作区：{ws.name}")
    io.print_info("输入 /help 查看可用指令。")

    # 初始化 Agent（使用事件总线）
    from Jianzhi import JianzhiAgent
    bus = EventBus()
    jianzhi = JianzhiAgent(config, ws, SKILLS_DIR, bus)

    # 启动事件消费线程（将事件翻译为 CLI 输出）
    consumer = _CLIConsumer(io, bus)
    consumer_thread = threading.Thread(target=consumer.run, daemon=True)
    consumer_thread.start()

    # REPL 主循环
    while True:
        text = io.read_line()
        if text is None:
            break

        stripped = text.strip()
        if not stripped:
            continue

        # 斜杠指令
        if stripped.startswith("/"):
            cmd_text = stripped[1:]
            io.log_cli(cmd_text)
            if not _handle_slash(cmd_text, io, jianzhi, config, ws):
                break
        else:
            # 普通对话 — 在独立线程中运行 Agent
            agent_done = threading.Event()

            def _run_agent():
                try:
                    jianzhi.chat(text)
                except Exception as e:
                    bus.emit(EventType.ERROR, {"text": f"Agent 异常：{e}"}, source="jianzhi")
                finally:
                    bus.emit(EventType.TASK_DONE, {}, source="jianzhi")
                    agent_done.set()

            agent_thread = threading.Thread(target=_run_agent, daemon=True)
            agent_thread.start()

            # 主线程等待 Agent 完成（事件由后台消费线程统一处理）
            consumer.wait_for_done(agent_done)

    consumer.stop()
    io.close_logger()
    io.print_info("再见！")


class _CLIConsumer:
    """事件消费者 — 将 EventBus 事件翻译为 CLI 输出（CLIAdapter 雏形）

    单消费者模型：所有事件（含确认交互）均由后台 run() 线程处理，
    主线程仅通过 wait_for_done() 等待 Agent 完成，避免双线程竞争队列。
    """

    def __init__(self, io, bus: EventBus):
        self.io = io
        self.bus = bus
        self._running = True

    def run(self):
        """消费循环 — 在独立线程中运行，统一处理所有事件"""
        while self._running:
            event = self.bus.get(timeout=0.05)
            if event is None:
                continue
            try:
                self._handle(event)
            except Exception as e:
                # 兜底：确认类事件异常时自动放行，避免 Agent 线程永久阻塞
                if event.type == EventType.CONFIRM_REQUEST:
                    cid = event.data.get("confirm_id", "")
                    if cid:
                        self.bus.resolve_confirm(cid, {"action": "approve"})
                try:
                    self.io.print_info(f"事件处理异常：{e}")
                except Exception:
                    pass

    def stop(self):
        self._running = False

    def wait_for_done(self, agent_done: 'threading.Event'):
        """主线程等待 Agent 完成（事件由 run() 线程统一消费）"""
        while not agent_done.is_set():
            agent_done.wait(timeout=0.1)
        # 等待消费线程处理完剩余事件
        import time
        time.sleep(0.2)

    def _handle(self, event):
        """处理单个事件"""
        match event.type:
            case EventType.TOKEN:
                pass  # CLI 不逐 token 输出，等 OUTPUT 事件
            case EventType.OUTPUT:
                self.io.print_output(event.data["text"])
            case EventType.THINKING:
                self.io.print_thinking("思考中...")
            case EventType.THINKING_DONE:
                self.io.print_thinking_done(event.data.get("elapsed", 0))
            case EventType.REASONING:
                self.io.print_reasoning(event.data["text"])
            case EventType.TOOL_CALL:
                self.io.print_tool_call(event.data["name"], event.data.get("brief", ""))
            case EventType.TOOL_RESULT:
                self.io.print_tool_result(event.data["msg"])
            case EventType.INFO:
                self.io.print_info(event.data["text"])
            case EventType.ERROR:
                self.io.print_info(f"错误：{event.data['text']}")
            case EventType.TOKEN_STATS:
                # 写入日志
                if self.io.logger:
                    accum = event.data.get("accum", {})
                    self.io.logger.write(
                        "TOKEN",
                        f"本次: input={event.data['input']}, output={event.data['output']} | "
                        f"累计: input={accum.get('input',0)}, output={accum.get('output',0)}, total={accum.get('total',0)}"
                    )
            case EventType.CONFIRM_REQUEST:
                self._handle_confirm(event)
            case _:
                pass

    def _handle_confirm(self, event):
        """处理确认请求 — 复用现有 IOChannel 的阻塞式交互"""
        confirm_id = event.data["confirm_id"]
        confirm_type = event.data["confirm_type"]
        payload = event.data["payload"]

        if confirm_type == "plan":
            self.io.print_plan(payload)
            confirmed = self.io.confirm("是否执行此计划？(y/n)")
            if confirmed:
                self.bus.resolve_confirm(confirm_id, {"action": "approve"})
            else:
                self.io.print_info("请输入打回理由：")
                reason = self.io.read_line() or ""
                self.bus.resolve_confirm(confirm_id, {"action": "reject", "reason": reason})

        elif confirm_type == "forced_debt":
            items = payload.get("items", [])
            self.io.print_info("")
            self.io.print_info("⚠️ 以下断链实体重要性等级≥2，进入强制债务审核：")
            for i, item in enumerate(items, 1):
                self.io.print_info(
                    f"  [{i}] {item['target']}"
                    f"（等级{item.get('level', '?')} / "
                    f"{item.get('mention_count', 0)}条目提及 / "
                    f"词频{item.get('frequency', 0)} / "
                    f"覆盖{item.get('chapter_count', 0)}章）"
                )
            self.io.print_info("")
            self.io.print_info('回车=全部通过，输入拒绝编号（如 "2" 或 "1,2"）：')
            response = self.io.read_line()
            if response is None or response.strip() == "":
                self.bus.resolve_confirm(confirm_id, {"action": "approve_all"})
            else:
                try:
                    reject_ids = {int(x.strip()) - 1 for x in response.split(",") if x.strip()}
                except ValueError:
                    reject_ids = set()
                self.bus.resolve_confirm(confirm_id, {"rejected_indices": list(reject_ids)})

        else:
            # 未知确认类型，默认通过
            self.bus.resolve_confirm(confirm_id, {"action": "approve"})


def _handle_slash(cmd: str, io, jianzhi, config: dict, ws) -> bool:
    """处理斜杠指令，返回 False 表示退出"""
    parts = cmd.strip().split()
    if not parts:
        return True

    command = parts[0].lower()

    if command == "exit":
        return False

    elif command == "help":
        io.print_info(_HELP_TEXT)

    elif command == "clear":
        jianzhi.clear_context()

    elif command == "compact":
        jianzhi.compact_history()

    elif command == "context":
        io.print_info(jianzhi.context_report())

    elif command == "token":
        io.print_info(jianzhi.token_report())

    elif command == "chapters":
        n = 50
        # 支持 /chapters 5 和 /chapters -n 5 两种格式
        args = parts[1:]
        if "-n" in args:
            idx = args.index("-n")
            if idx + 1 < len(args):
                try:
                    n = int(args[idx + 1])
                except ValueError:
                    pass
        elif args:
            try:
                n = int(args[0])
            except ValueError:
                pass
        from tools import workspace as workspace_tools
        io.print_info(workspace_tools.list_latest_chapters(ws, n))

    elif command == "show":
        if len(parts) < 2:
            io.print_info("用法：/show <章节号>")
            return True
        try:
            num = int(parts[1])
        except ValueError:
            io.print_info("错误：章节号必须为整数")
            return True
        from tools.chapter import show_chapter
        io.print_info(show_chapter(ws, num))

    elif command == "status":
        from tools.chapter import chapter_list
        io.print_info(chapter_list(ws))

    elif command == "wiki":
        if len(parts) < 2:
            io.print_info("用法：/wiki <词条名>")
            return True
        name = parts[1]
        from tools.editor import _get_proxy
        proxy = _get_proxy(ws)
        cats = proxy.list_categories()
        found = False
        for cat in cats:
            result = proxy.read_doc("wiki", name, category=cat["name"], yaml_only=False)
            if not result.startswith("错误"):
                io.print_info(result)
                found = True
                break
        if not found:
            io.print_info(f"词条「{name}」不存在")

    elif command == "rule":
        from tools.rules import rules_list, read_rule
        if len(parts) > 1:
            io.print_info(read_rule(ws, parts[1]))
        else:
            io.print_info(rules_list(ws))

    elif command == "relation":
        if len(parts) < 2:
            io.print_info("用法：/relation <词条名>")
            return True
        from tools.relation import query_relations
        io.print_info(query_relations(ws, parts[1]))

    elif command == "memory":
        from tools.memory import read_memory
        io.print_info(read_memory(ws, None))

    elif command == "remember":
        if len(parts) < 2:
            io.print_info("用法：/remember <记忆内容>")
            return True
        text = " ".join(parts[1:])
        from tools.memory import memory_write
        result = memory_write(ws, category="preference", content=text, source="user")
        io.print_info(result)

    elif command == "forget":
        if len(parts) < 2:
            io.print_info("用法：/forget <记忆ID>")
            return True
        try:
            memory_id = int(parts[1].lstrip("#"))
        except ValueError:
            io.print_info("错误：记忆 ID 必须为整数")
            return True
        from tools.memory import memory_forget
        result = memory_forget(ws, id=memory_id)
        io.print_info(result)

    elif command == "extract":
        io.print_info("触发知识提取...")
        _bus = jianzhi.bus
        agent_done = threading.Event()

        def _run_extract():
            try:
                jianzhi.chat("请执行知识提取流程")
            except Exception as e:
                _bus.emit(EventType.ERROR, {"text": f"Agent 异常：{e}"}, source="jianzhi")
            finally:
                _bus.emit(EventType.TASK_DONE, {}, source="jianzhi")
                agent_done.set()

        threading.Thread(target=_run_extract, daemon=True).start()
        # 等待 Agent 完成（事件由后台消费线程统一处理）
        import time as _time
        while not agent_done.is_set():
            agent_done.wait(timeout=0.1)
        _time.sleep(0.2)  # 等待消费线程处理完剩余事件

    else:
        io.print_info(f"未知指令：/{command}，输入 /help 查看可用指令")

    return True
