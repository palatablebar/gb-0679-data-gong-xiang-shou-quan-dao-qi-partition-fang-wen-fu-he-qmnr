import csv,io
import pytest
from app.errors import DomainError
from app.pipeline import calculate,execute,load_sample
from app.modules.m8_backfill.rules import adjudicate,validate_shape,RULING_POLICY
from app import store

def item(**over):
    base={"id":"BF1","principal":"contractor","resource":"orders",
          "partition_from":"2026-09","partition_to":"2026-11","effect":"allow",
          "document_date":"2026-11-10T00:00:00Z","arrived_at":"2026-11-18T00:00:00Z",
          "effective_end":"2026-12-01T00:00:00Z","basis":["工单OPS-77","审批邮件"],
          "source_refs":["line:1"]}
    base.update(over);return base

def batch(*items,key="bf-key-1"):
    return {"request_key":key,"items":list(items)}

# ---------- 裁决口径：到达生效，不按单据日期回溯 ----------
def test_ruling_is_arrival_effective_not_document_backdate():
    "TRAP-28 审计只采信访问时已到达的规则"
    r=adjudicate(batch(item()))
    ruling=r["rows"][0]
    assert ruling["ruling"]=="arrival_effective"
    assert ruling["policy"]==RULING_POLICY
    assert ruling["document_claims_backdate"] is True  # 单据11-10早于到达11-18
    grant=r["grants"][0]
    assert grant["start"]=="2026-11-18T00:00:00+00:00"  # 生效起点=到达，不是单据日期
    assert grant["source"]=="backfill"

def test_backfill_grant_does_not_admit_historical_access():
    from app.modules.m3_grants.rules import evaluate as m3
    r=adjudicate(batch(item()))
    ctx={"parameters":{},"backfill_admitted":r["grants"]}
    def row(at): return {"access_time":at,"reported":"1","principal":"contractor",
                         "resource":"orders","partition":"2026-10"}
    before=m3([row("2026-11-16T00:00:00Z")],ctx)["rows"][0]
    after=m3([row("2026-11-19T00:00:00Z")],ctx)["rows"][0]
    assert before["candidate_count"]==0            # 访问在到达前 -> 不回溯放行
    assert after["candidate_count"]==1
    assert after["candidates"][0]["id"]=="B-BF1"

def test_grant_half_open_window_end_excluded():
    from app.modules.m3_grants.rules import evaluate as m3
    r=adjudicate(batch(item()))
    ctx={"parameters":{},"backfill_admitted":r["grants"]}
    row={"access_time":"2026-12-01T00:00:00Z","reported":"1","principal":"contractor",
         "resource":"orders","partition":"2026-10"}
    assert m3([row],ctx)["rows"][0]["candidate_count"]==0

# ---------- 唯一追溯裁决 ----------
def test_unique_adjudication_id_is_deterministic():
    a=adjudicate(batch(item()));b=adjudicate(batch(item()))
    assert a["rows"][0]["adjudication_id"]==b["rows"][0]["adjudication_id"]
    assert a["batch_id"]==b["batch_id"]

def test_one_ruling_per_natural_key():
    r=adjudicate(batch(item(),item(),item()))
    assert r["metrics"]["submitted"]==3
    assert r["metrics"]["unique_rulings"]==1
    assert len(r["rows"])==1

def test_distinct_items_get_distinct_rulings():
    r=adjudicate(batch(item(),item(id="BF2",partition_from="2026-01")))
    assert r["metrics"]["unique_rulings"]==2
    ids={x["adjudication_id"] for x in r["rows"]}
    assert len(ids)==2

# ---------- 同一补录重试不能重复计数 ----------
def test_identical_retry_collapsed_not_double_counted():
    r=adjudicate(batch(item(),item()))
    assert r["metrics"]["retries_collapsed"]==1
    assert r["metrics"]["grants_admitted"]==1

def test_cross_request_retry_ledger_single_attempt(sample):
    p={"backfill":batch(item())}
    first=execute(sample,p);second=execute(sample,p)
    assert first["run_id"]==second["run_id"]            # 同输入复用同批次
    led=store.attempts()
    assert len(led)==1 and led[0]["request_key"]=="bf-key-1"
    got=store.get(first["run_id"])
    assert got["backfill"]["metrics"]["ledger_attempts"]==1

def test_same_key_different_content_increments_attempt(sample):
    execute(sample,{"backfill":batch(item())})
    execute(sample,{"backfill":batch(item(basis=["工单OPS-77","审批邮件","合同附件"]))})
    led=store.attempts()
    assert len(led)==1 and led[0]["attempts"]==2

# ---------- 依据不完整不能封存 ----------
@pytest.mark.parametrize("bad_basis",[[""],[" "],["待定"],["未知"],["NULL"],[]])
def test_incomplete_basis_blocks_seal(sample,bad_basis):
    r=calculate(sample,{"backfill":batch(item(basis=bad_basis))})
    m8=r["stages"][7];m7=r["stages"][6]
    assert m8["metrics"]["basis_incomplete"]==1
    assert m8["metrics"]["grants_admitted"]==0          # 依据不足不入册授权
    assert m7["metrics"]["sealable"] is False
    assert m7["seal_blockers"][0]["code"]=="BACKFILL_BASIS_INCOMPLETE"

