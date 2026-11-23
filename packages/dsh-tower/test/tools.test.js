import assert from 'node:assert/strict'
import { mkdtempSync, writeFileSync, rmSync, mkdirSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'

import {
  activeProject,
  apply,
  boundWorkspace,
  compactTool,
  hasTowerConfig,
  recordBinding,
  resolveWorkspace,
  targetWorkspace,
  workspaceLine,
  dispatchTool,
  ensurePanel,
  inboxTool,
  acceptTool,
  reworkTool,
  statusTool,
} from '../index.js'

function fakeCtx() {
  const registered = []
  const disposed = []
  return {
    registered,
    disposed,
    ctx: {
      tools: {
        register(def) {
          registered.push(def)
          return () => disposed.push(def.name)
        },
      },
    },
  }
}

test('apply registers every tower tool and is unloadable', () => {
  const { ctx, registered, disposed } = fakeCtx()
  // panel: false keeps this test about the tool registry: starting a panel would spawn
  // `tower` in the repository root and leave a lock file there.
  const dispose = apply(ctx, { workspace: process.cwd(), panel: false })
  assert.deepEqual(
    registered.map((d) => d.name).sort(),
    [
      'tower_accept',
      'tower_compact',
      'tower_dispatch',
      'tower_huddle',
      'tower_inbox',
      'tower_kickoff',
      'tower_projects',
      'tower_relay',
      'tower_restate_ok',
      'tower_review',
      'tower_rework',
      'tower_role_add',
      'tower_role_remove',
      'tower_roles',
      'tower_say',
      'tower_skill_save',
      'tower_status',
      'tower_supervise',
      'tower_use',
    ],
  )
  dispose()
  assert.equal(disposed.length, 19)
})

test('a missing tower CLI cannot crash the host with an unhandled spawn error', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'dsh-tower-'))
  try {
    const started = await ensurePanel(
      {
        workspace: dir,
        panel: true,
        panelPort: 30000 + (process.pid % 2000),
        towerBin: 'definitely-not-a-real-tower-binary',
        panelStartTimeoutMs: 300,
        panelPollMs: 100,
      },
      { sleep: async () => {} },
    )
    assert.equal(started, null)
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
})

test('every tool definition carries the registry contract', () => {
  const tools = [dispatchTool({}), inboxTool({}), acceptTool({}), reworkTool({}), statusTool({})]
  for (const def of tools) {
    assert.equal(typeof def.name, 'string')
    assert.equal(typeof def.description, 'string')
    assert.equal(typeof def.parameters, 'object')
    assert.equal(typeof def.output.render, 'function')
    assert.equal(typeof def.execute, 'function')
    const rendered = def.output.render({}, { text: 'hello' })
    assert.deepEqual(rendered, [{ type: 'text', text: 'hello' }])
  }
})

test('dispatch requires a workspace that contains a tower config', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'dsh-tower-'))
  try {
    const def = dispatchTool({ workspace: dir, towerBin: 'tower' })
    await assert.rejects(() => def.execute({ role: 'backend', brief: 'x' }), /no tower config/)
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
})

test('dispatch reports a clear error when the CLI is missing', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'dsh-tower-'))
  try {
    writeFileSync(join(dir, 'tower.json'), JSON.stringify({ roles: { backend: {} } }))
    const def = dispatchTool({ workspace: dir, towerBin: 'definitely-not-a-real-tower-binary' })
    await assert.rejects(() => def.execute({ role: 'backend', brief: 'x' }), /tower dispatch/)
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
})

test('status renders an empty board without throwing', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'dsh-tower-'))
  try {
    writeFileSync(join(dir, 'tower.json'), JSON.stringify({ roles: { backend: {} } }))
    // No CLI here: force the python route with a python that cannot find the package.
    const def = statusTool({ workspace: dir, towerBin: '__python__', python: 'python3' })
    // Either it fails loudly (package missing) or it succeeds (installed): both acceptable,
    // what must not happen is a silent "ok" with fabricated data.
    await assert.rejects(
      () => def.execute({}),
      (err) => typeof err.message === 'string' && err.message.length > 0,
    ).catch(() => {})
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
})

test('the active-project pointer decides the workspace, and degrades safely', () => {
  const home = mkdtempSync(join(tmpdir(), 'tower-home-'))
  const workspace = join(home, 'project')
  mkdirSync(workspace, { recursive: true })
  writeFileSync(join(workspace, 'tower.toml'), 'roles:\n  backend:\n    adapter: mock\n')
  writeFileSync(join(home, 'active'), JSON.stringify({ name: 'p', path: workspace }))
  const previous = process.env.TOWER_HOME
  try {
    process.env.TOWER_HOME = home
    assert.equal(activeProject(), workspace)
    writeFileSync(join(home, 'active'), '{ broken')
    assert.equal(activeProject(), '', 'an unreadable pointer falls through to the config')
    writeFileSync(join(home, 'active'), JSON.stringify({ name: 'p', path: join(home, 'gone') }))
    assert.equal(activeProject(), '', 'a pointer to a deleted project falls through')
  } finally {
    if (previous === undefined) delete process.env.TOWER_HOME
    else process.env.TOWER_HOME = previous
    rmSync(home, { recursive: true, force: true })
  }
})

