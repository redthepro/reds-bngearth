"""Bounded cache of compiled bytes whose materialized encoder inputs were verified.

This does not cache geographic decisions or declare incomplete source facts final.
An artifact is one atomic file: bounded JSON plus checksummed product bytes. OS
locks on short-lived pin files protect readers/builders across worker processes;
closing the descriptor releases a crashed process's pin without an age guess.
"""
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import threading
import time
import uuid

import staging
import xproc

MAGIC = b"BE2ART1\n"
SCHEMA = 1
MAX_MANIFEST = 1 << 20
KEY_RE = re.compile(r"^[0-9a-f]{64}$")
ROLE_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,95}$")


def _lock(file, acquire=True):
    file.seek(0)
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK if acquire else msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB if acquire else fcntl.LOCK_UN)


@contextlib.contextmanager
def _guard(path, timeout=0.5):
    """Only short pin/publication/eviction transitions hold this root mutex."""
    file = open(path, "a+b")
    start = time.monotonic()
    try:
        while True:
            try:
                _lock(file)
                break
            except OSError:
                if time.monotonic() - start >= timeout:
                    raise TimeoutError("compiled cache metadata lock busy")
                time.sleep(0.005)
        yield
    finally:
        file.close()


class ArtifactCache:
    def __init__(self, root, cap_bytes=256 << 20, max_entry_bytes=64 << 20, max_entries=512):
        self.root = Path(root)
        self.cap_bytes, self.max_entry_bytes, self.max_entries = int(cap_bytes), int(max_entry_bytes), int(max_entries)
        if min(self.cap_bytes, self.max_entry_bytes, self.max_entries) < 1:
            raise ValueError("compiled cache limits must be positive")
        self._stats = dict.fromkeys(("hit", "miss", "build", "stored", "invalid", "evicted", "oversize", "bypass", "wait"), 0)
        self._stats_lock = threading.Lock()

    def _count(self, name):
        with self._stats_lock:
            self._stats[name] += 1

    def snapshot(self):
        with self._stats_lock:
            return dict(self._stats)

    @property
    def mutex(self):
        return self.root / ".metadata.lock"

    def _path(self, key):
        if not isinstance(key, str) or not KEY_RE.fullmatch(key):
            raise ValueError("artifact key must be a sha256 hex digest")
        return self.root / (key + ".artifact")

    @contextlib.contextmanager
    def _pin(self, key):
        self.root.mkdir(parents=True, exist_ok=True)
        pins = self.root / "pins"
        pins.mkdir(exist_ok=True)
        path = pins / f"{key}.{os.getpid()}.{uuid.uuid4().hex}.pin"
        file = None
        try:
            with _guard(self.mutex):
                file = open(path, "x+b")
                _lock(file)
            yield path.name
        finally:
            if file is not None:
                # Keep the descriptor locked until the transition mutex is held.
                try:
                    with _guard(self.mutex):
                        file.close()
                        file = None
                        path.unlink(missing_ok=True)
                except OSError:
                    pass
                finally:
                    if file is not None:
                        file.close()  # stale unlocked pin is cleaned by a later pruner

    @staticmethod
    def _pin_active(path):
        try:
            file = open(path, "r+b")
        except FileNotFoundError:
            return False
        except OSError:
            return True
        try:
            try:
                _lock(file)
            except OSError:
                return True
        finally:
            file.close()
        return False

    def _pinned(self, key):
        for path in (self.root / "pins").glob(key + ".*.pin"):
            if self._pin_active(path):
                return True
            try:
                path.unlink()
            except OSError:
                return True
        return False

    def _load(self, key, descriptor, roles):
        path = self._path(key)
        try:
            with open(path, "rb") as file:
                prefix = file.read(16)
                if len(prefix) != 16:
                    raise ValueError("truncated artifact prefix")
                magic, length = struct.unpack("<8sQ", prefix)
                if magic != MAGIC or not 0 < length <= MAX_MANIFEST:
                    raise ValueError("artifact schema/prefix")
                header = json.loads(file.read(length))
                if not isinstance(header, dict):
                    raise ValueError("artifact header must be an object")
                saved_descriptor = json.dumps(header.get("descriptor"), sort_keys=True, separators=(",", ":"), allow_nan=False)
                expected_descriptor = json.dumps(descriptor, sort_keys=True, separators=(",", ":"), allow_nan=False)
                if type(header.get("schema")) is not int or header["schema"] != SCHEMA or header.get("key") != key or saved_descriptor != expected_descriptor:
                    raise ValueError("artifact descriptor mismatch")
                products = header.get("products")
                if not isinstance(products, list) or not 0 < len(products) <= 64:
                    raise ValueError("artifact product list")
                if any(not isinstance(r, dict) for r in products):
                    raise ValueError("artifact product header type")
                if {r.get("role") for r in products} != set(roles) or len(products) != len(roles):
                    raise ValueError("artifact role mismatch")
                out, total = {}, 16 + length
                for row in products:
                    role, size, digest = row["role"], row["bytes"], row["sha256"]
                    if not ROLE_RE.fullmatch(role) or not isinstance(size, int) or isinstance(size, bool) or size < 0:
                        raise ValueError("artifact product header")
                    total += size
                    if total > self.max_entry_bytes or total > self.cap_bytes:
                        raise ValueError("artifact too large")
                    body = file.read(size)
                    if len(body) != size or hashlib.sha256(body).hexdigest() != digest:
                        raise ValueError("artifact payload checksum")
                    out[role] = body
                if file.read(1):
                    raise ValueError("artifact trailing bytes")
            try:
                os.utime(path, None)  # recency only; never used as source validity
            except OSError:
                pass
            return out
        except FileNotFoundError:
            return None
        except (OSError, ValueError, TypeError, KeyError, UnicodeError, struct.error, RecursionError):
            self._count("invalid")
            return None

    def _encode(self, key, descriptor, products):
        if not isinstance(products, dict) or not 0 < len(products) <= 64:
            raise ValueError("builder must return bounded product bytes")
        for role, body in products.items():
            if not isinstance(role, str) or not ROLE_RE.fullmatch(role) or not isinstance(body, bytes):
                raise ValueError("invalid compiled product role/body")
        rows = [{"role": role, "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}
                for role, body in sorted(products.items())]
        header = json.dumps({"schema": SCHEMA, "key": key, "descriptor": descriptor, "products": rows},
                            sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        if len(header) > MAX_MANIFEST:
            raise ValueError("compiled manifest exceeds bound")
        return struct.pack("<8sQ", MAGIC, len(header)) + header + b"".join(products[r["role"]] for r in rows)

    def _room(self, needed, keep_key=None, own_temporary=None):
        """Caller holds root mutex. Never evict an active read/build/install."""
        # A crashed writer releases its OS pin. Only those unpinned temporary
        # blobs can be removed; age alone cannot prove that a writer is dead.
        for temporary in self.root.glob(".*.tmp"):
            parts = temporary.name.split(".")
            if temporary == own_temporary:
                continue
            owner = self.root / "pins" / (temporary.name[1:-4] + ".pin")
            if len(parts) == 5 and KEY_RE.fullmatch(parts[1]) and not self._pin_active(owner):
                try:
                    temporary.unlink()
                    owner.unlink(missing_ok=True)
                except OSError:
                    pass
        entries = []
        for path in self.root.glob("*.artifact"):
            try:
                st = path.stat()
            except OSError:
                continue
            entries.append((st.st_mtime_ns, path.name, st.st_size, path))
        replacing = next((r[2] for r in entries if r[3].stem == keep_key), 0)
        total = sum(r[2] for r in entries) - replacing + needed
        count = len(entries) + int(needed > 0 and not replacing)
        for _, _, size, path in sorted(entries):
            if total <= self.cap_bytes and count <= self.max_entries:
                break
            if path.stem == keep_key or self._pinned(path.stem):
                continue
            try:
                path.unlink()
            except OSError:
                continue
            self._count("evicted")
            total -= size
            count -= 1
        return total <= self.cap_bytes and count <= self.max_entries

    def prune(self):
        self.root.mkdir(parents=True, exist_ok=True)
        with _guard(self.mutex):
            return self._room(0)

    def _publish(self, key, descriptor, products, pin_name=None):
        blob = self._encode(key, descriptor, products)
        if len(blob) > min(self.max_entry_bytes, self.cap_bytes):
            self._count("oversize")
            return False
        stem = Path(pin_name).stem if pin_name is not None else f"{key}.{os.getpid()}.{uuid.uuid4().hex}"
        temporary = self.root / f".{stem}.tmp"
        try:
            with open(temporary, "xb") as file:
                file.write(blob)
                file.flush()
                os.fsync(file.fileno())
            with _guard(self.mutex):
                if not self._room(len(blob), key, temporary):
                    self._count("bypass")
                    return False
                os.replace(temporary, self._path(key))
            self._count("stored")
            return True
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _install(products, targets):
        if set(products) != set(targets):
            raise ValueError("compiled product targets differ from builder roles")
        for role, path_ in targets.items():
            path = os.fspath(path_)
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            temporary = staging.tmp_for(path) + "." + uuid.uuid4().hex + ".tmp"
            try:
                with open(temporary, "xb") as file:
                    file.write(products[role])
                staging.replace(temporary, path)
            finally:
                try:
                    os.remove(temporary)
                except FileNotFoundError:
                    pass

    def materialize(self, key, descriptor, builder, targets, kind="demand"):
        self._path(key)
        if not targets or any(not ROLE_RE.fullmatch(role) for role in targets):
            raise ValueError("invalid artifact targets")
        # Cache availability is optional, but generation/installation errors must
        # propagate. Pin acquisition failure cannot turn into a generation failure.
        try:
            pin = self._pin(key)
            pin_name = pin.__enter__()
        except OSError:
            self._count("bypass")
            products = builder()
            self._install(products, targets)
            return {"hit": False, "stored": False, "bytes": sum(map(len, products.values())), "key": key}
        mine = False
        try:
            ready = lambda: self._load(key, descriptor, targets)
            products, mine = xproc.once(str(self.root / (key + ".lock")), ready, wait_s=2.0,
                                        kind=kind, on_wait=lambda: self._count("wait"))
            hit, stored = products is not None, False
            if hit:
                self._count("hit")
            else:
                self._count("miss")
                self._count("build")
                products = builder()
                if set(products) != set(targets):
                    raise ValueError("builder product roles differ from requested targets")
                try:
                    stored = self._publish(key, descriptor, products, pin_name)
                except (OSError, ValueError, TypeError):
                    self._count("bypass")
            self._install(products, targets)
            return {"hit": hit, "stored": stored, "bytes": sum(map(len, products.values())), "key": key}
        finally:
            if mine:
                xproc.release(str(self.root / (key + ".lock")))
            pin.__exit__(None, None, None)
