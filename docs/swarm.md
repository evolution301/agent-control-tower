# 蜂群协作模式（Swarm + Control Tower 混合增强架构）

> **一句话看懂**：把「公开微信大群 + `@唤醒` + 随时招人分片 + 独立书桌工位 + 原地换大脑」的蜂群体验，和控制塔最硬核的「文件实查 + 测试重跑 + QA 质检门」合二为一。

---

## 1. 为什么这样设计？（用生活例子理解）

- **共享消息板（`.tower/swarm/board.jsonl`）= 公开工作大群**  
  所有人（总调度、各路工蜂、质检员）都在同一个大群里说话。派活说明（brief）、回复、换模型原因、验收结果全部公开留档，不会出现“私聊失忆”。
- **`@名字` 唤醒 + `waiting` 休眠 = 按需叫醒、平时省钱**  
  工蜂干完活就自动进入 `waiting`（休眠待命，不耗算力）；只有群里有人 `@它` 或 `@all` 时，它才立刻醒来变成 `working`，干完把结果发回群里再继续睡。
- **动态招人与分片（`add` / `n` / `tasks` / `fork`）= 随时扩编的弹性施工队**  
  不用再把每个角色死死写进 `tower.toml`。临时缺前端就现场 `add frontend`；大活可以一键分身 `--n 3` 或按清单分片 `--tasks ...` 并行跑；还能 `--fork <老员工>` 把老员工的记忆复制给新员工接着干；工蜂忙不过来还能自己招帮手。
- **独立工位隔离（`inherit` / `share` / `branch`）= 每人一张独立书桌**  
  - `inherit`：共享主文件夹，适合**只读调研、代码审查、QA**；
  - `share`：通过 `git worktree` 给工蜂分一间共享底层仓库的独立工作副本（非 git 目录自动降级为安全拷贝），适合**同时改代码的开发工蜂**，互不踩文件；
  - `branch`：完全独立的本地克隆分支，适合高风险大改造。  
  干完后经过控制塔验证，一键 `merge` 合并回主工作区；`kill` 解散时自动清理临时工位。
- **无损换大脑（`model`）= 换引擎不丢记忆**  
  某只工蜂的模型额度耗尽或想换更强的模型时，执行 `tower swarm model <name> <new_model>`：完整保留这只工蜂的历史对话记忆与工位，换完新模型自动续跑。
- **控制塔验证门（`verify`）= 蜂群的铁面质检员**  
  工蜂在群里说“干完了”不算数，执行 `tower swarm verify <name> --land` 会检查交付文件是否真实存在、在工蜂工位里真跑一遍 `verify_commands`，全部通过才会把代码合并回主分支；不通过则把真实报错直接 `@回` 群里让工蜂返工。

---

## 2. 磁盘落盘结构

所有蜂群状态都落在 `.tower/swarm/` 下，纯文本 + Git 友好，不依赖任何数据库或云服务：

```text
.tower/swarm/
├── state.json              # 蜂群标题、默认后端/模型、工蜂花名册、历史记忆与状态
├── board.jsonl             # 追加式公开共享消息板（每行一条 JSON 消息）
└── workspaces/             # share / branch 模式下各工蜂的独立工位目录
    ├── coder-1/
    └── coder-2/
```

同时，蜂群的关键动作（招人、换模型、验收、合并、解散）也会同步写入 `.tower/ledger.jsonl`，与控制塔原有审计账本完全打通。

---

## 3. 工蜂生命周期（4 种状态）

| 状态 | 含义 | 触发方式 |
|---|---|---|
| `waiting` | 在大群里休眠待命，零消耗 | 干完一轮自动进入；或 `--no-wake` 创建后进入 |
| `working` | 正在执行任务 | 被 `add` 或群内 `@名字` 唤醒 |
| `stopped` | 暂停/离线（如重启后） | 被群里 `@名字` 提及时自动复活 |
| `killed` | 永久解散，独立工位已清理 | `tower swarm kill <name>`（不可复活，如需重启请重新 `add`） |

---

## 4. 可选配置（`tower.toml`）

现有 `tower.toml` **完全无需修改**即可使用蜂群模式。如果想自定义默认后端、默认模型或可选模型清单，可按需添加 `swarm:` 段：

```yaml
swarm:
  default_adapter: cli        # mock | cli | openai | dsh
  default_model: claude-sonnet-4
  workspace_mode: share       # inherit | share | branch
  models:
    - id: claude-sonnet-4
      label: Claude Sonnet 4
      adapter: cli
      suggested: true
    - id: gpt-4.1-mini
      label: GPT-4.1 Mini
      adapter: openai
      suggested: true
    - id: deepseek-chat
      label: DeepSeek Chat
      adapter: openai
      suggested: true
    - id: mock-fast
      label: Mock (Offline)
      adapter: mock
      suggested: true
```

---

## 5. 常用命令速查（复制粘贴即可跑）

```bash
# 1. 离线一键体验完整蜂群闭环（无需模型、无需网络）
tower swarm demo

# 2. 查看蜂群状态与花名册
tower swarm status

# 3. 召唤单只工蜂（独立 git worktree 工位 + 指定模型）
tower swarm add frontend --brief "重做首页卡片布局并跑通测试" --workspace share --model gpt-4.1-mini

# 4. 一次召唤 3 只分片工蜂并行干活（自动命名 coder-1 / coder-2 / coder-3）
tower swarm add coder --brief "按模块补齐单元测试" --tasks "auth模块" "pay模块" "order模块" --workspace share

# 5. 从已有工蜂克隆记忆继续干活
tower swarm add reviewer --brief "复核 coder-1 的改动" --fork coder-1 --workspace inherit

# 6. 在公开大群发消息并 @唤醒 指定工蜂（或 @all）
tower swarm post "@frontend 请把按钮间距再收紧 4px"

# 7. 查看共享消息板
tower swarm board --tail 20

# 8. 无损切换模型（保留工蜂完整对话记忆与工位）
tower swarm model frontend claude-sonnet-4 --reason "复杂样式需要更强模型"

# 9. 用控制塔质检门验证工蜂成果，通过后自动合并独立工位
tower swarm verify frontend --land

# 10. 巡检是否有卡死工蜂，必要时自动 @催办
tower swarm monitor --nudge

# 11. 任务完成后永久解散工蜂并自动清理它的临时工位
tower swarm kill frontend

# 12. 启动网页看板并访问蜂群实时群聊面板
tower panel   # 打开浏览器访问 http://127.0.0.1:8787/swarm
```

---

## 6. 权限边界规则

- **总调度（`coordinator`）**：可设置蜂群标题（`title`）、召唤任意工蜂、为任意工蜂切换模型、解散任意工蜂。
- **工蜂（worker）**：可以通过 `--by <自己的名字>` 召唤自己的小帮手；只能切换自己或自己召唤的帮手的模型；只能解散自己召唤的帮手（解散某只工蜂不会连带杀掉它召唤的帮手）。
