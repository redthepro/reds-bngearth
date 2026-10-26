-- Real governor under a minute of continuously queued road/far work: custom replacements must
-- finish loading without bypassing the budget or delaying terrain heavy slots indefinitely.
local dir = (arg[0]:match('^(.*)[/\\]') or '.')
local T = dofile(dir .. '/testlib.lua')
local S = dofile(dir .. '/stubs.lua')
local G = require('beamngEarth/geodesy')
local Gov = require('beamngEarth/governor')
local L = require('beamngEarth/landmarks')
local now = 0
hptimer = function()
  local last = now
  return { stopAndReset = function() local d = now - last; last = now; return d end }
end
local originalCreate, originalLoad = createObject, loadJsonMaterialsFile
createObject = function(class) now = now + 20; return originalCreate(class) end
loadJsonMaterialsFile = function(path) now = now + 3; return originalLoad(path) end
local anchor = G.newAnchor(38.889, -77.035)
local base = '/levels/beamng_earth/art/shapes/beamngEarth/landmarks/'
local rows = {}
for i = 1, 3 do
  local id = 'model' .. i
  rows[i] = { id=id, shape=base .. id .. '/compiled.cdae', materials=base .. id .. '/main.materials.json',
    x=i*100, y=0, z=20, lat=anchor.lat, lon=anchor.lon }
end
for _, saturatedRoads in ipairs({false, true}) do
  L.reset()
  L.onMessage({anchorId=1, rows=rows}, 1, anchor)
  local gov = Gov.new({clock=function() return now end})
  local roads, far, heavy, finished, total, allocated, due = 0, 0, 0, nil, 0, 0, false
  for frame = 1, 3600 do
    local start = now
    gov:beginFrame(true)
    allocated = allocated + gov.b
    if frame == 20 or frame == 180 then due = true end
    if due and gov:requestHeavy(true) then due = false; heavy = heavy + 1; now = now + 45 end
    L.reserve(gov, 1, {x=0,y=0})
    -- Exactly core's order: terrain decision, reservation, road work, landmark unit, far work.
    while gov:try('roadChunk') do
      now = now + 0.5; gov:charge('roadChunk', 0.5); roads = roads + 1
      if not saturatedRoads then break end
    end
    L.update(gov, 1, {x=0,y=0})
    while gov:try('cdae') do now = now + 1.75; gov:charge('cdae', 1.75); far = far + 1 end
    total = total + gov.frameSpent
    if gov.heavyFrame == gov.frame then T.check(gov.frameUnits == 0, 'terrain heavy frame remains isolated') end
    if not finished and not L.hasWork() then finished = frame end
    now = start + 1000/60 + (now-start)
  end
  T.check(finished and finished < 900, 'all three landmarks finish despite continuous imports (frame '..tostring(finished)..')')
  T.check(heavy == 2 and not due, 'both terrain requests complete while landmark work is pending')
  T.check(roads > 3000, 'roads continue receiving service throughout the minute')
  if not saturatedRoads then T.check(far > 1000, 'continuous far imports also make progress') end
  T.check(total <= allocated + 1e-6, 'reservation does not mint extra streaming budget')
  T.near(gov.est['landmarkImport:model1'], 20, 1e-6, 'actual 20 ms import reaches governor cost learning')
  T.check(#S.liveOf('TSStatic') == 3, 'three persistent actors, no duplicate imports')
  T.check(gov:maxUnit() <= math.max(gov.est.cdae, gov.est.roadChunk), 'completed landmark imports do not enlarge later far/road bursts')
end
L.reset()
-- Membership cancellation returns unspent credit to the shared bucket immediately.
local gov = Gov.new({clock=function() return now end})
L.onMessage({anchorId=1, rows=rows}, 1, anchor)
gov:beginFrame(true, 16)
L.reserve(gov, 1, {x=0,y=0})
gov:beginFrame(true, 16)
local before = gov.tokens
L.reserve(gov, 1, {x=0,y=0})
T.check(gov.tokens < before, 'pending work holds a bounded fraction of tokens')
L.onMessage({anchorId=1, rows={}}, 1, anchor)
T.near(gov.tokens, before, 1e-9, 'membership cancellation returns unspent credit')
T.check(gov.used.landmarkMaterials == nil, 'canceled reservation drops its temporary unit allowance')
T.check(not L.hasWork(), 'cancellation stops reservation work')
L.reset()
T.done('test_landmark_governor')
