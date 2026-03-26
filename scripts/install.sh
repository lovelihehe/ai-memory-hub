#!/usr/bin/env bash
# AI Memory Hub - Unix/macOS 安装脚本
#
# 功能：检查 Python 版本 → 创建虚拟环境 → 安装依赖 → 初始化
#
# 用法：
#   # 推荐：远程下载并执行（自动下载最新脚本）
#   curl -sSL https://raw.githubusercontent.com/lovelihehe/ai-memory-hub/main/scripts/install.sh | bash
#
#   # 本地执行（克隆仓库后）
#   bash scripts/install.sh
#
#   # 跳过初始化（仅安装不初始化）
#   SKIP_INIT=1 bash scripts/install.sh

set -euo pipefail

# ── 颜色输出 ─────────────────────────────────────────────────────────────────

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo_step() { echo -e "${GREEN}==>${NC} $1"; }
echo_warn() { echo -e "${YELLOW}WARNING:${NC} $1"; }
echo_error() { echo -e "${RED}ERROR:${NC} $1"; }

# ── 步骤 1：检查 Python ──────────────────────────────────────────────────────

check_python() {
    echo_step "Checking Python version..."
    if ! command -v python3 &> /dev/null; then
        echo_error "Python 3 is not installed. Please install Python 3.11+ from https://python.org"
        exit 1
    fi

    PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    REQUIRED_VERSION="3.11"

    if [ "$(printf '%s\n' "$REQUIRED_VERSION" "$PYTHON_VERSION" | sort -V | head -n1)" != "$REQUIRED_VERSION" ]; then
        echo_error "Python $REQUIRED_VERSION+ is required. Found Python $PYTHON_VERSION"
        exit 1
    fi
    echo "  Found Python $PYTHON_VERSION"
}

# ── 步骤 2：创建虚拟环境 ─────────────────────────────────────────────────────

create_venv() {
    echo_step "Creating virtual environment..."
    if [ -d ".venv" ]; then
        echo_warn "Virtual environment already exists. Skipping..."
    else
        python3 -m venv .venv
        echo "  Created .venv"
    fi
}

# ── 步骤 3：激活虚拟环境 ─────────────────────────────────────────────────────

activate_venv() {
    echo_step "Activating virtual environment..."
    source .venv/bin/activate
    echo "  Activated"
}

# ── 步骤 4：升级 pip ────────────────────────────────────────────────────────

upgrade_pip() {
    echo_step "Upgrading pip..."
    pip install --upgrade pip
}

# ── 步骤 5：安装包 ──────────────────────────────────────────────────────────

install_package() {
    echo_step "Installing AI Memory Hub..."
    pip install -e .

    if command -v ai-memory &> /dev/null; then
        echo "  Installed successfully: $(which ai-memory)"
    fi
}

# ── 步骤 6：初始化 ──────────────────────────────────────────────────────────

initialize() {
    if [ "${SKIP_INIT:-0}" -eq 1 ]; then
        return
    fi
    echo_step "Initializing AI Memory Hub..."
    ai-memory init
    echo "  Initialized"
}

# ── 主入口 ──────────────────────────────────────────────────────────────────

main() {
    echo ""
    echo "=========================================="
    echo "  AI Memory Hub Installation"
    echo "=========================================="
    echo ""

    # 检测操作系统（仅用于提示，不做包管理操作）
    OS="$(uname -s)"
    echo "  Detected OS: ${OS}"

    check_python
    create_venv
    activate_venv
    upgrade_pip
    install_package
    initialize

    echo ""
    echo "=========================================="
    echo "  Installation Complete!"
    echo "=========================================="
    echo ""
    echo "Next steps:"
    echo "  1. Configure your AI tools in ~/.ai-memory/config.json"
    echo "  2. Run: ai-memory pipeline"
    echo "  3. Run: ai-memory doctor"
    echo ""
    echo "For more information, see: https://github.com/lovelihehe/ai-memory-hub"
    echo ""
}

main "$@"
