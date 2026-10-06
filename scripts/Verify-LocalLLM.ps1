param(
    [string]$BaseUrl = 'http://127.0.0.1:11434/v1',
    [string]$Model = $env:LOCAL_LLM_MODEL
)
$ErrorActionPreference = 'Stop'
# Read-only checks. No serve, pull, run, device options, inference or fallback.
$taskUri = [uri]$BaseUrl
if (-not $taskUri.IsLoopback -or $taskUri.Scheme -ne 'http' -or
    $taskUri.IsDefaultPort -or $taskUri.UserInfo -or $taskUri.Query -or $taskUri.Fragment -or
    $taskUri.AbsolutePath.TrimEnd('/') -ne '/v1') {
    throw 'BaseUrl must be an ordinary loopback HTTP /v1 API URL with an explicit port.'
}
$taskExecutable = (Get-Command ollama -ErrorAction Stop).Source
$taskCliDirectory = Join-Path ([IO.Path]::GetTempPath()) ('aig-ollama-check-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $taskCliDirectory | Out-Null
try {
    foreach ($taskArgument in @('--version', 'list')) {
        $taskStdout = Join-Path $taskCliDirectory 'stdout.txt'
        $taskStderr = Join-Path $taskCliDirectory 'stderr.txt'
        $taskProcess = Start-Process -FilePath $taskExecutable -ArgumentList $taskArgument `
            -PassThru -WindowStyle Hidden -RedirectStandardOutput $taskStdout -RedirectStandardError $taskStderr
        if (-not $taskProcess.WaitForExit(5000)) {
            $taskProcess.Kill()
            $taskProcess.WaitForExit()
            throw ('Ollama CLI check timed out: ' + $taskArgument)
        }
        Get-Content -LiteralPath $taskStdout
        Get-Content -LiteralPath $taskStderr
        if ($taskProcess.ExitCode -ne 0) { throw ('Ollama CLI check failed: ' + $taskArgument) }
    }
    $taskRoot = $taskUri.GetLeftPart([UriPartial]::Authority)
    $taskHealth = Invoke-WebRequest -Uri ($taskRoot + '/') -TimeoutSec 5 -NoProxy
    if ($taskHealth.StatusCode -ne 200 -or $taskHealth.Content.Trim() -ne 'Ollama is running') {
        throw 'Ollama health check failed.'
    }
    $taskModels = Invoke-RestMethod -Uri ($BaseUrl.TrimEnd('/') + '/models') -TimeoutSec 5 -NoProxy
    if (-not $Model) { throw 'Set LOCAL_LLM_MODEL to the exact installed phi-4-mini tag shown above.' }
    if ($Model -notmatch '^phi4-mini(:[A-Za-z0-9._-]+)?$' -or $Model -notin @($taskModels.data.id)) {
        throw 'The exact requested phi-4-mini tag is not installed. No model was pulled or substituted.'
    }
    Write-Output ('OLLAMA_SETUP=PASS MODEL=' + $Model + ' BASE_URL=' + $BaseUrl)
} finally {
    # Remove only these exact helper-generated text files, then their empty private directory.
    foreach ($taskName in @('stdout.txt', 'stderr.txt')) {
        $taskOutputPath = Join-Path $taskCliDirectory $taskName
        if (Test-Path -LiteralPath $taskOutputPath) { Remove-Item -LiteralPath $taskOutputPath }
    }
    Remove-Item -LiteralPath $taskCliDirectory
}
