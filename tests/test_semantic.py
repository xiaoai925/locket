"""Tests for the semantic conflict detector (spec §6, strategy layer). Pure Memory, no git."""
from locket.ids import new_ulid
from locket.memory import Memory
from locket.semantic import (Finding, HeuristicDetector, LLMDetector, NullDetector)


def mk(body, *, about="self", type="fact", title="", mid=None, supersedes=None):
    meta = {"pmf": "0.2", "id": mid or new_ulid(), "type": type, "about": about}
    if title:
        meta["title"] = title
    if supersedes:
        meta["supersedes"] = supersedes
    return Memory(meta, body, f"self/{meta['id'][-8:].lower()}.md")


def cands(*mems):
    return [(m, 1.0) for m in mems]


# ---- NullDetector ----------------------------------------------------------

def test_null_detector_is_always_empty():
    nd = NullDetector()
    a = mk("用户喜欢喝茶", title="饮品")
    b = mk("用户喜欢喝茶", title="饮品")
    assert nd.judge(a, cands(b)) == []
    assert nd.judge(a, []) == []


# ---- HeuristicDetector -----------------------------------------------------

def test_near_identical_same_about_is_duplicate():
    det = HeuristicDetector()
    a = mk("用户的主力开发环境是 Windows 11 系统", title="环境")
    b = mk("用户的主力开发环境是 Windows 11 系统", title="环境")
    out = det.judge(a, cands(b))
    assert len(out) == 1
    assert out[0].kind == "duplicate" and out[0].other_id == b.id
    assert 0.0 <= out[0].confidence <= 1.0


def test_same_topic_type_differing_text_is_contradiction():
    det = HeuristicDetector()
    # medium-high overlap (shared words) but differing bodies, same about + type
    a = mk("用户的主力开发环境是 Windows 11 系统 常用 py 命令", title="环境", type="fact")
    b = mk("用户的主力开发环境是 Windows 11 系统 偶尔用别的", title="环境", type="fact")
    out = det.judge(a, cands(b))
    assert len(out) == 1
    assert out[0].kind == "contradiction" and out[0].other_id == b.id
    assert 0.5 <= out[0].confidence < 0.85


def test_different_about_is_not_reported():
    det = HeuristicDetector()
    a = mk("项目使用 Python 3.12 来开发", about="project:x")
    b = mk("项目使用 Python 3.12 来开发", about="project:y")
    assert det.judge(a, cands(b)) == []


def test_mutual_supersede_is_skipped():
    det = HeuristicDetector()
    old = mk("用户住在北京的城区", title="居住", mid=new_ulid())
    new = mk("用户住在上海的城区", title="居住", mid=new_ulid(), supersedes=[old.id])
    # even though overlapping, the supersede link means it's a world-change, not a conflict
    assert det.judge(new, cands(old)) == []
    assert det.judge(old, cands(new)) == []


def test_unrelated_low_overlap_not_reported():
    det = HeuristicDetector()
    a = mk("用户喜欢在周末登山远足", title="爱好")
    b = mk("服务器部署在东京的机房里", title="运维")
    assert det.judge(a, cands(b)) == []


def test_self_is_skipped():
    det = HeuristicDetector()
    a = mk("完全相同的一条记忆内容", title="x")
    assert det.judge(a, cands(a)) == []


def test_confidence_bounds_and_descending_order():
    det = HeuristicDetector()
    base = mk("用户的主力开发环境是 Windows 11 系统 常用 py 命令", title="环境")
    dup = mk("用户的主力开发环境是 Windows 11 系统 常用 py 命令", title="环境")   # ~1.0
    partial = mk("用户的主力开发环境是 Windows 11 系统 偶尔用别的", title="环境")  # lower
    out = det.judge(base, cands(partial, dup))
    assert all(0.0 <= f.confidence <= 1.0 for f in out)
    assert out == sorted(out, key=lambda f: f.confidence, reverse=True)
    assert len(out) >= 1


def test_empty_candidates_and_empty_title():
    det = HeuristicDetector()
    a = mk("没有标题的记忆正文内容在此", title="")
    assert det.judge(a, []) == []
    b = mk("没有标题的记忆正文内容在此", title="")
    out = det.judge(a, cands(b))
    assert len(out) == 1 and out[0].kind == "duplicate"


# ---- LLMDetector -----------------------------------------------------------

def test_llm_detector_without_fn_is_empty():
    det = LLMDetector()
    a = mk("任意内容", title="x")
    b = mk("另一个内容", title="y")
    assert det.judge(a, cands(b)) == []


def test_llm_detector_without_candidates_is_empty():
    det = LLMDetector(judge_fn=lambda m, cs: [{"index": 0, "kind": "contradiction",
                                               "question": "?", "confidence": 0.9}])
    a = mk("任意内容", title="x")
    assert det.judge(a, []) == []


def test_llm_detector_maps_fn_output_to_findings():
    def fake_fn(memory_text, candidates_texts):
        return [{"index": 1, "kind": "contradiction", "question": "矛盾吗？", "confidence": 0.8},
                {"index": 0, "kind": "duplicate", "question": "重复吗？", "confidence": 0.95}]
    det = LLMDetector(judge_fn=fake_fn)
    a = mk("主记忆", title="m")
    c0 = mk("候选零", title="c0")
    c1 = mk("候选一", title="c1")
    out = det.judge(a, cands(c0, c1))
    assert len(out) == 2
    # sorted by confidence desc
    assert out[0].confidence == 0.95 and out[0].other_id == c0.id and out[0].kind == "duplicate"
    assert out[1].confidence == 0.8 and out[1].other_id == c1.id and out[1].kind == "contradiction"


def test_llm_detector_ignores_out_of_range_index():
    det = LLMDetector(judge_fn=lambda m, cs: [{"index": 9, "kind": "duplicate",
                                              "question": "?", "confidence": 0.5}])
    a = mk("主记忆", title="m")
    b = mk("候选", title="c")
    assert det.judge(a, cands(b)) == []
