#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════════════
# ASTREX v3.0 — Installation Script
# Automated setup with hardware detection
# ═══════════════════════════════════════════════════════════════════════════════

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color
BOLD='\033[1m'

# Banner
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

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

log_step() {
    echo -e "\n${BLUE}${BOLD}=== $1 ===${NC}\n"
}

check_command() {
    command -v "$1" >/dev/null 2>&1
}

# ═══════════════════════════════════════════════════════════════════════════════
# STEP 1: SYSTEM CHECK
# ═══════════════════════════════════════════════════════════════════════════════

log_step "Step 1/7: System Check"

# Check Python
if ! check_command python3; then
    log_error "Python 3 not found. Please install Python 3.11 or 3.12"
    exit 1
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PYTHON_MAJOR=$(python3 -c 'import sys; print(sys.version_info.major)')
PYTHON_MINOR=$(python3 -c 'import sys; print(sys.version_info.minor)')

log_info "Python version: $PYTHON_VERSION"

# Check Python version compatibility
PYTHON_COMPAT="full"
if [[ "$PYTHON_MAJOR" -eq 3 && "$PYTHON_MINOR" -ge 14 ]]; then
    echo ""
    echo -e "${YELLOW}╔════════════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${YELLOW}║  WARNING: Python $PYTHON_VERSION detected                                   ║${NC}"
    echo -e "${YELLOW}║                                                                    ║${NC}"
    echo -e "${YELLOW}║  spaCy is not compatible with Python 3.14+                        ║${NC}"
    echo -e "${YELLOW}║  Some NLP features will be disabled (regex-only mode)             ║${NC}"
    echo -e "${YELLOW}║                                                                    ║${NC}"
    echo -e "${YELLOW}║  For full functionality, use Python 3.11 or 3.12:                 ║${NC}"
    echo -e "${YELLOW}║    pyenv install 3.12.0 && pyenv local 3.12.0                     ║${NC}"
    echo -e "${YELLOW}╚════════════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    PYTHON_COMPAT="limited"
    
    read -p "Continue with limited functionality? [y/N]: " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        log_info "Installation cancelled."
        exit 0
    fi
fi

# Check pip
if ! check_command pip3; then
    log_error "pip3 not found. Please install pip."
    exit 1
fi

# OS detection
OS="unknown"
if [[ "$OSTYPE" == "linux-gnu"* ]]; then
    OS="linux"
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        log_info "OS: $NAME"
    fi
elif [[ "$OSTYPE" == "darwin"* ]]; then
    OS="macos"
    log_info "OS: macOS"
else
    OS="other"
    log_info "OS: $OSTYPE"
fi

# ═══════════════════════════════════════════════════════════════════════════════
# STEP 2: HARDWARE DETECTION
# ═══════════════════════════════════════════════════════════════════════════════

log_step "Step 2/7: Hardware Detection"

GPU_TYPE="cpu"
GPU_NAME=""

# Check for NVIDIA GPU
if check_command nvidia-smi; then
    GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)
    if [ -n "$GPU_NAME" ]; then
        GPU_TYPE="nvidia"
        log_info "NVIDIA GPU detected: $GPU_NAME"
    fi
fi

# Check for Intel GPU
if [ "$GPU_TYPE" == "cpu" ]; then
    if [ -d "/sys/class/drm" ]; then
        for card in /sys/class/drm/card*/device/vendor; do
            if [ -f "$card" ]; then
                vendor=$(cat "$card" 2>/dev/null)
                if [ "$vendor" == "0x8086" ]; then
                    GPU_TYPE="intel"
                    # Try to get GPU name
                    if check_command lspci; then
                        GPU_NAME=$(lspci | grep -i "VGA\|Display" | grep -i intel | head -1 | sed 's/.*: //')
                    fi
                    log_info "Intel GPU detected: ${GPU_NAME:-Intel Graphics}"
                    break
                fi
            fi
        done
    fi
fi

# Check for AMD GPU
if [ "$GPU_TYPE" == "cpu" ]; then
    if check_command rocm-smi; then
        GPU_TYPE="amd"
        log_info "AMD GPU detected (ROCm)"
    fi
fi

# CPU info
CPU_CORES=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo "4")
log_info "CPU cores: $CPU_CORES"

if [ "$GPU_TYPE" == "cpu" ]; then
    log_info "No GPU detected - CPU-only mode"
fi

# ═══════════════════════════════════════════════════════════════════════════════
# STEP 3: MODE SELECTION
# ═══════════════════════════════════════════════════════════════════════════════

log_step "Step 3/7: Installation Mode"

echo "Select installation mode:"
echo ""
echo "  1) Minimal   - Core scanning, basic NLP (fastest install)"
echo "  2) Standard  - + spaCy, morphology, fuzzy search"
echo "  3) Full      - + sentence-transformers, vector DB, web API"
echo "  4) Complete  - + GPU acceleration, local LLM support"
echo ""

