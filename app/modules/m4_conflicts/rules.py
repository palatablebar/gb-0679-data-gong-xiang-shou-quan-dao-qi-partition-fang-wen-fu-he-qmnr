def decide(candidates,partition):
    hits=sorted((g for g in candidates if g["from_partition"]<=partition<g["to_partition"]),key=lambda g:g["id"])
    denied=[g["id"] for g in hits if g["effect"]=="deny"]
    allowed=[g["id"] for g in hits if g["effect"]=="allow"]
    backfill_hits=[g["id"] for g in hits if g.get("source")=="backfill"]
    return {"effect":"deny" if denied or not allowed else "allow","matched_rules":[g["id"] for g in hits],"deny_rules":denied,"allow_rules":allowed,"backfill_rules":backfill_hits,"backfill_hit":bool(backfill_hits),"conflict":bool(denied and allowed)}
def evaluate(rows,context):
    out=[]
    for row in rows:
        decision=decide(row.get("candidates",[]),row["partition"])
        out.append({**row,**decision,"rule_evidence":" / ".join(decision["matched_rules"]) or "NO_GRANT"})
    return {"rows":out,"quarantine":[],"metrics":{"conflicts":sum(r["conflict"] for r in out),"explicit_denies":sum(bool(r["deny_rules"]) for r in out),"default_denies":sum(not r["matched_rules"] for r in out),"backfill_hits":sum(r["backfill_hit"] for r in out)}}
