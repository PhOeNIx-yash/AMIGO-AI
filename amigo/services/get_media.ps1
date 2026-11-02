Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
Function AwaitTask($WinRtTask, $ResultType) {
    $asTask = $asTaskGeneric.MakeGenericMethod($ResultType)
    $netTask = $asTask.Invoke($null, @($WinRtTask))
    $netTask.Wait(1500) | Out-Null
    return $netTask.Result
}
try {
    [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager,Windows.Media,ContentType=WindowsRuntime] | Out-Null
    $manager = AwaitTask ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager]::RequestAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager])
    if ($manager) {
        $sessions = @()
        $curr = $manager.GetCurrentSession()
        if ($curr) { $sessions += $curr }
        $all = $manager.GetSessions()
        if ($all) { foreach ($s in $all) { if ($s -ne $curr) { $sessions += $s } } }
        foreach ($session in $sessions) {
            try {
                $props = AwaitTask ($session.TryGetMediaPropertiesAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionMediaProperties])
                if ($props -and $props.Title) {
                    $info = $session.GetPlaybackInfo()
                    $status = if ($info) { $info.PlaybackStatus } else { "Unknown" }
                    Write-Output "STATUS:$status"
                    Write-Output "TITLE:$($props.Title)"
                    Write-Output "ARTIST:$($props.Artist)"
                    break
                }
            } catch {}
        }
    }
} catch {
}
