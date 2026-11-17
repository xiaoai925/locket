"""Embedding layer (P6): offline default embedder + pluggable adapter."""
import math

from locket.embed import HashingEmbedder, CallableEmbedder, cosine, default_embedder


def _norm(v):
    return math.sqrt(sum(x * x for x in v))


def test_hashing_is_deterministic_and_normalized():
    e = HashingEmbedder()
    assert e.dim == 256 and e.name == "hashing-v1"
    v = e.embed("用户喜欢清晨跑步")
    assert abs(_norm(v) - 1.0) < 1e-9
    assert e.embed("用户喜欢清晨跑步") == v  # deterministic


def test_similar_scores_higher_than_unrelated():
    e = HashingEmbedder()
    q = e.embed("用户清晨六点起床跑步锻炼身体")
    near = e.embed("用户喜欢清晨六点起床跑步锻炼")
    far = e.embed("明天的天气预报是下雨刮风")
    assert cosine(q, near) > cosine(q, far)


def test_empty_and_dim_safety():
    e = HashingEmbedder(dim=64)
    assert e.embed("") == [0.0] * 64
    assert cosine([1.0, 0.0], [1.0, 0.0, 0.0]) == 0.0  # mismatched dims safe
    assert cosine([], [1.0]) == 0.0


def test_callable_embedder_normalizes_and_checks_dim():
    e = CallableEmbedder(lambda t: [3.0, 4.0], dim=2, name="x")
    v = e.embed("whatever")
    assert abs(_norm(v) - 1.0) < 1e-9 and v == [0.6, 0.8]


def test_default_embedder_is_offline_hashing():
    assert default_embedder().name == "hashing-v1"
