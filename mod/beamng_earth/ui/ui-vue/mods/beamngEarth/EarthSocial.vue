<template>
  <section
    class="earth-social"
    :class="{ compact }"
    aria-label="Earth social and voice controls"
  >
    <header v-if="!compact" class="social-header">
      <div>
        <span class="eyebrow">BEAMNG.EARTH · MULTIPLAYER</span>
        <h1>Social &amp; Voice</h1>
        <p>Your people. Your channels. Your choice.</p>
      </div>
      <span class="badge" :class="{ good: model.session }">{{
        model.session ? 'In server' : 'Offline'
      }}</span>
    </header>
    <div v-if="!model.session" class="notice">
      Join a BeamNG.Earth BeamMP server to see players and use voice.
    </div>
    <div v-else-if="!voice.companion" class="notice warning">
      Start the matching BeamNG.Earth companion app to connect voice.
    </div>
    <div v-else-if="!voice.available" class="notice">
      This server has not provided voice access. Map visibility and the player
      list work independently.
    </div>
    <div v-if="voice.status.error" class="notice warning" role="alert">
      {{ voice.status.error }}. Check the microphone, output device and server
      connection.
    </div>
    <div v-if="message" class="notice" role="status">{{ message }}</div>
    <div
      v-if="voice.notice"
      class="notice"
      :class="{ warning: !voice.notice.ok }"
      role="status"
    >
      {{ voice.notice.text }}
    </div>

    <div class="social-grid">
      <div class="main-column">
        <section class="social-card">
          <div class="card-heading">
            <div>
              <h2>Voice channels</h2>
              <p>
                Push to talk. Voice and global membership reset when you leave
                the server.
              </p>
            </div>
            <button
              class="voice-switch"
              :class="{ on: voice.options.enabled }"
              role="switch"
              :aria-checked="voice.options.enabled"
              :disabled="!voice.available || !voice.companion"
              @click="control('enabled', !voice.options.enabled)"
            >
              <span class="switch-track" aria-hidden="true"><span></span></span>
              Voice {{ voice.options.enabled ? 'On' : 'Muted' }}
            </button>
          </div>
          <div class="channels">
            <article
              v-for="(channel, i) in channels"
              :key="channel.id"
              class="channel"
              :class="{ transmitting: voice.status.talk === channel.id }"
            >
              <div class="channel-line">
                <h3>{{ channel.name }}</h3>
                <kbd>{{ channel.key }}</kbd>
              </div>
              <p>{{ channel.detail }}</p>
              <div class="channel-state">
                <span
                  class="dot"
                  :class="{ good: connected(channel.id) }"
                ></span
                >{{ channelStatus(channel.id) }}
              </div>
              <button
                v-if="channel.id === 's'"
                :disabled="!voice.options.enabled"
                @click="control('server', !voice.options.server)"
              >
                {{ voice.options.server ? 'Leave global' : 'Join global' }}
              </button>
              <button
                v-if="channel.id === 'g'"
                :disabled="!voice.available"
                @click="action(voice.state.group ? 'leave' : 'create')"
              >
                {{ voice.state.group ? 'Leave group' : 'Create group' }}
              </button>
              <label class="volume"
                >Volume
                <input
                  type="range"
                  min="0"
                  max="2"
                  step="0.05"
                  :aria-label="channel.name + ' volume'"
                  :value="voice.options.volumes[i]"
                  @input="volume(i, $event.target.value)"
                /><span
                  >{{ Math.round(voice.options.volumes[i] * 100) }}%</span
                ></label
              >
            </article>
          </div>
          <p class="hint">
            Default bindings shown; change them in Controls → BeamNG.Earth.
          </p>
        </section>

        <section class="social-card">
          <div class="card-heading">
            <div>
              <h2>Microphone check</h2>
              <p>A private level test. Nobody hears this test.</p>
            </div>
            <span class="badge" :class="{ good: mic.detected }">{{
              mic.active ? 'Mic in use' : 'Mic idle'
            }}</span>
          </div>
          <div class="devices">
            <label
              >Microphone<select
                :value="voice.options.input"
                @change="control('input', $event.target.value)"
              >
                <option value="">
                  System default —
                  {{
                    voice.status.deviceDefaults?.input || 'refresh to detect'
                  }}
                </option>
                <option
                  v-for="name in voice.status.inputs"
                  :key="name"
                  :value="name"
                >
                  {{ name }}
                </option>
              </select></label
            >
            <label
              >Speakers / headphones<select
                :value="voice.options.output"
                @change="control('output', $event.target.value)"
              >
                <option value="">
                  System default —
                  {{
                    voice.status.deviceDefaults?.output || 'refresh to detect'
                  }}
                </option>
                <option
                  v-for="name in voice.status.outputs"
                  :key="name"
                  :value="name"
                >
                  {{ name }}
                </option>
              </select></label
            >
          </div>
          <div class="mic-footer device-state">
            <span
              >{{
                voice.status.deviceActive?.input
                  ? 'Mic in use: ' + voice.status.deviceActive.input
                  : 'Mic selected: ' +
                    (voice.options.input ||
                      voice.status.deviceDefaults?.input ||
                      'System default')
              }}<br />
              {{
                voice.status.deviceActive?.output
                  ? 'Output in use: ' + voice.status.deviceActive.output
                  : 'Output selected: ' +
                    (voice.options.output ||
                      voice.status.deviceDefaults?.output ||
                      'System default')
              }}</span
            >
            <button :disabled="!voice.companion" @click="refreshDevices">
              Refresh devices
            </button>
          </div>
          <p
            v-if="voice.status.deviceError"
            class="notice warning"
            role="alert"
          >
            {{ voice.status.deviceError }}
          </p>
          <div
            class="mic-meter"
            role="meter"
            aria-label="Microphone input level"
            aria-valuemin="0"
            aria-valuemax="100"
            :aria-valuenow="Math.round(micLevel * 100)"
          >
            <div :style="{ width: micLevel * 100 + '%' }"></div>
          </div>
          <div class="mic-footer">
            <span role="status">{{ micLabel(mic, voice.status.testing) }}</span>
            <button
              :disabled="
                !voice.options.enabled || !voice.companion || !voice.available
              "
              @pointerdown.prevent="startTest"
              @pointerup="stopTest"
              @pointerleave="stopTest"
              @pointercancel="stopTest"
              @keydown.space.prevent="startTest"
              @keyup.space.prevent="stopTest"
              @keydown.enter.prevent="startTest"
              @keyup.enter.prevent="stopTest"
              @blur="stopTest"
            >
              {{ testing ? 'Testing… release to stop' : 'Hold to test mic' }}
            </button>
          </div>
          <p class="hint">
            Stops on release, focus loss, or after 10 seconds. A moving level
            confirms capture; ask a second player to verify the full voice
            connection.
          </p>
        </section>
        <section class="social-card">
          <div class="card-heading">
            <div>
              <h2>Map visibility</h2>
              <p>
                Choose whether other players see your marker on the world map.
              </p>
            </div>
            <label class="toggle"
              ><input
                type="checkbox"
                :checked="model.social.mapVisible"
                :disabled="!model.social.supported"
                @change="visibility($event.target.checked)"
              />
              Show me on map</label
            >
          </div>
          <p class="hint">
            Hidden by default each session. The map’s Players menu has the same
            switch. Vehicle and server position sync still operate while your
            marker is hidden.
          </p>
        </section>
      </div>

      <aside class="social-card players-card">
        <div class="card-heading">
          <h2>
            Players <span class="count">{{ players.length }}</span>
          </h2>
        </div>
        <label class="search-label"
          >Find a player<input
            v-model="search"
            type="search"
            placeholder="Search this server"
            maxlength="80"
        /></label>
        <div v-if="voice.state.invite" class="invite" role="status">
          <strong>Group invitation</strong>
          <p>
            {{ voice.state.roster[voice.state.invite]?.name || 'A player' }}
            invited you.
          </p>
          <button @click="action('accept')">Join group</button
          ><button @click="action('decline')">Decline</button>
        </div>
        <p v-if="voice.state.group" class="hint">
          Private group · {{ groupCount }}/16 members{{
            owner ? ' · You are the owner' : ''
          }}
        </p>
        <div class="player-list">
          <article
            v-for="player in filtered"
            :key="player.id"
            class="player-row"
            :class="{ speaking: player.speaking }"
          >
            <span class="avatar" aria-hidden="true">{{
              (player.name || '?').slice(0, 1).toUpperCase()
            }}</span>
            <div class="player-body">
              <strong
                >{{ player.name }} <small v-if="player.self">you</small></strong
              >
              <p>
                {{
                  player.speaking
                    ? 'Speaking'
                    : player.enabled
                      ? 'Voice enabled'
                      : 'In server'
                }}<span v-if="player.inGroup"> · Group</span>
              </p>
              <p>{{ player.mapVisible ? 'Visible on map' : 'Map hidden' }}</p>
              <div v-if="!player.self" class="player-actions">
                <button
                  v-if="Number.isFinite(player.base)"
                  @click="mute(player.base)"
                >
                  {{ player.muted ? 'Unmute' : 'Mute' }}</button
                ><button
                  v-if="owner && player.group === ''"
                  @click="action('invite', player.id)"
                >
                  Invite</button
                ><button
                  v-if="owner && player.inGroup"
                  @click="action('kick', player.id)"
                >
                  Remove
                </button>
              </div>
            </div>
          </article>
          <p v-if="!players.length" class="hint">
            Waiting for the server’s player list…
          </p>
          <p v-else-if="!filtered.length" class="hint">No matching players.</p>
        </div>
      </aside>
    </div>
  </section>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { channels, normalize, playersIn, micLabel } from './socialModel.mjs'
