# Sets up Moo Local on this Windows computer, so a Moo on the same home
# network can use it for listening, stories and voice. "Install Moo Local.cmd",
# which the Moo hands out, downloads this file and runs it.
#
# Everything goes into the home folder:
#   %USERPROFILE%\.moo-local\app       Moo Local itself, fetched from the Moo
#   %USERPROFILE%\.moo-local\bin       uv, which brings its own Python 3.12
#   %USERPROFILE%\.moo-local-venv      Moo Local's libraries
#   %USERPROFILE%\.moo-kitten2-venv    Kitten TTS 2's libraries
# Ollama comes from winget, or from Ollama's own installer when winget is
# missing. It downloads no AI models: the parent chooses them afterwards on
# the Moo's AI engine screen. It starts Moo Local and adds it to the Startup folder. Windows asks
# once (an administrator "Yes") to let the Moo reach Moo Local through the
# firewall. Run it again at any time to update.
param([string]$Moo = "__MOO_URL__")

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"  # Windows PowerShell downloads crawl with a progress bar
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Root = Join-Path $env:USERPROFILE ".moo-local"
$App = Join-Path $Root "app"
$Venv = Join-Path $env:USERPROFILE ".moo-local-venv"
$KVenv = Join-Path $env:USERPROFILE ".moo-kitten2-venv"
$UvVersion = "0.13.9"
# Every library Moo Local needs has a build for 64-bit Intel/AMD Windows; an
# ARM computer runs that same Python through Windows' own emulation.
$PythonRequest = "cpython-3.12-windows-x86_64-none"

New-Item -ItemType Directory -Force -Path $App, (Join-Path $Root "bin"), (Join-Path $Root "kokoro"), (Join-Path $Root "voices") | Out-Null
try { Start-Transcript -Append -Path (Join-Path $Root "install.log") | Out-Null } catch {}

function Say($text) { Write-Host ""; Write-Host "==> $text" -ForegroundColor Cyan }
function Fail($text) {
    Write-Host ""
    Write-Host "Moo Local could not be set up: $text" -ForegroundColor Red
    Write-Host "The details are in $Root\install.log. Running the installer again continues where it stopped."
    try { Stop-Transcript | Out-Null } catch {}
    exit 1
}
function Download($url, $out) {
    $part = "$out.part"
    $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
    if ($curl) { & $curl.Source -fL --progress-bar $url -o $part; if ($LASTEXITCODE -ne 0) { throw "download failed: $url" } }
    else { Invoke-WebRequest -UseBasicParsing $url -OutFile $part }
    Move-Item -Force $part $out
}
function Ollama-Up {
    try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 "http://127.0.0.1:11434/api/tags" | Out-Null; return $true } catch { return $false }
}
function Find-Ollama {
    # A program installed a moment ago is not on this window's PATH yet: read it again.
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
    $c = Get-Command ollama.exe -ErrorAction SilentlyContinue
    if ($c) { return $c.Source }
    foreach ($dir in @((Join-Path $env:LOCALAPPDATA "Programs\Ollama"), (Join-Path $env:ProgramFiles "Ollama"), (Join-Path $env:LOCALAPPDATA "Ollama"))) {
        $p = Join-Path $dir "ollama.exe"
        if (Test-Path $p) { return $p }
        if (Test-Path $dir) {
            $found = Get-ChildItem -Path $dir -Filter ollama.exe -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($found) { return $found.FullName }
        }
    }
    return $null
}
function Wait-Ollama($seconds) {
    # Ollama's own app starts the service after its first window; give it time.
    for ($i = 0; $i -lt $seconds; $i++) {
        if (Ollama-Up) { return $true }
        Start-Sleep 1
    }
    return (Ollama-Up)
}

# --- Moo Local's files, from the Moo -----------------------------------------
Say "Moo Local from the Moo ($Moo)"
foreach ($f in "moo_local.py", "mlx_voice_worker.py", "kitten2_worker.py", "requirements-base.txt", "requirements.txt", "requirements-kitten2.txt") {
    try { Invoke-WebRequest -UseBasicParsing "$Moo/local-ai/files/$f" -OutFile (Join-Path $App "$f.new") }
    catch { Fail "the Moo at $Moo did not answer. Is this computer on the same wifi as the Moo?" }
    Move-Item -Force (Join-Path $App "$f.new") (Join-Path $App $f)
}

# --- uv and Python ---------------------------------------------------------------
Say "Python 3.12 (through uv)"
$Uv = Join-Path $Root "bin\uv.exe"
$haveUv = (Test-Path $Uv) -and ((& $Uv --version) -match [regex]::Escape($UvVersion))
if (-not $haveUv) {
    $arch = if ($env:PROCESSOR_ARCHITECTURE -eq "ARM64") { "aarch64" } else { "x86_64" }
    $zip = Join-Path $env:TEMP "uv-$UvVersion.zip"
    try {
        Download "https://github.com/astral-sh/uv/releases/download/$UvVersion/uv-$arch-pc-windows-msvc.zip" $zip
        Expand-Archive -Force $zip (Join-Path $Root "bin")
        Remove-Item -Force $zip
    } catch { Fail "uv could not be downloaded. Check the internet connection." }
}
$env:UV_PYTHON_PREFERENCE = "only-managed"

