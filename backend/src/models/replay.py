from sqlalchemy import String, JSON, Double, Integer, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from src.config.database import Base


class Voucher(Base):
    """领用凭证：登记领用发生日、入账时间与更正版本。

    version=1 为首发；version>1 为更正，corrected_id 指向被更正的凭证。
    entry_type: normal 常规 / backfill 领用补记 / correction 更正。
    """
    __tablename__ = 'vouchers'
    id: Mapped[int] = mapped_column(primary_key=True)
    part: Mapped[str] = mapped_column(String(60), index=True)
    occur_date: Mapped[str] = mapped_column(String(10), index=True)   # YYYY-MM-DD 领用发生日
    posted_at: Mapped[float] = mapped_column(Double, index=True)       # epoch 秒，入账时间
    demand: Mapped[float] = mapped_column(Double)
    version: Mapped[int] = mapped_column(Integer, default=1)
    entry_type: Mapped[str] = mapped_column(String(20), default='normal')
    corrected_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source: Mapped[str] = mapped_column(String(120), default='manual')
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[float] = mapped_column(Double)


class ReplayRun(Base):
    """一次时点回放运行。manifest 固化输入清单，中断后按清单恢复。"""
    __tablename__ = 'replay_runs'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    manifest: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default='queued')  # queued/running/completed/failed
    progress: Mapped[dict] = mapped_column(JSON, default=dict)         # {meeting_epoch: point}
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(String(1000), default='')
    archive_path: Mapped[str] = mapped_column(String(260), default='')
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[float] = mapped_column(Double)
    finished_at: Mapped[float | None] = mapped_column(Double, nullable=True)


class ReplayPoint(Base):
    """单个会议时点的归档记录；幂等键 (run_id, meeting_epoch) 防止重复归档。"""
    __tablename__ = 'replay_points'
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey('replay_runs.id'), index=True)
    meeting_epoch: Mapped[float] = mapped_column(Double, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(120), unique=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    archive_path: Mapped[str] = mapped_column(String(260), default='')
    created_at: Mapped[float] = mapped_column(Double)
