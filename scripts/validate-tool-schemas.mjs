// 校验已安装 bundle 的 8 个工具参数是否是标准 JSON Schema
// 用法: node validate-tool-schemas.mjs <path-to-index.js>
import { pathToFileURL } from "node:url";

const mod = await import(pathToFileURL(process.argv[2]).href);

const tools = [];
const fakeCtx = { tools: { register: (def) => { tools.push(def); return () => {}; } } };
mod.apply(fakeCtx, { workspace: process.cwd() });

const problems = [];
const isObj = (v) => v !== null && typeof v === "object" && !Array.isArray(v);

for (const def of tools) {
  const where = `tool ${def.name}`;
  if (!def.name || typeof def.name !== "string") problems.push(`${where}: missing name`);
  if (!isObj(def.parameters)) {
    problems.push(`${where}: parameters is not an object`);
    continue;
  }
  // 标准 JSON Schema 顶层
  if (def.parameters.type !== "object") problems.push(`${where}: parameters.type must be "object" (got ${JSON.stringify(def.parameters.type)})`);
  if (!isObj(def.parameters.properties)) problems.push(`${where}: parameters.properties must be an object`);
  if (def.parameters.required !== undefined && !Array.isArray(def.parameters.required)) {
    problems.push(`${where}: parameters.required must be an array (got ${typeof def.parameters.required})`);
  }
  // 每个属性必须是合法 schema 对象：不能把 required 塞在属性里（v0.6.1 的 bug）
  for (const [key, schema] of Object.entries(def.parameters.properties || {})) {
    if (!isObj(schema)) {
      problems.push(`${where}.${key}: property schema is not an object`);
      continue;
    }
    if (typeof schema.type !== "string") {
      problems.push(`${where}.${key}: property schema needs a string "type" (got ${JSON.stringify(schema.type)})`);
    }
    if ("required" in schema) {
      problems.push(`${where}.${key}: "required" must not live inside a property schema (v0.6.1 bug)`);
    }
  }
  // properties 里没有的 required 项
  for (const key of def.parameters.required || []) {
    if (!(key in (def.parameters.properties || {}))) problems.push(`${where}: required "${key}" has no matching property`);
  }
  // output / execute 契约
  if (!isObj(def.output) || typeof def.output.render !== "function") problems.push(`${where}: output.render missing`);
  if (typeof def.execute !== "function") problems.push(`${where}: execute missing`);
}

console.log(`tools: ${tools.length}`);
for (const t of tools) {
  const req = (t.parameters && t.parameters.required) || [];
  console.log(`  ${t.name.padEnd(18)} type=${t.parameters?.type} props=${Object.keys(t.parameters?.properties || {}).length} required=[${req.join(",")}]`);
}
if (problems.length) {
  console.log("\nPROBLEMS:");
  for (const p of problems) console.log("  - " + p);
  process.exit(1);
}
console.log("\nOK: every tool's parameters is standard JSON Schema {type:'object', properties, required}");
