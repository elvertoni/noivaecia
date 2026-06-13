param(
    [string]$SourceMdb = 'brcom\brcom.mdb',
    [string]$OutputDir = 'var\legacy_export'
)

$ErrorActionPreference = 'Stop'
$ExporterVersion = '2026.06.12-r1'

if ([Environment]::Is64BitProcess) {
    $ps32 = "$env:WINDIR\SysWOW64\WindowsPowerShell\v1.0\powershell.exe"
    if (-not (Test-Path $ps32)) {
        throw '32-bit Windows PowerShell was not found. Jet 4.0 is required for this Access 97/Jet database.'
    }

    & $ps32 -NoProfile -ExecutionPolicy Bypass -File $PSCommandPath `
        -SourceMdb $SourceMdb `
        -OutputDir $OutputDir
    exit $LASTEXITCODE
}

function Convert-LegacyValue($value) {
    if ($null -eq $value -or $value -is [DBNull]) {
        return $null
    }
    if ($value -is [datetime]) {
        return $value.ToString('yyyy-MM-ddTHH:mm:ss', [Globalization.CultureInfo]::InvariantCulture)
    }
    if ($value -is [bool]) {
        if ($value) { return 1 }
        return 0
    }
    return $value
}

function Write-JsonFile($path, $object) {
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    $json = $object | ConvertTo-Json -Depth 10
    [IO.File]::WriteAllText($path, $json, $utf8NoBom)
}

function Reset-ExportSubdir($rootPath, $subdirName) {
    $rootFullPath = [IO.Path]::GetFullPath($rootPath)
    $targetPath = [IO.Path]::GetFullPath((Join-Path $rootFullPath $subdirName))
    $expectedPrefix = $rootFullPath.TrimEnd('\') + '\'

    if (-not $targetPath.StartsWith($expectedPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to reset export path outside output directory: $targetPath"
    }

    if (Test-Path -LiteralPath $targetPath) {
        Remove-Item -LiteralPath $targetPath -Recurse -Force
    }
    New-Item -ItemType Directory -Force -Path $targetPath | Out-Null
    return $targetPath
}

if (-not (Test-Path -LiteralPath $SourceMdb)) {
    throw "Source MDB not found: $SourceMdb"
}

$sourcePath = (Resolve-Path $SourceMdb).Path
$sourceItem = Get-Item -LiteralPath $sourcePath
$sourceHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $sourcePath).Hash
$outputPath = Join-Path (Get-Location) $OutputDir
$schemaDir = Join-Path $outputPath 'schema'
$dataDir = Join-Path $outputPath 'data'

$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$connection = New-Object -ComObject ADODB.Connection
$connection.Open("Provider=Microsoft.Jet.OLEDB.4.0;Data Source=$sourcePath;")

try {
    New-Item -ItemType Directory -Force -Path $outputPath | Out-Null
    $schemaDir = Reset-ExportSubdir $outputPath 'schema'
    $dataDir = Reset-ExportSubdir $outputPath 'data'

    $tables = @()
    $tablesRs = $connection.OpenSchema(20)
    while (-not $tablesRs.EOF) {
        $tableName = [string]$tablesRs.Fields.Item('TABLE_NAME').Value
        $tableType = [string]$tablesRs.Fields.Item('TABLE_TYPE').Value
        if ($tableType -eq 'TABLE' -and -not $tableName.StartsWith('MSys')) {
            $tables += $tableName
        }
        $tablesRs.MoveNext()
    }
    $tablesRs.Close()

    $manifestTables = @()
    foreach ($tableName in ($tables | Sort-Object)) {
        Write-Host "Exporting $tableName"

        $columns = @()
        $schemaRs = New-Object -ComObject ADODB.Recordset
        $schemaRs.Open("SELECT * FROM [$tableName] WHERE 1 = 0", $connection, 0, 1)
        for ($i = 0; $i -lt $schemaRs.Fields.Count; $i++) {
            $field = $schemaRs.Fields.Item($i)
            $columns += [ordered]@{
                name = [string]$field.Name
                data_type = $field.Type
                max_length = $field.DefinedSize
                nullable = $null
            }
        }
        $schemaRs.Close()

        $rowCount = 0
        $dataPath = Join-Path $dataDir "$tableName.jsonl"
        $writer = New-Object IO.StreamWriter($dataPath, $false, $utf8NoBom)
        try {
            $rs = New-Object -ComObject ADODB.Recordset
            $rs.Open("SELECT * FROM [$tableName]", $connection, 0, 1)
            while (-not $rs.EOF) {
                $row = [ordered]@{}
                for ($i = 0; $i -lt $rs.Fields.Count; $i++) {
                    $field = $rs.Fields.Item($i)
                    $row[$field.Name] = Convert-LegacyValue $field.Value
                }
                $writer.WriteLine(($row | ConvertTo-Json -Compress -Depth 5))
                $rowCount += 1
                $rs.MoveNext()
            }
            $rs.Close()
        }
        finally {
            $writer.Close()
        }

        $schema = [ordered]@{
            source_mdb = $sourcePath
            table = $tableName
            exported_at = (Get-Date).ToString('o')
            row_count = $rowCount
            columns = $columns
        }
        Write-JsonFile (Join-Path $schemaDir "$tableName.json") $schema
        $manifestTables += [ordered]@{
            table = $tableName
            row_count = $rowCount
            data_file = "data/$tableName.jsonl"
            schema_file = "schema/$tableName.json"
            columns = $columns
        }
    }

    Write-JsonFile (Join-Path $outputPath 'manifest.json') ([ordered]@{
        exporter_version = $ExporterVersion
        source_mdb = $sourcePath
        source_mdb_sha256 = $sourceHash
        source_mdb_size = $sourceItem.Length
        source_mdb_last_write_time = $sourceItem.LastWriteTime.ToString('o')
        exported_at = (Get-Date).ToString('o')
        powershell_version = $PSVersionTable.PSVersion.ToString()
        is_64_bit_process = [Environment]::Is64BitProcess
        tables = $manifestTables
    })
}
finally {
    $connection.Close()
}
