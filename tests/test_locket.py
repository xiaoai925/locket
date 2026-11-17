import json
import subprocess
import sys
from pathlib import Path

import pytest

from locket import hooks
from locket.gitrepo import Identity
from locket.cli import main
from locket.memory import parse
from locket.search import tokenize
from locket.store import MAIN, Locket, LocketError, Session, short_id


@pytest.fixture
def store(tmp_path):
    s = Locket(tmp_path / "mem")
    s.init("Tester <tester@example.com>")
    return s


def git(store, *args):
    return store.git.run(*args)


def test_init_creates_spec_files_and_checks_out_main(store):
    assert (store.root / ".pmf" / "config.toml").read_text("utf-8").startswith('spec = "0.2"')
    assert (store.root / ".pmf" / "tombstones.jsonl").exists()
    assert store.init() is False
    assert "Pmf-Op: init" in git(store, "log", "-1", "--format=%B")


def test_remember_writes_valid_pmf_file_on_session_branch(store):
    s = Session("claude-code", "abc")
    m, _ = store.remember("用户偏好中文交流", s, type="preference", title="交流语言",
                          quote="我习惯用中文")
    assert store.git.rev_parse(s.ref)
    assert not (store.root / m.path).exists()  # main untouched until merge
    data = store.git.show(s.ref, m.path)
    parsed = parse(data)
    assert parsed.meta["pmf"] == "0.2"
    assert len(parsed.meta["id"]) == 26
    assert parsed.meta["sources"][0]["session"] == "claude-code/abc"
    assert parsed.meta["sources"][0]["quote"] == "我习惯用中文"
    assert isinstance(parsed.meta["created"], str)
    trailers = git(store, "log", "-1", "--format=%B", s.ref)
    assert "Pmf-Op: learn" in trailers and "Pmf-Session: claude-code/abc" in trailers


def test_session_sees_its_own_writes_and_merge_publishes_them(store):
    s = Session("claude-code", "abc")
    m, _ = store.remember("SI_Ag 项目使用 Python 3.12", s, about="project:SI_Ag")
    assert [h.memory.id for h in store.recall("Python", session=s)] == [m.id]
    assert store.recall("Python") == []
    result = store.end_session(s)
    assert result.merged and not result.conflicts
    assert (store.root / m.path).exists()
    assert m.path.startswith("projects/si-ag/")
    assert store.git.rev_parse(s.ref) is None
    assert store.recall("Python")[0].memory.id == m.id
    parents = git(store, "log", "-1", "--format=%P", MAIN).split()
    assert len(parents) == 2


def test_chinese_recall_uses_bigrams(store):
    s = Session("cli", "t")
    store.remember("用户的主力开发环境是 Windows 11", s)
    store.remember("用户喜欢喝咖啡", s)
    store.end_session(s)
    hits = store.recall("开发环境")
    assert len(hits) == 1 and "Windows" in hits[0].memory.body
    assert tokenize("开发环境") == ["开发", "发环", "环境"]


def test_correction_keeps_id_and_history(store):
    s = Session("cli", "1")
    m, _ = store.remember("生日是 3 月 5 日", s, title="生日")
    store.end_session(s)
    s2 = Session("cli", "2")
    store.update(short_id(m.id), s2, text="生日是 5 月 3 日", reason="记反了")
    store.end_session(s2)
    now = store.get(m.id)
    assert now.body == "生日是 5 月 3 日" and now.id == m.id
    ops = [e.op for e in store.history(now)]
    assert "correct" in ops and "learn" in ops


def test_update_cannot_change_identity(store):
    s = Session("cli", "1")
    m, _ = store.remember("x 事实内容", s)
    with pytest.raises(LocketError):
        store.update(m.id, s, fields={"id": "NEW"})


def test_supersede_keeps_both_with_validity(store):
    s = Session("cli", "1")
    old, _ = store.remember("用户住在北京", s, title="居住城市")
    store.end_session(s)
    s2 = Session("cli", "2")
    old2, new = store.supersede(old.id, "用户住在上海", s2, effective="2026-10-01")
    store.end_session(s2)
    assert store.get(old.id).meta["valid_to"] == "2026-10-01"
    assert store.get(new.id).meta["supersedes"] == [old.id]
    active = [h.memory.id for h in store.recall("用户住在")]
    assert active == [new.id]
    assert {h.memory.id for h in store.recall("用户住在", include_inactive=True)} == {old.id, new.id}


def test_forget_removes_from_tree_but_not_history(store):
    s = Session("cli", "1")
    m, _ = store.remember("临时信息内容", s)
    store.end_session(s)
    s2 = Session("cli", "2")
    store.forget(m.id, s2)
    store.end_session(s2)
    assert not (store.root / m.path).exists()
    with pytest.raises(LocketError):
        store.get(m.id)
    assert store.git.show(f"{MAIN}~1", m.path) is not None


def test_concurrent_sessions_merge_cleanly(store):
    a, b = Session("claude-code", "a"), Session("codex", "b")
    ma, _ = store.remember("会话 A 学到的事", a)
    mb, _ = store.remember("会话 B 学到的事", b)
    store.end_session(a)
    store.end_session(b)
    ids = {m.id for m in store.memories()}
    assert {ma.id, mb.id} <= ids


