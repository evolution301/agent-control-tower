import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, writeFileSync, mkdirSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { buildTowerGuide } from '../index.js'

function workspace() {
  const dir = mkdtempSync(join(tmpdir(), 'tower-guide-'))
  mkdirSync(dir, { recursive: true })
  return dir
}

test('guide renders the four bindings and the two verbatim closing sentences', () => {
  const ws = workspace()
  const text = buildTowerGuide({ workspace: ws }, {
    code: 0,
    stdout: 'workspace: ' + ws + '\nroles: seo, frontend\nproblems: none',
    stderr: '',
  })
  assert.match(text, /✅ 已连上 DSH/)
  assert.match(text, /✅ 已有 2 个角色/)
  assert.match(text, /① DSH 会话 ② roles\.<name> ③ sessions\.<name>/)
  assert.match(text, /1\. 侧栏可见需要重启 DSH/)
  assert.match(text, /2\. 毕业考试：自动派一条「只回复 ready」的任务/)
  assert.match(text, /dispatch → report → verify → accept/)
})

test('guide turns a 401 doctor run into the secret location hint', () => {
  const ws = workspace()
  const text = buildTowerGuide({ workspace: ws }, {
    code: 1,
    stdout: [
      'workspace: ' + ws,
      'roles: seo',
      "problems: ['dsh adapter http://127.0.0.1:60388 unreachable or unauthorized: http 401 — check base_url (the desktop port changes on every restart) and secret']",
    ].join('\n'),
    stderr: '',
  })
  assert.match(text, /❌ DSH 连接/)
  assert.match(text, /records\.\*\.payload\.secret/)
  assert.match(text, /第 2 步/)
})

test('guide points an empty tower at step 1 (roles)', () => {
  const ws = workspace()
  const text = buildTowerGuide({ workspace: ws }, {
    code: 0,
    stdout: 'workspace: ' + ws + '\nroles: \nproblems: none',
    stderr: '',
  })
  assert.match(text, /❌ 还没有任何角色/)
  assert.match(text, /第 1 步/)
})

test('guide flags unbound sessions as the forever-running failure', () => {
  const ws = workspace()
  const text = buildTowerGuide({ workspace: ws }, {
    code: 1,
    stdout: [
      'workspace: ' + ws,
      'roles: seo',
      "problems: [\"role seo: pinned session session-x is not on http://127.0.0.1:1 — dispatch would create a duplicate session\"]",
    ].join('\n'),
    stderr: '',
  })
  assert.match(text, /⚠️ 有角色但 sessions 未绑定/)
  assert.match(text, /永远 running/)
})
