import time
from app.config import ROOT,MODULES
from app.errors import DomainError
from app.values import scalar
from app.manifest import digest,manifest
from app import store
from app.modules.m1_accesses.rules import evaluate as stage1
from app.modules.m2_resources.rules import evaluate as stage2
from app.modules.m3_grants.rules import evaluate as stage3
from app.modules.m4_conflicts.rules import evaluate as stage4
from app.modules.m5_partitions.rules import evaluate as stage5
from app.modules.m6_audit.rules import evaluate as stage6
from app.modules.m7_seals.rules import evaluate as stage7
from app.modules.m8_backfill.rules import evaluate as stage8,adjudicate as backfill_adjudicate,validate_shape as validate_backfill
STAGES=[stage1,stage2,stage3,stage4,stage5,stage6,stage7,stage8]
def load_sample(name):
    if name not in {"dirty.csv","clean.csv","legacy.csv"}: raise DomainError("BAD_SAMPLE","样例不存在",404)
    return (ROOT/"sample_data"/name).read_bytes()
def prepare_backfill(params):
    """在授权定版之前先形成补录追溯裁决。

    裁决对每批补录只产生一份结论：授权命中、分区判定、对账与证据导出
    全部通过context服从它；形状非法直接抛错，批次整体不计算、不落库。
    """
    batch=(params or {}).get("backfill")
    if not batch: return None
    validate_backfill(batch)
    return backfill_adjudicate(batch)
def calculate(raw,params=None,stop=7):
    params=params or {};started=time.monotonic();context={"parameters":params,"raw":raw,"quarantine":[]};stages=[];rows=[]
    backfill=prepare_backfill(params)
    if backfill is not None:
        # 唯一追溯裁决贯穿后续全部阶段
        context["backfill"]=backfill
        context["backfill_admitted"]=backfill["grants"]
    for index,stage in enumerate(STAGES[:stop+1]):
        result=stage(raw if index==0 else rows,context)
        result=scalar(result)
        # M8为补录追溯裁决，其rows是裁决行而非访问行，不覆盖主链路访问记录
        if index<7: rows=result["rows"]
        context["quarantine"].extend(result.get("quarantine",[]))
        if index==0: context["intake"]=result["metrics"]
        result["cumulative_quarantine"]=list(context["quarantine"])
        context[MODULES[index][0]]=result
        stages.append(result)
    return {"run_id":digest(raw,params)[:20],"stages":stages,"manifest":manifest(raw,params,started),"parameters":params,"backfill":scalar(backfill)}
def execute(raw,params=None):
    result=calculate(raw,params);return store.save(result["run_id"],result)
def stage_preview(index,payload):
    if "rows" in payload and index>0:
        if not isinstance(payload["rows"],list) or any(not isinstance(row,dict) for row in payload["rows"]): raise DomainError("BAD_ROWS","记录需要对象列表")
        if not isinstance(payload.get("context",{}),dict): raise DomainError("BAD_CONTEXT","上下文需要对象")
        return scalar(STAGES[index](payload["rows"],payload.get("context",{"parameters":{},"quarantine":[]})))
    raw=payload.get("csv")
    raw=raw.encode() if isinstance(raw,str) else load_sample(payload.get("sample","dirty.csv"))
    return calculate(raw,payload.get("parameters",{}),index)["stages"][-1]
