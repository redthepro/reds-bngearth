"""Shared terrain preparation for the in-process and process-worker callers.

The caller owns progress/urgent lanes, profile adapters, request cancellation,
photo work and activation. This module preserves the current preparation order.
Optional persistent reuse is compile-only: current materialized inputs are
prepared before every lookup; uncertain geographic decisions remain per-run.
"""
import io
import os
from pathlib import Path
import time

import numpy as np
from PIL import Image

import compiledcache
import grading
import heightmap
import mapcells
import surface as surfacemod
import worldplan

WATER_BED_M = 0.05


def shared_enabled():
    return os.environ.get("BNGE_SHARED_TERRAIN", "on") != "off"


def dependency_keys(frame, cx, cy, size, halo_m=450.0):
    """Bounded declared request halo, not a claim that arbitrary profiles close it."""
    half = size * heightmap.SQUARE / 2 + halo_m
    la, lo, _ = frame.enu_to_geodetic(np.array([cx - half, cx + half, cx - half, cx + half]),
                                     np.array([cy - half, cy - half, cy + half, cy + half]), 0.0)
    lo = frame.lon0 + (np.asarray(lo) - frame.lon0 + 180.0) % 360.0 - 180.0
    try:
        return mapcells.cells_for_bbox(float(np.min(la)), float(np.min(lo)), float(np.max(la)),
                                       float(np.max(lo)), max_cells=worldplan.MAX_FACT_CELLS)
    except ValueError:
        return []  # unknown/unclosed; never promote an oversized/polar halo to final


