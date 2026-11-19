import csv,io,json
def build_archive(run):
    stream=io.StringIO(newline="");writer=csv.writer(stream)
    writer.writerow(["run_id","input_sha256","rule_version","kind","source_lines","reason","evidence"])
    meta=run["manifest"]
    def prefix(): return [run["run_id"],meta["input_sha256"],meta["rule_version"]]
    for row in run["stages"][-2]["rows"]:
        writer.writerow(prefix()+["accepted",json.dumps(row.get("_sources",[])),row.get("status",""),json.dumps(row,ensure_ascii=False)])
    for row in run["stages"][-2].get("cumulative_quarantine",[]):
        writer.writerow(prefix()+["quarantine",json.dumps(row.get("_sources",[])),row.get("reason",""),json.dumps(row,ensure_ascii=False)])
    # 补录追溯裁决随证据导出：每批补录的唯一裁决、生效口径与依据完整性
    backfill=run.get("backfill") or {}
    for ruling in backfill.get("rows",[]) or []:
        writer.writerow(prefix()+["backfill_ruling",json.dumps(ruling.get("source_refs",[])),ruling.get("ruling",""),json.dumps(ruling,ensure_ascii=False)])
    for blocker in backfill.get("seal_blockers",[]) or []:
        writer.writerow(prefix()+["seal_blocker",json.dumps(blocker.get("adjudication_ids",[])),blocker.get("code",""),json.dumps(blocker,ensure_ascii=False)])
    seal=run["stages"][6]["metrics"]
    writer.writerow(prefix()+["seal_manifest",json.dumps([]),"READY" if seal.get("sealable",True) else "BLOCKED",json.dumps(seal,ensure_ascii=False)])
    return "﻿"+stream.getvalue()
