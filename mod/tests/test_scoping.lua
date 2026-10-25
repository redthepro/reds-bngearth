-- luajit mod/tests/test_scoping.lua  (every mod Lua file): no global read of a name the same file declares as a local.
-- A function that uses a local declared further down reads the (nil) global of that name instead: the BeamMP session's
-- test switch errored that way (remoteOwners) and roads.lua's prewarm after a level reload never ran (2026-10-22).
-- Inspect LuaJIT bytecode directly; this also works with Lupa's embedded LuaJIT.
local dir = (arg[0]:match('^(.*)[/\\]') or '.')
local T = dofile(dir .. '/testlib.lua')
local root = dir .. '/../beamng_earth/lua'

local files = {}
local windows = package.config:sub(1, 1) == '\\'
local command = windows and ('dir /s /b "' .. root:gsub('/', '\\') .. '\\*.lua"')
  or ('find "' .. root .. '" -name "*.lua"')
local p = assert(io.popen(command))
for f in p:lines() do files[#files + 1] = f end
p:close()
T.check(#files > 20, 'found the mod\'s Lua files: ' .. #files)

local bad = {}
local util, bit = require('jit.util'), require('bit')
-- Derive GGET's opcode from a tiny chunk instead of hard-coding an opcode number.
local probe = assert(loadstring('return __scoping_probe__'))
local gget = bit.band(util.funcbc(probe, 1), 255)
local function globals(fn, out)
  local pc = 1
  while true do
    local instruction = util.funcbc(fn, pc)
    if not instruction then break end
    if bit.band(instruction, 255) == gget then
      out[util.funck(fn, -bit.rshift(instruction, 16) - 1)] = true
    end
    pc = pc + 1
  end
  local index = -1
  while true do
    local constant = util.funck(fn, index)
    if constant == nil then break end
    if type(constant) == 'proto' then globals(constant, out) end
    index = index - 1
  end
end
local nested = {}
globals(assert(loadstring('local fn = function() return __late_local__ end; local __late_local__ = 1; return fn')), nested)
T.check(nested.__late_local__ == true, 'nested function reads a later local as a global')
local scoped = {}
globals(assert(loadstring('local __early_local__ = 1; return function() return __early_local__ end')), scoped)
T.check(scoped.__early_local__ == nil, 'properly scoped upvalues are not globals')
for _, f in ipairs(files) do
  local input = assert(io.open(f))
  local src = input:read('*a')
  input:close()
  local seen = {}
  globals(assert(loadfile(f)), seen)
  for g in pairs(seen) do
      if src:find('\n%s*local%s+' .. g .. '%f[^%w_]') or src:find('\n%s*local%s+function%s+' .. g .. '%f[^%w_]')
          or src:find('\n%s*local%s+[%w_,%s]-,%s*' .. g .. '%f[^%w_]') then
        bad[#bad + 1] = f:gsub('\\', '/'):match('lua/(.*)$') .. ': ' .. g
      end
  end
end
T.check(#bad == 0, 'no global reads of a file\'s own locals: ' .. table.concat(bad, ', '))
T.done('test_scoping')
