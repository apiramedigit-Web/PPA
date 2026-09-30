# Product Performance Automation (PPA) - monthly scheduler registration.
#
#   .\scheduler.ps1                 register (or repair in place) the monthly task
#   .\scheduler.ps1 -Mode status    show the registration and next run
#   .\scheduler.ps1 -Mode remove    unregister the task
#
# SCHEDULE: 3rd of every month at 06:00 local time (operational schedule requested by the
# project owner; the original requirement said the 2nd at 06:00). run.py works out the previous
# full calendar month from the run date - no month is ever hard-coded here.
#
# One task only: registration replaces a task of the same name in place and reports any other
# task that already invokes this run.py.
#
# ELEVATION: this shell has a filtered token, so the task is registered with InteractiveToken /
# LeastPrivilege (runs when this user is logged on). A HighestAvailable XML is written next to
# this script for an administrator to import if ever needed.
#
# ENVIRONMENT: DATABASE_URL and WLP_SOURCE_DB_URL must be set at USER level; otherwise the run
# stops in preflight (FAILED, alert written) without touching anything.
#
# ASCII only (Windows PowerShell 5.1 parses BOM-less UTF-8 badly).

[CmdletBinding()]
param(
    [ValidateSet('register', 'status', 'remove')]
    [string]$Mode = 'register',
    [string]$TaskName = 'PPA_Monthly_Product_Performance',
    [int]$Day = 3,
    [string]$Time = '06:00'
)
$ErrorActionPreference = 'Stop'
$AutomationDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RunScript = Join-Path $AutomationDir 'run.py'
if (-not (Test-Path $RunScript)) { throw "run.py not found at $RunScript" }
$Python = (Get-Command python -ErrorAction Stop).Source
$h = [int]$Time.Split(':')[0]; $m = [int]$Time.Split(':')[1]
$next = Get-Date -Day $Day -Hour $h -Minute $m -Second 0 -Millisecond 0
if ($next -le (Get-Date)) { $next = $next.AddMonths(1) }
$start = $next.ToString('yyyy-MM-ddTHH:mm:ss')
$user = "$env:USERDOMAIN\$env:USERNAME"

function New-TaskXml([string]$RunLevel) {
@"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Product Performance Automation (PPA) - monthly run on the 3rd at 06:00 for the previous full calendar month: raw capture, mapping, Reports 1-4, exceptions, reconciliation, 15-point validation, history, workbook, and publication of the validated dashboard to tech_team_outputs.ph_task (project_code PPA, 30 PHs). Nothing is published unless every check passes.</Description>
    <URI>\$TaskName</URI>
  </RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>$start</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByMonth>
        <DaysOfMonth><Day>$Day</Day></DaysOfMonth>
        <Months><January /><February /><March /><April /><May /><June /><July /><August /><September /><October /><November /><December /></Months>
      </ScheduleByMonth>
    </CalendarTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author"><UserId>$user</UserId><LogonType>InteractiveToken</LogonType><RunLevel>$RunLevel</RunLevel></Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT4H</ExecutionTimeLimit>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>$Python</Command>
      <Arguments>"$RunScript" --trigger scheduler</Arguments>
      <WorkingDirectory>$AutomationDir</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"@
}

function Show-Task {
    $t = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($null -eq $t) { Write-Host "  '$TaskName' is NOT registered"; return }
    $i = Get-ScheduledTaskInfo -TaskName $TaskName
    Write-Host "  TaskName : $($t.TaskName)   State: $($t.State)   RunLevel: $($t.Principal.RunLevel)   Logon: $($t.Principal.LogonType)"
    foreach ($a in $t.Actions) { Write-Host "  Action   : $($a.Execute) $($a.Arguments)" }
    Write-Host "  Next run : $($i.NextRunTime)"
    Write-Host "  Last run : $($i.LastRunTime)  result $($i.LastTaskResult)"
    $others = Get-ScheduledTask | Where-Object { $_.TaskName -ne $TaskName -and ($_.Actions | Where-Object { $_.Arguments -like '*Product_Performance_Automation*run.py*' }) }
    if ($others) { Write-Warning "other tasks also invoke this run.py: $(($others | ForEach-Object TaskName) -join ', ')" } else { Write-Host "  no other task invokes this run.py" }
}

Write-Host "PPA monthly scheduler: day $Day at $Time ($((Get-TimeZone).Id)); action: $Python `"$RunScript`" --trigger scheduler"
if ($Mode -eq 'status') { Show-Task; exit 0 }
if ($Mode -eq 'remove') { Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false; Write-Host "  removed"; exit 0 }

(New-TaskXml 'HighestAvailable') | Out-File -FilePath (Join-Path $AutomationDir "$TaskName`_FULL_PRIVILEGE.xml") -Encoding Unicode
Register-ScheduledTask -TaskName $TaskName -Xml (New-TaskXml 'LeastPrivilege') -Force | Out-Null
Write-Host "  registered (InteractiveToken / LeastPrivilege)"
Show-Task
