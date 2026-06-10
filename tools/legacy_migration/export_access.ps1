param(
    [string]$SourceMdb = 'C:\Users\coimb\Desktop\brcom\brcom\brcom.mdb',
    [string]$OutputDir = 'var\legacy_export'
)

$ErrorActionPreference = 'Stop'

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

$sourcePath = (Resolve-Path $SourceMdb).Path
$outputPath = Join-Path (Get-Location) $OutputDir
$schemaDir = Join-Path $outputPath 'schema'
$dataDir = Join-Path $outputPath 'data'
New-Item -ItemType Directory -Force -Path $schemaDir, $dataDir | Out-Null

$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$connection = New-Object -ComObject ADODB.Connection
$connection.Open("Provider=Microsoft.Jet.OLEDB.4.0;Data Source=$sourcePath;")

try {
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
        }
    }

    Write-JsonFile (Join-Path $outputPath 'manifest.json') ([ordered]@{
        source_mdb = $sourcePath
        exported_at = (Get-Date).ToString('o')
        tables = $manifestTables
    })
}
finally {
    $connection.Close()
}
