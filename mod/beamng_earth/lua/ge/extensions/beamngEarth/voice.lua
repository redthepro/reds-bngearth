-- Voice is opt-in and PTT only. No microphone data passes through game Lua.
local M = {}
local net = require('beamngEarth/net')
local enabled, handlers, elapsed = false, false, 0
local held, config, state, status = {}, nil, {}, {}
local options = { enabled=false, volumes={1,1,1}, muted={}, input='', output='' }
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
function M.onServerHello()
  if config then send('control',options); send('session',config); send('state',state) end
end
function M.talk(role, down)
  if role ~= 'p' and role ~= 's' and role ~= 'g' then return end
  held[role] = down == true or down == 1
  if not held[role] then net.send({type='voice',action='ptt',talk=''}) end
end
function M.control(raw)
  local d = type(raw) == 'table' and raw or decode(raw); if not d then return end
  options = { enabled=d.enabled == true, volumes=d.volumes or {1,1,1},
    muted=d.muted or {}, input=d.input or '', output=d.output or '' }
  enabled = options.enabled
  if not enabled then held = {} end
  send('control',options)
end
function M.action(action, player)
  if rawget(_G,'TriggerServerEvent') then
    TriggerServerEvent('bngeVoiceAction',jsonEncode({ action=action,player=player }))
  end
end
function M.uiState()
  return { options=options, status=status, state=state, me=config and config.me or '', available=config ~= nil }
end
function M.reset()
  held, config, state, status = {}, nil, {}, {}
  net.send({type='voice',action='reset'})
end
function M.update(dt, anchor, player, inSession, focused)
  if not handlers and rawget(_G,'AddEventHandler') then
    AddEventHandler('bngeVoiceConfig',onConfig,'beamngEarth')
    AddEventHandler('bngeVoiceState',onState,'beamngEarth'); handlers = true
  end
  if not inSession or not anchor or not player then
    if config then M.reset() end
    held = {}; return
  end
  if not focused then held = {} end
  elapsed = elapsed + dt
  if elapsed < 0.1 then return end
  elapsed = 0
  local x,y,z = anchor:enuToEcef(player.x,player.y,player.z or 0)
  local role = enabled and (held.s and 's' or held.g and 'g' or held.p and 'p' or '') or ''
  if rawget(_G,'TriggerServerEvent') then
    TriggerServerEvent('bngeVoicePose',jsonEncode({pos={x,y,z},talk=role}))
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
