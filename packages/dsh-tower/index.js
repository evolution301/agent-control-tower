/**
 * dsh-tower — DeepSeek Harness bundle for agent-control-tower.
 *
 * Exports the Cordis plugin contract (`name` / `inject` / `apply`) that the
 * loader mounts through this bundle's `cordis.patch.yml` row, and registers
 * five tools on the harness tool registry so a DSH agent can drive a control
 * tower without leaving the conversation:
 *
 *   tower_dispatch  dispatch a brief to a role and get its report + verdict
 *   tower_inbox     read the reports roles have filed
 *   tower_accept    verify a reported task and accept it (or reject with reasons)
 *   tower_rework    send a task back with feedback and re-dispatch it
 *   tower_status    list the task board
 *
 * The tools shell out to the `tower` CLI (or `python -m control_tower`), so the
 * Python package remains the single implementation; this bundle is the DSH-side
 * adapter. No runtime dependencies.
 *
 * @module
 */

import { execFile, spawn, spawnSync } from 'node:child_process'
import net from 'node:net'
import { existsSync, mkdirSync, openSync, writeSync, closeSync, readFileSync, unlinkSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'

/** Cordis plugin name (reader-facing label in diagnostics). */
export const name = 'dsh-tower'

/** Services this plugin requires to be mounted before `apply`. */
export const inject = ['tools']

/** Plugin version, mirroring package.json (a test keeps the two in step). */
export const VERSION = '0.7.3'

/** Resolved runtime settings. */
function settings(config = {}) {
  const workspace = resolve(config.workspace ?? process.cwd())
  return {
    workspace,
    towerBin: config.towerBin ?? 'tower',
    python: config.python ?? 'python',
    timeoutMs: config.timeoutMs ?? 30 * 60 * 1000,
    panel: config.panel !== false,
    panelPort: Number(config.panelPort ?? 8787),
  }
}

/** Run the tower CLI and parse its JSON output. */
function runTower(settings, args) {
  const usePython = !settings.towerBin || settings.towerBin === '__python__'
  const file = usePython ? settings.python : settings.towerBin
  const argv = usePython ? ['-m', 'control_tower', ...args] : args
  return new Promise((resolvePromise, reject) => {
    execFile(
      file,
      argv,
      { cwd: settings.workspace, timeout: settings.timeoutMs, maxBuffer: 20 * 1024 * 1024 },
      (error, stdout, stderr) => {
        const text = (stdout ?? '').trim()
        const errText = (stderr ?? '').trim()
        if (text) {
          const parsed = parseJsonOutput(text)
          if (parsed !== undefined) {
            resolvePromise({ ok: !error, code: error?.code ?? 0, data: parsed, stderr: errText })
            return
          }
        }
        if (error) {
          reject(new Error(`tower ${args.join(' ')} failed: ${errText || error.message}`))
          return
        }
        resolvePromise({ ok: true, code: 0, data: text, stderr: errText })
      },
    )
  })
}

/**
 * Parse the JSON document in a stdout blob, if any.
 *
 * The CLI prints only JSON in `--json` mode, but older releases printed the
 * human report first (`task T-0001 ... / --- report ---`). Trying every `{`/`[`
 * start keeps the bundle working against either CLI: taking only the first one
 * silently degraded the parsed task to a string and then failed the output
 * schema with `"value.task" must be an object`.
 */
export function parseJsonOutput(text) {
  for (let i = 0; i < text.length; i += 1) {
    if (text[i] !== '{' && text[i] !== '[') continue
    try {
      return JSON.parse(text.slice(i))
    } catch {
      // not the payload — keep looking
    }
  }
  return undefined
}

/** Fail early and clearly when the workspace is not a tower. */
function assertWorkspace(settings) {
  const configCandidates = ['tower.toml', 'tower.yaml', 'tower.yml', 'tower.json']
  if (!configCandidates.some((f) => existsSync(resolve(settings.workspace, f)))) {
    throw new Error(
      `no tower config in ${settings.workspace} — run \`tower init .\` there, or set the plugin's \`workspace\` option`,
    )
  }
}

/** Compact one-line summary of a dispatch result for the model. */
function summarizeDispatch(data) {
  if (typeof data === 'string') return data
  const task = data.task ?? data
  const lines = [
    `task ${task.id} role=${task.role} status=${task.status}`,
    `rounds=${task.round ?? 0}/${task.max_rounds ?? 1}` +
      (task.write_scopes?.length ? ` scope=${task.write_scopes.join(',')}` : ''),
  ]
  if (task.report) {
    lines.push('', 'summary:', task.report.summary || '(none)')
    if (task.report.files?.length) {
      lines.push('files:')
      for (const f of task.report.files.slice(0, 12)) lines.push(`  - ${f}`)
    }
    if (task.report.checks?.length) {
      lines.push('checks:')
      for (const c of task.report.checks.slice(0, 12)) lines.push(`  - ${c}`)
    }
    if (task.report.open_issues?.length) {
      lines.push('open issues:')
      for (const i of task.report.open_issues.slice(0, 8)) lines.push(`  - ${i}`)
    }
  }
  return lines.join('\n')
}

const textOut = (text) => [{ type: 'text', text }]

/**
 * Hoist per-property `required: true` flags into the object node's `required`
 * array, which is the only spelling DSH's `assertSupportedJsonSchema` accepts.
 *
 * A `required` key inside a property node (`{ type: 'string', required: true }`)
 * makes `ctx.tools.register()` throw `JsonSchemaError: ... required is not
 * supported on type "string"`; the bundle then fails to mount, the failure is
 * only visible in the host log, and the tools silently never appear.
 */
function normalizeOutputSchema(node) {
  if (node === null || typeof node !== 'object' || Array.isArray(node)) return node
  const out = { ...node }
  if (out.properties && typeof out.properties === 'object') {
    const properties = {}
    const required = new Set(Array.isArray(out.required) ? out.required : [])
    for (const [name, property] of Object.entries(out.properties)) {
      if (property && typeof property === 'object' && !Array.isArray(property)) {
        const { required: flag, ...rest } = property
        if (flag === true) required.add(name)
        properties[name] = normalizeOutputSchema(rest)
      } else {
        properties[name] = property
      }
    }
    out.properties = properties
    if (required.size > 0) out.required = [...required]
    else delete out.required
  }
  return out
}

/**
 * Expand the harness' field-map shorthand into standard JSON Schema.
 *
 * `{ role: { type: 'string', required: true, description: ... } }` is accepted by
 * the harness, but providers that validate tool schemas with plain JSON-Schema
 * rules reject the property objects ("... is not of type string") and fail the
 * whole turn. Emitting an explicit object schema keeps both happy.
 */
function normalizeParameters(parameters) {
  if (parameters === null || typeof parameters !== 'object' || Array.isArray(parameters)) {
    return parameters
  }
  if (parameters.type === 'object' || parameters.properties) return parameters
  const properties = {}
  const required = []
  for (const [name, spec] of Object.entries(parameters)) {
    if (spec === null || typeof spec !== 'object' || Array.isArray(spec)) continue
    const { required: flag, ...rest } = spec
    properties[name] = rest
    if (flag === true) required.push(name)
  }
  return {
    type: 'object',
    properties,
    ...(required.length ? { required } : {}),
    additionalProperties: false,
  }
}

/** Attach a tool definition, normalising both schemas for the harness. */
const tool = (def) => ({
  ...def,
  parameters: normalizeParameters(def.parameters),
  output: { ...def.output, schema: normalizeOutputSchema(def.output?.schema) },
})

/**
 * Mount the five tower tools on `ctx.tools`.
 *
 * @param ctx - Cordis context with the `tools` service injected.
 * @param config - optional configuration (see cordis.patch.yml notes).
 * @returns disposer that unregisters the tools.
 */
/**
 * Whether something already listens on 127.0.0.1:<port>.
 *
 * A connection attempt is the cheapest liveness check and avoids depending on a
 * platform-specific tool; a closed port answers with ECONNREFUSED immediately.
 */
export function portOpen(port, timeoutMs = 500) {
  return new Promise((resolvePromise) => {
    const socket = net.connect({ host: '127.0.0.1', port })
    let settled = false
    const finish = (ok) => {
      if (settled) return
      settled = true
      socket.destroy()
      resolvePromise(ok)
    }
    socket.setTimeout(timeoutMs)
    socket.once('connect', () => finish(true))
    socket.once('error', () => finish(false))
    socket.once('timeout', () => finish(false))
  })
}

/** Wait `ms` milliseconds (injectable so tests do not pay real time). */
function sleep(ms) {
  return new Promise((resolvePromise) => setTimeout(resolvePromise, ms))
}

/**
 * Path of the per-port lock file that serialises panel starts.
 *
 * Two DSH windows (or two profiles) mount this bundle at once; both see a closed
 * port and both spawn `tower panel`, so one loses the bind race and stays behind
 * as an orphan. The lock makes the start exclusive across processes.
 */
export function panelLockPath(cfg) {
  return join(cfg.workspace, '.tower', `panel-${cfg.panelPort}.lock`)
}

/** Whether a pid is still running (signal 0 never reaches the target). */
function pidAlive(pid) {
  const n = Number(pid)
  if (!Number.isInteger(n) || n <= 0) return false
  try {
    process.kill(n, 0)
    return true
  } catch (error) {
    return error?.code === 'EPERM'
  }
}

/**
 * Take the panel lock, or return null when another live process holds it.
 *
 * A lock whose pid is gone is stale (a crashed host, a reboot) and is replaced:
 * refusing to start forever would leave the settings section permanently empty.
 */
export function acquirePanelLock(cfg) {
  const path = panelLockPath(cfg)
  try {
    mkdirSync(dirname(path), { recursive: true })
  } catch {
    return null
  }
  for (let attempt = 0; attempt < 2; attempt += 1) {
    try {
      const fd = openSync(path, 'wx')
      writeSync(fd, String(process.pid))
      closeSync(fd)
      return { path, pid: process.pid }
    } catch (error) {
      if (error?.code !== 'EEXIST') return null
      let holder = null
      try {
        holder = Number(readFileSync(path, 'utf8').trim())
      } catch {
        holder = null
      }
      if (holder && pidAlive(holder)) return null
      try {
        unlinkSync(path)
      } catch {
        return null
      }
    }
  }
  return null
}

/** Drop a lock this process owns; a foreign holder is never touched. */
export function releasePanelLock(lock) {
  if (!lock?.path || lock.pid !== process.pid) return
  try {
    unlinkSync(lock.path)
  } catch {
    // already gone
  }
}

/**
 * Kill a detached panel and the interpreter it started.
 *
 * `tower` on Windows is a launcher exe in front of `python.exe`, so a bare
 * `child.kill()` leaves the real server running — that half-kill is the other
 * half of the duplicate-panel bug. `/T` takes the whole tree.
 */
export function killPanel(child, deps = {}) {
  if (!child) return
  const run = deps.spawnSync ?? spawnSync
  try {
    if (process.platform === 'win32' && child.pid) {
      run('taskkill', ['/PID', String(child.pid), '/T', '/F'], { stdio: 'ignore', windowsHide: true })
      return
    }
    child.kill?.('SIGTERM')
  } catch {
    // best effort: a panel that already exited is not an error
  }
}

/** Spawn `tower panel` detached, in this workspace. */
function defaultSpawnPanel(cfg) {
  const child = spawn(cfg.towerBin, ['panel', '--port', String(cfg.panelPort)], {
    cwd: cfg.workspace,
    windowsHide: true,
    detached: true,
    stdio: 'ignore',
  })
  child.unref?.()
  return child
}

/**
 * Start `tower panel` unless this machine already serves it.
 *
 * The settings section embeds that URL, so the plugin owns the process: the start
 * is exclusive (lock file), the child is spawned detached (the harness must not
 * tie it to a turn), and it is only kept once the port actually answers. A child
 * that never comes up — it lost the bind race, the CLI is missing, the port is
 * held by something else — is killed instead of being left as an orphan.
 *
 * `deps` is an injection seam for the tests (`portOpen`, `spawnPanel`, `killPanel`,
 * `sleep`); production always uses the real ones.
 *
 * @returns `{ child, lock }` when this call started the panel, else `null`.
 */
export async function ensurePanel(cfg, deps = {}) {
  if (cfg.panel === false) return null
  const checkPort = deps.portOpen ?? portOpen
  const wait = deps.sleep ?? sleep
  const kill = deps.killPanel ?? killPanel
  if (await checkPort(cfg.panelPort)) return null

  const lock = acquirePanelLock(cfg)
  if (!lock) return null

  let child = null
  try {
    const spawnPanel = deps.spawnPanel ?? defaultSpawnPanel
    child = spawnPanel(cfg)
  } catch {
    child = null
  }
  if (!child) {
    releasePanelLock(lock)
    return null
  }

  const budgetMs = Number(cfg.panelStartTimeoutMs ?? 6000)
  const pollMs = Math.max(50, Number(cfg.panelPollMs ?? 150))
  const attempts = Math.max(1, Math.ceil(budgetMs / pollMs))
  for (let i = 0; i < attempts; i += 1) {
    await wait(pollMs)
    if (await checkPort(cfg.panelPort)) return { child, lock }
    if (child.exitCode !== null && child.exitCode !== undefined) break
  }

  kill(child)
  releasePanelLock(lock)
  return null
}

export function apply(ctx, config = {}) {
  const cfg = settings(config)
  let panel = null
  ensurePanel(cfg)
    .then((started) => {
      panel = started
    })
    .catch(() => {})
  const disposers = [
    ctx.tools.register(dispatchTool(cfg)),
    ctx.tools.register(inboxTool(cfg)),
    ctx.tools.register(acceptTool(cfg)),
    ctx.tools.register(reworkTool(cfg)),
    ctx.tools.register(statusTool(cfg)),
    ctx.tools.register(rolesTool(cfg)),
    ctx.tools.register(roleAddTool(cfg)),
    ctx.tools.register(roleRemoveTool(cfg)),
  ]
  return () => {
    for (const dispose of disposers) dispose?.()
    if (panel) {
      killPanel(panel.child)
      releasePanelLock(panel.lock)
    }
  }
}

/** `tower_dispatch`: create a task for one role and return its report. */
export function dispatchTool(cfg) {
  return tool({
    name: 'tower_dispatch',
    description:
      'Dispatch work to a role agent through the control tower. Creates a task, renders the report contract, runs the role adapter and returns the role\'s report (summary/files/checks/open issues) plus the task id. Use this instead of pasting a brief by hand when the project uses agent-control-tower.',
    parameters: {
      role: { type: 'string', required: true, description: 'Role name from tower.toml (e.g. backend, frontend, qa).' },
      brief: { type: 'string', required: true, description: 'The task brief. Be explicit about deliverable and acceptance.' },
      batch: { type: 'string', description: 'Optional batch label to group related tasks.' },
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: true,
        properties: {
          ok: { type: 'boolean', required: true },
          task: { type: 'object' },
          text: { type: 'string', required: true },
        },
      },
      render: (_args, value) => textOut(value.text),
    },
    async execute(args) {
      assertWorkspace(cfg)
      const argv = ['dispatch', String(args.role ?? ''), String(args.brief ?? ''), '--json']
      if (args.batch) argv.push('--batch', String(args.batch))
      const { data } = await runTower(cfg, argv)
      const text = summarizeDispatch(data)
      return { ok: true, task: data?.task ?? data, text }
    },
    presentCall(args) {
      return { card: 'command', title: `tower dispatch ${args.role ?? ''}`, kind: 'other' }
    },
  })
}

