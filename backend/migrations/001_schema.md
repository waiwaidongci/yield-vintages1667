# 初始结构

## 001 初始结构
SQLAlchemy启动创建以下业务表：yield_periods, yield_lots, denominator_policies, yield_runs, delivery_schedules, deliveries、users、audits。Source专属JSON字段由对应validation严格校验；Policy为不可覆盖的版本记录；Run保存source/policy冻结快照；Period仅用于已有期间选择器，不计入核心复杂度。已有数据库升级需先备份，后续字段演进应以独立迁移执行。

## 002 试制排产模块
新增五张表（均由 SQLAlchemy `create_all` 自动建立，已有库重启即生效，不影响旧表数据）：
- `sched_line_shifts`：可排线班主数据，含班余量 `capacity` 与乐观锁版本 `version`；每次产能调整/方案扣减都会令 version 自增。
- `sched_batches`：不可拆试制批次主数据（数量、可加工线班 id 列表），自然键幂等。
- `sched_orders`：排产单。`idempotency_key` 唯一保证重试不重复占用；`snapshot` 为接单时封存的底稿良率矩阵、口径与各班余量，附 SHA256 `checksum`；`status` 取值 queued/running/completed/rejected；`leased_by/leased_at` 为 worker 租约，超时（60 秒）可被回收重排。
- `sched_assignments`：生效方案的逐批安排，仅在 OPTIMAL 提交事务中写入；未证最优/退回单不留任何记录。
- `sched_lease_logs`：接单、租约、冲突退回、重排、提交、人工重试等全过程留痕。

并发控制：提交方案时对相关线班行 `SELECT ... FOR UPDATE`，版本不符或余量不足即整单退回并按最新余量重排（上限 5 次），方案写入与余量扣减在同一事务。快照 JSON 落库后键会被序列化为字符串，求解器入口统一归一化为整数线班 id；JSON 原地修改使用 `flag_modified` 显式标记。
