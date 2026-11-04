"""Exercise the published CLI against a disposable, loopback-only SSH server."""

from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest

import paramiko


ROOT = Path(__file__).resolve().parents[1]


class Auth(paramiko.ServerInterface):
    def check_auth_password(self, username, password):
        return paramiko.AUTH_SUCCESSFUL if (username, password) == ("fixture", "fixture-password") else paramiko.AUTH_FAILED

    def get_allowed_auths(self, username):
        return "password"

    def check_channel_request(self, kind, chanid):
        return paramiko.OPEN_SUCCEEDED if kind == "session" else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_channel_exec_request(self, channel, command):
        def reply():
            time.sleep(0.02)
            if b"fixture-fail" in command:
                channel.send_stderr(b"intentional fixture failure")
                channel.send_exit_status(7)
            else:
                channel.send(b"remote-smoke\n")
                channel.send_exit_status(0)
            channel.shutdown_write()
            channel.close()
        threading.Thread(target=reply, daemon=True).start()
        return True


class Files(paramiko.SFTPServerInterface):
    def __init__(self, server, *, root):
        super().__init__(server)
        self.root = Path(root).resolve()

    def local(self, value):
        target = (self.root / value.lstrip("/")).resolve()
        if not target.is_relative_to(self.root):
            raise OSError("outside fixture")
        return target

    def stat(self, path):
        try:
            return paramiko.SFTPAttributes.from_stat(self.local(path).stat())
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno or 13)

    lstat = stat

    def mkdir(self, path, attr):
        try:
            self.local(path).mkdir()
            return paramiko.SFTP_OK
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno or 13)

    def open(self, path, flags, attr):
        try:
            fd = os.open(self.local(path), flags, 0o600)
            mode = "r+b" if flags & os.O_RDWR else ("wb" if flags & os.O_WRONLY else "rb")
            file = os.fdopen(fd, mode)
            handle = paramiko.SFTPHandle(flags)
            handle.readfile = file
            handle.writefile = file
            return handle
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno or 13)


class RemoteTests(unittest.TestCase):
    def test_real_cli_ssh_and_sftp(self):
        with tempfile.TemporaryDirectory(prefix="ai-remote-public-") as folder:
            temp = Path(folder)
            home, remote = temp / "home", temp / "remote"
            home.mkdir()
            remote.mkdir()
            key = paramiko.RSAKey.generate(2048)
            listener = socket.socket()
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            listener.settimeout(0.2)
            port = listener.getsockname()[1]
            stop = threading.Event()
            transports = []

            def accept():
                while not stop.is_set():
                    try:
                        client, _ = listener.accept()
                    except socket.timeout:
                        continue
                    except OSError:
                        break
                    transport = paramiko.Transport(client)
                    transports.append(transport)
                    transport.add_server_key(key)
                    transport.set_subsystem_handler("sftp", paramiko.SFTPServer, Files, root=remote)
                    transport.start_server(server=Auth())

            thread = threading.Thread(target=accept, daemon=True)
            thread.start()
            env = dict(os.environ, USERPROFILE=str(home), HOME=str(home), PYTHONUTF8="1")
            env.pop("AI_REMOTE_PASSWORD", None)

            def cli(*args, expected=0):
                result = subprocess.run(
                    [sys.executable, "-B", "-m", "ai_remote", "--json", *args],
                    cwd=ROOT, env=env, capture_output=True, text=True,
                    encoding="utf-8", timeout=25,
                )
                self.assertEqual(result.returncode, expected, result.stderr + result.stdout)
                return json.loads(result.stdout)

            try:
                self.assertFalse(cli("status", expected=1)["ok"])
                secret = temp / "server-fixture.txt"
                secret.write_text("127.0.0.1\nfixture-password\n", encoding="utf-8")
                self.assertTrue(cli("init", "--from-file", str(secret), "--user", "fixture", "--port", str(port))["ok"])
                self.assertTrue(cli("status")["ok"])
                result = cli("exec", "fixture-success")
                self.assertEqual(result["stdout"], "remote-smoke\n")
                self.assertEqual(cli("exec", "fixture-fail", expected=7)["exit_code"], 7)
                source = temp / "source"
                source.mkdir()
                (source / "file.txt").write_text("upload-marker", encoding="utf-8")
                (source / "__pycache__").mkdir()
                (source / "__pycache__/skip.txt").write_text("excluded", encoding="utf-8")
                self.assertEqual(cli("upload", str(source), "/upload")["uploaded_files"], 1)
                self.assertEqual((remote / "upload/file.txt").read_text(), "upload-marker")
                download = temp / "download.txt"
                cli("download", "/upload/file.txt", str(download))
                self.assertEqual(download.read_text(), "upload-marker")
                self.assertTrue(cli("deploy", str(source), "/deploy", "--command", "fixture-success")["ok"])
                self.assertFalse(cli("upload", str(temp / "missing"), "/missing", expected=1)["ok"])
                profile = home / ".ai-remote/profiles/default.json"
                config = json.loads(profile.read_text(encoding="utf-8"))
                config["password"] = "wrong-fixture-password"
                profile.write_text(json.dumps(config), encoding="utf-8")
                self.assertFalse(cli("status", expected=1)["ok"])
                config["password"] = "fixture-password"
                profile.write_text(json.dumps(config), encoding="utf-8")
                key = paramiko.RSAKey.generate(2048)
                mismatch = cli("status", expected=1)
                self.assertFalse(mismatch["ok"])
                self.assertIn("key", mismatch["error"].lower())
            finally:
                stop.set()
                listener.close()
                for transport in transports:
                    transport.close()
                thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
