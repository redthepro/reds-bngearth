-- Persistent, bundled landmark actors. Their precompiled camera LODs are engine-selected.
-- Terrain and roads get the governor first; each update performs at most one resource operation.
local M = {}
local geodesy = require('beamngEarth/geodesy')
local stopwatch = require('beamngEarth/stopwatch')
local PREFIX = '/levels/beamng_earth/art/shapes/beamngEarth/landmarks/'
local MAX_MODELS = 32
local actors, wanted, materials, failed = {}, {}, {}, {}
local materialCount = 0 -- global material registrations are bounded even across repeated snapshots
local anchorId = nil
local credit, creditKind, creditGov = 0, nil, nil
M.RESERVE_SHARE = 0.25 -- fraction of the existing budget, capped at ONE pending unit, never extra budget

local function releaseCredit()
  if creditGov then
    creditGov.tokens = creditGov.tokens + credit
    if creditGov.used then creditGov.used[creditKind] = nil end
  end
  credit, creditKind, creditGov = 0, nil, nil
end

local function finite(v) return type(v) == 'number' and v == v and math.abs(v) < math.huge end
local function pathOK(path, id, suffix)
  local base = PREFIX .. id .. '/'
  return type(path) == 'string' and path:sub(1, #base) == base and path:sub(-#suffix) == suffix
    and not path:find('..', 1, true) and not path:find('\\', 1, true)
end
local function object(a) return a and a.objectId and scenetree.findObjectById(a.objectId) end
local function move(a)
  local o = object(a)
  if o then o:setPosRot(a.x, a.y, a.z, a.qx, a.qy, a.qz, a.qw) end
end
local function dispose(id)
  local o = object(actors[id])
  if o then o:delete() end
  actors[id] = nil
end

-- Full desired-set snapshot, scoped to the committed frame. No shape/material loads here.
function M.onMessage(msg, aid, anchor)
  if msg.anchorId ~= aid or not anchor or type(msg.rows) ~= 'table' then return end
  releaseCredit()
  local nextWanted = {}
  for i, row in ipairs(msg.rows) do
    if i > MAX_MODELS then break end
    local id = type(row) == 'table' and row.id
    if type(id) == 'string' and #id <= 64 and id:match('^[a-z0-9_-]+$')
      and pathOK(row.shape, id, '.cdae') and pathOK(row.materials, id, '.materials.json')
      and finite(row.x) and finite(row.y) and finite(row.z) and finite(row.lat) and finite(row.lon)
      and math.abs(row.lat) <= 90 and math.abs(row.lon) <= 180 then
      -- At long range its local vertical/north differ from the anchor's. Preserve the rigid frame.
      local xf = geodesy.newAnchor(row.lat, row.lon):transformTo(anchor)
      local qx, qy, qz, qw = xf:beamngQuat()
      local a = actors[id]
      if a and (a.shape ~= row.shape or a.materials ~= row.materials) then a = nil end
      if not a then a = { id = id, shape = row.shape, materials = row.materials } end
      a.x, a.y, a.z, a.qx, a.qy, a.qz, a.qw = row.x, row.y, row.z, qx, qy, qz, qw
      nextWanted[id] = a
      if actors[id] == a then move(a) end
    end
  end
  wanted, anchorId = nextWanted, aid
end

function M.hasWork()
  for id, a in pairs(actors) do if wanted[id] ~= a then return true end end
  for id, a in pairs(wanted) do if not a.objectId and not failed[id] then return true end end
  return false
end

local function run(gov, kind, prior, fn)
  -- Separate learning from tiny terrain imports: a landmark can contain much more geometry.
  if gov.est and gov.est[kind] == nil then gov.est[kind] = prior end
  local saved = creditKind == kind and creditGov == gov and credit or 0
  if saved > 0 then gov.tokens = gov.tokens + saved end
  if not gov:try(kind) then
    if saved > 0 then gov.tokens = gov.tokens - saved end
    return false
  end
  if saved > 0 then credit, creditKind, creditGov = 0, nil, nil end
  local sw = stopwatch.new()
  local ok, err = pcall(fn)
  gov:charge(kind, sw()) -- failed loads also consumed the resource budget
  -- Keep cost learning, but a finished one-shot import must not permanently increase maxUnit
  -- (and therefore ordinary road/far frame bursts) for the rest of the driving session.
  if gov.used then gov.used[kind] = nil end
  if not ok then log('W', 'beamngEarth.landmarks', tostring(err)) end
  return true, ok
end

local function nearest(player, view)
  -- Nearest to either focus first, deterministic when the distances tie.
  local selected, distance
  for id, a in pairs(wanted) do
    if not a.objectId and not failed[id] then
      local d = math.huge
      for _, p in pairs({ player = player, view = view }) do
        d = math.min(d, (a.x - p.x)^2 + (a.y - p.y)^2)
      end
      if not selected or d < distance or (d == distance and id < selected.id) then selected, distance = a, d end
    end
  end
  return selected
end

-- Called after terrain decisions, before roads. Roads retain 75%+ of new budget and are serviced
-- first; optional far imports cannot spend this one unit's saved credit. Heavy slots still win.
function M.reserve(gov, aid, player, view)
  if aid ~= anchorId or gov.blocked or gov.heavyPending or gov.heavyFrame == gov.frame then
    releaseCredit()
    return
  end
  local kind, prior
  for id, a in pairs(actors) do if wanted[id] ~= a then kind, prior = 'landmarkDelete', 0.3; break end end
  if not kind then
    local a = nearest(player, view)
    if a then
      kind = materials[a.materials] and ('landmarkImport:' .. a.id) or 'landmarkMaterials'
      prior = materials[a.materials] and 20 or 3
    end
  end
  if not kind then releaseCredit(); return end
  if creditKind ~= kind or creditGov ~= gov then releaseCredit(); creditKind, creditGov = kind, gov end
  gov.est[kind] = gov.est[kind] or prior
  gov:try(kind) -- register its cost with maxUnit, without admitting or charging a resource operation
  local amount = math.min(math.max(0, gov.est[kind] - credit), math.max(0, gov.tokens), gov.b * M.RESERVE_SHARE)
  credit, gov.tokens = credit + amount, gov.tokens - amount
end

function M.update(gov, aid, player, view)
  if aid ~= anchorId then return end
  for id, a in pairs(actors) do
    if wanted[id] ~= a then run(gov, 'landmarkDelete', 0.3, function() dispose(id) end); return end
  end
  local selected = nearest(player, view)
  if not selected then return end
  local a = selected
  if not materials[a.materials] then
    local ran, ok = run(gov, 'landmarkMaterials', 3, function()
      if materialCount >= MAX_MODELS then error('landmark material limit reached') end
      if not FS:fileExists(a.materials) or not FS:fileExists(a.shape) then error('missing bundled landmark ' .. a.id) end
      if loadJsonMaterialsFile(a.materials) == false then error('landmark material load failed: ' .. a.id) end
      materials[a.materials] = true
      materialCount = materialCount + 1
    end)
    if ran and not ok then failed[a.id] = true end
    return
  end
  local o
  local ran, ok = run(gov, 'landmarkImport:' .. a.id, 20, function()
    if not FS:fileExists(a.shape) then error('missing bundled landmark shape: ' .. a.id) end
    o = createObject('TSStatic')
    o.canSave = false
    o:setField('shapeName', 0, a.shape)
    o:setField('collisionType', 0, 'None') -- procedural building colliders remain in the road pipeline
    o:setField('decalType', 0, 'None')
    o:setField('dynamic', 0, 'true')
    o:setField('castShadows', 0, 'false')
    o:setPosRot(a.x, a.y, a.z, a.qx, a.qy, a.qz, a.qw)
    if o:registerObject('') == false then error('landmark registration failed: ' .. a.id) end
    scenetree.MissionGroup:addObject(o)
    a.objectId = o:getID()
    actors[a.id] = a
  end)
  if ran and not ok then
    if o then pcall(function() o:delete() end) end
    failed[a.id] = true
  end
end

-- Called inside core's existing atomic rebase phase, before the next rendered world frame.
-- Queued actors also move, so a late import never uses old coordinates or resets to the origin.
function M.transform(xf, aid)
  local bx, by, bz, bw = xf:beamngQuat()
  local seen = {}
  local function transform(a)
    if seen[a] then return end
    seen[a] = true
    a.x, a.y, a.z = xf:apply(a.x, a.y, a.z)
    a.qx, a.qy, a.qz, a.qw = geodesy.quatMul(a.qx, a.qy, a.qz, a.qw, bx, by, bz, bw)
    move(a)
  end
  for _, a in pairs(actors) do transform(a) end
  for _, a in pairs(wanted) do transform(a) end
  anchorId = aid
end

function M.clear()
  releaseCredit()
  for id in pairs(actors) do dispose(id) end
  actors, wanted, failed, anchorId = {}, {}, {}, nil
end
function M.reset() M.clear(); materials, materialCount = {}, 0 end
function M.state() return actors, wanted, materials, failed end
return M
