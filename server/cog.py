"""A minimal Cloud-Optimised GeoTIFF reader over byte ranges (no GDAL, no tifffile).

Enough for the map-data rasters (ESA WorldCover 10 m, Meta/WRI canopy height): classic TIFF and
BigTIFF, either byte order, tiled single-band images of 8/16/32-bit integers or 32/64-bit floats,
compression none / Deflate (zlib, from the standard library) / PackBits, predictor 1 (none), 2
(horizontal differencing) or 3 (floating point, float32: the Copernicus DEM). The geotransform
comes from ModelPixelScale + ModelTiepoint, the CRS from the GeoKeys (EPSG of the geographic or
projected type), nodata from GDAL's tag 42113. The full-resolution image (the first IFD) is read,
or one of the overviews after it (level); masks are skipped. A TileCache keeps decoded tiles.

Cog(remote) takes anything with .read(start, end, kind) and .size (rangeread.RemoteFile, or
rangeread.MemoryRange for tests): the header is read once (HEADER bytes, more if an IFD array
lies beyond), then read_window() fetches only the tiles under the window, neighbouring tiles of
one row in one read when they lie close together in the file (COGs store them in row order).
Missing tiles (offset 0, byte count 0: GDAL's sparse files) are nodata.
"""
import struct
import threading
import zlib
from collections import OrderedDict

import numpy as np

import fast

HEADER = 64 * 1024          # bytes read first: every IFD of the files in use fits in it
MERGE_GAP = 64 * 1024       # tiles closer than this in the file are read in one request

TYPES = {1: ("B", 1), 2: ("s", 1), 3: ("H", 2), 4: ("I", 4), 5: ("II", 8), 6: ("b", 1), 7: ("B", 1),
         8: ("h", 2), 9: ("i", 4), 10: ("ii", 8), 11: ("f", 4), 12: ("d", 8), 16: ("Q", 8), 17: ("q", 8),
         18: ("Q", 8)}
TAGS = {"width": 256, "height": 257, "bits": 258, "compression": 259, "photometric": 262,
        "samples": 277, "planar": 284, "predictor": 317, "tile_w": 322, "tile_h": 323,
        "tile_offsets": 324, "tile_counts": 325, "sample_format": 339, "subfile": 254,
        "pixel_scale": 33550, "tiepoint": 33922, "geokeys": 34735, "nodata": 42113}
COMPRESSIONS = {1: "none", 8: "deflate", 32946: "deflate", 32773: "packbits"}


class CogError(ValueError):
    """Not a TIFF this reader supports (or a corrupt one)."""


def _dtype(bits, fmt, bo):
    kind = {1: "u", 2: "i", 3: "f"}.get(fmt)
    if kind is None or bits not in (8, 16, 32, 64) or (kind == "f" and bits < 32):
        raise CogError(f"unsupported sample format {fmt} / {bits} bits")
    return np.dtype(f"{bo}{kind}{bits // 8}")


def _packbits(data, size):
    out = bytearray()
    i, n = 0, len(data)
    while i < n and len(out) < size:
        c = data[i]
        i += 1
        if c < 128:
            out += data[i:i + c + 1]
            i += c + 1
        elif c > 128:
            out += bytes([data[i]]) * (257 - c)
            i += 1
    return bytes(out)


class TileCache:
    """Decoded tiles of any number of Cogs, least recently used out beyond max_bytes (thread-safe)."""

    def __init__(self, max_bytes):
        self.max_bytes, self.bytes = max_bytes, 0
        self._d = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            a = self._d.get(key)
            if a is not None:
                self._d.move_to_end(key)
            return a

    def put(self, key, a):
        with self._lock:
            old = self._d.pop(key, None)
            if old is not None:
                self.bytes -= old.nbytes
            self._d[key] = a
            self.bytes += a.nbytes
            while self.bytes > self.max_bytes and len(self._d) > 1:
                _, gone = self._d.popitem(last=False)
                self.bytes -= gone.nbytes


