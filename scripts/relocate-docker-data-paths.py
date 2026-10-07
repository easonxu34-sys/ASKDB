#!/usr/bin/env python3
"""Rewrite only persisted Docker Wren project paths in an imported SQLite copy."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


DOCKER_DATA_ROOT = Path("/app/data")


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: relocate-docker-data-paths.py DATABASE DATA_ROOT", file=sys.stderr)
        return 2

    database = Path(sys.argv[1]).resolve()
    data_root = Path(sys.argv[2]).resolve()
    if not database.is_file() or not data_root.is_dir():
        print("迁移数据目录或 SQLite 数据库不存在。", file=sys.stderr)
        return 1

    connection = sqlite3.connect(database)
    try:
        check = connection.execute("PRAGMA quick_check").fetchone()
        if not check or check[0] != "ok":
            print("迁移后的 SQLite 数据库未通过完整性检查。", file=sys.stderr)
            return 1

        rows = connection.execute(
            "SELECT DISTINCT project_dir FROM wren_revisions "
            "WHERE project_dir IS NOT NULL"
        ).fetchall()
        replacements: list[tuple[str, str]] = []
        for (stored_value,) in rows:
            stored_path = Path(stored_value)
            try:
                relative_path = stored_path.relative_to(DOCKER_DATA_ROOT)
            except ValueError:
                continue

            destination = (data_root / relative_path).resolve()
            if data_root not in destination.parents or not destination.is_dir():
                print(
                    "迁移副本缺少数据库引用的 Wren revision 文件；未修改数据库。",
                    file=sys.stderr,
                )
                return 1
            replacements.append((str(destination), stored_value))

        try:
            with connection:
                connection.executemany(
                    "UPDATE wren_revisions SET project_dir=? WHERE project_dir=?",
                    replacements,
                )
        except sqlite3.Error:
            print("更新 SQLite 中的 Wren 项目路径失败。", file=sys.stderr)
            return 1

        check = connection.execute("PRAGMA quick_check").fetchone()
        if not check or check[0] != "ok":
            print("路径更新后的 SQLite 数据库未通过完整性检查。", file=sys.stderr)
            return 1
        if replacements:
            print(f"已将 {len(replacements)} 个 Wren revision 路径切换到本机数据目录。")
        else:
            print("没有需要转换的 Docker Wren 路径。")
        return 0
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
