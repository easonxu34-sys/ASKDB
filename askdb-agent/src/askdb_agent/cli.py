from __future__ import annotations

import argparse
import sys

from dotenv import load_dotenv

from askdb_agent.application.auth import AuthApplication
from askdb_agent.auth_store import AuthStore
from askdb_agent.domain.auth import AuthError


def main() -> int:
    parser = argparse.ArgumentParser(prog="askdb-agent")
    commands = parser.add_subparsers(dest="command", required=True)
    auth_parser = commands.add_parser("auth", help="账号运维命令")
    auth_commands = auth_parser.add_subparsers(dest="auth_command", required=True)
    auth_commands.add_parser("init-admin", help="一次性初始化首位管理员")
    auth_commands.add_parser("recover-admin", help="在 Agent 主机恢复管理员账号")
    args = parser.parse_args()

    if args.command == "auth" and args.auth_command in {"init-admin", "recover-admin"}:
        if not sys.stdin.isatty():
            parser.error("管理员初始化和恢复必须在 Agent 主机的交互式终端执行。")
        load_dotenv()
        application = AuthApplication(AuthStore())
        try:
            application.store.initialize()
            if args.auth_command == "init-admin":
                username = input("首位管理员用户名或工号: ")
                user, temporary_password = application.bootstrap_admin(username)
                action = "首位管理员已创建"
            else:
                username = input("要恢复为管理员的现有用户名或工号: ")
                confirmation = input(f"这会启用并恢复账号“{username.strip()}”为管理员，继续请输入 YES: ")
                if confirmation != "YES":
                    print("已取消管理员恢复。", file=sys.stderr)
                    return 1
                user, temporary_password = application.recover_admin(username)
                action = f"管理员账号“{user.username}”已恢复"
        except (AuthError, ValueError) as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(f"\n{action}。临时密码只显示这一次，请立即交付给该管理员：")
        print(temporary_password)
        print("首次登录后必须修改密码。")
        del temporary_password
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
