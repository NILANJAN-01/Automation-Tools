<#
.SYNOPSIS
  Registers the Job Mail Notifier: at logon, then every 2 minutes and on every unlock (e.g. opening
  the lid after sleep). Every run checks Gmail over IMAP and makes sure the popup window is running,
  so a killed popup comes back within 2 minutes. Runs as you while you are signed in; no admin needed.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\install_task.ps1                        # install / update
  powershell -ExecutionPolicy Bypass -File .\install_task.ps1 -IntervalMinutes 5     # check every 5 min instead
  powershell -ExecutionPolicy Bypass -File .\install_task.ps1 -Uninstall
  powershell -ExecutionPolicy Bypass -File .\install_task.ps1 -Uninstall -ForgetPassword   # also delete the saved app password
#>
param(
    [ValidateRange(1, 1440)][int]$IntervalMinutes = 2,
    [switch]$Uninstall,
    [switch]$ForgetPassword
)
$ErrorActionPreference = 'Stop'

$Dir          = $PSScriptRoot
$LogonTask    = 'Job Mail Notifier (logon)'
$PeriodicTask = 'Job Mail Notifier (periodic)'
$OldTasks     = @('Job Mail Notifier (unlock)')   # names used by earlier versions

foreach ($name in @($LogonTask, $PeriodicTask) + $OldTasks) {
    if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $name -Confirm:$false
        Write-Host "Removed task '$name'"
    }
}
if ($Uninstall) {
    # Also close the popup (it would otherwise stay open until sign-out).
    Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe'" |
        Where-Object { $_.CommandLine -like "*$Dir*popup.py*" } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    if ($ForgetPassword -and (Test-Path (Join-Path $Dir '.venv\Scripts\python.exe'))) {
        & (Join-Path $Dir '.venv\Scripts\python.exe') (Join-Path $Dir 'main.py') --forget
    }
    return
}

# Virtual environment with the dependencies (needs Python 3.11+ on PATH, or the py launcher).
$Venv = Join-Path $Dir '.venv'
$Python = Join-Path $Venv 'Scripts\python.exe'
if (-not (Test-Path $Python)) {
    Write-Host 'Creating virtual environment...'
    if (Get-Command py -ErrorAction SilentlyContinue) { py -3 -m venv $Venv } else { python -m venv $Venv }
    if (-not (Test-Path $Python)) { throw 'Could not create .venv - install Python 3.11+ from python.org first.' }
}
& $Python -c "import sys; sys.exit(sys.version_info < (3, 11))"
if ($LASTEXITCODE -ne 0) { throw "Python 3.11 or newer is needed (found $(& $Python --version)). Delete .venv and install a newer Python." }
& $Python -m pip install --quiet --disable-pip-version-check -r (Join-Path $Dir 'requirements.txt')
$PythonW = Join-Path $Venv 'Scripts\pythonw.exe'   # pythonw = no console window

# Gmail is read over IMAP with an app password (kept in Windows Credential Manager).
& $Python (Join-Path $Dir 'main.py') --is-set-up
if ($LASTEXITCODE -ne 0) {
    & $Python (Join-Path $Dir 'main.py') --setup
    if ($LASTEXITCODE -ne 0) { Write-Warning 'Gmail is not set up yet. Run  .\.venv\Scripts\python.exe main.py --setup  later.' }
}

$User      = "$env:USERDOMAIN\$env:USERNAME"
$Principal = New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Limited
$Settings  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
                -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 10)

function New-NotifierAction([string]$Trigger) {
    New-ScheduledTaskAction -Execute $PythonW -Argument "`"$Dir\main.py`" --trigger $Trigger" -WorkingDirectory $Dir
}

# 1) At logon (after boot / restart / sign-in). The popup opens at once; the Gmail check waits for Wi-Fi.
$LogonTrigger = New-ScheduledTaskTrigger -AtLogOn -User $User
$LogonTrigger.Delay = 'PT15S'
Register-ScheduledTask -TaskName $LogonTask -Action (New-NotifierAction 'logon') -Trigger $LogonTrigger `
    -Principal $Principal -Settings $Settings -Description 'Job mail popup + Gmail check at logon.' | Out-Null
Write-Host "Registered '$LogonTask'"

# 2) Every N minutes, plus on unlock (waking from sleep): check Gmail and keep the popup running.
$Start = (Get-Date).AddMinutes($IntervalMinutes)
# No -RepetitionDuration = repeat indefinitely.
$RepeatTrigger = New-ScheduledTaskTrigger -Once -At $Start -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes)

$Class = Get-CimClass -Namespace 'Root/Microsoft/Windows/TaskScheduler' -ClassName 'MSFT_TaskSessionStateChangeTrigger'
$UnlockTrigger = New-CimInstance -CimClass $Class -ClientOnly
$UnlockTrigger.StateChange = 8   # TASK_SESSION_UNLOCK
$UnlockTrigger.UserId      = $User
$UnlockTrigger.Delay       = 'PT15S'
$UnlockTrigger.Enabled     = $true

Register-ScheduledTask -TaskName $PeriodicTask -Action (New-NotifierAction 'interval') `
    -Trigger @($RepeatTrigger, $UnlockTrigger) -Principal $Principal -Settings $Settings `
    -Description "Job mail check every $IntervalMinutes min and on unlock; restarts the popup if it was closed." | Out-Null
Write-Host "Registered '$PeriodicTask' (every $IntervalMinutes min + on unlock)"

Write-Host "`nDone. Test now with:  Start-ScheduledTask -TaskName '$LogonTask'"
