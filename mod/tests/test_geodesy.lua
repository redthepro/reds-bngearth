-- luajit mod/tests/test_geodesy.lua
local T = dofile((arg[0]:match('^(.*)[/\\]') or '.') .. '/testlib.lua')
local g = require('beamngEarth/geodesy')

local A, B = 6378137.0, 6356752.314245 -- WGS84 semi-axes

-- known ECEF vectors
do
  local x, y, z = g.geodeticToEcef(0, 0, 0)
  T.near(x, A, 1e-6, 'ecef(0,0,0).x'); T.near(y, 0, 1e-6, 'ecef(0,0,0).y'); T.near(z, 0, 1e-6, 'ecef(0,0,0).z')
  x, y, z = g.geodeticToEcef(0, 90, 0)
  T.near(x, 0, 1e-6, 'ecef(0,90,0).x'); T.near(y, A, 1e-6, 'ecef(0,90,0).y')
  x, y, z = g.geodeticToEcef(90, 0, 0)
  T.near(x, 0, 1e-6, 'ecef(90,0,0).x'); T.near(z, B, 1e-5, 'ecef(90,0,0).z')
  x, y, z = g.geodeticToEcef(-90, 0, 100)
  T.near(z, -(B + 100), 1e-5, 'ecef(-90,0,100).z')
end

-- geodetic -> ECEF -> geodetic round trips (incl. poles, antimeridian, below sea level, LEO height)
local points = {
  { 36.5785, -118.2923, 0 }, { 36.5785, -118.2923, 4421 }, { 0, 0, 0 }, { 0, 180, 10 }, { 0, -179.999999, 0 },
  { 89.999999, 45, 0 }, { -89.999999, -120, 2835 }, { 90, 0, 0 }, { -90, 0, 0 },
  { 51.4779, -0.0015, 45 }, { -33.8568, 151.2153, -400 }, { 27.9881, 86.9250, 8848.86 }, { 45, 45, 100000 },
}
for _, p in ipairs(points) do
  local lat, lon, h = g.ecefToGeodetic(g.geodeticToEcef(p[1], p[2], p[3]))
  local tag = string.format('round trip (%g, %g, %g)', p[1], p[2], p[3])
  T.near(lat, p[1], 1e-9, tag .. ' lat')
  if math.abs(p[1]) < 90 then
    local dlon = (lon - p[2] + 540) % 360 - 180
    T.near(dlon, 0, 1e-9, tag .. ' lon')
  end
  T.near(h, p[3], 1e-6, tag .. ' h')
end

-- ENU: origin, axes, round trips
for _, a in ipairs({ { 36.5785, -118.2923 }, { 0, 0 }, { 89.9999, 10 }, { -89.9999, -170 }, { 64.1466, -21.9426 } }) do
  local anc = g.newAnchor(a[1], a[2])
  local tag = string.format('anchor (%g, %g)', a[1], a[2])
  local e, n, u = anc:geodeticToEnu(a[1], a[2], 0)
  T.near(e, 0, 1e-6, tag .. ' origin e'); T.near(n, 0, 1e-6, tag .. ' origin n'); T.near(u, 0, 1e-6, tag .. ' origin u')
  e, n, u = anc:geodeticToEnu(a[1], a[2], 1234.5)
  T.near(u, 1234.5, 1e-6, tag .. ' up is the ellipsoid normal')
  T.near(math.abs(e) + math.abs(n), 0, 1e-6, tag .. ' up has no horizontal part')
  for _, v in ipairs({ { 0, 0, 0 }, { 1200, -900, 35 }, { -40000, 40000, -3000 }, { 4096, 0, 8000 } }) do
    local e2, n2, u2 = anc:ecefToEnu(anc:enuToEcef(v[1], v[2], v[3]))
    T.near(e2, v[1], 1e-6, tag .. ' enu round trip e'); T.near(n2, v[2], 1e-6, tag .. ' enu round trip n')
    T.near(u2, v[3], 1e-6, tag .. ' enu round trip u')
    local lat, lon, h = anc:enuToGeodetic(v[1], v[2], v[3])
    e2, n2, u2 = anc:geodeticToEnu(lat, lon, h)
    T.near(e2, v[1], 1e-5, tag .. ' enu->geodetic->enu e'); T.near(u2, v[3], 1e-5, tag .. ' enu->geodetic->enu u')
  end
