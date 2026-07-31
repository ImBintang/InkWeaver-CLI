"""SessionManager — JSONL-backed storage for chat sessions in one book."""

import json
import sys
import time
import secrets
from pathlib import Path
from typing import Any


class SessionFullError(Exception):
    """会话消息数已达上限"""
    def __init__(self, session_id: str):
        self.session_id = session_id
        super().__init__(f"会话「{session_id}」消息数已达上限")


class SessionNotFound(Exception):
    """会话不存在"""
    def __init__(self, session_id: str):
        self.session_id = session_id
        super().__init__(f"会话「{session_id}」不存在")


class SessionManager:
    def __init__(self, workspace_path: Path, sessions_dir: Path, default_cap: int = 500):
        self.workspace = workspace_path
        self.sessions_dir = sessions_dir
        self.index_path = sessions_dir / "index.json"
        self.default_cap = default_cap
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def generate_id() -> str:
        ts = time.strftime("%Y%m%d_%H%M%S")
        rand = secrets.token_hex(3)
        return f"sess_{ts}_{rand[:5]}"

    def sess_file_path(self, sid: str) -> Path:
        return self.sessions_dir / f"{sid}.jsonl"

    def _empty_index(self) -> dict:
        return {"current_session_id": None, "sessions": []}

    def load_index(self) -> dict:
        if not self.index_path.exists():
            return self._empty_index()
        try:
            data = json.loads(self.index_path.read_text(encoding="utf-8"))
            if not isinstance(data.get("sessions"), list):
                return self._empty_index()
            return data
        except (json.JSONDecodeError, OSError) as e:
            # 不静默：索引损坏被静默重置会让用户看到"会话全部丢失"
            # 且无法排查；打印警告到 stderr（消费端：服务日志）
            print(f"[session_manager] 会话索引损坏，已重建空索引（{self.index_path}）：{e}",
                  file=sys.stderr)
            return self._empty_index()

    def _save_index(self, index: dict):
        tmp = self.index_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.index_path)

    def _meta_summary(self, meta: dict) -> dict:
        return {
            "id": meta["id"], "name": meta.get("name", ""),
            "archived": meta.get("archived", False),
            "created_at": meta.get("created_at", ""),
            "updated_at": meta.get("updated_at", ""),
            "message_count": meta.get("message_count", 0),
            "first_user_message": meta.get("first_user_message", ""),
            "cap": meta.get("cap", self.default_cap),
        }

    def load_sessions(self, include_archived: bool = True) -> list[dict]:
        idx = self.load_index()
        return idx["sessions"] if include_archived else [s for s in idx["sessions"] if not s.get("archived")]

    def _parse_jsonl(self, fpath: Path) -> tuple[dict | None, list[dict]]:
        if not fpath.exists():
            return None, []
        raw = fpath.read_text(encoding="utf-8").rstrip("\n")
        if not raw:
            return None, []
        lines = raw.split("\n")
        end = len(lines)
        while end > 0:
            try:
                json.loads(lines[end - 1])
                break
            except json.JSONDecodeError:
                end -= 1
        meta, messages = None, []
        skipped = 0
        for line in lines[:end]:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                # 不静默：跳过损坏行（尾部截断容错），但统计数量并在
                # 非零时打印警告——文件损坏必须可发现（消费端：服务日志）
                skipped += 1
                continue
            if obj.get("type") == "meta":
                meta = obj
            elif obj.get("type") == "message":
                messages.append(obj)
        if skipped:
            print(f"[session_manager] 会话文件含 {skipped} 行损坏 JSON（{fpath}），已跳过",
                  file=sys.stderr)
        return meta, messages

    def _rewrite_meta_line(self, fpath: Path, new_meta: dict):
        lines = []
        if fpath.exists():
            raw = fpath.read_text(encoding="utf-8").rstrip("\n")
            lines = raw.split("\n") if raw else []
        idx = next((i for i, l in enumerate(lines) if l.strip()), None)
        s = json.dumps(new_meta, ensure_ascii=False)
        if idx is not None:
            lines[idx] = s
            content = "\n".join(lines) + "\n"
        else:
            content = s + "\n"
        tmp = fpath.with_suffix(".tmp")
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(fpath)

    def _read_meta_only(self, fpath: Path) -> dict | None:
        """只读取 JSONL 首行 meta（不解析全部消息）

        P1-21：add_message / save_pending_confirm 等高频路径原用 get_session
        全量解析，每追加一条消息 O(n)，长会话 O(n²) 膨胀。本方法只读首行。
        """
        if not fpath.exists():
            return None
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                first = f.readline().strip()
            if not first:
                return None
            obj = json.loads(first)
            return obj if obj.get("type") == "meta" else None
        except json.JSONDecodeError as e:
            # 不静默：meta 损坏意味着会话不可恢复，不能与"会话不存在"
            # 混为一谈；打印真实原因，调用方仍按不存在处理但日志可查
            print(f"[session_manager] 会话 meta 损坏（{fpath}）：{e}", file=sys.stderr)
            return None
        except OSError as e:
            print(f"[session_manager] 读取会话 meta 失败（{fpath}）：{e}", file=sys.stderr)
            return None

    def get_session(self, sid: str) -> dict:
        fpath = self.sess_file_path(sid)
        meta, messages = self._parse_jsonl(fpath)
        if meta is None:
            raise SessionNotFound(sid)
        return {**meta, "messages": messages, "compact_summary": meta.get("compact_summary", ""),
                "pending_confirm": meta.get("pending_confirm"),
                "cap": meta.get("cap", self.default_cap),
                "message_count": meta.get("message_count", 0)}

    def create_session(self, name: str, cap: int | None = None) -> str:
        sid = self.generate_id()
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        meta = {"type": "meta", "id": sid, "name": name, "created_at": now, "updated_at": now,
                "archived": False, "compact_summary": "", "pending_confirm": None,
                "message_count": 0, "first_user_message": "", "cap": cap or self.default_cap}
        fpath = self.sess_file_path(sid)
        fpath.write_text(json.dumps(meta, ensure_ascii=False) + "\n", encoding="utf-8")
        idx = self.load_index()
        idx["current_session_id"] = sid
        idx["sessions"].insert(0, self._meta_summary(meta))
        self._save_index(idx)
        return sid

    def activate(self, sid: str, clear_pending_confirm: bool = True) -> dict:
        idx = self.load_index()
        if not any(s["id"] == sid for s in idx["sessions"]):
            raise SessionNotFound(sid)
        idx["current_session_id"] = sid
        self._save_index(idx)
        sess = self.get_session(sid)
        if clear_pending_confirm:
            sess["pending_confirm"] = None
            self.save_pending_confirm(sid, None)
        return sess

    def add_message(self, sid: str, msg: dict):
        fpath = self.sess_file_path(sid)
        # P1-21：轻量读取 meta（仅首行），避免每消息全量解析 O(n²)
        meta = self._read_meta_only(fpath)
        if meta is None:
            raise SessionNotFound(sid)
        if meta.get("message_count", 0) >= meta.get("cap", self.default_cap):
            raise SessionFullError(sid)
        with open(fpath, "a", encoding="utf-8") as f:
            f.write(json.dumps({"type": "message", **msg}, ensure_ascii=False) + "\n")
        new_meta = {**meta, "type": "meta",
                    "message_count": meta.get("message_count", 0) + 1,
                    "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        if not meta.get("first_user_message") and msg.get("role") == "user":
            new_meta["first_user_message"] = (msg.get("content") or "")[:100]
        self._rewrite_meta_line(fpath, new_meta)
        self._update_index_summary(sid, name=meta.get("name", ""),
                                    count=new_meta["message_count"],
                                    updated=new_meta["updated_at"])

    def _update_index_summary(self, sid: str, name=None, count=None, archived=None, updated=None):
        idx = self.load_index()
        for s in idx["sessions"]:
            if s["id"] == sid:
                if name is not None: s["name"] = name
                if count is not None: s["message_count"] = count
                if archived is not None: s["archived"] = archived
                if updated is not None: s["updated_at"] = updated
        self._save_index(idx)

    def rename(self, sid: str, new_name: str):
        sess = self.get_session(sid)
        self._rewrite_meta_line(self.sess_file_path(sid), {**sess, "type": "meta", "name": new_name})
        self._update_index_summary(sid, name=new_name)

    def archive(self, sid: str, archived: bool = True):
        sess = self.get_session(sid)
        self._rewrite_meta_line(self.sess_file_path(sid), {**sess, "type": "meta", "archived": archived})
        self._update_index_summary(sid, archived=archived)

    def delete_session(self, sid: str):
        fpath = self.sess_file_path(sid)
        if fpath.exists(): fpath.unlink()
        idx = self.load_index()
        idx["sessions"] = [s for s in idx["sessions"] if s["id"] != sid]
        if idx["current_session_id"] == sid:
            idx["current_session_id"] = idx["sessions"][0]["id"] if idx["sessions"] else None
        self._save_index(idx)

    def update_compact_summary(self, sid: str, summary: str) -> dict:
        sess = self.get_session(sid)
        result = {**sess, "type": "meta", "compact_summary": summary}
        self._rewrite_meta_line(self.sess_file_path(sid), result)
        return result

    def save_pending_confirm(self, sid: str, confirm: dict | None):
        fpath = self.sess_file_path(sid)
        # P1-21：轻量读取 meta（仅首行），SSE 确认路径高频调用，避免全量解析
        meta = self._read_meta_only(fpath)
        if meta is None:
            raise SessionNotFound(sid)
        self._rewrite_meta_line(fpath, {**meta, "type": "meta", "pending_confirm": confirm})

    def load_pending_confirm(self, sid: str) -> dict | None:
        return self.get_session(sid).get("pending_confirm")

    def save_usage(self, sid: str, input_tokens: int, output_tokens: int, model: str = ""):
        fpath = self.sess_file_path(sid)
        obj = {"type": "usage", "input_tokens": input_tokens, "output_tokens": output_tokens,
               "model": model, "timestamp": time.time()}
        with open(fpath, "a", encoding="utf-8") as f:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")

    def get_stats(self, sid: str) -> dict:
        sess = self.get_session(sid)
        fpath = self.sess_file_path(sid)
        total_in, total_out, model_usage = 0, 0, {}
        skipped = 0
        if fpath.exists():
            for raw in fpath.read_text(encoding="utf-8").rstrip("\n").split("\n"):
                try:
                    obj = json.loads(raw)
                except json.JSONDecodeError:
                    # 不静默：损坏行跳过但计数，避免统计"悄悄少算"
                    skipped += 1
                    continue
                if obj.get("type") == "usage":
                    total_in += obj.get("input_tokens", 0)
                    total_out += obj.get("output_tokens", 0)
                    m = obj.get("model") or "default"
                    mu = model_usage.setdefault(m, {"input": 0, "output": 0})
                    mu["input"] += obj.get("input_tokens", 0)
                    mu["output"] += obj.get("output_tokens", 0)
        if skipped:
            print(f"[session_manager] 会话统计跳过 {skipped} 行损坏 JSON（{fpath}）",
                  file=sys.stderr)
        return {"total_input_tokens": total_in, "total_output_tokens": total_out,
                "total_messages": sess["message_count"], "last_active": sess["updated_at"],
                "model_usage": model_usage}

    def recover_index(self) -> dict:
        sessions = []
        for f in sorted(self.sessions_dir.glob("sess_*.jsonl"), reverse=True):
            meta, _ = self._parse_jsonl(f)
            if meta:
                sessions.append(self._meta_summary(meta))
        cur = next((s["id"] for s in sessions if not s.get("archived")), sessions[0]["id"] if sessions else None)
        idx = {"current_session_id": cur, "sessions": sessions}
        self._save_index(idx)
        return idx

    def set_current_in_index(self, sid: str | None):
        """公开方法：更新 index 中的 current_session_id"""
        idx = self.load_index()
        idx["current_session_id"] = sid
        self._save_index(idx)

    def clear_session(self, sid: str) -> dict:
        """清空会话消息（保留 meta，重置 message_count=0）"""
        sess = self.get_session(sid)
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        new_meta = {
            "type": "meta", "id": sess["id"], "name": sess.get("name", "新会话"),
            "created_at": sess.get("created_at", ""), "updated_at": now,
            "archived": False, "compact_summary": sess.get("compact_summary", ""),
            "pending_confirm": None, "message_count": 0, "first_user_message": "",
            "cap": sess.get("cap", self.default_cap),
        }
        fpath = self.sess_file_path(sid)
        fpath.write_text(json.dumps(new_meta, ensure_ascii=False) + "\n", encoding="utf-8")
        self._update_index_summary(sid, count=0, updated=now)
        return new_meta
