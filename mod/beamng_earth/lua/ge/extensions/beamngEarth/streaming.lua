-- Visual work priorities; collision and terrain-follow requests never depend on the camera.
local M = {}
M.DRIVE_RADIUS, M.DRIVE_LEAD = 150, 6 -- metres, seconds (537 m ahead at 200 mph)
M.FRAME_MS, M.MIN_SCALE = 1000 / 60, 0.25

-- Respect an intentional foreground frame cap (e.g. 30 FPS on a handheld). Uncapped games
-- use 60 FPS as the controller's target; background-limiter samples are excluded by core.
function M.frameTarget(settings)
  if not settings or type(settings.getValue) ~= 'function' then return M.FRAME_MS end
  local ok, enabled = pcall(settings.getValue, 'fpsLimitEnabled')
  if not ok or enabled ~= true then return M.FRAME_MS end
  local got, value = pcall(settings.getValue, 'fpsLimit')
  local fps = got and tonumber(value)
  if fps and fps == fps and fps >= 5 and fps <= 1000 then return 1000 / fps end
  return M.FRAME_MS
end

-- Conservative driving corridor: turns keep a full ring around the vehicle. The Rust queue
-- classifier uses the same box; a distant free camera must not pull priority away from the car.
function M.classifier(cam, player)
  local lx, ly, hx, hy
  if player then
    local x, y = player.x, player.y
    local ex, ey = x + (player.vx or 0) * M.DRIVE_LEAD, y + (player.vy or 0) * M.DRIVE_LEAD
    local r = M.DRIVE_RADIUS
    lx, ly, hx, hy = math.min(x, ex) - r, math.min(y, ey) - r, math.max(x, ex) + r, math.max(y, ey) + r
  end
  local fx, fy = cam and cam.fx or 0, cam and cam.fy or 0
  local n = math.sqrt(fx * fx + fy * fy)
  local cosmax
  if cam and n > 0.2 then
    fx, fy = fx / n, fy / n
    local half = math.min(80, math.deg(math.atan(math.tan(math.rad(cam.fovDeg or 60) / 2) * 16 / 9)) + 15)
    cosmax = math.cos(math.rad(half))
  end
  -- Precompute camera angles/corridor once per queue scan, not once per pending tile.
  return function(inst)
    if lx and inst.x0 <= hx and inst.x1 >= lx and inst.y0 <= hy and inst.y1 >= ly then return 0 end
    if not cosmax then return 1 end
    local cx, cy = (inst.x0 + inst.x1) / 2 - cam.x, (inst.y0 + inst.y1) / 2 - cam.y
    local d = math.sqrt(cx * cx + cy * cy)
    if d <= inst.x1 - inst.x0 or (cx * fx + cy * fy) / d >= cosmax then return 1 end
    return 2 -- warm reserve, never removed solely because the camera turned
  end
end

function M.priority(inst, cam, player) return M.classifier(cam, player)(inst) end

-- Reduce optional work quickly, restore it slowly. A single hitch or alt-tab cannot change the
-- quality tier. Loading/physics commits are excluded by the caller. CPU/GPU names are not a
-- reliable measure of spare capacity while BeamNG is running.
function M.adjust(scale, smooth, ms, frameMs)
  if not ms or ms ~= ms or ms < 1 or ms > 250 then return scale, smooth end
  local dt = math.min(ms / 1000, 0.1)
  smooth = smooth and smooth + (1 - math.exp(-dt / 0.5)) * (ms - smooth) or ms
  frameMs = frameMs or M.FRAME_MS
  if smooth > frameMs * 1.2 then scale = math.max(M.MIN_SCALE, scale - dt * 0.5)
  elseif smooth < frameMs * 1.05 then scale = math.min(1, scale + dt * 0.05) end
  return scale, smooth
end

return M
