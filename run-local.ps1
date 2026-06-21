param(
    [string]$Python = "",
    [string[]]$PythonArgs = @(),
    [switch]$NoInstall
)

$ErrorActionPreference = "Stop"

if (-not $PSBoundParameters.ContainsKey("Python") -and $PythonArgs.Count -gt 0) {
    throw "-PythonArgs requires -Python so the launcher knows which interpreter receives the args."
}

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$backend = Join-Path $root "backend"
$venvPython = Join-Path $backend ".venv\Scripts\python.exe"
$backendEnv = Join-Path $backend ".env"
$backendEnvExample = Join-Path $backend ".env.example"
$nodeModules = Join-Path $root "node_modules"

function Invoke-Step {
    param(
        [string]$Label,
        [scriptblock]$Command
    )
    Write-Host "[$Label]" -ForegroundColor Cyan
    & $Command
}

function Invoke-NativeChecked {
    param(
        [string]$FilePath,
        [string[]]$ArgumentList,
        [string]$WorkingDirectory
    )
    Push-Location $WorkingDirectory
    try {
        & $FilePath @ArgumentList
        if ($LASTEXITCODE -ne 0) {
            throw "Command failed with exit code ${LASTEXITCODE}: $FilePath $($ArgumentList -join ' ')"
        }
    } finally {
        Pop-Location
    }
}

function Test-NativeCommand {
    param(
        [string]$FilePath,
        [string[]]$ArgumentList,
        [string]$WorkingDirectory
    )
    Push-Location $WorkingDirectory
    try {
        & $FilePath @ArgumentList *> $null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    } finally {
        Pop-Location
    }
}

function Test-PythonDependencies {
    param(
        [string]$PythonExe,
        [string[]]$PythonArgs
    )
    $modules = @(
        "fastapi",
        "uvicorn",
        "pydantic_settings",
        "dotenv",
        "numpy",
        "pandas",
        "scipy",
        "statsmodels",
        "pyarrow",
        "orjson",
        "websockets",
        "httpx",
        "pytest"
    )
    $moduleList = $modules -join ","
    $checkScript = "import importlib.util, sys; modules = '$moduleList'.split(','); missing = [name for name in modules if importlib.util.find_spec(name) is None]; print('missing python modules: ' + ', '.join(missing), file=sys.stderr) if missing else None; raise SystemExit(1 if missing else 0)"
    $cmdArgs = @($PythonArgs) + @("-c", $checkScript)
    return Test-NativeCommand -FilePath $PythonExe -ArgumentList $cmdArgs -WorkingDirectory $backend
}

function Test-FrontendDependencies {
    if (-not (Test-Path -LiteralPath $nodeModules)) {
        return $false
    }
    return Test-NativeCommand -FilePath "npm.cmd" -ArgumentList @("ls", "--depth=0", "--silent") -WorkingDirectory $root
}

function Repair-ProcessPathEnvironment {
    $pathValue = [Environment]::GetEnvironmentVariable("Path", "Process")
    if (-not $pathValue) {
        $pathValue = [Environment]::GetEnvironmentVariable("PATH", "Process")
    }
    if ($pathValue) {
        [Environment]::SetEnvironmentVariable("PATH", $null, "Process")
        [Environment]::SetEnvironmentVariable("Path", $pathValue, "Process")
    }
}

function Stop-ProcessTree {
    param(
        [System.Diagnostics.Process]$Process
    )
    if ($null -eq $Process -or $Process.HasExited) {
        return
    }
    & taskkill.exe /PID $Process.Id /T /F *> $null
    if ($LASTEXITCODE -ne 0 -and -not $Process.HasExited) {
        Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue
    }
}

