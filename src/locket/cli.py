"""locket command line."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import __version__, hooks
from .gitrepo import GitError
from .memory import CORE_TYPES, load_yaml, shorten
from .store import Locket, LocketError, Session, short_id

DEFAULT_AGENT = "cli"


def _session(args) -> Session:
    sid = args.session or os.environ.get("LOCKET_SESSION") or "manual"
    agent = args.agent or os.environ.get("LOCKET_AGENT") or (
        hooks.AGENT if args.session or os.environ.get("LOCKET_SESSION") else DEFAULT_AGENT)
    return Session(agent, sid)


def _session_or_none(args) -> Session | None:
    return _session(args) if (args.session or os.environ.get("LOCKET_SESSION")) else None


def _fields(pairs: list[str]) -> dict:
    fields = {}
    for pair in pairs or []:
        if "=" not in pair:
            raise LocketError(f"--set 需要 key=value 形式，收到 {pair!r}")
        key, value = pair.split("=", 1)
        fields[key.strip()] = None if value == "" else load_yaml(value)
    return fields


def _print_memory(m, verbose: bool = False) -> None:
    head = f"[{short_id(m.id)}] {m.type:<10} {m.about:<20}"
    expired = "" if m.active else f"（{m.meta['valid_to']} 起失效）"
    print(f"{head} {m.title + '：' if m.title else ''}{shorten(m.body, 70 if not verbose else 400)}{expired}")


def cmd_init(store: Locket, args) -> None:
    created = store.init(args.owner)
    print(("已创建记忆仓库：" if created else "记忆仓库已存在：") + str(store.root))


def cmd_remember(store: Locket, args) -> None:
    session = _session(args)
    memory, similar = store.remember(
        args.text, session, type=args.type, about=args.about, title=args.title,
        tags=args.tag, importance=args.importance, confidence=args.confidence,
        sensitivity=args.sensitivity, quote=args.quote, valid_from=args.valid_from)
    print(f"已记住 [{short_id(memory.id)}] {memory.path}")
    if similar:
        print("可能重复的已有记忆（如果是同一件事，改用 update 或 supersede，并 forget 这条新的）：")
        for hit in similar:
            _print_memory(hit.memory)
    if not args.session and not os.environ.get("LOCKET_SESSION"):
        result = store.end_session(session, owner=True)
        if result.merged:
            print("已合并进长期记忆")


def cmd_update(store: Locket, args) -> None:
    session = _session(args)
    memory = store.update(args.id, session, text=args.text, fields=_fields(args.set),
                          reason=args.reason)
    print(f"已更正 [{short_id(memory.id)}] {memory.path}")
    _autoend(store, args, session)


def cmd_supersede(store: Locket, args) -> None:
    session = _session(args)
    old, new = store.supersede(args.id, args.text, session, effective=args.effective,
                               title=args.title, quote=args.quote)
    print(f"[{short_id(old.id)}] 有效期截止 {old.meta['valid_to']}，"
          f"新记忆 [{short_id(new.id)}] {new.path}")
    _autoend(store, args, session)


def cmd_forget(store: Locket, args) -> None:
    session = _session(args)
    memory = store.forget(args.id, session, args.reason)
    print(f"已遗忘 [{short_id(memory.id)}]（历史中仍可找回）")
    _autoend(store, args, session)


def _autoend(store: Locket, args, session: Session) -> None:
    if not args.session and not os.environ.get("LOCKET_SESSION"):
        store.end_session(session, owner=True)


def cmd_recall(store: Locket, args) -> None:
    hits = store.recall(args.query, k=args.k, session=_session_or_none(args), about=args.about,
                        include_inactive=args.all)
    if args.json:
        print(json.dumps([{"id": h.memory.id, "path": h.memory.path, "score": round(h.score, 3),
                           "type": h.memory.type, "about": h.memory.about,
                           "title": h.memory.title, "text": h.memory.body} for h in hits],
                         ensure_ascii=False, indent=2))
        return
    if not hits:
        print("没有找到相关记忆")
    for hit in hits:
        _print_memory(hit.memory)


def cmd_list(store: Locket, args) -> None:
    items = store.memories(_session_or_none(args), include_inactive=args.all)
    if args.about:
        items = [m for m in items if m.about == args.about]
    if args.type:
        items = [m for m in items if m.type == args.type]
    items.sort(key=lambda m: (m.about, m.path or ""))
    for m in items:
        _print_memory(m)
    print(f"共 {len(items)} 条")


def cmd_show(store: Locket, args) -> None:
    memory = store.get(args.id, _session_or_none(args))
    print(f"# {memory.path}")
    print(memory.dumps().decode("utf-8"), end="")


def cmd_why(store: Locket, args) -> None:
    from . import signing
    memory = store.get(args.id, _session_or_none(args))
    print(f"[{short_id(memory.id)}] {memory.label}")
    print(f"  文件：{memory.path}")
    if memory.meta.get("valid_from") or memory.meta.get("valid_to"):
        print(f"  有效期：{memory.meta.get('valid_from', '…')} 至 {memory.meta.get('valid_to', '今')}")
    print("  来源：")
    for src in memory.meta.get("sources") or []:
        where = src.get("session") or src.get("from") or ""
        quote = f"  “{src['quote']}”" if src.get("quote") else ""
        print(f"    - {src.get('kind')} {where} {src.get('at', '')}{quote}")
    for key, label in (("supersedes", "取代了"), ("derived_from", "推导自")):
        for ref in memory.meta.get(key) or []:
            print(f"  {label}：[{short_id(str(ref))}]")
    history = store.history(memory)
    if history:
        print(f"  签名：{signing.describe(store.git, history[0].sha)}")
    print("  修改历史：")
    for entry in history:
        print(f"    {entry.date[:16]} {entry.sha[:7]} {entry.author:<12} {entry.subject}")


def cmd_log(store: Locket, args) -> None:
    for entry in store.git.log("refs/heads/main")[:args.n]:
        print(f"{entry.date[:16]} {entry.sha[:7]} {entry.author:<12} {entry.subject}")


def cmd_conflicts(store: Locket, args) -> None:
    items = store.conflicts(status="" if args.all else "open")
    if not items:
        print("没有待处理的冲突")
    for c in items:
        print(f"[{short_id(c.id)}] {c.meta.get('status')} {c.meta.get('path', '')}  {c.meta.get('question', '')}")


def cmd_detect_conflicts(store: Locket, args) -> None:
    findings = store.detect_conflicts()
    if not findings:
        print("未发现疑似重复/矛盾（需在 .pmf/config.toml 设 policy.semantic_conflicts = \"heuristic\" 开启）")
        return
    for f in findings:
        print(f"[{short_id(f['a'])}] × [{short_id(f['b'])}] {f['kind']}"
              f"（{f['confidence']}）：{f['question']}")


def cmd_resolve(store: Locket, args) -> None:
    item = store.resolve(args.id, args.note)
    print(f"冲突 [{short_id(item.id)}] 已标记为解决")


def cmd_redact(store: Locket, args) -> None:
    if not args.yes:
        memory = store.get(args.id)
        print(f"将永久删除 [{short_id(memory.id)}] {memory.label}，并写入墓碑。确认请加 --yes")
        return
    memory = store.redact(args.id, args.reason)
    print(f"已删除 [{short_id(memory.id)}]，墓碑已记录")


def cmd_end(store: Locket, args) -> None:
    result = store.end_session(_session(args))
    if result.pending:
        print("会话已提交，等待所有者批准合并（locket approve <会话>）")
        return
    if not result.merged:
        print("这个会话没有需要合并的变更")
        return
    print(f"已合并 {len(result.changed)} 处变更到 main（{result.commit[:7]}）")
    for path in result.conflicts:
        print(f"  冲突已记录：{path}")
    if not result.worktree_synced:
        print("  注意：工作区有未保存的手动修改，文件没有刷新；先运行 locket save")


def cmd_approve(store: Locket, args) -> None:
    agent, sid = (args.session.split("/", 1) if "/" in args.session
                  else (hooks.AGENT, args.session))
    result = store.approve(Session(agent, sid))
    if not result.merged:
        print("这个会话没有需要合并的变更")
        return
    print(f"已批准并合并 {len(result.changed)} 处变更到 main（{result.commit[:7]}）")
    for path in result.conflicts:
        print(f"  冲突已记录：{path}")


def cmd_sessions(store: Locket, args) -> None:
    items = store.sessions()
    if not items:
        print("没有进行中的会话")
    approval = store.requires_approval()
    for session, sha in items:
        tag = "  [待批准]" if approval else ""
        print(f"{session.label}  {sha[:7]}{tag}")


def cmd_save(store: Locket, args) -> None:
    commit = store.save()
    print(f"已提交手动修改（{commit[:7]}）" if commit else "没有手动修改")


def cmd_remote(store: Locket, args) -> None:
    url = store.remote(args.url)
    if args.url:
        print(f"同步远程已设为：{url}")
    elif url:
        print(url)
    else:
        print("还没有配置远程。用 locket remote <仓库地址> 设置（可指向你的 Gitea 私有仓）")


def _print_sync(label: str, r) -> None:
    if r.status in ("up-to-date",):
        print(f"{label}：已是最新{('（' + r.detail + '）') if r.detail else ''}")
        return
    if r.status == "pushed":
        print(f"{label}：已推送 main（{(r.commit or '')[:7]}）")
        return
    kind = {"fast-forward": "快进", "merge": "合并", "created": "创建"}.get(r.status, r.status)
    print(f"{label}：{kind} {len(r.changed)} 处变更" + (f"（{r.commit[:7]}）" if r.commit else ""))
    for path in r.conflicts:
        print(f"  冲突已记录：{path}")
    if not r.worktree_synced:
        print("  注意：工作区有未保存的手动修改，文件没有刷新；先运行 locket save")


def cmd_pull(store: Locket, args) -> None:
    _print_sync("拉取", store.pull())


def cmd_push(store: Locket, args) -> None:
    _print_sync("推送", store.push())


def cmd_sync(store: Locket, args) -> None:
    pulled, pushed = store.sync()
    _print_sync("拉取", pulled)
    _print_sync("推送", pushed)


def cmd_gc(store: Locket, args) -> None:
    if not args.yes:
        print("gc 会改写历史、物理删除已 redact 的记忆内容，并使下一次 push 变成强制推送。确认请加 --yes")
        return
    r = store.gc()
    if not r.get("blobs"):
        print("没有墓碑，无需清理")
        return
    print(f"已物理删除 {r['blobs']} 条被删记忆的对象，改写 {r['rewritten']} 个提交"
          f"（{r['old'][:7]} → {r['new'][:7]}）")


def cmd_reindex(store: Locket, args) -> None:
    n = store.reindex()
    extra = "（含向量索引）" if store.vectors is not None else ""
    print(f"已重建派生索引{extra}：{n} 条记忆")


def cmd_settings(store: Locket, args) -> None:
    if args.set:
        patch = {}
        for pair in args.set:
            if "=" not in pair:
                raise LocketError(f"--set 需要 key=value，收到 {pair!r}")
            key, value = pair.split("=", 1)
            # dotted keys like cloud.url / embedding.backend / privacy.auto_sync
            node = patch
            parts = key.strip().split(".")
            for seg in parts[:-1]:
                node = node.setdefault(seg, {})
            v: object = value
            if value.lower() in ("true", "false"):
                v = value.lower() == "true"
            node[parts[-1]] = v
        store.save_settings(patch)
    print(json.dumps(store.settings().to_public_dict(), ensure_ascii=False, indent=2))


def cmd_view(store: Locket, args) -> None:
    from . import views
    if args.name:
        r = views.generate_view(store, args.name)
        print(f"已生成视图 {r.name}（{r.count} 条）→ {r.ref}")
    else:
        results = views.generate_all(store)
        if not results:
            print("还没有定义视图。在 .pmf/views.toml 里定义后再运行（见 SPEC §9.2）")
        for r in results:
            print(f"已生成视图 {r.name}（{r.count} 条）→ {r.ref}")


def cmd_views(store: Locket, args) -> None:
    from . import views
    items = views.list_views(store)
    if not items:
        print("还没有生成任何视图")
    for name, sha in items:
        print(f"{name}  {sha[:7]}")


def cmd_genkey(store: Locket, args) -> None:
    from . import crypto
    if not crypto.available():
        raise LocketError("需要加密支持：pip install 'locket-memory[secret]'")
    pub, priv = crypto.generate_identity()
    print("# 把下面的【公钥】加进记忆仓的 .pmf/recipients.toml：")
    print(f'recipients = ["{pub}"]')
    print("\n# 【私钥】请自己保存好（解密用，切勿提交进仓库、切勿上传服务器）：")
    print(priv)


def cmd_reveal(store: Locket, args) -> None:
    key = args.key
    if args.key_file:
        key = Path(args.key_file).expanduser().read_text("utf-8").strip()
    if not key:
        raise LocketError("需要私钥：--key <私钥> 或 --key-file <文件>")
    print(store.reveal(args.id, key, _session_or_none(args)))


def cmd_serve_api(store: Locket, args) -> None:
    from . import webapi
    if args.gitea:
        from .service import MemoryService
        from .tenancy import GiteaAuthenticator, GiteaResolver
        base = args.gitea_url or os.environ.get("GITEA_URL")
        if not base:
            raise LocketError("--gitea 模式需要 --gitea-url 或环境变量 GITEA_URL")
        data = args.data_dir or os.environ.get("LOCKET_DATA_DIR") or str(Path(store.root) / "tenants")
        service = MemoryService(resolve=GiteaResolver(base, data))
        webapi.serve(service, host=args.host, port=args.port,
                     authenticate=GiteaAuthenticator(base))
    else:
        webapi.serve(host=args.host, port=args.port, repo=store.root)


def cmd_mcp(store: Locket, args) -> None:
    from . import mcp as mcp_server
    mcp_server.serve(repo=store.root, transport=args.transport, host=args.host,
                     port=args.port, grants=args.grants, gitea_url=args.gitea_url,
                     data_dir=args.data_dir, resource_url=args.resource_url)


def cmd_hook(store: Locket, args) -> int:
    return hooks.run(args.event, store)


def cmd_install(store: Locket, args) -> None:
    command = args.command or "locket"
    if args.print:
        print(json.dumps(hooks.settings_snippet(command), ensure_ascii=False, indent=2))
        return
    target = Path(args.settings).expanduser()
    added = hooks.install(target, command)
    print(f"已写入 {target}：{', '.join(added) if added else '钩子已存在，未改动'}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="locket", description="Locket：归用户所有、可以带走的 AI 记忆")
    p.add_argument("--version", action="version", version=f"locket {__version__}")
    p.add_argument("--repo", help="记忆仓库位置（默认 $LOCKET_HOME 或 ~/.locket）")
    sub = p.add_subparsers(dest="cmd", required=True, metavar="<命令>")

    def session_args(sp):
        sp.add_argument("--session", help="会话 id；不给时直接写入长期记忆")
        sp.add_argument("--agent", help="写入者名称（默认 claude-code 或 cli）")

    sp = sub.add_parser("init", help="创建记忆仓库")
    sp.add_argument("--owner", help='所有者，形如 "名字 <邮箱>"')
    sp.set_defaults(fn=cmd_init)

    sp = sub.add_parser("remember", help="新增一条记忆")
    sp.add_argument("text")
    sp.add_argument("--title")
    sp.add_argument("--type", default="fact", help=f"{'/'.join(CORE_TYPES)}，默认 fact")
    sp.add_argument("--about", default="self", help="self、world、project:<名>、person:<名>")
    sp.add_argument("--tag", action="append")
    sp.add_argument("--importance", type=float)
    sp.add_argument("--confidence", type=float)
    sp.add_argument("--sensitivity", choices=["public", "personal", "sensitive", "secret"],
                    help="secret 需先 genkey 并配 .pmf/recipients.toml")
    sp.add_argument("--quote", help="用户原话")
    sp.add_argument("--valid-from")
    session_args(sp)
    sp.set_defaults(fn=cmd_remember)

    sp = sub.add_parser("update", help="更正记错的记忆（同一个 id 原地修改）")
    sp.add_argument("id")
    sp.add_argument("--text")
    sp.add_argument("--set", action="append", metavar="KEY=VALUE", help="修改字段，值为空表示删除")
    sp.add_argument("--reason")
    session_args(sp)
    sp.set_defaults(fn=cmd_update)

    sp = sub.add_parser("supersede", help="情况变了：旧记忆写上截止日期，新建一条")
    sp.add_argument("id")
    sp.add_argument("text")
    sp.add_argument("--effective", help="变化生效日期，默认今天")
    sp.add_argument("--title")
    sp.add_argument("--quote")
    session_args(sp)
    sp.set_defaults(fn=cmd_supersede)

    sp = sub.add_parser("forget", help="软遗忘：从当前记忆中移除，历史中仍可找回")
    sp.add_argument("id")
    sp.add_argument("--reason")
    session_args(sp)
    sp.set_defaults(fn=cmd_forget)

    sp = sub.add_parser("recall", help="检索记忆")
    sp.add_argument("query")
    sp.add_argument("-k", type=int, default=5)
    sp.add_argument("--about")
    sp.add_argument("--all", action="store_true", help="包括已过有效期的记忆")
    sp.add_argument("--json", action="store_true")
    session_args(sp)
    sp.set_defaults(fn=cmd_recall)

    sp = sub.add_parser("list", help="列出记忆")
    sp.add_argument("--about")
    sp.add_argument("--type")
    sp.add_argument("--all", action="store_true", help="包括已过有效期的记忆")
    session_args(sp)
    sp.set_defaults(fn=cmd_list)

    sp = sub.add_parser("show", help="显示一条记忆的完整文件")
    sp.add_argument("id")
    session_args(sp)
    sp.set_defaults(fn=cmd_show)

    sp = sub.add_parser("why", help="这条记忆从哪来、改过几次")
    sp.add_argument("id")
    session_args(sp)
    sp.set_defaults(fn=cmd_why)

    sp = sub.add_parser("log", help="长期记忆的变更历史")
    sp.add_argument("-n", type=int, default=20)
    sp.set_defaults(fn=cmd_log)

    sp = sub.add_parser("conflicts", help="列出冲突")
    sp.add_argument("--all", action="store_true")
    sp.set_defaults(fn=cmd_conflicts)

    sp = sub.add_parser("detect-conflicts", help="扫描疑似重复/语义矛盾（只读预览，§6.2）")
    sp.set_defaults(fn=cmd_detect_conflicts)

    sp = sub.add_parser("resolve", help="把冲突标记为已解决")
    sp.add_argument("id")
    sp.add_argument("--note")
    sp.set_defaults(fn=cmd_resolve)

    sp = sub.add_parser("redact", help="永久删除一条记忆并写入墓碑")
    sp.add_argument("id")
    sp.add_argument("--reason", default="owner-request")
    sp.add_argument("--yes", action="store_true")
    sp.set_defaults(fn=cmd_redact)

    sp = sub.add_parser("end-session", help="把会话分支合并进长期记忆")
    session_args(sp)
    sp.set_defaults(fn=cmd_end)

    sp = sub.add_parser("approve", help="所有者批准一个待合并的会话分支（main_requires_approval 时）")
    sp.add_argument("session", help="会话标识，如 claude-code/<id> 或 <id>")
    sp.set_defaults(fn=cmd_approve)

    sp = sub.add_parser("sessions", help="列出未合并的会话")
    sp.set_defaults(fn=cmd_sessions)

    sp = sub.add_parser("save", help="提交你在仓库里手动改过的文件")
    sp.set_defaults(fn=cmd_save)

    sp = sub.add_parser("remote", help="查看或设置同步远程（可指向你的 Gitea 私有仓）")
    sp.add_argument("url", nargs="?", help="远程仓库地址；省略则显示当前远程")
    sp.set_defaults(fn=cmd_remote)

    sp = sub.add_parser("pull", help="从远程拉取长期记忆并合并")
    sp.set_defaults(fn=cmd_pull)

    sp = sub.add_parser("push", help="把本地长期记忆推送到远程")
    sp.set_defaults(fn=cmd_push)

    sp = sub.add_parser("sync", help="先 pull 再 push：把本设备和其它设备对齐")
    sp.set_defaults(fn=cmd_sync)

    sp = sub.add_parser("gc", help="改写历史、物理清除已 redact 的记忆（墓碑传播的维护步骤）")
    sp.add_argument("--yes", action="store_true")
    sp.set_defaults(fn=cmd_gc)

    sp = sub.add_parser("reindex", help="重建派生检索索引（故障或迁移后用）")
    sp.set_defaults(fn=cmd_reindex)

    sp = sub.add_parser("settings", help="查看/设置本设备记忆模式（本地/云端/混合、云、embedding、隐私）")
    sp.add_argument("--set", action="append", metavar="KEY=VALUE",
                    help="如 mode=hybrid、embedding.backend=local、cloud.url=http://...、privacy.auto_sync=true")
    sp.set_defaults(fn=cmd_settings)

    sp = sub.add_parser("genkey", help="生成加密密钥对（公钥配进 recipients.toml，私钥自存）")
    sp.set_defaults(fn=cmd_genkey)

    sp = sub.add_parser("view", help="按 .pmf/views.toml 生成只读分享视图（SPEC §9.2）")
    sp.add_argument("name", nargs="?", help="视图名；省略则生成全部")
    sp.set_defaults(fn=cmd_view)

    sp = sub.add_parser("views", help="列出已生成的视图分支")
    sp.set_defaults(fn=cmd_views)

    sp = sub.add_parser("reveal", help="用私钥在本地解密一条 secret 记忆（私钥不经服务器）")
    sp.add_argument("id")
    sp.add_argument("--key", help="私钥（base64）")
    sp.add_argument("--key-file", help="从文件读私钥")
    session_args(sp)
    sp.set_defaults(fn=cmd_reveal)

    sp = sub.add_parser("serve-api", help="启动 HTTP/JSON API（Web 与其它前端的后端）")
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=8723)
    sp.add_argument("--gitea", action="store_true", help="多租户：用 Gitea 账号鉴权、每用户一个仓")
    sp.add_argument("--gitea-url", help="Gitea 地址，默认取环境变量 GITEA_URL")
    sp.add_argument("--data-dir", help="每用户副本存放目录（--gitea 模式）")
    sp.set_defaults(fn=cmd_serve_api)

    sp = sub.add_parser("mcp", help="启动 MCP 服务器（供 Agent 接入，需 pip install 'locket-memory[mcp]'）")
    sp.add_argument("--transport", choices=("stdio", "streamable-http"), default="stdio")
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=8724)
    sp.add_argument("--grants", help="本机授权文件（SHA256 令牌摘要，不存明文令牌）")
    sp.add_argument("--gitea-url", help="改用 Gitea 账号鉴权，每个账号独立记忆仓")
    sp.add_argument("--data-dir", help="Gitea 用户副本目录")
    sp.add_argument("--resource-url", help="代理对外提供的 MCP URL")
    sp.set_defaults(fn=cmd_mcp)

    sp = sub.add_parser("hook", help="供 Claude Code 钩子调用")
    sp.add_argument("event", choices=sorted(hooks.HANDLERS))
    sp.set_defaults(fn=cmd_hook)

    sp = sub.add_parser("install-claude-hooks", help="把钩子写进 Claude Code 的 settings.json")
    sp.add_argument("--settings", default="~/.claude/settings.json")
    sp.add_argument("--command", help="调用 locket 的命令，默认 locket")
    sp.add_argument("--print", action="store_true", help="只打印配置，不写文件")
    sp.set_defaults(fn=cmd_install)
    return p


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    store = Locket(args.repo)
    try:
        code = args.fn(store, args)
    except (LocketError, GitError) as e:
        print(f"错误：{e}", file=sys.stderr)
        return 1
    return code or 0
