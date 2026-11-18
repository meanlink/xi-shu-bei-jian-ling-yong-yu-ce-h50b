# 验收记录 · gb-1015 时点回放

记录实际执行过的检查；未执行的项目明确标注，不写成通过。

## 已执行（宿主环境，无 Docker / 无第三方依赖）

- 后端全部 Python 文件 `py_compile` 通过。
- 纯标准库单元测试（引擎与分析契约不依赖 FastAPI/SQLAlchemy）：
  `python3 -m unittest discover -s test-artifacts/gb-1015/tests -p 'test_*.py'`
  结果：23 passed，0 failed。
  - `test_replay.py`：凭证三要素校验、补记前视隔离、更正版本替换、零需求日填充、
    会议重放无法计算标注、逐日回测只取当时已入账版本、差异归因分类、
    清单固化/中断恢复/并发补录留待下次/幂等键。
  - `test_business.py`：原 Croston/SBA `analyze` 契约与种子数据形状回归。
- 种子剧本端到端口径演练（引擎层脚本，未落库）：六个会议点中首场因历史不足 45 日
  标注无法计算；5/12 补记进入 5/20 会议差异；7/15 更正 v2 进入 7/17 会议差异。
- 前端全部 JS `node --check` 语法通过。

## 未执行（环境不具备，需在容器环境补验）

宿主无 Docker、无 pip/venv，以下项目未在本机执行，不声明通过：

- `docker compose up -d --build --wait` 五服务健康检查。
- FastAPI 路由级集成：`/replay/vouchers`、`/replay/runs`、`/replay/compare`
  的鉴权（viewer 只读、analyst/admin 可写）、422/409 错误响应。
- worker 真实 Redis 队列（`replay:queue`）、SQL 兜底轮询、崩溃恢复与
  `ReplayPoint` 唯一幂等键的数据库行为。
- 种子 `scripts.seed_demo` 在 PostgreSQL 上的幂等执行与归档文件落盘。
- 浏览器页面截图与人工走查（导航新增"时点回放"、凭证登记表单、对照与归档展开）。

## 复现命令

```bash
# 无需依赖的快速验证（在仓库根目录）
python3 -m unittest discover -s test-artifacts/gb-1015/tests -p 'test_*.py'

# 容器环境完整验收（需 Docker）
docker compose up -d --build --wait
docker compose exec -T backend python -m scripts.seed_demo
```
