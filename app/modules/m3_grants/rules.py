from app.values import instant,number
from app.reasons import reject
from app.errors import DomainError
GRANTS=[{"id":"G-01","principal":"analyst","resource":"orders","start":"2026-11-01T00:00:00Z","end":"2026-12-01T00:00:00Z","from_partition":"2026-01","to_partition":"2026-12","effect":"allow"},{"id":"G-02","principal":"analyst","resource":"orders","start":"2026-11-10T00:00:00Z","end":"2026-11-20T00:00:00Z","from_partition":"2026-10","to_partition":"2026-11","effect":"deny"},{"id":"G-03","principal":"auditor","resource":"customers","start":"2026-11-01T00:00:00Z","end":"2026-12-01T00:00:00Z","from_partition":"2026-01","to_partition":"2026-12","effect":"allow"}]
def validate_grants(grants):
    ids=set()
    for g in grants:
        if g["id"] in ids or instant(g["start"])>=instant(g["end"]) or g["effect"] not in {"allow","deny"} or g["from_partition"]>=g["to_partition"]:raise DomainError("BAD_GRANT","授权版本无效")
        ids.add(g["id"])
    return grants
def resolve_grants(context):
    """授权定版的授权集合 = 规则库授权 + 补录追溯裁决已采信的授权。

    补录授权由M8追溯裁决唯一产出：仅依据完整者可入册，且生效起点固定为
    规则到达时刻，因此这里直接沿用上一步裁决，不做任何回溯放宽。
    显式传入parameters.grants时仍以调用方授权集合为准（测试/预览口径）。
    """
    params=context.get("parameters",{}) or {}
    if "grants" in params:
        return validate_grants(params["grants"])
    grants=[dict(g) for g in GRANTS]
    backfill=context.get("backfill_admitted") or []
    ids={g["id"] for g in grants}
    for extra in backfill:
        grant={k:v for k,v in extra.items() if k not in {"adjudication_id"}}
        if grant["id"] in ids:
            raise DomainError("BACKFILL_GRANT_COLLISION","补录授权与在册授权ID冲突")
        ids.add(grant["id"]);grants.append(grant)
    return validate_grants(grants)
def evaluate(rows,context):
    grants=resolve_grants(context);good=[];bad=[]
    for row in rows:
        try:
            at=instant(row["access_time"]);reported=number(row["reported"])
            if reported not in {0,1}:raise DomainError("BAD_OBSERVED","报送判定必须为0或1")
            active=[g for g in grants if g["principal"]==row["principal"] and g["resource"]==row["resource"] and instant(g["start"])<=at<instant(g["end"])]
            good.append({**row,"at":at.isoformat(),"candidates":active,"candidate_count":len(active),"reported":str(reported)})
        except (DomainError,KeyError) as e:bad.append(reject(row,getattr(e,"code","MISSING_FIELD"),str(e)))
    return {"rows":good,"quarantine":bad,"metrics":{"active_hits":sum(r["candidate_count"] for r in good),"grant_versions":len(grants)}}
