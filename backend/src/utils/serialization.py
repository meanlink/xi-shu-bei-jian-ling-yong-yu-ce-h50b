import hashlib, json
def checksum(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
def dataset_dict(d):
    return {'id':d.id, 'name':d.name, 'source':d.source, 'rows':len(d.records), 'checksum':d.checksum, 'created_at':d.created_at}
def job_dict(j):
    return {k:getattr(j,k) for k in ['id','name','dataset_id','params','status','result','error','approved','created_at','finished_at']}
def report_dict(r):
    return {k:getattr(r,k) for k in ['id','name','job_id','destination','interval_seconds','next_run','enabled']}
def delivery_dict(d):
    return {k:getattr(d,k) for k in ['id','report_id','created_at','checksum','filename','destination']}
def voucher_dict(v):
    return {k:getattr(v,k) for k in ['id','part','occur_date','posted_at','demand','version','entry_type','corrected_id','source','created_at']}
def replay_run_dict(r):
    return {k:getattr(r,k) for k in ['id','name','params','manifest','status','progress','result','error','archive_path','created_at','finished_at']}
def replay_point_dict(p):
    return {k:getattr(p,k) for k in ['id','run_id','meeting_epoch','idempotency_key','payload','archive_path','created_at']}
