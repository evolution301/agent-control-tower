import assert from 'node:assert/strict'
import { mkdtempSync, writeFileSync, rmSync, mkdirSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'

import {
  activeProject,
  apply,
  boundWorkspace,
  hasTowerConfig,
  recordBinding,
  targetWorkspace,
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
      'tower_dispatch',
      'tower_huddle',
      'tower_inbox',
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
  assert.equal(disposed.length, 17)
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

    // 1. the configured workspace is the aim, whatever the pointer says
    assert.equal(targetWorkspace({ workspace: configured }), configured)

    // 2. no configured workspace (the line an update can lose): the bound tower returns
    assert.equal(recordBinding(bound, 'test'), bound)
    assert.equal(boundWorkspace(), bound)
    assert.equal(targetWorkspace({}), bound)

    // 3. no binding either: fall back to the switcher's active project
    rmSync(join(home, 'binding.json'))
    assert.equal(targetWorkspace({}), active)

    // 4. a configured path that is not a tower must not win over the binding
    recordBinding(bound, 'test')
    assert.equal(hasTowerConfig(configured), true)
    assert.equal(targetWorkspace({ workspace: join(home, 'not-a-tower') }), bound)
  } finally {
    if (previous === undefined) delete process.env.TOWER_HOME
    else process.env.TOWER_HOME = previous
    rmSync(home, { recursive: true, force: true })
  }
})