end

-- north is +y: a point slightly north of the anchor has n > 0, e ~ 0
do
  local anc = g.newAnchor(36.5785, -118.2923)
  local e, n = anc:geodeticToEnu(36.5885, -118.2923, 0)
  T.check(n > 1100 and n < 1112, 'north is +y (n=' .. n .. ')'); T.near(e, 0, 1e-6, 'north has e=0')
  e, n = anc:geodeticToEnu(36.5785, -118.2823, 0)
  T.check(e > 890 and e < 900, 'east is +x (e=' .. e .. ')')
end

-- curvature: the ellipsoid surface 10 km east of the anchor sits ~d^2/(2R) = 7.85 m lower
local function surfaceDrop(anc, e, n)
  local lat, lon = anc:enuToGeodetic(e, n, 0)
  return anc:geodeticToEnu(lat, lon, 0)
end
for _, a in ipairs({ { 0, 0 }, { 36.5785, -118.2923 }, { 47, 8 } }) do
  local anc = g.newAnchor(a[1], a[2])
  local e, n, u = surfaceDrop(anc, 10000, 0)
  T.near(u, -7.85, 0.05, string.format('10 km east drop at lat %g', a[1]))
  T.near(e, 10000, 0.1, '10 km east stays ~10 km east')
  local _, _, u1 = surfaceDrop(anc, 0, 1000)
  T.near(u1, -0.0785, 0.002, '1 km north drop')
  local _, _, u100 = surfaceDrop(anc, 100000, 0)
  T.near(u100, -785, 6, '100 km east drop')
end

-- rebase: old ENU -> new ENU and back is exact; the new anchor's origin maps to (0,0,0)
do
  local old = g.newAnchor(36.5785, -118.2923)
  local lat, lon = old:enuToGeodetic(1200, 300, 0)
  local new = g.newAnchor(lat, lon)
  local e, n, u = old:toFrame(new, 1200, 300, 0)
  T.near(e, 0, 1e-6, 'rebase origin e'); T.near(n, 0, 1e-6, 'rebase origin n')
  -- the geodetic point of the new origin is at h=0 by construction, but ENU_old(1200,300,0) is
  -- above the ellipsoid by the curvature drop (~0.12 m)
  T.near(u, (1200 ^ 2 + 300 ^ 2) / (2 * 6371000), 0.005, 'rebase origin u = curvature lift')
  local x, y, z = old:toFrame(new, 1250.5, 290.25, 1530.0)
  local e2, n2, u2 = new:toFrame(old, x, y, z)
  T.near(e2, 1250.5, 1e-6, 'rebase round trip e'); T.near(n2, 290.25, 1e-6, 'rebase round trip n')
  T.near(u2, 1530.0, 1e-6, 'rebase round trip u')
  -- near the ground the move is ~ a pure translation; at 1530 m the ignored d/R rotation
  -- (~1.9e-4 rad) shows up as ~0.3 m horizontally, which is why vehicles are mapped exactly
  x, y = old:toFrame(new, 1250.5, 290.25, 30.0)
  T.near(x, 50.5, 0.02, 'rebased point e ~ shifted'); T.near(y, -9.75, 0.02, 'rebased point n ~ shifted')
end

