"""凭证登记与时点回放的持久化服务。"""
import json
import os
import time

from fastapi import HTTPException
from sqlalchemy import select

from src.config.settings import ARTIFACT_DIR
from src.models import Voucher, ReplayRun, ReplayPoint
from src.services import replay as engine
from src.services.business import metadata
from src.services.engine import validate_params


def register_vouchers(db, batch, user):
    """登记一批凭证。更正凭证必须指向已存在的同备件凭证。"""
    created = []
    for item in batch.vouchers:
        raw = item.model_dump()
        try:
            clean = engine.validate_voucher(raw)
        except engine.ReplayError as exc:
            raise HTTPException(422, str(exc)) from None
        if clean["entry_type"] == "correction" or clean["version"] > 1:
            target = db.get(Voucher, clean["corrected_id"]) if clean["corrected_id"] else None
            if not target:
                raise HTTPException(422, f"更正凭证必须携带已存在凭证的 corrected_id（发生日 {clean['occur_date']}）")
            if target.part != clean["part"] or target.occur_date != clean["occur_date"]:
                raise HTTPException(422, "更正凭证的备件与领用发生日必须与原凭证一致")
            clean["version"] = max(clean["version"], target.version + 1)
            clean["entry_type"] = "correction"
        v = Voucher(
            part=clean["part"], occur_date=clean["occur_date"],
            posted_at=clean["posted_at"], demand=clean["demand"],
            version=clean["version"], entry_type=clean["entry_type"],
            corrected_id=clean["corrected_id"], source=clean["source"],
            created_by=user.id, created_at=time.time(),
        )
        db.add(v)
        db.flush()
        created.append(v)
    return created


def list_vouchers(db, part=None):
    stmt = select(Voucher).order_by(Voucher.occur_date, Voucher.version, Voucher.id)
    if part:
        stmt = stmt.where(Voucher.part == part)
    return list(db.scalars(stmt))


def _voucher_payload(v):
    return {
        "id": v.id, "part": v.part, "occur_date": v.occur_date,
        "posted_at": v.posted_at, "demand": v.demand, "version": v.version,
        "entry_type": v.entry_type, "corrected_id": v.corrected_id,
    }


def create_run(db, data, user):
    allowed = {p["key"]: p for p in metadata().get("parameters", [])}
    if set(data.params) - set(allowed):
        raise HTTPException(422, "包含未知分析参数")
    params = {k: p["default"] for k, p in allowed.items()}
    params.update(data.params)
    try:
        validate_params(params)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    meeting_epochs = []
    for value in data.meeting_epochs:
        try:
            meeting_epochs.append(engine.parse_epoch(value))
        except engine.ReplayError as exc:
            raise HTTPException(422, str(exc)) from None
    vouchers = list_vouchers(db, params.get("part"))
    if not vouchers:
        raise HTTPException(409, "所选备件尚无凭证，无法发起时点回放")
    payloads = [_voucher_payload(v) for v in vouchers]
    manifest = engine.build_manifest(payloads, meeting_epochs, params)
    run = ReplayRun(
        name=data.name, params=params, manifest=manifest,
        status="queued", progress={}, result={},
        created_by=user.id, created_at=time.time(),
    )
    db.add(run)
    db.flush()
    return run


def enqueue_replay(run_id):
    # 复用分析队列；worker 按 id 类型分流，Redis 不可用时 SQL queued 兜底。
    try:
        from redis import Redis
        from src.config.settings import REDIS_URL
        r = Redis.from_url(REDIS_URL)
        r.rpush("replay:queue", run_id)
        r.close()
    except Exception:
        pass


def _archive_point(run, point):
    """幂等写归档文件；重试时同会议点不重复落盘。"""
    meeting_epoch = point["meeting_epoch"]
    os.makedirs(ARTIFACT_DIR, exist_ok=True)
    filename = f"replay-{run.id}-meeting-{int(meeting_epoch)}.json"
    path = os.path.join(ARTIFACT_DIR, filename)
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"run": run.name, "params": run.params, "point": point},
                      f, ensure_ascii=False, allow_nan=False)
    return filename


def execute_run(db, run_id):
    """按固化清单执行回放；中断恢复沿用原清单，已完成点跳过、不重复归档。"""
    run = db.get(ReplayRun, run_id)
    if not run or run.status not in ("queued", "running"):
        return
    run.status = "running"
    db.commit()
    try:
        vouchers = {v.id: _voucher_payload(v) for v in db.scalars(select(Voucher))}
        output = engine.run_manifest(vouchers, run.manifest, progress=run.progress)
        # 逐会议点幂等归档：唯一键冲突说明已归档，直接沿用。
        for point in output["points"]:
            key = f"run-{run.id}-meeting-{int(point['meeting_epoch'])}"
            existing = db.scalar(select(ReplayPoint).where(ReplayPoint.idempotency_key == key))
            if existing:
                continue
            filename = _archive_point(run, point)
            db.add(ReplayPoint(
                run_id=run.id, meeting_epoch=point["meeting_epoch"],
                idempotency_key=key, payload=point, archive_path=filename,
                created_at=time.time(),
            ))
            db.flush()
        run.result = output
        run.status = "completed"
        run.finished_at = time.time()
    except Exception as exc:
        run.status = "failed"
        run.error = str(exc)[:990]
        run.finished_at = time.time()
        raise
    finally:
        db.commit()


def preview_compare(db, meeting_epoch_raw, params):
    """工作台即时对照（不落库）：会议当时预测 vs 现有凭证回算。"""
    try:
        meeting_epoch = engine.parse_epoch(meeting_epoch_raw)
        validate_params(params)
    except (engine.ReplayError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from None
    part = str(params.get("part", "R17"))
    vouchers = [_voucher_payload(v) for v in list_vouchers(db, part)]
    if not vouchers:
        raise HTTPException(404, "该备件暂无凭证，无法对照")
    return engine.compare_meeting_vs_current(vouchers, meeting_epoch, params)
