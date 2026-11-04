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

import { execFile } from 'node:child_process'
import { existsSync } from 'node:fs'
import { resolve } from 'node:path'

/** Cordis plugin name (reader-facing label in diagnostics). */
export const name = 'dsh-tower'

/** Services this plugin requires to be mounted before `apply`. */
export const inject = ['tools']

/** Plugin version, mirroring package.json. */
export const VERSION = '0.2.0'

/** Resolved runtime settings. */
function settings(config = {}) {
  const workspace = resolve(config.workspace ?? process.cwd())
  return {
    workspace,
    towerBin: config.towerBin ?? 'tower',
    python: config.python ?? 'python',
    timeoutMs: config.timeoutMs ?? 30 * 60 * 1000,
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

/** Attach a tool definition, normalising its output schema for the harness. */
const tool = (def) => ({
  ...def,
  output: { ...def.output, schema: normalizeOutputSchema(def.output?.schema) },
})

/**
 * Mount the five tower tools on `ctx.tools`.
 *
 * @param ctx - Cordis context with the `tools` service injected.
 * @param config - optional configuration (see cordis.patch.yml notes).
 * @returns disposer that unregisters the tools.
 */
export function apply(ctx, config = {}) {
  const cfg = settings(config)
  const disposers = [
    ctx.tools.register(dispatchTool(cfg)),
    ctx.tools.register(inboxTool(cfg)),
    ctx.tools.register(acceptTool(cfg)),
    ctx.tools.register(reworkTool(cfg)),
    ctx.tools.register(statusTool(cfg)),
  ]
  return () => {
    for (const dispose of disposers) dispose?.()
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
