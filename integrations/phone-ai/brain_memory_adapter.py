"""brain_memory_adapter.py — phone-ai 大脑调用 Locket 记忆的薄适配层（样例）。

纯标准库（urllib），无第三方依赖，方便直接放进 phone-ai 的 live/brain/ 下用。
它把大脑的「记住一条经历 / 检索相关记忆 / 这条记忆从哪来 / 更正 / 遗忘」映射到
Locket 的 HTTP API（`locket serve-api`）。两边互不改源码：这里只是一个客户端。

脑区记忆类型 → PMF type（见 README「记忆类型对齐」）：
    情景经历「谁在何时说过什么」(海马体) → type="event",      about="person:<观众ID>"（带 quote 原文）
    稳定人设/角色设定                     → type="fact",       about="self"
    观众偏好「别总反问」「短一点」         → type="preference", about="person:<观众ID>"
    关系摘要（多来源归纳）                 → type="summary",    about="person:<观众ID>"
    通用知识                               → type="fact",       about="world"
    做某事的流程/技能                      → type="procedure",  about="self"

对接约定：
  - 单仓本地模式（手机 Termux / 电脑同机）直连 serve-api，可不带 token。
  - 多租户 / 云端模式按 GITEA-SETUP.md 带 Bearer <Gitea 令牌>。
  - base_url 默认 http://127.0.0.1:18874（与 termux-setup.sh / adb reverse 端口一致）。
  - 若经 Go 服务器反代，路径前缀是 /api，用 path_prefix="/api"。

自检：python -c "import brain_memory_adapter"
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass


class LocketError(RuntimeError):
    """Locket 返回的错误（4xx 带 {"error": ...}），或网络/超时错误。"""


@dataclass
class Memory:
    """从 Locket 返回的记忆（字段与 service.mem_dict 对齐，缺的为 None）。"""
    id: str
    short: str
    type: str
    about: str
    title: str | None
    text: str | None
    score: float | None = None
    raw: dict | None = None

    @classmethod
    def from_dict(cls, d: dict, score: float | None = None) -> "Memory":
        return cls(
            id=d.get("id", ""), short=d.get("short", ""), type=d.get("type", ""),
            about=d.get("about", ""), title=d.get("title"), text=d.get("text"),
            score=score if score is not None else d.get("score"), raw=d,
        )


class LocketMemory:
    """大脑用的记忆客户端。线程不安全假设之外都安全：每次调用独立一次 HTTP。

    Examples
    --------
    >>> mem = LocketMemory(base_url="http://127.0.0.1:18874")          # doctest: +SKIP
    >>> m = mem.remember("观众阿白说今天加班到很晚", about="person:abai",
    ...                   type="event", quote="今天加班到很晚")        # doctest: +SKIP
    >>> hits = mem.recall("阿白 加班", about="person:abai", k=5)        # doctest: +SKIP
    """

    def __init__(self, base_url: str = "http://127.0.0.1:18874", token: str | None = None,
                 *, path_prefix: str = "", agent: str = "brain",
                 session: str | None = None, timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.path_prefix = path_prefix.rstrip("/")
        self.token = token
        self.agent = agent
        self.session = session
        self.timeout = timeout

    # ---- low-level ---------------------------------------------------------

    def _url(self, path: str) -> str:
        return f"{self.base_url}{self.path_prefix}{path}"

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json; charset=utf-8",
             "Accept": "application/json"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        # 写入位置：带会话则进会话分支（需显式 end_session），否则即时合并进长期记忆
        h["X-Locket-Agent"] = self.agent
        if self.session:
            h["X-Locket-Session"] = self.session
        return h

    def _call(self, method: str, path: str, body: dict | None = None) -> dict:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self._url(path), data=data, method=method,
                                     headers=self._headers())
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")
            try:
                msg = json.loads(detail).get("error", detail)
            except ValueError:
                msg = detail or str(e)
            raise LocketError(f"HTTP {e.code}: {msg}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise LocketError(f"连接 Locket 失败：{e}") from None
        return json.loads(raw) if raw.strip() else {}

    # ---- memory operations -------------------------------------------------

    def remember(self, text: str, *, about: str = "self", type: str = "fact",
                 title: str | None = None, quote: str | None = None,
                 tags: list[str] | None = None, importance: float | None = None,
                 sensitivity: str | None = None) -> Memory:
        """记住一条经历/事实/偏好。大脑按脑区类型选 type/about（见模块顶部映射）。
        quote 放原话（情景记忆的可追溯原文）。返回新建的记忆。"""
        body: dict = {"text": text, "type": type, "about": about}
        for k, v in (("title", title), ("quote", quote), ("tags", tags),
                     ("importance", importance), ("sensitivity", sensitivity)):
            if v is not None:
                body[k] = v
        return Memory.from_dict(self._call("POST", "/remember", body)["memory"])

    def recall(self, query: str, *, about: str | None = None, k: int = 5,
               include_inactive: bool = False) -> list[Memory]:
        """检索相关记忆（关键词 + 本地向量语义混合）。about 可限定到某观众/self/world。
        返回按相关度排序的记忆列表。"""
        body: dict = {"query": query, "k": k, "include_inactive": include_inactive}
        if about:
            body["about"] = about
        res = self._call("POST", "/recall", body)
        return [Memory.from_dict(h, score=h.get("score")) for h in res.get("hits", [])]

    def recall_with_conflicts(self, query: str, **kw) -> tuple[list[Memory], list[dict]]:
        """同 recall，但一并返回相关的未解决冲突（大脑可据此知道「这里有不确定」）。"""
        body: dict = {"query": query, "k": kw.get("k", 5),
                      "include_inactive": kw.get("include_inactive", False)}
        if kw.get("about"):
            body["about"] = kw["about"]
        res = self._call("POST", "/recall", body)
        hits = [Memory.from_dict(h, score=h.get("score")) for h in res.get("hits", [])]
        return hits, res.get("open_conflicts", [])

    def get(self, memory_id: str) -> Memory:
        """按 id（或 8 位短 id）读取一条记忆。"""
        return Memory.from_dict(self._call("GET", f"/memory/{memory_id}")["memory"])

    def list(self, *, about: str | None = None, type: str | None = None) -> list[Memory]:
        """列出记忆（只含标题概览，不含正文）。"""
        q = {}
        if about:
            q["about"] = about
        if type:
            q["type"] = type
        qs = ("?" + urllib.parse.urlencode(q)) if q else ""
        res = self._call("GET", f"/memories{qs}")
        return [Memory.from_dict(m) for m in res.get("memories", [])]

    def why(self, memory_id: str) -> dict:
        """这条记忆从哪来：来源链、取代/推导关系、签名状态与修改历史。
        返回原始 dict（含 sources/supersedes/derived_from/signature/history）。"""
        return self._call("GET", f"/memory/{memory_id}/why")

    def correct(self, memory_id: str, text: str, *, reason: str | None = None) -> Memory:
        """认知更正：之前记错了，同一 id 原地改成正确内容（旧版本留历史）。"""
        body: dict = {"text": text}
        if reason:
            body["reason"] = reason
        return Memory.from_dict(self._call("POST", f"/memory/{memory_id}/update", body)["memory"])

    def supersede(self, memory_id: str, text: str, *, effective: str | None = None,
                  title: str | None = None) -> dict:
        """世界变化：旧记忆曾正确、现在变了。旧的加截止日期，新建一条取代。
        返回 {"old":..., "new":...}。"""
        body: dict = {"text": text}
        for k, v in (("effective", effective), ("title", title)):
            if v is not None:
                body[k] = v
        return self._call("POST", f"/memory/{memory_id}/supersede", body)

    def forget(self, memory_id: str, *, reason: str | None = None) -> dict:
        """软遗忘（「退出记忆 / 忘掉我」）：从当前记忆移除，历史仍可回溯。"""
        body = {"reason": reason} if reason else {}
        return self._call("POST", f"/memory/{memory_id}/forget", body)

    def end_session(self) -> dict:
        """结束当前会话：把会话分支的记忆合并进长期记忆（需构造时带了 session）。"""
        return self._call("POST", "/session/end", {})

    def healthy(self) -> bool:
        """探活：Locket serve-api 是否在线（大脑可据此在本地/云端模式间决策）。"""
        try:
            return bool(self._call("GET", "/healthz").get("ok"))
        except LocketError:
            return False


if __name__ == "__main__":  # 简单自检，不联网
    m = LocketMemory(base_url="http://127.0.0.1:18874", session="demo")
    assert m._url("/recall") == "http://127.0.0.1:18874/recall"
    m2 = LocketMemory(base_url="http://h/", path_prefix="/api", token="t")
    assert m2._url("/memories") == "http://h/api/memories"
    assert m2._headers()["Authorization"] == "Bearer t"
    print("brain_memory_adapter 自检通过")
