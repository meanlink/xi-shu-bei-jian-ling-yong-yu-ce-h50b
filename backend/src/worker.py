import time, json, logging
from pathlib import Path
from sqlalchemy import select
from redis import Redis
from src.config.database import Session
from src.config.settings import REDIS_URL
from src.models import Job, Dataset, Report, ReplayRun
from src.services.business import analyze
from src.services.reports import deliver
from src.services.replay_runs import execute_run
from src.services.audit import record
logging.basicConfig(level=logging.INFO)
def execute(job_id):
    with Session() as db:
        j=db.get(Job,job_id)
        if not j or j.status!='queued':return
        j.status='running'; db.commit()
        try:
            d=db.get(Dataset,j.dataset_id)
            result=analyze(d.records,j.params)
            json.dumps(result,allow_nan=False)
            j.result=result;j.status='completed';j.finished_at=time.time()
            record(db,'worker','job.completed',{'id':j.id})
        except Exception as e:
            logging.exception('Analysis failed')
            j.status='failed';j.error=str(e)[:990];j.finished_at=time.time()
            record(db,'worker','job.failed',{'id':j.id,'error':j.error})
        db.commit()
def execute_replay(run_id):
    with Session() as db:
        r=db.get(ReplayRun,run_id)
        if not r or r.status not in ('queued','running'):return
        try:
            execute_run(db,run_id)
            record(db,'worker','replay.completed',{'id':run_id})
        except Exception:
            logging.exception('Replay failed')
            record(db,'worker','replay.failed',{'id':run_id})
def tick():
    with Session() as db:
        queued=list(db.scalars(select(Job.id).where(Job.status=='queued').limit(3)))
    for job_id in queued:execute(job_id)
    # 回放队列的 SQL 兜底：Redis 丢失入队消息时仍能被捞起。
    with Session() as db:
        replay_queued=list(db.scalars(select(ReplayRun.id).where(ReplayRun.status=='queued').limit(2)))
    for run_id in replay_queued:execute_replay(run_id)
    with Session() as db:
        for r in db.scalars(select(Report).where(Report.enabled==True,Report.next_run<=time.time())):
            d=deliver(db,r)
            if d:record(db,'worker','report.delivered',{'id':r.id,'delivery':d.id})
        db.commit()
def main():
    r=Redis.from_url(REDIS_URL,socket_timeout=3)
    # This Compose stack has a single worker. A crash must not strand its jobs.
    with Session() as db:
        for j in db.scalars(select(Job).where(Job.status=='running')):
            j.status='queued';record(db,'worker','job.recovered',{'id':j.id})
        # 回放中断后沿用原清单恢复：running 重新入队，progress 保证不重复归档。
        for rr in db.scalars(select(ReplayRun).where(ReplayRun.status=='running')):
            rr.status='queued';record(db,'worker','replay.recovered',{'id':rr.id})
        db.commit()
    while True:
        Path('/tmp/heartbeat').touch()
        try:
            item=r.blpop(['analysis:queue','replay:queue'],timeout=1)
            if item:
                queue, payload = item[0], int(item[1])
                if queue == b'replay:queue':execute_replay(payload)
                else:execute(payload)
            tick()
        except Exception:
            logging.exception('Worker retry');time.sleep(2)
if __name__=='__main__':main()