# --- Ollama -----------------------------------------------------------------------
# What matters is that Ollama answers on this computer. Where its program
# lives, and whether it was just installed, varies between versions.
Say "Ollama, for the stories"
$Ollama = Find-Ollama
if (-not $Ollama -and -not (Ollama-Up)) {
    $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if ($winget) {
        & $winget.Source install --id Ollama.Ollama -e --silent --accept-source-agreements --accept-package-agreements
    }
    $Ollama = Find-Ollama
    if (-not $Ollama -and -not (Wait-Ollama 5)) {
        Write-Host "Using Ollama's own installer."
        $setup = Join-Path $env:TEMP "OllamaSetup.exe"
        try { Download "https://ollama.com/download/OllamaSetup.exe" $setup } catch { Fail "Ollama could not be downloaded. Check the internet connection." }
        # Wait for the installer only, not for the Ollama app it opens at the end.
        $proc = Start-Process $setup -ArgumentList "/VERYSILENT", "/NORESTART", "/SUPPRESSMSGBOXES" -PassThru
        $proc.WaitForExit()
        for ($i = 0; $i -lt 30 -and -not $Ollama; $i++) { Start-Sleep 1; $Ollama = Find-Ollama }
    }
}
if (-not (Ollama-Up) -and $Ollama) {
    Start-Process -WindowStyle Hidden $Ollama -ArgumentList "serve"
}
if (-not (Wait-Ollama 60)) {
    Write-Host ""
    Write-Host "Ollama is installed but has not started yet." -ForegroundColor Yellow
    Write-Host "If an Ollama window is open, finish its steps. Otherwise open Ollama from the Start menu once."
    Write-Host "Then open Install Moo Local.cmd again: it carries on from here."
    Fail "Ollama is not running yet."
}
Write-Host "Ollama is running."

# --- Python libraries ---------------------------------------------------------------
Say "Python libraries (several minutes the first time)"
$Py = Join-Path $Venv "Scripts\python.exe"
if (-not (Test-Path $Py)) { & $Uv venv --python $PythonRequest $Venv; if ($LASTEXITCODE -ne 0) { Fail "Python 3.12 could not be set up." } }
& $Uv pip install --python $Py -r (Join-Path $App "requirements-base.txt")
if ($LASTEXITCODE -ne 0) { Fail "the Python libraries could not be installed." }

Say "Kitten TTS 2 (its own libraries)"
$KPy = Join-Path $KVenv "Scripts\python.exe"
if (-not (Test-Path $KPy)) { & $Uv venv --seed --python $PythonRequest $KVenv }
if (Test-Path $KPy) { & $Uv pip install --python $KPy -r (Join-Path $App "requirements-kitten2.txt") }
if ($LASTEXITCODE -ne 0) { Write-Host "Kitten TTS 2 could not be installed; Moo Local works without it." }

# --- The firewall ----------------------------------------------------------------
# The Moo calls Moo Local on port 8765 and finds it by mDNS (5353). One rule,
# for this home network only, made with an administrator "Yes".
$PyW = Join-Path $Venv "Scripts\pythonw.exe"
$haveRule = $false
try { $haveRule = [bool](Get-NetFirewallRule -DisplayName "Moo Local" -ErrorAction SilentlyContinue) } catch {}
if (-not $haveRule) {
    Say "Letting the Moo reach Moo Local (Windows asks for a Yes)"
    $rule = "New-NetFirewallRule -DisplayName 'Moo Local' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8765 -RemoteAddress LocalSubnet -Profile Any | Out-Null; " +
            "New-NetFirewallRule -DisplayName 'Moo Local' -Direction Inbound -Action Allow -Protocol UDP -LocalPort 5353 -RemoteAddress LocalSubnet -Profile Any | Out-Null"
    try { Start-Process powershell -Verb RunAs -Wait -WindowStyle Hidden -ArgumentList "-NoProfile", "-Command", $rule }
    catch { Write-Host "Without the Yes, Windows may ask again when Moo Local starts: choose Allow." }
}

# --- Start now and at every login ---------------------------------------------------
Say "Starting Moo Local"
Get-CimInstance Win32_Process -Filter "Name like 'python%'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*moo_local.py*" -and $_.CommandLine -notlike "*--prepare*" } |
    ForEach-Object { Stop-Process -Force -Id $_.ProcessId -ErrorAction SilentlyContinue }
$Log = Join-Path $Root "server.log"
$LaunchArgs = "`"$App\moo_local.py`" --log `"$Log`""
$startup = [Environment]::GetFolderPath("Startup")
$shell = New-Object -ComObject WScript.Shell
$lnk = $shell.CreateShortcut((Join-Path $startup "Moo Local.lnk"))
$lnk.TargetPath = $PyW
$lnk.Arguments = $LaunchArgs
$lnk.WorkingDirectory = $App
$lnk.Description = "Moo Local, the Moo's AI on this computer"
$lnk.Save()
Start-Process -WorkingDirectory $App $PyW -ArgumentList $LaunchArgs

$up = $false
for ($i = 0; $i -lt 60 -and -not $up; $i++) {
    try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 "http://127.0.0.1:8765/health" | Out-Null; $up = $true } catch { Start-Sleep 1 }
}
if (-not $up) { Fail "Moo Local did not start. See $Log" }
Write-Host ""
Write-Host "Moo Local is running on this computer, and starts by itself at every login." -ForegroundColor Green
Write-Host "On the Moo website, open AI engine: this computer appears under Local machines."
Write-Host "Choose it there, then pick the story, listening and voice models to download under Models."
Write-Host "This window can be closed."
try { Stop-Transcript | Out-Null } catch {}
