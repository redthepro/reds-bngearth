import assert from 'node:assert/strict'
import {
  normalize,
  playersIn,
  micLabel,
} from '../beamng_earth/ui/ui-vue/mods/beamngEarth/socialModel.mjs'

const empty = normalize({
  social: { players: {} },
  voice: {
    options: { muted: {}, volumes: {} },
    status: { connected: {}, speaking: {}, inputs: {}, outputs: {} },
  },
})
assert.deepEqual(playersIn(empty), [])
assert.deepEqual(empty.voice.options.volumes, [1, 1, 1])
const active = normalize({
  session: true,
  social: {
    me: '0',
    players: [
      { id: '0', name: '<You>', mapVisible: true },
      { id: '1', name: 'Same name' },
      { id: '2', name: 'Same name' },
      { id: '3', name: 'Loading player' },
    ],
  },
  voice: {
    me: '0',
    state: {
      group: 'party',
      roster: {
        0: { name: '<You>', base: 3000, group: 'party' },
        1: { name: 'Same name', base: 3003, group: 'party' },
        2: { name: 'Same name', base: 3006, group: '' },
      },
    },
    status: { speaking: [3004] },
    options: { muted: [3006] },
  },
})
const rows = playersIn(active)
assert.equal(rows.length, 4)
assert.equal(rows[0].self, true)
assert.equal(rows[0].name, '<You>') // Vue renders names as text, never raw HTML.
assert.equal(rows.find((p) => p.id === '1').speaking, true)
assert.equal(rows.find((p) => p.id === '2').muted, true)
assert.equal(rows.find((p) => p.id === '3').base, undefined)
assert.equal(rows.filter((p) => p.inGroup).length, 2)
active.social.supported = true
active.voice.state.roster['99'] = { name: 'Disconnected player', base: 3999 }
assert.equal(
  playersIn(active).some((p) => p.id === '99'),
  false,
)
assert.match(
  micLabel({ active: true, detected: true }, false),
  /microphone working/,
)
assert.match(micLabel({}, true), /Opening/)
console.log(
  'Social model: empty Lua arrays, duplicate names, loading players, group/mute/speaker state passed',
)
