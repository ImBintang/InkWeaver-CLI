"""Tests for SessionManager — JSONL-backed storage for chat sessions."""

import json
import time
from pathlib import Path

import pytest

from tools.session_manager import SessionManager, SessionFullError, SessionNotFound


@pytest.fixture
def tmp_sessions(tmp_path):
    """Create a SessionManager with a tmp workspace + sessions dir."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    sess_dir = ws / "chat_sessions"
    mgr = SessionManager(ws, sess_dir, default_cap=5)
    return mgr, sess_dir, ws


def test_generate_id_format():
    sid = SessionManager.generate_id()
    assert sid.startswith("sess_")
    parts = sid.split("_")
    assert len(parts) == 4  # sess, YYYYMMDD, HHMMSS, rand5
    assert len(parts[1]) == 8   # date
    assert len(parts[2]) == 6   # time
    assert len(parts[3]) == 5   # rand hex (5 chars from 6 hex)


def test_generate_id_uniqueness():
    ids = {SessionManager.generate_id() for _ in range(100)}
    assert len(ids) == 100


def test_create_session_basic(tmp_sessions):
    mgr, sess_dir, _ = tmp_sessions
    sid = mgr.create_session("测试会话")
    assert sid.startswith("sess_")
    fpath = sess_dir / f"{sid}.jsonl"
    assert fpath.exists()
    lines = fpath.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 1
    meta = json.loads(lines[0])
    assert meta["type"] == "meta"
    assert meta["name"] == "测试会话"
    assert meta["archived"] is False
    assert meta["message_count"] == 0
    assert meta["first_user_message"] == ""
    assert meta["cap"] == 5


def test_create_session_with_custom_cap(tmp_sessions):
    mgr, _, _ = tmp_sessions
    sid = mgr.create_session("小会话", cap=10)
    sess = mgr.get_session(sid)
    assert sess["cap"] == 10


def test_create_session_default_cap(tmp_sessions):
    mgr, _, _ = tmp_sessions
    sid = mgr.create_session("默认会话")
    sess = mgr.get_session(sid)
    assert sess["cap"] == 5  # from default_cap=5


def test_create_session_sets_current(tmp_sessions):
    mgr, _, _ = tmp_sessions
    sid1 = mgr.create_session("第一")
    idx = mgr.load_index()
    assert idx["current_session_id"] == sid1
    sid2 = mgr.create_session("第二")
    idx = mgr.load_index()
    assert idx["current_session_id"] == sid2


def test_load_sessions(tmp_sessions):
    mgr, _, _ = tmp_sessions
    mgr.create_session("A")
    mgr.create_session("B")
    sessions = mgr.load_sessions()
    assert len(sessions) == 2
    # newest first
    assert sessions[0]["name"] == "B"
    assert sessions[1]["name"] == "A"


def test_load_sessions_exclude_archived(tmp_sessions):
    mgr, _, _ = tmp_sessions
    sid1 = mgr.create_session("Active")
    sid2 = mgr.create_session("Old")
    mgr.archive(sid2, True)
    active = mgr.load_sessions(include_archived=False)
    assert len(active) == 1
    assert active[0]["id"] == sid1
    all_sessions = mgr.load_sessions(include_archived=True)
    assert len(all_sessions) == 2


def test_get_session_not_found(tmp_sessions):
    mgr, _, _ = tmp_sessions
    with pytest.raises(SessionNotFound) as exc:
        mgr.get_session("sess_nope_xxxxx")
    assert exc.value.status_code == 404
    assert "sess_nope_xxxxx" in str(exc.value.detail)


def test_get_session_returns_messages(tmp_sessions):
    mgr, _, _ = tmp_sessions
    sid = mgr.create_session("测试")
    mgr.add_message(sid, {"role": "user", "content": "hello"})
    sess = mgr.get_session(sid)
    assert sess["name"] == "测试"
    assert len(sess["messages"]) == 1
    assert sess["messages"][0]["role"] == "user"
    assert sess["messages"][0]["content"] == "hello"


def test_add_message_updates_count(tmp_sessions):
    mgr, _, _ = tmp_sessions
    sid = mgr.create_session("计数测试")
    mgr.add_message(sid, {"role": "user", "content": "hi"})
    mgr.add_message(sid, {"role": "assistant", "content": "hello"})
    sess = mgr.get_session(sid)
    assert sess["message_count"] == 2
    assert sess["messages"][0]["role"] == "user"
    assert sess["messages"][1]["role"] == "assistant"


def test_add_message_sets_first_user_message(tmp_sessions):
    mgr, _, _ = tmp_sessions
    sid = mgr.create_session("首消息测试")
    mgr.add_message(sid, {"role": "assistant", "content": "system prompt"})
    sess = mgr.get_session(sid)
    assert sess["first_user_message"] == ""
    mgr.add_message(sid, {"role": "user", "content": "第一次用户问"})
    sess = mgr.get_session(sid)
    assert sess["first_user_message"] == "第一次用户问"


def test_add_message_truncates_first_user_message(tmp_sessions):
    mgr, _, _ = tmp_sessions
    sid = mgr.create_session("截断测试")
    long_text = "x" * 200
    mgr.add_message(sid, {"role": "user", "content": long_text})
    sess = mgr.get_session(sid)
    assert len(sess["first_user_message"]) == 100


def test_add_message_session_full(tmp_sessions):
    mgr, _, _ = tmp_sessions
    sid = mgr.create_session("满会话", cap=2)
    mgr.add_message(sid, {"role": "user", "content": "1"})
    mgr.add_message(sid, {"role": "assistant", "content": "ok1"})
    with pytest.raises(SessionFullError) as exc:
        mgr.add_message(sid, {"role": "user", "content": "overflow"})
    assert exc.value.status_code == 403
    detail = exc.value.detail
    assert detail["code"] == "session_full"
    assert detail["session_id"] == sid


def test_activate_not_found(tmp_sessions):
    mgr, _, _ = tmp_sessions
    with pytest.raises(SessionNotFound):
        mgr.activate("sess_20260101_000000_xxxxx")


def test_activate_updates_current(tmp_sessions):
    mgr, _, _ = tmp_sessions
    sid1 = mgr.create_session("A")
    sid2 = mgr.create_session("B")
    mgr.activate(sid1)
    idx = mgr.load_index()
    assert idx["current_session_id"] == sid1


def test_activate_clears_pending_confirm(tmp_sessions):
    mgr, _, _ = tmp_sessions
    sid = mgr.create_session("带确认")
    mgr.save_pending_confirm(sid, {"confirm_id": "abc", "confirm_type": "planned_extract"})
    sess = mgr.activate(sid, clear_pending_confirm=True)
    assert sess["pending_confirm"] is None
    mgr.save_pending_confirm(sid, {"confirm_id": "def"})
    sess_no_clear = mgr.activate(sid, clear_pending_confirm=False)
    assert sess_no_clear["pending_confirm"]["confirm_id"] == "def"


def test_rename(tmp_sessions):
    mgr, _, _ = tmp_sessions
    sid = mgr.create_session("原名")
    mgr.rename(sid, "新名")
    sess = mgr.get_session(sid)
    assert sess["name"] == "新名"
    idx = mgr.load_index()
    entry = next(s for s in idx["sessions"] if s["id"] == sid)
    assert entry["name"] == "新名"


def test_archive_and_unarchive(tmp_sessions):
    mgr, _, _ = tmp_sessions
    sid = mgr.create_session("归档测试")
    mgr.archive(sid, archived=True)
    sess = mgr.get_session(sid)
    assert sess["archived"] is True
    mgr.archive(sid, archived=False)
    sess = mgr.get_session(sid)
    assert sess["archived"] is False


def test_delete_session(tmp_sessions):
    mgr, sess_dir, _ = tmp_sessions
    sid = mgr.create_session("删除测试")
    mgr.delete_session(sid)
    assert not (sess_dir / f"{sid}.jsonl").exists()
    idx = mgr.load_index()
    assert len(idx["sessions"]) == 0
    assert idx["current_session_id"] is None


def test_delete_session_updates_current(tmp_sessions):
    mgr, _, _ = tmp_sessions
    sid1 = mgr.create_session("A")
    sid2 = mgr.create_session("B")
    mgr.delete_session(sid2)
    idx = mgr.load_index()
    assert idx["current_session_id"] == sid1


def test_delete_session_file_only(tmp_sessions):
    mgr, sess_dir, _ = tmp_sessions
    sid = mgr.create_session("只有文件")
    # Manually remove from index so file exists but index doesn't
    fpath = sess_dir / f"{sid}.jsonl"
    mgr.delete_session(sid)
    assert not fpath.exists()


def test_update_compact_summary(tmp_sessions):
    mgr, _, _ = tmp_sessions
    sid = mgr.create_session("摘要测试")
    result = mgr.update_compact_summary(sid, "精简摘要内容")
    assert result["compact_summary"] == "精简摘要内容"
    sess = mgr.get_session(sid)
    assert sess["compact_summary"] == "精简摘要内容"


def test_save_and_load_pending_confirm(tmp_sessions):
    mgr, _, _ = tmp_sessions
    sid = mgr.create_session("确认测试")
    confirm = {"confirm_id": "abc123", "confirm_type": "forced_debt",
               "payload": {"new_wikis": ["W1"]}}
    mgr.save_pending_confirm(sid, confirm)
    loaded = mgr.load_pending_confirm(sid)
    assert loaded == confirm


def test_save_pending_confirm_none(tmp_sessions):
    mgr, _, _ = tmp_sessions
    sid = mgr.create_session("空确认")
    mgr.save_pending_confirm(sid, None)
    assert mgr.load_pending_confirm(sid) is None


def test_save_usage(tmp_sessions):
    mgr, _, _ = tmp_sessions
    sid = mgr.create_session("用量测试")
    mgr.save_usage(sid, 100, 50, "gpt-4")
    mgr.save_usage(sid, 200, 80, "gpt-4")
    mgr.save_usage(sid, 50, 20, "claude-3")
    fpath = mgr.sess_file_path(sid)
    lines = fpath.read_text(encoding="utf-8").strip().split("\n")
    usage_lines = [l for l in lines if json.loads(l)["type"] == "usage"]
    assert len(usage_lines) == 3


def test_get_stats(tmp_sessions):
    mgr, _, _ = tmp_sessions
    sid = mgr.create_session("统计测试")
    mgr.add_message(sid, {"role": "user", "content": "hi"})
    mgr.add_message(sid, {"role": "assistant", "content": "hello"})
    mgr.save_usage(sid, 100, 50, "gpt-4")
    mgr.save_usage(sid, 200, 80, "gpt-4")
    mgr.save_usage(sid, 50, 25, "claude-3")
    stats = mgr.get_stats(sid)
    assert stats["total_input_tokens"] == 350
    assert stats["total_output_tokens"] == 155
    assert stats["total_messages"] == 2
    assert "gpt-4" in stats["model_usage"]
    assert stats["model_usage"]["gpt-4"]["input"] == 300
    assert stats["model_usage"]["gpt-4"]["output"] == 130
    assert stats["model_usage"]["claude-3"]["input"] == 50
    assert stats["model_usage"]["claude-3"]["output"] == 25


def test_recover_index(tmp_sessions):
    mgr, _, _ = tmp_sessions
    sid_old = mgr.create_session("旧会话")
    time.sleep(1.1)  # force different timestamp so sort by filename reflects creation order
    sid_new = mgr.create_session("新会话")
    # Corrupt the index by nuking it
    mgr.index_path.unlink()
    recovered = mgr.recover_index()
    assert len(recovered["sessions"]) == 2
    ids = {s["id"] for s in recovered["sessions"]}
    assert sid_old in ids
    assert sid_new in ids
    # Sessions should be in reverse creation order (newest first)
    assert recovered["sessions"][0]["id"] == sid_new
    assert recovered["sessions"][1]["id"] == sid_old
    # Current should be the newest non-archived
    assert recovered["current_session_id"] == sid_new


def test_recover_index_with_corrupt_files(tmp_sessions):
    mgr, sess_dir, _ = tmp_sessions
    sid = mgr.create_session("正常")
    # Create a corrupt file
    bad_path = sess_dir / "sess_20260101_000000_corrupt.jsonl"
    bad_path.write_text("not json at all\n", encoding="utf-8")
    recovered = mgr.recover_index()
    ids = {s["id"] for s in recovered["sessions"]}
    assert sid in ids
    assert "sess_20260101_000000_corrupt" not in ids


def test_parse_jsonl_corrupt_last_line(tmp_sessions):
    mgr, sess_dir, _ = tmp_sessions
    sid = mgr.create_session("损坏测试")
    mgr.add_message(sid, {"role": "user", "content": "ok"})
    mgr.add_message(sid, {"role": "assistant", "content": "fine"})
    # Append corrupt data
    fpath = sess_dir / f"{sid}.jsonl"
    with open(fpath, "a", encoding="utf-8") as f:
        f.write("{CORRUPT 无效内容\n")
    meta, messages = mgr._parse_jsonl(fpath)
    assert meta is not None
    assert len(messages) == 2


def test_parse_jsonl_empty_file(tmp_sessions):
    mgr, _, _ = tmp_sessions
    sid = mgr.create_session("空文件")
    fpath = mgr.sess_file_path(sid)
    # Truncate the file to empty
    fpath.write_text("", encoding="utf-8")
    meta, messages = mgr._parse_jsonl(fpath)
    assert meta is None
    assert messages == []


def test_parse_jsonl_no_file(tmp_sessions):
    mgr, _, _ = tmp_sessions
    fpath = mgr.sess_file_path("sess_never_created_xxxxx")
    meta, messages = mgr._parse_jsonl(fpath)
    assert meta is None
    assert messages == []


def test_index_is_atomic(tmp_sessions):
    mgr, _, _ = tmp_sessions
    mgr.create_session("原子写")
    # _save_index should not leave .tmp files
    tmp_files = list(mgr.sessions_dir.glob("*.tmp"))
    assert len(tmp_files) == 0
    # And index.json should be valid
    idx = mgr.load_index()
    assert len(idx["sessions"]) == 1


def test_index_corrupt_recovery(tmp_sessions):
    mgr, _, _ = tmp_sessions
    mgr.create_session("原")
    # Corrupt the index
    mgr.index_path.write_text("{CORRUPT", encoding="utf-8")
    # load_index should return empty
    idx = mgr.load_index()
    assert idx["sessions"] == []
    assert idx["current_session_id"] is None


def test_create_session_no_cap_fallback_to_default(tmp_sessions):
    mgr, _, _ = tmp_sessions
    # cap=0 should fall back to default (since `0 or default_cap` → default_cap)
    sid = mgr.create_session("零cap", cap=0)
    sess = mgr.get_session(sid)
    assert sess["cap"] == 5  # default_cap


def test_message_order_preserved(tmp_sessions):
    mgr, _, _ = tmp_sessions
    sid = mgr.create_session("顺序测试")
    for i in range(5):
        mgr.add_message(sid, {"role": "user", "content": f"消息{i}"})
    sess = mgr.get_session(sid)
    assert len(sess["messages"]) == 5
    for i in range(5):
        assert sess["messages"][i]["content"] == f"消息{i}"


def test_update_index_summary(tmp_sessions):
    mgr, _, _ = tmp_sessions
    sid = mgr.create_session("摘要更新")
    mgr._update_index_summary(sid, count=99, archived=True)
    idx = mgr.load_index()
    entry = next(s for s in idx["sessions"] if s["id"] == sid)
    assert entry["message_count"] == 99
    assert entry["archived"] is True


def test_sessions_dir_auto_created(tmp_path):
    ws = tmp_path / "ws2"
    ws.mkdir()
    sess_dir = ws / "chat_sessions" / "sub"
    mgr = SessionManager(ws, sess_dir, default_cap=50)
    assert sess_dir.exists()
