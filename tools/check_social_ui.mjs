// Run after npm install --prefix build/social-ui-check vue@3.5.22
// @vue/compiler-sfc@3.5.22 @vue/server-renderer@3.5.22.
// Compiles real game templates and renders the social panel with a fake server.
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { createRequire } from 'node:module'
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const work = path.join(root, 'build/social-ui-check')
const require = createRequire(path.join(work, 'package.json'))
const compiler = require('@vue/compiler-sfc')
const { createSSRApp } = require('vue')
const { renderToString } = require('@vue/server-renderer')
const ui = path.join(root, 'mod/beamng_earth/ui/ui-vue/mods/beamngEarth')
for (const name of ['EarthSocial.vue', 'EarthSettings.vue', 'EarthMap.vue']) {
  const filename = path.join(ui, name)
  const { descriptor, errors } = compiler.parse(
    fs.readFileSync(filename, 'utf8'),
    { filename },
  )
  if (errors.length) throw errors[0]
  compiler.compileScript(descriptor, { id: name })
  const template = compiler.compileTemplate({
    source: descriptor.template.content,
    filename,
    id: name,
  })
  if (template.errors.length) throw template.errors[0]
  console.log('Compiled', name)
  if (name !== 'EarthSocial.vue') continue
  const compiled = compiler.compileScript(descriptor, {
    id: name,
    inlineTemplate: true,
    templateOptions: { ssr: true },
  })
  let code = compiled.content.replace(
    "'./socialModel.mjs'",
    JSON.stringify(pathToFileURL(path.join(ui, 'socialModel.mjs')).href),
  )
  // Render a deterministic connected fixture without game calls or microphone access.
  const fixture = {
    session: true,
    social: {
      supported: true,
      me: '0',
      mapVisible: false,
      players: [
        { id: '0', name: 'Your driver' },
        { id: '1', name: 'Alex' },
        { id: '2', name: '<Player & friends>' },
      ],
    },
    voice: {
      available: true,
      companion: true,
      me: '0',
      options: { enabled: true, server: false },
      status: {
        connected: ['p', 'g'],
        speaking: [3004],
        inputs: ['USB Microphone'],
        outputs: ['Headphones'],
      },
      state: {
        group: 'test',
        owner: '0',
        roster: {
          0: { name: 'Your driver', base: 3000, group: 'test', enabled: true },
          1: { name: 'Alex', base: 3003, group: 'test', enabled: true },
          2: { name: '<Player & friends>', base: 3006, group: '' },
        },
      },
    },
  }
  code = code.replace(
    'ref(normalize(null))',
    'ref(normalize(' + JSON.stringify(fixture) + '))',
  )
  const output = path.join(work, 'EarthSocial.generated.mjs')
  fs.writeFileSync(output, code)
  const component = (
    await import(pathToFileURL(output).href + '?v=' + Date.now())
  ).default
  const html = await renderToString(createSSRApp(component))
  if (
    !html.includes('Join global') ||
    !html.includes('Hold to test mic') ||
    !html.includes('&lt;Player &amp; friends&gt;')
  ) {
    throw new Error(
      'Social UI fixture did not render its controls or escaped player names',
    )
  }
  fs.writeFileSync(
    path.join(work, 'social-preview.html'),
    '<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Earth Social & Voice</title><style>body{margin:0;background:#141b24}' +
      descriptor.styles.map((s) => s.content).join('\n') +
      '</style></head><body>' +
      html +
      '</body></html>',
  )
  console.log(
    'Rendered social panel with connected players and escaped player names',
  )
}
