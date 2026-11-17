"""Real independent processes coordinate the same Git checkout."""
import multiprocessing

from locket.locking import repository_lock
from locket.service import MemoryService, Principal
from locket.store import Locket


def write(root, index):
    service = MemoryService(repo=root)
    try:
        service.remember(Principal(), f"Synthetic memory {index}")
    finally:
        service.repo(Principal()).close()


def test_multiple_process_writers_do_not_lose_commits(tmp_path):
    root = str(tmp_path / "repo")
    Locket(root).init("Synthetic <t@example.invalid>")
    ctx = multiprocessing.get_context("spawn")
    processes = [ctx.Process(target=write, args=(root, i)) for i in range(3)]
    for p in processes:
        p.start()
    try:
        for p in processes:
            p.join(40)
            assert p.exitcode == 0
        store = Locket(root)
        try:
            assert len(store.memories()) == 3
        finally:
            store.close()
    finally:
        for p in processes:
            if p.is_alive():
                p.terminate()
                p.join()


def test_repository_lock_reentrant_and_released(tmp_path):
    with repository_lock(tmp_path):
        with repository_lock(tmp_path):
            pass
    with repository_lock(tmp_path, timeout=.1):
        pass
