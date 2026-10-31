-- Deterministic lifecycle fixtures with assumed engine stubs; not BeamNG validation.
local dir = (arg[0]:match('^(.*)[/\\]') or '.')
local T = dofile(dir .. '/testlib.lua')
local S = dofile(dir .. '/stubs.lua')
local F = require('beamngEarth/farfield')
local R = require('beamngEarth/roads')
local N = require('beamngEarth/navgraph')
local free = { clock = function() return 0 end, est = { cdae = 3 }, try = function() return true end,
  charge = function() end, chargeMotion = function() end }
F.SPARE_MAX = 0
F.switchAnchor(1, false, { cx = 0, cy = 0 }, 'fixture')
F.configureStreaming({ lifecycle = 1 })
local function tile(seq, key)
  return { anchorId = 1, key = key or '0_0_0', level = 0, pos = { 0, 0, 0 },
    shape = '/fixture_' .. seq .. '.cdae', state = 'group', streamSeq = seq, installBytes = 100 }
end
F.onTile(tile(1))
F.onTile(tile(1))
T.check(F.queuedCount() == 1, 'duplicate tile does not enqueue twice')
local p = F.pressure(free)
T.check(p.receivedSeq == 1 and p.pendingBytes == 100 and p.installMs == 3,
  'receipt does not release pending installation bytes or work')
F.onTile(tile(2))
F.onTile(tile(1))
T.check(F.queuedCount() == 1 and F._pending('0_0_0').shape == '/fixture_2.cdae', 'late older tile cannot replace newer')
F.onRemove({ anchorId = 1, key = '0_0_0', retireThrough = 1 })
T.check(F.queuedCount() == 1, 'late retirement cannot discard newer preparation')
F.update(free, 1 / 60)
T.check(F.pressure(free).pendingBytes == 0, 'successful hidden installation releases queue pressure')
F.onTile(tile(3, '0_1_0'))
F.switchAnchor(2, false, { cx = 0, cy = 0 }, 'fixture')
T.check(F.pressure(free).pendingBytes == 0 and F.pressure(free).receivedSeq == 3,
  'anchor switch discards queued work without pretending server receipt was installation')
F.configureStreaming({ lifecycle = 1 })
T.check(F.pressure(free).receivedSeq == 0, 'reconnect resets connection sequence space')

R.setTerrainReceipt(nil, nil)
R.onTile({ anchorId = 2, key = '2_0', data = { chunks = {},
  deck = { shape = '/unversioned-deferred.cdae', pos = { 512, 0, 10 } } } }, 2)
local unversionedObjects = #S.liveOf('TSStatic')
R.setTerrainReceipt(2, 'fixture:2:1')
R.update(free, 2)
T.check(#S.liveOf('TSStatic') == unversionedObjects,
  'unversioned queued collider cannot install after terrain receipt negotiation')
T.check(R.onTile({ anchorId = 2, key = '0_0', data = { chunks = {} }, planReceipt = 'fixture:2:1' }, 2),
  'matching committed terrain permits road preparation')
T.check(not R.onTile({ anchorId = 2, key = '0_0', data = { chunks = {} }, planReceipt = 'fixture:2:2' }, 2),
  'new collision receipt cannot replace support before terrain commit')
local heldTiles = R.status().tiles
T.check(not R.onTile({ anchorId = 2, key = '0_0', data = { chunks = {} } }, 2)
  and R.status().tiles == heldTiles, 'missing receipt cannot replace committed support')
R.setTerrainReceipt(2, 'fixture:2:2')
T.check(R.onTile({ anchorId = 2, key = '0_0', data = { chunks = {} }, planReceipt = 'fixture:2:2' }, 2),
  'successful terrain commit admits matching road replacement')
T.check(not R.onTile({ anchorId = 2, key = '0_0', data = { chunks = {} }, planReceipt = 'fixture:2:1' }, 2),
  'late old road receipt cannot reactivate after terrain commit')
R.onTile({ anchorId = 2, key = '1_0', data = { chunks = {},
  deck = { shape = '/deferred-deck.cdae', pos = { 256, 0, 10 } } }, planReceipt = 'fixture:2:2' }, 2)
