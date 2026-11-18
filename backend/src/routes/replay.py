from fastapi import APIRouter, Depends
from sqlalchemy import select

from src.config.database import get_db
from src.middlewares.auth import current_user, can_write
from src.models import Voucher, ReplayRun, ReplayPoint
from src.schemas.replay import VoucherBatchInput, ReplayInput
from src.services import replay_runs
from src.services.audit import record
from src.utils.serialization import voucher_dict, replay_run_dict, replay_point_dict

router = APIRouter(prefix="/replay")


@router.get("/vouchers")
def vouchers(part: str | None = None, db=Depends(get_db), u=Depends(current_user)):
    return [voucher_dict(v) for v in replay_runs.list_vouchers(db, part)]


@router.post("/vouchers", status_code=201)
def register(data: VoucherBatchInput, db=Depends(get_db), u=Depends(can_write)):
    created = replay_runs.register_vouchers(db, data, u)
    record(db, u.email, "voucher.register", {"count": len(created),
                                             "ids": [v.id for v in created]})
    db.commit()
    return [voucher_dict(v) for v in created]


@router.get("/runs")
def runs(db=Depends(get_db), u=Depends(current_user)):
    rows = db.scalars(select(ReplayRun).order_by(ReplayRun.id.desc()).limit(100))
    return [replay_run_dict(r) for r in rows]


@router.get("/runs/{id}")
def run_detail(id: int, db=Depends(get_db), u=Depends(current_user)):
    run = db.get(ReplayRun, id)
    if not run:
        from fastapi import HTTPException
        raise HTTPException(404, "回放运行不存在")
    points = db.scalars(select(ReplayPoint).where(ReplayPoint.run_id == id)
                        .order_by(ReplayPoint.meeting_epoch))
    return {**replay_run_dict(run), "points": [replay_point_dict(p) for p in points]}


@router.post("/runs", status_code=201)
def create_run(data: ReplayInput, db=Depends(get_db), u=Depends(can_write)):
    run = replay_runs.create_run(db, data, u)
    record(db, u.email, "replay.create", {"id": run.id,
                                          "meetings": len(run.manifest["meeting_epochs"])})
    db.commit()
    replay_runs.enqueue_replay(run.id)
    return replay_run_dict(run)


@router.post("/compare")
def compare(body: dict, db=Depends(get_db), u=Depends(current_user)):
    """工作台即时对照：会议时点预测 vs 现有凭证回算，并给出差异凭证。"""
    return replay_runs.preview_compare(db, body.get("meeting_epoch"),
                                       body.get("params") or {})
