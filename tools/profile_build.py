#!/usr/bin/env python3
"""Measure existing cached world builds, or a tiny deterministic offline baseline.

Timing is unprofiled by default; --profile explicitly enables expensive cProfile.
The synthetic mode uses the real source/normalized cache and never accesses the
game. Invoke cold and warm_restart in separate processes with the same cache.

    python3 tools/profile_build.py --at 36.17,-115.145 --tiles 2 --out /tmp/prof.txt
"""
import argparse
import cProfile
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import pstats
import queue
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server"))


def revision_metadata():
    """Fingerprint the actual processing files, including uncommitted edits."""
    def git(*args):
        return subprocess.check_output(["git", "-C", ROOT, *args], text=True).strip()

    paths = list(Path(ROOT, "server").glob("*.py"))
    paths += [Path(__file__), Path(ROOT, "tools", "baseline_fixture.py"),
              Path(ROOT, "server", "tests", "mapfixtures.py")]
    paths += [p for p in (Path(ROOT, "server", "earthfast.pyd"),
                         Path(ROOT, "server", "earthfast.abi3.so")) if p.exists()]
    hashes = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}
    digest = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
    dependencies = {}
    for name in ("numpy", "Pillow", "duckdb", "fsspec", "certifi", "lupa"):
        try:
            dependencies[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            dependencies[name] = None
    try:
        import earthfast
        native_version = earthfast.VERSION
    except ImportError:
        native_version = None
    return {"source_commit": git("rev-parse", "HEAD"), "source_dirty": git("status", "--porcelain=v1").splitlines(),
            "processing_sha256": digest, "processing_files": hashes, "python": sys.version,
            "platform": platform.platform(), "dependencies": dependencies, "native_version": native_version,
            "pid": os.getpid(), "timings_enabled": os.environ.get("BNGE_TIMINGS") == "1"}


def checked_paths(cache_dir, output_dir, json_path):
    """Never clear an existing directory or mix source/cache/output trees."""
    cache, output = Path(cache_dir).resolve(), Path(output_dir).resolve()
    source = Path(ROOT).resolve()
    if cache == output or cache in output.parents or output in cache.parents:
        raise ValueError("cache and output directories must be separate, non-overlapping trees")
    if source == cache or source in cache.parents or source == output or source in output.parents:
        raise ValueError("benchmark cache/output must be outside the source checkout")
    if json_path is not None:
        target = Path(json_path).resolve()
        if output not in target.parents:
            raise ValueError("--json must be a file within --output-dir")
        if any(folder == target or folder in target.parents for folder in
               (output / "products", output / "fixture-source")):
            raise ValueError("--json must not replace a benchmark product or fixture source")
    return cache, output


def synthetic(a):
    from baseline_fixture import empty_jobs, run

    cache, output = checked_paths(a.cache_dir, a.output_dir, a.json)
    if a.json is None:
        raise ValueError("--synthetic requires --json inside --output-dir")
    for target in (a.dump, a.out):
        if target is not None and output not in Path(target).resolve().parents:
            raise ValueError("synthetic profile outputs must be inside --output-dir")
    if output.exists() and any(output.iterdir()):
        raise ValueError("--output-dir must be absent or empty; previous results are preserved")
    output.mkdir(parents=True, exist_ok=True)
    metadata = revision_metadata()
    metadata["profiled"] = a.profile
    report = {"schema_version": 1, "kind": "synthetic_world_build", "metadata": metadata,
              "input": None, "cache": {"mode": a.cache_mode}, "jobs": empty_jobs(),
              "outputs": None, "success": False, "error": None}
    profile = cProfile.Profile() if a.profile else None
    start, cpu = time.perf_counter(), time.process_time()
    try:
        report.update(run(cache, output, a.cache_mode, metadata["processing_sha256"], report["jobs"], profile))
        report["success"] = True
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        print(report["error"], file=sys.stderr)
    report["total_wall_s"] = time.perf_counter() - start
    report["total_process_cpu_s"] = time.process_time() - cpu
    report["scope"] = "offline diagnostic fixture; not game frame time, drivability or production queue performance"
    with open(a.json, "w", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, sort_keys=True, allow_nan=False)
    if profile and report["success"]:
        profile.dump_stats(a.dump or str(output / "profile.pstats"))
        pstats.Stats(profile).sort_stats("tottime").print_stats(a.top)
    print(f"{a.cache_mode}: {'OK' if report['success'] else 'FAILED'}; {report['total_wall_s']:.3f} s; {a.json}")
    return 0 if report["success"] else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--at", default="36.17,-115.145", help="lat,lon (an area whose map data is cached)")
    ap.add_argument("--tiles", type=int, default=1, help="road tiles from -n to n on each axis (256 m each)")
    ap.add_argument("--blocks", type=int, default=2, help="terrain blocks to build")
    state = Path(os.environ.get("LOCALAPPDATA", os.path.expanduser("~/.cache"))) / "beamng-earth-2"
    ap.add_argument("--cache-dir", default=str(state / "cache" / "profile-build"))
    ap.add_argument("--output-dir", default=str(state / "runtime" / "profile-build"))
    ap.add_argument("--synthetic", action="store_true", help="tiny deterministic fake-source baseline, no network/game")
    ap.add_argument("--cache-mode", choices=("cold", "warm_restart"), default="cold")
    ap.add_argument("--json", default=None, help="structured result inside --output-dir (required for --synthetic)")
    ap.add_argument("--out", default=None, help="write the full stats here too")
    ap.add_argument("--top", type=int, default=35)
    profiling = ap.add_mutually_exclusive_group()
    profiling.add_argument("--profile", action="store_true", help="enable expensive deterministic cProfile tracing")
    profiling.add_argument("--no-profile", dest="profile", action="store_false", help="times only (the default)")
    ap.set_defaults(profile=False)
    ap.add_argument("--dump", default=None, help="save the raw profile here (pstats, e.g. for print_callers)")
    a = ap.parse_args(argv)
    if a.tiles < 0 or a.blocks < 0:
        ap.error("--tiles and --blocks must be non-negative")
    if a.synthetic:
        try:
            return synthetic(a)
        except (ValueError, OSError) as exc:
            ap.error(str(exc))
    lat, lon = (float(v) for v in a.at.split(","))

    import blockworker
    blockworker.init(a.cache_dir, True, True, True, True, True, queue.Queue(), 30, True, True)
    import earthd
    import geodesy
    import dem as demmod
    out_dir = a.output_dir
    os.makedirs(out_dir, exist_ok=True)
    zoom = demmod.zoom_for(lat, 2.5)
    times = {}
    cpu = {}
    prof = cProfile.Profile()
    failures = []

    def timed(name, fn, *args, **kw):
        t0 = time.perf_counter()
        c0 = time.process_time()
        if a.profile:
            prof.enable()
        try:
            fn(*args, **kw)
        except Exception as e:      # a missing cell offline: reported, the rest goes on
            print(f"{name}: {type(e).__name__}: {e}", file=sys.stderr)
            failures.append(name)
        finally:
            prof.disable()
        times.setdefault(name.split("(")[0], []).append(time.perf_counter() - t0)
        cpu.setdefault(name.split("(")[0], []).append(time.process_time() - c0)

    # road tiles (with buildings, sidewalks, furniture)
    n = a.tiles
    for tx in range(-n, n + 1):
        for ty in range(-n, n + 1):
            timed(f"roads_tile({tx},{ty})", blockworker.roads_tile, 1, lat, lon, zoom, tx, ty, out_dir, "/temp/x",
                  True, 0.0, True, False, True)
    # far building tiles
    for tx in range(-n, n + 1):
        timed(f"far_buildings({tx},0)", blockworker.far_buildings_tile, 1, lat, lon, zoom, tx, 0, out_dir, "/temp/x")
    # terrain blocks (the 512 block's 128 m grid)
    frame = geodesy.EnuFrame(lat, lon)
    q = earthd.survey_q(frame, blockworker._s.dem, 1024)
    for k in range(a.blocks):
        cx = 128.0 * k
        timed(f"block({cx},0)", blockworker.build, 1, lat, lon, zoom, q, cx, 0.0, 512, out_dir, f"hm_{k}.png",
              f"hm_{k}.ter")

    print("job wall times:")
    for k, v in times.items():
        c = cpu[k]
        print(f"  {k}: {len(v)} x, avg {sum(v) / len(v):.2f} s, total {sum(v):.1f} s; CPU (all threads) avg "
              f"{sum(c) / len(c):.2f} s, total {sum(c):.1f} s")
    if not a.profile:
        return int(bool(failures))
    st = pstats.Stats(prof)
    print("\nby own time (tottime):")
    st.sort_stats("tottime").print_stats(a.top)
    print("\nby cumulative time:")
    st.sort_stats("cumulative").print_stats(a.top)
    if a.dump:
        prof.dump_stats(a.dump)
    if a.out:
        with open(a.out, "w") as f:
            pstats.Stats(prof, stream=f).sort_stats("tottime").print_stats(200)
    return int(bool(failures))


if __name__ == "__main__":
    sys.exit(main())
