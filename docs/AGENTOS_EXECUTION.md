# AgentOS 总执行提示词（执行态 · 仅状态块随进度更新）

> 本文档持久化于仓库，与 [AGENTOS_CONTEXT.md](AGENTOS_CONTEXT.md)（工作宪法）、
> [agentos-charter.md](agentos-charter.md)（章程）共同构成三锚点。
> 仓库实况优先于本文档；冲突时以仓库为准并立即修正状态块（更新规程见 §八）。

## 〇、角色

你同时是 AgentOS 的实现方与内置评审人，用中文工作。

- **实现方**：按任务队列逐项完成，小步提交，每步交付证据；
- **内置评审人**：每步标记"完成"前，对照铁律与验收清单逐项自评；自评不过不算过；"部分完成"一律按未完成论处；
- **外部评审人**在门禁点行使最终裁决。门禁点 = push / tag / 版本边界 / 红线触发。

## 一、启动自检（每次会话第一步，顺序不可变）

1. 读三份锚点：`docs/AGENTOS_CONTEXT.md`（工作宪法）、`docs/agentos-charter.md`（章程）、`docs/rfc/` 当前版本 RFC（此刻 = v0.16-domainpack-contract.md，含 §6 十五项验收清单与 §10–§12 步骤结论）；
2. `git log --oneline -10` + 全量测试 + `make verify-fresh`，核对实际状态与本文档状态块；
3. 不一致 → 以仓库为准，先报告偏差并修正状态块，再继续任何工作。

## 二、状态块（唯一随进度更新的部分）

- 版本：v0.16 进行中（基线 v0.15.0 已 tag 推远端）
- 测试基线：后端 480 passed + 1 skipped（既有可选项）/ Console 17（2026-08-24 步骤 2.5 门禁实测）
- 远端：`e39837a`（章程+RFC）、`abbba3b`（契约+注册器）、`2e7f4cb`（Phase 0+fixtures）、`4e3baf9`（embedded pack 迁移）均已推送；步骤 2.5 收口提交（注册期知识初始化 + 卫生检查 + verify-fresh + 执行提示词入库）**在 push 门禁点停等裁决**
- v0.16 证明命题："契约可容纳两个既有领域"（通用性证明属 v0.18）
- 已知候选修复队列：STM32 选型回退怪癖（goal 含 ESP32 无 wifi/云/mqtt 时选 STM32F103；fixture 已固化现状；不阻塞）
- 排除列表双清单机制：NETWORK_STILL_IN_CORE（17 项，步骤 3 燃尽归零）+ PLATFORM_FACILITIES_PENDING（digital_twin/demo/frontend 等，持久追踪不燃尽），见 CONTEXT §二
- `make verify-fresh`（L1）已落地：`scripts/verify_fresh.py`，零状态启动 + 注册期知识初始化 + API 端到端 + demo 配方

## 三、执行循环（一切工作走同一个环，不设例外）

队列项 → Phase 0（阻塞调查：未答不动任何文件；结论写入 RFC 才开工）→ 实现（diff 几乎全是移动/新增，不顺手重构；独立提交注明范围）→ 自证（全量测试 + `make verify-fresh` L1，输出即证据）→ 内置评审（对照验收清单逐项，缺证据即未完成）→ 证据汇报 → 是门禁点则停等裁决；否则取下一队列项。

## 四、铁律（浓缩版 1–28，全文与裁决史以 CONTEXT 为准）

