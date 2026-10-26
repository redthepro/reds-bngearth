// Pure presentation model shared by the in-game panel and headless UI checks.
const list = (value) => (Array.isArray(value) ? value : [])
export const channels = [
  {
    id: 'p',
    name: 'Nearby',
    key: 'Alt+V',
    detail: 'Players within 80 m · positional audio',
  },
  {
    id: 's',
    name: 'Global',
    key: 'Alt+N',
    detail: 'Everyone who joined global on this server',
  },
  {
    id: 'g',
    name: 'Private group',
    key: 'Alt+B',
    detail: 'Only members of your invited group',
  },
]
export function normalize(data) {
  const voice = data?.voice || {},
    status = voice.status || {},
    options = voice.options || {}
  return {
    session: data?.session === true,
    social: {
      players: [],
      ...data?.social,
      players: list(data?.social?.players),
    },
    voice: {
      ...voice,
      options: {
        enabled: false,
        server: false,
        input: '',
        output: '',
        ...options,
        volumes:
          list(options.volumes).length === 3 ? options.volumes : [1, 1, 1],
        muted: list(options.muted),
      },
      status: {
        ...status,
        connected: list(status.connected),
        speaking: list(status.speaking),
        inputs: list(status.inputs),
        outputs: list(status.outputs),
        microphone: status.microphone || {},
      },
      state: {
        group: '',
        owner: '',
        invite: '',
        ...voice.state,
        roster: voice.state?.roster || {},
      },
    },
  }
}
export function playersIn(data) {
  const roster = data.voice.state.roster
  // Merge by server ID, never by display name: different players can share a name.
  const rows = new Map(
    data.social.players.map((p) => [String(p.id), { ...p, id: String(p.id) }]),
  )
  for (const [id, p] of Object.entries(roster)) {
    if (!data.social.supported || rows.has(id))
      rows.set(id, { ...rows.get(id), ...p, id })
  }
  return [...rows.values()]
    .map((p) => ({
      ...p,
      self: p.id === String(data.social.me || data.voice.me),
      speaking:
        Number.isFinite(p.base) &&
        data.voice.status.speaking.some(
          (id) => id >= p.base && id < p.base + 3,
        ),
      muted: data.voice.options.muted.includes(p.base),
      inGroup: !!p.group && p.group === data.voice.state.group,
    }))
    .sort(
      (a, b) =>
        Number(b.self) - Number(a.self) ||
        String(a.name).localeCompare(String(b.name)) ||
        a.id.localeCompare(b.id),
    )
}
export function micLabel(microphone, testing) {
  if (!microphone?.active)
    return testing ? 'Opening microphone…' : 'Microphone idle'
  return microphone.detected
    ? 'Audio detected · microphone working'
    : 'Microphone open · speak to check the level'
}
