param(
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [switch]$BuildJar
)

$ErrorActionPreference = "Stop"

function Test-CommandExists {
    param([Parameter(Mandatory = $true)][string]$Name)
    return $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

Write-Host "Repo root: $RepoRoot"

if (-not (Test-CommandExists "java")) {
    throw "java is not on PATH. Install JDK 11+ and ensure JAVA_HOME/bin is in PATH."
}
if (-not (Test-CommandExists "javac")) {
    throw "javac is not on PATH. Install JDK (not JRE) and ensure JAVA_HOME/bin is in PATH."
}
if (-not (Test-CommandExists "mvn")) {
    throw "mvn is not on PATH. Install Maven and add Maven bin to PATH."
}

Write-Host ""
Write-Host "== Tool versions =="
java -version
javac -version
mvn -version

$JarPath = Join-Path $RepoRoot "rosetta-validator\target\rosetta-validator-1.0.0.jar"

if ($BuildJar) {
    Write-Host ""
    Write-Host "== Building rosetta-validator jar =="
    Push-Location (Join-Path $RepoRoot "rosetta-validator")
    try {
        mvn package -DskipTests
    }
    finally {
        Pop-Location
    }
}

Write-Host ""
Write-Host "== Jar check =="
if (Test-Path $JarPath) {
    $jar = Get-Item $JarPath
    Write-Host "OK: $($jar.FullName)"
    Write-Host "Size: $([math]::Round($jar.Length / 1MB, 2)) MB"
    Write-Host "LastWriteTime: $($jar.LastWriteTime)"
}
else {
    Write-Host "MISSING: $JarPath"
    Write-Host "Build it with:"
    Write-Host "  cd rosetta-validator"
    Write-Host "  mvn package -DskipTests"
    exit 2
}

Write-Host ""
Write-Host "Environment check complete."
