# Register the sync-rate explorer server to start automatically when this Windows user logs on.
#
#   powershell -ExecutionPolicy Bypass -File webapp\install_autostart.ps1            # register + start now
#   powershell -ExecutionPolicy Bypass -File webapp\install_autostart.ps1 -Remove    # unregister
#
# The task runs webapp\start_webapp.bat in a minimized window. That script restarts the
# server if it ever stops, and reads its settings from <repo>\.env.

param([switch]$Remove)

$TaskName = "SyncRateExplorer"

if ($Remove) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Removed scheduled task '$TaskName'. (A server window that is already open keeps running until you close it.)"
    exit 0
}

$repo = Split-Path -Parent $PSScriptRoot
$bat = Join-Path $PSScriptRoot "start_webapp.bat"
if (-not (Test-Path (Join-Path $repo ".env"))) {
    Write-Warning "No .env found in $repo - copy .env.example to .env and fill in NARA_SERVICE_KEY first."
    exit 1
}

$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c start `"SyncRateExplorer`" /min `"$bat`"" -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Description "Sync-rate explorer web app (python -m webapp) - auto start at logon" -Force | Out-Null
Write-Host "Registered '$TaskName': the server will start automatically every time you log on."

Start-ScheduledTask -TaskName $TaskName
Write-Host "Started now. Log file: $repo\data\webapp.log"
