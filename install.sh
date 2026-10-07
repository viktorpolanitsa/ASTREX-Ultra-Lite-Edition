#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# ASTREX v3.0 — Installation Script
# Automated setup with hardware detection
#
#   ./install.sh                      интерактивно
#   ./install.sh --yes --mode full    без вопросов (CI, ssh без терминала)
#
# Параметры:
#   --mode minimal|standard|full|complete   (или 1-4; по умолчанию full)
#   --gpu auto|cpu|nvidia|amd|intel         PyTorch для режима complete (по умолчанию auto)
#   --yes, -y                               не задавать вопросов (ответы по умолчанию)
#   --recreate-venv                         пересоздать venv/
#   --build-gui                             собрать GUI (ui/build.sh), если есть Qt 6
#   --python /path/to/python3               интерпретатор для venv
# Переменные окружения: ASTREX_INSTALL_MODE, ASTREX_GPU, PYTHON.
# ═══════════════════════════════════════════════════════════════════════════════

set -eo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'
BOLD='\033[1m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/venv"

INSTALL_MODE="${ASTREX_INSTALL_MODE:-}"
GPU_CHOICE="${ASTREX_GPU:-auto}"
ASSUME_YES=0
RECREATE_VENV=0
BUILD_GUI=0
PYTHON_BIN="${PYTHON:-}"
FAILED_OPTIONAL=()

usage() {
    # Комментарий в начале файла (до первой строки кода)
    awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$0"
}

while [ $# -gt 0 ]; do
    case "$1" in
        --mode) INSTALL_MODE="${2:-}"; shift 2 ;;
        --mode=*) INSTALL_MODE="${1#*=}"; shift ;;
        --gpu) GPU_CHOICE="${2:-auto}"; shift 2 ;;
        --gpu=*) GPU_CHOICE="${1#*=}"; shift ;;
        --python) PYTHON_BIN="${2:-}"; shift 2 ;;
        --python=*) PYTHON_BIN="${1#*=}"; shift ;;
        -y|--yes) ASSUME_YES=1; shift ;;
        --recreate-venv) RECREATE_VENV=1; shift ;;
        --build-gui) BUILD_GUI=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1 (see --help)" >&2; exit 2 ;;
    esac
done

# Без терминала (curl | bash, ssh без -t, CI) вопросы не задаются: раньше
# "read" при set -e обрывал установку на первом же вопросе
if [ ! -t 0 ]; then
    ASSUME_YES=1
fi

echo -e "${CYAN}"
echo "╔═══════════════════════════════════════════════════════════════════════════════╗"
echo "║                                                                               ║"
echo "║     █████╗ ███████╗████████╗██████╗ ███████╗██╗  ██╗    ██╗   ██╗██████╗     ║"
echo "║    ██╔══██╗██╔════╝╚══██╔══╝██╔══██╗██╔════╝╚██╗██╔╝    ██║   ██║╚════██╗    ║"
echo "║    ███████║███████╗   ██║   ██████╔╝█████╗   ╚███╔╝     ██║   ██║ █████╔╝    ║"
echo "║    ██╔══██║╚════██║   ██║   ██╔══██╗██╔══╝   ██╔██╗     ╚██╗ ██╔╝ ╚═══██╗    ║"
echo "║    ██║  ██║███████║   ██║   ██║  ██║███████╗██╔╝ ██╗     ╚████╔╝ ██████╔╝    ║"
echo "║    ╚═╝  ╚═╝╚══════╝   ╚═╝   ╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝      ╚═══╝  ╚═════╝     ║"
echo "║                                                                               ║"
echo "║                    INTELLIGENCE SYSTEM — INSTALLER v3.0                       ║"
echo "╚═══════════════════════════════════════════════════════════════════════════════╝"
echo -e "${NC}"

# ═══════════════════════════════════════════════════════════════════════════════
# FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

log_info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1" >&2; }
log_step()  { echo -e "\n${BLUE}${BOLD}=== $1 ===${NC}\n"; }

check_command() {
    command -v "$1" >/dev/null 2>&1
}

# ask "вопрос" "ответ по умолчанию" → ответ в $REPLY
ask() {
    local prompt="$1" default="$2"
    if [ "$ASSUME_YES" -eq 1 ]; then
        REPLY="$default"
        echo "$prompt $default (auto)"
        return 0
    fi
    read -r -p "$prompt " REPLY || REPLY=""
    REPLY="${REPLY:-$default}"
}

