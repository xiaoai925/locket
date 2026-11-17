"""Root-only immediate revocation of every fixed grant for one tenant."""
import fcntl
import json
import os
from pathlib import Path
import tempfile
import sys


def main():
    if os.geteuid() != 0 or len(sys.argv) != 2:
        raise SystemExit('Run as root: revoke_tenant.py USER')
    config = Path('/etc/locket-mcp/grants.json')
    with open('/etc/locket-mcp/admin.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = json.loads(config.read_text())
        found = [g for g in data['grants'] if g['user'] == sys.argv[1]]
        if not found:
            raise SystemExit('Tenant not found')
        for grant in found:
            grant['disabled'] = True
        st = config.stat()
        fd, staged = tempfile.mkstemp(prefix='.grants-', dir=config.parent)
        try:
            os.fchown(fd, st.st_uid, st.st_gid)
            os.fchmod(fd, 0o640)
            with os.fdopen(fd, 'w') as stream:
                json.dump(data, stream, indent=2)
                stream.write('\n')
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(staged, config)
        finally:
            if os.path.exists(staged):
                os.unlink(staged)
    print('Grants disabled. Stored memories were retained; no service restart required.')


if __name__ == '__main__':
    main()
