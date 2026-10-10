# 2.4 项目长期连续开发：项目规划、索引与认知账本

**这是认知助手自己的项目执行合同，不是一个新 Skill 或第五个认知工作流。** 所有正式项目工作均应恢复规划，认知分析深度随任务调整；小变更并不等于禁止系统思考。

## 唯一的新状态根

2.4 新安装默认使用 `~/.cognitive-workbench/`（可用 `COGNITIVE_HOME` 明确覆盖）。与 `~/.codex/memories` 及任何旧状态仓库完全分离；不读取、迁移或覆盖旧账本。用户要求全新项目，不从其他项目名字相似就套入目标或默认约束。首次有权写入时由 `scripts/project_session.py init` 幂等创建：

```text
~/.cognitive-workbench/
├── STATE_INFO.json                 # 2.4 独立身份标记
└── memories/
    ├── PROJECT_INDEX.json         # 唯一项目入口和现役状态
    └── COGNITIVE_EVENTS.jsonl     # 唯一重要认知事件账本
```

`runtime/` 下的短期每轮 Git 观察标记是临时中间态，不是第二本账本；完成 Stop 后移除。原 `project_index.py` 和 `cognitive_ledger.py` 继续管理统一索引与重要决策。任何只读审计只能 `peek`，不能因读取本参考而写入状态。

## 项目三种知识面各司其职

- **项目文档**（优先现役 `docs/PROJECT_PLAN.md`、`PRODUCT.md`、`docs/PROJECT_BASELINE.md` 等）：最终目的、组件边界、实际操作路径、阶段目标、工程与设计约定。项目不存在文档时，正式立项或全面开发可以在已有写入授权下建立简洁的 `docs/PROJECT_PLAN.md`，不是强制每轮重写。
- **项目索引**：准确源码根、身份、现役规划基线、入口文件、最近任务摘要和 Git 检查点。只保存可恢复的最小状态，不复制所有文档。
- **认知账本**：影响未来行动的关键用户决定、重要否决、可验证假设、阶段性可复用结论。仍使用四类事件及 episode/thread/parent 关联。不要把每次按钮微调或聊天记录写成 `task_close`。

## 每轮项目开发三阶段

**开始前**：准确解析项目根；已有索引则恢复规划、规则、上一次结果和相关决策；未登记而本轮开始正式施工时，新建本项目索引身份。已明确的目标不重新盘问。索引失效或与当前用户纠正冲突时，不拿旧规划继续做，先更新受影响层。每次继续开发也要保留系统视野，不能因为局部修改就失去架构/UI/业务背景。

**执行中**：主动发现更好的实现候选；依约定检查职责、依赖、接口、设计规范、风险和受影响的上游消费者。既有路线合理就继续，有证据支持的更好方法在授权内直接使用。真正改变产品语义或用户确认边界再回到共同定义，不为普通技术取舍重复问用户。

**完成后**：以测试、真实文件和 Git 为证据，对齐本轮结果。普通进度更新索引 `last_task` / `last_checkpoint`；重要决定、验证、否决路线才通过 `cognitive_ledger.py record/update` 写认知账本。当前规划被纠正时同步实际权威文档及规划基线。**只有 Git 文件变化的钩子仅写 `last_observed_change`，不能声称功能完成、用户确认，也不能自动生成认知结论。**

## 确定性脚本入口

`<SKILL_DIR>` 是本版本安装路径。脚本无需守护进程、数据库和付费 API。所有写操作必须符合用户和宿主权限。

```sh
# 新建 2.4 独立数据根（只在首次且获准写入时）
python3 <SKILL_DIR>/scripts/project_session.py init

# 项目正式开发时：恢复或登记、返回上下文
python3 <SKILL_DIR>/scripts/project_session.py begin --project <代码项目根>

# 只读审计时：恢复存在的上下文，不写入
python3 <SKILL_DIR>/scripts/project_session.py peek --project <代码项目根>

# 任务完成时：记录可核验的进度；不伪造认知事件
python3 <SKILL_DIR>/scripts/project_session.py complete --project <代码项目根> --summary '<实际完成事项>'

# 关键决策仍调用原认知账本：以 begin 返回的 project_id 作为 --project
python3 <SKILL_DIR>/scripts/cognitive_ledger.py record ...
```

含 Git 的工程，完成时可传 `--checkpoint <当前HEAD>`；必须与真实 HEAD 相符。阶段复杂且用户要求方案，交付具体方案和工程规范，不能仅记账。创建项目文件的写入范围服从当前任务授权。

## 全局接入，不靠反复点名

Mac Codex 可在全局 `~/.codex/AGENTS.md` 添一段轻量规则：每个项目开发任务调用本 Skill 并恢复规划。若用户**明确安装并信任** `integration/hooks.json.example` 所示钩子，Codex 的 SessionStart / UserPromptSubmit 会在支持的宿主把已存在的规划入口加到上下文；Stop 根据 Git 的真实前后状态把机械改动记在索引。

- 只有经过 `/hooks` 审查和信任的钩子才会执行；未配置、未信任、运行环境无 Python 或无法访问本机路径时不会自动调用。
- `UserPromptSubmit` 的临时基线不永久记录聊天；Stop 不分析全文，不把自然语言意图或推断自动保存为用户决策；非 Git 项目依赖 Agent 调用 `complete`。
- 即使钩子已开启，也不能把“已看见 Git 状态”冒充“已写入产品语义规划”。关键项目规划和重要决定由 Agent 在用户已授权范围内完成。
- ChatGPT 网页、远程 Codex、Claude 等不能自动读取当前 Mac 的本地目录；需要各自受支持的执行环境和明确的同步机制。

## 可信事实与更新边界

用户明确表态 > 真实实现/运行结果 > 现役项目文档 > 原索引 > 历史材料。用户确认过的目标是约束，不可被 AI 猜测覆盖；旧规划已经废弃时不要重建它。无可靠证据标成待核实，不能假装已部署或全部完成。仅在有新的长期价值时创建事件，已存在事件续接更新；保留反证与下次召回条件。
