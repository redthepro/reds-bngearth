"""Small, deterministic offline inputs for profile_build's diagnostic mode.

This adapter deliberately uses the repository's synthetic GeoParquet transport and
the real MapData cache. It never opens a live provider or a game connection.
"""
import hashlib
import json
import os
from pathlib import Path
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np


STAGES = ("source_fetch", "normalization", "terrain_planning", "road_planning",
          "compilation", "serialization", "queue_wait", "engine_installation", "activation")
REVISION = "mapfixtures-roads-baseline-v1"


def empty_jobs():
    jobs = {name: {"wall_s": 0.0, "process_cpu_s": 0.0, "count": 0, "errors": 0} for name in STAGES}
    for name in ("engine_installation", "activation"):
        jobs[name].update(wall_s=None, process_cpu_s=None,
                          reason="pending: requires an unoccupied authorized BeamNG test session")
    return jobs


def measured(jobs, stage, fn, *args, **kwargs):
    wall, cpu = time.perf_counter(), time.process_time()
    row = jobs[stage]
    row["count"] += 1
    try:
        return fn(*args, **kwargs)
    except BaseException:
        row["errors"] += 1
        raise
    finally:
        row["wall_s"] += time.perf_counter() - wall
        row["process_cpu_s"] += time.process_time() - cpu


