# Design: Automatic Skill Capture (左半圈)

Status: DRAFT — for review before implementation
Owner: skill-system
Related: README.md, CHANGELOG.md v1.2.0

## 1. 目标

把 agent 的"做完复杂任务自动沉淀成 skill"这个行为，从
"3 行系统提示词靠模型自觉"升级成**有真实判定 + 真实注入的生产级机制**。

平台定位：**核心 CLI-agnostic，任何 agent 工具只写薄适配层即可接入。**
不止 Claude Code / OpenCode / Codex，未来要能接 Cursor、Aider、
自研 agent、甚至非 CLI 的 agent runtime。

右半圈（`skill_manage` 6 层校验 + `skill_curator` 状态机 + `skill_index`
提示词路由）保留现状不动，本设计只补左半圈。

## 2. 现状缺口

```
[Session happens]
   → (空)                          ← 缺：信号采集
   → (空)                          ← 缺：复杂度判定
   → 3 行 prompt 靠模型自觉 offer   ← 缺：注入机制
   → skill_manage(create)          ← 已有
   → skill_index 注入路由           ← 已有
   → curator 淘汰                   ← 已有
```

左半圈三步全缺。`Stop` hook 现在只跑 curator，和 capture 无关。

## 3. 架构

核心原则：**判定逻辑、信号 schema、offer 文案全部住在 `lib/`，是
单一源。每个 agent 工具只实现两个适配器：Collector（产信号）和
Injector（投 offer）。**

```
                 ┌─────────────────────────────────────┐
                 │  lib/  (CLI-agnostic, shared core)   │
                 │                                       │
                 │  SessionProfile  (schema + scoring)  │
                 │  OfferGate       (threshold + cooldown)│
                 │  OfferMessage    (portable fragment)  │
                 │  skill_manage    (already exists)     │
                 │  skill_curator   (already exists)     │
                 │  skill_index      (already exists)     │
                 └──────────┬──────────────────────────┘
                            │
              ┌─────────────┼─────────────┐
              │             │             │
   adapters/claude_code  adapters/opencode  adapters/<future>
   - Collector           - Collector        - Collector
   - Injector            - Injector         - Injector
```

**三个 CLI 都有原生 hook，全部一等公民，无降级路径。** `stdin_json`
是给未来非这三者的工具（Cursor / Aider / 自研 agent）的逃生口。

能力对照（均已查证官方文档）：

| CLI | hook 机制 | 计数工具调用 | 任务完成 | 注入 offer |
|---|---|---|---|---|
| Claude Code | shell hooks | `PostToolUse` | `Stop` | Stop stdout 进下一轮上下文 |
| OpenCode | JS/TS 插件 | `tool.execute.after` | `session.idle` | `tui.prompt.append` 直接注入 |
| Codex CLI | `hooks.json` | `on_tool_call` | `on_task_complete` | `action: custom` 跑命令 |

三个 adapter 都能完整实现 Collector + Injector，只是 hook 格式不同。

## 4. SessionProfile schema (portable)

所有 Collector 产出同一 schema。版本化，向后兼容靠 `schema_version`。

```json
{
  "schema_version": 1,
  "session_id": "uuid或cli原生id",
  "agent_tool": "claude-code",
  "started_at": "2026-06-30T10:00:00Z",
  "ended_at":   "2026-06-30T11:30:00Z",
  "tool_calls": 12,
  "distinct_tools": ["read_file", "edit", "bash", "grep"],
  "errors_encountered": 2,
  "error_recoveries": 1,
  "user_corrections": 1,
  "turns": 8,
  "signals": {}
}
```

字段语义（`signals` 留作各 Collector 扩展，gate 不强依赖）：

- `tool_calls` — 一次会话内 agent 调用工具的总次数
- `distinct_tools` — 用到的工具种类，衡量"多步组合"
- `errors_encountered` — 工具返回 error 的次数
- `error_recoveries` — error 后续有对同类工具的成功调用（启发式）
- `user_corrections` — 用户消息中包含纠正意图的次数（关键词 + 长度突变）
- `turns` — agent 完成的轮次

## 5. OfferGate (复杂度判定 + 防骚扰)

纯函数，无副作用。输入 SessionProfile，输出决策。

```
score = base_rules(profile)
should_offer = score >= threshold AND not_in_cooldown(session_id, agent_tool)
```

默认规则（每条独立 OR，env 可覆盖阈值）：

