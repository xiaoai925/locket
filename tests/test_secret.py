"""Secret-level memory encryption at rest (spec §9.3). Skips if `cryptography` absent."""
import pytest

from locket import crypto
from locket.store import Locket, LocketError, Session

pytestmark = pytest.mark.skipif(not crypto.available(),
                                reason="需要 cryptography：pip install 'locket-memory[secret]'")


@pytest.fixture
def store(tmp_path):
    s = Locket(tmp_path / "mem")
    s.init("Tester <t@example.com>")
    return s


def _set_recipients(store, pub):
    (store.root / ".pmf").mkdir(parents=True, exist_ok=True)
    (store.root / ".pmf" / "recipients.toml").write_text(f'recipients = ["{pub}"]\n', "utf-8")
    store.save()


def test_secret_rejected_without_recipients(store):
    with pytest.raises(LocketError):
        store.remember("超级机密", Session("cli", "s"), sensitivity="secret")


def test_secret_is_encrypted_at_rest_and_reveals(store):
    pub, priv = crypto.generate_identity()
    _set_recipients(store, pub)
    s = Session("cli", "s")
    m, _ = store.remember("用户的提款密码是 hunter2", s, title="pin",
                          sensitivity="secret", quote="别存明文")
    store.end_session(s)
    # on-disk / in-repo body is ciphertext, plaintext nowhere in the file
    raw = store.git.show("refs/heads/main", m.path).decode("utf-8")
    assert "hunter2" not in raw and "别存明文" not in raw  # body + quote both protected
    assert crypto.CONTAINER_VERSION in raw
    # the right private key reveals it locally
    assert store.reveal(m.id, priv) == "用户的提款密码是 hunter2"
    # a stranger's key cannot
    _, other = crypto.generate_identity()
    with pytest.raises(LocketError):
        store.reveal(m.id, other)


def test_secret_not_searchable_by_plaintext(store):
    pub, _ = crypto.generate_identity()
    _set_recipients(store, pub)
    s = Session("cli", "s")
    store.remember("秘密口令蝴蝶飞舞", s, title="x", sensitivity="secret")
    store.end_session(s)
    # the ciphertext must not surface the plaintext token through recall
    assert store.recall("蝴蝶飞舞") == []


def test_reveal_passthrough_for_plaintext(store):
    s = Session("cli", "s")
    m, _ = store.remember("普通记忆不加密", s)
    store.end_session(s)
    assert store.reveal(m.id, "anykeyignored") == "普通记忆不加密"
