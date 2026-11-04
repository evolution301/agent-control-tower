/**
 * The hand-written client module must satisfy the harness' client contract:
 * load through `window.__ModuleLoader__.load`, export `apply` + `inject`, and
 * register exactly one `settings.section` entry.
 */
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const clientPath = join(here, '..', 'lib', 'client.js')

async function loadClient() {
  let loaded = null
  globalThis.window = {
    __ModuleLoader__: {
      load: (definition) => {
        loaded = definition
      },
    },
    open: () => {},
  }
  await import(pathToFileURL(clientPath).href + '?v=' + Date.now())
  return loaded
}

function reactStub() {
  return {
    createElement: (type, props, ...children) => ({ type, props, children }),
    useState: (initial) => [initial, () => {}],
  }
}

test('the client bundle registers a settings section named Tower', async () => {
  const loaded = await loadClient()
  assert.ok(loaded, 'window.__ModuleLoader__.load must be called')
  assert.equal(loaded.id, 'dsh-tower')
  assert.equal(typeof loaded.factory, 'function')

  const exports = loaded.factory((name) => {
    assert.equal(name, 'react')
    return reactStub()
  })
  assert.deepEqual(exports.inject, ['@deepseek-ai/dsh-client-ui-slots'])
  assert.equal(typeof exports.apply, 'function')

  const calls = []
  const ctx = {
    slots: {
      inject: (name, factory) => {
        calls.push(['inject', name])
        return factory()
      },
      register: (row, component) => {
        calls.push(['register', row, component])
        return () => calls.push(['dispose', row.id])
      },
    },
  }
  const dispose = exports.apply(ctx)
  assert.deepEqual(calls[0], ['inject', 'settings.section'])
  const registered = calls.find((call) => call[0] === 'register')
  assert.equal(registered[1].name, 'settings.section')
  assert.equal(registered[1].id, 'tower')
  assert.equal(typeof registered[1].label(), 'string')
  assert.equal(registered[1].label(), 'Tower')
  assert.equal(typeof registered[2], 'function')

  dispose()
  assert.ok(calls.some((call) => call[0] === 'dispose'))

  // the component renders without React being anything more than the stub
  const element = registered[2]({})
  assert.equal(element.type, 'div')
})

test('a broken slot service does not take the client plugin down', async () => {
  const loaded = await loadClient()
  const exports = loaded.factory(() => reactStub())
  const ctx = {
    slots: {
      inject: () => {
        throw new Error('slots unavailable')
      },
    },
  }
  const dispose = exports.apply(ctx)
  assert.equal(typeof dispose, 'function')
  dispose()
})

test('the client file is valid JavaScript on its own', () => {
  const source = readFileSync(clientPath, 'utf8')
  assert.ok(source.includes('window.__ModuleLoader__.load'))
  assert.ok(source.includes("name: 'settings.section'"))
  assert.ok(source.includes("id: 'tower'"))
})