if [ "$PYTHON_COMPAT" == "limited" ]; then
    echo -e "${YELLOW}Note: spaCy unavailable on Python 3.14+${NC}"
    echo ""
fi

read -p "Enter choice [1-4, default=3]: " MODE_CHOICE
MODE_CHOICE=${MODE_CHOICE:-3}

case $MODE_CHOICE in
    1) INSTALL_MODE="minimal" ;;
    2) INSTALL_MODE="standard" ;;
    3) INSTALL_MODE="full" ;;
    4) INSTALL_MODE="complete" ;;
    *) INSTALL_MODE="full" ;;
esac

log_info "Selected mode: $INSTALL_MODE"

# ═══════════════════════════════════════════════════════════════════════════════
# STEP 4: VIRTUAL ENVIRONMENT
# ═══════════════════════════════════════════════════════════════════════════════

log_step "Step 4/7: Python Environment"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/venv"

if [ -d "$VENV_DIR" ]; then
    log_info "Virtual environment exists at $VENV_DIR"
    read -p "Recreate virtual environment? [y/N]: " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        rm -rf "$VENV_DIR"
        python3 -m venv "$VENV_DIR"
        log_info "Virtual environment recreated"
    fi
else
    python3 -m venv "$VENV_DIR"
    log_info "Virtual environment created at $VENV_DIR"
fi

# Activate venv
source "$VENV_DIR/bin/activate"
log_info "Virtual environment activated"

# Upgrade pip
pip install --upgrade pip wheel setuptools -q
log_info "pip upgraded"

# ═══════════════════════════════════════════════════════════════════════════════
# STEP 5: INSTALL DEPENDENCIES
# ═══════════════════════════════════════════════════════════════════════════════

log_step "Step 5/7: Installing Dependencies"

# Core (always)
log_info "Installing core dependencies..."
pip install pypdf chardet -q

# Extractors
log_info "Installing extractors..."
pip install olefile striprtf extract-msg rarfile py7zr beautifulsoup4 lxml -q

if [ "$INSTALL_MODE" != "minimal" ]; then
    # NLP
    log_info "Installing NLP dependencies..."
    pip install pymorphy2 pymorphy2-dicts-ru rapidfuzz -q
    
    # spaCy (only if Python < 3.14)
    if [ "$PYTHON_COMPAT" == "full" ]; then
        log_info "Installing spaCy..."
        pip install spacy -q
        
        log_info "Downloading Russian language model..."
        python -m spacy download ru_core_news_sm -q || log_warn "Failed to download spaCy model"
    else
        log_warn "Skipping spaCy (Python 3.14+ not supported)"
    fi
fi

if [ "$INSTALL_MODE" == "full" ] || [ "$INSTALL_MODE" == "complete" ]; then
    # Sentence transformers
    log_info "Installing sentence-transformers..."
    pip install sentence-transformers -q
    
    # Vector DB
    log_info "Installing vector database..."
    pip install chromadb -q
    
    # Web API
    log_info "Installing web API dependencies..."
    pip install fastapi uvicorn -q
fi

if [ "$INSTALL_MODE" == "complete" ]; then
    # GPU-specific installations
    log_info "Installing GPU acceleration..."
    
    case $GPU_TYPE in
        nvidia)
            log_info "Installing CUDA support..."
            pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121 -q || \
            pip install torch -q
            ;;
        intel)
            log_info "Installing Intel GPU support..."
            pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu -q
            pip install intel-extension-for-pytorch -q 2>/dev/null || log_warn "IPEX not available"
            pip install openvino openvino-dev -q 2>/dev/null || log_warn "OpenVINO installation failed"
            ;;
        amd)
            log_info "Installing AMD ROCm support..."
            pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/rocm5.6 -q || \
            pip install torch -q
            ;;
        *)
            log_info "Installing CPU PyTorch..."
            pip install torch -q
            ;;
    esac
fi

# ═══════════════════════════════════════════════════════════════════════════════
# STEP 6: SYSTEM DEPENDENCIES
# ═══════════════════════════════════════════════════════════════════════════════

log_step "Step 6/7: System Dependencies"

# Tesseract OCR
if check_command tesseract; then
    TESSERACT_VERSION=$(tesseract --version 2>&1 | head -1)
    log_info "Tesseract OCR: $TESSERACT_VERSION"
else
    log_warn "Tesseract OCR not found. OCR features will be disabled."
    echo "  Install with:"
    if [ "$OS" == "linux" ]; then
        echo "    sudo apt install tesseract-ocr tesseract-ocr-rus"
        echo "  or"
        echo "    sudo pacman -S tesseract tesseract-data-rus"
    elif [ "$OS" == "macos" ]; then
        echo "    brew install tesseract tesseract-lang"
    fi
