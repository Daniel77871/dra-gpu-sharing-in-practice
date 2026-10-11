"""Uninjected host filesystem controller; this module never imports CUDA."""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import time

HOST_ROOT = ""  # Tests may replace this; the debug helper chroots before execution.


def host_path(logical):
    path = Path(logical)
    if not path.is_absolute() or ".." in path.parts:
        raise RuntimeError("host path must be absolute without traversal")
    if HOST_ROOT:
        try:
            path.relative_to(HOST_ROOT)
        except ValueError:
            return Path(HOST_ROOT) / str(path).lstrip("/")
    return path


HOOK_ROOTS = ('/usr/local/vgpu', '/var/lib/kubelet/plugins/hami-core-gpu.project-hami.io', '/tmp/vgpulock')


def identity(path):
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode):
        raise RuntimeError('control identity is not a real directory')
    return {'dev': info.st_dev, 'ino': info.st_ino, 'uid': info.st_uid}


def atomic(path, body):
    tmp = path.with_suffix('.tmp')
    if tmp.is_symlink() or path.is_symlink():
        raise RuntimeError('refusing symlink control file')
    tmp.write_text(json.dumps(body) + '\n'); os.replace(tmp, path)


def create_control(root, run_id):
    root = host_path(root)
    root.mkdir(mode=0o700)  # Must not already exist.
    owner = dict(identity(root), run_id=run_id)
    owner['clients'] = {}
    for cid in ('0', '1'):
        directory = root / cid; directory.mkdir(mode=0o700)
        item = dict(identity(directory), run_id=run_id, client_id=cid)
        atomic(directory / 'owner.json', item); owner['clients'][cid] = item
    atomic(root / 'owner.json', owner)
    return owner


def check_control(root, owner):
    root = host_path(root)
    if identity(root) != {key: owner[key] for key in ('dev', 'ino', 'uid')}:
        raise RuntimeError('control root identity changed')
    if (root / 'owner.json').is_symlink() or json.loads((root / 'owner.json').read_text()) != owner:
        raise RuntimeError('control owner identity changed')
    for cid, item in owner['clients'].items():
        directory = root / cid
        if identity(directory) != {key: item[key] for key in ('dev', 'ino', 'uid')}:
            raise RuntimeError('control client identity changed')
        if (directory / 'owner.json').is_symlink() or json.loads((directory / 'owner.json').read_text()) != item:
            raise RuntimeError('control client owner identity changed')


def send_command(root, owner, command, abort=False, previous=None):
    root = host_path(root); check_control(root, owner)
    if command['run_id'] != owner['run_id']:
        raise RuntimeError('control ownership mismatch')
    # Validate BOTH destinations before any write.
    for cid in ('0', '1'):
        path = root / cid / 'command.json'
        if path.is_symlink() or path.with_suffix('.tmp').is_symlink():
            raise RuntimeError('refusing symlink control command')
        observed = json.loads(path.read_text()) if path.exists() else {'seq': 0}
        if command['seq'] == observed['seq']:
            if observed != command:
                raise RuntimeError('control sequence was reused for another command')
        elif command['seq'] != observed['seq'] + 1:
            raise RuntimeError('control sequence is out of order')
        elif observed['seq']:
            if previous is not None and observed != previous:
                raise RuntimeError('previous control command differs from the saved command')
            if not command_acknowledged(root, owner, observed):
                raise RuntimeError('previous command barrier incomplete')
    if abort and command['operation'] != 'stop':
        raise RuntimeError('abort permits only CUDA release')
    for cid in ('0', '1'):
        atomic(root / cid / 'command.json', command)
    return command


def command_acknowledged(root, owner, command):
    acknowledged = True
    phase = {'allocate': 'allocation', 'snapshot': 'snapshot', 'stop': 'released'}[command['operation']]
    for cid in ('0', '1'):
        path = root / cid / ('ack-%d.json' % command['seq'])
        if not path.exists():
            acknowledged = False; continue
        if path.is_symlink() or not path.is_file():
            raise RuntimeError('refusing nonregular control acknowledgment')
        ack = json.loads(path.read_text())
        if (ack.get('run_id'), ack.get('client_id'), ack.get('command_seq'), ack.get('phase')) != (
                owner['run_id'], cid, command['seq'], phase):
            raise RuntimeError('control acknowledgment identity differs')
    return acknowledged


