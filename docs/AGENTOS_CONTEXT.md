# AGENTOS_CONTEXT（工作宪法 · 增量维护版）

> 本文件是**工作宪法**：任何新会话以本文件为最新态入口。
> 完整基线见 [docs/agentos-charter.md](agentos-charter.md)（v0.15.0 定稿，含终极定位、冻结路线图、开发流程、评审准则）；
> 当期验收依据见 [docs/rfc/v0.16-domainpack-contract.md](rfc/v0.16-domainpack-contract.md) §6（十五项清单）。
> 维护规则：不重生成全文，只更新下方三节（现状/待办/铁律增量）。

## 一、项目现状（**v0.16.0 已发布**，2026-08-24）

- 证明命题兑现：同一 Runtime 经同一 DomainPack 契约容纳 embeddedops + networkops 双领域同时运行，core 零领域代码（架构测试强制），旧 checkpoint 兼容有 golden 证据。
- 发布门禁：485+1skip / Console 17 / L1+L2 verify-fresh 全部通过；tag `v0.16.0`。
- 显式债务：network capabilities=()（RFC §11）、生产 bge 知识初始化挂接（装配收口）、STM32 选型回退怪癖（候选修复队列）。

- 基线：后端 **467** / Console **17** 全绿（v0.15.0 为 446/17，零回归）。
- 已落地提交：
  - `e39837a` docs：章程固化 + v0.16 RFC（验收清单定稿 = 用户 10 项 + RFC 补充 a–e，共 15 项）
  - `abbba3b` feat：`src/network_agent_rag/packs/` 契约（DomainPackSpec，治理资产强制）+ PackRegistry fail-fast 管线（冲突/依赖/权限目录边界/角色与能力权限一致性/PolicyEngine 覆盖/factory 解析）+ 21 项测试。**v0.15 遗留项①（Registry↔Policy 一致性）已清零**，风险敞口注释在注册器 docstring。
- 命名查重结论（铁律 17 已清偿）：**裸名 "AgentOS" 不可对外发布**（Agno AgentOS runtime、npm `@framers/agentos`、AG2 自称 Open-Source AgentOS）。内部代号继续用；对外名称待差异化候选评审。
- v0.16 步骤 2（embedded pack 迁移）进行中；Phase 0（checkpoint 兼容性调查）已完成，结论与证据见 RFC §10。

## 二、当前待办

**v0.16 步骤 2（进行中）**：embedded 领域资产全部经 PackRegistry 管线注册。
- **Phase 0 已完成**（结论：非破坏性迁移，证据 = golden checkpoint fixtures + 恢复测试，方法见 RFC §10）。
- 迁移范围：三 Agent、capabilities 多版本行为不变、固定图冻结、三权限 + EmbeddedEngineer、embedded_knowledge collection（注册时初始化）、`/api/v1/embedded/*` routers、embedded_cases 评测集、ui_extensions 仅登记（Console 零改动）。
- 纪律：import 扫描测试先建（core 禁 import packs，附"仍在 core 的领域模块"排除列表；embedded 本步清空、network 全部在列）；存量语义断言零修改（import 路径调整除外且需列明全部改动行）；diff 几乎全是移动不顺手重构；全量门禁 + `make embedded-demo` 绿后才可提交。
- 汇报五项证据：测试数据 / 语义零修改证据 / Phase 0 结论（含 golden checkpoint 恢复事实）/ 排除列表当前内容 / embedded-demo 端到端结果 + 清单推进项。

**v0.16 步骤 2.5（已完成，2026-08-24）**：推送前收口——注册期知识初始化（幂等、fail-loud、fresh-init 可执行验证）、向量库卫生检查（结论 RFC §12：无脏数据，demo 子目录自包含）、verify-fresh L1 落地、执行提示词持久化。

**v0.16 步骤 3（已完成，2026-08-24）**：network pack 迁移——16 模块入包、api.benchmarks 重分类、燃尽清单归零；golden fixtures（审批挂起 + mid-flight）新布局恢复并 resume 至 executed；全量 485 + Console 17 + verify-fresh 双 pack 全绿。Phase 0 四项结论见 RFC §11。
**v0.16 步骤 4/5（排队）**：main.py 全面 pack 驱动装配 + 架构测试收口；README Roadmap + 0.16.0 + tag。

