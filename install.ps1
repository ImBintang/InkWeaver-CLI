# InkWeaver 一键安装脚本（Windows / PowerShell 7+）
# 用法：
#   ./install.ps1                 # 默认：装依赖 + 本包，提示配置
#   ./install.ps1 -Host qoder     # 同时写入 Qoder 的 mcp.json（claude|cursor|none）
#   ./install.ps1 -Mirror         # pip 走清华镜像（国内网络）
#   ./install.ps1 -Test           # 装完后做 stdio 握手验证（list_tools 打印工具数）
# 幂等：重复执行安全（.venv 存在则复用）

param(
    [string]$Host = "none",        # qoder | claude | cursor | none
    [switch]$Mirror,               # 使用清华 PyPI 镜像
    [switch]$Test                  # 握手验证
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Venv = Join-Path $Root ".venv"

Write-Host "== InkWeaver v7.2.0 一键安装 ==" -ForegroundColor Cyan

# 1) 检测 Python（优先 python，回退 py）
$Py = $null
foreach ($cand in @("python", "py")) {
    try {
        $v = & $cand -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
        if ($LASTEXITCODE -eq 0 -and $v -match "^\d+\.\d+$") {
            $Py = $cand
            break
        }
    } catch { }
}
if (-not $Py) {
    Write-Host "未检测到 Python。请先安装 Python 3.10+（https://www.python.org/downloads/）" -ForegroundColor Red
    exit 1
}
$PyVer = & $Py -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ([int]$PyVer.Split('.')[0] -lt 3 -or ([int]$PyVer.Split('.')[0] -eq 3 -and [int]$PyVer.Split('.')[1] -lt 10)) {
    Write-Host "Python 版本过低：$PyVer（需要 3.10+）。可用 uv 安装：uv python install 3.11" -ForegroundColor Red
    exit 1
}
Write-Host "[1/5] Python $PyVer 检测通过"

# 2) 创建虚拟环境（幂等）
if (-not (Test-Path (Join-Path $Venv "Scripts\python.exe"))) {
    Write-Host "[2/5] 创建虚拟环境 .venv ..."
    & $Py -m venv $Venv
    if ($LASTEXITCODE -ne 0) { Write-Host "venv 创建失败" -ForegroundColor Red; exit 1 }
} else {
    Write-Host "[2/5] .venv 已存在，复用"
}
$VenvPy = Join-Path $Venv "Scripts\python.exe"
$VenvPip = Join-Path $Venv "Scripts\pip.exe"

# 3) 安装依赖（uv 优先，失败回退 pip；-Mirror 走清华镜像）
Write-Host "[3/5] 安装依赖 ..."
$PipIndex = ""
if ($Mirror) { $PipIndex = "-i https://pypi.tuna.tsinghua.edu.cn/simple" }
$Uv = Get-Command uv -ErrorAction SilentlyContinue
if ($Uv) {
    & uv pip install --python $VenvPy -r (Join-Path $Root "requirements.txt") $PipIndex
    if ($LASTEXITCODE -ne 0) {
        Write-Host "uv 安装失败，回退 pip ..." -ForegroundColor Yellow
        & $VenvPip install $PipIndex -r (Join-Path $Root "requirements.txt")
    }
} else {
    & $VenvPip install $PipIndex -r (Join-Path $Root "requirements.txt")
}
if ($LASTEXITCODE -ne 0) { Write-Host "依赖安装失败，请检查网络或改用 -Mirror" -ForegroundColor Red; exit 1 }

# 4) 安装本包（验证打包修复）
Write-Host "[4/5] 安装 inkweaver 本包（pip install -e .）..."
& $VenvPip install -e $Root $PipIndex
if ($LASTEXITCODE -ne 0) {
    Write-Host "本包安装失败（可能是 setuptools 打包配置问题）。请带上完整报错到 Issue 反馈" -ForegroundColor Red
    exit 1
}

# 5) 写入宿主 MCP 配置
if ($Host -ne "none") {
    Write-Host "[5/5] 写入 $Host 的 MCP 配置 ..."
    $PyAbs = (Resolve-Path $VenvPy).Path
    switch ($Host.ToLower()) {
        "qoder" {
            $CfgDir = Join-Path $env:APPDATA "QoderCN\SharedClientCache"
            $CfgPath = Join-Path $CfgDir "mcp.json"
        }
        "claude" {
            $CfgDir = Join-Path $env:APPDATA "Claude"
            $CfgPath = Join-Path $CfgDir "claude_desktop_config.json"
        }
        "cursor" {
            $CfgDir = Join-Path $env:APPDATA "Cursor"
            $CfgPath = Join-Path $CfgDir "mcp.json"
        }
        default {
            Write-Host "未知宿主：$Host（可选 qoder|claude|cursor|none）" -ForegroundColor Red
            exit 1
        }
    }
    if (-not (Test-Path $CfgDir)) { New-Item -ItemType Directory -Path $CfgDir -Force | Out-Null }

    $Cfg = @{ mcpServers = @{ } }
    if (Test-Path $CfgPath) {
        $Cfg = Get-Content $CfgPath -Raw | ConvertFrom-Json
        if ($Cfg.mcpServers.inkweaver) {
            Copy-Item $CfgPath "$CfgPath.bak" -Force
            Write-Host "已存在 inkweaver 条目，备份为 $CfgPath.bak 后覆盖" -ForegroundColor Yellow
        }
    }
    $Cfg.mcpServers.inkweaver = @{
        command = $PyAbs
        args    = @("main.py", "mcp")
        cwd     = $Root
    }
    $Cfg | ConvertTo-Json -Depth 5 | Set-Content $CfgPath -Encoding UTF8
    Write-Host "已写入 $CfgPath（command=$PyAbs, cwd=$Root）"
} else {
    Write-Host "[5/5] 跳过宿主配置写入（-Host none）"
}

# 6) 配置模板提示
$CfgYaml = Join-Path $Root ".env\config.yaml"
if (-not (Test-Path $CfgYaml)) {
    Write-Host "提示：请复制 config.example.yaml 到 .env\config.yaml（外部编排模式可留 api_key 占位）" -ForegroundColor Yellow
}

# 7) 握手验证
if ($Test) {
    Write-Host "== 握手验证：启动 stdio server 并 list_tools ==" -ForegroundColor Cyan
    $env:INKWEAVER_TEST = "1"
    & $VenvPy (Join-Path $Root "main.py") mcp 2>$null | Out-Null
    $Probe = @"
import subprocess, sys, json
p = subprocess.Popen([sys.executable, "main.py", "mcp"], cwd=r"$Root",
                     stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                     stderr=subprocess.DEVNULL, text=True)
def send(obj):
    p.stdin.write(json.dumps(obj) + "\n"); p.stdin.flush()
send({"jsonrpc":"2.0","id":1,"method":"initialize","params":{
    "protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"probe","version":"1"}}})
send({"jsonrpc":"2.0","method":"notifications/initialized","params":{}})
send({"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}})
out = []
while True:
    line = p.stdout.readline()
    if not line: break
    obj = json.loads(line)
    if obj.get("id") == 1 and "result" in obj: out.append("initialize OK")
    if obj.get("id") == 2:
        tools = obj["result"]["tools"]; out.append(f"tools: {len(tools)}"); break
print(" | ".join(out))
"@
    $ProbeFile = Join-Path $env:TEMP "inkweaver_probe.py"
    Set-Content $ProbeFile $Probe -Encoding UTF8
    $Result = & $VenvPy $ProbeFile
    Write-Host $Result -ForegroundColor Green
    Remove-Item $ProbeFile -Force
}

Write-Host "== 安装完成。下一步：复制 config.example.yaml → .env\config.yaml 后接入 AI 工作台 ==" -ForegroundColor Green
