import time, json
from datetime import datetime, date, timedelta
from sqlalchemy import select, func
from src.config.database import Session
from migrations.initial import migrate
from src.models import User, Dataset, Job, Report, Delivery, Audit, Voucher, ReplayRun
from src.services.business import metadata, seed_records, analyze
from src.services.reports import deliver
from src.services.replay_runs import execute_run
from src.services import replay as replay_engine
from src.utils.security import hash_password
from src.utils.serialization import checksum

# 补记/更正剧本：发生日 -> (延后入账天数, 说明)
BACKFILL_SCENARIO = {
    ("R17", "2026-05-12"): 19,    # 5/12 领用 1 件，5/31 才补录入账（5/20 例会不可见）
    ("G22", "2026-05-26"): 20,    # 5/26 领用 2 件，6/15 补录入账（6/12 例会不可见）
}
# 更正剧本：发生日 -> 更正后增量（v2 在 v1 之后 10 天入账）
CORRECTION_SCENARIO = {("R17", "2026-07-15"): 2}  # v1=5 件，7/25 更正为 7 件
# 例会时点：周五 10:00（本地时间）；首场仅 38 天历史，演示无法计算标注
MEETINGS = ["2026-05-08T10:00", "2026-05-20T10:00", "2026-06-12T10:00",
            "2026-07-17T10:00", "2026-08-21T10:00", "2026-09-18T10:00"]


def _dt(text):
    return datetime.fromisoformat(text).timestamp()


def seed_vouchers(db, rows):
    """把 180 天日历登记为带入账时间与版本的凭证。"""
    created = []
    for row in rows:
        occur = date.fromisoformat(row["day"])
        part = row["part"]
        normal_post = datetime(occur.year, occur.month, occur.day, 20, 0).timestamp()
        key = (part, row["day"])
        if key in BACKFILL_SCENARIO:
            post = (datetime(occur.year, occur.month, occur.day, 9, 30)
                    + timedelta(days=BACKFILL_SCENARIO[key])).timestamp()
            v = Voucher(part=part, occur_date=row["day"], posted_at=post,
                        demand=float(row["demand"]), version=1, entry_type="backfill",
                        corrected_id=None, source="erp-backfill.csv",
                        created_by=1, created_at=time.time())
            db.add(v); db.flush(); created.append(v)
        elif key in CORRECTION_SCENARIO:
            v1 = Voucher(part=part, occur_date=row["day"], posted_at=normal_post,
                         demand=float(row["demand"]), version=1, entry_type="normal",
                         corrected_id=None, source="erp-night.csv",
                         created_by=1, created_at=time.time())
            db.add(v1); db.flush()
            fix_post = (datetime(occur.year, occur.month, occur.day, 15, 0)
                        + timedelta(days=10)).timestamp()
            v2 = Voucher(part=part, occur_date=row["day"], posted_at=fix_post,
                         demand=float(row["demand"]) + CORRECTION_SCENARIO[key],
                         version=2, entry_type="correction", corrected_id=v1.id,
                         source="erp-correction.csv", created_by=1,
                         created_at=time.time())
            db.add(v2); db.flush(); created.extend([v1, v2])
        else:
            v = Voucher(part=part, occur_date=row["day"], posted_at=normal_post,
                        demand=float(row["demand"]), version=1, entry_type="normal",
                        corrected_id=None, source="erp-night.csv",
                        created_by=1, created_at=time.time())
            db.add(v); db.flush(); created.append(v)
    return created


def seed_replay(db, params):
    meeting_epochs = [_dt(x) for x in MEETINGS]
    vouchers = [
        {"id": v.id, "part": v.part, "occur_date": v.occur_date,
         "posted_at": v.posted_at, "demand": v.demand, "version": v.version,
         "entry_type": v.entry_type, "corrected_id": v.corrected_id}
        for v in db.scalars(select(Voucher).where(Voucher.part == params["part"]))
    ]
    manifest = replay_engine.build_manifest(vouchers, meeting_epochs, params)
    run = ReplayRun(name="例会时点回放 · 补记与更正隔离", params=params, manifest=manifest,
                    status="queued", progress={}, result={}, created_by=1,
                    created_at=time.time())
    db.add(run); db.flush()
    execute_run(db, run.id)  # 同步执行，种子结束即有可查看的归档点
    db.add(Audit(actor="seed", action="demo.replay", detail={"run": run.id}, at=time.time()))
    return run


def seed():
    migrate()
    with Session() as db:
        for role,name in [('admin','林知 · 业务负责人'),('analyst','陈舟 · 分析员'),('viewer','周禾 · 观察员')]:
            email=role+'@demo.local'
            if not db.scalar(select(User).where(User.email==email)):
                db.add(User(email=email,name=name,role=role,password=hash_password('Demo123!')))
        db.flush()
        m=metadata()
        d=db.scalar(select(Dataset).where(Dataset.name==m['dataset_name']))
        if not d:
            rows=seed_records()
            d=Dataset(name=m['dataset_name'],source=m['short']+'-202611.csv',records=rows,checksum=checksum(rows),created_at=time.time())
            db.add(d); db.flush()
        if not db.scalar(select(Job).where(Job.name=='基线复核 · '+m['name'])):
            params={p['key']:p['default'] for p in m.get('parameters',[])}
            result=analyze(d.records,params)
            json.dumps(result,ensure_ascii=False,allow_nan=False)
            admin=db.scalar(select(User).where(User.role=='admin'))
            j=Job(name='基线复核 · '+m['name'],dataset_id=d.id,params=params,status='completed',result=result,created_by=admin.id,created_at=time.time()-3600,finished_at=time.time()-3595,approved=True)
            db.add(j);db.flush()
            r=Report(name='每日业务复核简报',job_id=j.id,destination='业务复核组 · 本地收件箱',interval_seconds=86400,next_run=time.time()+86400,enabled=True)
            db.add(r);db.flush();deliver(db,r)
            db.add(Audit(actor='seed',action='demo.baseline',detail={'dataset':d.id},at=time.time()))
        # 凭证台账与演示回放（幂等；归档目录不可用时不阻断主种子）
        if not db.scalar(select(Voucher).limit(1)):
            seed_vouchers(db, seed_records())
            db.add(Audit(actor='seed', action='demo.vouchers',
                         detail={'rows': db.scalar(select(func.count()).select_from(Voucher))},
                         at=time.time()))
        if not db.scalar(select(ReplayRun).limit(1)):
            params={p['key']:p['default'] for p in m.get('parameters',[])}
            try:
                seed_replay(db, params)
            except Exception:
                import logging; logging.exception('Demo replay seed skipped')
        db.commit()
        counts={model.__tablename__:db.scalar(select(func.count()).select_from(model)) for model in [User,Dataset,Job,Report,Delivery,Audit,Voucher,ReplayRun]}
        print(json.dumps(counts,ensure_ascii=False))
        return counts
if __name__=='__main__':seed()
