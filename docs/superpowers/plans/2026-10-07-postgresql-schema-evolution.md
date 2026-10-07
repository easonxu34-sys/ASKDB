# PostgreSQL 数据库演进实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**目标：** 建立有序、校验和可验证的 PostgreSQL SQL 迁移器，使当前 `001_initial` 数据库安全升级、空数据库完整初始化、重复启动跳过已应用版本，并支持 `pgvector`。

**架构：** SQL 文件放在 `askdb-agent/src/integrations/migrations/`，按数字版本排序；`app_schema_migrations` 保存每个迁移的 ID 和 SHA-256。启动时取得现有事务 advisory lock，检查历史记录和 schema 状态，只在同一事务中执行未应用迁移。`002_enable_pgvector.sql` 在运行角色有权限时启用扩展；普通 Agent 角色要求数据库管理员先在每个新库启用一次。应用当前不直接读写向量，因此不增加 Python `pgvector` 适配包。

**技术栈：** Python 3.11+、psycopg 3、PostgreSQL、pytest。

**需求来源：** 用户于 2026-10-07 提出的数据库演进要求：增量迁移、确认目标库支持 pgvector、旧库可升级、新库可初始化、重复启动不重复执行。

## 全局约束

- 保持 `001_initial.sql` 字节不变，使已记录其校验和的数据库可以升级。
- 对校验和变化、磁盘缺失的已记录迁移、迁移文件名错误、版本断档和无迁移记录的非空 schema 失败关闭。
- 每个迁移 SQL 和对应 ledger 记录在同一 PostgreSQL 事务中提交，并持有 advisory lock。
- pgvector 服务端文件必须匹配 PostgreSQL 主版本；普通 Agent 角色不是超级用户时，由管理员在每个新数据库执行 `CREATE EXTENSION vector WITH SCHEMA public`。
- 不增加 SQLite 支持，不修改无关工作区内容，不提交更改。

---

### 任务 1：先证明升级、初始化和幂等行为

**文件：**
- 修改：`askdb-agent/tests/test_postgres_database.py`
- 使用：`askdb-agent/src/integrations/migrations/001_initial.sql`

**接口：**
- 使用 `PostgresDatabase.connect()`、`PostgresConnection.execute_script()` 和 `apply_postgres_migrations(connection)`。
- PostgreSQL 集成测试通过 `postgres_dsn` fixture 在配置的测试数据库中创建并清理唯一 schema。

- [x] **步骤 1：添加旧库升级测试。** 直接执行 `001_initial.sql`，写入该文件的精确 SHA-256 到 `app_schema_migrations`，插入一个测试用户；新建 `PostgresDatabase` 后断言测试用户仍存在，并记录最新迁移。
- [x] **步骤 2：添加空 schema 和重复启动测试。** 用两个独立 `PostgresDatabase` 实例启动同一隔离 schema，断言基础表和 pgvector 类型存在，且每个迁移 ID 只出现一条记录。
- [x] **步骤 3：添加历史异常测试。** 修改已记录 checksum 或插入未知迁移 ID，断言启动失败关闭。
- [x] **步骤 4：运行测试确认先红后绿。** 通过 `uv run pytest -q tests/test_postgres_database.py -k migration` 运行；首次失败应指向缺少 `002`、版本发现器或未知迁移检查。

### 任务 2：实现版本化 SQL 迁移发现和执行

**文件：**
- 修改：`askdb-agent/src/integrations/postgres_migrations.py`
- 新建：`askdb-agent/src/integrations/migrations/002_enable_pgvector.sql`
- 测试：`askdb-agent/tests/test_postgres_database.py`

**接口：**
- 保留 `apply_postgres_migrations(connection: PostgresConnection) -> None`。
- 文件名格式为 `NNN_description.sql`；迁移 ID 使用文件名主体；SHA-256 针对文件原始字节计算。

- [x] **步骤 1：发现并排序 SQL 文件。** 拒绝非法文件名、重复版本、缺少 `001` 或版本不连续。
- [x] **步骤 2：在事务 advisory lock 内读取 ledger。** 拒绝数据库记录了仓库中不存在的迁移、checksum 漂移和迁移顺序断裂；无历史记录但存在业务表时拒绝接管。
- [x] **步骤 3：按版本执行所有未应用 SQL。** 每个 SQL 后在同一事务插入对应 `migration_id`、checksum 和时间；保留调用方提交/回滚边界。
- [x] **步骤 4：加入 pgvector 迁移。** 若扩展未安装且当前角色为超级用户，执行 `CREATE EXTENSION vector WITH SCHEMA public`；若服务端文件缺失或普通 Agent 角色尚未由管理员预启用，返回明确错误。
- [x] **步骤 5：运行迁移测试。** 覆盖旧版升级、空 schema、重复执行、checksum 漂移、未知 ID、版本断档和非法文件名。

### 任务 3：核验目标服务器并说明部署顺序

**文件：**
- 修改：`askdb-agent/README.md`
- 核验：`askdb-agent/.env` 配置的开发 PostgreSQL

**接口：**
- 只读取服务器版本、`pg_available_extensions`、已安装扩展版本/namespace、迁移 ID 和向量操作探针；不读取业务表行。

- [x] **步骤 1：检查目标库。** 查询 PostgreSQL 版本、`vector` 是否可用/已安装，并执行向量类型及距离运算探针。
- [x] **步骤 2：验证旧库升级。** 使用现有 `001_initial` 数据库应用 `002_enable_pgvector`，确认原有结构和迁移历史保留。
- [x] **步骤 3：验证真实新库。** 创建临时空数据库，由管理员预启用 pgvector；以 Agent 角色执行迁移两次，确认 37 张基础表、两个迁移记录各一条且向量距离探针成功；随后删除临时库。
- [x] **步骤 4：更新中文文档。** 说明迁移编号、checksum/ledger、升级和启动幂等行为，以及新数据库先由管理员启用 pgvector 的顺序。
- [x] **步骤 5：运行 PostgreSQL 定向测试、Python 编译和 `git diff --check`。** 分开报告每项结果；未运行的验证不标为通过。

## 验收结果

- 当前记录在 `001_initial` 的数据库升级到 `002_enable_pgvector`，旧测试数据保留。
- 真实空数据库应用全部迁移，生成完整基础 schema 和 pgvector 类型。
- 分开的启动实例和重复迁移调用均只保留每个版本一条记录；已应用迁移仍执行 checksum 校验。
- 目标 PostgreSQL 18.6 可用 pgvector 0.8.7，且向量距离运算成功。
