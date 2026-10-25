"""Persistent, versioned and size-bounded replies for compute sharing."""
import os
import threading
from pathlib import Path


class ReplyCache:
    def __init__(self, root, max_bytes=1 << 30, namespace=None):
        import peerwork
        self.root = os.path.abspath(root)
        self.max_bytes = max(0, int(max_bytes))
        self.namespace = namespace or f'{peerwork.PROTOCOL}-{peerwork.code_id()}'
        if not self.namespace or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in self.namespace):
            raise ValueError('invalid cache namespace')
        self.lock = threading.Lock()
        self.sizes = {}
        self.total = 0
        entries = []
        for path in Path(self.root).rglob('*.bin'):
            if path.is_symlink():
                continue
            try:
                st = path.stat()
                entries.append((st.st_mtime_ns, str(path), st.st_size))
            except OSError:
                continue
        for _, path, size in sorted(entries):
            self.sizes[path] = size
            self.total += size
        self._trim()

    def _path(self, key):
        if not isinstance(key, str) or len(key) != 32 or any(c not in '0123456789abcdef' for c in key):
            raise ValueError('invalid cache key')
        return os.path.join(self.root, self.namespace, key[:2], key + '.bin')

    def _remove(self, path):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except OSError:
            return False
        self.total -= self.sizes.pop(path, 0)
        return True

    def _trim(self):
        for path in list(self.sizes):
            if self.total <= self.max_bytes:
                break
            self._remove(path)

    def get(self, key):
        import peerwork
        path = self._path(key)
        with self.lock:
            if path not in self.sizes:
                return None
            try:
                with open(path, 'rb') as f:
                    data = f.read(peerwork.MAX_MESSAGE + peerwork.MAX_HEADER + 128 + 1)
                if len(data) > peerwork.MAX_MESSAGE + peerwork.MAX_HEADER + 128:
                    raise ValueError('oversized cached reply')
                reply = peerwork.decode_msg(data)
                os.utime(path, None)
            except (OSError, ValueError, ConnectionError):
                self._remove(path)
                return None
            self.sizes[path] = self.sizes.pop(path)
            return reply

    def put(self, key, header, blobs):
        import peerwork
        path = self._path(key)
        data = peerwork.encode_msg(header, blobs)
        with self.lock:
            if len(data) > self.max_bytes:
                self._remove(path)
                return
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = f'{path}.{os.getpid()}.{threading.get_ident()}.tmp'
            try:
                with open(tmp, 'wb') as f:
                    f.write(data)
                os.replace(tmp, path)
            finally:
                try:
                    os.remove(tmp)
                except FileNotFoundError:
                    pass
            self.total += len(data) - self.sizes.pop(path, 0)
            self.sizes[path] = len(data)
            self._trim()