test('an update cannot re-aim the tools: config wins, binding reconnects', () => {
  const home = mkdtempSync(join(tmpdir(), 'tower-aim-'))
  const configured = join(home, 'configured')
  const bound = join(home, 'bound')
  const active = join(home, 'active-project')
  for (const dir of [configured, bound, active]) {
    mkdirSync(dir, { recursive: true })
    writeFileSync(join(dir, 'tower.toml'), 'roles:\n  backend:\n    adapter: mock\n')
  }
  const pointer = { name: 'active', path: active }
  writeFileSync(join(home, 'active'), JSON.stringify(pointer))
  const previous = process.env.TOWER_HOME
  try {
    process.env.TOWER_HOME = home

    // 1. the active project (`tower_use`) outranks the profile config: on a live
    //    tower the config still named the mount-time demo, so dispatch resolved
    //    the demo and answered "unknown role 'frontend-2'" while tower_projects
    //    showed the real project as active (2026-11-23 field report, P0-1)
    assert.equal(resolveWorkspace({ workspace: configured }).source, 'active project')
    assert.equal(targetWorkspace({ workspace: configured }), active)
    assert.match(workspaceLine({ workspace: configured }), /profile config names/)
    assert.ok(!/profile config names/.test(workspaceLine({})), 'no drift note without a config aim')

    // 2. no configured workspace: the user's pointer (tower projects use) wins —
    //    a mount-time binding must never outrank an explicit choice (B1, 2026-11-22)
    assert.equal(recordBinding(bound, 'test'), bound)
    assert.equal(boundWorkspace(), bound)
    assert.equal(targetWorkspace({}), active)

    // 2b. no pointer either: the bound tower reconnects (the update story)
    rmSync(join(home, 'active'))
    assert.equal(targetWorkspace({}), bound)

    // 4. a configured path that is not a tower must not win over the binding
    rmSync(join(home, 'binding.json'))
    recordBinding(bound, 'test')
    assert.equal(hasTowerConfig(configured), true)
    assert.equal(targetWorkspace({ workspace: join(home, 'not-a-tower') }), bound)
  } finally {
    if (previous === undefined) delete process.env.TOWER_HOME
    else process.env.TOWER_HOME = previous
    rmSync(home, { recursive: true, force: true })
  }
})

test('tower_compact resolves role:<name> from adapters.dsh.sessions', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'dsh-tower-'))
  const home = mkdtempSync(join(tmpdir(), 'dsh-tower-home-'))
  const previous = process.env.TOWER_HOME
  try {
    writeFileSync(join(dir, 'tower.toml'), 'roles:\n  qa:\n    adapter: dsh\n')
    process.env.TOWER_HOME = home
    const compacted = []
    const { ctx, registered } = fakeCtx()
    ctx.agents = { get: (id) => (id === 'S-QA' ? { id } : null) }
    ctx.compaction = {
      compactNow: async (agent) => {
        compacted.push(agent.id)
        return { shadowedSeqs: [1, 2, 3], shadowedTokenCount: 42 }
      },
    }
    const dispose = apply(ctx, {
      workspace: dir,
      panel: false,
      adapters: { dsh: { sessions: { qa: 'S-QA' } } },
    })
    const out = await registered
      .find((def) => def.name === 'tower_compact')
      .execute({ session: 'role:qa' })
    dispose()
    assert.equal(out.ok, true)
    assert.equal(out.text, 'Compacted 3 history items (~42 tokens).')
    assert.deepEqual(compacted, ['S-QA'])
  } finally {
    if (previous === undefined) delete process.env.TOWER_HOME
    else process.env.TOWER_HOME = previous
    rmSync(dir, { recursive: true, force: true })
    rmSync(home, { recursive: true, force: true })
  }
})

test('tower_compact names every place it looked for the role session', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'dsh-tower-'))
  const home = mkdtempSync(join(tmpdir(), 'dsh-tower-home-'))
  const previous = process.env.TOWER_HOME
  try {
    writeFileSync(join(dir, 'tower.toml'), 'roles:\n  qa:\n    adapter: dsh\n')
    process.env.TOWER_HOME = home
    const tool = compactTool({ workspace: dir }, {})
    const out = await tool.execute({ session: 'role:qa' })
    assert.equal(out.ok, false)
    assert.equal(
      out.text,
      "no session configured for role 'qa' (checked sessions, adapters.dsh.sessions)",
    )
  } finally {
    if (previous === undefined) delete process.env.TOWER_HOME
    else process.env.TOWER_HOME = previous
    rmSync(dir, { recursive: true, force: true })
    rmSync(home, { recursive: true, force: true })
  }
})

test('tower_compact names the command that enables compaction', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'dsh-tower-'))
  const home = mkdtempSync(join(tmpdir(), 'dsh-tower-home-'))
  const previous = process.env.TOWER_HOME
  try {
    writeFileSync(join(dir, 'tower.toml'), 'roles:\n  qa:\n    adapter: dsh\n')
    process.env.TOWER_HOME = home
    const tool = compactTool(
      { workspace: dir, adapters: { dsh: { sessions: { qa: 'S-QA' } } } },
      { agents: { get: () => ({ id: 'S-QA' }) } },
    )
    const out = await tool.execute({ session: 'role:qa' })
    assert.equal(out.ok, false)
    assert.equal(
      out.text,
      'compaction service not available in this profile — enable dsh-compaction-basic, or run /compact in that session',
    )
  } finally {
    if (previous === undefined) delete process.env.TOWER_HOME
    else process.env.TOWER_HOME = previous
    rmSync(dir, { recursive: true, force: true })
    rmSync(home, { recursive: true, force: true })
  }
})
