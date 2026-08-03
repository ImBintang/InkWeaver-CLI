"""InkWeaver-CLI 入口程序 — typer 子命令化"""

import sys
import typer

from commands.workspace import app as workspace_app
from commands.chapter import app as chapter_app
from commands.kb import app as kb_app
from commands.settings import app as settings_app
from commands.chat import chat
from commands.ask import ask
from commands.extract import extract
from commands.muse_cmd import muse
from commands.serve import serve

app = typer.Typer(
    name="inkweaver",
    help="InkWeaver-CLI 小说知识库与写作辅助工具",
    no_args_is_help=True,
)

# 注册子命令组
app.add_typer(workspace_app, name="workspace", help="工作区管理")
app.add_typer(chapter_app, name="chapter", help="章节管理")
app.add_typer(kb_app, name="kb", help="知识库查询（wiki/rule/plot）")
app.add_typer(settings_app, name="settings", help="配置管理（模型/分配）")

# 注册顶级命令
app.command(name="chat")(chat)
app.command(name="ask")(ask)
app.command(name="extract")(extract)
app.command(name="muse")(muse)
app.command(name="serve")(serve)


def app_entry():
    """pyproject.toml entry_points 入口"""
    app()


if __name__ == "__main__":
    # 开发期兼容：python main.py 等价于 inkweaver chat
    if len(sys.argv) == 1:
        sys.argv.append("chat")
    app()
