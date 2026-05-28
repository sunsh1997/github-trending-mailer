param(
  [Parameter(Mandatory = $true)]
  [string]$ReportPath,

  [string]$Subject = ("GitHub 飙升项目日报 - " + (Get-Date).ToString("yyyy-MM-dd"))
)

$ErrorActionPreference = "Stop"
[System.Net.ServicePointManager]::SecurityProtocol = [System.Net.SecurityProtocolType]::Tls12

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$configPath = Join-Path $root "mail_config.json"
$passwordPath = Join-Path $root "smtp_password.sec"

if (-not (Test-Path -LiteralPath $ReportPath)) {
  throw "Report file not found: $ReportPath"
}

if (-not (Test-Path -LiteralPath $passwordPath)) {
  throw "Encrypted SMTP password file not found: $passwordPath"
}

$config = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
$body = Get-Content -LiteralPath $ReportPath -Raw -Encoding UTF8
$passwordText = Get-Content -LiteralPath $passwordPath -Raw -Encoding UTF8
$passwordText = $passwordText.Trim()
if ($passwordText.Length -gt 0 -and $passwordText[0] -eq [char]0xFEFF) {
  $passwordText = $passwordText.Substring(1)
}
$securePassword = $passwordText | ConvertTo-SecureString
$credential = [System.Management.Automation.PSCredential]::new($config.username, $securePassword)

$message = [System.Net.Mail.MailMessage]::new()
$message.From = [System.Net.Mail.MailAddress]::new($config.from)
$message.To.Add($config.to)
$message.Subject = $Subject
$message.SubjectEncoding = [System.Text.Encoding]::UTF8
$message.Body = $body
$message.BodyEncoding = [System.Text.Encoding]::UTF8
$message.IsBodyHtml = $false

$client = [System.Net.Mail.SmtpClient]::new($config.host, [int]$config.port)
$client.EnableSsl = [bool]$config.enableSsl
$client.Credentials = $credential.GetNetworkCredential()
$client.Send($message)

Write-Host "Sent GitHub trending report to $($config.to)"
