# 授权定义

资源orders与订单等价，customers与客户等价。principal为主体文本，不支持模糊主体。partition为YYYY-MM；分区授权[start,end)不含end月。授权时间也为[start,end)，到期时刻立即失效。

每条授权包含主体、资源、生效窗、分区窗和allow/deny。全部条件必须同时匹配。拒绝优先，多个允许不能覆盖任何显式拒绝；无匹配默认拒绝。匹配规则ID按字典序稳定排序，冲突显示全部允许与拒绝证据。

observed为真实访问执行结果allowed或blocked；reported为上游0/1判定，留作证据。expected为实际授权复算，分区模块重建reported为执行结果便于二值对账。越权放行和误拦截分别计数，禁止用相互抵消的总数代替逐条核查。

业务ID和超大整数按文本保形；Decimal参与数量金额计算，对外序列化为字符串。哨兵集合定义在app/values.py，缺失值不能当零。所有时间带UTC偏移，先转UTC比较，业务日按指定地区划分。区间统一[start,end)。重复业务键保留第一条、完全相同行计重复；值冲突行隔离且保留原值。空文件、全空白、仅表头分开拒绝。

运行清单包括输入sha256、规则版本、参数、生成时刻与耗时。源行编号以CSV解析器的物理结束行号表示，引号内换行不增加记录数。输入超过4MB返回413，非法表头和类型返回422，失效版本复核返回409。

# 补录追溯裁决

补录授权是访问发生之后才补登记的规则。审计只能采信访问时点已经到达的规则，因此系统裁决口径固定为 arrival_effective：补录授权一律自规则到达时刻 arrived_at 起，在半开区间 [arrived_at, effective_end) 内生效；单据日期 document_date 早于到达时刻时只登记为业务方的回溯主张（document_claims_backdate=true），不能回溯放行历史访问。

补录批次 `{request_key, items[]}` 字段：

| 字段 | 含义 |
|---|---|
| request_key | 补录请求幂等键，同键重试进同一台账 |
| id | 补录授权业务编号，入册授权前缀 B- |
| principal/resource/partition_from/partition_to | 主体、资源与分区窗 [from,to)，不含 to 月 |
| effect | allow/deny，拒绝仍遵守拒绝优先 |
| document_date | 单据声称生效日期（业务主张，留证） |
| arrived_at | 规则实际到达规则库时刻（裁决生效起点） |
| effective_end | 补录授权生效截止时刻，必须晚于 arrived_at |
| basis | 依据材料清单，空值或哨兵（待定/未知/NULL等）判依据不完整 |
| source_refs | 来源依据引用，随证据导出 |

每个自然键（id+主体+资源+分区窗+effect+三个时间）经 sha256 生成唯一裁决号 RA-xxxx；整批生成批次号 RAB-xxxx。同自然键的重复条目是重试，折叠为一条裁决并计 retries_collapsed，绝不重复计数；同 id 但内容不同判 BAD_BACKFILL_DUP_ID。跨请求重试由 backfill_attempts 台账按 request_key 去重。

依据不完整的补录：不进入授权定版（grants_admitted 不增加）、不产生授权命中，M7 sealable=false 并给出 BACKFILL_BASIS_INCOMPLETE 阻断；证据导出保留该裁决与阻断记录（seal_manifest=BLOCKED），封存签核返回 409 SEAL_BLOCKED 且保留旧版结论。补录授权命中在M4以 backfill_rules/backfill_hit 标注，分区判定、对账差异与证据导出均读取同一份裁决。