local beforeObjects = #S.liveOf('TSStatic')
R.setTerrainReceipt(2, 'fixture:2:3')
R.update(free, 2)
T.check(#S.liveOf('TSStatic') == beforeObjects, 'deferred collider rechecks terrain receipt immediately before installation')
local count = R.status().tiles
T.check(not R.onTile({ anchorId = 2, key = '0_0', data = { chunks = false }, path = '/bad' }, 2),
  'malformed road replacement rejected')
T.check(R.status().tiles == count, 'malformed replacement retains previous support record')
R.enabled = false
T.check(not R.supportedSegment(2, 'fixture:2:1', { 0, 0 }, { 1, 1 }), 'roads disabled never proves bridge support')
R.enabled = true

local graphData = { nodes = { a = { 0, 0, 10, 3 }, b = { 30, 0, 10, 3 } },
  edges = { { 'a', 'b', 0, 1, 1, 10, 1, 3, true } }, bridgeSupportKnown = true }
local _, _, missing = N.toNodes(graphData, nil, 0, false)
T.check(missing == 0, 'bridge omitted without engine collision proof')
local _, _, supported = N.toNodes(graphData, nil, 0, false, nil, function() return true end)
T.check(supported == 1, 'supported bridge survives graph clipping')
graphData.bridgeSupportKnown = false
local _, _, uncertain = N.toNodes(graphData, nil, 0, false, nil, function() return true end)
T.check(uncertain == 0, 'unknown process snapshot remains withheld despite local collider claim')
graphData.bridgeSupportKnown = true
local reads = 0
readFile = function(p) reads = reads + 1; return p end
jsonDecode = function() return graphData end
jsonEncode = function() return '{}' end
writeFile = function() return true end
table.clear = function(t) for k in pairs(t) do t[k] = nil end end
path = { getPathLevelInfo = function() return '/fixture' end }
be.queueAllObjectLua = function() end
local current, resets = { nodes = {} }, 0
map = { getMap = function() return current end,
  reset = function(nodes) current = { nodes = nodes }; resets = resets + 1 end }
N.choice = { traffic = false }
N.MIN_GAP_S, N.PREP_YIELD, N.PREP_MS = 0, 1, 0
local msg = { anchorId = 2, path = '/graph', serial = 2, planReceipt = 'fixture:2:1' }
T.check(N.onMessage(msg, 2), 'new graph accepted')
local before = reads
T.check(not N.onMessage({ anchorId = 2, path = '/late', serial = 1, planReceipt = 'fixture:2:1' }, 2)
  and reads == before, 'stale graph rejected before reading its path')
local revision, deckReady = 1, true
local support = { planReceipt = 'fixture:2:1', supportComplete = true, settled = true,
  roads = { supportRevision = function() return revision end,
    supportedSegment = function() return deckReady end } }
for i = 1, 20 do N.update(2, nil, nil, support) end
T.check(current.nodes.a and current.nodes.a.links.b, 'matching collision receipt activates prepared bridge graph')
revision, deckReady = 2, false
local previous = resets
N.update(2, nil, nil, support)
T.check(resets == previous + 1 and next(current.nodes) == nil,
  'dropped deck immediately withdraws applied links while replacement coroutine yields')
revision, deckReady = 3, true
for i = 1, 20 do N.update(2, nil, nil, support) end
T.check(current.nodes.a and current.nodes.a.links.b, 'compatible support can reactivate after successful preparation')
N.onMessage({ anchorId = 2, path = '/new', serial = 3, planReceipt = 'fixture:2:2' }, 2)
N.update(2, nil, nil, support)
T.check(next(current.nodes) == nil, 'pending graph receipt mismatch withdraws old graph before activation')
support.planReceipt = 'fixture:2:2'
for i = 1, 20 do N.update(2, nil, nil, support) end
support.supportComplete = false
N.update(2, nil, nil, support)
T.check(next(current.nodes) == nil, 'failed or provisional collision preparation never leaves active navigation')
T.done('test_streaming_lifecycle')