def test_both_sides_edit_same_memory_records_conflict(store):
    s = Session("cli", "0")
    m, _ = store.remember("原始内容", s, title="同一条")
    store.end_session(s)
    a, b = Session("x", "a"), Session("y", "b")
    store.update(m.id, a, text="A 的版本")
    store.update(m.id, b, text="B 的版本")
    store.end_session(a)
    result = store.end_session(b)
    assert len(result.conflicts) == 1
    assert store.get(m.id).body == "A 的版本"  # main kept, nothing silently lost
    conflict = store.conflicts()[0]
    assert conflict.meta["members"] == [m.id] and "B 的版本" in conflict.body
    store.resolve(short_id(conflict.id), "保留 A")
    assert store.conflicts() == []
    assert store.conflicts(status="resolved")[0].meta["resolution"] == "保留 A"


def test_redact_writes_tombstone_and_blocks_resurrection(store):
    s = Session("cli", "0")
    m, _ = store.remember("不想被记住的事情", s)
    store.end_session(s)
    stale = Session("cli", "stale")
    store.update(m.id, stale, text="不想被记住的事情（改）")  # an old session still holds it
    store.redact(m.id)
    line = json.loads((store.root / ".pmf/tombstones.jsonl").read_text("utf-8").strip())
    assert line["id"] == m.id and line["blobs"] and "不想" not in json.dumps(line, ensure_ascii=False)
    store.end_session(stale)
    assert all(x.id != m.id for x in store.memories())


def test_owner_hand_edit_is_saved_before_merge(store):
    s = Session("cli", "0")
    m, _ = store.remember("手改前", s)
    store.end_session(s)
    path = store.root / m.path
    path.write_text(path.read_text("utf-8").replace("手改前", "手改后"), "utf-8")
    s2 = Session("cli", "1")
    store.remember("另一条记忆", s2)
    store.end_session(s2)
    assert store.get(m.id).body == "手改后"
    assert "correct" in [e.op for e in store.history(store.get(m.id))]


def test_unknown_fields_survive_update(store):
    s = Session("cli", "0")
    m, _ = store.remember("带扩展字段", s)
    store.end_session(s)
    path = store.root / m.path
    text = path.read_text("utf-8").replace("type: fact", "type: fact\nx-acme-weight: 3")
    path.write_text(text, "utf-8")
    store.save()
    s2 = Session("cli", "1")
    store.update(m.id, s2, text="改了正文")
    store.end_session(s2)
    assert store.get(m.id).meta["x-acme-weight"] == 3


def test_hooks_inject_and_merge(store, monkeypatch):
    s = Session(hooks.AGENT, "sess1")
    store.remember("用户偏好中文交流", s, type="preference", title="交流语言")
    store.end_session(s)
    ctx = hooks.session_start({"session_id": "sess2", "cwd": str(store.root)}, store)
    assert "交流语言" in ctx and "--session sess2" in ctx
    hit = hooks.prompt_submit({"session_id": "sess2", "prompt": "用中文交流可以吗"}, store)
    assert "交流语言" in hit
    assert hooks.prompt_submit({"session_id": "sess2", "prompt": "天气"}, store) == ""
    store.remember("新学到的内容", Session(hooks.AGENT, "sess2"))
    assert "合并了" in hooks.session_end({"session_id": "sess2"}, store)


def test_install_hooks_is_idempotent(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"model": "x", "hooks": {"Stop": [{"hooks": []}]}}), "utf-8")
    assert len(hooks.install(settings, "locket")) == 3
    assert hooks.install(settings, "locket") == []
    data = json.loads(settings.read_text("utf-8"))
    assert data["model"] == "x" and "Stop" in data["hooks"]


def test_cli_roundtrip(tmp_path, capsys):
    repo = str(tmp_path / "cli-mem")
    assert main(["--repo", repo, "init"]) == 0
    assert main(["--repo", repo, "remember", "用户用 py -3 运行 Python", "--type", "procedure",
                 "--title", "运行 Python"]) == 0
    out = capsys.readouterr().out
    sid = out.split("[")[1].split("]")[0]
    assert main(["--repo", repo, "recall", "运行 Python"]) == 0
    assert sid in capsys.readouterr().out
    assert main(["--repo", repo, "why", sid]) == 0
    assert "learn" in capsys.readouterr().out
    assert main(["--repo", repo, "show", "zz"]) == 1


def _bare_remote(tmp_path) -> str:
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(bare)], check=True)
    return str(bare)


def _clone(url: str, dest: Path) -> Locket:
    subprocess.run(["git", "clone", "-q", url, str(dest)], check=True)
    return Locket(dest)


