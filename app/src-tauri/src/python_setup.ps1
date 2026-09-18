$ErrorActionPreference = 'Stop'
# The host assigns this process to its kill-on-close Job before releasing stdin.
$null = [Console]::In.ReadLine()

$root = [IO.Path]::GetFullPath($env:UV_PYTHON_INSTALL_DIR)
$runtime = [IO.Path]::GetFullPath((Get-Location).ProviderPath)
$venv = [IO.Path]::GetFullPath($env:UV_PROJECT_ENVIRONMENT)
if ([IO.Path]::GetDirectoryName($root) -ne [IO.Path]::GetDirectoryName($runtime) -or
    [IO.Path]::GetFileName($root) -ne 'python' -or
    [IO.Path]::GetFileName($runtime) -ne 'python-runtime' -or
    $venv -ne [IO.Path]::Combine($runtime, '.venv')) {
    throw 'Python setup paths are outside the Oculus runtime layout.'
}

function Find-WorkingPython {
    # Do not use uv's minor-version junction here. uv#19622 can recreate an
    # invalid junction even after Windows has repaired it. The real, versioned
    # interpreter remains usable and is the only path passed to uv sync.
    $candidates = @(Get-ChildItem -LiteralPath $root -Directory -ErrorAction SilentlyContinue |
        Where-Object {
            $_.Name -match '^cpython-3\.12\.(\d+)-windows-(x86_64|aarch64)-none$' -and
            -not ($_.Attributes -band [IO.FileAttributes]::ReparsePoint)
        } |
        Sort-Object { [version](($_.Name -split '-')[1]) } -Descending)
    foreach ($candidate in $candidates) {
        $python = Join-Path $candidate.FullName 'python.exe'
        if (-not [IO.File]::Exists($python)) { continue }
        try {
            & $python -I -c 'import sys, encodings, venv; assert sys.version_info[:2] == (3, 12)'
            if ($LASTEXITCODE -eq 0) { return $python }
        } catch {
            # An interrupted install can leave a real but incomplete patch
            # directory. Only a successfully executed interpreter is usable.
        }
    }
    return $null
}

$python = Find-WorkingPython
if (-not $python) {
    & $env:OCULUS_BOOTSTRAP_UV python install 3.12 --no-bin --no-registry
    $installExit = $LASTEXITCODE
    $python = Find-WorkingPython
    if (-not $python) {
        throw "Python installation did not produce a working 3.12 interpreter (exit $installExit)."
    }
    if ($installExit -ne 0) {
        Write-Output 'The interpreter is valid; bypassing an unavailable uv minor-version link.'
    }
}

# An explicit patch executable avoids the broken minor junction both during
# interpreter discovery and when uv writes the virtual environment's home.
# Do not rerun `python install`: it may recreate the malformed junction.
Write-Output "Using managed Python: $python"
& $env:OCULUS_BOOTSTRAP_UV sync --frozen --no-dev --no-python-downloads --python $python
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$environmentPython = Join-Path $venv 'Scripts\python.exe'
& $environmentPython -I -c 'import sys, encodings; assert sys.version_info[:2] == (3, 12)'
if ($LASTEXITCODE -ne 0) { throw 'The prepared Python environment could not start.' }
exit 0
