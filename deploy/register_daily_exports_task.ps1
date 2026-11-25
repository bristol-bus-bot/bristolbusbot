# Register (or replace) the "BristolBusBot daily exports" scheduled task.
# Planning #152. Run from the repository root in PowerShell:
#     powershell -ExecutionPolicy Bypass -File deploy\register_daily_exports_task.ps1
#
# A single 09:00 run was missed on 24 and 25 Nov 2026 because the laptop was
# asleep (modern standby) at 09:00 and "run when available" did not catch up.
# So the task now fires when Tom signs in, when he unlocks the PC, and every
# hour while it is on. The script's --once-a-day flag means only the first
# successful run of the day does any work; later ones exit at once.

$ErrorActionPreference = "Stop"
$name = "BristolBusBot daily exports"
$repo = (Get-Location).Path
$python = Join-Path $repo ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $python)) { throw "Run this from the repository root: $python not found" }

$action = New-ScheduledTaskAction -Execute $python `
    -Argument "deploy\get_daily_exports.py --combine --log --once-a-day" `
    -WorkingDirectory $repo

$user = "$env:USERDOMAIN\$env:USERNAME"
$atLogOn = New-ScheduledTaskTrigger -AtLogOn -User $user

# Every hour, all day, every day.
$hourly = New-ScheduledTaskTrigger -Daily -At 00:00
$hourly.Repetition = (New-ScheduledTaskTrigger -Once -At 00:00 `
    -RepetitionInterval (New-TimeSpan -Hours 1) `
    -RepetitionDuration (New-TimeSpan -Days 1)).Repetition

# On unlock (StateChange 8 = session unlock); PowerShell has no shortcut.
$class = Get-CimClass -Namespace Root/Microsoft/Windows/TaskScheduler `
    -ClassName MSFT_TaskSessionStateChangeTrigger
$onUnlock = New-CimInstance -CimClass $class -ClientOnly
$onUnlock.StateChange = 8
$onUnlock.UserId = $user
$onUnlock.Enabled = $true

$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -MultipleInstances IgnoreNew

$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive

Register-ScheduledTask -TaskName $name -Action $action `
    -Trigger @($atLogOn, $hourly, $onUnlock) -Settings $settings `
    -Principal $principal -Force | Out-Null
Write-Output "Registered '$name': at sign-in, at unlock and hourly, once a day."
