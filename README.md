# AI Remote

通用 SSH 远程控制与部署命令行工具，适合 Codex、Claude Code、Cursor 以及其他能调用终端的代码助手。

## 功能

- 保存多个服务器配置
- SSH 密码或密钥登录
- 执行远程命令
- 上传、下载文件
- 上传项目目录并执行构建或启动命令
- `--json` 输出，方便 AI 读取
- 自动保存 SSH 主机指纹，主机指纹变化时由 Paramiko 拒绝连接

## 安装

```powershell
cd ai-remote
python -m pip install -e .
```

## 服务器要求

工具使用 SSH，不使用 Windows 远程桌面协议。Windows 服务器需要安装并启动 OpenSSH Server，并在防火墙放行 SSH 端口：

```powershell
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
Start-Service sshd
Set-Service -Name sshd -StartupType Automatic
New-NetFirewallRule -Name OpenSSH-Server-In-TCP -DisplayName "OpenSSH Server (sshd)" -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22
```

如果服务器只开放了 `3389`，那是远程桌面端口，不能直接用于本工具；安装 SSH 后可在初始化时指定实际端口：

```powershell
ai-remote init --from-file .\服务器.txt --user Administrator --port 22
```

### 一键初始化脚本（Windows 服务器）

仓库里的 `setup-windows-ssh.ps1` 把上面的步骤合并成一条命令，并在防火墙规则里限制只允许指定客户端 IP 访问 22 端口，同时把 SSH 默认 Shell 设为 PowerShell（本工具的命令语法即 PowerShell）。在服务器上以管理员身份运行：

```powershell
# 在服务器的管理员 PowerShell 里执行；AllowedClientIP 换成你本机的公网出口 IP
.\setup-windows-ssh.ps1 -AllowedClientIP 203.0.113.10
```

脚本做四件事：安装 OpenSSH Server、设为自启动、把 SSH DefaultShell 指向 PowerShell、新建/启用仅放行 `AllowedClientIP` 的 22 端口防火墙规则并重启 sshd。执行完回到本机运行 `ai-remote status` 验证即可。

## 初始化服务器

`服务器.txt` 格式：

```text
服务器IP或域名
服务器密码
```

初始化配置：

```powershell
ai-remote init --from-file ".\server.example.txt" --user Administrator
```

密码只会保存到当前用户目录下的 `%USERPROFILE%\.ai-remote\profiles\default.json`，不会写入项目目录。也可以删除配置中的 `password`，改用环境变量：

```powershell
$env:AI_REMOTE_PASSWORD = "服务器密码"
ai-remote status
```

## 使用

```powershell
ai-remote status
ai-remote exec "Get-Process | Select-Object -First 5"
ai-remote upload .\dist C:\deploy\my-app
ai-remote download C:\deploy\my-app\logs\app.log .\app.log
ai-remote deploy .\my-app C:\deploy\my-app --command "cd C:\deploy\my-app; npm install; npm run build"
```

给 AI 或脚本调用时使用 JSON：

```powershell
ai-remote --json status
ai-remote --json exec "Get-Service sshd"
```

注意：`deploy` 的 `--command` 会在远端目标目录内执行（自动先进入该目录）。远程默认 Shell 是 PowerShell 5.1 时不支持 `&&` 链接命令，请用 `;` 分隔（如 `npm install; npm run build`），或一次只跑一条命令。

## 多服务器

```powershell
ai-remote init --from-file .\prod.txt --user Administrator --name production
ai-remote --profile production status
```

## 安全建议

- 建议在服务器开启 SSH 密钥登录后删除本地配置里的密码。
- 不要把 `%USERPROFILE%\.ai-remote`、`服务器.txt` 或包含密码的配置提交到 Git。
- 生产部署前建议先执行 `status`，再使用 `deploy`。