/** `tower_inbox`: read the reports that roles have filed. */
export function inboxTool(cfg) {
  return tool({
    name: 'tower_inbox',
    description:
      'Read the control tower inbox: the reports roles filed (task id, role, summary, file and check counts). Use it to see what came back before accepting anything.',
    parameters: {
      all: { type: 'boolean', description: 'Include reports already marked read (default false).' },
      mark_read: { type: 'boolean', description: 'Mark every listed report as read afterwards.' },
    },
    output: {
      schema: { type: 'object', additionalProperties: true, properties: { text: { type: 'string', required: true } } },
      render: (_args, value) => textOut(value.text),
    },
    async execute(args) {
      assertWorkspace(cfg)
      const argv = ['inbox']
      if (args.all) argv.push('--all')
      if (args.mark_read) argv.push('--read')
      const { data } = await runTower(cfg, argv)
      const text = typeof data === 'string' ? data : JSON.stringify(data, null, 2)
      return { text: text || 'inbox empty' }
    },
    presentCall() {
      return { card: 'generic', title: 'Read control tower inbox', kind: 'other' }
    },
  })
}

/** `tower_accept`: verify a reported task and accept it. */
export function acceptTool(cfg) {
  return tool({
    name: 'tower_accept',
    description:
      'Verify and accept a reported task. Re-checks that reported files exist, that every check line carries a result, and runs the tower\'s verify_commands. If verification fails the task goes back to rework and the reasons are returned — do not accept on the strength of the role\'s own summary.',
    parameters: {
      task: { type: 'string', required: true, description: 'Task id, e.g. T-0001.' },
      note: { type: 'string', description: 'Optional note recorded with the acceptance.' },
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: true,
        properties: {
          ok: { type: 'boolean', required: true },
          accepted: { type: 'boolean', required: true },
          text: { type: 'string', required: true },
        },
      },
      render: (_args, value) => textOut(value.text),
    },
    async execute(args) {
      assertWorkspace(cfg)
      const argv = ['accept', String(args.task ?? '')]
      if (args.note) argv.push('--note', String(args.note))
      const { data, code } = await runTower(cfg, argv)
      const text = typeof data === 'string' ? data : JSON.stringify(data, null, 2)
      const accepted = /ACCEPTED/.test(text) && code === 0
      return { ok: code === 0, accepted, text }
    },
    presentCall() {
      return { card: 'generic', title: 'Verify and accept report', kind: 'other' }
    },
  })
}

