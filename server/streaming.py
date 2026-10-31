"""Shared visual scheduling: driving corridor, camera view, warm reserve.

This never changes collision requests or the desired terrain set. Completed off-screen
tiles remain usable when the camera turns; lower throughput only limits optional generation.
"""
import math

import numpy as np

import fast

DRIVE_RADIUS = 150.0
DRIVE_LEAD = 6.0
FEEDBACK_TTL = 3.0
MIN_SCALE = 0.25
BACKLOG_TARGET = 64             # tiles waiting on the game's import budget
PENDING_BYTES = 64 << 20
INSTALL_MS = 256.0
JOB_BYTES = 8 << 20             # conservative admission reservation, separate from payload bytes
CRITICAL_BYTES = 32 << 20
RETRY_S = 0.1


def arrival(tile, focus, velocity):
    """Earliest straight-line arrival at a tile's safety envelope (seconds)."""
    speed = math.hypot(*velocity)
    if speed < 1.0:
        return tile.distance(*focus)
    x0, y0, x1, y1 = tile.bbox
    dx, dy = (x0 + x1) / 2 - focus[0], (y0 + y1) / 2 - focus[1]
    along = (dx * velocity[0] + dy * velocity[1]) / speed
    return max(0.0, along - DRIVE_RADIUS - tile.size * 0.71) / speed if along >= 0 else math.inf


def pressure(value):
    """Bound untrusted negotiated feedback. Receipt is not installation/release."""
    if not isinstance(value, dict):
        return None
    out = {}
    for key, cap in (("feedbackSeq", 2**53 - 1), ("receivedSeq", 2**53 - 1),
                     ("pendingBytes", 1 << 40), ("installMs", 1e7)):
        v = value.get(key)
        if not finite_number(v) or v < 0 or v > cap:
            return None
        if key != "installMs" and int(v) != v:
            return None
        out[key] = v
    return out


def payload_cost(info, tex_px=0):
    """Payload plus decoded texture allowance; not a measured engine-memory bound."""
    size = max(0, int(info.bytes)) + max(0, int(info.tex_bytes))
    return size + (max(0, int(tex_px)) ** 2 * 4 if info.material is not None else 0)


def priorities(tiles, focus, velocity, view_focus, cone):
    if not tiles:
        return np.empty((0, 2))
    fast.require(69, "stream_priorities")
    boxes = np.asarray([t.bbox for t in tiles], dtype=np.float64)
    return fast._ef.stream_priorities(boxes, [*focus, *velocity], list(view_focus),
                                     list(cone) if cone else [], DRIVE_RADIUS, DRIVE_LEAD)


def feedback(value):
    """Untrusted/old clients cannot remove all visual workers or submit non-finite values."""
    if not isinstance(value, dict):
        return 1.0
    scale = value.get("scale")
    if not finite_number(scale):
        return 1.0
    scale = min(1.0, max(MIN_SCALE, scale))
    queued = value.get("queued")
    # Loading screens intentionally fill faster. While driving, a growing client import
    # queue is backpressure even when its FPS cap is being met. Never stop all workers.
    if value.get("loading") is not True and finite_number(queued) and queued > BACKLOG_TARGET:
        scale = min(scale, max(MIN_SCALE, BACKLOG_TARGET / queued))
    return scale


def finite_number(value):
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def worker_limit(workers, scale, updated, now):
    if now - updated > FEEDBACK_TTL:
        return workers
    return max(1, math.ceil(workers * scale))
