# 部署守卫（deploy guard）

> 触发：2026-11-11。一个 bot **直接改了线上主题文件**（`functions.php`：Product schema
> 提前 return + Microsoft Clarity 统计块），本地工作副本没同步；随后 T-0102 走标准
> 「改本地 → w.py upload」把线上那两处**静默覆盖**，最后靠服务器上传前备份恢复。
> 塔台对此必须有一道闸，而不是靠人记得。

## 规则：上传前必须 `tower deploy check`

`tower deploy` 对每个「本地文件 ↔ 远程路径」组合维护一个 **baseline**（最近一次同步时
远端内容的摘要），比较三方：

| local vs remote vs baseline | 判定 | 动作 |
|---|---|---|
| remote == local | `in_sync` | 无需上传 |
| remote == baseline（只有本地变了） | `local_changed` | **可以上传** |
| remote != baseline（线上变了） | `remote_drift` | **拒绝上传**：先 `pull` 或人工合并 |

## 命令

```bash
# 首次纳管：以当前线上内容为基线（不改文件）
tower deploy init generatepress-child/functions.php \
  --remote wp-content/themes/generatepress-child/functions.php

# 上传前检查（exit 1 = 线上漂移，禁止上传）
tower deploy check generatepress-child/functions.php \
  --remote wp-content/themes/generatepress-child/functions.php

# 把线上内容拉回本地（本地先备份到 .tower/deploy/backup/，再更新基线）
tower deploy pull generatepress-child/functions.php \
  --remote wp-content/themes/generatepress-child/functions.php --role frontend

# 上传成功后验证并推进基线（远端必须已等于本地，否则报错）
tower deploy done generatepress-child/functions.php \
  --remote wp-content/themes/generatepress-child/functions.php --role frontend

# 查看所有纳管文件
tower deploy status
```

## 状态与回执

- 基线：`.tower/deploy/<key>.json`（`baseline_sha` / `baseline_at` / note）
- 本地备份：`.tower/deploy/backup/<时间戳>-<文件名>`
- 回执：`.tower/deploy/receipts/<时间戳>-<key>-<动作>.md`
- `--role <角色>` 时额外追加一行到 `roles/<role>/DEPLOY-SYNC.md`，让 bot 在角色目录里
  就能看到「这个文件什么时候和线上同步过」
- 所有动作进 `.tower/ledger.jsonl`（`deploy_check/init/pull/done`）

## 取远端内容：`deploy.fetch`

守卫需要一条「打印远端文件到 stdout」的命令，在 tower.toml 配置一次：

```yaml
deploy:
  fetch: ["D:\\Python314\\python.exe", "-m", "control_tower.deploy_fetch",
          "--cwd", "G:/新网站项目/需要的网站/evergocasa-new/generatepress-child",
          "--remote", "{remote}"]
```

占位符：`{remote}`、`{local}`、`{workspace}`、`{project_root}`、`{python}`。
`control_tower.deploy_fetch` 会 `import w` 并调用
`w.sync_request({"action": "read_file", "path": remote})`，因此对任何 w.py 项目通用。
也可以直接用 `--fetch-cmd "<任意命令>"` 覆盖。

## bot 纪律（会随每次派单注入）

1. **任何线上直改之后**：立刻 `tower deploy pull <本地文件> --remote <远端路径>`，
   把线上内容落回本地工作副本（本地旧版自动备份），再继续。
2. **任何 upload 之前**：`tower deploy check`，`remote_drift` 一律停下处理，不 upload。
3. **upload 之后**：`tower deploy done`，确认线上确实等于本地并推进基线。
4. 找不到该文件的基线时先 `init`（以当前线上为准）再走 1–3。

## 为什么不是「上传后自动同步本地」

上传这个动作由各项目自己的 `w.py` 执行，塔台不拦截外部工具。守卫把
「不可逆的覆盖」变成「上传前可判定的检查 + 拉回命令」，并把结果留痕到
roles/ 与 ledger——这正是 bot 真正需要的那一层。