| 规则 | 默认阈值 | 说明 |
|---|---|---|
| tool_calls | >= 5 | 复杂任务主信号 |
| error_recoveries | >= 1 | 踩坑后救回 = 高价值经验 |
| user_corrections | >= 1 | 用户纠正过的流程 = 易错点 |
| distinct_tools 且 tool_calls | >= 4 且 >= 3 | 多工具组合 |

**防骚扰 (cooldown)** —— 状态机在 `~/.skill-system/state/offer_state.json`：

```
IDLE
  → profile 达标 → emit offer → WAITING
WAITING
  → skill_manage(create) 被调用 → COOLDOWN (默认 24h, 同 session 模式)
  → 超时 (默认 1h) 无 create → IDLE
COOLDOWN
  → 到期 → IDLE
```

同 session 模式在 cooldown 内不重复 offer。避免一次任务被切几段后
被骚扰 3 次。

## 6. OfferMessage (portable 注入片段)

一段固定的、CLI-agnostic 的提示词片段，由 Injector 投递给 agent。
内容是命令式指令，不是建议：

```
Your last session met the complexity threshold for skill capture
({reason}; profile: {tool_calls} tool calls, {error_recoveries} 
recoveries, {user_corrections} corrections).

If the just-completed task represents a reusable workflow:
1. Summarize the approach in 2-3 sentences to the user.
2. Ask if they'd like it saved as a skill.
3. On yes, call skill_manage(action="create", name="<slug>", content="<full SKILL.md>")
   following the 8-section format. Do NOT skip the offer if the task was complex.

Hard constraints still apply: description <= 60 chars, author = "skill-system".
```

这一段比原来的 3 行强在：(a) 有量化理由，(b) 命令式而非"建议"，
(c) 指定 agent 要先问用户再落盘，不是默默 create。

## 7. 适配器契约 (Collector + Injector)

每个 agent 工具实现两个抽象类（`adapters/base.py`）：

```python
class Collector:
    agent_tool: str
    def collect(self) -> SessionProfile: ...

class Injector:
    agent_tool: str
    def inject(self, message: OfferMessage) -> None: ...
```

### 7.1 Claude Code 适配器

- **Collector**: 读 Stop hook 的 stdin JSON（含 `transcript_path`），
  解析 Claude Code 的 transcript JSONL，累加 tool_calls / errors。
  纯 Python，零依赖。
- **Injector**: Stop hook 的 stdout 在下一轮会被加进 agent 上下文。
  Stop hook 里跑判定，达标就把 OfferMessage 打到 stdout。下一轮
  UserPromptSubmit 不需要改，agent 自然看到上一轮结尾的 offer。

### 7.2 OpenCode 适配器

OpenCode 有完整插件系统（JS/TS），事件比 Claude Code 还丰富。

- **Collector**: JS 插件订阅 `tool.execute.after` 累加 tool_calls /
  errors，订阅 `session.idle` 触发判定。插件把 profile 写到
  `~/.skill-system/state/last_session.json`，调 `skill-profile` 命令评分。
- **Injector**: 用 `tui.prompt.append` 在下一轮 prompt 开头追加 OfferMessage
  片段。比 Claude Code 的 stdout 注入更直接——不用等下一轮，当轮就能投。
  也可注册 custom tool（`session_report`）让 agent 主动调。

插件文件：`~/.config/opencode/plugins/skill-capture.js`，installer 自动写入。

### 7.3 Codex 适配器

Codex CLI 在 `~/.codex/hooks.json` 配置 hook，事件
`on_tool_call` / `on_task_complete` / `on_error`，action 支持
`custom`（跑命令）+ `log`。

- **Collector**: `on_tool_call` 配 `action: log` 写到
  `~/.skill-system/state/codex_tool.log`，`on_task_complete` 触发
  `skill-profile --from-log` 解析累加并评分。
- **Injector**: `on_task_complete` 配 `action: custom` 跑
  `skill-offer --emit`，输出 OfferMessage。Codex 把 custom 命令的
  stdout 注入下一轮对话（需在实现时验证注入路径，否则降级为
  写 `last_offer.txt` + `AGENTS.md` 一行让 agent 检查）。

需要 `[features].codex_hooks = true`，installer 自动写入配置。

### 7.4 stdin_json 通用适配器 (未来扩展)

任何 agent 工具，只要能在任务结束时把 SessionProfile JSON 通过
管道或文件喂进来，就能接入：

