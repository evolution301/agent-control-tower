/**
 * Regression tests for the tool schema shape the harness accepts, and for the
 * JSON parsing the tools rely on.
 *
 * DSH validates every registered tool with `assertSupportedJsonSchema`. The
 * 0.2.x rule that bit us: `required` is only valid on an object node, as an
 * array of property names. A `required: true` inside a property node throws
 * `JsonSchemaError`, `ctx.tools.register()` throws with it, and the bundle then
 * fails to mount without any user-visible error (see CHANGELOG 0.2.1).
 */
import assert from 'node:assert/strict'
import { test } from 'node:test'

import {
  acceptTool,
  dispatchTool,
  inboxTool,
  parseJsonOutput,
  reworkTool,
  roleAddTool,
  roleRemoveTool,
  rolesTool,
  statusTool,
} from '../index.js'

const TOOLS = [
  dispatchTool({}),
  inboxTool({}),
  acceptTool({}),
  reworkTool({}),
  statusTool({}),
  rolesTool({}),
  roleAddTool({}),
  roleRemoveTool({}),
]

/** The subset of the harness rules these tools are allowed to touch. */
function assertSupportedSchema(node, path = 'schema') {
  if (node === null || typeof node !== 'object' || Array.isArray(node)) return
  if ('required' in node && !Array.isArray(node.required)) {
    throw new Error(`${path}.required must be an array of property names`)
  }
  for (const [name, property] of Object.entries(node.properties ?? {})) {
    if (property && typeof property === 'object' && !Array.isArray(property)) {
      if ('required' in property) {
        throw new Error(`${path}.properties.${name}.required is not supported on type "${property.type}"`)
      }
      assertSupportedSchema(property, `${path}.properties.${name}`)
    }
  }
}

test('every tool output schema is accepted by the harness validator', () => {
  for (const def of TOOLS) assertSupportedSchema(def.output.schema, `${def.name}.output.schema`)
})

test('required properties are declared on the object, not on the property', () => {
  for (const def of TOOLS) {
    const { schema } = def.output
    assert.ok(Array.isArray(schema.required), `${def.name}: output.schema.required must be an array`)
    assert.ok(schema.required.length > 0, `${def.name}: at least one property must be required`)
    for (const name of schema.required) {
      assert.ok(name in schema.properties, `${def.name}: required name ${name} must exist in properties`)
    }
  }
})

test('the validator still rejects the property-level spelling (guards the regression)', () => {
  assert.throws(
    () => assertSupportedSchema({ type: 'object', properties: { text: { type: 'string', required: true } } }),
    /required is not supported on type "string"/,
  )
})

test('parseJsonOutput reads a pure JSON payload', () => {
  const parsed = parseJsonOutput('{"id": "T-0001", "role": "backend"}')
  assert.equal(parsed.id, 'T-0001')
})

test('parseJsonOutput skips the human report preamble printed by CLI <= 0.2.0', () => {
  const newline = String.fromCharCode(10)
  const blob = [
    'task T-0001 role=backend status=reported',
    '',
    '--- report ---',
    'summary: [mock] handled it',
    'files: 1 | checks: 2',
    'verify: PASS ',
    '{"id": "T-0001", "role": "backend", "report": {"summary": "[mock] handled it"}}',
  ].join(newline)
  const parsed = parseJsonOutput(blob)
  assert.equal(parsed.id, 'T-0001')
  assert.equal(parsed.report.summary, '[mock] handled it')
})

test('parseJsonOutput returns undefined when the blob carries no JSON', () => {
  assert.equal(parseJsonOutput('usage: tower ...'), undefined)
})

test('every tool emits a standard JSON Schema for its parameters', () => {
  for (const def of TOOLS) {
    const p = def.parameters || {}
    assert.equal(p.type, 'object', def.name + ': parameters.type must be object')
    assert.ok(p.properties && typeof p.properties === 'object', def.name + ': parameters.properties missing')
    if (p.required !== undefined) {
      assert.ok(Array.isArray(p.required), def.name + ': parameters.required must be an array')
      for (const name of p.required) {
        assert.ok(name in p.properties, def.name + ': required name ' + name + ' must be declared')
      }
    }
    for (const [name, spec] of Object.entries(p.properties)) {
      assert.equal(typeof spec.type, 'string', def.name + '.' + name + ': missing type')
      assert.equal('required' in spec, false, def.name + '.' + name + ': required belongs on the object node')
    }
  }
})
