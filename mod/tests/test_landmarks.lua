local dir = (arg[0]:match('^(.*)[/\\]') or '.')
local T = dofile(dir .. '/testlib.lua')
local S = dofile(dir .. '/stubs.lua')
local L = require('beamngEarth/landmarks')
local G = require('beamngEarth/geodesy')
local anchor = G.newAnchor(38.889, -77.035)
local prefix = '/levels/beamng_earth/art/shapes/beamngEarth/landmarks/'
local function row(id, x)
  return { id = id, shape = prefix .. id .. '/compiled.cdae', materials = prefix .. id .. '/main.materials.json',
    lat = 38.889, lon = -77.035, x = x or 100, y = 200, z = 30 }
end
local gov = { est = {}, allowed = false, tries = 0, charges = 0 }
function gov:try(kind) self.tries = self.tries + 1; return self.allowed end
function gov:charge(kind, cost) self.charges = self.charges + 1; self.kind = kind end
local function update() L.update(gov, 1, { x = 0, y = 0 }) end
local function live() return S.liveOf('TSStatic') end
L.onMessage({ anchorId = 1, rows = { row('one'), row('two', 1000) } }, 1, anchor)
T.check(L.hasWork(), 'queued models participate in governor work')
update()
T.check(#live() == 0 and not S.calls.loadJsonMaterialsFile, 'budget denial loads no resources')
gov.allowed = true
update()
T.check(#live() == 0 and S.calls.loadJsonMaterialsFile == 1, 'materials are a separate budgeted unit')
update()
T.check(#live() == 1 and gov.charges == 2, 'one import per update')
local original = live()[1]
T.check(original.fields.collisionType == 'None', 'distant visual model adds no expensive physics')
T.near(original.pos.x, 100, 1e-6, 'position is set before registration')
local other = G.newAnchor(38.9, -77.05)
local xf = anchor:transformTo(other)
local x, y, z = xf:apply(100, 200, 30)
L.transform(xf, 2)
T.check(live()[1] == original, 'rebase preserves actor identity')
T.near(original.pos.x, x, 1e-6, 'existing actor transformed in same call')
T.near(original.pos.z, z, 1e-6, 'rebase includes vertical curvature')
L.update(gov, 2, {x=0,y=0})
L.update(gov, 2, {x=0,y=0})
T.check(#live() == 2, 'queued actor survives rebase')
local actors = L.state()
local xx, yy, zz = xf:apply(1000, 200, 30)
T.near(actors.two.x, xx, 1e-6, 'queued actor never imports in stale frame')
L.onMessage({ anchorId = 1, rows = {} }, 2, other)
T.check(not L.hasWork(), 'late old anchor message ignored')
L.onMessage({ anchorId = 2, rows = {} }, 2, other)
L.update(gov, 2, {x=0,y=0})
T.check(#live() == 1, 'range removal consumes one unit')
L.update(gov, 2, {x=0,y=0})
T.check(#live() == 0 and not L.hasWork(), 'empty snapshot removes all actors')
local loaded = S.calls.loadJsonMaterialsFile
L.onMessage({ anchorId = 1, rows = { row('one') } }, 1, anchor)
update()
T.check(#live() == 1 and S.calls.loadJsonMaterialsFile == loaded, 'reenter range reuses materials')
L.clear()
T.check(#live() == 0 and not L.hasWork(), 'disconnect/teleport clear has no leftover queue or actor')
local bad = row('bad'); bad.materials = prefix .. 'bad/../other/main.materials.json'
L.onMessage({ anchorId = 1, rows = { bad } }, 1, anchor)
T.check(not L.hasWork(), 'reject material path traversal before any resource call')
FS.fileExists = function() return false end
L.onMessage({ anchorId = 1, rows = { row('missing') } }, 1, anchor)
local before = gov.charges
update()
T.check(gov.charges == before + 1 and not L.hasWork() and #live() == 0, 'missing asset fails once and charges budget')
update()
T.check(gov.charges == before + 1, 'failed asset cannot churn resource loads')
L.reset()
T.check(next((select(3, L.state()))) == nil, 'mission reset clears material bookkeeping')
FS.fileExists = function() return true end
for i = 1, 32 do
  L.onMessage({ anchorId = 1, rows = { row('asset' .. i) } }, 1, anchor)
  update(); update(); L.clear()
end
loaded = S.calls.loadJsonMaterialsFile
L.onMessage({ anchorId = 1, rows = { row('over_limit') } }, 1, anchor)
update()
T.check(S.calls.loadJsonMaterialsFile == loaded and not L.hasWork(), 'material registration is bounded across snapshots')
L.reset()
local create = createObject
createObject = function(class)
  local o = create(class)
  function o:registerObject() error('engine rejected shape') end
  return o
end
L.onMessage({ anchorId = 1, rows = { row('broken') } }, 1, anchor)
update(); update()
T.check(#live() == 0 and not L.hasWork(), 'failed actor registration leaves no actor or retry churn')
createObject = create
L.reset()
T.done('test_landmarks')
