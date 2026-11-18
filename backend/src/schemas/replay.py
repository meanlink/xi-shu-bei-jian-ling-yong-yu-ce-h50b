from pydantic import BaseModel, Field


class VoucherInput(BaseModel):
    part: str = Field(min_length=1, max_length=60)
    occur_date: str = Field(min_length=8, max_length=10)          # YYYY-MM-DD 领用发生日
    posted_at: float | str                                         # epoch 秒或 ISO 时间，入账时间
    demand: float = Field(ge=0, le=1_000_000_000)
    version: int = Field(default=1, ge=1)
    entry_type: str = Field(default='normal')
    corrected_id: int | None = None
    source: str = Field(default='manual', max_length=120)


class VoucherBatchInput(BaseModel):
    vouchers: list[VoucherInput] = Field(min_length=1, max_length=2000)


class ReplayInput(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    params: dict = Field(default_factory=dict)
    meeting_epochs: list[float | str] = Field(min_length=1, max_length=60)
