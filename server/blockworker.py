"""Physics-block builds in a process of their own (2026-10-12).

The server is one Python process: its threads share one interpreter lock, so while three far-tile workers
built, a physics block took 3-10x longer than alone (land cover 1.4 s instead of 0.1 s), too long to follow a
car at 58 m/s, where the 512² block moves every 2.2 s. This process builds the blocks the follow and the
prefetch ask for on a core of its own. It keeps its own DEM reader, road plans (grading.Grader per anchor),
street widths, height chunks and land-cover reader over the same disk caches. All of them are deterministic,
so a block built here is the one the server would build (the far tiles, graded in the server process, agree
with it).

init(...) runs once in the process (ProcessPoolExecutor initializer); build(...), base_entry(...), base_photo(...) and
far_geometry(...) (the far-tile workers, the same state) are its jobs. warm_q (a multiprocessing queue) carries the road cells to plan ahead of the player: (anchor id, lat,
lon, [mapcells.CellKey]), the latest list replacing the earlier one.
"""
import logging
import contextlib
import copy
from contextvars import ContextVar
import os
import queue
import threading
import time

import numpy as np

import dem as demmod
import farfield
import geodesy
import grading as gradingmod
import heightmap
import mapdata as mapdatamod
import netfetch
import plancache
import roadwidth as roadwidthmod
import seacarve as seacarvemod
import surface as surfacemod
import terraincache
import xproc

log = logging.getLogger("earthd.blockworker")

_s = None       # this process's state (init)
_bound_state = ContextVar("earth_blockworker_generation", default=None)


def _state():
    """One immutable decision-cache bundle for this job, or initializer state."""
    bound = _bound_state.get()
    return bound if bound is not None else _s


def _generation_settings(state):
    import worldplan
    return worldplan.geometry_settings({"grading": state.grading, "road_widths": state.widths is not None,
        "surface": state.surface is not None, "airports": state.airports is not None,
        "sea": state.sea is not None})


@contextlib.contextmanager
def _generation_scope(aid, lat, lon):
    """Bind once; nested helpers cannot switch generation halfway through a job."""
    import worldplan
    base = _s
    if _bound_state.get() is not None or not worldplan.generation_enabled() or not isinstance(base, _State):
        yield _state()
        return
    with base.lock:
        if getattr(base, "_world_generations", None) is None:
            base._world_generations = worldplan.GenerationStore(limit=ANCHORS_KEPT)
        store = base._world_generations
    receipt = worldplan.runtime_receipt(base.dem, base.md, _generation_settings(base))

    def fresh(serial):
        snapshot = copy.copy(base)
        snapshot.graders, snapshot.chunks, snapshot.bridges, snapshot.tunnels = {}, {}, {}, {}
        snapshot.lock = threading.Lock()
        snapshot.uncertain_worldplan = True
        snapshot.worldplan_generation = serial
        snapshot.worldplan_receipt = receipt
        return snapshot

    snapshot = store.acquire((aid, float(lat), float(lon)), receipt, fresh)
    token = _bound_state.set(snapshot)
    try:
        yield snapshot
    finally:
        _bound_state.reset(token)


def _generation_job(fn):
    import functools
    @functools.wraps(fn)
    def run(aid, lat, lon, *args, **kwargs):
        with _generation_scope(aid, lat, lon) as state:
            starting_epoch = int(getattr(getattr(state, "dem", None), "invalidation_epoch", 0))
            result = fn(aid, lat, lon, *args, **kwargs)
            if fn.__name__ == "build" and isinstance(result, dict) and state is not None:
                result["_worker_epoch_changed"] = int(getattr(getattr(state, "dem", None), "invalidation_epoch", 0)) != starting_epoch
            if fn.__name__ == "build" and isinstance(result, dict) and hasattr(state, "worldplan_generation"):
                result["_worker_generation"] = state.worldplan_generation
                result["_worker_epoch_changed"] |= (
                    int(getattr(state.dem, "invalidation_epoch", 0)) != state.worldplan_receipt["dem_epoch"])
            return result
    return run


