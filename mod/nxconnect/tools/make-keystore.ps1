# 生成本地签名密钥
#
# 用法：
#   pwsh tools/make-keystore.ps1
#
# 生成两个文件（都在 .gitignore 里，**不要提交**）：
#   NXConnect/nxconnect-release.jks     密钥库
#   NXConnect/keystore.properties       口令（构建脚本读它）
#
# ⚠️ 这两个文件必须一起备份。丢失后无法再为已安装的用户发布更新 ——
#    Android 要求更新的 APK 用**同一个密钥**签名，否则只能让用户卸载重装。
#
# ⚠️ 不要用弱口令。这个项目曾经把口令硬编码为 "nxconnect" 并连同密钥库一起
#    放在仓库里；开源后那等于公开签名身份，任何人都能签 APK 冒充本应用。

$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path (Join-Path $root 'NXConnect'))) {
    $root = $PSScriptRoot   # 兼容脚本被复制到别处的场景
}
$proj = Join-Path $root 'NXConnect'
$jks = Join-Path $proj 'nxconnect-release.jks'
$propsPath = Join-Path $proj 'keystore.properties'

Write-Host '=== NXConnectForNX500 签名密钥生成 ===' -ForegroundColor Cyan
Write-Host ''

# ---- 1. 找 keytool ----
$keytool = $null
foreach ($candidate in @(
        (Join-Path $root '_host\jdk\bin\keytool.exe'),
        (Join-Path $env:JAVA_HOME 'bin\keytool.exe')
    )) {
    if ($candidate -and (Test-Path $candidate)) { $keytool = $candidate; break }
}
if (-not $keytool) {
    $cmd = Get-Command keytool -ErrorAction SilentlyContinue
    if ($cmd) { $keytool = $cmd.Source }
}
if (-not $keytool) {
    Write-Host '❌ 找不到 keytool。请安装 JDK 17+，或设置 JAVA_HOME。' -ForegroundColor Red
    exit 1
}
Write-Host "keytool: $keytool"
Write-Host ''

# ---- 2. 已有密钥时确认覆盖 ----
if (Test-Path $jks) {
    Write-Host "⚠️  已存在 $jks" -ForegroundColor Yellow
    Write-Host '    覆盖后，用旧密钥签名的 APK 将无法升级安装（需卸载重装）。'
    $answer = Read-Host '    确定覆盖？输入 yes 继续'
    if ($answer -ne 'yes') { Write-Host '已取消。'; exit 0 }
}

# ---- 3. 生成强随机口令 ----
# ★ 注意：不要用 [RandomNumberGenerator]::Fill() —— 它在部分 PowerShell/.NET 版本上
#   不存在，会静默失败，导致 $pw 为 $null，最终生成一个空口令的密钥库。
#   这里显式 try/catch 并在生成后校验长度。
$pw = $null
try {
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    $bytes = New-Object byte[] 32
    $rng.GetBytes($bytes)
    $rng.Dispose()
    $pw = [Convert]::ToBase64String($bytes) -replace '[+/=]', ''
} catch {
    Write-Host '  随机数 API 不可用，改用 GUID 混合回退' -ForegroundColor Yellow
    $seed = ([guid]::NewGuid().ToString() + [guid]::NewGuid().ToString() +
        (Get-Random -Minimum 0 -Maximum 2147483647) + (Get-Date).Ticks)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    $pw = [Convert]::ToBase64String(
        $sha.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($seed))) -replace '[+/=]', ''
}
if ([string]::IsNullOrWhiteSpace($pw) -or $pw.Length -lt 32) {
    Write-Host '❌ 口令生成失败或过短，已中止（不会写出弱口令的密钥库）。' -ForegroundColor Red
    exit 1
}
Write-Host "已生成 $($pw.Length) 字符的随机口令。"
Write-Host ''

# ---- 4. 生成密钥 ----
Write-Host '正在生成 RSA 4096 密钥（有效期 10000 天）…'
Remove-Item $jks -Force -ErrorAction SilentlyContinue
& $keytool -genkeypair -v `
    -keystore $jks -alias nxconnect `
    -keyalg RSA -keysize 4096 -validity 10000 `
    -storepass $pw -keypass $pw `
    -dname 'CN=NXConnectForNX500, OU=NXConnectForNX500, O=NXConnectForNX500, L=Unknown, ST=Unknown, C=CN'
if (-not (Test-Path $jks)) {
    Write-Host '❌ 密钥生成失败。' -ForegroundColor Red
    exit 1
}
Write-Host ''

# ---- 5. 写 keystore.properties ----
$props = @"
# 本地签名配置 —— **不要提交到仓库**
#
# 与 nxconnect-release.jks 一起备份。丢失后无法为已安装用户发布更新。
# 由 tools/make-keystore.ps1 生成。
storeFile=nxconnect-release.jks
storePassword=$pw
keyAlias=nxconnect
keyPassword=$pw
"@
[System.IO.File]::WriteAllText($propsPath, $props, (New-Object System.Text.UTF8Encoding($false)))

Write-Host '=== 完成 ===' -ForegroundColor Green
Write-Host ''
Write-Host '  已写入:'
Write-Host "    $jks"
Write-Host "    $propsPath"
Write-Host ''
Write-Host '  ⚠️  请立刻把下面的口令保存到密码管理器，并备份上面两个文件：' -ForegroundColor Yellow
Write-Host ''
Write-Host "      $pw" -ForegroundColor White
Write-Host ''
Write-Host '  这两个文件已被 .gitignore 排除，不会进入版本库。'