def replay_command(root, owner, command, previous=None):
    """Repair a partial send using exact local journal bodies, before advancing."""
    root = host_path(root); check_control(root, owner)
    if (command.get('run_id') != owner['run_id'] or type(command.get('seq')) is not int
            or command['seq'] <= 0 or command.get('operation') not in ('allocate', 'snapshot', 'stop')):
        raise RuntimeError('saved control command ownership/sequence differs')
    missing = False
    for cid in ('0', '1'):
        path = root / cid / 'command.json'
        if path.is_symlink() or path.with_suffix('.tmp').is_symlink():
            raise RuntimeError('refusing symlink control command')
        observed = json.loads(path.read_text()) if path.exists() else None
        if observed == command:
            continue
        if command['seq'] == 1 and observed is None:
            missing = True
        elif (previous is not None and previous.get('run_id') == owner['run_id']
              and previous.get('seq') == command['seq'] - 1 and observed == previous):
            missing = True
        else:
            raise RuntimeError('foreign or unjournaled control command; refusing replay')
    acknowledged = command_acknowledged(root, owner, command)
    if missing:
        send_command(root, owner, command, abort=command['operation'] == 'stop', previous=previous)
    return {'command': command, 'acknowledged': acknowledged}


def read_control(root, owner):
    root = host_path(root); check_control(root, owner)
    result = {}
    for cid in ('0', '1'):
        result[cid] = {}
        for path in sorted((root / cid).iterdir()):
            if path.suffix != '.json':
                continue
            if path.is_symlink() or not path.is_file():
                raise RuntimeError('control entry is not a regular file')
            result[cid][path.name] = json.loads(path.read_text())
    return result


def remove_control(root, owner, cuda_exited, resume=False):
    if not cuda_exited:
        raise RuntimeError('CUDA exit is required before deleting control')
    root = host_path(root)
    if root.is_symlink():
        raise RuntimeError('control root is a symlink')
    if resume and not root.exists():
        return {'removed': True, 'already_absent': True, 'path': str(root), 'owner': owner}
    if not resume:
        check_control(root, owner)
    elif identity(root) != {key: owner[key] for key in ('dev', 'ino', 'uid')}:
        raise RuntimeError('control root identity changed during removal retry')
    known = re.compile(r'(owner|ready|command|error|ack-\d+)\.(json|tmp)$')
    paths, directories = [], []
    if not {p.name for p in root.iterdir()} <= {'0', '1', 'owner.json'}:
        raise RuntimeError('unexpected control root entry')
    root_owner = root / 'owner.json'
    if root_owner.is_symlink() or root_owner.exists() and (
            not root_owner.is_file() or json.loads(root_owner.read_text()) != owner):
        raise RuntimeError('control owner identity changed during removal retry')
    for cid in ('0', '1'):
        directory = root / cid
        if resume and not directory.exists() and not directory.is_symlink():
            continue
        item = owner['clients'][cid]
        if identity(directory) != {key: item[key] for key in ('dev', 'ino', 'uid')}:
            raise RuntimeError('control client identity changed during removal retry')
        client_owner = directory / 'owner.json'
        if client_owner.is_symlink() or client_owner.exists() and (
                not client_owner.is_file() or json.loads(client_owner.read_text()) != item):
            raise RuntimeError('control client owner identity changed during removal retry')
        directories.append(directory)
        for path in directory.iterdir():
            if not known.fullmatch(path.name) or path.is_symlink() or not path.is_file():
                raise RuntimeError('unexpected control file; preserve entire directory')
            paths.append(path)
    for path in sorted(paths, key=lambda path: path.name == 'owner.json'):
        path.unlink()
    for directory in directories:
        directory.rmdir()
    if root_owner.exists():
        root_owner.unlink()
    root.rmdir()
    return {'removed': True, 'path': str(root), 'owner': owner}


def footprint():
    result = {}
    for dirname in HOOK_ROOTS + ('/var/run/cdi',):
        root = host_path(dirname)
        entries = []
        if os.path.lexists(root):
            for path in [root] + sorted(root.rglob('*')):
                info = path.lstat()
                row = {'path': '/' + str(path.relative_to(HOST_ROOT)).lstrip('/') if HOST_ROOT else str(path), 'mode': info.st_mode, 'dev': info.st_dev,
                       'ino': info.st_ino, 'uid': info.st_uid, 'size': info.st_size}
                if stat.S_ISREG(info.st_mode):
                    data = path.read_bytes(); row['sha256'] = hashlib.sha256(data).hexdigest()
                    if path.suffix in ('.json', '.yaml', '.yml') or 'checkpoint' in path.name:
                        try:
                            row['content'] = data.decode()
                        except UnicodeDecodeError:
                            pass
                if stat.S_ISLNK(info.st_mode):
                    row['link'] = os.readlink(path)
                entries.append(row)
        result[dirname] = entries
    return result


