# 认知事件生命周期

## 身份与阶段

请求是一次用户表达；事件是改变行动的认知动作；episode 是围绕同一真实问题的一组关联阶段。一个线程可以含不同问题，一个问题可以跨线程续接。关键词命中只是候选，恢复时核对项目、问题和来源，不自动串台。

四类型保持 expansion_gate、hypothesis、task_close、portfolio_review。共同定义中的目的/方案/样板/实施是同一 expansion_gate 的检查点，不各建事件。

## 新建或续接

先按项目与阶段查近期事件，检查目标/约束是否相同，以及是否有新事实、范围决定、测试开始/返回或新长期结论。只是改稿、重述、继续已确认实施，不新建。新事实若只改变原决定的证据或实施状态，update 原事件。

同一阶段 expansion_gate、task_close、portfolio_review 各至多一条；开放的 hypothesis 先解决/更新，不并行制造同义测试。新问题可使用新的 episode，即使 thread_id 相同；不伪造新 ID 绕过同一问题去重。

## 阶段转移

扩张判断作出暂缓/取消或建设选择，更新/关闭；正式开始验证，新建关联 hypothesis。假设结果返回更新为 supported/refuted/inconclusive；形成长期决定后关联 task_close。后续执行、回归或采用更新该收口，不再造第二条。

## 关联与写入

先解析 parent_id，再核对同项目与有效 episode_id，再去重；二者冲突拒绝。首事件可自动以自身 ID 为 episode；后续沿用，不重新编号。thread_id 能取得必须使用；不能取得写 thread_unavailable_reason，不猜造 ID。

record/update 使用锁覆盖读取、检查、追加；只追加历史，不重写旧预测或删除旧事件。metadata 递归合并保留旧扩展，note 追加。旧行缺少 episode 时查询以自身 ID 解释，不写回补填。

新 hypothesis 必须有 due_at 或 review_trigger；open/active 更新不能同时清空两者。closed 必须有 outcome。系统尚未获认可的建议保持 open/proposed，用户决定或真实行动发生后才写相应状态。

## 用户决定与纠正

用户原话明确、接受已展示选择或真实动作是来源；“继续设计”不确认全部建议。“先看看/待定”不提升确认状态。当前纠正更新对应决定并保留来源，未受影响部分有效，不倒写成最初目的。

## 知识收尾的落点

知识面盘点、文档同步和复核现场属于当前 `task_close` 的内部工作，不增加事件类型。文档足以承载的事实不重复进账本；无新结论就不记。已有收口的证据、来源和受影响知识面需要补充时，沿同一项目与 episode 追加更新。

本包维护的 JSONL 账本与项目索引，不等同于宿主自动生成的 MEMORY.md、历史汇总或检索索引。前者通过当前已授权脚本更新；后者按平台权限默认只读。更正账本用 update 保留历史，不能把“清理记忆”变成删掉旧事件或重写原预测。

## 兼容与审计

`list` 保留旧项目子串查询；`audit/due --project` 是精确项目过滤，另可用 --episode-id/--thread-id。全局复盘仍可不带过滤。

`healthy` 与旧版一样同时考虑结构缺口和到期；新增 `structural_healthy`、`review_needed` 分别解释。到期不是结构损坏，更不是其他项目阻断当前任务的理由。

历史重复不删除。核实后可给非主事件 metadata.lifecycle_migration.legacy_duplicate_of 标记，审计单列历史；历史 closed 缺 outcome 可标 legacy_missing_outcome_reason，不伪造结果。结构合法不代表事实已核实或确认真实发生。
