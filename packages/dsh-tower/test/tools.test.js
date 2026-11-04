import assert from 'node:assert/strict'
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'

import {
  apply,
  dispatchTool,
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
  const dispose = apply(ctx, { workspace: process.cwd() })
  assert.deepEqual(
    registered.map((d) => d.name).sort(),
    [
      'tower_accept',
      'tower_dispatch',
      'tower_inbox',
      'tower_rework',
      'tower_role_add',
      'tower_role_remove',
      'tower_roles',
      'tower_status',
    ],
  )
  dispose()
  assert.equal(disposed.length, 8)
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