def proc_read(pids):
    result = {}
    for pid in pids:
        directory = host_path('/proc') / str(int(pid))
        if not directory.exists():
            result[str(pid)] = {'absent': True}; continue
        result[str(pid)] = {name: (directory / name).read_bytes().decode(errors='replace')
                            for name in ('status', 'cgroup', 'cmdline', 'maps')}
    return result


def remove_hooks(before, installed, checked):
    """Delete absent-before roots, accepting only the driver's named runtime files."""
    current = footprint()
    plugin_root = '/var/lib/kubelet/plugins/hami-core-gpu.project-hami.io'
    runtime_paths = {plugin_root + '/' + name for name in ('pu.lock', 'cp.lock', 'checkpoint.json')}
    for root in HOOK_ROOTS:
        if before[root] or current[root] != checked[root]:
            raise RuntimeError('hook root ownership/concurrent change; refusing cleanup')
        prior = {row['path']: row for row in installed[root]}
        for row in current[root]:
            path = row['path']
            checkpoint = path == plugin_root + '/checkpoint.json'
            if path in runtime_paths and (not stat.S_ISREG(row['mode']) or row['uid'] != 0
                                         or row['dev'] != prior[root]['dev']):
                raise RuntimeError('runtime hook file is not regular or its ownership changed: ' + path)
            if path not in prior:
                # Prepare creates this fixed parent below a root absent before
                # installation; Unprepare removes each Claim subdirectory.
                # Only the empty parent is allowed without an earlier inode.
                if (path == '/usr/local/vgpu/claims' and stat.S_ISDIR(row['mode'])
                        and not any(host_path(path).iterdir()) and row['uid'] == 0
                        and row['dev'] == prior[root]['dev']):
                    prior[path] = row
                elif path in runtime_paths:
                    # These fixed files can first appear during Prepare. The
                    # stopped provider and checked snapshot bound their ownership.
                    prior[path] = row
                else:
                    raise RuntimeError('unknown hook entry: ' + path)
            if checkpoint:
                state = json.loads(row.get('content', '{}'))
                if (not isinstance(state, dict) or not isinstance(state.get('v2'), dict)
                        or state['v2'].get('preparedClaims', {}) != {}):
                    raise RuntimeError('checkpoint still contains prepared Claims')
            keys = ('dev', 'uid', 'mode') if checkpoint else ('dev', 'ino', 'uid', 'mode')
            if tuple(row[k] for k in keys) != tuple(prior[path][k] for k in keys):
                raise RuntimeError('hook entry identity changed: ' + path)
            if 'sha256' in row and row.get('sha256') != prior[path].get('sha256'):
                # Claim preparation changes the driver's checkpoint. It must now be empty.
                if not checkpoint:
                    raise RuntimeError('installed hook bytes changed: ' + path)
    for row in current['/var/run/cdi']:
        if any('hami-core-gpu.project-hami.io' in row.get(key, '') for key in ('path', 'content')):
            raise RuntimeError('HAMi CDI entries remain; finish unprepare before cleanup')
    for root in HOOK_ROOTS:
        for row in sorted(current[root], key=lambda x: len(x['path']), reverse=True):
            path = host_path(row['path'])
            if stat.S_ISDIR(row['mode']):
                path.rmdir()
            else:
                path.unlink()
    return footprint()


def main():
    args = json.loads(sys.argv[1]); operation = args.pop('operation')
    if operation == 'assert-empty':
        value = footprint()
        if any(value[root] for root in HOOK_ROOTS):
            raise RuntimeError('preexisting HAMi host paths; installation refused without mutation')
    elif operation == 'create':
        value = create_control(**args)
    elif operation == 'send':
        value = send_command(**args)
    elif operation == 'replay':
        value = replay_command(**args)
    elif operation == 'read':
        value = read_control(**args)
    elif operation == 'remove':
        value = remove_control(**args)
    elif operation == 'footprint':
        value = footprint()
    elif operation == 'proc':
        value = proc_read(**args)
    elif operation == 'remove-hooks':
        value = remove_hooks(**args)
    else:
        raise ValueError('unknown host operation')
    print(json.dumps({'time_ns': time.time_ns(), 'result': value}))


if __name__ == '__main__':
    # Python and its stdlib are already loaded from the pinned debug image.
    # chroot resolves absolute host symlinks and uses host /proc.
    os.chroot('/host')
    os.chdir('/')
    main()
