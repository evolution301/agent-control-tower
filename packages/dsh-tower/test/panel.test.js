/** Host-side panel supervision: detect an already-served port, never double-spawn. */
import assert from 'node:assert/strict'
import { test } from 'node:test'
import net from 'node:net'

import { ensurePanel, portOpen } from '../index.js'

function listen() {
  return new Promise((resolvePromise) => {
    const server = net.createServer((socket) => socket.end())
    server.listen(0, '127.0.0.1', () => resolvePromise(server))
  })
}

function close(server) {
  return new Promise((resolvePromise) => server.close(resolvePromise))
}

test('portOpen detects a listening socket and a closed port', async () => {
  const server = await listen()
  const port = server.address().port
  assert.equal(await portOpen(port, 1000), true)
  await close(server)
  assert.equal(await portOpen(port, 300), false)
})

test('ensurePanel does not spawn when the panel port is already served', async () => {
  const server = await listen()
  const port = server.address().port
  const cfg = {
    panel: true,
    panelPort: port,
    towerBin: 'definitely-not-a-binary-zzz',
    workspace: process.cwd(),
  }
  assert.equal(await ensurePanel(cfg), null)
  await close(server)
})

test('ensurePanel is disabled by panel: false', async () => {
  assert.equal(await ensurePanel({ panel: false, panelPort: 1 }), null)
})