def test_incomplete_basis_not_a_grant(sample):
    r=calculate(sample,{"backfill":batch(item(basis=["待定"]))})
    assert r["backfill"]["grants"]==[]
    # M3 授权版本数与无补录基线一致
    base=calculate(sample)
    assert r["stages"][2]["metrics"]["grant_versions"]==base["stages"][2]["metrics"]["grant_versions"]

def test_seal_review_rejected_keeps_previous_version(client,sample):
    p=client.post("/api/runs",json={"sample":"dirty.csv",
                  "parameters":{"backfill":batch(item(basis=["待定"]))}}).get_json()
    u="/api/runs/"+p["run_id"]+"/review"
    resp=client.post(u,json={"version":1,"decision":"已复核"})
    assert resp.status_code==409
    assert resp.get_json()["code"]=="SEAL_BLOCKED"
    kept=store.get(p["run_id"])
    assert kept["version"]==1 and kept["decision"]=="待复核"   # 旧版结论保留

def test_complete_basis_allows_seal_review(client):
    p=client.post("/api/runs",json={"sample":"dirty.csv",
                  "parameters":{"backfill":batch(item())}}).get_json()
    u="/api/runs/"+p["run_id"]+"/review"
    ok=client.post(u,json={"version":1,"decision":"已复核"})
    assert ok.status_code==200 and ok.get_json()["version"]==2

def test_stale_version_still_409_after_backfill(client):
    p=client.post("/api/runs",json={"sample":"dirty.csv",
                  "parameters":{"backfill":batch(item())}}).get_json()
    u="/api/runs/"+p["run_id"]+"/review"
    client.post(u,json={"version":1,"decision":"已复核"})
    assert client.post(u,json={"version":1,"decision":"退回复核"}).status_code==409

# ---------- 形状非法整批回切，不落库 ----------
def test_missing_request_key_rejected():
    with pytest.raises(DomainError) as e:
        validate_shape({"items":[item()]})
    assert e.value.code=="BAD_BACKFILL_KEY"

def test_bad_shape_does_not_persist(client):
    resp=client.post("/api/runs",json={"sample":"dirty.csv",
                     "parameters":{"backfill":{"request_key":"x","items":[{"id":"z"}]}}})
    assert resp.status_code==422
    assert store.history()==[]

def test_naive_time_rejected():
    bad=item(arrived_at="2026-11-18T00:00:00")
    with pytest.raises(DomainError) as e:
        validate_shape(batch(bad))
    assert e.value.code=="INVALID_TIME"

def test_same_id_conflicting_content_rejected():
    with pytest.raises(DomainError) as e:
        validate_shape(batch(item(),item(partition_from="2026-01")))
    assert e.value.code=="BAD_BACKFILL_DUP_ID"

def test_effective_end_must_be_after_arrival():
    with pytest.raises(DomainError) as e:
        adjudicate(batch(item(effective_end="2026-11-18T00:00:00Z")))
    assert e.value.code=="BAD_BACKFILL_WINDOW"

# ---------- 全链路服从唯一裁决 ----------
def test_all_stages_carry_backfill_context(sample):
    r=calculate(sample,{"backfill":batch(item())})
    assert len(r["stages"])==8
    m4=r["stages"][3]
    assert "backfill_hits" in m4["metrics"]
    m7=r["stages"][6]
    assert m7["metrics"]["backfill_rulings"]==1
    assert m7["metrics"]["backfill_batch_id"]==r["backfill"]["batch_id"]

def test_archive_export_contains_ruling_and_obey_seal_flag(client):
    p=client.post("/api/runs",json={"sample":"dirty.csv",
                  "parameters":{"backfill":batch(item())}}).get_json()
    data=client.get("/api/runs/"+p["run_id"]+"/archive").data.decode("utf-8-sig")
    rows=list(csv.reader(io.StringIO(data)))
    kinds=[x[3] for x in rows[1:]]
    assert "backfill_ruling" in kinds
    seal=[x for x in rows[1:] if x[3]=="seal_manifest"][0]
    assert seal[5]=="READY"
    # 阻断批次导出仍可用，但显式 BLOCKED
    q=client.post("/api/runs",json={"sample":"dirty.csv",
                  "parameters":{"backfill":batch(item(basis=["待定"]),key="bf-block-1")}}).get_json()
    data2=client.get("/api/runs/"+q["run_id"]+"/archive").data.decode("utf-8-sig")
    rows2=list(csv.reader(io.StringIO(data2)))
    assert any(x[3]=="seal_blocker" for x in rows2[1:])
    assert [x for x in rows2[1:] if x[3]=="seal_manifest"][0][5]=="BLOCKED"

def test_no_backfill_pipeline_unchanged(sample):
    r=calculate(sample)
    assert r["stages"][7]["metrics"]["unique_rulings"]==0
    assert r["stages"][7]["metrics"]["sealable"] is True
    assert r["backfill"] is None

def test_backfill_module_pages_and_api(client):
    assert client.get("/api/backfill").status_code==200
    assert client.get("/backfill").status_code==200
    resp=client.post("/api/backfill",json={
        "sample":"dirty.csv","parameters":{"backfill":batch(item())}})
    assert resp.status_code==200
    assert resp.get_json()["metrics"]["unique_rulings"]==1
