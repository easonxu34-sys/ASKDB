# PostgreSQL-only 清理计划

## 目标

移除 ASKDB-Agent 运行时代码、命令、部署配置和当前维护文档中的 SQLite 支持。保留 PostgreSQL 的事务锁定语义与已迁移业务数据；历史设计记录不改写。

## 步骤

1. 盘点活跃调用链、测试、部署脚本、依赖、文档和本地 SQLite 数据文件，确认 PostgreSQL 已包含最新数据。
2. 移除一次性 SQLite 导入命令、导入模块及仅服务于导入审计的数据库结构。
3. 将存储 SQL 改为 psycopg 原生占位符，并将 SQLite 的 `BEGIN IMMEDIATE` 适配改为显式 PostgreSQL 写锁 API。
4. 删除 SQLite 专用旧部署迁移脚本，修正 Compose、Agent 配置和当前 README/开发文档。
5. 删除已核验的本地 SQLite 主文件及其 WAL/SHM/锁文件；保留其他 Agent 数据。
6. 运行静态语法与 diff 检查，并在排除归档文档后复查活跃代码和配置中无 SQLite 存储残留。

## 验收

- Agent 运行时代码不导入 `sqlite3`，无 SQLite 路径、导入 CLI、qmark 翻译或 `BEGIN IMMEDIATE` 兼容行为。
- PostgreSQL 的跨连接写事务串行化继续有效。
- Compose 与本地启动配置只指向 PostgreSQL。
- 经数据核对后清除本机旧 SQLite 数据文件，不影响 Wren 文件、记忆 journal、密钥及其他未提交改动。
- Python 源码可编译，`git diff --check` 通过；不将未运行的测试描述为通过。
