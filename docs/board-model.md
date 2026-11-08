# 看板模型（唯一规范）

> 这个文件是看板、CLI、面板三处共用的一份定义。页面上的每个数字都能在这里找到公式；
> 代码里算不出这个公式的数字，就不允许出现在页面上。

## 1. 三个实体，没有第四个

| 实体 | 是什么 | 存在哪里 |
|---|---|---|
| **项目** | 一个工作区，里面有一个塔 | `<工作区>/tower.toml` + `<工作区>/.tower/`；登记在 `~/.tower/projects.json` |
| **bot** | `tower.toml` 里的一个角色（role），对应一个长期会话 | `tower.toml` 的 `roles:` |
| **任务** | 一次派活，一个任务号 `T-0001` | `.tower/tasks/T-0001.json` + `.tower/ledger.jsonl` |

「团队 / 队 / agent / 员工」都是同义词，看板上统一叫 **项目 / bot / 任务**。

## 2. 任务只可能落在四个队列里（互斥、有优先顺序）

| 队列 | 判定（按顺序第一个命中） | 谁处理 |
|---|---|---|
| **需要你** | `state ∈ {blocked, failed}` 且未验收 | **人**：一个具体决定（看 `## Attention`） |
| **待总控** | `status ∈ {reported, rework, restating}`，或 `state = ready`，或已逾期 | **塔自己**：验收 / 打回重试 / 催办，不用人 |
| **进行中** | `status ∈ {claimed, in_progress}` | bot |
| **已完成** | `status = accepted` | 谁也不占：只计数，不进任何队列 |

硬规则：

1. `accepted` 的任务**永远**不进「需要你」和「待总控」。之前"需要你 32 个"就是这条被违反造成的。
2. 只有 `blocked` / `failed` 才计入「需要你」，而且必须有 `## Attention` 里的一个具体问题。
3. 塔的重试次数上限是角色的 `max_rounds`；用尽后任务从「待总控」升级到「需要你」。

## 3. 页面数字的公式（一处实现，三处显示）

| 页面上的数字 | 公式 |
|---|---|
| 项目 | `~/.tower/projects.json` 里登记的项目数 |
| bot | 当前项目的角色数；「全部项目」视图为各项目之和 |
| 进行中 | 当前项目 `status ∈ {claimed, in_progress}` 的任务数 |
| 待总控 | 当前项目 `Board.needs_lead()` 的长度 |
| 需要你 | 当前项目 `Board.needs_person()` 的长度 |
| 逾期 | `due_at` 已过且未验收；**为 0 时不显示** |
| 已完成 | `status = accepted` 的任务数（只出现在「统计」里） |
| 验收率 | `accepted / (accepted + rework)`；没有裁决时显示 `—`，不显示 0% |
| 周期 | 创建 → 首次 accepted 的秒数，取 p50 / p90 |

## 4. 接入逻辑：任务只能从这三条路进塔

```
① CLI            tower dispatch <bot> "<brief>"   /  tower batch ...  /  tower restate ok
② DSH 插件工具    tower_dispatch（内部就是调 ①，同一份 CLI）
③ 脚本 / 定时     直接调 ①
```

- 进来即落盘：`.tower/tasks/T-xxxx.json`；看板**只读**，页面上没有任何写操作。
- 所以「为什么看板上没有这条任务」的答案永远是：**没人用上面三条路把它派进来**（或它已 accepted）。

## 5. 瞄准（一个 profile 对应哪一个塔）

优先级，前一个命中就停：

```
1. profile 配置      cordis.patch.yml → tower.workspace   （显式瞄准，更新也不会改）
2. 绑定记录          ~/.tower/binding.json                  （上次真正干活的塔；配置丢了就接回它）
3. 当前项目          ~/.tower/active                        （tower projects use 写的）
```

- `tower doctor` 会打印 `aim:`，配置与绑定不一致时在派活前给出警告。
- 未登记的项目**不会**出现在看板上（演示塔不会混进来）；登记用 `tower projects add|scan`。
- 插件更新只换代码，不碰塔数据；丢了瞄准会按上面顺序自动接回。

## 6. 闭环（从派活到沉淀）

```
dispatch → bot 干活 → 写回执 → triage（自动）
                                   ├─ 验收通过 → accepted
                                   ├─ 没过且有重试额度 → 打回，附上失败原因，重派
                                   └─ 重试用尽 / blocked → 升级到「需要你」
accepted → tower skill save → 下次派活自动注入该 bot 的技能索引
```

`tower triage` 跑整条队列；`tower dispatch` 默认在交回执后**自动**跑同一套（`--no-auto` 可关）。

## 7. 页面上不允许出现的东西

- 没有公式的数字（例如把「已验收」算进「需要你」）。
- 无意义的分类（「未完成」这种既是状态又不是状态的说法）。
- 演示数据冒充真实项目：演示塔可以存在，但必须未登记或显式命名为 `演示-*`。
