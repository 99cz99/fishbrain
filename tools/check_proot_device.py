"""Bounded, read-only container launch checks; does not start QQ."""
import os
import subprocess
import time

binary = '/data/data/com.termux/files/usr/bin'
for mode in ('compatibility', 'accelerated'):
    env = os.environ.copy()
    env['PATH'] = binary+':'+binary+'/applets:'+env.get('PATH', '')
    if mode == 'compatibility':
        env['PROOT_NO_SECCOMP'] = '1'
    else:
        env.pop('PROOT_NO_SECCOMP', None)
    start = time.monotonic()
    try:
        p = subprocess.run([binary+'/proot-distro', 'login', 'debian', '--', 'true'],
                           env=env, timeout=8, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(mode, 'exit', p.returncode, 'seconds', round(time.monotonic()-start, 2), flush=True)
    except subprocess.TimeoutExpired:
        print(mode, 'timed out', flush=True)