# Обязательные пакеты: при ошибке установка прерывается
pip_required() {
    log_info "pip install $*"
    if ! "$VENV_PY" -m pip install -q "$@"; then
        log_error "Failed to install required packages: $*"
        exit 1
    fi
}

# Необязательные пакеты: ошибка не прерывает установку
pip_optional() {
    local label="$1"
    shift
    log_info "pip install $* ($label)"
    if ! "$VENV_PY" -m pip install -q "$@"; then
        log_warn "Could not install $label — the related features will be disabled"
        FAILED_OPTIONAL+=("$label")
        return 1
    fi
    return 0
}

# ═══════════════════════════════════════════════════════════════════════════════
# STEP 1: SYSTEM CHECK
# ═══════════════════════════════════════════════════════════════════════════════

log_step "Step 1/7: System Check"

if [ -z "$PYTHON_BIN" ]; then
    PYTHON_BIN="$(command -v python3 || true)"
fi
if [ -z "$PYTHON_BIN" ] || ! "$PYTHON_BIN" -c 'import sys' >/dev/null 2>&1; then
    log_error "Python 3 not found. Install Python 3.10–3.13 (e.g. sudo apt install python3 python3-venv)"
    exit 1
fi

PYTHON_VERSION=$("$PYTHON_BIN" -c 'import sys; print("%d.%d" % sys.version_info[:2])')
PYTHON_MINOR=$("$PYTHON_BIN" -c 'import sys; print(sys.version_info[1])')
PYTHON_MAJOR=$("$PYTHON_BIN" -c 'import sys; print(sys.version_info[0])')
log_info "Python: $PYTHON_BIN ($PYTHON_VERSION)"

if [ "$PYTHON_MAJOR" -ne 3 ] || [ "$PYTHON_MINOR" -lt 10 ]; then
    log_error "Python $PYTHON_VERSION is too old: ASTREX requires Python 3.10 or newer"
    exit 1
fi
if [ "$PYTHON_MINOR" -ge 14 ]; then
    log_warn "Python $PYTHON_VERSION: spaCy and some ML packages may not have wheels yet."
    log_warn "If their installation fails, ASTREX works without them (regex NLP)."
fi

if ! "$PYTHON_BIN" -m venv --help >/dev/null 2>&1 || ! "$PYTHON_BIN" -c 'import ensurepip' >/dev/null 2>&1; then
    log_error "The venv module is not available for $PYTHON_BIN."
    echo "  Debian/Ubuntu/Kali: sudo apt install python3-venv"
    echo "  Fedora:             sudo dnf install python3-virtualenv"
    exit 1
fi

OS="unknown"
if [[ "$OSTYPE" == "linux-gnu"* ]]; then
    OS="linux"
    if [ -f /etc/os-release ]; then
        # shellcheck disable=SC1091
        . /etc/os-release
        log_info "OS: ${PRETTY_NAME:-$NAME}"
    fi
elif [[ "$OSTYPE" == "darwin"* ]]; then
    OS="macos"
    log_info "OS: macOS"
else
    OS="other"
    log_info "OS: $OSTYPE"
fi

# Права могли потеряться при распаковке zip-архива
chmod +x "$SCRIPT_DIR/astrex.py" "$SCRIPT_DIR/install.sh" "$SCRIPT_DIR/ui/build.sh" 2>/dev/null || true
[ -f "$SCRIPT_DIR/ui/Astrex" ] && chmod +x "$SCRIPT_DIR/ui/Astrex" 2>/dev/null || true

# ═══════════════════════════════════════════════════════════════════════════════
# STEP 2: HARDWARE DETECTION
# ═══════════════════════════════════════════════════════════════════════════════

log_step "Step 2/7: Hardware Detection"

GPU_TYPE="cpu"
GPU_NAME=""

if check_command nvidia-smi; then
    GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1 || true)
    if [ -n "$GPU_NAME" ]; then
        GPU_TYPE="nvidia"
        log_info "NVIDIA GPU detected: $GPU_NAME"
    fi
fi

if [ "$GPU_TYPE" = "cpu" ] && { check_command rocm-smi || [ -e /dev/kfd ]; }; then
    for vendor_file in /sys/class/drm/card[0-9]*/device/vendor; do
        [ -f "$vendor_file" ] || continue
        if [ "$(cat "$vendor_file" 2>/dev/null)" = "0x1002" ]; then
            GPU_TYPE="amd"
            log_info "AMD GPU detected (ROCm)"
            break
        fi
    done
