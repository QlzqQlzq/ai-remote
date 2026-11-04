# Run on the server, not on the client workstation.
#Requires -RunAsAdministrator
param(
    [Parameter(Mandatory = $true)]
    [string]$AllowedClientIP
)

$ErrorActionPreference = 'Stop'
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
Set-Service -Name sshd -StartupType Automatic

# The CLI currently sends PowerShell commands, so configure that SSH shell.
New-Item -Path 'HKLM:\SOFTWARE\OpenSSH' -Force | Out-Null
New-ItemProperty -Path 'HKLM:\SOFTWARE\OpenSSH' -Name DefaultShell `
    -Value "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" `
    -PropertyType String -Force | Out-Null

$rule = Get-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -ErrorAction SilentlyContinue
if ($rule) {
    Set-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -Enabled True `
        -Direction Inbound -Action Allow -RemoteAddress $AllowedClientIP
} else {
    New-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' `
        -DisplayName 'OpenSSH Server (sshd)' -Enabled True `
        -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22 `
        -RemoteAddress $AllowedClientIP | Out-Null
}

Restart-Service sshd
Get-Service sshd | Select-Object Status, Name, StartType
Write-Host 'Also allow TCP 22 from the client IP in the cloud security group.'
