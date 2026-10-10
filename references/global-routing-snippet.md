# 可选全局接入

仅用户明确要求安装时差异合并进宿主全局规则；本次独立包不自动安装、替换原 Skill、修改 AGENTS.md 或创建定时任务。入口是认知一下与 $cognitive-workbench；不要在同一宿主同时安装两个相同标识而声称路由确定。

项目内 `AGENTS.md` 的工程基线入口由 [项目工程约定](project-baseline.md) 按当前项目授权合并，和这里的全局路由不同；不要为建立项目基线重复安装全局片段。

标准的 2.4 全局片段已单独提供在交付包的 `integration/AGENTS.global.snippet.md`；需要人工按差异合并到本机 `~/.codex/AGENTS.md`。其原则是：**所有项目开发都由认知助手统筹**，包括 UI、Bug 修复和小改动，但思考深度按问题调整，普通讨论不因此建立事件。

可选 `integration/hooks.json.example` 通过 Codex 已支持的 `SessionStart` / `UserPromptSubmit` / `Stop` 事件恢复项目上下文与观察 Git 变化。全局安装和钩子信任需用户在本机显式操作，不自动覆盖同名 Skill、现有 AGENTS 或 hooks。

allow_implicit_invocation 是宿主允许隐式选择的声明，不保证每次触发。没有宿主定时/事件入口就不能声称已无人运行。周度复盘仅在用户授权创建的定时任务中显式调用本 Skill；不新建独立后台系统。

POSIX 状态脚本、Codex 默认目录与其他宿主路径的差异见 runtime-contract.md；原目录不迁移。
