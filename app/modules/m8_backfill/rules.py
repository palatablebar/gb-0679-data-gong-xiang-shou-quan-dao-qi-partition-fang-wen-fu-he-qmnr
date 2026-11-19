import hashlib,json
from app.values import instant
from app.errors import DomainError

# 核心裁决（TRAP-28）：
# 补录授权在访问发生之后才到达规则库，访问时点并不存在该规则，
# 审计只能采信访问时已到达的规则，因此补录一律自“规则到达时刻”起生效，
# 单据日期只是业务方的回溯主张，作为证据保留但不能改变生效起点。
RULING_POLICY="arrival_effective"
RULING_TEXT="补录授权自规则到达时生效，不按单据日期回溯；单据日期仅作为业务主张留证。"
RULING_REASON="审计只能采信访问时点已到达的规则，后到规则不得回溯放行历史访问。"

REQUIRED_FIELDS=("id","principal","resource","partition_from","partition_to",
                 "effect","document_date","arrived_at","effective_end","basis")
EFFECTS={"allow","deny"}

# 视为依据不完整的哨兵：空、仅空白、显式未知标记
BASIS_SENTINELS={"","-","N/A","NA","NULL","NONE","UNKNOWN","未知","无","待定","待补"}


def _clean(value):
    return "" if value is None else str(value).strip()


def _check_partition(value):
    part=_clean(value)
    if len(part)!=7 or part[4]!="-" or not part[:4].isdigit() or not part[5:].isdigit() \
       or not 1<=int(part[5:])<=12:
        raise DomainError("BAD_BACKFILL_PARTITION","补录分区必须为YYYY-MM")
    return part


def validate_shape(batch):
    """结构校验：形状非法直接422，批次不落库（整批回切）。"""
    if not isinstance(batch,dict):
        raise DomainError("BAD_BACKFILL","补录批次需要对象")
    items=batch.get("items")
    if not isinstance(items,list) or not items:
        raise DomainError("BAD_BACKFILL","补录批次必须包含非空items列表")
    request_key=_clean(batch.get("request_key"))
    if not request_key:
        raise DomainError("BAD_BACKFILL_KEY","补录批次缺少request_key，重试无法去重")
    id_keys={}
    for index,item in enumerate(items):
        if not isinstance(item,dict):
            raise DomainError("BAD_BACKFILL_ITEM","补录条目必须为对象")
        for field in REQUIRED_FIELDS:
            if field not in item:
                raise DomainError("BAD_BACKFILL_ITEM",f"补录条目{index}缺少字段{field}")
        if _clean(item["effect"]) not in EFFECTS:
            raise DomainError("BAD_BACKFILL_EFFECT","补录裁决必须为allow或deny")
        # 时间必须带时区偏移，与全系统口径一致
        instant(item["document_date"]);instant(item["arrived_at"]);instant(item["effective_end"])
        pf=_check_partition(item["partition_from"]);pt=_check_partition(item["partition_to"])
        if pf>=pt:
            raise DomainError("BAD_BACKFILL_PARTITION","补录分区窗[from,to)非法")
        # 同一自然键的重复条目是重试，交由裁决折叠；同ID但不同内容才是冲突
        nkey=_natural_key(item);bid=_clean(item["id"])
        if bid in id_keys and id_keys[bid]!=nkey:
            raise DomainError("BAD_BACKFILL_DUP_ID","补录条目ID相同但内容冲突")
        id_keys[bid]=nkey
    return batch


def _natural_key(item):
    # 同一授权的补录重试：业务身份+单据日期+到达时刻+分区窗
    return "|".join([_clean(item[k]) for k in
                     ("id","principal","resource","partition_from","partition_to",
                      "effect","document_date","arrived_at")])


