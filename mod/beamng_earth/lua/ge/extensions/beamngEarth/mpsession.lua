-- BeamMP sessions, with BeamNG.Earth's server plugin (beammp/Server/BeamNGEarth/main.lua; the user, 2026-10-22:
-- BeamMP support). BeamMP sends every car's position in its driver's world coordinates, metres from the driver's own
-- anchor; mpanchor.lua (Thai-v4's PR #88) converts the other players' positions into ours, given their anchors. Each
-- POSE_S (and at once when our anchor changes) this sends the plugin our anchor and place (bngePose); it answers with
--   - bngeAnchors { pid -> { aLat, aLon, lat, lon, name, h } }: every player's anchor (to mpanchor), place, name and
--     heading (others(): the world map's arrows); the cars of a
--     player whose anchor is not known (no BeamNG.Earth, or not heard yet) are deactivated here: they would stand in
--     the wrong place;
--   - bngeGoTo { lat, lon }: on joining, a teleport to where the others are.
-- Damage: BeamMP sends parts that break off (break groups) but not dents; its own full-damage message (nodesVE.getNodes:
-- every node and beam, applied on the other players' copies by its nodesGE) is never sent. This sends it for our own
-- cars when another player comes within DAMAGE_NEAR_M (they see the car as it is) and DAMAGE_SETTLE_S after our car's
-- damage changed by DAMAGE_STEP while one is near.
-- Shared generation (the user, 2026-10-22: "3 players in Utah and 1 in Italy ... the Utah players split generation
-- between each other"): players near each other converge onto one anchor (pickAnchor: within ADOPT_M, the one most of
-- them use; a teleport next to them lands on it, core.teleportTo / adoptAnchor; otherwise a smooth rebaseTo), and
-- our world server serves its group (the "share" message, on the port the plugin's bngeConfig names: its port and code
-- go out in our poses); the plugin sends
-- each player the others on its anchor (bngePeers), handed to the world server ("peers"), which splits the far tiles
-- among them (server/peerwork.py).
-- BeamMP's files are not changed: its client events, its own functions, and mpanchor's wrap of positionGE.handle.
local M = {}

M.PROTO = 2                 -- as the plugin's G.PROTO
M.POSE_S = 2
M.HIDE_S = 0.5
M.GOTO_OFFSET_M = 12        -- a joining player lands this far from the one it goes to (not on top of it)
M.DAMAGE_STEP = 50          -- (the game's damage figure, map.objects[id].damage)
M.DAMAGE_SETTLE_S = 2
M.DAMAGE_NEAR_M = 2000      -- a player this near sees our cars' damage (sendDamage)
M.STATUS_S = 5              -- the session's state to the log and STATUS_FILE this often
M.STATUS_FILE = '/temp/beamngEarth/mpsession_status.json'
M.CONFIG_WAIT_S = 10        -- the server's settings waited for this long before sharing without them (an older plugin
                            -- sends none)
M.ADOPT_M = 7500            -- another player's anchor this near is ours to share (half the rebase distance: no loop)
M.CONVERGE_S = 5            -- the anchor checked this often (converge)
M.SETTLE_S = 10             -- ... and not within this of our own anchor's last change

local handlers = false
local anchors = {}          -- tostring(server player id) -> { aLat, aLon, lat, lon } (bngeAnchors)
local deactivated = {}      -- game vehicle id -> true: deactivated here
local poseIn, hideIn = 0, 0
local joined = false        -- BeamMP joined and no pose sent yet: the first pose asks for the meeting place
local wentTo = false        -- the session's one bngeGoTo done
local camPlaced = false     -- the free camera put on the ground once (BeamMP joins without a car)
local lastPlayers = -1      -- (the log: the player count as last logged)
local stats = { anchors = 0, go = 0, poses = 0, converged = 0, peers = 0 }
local share = nil           -- { port, code } of our world server's group service (onShare); false: asked
local config = nil          -- the server's settings (bngeConfig { sharePort }), nil until they come
local configWait = 0        -- s waited for them
local convergeIn = 0
local anchorSince = 0       -- seconds our anchor has been the same (converge's settle)
local lastAnchorKey = nil
local statusIn = 5
local members = {}          -- tostring(pid) -> true: the other players within DAMAGE_NEAR_M at the last check
local sentDamage = {}       -- own game vehicle id -> the damage its state was last sent at
local damageSince = {}      -- own game vehicle id -> seconds its damage has differed from sentDamage

function M.key(aLat, aLon) return string.format('%.6f,%.6f', aLat, aLon) end

-- Pure: the anchor a player at (lat, lon) should share: of the players' anchors (bngeAnchors' table) within ADOPT_M of
-- it, the one the most of them use, ties to the lowest key; skip: a player id left out (ourselves, for a teleport).
-- -> aLat, aLon, players on it (nil: none in reach)
function M.pickAnchor(list, lat, lon, skip)
  local count, ll = {}, {}
  for pid, a in pairs(list) do
    local aLat, aLon = type(a) == 'table' and tonumber(a.aLat), type(a) == 'table' and tonumber(a.aLon)
    if pid ~= skip and aLat and aLon then
      local k = M.key(aLat, aLon)
      count[k], ll[k] = (count[k] or 0) + 1, { aLat, aLon }
    end
  end
  local best, bestN = nil, 0
  for k, n in pairs(count) do
    if M.dist(lat, lon, ll[k][1], ll[k][2]) <= M.ADOPT_M and (n > bestN or (n == bestN and k < best)) then
      best, bestN = k, n
    end
  end
  if best then return ll[best][1], ll[best][2], bestN end
  return nil
end

-- Pure: the remote vehicles to deactivate: owners { game vehicle id -> owner's server player id }, known (owner's id
-- -> true when its anchor is known). -> { game vehicle id -> true }
function M.toHide(owners, known)
  local out = {}
  for gid, owner in pairs(owners) do
    if not known(owner) then out[gid] = true end
  end
  return out
end

-- Metres between two latitude / longitude points (equirectangular: a few km).
function M.dist(lat1, lon1, lat2, lon2)
  local k = math.pi / 180
  local x = (lon2 - lon1) * k * math.cos((lat1 + lat2) / 2 * k)
  return 6371000 * math.sqrt(x * x + ((lat2 - lat1) * k) ^ 2)
end

-- Pure: should our car's full damage go out now? newcomer: a player came near; damage, sent: now and at the last
-- send (nil: never); settled: seconds it has differed. -> send, settled (updated)
function M.damageDue(newcomer, damage, sent, settled, dt)
  -- (a newcomer gets our car's full state only when it has damage to show: an undamaged car is the one BeamMP spawned
  -- there already, and every join sent every nearby player's every car to every client, 2026-10-23: "the game freezes
  -- when players join")
  if newcomer then return (damage or 0) >= M.DAMAGE_STEP, 0 end
  if not damage or math.abs(damage - (sent or 0)) < M.DAMAGE_STEP then return false, 0 end
  settled = (settled or 0) + dt
  return settled >= M.DAMAGE_SETTLE_S, settled
end

local function mp() return rawget(_G, 'MPVehicleGE') end
local function core() return rawget(_G, 'beamngEarth_core') end
local remoteOwners          -- (below; the test switch uses it)

-- A vehicle by its game id, as the game's vehicle class (setActive, queueLuaCommand; scenetree.findObjectById's
-- object has no setActive in the native Linux build: the session stopped at the first car to hide)
local function vehicle(gid)
  local get = rawget(_G, 'getObjectByID')
  local o = get and get(gid)
  if not o and be and be.getObjectByID then o = be:getObjectByID(gid) end
  o = o or scenetree.findObjectById(gid)
  return o and o.setActive and o or nil         -- (vehicles only)
end

-- Test switch (two BeamMP players, .claude/work/mp_two.sh): the game's start arguments -bngetest car,damage
-- spawn a car TEST_CAR_S after the first pose with none, damage it TEST_DAMAGE_S after that; the status file then
-- lists the other players' cars' broken beams (the damage they show here). 'switch': TEST_SWITCH_S after the car,
-- into another player's car (the game's vehicle switch) for TEST_SWITCH_HOLD_S, then back; 'perf': the frame times
-- (perfmon.lua) since the last status in the status file. 'away': TEST_AWAY_S after the car, a teleport 130 degrees
-- of longitude away (about California to Italy) for TEST_AWAY_HOLD_S, then back (a player across the world and back:
-- the other's copy of our car must survive). 'drive': TEST_DRIVE_S after the car, the game's AI drives it (traffic
-- mode) for the frame times while the world streams. 'goto' (with -bngegoto LAT,LON): TEST_GOTO_S after the first
-- pose, a teleport there (a fresh place: the shared generation's timing); -bngegotoat UNIXTIME: at that time instead
-- (two machines arriving together). 'map': TEST_MAP_S after the first pose, the world map opened (the other players'
-- arrows, for a screenshot).
M.TEST_CAR_S, M.TEST_DAMAGE_S = 5, 25
M.TEST_SWITCH_S, M.TEST_SWITCH_HOLD_S = 40, 30
M.TEST_AWAY_S, M.TEST_AWAY_HOLD_S = 40, 60
M.TEST_DRIVE_S = 20
M.TEST_GOTO_S = 3
M.TEST_MAP_S = 30
local test = {}
do
  local ok, args = pcall(function() return Engine.getStartingArgs() end)
  if ok and type(args) == 'table' then
    for i, a in ipairs(args) do
      if a == '-bngetest' and type(args[i + 1]) == 'string' then
        for w in args[i + 1]:gmatch('[^,]+') do test[w] = true end
      end
      if a == '-bngegoto' and type(args[i + 1]) == 'string' then
        local la, lo = args[i + 1]:match('^([%-%d%.]+),([%-%d%.]+)$')
        test.gotoLat, test.gotoLon = tonumber(la), tonumber(lo)
      end
      if a == '-bngegotoat' then test.gotoAt = tonumber(args[i + 1]) end
    end
  end
end
local testClock, testCarAt, testDamaged = 0, nil, false
local testOwnId = nil       -- (test) our test car's game id
local anchorRef = nil       -- (test) the anchor of the latest update
local switchLog = {}        -- (test 'switch') { { t, what, active, ownThere } }
local awayLog = {}          -- (test 'away') { { t, what, lat, lon } }
local lateGoTo = nil         -- (test 'late': the go-to held until TEST_LATE_S)
M.TEST_LATE_S = 45
M.TEST_DELREMOTE_S = 40
M.TEST_DROP_S, M.TEST_DROP_M = 40, 2500
local brokenOf = {}         -- (test) remote game vehicle id -> broken beams

-- (test) a remote car's broken beams, from its vehicle Lua
local deformedOf = {}
function M.onBroken(gid, n, d) brokenOf[tonumber(gid)] = tonumber(n); deformedOf[tonumber(gid)] = tonumber(d) end

local onGoToRef = nil
local function testStep(dt)
  if not next(test) then return end
  testClock = testClock + (dt or 0)
  if lateGoTo and testClock >= M.TEST_LATE_S and onGoToRef then
    local d = lateGoTo
    lateGoTo = nil
    log('I', 'beamngEarth', 'BeamMP test: going to the others now')
    onGoToRef(d)
  end
  if test['goto'] and test.gotoLat and not test.wentGoto and stats.poses > 0 and testClock > M.TEST_GOTO_S
      and (not test.gotoAt or os.time() >= test.gotoAt) then
    test.wentGoto = true
    local c = core()
    if c then
      log('I', 'beamngEarth', string.format('BeamMP test: to %.5f, %.5f', test.gotoLat, test.gotoLon))
      c.teleport(test.gotoLat, test.gotoLon, nil, { record = false })
    end
  end
  if test.map and not test.mapOpened and stats.poses > 0 and testClock > M.TEST_MAP_S then
    test.mapOpened = true
    local st = rawget(_G, 'beamngEarth_settings')
    if st and st.toggleMap then st.toggleMap(); log('I', 'beamngEarth', 'BeamMP test: map opened') end
  end
  local veh = getPlayerVehicle and getPlayerVehicle(0)
  if test.car and not testCarAt and not veh and stats.poses > 0 and testClock > M.TEST_CAR_S then
    testCarAt = testClock
    local c = core_camera and core_camera.getPosition and core_camera.getPosition()
    if c then
      local z = be:getSurfaceHeightBelow(vec3(c.x, c.y + 6, c.z + 50))
      core_vehicles.spawnNewVehicle('pickup', { pos = vec3(c.x, c.y + 6, (z or c.z) + 0.5),
        rot = quatFromDir(vec3(0, 1, 0), vec3(0, 0, 1)), autoEnterVehicle = true })
      log('I', 'beamngEarth', 'BeamMP test: car spawned')
    end
  end
  if testCarAt and not testOwnId and veh then testOwnId = veh:getId() end
  if test.away and testOwnId and anchorRef then
    local c = core()
    local function note(what, lat, lon)
      awayLog[#awayLog + 1] = { t = testClock, what = what, lat = lat, lon = lon }
      log('I', 'beamngEarth', 'BeamMP test: ' .. what)
    end
    if #awayLog == 0 and testClock - testCarAt > M.TEST_AWAY_S and veh then
      local q = veh:getPosition()
      local lat, lon = anchorRef:enuToGeodetic(q.x, q.y, q.z)
      note('away', lat, lon)
      c.teleport(lat, (lon + 130 + 180) % 360 - 180, nil, { record = false })
    elseif #awayLog == 1 and testClock - awayLog[1].t > M.TEST_AWAY_HOLD_S then
      note('back', awayLog[1].lat, awayLog[1].lon)
      c.teleport(awayLog[1].lat, awayLog[1].lon, nil, { record = false })
    end
  end
  if test.drive and testOwnId and not test.driving and veh and testClock - testCarAt > M.TEST_DRIVE_S then
    test.driving = true
    veh:queueLuaCommand('ai.setMode("traffic") ai.setSpeedMode("legal")')
    log('I', 'beamngEarth', 'BeamMP test: AI driving')
  end
  if test.switch and testOwnId then
    local phase = #switchLog
    local function note(what)
      local a = getPlayerVehicle and getPlayerVehicle(0)
      switchLog[#switchLog + 1] = { t = testClock, what = what, active = a and a:getId() or nil,
        ownThere = vehicle(testOwnId) ~= nil }
      log('I', 'beamngEarth', 'BeamMP test: ' .. what)
    end
    if phase == 0 and testClock - testCarAt > M.TEST_SWITCH_S then
      local target = nil
      for gid in pairs(remoteOwners()) do
        if not deactivated[gid] and vehicle(gid) then target = gid; break end
      end
      if target then
        be:enterVehicle(0, vehicle(target))
        note('into the other player\'s car ' .. target)
      end
    elseif phase == 1 and testClock - switchLog[1].t > M.TEST_SWITCH_HOLD_S then
      note('still in the other car (before switching back)')
      local own = vehicle(testOwnId)
      if own then be:enterVehicle(0, own) end
      note('back into our car')
    elseif phase == 3 and testClock - switchLog[3].t > 10 then
      note('10 s after switching back')
    end
  end
  -- 'delremote': TEST_DELREMOTE_S after the car, the other players' cars removed here, as a player does for frames (the
  -- restore must leave them removed; 'oldrestore': 1.6.0's restore rule, for the before)
  if test.delremote and testCarAt and not test.deleted and testClock - testCarAt > M.TEST_DELREMOTE_S and remoteOwners then
    local owners = remoteOwners()
    for gid in pairs(owners) do
      local o = vehicle(gid)
      if o then
        test.deleted = true
        o:delete()
        log('I', 'beamngEarth', 'BeamMP test: removed the other player\'s car ' .. tostring(gid))
      end
    end
  end
  -- 'drop': TEST_DROP_S after the car, our car moved TEST_DROP_M east (BeamMP's teleport to a player far off our block)
  if test.drop and testCarAt and not test.dropped and veh and testClock - testCarAt > M.TEST_DROP_S then
    test.dropped = true
    local p = veh:getPosition()
    veh:setPosition(vec3(p.x + M.TEST_DROP_M, p.y, p.z + 5))
    log('I', 'beamngEarth', 'BeamMP test: car moved ' .. M.TEST_DROP_M .. ' m east')
  end
  if test.damage and testCarAt and not testDamaged and veh and testClock - testCarAt > M.TEST_DAMAGE_S then
    testDamaged = true
    -- a real crash (dropped from 20 m): dents, which BeamMP itself does not send; the other player's copy shows them
    -- only through the full-damage message (sendDamage)
    local p = veh:getPosition()
    veh:setPosition(vec3(p.x, p.y, p.z + 20))
    log('I', 'beamngEarth', 'BeamMP test: car damaged')
  end
end

local COUNT = [[local n,d=0,0 for _,b in pairs(v.data.beams) do if obj:beamIsBroken(b.cid) then n=n+1 ]]
  .. [[elseif obj:getBeamDeformation(b.cid) > 0.01 then d=d+1 end end ]]
  .. [[obj:queueGameEngineLua("local m=package.loaded['beamngEarth/mpsession'] if m then m.onBroken(%d,"..n..","..d..") end")]]

-- The other players' vehicles: { game vehicle id -> owner's server player id }, { game vehicle id -> BeamMP's
-- serverVehicleString }
remoteOwners = function()
  local out, sids = {}, {}
  local v = mp()
  if not v or type(v.getVehicles) ~= 'function' then return out, sids end
  local ok, all = pcall(v.getVehicles)
  if not ok or type(all) ~= 'table' then return out, sids end
  for _, veh in pairs(all) do
    -- (spawned ones only: a car BeamMP has queued has a placeholder game id, not a vehicle)
    if type(veh) == 'table' and veh.isSpawned ~= false and tonumber(veh.gameVehicleID) and veh.gameVehicleID > 0
      and veh.ownerID and not v.isOwn(veh.gameVehicleID) then
      out[veh.gameVehicleID] = veh.ownerID
      sids[veh.gameVehicleID] = veh.serverVehicleString
    end
  end
  return out, sids
end

-- Cars of other players that the game deleted here (BeamMP: isDeleted, drawn as its placeholder sphere; before
-- mpanchor.FAR_M a teleport put them thousands of km out, where the physics broke) come back with BeamMP's own
-- restorePlayerVehicle once their owner is within RESTORE_M of us, at most every RESTORE_AGAIN_S per player. Only a car
-- our world could have broken (mpanchor.wasFar: it was beyond FAR_M) and RESTORE_MAX times at most: isDeleted is also a
-- car this player removed on purpose (fewer cars, more frames), and respawning every one of those every 15 s (each a
-- spawn: a freeze, and BeamMP's "Trying to respawn" notice) fits the 1.6.0 players' "multiplayer freezing" (2026-10-23).
M.RESTORE_M, M.RESTORE_AGAIN_S, M.RESTORE_MAX = 20000, 15, 2
local restoredAt = {}       -- owner name -> clock of the last restore
local restores = {}         -- server vehicle string -> restores asked for it
local clock = 0
function M.restoreDeleted(lat, lon)
  local mpanchor = require('beamngEarth/mpanchor')
  local v = mp()
  if not v or type(v.getVehicles) ~= 'function' or type(v.restorePlayerVehicle) ~= 'function' then return 0 end
  local ok, all = pcall(v.getVehicles)
  if not ok or type(all) ~= 'table' then return 0 end
  local n = 0
  for sid, veh in pairs(all) do
    local a = type(veh) == 'table' and veh.isDeleted == true and not veh.isLocal and veh.ownerName
      and (test.oldrestore or (mpanchor.wasFar(tostring(sid)) and (restores[tostring(sid)] or 0) < M.RESTORE_MAX))
      and anchors[tostring(veh.ownerID)]
    if a and tonumber(a.lat) and tonumber(a.lon) and M.dist(lat, lon, tonumber(a.lat), tonumber(a.lon)) <= M.RESTORE_M
        and clock - (restoredAt[veh.ownerName] or -math.huge) >= M.RESTORE_AGAIN_S then
      restoredAt[veh.ownerName] = clock
      restores[tostring(sid)] = (restores[tostring(sid)] or 0) + 1
      stats.restored = (stats.restored or 0) + 1
      log('I', 'beamngEarth', 'BeamMP: ' .. tostring(veh.ownerName) .. '\'s deleted car brought back')
      pcall(v.restorePlayerVehicle, veh.ownerName)
      n = n + 1
    end
  end
  return n
end


-- The heading of the car the player is in, degrees clockwise from the frame's +y (north at the anchor); nil without one.
local function carHeading()
  local gpv = rawget(_G, 'getPlayerVehicle')
  local veh = gpv and gpv(0)
  if not veh then return nil end
  local ok, dir = pcall(function() return veh:getDirectionVector() end)
  if not ok or not dir or math.abs(dir.x) + math.abs(dir.y) < 1e-6 then return nil end
  return (math.deg(math.atan2(dir.x, dir.y)) + 360) % 360
end

-- Our own server player id (BeamMP's MPConfig), as a string; nil unknown.
local function myPid()
  local cfg = rawget(_G, 'MPConfig')
  if type(cfg) == 'table' and type(cfg.getPlayerServerID) == 'function' then
    local ok, id = pcall(cfg.getPlayerServerID)
    if ok and id ~= nil then return tostring(id) end
  end
  return nil
end

local function onAnchors(data)
  local ok, d = pcall(jsonDecode, data)
  if not ok or type(d) ~= 'table' then return end
  stats.anchors = stats.anchors + 1
  anchors = d
  local mpanchor = require('beamngEarth/mpanchor')
  local n = 0
  for pid, a in pairs(d) do
    n = n + 1
    if type(a) == 'table' then mpanchor.setRemote(pid, a.aLat, a.aLon) end
  end
  if n ~= lastPlayers then
    lastPlayers = n
    log('I', 'beamngEarth', string.format('BeamMP: %d player(s) with BeamNG.Earth', n))
  end
end

-- core.teleportTo: the anchor of other players near (lat, lon) to teleport onto (aLat, aLon), nil if none.
function M.adoptAnchor(lat, lon)
  if not next(anchors) then return nil end         -- (outside a session: empty)
  return M.pickAnchor(anchors, lat, lon, myPid())
end

-- Our world server's group service is up (core: the "share" answer): { port, code }.
function M.onServerHello()
  -- A restarted/reconnected companion has revoked its old sharing session.
  share, poseIn = nil, 0
end

function M.onShare(msg)
  local port = tonumber(msg.port)
  share = port and { port = port, code = tostring(msg.code or ''), weight = tonumber(msg.weight), token = msg.token } or false
  poseIn = 0
  log('I', 'beamngEarth', 'BeamMP: sharing generation on port ' .. tostring(port))
end

-- The server's settings (the plugin's bngeConfig, at our first pose): the port our world server shares on.
local function onConfig(data)
  local ok, d = pcall(jsonDecode, data)
  if not ok or type(d) ~= 'table' then return end
  config = d
  log('I', 'beamngEarth', 'BeamMP: the server\'s sharing port ' .. tostring(d.sharePort))
end

-- Pure: the "share" request for our world server: the server's port when it is a usable one.
function M.shareRequest(cfg)
  local port = cfg and tonumber(cfg.sharePort)
  if port and (port ~= math.floor(port) or port < 1024 or port > 65535) then port = nil end
  return { type = 'share', port = port }
end

local function onPeers(data)
  local ok, d = pcall(jsonDecode, data)
  if not ok or type(d) ~= 'table' or type(d.members) ~= 'table' then return end
  stats.peers = #d.members
  require('beamngEarth/net').send({ type = 'peers', me = tostring(d.me), members = d.members })
  log('I', 'beamngEarth', string.format('BeamMP: %d player(s) share our anchor\'s generation', #d.members))
end

-- Onto the anchor of the players near us when it is not ours (pickAnchor, everyone's anchors with ours): a smooth
-- rebase (core.rebaseTo). Every player applies the same rule, so a group ends on one anchor; a car ahead that
-- rebased takes the ones following onto its new anchor.
local function converge(dt, anchor, lat, lon)
  local k = M.key(anchor.lat, anchor.lon)
  if k ~= lastAnchorKey then lastAnchorKey, anchorSince = k, 0 end
  anchorSince = anchorSince + (dt or 0)
  convergeIn = convergeIn - (dt or 0)
  if convergeIn > 0 or anchorSince < M.SETTLE_S then return end
  convergeIn = M.CONVERGE_S
  local mine = myPid()
  if not mine or not anchors[mine] then return end   -- (ours not heard back yet: the counts would be off)
  local aLat, aLon = M.pickAnchor(anchors, lat, lon)
  if not aLat or M.key(aLat, aLon) == k then return end
  local c = core()
  local ok, why = false, 'no core'
  if c and c.rebaseTo then ok, why = c.rebaseTo(aLat, aLon) end
  if ok then
    stats.converged = stats.converged + 1
    log('I', 'beamngEarth', string.format('BeamMP: onto the shared anchor %s', M.key(aLat, aLon)))
  end
end

local function onGoTo(data)
  local ok, d = pcall(jsonDecode, data)
  local c = core()
  stats.go = stats.go + 1
  if test.late and testClock < M.TEST_LATE_S then lateGoTo = data; return end
  if wentTo or not ok or type(d) ~= 'table' or not c then return end
  local lat, lon = tonumber(d.lat), tonumber(d.lon)
  if not lat or not lon then return end
  wentTo = true
  -- beside the other player, not on it (12 m east)
  lon = lon + M.GOTO_OFFSET_M / (111320 * math.max(0.01, math.cos(math.rad(lat))))
  log('I', 'beamngEarth', string.format('BeamMP: to the other players at %.5f, %.5f', lat, lon))
  c.teleport(lat, lon, nil, { record = false })
end

onGoToRef = onGoTo

-- No car yet (BeamMP joins without one): the free camera, left at the level's spawn point under the generated
-- ground, put once on the ground at the origin (where the vehicle selector spawns the car); and lifted back over the
-- ground whenever it is under it (a teleport moves the world under a free camera: from a valley to a mountain it
-- ended 1400 m underground, a black screen).
local function placeCamera(player)
  if (getPlayerVehicle and getPlayerVehicle(0)) or not be or not be.getSurfaceHeightBelow then return end
  local x, y = 0, -8
  if camPlaced then
    if not player or not player.x then return end
    x, y = player.x, player.y
  end
  local z = be:getSurfaceHeightBelow(vec3(x, y, 9000))
  if type(z) ~= 'number' or z < -1e5 then return end      -- (the ground not there yet)
  if camPlaced and player.z >= z - 0.5 then return end
  camPlaced = true
  local q = quatFromDir(vec3(0, 1, -0.15), vec3(0, 0, 1))
  if core_camera and core_camera.setPosRot then core_camera.setPosRot(0, x, y, z + 3, q.x, q.y, q.z, q.w) end
end

-- BeamMP's join done (core.lua onBeamMPPostJoin): the first pose asks the plugin where the others are.
function M.onPostJoin()
  joined, wentTo, camPlaced = true, false, false
  anchors = {}
end

-- Each frame (core.lua) in a BeamMP session in our level: anchor (core's st.anchor), player { x, y, z } in its frame
-- (the car; the camera when there is none).
function M.update(dt, anchor, player)
  if not handlers and rawget(_G, 'AddEventHandler') then
    AddEventHandler('bngeAnchors', onAnchors, 'beamngEarth')
    AddEventHandler('bngeGoTo', onGoTo, 'beamngEarth')
    AddEventHandler('bngePeers', onPeers, 'beamngEarth')
    AddEventHandler('bngeConfig', onConfig, 'beamngEarth')
    handlers = true
  end
  local mpanchor = require('beamngEarth/mpanchor')
  mpanchor.install()            -- (a no-op once installed, and until BeamMP's positionGE has loaded)
  clock = clock + (dt or 0)
  statusIn = statusIn - (dt or 0)
  if statusIn <= 0 then
    statusIn = M.STATUS_S
    local n = 0
    for _ in pairs(anchors) do n = n + 1 end
    log('I', 'beamngEarth', string.format('BeamMP status: anchor %s, %d player(s) known, events anchors/goto %d/%d, '
      .. 'poses %d, %s', anchor and M.key(anchor.lat, anchor.lon) or 'none', n, stats.anchors, stats.go, stats.poses,
      mpanchor.status()))
    -- the same as a file (the game's log reaches the disk late; BeamMP turns the game's MCP off in a session)
    local hidden = 0
    for _ in pairs(deactivated) do hidden = hidden + 1 end
    local remote = nil
    local own = nil
    if next(test) then
      local v = getPlayerVehicle and getPlayerVehicle(0)
      if v then
        local gid = v:getId()
        v:queueLuaCommand(string.format(COUNT, gid))
        own = { broken = brokenOf[gid], deformed = deformedOf[gid], damage = rawget(_G, 'map') and map.objects[gid] and map.objects[gid].damage,
          sent = sentDamage[gid] }
        if anchor then
          local q = v:getPosition()
          own.lat, own.lon = anchor:enuToGeodetic(q.x, q.y, q.z)
        end
      end
      remote = {}
      for gid in pairs(remoteOwners()) do
        local o = vehicle(gid)
        if o then o:queueLuaCommand(string.format(COUNT, gid)) end
        local r = { broken = brokenOf[gid], deformed = deformedOf[gid], hidden = deactivated[gid] == true }
        if o and anchor then        -- (where it stands here, as latitude / longitude: the conversion's check)
          local q = o:getPosition()
          r.lat, r.lon = anchor:enuToGeodetic(q.x, q.y, q.z)
        end
        remote[tostring(gid)] = r
      end
    end
    local perf = nil
    if test.perf then
      local pm = rawget(_G, 'beamngEarth_perfmon')
      if not pm and extensions and extensions.load then extensions.load('beamngEarth_perfmon') end
      pm = rawget(_G, 'beamngEarth_perfmon')
      local ok, snap = pcall(function() return pm and pm.snapshot() end)
      if ok and type(snap) == 'table' then
        perf = { frames = snap.frames, fps = snap.fps, p50 = snap.p50, p99 = snap.p99, maxMs = snap.maxMs,
          over33 = snap.over33, over50 = snap.over50, over100 = snap.over100 }
        local gov = require('beamngEarth/governor')
        if next(gov.slow) then perf.slow, gov.slow = gov.slow, {} end     -- (the slow frames since the last)
      end
    end
    pcall(jsonWriteFile, M.STATUS_FILE, { t = os.time(), anchor = anchor and M.key(anchor.lat, anchor.lon) or nil,
      perf = perf, switch = next(switchLog) and switchLog or nil, away = next(awayLog) and awayLog or nil,
      anchors = anchors, stats = stats, hidden = hidden, remote = remote, own = own, player = player and { x = player.x,
      y = player.y, z = player.z, camera = player.camera } or nil }, true)
  end
  configWait = configWait + (dt or 0)
  if share == nil and (config or configWait >= M.CONFIG_WAIT_S) then
    share = false               -- our world server's group service, asked once a session (its answer: onShare), on
    require('beamngEarth/net').send(M.shareRequest(config))     -- the server's port
  end
  if not anchor then return end
  placeCamera(player)
  anchorRef = anchor
  testStep(dt)
  if not player then return end
  local lat, lon = anchor:enuToGeodetic(player.x, player.y, player.z or 0)
  poseIn = poseIn - (dt or 0)
  if (poseIn <= 0 or mpanchor.ownChanged) and rawget(_G, 'TriggerServerEvent') then
    poseIn = M.POSE_S
    mpanchor.ownChanged = false
    local h = carHeading()
    if h and anchor.northAt then        -- (true north: the frame's +y drifts from it away from the anchor)
      local ok, nx, ny = pcall(anchor.northAt, anchor, player.x, player.y)
      if ok and tonumber(nx) and tonumber(ny) then h = (h - math.deg(math.atan2(nx, ny)) + 360) % 360 end
    end
    TriggerServerEvent('bngePose', jsonEncode({ v = M.PROTO, aLat = anchor.lat, aLon = anchor.lon, lat = lat,
      lon = lon, joined = joined, h = h and math.floor(h * 10 + 0.5) / 10 or nil,
      sp = share and share.port or nil, sc = share and share.code or nil, sw = share and share.weight or nil,
      st = share and share.token or nil }))
    stats.poses = stats.poses + 1
    if joined then log('I', 'beamngEarth', string.format('BeamMP: first pose sent (%.5f, %.5f)', lat, lon)) end
    joined = false
  end
  converge(dt, anchor, lat, lon)
  hideIn = hideIn - (dt or 0)
  if hideIn > 0 then return end
  hideIn = M.HIDE_S
  local owners, sids = remoteOwners()
  local hide = M.toHide(owners, mpanchor.knows)
  for gid, sid in pairs(sids) do
    if mpanchor.isFar(sid) then hide[gid] = true end     -- (not placed: beyond our world)
  end
  M.sendDamage(lat, lon, M.HIDE_S)
  M.restoreDeleted(lat, lon)
  for gid in pairs(hide) do
    if not deactivated[gid] then
      local veh = vehicle(gid)
      if veh then veh:setActive(0); deactivated[gid] = true end
    end
  end
  for gid in pairs(deactivated) do
    if not hide[gid] then
      local veh = vehicle(gid)
      if veh then veh:setActive(1) end
      deactivated[gid] = nil
    end
  end
end

-- Our own cars' full damage to the players near (see the top), with BeamMP's own nodesVE.getNodes. (lat, lon): our
-- place.
function M.sendDamage(lat, lon, dt)
  local now, newcomer, any = {}, false, false
  local mine = myPid()
  for k, a in pairs(anchors) do
    if k ~= mine and type(a) == 'table' and tonumber(a.lat) and tonumber(a.lon)
        and M.dist(lat, lon, tonumber(a.lat), tonumber(a.lon)) <= M.DAMAGE_NEAR_M then
      now[k] = true
      any = true
      if not members[k] then newcomer = true end
    end
  end
  members = now
  local v = mp()
  if not any or not v or type(v.getVehicles) ~= 'function' then return end
  local objs = rawget(_G, 'map') and map.objects or {}
  local ok, all = pcall(v.getVehicles)
  if not ok or type(all) ~= 'table' then return end
  for _, veh in pairs(all) do
    local gid = type(veh) == 'table' and veh.gameVehicleID
    if gid and v.isOwn(gid) then
      local o = objs[gid]
      local dmg = o and tonumber(o.damage)
      local send
      send, damageSince[gid] = M.damageDue(newcomer, dmg, sentDamage[gid], damageSince[gid], dt)
      local obj = send and vehicle(gid)
      if obj then
        obj:queueLuaCommand('if nodesVE and nodesVE.getNodes then nodesVE.getNodes() end')
        sentDamage[gid] = dmg or 0
      end
    end
  end
end

-- The session's end (BeamMP left, the level's end): the deactivated cars back on, everything forgotten.
function M.reset()
  if share ~= nil then require('beamngEarth/net').send({ type = 'share_stop' }) end
  for gid in pairs(deactivated) do
    local veh = vehicle(gid)
    if veh then veh:setActive(1) end
  end
  deactivated, anchors, poseIn, hideIn, joined, wentTo, camPlaced, lastPlayers = {}, {}, 0, 0, false, false, false, -1
  members, sentDamage, damageSince, restoredAt, restores = {}, {}, {}, {}, {}
  share, config, configWait, convergeIn, anchorSince, lastAnchorKey = nil, nil, 0, 0, 0, nil
  stats, statusIn = { anchors = 0, go = 0, poses = 0, converged = 0, peers = 0 }, M.STATUS_S
  require('beamngEarth/mpanchor').reset()
end

-- The other players for the world map: { { name, lat, lon, h } } (h: heading in degrees from true north, nil when
-- they have no car), from the plugin's latest bngeAnchors (POSE_S old at most).
function M.others()
  local out, mine = {}, myPid()
  for k, a in pairs(anchors) do
    if k ~= mine and type(a) == 'table' and tonumber(a.lat) and tonumber(a.lon) then
      out[#out + 1] = { name = type(a.name) == 'string' and a.name or ('player ' .. k), lat = tonumber(a.lat),
        lon = tonumber(a.lon), h = tonumber(a.h) }
    end
  end
  table.sort(out, function(x, y) return x.name < y.name end)
  return out
end

-- (tests, the dev tools)
function M.state() return { anchors = anchors, deactivated = deactivated, joined = joined, wentTo = wentTo } end

return M