-- rebase transform: p_new = R*p_old + t (V1_1_SPEC 1b)
do
  local old = g.newAnchor(36.5785, -118.2923)
  for _, d in ipairs({ { 10000, 0 }, { 0, -10000 }, { 7071, 7071 }, { 2000, 500 }, { -25000, 3000 } }) do
    local lat, lon = old:enuToGeodetic(d[1], d[2], 0)
    local new = g.newAnchor(lat, lon)
    local xf = old:transformTo(new)
    local tag = string.format('xf (%g, %g)', d[1], d[2])
    -- R is orthonormal with det +1
    local r = xf.r
    for i = 0, 2 do
      for j = 0, 2 do
        local dot = r[i * 3 + 1] * r[j * 3 + 1] + r[i * 3 + 2] * r[j * 3 + 2] + r[i * 3 + 3] * r[j * 3 + 3]
        T.near(dot, i == j and 1 or 0, 1e-12, tag .. ' R orthonormal')
      end
    end
    local det = r[1] * (r[5] * r[9] - r[6] * r[8]) - r[2] * (r[4] * r[9] - r[6] * r[7]) + r[3] * (r[4] * r[8] - r[5] * r[7])
    T.near(det, 1, 1e-12, tag .. ' det R')
    -- same result as the ECEF path, for points near and very far (far tiles reach 80 km)
    for _, p in ipairs({ { d[1], d[2], 35 }, { 0, 0, 0 }, { 80000, -60000, -500 }, { d[1] + 3, d[2] - 2, 1500 } }) do
      local ex, ey, ez = old:toFrame(new, p[1], p[2], p[3])
      local x, y, z = xf:apply(p[1], p[2], p[3])
      T.near(x, ex, 1e-6, tag .. ' apply x'); T.near(y, ey, 1e-6, tag .. ' apply y'); T.near(z, ez, 1e-6, tag .. ' apply z')
      -- round trip through the inverse
      local bx, by, bz = xf:inverse():apply(x, y, z)
      T.near(bx, p[1], 1e-6, tag .. ' inverse x'); T.near(by, p[2], 1e-6, tag .. ' inverse y'); T.near(bz, p[3], 1e-6, tag .. ' inverse z')
    end
    -- the new origin is the old point (d, 0) on the ellipsoid
    local ox, oy = xf:apply(d[1], d[2], 0)
    T.near(ox, 0, 1e-6, tag .. ' new origin x'); T.near(oy, 0, 1e-6, tag .. ' new origin y')
    -- the up axis tilts by ~ d / R_earth (0.09 deg at 10 km) ...
    local dist = math.sqrt(d[1] ^ 2 + d[2] ^ 2)
    local tilt = math.acos(math.min(1, r[9]))
    T.near(tilt, dist / 6371000, 0.01 * dist / 6371000, tag .. ' tilt ~ d/R')
    -- ... and on an east-west move the frame also yaws by the meridian convergence dlon*sin(lat)
    -- (0.067 deg for 10 km at 36.6 N), so the whole rotation is sqrt(tilt^2 + yaw^2)
    local yaw = math.rad(lon - old.lon) * math.sin(math.rad((lat + old.lat) / 2))
    T.near(xf:angle(), math.sqrt(tilt ^ 2 + yaw ^ 2), 0.01 * xf:angle(), tag .. ' angle = tilt + convergence')
    -- quaternion = R (standard convention), and the BeamNG quaternion rotates like R with
    -- BeamNG's own quat*vec3 formula (mathlib.lua LuaQuat.__mul)
    local qx, qy, qz, qw = xf:quat()
    T.near(qx * qx + qy * qy + qz * qz + qw * qw, 1, 1e-12, tag .. ' |q| = 1')
    local bqx, bqy, bqz, bqw = xf:beamngQuat()
    for _, v in ipairs({ { 1, 0, 0 }, { 0, 1, 0 }, { 0, 0, 1 }, { 3, -4, 12 } }) do
      local rx, ry, rz = xf:rotate(v[1], v[2], v[3])
      local bx, by, bz = g.beamngRotate(bqx, bqy, bqz, bqw, v[1], v[2], v[3])
      T.near(bx, rx, 1e-12, tag .. ' beamng quat x'); T.near(by, ry, 1e-12, tag .. ' beamng quat y')
      T.near(bz, rz, 1e-12, tag .. ' beamng quat z')
    end
    -- composing a tile's rotation then R: (a*b)*v == b*(a*v) for BeamNG quaternions
    local ax, ay, az, aw = 0, 0, math.sin(0.35), math.cos(0.35) -- some yaw
    local cx, cy, cz, cw = g.quatMul(ax, ay, az, aw, bqx, bqy, bqz, bqw)
    local v1x, v1y, v1z = g.beamngRotate(ax, ay, az, aw, 2, 1, 0.5)
    local wx, wy, wz = g.beamngRotate(bqx, bqy, bqz, bqw, v1x, v1y, v1z)
    local ux, uy, uz = g.beamngRotate(cx, cy, cz, cw, 2, 1, 0.5)
    T.near(ux, wx, 1e-12, tag .. ' quat compose x'); T.near(uy, wy, 1e-12, tag .. ' quat compose y')
    T.near(uz, wz, 1e-12, tag .. ' quat compose z')
  end
  -- far-field consequence of dropping R at 10 km: an 80 km tile would sit ~125 m off vertically
  local lat, lon = old:enuToGeodetic(10000, 0, 0)
  local xf = old:transformTo(g.newAnchor(lat, lon))
  local _, _, z = xf:apply(80000, 0, 0)
  local _, _, zt = xf:apply(0, 0, 0)
  T.check(math.abs((z - zt) - 0) > 100, 'R matters at 80 km (' .. string.format('%.1f', z - zt) .. ' m)')
