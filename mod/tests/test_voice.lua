local dir = (arg[0]:match('^(.*)[/\\]') or '.')
local T = dofile(dir .. '/testlib.lua')
dofile(dir .. '/stubs.lua')
local sent, events, handlers = {}, {}, {}
package.loaded['beamngEarth/net'] = { send=function(d) sent[#sent+1]=d end }
function AddEventHandler(name, fn) handlers[name]=fn end
function jsonDecode(d) return d == 'config' and {me='1',base=3000} or {} end
function jsonEncode(d) return d end
function TriggerServerEvent(name, d) events[#events+1]={name=name,data=d} end
local v = require('beamngEarth/voice')
local a = require('beamngEarth/geodesy').newAnchor(38.9,-77)
local player = {x=10,y=20,z=30}
v.update(.1,a,player,true,true)
T.check(events[#events].data.talk == '', 'disabled: never transmits')
handlers.bngeVoiceConfig('config')
v.control({enabled=true})
v.talk('p',true); v.update(.1,a,player,true,true)
T.check(events[#events].data.talk == 'p', 'held proximity sends request')
T.check(sent[#sent].action == 'pose' and #sent[#sent].pos == 3, 'pose uses ECEF')
v.update(.1,a,player,true,false)
T.check(events[#events].data.talk == '', 'focus loss releases PTT')
v.talk('s',true); v.update(.1,a,player,true,true)
T.check(events[#events].data.talk == 's', 'global key selects global')
v.talk('s',false)
T.check(sent[#sent].action == 'ptt' and sent[#sent].talk == '', 'release is immediate')
v.reset()
T.check(not v.uiState().available and sent[#sent].action == 'reset', 'leave clears credentials')
T.done()