def files_digest(directory):
    rows = []
    for path in sorted(Path(directory).glob("*")):
        if path.is_file():
            data = path.read_bytes()
            rows.append({"path": path.name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    digest = hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"files": rows, "bytes": sum(row["bytes"] for row in rows), "sha256": digest}


def run(cache_dir, output_dir, mode, processing_sha256, jobs, profile=None):
    import sys
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "server" / "tests"))
    import blockworker
    import geodesy
    import grading
    import heightmap
    import mapdata
    import mapfixtures as mf
    import plancache
    import roadmesh
    import roads

    cache_dir, output_dir = Path(cache_dir), Path(output_dir)
    manifest_path = cache_dir / "baseline-manifest.json"
    expected = None
    if mode == "cold":
        if cache_dir.exists() and any(cache_dir.iterdir()):
            raise ValueError("cold mode requires an absent or empty dedicated cache; nothing was deleted")
    else:
        if not manifest_path.is_file():
            raise ValueError("warm_restart requires a successful cold baseline-manifest.json")
        expected = json.loads(manifest_path.read_text(encoding="utf-8"))

    cache_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    products = output_dir / "products"
    if products.exists() and any(products.iterdir()):
        raise ValueError("output products directory must be empty; use a new output directory")
    products.mkdir(exist_ok=True)
    fixture_dir = output_dir / "fixture-source"
    fixture_dir.mkdir(exist_ok=True)
    setup_start = time.perf_counter()
    files, _ = mf.release(str(fixture_dir), extra_far_file=False)
    fixture_hash = hashlib.sha256()
    for url, body in sorted(files.items()):
        fixture_hash.update(url.encode())
        fixture_hash.update(hashlib.sha256(body).digest())
    input_info = {"fixture_revision": REVISION, "fixture_sha256": fixture_hash.hexdigest(),
                  "source_release": mf.RELEASE, "cell": list(mf.CELL), "synthetic": True,
                  "network": "none: in-memory fake HTTP Range transport",
                  "fixture_setup_wall_s": time.perf_counter() - setup_start}
    if expected and (expected["fixture_sha256"] != input_info["fixture_sha256"] or
                     expected["processing_sha256"] != processing_sha256):
        raise ValueError("warm_restart fixture or processing revision differs from its cold run")

    class TimedTransport(mf.Transport):
        def __init__(self, files):
            super().__init__(files)
            self.timing_lock = threading.Lock()

        def request(self, *args, **kwargs):
            # These fake-source timings overlap MapData.cell's inclusive timing.
            # Keep updates safe if source reading uses more than one thread.
            start, cpu = time.perf_counter(), time.process_time()
            error = 1
            try:
                result = super().request(*args, **kwargs)
                error = 0
                return result
            finally:
                wall_s, cpu_s = time.perf_counter() - start, time.process_time() - cpu
                with self.timing_lock:
                    row = jobs["source_fetch"]
                    row["count"] += 1
                    row["errors"] += error
                    row["wall_s"] += wall_s
                    row["process_cpu_s"] += cpu_s

    transport = TimedTransport(files)
    fetcher = mf.fetcher(transport, offline=(mode == "warm_restart"))
    md = mapdata.MapData(str(cache_dir), fetcher, mf.RELEASE, threads=1)
    blockworker.timing_snapshot(reset=True)
    plancache.start_run(str(cache_dir))
    plan_run = os.environ.get(plancache.RUN_ENV)
    if profile:
        profile.enable()
    try:
        submitted = time.perf_counter()
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="baseline-queue") as executor:
            started = executor.submit(time.perf_counter).result()
        jobs["queue_wait"].update(wall_s=max(0.0, started - submitted), process_cpu_s=None, count=1,
                                  note="uncontended diagnostic executor; not production queue age")
        cd = measured(jobs, "normalization", md.cell, mf.CELL, ("roads",))
        jobs["normalization"]["note"] = "inclusive MapData.cell: fetch/decode/normalize/cache IO; overlaps source_fetch"
        jobs["source_fetch"]["note"] = "fake transport callbacks only; overlaps normalization; not network latency"
        input_info["source_versions"] = cd.versions
        normalized_digest = cd.digest()
        if expected and normalized_digest != expected["normalized_digest"]:
            raise ValueError("normalized content changed across restart")
        if mode == "warm_restart" and not all(row["hit"] for row in cd.stats.values()):
            raise ValueError("warm_restart did not load the persisted normalized layer")

        # Anchor on the primary fixture road. All planning/compilation below
        # consumes the normalized source above, not independently invented roads.
        f = cd.layers["roads"]
        primary = f.parts(f.index("seg-01"))[0][0]
        lon, lat = primary.mean(axis=0)
        frame = geodesy.EnuFrame(float(lat), float(lon))

        def raw(la, lo):
            east, north, _ = frame.geodetic_to_enu(np.asarray(la), np.asarray(lo), 0.0)
            return 25.0 + 0.015 * east + 0.03 * north + 0.15 * np.sin(east / 13.0)

        grader = grading.Grader(frame, md, raw)
        grader.shared = plancache.for_grader(str(cache_dir), grader, None, None)
        measured(jobs, "road_planning", grader._cell_plans, mf.CELL)

        def elevated(la, lo):
            return grader.apply(la, lo, raw(la, lo), grading.ROAD_LIFT)

        chunks = measured(jobs, "road_planning", roads.build_tile, frame, 0, 0, [f], elevated)
        if not chunks:
            raise ValueError("synthetic road tile unexpectedly empty")
        terrain_preparation = None
        if os.environ.get("BNGE_BENCH_TERRAINPREP") == "1":
            import terrainprep
            import worldplan
            def prepare():
                return terrainprep.prepare(frame, 0.0, 0.0, 128, 1 / 64, str(products),
                    "terrain.png", None, sample=elevated, cache_dir=str(cache_dir),
                    plan_context=lambda keys: worldplan.capture_context(grader, md, keys),
                    settings={"fixture": REVISION})
            terrain_preparation = measured(jobs, "terrain_planning", prepare)
            heights = None
            jobs["terrain_planning"]["note"] = "shared preparation includes PNG encoding/install; compare total pipeline, not old exclusive stage"
        else:
            heights = measured(jobs, "terrain_planning", heightmap.grid_u, frame, elevated, 0.0, 0.0, 128)
        mesh = measured(jobs, "compilation", roadmesh.build, frame, chunks, elevated, (0.0, 0.0, 0.0),
                        clip=roads.tile_bbox(0, 0), follow=True, q=1 / 64)
        if mesh is None:
            raise ValueError("synthetic road mesh unexpectedly empty")
        if terrain_preparation is None:
            measured(jobs, "serialization", heightmap.write_png, str(products / "terrain.png"), heights, 1 / 64)
        measured(jobs, "serialization", roads.write_tile, str(products), "/temp/baseline", 1, 0, 0,
                 chunks, road_mesh=mesh)
        outputs = files_digest(products)
        if expected and outputs["sha256"] != expected["outputs_sha256"]:
            raise ValueError("compiled output digest changed across restart")
        result = {"input": input_info,
                  "cache": {"mode": mode, "path": str(cache_dir), "plan_run": plan_run,
                            "layer_stats": cd.stats, "transport_requests": transport.count(),
                            "fetch_counters": fetcher.snapshot(), "normalized_digest": normalized_digest,
                            "plancache": dict(grader.shared.stats) if grader.shared else None,
                            "baseline_digest_match": True if expected else None,
                            "plan_persistence": "new run namespace; plans intentionally rebuilt across restart"},
                  "outputs": outputs, "worker_timings": {
                      **blockworker.timing_snapshot(),
                      "reason": "direct stage calls bypass worker jobs; measure _accounted overhead separately"}}
        if terrain_preparation is not None:
            result["terrain_preparation"] = terrain_preparation
        if mode == "cold":
            manifest = {"fixture_sha256": input_info["fixture_sha256"], "processing_sha256": processing_sha256,
                        "normalized_digest": normalized_digest, "outputs_sha256": outputs["sha256"],
                        "pid": os.getpid(), "plan_run": plan_run}
            temporary = manifest_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            os.replace(temporary, manifest_path)
        else:
            result["cache"]["cold_pid"] = expected["pid"]
            result["cache"]["distinct_process"] = expected["pid"] != os.getpid()
            if not result["cache"]["distinct_process"]:
                raise ValueError("warm_restart must run in a separate process from cold")
        return result
    finally:
        if profile:
            profile.disable()
        md.close()
        fetcher.close()
