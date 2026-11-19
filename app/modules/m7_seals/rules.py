import hashlib,json
from decimal import Decimal
from app.errors import DomainError
from app.reasons import sources
def evaluate(rows,context):
    stages=[v for v in context.values() if isinstance(v,dict) and "metrics" in v]
    balances=[s["metrics"]["balanced"] for s in stages if "balanced" in s["metrics"]]
    if balances and not all(balances): raise DomainError("UNBALANCED","来源行未对平，不能归档")
    ids=[str(r.get("event_id",r.get("_line",""))) for r in rows]
    if len(ids)!=len(set(ids)): raise DomainError("ARCHIVE_DUPLICATE","归档业务键重复")
    encoded=json.dumps(rows,ensure_ascii=False,sort_keys=True,default=str).encode()
    # 补录追溯裁决是封存的前置约束：依据不完整的补录不得封存，
    # 但证据导出仍保留（ready=False并列出阻断原因），由复核/封存入口拒绝落封。
    backfill=context.get("backfill") or {}
    seal_blockers=list(backfill.get("seal_blockers",[]) or [])
    ruling_ids=[r.get("adjudication_id") for r in backfill.get("rows",[]) or []]
    if len(ruling_ids)!=len(set(ruling_ids)):
        raise DomainError("BACKFILL_RULING_DUPLICATE","追溯裁决不唯一，不能归档")
    return {"rows":rows,"quarantine":[],"metrics":{"records":len(rows),"source_lines":len(set(x for row in rows for x in sources(row))),"sha256":hashlib.sha256(encoded).hexdigest(),"ready":not seal_blockers,"sealable":not seal_blockers,"differences":sum(r.get("status")=="差异" for r in rows),"backfill_rulings":len(ruling_ids),"backfill_blocked":len(seal_blockers),"backfill_batch_id":backfill.get("batch_id",""),"backfill_policy":backfill.get("policy","")},"seal_blockers":seal_blockers}
