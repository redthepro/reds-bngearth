-- Optional voice broker. Private files live outside Resources (never client downloads).
local V = { players = {}, groups = {}, invites = {}, time = 0, serial = 0 }
local root = 'voice/'
local function read(name)
  local f = io.open(root .. name, 'rb'); if not f then return nil end
  local s = f:read(262145); f:close()
  if not s or #s > 262144 then return nil end
  local ok, d = pcall(Util.JsonDecode, s); if ok and type(d) == 'table' then return d end
end
local endpoint = read('endpoint.json')
if not endpoint then return V end
local epoch = tostring(os.time()) .. ':' .. tostring(os.clock())
local function send(pid, event, d) MP.TriggerClientEvent(pid, event, Util.JsonEncode(d)) end
local function finite(x) return type(x) == 'number' and x == x and math.abs(x) < 1e8 end
local function distance(a, b)
  if not a or not b then return math.huge end
  return math.sqrt((a[1]-b[1])^2 + (a[2]-b[2])^2 + (a[3]-b[3])^2)
end
local function leave(pid)
  local p = V.players[pid]; if not p then return end
  local g = V.groups[p.group]
  if g then
    g.members[pid] = nil
    if g.owner == pid then
      for id in pairs(g.members) do g.owner = id; break end
    end
    if not next(g.members) then V.groups[p.group] = nil end
  end
  p.group, p.want = '', ''
end
function BNGE_voiceLeave(pid)
  pid = tostring(pid); leave(pid); V.players[pid] = nil; V.invites[pid] = nil
end
function BNGE_voicePose(pid, raw)
  if type(raw) ~= 'string' or #raw > 2048 then return end
  local ok, d = pcall(Util.JsonDecode, raw); if not ok or type(d) ~= 'table' then return end
  pid = tostring(pid)
  local p = V.players[pid]
  if not p then
    local name = MP.GetPlayerName(tonumber(pid)); if not name then return end
    V.serial = V.serial + 1
    p = { epoch = epoch .. ':' .. V.serial, name = tostring(name):sub(1,80), group = '', want = '', talk = '' }
    V.players[pid] = p
  end
  if p.poseAt and V.time - p.poseAt < 0.075 then return end
  p.poseAt, p.seen = V.time, V.time
  local pos = d.pos
  if type(pos) == 'table' and finite(pos[1]) and finite(pos[2]) and finite(pos[3])
      and math.abs(pos[1]) <= 7e6 and math.abs(pos[2]) <= 7e6 and math.abs(pos[3]) <= 7e6 then p.pos = pos end
  local want = d.talk
  if want ~= 'p' and want ~= 's' and want ~= 'g' then want = '' end
  if want == 'g' and p.group == '' then want = '' end
  if want ~= p.want then p.requested = V.time end
  p.want = want
end
function BNGE_voiceAction(pid, raw)
  pid = tostring(pid)
  local p = V.players[pid]; if not p or type(raw) ~= 'string' or #raw > 1024 then return end
  if p.actionAt and V.time - p.actionAt < 0.5 then return end
  p.actionAt = V.time
  local ok, d = pcall(Util.JsonDecode, raw); if not ok or type(d) ~= 'table' then return end
  local target = tostring(d.player or '')
  if d.action == 'create' and p.group == '' then
    V.serial = V.serial + 1
    p.group = epoch .. ':' .. V.serial
    V.groups[p.group] = { owner = pid, members = { [pid] = true } }
  elseif d.action == 'invite' then
    local g = V.groups[p.group]
    if g and g.owner == pid and V.players[target] and V.players[target].group == '' then
      V.invites[target] = { group = p.group, expires = V.time + 30, from = pid }
    end
  elseif d.action == 'accept' and p.group == '' then
    local invite = V.invites[pid]; local g = invite and V.groups[invite.group]
    if g and invite.expires > V.time then
      local n = 0; for _ in pairs(g.members) do n = n+1 end
      if n < 16 then p.group = invite.group; g.members[pid] = true end
    end
    V.invites[pid] = nil
  elseif d.action == 'leave' then leave(pid)
  elseif d.action == 'kick' then
    local g = V.groups[p.group]
    if g and g.owner == pid and target ~= pid and g.members[target] then leave(target) end
  end
