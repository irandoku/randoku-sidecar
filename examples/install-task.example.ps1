<#
.SYNOPSIS
    Example scheduled-task installer for randoku-sidecar.
.DESCRIPTION
    Creates a logon task that runs your local start script.
    Replace paths for your own machine before using.
#>

$TaskName = 'Randoku Sidecar Bridge'
$ScriptPath = 'C:\Users\<YOU>\randoku-sidecar\start-randoku-sidecar.ps1'
$WorkingDir = 'C:\Users\<YOU>\randoku-sidecar'

$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-ExecutionPolicy Bypass -NoProfile -File `"$ScriptPath`"" -WorkingDirectory $WorkingDir
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Highest

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Description 'Example randoku-sidecar start task.' -Force
