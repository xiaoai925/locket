"""Tests for PMF §9.2 views: deterministic, filtered, read-only memory subsets."""
from __future__ import annotations

import pytest

from locket.store import MAIN, Locket, Session
from locket.views import generate_all, generate_view, list_views, view_specs


@pytest.fixture
def store(tmp_path):
    s = Locket(tmp_path / "mem")
    s.init("Tester <tester@example.com>")
    return s


def _add(store, text, **kw):
    sess = Session("cli", "seed")
    m, _ = store.remember(text, sess, **kw)
    store.end_session(sess)
    return m


def _write_views_toml(store, body: str):
    path = store.root / ".pmf" / "views.toml"
    path.write_text(body, "utf-8")
    store.save()


def _write_secret(store):
    """store.remember rejects secret, so craft a secret memory file by hand on main."""
    path = store.root / "self" / "secret-000000000000000000000z.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n"
        "pmf: \"0.2\"\n"
        "id: 01TESTSECRET0000000000000Z\n"
        "type: fact\n"
        "about: self\n"
        "sensitivity: secret\n"
        "---\n"
        "绝密内容\n",
        "utf-8",
    )
    store.save()
    return "self/secret-000000000000000000000z.md"


def _seed(store):
    _add(store, "我偏好中文交流", about="self", title="语言", type="preference")
    _add(store, "work 项目是公开的", about="project:work", title="公开事实",
         sensitivity="public")
    _add(store, "work 项目的敏感内幕", about="project:work", title="敏感事实",
         sensitivity="sensitive")
    _add(store, "一个通用过程", about="self", type="procedure", title="流程",
         sensitivity="public")


def test_view_specs_parses_toml(store):
    assert view_specs(store) == {}  # none yet
    _write_views_toml(store, '[work]\ninclude = ["projects/**"]\nmax_sensitivity = "public"\n')
    specs = view_specs(store)
    assert "work" in specs
    assert specs["work"]["include"] == ["projects/**"]
    assert specs["work"]["max_sensitivity"] == "public"
    assert specs["work"]["ref"] == "refs/heads/view/work"


def test_view_defaults_when_keys_missing(store):
    _write_views_toml(store, "[all]\n")
    spec = view_specs(store)["all"]
    assert spec["include"] == ["**"] and spec["exclude"] == []
    assert spec["max_sensitivity"] == "public"


def test_generate_view_filters_by_include_and_sensitivity(store):
    _seed(store)
    _write_views_toml(
        store,
        '[work]\ninclude = ["projects/**", "procedures/**"]\nmax_sensitivity = "public"\n',
    )
    result = generate_view(store, "work")
    assert result.ref == "refs/heads/view/work"
    assert store.git.rev_parse(result.ref) is not None

    paths = set(store.git.tree_files(result.ref))
    # The public project fact and the procedure are in; the sensitive project fact is not.
    assert any(p.startswith("projects/work/") for p in paths)
    assert any(p.startswith("procedures/") for p in paths)
    bodies = store.git.read_blobs(list(store.git.tree_files(result.ref).values()))
    joined = b"".join(bodies.values()).decode("utf-8")
    assert "公开" in joined
    assert "敏感内幕" not in joined          # sensitive excluded by max_sensitivity=public
    assert "偏好中文" not in joined          # self/* not matched by include
    assert result.count == len(paths)


def test_self_only_view_excludes_projects(store):
    _seed(store)
    _write_views_toml(store, '[me]\ninclude = ["self/**"]\nmax_sensitivity = "personal"\n')
    result = generate_view(store, "me")
    joined = b"".join(
        store.git.read_blobs(list(store.git.tree_files(result.ref).values())).values()
    ).decode("utf-8")
    assert "偏好中文" in joined
    assert "work 项目" not in joined


def test_secret_never_enters_view(store):
    _seed(store)
    _write_secret(store)
    # Even a permissive view (everything, high ceiling) must drop secret.
    _write_views_toml(store, '[everything]\ninclude = ["**"]\nmax_sensitivity = "sensitive"\n')
    result = generate_view(store, "everything")
    paths = set(store.git.tree_files(result.ref))
    assert not any("secret" in p for p in paths)
    joined = b"".join(
        store.git.read_blobs(list(store.git.tree_files(result.ref).values())).values()
    ).decode("utf-8")
    assert "绝密内容" not in joined
    # but the sensitive (non-secret) one IS allowed at this ceiling
    assert "敏感内幕" in joined


def test_exclude_patterns(store):
    _add(store, "普通项目记忆", about="project:work", sensitivity="public", title="普通")
    _add(store, "被排除的项目记忆", about="project:secretplan", sensitivity="public", title="排除")
    _write_views_toml(
        store,
        '[pub]\ninclude = ["projects/**"]\nexclude = ["projects/secretplan/**"]\n'
        'max_sensitivity = "public"\n',
    )
    result = generate_view(store, "pub")
    joined = b"".join(
        store.git.read_blobs(list(store.git.tree_files(result.ref).values())).values()
    ).decode("utf-8")
    assert "普通项目记忆" in joined
    assert "被排除的项目记忆" not in joined


def test_regeneration_is_deterministic(store):
    _seed(store)
    _write_views_toml(store, '[work]\ninclude = ["projects/**"]\nmax_sensitivity = "public"\n')
    generate_view(store, "work")
    tree1 = store.git.proc("rev-parse", "refs/heads/view/work^{tree}").stdout.decode().strip()
    generate_view(store, "work")
    tree2 = store.git.proc("rev-parse", "refs/heads/view/work^{tree}").stdout.decode().strip()
    assert tree1 == tree2 and tree1  # same main + same rule → identical tree


def test_regeneration_drops_now_unselected(store):
    """A memory that stops matching the rule must disappear on the next generation."""
    _add(store, "work 公开事实", about="project:work", sensitivity="public", title="公开")
    _write_views_toml(store, '[work]\ninclude = ["projects/**"]\nmax_sensitivity = "public"\n')
    r1 = generate_view(store, "work")
    assert r1.count == 1
    # Tighten the rule so nothing matches anymore.
    _write_views_toml(store, '[work]\ninclude = ["procedures/**"]\nmax_sensitivity = "public"\n')
    r2 = generate_view(store, "work")
    assert r2.count == 0
    assert list(store.git.tree_files(r2.ref)) == []  # stale file removed


def test_generate_all_and_list_views(store):
    _seed(store)
    _write_views_toml(
        store,
        '[work]\ninclude = ["projects/**"]\nmax_sensitivity = "public"\n'
        '[me]\ninclude = ["self/**"]\nmax_sensitivity = "personal"\n',
    )
    results = generate_all(store)
    assert {r.name for r in results} == {"work", "me"}
    assert dict(list_views(store)).keys() == {"work", "me"}


def test_unknown_view_raises(store):
    from locket.store import LocketError
    _write_views_toml(store, "[work]\n")
    with pytest.raises(LocketError):
        generate_view(store, "nope")