fi

if [ "$GPU_TYPE" = "cpu" ]; then
    for vendor_file in /sys/class/drm/card[0-9]*/device/vendor; do
        [ -f "$vendor_file" ] || continue
        if [ "$(cat "$vendor_file" 2>/dev/null)" = "0x8086" ]; then
            GPU_TYPE="intel"
            if check_command lspci; then
                GPU_NAME=$(lspci | grep -iE "VGA|Display" | grep -i intel | head -1 | sed 's/.*: //' || true)
            fi
            log_info "Intel GPU detected: ${GPU_NAME:-Intel Graphics}"
            break
        fi
    done
fi

CPU_CORES=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo "4")
log_info "CPU cores: $CPU_CORES"
[ "$GPU_TYPE" = "cpu" ] && log_info "No GPU detected — CPU-only mode"

case "$GPU_CHOICE" in
    auto|"") ;;
    cpu|nvidia|amd|intel) GPU_TYPE="$GPU_CHOICE"; log_info "GPU backend forced: $GPU_TYPE" ;;
    *) log_error "Unknown --gpu value: $GPU_CHOICE"; exit 2 ;;
esac

# ═══════════════════════════════════════════════════════════════════════════════
# STEP 3: MODE SELECTION
# ═══════════════════════════════════════════════════════════════════════════════

log_step "Step 3/7: Installation Mode"

if [ -z "$INSTALL_MODE" ]; then
    echo "Select installation mode:"
    echo ""
    echo "  1) Minimal   - Core scanning, all document formats, regex NLP"
    echo "  2) Standard  - + Russian morphology (pymorphy3), fuzzy search, spaCy"
    echo "  3) Full      - + sentence-transformers, vector DB (RAG), web API, media/network formats"
    echo "  4) Complete  - + PyTorch for your GPU, OpenVINO / Whisper"
    echo ""
    ask "Enter choice [1-4, default=3]:" "3"
    INSTALL_MODE="$REPLY"
fi

case "$INSTALL_MODE" in
    1|minimal)  INSTALL_MODE="minimal" ;;
    2|standard) INSTALL_MODE="standard" ;;
    3|full)     INSTALL_MODE="full" ;;
    4|complete) INSTALL_MODE="complete" ;;
    *) log_warn "Unknown mode '$INSTALL_MODE' — using full"; INSTALL_MODE="full" ;;
esac
log_info "Selected mode: $INSTALL_MODE"

# ═══════════════════════════════════════════════════════════════════════════════
# STEP 4: VIRTUAL ENVIRONMENT
# ═══════════════════════════════════════════════════════════════════════════════

log_step "Step 4/7: Python Environment"

if [ -d "$VENV_DIR" ]; then
    log_info "Virtual environment exists at $VENV_DIR"
    if [ "$RECREATE_VENV" -eq 0 ]; then
        ask "Recreate virtual environment? [y/N]:" "n"
        [[ "$REPLY" =~ ^[Yy]$ ]] && RECREATE_VENV=1
    fi
    if [ "$RECREATE_VENV" -eq 1 ]; then
        rm -rf "$VENV_DIR"
    fi
fi
if [ ! -x "$VENV_DIR/bin/python" ]; then
    "$PYTHON_BIN" -m venv "$VENV_DIR"
    log_info "Virtual environment created at $VENV_DIR"
fi

# Всё ставится интерпретатором venv напрямую (GUI находит venv/ сам)
VENV_PY="$VENV_DIR/bin/python"
if "$VENV_PY" -m pip install -q --upgrade pip wheel setuptools; then
    log_info "pip upgraded"
else
    log_warn "Could not upgrade pip (offline?) — continuing with the bundled version"
fi

# ═══════════════════════════════════════════════════════════════════════════════
# STEP 5: INSTALL DEPENDENCIES
# ═══════════════════════════════════════════════════════════════════════════════

log_step "Step 5/7: Installing Dependencies"