class _State:
    def __init__(self, cache_dir, offline, roads, grading, road_widths, surface, airports=False, sea=False):
        self.cache_dir = cache_dir
        self.imagery = None         # (its settings, imagery.Imagery from the disk alone): far_tile's textures
        net = netfetch.Fetcher(offline=offline)
        self.dem = demmod.Dem(os.path.join(cache_dir, "dem"), fetcher=net)
        # runways graded in (--airports on): the copy the server loaded, never refreshed here
        self.airports = load_airports(cache_dir, self.dem) if airports else None
        md = mapdatamod.MapData(cache_dir, fetcher=net) if (roads or surface or sea) else None
        # the sea's and the lakes' floors carved in (--sea on)
        self.sea = seacarvemod.SeaCarver(md, cache_dir, dem=self.dem) if sea else None
        self.md = md if roads else None
        self.grading = bool(grading) and self.md is not None
        self.widths = (roadwidthmod.RoadWidths(
            lambda key: md.cell(key, layers=("buildings",), kind="demand").layers.get("buildings"),
            roadwidthmod.store_dir(cache_dir, md)).get
            if road_widths and self.md is not None else None)
        self.surface = surfacemod.Surface(md) if surface else None
        self.graders = {}           # anchor id -> grading.Grader
        self.chunks = {}            # anchor id -> heightmap.ChunkCache
        self.bridges = {}           # anchor id -> roads.BridgeProfiles (roads_tile)
        self.tunnels = {}           # anchor id -> roads.TunnelProfiles (roads_tile, the block's holes)
        self.lock = threading.Lock()
        self.want = []              # (grader, cell key) to plan, most urgent first
        self.cv = threading.Condition()

    def anchor(self, aid, lat, lon):
        """(Grader or None, ChunkCache) of an anchor; only the last few anchors are kept."""
        with self.lock:
            if aid not in self.chunks:
                frame = geodesy.EnuFrame(lat, lon)
                self.chunks[aid] = heightmap.ChunkCache()
                self.graders[aid] = (gradingmod.Grader(
                    frame, self.md, lambda la, lo: self.dem.sample(la, lo, gradingmod.PROFILE_ZOOM), self.widths)
                    if self.grading else None)
                if self.graders[aid] is not None:
                    self.graders[aid].wet = self.sea.depth if getattr(self, "sea", None) is not None else None
                    self.graders[aid].dem = self.dem          # (its urgent lane: _plan_missing)
                    self.graders[aid].shared = (None if getattr(self, "uncertain_worldplan", False) else
                        plancache.for_grader(self.cache_dir, self.graders[aid], self.widths, self.graders[aid].wet))
                for old in sorted(self.chunks)[:-ANCHORS_KEPT]:
                    del self.chunks[old], self.graders[old]
            return self.graders[aid], self.chunks[aid]


ANCHORS_KEPT = 2
WATER_BED_M = 0.05           # ground carved deeper than this under the water gets the bed layer (MUD)


def load_airports(cache_dir, dem):
    """An airportgrade.AirportGrader over the OurAirports copy in the cache (no download), or None."""
    import airportgrade
    import airports as airportsmod
    try:
        db = airportsmod.Store(cache_dir, netfetch.Fetcher(offline=True)).load(
            max_age=float("inf"))
    except (airportsmod.AirportsUnavailable, OSError, ValueError) as e:
        log.warning("airports: no runway grading (%s)", e)
        return None
    return airportgrade.AirportGrader(db, dem)


def _rss_mb():
    try:
        with open("/proc/self/statm") as f:
            return int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 1e6
    except (OSError, ValueError):
        return 0.0


def _trace_memory(every_s=60.0, top=10):
    """BNGE_TRACEMALLOC: log this worker's resident size, what Python and numpy hold of it and its top allocation
    sites every every_s (the rest is native: DuckDB, the allocator's free lists)."""
    import tracemalloc
    tracemalloc.start(1)

    def loop():
        log = logging.getLogger("earthd.worker")
        while True:
            time.sleep(every_s)
            snap = tracemalloc.take_snapshot()
            total = sum(st.size for st in snap.statistics("filename"))
            log.info("memory pid %d: rss %.0f MB, traced %.0f MB; top: %s", os.getpid(), _rss_mb(), total / 1e6,
                     "; ".join(f"{os.path.basename(st.traceback[0].filename)}:{st.traceback[0].lineno} "
                               f"{st.size / 1e6:.0f} MB" for st in snap.statistics("lineno")[:top]))
    threading.Thread(target=loop, daemon=True, name="trace-mem").start()


