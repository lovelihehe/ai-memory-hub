# AI Memory Hub - Windows 一键安装脚本
#
# 功能：检查 Python 版本 → 创建虚拟环境 → 安装依赖 → 初始化
#
# 用法：
#   # 推荐：远程下载并执行（自动下载最新脚本）
#   irm https://raw.githubusercontent.com/lovelihehe/ai-memory-hub/main/scripts/install.ps1 | iex
#
#   # 本地执行（克隆仓库后）
#   powershell -ExecutionPolicy Bypass -File scripts\install.ps1
#
#   # 跳过 Python 版本检查（已装但路径不在 PATH 时）
#   powershell -ExecutionPolicy Bypass -File scripts\install.ps1 -SkipPythonCheck
#
#   # 跳过 ai-memory init（仅安装不初始化）
#   powershell -ExecutionPolicy Bypass -File scripts\install.ps1 -SkipInit

param(
    [switch]$SkipPythonCheck,
    [switch]$SkipInit
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

# ── 输出工具 ────────────────────────────────────────────────────────────────

function Write-Step {
    param([string]$Message)
    Write-Host "==>" -ForegroundColor Green -NoNewline
    Write-Host " $Message"
}

function Write-Warn {
    param([string]$Message)
    Write-Host "WARNING:" -ForegroundColor Yellow -NoNewline
    Write-Host " $Message"
}

function Write-Err {
    param([string]$Message)
    Write-Host "ERROR:" -ForegroundColor Red -NoNewline
    Write-Host " $Message"
}

# ── 步骤 1：检查 Python ─────────────────────────────────────────────────────

function Test-Python {
    if (-not $SkipPythonCheck) {
        Write-Step "Checking Python version..."

        $pythonCmd = Get-Command python -ErrorAction SilentlyContinue
        if (-not $pythonCmd) {
            Write-Err "Python is not installed. Download from https://python.org (requires 3.11+)"
            exit 1
        }

        $version = python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
        Write-Host "  Found Python $version"

        if ([version]$version -lt [version]"3.11") {
            Write-Err "Python 3.11+ is required. Found Python $version"
            exit 1
        }
    }
}

# ── 步骤 2：创建虚拟环境 ───────────────────────────────────────────────────

function New-VirtualEnv {
    Write-Step "Creating virtual environment..."

    if (Test-Path ".venv") {
        Write-Warn "Virtual environment already exists. Skipping..."
    } else {
        python -m venv .venv
        Write-Host "  Created .venv"
    }
}

# ── 步骤 3：激活虚拟环境 ───────────────────────────────────────────────────

function Enable-Venv {
    Write-Step "Activating virtual environment..."
    & .\.venv\Scripts\Activate.ps1
    Write-Host "  Activated"
}

# ── 步骤 4：升级 pip ───────────────────────────────────────────────────────

function Update-Pip {
    Write-Step "Upgrading pip..."
    pip install --upgrade pip | Out-Null
}

# ── 步骤 5：安装包 ─────────────────────────────────────────────────────────

function Install-Package {
    Write-Step "Installing AI Memory Hub..."
    pip install -e .

    $installed = Get-Command ai-memory -ErrorAction SilentlyContinue
    if ($installed) {
        Write-Host "  Installed successfully"
    }
}

# ── 步骤 6：初始化 ─────────────────────────────────────────────────────────

function Initialize-MemoryHub {
    if (-not $SkipInit) {
        Write-Step "Initializing AI Memory Hub..."
        ai-memory init
        Write-Host "  Initialized"
    }
}

# ── 主入口 ─────────────────────────────────────────────────────────────────

function Main {
    Write-Host ""
    Write-Host "==========================================" -ForegroundColor Cyan
    Write-Host "  AI Memory Hub Installation (Windows)"
    Write-Host "==========================================" -ForegroundColor Cyan
    Write-Host ""

    Test-Python
    New-VirtualEnv
    Enable-Venv
    Update-Pip
    Install-Package
    Initialize-MemoryHub

    Write-Host ""
    Write-Host "==========================================" -ForegroundColor Green
    Write-Host "  Installation Complete!"
    Write-Host "==========================================" -ForegroundColor Green
    Write-Host ""
    Write-Host "Next steps:"
    Write-Host "  1. Configure your AI tools in ~/.ai-memory/config.json"
    Write-Host "  2. Run: ai-memory pipeline"
    Write-Host "  3. Run: ai-memory doctor"
    Write-Host ""
    Write-Host "For more information, see: https://github.com/lovelihehe/ai-memory-hub"
    Write-Host ""
}

Main