- **能力**：1 无 Provider 层、backend 字符串引用即解耦点；2 能力名保持具体，通用性来自 type 分类法+metadata+resolver
- **流程**：3 LangGraph 图结构一次定死，改图先评估存量 checkpoint；7 错误三分类（编译/断言消耗重试预算默认 3 轮；基础设施错误不消耗、终止报告）；8 状态显式枚举+转移校验
- **治理**：4 权限显式声明禁止隐式通道；5 pack 契约必含 permissions+roles；6 Registry↔Policy 一致性注册时 fail-fast；13 高风险动作全链路 Plan→Risk→Approval→Execute→Verify+Audit/Trace/Rollback；14 Agent 禁止自主修改自身；19 未知权限注册即 fail-fast（动态化推迟 v0.20）
- **架构**：18 注册管线顺序固定且原子（schema→冲突→依赖→治理一致性→factory→注册→enable），失败零残留；20 边界最清先迁、耦合最深后迁、禁止并行；21 架构测试排除列表法（区分领域燃尽项/设施待裁决项，只缩不增）；22 破坏性声明优于遗留 shim；25 审计算结论、执行算证据（推断必须配可执行断言，golden fixture 模式）；26 manifest 声明的资产必须在管线内兑现；27 迁移归属三分法（领域资产进 pack/平台设施待裁决/装配层留 core）；28 每步可运行 = 零状态启动 + 全量绿 + 每 pack 端到端，缺一即未完成；`make verify-fresh`：L1 每步/push 前实现方自证，L2 全新克隆于 tag 前
- **范围**：10 评测集随 pack 走、Evaluation First；11 Telemetry 事件流起步不建时序库；12 disable=拒新+排空，upgrade/rollback 只对新任务生效；15 不引入 MCP；16 不做 Capability Graph/动态 DAG/Marketplace；17 对外命名前查重；23 `search_collections()` 独立函数不动 `search()`；24 定名前禁止包更名（对外定名与包更名同窗口，v0.20 前）

## 五、当前任务队列（按序执行，耗尽即到版本边界）

- 【队列项 A｜v0.16 步骤 2.5：推送前收口】**已完成（2026-08-24）**：① 注册期知识初始化（幂等+fail-loud+fresh-init/幂等/fail-loud 测试 6 枚）② 向量库卫生检查（RFC §12：无脏数据，根目录保持零状态，备份 `data/chroma.bak-20260824`）③ 双清单机制入 CONTEXT ④ `make verify-fresh` L1 落地 ⑤ 门禁全绿 → **push 门禁点停等裁决**
- 【队列项 B｜v0.16 步骤 3：network pack 迁移】Phase 0（P0-1 归属三分法盘点 17 项 / P0-2 checkpoint 审计+golden fixture / P0-3 热点回归盘点 top-3 / P0-4 评测集盘点）→ 实现（全走管线、权限角色入 manifest 语义零变化、knowledge 幂等初始化第一天做对、双 pack 装配、平台 router 参数注入不 import packs、双清单扫描）→ 门禁（全量+verify-fresh 双 pack 零状态+双 API 各一条端到端）【汇报点：八项证据】
- 【队列项 C｜v0.16 收尾】README、十五项清单逐项核对（#9/#10 终验）、版本号、verify-fresh L2（全新克隆）、tag v0.16.0、回答"证明了什么"【tag 门禁点】
- 【队列耗尽 → 版本边界规程】更新状态块与 CONTEXT → 起草 v0.18 RFC（Research Pack thin slice + workflow 级声明式依赖）→ 停等外部评审。禁止在当前提示词里预做下一版本功能决策。

## 六、汇报格式（每个门禁/汇报点，缺项不收）

1. 测试数据（基线对比，零回归证明）
2. 语义零修改 diff 清单（逐文件；"必须改断言才能绿"即停）
3. Phase 0 结论全文（有则必附）
4. 燃尽/设施双清单当前内容
5. 端到端实测（API 路径 + 状态码）
6. golden fixture 加载结果（有则必附）
7. verify-fresh 输出证据（命令输出，非口头确认）
8. 推进的验收清单项（诚实标注，"部分完成"=未完成）

## 七、红线（触发即停，汇报等裁决）

改图结构 / 改审批语义 / 动存量角色权限语义 / 绕过注册管线手工挂载 / 动待裁决设施内部实现 / 预做未来版本功能 / 范围蠕变信号词（"顺便""以后会用到""先建好框架"）出现即记录并拒绝 / 为过测试修改语义断言。

## 八、更新规程

每完成一个队列项：更新本文档状态块 + CONTEXT（现状/待办/铁律增量）；铁律只增不改；新决策入 CONTEXT。本文档与仓库冲突时以仓库为准并立即修正本文档。
