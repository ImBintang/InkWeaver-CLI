"""正文后处理工具"""

import re


def polish_draft(text: str) -> str:
    """后处理 Writer 产出的草稿：
    1. strip 首尾空白
    2. 删除所有以 # 开头的行（AI 自加标题）
    3. 全角置换标点符号
    """
    # 1. strip
    text = text.strip()

    # 2. 删除标题行
    lines = text.split("\n")
    lines = [l for l in lines if not l.strip().startswith("#")]
    text = "\n".join(lines).strip()

    # 3. 全角置换（使用正则精确匹配成对引号）
    def _replace_quotes(match):
        """将成对的直引号替换为弯引号"""
        content = match.group(0)
        result = []
        depth = 0
        for ch in content:
            if ch in ('"', "'"):
                if depth % 2 == 0:
                    result.append("\u201c" if ch == '"' else "\u2018")
                else:
                    result.append("\u201d" if ch == '"' else "\u2019")
                depth += 1
            else:
                result.append(ch)
        return "".join(result)

    # 仅在中文语境下替换（前面有中文或行首的引号）
    text = re.sub(
        r'(?<=[一-鿿，。！？；：、）】\s])"[^"]*"(?=[\s，。！？；：、（【\u4e00-鿿])',
        _replace_quotes, text
    )
    text = re.sub(
        r'(?<=[一-鿿，。！？；：、）】\s])\'[^\']*\'(?=[\s，。！？；：、（【\u4e00-鿿])',
        _replace_quotes, text
    )
    text = text.replace("---", "\u2014\u2014")  # 双破折号
    text = text.replace("--", "\u2014")  # 单破折号
    text = text.replace("...", "\u2026\u2026")  # 省略号
    # 仅替换中文语境下的冒号（排除 URL 和数字时间）
    text = re.sub(r'(?<=[一-鿿]):(?=[\u4e00-鿿（【\s])', "\uff1a", text)

    return text
