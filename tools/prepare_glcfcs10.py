#!/usr/bin/env python3
"""Prepare one bounded local GLC_FCS10 GeoTIFF window; never download archives."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
import glcfcs10


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="local publisher GeoTIFF, already extracted")
    parser.add_argument("root", type=Path, help="prepared semantic catalog directory")
    parser.add_argument("--bounds", nargs=4, type=float, required=True, metavar=("W", "S", "E", "N"))
    parser.add_argument("--shape", nargs=2, type=int, default=(128, 128), metavar=("ROWS", "COLS"))
    parser.add_argument("--acquired-at", required=True, help="actual acquisition timestamp, preferably ISO 8601 UTC")
    parser.add_argument("--expected-sha256", required=True, help="SHA256 of the extracted input TIFF")
    parser.add_argument("--source-revision", default=glcfcs10.SOURCE_REVISION)
    parser.add_argument("--band", type=int, default=1)
    parser.add_argument("--source-crs", help="explicit CRS override only if the input has verified missing/wrong metadata")
    parser.add_argument("--source-nodata", type=float, help="verified nodata override; no 0/255 assumption is made")
    args = parser.parse_args(argv)
    expected = args.expected_sha256.lower()
    if not re.fullmatch("[0-9a-f]{64}", expected):
        parser.error("--expected-sha256 must be 64 hexadecimal characters")
    if not args.source.is_file() or args.source.stat().st_size > (1 << 30):
        parser.error("source must be a local TIFF of at most 1 GiB")
    digest = hashlib.sha256()
    with args.source.open("rb") as source:
        for block in iter(lambda: source.read(1 << 20), b""):
            digest.update(block)
    if digest.hexdigest() != expected:
        parser.error("source checksum differs from the pinned input")
    metadata = dict(acquired_at=args.acquired_at, source_revision=args.source_revision,
                    source_crs=args.source_crs or "from_input", expected_source_sha256=expected)
    row = glcfcs10.prepare(args.source, args.root, args.bounds, tuple(args.shape), metadata,
                          band=args.band, src_crs=args.source_crs, src_nodata=args.source_nodata)
    print(json.dumps(row, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