### 双清单机制（铁律 21，2026-08-24 建档）

**清单一：NETWORK_STILL_IN_CORE（领域燃尽清单，步骤 3 归零）**——见 `tests/test_pack_architecture.py`，17 项网络领域模块，每迁移一项删一项，只缩不增。

**清单二：PLATFORM_FACILITIES_PENDING（平台设施待裁决清单，持久追踪不燃尽）**：

| 设施 | 现状 | 裁决窗口 |
| --- | --- | --- |
| ~~`digital_twin/`、`frontend/`、四个顶层 demo 入口~~ | **已裁决（2026-08-24，步骤 3）**：全部随 network pack 迁入 `packs/networkops/` | 已执行 |
| `demo/`（包）+ `scripts/demo.*` | v0.14 本地故障演示层（引用已重指向 pack），含独立 persist 目录（`data/chroma/sw1_sw2_*`） | 步骤 4 装配收口时定归属（子目录归属见 RFC §12） |
| `api/benchmarks.py` / `api/observability.py` | 平台服务投影（跨域）；observability 的 `IncidentId` 已本地化（不再 import pack） | 步骤 4 装配收口时复核 |
| ~~`api/history.py`~~ | **已裁决**：随 chat 面迁入 network pack | 已执行 |
| `governance/` / `observability/` / `evaluation/`（runner） | 平台设施，跨域复用 | 留 core（初裁），v0.20 复核 |

清单二条目"临时归属+待裁决"均为显式记录；裁决时点到来前不动其内部实现（红线）。

## 三、铁律增量（自基线章程 v0.15.0 之后新增，编号接续基线 §六）

18. **Phase 0 未答不动文件**：任何迁移类任务，先完成事实调查（如 checkpoint 序列化机制）并把结论写入 RFC，方可开始改代码；调查结论是汇报必填项。
19. **存量语义断言零修改**（验收 d）：迁移只允许改 import/路径类断言，且全部改动行需在汇报中列明；出现"必须改断言才能绿"即为语义变更，停下回报。
20. **迁移 diff 应几乎全是移动**：不顺手重构、不改图结构、不改审批语义、不动存量角色权限。
21. **import 扫描测试先于迁移落地**：无检验机制的"core 零领域代码"验收项不算数；排除列表显式维护（双清单：领域燃尽项 + 设施待裁决项），作为后续步骤的完成条件刻度；只缩不增。
22. **命名已定结论**：AgentOS 仅为内部代号；对外发布名须差异化并另行评审（查重证据在 RFC §9 附录）。
23. **排除列表是活文档**：每步迁移汇报必须附当前排除列表内容，评审以此核对进度与回归。
24. **兼容性结论必须是可执行证据，不是审计结论**："旧数据可恢复"类命题须以旧布局生成的 golden fixture + 新布局恢复测试证明；注意 fail-loud 发生在读而非写——新代码自建自读是循环论证。fixture 必须在布局变更前生成并随库提交。
25. **审计算结论、执行算证据**：推断性命题（"初始化会成功""数据可恢复"）必须配可执行断言（fresh-init 测试、golden fixture 模式）；注册期初始化失败必须 fail-loud，禁止静默降级。
26. **manifest 声明的资产必须在管线内兑现**：capabilities/权限/知识 collection/评测集/router 声明后必须经注册管线落地或显式标注兑现时点，禁止"声明了但永远没人执行"。
27. **迁移归属三分法**：领域资产进 pack / 平台设施待裁决（清单二追踪）/ 装配层留 core（唯一可 import packs 的位置）；疑难项允许"临时归属+待裁决"但必须显式记录。
28. **每步可运行 = 零状态启动 + 全量绿 + 每 pack 端到端**，缺一即未完成；`make verify-fresh`：L1 每步/push 前实现方自证，L2 全新克隆于 tag 前执行。
