"""Exercise the real JS-generated expressions inside BeamNG's Lua callback wrapper."""
import json
from pathlib import Path
import subprocess
import unittest

from lupa.luajit21 import LuaRuntime

ROOT = Path(__file__).resolve().parents[2]


class SocialBridgeTest(unittest.TestCase):
    def test_callback_expression_reads_state_and_changes_voice(self):
        module = (ROOT/'mod/beamng_earth/ui/ui-vue/mods/beamngEarth/socialBridge.mjs').as_uri()
        script = ('import {luaCall} from '+json.dumps(module)+'; '
                  'console.log(JSON.stringify([luaCall("b.socialState()"),'
                  'luaCall(\'b.voiceControl("enable")\'),luaCall("b.voiceDevices()")]))')
        result = subprocess.run(['node','--input-type=module','-e',script],capture_output=True,text=True,check=True)
        expressions = json.loads(result.stdout)
        lua = LuaRuntime()
        lua.execute('''
            enabled, enumerated = false, false
            beamngEarth_core = {
              socialState=function() return {enabled=enabled} end,
              voiceControl=function(raw) enabled=raw=='enable'; return true end,
              voiceDevices=function() enumerated=true; return true end,
            }
            guihooks={trigger=function(name,id,data) callback=data end}
        ''')
        def execute(expression):
            # This exact wrapping is visible in beamng.log: statements cannot
            # be interpolated directly into its third argument.
            lua.execute('guihooks.trigger("onBNGAPICallback",1,'+expression+')')
            return lua.globals().callback
        self.assertFalse(execute(expressions[0])['enabled'])
        self.assertTrue(execute(expressions[1]))
        self.assertTrue(execute(expressions[0])['enabled'])
        self.assertTrue(execute(expressions[2]))
        self.assertTrue(lua.globals().enumerated)
        lua.execute('beamngEarth_core=nil')
        self.assertIsNone(execute(expressions[0]))


if __name__ == '__main__': unittest.main()