fi

# libpst for PST files
if check_command readpst; then
    log_info "libpst: available"
else
    log_warn "libpst not found. PST file support limited."
    echo "  Install with:"
    if [ "$OS" == "linux" ]; then
        echo "    sudo apt install pst-utils"
    elif [ "$OS" == "macos" ]; then
        echo "    brew install libpst"
    fi
fi

# ═══════════════════════════════════════════════════════════════════════════════
# STEP 7: VERIFICATION
# ═══════════════════════════════════════════════════════════════════════════════

log_step "Step 7/7: Verification"

log_info "Running self-test..."

# Create test script
TEST_SCRIPT=$(cat <<'EOF'
import sys
import warnings
warnings.filterwarnings('ignore')

print(f"Python: {sys.version}")
print()

# Core
print("Core modules:")
try:
    from core.config import ASTREX_HOME
    print(f"  ✓ config (home: {ASTREX_HOME})")
except Exception as e:
    print(f"  ✗ config: {e}")

try:
    from core.index import file_index
    stats = file_index.get_stats()
    print(f"  ✓ index ({stats['total_files']} files)")
except Exception as e:
    print(f"  ✗ index: {e}")

# NLP
print("\nNLP modules:")
try:
    from core.nlp import morph_analyzer
    print(f"  {'✓' if morph_analyzer.available else '✗'} morphology (pymorphy2)")
except Exception as e:
    print(f"  ✗ morphology: {e}")

try:
    from core.nlp import entity_extractor
    print(f"  {'✓' if entity_extractor.nlp else '✗'} spaCy")
except Exception as e:
    print(f"  ✗ spaCy: {e}")

try:
    from core.nlp import relevance_calculator
    print(f"  {'✓' if relevance_calculator.sbert_model else '✗'} sentence-transformers")
except Exception as e:
    print(f"  ✗ sentence-transformers: {e}")

try:
    from core.nlp import FuzzyMatcher
    print(f"  {'✓' if FuzzyMatcher.is_available() else '✗'} fuzzy search (rapidfuzz)")
except Exception as e:
    print(f"  ✗ fuzzy search: {e}")

# Extractors
print("\nExtractors:")
try:
    from extractors import registry
    extractors = registry.list_extractors()
    available = sum(1 for e in extractors if e['available'])
    print(f"  ✓ {available}/{len(extractors)} extractors available")
except Exception as e:
    print(f"  ✗ extractors: {e}")

# ML
print("\nML modules:")
try:
    from ml import vector_store
    vs_stats = vector_store.get_stats()
    print(f"  {'✓' if vs_stats.get('available') else '✗'} vector store")
except Exception as e:
    print(f"  ✗ vector store: {e}")

try:
    from ml import get_llm_status
    llm = get_llm_status()
    print(f"  {'✓' if llm.get('available') else '✗'} LLM (Ollama)")
except Exception as e:
    print(f"  ✗ LLM: {e}")

# GPU
print("\nGPU:")
try:
    import torch
    if torch.cuda.is_available():
        print(f"  ✓ CUDA: {torch.cuda.get_device_name(0)}")
    elif hasattr(torch, 'xpu') and torch.xpu.is_available():
        print(f"  ✓ Intel XPU available")
    else:
        print("  ○ CPU mode")
except ImportError:
    print("  ○ PyTorch not installed")
except Exception as e:
    print(f"  ✗ GPU: {e}")

print("\n" + "="*50)
print("Installation complete!")
EOF
)

cd "$SCRIPT_DIR"
python3 -c "$TEST_SCRIPT" || log_warn "Some components may not be fully functional"

# ═══════════════════════════════════════════════════════════════════════════════
# DONE
# ═══════════════════════════════════════════════════════════════════════════════

echo ""
echo -e "${GREEN}╔═══════════════════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║                      ASTREX v3.0 Installation Complete                        ║${NC}"
echo -e "${GREEN}╚═══════════════════════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo "Usage:"
echo "  source venv/bin/activate"
echo ""
echo "  # CLI"
echo "  python astrex.py scan \"query\" /path/to/folder"
echo "  python astrex.py search \"query\""
echo "  python astrex.py status"
echo ""
echo "  # Web API"
echo "  python -m web.api"
echo ""
echo "  # GUI (requires Qt6)"
echo "  cd ui && qmake6 && make && ./Astrex"
echo ""

if [ "$PYTHON_COMPAT" == "limited" ]; then
    echo -e "${YELLOW}Note: Running in limited mode (Python 3.14+)${NC}"
    echo -e "${YELLOW}For full functionality, use Python 3.11 or 3.12${NC}"
    echo ""
fi