/** `tower_rework`: send a task back with feedback and re-dispatch it. */
export function reworkTool(cfg) {
  return tool({
    name: 'tower_rework',
    description:
      'Send a task back to its role with concrete feedback, then re-dispatch it (round + 1). Use this instead of dispatching a duplicate task. When the round budget is exhausted the tower returns an escalation message.',
    parameters: {
      task: { type: 'string', required: true, description: 'Task id, e.g. T-0001.' },
      note: { type: 'string', required: true, description: 'What is wrong and what to change.' },
    },
    output: {
      schema: { type: 'object', additionalProperties: true, properties: { text: { type: 'string', required: true } } },
      render: (_args, value) => textOut(value.text),
    },
    async execute(args) {
      assertWorkspace(cfg)
      const { data } = await runTower(cfg, ['rework', String(args.task ?? ''), '--note', String(args.note ?? '')])
      const text = typeof data === 'string' ? data : JSON.stringify(data, null, 2)
      return { text }
    },
    presentCall() {
      return { card: 'generic', title: 'Rework task', kind: 'other' }
    },
  })
}

/** `tower_status`: list the task board. */
export function statusTool(cfg) {
  return tool({
    name: 'tower_status',
    description:
      'List the control tower task board: id, role, status, round and brief for every task. Use it to answer "what is in flight" without reading the filesystem.',
    parameters: {},
    output: {
      schema: { type: 'object', additionalProperties: true, properties: { text: { type: 'string', required: true } } },
      render: (_args, value) => textOut(value.text),
    },
    async execute() {
      assertWorkspace(cfg)
      const { data } = await runTower(cfg, ['status', '--json'])
      const tasks = Array.isArray(data) ? data : []
      const text = tasks.length
        ? tasks.map((t) => `${t.id}\t${t.role}\t${t.status}\tround ${t.round}/${t.max_rounds}\t${(t.brief ?? '').slice(0, 70)}`).join('\n')
        : 'no tasks yet'
      return { text }
    },
    presentCall() {
      return { card: 'generic', title: 'Control tower status', kind: 'other' }
    },
  })
}

