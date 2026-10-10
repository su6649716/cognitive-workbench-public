# 运行合同

## 环境与路径

Python 3.10+ 标准库、POSIX 文件锁；Git 检查/同步另需 Git。本包环境与验证记录见交付 `verification/results.json`。不声称 Windows、全部宿主或真实账号验证。无需 pip 安装。

`<SKILL_DIR>` 指当前读取的本包绝对路径，不能把相对 scripts 路径误认成用户项目里的文件。**2.4 默认全新状态根：`~/.cognitive-workbench/`**。索引和账本在其 `memories/` 下；不会自动读取 `~/.codex/memories/`。`COGNITIVE_HOME` 可明确覆盖状态根。同步默认也使用新根，但不会自动创建远端仓库或执行推送。明确的 `--ledger/--index/--codex-home/--repo` 优先，手动指向旧数据属于显式操作。

独立试用用隔离数据路径；不得仅因读取新版就执行默认个人路径写入。所有写操作继承用户/宿主授权，本脚本不自行判断用户是否真正允许。

## 命令与返回

| 入口 | 保留命令 | 返回约定 |
|---|---|---|
| cognitive_ledger.py | record/update/list/audit/due | 正常0；有待回看/审计发现1；参数/结构/IO错误2 |
| project_index.py | upsert/find/list/system/gate | 正常0；未命中/闸门未过1；错误2 |
| git_project_guard.py | --path、--init-if-missing、--check-close、--baseline-head、--task-path | 检查成功0，拒绝/读取失败2；0不等于closure.ready |
| sync_state.py | --codex-home、--repo、--push | 成功0，阻断1；以pushed、remote_verified和SHA解释同步 |

新增 audit/due 可选 --project/--episode-id/--thread-id；project为精确过滤，旧list仍支持子串。新增同步 --dry-run、--remote、--branch；新增upsert --merge-baseline可按字段更新；这些选项的具体目标由当前调用参数决定。

示例：

```text
python3 <SKILL_DIR>/scripts/cognitive_ledger.py --ledger <副本>/events.jsonl list --project <项目>
python3 <SKILL_DIR>/scripts/cognitive_ledger.py --ledger <副本>/events.jsonl audit --project <项目>
python3 <SKILL_DIR>/scripts/project_index.py --index <副本>/index.json find --query <名称>
python3 <SKILL_DIR>/scripts/project_index.py --index <副本>/index.json gate --query <名称> --risk medium --target-stage full
python3 <SKILL_DIR>/scripts/test_cognitive_workbench.py
```

## 项目内工程约定与入口

全新建设依据当前目标建立 `docs/PROJECT_BASELINE.md`；维护任务才核实并更新仍适用的权威文件。`assets/project-baseline/PROJECT_BASELINE.md` 是待填骨架，`assets/project-baseline/AGENT_ENTRY.md` 是待合并的项目级入口；都不是已经给用户项目安装好的规则。

经项目文档写入授权后才合并项目 `AGENTS.md`，全局入口仍单独授权。没有新运行器、安装脚本、定时任务或数据库；新增模块从同一权威文件和公共代码继续。实际文档路径登记到既有 `product_docs`，基线值留在真实代码/配置，不新建 schema 或第二份索引。

## 状态数据

账本保留 event/update JSONL、四类型、旧ID、episode/parent/thread关系、原预测与追加时间。合法旧事件没有episode时只在查询视图取自身ID；缺旧任务关联或结果由审计指出，不倒填。未知扩展字段保留；语法合法但缺事件身份/类型/项目/statement的不再当有效快照。

索引读取旧schema，保留未知顶层/项目/基线扩展；更新未给--status保留旧值，新项目默认unknown。别名、成品入口、远端、上下游、真源继续合并。未提供基线时原字段与扩展完整保留；显式基线JSON默认沿用旧版整对象替换，只有增加--merge-baseline才按字段合并（列表显式提供则替换）；用户纠正后调用方必须主动标stale，脚本不会凭修改内容猜“用户已确认”。

规划known状态与source类型受检；null/对象不能转字符串假装来源。旧结构存在且来源缺失仍可读取，进入中高风险gate时不得通过。状态字符串本身不是语义确认的证明。

## 锁与错误

账本/索引锁包住读取、检查、写入；索引原子替换，账本只追加并flush/fsync。同步用状态仓库Git目录锁，且源字节一次读取后全程一致核验。锁只协调本工具，不是对其他进程的沙箱。

Git读取失败不报告干净，不猜分支main；收口核对起点祖先关系。只读查询正常不写业务数据；文件权限、损坏与冲突要报具体缺口，不能静默重置。

## 校验边界

本包的validate_skill.py是自带静态检查，不是官方认证。规则存在检查与确定性测试不等于独立模型行为增益；本包不附历史实验资料或未执行行为实验的通过结论。

## 知识收尾只读盘点

```text
python3 <SKILL_DIR>/scripts/closeout_inventory.py --project <项目根>
python3 <SKILL_DIR>/scripts/closeout_inventory.py --project <项目根> --ancestor-rules
```

仅用 Python 标准库；Git 为可选的只读证据源。默认上限 20000 项，`--max-entries` 可显式调整；计数含目录和文件，避免海量空目录绕过上限。退出 0 表示盘点完整，不证明知识正确；1 表示部分盘点或 Git 证据未取得；2 表示输入无效。盘点程序不提取或输出文档/规则/记忆正文；Git 状态查询可能检查受跟踪文件，但返回本程序的仅是元数据。符号链接只列目标和状态，不遍历链接目录，不读取外部目标正文；上级规则仅在显式选项下列候选。不自动写报告文件，调用方可将 stdout 保存到已授权的交付位置。

输出中的规则候选不证明宿主加载；残留候选不等于删除许可；工作区盘点不等于内容审计。独立知识收尾不因本脚本返回成功就称发布已完成。
