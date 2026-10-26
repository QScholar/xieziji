# Deployment rollback without tar snapshots.
#
# Every deployed file lives in this Git work tree, so a failed deployment is
# undone by restoring the committed bytes of exactly the files it touched.
# Nothing is copied aside before deploying: the previous state is HEAD.
$ErrorActionPreference = 'Stop'

function Get-DeployedPath {
    param([Parameter(Mandatory)][string]$Root, [Parameter(Mandatory)][string[]]$Relative,
          [string]$Base)
    # Resolve release-relative paths and refuse anything outside the repository.
    # `-Base` is where the names are relative to (the release directory); without
    # it the repository root is used.
    $base = if ($Base) { $Base } else { $Root }
    $resolved = foreach ($item in $Relative) {
        $full = [IO.Path]::GetFullPath((Join-Path $base $item))
        if (-not $full.StartsWith($Root + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
            throw ('Path escapes the repository: ' + $item)
        }
        $full
    }
    return @($resolved)
}

function Get-GitPath {
    param([Parameter(Mandatory)][string]$Root, [Parameter(Mandatory)][string[]]$Full)
    # `git` resolves pathspecs from the repository root and prints it with forward
    # slashes, while the callers pass release-relative names, so translate both.
    $top = (& git -C $Root rev-parse --show-toplevel)
    if ($LASTEXITCODE -ne 0 -or -not $top) { throw 'git rev-parse failed; is this a Git work tree?' }
    $top = [IO.Path]::GetFullPath($top.Trim().Replace('/', [IO.Path]::DirectorySeparatorChar))
    $prefix = $top.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    return @($Full | ForEach-Object {
        if (-not $_.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) { throw ('Path outside the work tree: ' + $_) }
        # git accepts either separator, but a stable forward-slash pathspec is portable.
        $_.Substring($prefix.Length).Replace([IO.Path]::DirectorySeparatorChar, '/')
    })
}

function Assert-CleanSource {
    param([Parameter(Mandatory)][string]$Root, [Parameter(Mandatory)][string[]]$Relative, [string]$Base)
    # A dirty source file would be silently reverted by the rollback, hiding local work.
    $paths = Get-DeployedPath -Root $Root -Relative $Relative -Base $Base
    $output = & git -C $Root status --porcelain -- @(Get-GitPath -Root $Root -Full $paths)
    if ($LASTEXITCODE -ne 0) { throw 'git status failed; is this a Git work tree?' }
    if ($output) { throw ('Uncommitted changes in deployed files; commit before deploying: ' + ($output -join '; ')) }
    return $paths
}

function Restore-CommittedFiles {
    param([Parameter(Mandatory)][string]$Root, [Parameter(Mandatory)][string[]]$Relative, [string]$Base)
    # Rollback = discard the deployed edits; HEAD is the state that was live before.
    $paths = Get-DeployedPath -Root $Root -Relative $Relative -Base $Base
    $gitPaths = Get-GitPath -Root $Root -Full $paths
    & git -C $Root checkout HEAD -- @($gitPaths)
    if ($LASTEXITCODE -ne 0) { throw 'Rollback failed: git checkout returned an error.' }
    $left = & git -C $Root status --porcelain -- @($gitPaths)
    if ($left) { throw ('Rollback left modified files: ' + ($left -join '; ')) }
}
