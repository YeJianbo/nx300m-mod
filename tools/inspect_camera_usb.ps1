param(
    [string]$OutputFile = (Join-Path $PSScriptRoot '..\outputs\deployment\camera-usb.json')
)

$ErrorActionPreference = 'Stop'

# Enumerate Windows PnP records only. No USB commands, driver changes or device writes.
$devices = @(Get-PnpDevice -PresentOnly)
$candidates = @($devices | Where-Object {
    $_.InstanceId -match '^USB\\VID_04E8&' -or
    $_.Class -eq 'WPD' -or
    $_.FriendlyName -match '\bNX[ -]?300M?\b'
})
$records = @($candidates | ForEach-Object {
    $device = $_
    $properties = @{}
    Get-PnpDeviceProperty -InstanceId $device.InstanceId -KeyName @(
        'DEVPKEY_Device_BusReportedDeviceDesc',
        'DEVPKEY_Device_HardwareIds',
        'DEVPKEY_Device_Service',
        'DEVPKEY_Device_DriverInfPath'
    ) -ErrorAction SilentlyContinue | ForEach-Object {
        $properties[$_.KeyName] = $_.Data
    }
    [ordered]@{
        name = $device.FriendlyName
        class = $device.Class
        status = $device.Status
        instance_id = $device.InstanceId
        bus_description = $properties['DEVPKEY_Device_BusReportedDeviceDesc']
        hardware_ids = $properties['DEVPKEY_Device_HardwareIds']
        service = $properties['DEVPKEY_Device_Service']
        driver_inf = $properties['DEVPKEY_Device_DriverInfPath']
        nx_name_match = ($device.FriendlyName -match '\bNX[ -]?300M?\b' -or
            [string]$properties['DEVPKEY_Device_BusReportedDeviceDesc'] -match '\bNX[ -]?300M?\b')
    }
})
$report = [ordered]@{
    observed_at = (Get-Date).ToString('o')
    scope = 'Present Windows PnP devices; no device protocol requests'
    nx_name_matches = @($records | Where-Object { $_.nx_name_match }).Count
    candidates = $records
}
$destination = [IO.Path]::GetFullPath($OutputFile)
[void][IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($destination))
[IO.File]::WriteAllText($destination, ($report | ConvertTo-Json -Depth 6), [Text.UTF8Encoding]::new($false))
Write-Output "NX-named devices: $($report.nx_name_matches); USB/WPD candidates: $($records.Count)"
Write-Output "Report: $destination"