end
function BNGE_voiceTick()
  V.time = V.time + 0.1
  local ordered = {}
  for pid, p in pairs(V.players) do
    if V.time - (p.seen or 0) > 5 then BNGE_voiceLeave(pid)
    else
      if V.time - (p.poseAt or 0) > 0.4 then p.want = '' end
      p.talk = ''; ordered[#ordered+1] = { pid = pid, p = p }
    end
  end
  table.sort(ordered, function(a,b)
    if (a.p.requested or 0) == (b.p.requested or 0) then return a.pid < b.pid end
    return (a.p.requested or 0) < (b.p.requested or 0)
  end)
  local global, groups, near = 0, {}, {}
  for _, row in ipairs(ordered) do
    local p = row.p
    if p.want == 's' and global < 4 then p.talk = 's'; global = global + 1
    elseif p.want == 'g' and (groups[p.group] or 0) < 6 then
      p.talk = 'g'; groups[p.group] = (groups[p.group] or 0) + 1
    elseif p.want == 'p' and p.pos then
      local n = 0; for _, q in ipairs(near) do if distance(p.pos,q.pos) < 160 then n = n+1 end end
      if n < 8 then p.talk = 'p'; near[#near+1] = p end
    end
  end
  local f = io.open(root .. 'state.json.new', 'wb')
  if f then
    local players = {}
    for pid, p in pairs(V.players) do players[pid] = { epoch=p.epoch, name=p.name, group=p.group, talk=p.talk, pos=p.pos } end
    f:write(Util.JsonEncode({ time=os.time(), players=players })); f:close()
    os.rename(root .. 'state.json.new', root .. 'state.json')
  end
  local creds = read('credentials.json'); if not creds or type(creds.players) ~= 'table' then return end
  local roster = {}
  for pid, c in pairs(creds.players) do
    local p = V.players[pid]
    if p and c.epoch == p.epoch then roster[pid] = { name=p.name, base=c.base, group=p.group } end
  end
  local rosterSig = Util.JsonEncode(roster)
  for pid, p in pairs(V.players) do
    local c = creds.players[pid]
    if c and c.epoch == p.epoch then
      if p.configToken ~= c.token then
        p.configToken = c.token
        send(tonumber(pid),'bngeVoiceConfig',{ host=endpoint.host, port=endpoint.port,
          fingerprint=endpoint.fingerprint, token=c.token, base=c.base, me=pid, range=80 })
      end
      local nearby = {}
      for id, q in pairs(V.players) do
        if id ~= pid and roster[id] and distance(p.pos,q.pos) <= 80 then nearby[#nearby+1] = roster[id].base end
      end
      local g = V.groups[p.group]; local invite = V.invites[pid]
      if invite and invite.expires <= V.time then V.invites[pid] = nil; invite = nil end
      local d = { group=p.group, owner=g and g.owner or '', talk=p.talk,
        nearby=nearby, invite=invite and invite.from or '' }
      if p.rosterSig ~= rosterSig then d.roster = roster; p.rosterSig = rosterSig end
      send(tonumber(pid),'bngeVoiceState',d)
    end
  end
end
MP.RegisterEvent('bngeVoicePose','BNGE_voicePose')
MP.RegisterEvent('bngeVoiceAction','BNGE_voiceAction')
MP.RegisterEvent('onPlayerDisconnect','BNGE_voiceLeave')
MP.RegisterEvent('BNGE_voiceTick','BNGE_voiceTick')
MP.CreateEventTimer('BNGE_voiceTick',100)
print('[BeamNG.Earth] optional voice broker loaded')
return V