function New-RepoVenv {
    if ($NoInstall) {
        throw "backend/.venv is missing, no active Conda environment was detected, and -NoInstall was set."
    }

    $bootstrapPython = "py"
    $bootstrapArgs = @("-3.11")
    if (-not (Get-Command $bootstrapPython -ErrorAction SilentlyContinue)) {
        $bootstrapPython = "python"
        $bootstrapArgs = @()
    }

    Invoke-Step "backend venv" {
        Invoke-NativeChecked `
            -FilePath $bootstrapPython `
            -ArgumentList (@($bootstrapArgs) + @("-m", "venv", ".venv")) `
            -WorkingDirectory $backend
    }
}

function Resolve-BackendPython {
    if ($PSBoundParameters.ContainsKey("Python")) {
        return @{
            Exe = $Python
            Args = $PythonArgs
            Source = "custom"
        }
    }

    if ($env:CONDA_PREFIX) {
        $condaPython = Join-Path $env:CONDA_PREFIX "python.exe"
        if (Test-Path -LiteralPath $condaPython) {
            return @{
                Exe = $condaPython
                Args = @()
                Source = "conda:$($env:CONDA_DEFAULT_ENV)"
            }
        }
    }

    if (-not (Test-Path -LiteralPath $venvPython)) {
        New-RepoVenv
    }

    return @{
        Exe = $venvPython
        Args = @()
        Source = "backend/.venv"
    }
}

if (-not (Test-Path -LiteralPath $backendEnv)) {
    if (-not (Test-Path -LiteralPath $backendEnvExample)) {
        throw "Missing backend/.env.example"
    }
    Copy-Item -LiteralPath $backendEnvExample -Destination $backendEnv
    Write-Host "[setup] Created backend/.env from backend/.env.example" -ForegroundColor Yellow
    Write-Host "[setup] Add Binance Demo/Testnet keys there before pressing Start in the dashboard." -ForegroundColor Yellow
}

$backendPythonInfo = Resolve-BackendPython
$backendPython = [string]$backendPythonInfo.Exe
$backendPythonArgs = [string[]]$backendPythonInfo.Args
Write-Host "[backend] using Python: $($backendPythonInfo.Source) -> $backendPython $($backendPythonArgs -join ' ')" -ForegroundColor DarkCyan

if (-not (Test-PythonDependencies -PythonExe $backendPython -PythonArgs $backendPythonArgs)) {
    if ($NoInstall) {
        throw "Python dependencies are missing and -NoInstall was set."
    }
    Invoke-Step "backend dependencies" {
        Invoke-NativeChecked -FilePath $backendPython -ArgumentList (@($backendPythonArgs) + @("-m", "pip", "install", "--upgrade", "pip")) -WorkingDirectory $backend
        Invoke-NativeChecked -FilePath $backendPython -ArgumentList (@($backendPythonArgs) + @("-m", "pip", "install", "-r", "requirements.txt")) -WorkingDirectory $backend
    }
} else {
    Write-Host "[backend] dependencies already installed" -ForegroundColor DarkGray
}

if (-not (Test-FrontendDependencies)) {
    if ($NoInstall) {
        throw "Frontend dependencies are missing or incomplete and -NoInstall was set."
    }
    Invoke-Step "frontend dependencies" {
        Invoke-NativeChecked -FilePath "npm.cmd" -ArgumentList @("ci") -WorkingDirectory $root
    }
} else {
    Write-Host "[frontend] dependencies already installed" -ForegroundColor DarkGray
}

Write-Host ""
Write-Host "Starting local services. Press Ctrl+C to stop both." -ForegroundColor Green
Write-Host "Backend:   http://127.0.0.1:8000"
Write-Host "Dashboard: http://localhost:5173"
Write-Host ""

Repair-ProcessPathEnvironment

$backendProc = $null
$frontendProc = $null

try {
    $backendProc = Start-Process `
        -FilePath $backendPython `
        -ArgumentList (@($backendPythonArgs) + @("main.py")) `
        -WorkingDirectory $backend `
        -NoNewWindow `
        -PassThru

    $frontendProc = Start-Process `
        -FilePath "npm.cmd" `
        -ArgumentList "run", "dev" `
        -WorkingDirectory $root `
        -NoNewWindow `
        -PassThru

    while ($true) {
        Start-Sleep -Seconds 1
        if ($backendProc.HasExited) {
            throw "Backend exited with code $($backendProc.ExitCode)."
        }
        if ($frontendProc.HasExited) {
            throw "Frontend exited with code $($frontendProc.ExitCode)."
        }
    }
} finally {
    foreach ($proc in @($frontendProc, $backendProc)) {
        Stop-ProcessTree -Process $proc
    }
}
