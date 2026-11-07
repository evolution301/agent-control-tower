/**
 * Panel lifecycle: one panel per machine, and nothing left behind.
 *
 * Before v0.7.3 every mount of the bundle raced the port check, and the
 * disposer only killed the launcher process, so a machine ended up with two
 * `tower panel` processes: one serving 127.0.0.1:8787 and one orphan that never
 * exits. These tests pin both halves of the fix.
 */
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { mkdirSync, mkdtempSync, readFileSync, writeFileSync, existsSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { createRequire } from 'node:module'

import { VERSION, ensurePanel, panelLockPath } from '../index.js'

const require = createRequire(import.meta.url)
const pkg = require('../package.json')

function cfg(overrides = {}) {
  return {
    panel: true,
    panelPort: 18787,
    towerBin: 'tower',
    workspace: mkdtempSync(join(tmpdir(), 'tower-panel-')),
    panelStartTimeoutMs: 200,
    panelPollMs: 50,
    ...overrides,
  }
}

function fakeChild(pid = 4242) {
  return { pid, exitCode: null, kill() {} }
}

test('a panel that answers the port is kept and writes the lock', async () => {
  const c = cfg()
  const child = fakeChild()
  let checks = 0
  const started = await ensurePanel(c, {
    // first look: nothing listens; after the spawn: the panel is up
    portOpen: async () => {
      checks += 1
      return checks > 1
    },
    spawnPanel: () => child,
    killPanel: () => {
      throw new Error('a live panel must not be killed')
    },
    sleep: async () => {},
  })
  assert.equal(started.child, child)
  assert.ok(existsSync(panelLockPath(c)), 'lock file is written for the owner')
  assert.equal(readFileSync(panelLockPath(c), 'utf8'), String(process.pid))
})

test('a panel that never answers is killed and its lock dropped', async () => {
  const c = cfg()
  const child = fakeChild()
  const killed = []
  const started = await ensurePanel(c, {
    portOpen: async () => false,
    spawnPanel: () => child,
    killPanel: (target) => killed.push(target),
    sleep: async () => {},
  })
  assert.equal(started, null)
  assert.deepEqual(killed, [child], 'the loser is killed, not left as an orphan')
  assert.equal(existsSync(panelLockPath(c)), false, 'the lock does not outlive the attempt')
})

test('a child that exits during startup is not waited on', async () => {
  const c = cfg({ panelStartTimeoutMs: 5000 })
  const child = fakeChild()
  child.exitCode = 1
  const killed = []
  const started = await ensurePanel(c, {
    portOpen: async () => false,
    spawnPanel: () => child,
    killPanel: (target) => killed.push(target),
    sleep: async () => {},
  })
  assert.equal(started, null)
  assert.deepEqual(killed, [child])
})

test('a live lock stops a second panel from being spawned', async () => {
  const c = cfg()
  const lockPath = panelLockPath(c)
  mkdirSync(dirname(lockPath), { recursive: true })
  writeFileSync(lockPath, String(process.pid))
  let spawned = false
  const started = await ensurePanel(c, {
    portOpen: async () => false,
    spawnPanel: () => {
      spawned = true
      return fakeChild()
    },
    killPanel: () => {},
    sleep: async () => {},
  })
  assert.equal(started, null)
  assert.equal(spawned, false, 'the second window reuses the running panel')
})

test('a stale lock is replaced instead of blocking the panel forever', async () => {
  const c = cfg()
  const lockPath = panelLockPath(c)
  mkdirSync(dirname(lockPath), { recursive: true })
  writeFileSync(lockPath, '999999') // no such process
  const child = fakeChild()
  let checks = 0
  const started = await ensurePanel(c, {
    portOpen: async () => {
      checks += 1
      return checks > 1
    },
    spawnPanel: () => child,
    killPanel: () => {},
    sleep: async () => {},
  })
  assert.equal(started.child, child)
})

test('VERSION mirrors package.json', () => {
  assert.equal(VERSION, pkg.version)
})
