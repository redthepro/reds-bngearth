-- BeamNG.Earth's BeamMP server plugin (Resources/Server/BeamNGEarth/main.lua; BeamMP server Lua 5.3).
--
-- BeamMP sends every car's position in its driver's world coordinates, and BeamNG.Earth's world coordinates are
-- metres from an origin (the "anchor") that each player's game picks for itself and moves as they drive (a rebase
-- every 15 km). Each client converts the other players' positions from their owner's anchor into its own
-- (mpanchor.lua, from Thai-v4's PR #88); this plugin tells every client where everyone's anchor is:
--   - bngeAnchors { pid -> { aLat, aLon, lat, lon, name, h } }: every player's anchor and place (from the client's
--     bngePose, every 2 s and at once when its anchor changes), its name and heading (degrees from north; none
--     without a car: the world map's arrows), to everyone each TICK_MS and at once when an anchor changes;
--   - bngeGoTo { lat, lon }: a player who joins is sent to the others (the longest-joined player's place);
--   - bngeConfig { sharePort }: the server's settings (above), to a player at their first pose;
--   - bngePeers { me, members = [{ id, host, port, code, weight }] }: the players on the same anchor whose world server
--     shares generation (their pose's sp / sc / sw; host: the address BeamMP sees; weight: its machine's capacity,
--     its share of the tiles), each tick when its group changed. Players
--     near each other share an anchor (the clients converge, mpsession.lua), so they split the tiles to build.
-- Positions stay latitude / longitude here: no coordinates are converted on the server. Nothing of BeamMP itself is
-- changed: only its events (MP.RegisterEvent, MP.TriggerClientEvent).
local G = {}

-- ===== Settings: edit here, then restart the BeamMP server ========================================================
-- The TCP port every player's world server listens on to share world building with the players near them (the
-- others connect to it: over the internet, each player forwards this port to their computer; on one network nothing
-- is needed). Sent to each player when they join. A player whose port is taken uses the next free one (up to 9 more).
G.SHARE_PORT = 47621
-- ===================================================================================================================

G.PROTO = 2                 -- (2: anchors for the clients' conversion; 1 grouped players by anchor)
G.MEET_M = 3000             -- a joining player farther than this from the others is sent to them
G.STALE_S = 15              -- a player's pose this old: left out
G.TICK_MS = 2000

G.players = {}              -- pid -> { name, aLat, aLon, lat, lon, t, joinedAt }
G.clock = 0

function G.key(aLat, aLon) return string.format('%.6f,%.6f', aLat, aLon) end

-- Metres between two latitude / longitude points (equirectangular: the distances here are a few km).
function G.dist(lat1, lon1, lat2, lon2)
  local k = math.pi / 180
  local x = (lon2 - lon1) * k * math.cos((lat1 + lat2) / 2 * k)
  local y = (lat2 - lat1) * k
  return 6371000 * math.sqrt(x * x + y * y)
end

-- The players with a pose newer than STALE_S.
function G.live()
  local out = {}
  for pid, p in pairs(G.players) do
    if G.clock - (p.t or -math.huge) <= G.STALE_S then out[pid] = p end
  end
  return out
end

-- Pure: the anchors message { tostring(pid) -> { aLat, aLon, lat, lon, name, h } } of players.
function G.anchors(players)
  local out = {}
  for pid, p in pairs(players) do
    out[tostring(pid)] = { aLat = p.aLat, aLon = p.aLon, lat = p.lat, lon = p.lon, name = p.name, h = p.h }
  end
  return out
end

-- Pure: where a joining player goes: the place of the longest-joined other player (nil: alone). { lat, lon }
function G.meetingPlace(players, newPid)
  local best = nil
  for pid, p in pairs(players) do
    if pid ~= newPid and (not best or (p.joinedAt or 0) < (players[best].joinedAt or 0)
        or ((p.joinedAt or 0) == (players[best].joinedAt or 0) and pid < best)) then
      best = pid
    end
  end
  return best and { lat = players[best].lat, lon = players[best].lon } or nil
end

-- A client's pose { v, aLat, aLon, lat, lon, joined, h, sp, sc, sw } (its world server's sharing port, code, weight). Returns place (the meeting place for its first pose after
-- joining, or nil) and moved (its anchor is new or changed: the anchors go out at once).
function G.onPose(pid, d, name)
  if type(d) ~= 'table' or tonumber(d.v) ~= G.PROTO then return nil, false end
  local aLat, aLon, lat, lon = tonumber(d.aLat), tonumber(d.aLon), tonumber(d.lat), tonumber(d.lon)
  if not (aLat and aLon and lat and lon) or math.abs(lat) > 90 or math.abs(lon) > 180
      or math.abs(aLat) > 90 or math.abs(aLon) > 180 then
    return nil, false
  end
  local p = G.players[pid]
  local first = p == nil
  if first then
    p = { joinedAt = G.clock }
    G.players[pid] = p
  end
  local moved = first or p.aLat ~= aLat or p.aLon ~= aLon
  p.name, p.aLat, p.aLon, p.lat, p.lon, p.t = name, aLat, aLon, lat, lon, G.clock
  local h = tonumber(d.h)
  p.h = (h and h >= 0 and h <= 360) and h or nil
  local sp = tonumber(d.sp)
  p.sp = (sp and sp >= 1 and sp <= 65535 and sp % 1 == 0) and sp or nil
  p.sc = type(d.sc) == 'string' and #d.sc <= 64 and d.sc or nil
  p.st = type(d.st) == 'string' and #d.st == 64 and d.st:match('^[0-9a-f]+$') and d.st or nil
  local sw = tonumber(d.sw)
  p.sw = (sw and sw >= 0.1 and sw <= 1000) and sw or 1
  if first and d.joined == true then
    local place = G.meetingPlace(G.live(), pid)
    if place and G.dist(place.lat, place.lon, lat, lon) > G.MEET_M then return place, moved end
  end
  return nil, moved
end

function G.onLeave(pid) G.players[pid] = nil; G.sentPeers[pid] = nil end

-- Pure: each sharing player's group { pid -> { me, members } }: the players with the same anchor that share (a
-- port, a code and an address), sorted by id (the same list for every member).
function G.peers(players)
  local byKey = {}
  for pid, p in pairs(players) do
    if p.sp and p.sc and p.ip and p.st then
      local k = G.key(p.aLat, p.aLon)
      byKey[k] = byKey[k] or {}
      table.insert(byKey[k], { id = tostring(pid), host = p.ip, port = p.sp, code = p.sc, weight = p.sw, token = p.st })
    end
  end
  local out = {}
  for pid, p in pairs(players) do
    local list = p.sp and p.sc and p.ip and p.st and byKey[G.key(p.aLat, p.aLon)]
    if list then
      table.sort(list, function(a, b) return a.id < b.id end)
      out[pid] = { me = tostring(pid), members = list }
    end
  end
  return out
