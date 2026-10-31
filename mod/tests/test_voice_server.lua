local dir = (arg[0]:match('^(.*)[/\\]') or '.')
local T = dofile(dir .. '/testlib.lua')
local decoded = { endpoint={host='voice.example',port=64738,fingerprint=string.rep('0',64)}, creds={players={}} }
Util = { JsonDecode=function(s) return decoded[s] end, JsonEncode=function(d) return '{}' end }
local realOpen, realRename = io.open, os.rename
io.open = function(path, mode)
  if path == 'voice/endpoint.json' then return {read=function() return 'endpoint' end,close=function() end} end
  if path == 'voice/credentials.json' then return {read=function() return 'creds' end,close=function() end} end
  if path:match('^voice/') then return {write=function() end,close=function() end} end
  return realOpen(path,mode)
end
os.rename = function() return true end
local connected = {[1]=true,[2]=true,[3]=true}
MP = { GetPlayerName=function(pid) return connected[pid] and 'Player'..pid or nil end, TriggerClientEvent=function() end,
  RegisterEvent=function() end, CreateEventTimer=function() end }
local V = dofile(dir .. '/../../beammp/Server/BeamNGEarth/lib/voice.lua')
local function pose(pid,role)
  decoded.request={pos={1100000+pid*10,-4800000,4000000},talk=role}
  BNGE_voicePose(pid,'request')
end
local function action(pid,action,target)
  decoded.request={action=action,player=target};BNGE_voiceAction(pid,'request')
end
pose(1,'p');pose(2,'p');pose(3,'s')
BNGE_voiceTick()
T.check(V.players['1'].talk == 'p' and V.players['3'].talk == 's','channels granted independently')
action(1,'create')
local group = V.players['1'].group
T.check(group ~= '' and V.groups[group].owner == '1','creator owns private group')
action(3,'invite','2')
T.check(V.invites['2'] == nil,'outsider cannot invite into group')
for _=1,6 do BNGE_voiceTick() end
action(1,'invite','2')
T.check(V.invites['2'] and V.invites['2'].from == '1','owner can invite')
action(2,'accept')
T.check(V.players['2'].group == group,'invite accepted')
for _=1,6 do BNGE_voiceTick() end
action(2,'kick','1')
T.check(V.players['1'].group == group,'member cannot kick owner')
action(1,'kick','2')
T.check(V.players['2'].group == '','owner removes member')
for _=1,6 do BNGE_voiceTick() end
connected[2] = nil -- registry can lag behind a BeamMP disconnect notification
action(1,'invite','2')
T.check(V.invites['2'] == nil,'disconnected registry entry cannot receive an invitation')
for _=1,6 do BNGE_voiceTick() end
connected[2] = true
action(1,'invite','2')
connected[1] = nil
action(2,'accept')
T.check(V.players['2'].group == '', 'invitation from a disconnected player cannot be accepted')
for _=1,6 do BNGE_voiceTick() end
action(1,'invite','3')
T.check(V.invites['3'] == nil,'disconnected caller cannot invite connected players')
BNGE_voiceLeave(1)
T.check(V.players['1'] == nil and V.groups[group] == nil,'disconnect revokes identity and group')
for _=1,6 do BNGE_voiceTick() end
T.check(V.players['3'].talk == '', 'expired PTT stops transmitting')
io.open,os.rename = realOpen,realRename
T.done()
