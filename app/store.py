import sqlite3,json
from pathlib import Path
from app.config import DATA_DIR
from app.errors import DomainError
def connect():
    DATA_DIR.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(DATA_DIR/"records.db",timeout=20)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY,payload TEXT NOT NULL,version INTEGER NOT NULL DEFAULT 1,decision TEXT NOT NULL DEFAULT '待复核')")
    # 补录追溯请求台账：同一request_key的重试只登记一次，跨请求重试不重复计数。
    # 唯一约束保证并发/重复提交折叠；旧结论与来源依据随runs.payload永久保留。
    db.execute("CREATE TABLE IF NOT EXISTS backfill_attempts (request_key TEXT PRIMARY KEY,run_id TEXT NOT NULL,batch_id TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 1,first_seen TEXT NOT NULL DEFAULT (datetime('now')))")
    return db
def save(run_id,payload):
    with connect() as db:
        db.execute("INSERT OR IGNORE INTO runs(id,payload) VALUES (?,?)",(run_id,json.dumps(payload,ensure_ascii=False)))
        backfill=payload.get("backfill")
        if backfill:
            request_key=backfill["metrics"]["request_key"]
            # 同一补录重试：INSERT OR IGNORE折叠，attempts仅在首次插入时为1，
            # 重复提交不会产生新计数；run_id指向承载裁决的批次。
            db.execute("INSERT OR IGNORE INTO backfill_attempts(request_key,run_id,batch_id,attempts) VALUES (?,?,?,1)",
                       (request_key,run_id,backfill["batch_id"]))
            db.execute("UPDATE backfill_attempts SET attempts=attempts+1 WHERE request_key=? AND run_id<>?",
                       (request_key,run_id))
    return get(run_id)
def get(run_id):
    with connect() as db: row=db.execute("SELECT payload,version,decision FROM runs WHERE id=?",(run_id,)).fetchone()
    if not row: raise DomainError("NOT_FOUND","批次不存在",404)
    run={**json.loads(row[0]),"version":row[1],"decision":row[2]}
    backfill=run.get("backfill")
    if backfill:
        ledger=db_attempt(backfill["metrics"]["request_key"])
        if ledger: backfill["metrics"]["ledger_attempts"]=ledger["attempts"]
    return run
def db_attempt(request_key):
    with connect() as db:
        row=db.execute("SELECT request_key,run_id,batch_id,attempts FROM backfill_attempts WHERE request_key=?",(request_key,)).fetchone()
    return dict(zip(("request_key","run_id","batch_id","attempts"),row)) if row else None
def attempts():
    with connect() as db:
        return [dict(zip(("request_key","run_id","batch_id","attempts"),row))
                for row in db.execute("SELECT request_key,run_id,batch_id,attempts FROM backfill_attempts ORDER BY rowid")]
def latest():
    with connect() as db: row=db.execute("SELECT id FROM runs ORDER BY rowid DESC LIMIT 1").fetchone()
    return get(row[0]) if row else None
def history():
    with connect() as db: return [{"run_id":i,"version":v,"decision":d} for i,v,d in db.execute("SELECT id,version,decision FROM runs ORDER BY rowid DESC")]
def _sealable(run):
    seals=run["stages"][6]
    blockers=seals.get("seal_blockers",[])
    return seals["metrics"].get("sealable",True) and not blockers,blockers
def review(run_id,version,decision):
    if decision not in {"已复核","退回复核"}: raise DomainError("BAD_DECISION","复核状态无效")
    with connect() as db:
        current=db.execute("SELECT payload,version FROM runs WHERE id=?",(run_id,)).fetchone()
        if not current: raise DomainError("NOT_FOUND","批次不存在",404)
        if current[1]!=version: raise DomainError("STALE_VERSION","版本已更新，请刷新",409)
        run=json.loads(current[0])
        # 封存/复核签核必须服从补录追溯裁决：依据不完整时拒绝，
        # 条件更新不执行，旧版结论与来源依据原样保留（回切失败保留旧版）。
        if decision=="已复核":
            ok,blockers=_sealable(run)
            if not ok:
                raise DomainError("SEAL_BLOCKED","补录依据不完整，不能封存："+"；".join(b["code"] for b in blockers),409)
        cursor=db.execute("UPDATE runs SET version=version+1,decision=? WHERE id=? AND version=?",(decision,run_id,version))
        if cursor.rowcount!=1: raise DomainError("STALE_VERSION","版本已更新，请刷新",409)
    return get(run_id)
