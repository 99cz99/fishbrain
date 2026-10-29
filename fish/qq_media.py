"""Read QQ images from network or confined NapCat/QQ caches."""
import base64
import hashlib
import re
import urllib.request
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOTFS = Path('/data/data/com.termux/files/usr/var/lib/proot-distro/containers/debian/rootfs')
MAX_IMAGE = 20 * 1024 * 1024


def mime_of(raw):
    if raw.startswith(b'\x89PNG\r\n\x1a\n'): return 'image/png'
    if raw.startswith(b'\xff\xd8\xff'): return 'image/jpeg'
    if raw.startswith((b'GIF87a', b'GIF89a')): return 'image/gif'
    if raw.startswith(b'RIFF') and raw[8:12] == b'WEBP': return 'image/webp'
    if raw.startswith(b'BM'): return 'image/bmp'
    return None


def validated(raw):
    mime = mime_of(raw)
    if not mime or len(raw) > MAX_IMAGE:
        raise ValueError('invalid image body or size')
    return raw, mime


def download(url):
    if urlparse(url).scheme not in ('http','https'):
        raise ValueError('unsupported URL scheme')
    req = urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0'})
    with urllib.request.urlopen(req,timeout=12) as response:
        return validated(response.read(MAX_IMAGE+1))


def local_image(value, rootfs=None):
    rootfs = ROOTFS if rootfs is None else Path(rootfs)
    if not isinstance(value,str) or not value:
        raise ValueError('empty file')
    if value.startswith('base64://'):
        if len(value) > MAX_IMAGE*4//3+32: raise ValueError('image too large')
        return validated(base64.b64decode(value[9:],validate=True))
    if value.startswith('file://'):
        value = unquote(urlparse(value).path)
    path = Path(value)
    # Only QQ-owned caches may be mapped from a container path. No traversal/symlink escape.
    roots = [rootfs/'root/.config/QQ',rootfs/'opt/QQ/resources/app/app_launcher/napcat/cache',
             rootfs/'opt/QQ/resources/app/app_launcher/napcat/temp',rootfs/'tmp']
    candidates = [path]
    if value.startswith('/'):
        candidates.append(rootfs/value.lstrip('/'))
    for candidate in candidates:
        resolved = candidate.resolve()
        if any(resolved.is_relative_to(r.resolve()) for r in roots) and resolved.is_file():
            with resolved.open('rb') as file:
                return validated(file.read(MAX_IMAGE+1))
    raise FileNotFoundError('not in QQ cache')


def cached_image(file_ref, rootfs=None):
    """An exact MD5 match, never arbitrary filename/path searches from a QQ message."""
    rootfs = ROOTFS if rootfs is None else Path(rootfs)
    match = re.fullmatch(r'([a-fA-F0-9]{32})(?:\.[a-zA-Z0-9]+)?',str(file_ref))
    if not match:
        raise FileNotFoundError('no image hash')
    digest = match[1].lower()
    qq = rootfs/'root/.config/QQ'
    for pattern in (f'nt_qq_*/nt_data/Pic/*/Ori/{digest}.*',
                    f'nt_qq_*/nt_data/Pic/*/Ori/{digest.upper()}.*'):
        for path in qq.glob(pattern):
            raw,mime = local_image(str(path),rootfs)
            if hashlib.md5(raw).hexdigest() == digest:
                return raw,mime
    raise FileNotFoundError('original image not cached')
