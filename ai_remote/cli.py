from __future__ import annotations

import argparse
import getpass
import json
import os
import posixpath
import shutil
import stat
import sys
from pathlib import Path
from typing import Any


APP_DIR = Path.home() / ".ai-remote"
PROFILES_DIR = APP_DIR / "profiles"
KNOWN_HOSTS = APP_DIR / "known_hosts"


def die(message: str, json_mode: bool = False, code: int = 1) -> int:
    payload = {"ok": False, "error": message}
    if json_mode:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(f"错误: {message}", file=sys.stderr)
    return code


def emit(payload: dict[str, Any], json_mode: bool) -> None:
    if json_mode:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif payload.get("ok"):
        if "stdout" in payload:
            print(payload["stdout"], end="" if payload["stdout"].endswith("\n") else "\n")
        elif "message" in payload:
            print(payload["message"])


def profile_path(name: str) -> Path:
    return PROFILES_DIR / f"{name}.json"


def load_profile(name: str) -> dict[str, Any]:
    path = profile_path(name)
    if not path.exists():
        raise RuntimeError(
            f"找不到配置 {path}，请先运行 "
            f"`python -m ai_remote init --from-file \"服务器.txt\" --user Administrator`"
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"配置文件格式错误: {path}") from exc
    if not data.get("host") or not data.get("username"):
        raise RuntimeError(f"配置缺少 host 或 username: {path}")
    return data


