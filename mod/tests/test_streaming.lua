local dir = (arg[0]:match('^(.*)[/\\]') or '.')
local T = dofile(dir .. '/testlib.lua')
local S = dofile(dir .. '/stubs.lua')
local streaming = require('beamngEarth/streaming')
local far = require('beamngEarth/farfield')
local governor = require('beamngEarth/governor')

local cam = { x = 0, y = 0, fx = -1, fy = 0, fovDeg = 60 }
local player = { x = 0, y = 0, vx = 89.408, vy = 0 }
local function bounds(x) return { x0 = x, y0 = 0, x1 = x + 256, y1 = 256 } end
T.check(streaming.priority(bounds(512), cam, player) == 0, '200 mph driving corridor wins while looking backwards')
T.check(streaming.priority(bounds(-1024), cam, player) == 1, 'camera view is second')
T.check(streaming.priority(bounds(1024), cam, player) == 2, 'off-screen area is warm reserve')
cam.fx = 1
T.check(streaming.priority(bounds(1024), cam, player) == 1, 'camera turn promotes a warm tile immediately')
T.check(streaming.priority(bounds(-1024), nil, nil) == 1, 'missing camera preserves distance-based loading')
T.check(streaming.priority(bounds(-128), cam, player) == 0, 'nearby ground remains critical in every direction')
T.check(streaming.priority(bounds(512), nil, player) == 0, 'missing camera retains the driving corridor')

local fpsSettings = { fpsLimitEnabled = true, fpsLimit = 30 }
local settingsStub = { getValue = function(k) return fpsSettings[k] end }
T.near(streaming.frameTarget(settingsStub), 1000 / 30, 1e-6, 'intentional 30 FPS cap sets the frame target')
fpsSettings.fpsLimitEnabled = false
T.near(streaming.frameTarget(settingsStub), 1000 / 60, 1e-6, 'disabled limiter uses the normal target')
fpsSettings.fpsLimitEnabled, fpsSettings.fpsLimit = true, 'invalid'
T.near(streaming.frameTarget(settingsStub), 1000 / 60, 1e-6, 'bad limiter value cannot break streaming')

local scale, smooth = 1, nil
scale, smooth = streaming.adjust(scale, smooth, 1000)
T.check(scale == 1 and smooth == nil, 'alt-tab sample ignored')
for _ = 1, 600 do scale, smooth = streaming.adjust(scale, smooth, 33.33) end
T.near(scale, streaming.MIN_SCALE, 1e-6, 'sustained slow frames throttle optional work')
for _ = 1, 60 do scale, smooth = streaming.adjust(scale, smooth, 12) end
T.check(scale < 0.3, 'recovery is gradual rather than oscillating')
for _ = 1, 2400 do scale, smooth = streaming.adjust(scale, smooth, 12) end
T.near(scale, 1, 1e-6, 'sustained headroom restores full streaming')

-- Verify the real import queue, not only its scoring helper. One shape per update.
far.setNearVisual('mesh')
far.switchAnchor(1, false, { cx = 0, cy = 0 })
local function tile(i)
  return { anchorId = 1, key = '0_' .. i .. '_0', level = 0, pos = { i * 256, 0, 0 }, err = 0,
    shape = '/temp/test_stream_' .. i .. '.cdae', state = 'group' }
end
far.onTile(tile(4)); far.onTile(tile(-4)); far.onTile(tile(2))
T.check(far.queuedCount() == 3, 'pose backpressure counts queued engine imports')
local g = { clock = function() return 0 end, scale = 1, frame = 1 }
function g:try(kind) return kind ~= 'cdae' or not self.imported end
function g:charge(kind) if kind == 'cdae' then self.imported = true end end
function g:chargeMotion() end
local function step()
  g.imported = false
  far.update(g, 1 / 60, cam, player)
end
cam.fx = -1; step()
T.check(far.queuedCount() == 2, 'completed imports leave the backpressure count')
T.check(far._pending('0_2_0').loaded and not far._pending('0_-4_0').loaded,
  'actual import loads driving ground before the backwards camera view')
cam.fx = 1; step()
T.check(far._pending('0_4_0').loaded and not far._pending('0_-4_0').loaded,
  'turning reprioritizes the existing queue without a download or removal')
g.scale, g.frame = 0.25, 1; step()
T.check(not far._pending('0_-4_0').loaded, 'under pressure warm imports wait')
g.frame = 4; step()
T.check(far._pending('0_-4_0').loaded, 'warm imports still progress under sustained pressure')
far.clear()
far.switchAnchor(2, false, { cx = 0, cy = 0 })
local rear, ahead = tile(-4), tile(2)
rear.anchorId, ahead.anchorId = 2, 2
far.onTile(rear); far.onTile(ahead)
g.scale, g.frame, g.imported = 1, 1, false
far.update(g, 1 / 60, nil, player)
T.check(far._pending('0_2_0').loaded and not far._pending('0_-4_0').loaded,
  'after anchor switch, missing camera still loads driving ground first')
far.configureReserve({ small = true })
T.check(far.SPARE_MAX == 64 and far.SPARE_S == 30, 'low memory machines retain a smaller warm reserve')
far.configureReserve({ small = false })
T.check(far.SPARE_MAX == 400 and far.SPARE_S == 120, 'desktop reserve restored')

local now = 0
local gov = governor.new({ clock = function() return now end })
for _ = 1, 600 do now = now + 33.33; gov:beginFrame(true) end
T.check(gov:targetNow() < 1 and gov:snapshot().scale == streaming.MIN_SCALE,
  'real governor reduces its import budget on slow machines')
T.check(gov:requestHeavy(true), 'urgent physics commit remains available despite visual throttling')
gov:setLoading(true)
T.check(gov:targetNow() == governor.LOADING_TARGET_MS, 'initial loading retains its own budget')

local capped = governor.new({ clock = function() return now end })
capped:setFrameContext(1000 / 30, true)
for _ = 1, 600 do now = now + 1000 / 30; capped:beginFrame(true) end
T.near(capped.scale, 1, 1e-6, 'intentional 30 FPS does not throttle the real governor')
capped:setFrameContext(1000 / 60, false)
for _ = 1, 300 do now = now + 100; capped:beginFrame(true) end
T.near(capped.scale, 1, 1e-6, 'background frame cap does not throttle foreground streaming')
capped:setFrameContext(1000 / 60, true)
for _ = 1, 30 do now = now + 1000 / 60; capped:beginFrame(true) end
now = now + 200; capped:beginFrame(true)
for _ = 1, 120 do now = now + 1000 / 60; capped:beginFrame(true) end
T.near(capped.scale, 1, 1e-6, 'one 200 ms hitch is rejected instead of causing sustained false overload')

-- Roads share the governor and run before far imports. Even at the visual floor a collidable
-- road mesh accumulates enough tokens; the adaptation must never make that lane zero-budget.
capped.scale, capped.tokens, capped.b = streaming.MIN_SCALE, 0, 0
local roadLoaded = false
for _ = 1, 30 do
  now = now + 1000 / 30; capped:beginFrame(true)
  if capped:try('roadWalk') then capped:charge('roadWalk', 3); roadLoaded = true; break end
end
T.check(roadLoaded, 'road collision mesh still gets funding at minimum streaming scale')
T.done('test_streaming')