/** `tower_roles`: list the configured roles (bots) and their wired sessions. */
export function rolesTool(cfg) {
  return tool({
    name: 'tower_roles',
    description:
      'List the control tower roles (bots): adapter, write scope, round budget and the DSH session id wired to each one. Use it before dispatching to see who exists, and after creating a bot to confirm it is wired.',
    parameters: {},
    output: {
      schema: { type: 'object', additionalProperties: true, properties: { text: { type: 'string', required: true } } },
      render: (_args, value) => textOut(value.text),
    },
    async execute() {
      assertWorkspace(cfg)
      const { data } = await runTower(cfg, ['role', 'list', '--json'])
      const rows = Array.isArray(data) ? data : []
      const text = rows.length
        ? rows
            .map(
              (row) =>
                `${row.name}	${row.adapter}	rounds=${row.max_rounds}	scope=${(row.write_scopes || []).join(',') || '-'}	${row.session_id || 'NO session id'}`,
            )
            .join(String.fromCharCode(10))
        : 'no roles yet'
      return { text }
    },
    presentCall() {
      return { card: 'generic', title: 'Control tower roles', kind: 'other' }
    },
  })
}

/** `tower_role_add`: create a role (bot) with its charter and session wiring. */
export function roleAddTool(cfg) {
  return tool({
    name: 'tower_role_add',
    description:
      'Create a role (bot) in the control tower: appends it to tower.toml, writes roles/<name>/AGENT.md from the description and write scopes, and wires the DSH session id when one is given. Use it when the user asks for a new kind of worker, then dispatch to it.',
    parameters: {
      name: { type: 'string', required: true, description: 'Slug, e.g. keyword-research.' },
      description: { type: 'string', required: true, description: 'One line: what this bot owns.' },
      scopes: { type: 'string', description: 'Comma-separated write scopes, e.g. "content/,templates/".' },
      adapter: { type: 'string', description: 'mock | cli | dsh | openai (default dsh).' },
      session_id: { type: 'string', description: 'DSH session id of this bot, when it already exists.' },
      max_rounds: { type: 'integer', description: 'Rework budget before escalation (default 2).' },
    },
    output: {
      schema: { type: 'object', additionalProperties: true, properties: { text: { type: 'string', required: true } } },
      render: (_args, value) => textOut(value.text),
    },
    async execute(args) {
      assertWorkspace(cfg)
      const argv = ['role', 'add', String(args.name ?? '')]
      if (args.description) argv.push('--description', String(args.description))
      if (args.adapter) argv.push('--adapter', String(args.adapter))
      const scopes = String(args.scopes ?? '')
        .split(',')
        .map((scope) => scope.trim())
        .filter(Boolean)
      for (const scope of scopes) argv.push('--scope', scope)
      if (args.session_id) argv.push('--session', String(args.session_id))
      if (args.max_rounds) argv.push('--max-rounds', String(args.max_rounds))
      argv.push('--json')
      const { data } = await runTower(cfg, argv)
      const text =
        typeof data === 'string'
          ? data
          : `role ${data.name} ready — adapter=${data.adapter}, scope=${(data.write_scopes || []).join(',') || '-'}, charter=${data.charter}`
      return { text }
    },
    presentCall(args) {
      return { card: 'generic', title: `Create bot ${args.name ?? ''}`, kind: 'other' }
    },
  })
}

/** `tower_role_remove`: unregister a role (bot). */
export function roleRemoveTool(cfg) {
  return tool({
    name: 'tower_role_remove',
    description:
      'Remove a role (bot) from the control tower: drops it from tower.toml and from the session mapping. The charter under roles/<name>/ is kept unless purge is true.',
    parameters: {
      name: { type: 'string', required: true, description: 'Role to remove.' },
      purge: { type: 'boolean', description: 'Also delete roles/<name>/ (default false).' },
    },
    output: {
      schema: { type: 'object', additionalProperties: true, properties: { text: { type: 'string', required: true } } },
      render: (_args, value) => textOut(value.text),
    },
    async execute(args) {
      assertWorkspace(cfg)
      const argv = ['role', 'remove', String(args.name ?? '')]
      if (args.purge) argv.push('--purge')
      argv.push('--json')
      const { data } = await runTower(cfg, argv)
      return { text: typeof data === 'string' ? data : `role ${data.name} removed` }
    },
    presentCall(args) {
      return { card: 'generic', title: `Remove bot ${args.name ?? ''}`, kind: 'other' }
    },
  })
}
