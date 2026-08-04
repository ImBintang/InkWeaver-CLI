"""Skill 注册与按需加载（参照 s05）"""

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class SkillManifest:
    name: str
    description: str


@dataclass
class SkillDocument:
    manifest: SkillManifest
    body: str


class SkillRegistry:
    """扫描 skills/ 目录，按需加载 SKILL.md"""

    def __init__(self, skills_dir: Path):
        self.skills_dir = skills_dir
        self.documents: dict[str, SkillDocument] = {}
        self._load_all()

    def _load_all(self):
        if not self.skills_dir.exists():
            return
        for path in sorted(self.skills_dir.rglob("*.skill.md")):
            # v7.0.1: 非 UTF-8 技能文件不再让整个注册表加载失败，跳过并告警
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError) as e:
                print(f"[WARN] 跳过技能文件（读取失败）{path}: {e}")
                continue
            meta, body = self._parse_frontmatter(text)
            name = meta.get("name", path.parent.name)
            description = meta.get("description", "No description")
            self.documents[name] = SkillDocument(
                manifest=SkillManifest(name=name, description=description),
                body=body.strip(),
            )

    def _parse_frontmatter(self, text: str) -> tuple[dict, str]:
        """解析 YAML 风格 frontmatter"""
        match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)", text, re.DOTALL)
        if not match:
            return {}, text
        meta = {}
        for line in match.group(1).strip().splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            meta[key.strip()] = value.strip()
        return meta, match.group(2)

    def describe_available(self) -> str:
        """返回技能清单"""
        if not self.documents:
            return "(暂无可用技能)"
        lines = []
        for name in sorted(self.documents):
            d = self.documents[name].manifest.description
            lines.append(f"- {name}: {d}")
        return "\n".join(lines)

    def load_full_text(self, name: str) -> str:
        """按需加载技能全文"""
        doc = self.documents.get(name)
        if not doc:
            known = ", ".join(sorted(self.documents)) or "(无)"
            return f"错误：未知技能「{name}」，可用技能：{known}"
        return (
            f"<skill name=\"{doc.manifest.name}\">\n"
            f"{doc.body}\n"
            "</skill>"
        )

    def skill_names(self) -> list[str]:
        return sorted(self.documents.keys())
