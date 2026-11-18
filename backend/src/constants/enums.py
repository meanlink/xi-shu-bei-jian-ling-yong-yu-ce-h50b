from enum import Enum
class Role(str, Enum):
    admin = 'admin'
    analyst = 'analyst'
    viewer = 'viewer'
class JobStatus(str, Enum):
    queued = 'queued'
    running = 'running'
    completed = 'completed'
    failed = 'failed'


class ReplayStatus(str, Enum):
    queued = 'queued'
    running = 'running'
    completed = 'completed'
    failed = 'failed'


class EntryType(str, Enum):
    normal = 'normal'       # 常规领用
    backfill = 'backfill'   # 领用补记（晚入账）
    correction = 'correction'  # 更正版本
