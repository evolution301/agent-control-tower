// 从 CHANGELOG.md 抽出某一段作为 GitHub Release 正文。
//
// 用法：
//   node scripts/release-notes.mjs 0.7.3          # 打到标准输出
//   node scripts/release-notes.mjs --tag v0.7.3
//   node scripts/release-notes.mjs 0.7.3 --out notes.md
//
// 只取 "## [0.7.3] - 日期" 到下一个 "## [" 之前的内容；找不到就 exit 1。
import { readFileSync, writeFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const args = process.argv.slice(2)

const outIndex = args.indexOf('--out')
const outPath = outIndex === -1 ? null : args[outIndex + 1]
if (outPath) args.splice(outIndex, 2)

const tagIndex = args.indexOf('--tag')
if (tagIndex !== -1) {
  const tag = args[tagIndex + 1] || ''
  args.splice(tagIndex, 2)
  args.unshift(tag.replace(/^v/, ''))
}

const version = (args[0] || '').trim()
if (!/^\d+\.\d+\.\d+$/.test(version)) {
  console.error('用法: node scripts/release-notes.mjs <X.Y.Z> [--tag vX.Y.Z] [--out file]')
  process.exit(2)
}

const changelog = readFileSync(join(root, 'CHANGELOG.md'), 'utf8')
const lines = changelog.split(/\r?\n/)
const escape = (v) => v.replace(/\./g, '\\.')
const start = lines.findIndex((line) => new RegExp(`^## \\[${escape(version)}\\]`).test(line))

if (start === -1) {
  console.error(`CHANGELOG.md 里没有 "## [${version}]" 段落。`)
  process.exit(1)
}

let end = lines.length
for (let i = start + 1; i < lines.length; i += 1) {
  if (/^## \[/.test(lines[i])) {
    end = i
    break
  }
}

const body = lines
  .slice(start, end)
  .join('\n')
  .replace(/\s+$/, '')

if (body.length === 0) {
  console.error(`"## [${version}]" 段落是空的。`)
  process.exit(1)
}

if (outPath) {
  writeFileSync(join(root, outPath), `${body}\n`, 'utf8')
  console.log(`已写入 ${outPath}（${body.split('\n').length} 行）`)
} else {
  console.log(body)
}