def test_sync_shares_memory_between_clones(tmp_path, monkeypatch):
    bare = _bare_remote(tmp_path)
    a = Locket(tmp_path / "A")
    a.init("A <a@example.com>")
    a.remote(bare)
    sa = Session("cli", "a1")
    a.remember("用户喜欢喝茶", sa, title="饮品")
    a.end_session(sa)
    assert a.push().status == "pushed"

    b = _clone(bare, tmp_path / "B")
    assert any("喝茶" in m.body for m in b.memories())

    sb = Session("cli", "b1")
    b.remember("项目跑在 Windows 上", sb, about="project:SI_Ag")
    b.end_session(sb)
    extra = {f'notes/sync-{i}.txt': f'note {i}'.encode() for i in range(40)}
    b.git.commit_changes(MAIN, extra, 'Sync fixture', Identity('Fixture', 'fixture@example.com'))
    b.push()
    calls = []
    tree_files = a.git.tree_files
    def counted(commit):
        calls.append(commit)
        return tree_files(commit)
    monkeypatch.setattr(a.git, 'tree_files', counted)
    result = a.pull()
    assert result.status == "fast-forward"
    assert set(extra).issubset(result.changed)
    assert len(calls) <= 8  # Tree reads must not grow with the number of paths.
    assert any("Windows" in m.body for m in a.memories())


def test_sync_diverged_keeps_both_sides(tmp_path):
    bare = _bare_remote(tmp_path)
    a = Locket(tmp_path / "A")
    a.init("A <a@example.com>")
    a.remote(bare)
    s = Session("cli", "0")
    a.remember("共同的基础记忆", s)
    a.end_session(s)
    a.push()
    b = _clone(bare, tmp_path / "B")

    sa = Session("cli", "a")
    a.remember("只有 A 学到的事", sa, title="A事")
    a.end_session(sa)
    sb = Session("cli", "b")
    b.remember("只有 B 学到的事", sb, title="B事")
    b.end_session(sb)
    b.push()

    result = a.pull()
    assert result.status == "merge"
    bodies = " ".join(m.body for m in a.memories())
    assert "只有 A 学到的事" in bodies and "只有 B 学到的事" in bodies
    a.push()
    b.pull()
    assert "只有 A 学到的事" in " ".join(m.body for m in b.memories())


def test_push_rejects_when_behind(tmp_path):
    bare = _bare_remote(tmp_path)
    a = Locket(tmp_path / "A")
    a.init("A <a@example.com>")
    a.remote(bare)
    a.remember("基础", Session("cli", "0"))
    a.end_session(Session("cli", "0"))
    a.push()
    b = _clone(bare, tmp_path / "B")
    b.remember("B 的新记忆", Session("cli", "b"))
    b.end_session(Session("cli", "b"))
    b.push()
    a.remember("A 的新记忆", Session("cli", "a"))
    a.end_session(Session("cli", "a"))
    with pytest.raises(LocketError):
        a.push()  # behind the remote; must pull first


def test_redaction_propagates_through_sync(tmp_path):
    bare = _bare_remote(tmp_path)
    a = Locket(tmp_path / "A")
    a.init("A <a@example.com>")
    a.remote(bare)
    s = Session("cli", "0")
    m, _ = a.remember("不该外传的秘密内容", s)
    a.end_session(s)
    a.push()
    b = _clone(bare, tmp_path / "B")
    assert any(x.id == m.id for x in b.memories())

    a.redact(m.id)
    a.push()
    assert b.pull().status in ("fast-forward", "merge")
    assert all(x.id != m.id for x in b.memories())
    assert not (b.root / m.path).exists()
    assert m.id in (b.root / ".pmf/tombstones.jsonl").read_text("utf-8")


def test_gc_erases_redacted_blob_from_history(tmp_path):
    store = Locket(tmp_path / "mem")
    store.init("A <a@example.com>")
    s = Session("cli", "0")
    keep, _ = store.remember("要保留的记忆内容", s, title="留")
    secret, _ = store.remember("要被彻底抹掉的内容", s, title="删")
    store.end_session(s)
    store.redact(secret.id)
    line = next(l for l in (store.root / ".pmf/tombstones.jsonl").read_text("utf-8").splitlines() if l.strip())
    dead = json.loads(line)["blobs"][0]
    assert store.git.proc("cat-file", "-e", dead).returncode == 0

    report = store.gc()
    assert report["blobs"] == 1 and report["rewritten"] >= 1
    assert store.git.proc("cat-file", "-e", dead).returncode != 0  # bytes are gone
    assert any(x.id == keep.id for x in store.memories())  # the rest survives
    assert len(store.git.log(MAIN, store.get(keep.id).path, follow=True)) >= 1
    assert store.gc()["rewritten"] == 0  # tombstone stays, but nothing left to rewrite


def test_hook_entrypoint_never_fails(tmp_path):
    env = {"LOCKET_HOME": str(tmp_path / "hookmem"), "PYTHONPATH": str(Path(__file__).parents[1] / "src")}
    import os
    full = {**os.environ, **env}
    p = subprocess.run([sys.executable, "-m", "locket", "hook", "session-start"],
                       input=b"not json", capture_output=True, env=full)
    assert p.returncode == 0
    p = subprocess.run([sys.executable, "-m", "locket", "hook", "session-start"],
                       input=json.dumps({"session_id": "s1"}).encode(), capture_output=True, env=full)
    assert p.returncode == 0 and "Locket" in p.stdout.decode("utf-8")