def prepare(frame, cx, cy, size, q, out_dir, name, ter_name, *, sample,
            ground=None, provisional_ground=None, grader=None, surface=None,
            widths=None, sea=None, arch=None, holes=None, open_tunnels=None,
            chunks=None, provisional=False, t0=None, cpu_stages=False,
            cache_dir=None, plan_context=None, settings=None, cache_kind="demand", checkpoint=None, ground_at=(0.0, 0.0)):
    """Return the callers' existing info dict after preparing their files.

    arch(elev) optionally returns a terrain sampler; holes(elev, keep, idx, info)
    adjusts only TER indices. Surface masks intentionally precede tunnel holes.
    """
    t0 = time.perf_counter() if t0 is None else t0
    c0 = time.thread_time()
    keep = {}
    grade_s = [0.0, 0.0]
    check = checkpoint or (lambda: None)
    check()
    compiled = os.environ.get("BNGE_COMPILED_TERRAIN") == "1" and cache_dir is not None

    def elev(la, lo):
        z = sample(la, lo)
        if provisional:
            return provisional_ground(la, lo, z) if provisional_ground is not None else z
        if ground is not None:
            z = ground(la, lo, z)
        if grader is None:
            return z
        wall, cpu = time.perf_counter(), time.thread_time()
        z = grader.apply(la, lo, z, grading.ROAD_LIFT)
        grade_s[0] += time.perf_counter() - wall
        grade_s[1] += time.thread_time() - cpu
        return z

    gen = arch(elev) if arch is not None and not provisional else elev
    writer = (lambda path, u, q: heightmap.quantized_grid(u, q)) if compiled else None
    info = heightmap.generate(frame, gen, os.path.join(out_dir, name), cx, cy, q=q,
                              size=size, keep=keep, chunks=None if provisional else chunks,
                              png_writer=writer, ground_at=ground_at)
    check()
    stages = {"heights": time.perf_counter() - t0, "grading": grade_s[0]}
    if cpu_stages:
        stages.update(gradingCpu=grade_s[1], heightsCpu=time.thread_time() - c0)
    idx, mask_idx = None, None
    if surface is not None:
        start = time.perf_counter()
        classes, complete = surface.classes(keep["lat"], keep["lon"])
        check()
        idx = surfacemod.classify(classes, keep["u"], heightmap.SQUARE, keep["lat"], keep["lon"])
        if sea is not None and not provisional:
            idx[sea.depth(keep["lat"], keep["lon"]) > WATER_BED_M] = surfacemod.MUD
        road_feats, roads_complete = surface.roads(keep["lat"], keep["lon"])
        stages["landcover"] = time.perf_counter() - start
        start = time.perf_counter()
        if road_feats:
            idx = surfacemod.paint_roads(idx, keep["lat"], keep["lon"], road_feats,
                                         heightmap.SQUARE, surface.urban,
                                         None if provisional else widths,
                                         open_tunnels(keep) if open_tunnels is not None else [])
        if hasattr(surface, "lots"):
            idx = surfacemod.paint_lots(idx, keep["lat"], keep["lon"], surface.lots(keep["lat"], keep["lon"]))
        if hasattr(surface, "fuel_pads"):
            idx = surfacemod.paint_pads(idx, keep["lat"], keep["lon"], surface.fuel_pads(keep["lat"], keep["lon"]))
        stages["roadPaint"] = time.perf_counter() - start
        check()
        # Do not let portal holes mutate the surface-mask snapshot.
        mask_idx = idx.copy() if compiled else None
        info["layers"] = (surfacemod.mask_names(name[:-4]) if compiled else
                          surfacemod.write_masks(idx[::-1], out_dir, name[:-4]))
        info["surfaceComplete"] = complete and roads_complete
    if ter_name is not None:
        check()
        start = time.perf_counter()
        if holes is not None and not provisional:
            idx = holes(elev, keep, idx, info)
        if compiled:
            info["ter"] = ter_name
            mats = None if idx is None else surfacemod.used_materials(idx)
        else:
            info["ter"], mats = surfacemod.write_ter(out_dir, ter_name, keep["qgrid"], idx)
        if mats is not None:
            info["mats"] = mats
        # Historically only the worker reported the TER stage.
        if not cpu_stages:
            stages["ter"] = time.perf_counter() - start

    if compiled:
        check()
        start = time.perf_counter()
        context = plan_context(dependency_keys(frame, cx, cy, size)) if callable(plan_context) else plan_context
        descriptor = worldplan.build_descriptor(frame, cx, cy, size, info["q"],
            {"height_u": keep["u"], "qgrid": keep["qgrid"], "mask_idx": mask_idx, "ter_idx": idx},
            context=context, settings={"provisional": bool(provisional),
                "ter": ter_name is not None, "materials": list(surfacemod.MATERIALS), **(settings or {}),
                "z_base_m": info["zBase"], "max_height_m": info["maxHeight"]})
        targets = {"height.png": os.path.join(out_dir, name)}
        if mask_idx is not None:
            targets.update({f"mask{i}.png": os.path.join(out_dir, n) for i, n in enumerate(info["layers"])})
        if ter_name is not None:
            targets["terrain.ter"] = os.path.join(out_dir, ter_name)

        def encode():
            check()
            products = {"height.png": heightmap.png_bytes(keep["qgrid"])}
            if mask_idx is not None:
                north = mask_idx[::-1]
                for i in range(len(surfacemod.MATERIALS)):
                    stream = io.BytesIO()
                    Image.fromarray(np.where(north == i, 255, 0).astype(np.uint8), "L").save(
                        stream, format="PNG", compress_level=1)
                    products[f"mask{i}.png"] = stream.getvalue()
            if ter_name is not None:
                if idx is None:
                    indices, materials = None, [surfacemod.MATERIALS[surfacemod.DIRT][0]]
                else:
                    used = info["mats"]
                    lut = np.full(256, heightmap.TER_HOLE, np.uint8)
                    lut[used] = np.arange(len(used), dtype=np.uint8)
                    indices, materials = lut[np.asarray(idx, np.uint8)], [surfacemod.MATERIALS[i][0] for i in used]
                products["terrain.ter"] = heightmap.ter_bytes(keep["qgrid"], indices, materials)
            return products

        cache = compiledcache.ArtifactCache(Path(cache_dir) / "compiled-terrain")
        info["compiled"] = cache.materialize(worldplan.content_key(descriptor), descriptor, encode, targets,
                                               kind=cache_kind)
        info["compiled"].update(canonical_id=descriptor["canonical_id"],
                                  input_quality=descriptor["input_quality"],
                                  decision_plan_persisted=False)
        stages["compile"] = time.perf_counter() - start
    check()
    info["stages"] = stages
    return info
