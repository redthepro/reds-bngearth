-- Forest lamp models do not contain scene lights. Attach a bounded set of night lights to the
-- fittings that trees.lua actually shows, using the game's solar day/night state.
local M = { MAX_LIGHTS = 48, RANGE_M = 180, FADE_M = 120, INTERVAL_MS = 250, CREATE_PER_UPDATE = 4 }
local clock = require('beamngEarth/stopwatch').clock()
local last = -math.huge
local lamps, active = {}, {}
-- Lens centres measured from the stock shapes' glass geometry, in their local coordinates.
local lenses = { streetlight = { 2.7113, 0, 7.954 }, polelamp = { -2.6759, 0, 0.4351 } }

local function dispose(key)
  local id = active[key]
  local obj = id and scenetree.findObjectById(id)
  if obj then obj:delete() end
  active[key] = nil
end

function M.add(key, row, group, scale)
  local lens = lenses[group]
  if not lens then return end
  local a, s = math.rad(row[5] or 0), scale or 1
  local x, y = lens[1] * s, lens[2] * s
  lamps[key] = { x = row[2] + x * math.cos(a) - y * math.sin(a),
    y = row[3] + x * math.sin(a) + y * math.cos(a), z = row[4] + lens[3] * s }
end

function M.remove(key)
  lamps[key] = nil
  dispose(key)
end

function M.isNight()
  if core_environment and core_environment.getLightState then
    local state = core_environment.getLightState()
    if state and state.isNight ~= nil then return state.isNight == true end
  end
  -- Older engine builds: use the actual sun elevation when available, then the noon-zero clock.
  local sky = scenetree.findClassObjects('ScatterSky')
  local obj = sky and sky[1] and scenetree.findObject(sky[1])
  if obj and tonumber(obj.elevation) then return tonumber(obj.elevation) < 0 end
  local names = scenetree.findClassObjects('TimeOfDay')
  obj = names and names[1] and scenetree.findObject(names[1])
  local time = obj and tonumber(obj.time)
  return time ~= nil and time % 1 > 0.25 and time % 1 < 0.75
end

function M.update(player, view)
  local now = clock()
  if now - last < M.INTERVAL_MS then return end
  last = now
  local eye = view or player
  if not eye or not M.isNight() then
    for key in pairs(active) do dispose(key) end
    return
  end
  local nearest = {}
  for key, p in pairs(lamps) do
    local d = math.sqrt((p.x - eye.x)^2 + (p.y - eye.y)^2 + (p.z - (eye.z or p.z))^2)
    if d < M.RANGE_M then nearest[#nearest + 1] = { key = key, p = p, d = d } end
  end
  table.sort(nearest, function(a, b) return a.d == b.d and a.key < b.key or a.d < b.d end)
  local wanted = {}
  for i = 1, math.min(#nearest, M.MAX_LIGHTS) do wanted[nearest[i].key] = true end
  for key in pairs(active) do if not wanted[key] then dispose(key) end end
  local budget = M.CREATE_PER_UPDATE
  for i = 1, math.min(#nearest, M.MAX_LIGHTS) do
    local lt = nearest[i]
    local obj = active[lt.key] and scenetree.findObjectById(active[lt.key])
    if not obj and budget > 0 then
      obj = createObject('PointLight')
      if obj then
        obj.canSave = false
        obj:setField('radius', 0, '18')
        obj:setField('color', 0, '1 0.85 0.65 1')
        obj:setField('castShadows', 0, 'false')
        -- We manage streaming and dusk locally; no repeated full-scene environment rescans.
        obj:setField('nightLight', 0, '0')
        obj:setField('isEnabled', 0, 'true')
        obj:registerObject('')
        scenetree.MissionGroup:addObject(obj)
        active[lt.key] = obj:getID()
        budget = budget - 1
      end
    end
    if obj then
      local fade = math.min(1, (M.RANGE_M - lt.d) / (M.RANGE_M - M.FADE_M))
      obj:setField('intensity', 0, tostring(9000 * fade))
      obj:setPosition(vec3(lt.p.x, lt.p.y, lt.p.z))
      if obj.setLightEnabled then obj:setLightEnabled(true) end
      if obj.postApply then obj:postApply() end
    end
  end
end

function M.transform(xf)
  for key, p in pairs(lamps) do
    p.x, p.y, p.z = xf:apply(p.x, p.y, p.z)
    local obj = active[key] and scenetree.findObjectById(active[key])
    if obj then obj:setPosition(vec3(p.x, p.y, p.z)) end
  end
  last = -math.huge
end

function M.clear()
  for key in pairs(active) do dispose(key) end
  lamps, active, last = {}, {}, -math.huge
end

return M
