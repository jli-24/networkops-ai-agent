# AGENTOS_CONTEXT（工作宪法 · 增量维护版）

> 本文件是**工作宪法**：任何新会话以本文件为最新态入口。
> 完整基线见 [docs/agentos-charter.md](agentos-charter.md)（v0.15.0 定稿，含终极定位、冻结路线图、开发流程、评审准则）；
> 当期验收依据见 [docs/rfc/v0.16-domainpack-contract.md](rfc/v0.16-domainpack-contract.md) §6（十五项清单）。
> 维护规则：不重生成全文，只更新下方三节（现状/待办/铁律增量）。

## 一、项目现状（v0.16 进行中）

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

**v0.16 步骤 3（待下达）**：network pack 迁移（排除列表归零是其完成条件）。
**v0.16 步骤 4/5（排队）**：main.py 全面 pack 驱动装配 + 架构测试收口；README Roadmap + 0.16.0 + tag。

## 三、铁律增量（自基线章程 v0.15.0 之后新增，编号接续基线 §六）

18. **Phase 0 未答不动文件**：任何迁移类任务，先完成事实调查（如 checkpoint 序列化机制）并把结论写入 RFC，方可开始改代码；调查结论是汇报必填项。
19. **存量语义断言零修改**（验收 d）：迁移只允许改 import/路径类断言，且全部改动行需在汇报中列明；出现"必须改断言才能绿"即为语义变更，停下回报。
20. **迁移 diff 应几乎全是移动**：不顺手重构、不改图结构、不改审批语义、不动存量角色权限。
21. **import 扫描测试先于迁移落地**：无检验机制的"core 零领域代码"验收项不算数；排除列表显式维护，作为后续步骤的完成条件刻度。
22. **命名已定结论**：AgentOS 仅为内部代号；对外发布名须差异化并另行评审（查重证据在 RFC §9 附录）。
23. **排除列表是活文档**：每步迁移汇报必须附当前排除列表内容，评审以此核对进度与回归。
24. **兼容性结论必须是可执行证据，不是审计结论**："旧数据可恢复"类命题须以旧布局生成的 golden fixture + 新布局恢复测试证明；注意 fail-loud 发生在读而非写——新代码自建自读是循环论证。fixture 必须在布局变更前生成并随库提交。