end

-- Pure: a group's signature (sent again only when it changes).
function G.peersKey(g)
  local parts = {}
  for _, m in ipairs(g.members) do
    parts[#parts + 1] = m.id .. '@' .. m.host .. ':' .. m.port .. '/' .. m.code .. 'x' .. tostring(m.weight) .. '/' .. m.token
  end
  return table.concat(parts, ',')
end

G.sentPeers = {}            -- pid -> the signature last sent

-- Every TICK_MS: the anchors message for the clients.
function G.tick(dt)
  G.clock = G.clock + (dt or 0)
  return G.anchors(G.live())
end

-- The settings each player gets (bngeConfig): the sharing port, when it is a usable one.
function G.config()
  local port = tonumber(G.SHARE_PORT)
  port = port and math.floor(port)
  return { sharePort = (port and port >= 1024 and port <= 65535) and port or nil }
end

-- ---------------------------------------------------------------------------------------------------------------
-- BeamMP wiring (absent in the offline tests)
if MP and MP.RegisterEvent then
  local function encode(t) return Util.JsonEncode(t) end

  function BNGE_onPose(pid, data)
    local ok, d = pcall(Util.JsonDecode, data)
    if not ok then return end
    local known = G.players[pid] ~= nil
    local place, moved = G.onPose(pid, d, MP.GetPlayerName(pid))
    local p = G.players[pid]
    if p and not p.ip and MP.GetPlayerIdentifiers then      -- (the address the others reach its world server at)
      local okI, ids = pcall(MP.GetPlayerIdentifiers, pid)
      if okI and type(ids) == 'table' and type(ids.ip) == 'string' then p.ip = ids.ip end
    end
    if p and not known then
      MP.TriggerClientEvent(pid, 'bngeConfig', encode(G.config()))
      print(string.format('[BeamNG.Earth] %s at %.5f, %.5f (anchor %s)', tostring(p.name), p.lat, p.lon,
        G.key(p.aLat, p.aLon)))
    end
    -- (the anchors first: the joiner teleports onto the others' anchor, mpsession.adoptAnchor)
    if moved then MP.TriggerClientEvent(-1, 'bngeAnchors', encode(G.anchors(G.live()))) end
    if place then
      print(string.format('[BeamNG.Earth] %s sent to the others at %.5f, %.5f', tostring(p and p.name), place.lat,
        place.lon))
      MP.TriggerClientEvent(pid, 'bngeGoTo', encode(place))
    end
  end

  function BNGE_onLeave(pid) G.onLeave(pid) end

  function BNGE_tick()
    MP.TriggerClientEvent(-1, 'bngeAnchors', encode(G.tick(G.TICK_MS / 1000)))
    local groups = G.peers(G.live())
    for pid in pairs(G.sentPeers) do
      if not groups[pid] then
        MP.TriggerClientEvent(pid, 'bngePeers', encode({ me = tostring(pid), members = {} }))
        G.sentPeers[pid] = nil
      end
    end
    for pid, g in pairs(groups) do
      local sig = G.peersKey(g)
      if G.sentPeers[pid] ~= sig then
        G.sentPeers[pid] = sig
        MP.TriggerClientEvent(pid, 'bngePeers', encode(g))
        print(string.format('[BeamNG.Earth] %s shares generation with %d player(s)', tostring(G.players[pid].name),
          #g.members - 1))
      end
    end
  end

  MP.RegisterEvent('bngePose', 'BNGE_onPose')
  MP.RegisterEvent('onPlayerDisconnect', 'BNGE_onLeave')
  MP.RegisterEvent('BNGE_tick', 'BNGE_tick')
  MP.CreateEventTimer('BNGE_tick', G.TICK_MS)
  print(string.format('[BeamNG.Earth] plugin loaded (players share world building on TCP port %s)',
    tostring(G.config().sharePort or 'their own default')))
end

return G
