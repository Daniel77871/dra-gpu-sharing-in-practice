"""Non-CUDA owner/control helper; invoked by init container and kubectl exec."""
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path('/opt/e3-control')


def domain():
    return Path('/proc/sys/kernel/random/boot_id').read_text().strip() + '/' + os.readlink('/proc/self/ns/time')


def atomic(path, value):
    temporary = path.with_suffix('.tmp')
    if path.is_symlink() or temporary.is_symlink():
        raise ValueError('symlink control file')
    temporary.write_text(json.dumps(value) + '\n')
    os.replace(temporary, path)


def main():
    action = sys.argv[1]
    if action == 'init':
        inode = ROOT.stat()
        owner = {'run_id': os.environ['EXPERIMENT_RUN'], 'task_id': os.environ['TASK_ID'],
                 'dev': inode.st_dev, 'ino': inode.st_ino, 'uid': inode.st_uid}
        if (ROOT / 'owner.json').exists() and json.loads((ROOT / 'owner.json').read_text()) != owner:
            raise ValueError('owner changed')
        atomic(ROOT / 'owner.json', owner)
        return
    owner = json.loads((ROOT / 'owner.json').read_text())
    inode = ROOT.stat()
    if owner['run_id'] != os.environ['EXPERIMENT_RUN'] or (inode.st_dev, inode.st_ino, inode.st_uid) != (owner['dev'], owner['ino'], owner['uid']):
        raise ValueError('control ownership changed')
    if action == 'read':
        result = {p.name: json.loads(p.read_text()) for p in ROOT.glob('*.json') if not p.is_symlink()}
    elif action == 'send':
        command = json.loads(sys.argv[2])
        previous = json.loads((ROOT / 'command.json').read_text()) if (ROOT / 'command.json').exists() else {'seq': 0}
        if command['run_id'] != owner['run_id'] or command.get('clock_domain', domain()) != domain():
            raise ValueError('command owner/clock mismatch')
        if command['seq'] == previous['seq']:
            if command != previous:
                raise ValueError('command replay differs')
        else:
            if command['seq'] != previous['seq'] + 1:
                raise ValueError('control sequence mismatch')
            prerequisite = {'start': 'ready.json', 'verify': 'end.json', 'stop': 'verified.json'}
            if not command.get('abort') and not (ROOT / prerequisite[command['operation']]).is_file():
                raise ValueError('missing prerequisite')
            atomic(ROOT / 'command.json', command)
        result = command
    else:
        raise ValueError('unknown control action')
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
