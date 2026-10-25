local dir = (arg[0]:match('^(.*)[/\\]') or '.')
local T = dofile(dir .. '/testlib.lua')
local S = dofile(dir .. '/stubs.lua')
local lights = require('beamngEarth/streetlights')
lights.INTERVAL_MS = 0
local night = false
core_environment = { getLightState = function() return { isNight = night } end }
local player = { x = 0, y = 0, z = 0 }
local function live() return S.liveOf('PointLight') end

lights.add('street', { 0, 10, 20, 3, 90, 1, 9, 0 }, 'streetlight', 1)
lights.add('pole', { 0, 0, 0, 8, 0, 1, 1.1, 0 }, 'polelamp', 1)
lights.add('tree', { 0, 0, 0, 0, 0, 1, 9, 0 }, 'deciduous', 1)
lights.update(player)
T.check(#live() == 0, 'daylight: no scene lights')
night = true
lights.update(player)
T.check(#live() == 2, 'night: both lamp types light up, trees do not')
local street, pole
for _, o in ipairs(live()) do
  if o.pos.x > 5 then street = o else pole = o end
  T.check(o.fields.castShadows == 'false' and tonumber(o.fields.intensity) > 0, 'lit without expensive shadows')
end
T.near(street.pos.x, 10, 1e-6, 'rotated lens x')
T.near(street.pos.y, 22.7113, 1e-6, 'rotated lens y')
T.near(street.pos.z, 10.954, 1e-6, 'lens height, not the pole origin')
T.near(pole.pos.x, -2.6759, 1e-6, 'utility lamp projects from the opposite local side')
T.near(pole.pos.z, 8.4351, 1e-6, 'utility lamp row is already at mounting height')

local made = S.calls.createObject_PointLight
lights.update(player)
T.check(S.calls.createObject_PointLight == made, 'stationary: reuse active lights')
lights.transform({ apply = function(_, x, y, z) return x + 1000, y - 500, z + 2 end })
T.near(street.pos.x, 1010, 1e-6, 'rebase moves the existing light immediately')
lights.update({ x = 1000, y = -500, z = 2 })
T.check(S.calls.createObject_PointLight == made, 'rebase keeps the same objects')
lights.remove('pole')
T.check(#live() == 1, 'dropped fitting drops its light')
night = false
lights.update(player)
T.check(#live() == 0, 'dawn removes illumination')
lights.clear()
night = true

for i = 1, 100 do lights.add('light' .. i, { 0, i, 0, 0, 0, 1, 9, 0 }, 'streetlight', 1) end
lights.update(player)
T.check(#live() == lights.CREATE_PER_UPDATE, 'creation is spread over updates')
for i = 1, 30 do lights.update(player) end
T.check(#live() == lights.MAX_LIGHTS, 'dense streets respect the active-light cap')
lights.update(player, { x = 5000, y = 5000 })
T.check(#live() == 0, 'camera away: distant lights culled')
lights.update({ x = 5000, y = 5000 }, player)
T.check(#live() > 0, 'free camera gets nearby lights')
lights.clear()
lights.update(player)
T.check(#live() == 0, 'clear removes sources and objects')

lights.add('fade', { 0, 150, 0, 0, 0, 1, 9, 0 }, 'streetlight', 1)
lights.update(player)
T.check(tonumber(live()[1].fields.intensity) < 9000, 'distance fades before the range cutoff')
lights.clear()
core_environment = nil
local tod = S.newObject('TimeOfDay'); tod:registerObject('tod')
tod.time = 0.5
T.check(lights.isNight(), 'older engine fallback: midnight')
tod.time = 0
T.check(not lights.isNight(), 'older engine fallback: noon')
T.done('test_streetlights')