# Ядро и форматы документов (Minimal)
pip_required "pypdf>=3.0.0" "chardet>=5.0.0" "pyyaml>=6.0"
pip_optional "document formats" "olefile>=0.46" "xlrd>=2.0.1" "openpyxl>=3.1" "striprtf>=0.0.26" \
    "extract-msg>=0.45.0" "rarfile>=4.0" "py7zr>=0.20.0" "beautifulsoup4>=4.12.0" "lxml>=4.9.0" \
    "Pillow>=10.0" "numpy>=1.24" "zstandard>=0.21" "lz4>=4.0" || true

if [ "$INSTALL_MODE" != "minimal" ]; then
    # pymorphy3 — pymorphy2 не работает на Python 3.11+ (inspect.getargspec удалён)
    pip_optional "Russian morphology" "pymorphy3>=2.0" "pymorphy3-dicts-ru" || true
    pip_optional "fuzzy search" "rapidfuzz>=3.0.0" || true
    if pip_optional "spaCy" "spacy>=3.5.0"; then
        log_info "Downloading Russian spaCy model..."
        "$VENV_PY" -m spacy download ru_core_news_sm -q || {
            log_warn "Failed to download the spaCy model ru_core_news_sm (regex NER will be used)"
            FAILED_OPTIONAL+=("spaCy model")
        }
    fi
fi

if [ "$INSTALL_MODE" = "full" ] || [ "$INSTALL_MODE" = "complete" ]; then
    # Без GPU — CPU-сборка PyTorch (иначе sentence-transformers скачает CUDA-версию на ~2 ГБ)
    if [ "$INSTALL_MODE" = "full" ] && [ "$GPU_TYPE" = "cpu" ] && [ "$OS" = "linux" ]; then
        pip_optional "PyTorch (CPU)" torch --index-url https://download.pytorch.org/whl/cpu || true
    fi
    pip_optional "semantic relevance" "sentence-transformers>=2.2.0" || true
    pip_optional "vector database (RAG)" "chromadb>=0.4.0" || true
    pip_optional "web API" "fastapi>=0.100.0" "uvicorn>=0.23.0" || true
    pip_optional "media metadata" "mutagen>=1.47" || true
    pip_optional "network captures" "scapy>=2.5" || true
    pip_optional "PDF reports" "weasyprint>=60" || true
    pip_optional "PST mailboxes (pypff)" "libpff-python" || true
fi

if [ "$INSTALL_MODE" = "complete" ]; then
    log_info "Installing GPU acceleration for: $GPU_TYPE"
    case "$GPU_TYPE" in
        nvidia)
            # Колёса PyTorch с PyPI для Linux уже включают CUDA
            pip_optional "PyTorch (CUDA)" torch || true
            ;;
        amd)
            installed=0
            for rocm in rocm6.4 rocm6.3 rocm6.2; do
                if "$VENV_PY" -m pip install -q torch --index-url "https://download.pytorch.org/whl/$rocm"; then
                    log_info "PyTorch for ROCm installed ($rocm)"
                    installed=1
                    break
                fi
            done
            if [ "$installed" -eq 0 ]; then
                log_warn "PyTorch for ROCm not available — installing the CPU build"
                pip_optional "PyTorch (CPU)" torch --index-url https://download.pytorch.org/whl/cpu || true
            fi
            ;;
        intel)
            # PyTorch ≥ 2.5 поддерживает Intel GPU (XPU) без IPEX
            pip_optional "PyTorch (Intel XPU)" torch --index-url https://download.pytorch.org/whl/xpu || \
                pip_optional "PyTorch (CPU)" torch --index-url https://download.pytorch.org/whl/cpu || true
            pip_optional "OpenVINO" "openvino>=2024.0" || true
            ;;
        *)
            pip_optional "PyTorch (CPU)" torch --index-url https://download.pytorch.org/whl/cpu || true
            ;;
    esac
    pip_optional "audio/video transcription (Whisper)" openai-whisper || true
fi

# ═══════════════════════════════════════════════════════════════════════════════
# STEP 6: SYSTEM DEPENDENCIES
# ═══════════════════════════════════════════════════════════════════════════════

log_step "Step 6/7: System Dependencies (optional tools)"

APT_PKGS=()
check_tool() {
    # check_tool <команда> <описание> <пакет apt>
    if check_command "$1"; then
        log_info "$2: $(command -v "$1")"
    else
        log_warn "$2: not found (sudo apt install $3)"
        APT_PKGS+=("$3")
    fi
}

