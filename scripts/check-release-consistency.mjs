// 发布一致性检查：三处版本号必须相同，CHANGELOG 必须有这一段，tag（可选）必须对得上。
//
// 用法：
//   node scripts/check-release-consistency.mjs
//   node scripts/check-release-consistency.mjs --tag v0.7.4
//
// CI 每个 push 都跑一次，scripts/release.py 在打 tag 之前跑一次。任何一条不满足就 exit 1。
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const read = (rel) => readFileSync(join(root, rel), 'utf8')

const failures = []
const notes = []

function grab(label, text, re) {
  const hit = text.match(re)
  if (!hit) {
    failures.push(`${label}: 版本号没找到（正则 ${re}）`)
    return null
  }
  return hit[1]
}

const pyprojectVersion = grab('pyproject.toml', read('pyproject.toml'), /^version = "([^"]+)"/m)
const packageVersion = grab(
  'src/control_tower/__init__.py',
  read('src/control_tower/__init__.py'),
  /__version__ = "([^"]+)"/,
)
const bundleVersion = JSON.parse(read('packages/dsh-tower/package.json')).version

const versions = { pyproject: pyprojectVersion, package: packageVersion, bundle: bundleVersion }
const distinct = new Set(Object.values(versions).filter(Boolean))
if (distinct.size > 1) {
  failures.push(
    `三处版本不一致：${Object.entries(versions)
      .map(([k, v]) => `${k}=${v}`)
      .join(', ')}`,
  )
} else if (distinct.size === 1) {
  notes.push(`版本三处一致：${[...distinct][0]}`)
}

const changelog = read('CHANGELOG.md')
const currentVersion = [...distinct][0]

if (currentVersion) {
  const heading = new RegExp(`^## \\[${currentVersion.replace(/\./g, '\\.')}\\]`, 'm')
  if (heading.test(changelog)) {
    notes.push(`CHANGELOG 有 [${currentVersion}] 段落`)
  } else {
    failures.push(`CHANGELOG.md 缺 "## [${currentVersion}] - YYYY-MM-DD" 段落`)
  }
}

if (/^## \[Unreleased\]/m.test(changelog)) {
  notes.push('CHANGELOG 有 [Unreleased] 段落')
}

const tagIndex = process.argv.indexOf('--tag')
if (tagIndex !== -1) {
  const tag = (process.argv[tagIndex + 1] || '').trim()
  if (!/^v\d+\.\d+\.\d+$/.test(tag)) {
    failures.push(`tag 格式应为 vX.Y.Z，收到 "${tag}"`)
  } else if (currentVersion && tag.slice(1) !== currentVersion) {
    failures.push(`tag ${tag} 与版本号 ${currentVersion} 不一致`)
  } else {
    notes.push(`tag ${tag} 与版本号一致`)
  }
}

for (const line of notes) console.log(`OK   ${line}`)
for (const line of failures) console.error(`FAIL ${line}`)
if (failures.length > 0) {
  console.error(`\n${failures.length} 项不满足，发布流程停下。`)
  process.exit(1)
}
console.log('\n发布一致性检查通过。')
