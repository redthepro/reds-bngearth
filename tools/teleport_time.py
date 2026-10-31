#!/usr/bin/env python3
"""How long a teleport takes until the world around the car is there: teleport (the mod's own, kept out of the
recent places), then poll the mod's status every POLL_S and print when each part was first complete.

  python3 tools/teleport_time.py 39.2834,-76.5934 [--timeout 300] [--json out.json]

Milestones (seconds after the teleport call): anchor (the new frame committed: the car is placed), far (the far
field done), roads (every road tile's roads and meshes made, nothing queued), roads9 / roads18 / roads36 (that many
road tiles made), trees (no tree tile pending),
backlog (the server has nothing queued; fresh only with earthd --status-interval 1), all (everything at once).
The game's MCP server must be on and the companion server running. The car is left where it lands.
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mcp  # noqa: E402

POLL_S = 0.5
LUA = r'''
local s = loadstring('return ' .. beamngEarth_core.status())() or {}
local rs = require('beamngEarth/roads').status() or {}
local f, t = s.far or {}, s.trees or {}
return jsonEncode({ anchor = s.anchorId, farDone = f.done, farQueued = f.queued, live = f.live,
  roadTiles = rs.tiles, roadQ = rs.queued, walkQ = rs.walksQueued, buildings = rs.buildings, roads = rs.roads,
  treePending = t.pending, backlog = s.serverBacklog, status = s.status })
'''


def poll():
    return json.loads(mcp.call("run_lua", {"code": LUA}))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("latlon")
    ap.add_argument("--timeout", type=float, default=300)
    ap.add_argument("--json")
    a = ap.parse_args()
    lat, lon = (float(x) for x in a.latlon.split(","))
    before = poll()
    t0 = time.time()
    mcp.call("run_lua", {"code": f"return tostring(beamngEarth_core.teleport({lat}, {lon}, nil, {{record = false}}))"})
    marks, last = {}, None
    while time.time() - t0 < a.timeout:
        time.sleep(POLL_S)
        try:
            s = poll()
        except Exception as e:  # noqa: BLE001 - the game hitches while loading: try again
            print(f"poll: {e}", file=sys.stderr)
            continue
        t = round(time.time() - t0, 1)
        moved = s.get("anchor") is not None and s.get("anchor") != before.get("anchor")
        done = {
            "anchor": moved,
            "far": moved and bool(s["farDone"]) and not s["farQueued"],
            "roads": moved and (s["roadTiles"] or 0) > 0 and not s["roadQ"] and not s["walkQ"],
            "trees": moved and not s["treePending"],
            "backlog": moved and s["backlog"] == 0,
        }
        done["all"] = all(done.values())
        for n in (9, 18, 36):           # (road tiles made: the first ones' "roads" mark is when the mod's queue first ran dry)
            done[f"roads{n}"] = moved and (s["roadTiles"] or 0) >= n
        for k, v in done.items():
            if v and k not in marks:
                marks[k] = t
                print(f"{t:7.1f} s  {k}  ({s['status']}; road tiles {s['roadTiles']}, buildings {s['buildings']})")
        last = s
        if done["all"]:
            # everything once is not settled: the server sends the outer tiles later; wait for 5 s quiet
            quiet_until = time.time() + 5
            while time.time() < quiet_until:
                time.sleep(POLL_S)
                s2 = poll()
                if s2["roadQ"] or s2["walkQ"] or s2["farQueued"] or s2["treePending"] or s2["backlog"]:
                    quiet_until = time.time() + 5
                    marks.pop("settled", None)
            marks["settled"] = round(time.time() - t0 - 5, 1)
            last = s2
            print(f"{marks['settled']:7.1f} s  settled (road tiles {s2['roadTiles']}, buildings {s2['buildings']})")
            break
    out = {"lat": lat, "lon": lon, "marks": marks, "last": last}
    if a.json:
        with open(a.json, "w") as f:
            json.dump(out, f, indent=1)
    print(json.dumps(marks))


if __name__ == "__main__":
    main()