class Cog:
    def __init__(self, remote, kind="demand", level=0, tiles=None):
        """level: 0 the full-resolution image, n the n-th overview after it (its pixels as many times larger as the
        image is smaller; the georeferencing is the full image's, scaled). tiles: a TileCache that keeps decoded tiles (files read
        again and again, the polar DEM), or None."""
        self.remote, self.kind, self.level, self.tiles = remote, kind, level, tiles
        self._buf = remote.read(0, HEADER, kind)
        b = self._buf
        if b[:2] == b"II":
            self.bo = "<"
        elif b[:2] == b"MM":
            self.bo = ">"
        else:
            raise CogError("not a TIFF")
        magic = struct.unpack(self.bo + "H", b[2:4])[0]
        if magic == 42:
            self.big = False
            first = struct.unpack(self.bo + "I", b[4:8])[0]
        elif magic == 43:
            self.big = True
            first = struct.unpack(self.bo + "Q", b[8:16])[0]
        else:
            raise CogError(f"bad TIFF magic {magic}")
        self.tags = self._ifd(first)
        full = self.tags
        for _ in range(level):
            if not self._next:
                raise CogError(f"no overview {level}")
            self.tags = self._ifd(self._next)
        t = self.tags
        if TAGS["tile_w"] not in t:
            raise CogError("not tiled (strips are not supported)")
        if t.get(TAGS["samples"], (1,))[0] != 1:
            raise CogError("only single-band images are supported")
        self.width, self.height = t[TAGS["width"]][0], t[TAGS["height"]][0]
        self.tile_w, self.tile_h = t[TAGS["tile_w"]][0], t[TAGS["tile_h"]][0]
        comp = t.get(TAGS["compression"], (1,))[0]
        if comp not in COMPRESSIONS:
            raise CogError(f"unsupported compression {comp}")
        self.compression = COMPRESSIONS[comp]
        self.predictor = t.get(TAGS["predictor"], (1,))[0]
        if self.predictor not in (1, 2, 3):
            raise CogError(f"unsupported predictor {self.predictor}")
        self.dtype = _dtype(t[TAGS["bits"]][0], t.get(TAGS["sample_format"], (1,))[0], self.bo)
        self.offsets = np.asarray(t[TAGS["tile_offsets"]], np.int64)
        self.counts = np.asarray(t[TAGS["tile_counts"]], np.int64)
        self.tiles_x = -(-self.width // self.tile_w)
        self.tiles_y = -(-self.height // self.tile_h)
        if len(self.offsets) < self.tiles_x * self.tiles_y:
            raise CogError("tile offset table too short")
        nd = t.get(TAGS["nodata"], full.get(TAGS["nodata"]))
        self.nodata = None
        if nd:
            s = nd if isinstance(nd, str) else ""
            s = s.strip("\x00 ")
            try:
                self.nodata = float(s)
            except ValueError:
                self.nodata = None
        # georeferencing: pixel (col, row) corner -> (x0 + col * sx, y0 - row * sy)
        ps, tp = full.get(TAGS["pixel_scale"]), full.get(TAGS["tiepoint"])
        if ps and tp:
            sx, sy = float(ps[0]), float(ps[1])
            self.x0 = float(tp[3]) - float(tp[0]) * sx
            self.y0 = float(tp[4]) + float(tp[1]) * sy
            self.sx = sx * full[TAGS["width"]][0] / self.width
            self.sy = sy * full[TAGS["height"]][0] / self.height
        else:
            self.sx = self.sy = self.x0 = self.y0 = None
        self.epsg = self._epsg(full.get(TAGS["geokeys"]))
        self.reads = 0

    # --- header parsing -----------------------------------------------------------------------
    def _bytes(self, off, n):
        if off + n > len(self._buf):
            if off + n <= HEADER * 64:      # extend the header buffer (tables past the first read)
                self._buf += self.remote.read(len(self._buf), max(off + n, len(self._buf) + HEADER), self.kind)
            else:
                return self.remote.read(off, off + n, self.kind)
        if off + n > len(self._buf):
            raise CogError("truncated TIFF header")
        return self._buf[off:off + n]

    def _ifd(self, off):
        bo = self.bo
        if self.big:
            n = struct.unpack(bo + "Q", self._bytes(off, 8))[0]
            p, esz, inl = off + 8, 20, 8
        else:
            n = struct.unpack(bo + "H", self._bytes(off, 2))[0]
            p, esz, inl = off + 2, 12, 4
        if n > 4096:
            raise CogError("implausible IFD")
        raw = self._bytes(p, n * esz)
        tags = {}
        for i in range(n):
            e = raw[i * esz:(i + 1) * esz]
            if self.big:
                tag, typ, cnt = struct.unpack(bo + "HHQ", e[:12])
                val = e[12:20]
            else:
                tag, typ, cnt = struct.unpack(bo + "HHI", e[:8])
                val = e[8:12]
            if typ not in TYPES:
                continue
            fmt, sz = TYPES[typ]
            total = sz * cnt
            if total <= inl:
                data = val[:total]
            else:
                o = struct.unpack(bo + ("Q" if self.big else "I"), val)[0]
                data = self._bytes(o, total)
            if typ == 2:
                tags[tag] = data.decode("latin-1")
            else:
                tags[tag] = struct.unpack(bo + fmt[0] * (cnt * len(fmt)), data)
        nxt = p + n * esz
        self._next = struct.unpack(bo + ("Q" if self.big else "I"), self._bytes(nxt, 8 if self.big else 4))[0]
        return tags

    @staticmethod
    def _epsg(keys):
        if not keys or len(keys) < 4:
            return None
        found = {}
        for i in range(keys[3]):
            k, loc, cnt, val = keys[4 + 4 * i:8 + 4 * i]
            if loc == 0:
                found[k] = val
        for k in (3072, 2048):              # ProjectedCSTypeGeoKey, GeographicTypeGeoKey
            if k in found and found[k] not in (0, 32767):
                return found[k]
        return None

    # --- pixels -------------------------------------------------------------------------------
    def _decode(self, data):
        if self.compression == "deflate":
            data = zlib.decompress(data)
        elif self.compression == "packbits":
            data = _packbits(data, self.tile_w * self.tile_h * self.dtype.itemsize)
        if self.predictor == 3:
            return self._float_predictor(data)
        a = np.frombuffer(data, self.dtype, count=self.tile_w * self.tile_h).reshape(self.tile_h, self.tile_w)
        if self.predictor == 2:
            a = np.cumsum(a, axis=1, dtype=a.dtype)
        return a.astype(self.dtype.newbyteorder("="), copy=False)

    def _float_predictor(self, data):
        """TIFF's floating-point predictor (3; the Copernicus DEM): each row holds its values' bytes as planes, most
        significant first whatever the file's byte order, differenced byte by byte along the row (earthfast)."""
        if self.dtype.kind != "f" or self.dtype.itemsize != 4:
            raise CogError("predictor 3 is read for float32 samples only")
        fast.require(56, "float_predictor_f32")
        return fast._ef.float_predictor_f32(data, self.tile_w, self.tile_h)

    def _fill(self):
        return self.nodata if self.nodata is not None else 0

    def read_window(self, col0, row0, w, h):
        """Pixels [row0, row0 + h) x [col0, col0 + w) as a (h, w) array; outside the image: nodata."""
        out = np.full((h, w), self._fill(), self.dtype.newbyteorder("="))
        c0, r0 = max(0, col0), max(0, row0)
        c1, r1 = min(self.width, col0 + w), min(self.height, row0 + h)
        if c1 <= c0 or r1 <= r0:
            return out
        tx0, tx1 = c0 // self.tile_w, (c1 - 1) // self.tile_w
        ty0, ty1 = r0 // self.tile_h, (r1 - 1) // self.tile_h
        def place(i, tile):
            ty, tx = divmod(i, self.tiles_x)
            x0, y0 = tx * self.tile_w, ty * self.tile_h
            a0, a1 = max(c0, x0), min(c1, x0 + self.tile_w)
            b0, b1 = max(r0, y0), min(r1, y0 + self.tile_h)
            out[b0 - row0:b1 - row0, a0 - col0:a1 - col0] = tile[b0 - y0:b1 - y0, a0 - x0:a1 - x0]

        for ty in range(ty0, ty1 + 1):
            idx = []
            for tx in range(tx0, tx1 + 1):
                i = ty * self.tiles_x + tx
                tile = self.tiles.get((self.remote, self.level, i)) if self.tiles is not None else None
                if tile is not None:
                    place(i, tile)
                else:
                    idx.append(i)
            for group in self._groups(idx):
                start = int(self.offsets[group[0]])
                end = max(int(self.offsets[i] + self.counts[i]) for i in group)
                blob = self.remote.read(start, end, self.kind)
                self.reads += 1
                for i in group:
                    o, n = int(self.offsets[i]) - start, int(self.counts[i])
                    try:
                        tile = self._decode(blob[o:o + n])
                    except (zlib.error, ValueError):
                        # A cached block can have the right length but damaged bytes. Re-fetch
                        # this tile's range once, without discarding other cells or looping forever.
                        if not hasattr(self.remote, "invalidate"):
                            raise
                        pos = int(self.offsets[i])
                        self.remote.invalidate(pos, pos + n)
                        # Refresh the grouped buffer too: neighbouring tiles may share the same
                        # repaired block. Keeping the old blob would download that block repeatedly.
                        blob = self.remote.read(start, end, self.kind)
                        tile = self._decode(blob[o:o + n])
                        self.reads += 1
                    if self.tiles is not None:      # (the part inside the image: an overview's tile is mostly padding)
                        ty_, tx_ = divmod(i, self.tiles_x)
                        tile = np.ascontiguousarray(tile[:min(self.tile_h, self.height - ty_ * self.tile_h),
                                                         :min(self.tile_w, self.width - tx_ * self.tile_w)])
                        self.tiles.put((self.remote, self.level, i), tile)
                    place(i, tile)
        return out

    def _groups(self, idx):
        """Tile indices with data, split into runs that lie close together in the file."""
        have = sorted((i for i in idx if self.counts[i] > 0 and self.offsets[i] > 0),
                      key=lambda i: self.offsets[i])
        groups, cur, end = [], [], 0
        for i in have:
            o, e = int(self.offsets[i]), int(self.offsets[i] + self.counts[i])
            if cur and o - end > MERGE_GAP:
                groups.append(cur)
                cur = []
            end = e if not cur else max(end, e)
            cur.append(i)
        if cur:
            groups.append(cur)
        return groups

    def pixel_of(self, x, y):
        """Fractional (col, row) of CRS coordinates (x, y) (pixel corners at integers)."""
        return (np.asarray(x) - self.x0) / self.sx, (self.y0 - np.asarray(y)) / self.sy
