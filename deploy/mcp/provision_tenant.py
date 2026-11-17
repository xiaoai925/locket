"""Root-only tenant creation; writes the plaintext token to a mode-0600 file.

Usage: /opt/locket-mcp/venv/bin/python provision_tenant.py alice /root/alice-mcp.json
Existing tenant names and output files are refused, never overwritten.
"""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import subprocess
import sys
import tempfile
import time


def main():
    if os.geteuid() != 0 or len(sys.argv) != 3:
        raise SystemExit('Run as root: provision_tenant.py USER ABSOLUTE_OUTPUT_FILE')
    user, output = sys.argv[1], Path(sys.argv[2])
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,99}', user):
        raise SystemExit('Invalid user name')
    if not output.is_absolute() or output.exists():
        raise SystemExit('Output must be an absolute, new file')
    output.parent.mkdir(parents=True, exist_ok=True)
    repo = Path('/var/lib/locket-mcp/tenants') / user
    config = Path('/etc/locket-mcp/grants.json')
    identity = pwd.getpwnam('locketmcp')
    with open('/etc/locket-mcp/admin.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = json.loads(config.read_text()) if config.exists() else {'version': 1, 'grants': []}
        if repo.exists() or any(g['user'] == user for g in data['grants']):
            raise SystemExit('Tenant already exists; use a different name')
        subprocess.run(['/usr/sbin/runuser', '-u', 'locketmcp', '--',
                        '/opt/locket-mcp/venv/bin/python', '-m', 'locket',
                        '--repo', str(repo), 'init', '--owner',
                        f'{user} <{user}@memory.invalid>'], check=True)
        token = secrets.token_urlsafe(32)
        expires = int(time.time()) + 365 * 24 * 3600
        data['grants'].append({'sha256': hashlib.sha256(token.encode()).hexdigest(),
                              'user': user, 'repo': str(repo), 'agent': 'remote-agent',
                              'scope': {'about': ['*'], 'max_sensitivity': 'personal', 'can_write': True},
                              'expires_at': expires})
        endpoint = os.environ.get('LOCKET_RESOURCE_URL', 'https://memory.example.com/mcp')
        client = {'mcpServers': {'locket': {'url': endpoint,
                   'headers': {'Authorization': f'Bearer {token}'}}}}
        fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as stream:
            json.dump(client, stream, indent=2)
            stream.write('\n')
        fd, staged = tempfile.mkstemp(prefix='.grants-', dir=config.parent)
        try:
            os.fchown(fd, 0, identity.pw_gid)
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
    print(f'Tenant {user} created. Private client config: {output}. Expires at Unix {expires}.')


if __name__ == '__main__':
    main()
