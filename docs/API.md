# 接口

模块GET使用仓内样例，模块POST可传csv或rows与context，直接计算当前规则。/api/runs使用完整流水线并原子保存。每个阶段返回rows、quarantine、metrics；后续阶段还返回累计隔离。/api/upload接收multipart文件file。/api/runs/<id>/archive只导出一份带清单与证据的CSV。

## 补录追溯（M8 `/api/backfill`）

补录批次通过 `parameters.backfill` 随 `/api/runs` 提交，结构为 `{request_key, items[]}`。每个 item 必须含 id、principal、resource、partition_from/to、effect、document_date、arrived_at、effective_end、basis。时间一律带时区偏移。

裁决口径固定为 `arrival_effective`：补录授权自规则到达时刻（arrived_at）起在 [arrived_at, effective_end) 内生效，单据日期（document_date）仅作为业务回溯主张留证，不改变生效起点。每个自然键产生唯一裁决号 `RA-xxxx`，批次产生 `RAB-xxxx`；同自然键的重试条目折叠计数，不重复裁决、不重复授权。

`GET /api/backfill/attempts` 返回补录请求台账：同一 request_key 仅一条，attempts 记录跨请求次数，完全相同的重跑复用同一 run_id 且 attempts 不增长。依据不完整（basis 为空或命中哨兵）的条目不进入授权集合，M7 封存指标 sealable=false 并输出 seal_blockers；此时证据仍可导出（seal_manifest 标 BLOCKED），但 `POST /api/runs/<id>/review` 的“已复核”封存返回 409 SEAL_BLOCKED，旧版本与来源依据原样保留。补录形状非法在计算前抛 422，批次整体不落库。