if check_command tesseract; then
    if tesseract --list-langs 2>/dev/null | grep -qx rus; then
        log_info "OCR: tesseract with Russian language"
    else
        log_warn "OCR: tesseract without Russian language data (sudo apt install tesseract-ocr-rus)"
        APT_PKGS+=("tesseract-ocr-rus")
    fi
else
    log_warn "OCR (images, scanned PDF): tesseract not found (sudo apt install tesseract-ocr tesseract-ocr-rus)"
    APT_PKGS+=("tesseract-ocr" "tesseract-ocr-rus")
fi
if check_command unrar || check_command unar || check_command bsdtar; then
    log_info "RAR archives: $(command -v unrar || command -v unar || command -v bsdtar)"
else
    log_warn "RAR archives: unrar/unar not found (sudo apt install unar)"
    APT_PKGS+=("unar")
fi
check_tool readpst "PST mailboxes" "pst-utils"
check_tool mdb-export "MS Access databases" "mdbtools"
check_tool ffprobe "Audio/video metadata and transcription" "ffmpeg"
check_tool pg_restore "PostgreSQL custom dumps" "postgresql-client"
check_tool nfdump "NetFlow captures" "nfdump"

if [ ${#APT_PKGS[@]} -gt 0 ] && [ "$OS" = "linux" ]; then
    echo ""
    echo "  All missing tools at once (Debian/Ubuntu/Kali):"
    echo "    sudo apt install ${APT_PKGS[*]}"
fi

# ═══════════════════════════════════════════════════════════════════════════════
# STEP 7: VERIFICATION
# ═══════════════════════════════════════════════════════════════════════════════

log_step "Step 7/7: Verification"

cd "$SCRIPT_DIR"
SELFTEST_OK=1
"$VENV_PY" selftest.py || SELFTEST_OK=0

# GUI: готовый бинарник собран с Qt 6.10; на системах со старым Qt его нужно пересобрать
GUI_STATUS="not checked"
if [ -f "$SCRIPT_DIR/ui/Astrex" ] && check_command ldd; then
    LDD_OUT="$(ldd "$SCRIPT_DIR/ui/Astrex" 2>&1 || true)"
    if echo "$LDD_OUT" | grep -qE "not found"; then
        GUI_STATUS="needs rebuild"
        log_warn "Prebuilt GUI does not run here (Qt 6 missing or older than 6.10):"
        echo "$LDD_OUT" | grep "not found" | sed 's/^/    /'
        if [ "$BUILD_GUI" -eq 0 ]; then
            ask "Build the GUI from source now (needs qt6-base-dev)? [y/N]:" "n"
            [[ "$REPLY" =~ ^[Yy]$ ]] && BUILD_GUI=1
        fi
    else
        GUI_STATUS="ok"
    fi
fi
if [ "$BUILD_GUI" -eq 1 ]; then
    if "$SCRIPT_DIR/ui/build.sh"; then
        GUI_STATUS="built"
    else
        GUI_STATUS="build failed"
        log_warn "GUI build failed. Install a compiler and Qt 6: sudo apt install build-essential qt6-base-dev"
    fi
fi

# ═══════════════════════════════════════════════════════════════════════════════
# DONE
# ═══════════════════════════════════════════════════════════════════════════════

echo ""
echo -e "${GREEN}╔═══════════════════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║                      ASTREX v3.0 Installation Complete                        ║${NC}"
echo -e "${GREEN}╚═══════════════════════════════════════════════════════════════════════════════╝${NC}"
echo ""
if [ ${#FAILED_OPTIONAL[@]} -gt 0 ]; then
    log_warn "Not installed (optional): ${FAILED_OPTIONAL[*]}"
fi
[ "$SELFTEST_OK" -eq 1 ] || log_warn "Self-test reported problems — see the output above"
echo ""
echo "Usage:"
echo "  source venv/bin/activate"
echo ""
echo "  # CLI"
echo "  python astrex.py scan \"query\" /path/to/folder"
echo "  python astrex.py index /path/to/folder"
echo "  python astrex.py search \"query\""
echo "  python astrex.py status"
echo ""
echo "  # Web API (token is printed at startup; header 'Authorization: Bearer <token>')"
echo "  python astrex.py web"
echo ""
echo "  # GUI ($GUI_STATUS) — uses venv/ automatically"
echo "  ./ui/Astrex            (rebuild for your Qt: ./ui/build.sh)"
echo ""