def init(cache_dir, offline, roads, grading, road_widths, surface, warm_q, log_level=logging.INFO, airports=False,
         sea=False, nice=0, roads_in_ground=None):
    """nice: this worker's niceness on top of the server's (earthd NICE_*): the terrain blocks' workers keep the
    server's own, the road tiles' and the far field's run below them, so the ground under the car wins the server's
    few cores (2026-10-19: at 180 mph the next block waited 6 s behind the road tiles ahead and a ball fell off the
    block's edge)."""
    global _s
    # A helper may serve clients with different choices. Never use its own launcher
    # preference for a client's job, or mix painted and mesh roads within one world.
    if roads_in_ground is not None:
        import roads as roadsmod
        roadsmod.ROADS_IN_GROUND = roads_in_ground
    import budget
    try:
        budget.lower_priority(nice, worker=True)    # (Windows: the server's class, as inherited)
    except (OSError, AttributeError):
        pass
    logging.basicConfig(level=log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    budget.apply()              # (this process's caches: smaller on a small machine)
    if os.environ.get("BNGE_TRACEMALLOC"):      # debug: this worker's memory (earthd's trace_mem)
        _trace_memory()
    import sampler
    sampler.maybe_start(f".{os.getpid()}")      # BNGE_SAMPLE: this worker's own sampling profile (debugging)
    xproc.PRIORITY = nice
    _s = _State(cache_dir, offline, roads, grading, road_widths, surface, airports, sea)
    threading.Thread(target=_watch_parent, args=(os.getppid(),), daemon=True, name="parent-watch").start()
    threading.Thread(target=_read_warm, args=(warm_q,), daemon=True, name="warm-read").start()
    threading.Thread(target=_warm_loop, daemon=True, name="roads-ahead").start()


def _watch_parent(ppid):
    """Exit with the server: a killed server (SIGTERM) left its block processes running."""
    while os.getppid() == ppid:
        time.sleep(PARENT_POLL_S)
    os._exit(0)


PARENT_POLL_S = 2.0


def _read_warm(warm_q):
    while True:
        try:
            aid, lat, lon, keys = warm_q.get()
        except (EOFError, OSError):
            return
        try:
            while True:         # only the latest list matters
                aid, lat, lon, keys = warm_q.get_nowait()
        except queue.Empty:
            pass
        with _generation_scope(aid, lat, lon):
            g, _ = _state().anchor(aid, lat, lon)
        if g is None:
            continue
        with _state().cv:
            _state().want = [(g, k) for k in keys]
            _state().cv.notify()


def _warm_loop():
    while True:
        with _state().cv:
            job = None
            while job is None:
                job = next(((g, k) for g, k in _state().want if not g.planned(k)), None)
                if job is None:
                    _state().cv.wait()
        try:
            t0 = time.perf_counter()
            job[0].warm(job[1])
            log.debug("road plans ahead: %s in %.2f s", job[1], time.perf_counter() - t0)
        except Exception:
            log.exception("road plans ahead for %s", job[1])
            with _state().cv:
                _state().want = [w for w in _state().want if w[1] != job[1]]


def ground(frame, la, lo, z):
    """The DEM heights z at (la, lo) shaped before the road grading: runways graded in (--airports on),
    the sea floor carved in (--sea on)."""
    if _state().airports is not None:
        z = _state().airports.apply(la, lo, z)
    if _state().sea is not None:
        z = _state().sea.carve(la, lo, z)
    return z


def build(aid, lat, lon, hm_zoom, q, cx, cy, size, out_dir, name, ter_name=None):
    import terrainprep
    if not terrainprep.shared_enabled():
        return _build_legacy(aid, lat, lon, hm_zoom, q, cx, cy, size, out_dir, name, ter_name)
    t0 = time.perf_counter()
    g, chunks = _state().anchor(aid, lat, lon)
    frame = geodesy.EnuFrame(lat, lon)
    arch = None
    if ter_name is not None and getattr(_state(), "md", None) is not None:
        def arch(elev):
            import roads as roadsmod
            tunnels = tunnel_profiles(aid, frame, elev, g)
            half = size * heightmap.SQUARE / 2
            cell_fn = lambda key: _state().md.cell(key, layers=("roads",), kind="demand").layers.get("roads")
            return lambda la, lo: roadsmod.block_arch_raise(tunnels, cell_fn, frame, la, lo, elev(la, lo),
                                                            (cx - half, cy - half, cx + half, cy + half))
    import worldplan
    return terrainprep.prepare(frame, cx, cy, size, q, out_dir, name, ter_name,
        sample=lambda la, lo: _state().dem.sample(la, lo, hm_zoom),
        ground=lambda la, lo, z: ground(frame, la, lo, z), grader=g,
        surface=_state().surface, widths=_state().widths, sea=getattr(_state(), "sea", None), arch=arch,
        holes=lambda elev, keep, idx, info: _tunnel_holes(aid, frame, elev, keep, cx, cy, idx, info, g),
        open_tunnels=lambda keep: _open_tunnels(g, keep), chunks=chunks, t0=t0,
        cache_dir=getattr(_state(), "cache_dir", None),
        plan_context=lambda keys: worldplan.capture_context(g, getattr(_state(), "md", None), keys),
        settings={"dem_zoom": hm_zoom})


def _build_legacy(aid, lat, lon, hm_zoom, q, cx, cy, size, out_dir, name, ter_name=None):
    """Build and write the block (heightmap PNG name in out_dir, its surface masks beside it, and the
    .ter ter_name if given). Returns the info dict of earthd's make_heightmap (without seconds / zoom /
    base)."""
    t0 = time.perf_counter()
    g, chunks = _state().anchor(aid, lat, lon)
    frame = geodesy.EnuFrame(lat, lon)
    keep = {}
    grade_s = [0.0]

    def elev(la, lo):
        z = ground(frame, la, lo, _state().dem.sample(la, lo, hm_zoom))
        if g is None:
            return z
        t1 = time.perf_counter()
        z = g.apply(la, lo, z, gradingmod.ROAD_LIFT)
        grade_s[0] += time.perf_counter() - t1
        return z
    import roads as roadsmod
    raw = lambda la, lo: ground(frame, la, lo, _state().dem.sample(la, lo, hm_zoom))     # noqa: E731
    gen = elev
    if ter_name is not None and getattr(_state(), "md", None) is not None:
        tun = tunnel_profiles(aid, frame, elev, g)
        half = size * heightmap.SQUARE / 2
        cell_fn = lambda key: _state().md.cell(key, layers=("roads",), kind="demand").layers.get("roads")   # noqa: E731

        def gen(la, lo):        # the ground over the low arches raised over their vaults (roads.arch_raise)
            return roadsmod.block_arch_raise(tun, cell_fn, frame, la, lo, elev(la, lo),
                                             (cx - half, cy - half, cx + half, cy + half))
    info = heightmap.generate(frame, gen, os.path.join(out_dir, name), cx, cy, q=q, size=size, keep=keep,
                              chunks=chunks)
    stages = {"heights": time.perf_counter() - t0, "grading": grade_s[0]}
    idx = None
    if _state().surface is not None:
        t1 = time.perf_counter()
        classes, complete = _state().surface.classes(keep["lat"], keep["lon"])
        idx = surfacemod.classify(classes, keep["u"], heightmap.SQUARE, keep["lat"], keep["lon"])
        if _state().sea is not None:          # under water: the bed (no grass growing through the surface)
            idx[_state().sea.depth(keep["lat"], keep["lon"]) > WATER_BED_M] = surfacemod.MUD
        road_feats, roads_complete = _state().surface.roads(keep["lat"], keep["lon"])
        stages["landcover"] = time.perf_counter() - t1
        t1 = time.perf_counter()
        if road_feats:
            idx = surfacemod.paint_roads(idx, keep["lat"], keep["lon"], road_feats, heightmap.SQUARE,
                                         _state().surface.urban, _state().widths, _open_tunnels(g, keep))
        idx = (surfacemod.paint_lots(idx, keep["lat"], keep["lon"], _state().surface.lots(keep["lat"], keep["lon"]))
            if hasattr(_state().surface, "lots") else idx)
        stages["roadPaint"] = time.perf_counter() - t1
        info["layers"] = surfacemod.write_masks(idx[::-1], out_dir, name[:-4])
        info["surfaceComplete"] = complete and roads_complete
    if ter_name is not None:
        t1 = time.perf_counter()
        idx = _tunnel_holes(aid, frame, elev, keep, cx, cy, idx, info, g)
        info["ter"], mats = surfacemod.write_ter(out_dir, ter_name, keep["qgrid"], idx)
        if mats is not None:          # the .ter's own material list (MATERIALS indices)
            info["mats"] = mats
        stages["ter"] = time.perf_counter() - t1
    info["stages"] = stages
    return info


def _bridges(aid, frame, elev):
    """The anchor's roads.BridgeProfiles (deck heights of whole bridge chains), made once per anchor in this process."""
    import roads as roadsmod
    with _state().lock:
        bridges = _state().bridges.get(aid)
        if bridges is None:
            bridges = _state().bridges[aid] = roadsmod.BridgeProfiles(
                frame, lambda key: _state().md.cell(key, layers=("roads",), kind="demand").layers.get("roads"), elev)
            sea = getattr(_state(), "sea", None)
            bridges.wet = sea.depth if sea is not None else None        # (decks clear the water: roads.water_clear)
            bridges.infra_fn = lambda key: _state().md.cell(key, layers=("infrastructure",), kind="demand").layers.get(
                "infrastructure")                                       # (bridge:structure tags: bridgetypes)
            for old in sorted(_state().bridges)[:-ANCHORS_KEPT]:
                del _state().bridges[old]
    return bridges


def navgraph(aid, lat, lon, zoom, x, y, out_dir, vfs_dir, serial, left_hand, mph, rules):
    """earthd.Server.build_navgraph here (a road-tile process): the AI road graph around (x, y) written to out_dir;
    returns (vfs path, nodes, edges). The bridges' deck heights are this process's (the road tiles' own); in the
    server they were built a second time, in its interpreter (13 s of its CPU in a 4-minute drive, 2026-10-20)."""
    import navgraph as navgraphmod
    import worldplan
    import mapcells
    g, _ = _state().anchor(aid, lat, lon)
    frame = geodesy.EnuFrame(lat, lon)
    w = navgraphmod.WINDOW_M + 200.0
    la, lo, _ = frame.enu_to_geodetic(np.array([x - w, x + w]), np.array([y - w, y + w]), 0.0)
    keys = mapcells.cells_for_bbox(float(la.min()), float(lo.min()), float(la.max()), float(lo.max()))
    feats = [_state().md.cell(k, layers=("roads",), kind="demand").layers.get("roads") for k in keys]

    def elev(la_, lo_):         # the physics ground, lifted under roads (as the road tiles')
        z = ground(frame, la_, lo_, _state().dem.sample(la_, lo_, zoom))
        return g.apply(la_, lo_, z, gradingmod.ROAD_LIFT) if g is not None else z
    decks = {}
    try:
        for fid, _spec, xy, z, _lon in _bridges(aid, frame, elev).parts(feats, (x - w, y - w, x + w, y + w)):
            decks.setdefault(fid, []).append((np.asarray(xy, np.float64)[:, :2], np.asarray(z, np.float64)))
    except Exception as e:          # no decks: the graph follows the ground under bridges
        log.debug("navgraph decks: %s: %s", type(e).__name__, e)
    urban = _state().surface.urban if _state().surface is not None else None
    graph = navgraphmod.build(frame, feats, elev, (x, y), urban, decks=decks, widths=_state().widths, left_hand=left_hand,
                              mph=mph, require_bridge_support=True)
    # Local process epochs do not prove the parent's collision snapshot is identical.
    graph["bridgeSupportKnown"] = False
    graph["rules"] = rules
    path = navgraphmod.write(out_dir, vfs_dir, aid, serial, graph)
    return path, len(graph["nodes"]), len(graph["edges"])


def terrain_patch(aid, lat, lon, zoom, box, path):
    """earthd.Server.build_terrain_patch here (road-tile pool): the collision patch of the ground over box under a
    collision island off the terrain block (terrainpatch.py), the block's own ground (graded, lifted under roads)."""
    import terrainpatch
    g, _ = _state().anchor(aid, lat, lon)
    frame = geodesy.EnuFrame(lat, lon)

    def elev(la, lo):
        z = ground(frame, la, lo, _state().dem.sample(la, lo, zoom))
        return g.apply(la, lo, z, gradingmod.ROAD_LIFT) if g is not None else z
    return terrainpatch.build(frame, elev, box, path)


def roads_tile(aid, lat, lon, zoom, tx, ty, out_dir, vfs_dir, sidewalks, lift, buildings=False, left_hand=False,
               mph=True, q=None):
    """earthd.Server.build_roads_tile here (road-tile pool): the anchor's road tile (tx, ty) built and
    written; returns roads.write_tile's (vfs path, chunks). In the server's process its Python made up
    ~80 % of the main process's busy threads (in-game profile 2026-10-13): the block photos and far
    textures waited for the GIL."""
    import parking as parkingmod
    import roadmesh as roadmeshmod
    import roads as roadsmod
    g, _ = _state().anchor(aid, lat, lon)
    frame = geodesy.EnuFrame(lat, lon)
    cells = [_state().md.cell(k, layers=("roads",), kind="demand").layers.get("roads")
             for k in roadsmod.cells_of_tile(frame, tx, ty)]

    def elev(la, lo):           # the physics ground (lifted under roads: grading.ROAD_LIFT)
        z = ground(frame, la, lo, _state().dem.sample(la, lo, zoom))
        return g.apply(la, lo, z, gradingmod.ROAD_LIFT) if g is not None else z
    bridges = _bridges(aid, frame, elev)
    tunnels = tunnel_profiles(aid, frame, elev, g)
    walks = []
    urban = _state().surface.urban if _state().surface is not None else None
    props = [] if buildings else None
    lines = [] if buildings else None
    wires = [] if buildings else None
    park = [] if buildings else None
    items = town = None
    if buildings:
        import buildings as buildingsmod
        items = buildingsmod.road_tile_items(frame, tx, ty, _state().md,
                                             lambda la, lo: ground(frame, la, lo, _state().dem.sample(la, lo, zoom)),
                                             decks=_tile_decks(bridges, cells, tx, ty))
        town = buildingsmod.coverage(items) < buildingsmod.POLES_COVERAGE
    rwalls = []
    ctx = []
    box = roadsmod.tile_bbox(tx, ty)
    cell_fn = lambda key: _state().md.cell(key, layers=("roads",), kind="demand").layers.get("roads")   # noqa: E731

    def drawn(la, lo):          # the block's ground: raised over the low arches as the terrain is (_make_heightmap)
        return roadsmod.block_arch_raise(tunnels, cell_fn, frame, la, lo, elev(la, lo),
                                         (box[0] - 8, box[1] - 8, box[2] + 8, box[3] + 8))
    chunks = roadsmod.build_tile(frame, tx, ty, cells, elev, urban, walks=walks, deck_context=ctx, bridges=bridges, widths=_state().widths,
                                 props=props, lines=lines, wires=wires, town=town, park=park,
                                 left_hand=left_hand, mph=mph, structures=buildingsmod.centres(items, tx, ty) if items else (),
                                 tunnels=tunnels, walls=rwalls,
                                 ground=lambda x, y: roadmeshmod.drawn_ground(frame, drawn, np.c_[x, y], q),
                                 terrain_paint=getattr(_state(), "surface", None) is not None)
    if _state().airports is not None:         # runway markings, drawn by the road mesh like the lane lines
        chunks += _state().airports.tile_chunks(frame, tx, ty, elev, roadsmod.tile_bbox)
    x0, y0, _, _ = roadsmod.tile_bbox(tx, ty)
    if items is not None:               # front doors and shopfronts, driveways into the road mesh
        drives = []
        props.extend(buildingsmod.doors(items, lines, x0, y0, drives))
        props.extend(buildingsmod.toll_booth_rows(items, x0, y0))
        drives.extend(buildingsmod.garage_drives(items, lines, x0, y0))
        chunks += drives
        park = parkingmod.clear_of(park, props, drives)
        park = parkingmod.tile_lot_spots(frame, tx, ty, _state().md, elev, items, park)
        buildingsmod.brick_walks(walks, items, lat, lon)
    mesh = roadmeshmod.build(frame, chunks, elev, (x0, y0, 0.0), lift, clip=box, follow=True, q=q, drawn=drawn)
    mesh = _with_plazas(mesh, frame, tx, ty, elev, (x0, y0, 0.0), getattr(_state(), "surface", None))
    dmesh = roadmeshmod.deck(chunks, (x0, y0, 0.0), bridges.piers_in(roadsmod.tile_bbox(tx, ty)), ctx,
                              bridges.beams_in(roadsmod.tile_bbox(tx, ty)))
    tmesh = roadmeshmod.tunnel(chunks, (x0, y0, 0.0), rwalls)
    bld = None
    if buildings:
        import buildings as buildingsmod
        bld = buildingsmod.road_tile(frame, tx, ty, _state().md,
                                     lambda la, lo: ground(frame, la, lo, _state().dem.sample(la, lo, zoom)), None, None,
                                     items)
    return roadsmod.write_tile(out_dir, vfs_dir, aid, tx, ty, chunks, walks if sidewalks else None, mesh, dmesh,
                               bld, props, wires, park, tunnel_mesh=tmesh)


def _with_plazas(mesh, frame, tx, ty, elev, origin, surface):
    """The road mesh with the tile's squares and pedestrian areas paved (plazas.py), from the cached land use (none
    when it is not cached yet: surface.Surface.lots never waits)."""
    if surface is None or not hasattr(surface, "lots"):
        return mesh
    import plazas
    import roads as roadsmod
    try:
        box = roadsmod.tile_bbox(tx, ty)
        la, lo, _ = frame.enu_to_geodetic(np.array([box[0], box[2]]), np.array([box[1], box[3]]), np.zeros(2))
        rings = plazas.polygons(surface.lots(la, lo))
        if not rings:
            return mesh
        cover = None
        if mesh is not None and len(mesh[1]):
            P, T = np.asarray(mesh[0], np.float64), np.asarray(mesh[1], np.int64)
            cover = (P[T][:, :, :2] + np.asarray(origin, np.float64)[:2]).reshape(-1, 6)
        return plazas.merge(mesh, plazas.mesh(frame, box, rings, elev, origin, cover))
    except Exception:                   # no plazas rather than no road tile
        log.exception("plazas of tile %s_%s", tx, ty)
        return mesh


def _tile_decks(bridges, cells, tx, ty):
    """[(xy (k, 2), z (k,))] of the bridge decks over road tile (tx, ty) (the buildings they run through get a way:
    buildings.road_tile_items), [] without bridge profiles."""
    if bridges is None:
        return []
    import roads as roadsmod
    x0, y0, x1, y1 = roadsmod.tile_bbox(tx, ty)
    try:
        return [(np.asarray(xy, np.float64)[:, :2], np.asarray(z, np.float64))
                for _, _, xy, z, _ in bridges.parts(cells, (x0 - 20, y0 - 20, x1 + 20, y1 + 20))]
    except Exception as e:      # no ways cut rather than no buildings
        log.debug("tile decks: %s: %s", type(e).__name__, e)
        return []


def _open_tunnels(g, keep):
    """The tunnels' open sections over a block (grading.Grader.open_tunnels; none without a grader)."""
    if g is None:
        return []
    try:
        return g.open_tunnels(keep["lat"], keep["lon"])
    except Exception:
        log.exception("open tunnel sections")
        return []


def _tunnel_holes(aid, frame, elev, keep, cx, cy, idx, info, g=None):
    """idx with the block's tunnel portals as holes (roads.block_tunnel_holes); info["tunnelHoles"]: how many."""
    import roads as roadsmod
    if getattr(_state(), "md", None) is None:     # no map data (roads off): no tunnels
        return idx
    try:
        holes = roadsmod.block_tunnel_holes(
            tunnel_profiles(aid, frame, elev, g),
            lambda key: _state().md.cell(key, layers=("roads",), kind="demand").layers.get("roads"),
            frame, keep, cx, cy, heightmap.SQUARE)
    except Exception:                   # no holes rather than no block
        log.exception("tunnel holes of block (%s, %s)", cx, cy)
        return idx
    if holes is None or not holes.any():
        return idx
    idx = np.zeros(holes.shape, np.uint8) if idx is None else idx
    idx[holes] = heightmap.TER_HOLE
    info["tunnelHoles"] = int(holes.sum())
    return idx


def tunnel_profiles(aid, frame, elev, g=None):
    """The anchor's roads.TunnelProfiles in this process (road tiles and the block's terrain holes share it): its
    ground read at grading.PROFILE_ZOOM (raw; ends: graded by g), as in every other process."""
    import roads as roadsmod
    with _state().lock:
        if getattr(_state(), "tunnels", None) is None:        # (a state made without it: tests)
            _state().tunnels = {}
        t = _state().tunnels.get(aid)
        if t is None:
            t = _state().tunnels[aid] = roadsmod.TunnelProfiles(
                frame, lambda key: _state().md.cell(key, layers=("roads",), kind="demand").layers.get("roads"), elev)
            sea = getattr(_state(), "sea", None)
            t.wet = sea.depth if sea is not None else None
            t.raw = lambda la, lo: ground(frame, la, lo, _state().dem.sample(la, lo, gradingmod.PROFILE_ZOOM))  # noqa: E731
            if g is not None:
                t.ends = lambda la, lo: g.apply(la, lo, t.raw(la, lo), gradingmod.ROAD_LIFT, roads_only=True)    # noqa: E731
            for old in sorted(_state().tunnels)[:-ANCHORS_KEPT]:
                del _state().tunnels[old]
        return t


def far_buildings_tile(aid, lat, lon, zoom, tx, ty, out_dir, vfs_dir):
    """earthd.Server.build_far_bld_tile here (road-tile pool): buildings.far_tile."""
    import buildings as buildingsmod
    frame = geodesy.EnuFrame(lat, lon)
    return buildingsmod.far_tile(frame, tx, ty, _state().md, lambda la, lo: ground(frame, la, lo, _state().dem.sample(la, lo, zoom)),
                                 out_dir, vfs_dir, aid)


def base_entry(user_dir, base_vfs, ao_vfs, rgb, ao_value):
    """terraincache.base_entry here (its BC7 encode is the base photo's main cost)."""
    return terraincache.base_entry(user_dir, base_vfs, ao_vfs, rgb, ao_value)


def far_geometry(aid, lat, lon, tile, zoom, q, ref_zoom, with_sink, layout):
    """farfield.build_tile here (far-tile workers: the geometry, graded at levels <= 0 like the block, is
    most of a far tile's cost; its texture is rendered in the server, where the imagery corrections live).
    Returns the Built."""
    g, _ = _state().anchor(aid, lat, lon)
    frame = geodesy.EnuFrame(lat, lon)
    base = _state().airports.wrap(_state().dem) if _state().airports is not None else _state().dem
    if _state().sea is not None:
        base = seacarvemod.SeaDem(base, _state().sea if tile.level <= seacarvemod.CARVE_MAX_LEVEL else None)
    src = gradingmod.GradedDem(base, g) if (g is not None and tile.level <= 0) else base
    return farfield.build_tile(frame, src, tile, zoom, q, ref_zoom, with_sink=with_sink,
                               layout=layout)


WORKER_IMAGERY_TILES = 96      # decoded imagery tiles a far-tile worker keeps (imagery.MEM_TILES: 512, the server's)


def _imagery(tex):
    """This process's imagery.Imagery for the server's chain settings (tex: chain, tone, leaf_on, season_mode): the
    imagery tiles on disk alone (strict: a missing tile fails the render), or, for a compute-sharing helper
    (tex["online"], peerwork.py), fetched as needed."""
    import imagery as imagerymod
    # Imagery has its own settings key/lifetime; generation bundles must not
    # duplicate its executor and decoded-tile cache on every DEM upgrade.
    state = _s
    online = bool(tex.get("online"))
    key = (tuple(tex["chain"]), tex["tone"], tex["leaf_on"], tex["season_mode"], online)
    if state.imagery is None or state.imagery[0] != key:
        im = imagerymod.Imagery(os.path.join(state.cache_dir, "imagery"), key[0], fetch_workers=4 if online else 1,
                                mem_tiles=WORKER_IMAGERY_TILES, tone=key[1],
                                fetcher=netfetch.Fetcher(offline=not online), leaf_on=key[2], season_mode=key[3])
        im.strict = not online
        state.imagery = (key, im)
    return state.imagery[1]


def base_photo(lat, lon, x0, y0, size, px, tex, buildings, rock_zoom, path, cache):
    """earthd.Server.write_base here, for a server without its imagery analyses (a small machine): the block's base
    photo from the imagery tiles on disk, its building footprints filled and its cliffs as rock, written to path
    (PNG) and packed into the game's terrain texture cache (cache: terraincache.base_entry's arguments but the rgb).
    Returns (rgb, sources used, lazy tiles still to come), or None when an imagery tile it needs is not on disk (the
    server renders it, fetching what is missing). In the server process it ran 2 s alone but 8-11 s next to the
    rest of the world's threads on a Steam Deck (one interpreter lock), and the next block waited for it."""
    import imagery as imagerymod
    import roads as roadsmod
    import rockface as rockfacemod
    import staging
    from PIL import Image
    import buildings as buildingsmod
    im = _imagery(tex)
    frame = geodesy.EnuFrame(lat, lon)
    pending = set()
    try:
        rgb, used = im.render(frame, x0, y0, size, px, pending)
    except imagerymod.ImageryError:
        return None
    rgb = np.asarray(rgb, np.uint8)
    if buildings and _state().md is not None:
        try:
            mask = buildingsmod.photo_mask(frame, x0, y0, size, px, _state().md)
            if mask is not None:
                rgb = buildingsmod.fill_photo(rgb, mask, size, px)
        except Exception as e:      # the photo stays as it is
            log.info("base photo: building mask: %s", e)
    if roadsmod.ROADS_IN_GROUND:          # (earthd.Server.write_base)
        try:
            import surface as surfacemod
            rgb = surfacemod.paint_square(rgb, frame, x0, y0, size, getattr(_state(), "surface", None), _state().widths)
        except Exception as e:      # the photo stays as it is
            log.info("base photo: roads: %s", e)
    try:            # (earthd.Server.write_base)
        n = 65
        xs = x0 + np.arange(n) * (size / (n - 1))
        ys = y0 + np.arange(n) * (size / (n - 1))
        _, U = heightmap.surface(frame, lambda la, lo: _state().dem.sample(la, lo, rock_zoom), xs, ys)
        rgb = rockfacemod.rock(rgb, frame, x0, y0, size, U, x0, y0, size / (n - 1))
    except Exception as e:
        log.debug("base photo: rock faces: %s", e)
    tmp = staging.tmp_for(path)
    # stored, not deflated: the game reads the packed cache entry (terraincache), this file only if that is stale;
    # level 1 was 0.15 s of a block photo on a Steam Deck, level 0 0.08 (3 MB, a session keeps a few)
    Image.fromarray(rgb, "RGB").save(tmp, format="PNG", compress_level=0)
    staging.replace(tmp, path)
    user_dir, base_vfs, ao_vfs, ao_value = cache
    terraincache.base_entry(user_dir, base_vfs, ao_vfs, rgb, ao_value)
    return rgb, used, pending


def far_tile(aid, lat, lon, tile, zoom, q, ref_zoom, with_sink, layout, tex):
    """far_geometry and the tile's texture here, written to tex["path"] (the server without its imagery analyses:
    de-shadow and colour match off, a small machine). The texture from the imagery tiles on disk alone, the server's
    chain (Imagery.strict): (Built, (texture bytes, sources used)), or (Built, None) when a tile it needs is not on
    disk (the server renders it, fetching what is missing). On a Steam Deck the textures were most of the server
    process's own CPU, one interpreter for every thread (55 s of a drive), and the far field waited on it."""
    import imagery as imagerymod
    import rockface as rockfacemod
    geometry = getattr(far_geometry, "__wrapped__", far_geometry)        # (its CPU counted once, here)
    built = geometry(aid, lat, lon, tile, zoom, q, ref_zoom, with_sink, layout)
    im = _imagery(tex)
    frame = geodesy.EnuFrame(lat, lon)
    x0, y0, _, _ = tile.bbox
    px = tex["px"]
    sq = imagerymod.gutter_square(x0, y0, tile.size, px)
    try:
        rgb, used = im.render(frame, *sq, px)
    except imagerymod.ImageryError:
        return built, None
    if built.grid is not None:          # (earthd.Server.rock_faces)
        try:
            g = np.asarray(built.grid)
            rgb = rockfacemod.rock(rgb, frame, *sq, g, x0, y0, tile.size / (g.shape[1] - 1))
        except Exception as e:
            log.debug("rock faces of %s: %s", tile.key, e)
    import roads as roadsmod
    if roadsmod.ROADS_IN_GROUND:          # (earthd.Server.road_faces)
        try:
            import surface as surfacemod
            rgb = surfacemod.paint_square(rgb, frame, *sq, getattr(_state(), "surface", None), _state().widths)
        except Exception as e:
            log.debug("road faces of %s: %s", tile.key, e)
    if tex.get("mask") and tex["mask"][1]:  # (earthd.Server.write_road_mask)
        try:
            import surface as surfacemod
            mask = surfacemod.road_mask(frame, *sq, tex["mask"][1], getattr(_state(), "surface", None), _state().widths)
            if mask is not None:
                imagerymod.write_dds(tex["mask"][0], np.repeat(mask[..., None], 3, axis=2))
        except Exception as e:
            log.debug("road mask of %s: %s", tile.key, e)
    return built, (imagerymod.write_dds(tex["path"], rgb), used)


# --- CPU accounting ---------------------------------------------------------------------------------------------------
# Each job's CPU seconds by kind, logged by the process every CPU_LOG_S at debug level ("worker <pid> cpu: ..."), so a
# recorded session says which pool (road tiles, far buildings, far geometry, terrain blocks) the server's CPU went to.
CPU_LOG_S = 30.0
_cpu = {}
_cpu_logged = [time.monotonic()]
# Aggregate diagnostics only. No per-call records are retained, and ordinary
# worker jobs keep the existing accounting path unless explicitly opted in.
_TIMINGS_ENABLED = os.environ.get("BNGE_TIMINGS") == "1"
_timings = {}
_timing_lock = threading.Lock()
_TIMING_STAGES = frozenset(("heights", "grading", "gradingCpu", "landcover", "roadPaint", "ter"))


def timing_snapshot(reset=False):
    """Copy bounded job totals for diagnostics (seconds; CPU is process-wide).

    BNGE_TIMINGS=1 must be set before importing blockworker. Existing _cpu
    counters/logging remain independent. reset is intended for isolated probes.
    """
    with _timing_lock:
        result = {name: {**row, "stages": dict(row["stages"])} for name, row in _timings.items()}
        if reset:
            _timings.clear()
    return {"enabled": _TIMINGS_ENABLED, "jobs": result}


def _accounted(fn):
    import functools

    @functools.wraps(fn)
    def run(*a, **kw):
        c0 = time.process_time()
        enabled = _TIMINGS_ENABLED
        w0 = time.perf_counter() if enabled else None
        failed, result = True, None
        try:
            result = fn(*a, **kw)
            failed = False
            return result
        finally:
            cpu_s = time.process_time() - c0
            n, t = _cpu.get(fn.__name__, (0, 0.0))
            _cpu[fn.__name__] = (n + 1, t + cpu_s)
            if enabled:
                wall_s = time.perf_counter() - w0
                with _timing_lock:
                    row = _timings.setdefault(fn.__name__, {"count": 0, "errors": 0, "wall_s": 0.0,
                                                           "process_cpu_s": 0.0, "stages": {}})
                    row["count"] += 1
                    row["errors"] += int(failed)
                    row["wall_s"] += wall_s
                    row["process_cpu_s"] += cpu_s
                    stages = result.get("stages") if isinstance(result, dict) else None
                    if isinstance(stages, dict):
                        for key, value in stages.items():
                            if key in _TIMING_STAGES and isinstance(value, (int, float)):
                                row["stages"][key] = row["stages"].get(key, 0.0) + value
            now = time.monotonic()
            if now - _cpu_logged[0] >= CPU_LOG_S:
                _cpu_logged[0] = now
                shared = [g.shared.stats for g in list(getattr(_state(), "graders", {}).values())
                          if g is not None and g.shared is not None]
                log.debug("worker %d cpu: %s%s", os.getpid(),
                          ", ".join(f"{k} {v[0]} jobs {v[1]:.1f} s" for k, v in sorted(_cpu.items())),
                          "; shared road plans " + ", ".join(f"{k} {sum(st[k] for st in shared):.0f}"
                                                             for k in ("read", "written", "waited", "wait_s"))
                          if shared else "")
    return run


build = _generation_job(_accounted(build))
terrain_patch = _generation_job(_accounted(terrain_patch))
roads_tile = _generation_job(_accounted(roads_tile))
far_buildings_tile = _accounted(far_buildings_tile)
base_entry = _accounted(base_entry)
far_geometry = _generation_job(_accounted(far_geometry))
far_tile = _generation_job(_accounted(far_tile))
base_photo = _accounted(base_photo)
navgraph = _generation_job(_accounted(navgraph))
