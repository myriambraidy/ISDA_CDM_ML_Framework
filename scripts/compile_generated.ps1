param(
    [Parameter(Mandatory = $true)]
    [string]$ClassName,
    [string]$RepoRoot = "",
    [switch]$Run,
    [string]$OutJson
)

$ErrorActionPreference = "Stop"

if (-not $RepoRoot) {
    $scriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
    $RepoRoot = (Resolve-Path (Join-Path $scriptDir "..")).Path
}

$GeneratedDir = Join-Path $RepoRoot "rosetta-validator\generated"
$JavaFile = Join-Path $GeneratedDir "$ClassName.java"
$JarPath = Join-Path $RepoRoot "rosetta-validator\target\rosetta-validator-1.0.0.jar"
$Classpath = "$JarPath;$GeneratedDir"

if (-not (Get-Command javac -ErrorAction SilentlyContinue)) {
    throw "javac not found on PATH."
}
if (-not (Get-Command java -ErrorAction SilentlyContinue)) {
    throw "java not found on PATH."
}
if (-not (Test-Path $JarPath)) {
    throw "Required jar missing: $JarPath. Run: cd rosetta-validator; mvn package -DskipTests"
}
if (-not (Test-Path $JavaFile)) {
    throw "Java source not found: $JavaFile"
}

Write-Host "Compiling: $JavaFile"
javac -cp $Classpath -d $GeneratedDir $JavaFile
if ($LASTEXITCODE -ne 0) {
    throw "javac failed for $JavaFile (exit code $LASTEXITCODE)."
}
Write-Host "Compile OK: $ClassName.java"

if ($Run) {
    Write-Host "Running: $ClassName"
    if ($OutJson) {
        $output = java -cp $Classpath $ClassName
        if ($LASTEXITCODE -ne 0) {
            throw "java failed for class $ClassName (exit code $LASTEXITCODE)."
        }
        $output | Set-Content -Encoding utf8 $OutJson
        Write-Host "Run OK. Output written to: $OutJson"
    }
    else {
        java -cp $Classpath $ClassName
        if ($LASTEXITCODE -ne 0) {
            throw "java failed for class $ClassName (exit code $LASTEXITCODE)."
        }
    }
}
