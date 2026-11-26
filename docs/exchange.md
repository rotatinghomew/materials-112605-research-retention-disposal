# 留样交接与处置

物理质量为毫克，以 sample id 跟踪。拆分后的 ID 不可复用，母样消耗份额从母样可用量扣除。保全来源可以按报告或按实际实体，后者仅针对凭条列明的一组实物和从其划出的等分，按签收时间与 effective 区分。已确认销毁的实物仍出现在历史回执中。

资料使用 UTF-8 JSON。每个集合各自维护记录 id，跨集合引用使用相应记录 id。事件中的 happened 是业务发生时间，arrived 是对接方可提供该回执的时间。到达时间不能代替业务时间。这些记录来自不同流程阶段，读到一个回执并不代表本地已做过相应操作。

## `sites`

保管和处置组织的权能。字段为 `id`, `org`, `capability`。

## `samples`

样片标签、来源、克重和出库日期限制。字段为 `id`, `origin`, `parent`, `mass`, `initial_site`, `earliest`。

## `reports`

档案报告可支持 exact 实物或 origin 同源替代，min 控制复核量。字段为 `id`, `archive`, `owner`, `version`, `claims`, `from`, `until`, `replaces`。

## `holds`

法务保全及释放。报告范围与指定实物范围不同。字段为 `id`, `legal`, `scope`, `references`, `state`, `effective`, `reported`。

## `tickets`

库管拟办的处置申请。mass 是同张单待确认的实体重量。字段为 `id`, `sample`, `station`, `destination`, `mass`, `requested`。

## `receipts`

交接点、处置点回执分别确认不同事实。字段为 `id`, `ticket`, `sample`, `actor`, `state`, `mass`, `happened`, `arrived`。

## 回执查询

本机回放只提供对接方的既有回执。`GET /v1/receipts?at=...` 的 `at` 是 URL 编码后的带偏移时间，返回对象中 `receipts` 是在该时间之前及当时可取得的回执。同一条数据可以在连续查询中出现，列表先后不代表业务先后。`503` 表示当前无法查询回执。