def read_server_file(path: Path) -> tuple[str, str]:
    lines = [line.strip() for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    if len(lines) < 2:
        raise RuntimeError("服务器文件至少需要两行：第一行 IP/域名，第二行密码")
    return lines[0], lines[1]


def ensure_dirs() -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    PROFILES_DIR.mkdir(parents=True, exist_ok=True)


def write_private(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    if os.name != "nt":
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def cmd_init(args: argparse.Namespace) -> int:
    try:
        source = Path(args.from_file).expanduser()
        host, password = read_server_file(source)
        ensure_dirs()
        data = {
            "host": host,
            "port": args.port,
            "username": args.user,
            "password": password,
            "key_file": args.key_file or None,
            "remote_shell": args.remote_shell,
        }
        path = profile_path(args.name)
        write_private(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        emit(
            {
                "ok": True,
                "message": f"已创建配置 {path}",
                "profile": args.name,
                "host": host,
                "port": args.port,
                "username": args.user,
                "password_saved_locally": True,
            },
            args.json,
        )
        return 0
    except (OSError, RuntimeError) as exc:
        return die(str(exc), args.json)


def load_paramiko():
    try:
        import paramiko
    except ImportError as exc:
        raise RuntimeError("缺少 paramiko，请先运行 `python -m pip install -e .`") from exc
    return paramiko


class Remote:
    def __init__(self, profile: dict[str, Any]):
        paramiko = load_paramiko()
        self.paramiko = paramiko
        self.profile = profile
        self.client = paramiko.SSHClient()
        if KNOWN_HOSTS.exists():
            self.client.load_host_keys(str(KNOWN_HOSTS))
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    def __enter__(self) -> "Remote":
        password = os.environ.get("AI_REMOTE_PASSWORD") or self.profile.get("password")
        key_file = self.profile.get("key_file")
        self.client.connect(
            hostname=self.profile["host"],
            port=int(self.profile.get("port", 22)),
            username=self.profile["username"],
            password=password,
            key_filename=key_file or None,
            timeout=15,
            banner_timeout=15,
            auth_timeout=15,
            look_for_keys=not bool(password or key_file),
            allow_agent=True,
        )
        ensure_dirs()
        self.client.save_host_keys(str(KNOWN_HOSTS))
        return self

    def __exit__(self, *_: Any) -> None:
        self.client.close()

    def exec(self, command: str, timeout: int = 120) -> dict[str, Any]:
        stdin, stdout, stderr = self.client.exec_command(command, timeout=timeout)
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        code = stdout.channel.recv_exit_status()
        return {"ok": code == 0, "exit_code": code, "stdout": out, "stderr": err}

    def put_file(self, local: Path, remote: str) -> None:
        with self.client.open_sftp() as sftp:
            sftp.put(str(local), remote)

    def put_tree(self, local: Path, remote: str, excludes: set[str]) -> int:
        count = 0
        with self.client.open_sftp() as sftp:
            for source in local.rglob("*"):
                relative = source.relative_to(local)
                if any(part in excludes for part in relative.parts):
                    continue
                target = posixpath.join(remote.replace("\\", "/"), *relative.parts)
                if source.is_dir():
                    self._mkdirs(sftp, target)
                else:
                    self._mkdirs(sftp, posixpath.dirname(target))
                    sftp.put(str(source), target)
                    count += 1
        return count

    @staticmethod
    def _mkdirs(sftp: Any, path: str) -> None:
        parts = path.replace("\\", "/").split("/")
        current = ""
        if path.startswith("/"):
            current = "/"
        for part in parts:
            if not part:
                continue
            current = posixpath.join(current, part)
            try:
                sftp.stat(current)
            except OSError:
                try:
                    sftp.mkdir(current)
                except OSError:
                    pass


def connect(args: argparse.Namespace) -> tuple[dict[str, Any], Remote]:
    profile = load_profile(args.profile)
    return profile, Remote(profile)


def remote_dir_command(shell: str, remote_dir: str, command: str) -> str:
    """把命令包成“先进入 remote_dir 再执行”的形式，按远端 shell 适配语法。"""
    shell = (shell or "").lower()
    if "powershell" in shell or "pwsh" in shell:
        quoted = "'" + remote_dir.replace("'", "''") + "'"
        return f"Set-Location -LiteralPath {quoted}; {command}"
    quoted = "'" + remote_dir.replace("'", "'\\''") + "'"
    return f"cd {quoted} && {command}"


def cmd_status(args: argparse.Namespace) -> int:
    try:
        profile, remote = connect(args)
        with remote:
            result = remote.exec(
                "Write-Output ('host=' + $env:COMPUTERNAME); "
                "Write-Output ('user=' + [Environment]::UserName); "
                "Write-Output ('pwd=' + (Get-Location).Path); "
                "Write-Output ('ps=' + $PSVersionTable.PSVersion.ToString())"
            )
        result.update({"host": profile["host"], "username": profile["username"]})
        emit(result, args.json)
        return 0 if result["ok"] else 1
    except Exception as exc:
        return die(str(exc), args.json)


def cmd_exec(args: argparse.Namespace) -> int:
    try:
        _, remote = connect(args)
        with remote:
            result = remote.exec(args.command, args.timeout)
        emit(result, args.json)
        if not result["ok"] and result.get("stderr") and not args.json:
            print(result["stderr"], file=sys.stderr, end="")
        return 0 if result["ok"] else int(result["exit_code"] or 1)
    except Exception as exc:
        return die(str(exc), args.json)


def cmd_upload(args: argparse.Namespace) -> int:
    try:
        _, remote = connect(args)
        local = Path(args.local).expanduser().resolve()
        if not local.exists():
            return die(f"本地路径不存在: {local}", args.json)
        with remote:
            if local.is_dir():
                count = remote.put_tree(local, args.remote, set(args.exclude))
            else:
                remote.put_file(local, args.remote)
                count = 1
        result = {"ok": True, "uploaded_files": count, "remote": args.remote}
        emit(result, args.json)
        return 0
    except Exception as exc:
        return die(str(exc), args.json)


def cmd_download(args: argparse.Namespace) -> int:
    try:
        _, remote = connect(args)
        local = Path(args.local).expanduser().resolve()
        local.parent.mkdir(parents=True, exist_ok=True)
        with remote, remote.client.open_sftp() as sftp:
            sftp.get(args.remote, str(local))
        emit({"ok": True, "downloaded": str(local)}, args.json)
        return 0
    except Exception as exc:
        return die(str(exc), args.json)


def cmd_deploy(args: argparse.Namespace) -> int:
    try:
        profile, remote = connect(args)
        local = Path(args.local).expanduser().resolve()
        if not local.is_dir():
            return die(f"部署源必须是目录: {local}", args.json)
        with remote:
            count = remote.put_tree(local, args.remote, set(args.exclude))
            result: dict[str, Any] = {"ok": True, "uploaded_files": count, "remote": args.remote}
            if args.command:
                command = remote_dir_command(
                    str(profile.get("remote_shell") or ""), args.remote, args.command
                )
                result["command_result"] = remote.exec(command, args.timeout)
                result["ok"] = result["command_result"]["ok"]
        emit(result, args.json)
        return 0 if result["ok"] else 1
    except Exception as exc:
        return die(str(exc), args.json)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ai-remote", description="通用 SSH 远程控制与部署工具")
    parser.add_argument("--profile", default="default", help="配置名称，默认 default")
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    sub = parser.add_subparsers(dest="action", required=True)

    init = sub.add_parser("init", help="从服务器.txt创建本地连接配置")
    init.add_argument("--from-file", required=True)
    init.add_argument("--name", default="default")
    init.add_argument("--user", default="Administrator")
    init.add_argument("--port", type=int, default=22)
    init.add_argument("--key-file")
    init.add_argument("--remote-shell", default="powershell")
    init.set_defaults(func=cmd_init)

    status = sub.add_parser("status", help="检查 SSH 连接和远程环境")
    status.set_defaults(func=cmd_status)

    execute = sub.add_parser("exec", help="执行远程命令")
    execute.add_argument("command")
    execute.add_argument("--timeout", type=int, default=120)
    execute.set_defaults(func=cmd_exec)

    upload = sub.add_parser("upload", help="上传文件或目录")
    upload.add_argument("local")
    upload.add_argument("remote")
    upload.add_argument("--exclude", action="append", default=[".git", "__pycache__", "node_modules", ".venv", "venv"])
    upload.set_defaults(func=cmd_upload)

    download = sub.add_parser("download", help="下载远程文件")
    download.add_argument("remote")
    download.add_argument("local")
    download.set_defaults(func=cmd_download)

    deploy = sub.add_parser("deploy", help="上传项目并可选执行启动/构建命令")
    deploy.add_argument("local")
    deploy.add_argument("remote")
    deploy.add_argument("--command")
    deploy.add_argument("--timeout", type=int, default=600)
    deploy.add_argument("--exclude", action="append", default=[".git", "__pycache__", "node_modules", ".venv", "venv"])
    deploy.set_defaults(func=cmd_deploy)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
