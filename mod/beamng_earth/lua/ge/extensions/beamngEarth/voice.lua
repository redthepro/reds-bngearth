-- Voice is opt-in and PTT only. No microphone data passes through game Lua.
local M = {}
local net = require('beamngEarth/net')
local enabled, handlers, elapsed = false, false, 0
local held, config, state, status = {}, nil, {}, {}
local testAt, clock, hudAt = -math.huge, 0, 0
local notice, noticeAt = nil, 0
-- `server` is deliberately separate from the master opt-in. Nearby and group
-- chat stay available to an opted-in player, while all-server chat needs an
-- additional explicit choice every multiplayer session.
local options = { enabled=false, server=false, volumes={1,1,1}, muted={}, input='', output='' }
local function send(action, data) net.send({ type='voice', action=action, data=data }) end
local function decode(raw)
  if type(raw) ~= 'string' or #raw > 65536 then return nil end
  local ok, d = pcall(jsonDecode, raw); if ok and type(d) == 'table' then return d end
end
local function onConfig(raw)
  local d = decode(raw); if not d then return end
  config = d; send('session',d)
end
local function onState(raw)
  local d = decode(raw); if not d then return end
  if not d.roster then d.roster = state.roster end
  local wire = d
  state = d; send('state',wire)
end
function M.onStatus(d) status = d end
function M.test(on)
  testAt = on == true and clock or -math.huge
  net.send({type='voice', action='mic_test', data={active=on==true}})
end
function M.onServerHello()
  if config then send('control',options); send('session',config); send('state',state) end
end
function M.talk(role, down)
  if role ~= 'p' and role ~= 's' and role ~= 'g' then return end
  if role == 's' and not options.server then
    held.s = nil
    return
  end
  held[role] = down == true or down == 1
  if not held[role] then net.send({type='voice',action='ptt',talk=''}) end
end
function M.control(raw)
  local d = type(raw) == 'table' and raw or decode(raw); if not d then return end
  -- Controls send one changed field, so a late UI poll cannot overwrite another
  -- control's choice (or reset opt-in after closing and reopening the panel).
  for _, key in ipairs({'enabled','server'}) do
    if type(d[key])=='boolean' then options[key]=d[key] end
  end
  for _, key in ipairs({'volumes','muted'}) do
    if type(d[key])=='table' then options[key]=d[key] end
  end
  for _, key in ipairs({'input','output'}) do
    if type(d[key])=='string' then options[key]=d[key] end
  end
  enabled = options.enabled
  if not enabled then held = {}; M.test(false) end
  if not options.server then held.s = nil end
  send('control',options)
end
function M.action(action, player)
  if rawget(_G,'TriggerServerEvent') then
    TriggerServerEvent('bngeVoiceAction',jsonEncode({ action=action,player=player }))
  end
end
function M.uiState()
  return { options=options, status=status, state=state, me=config and config.me or '', available=config ~= nil,
    companion=net.isConnected and net.isConnected() or false,
    notice=clock-noticeAt < 6 and notice or nil }
end
function M.reset()
  local hadVoice = config ~= nil or enabled
  enabled, options.enabled, options.server = false, false, false
  held, config, state, status = {}, nil, {}, {}
  testAt = -math.huge
  notice = nil
  if rawget(_G,'guihooks') then guihooks.trigger('BeamNGEarthVoice', {visible=false}) end
  if hadVoice then send('control',options); net.send({type='voice',action='reset'}) end
end
function M.update(dt, anchor, player, inSession, focused)
  clock = clock + dt
  if (not focused or not inSession) and clock - testAt < 0.4 then M.test(false) end
  if clock >= hudAt then
    hudAt = clock + .25
    local names = {}
    for _, uid in ipairs(type(status.speaking)=='table' and status.speaking or {}) do
      for _, p in pairs(state.roster or {}) do
        if p.base and uid >= p.base and uid < p.base+3 then names[#names+1]=p.name; break end
      end
    end
    if rawget(_G,'guihooks') then guihooks.trigger('BeamNGEarthVoice', {
      visible=inSession and enabled, talk=status.talk or '', microphone=status.microphone or {},
      speaking=names, error=status.error or '', testing=status.testing==true,
    }) end
  end
  if not handlers and rawget(_G,'AddEventHandler') then
    AddEventHandler('bngeVoiceConfig',onConfig,'beamngEarth')
    AddEventHandler('bngeVoiceState',onState,'beamngEarth'); handlers = true
    AddEventHandler('bngeVoiceActionResult',function(raw)
      local d=decode(raw)
      if d and type(d.text)=='string' then notice, noticeAt={ok=d.ok==true,text=d.text:sub(1,200)},clock end
    end,'beamngEarth')
  end
  -- A player/anchor can be unavailable for a frame as a menu closes or the
  -- world rebases. That is not a BeamMP leave and must not revoke opt-in.
  if not inSession then
    if config or enabled then M.reset() end
    held = {}; return
  end
  if not anchor or not player then held = {}; return end
  if not focused then held = {} end
  elapsed = elapsed + dt
  if elapsed < 0.1 then return end
  elapsed = 0
  local x,y,z = anchor:enuToEcef(player.x,player.y,player.z or 0)
  local role = enabled and clock-testAt >= .4 and (options.server and held.s and 's' or held.g and 'g' or held.p and 'p' or '') or ''
  if rawget(_G,'TriggerServerEvent') then
    TriggerServerEvent('bngeVoicePose',jsonEncode({pos={x,y,z},talk=role,enabled=enabled,global=options.server}))
  end
  if config then
    net.send({type='voice',action='ptt',talk=role})
    local right = {anchor.ex,anchor.ey,anchor.ez}
    if rawget(_G,'getCameraQuat') then
      local q = getCameraQuat(); local r = q * vec3(1,0,0)
      right = {anchor.ex*r.x+anchor.nx*r.y+anchor.ux*r.z,
        anchor.ey*r.x+anchor.ny*r.y+anchor.uy*r.z, anchor.ez*r.x+anchor.nz*r.y+anchor.uz*r.z}
    end
    net.send({type='voice',action='pose',pos={x,y,z},right=right})
  end
end
return M