def _adjudication_id(item):
    digest=hashlib.sha256(json.dumps(
        {"key":_natural_key(item),"policy":RULING_POLICY},
        sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:16]
    return "RA-"+digest


def _basis_complete(item):
    """补录依据必须真实、可核验；空值/哨兵/仅空白都判不完整。"""
    basis=item.get("basis")
    if not isinstance(basis,list) or not basis:
        return False,["依据清单为空或不是列表"]
    missing=[]
    for index,evidence in enumerate(basis):
        text=_clean(evidence)
        if not text or text.upper() in BASIS_SENTINELS:
            missing.append(f"依据{index+1}缺失或不可核验")
    return (not missing),missing


def build_grant(item):
    """把已采信的补录条目转换为与授权定版同构的授权版本。
    生效起点固定为规则到达时刻（裁决核心），结束时刻取单据授权窗的end，
    半开区间[arrived_at, effective_end)，结束时刻必须显式给出且晚于到达。"""
    start=instant(item["arrived_at"])
    end_raw=_clean(item.get("effective_end"))
    if not end_raw:
        raise DomainError("BAD_BACKFILL_WINDOW","补录授权必须显式给出effective_end")
    end=instant(end_raw)
    if end<=start:
        raise DomainError("BAD_BACKFILL_WINDOW","补录生效结束时刻必须晚于到达时刻")
    return {
        "id":"B-"+_clean(item["id"]),
        "principal":_clean(item["principal"]),
        "resource":_clean(item["resource"]),
        "start":start.isoformat(),
        "end":end.isoformat(),
        "from_partition":_clean(item["partition_from"]),
        "to_partition":_clean(item["partition_to"]),
        "effect":_clean(item["effect"]),
        "source":"backfill",
        "backfill_id":_clean(item["id"]),
        "document_date":instant(item["document_date"]).isoformat(),
        "arrived_at":start.isoformat(),
    }


def adjudicate(batch,context=None):
    """对一批补录形成唯一追溯裁决。

    返回：
      rulings        每个自然键一条裁决（重试折叠，不重复计数）
      grants         依据完整且可采信的补录授权（生效起点=到达时刻）
      metrics        批次度量
      seal_blockers  封存阻断原因（依据不完整时非空）
    """
    validate_shape(batch)
    collapsed={};retries=0
    for item in batch["items"]:
        key=_natural_key(item)
        if key in collapsed:
            # 同一补录重试：折叠保留首条，只计重试次数，绝不重复计数
            retries+=1
            continue
        complete,missing=_basis_complete(item)
        document_at=instant(item["document_date"]);arrived_at=instant(item["arrived_at"])
        backdated=document_at<arrived_at
        ruling={
            "adjudication_id":_adjudication_id(item),
            "backfill_id":_clean(item["id"]),
            "request_key":_clean(batch["request_key"]),
            "principal":_clean(item["principal"]),
            "resource":_clean(item["resource"]),
            "partition_from":_clean(item["partition_from"]),
            "partition_to":_clean(item["partition_to"]),
            "effect":_clean(item["effect"]),
            "document_date":document_at.isoformat(),
            "arrived_at":arrived_at.isoformat(),
            "document_claims_backdate":backdated,
            "policy":RULING_POLICY,
            "ruling":"arrival_effective",
            "ruling_text":RULING_TEXT,
            "ruling_reason":RULING_REASON,
            "basis_complete":complete,
            "basis_gaps":missing,
            "basis":[_clean(x) for x in item["basis"]] if isinstance(item["basis"],list) else [],
            "source_refs":list(item.get("source_refs") or []),
            "status":"采信" if complete else "依据不足",
        }
        collapsed[key]=ruling

    rulings=list(collapsed.values())
    grants=[]
    for ruling in rulings:
        if not ruling["basis_complete"]:
            continue
        source_item=next(it for it in batch["items"]
                         if _adjudication_id(it)==ruling["adjudication_id"])
        grant=build_grant(source_item)
        grant["adjudication_id"]=ruling["adjudication_id"]
        grants.append(grant)

    incomplete=[r["adjudication_id"] for r in rulings if not r["basis_complete"]]
    backdated=[r["adjudication_id"] for r in rulings if r["document_claims_backdate"]]
    seal_blockers=[]
    if incomplete:
        seal_blockers.append({
            "code":"BACKFILL_BASIS_INCOMPLETE",
            "message":"补录依据不完整，不能封存",
            "adjudication_ids":incomplete,
        })

    batch_id="RAB-"+hashlib.sha256(json.dumps(
        {"request_key":_clean(batch["request_key"]),
         "keys":sorted(collapsed.keys())},
        sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:16]
    metrics={
        "backfill_batch_id":batch_id,
        "request_key":_clean(batch["request_key"]),
        "submitted":len(batch["items"]),
        "unique_rulings":len(rulings),
        "retries_collapsed":retries,
        "basis_incomplete":len(incomplete),
        "backdate_claims":len(backdated),
        "grants_admitted":len(grants),
        "sealable":not seal_blockers,
    }
    return {
        "rows":rulings,
        "quarantine":[],
        "metrics":metrics,
        "grants":grants,
        "seal_blockers":seal_blockers,
        "policy":RULING_POLICY,
        "ruling_text":RULING_TEXT,
        "batch_id":batch_id,
    }


def evaluate(rows,context):
    """M8 阶段入口：rows沿用上一阶段输出，裁决结果整体来自上下文。
    无补录批次时给出空裁决，保证阶段形状一致。"""
    batch=(context.get("parameters",{}) or {}).get("backfill")
    if not batch:
        return {"rows":[],"quarantine":[],"metrics":{
            "backfill_batch_id":"","request_key":"","submitted":0,
            "unique_rulings":0,"retries_collapsed":0,"basis_incomplete":0,
            "backdate_claims":0,"grants_admitted":0,"sealable":True},
            "seal_blockers":[],"policy":RULING_POLICY,"ruling_text":RULING_TEXT,
            "batch_id":""}
    result=adjudicate(batch,context)
    # M8的rows是裁决行（非访问行）；主链路访问记录由pipeline保持不变
    return result
