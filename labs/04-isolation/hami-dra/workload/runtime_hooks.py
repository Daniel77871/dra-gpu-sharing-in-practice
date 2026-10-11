"""Inspect the injected environment; this observer does not initialize CUDA."""
import hashlib
import json
import os
from pathlib import Path

cache = Path(os.environ['CUDA_DEVICE_MEMORY_SHARED_CACHE'])
identity = cache.stat()
library = Path('/usr/local/vgpu/libvgpu.so')
print(json.dumps({'env': {key: value for key, value in os.environ.items()
                         if key.startswith(('CUDA_', 'NVIDIA_', 'LD_', 'HAMI_'))},
                  'cache_path': str(cache), 'cache_dev': identity.st_dev, 'cache_inode': identity.st_ino,
                  'preload': Path('/etc/ld.so.preload').read_text(),
                  'library_sha256': hashlib.sha256(library.read_bytes()).hexdigest()}))
