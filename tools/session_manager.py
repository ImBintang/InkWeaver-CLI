"""SessionManager — JSONL-backed storage for chat sessions in one book."""

import json
import time
import secrets
from pathlib import Path
from typing import Any

from fastapi import HTTPException


class SessionFullError(HTTPException):
    def __init__(self, session_id: str):
        super().__init__(status_code=403, detail={"code": "session_full", "session_id": session_id})


class SessionNotFound(HTTPException):
    def __init__(self, session_id: str):
        super().__init__(status_code=404, detail=f"会话「{session_id}」不存在")


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
        except (json.JSONDecodeError, OSError):
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
        for line in lines[:end]:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("type") == "meta":
                meta = obj
            elif obj.get("type") == "message":
                messages.append(obj)
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
        sess = self.get_session(sid)
        if sess["message_count"] >= sess["cap"]:
            raise SessionFullError(sid)
        fpath = self.sess_file_path(sid)
        with open(fpath, "a", encoding="utf-8") as f:
            f.write(json.dumps({"type": "message", **msg}, ensure_ascii=False) + "\n")
        new_meta = {**sess, "type": "meta", "message_count": sess["message_count"] + 1,
                     "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        if not sess["first_user_message"] and msg.get("role") == "user":
            new_meta["first_user_message"] = msg.get("content", "")[:100]
        self._rewrite_meta_line(fpath, new_meta)
        self._update_index_summary(sid, name=sess["name"], count=new_meta["message_count"],
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
        sess = self.get_session(sid)
        self._rewrite_meta_line(self.sess_file_path(sid), {**sess, "type": "meta", "pending_confirm": confirm})

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
        if fpath.exists():
            for raw in fpath.read_text(encoding="utf-8").rstrip("\n").split("\n"):
                try:
                    obj = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if obj.get("type") == "usage":
                    total_in += obj.get("input_tokens", 0)
                    total_out += obj.get("output_tokens", 0)
                    m = obj.get("model") or "default"
                    mu = model_usage.setdefault(m, {"input": 0, "output": 0})
                    mu["input"] += obj.get("input_tokens", 0)
                    mu["output"] += obj.get("output_tokens", 0)
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
