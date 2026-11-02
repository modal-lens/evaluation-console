param(
    [string]$BackendUrl,
    [string]$ListenAddress = '0.0.0.0',
    [ValidateRange(1, 65535)]
    [int]$Port = 50112
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    throw '未找到 Node.js。请先安装 Node.js 22 或更高版本，并重新打开 PowerShell。'
}

$nodeMajor = [int](node -p "process.versions.node.split('.')[0]")
if ($nodeMajor -lt 22) {
    throw "当前 Node.js 主版本为 $nodeMajor；前端要求 Node.js 22 或更高版本。"
}

if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    throw '未找到 npm。请检查 Node.js 安装是否完整，并重新打开 PowerShell。'
}

if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'node_modules'))) {
    Write-Host '首次启动，正在安装前端依赖……'
    npm ci
    if ($LASTEXITCODE -ne 0) {
        throw "npm ci 失败，退出代码：$LASTEXITCODE"
    }
}

if ([string]::IsNullOrWhiteSpace($BackendUrl)) {
    if ([string]::IsNullOrWhiteSpace($env:BACKEND_URL)) {
        $BackendUrl = 'http://127.0.0.1:50111'
    }
    else {
        $BackendUrl = $env:BACKEND_URL
    }
}

$env:BACKEND_URL = $BackendUrl.TrimEnd('/')
$env:HOST = $ListenAddress
$env:PORT = [string]$Port
Write-Host "监听地址：${ListenAddress}:${Port}"
Write-Host "本机访问：http://127.0.0.1:${Port}"
Write-Host "远程访问：http://<服务器公网IP>:${Port}（需放通 TCP ${Port}）"
Write-Host "后端代理：$env:BACKEND_URL"
Write-Host '按 Ctrl+C 停止前端。'

npm run dev
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

