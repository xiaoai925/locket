"""Claude Code hooks: inject memories at session start and per prompt, merge at session end.

The hooks guarantee the read and the final commit happen; the model only has to
decide what is worth remembering.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from .memory import shorten
from .store import Hit, Locket, Session, short_id

AGENT = "claude-code"
MAX_CONTEXT_CHARS = 6000


def project_name(cwd: str | None) -> str | None:
    if not cwd:
        return None
    p = subprocess.run(["git", "-C", cwd, "rev-parse", "--show-toplevel"], capture_output=True)
    top = p.stdout.decode("utf-8", "replace").strip() if p.returncode == 0 else cwd
    return Path(top).name or None


def _line(memory, extra: str = "") -> str:
    head = f"[{short_id(memory.id)}] ({memory.type}, {memory.about})"
    if memory.title:
        head += f" {memory.title}："
    return f"- {head} {shorten(memory.body, 160)}{extra}"


def _usage(session: Session, project: str | None) -> str:
    about = f"project:{project}" if project else "self"
    s = session.id
    return (
        "## 如何写入记忆\n"
        "遇到值得长期记住的信息（用户偏好、关于用户的事实、项目决定、反复用到的做法）时，用 Bash 运行：\n"
        f'- 新增：`locket remember "内容" --title "短标题" --type preference --about self --session {s}`\n'
        f'  （type: fact/preference/event/procedure/summary；关于当前项目用 --about {about}）\n'
        f'- 记错了要更正：`locket update <id> --text "正确内容" --session {s}`\n'
        f'- 情况变了（旧的曾经正确）：`locket supersede <id> "新内容" --session {s}`\n'
        f"- 不再需要：`locket forget <id> --session {s}`\n"
        f'- 查找：`locket recall "关键词"`；查来源：`locket why <id>`\n'
        "不要记录密码、密钥这类机密，也不要记录一次性的临时信息。会话结束时会自动合并进长期记忆。"
    )


def session_start(data: dict, store: Locket) -> str:
    if not store.exists():
        store.init()
    session = Session(AGENT, data.get("session_id") or "manual")
    store.end_stale(AGENT, keep=session.id)
    project = project_name(data.get("cwd"))
    memories = store.memories(session, include_inactive=False)
    mine = {"self"} | ({f"project:{project}"} if project else set())
    picked = [m for m in memories if m.about in mine and m.type != "event"]
    picked.sort(key=lambda m: (m.about != "self", -m.importance, m.meta.get("created", "")))

    parts = [f"# Locket 长期记忆（仓库 {store.root}，会话 {session.id}）"]
    if project:
        parts.append(f"当前项目：{project}")
    if picked:
        parts.append("## 已知信息")
        size = 0
        for m in picked:
            line = _line(m)
            size += len(line)
            if size > MAX_CONTEXT_CHARS:
                parts.append(f"- ……另有 {len(picked)} 条未列出，用 locket recall 查找")
                break
            parts.append(line)
    else:
        parts.append("目前还没有关于用户和这个项目的记忆。")
    open_conflicts = store.conflicts(session)
    if open_conflicts:
        parts.append("## 待确认的冲突（合适时向用户确认，然后运行 locket resolve <id>）")
        parts += [f"- [{short_id(c.id)}] {c.meta.get('path', '')}：{c.meta.get('question', '')}"
                  for c in open_conflicts[:5]]
    parts.append(_usage(session, project))
    return "\n".join(parts)


def prompt_submit(data: dict, store: Locket) -> str:
    if not store.exists():
        return ""
    prompt = data.get("prompt") or ""
    if len(prompt.strip()) < 4:
        return ""
    session = Session(AGENT, data.get("session_id") or "manual")
    project = project_name(data.get("cwd"))
    boost = ("self",) + ((f"project:{project}",) if project else ())
    hits: list[Hit] = [h for h in store.recall(prompt, k=5, session=session, boost_about=boost)
                       if h.matched >= 2]
    if not hits:
        return ""
    lines = ["Locket 中与这条消息相关的记忆："]
    lines += [_line(h.memory) for h in hits]
    return "\n".join(lines)


def session_end(data: dict, store: Locket) -> str:
    if not store.exists():
        return ""
    result = store.end_session(Session(AGENT, data.get("session_id") or "manual"))
    if result.pending:
        return "Locket：本次记忆已提交，等待所有者批准合并"
    if not result.merged:
        return ""
    return f"Locket：合并了 {len(result.changed)} 处变更，{len(result.conflicts)} 个冲突"


HANDLERS = {"session-start": session_start, "prompt": prompt_submit, "session-end": session_end}


def run(event: str, store: Locket) -> int:
    """Hooks must never break the host: errors go to stderr, exit code stays 0."""
    try:
        raw = sys.stdin.buffer.read().decode("utf-8", "replace") if not sys.stdin.isatty() else ""
        data = json.loads(raw) if raw.strip() else {}
        out = HANDLERS[event](data, store)
        if out:
            sys.stdout.write(out + "\n")
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"locket hook {event} 出错：{e}\n")
    return 0


def settings_snippet(command: str) -> dict:
    def entry(event: str, timeout: int) -> list:
        return [{"hooks": [{"type": "command", "command": f"{command} hook {event}",
                            "timeout": timeout}]}]

    return {"hooks": {
        "SessionStart": entry("session-start", 20),
        "UserPromptSubmit": entry("prompt", 10),
        "SessionEnd": entry("session-end", 30),
    }}


def install(settings_path: Path, command: str) -> list[str]:
    """Add Locket hooks to a Claude Code settings file, keeping everything else."""
    settings = json.loads(settings_path.read_text("utf-8")) if settings_path.exists() else {}
    hooks = settings.setdefault("hooks", {})
    added = []
    for event, entries in settings_snippet(command)["hooks"].items():
        current = hooks.setdefault(event, [])
        already = any("locket" in h.get("command", "") and " hook " in h.get("command", "")
                      for e in current for h in e.get("hooks", []))
        if not already:
            current.extend(entries)
            added.append(event)
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n", "utf-8")
    return added
