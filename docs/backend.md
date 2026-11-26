# 留样后端：保全、处置与跨站交接

本包在《留样交接与处置》交换资料之上实现业务后端。只依赖 Python 3.11
标准库；`partner.py` 仍是对接方回执回放服务，本后端通过
`retention.exchange` 读取它，不把对端回执当成本地操作。

## 模块划分

| 模块 | 职责 |
| --- | --- |
| `retention.model` | 不可变领域记录与交换 JSON 的加载/校验 |
| `retention.ledger` | 按业务时间回放处置单与回执，重建在站/在途/处置点/已销毁质量桶 |
| `retention.holds` | 保全范围解析、释放链、跨站执行清单、追回动作 |
| `retention.reports` | 报告有效期与更正链、exact/origin 复核量核算 |
| `retention.disposal` | 处置许可计算、真实处置申请校验、保管点工作清单 |
| `retention.reconciliation` | 班次切换三态追回、迟到销毁冲减、待确认缺口 |
| `retention.service` | 各角色写命令（登记、拆分、保全、申请、回执） |
| `retention.exchange` | 对接方 HTTP 客户端（503 显式失败，不当作“无数据”） |
| `retention.api` / `server.py` | 标准库 HTTP 入口 |

## 领域规则落地方式

### 实物身份与拆分

- 样品 id 全局唯一、不可复用；拆分产生新 id，`parent` 指向母样，
  母样账面质量扣除划出份额（`RetentionService.split_sample`）。
- 保全按身份集合覆盖：sample 保全覆盖凭条列明实物及其全部拆分后代；
  拆出新标签不消灭原保全关系（`holds.covered_samples`）。

### 报告引用与更正

- `exact` claim 只认可指定样片及其拆分件；`origin` claim 认可同一
  `origin` 的任意留样互相替代，复核量按来源合并计算。
- 同一样片支撑多份报告时，保留需求取各 claim 的**最大值**，不累加。
- 更正报告（`replaces`）从自身 `from` 起接管旧报告；旧报告与历史
  回执仍可追溯。档案员视图 `archive_view` 区分每个版本引用的实物。

### 保全范围不扩大

- report 保全只覆盖引用报告 claims 列明的实物及其拆分件，**不**扩大
  到同 origin 的其他部门留样。
- sample 保全只覆盖凭条列明实物及其等分。
- 释放（scope=hold）只解除它引用的那条保全；误绑撤回不影响其他
  独立保全。是否有效按 `effective` 业务时间判定，`reported` 只表示
  法务凭证到达时间。
- 同一留存声明跨站点时，`site_hold_tasks` 按各站当时在手实物和其
  经手处置单生成本站任务，他站实物不出现在本站清单。

### 处置许可（`disposal.disposal_options` / `assert_ticket_allowed`）

许可量按以下顺序从实际在站量中扣除：

1. 有效保全覆盖份额——到期与保全冲突时按保全办理；
2. 有效报告的 exact 保留量（只认那块的剩余部分）；
3. origin 同源组缺口——组内替代充足时不锁定具体某一块；
4. 未结清处置单已占用份额——同一实物不签第二张处置单；
5. 最早出库时间 `earliest` 限制。

去向按站点权能区分：送往 `destroy` 点是销毁申请，`retain` 点之间
是交接转移。

### 回执事实不混同

`dispatched`（出库）、`accepted`（预接收）、`destroyed`（销毁）、
`returned`（退回）是四类事实，分别建质量桶。只有 `destroyed` 且
来自有销毁权能站点的回执才减少可复核量。证据链缺环（如缺出库却
预接收、缺预接收却退回）只登记 `gaps`/`inconsistencies` 并标出
待确认方，不虚构库存，也不因此补开处置单。

### 班次切换与迟到销毁

`reconciliation.classify_at_hold` 按保全生效时实物所在位置给结论：

- 站内 → `freeze_location`，未出库处置单 → `withdraw_ticket`；
- 在途 → `intercept`；处置点预接收 → `demand_return`；
- 已销毁且业务时间早于保全生效 → `write_off` 冲减；
- 销毁晚于保全生效 → `breach_investigation`。

`late_destruction_reconciliation` 冲减实际销毁份额后，按当前有效
报告重算复核是否充足，并保留处置依据（申请时间、许可质量、去向、
站点）和销毁/到达两个时间。`knowledge_at` 参数只采信该时刻前
`arrived` 的回执，用于复现“切换班次时看得到的证据”。

## HTTP 接口

启动：`python3 -m retention.server --data fixtures/exchange.json --port 8088`

- `GET /health`
- `GET /v1/reports?at=...[&known=...]` 档案员报告视图
- `GET /v1/stations/{site}/worklist?at=...[&known=...]` 保管点清单
- `GET /v1/disposal/options?at=...[&site=...]` 可处置范围
- `GET /v1/holds?at=...` 有效保全及覆盖身份
- `GET /v1/holds/{id}/recovery[&known=...]` 三态追回结论
- `GET /v1/holds/{id}/write-off[&at=...][&known=...]` 冲减与复核结论
- `GET /v1/confirmations[&known=...]` 待确认方与记录缺口
- `POST /v1/splits`、`/v1/reports`、`/v1/holds`、`/v1/holds/release`、
  `/v1/tickets`、`/v1/receipts`

时间参数均为带时区偏移的 ISO-8601（URL 编码）。业务违规返回 409，
参数错误返回 400，引用缺失返回 404。

## 测试

`python3 -m unittest discover -s tests -v` 运行全部测试，包括对
`partner.py` 真实子进程的同步与 503 集成测试。
