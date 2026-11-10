#!/usr/bin/env python3
"""incoPat 登录与凭据管理。

用法:
    # 保存凭据（会提示输入密码，不回显）
    python3 scripts/inco_login.py --save --user <账号>
    # 也可以一次给全（密码会进 shell 历史，不推荐）
    python3 scripts/inco_login.py --save --user <账号> --password <密码>

    # 验证当前浏览器登录状态（未登录则按凭据自动登录）
    python3 scripts/inco_login.py --check

    # 查看凭据文件位置与状态（不显示密码）
    python3 scripts/inco_login.py --status

凭据文件：默认 ~/.incopat/credential（权限 600），可用 INCOPAT_CREDENTIAL_FILE 覆盖。
该文件不进 skill、不进仓库、不进日志。
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import inco_client as INCO


def cmd_save(user: str, password: str) -> int:
    path = INCO.save_credential(user, password)
    print(f"已保存凭据到 {path}（权限 600）")
    print(f"账号：{user}")
    return 0


def cmd_status() -> int:
    path = INCO.CRED_FILE
    exists = os.path.exists(path)
    print(f"凭据文件：{path}")
    print(f"状态：{'存在' if exists else '不存在'}")
    if exists:
        mode = oct(os.stat(path).st_mode & 0o777)
        cred = INCO.load_credential()
        print(f"权限：{mode}")
        if cred:
            print(f"账号：{cred[0]}（密码不显示）")
        else:
            print("内容：格式不对，需要 {\"user\": \"...\", \"password\": \"...\"}")
    return 0 if exists else 1


def cmd_check() -> int:
    r = INCO.ensure_login(force=True)
    print(json.dumps(r, ensure_ascii=False))
    return 0 if r.get("ok") else 1


def main(argv) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--save", action="store_true", help="保存账号密码到凭据文件")
    ap.add_argument("--user", help="账号")
    ap.add_argument("--password", help="密码（不给则交互输入）")
    ap.add_argument("--check", action="store_true", help="检查/完成登录")
    ap.add_argument("--status", action="store_true", help="查看凭据文件状态")
    a = ap.parse_args(argv[1:])

    if a.status:
        return cmd_status()
    if a.save:
        user = a.user or input("incoPat 账号：").strip()
        pwd = a.password or getpass.getpass("incoPat 密码（不回显）：")
        if not user or not pwd:
            print("账号或密码为空", file=sys.stderr)
            return 2
        return cmd_save(user, pwd)
    if a.check:
        return cmd_check()
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