```bash
some-agent-tool ... | tee /tmp/profile.json
skill-profile --from-stdin < /tmp/profile.json
# 或
skill-profile --from-file /tmp/profile.json
```

这覆盖 Cursor / Aider / 自研 agent / 任何能写文件的 runtime。
不需要为每个工具写 Python 适配器——这是"适配更多工具"的真正
逃生口。

### 7.5 MCP 适配器 (未来)

把 `session_report` 作为一个新的 MCP tool 暴露，agent 在任务末尾
主动调用。比 stdin 更优雅，但需要 agent runtime 主动配合。本期
不实现，留接口。

## 8. 文件布局 (新增)

```
lib/
  session_profile.py     # schema + load + validate + score 基础
  offer_gate.py          # 阈值 + cooldown 状态机 (纯函数)
  offer_message.py       # OfferMessage 渲染
  adapters/
    __init__.py
    base.py              # Collector / Injector ABC
    claude_code.py
    opencode.py
    codex.py
    stdin_json.py        # 通用：从 JSON 读 profile
bin/
  skill-profile          # CLI: 解析 / 评分 / 触发 offer
  skill-offer            # CLI: 显示/管理 offer 状态 (--emit 输出片段)
hooks/
  claude_code_stop.sh    # 改：加 profile + gate + emit offer
  claude_code_posttooluse.sh  # 可选：增量累加 profile (性能优化)
plugins/
  opencode/
    skill-capture.js     # OpenCode 插件：订阅 session.idle + tool.execute.after
  codex/
    hooks.json.fragment  # Codex hooks.json 片段（installer 合并进用户配置）
docs/
  design.md             # 本文档
tests/
  test_profile.py       # schema 解析 + 评分
  test_gate.py          # 阈值 + cooldown 状态机
  test_adapters.py      # 各 Collector 用 fixture transcript 测试
```

## 9. 测试矩阵

| 层 | 测试 | 覆盖 |
|---|---|---|
| session_profile | 解析 3 种来源的 fixture transcript | tool_calls / errors / recoveries 计数正确 |
| offer_gate | 4 条规则各 case + cooldown 状态机 | 达标 / 未达标 / cooldown 阻断 / 超时回 IDLE |
| offer_message | 渲染含正确量化理由 | reason / profile 字段填充 |
| adapter.claude_code | fixture transcript → profile → gate → emit | 端到端 |
| adapter.stdin_json | 通用 JSON → profile → gate | 任何工具接入 |
| 防骚扰 | 同 session 重复 Stop 不重复 offer | cooldown 生效 |

目标：左半圈测试覆盖 >= 现有右半圈 (41 断言)。

## 10. 不在本期范围

- LLM consolidation pass (curator 那个 stub) — 仍保持默认关
- agent 自主决定 skill 质量 (eval 流程) — 那是 skill-creator 的事，
  本项目只管"何时 offer"，不管"offer 后质量"
- 跨会话的 skill 推荐排序 — 留给 index 已有的 state 排序
- 云端同步 / 多机 — 本期纯本地

## 11. 待决问题 (需要你拍板)

1. **offer 是否默认 ON？** 新装用户要不要默认开启自动 offer，
   还是默认 OFF 走手动 `skill-profile`，用户显式开启？  
   建议：默认 ON，但首次 offer 时文案里带一句"可关闭：skill-offer --pause"。

2. **offer 后是 agent 先问用户，还是 agent 直接 create？**  
   建议：先问。默默 create 会污染 skill 库（用户不知情就多了个文件），
   也违背 curator "积累必然腐烂"的前提——无意愿的 skill 进库等于噪声。

3. **阈值是否要可配置文件，还是只靠 env？**  
   建议：env 覆盖 + `~/.skill-system/config.toml` (可选)。本期只做 env。

4. **三个 CLI 都有原生 hook，本期三个 adapter 是否全做？**  
   建议：全做。能力对等，没有降级，工作量主要在 OpenCode 的 JS 插件
   和 Codex 的 hooks.json 格式验证。stdin_json 留给未来非这三者的工具。

5. **要不要给现有 3 行系统提示词瘦身，改成"offer 由机制注入，平时
   不在 prompt 里占位"？**  
   建议：瘦。平时 prompt 不含 capture 指令，只在 offer 待注入时
   由 Injector 投递。省 ~5 行常驻上下文。