import { luaCall } from './socialBridge.mjs'
defineProps({ compact: { type: Boolean, default: false } })
const model = ref(normalize(null)),
  search = ref(''),
  message = ref(''),
  testing = ref(false)
const voice = computed(() => model.value.voice),
  mic = computed(() => voice.value.status.microphone)
const micLevel = computed(() =>
  Math.min(1, Math.max(0, Number(mic.value.level) || 0) * 4),
)
const players = computed(() => playersIn(model.value))
const filtered = computed(() =>
  players.value.filter((p) =>
    String(p.name).toLowerCase().includes(search.value.toLowerCase()),
  ),
)
const owner = computed(
  () => !!voice.value.state.group && voice.value.state.owner === voice.value.me,
)
const groupCount = computed(() => players.value.filter((p) => p.inGroup).length)
let timer,
  testTimer,
  testStopTimer,
  mounted = true,
  request = 0,
  applied = 0
function call(expression, callback) {
  const api = window.bngApi
  if (!api?.engineLua) {
    message.value = 'Game connection unavailable.'
    return
  }
  api.engineLua(luaCall(expression), callback)
}
function refresh() {
  const id = ++request
  call('b.socialState()', (data) => {
    if (!mounted || id < applied) return
    applied = id
    model.value = normalize(data)
  })
}
function control(key, value) {
  applied = ++request
  if (key === 'enabled' && !value) stopTest()
  const data = JSON.stringify(JSON.stringify({ [key]: value }))
  call(`b.voiceControl(${data})`, refresh)
}
function volume(index, value) {
  const values = [...voice.value.options.volumes]
  values[index] = Number(value)
  model.value.voice.options.volumes = values
  control('volumes', values)
}
function connected(role) {
  return voice.value.status.connected.includes(role) && voice.value.companion
}
function channelStatus(role) {
  if (!voice.value.options.enabled) return 'Voice off'
  if (role === 's' && !voice.value.options.server) return 'Not joined'
  if (role === 'g' && !voice.value.state.group) return 'No group'
  return voice.value.status.talk === role
    ? 'Transmitting'
    : connected(role)
      ? 'Listening'
      : 'Connecting…'
}
function action(name, id = '') {
  call(`b.voiceAction(${JSON.stringify(name)}, ${JSON.stringify(id)})`, refresh)
  message.value = ''
}
function mute(base) {
  const values = [...voice.value.options.muted],
    index = values.indexOf(base)
  if (index < 0) values.push(base)
  else values.splice(index, 1)
  control('muted', values)
}
function visibility(on) {
  applied = ++request
  call(`b.mapVisibility(${on ? 'true' : 'false'})`, (ok) => {
    message.value = ok ? '' : 'Update the server to use map visibility.'
    refresh()
  })
}
function startTest() {
  if (testing.value || !voice.value.options.enabled) return
  testing.value = true
  call('b.voiceTest(true)')
  testTimer = setInterval(() => call('b.voiceTest(true)'), 100)
  testStopTimer = setTimeout(stopTest, 10000)
}
function stopTest() {
  if (!testing.value) return
  testing.value = false
  clearInterval(testTimer)
  clearTimeout(testStopTimer)
  call('b.voiceTest(false)')
}
function hidden() {
  if (document.hidden) stopTest()
}
function refreshDevices() {
  call('b.voiceDevices and b.voiceDevices()', refresh)
}
onMounted(() => {
  refresh()
  refreshDevices()
  timer = setInterval(refresh, 250)
  window.addEventListener('blur', stopTest)
  document.addEventListener('visibilitychange', hidden)
})
onUnmounted(() => {
  stopTest()
  mounted = false
  clearInterval(timer)
  window.removeEventListener('blur', stopTest)
  document.removeEventListener('visibilitychange', hidden)
})
</script>