end

-- matToQuat covers all of Shepperd's branches (180-degree turns about each axis)
for _, c in ipairs({
  { r = { 1, 0, 0, 0, -1, 0, 0, 0, -1 }, q = { 1, 0, 0, 0 } },
  { r = { -1, 0, 0, 0, 1, 0, 0, 0, -1 }, q = { 0, 1, 0, 0 } },
  { r = { -1, 0, 0, 0, -1, 0, 0, 0, 1 }, q = { 0, 0, 1, 0 } },
  { r = { 0, -1, 0, 1, 0, 0, 0, 0, 1 }, q = { 0, 0, math.sqrt(0.5), math.sqrt(0.5) } }, -- +90 deg about z
}) do
  local x, y, z, w = g.matToQuat(c.r)
  local s = (x * c.q[1] + y * c.q[2] + z * c.q[3] + w * c.q[4]) < 0 and -1 or 1
  T.near(s * x, c.q[1], 1e-12, 'matToQuat x'); T.near(s * y, c.q[2], 1e-12, 'matToQuat y')
  T.near(s * z, c.q[3], 1e-12, 'matToQuat z'); T.near(s * w, c.q[4], 1e-12, 'matToQuat w')
end

-- true north at a point of the frame (the minimap's compass)
do
  local a = g.newAnchor(39.2834, -76.5934)
  local x, y = a:northAt(0, 0)
  T.near(x, 0, 1e-9, 'northAt the anchor: +y'); T.near(y, 1, 1e-9, 'northAt the anchor: +y')
  x, y = a:northAt(15000, 0)
  local turn = math.deg(math.atan2(-x, y))
  T.check(x < 0, 'east of the anchor, north leans west (meridians converge)')
  T.near(turn, math.deg(15000 * math.tan(math.rad(39.2834)) / 6371000), 0.01, 'the turn is d tan(lat) / R')
  T.near(x * x + y * y, 1, 1e-12, 'northAt is a unit direction')
  local p = g.newAnchor(89.99, 20)
  local pe, pn = p:geodeticToEnu(90, 0, 0)
  for _, q in ipairs({ { 0, 3000 }, { 800, 1500 }, { -2000, -500 } }) do
    local nx, ny = p:northAt(q[1], q[2])
    local dx, dy = pe - q[1], pn - q[2]
    local l = math.sqrt(dx * dx + dy * dy)
    T.near(nx * dx / l + ny * dy / l, 1, 1e-6, 'near the North Pole, north is toward the pole')
  end
  local s = g.newAnchor(-89.99, 0)
  local se, sn = s:geodeticToEnu(-90, 0, 0)
  local nx, ny = s:northAt(0, -3000)
  T.check(nx * (0 - se) + ny * (-3000 - sn) > 0, 'past the South Pole, north points away from it')
end

do -- teleport anchors on their grid (snapAnchor): the same cell, the same anchor; the car within ~0.8 km
  local a1, o1 = g.snapAnchor(39.28071, -76.56112)
  local a2, o2 = g.snapAnchor(39.27951, -76.55891)
  T.check(a1 == a2 and o1 == o2, 'two spots in one grid cell share an anchor')
  T.near(a1, 39.28, 1e-9, 'latitude on the 0.01 deg grid')
  for _, p in ipairs({ { 39.28449, -76.56499 }, { 0.004, 179.996 }, { 64.1, -21.9 }, { -78.3, 166.7 }, { 84.9, 10 } }) do
    local la, lo = g.snapAnchor(p[1], p[2])
    local e, n = g.newAnchor(la, lo):geodeticToEnu(p[1], p[2], 0)
    T.check(math.sqrt(e * e + n * n) < 900, string.format('%.3f, %.3f lands within 900 m of its anchor', p[1], p[2]))
    T.check(lo >= -180 and lo < 180, 'longitude kept in [-180, 180)')
  end
  local la, lo = g.snapAnchor(88.5, 12.345)
  T.check(la == 88.5 and lo == 12.345, 'not snapped near the poles')
end

T.done('test_geodesy')