<style scoped>
.voice-switch {
  display: flex;
  align-items: center;
  gap: 9px;
  white-space: nowrap;
  color: #fff;
  background: #71323c;
  border-color: #c15b68;
}
.voice-switch.on {
  background: #245c48;
  border-color: #6bd3a6;
}
.switch-track {
  display: block;
  width: 36px;
  height: 20px;
  padding: 3px;
  border-radius: 12px;
  background: #401e27;
}
.voice-switch.on .switch-track {
  background: #153c2e;
}
.switch-track span {
  display: block;
  width: 14px;
  height: 14px;
  border-radius: 50%;
  background: #f4d7dc;
  transition: transform 0.15s;
}
.voice-switch.on .switch-track span {
  transform: translateX(16px);
  background: #a5f4cf;
}
.device-state {
  margin-top: 12px;
  color: #afc4d5;
  font-size: 12px;
}
.earth-social {
  box-sizing: border-box;
  color: #e9eef4;
  background: #141b24;
  padding: 28px;
  overflow: auto;
  max-height: 100%;
  font: 15px/1.45 sans-serif;
  width: 100%;
}
.earth-social * {
  box-sizing: border-box;
}
.social-header,
.card-heading,
.channel-line,
.mic-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}
.social-header {
  margin-bottom: 24px;
}
.eyebrow {
  color: #81cbbb;
  font-size: 11px;
  letter-spacing: 0.14em;
}
h1 {
  font-size: 32px;
  margin: 5px 0;
}
h2 {
  font-size: 18px;
  margin: 0;
}
h3 {
  font-size: 16px;
  margin: 0;
}
p {
  margin: 6px 0;
  color: #aab8c8;
}
.social-grid {
  display: grid;
  grid-template-columns: minmax(0, 2fr) minmax(260px, 1fr);
  gap: 18px;
}
.main-column {
  display: grid;
  gap: 18px;
  align-content: start;
}
.social-card {
  border: 1px solid #354252;
  background: #1c2632;
  border-radius: 12px;
  padding: 20px;
}
.card-heading {
  margin-bottom: 16px;
  align-items: flex-start;
}
.channels {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
}
.channel {
  padding: 14px;
  border: 1px solid #3b495b;
  border-radius: 9px;
  background: #17212d;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.channel p {
  font-size: 12px;
  min-height: 36px;
}
.channel.transmitting {
  border-color: #68d7b7;
  box-shadow: inset 0 0 0 1px #68d7b7;
}
.channel-state {
  font-size: 13px;
  display: flex;
  align-items: center;
  gap: 6px;
}
.dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #7e8c9b;
  display: inline-block;
}
.dot.good {
  background: #68d7b7;
}
.badge {
  border-radius: 20px;
  padding: 5px 11px;
  background: #344255;
  color: #c0cbda;
  white-space: nowrap;
  font-size: 12px;
}
.badge.good {
  color: #a8f3d6;
  background: #20463e;
}
.toggle {
  display: flex;
  align-items: center;
  gap: 8px;
  white-space: nowrap;
  font-size: 13px;
}
.toggle input {
  accent-color: #68d7b7;
  width: 18px;
  height: 18px;
}
.volume {
  font-size: 12px;
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
  margin-top: auto;
}
.volume input {
  width: 100%;
  accent-color: #68d7b7;
}
.devices {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 14px;
}
.devices label,
.search-label {
  display: grid;
  gap: 7px;
  font-size: 12px;
  color: #b8c6d5;
}
select,
input[type='search'] {
  width: 100%;
  min-width: 0;
  background: #111b27;
  color: #eef5fc;
  border: 1px solid #4c5e73;
  border-radius: 6px;
  padding: 10px;
  font: inherit;
}
button {
  cursor: pointer;
  color: #d8ffed;
  background: #284c43;
  border: 1px solid #4c8b79;
  border-radius: 6px;
  padding: 8px 12px;
  font: inherit;
  font-size: 13px;
}
button:hover:enabled {
  background: #366655;
}
.voice-switch:not(.on):hover:enabled {
  background: #89404b;
}
.voice-switch.on:hover:enabled {
  background: #30765b;
}
button:disabled {
  opacity: 0.4;
  cursor: default;
}
button:focus-visible,
input:focus-visible,
select:focus-visible {
  outline: 2px solid #8be5cb;
  outline-offset: 3px;
}
kbd {
  font-size: 11px;
  padding: 2px 5px;
  border: 1px solid #546277;
  border-radius: 4px;
  color: #d3deeb;
  white-space: nowrap;
}
.hint {
  font-size: 12px;
  line-height: 1.5;
}
.notice {
  padding: 12px 16px;
  border-radius: 7px;
  background: #253c4b;
  margin: 0 0 16px;
}
.warning {
  background: #503e28;
  color: #ffe0b1;
}
.mic-meter {
  background: #0e1620;
  border: 1px solid #425066;
  border-radius: 8px;
  height: 12px;
  margin: 18px 0 12px;
  overflow: hidden;
}
.mic-meter div {
  height: 100%;
  background: linear-gradient(90deg, #418f84, #8fe6b2);
  transition: width 0.12s;
}
.mic-footer {
  font-size: 13px;
}
.players-card {
  align-self: start;
}
.count {
  font-size: 12px;
  color: #a2b3c7;
  margin-left: 5px;
}
.player-list {
  max-height: 560px;
  overflow: auto;
  margin-top: 12px;
}
.player-row {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 14px 0;
  border-bottom: 1px solid #354252;
}
.player-row.speaking .avatar {
  box-shadow: 0 0 0 2px #68d7b7;
  color: #b3ffe2;
}
.avatar {
  display: grid;
  place-items: center;
  flex-shrink: 0;
  width: 36px;
  height: 36px;
  border-radius: 50%;
  background: #34445a;
  color: #bbccdf;
}
.player-body {
  min-width: 0;
  overflow-wrap: anywhere;
}
.player-body strong {
  font-size: 14px;
}
.player-body p {
  font-size: 12px;
  margin: 3px 0;
}
.player-body small {
  color: #8acbb7;
  font-weight: normal;
}
.player-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
}
.player-actions button {
  padding: 4px 8px;
  font-size: 12px;
}
.invite {
  margin-top: 14px;
  background: #294139;
  border: 1px solid #4c8b79;
  padding: 12px;
  border-radius: 7px;
}
.invite button {
  margin-right: 6px;
}
.compact {
  padding: 0;
  background: transparent;
}
.compact .social-grid {
  grid-template-columns: 1fr;
}
.compact .player-list {
  max-height: 300px;
}
@media (max-width: 1000px) {
  .social-grid {
    grid-template-columns: 1fr;
  }
  .players-card {
    width: 100%;
  }
  .social-header h1 {
    font-size: 26px;
  }
}
@media (max-width: 650px) {
  .channels,
  .devices {
    grid-template-columns: 1fr;
  }
  .card-heading,
  .mic-footer {
    flex-wrap: wrap;
  }
  .earth-social {
    padding: 14px;
  }
  .compact {
    padding: 0;
  }
}
</style>
