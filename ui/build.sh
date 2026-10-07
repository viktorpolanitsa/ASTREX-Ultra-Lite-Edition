#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# ASTREX — сборка графического интерфейса (Qt 6)
#
#   ./build.sh           собрать ui/Astrex
#   ./build.sh clean     удалить промежуточные файлы сборки
#
# Нужны компилятор C++17 и Qt 6 Widgets:
#   Debian/Ubuntu/Kali:  sudo apt install build-essential qt6-base-dev
#   Fedora:              sudo dnf install gcc-c++ qt6-qtbase-devel
#   Arch:                sudo pacman -S base-devel qt6-base
#
# Сборка выполняется под текущий процессор без -march=native: бинарник
# переносится на любой x86-64 с Qt 6 (той же или более новой версии).
# Переменные: CXX, CXXFLAGS, LDFLAGS (дополнительные флаги), QMAKE.
# ═══════════════════════════════════════════════════════════════════════════════
set -euo pipefail

cd "$(dirname "$0")"
BUILD_DIR="build"

if [ "${1:-}" = "clean" ]; then
    rm -rf "$BUILD_DIR"
    echo "Cleaned."
    exit 0
fi

# ── qmake (предпочтительно) ─────────────────────────────────────────────────
QMAKE="${QMAKE:-}"
if [ -z "$QMAKE" ]; then
    if command -v qmake6 >/dev/null 2>&1; then
        QMAKE="$(command -v qmake6)"
    elif command -v qmake >/dev/null 2>&1 && qmake -query QT_VERSION 2>/dev/null | grep -q '^6\.'; then
        QMAKE="$(command -v qmake)"
    fi
fi

if [ -n "$QMAKE" ]; then
    echo "== Building with $QMAKE (Qt $("$QMAKE" -query QT_VERSION))"
    mkdir -p "$BUILD_DIR"
    (cd "$BUILD_DIR" && "$QMAKE" ../Astrex.pro CONFIG+=release && make -j"$(nproc 2>/dev/null || echo 2)")
    echo "== Done: $(pwd)/Astrex"
    exit 0
fi

# ── pkg-config + moc/rcc ────────────────────────────────────────────────────
if ! command -v pkg-config >/dev/null 2>&1 || ! pkg-config --exists Qt6Widgets; then
    echo "Qt 6 development files not found (qmake6 / pkg-config Qt6Widgets)." >&2
    echo "Install them, e.g.: sudo apt install build-essential qt6-base-dev" >&2
    exit 1
fi

QT_VERSION="$(pkg-config --modversion Qt6Widgets)"
LIBEXEC="$(pkg-config --variable=libexecdir Qt6Core 2>/dev/null || true)"
MOC=""
RCC=""
for dir in "$LIBEXEC" /usr/lib/qt6/libexec /usr/lib64/qt6/libexec /usr/libexec/qt6 \
           /usr/lib/x86_64-linux-gnu/qt6/libexec /usr/lib/qt6/bin; do
    if [ -n "$dir" ] && [ -x "$dir/moc" ] && [ -x "$dir/rcc" ]; then
        MOC="$dir/moc"
        RCC="$dir/rcc"
        break
    fi
done
if [ -z "$MOC" ]; then
    echo "moc/rcc of Qt 6 not found (package qt6-base-dev-tools)." >&2
    exit 1
fi

CXX="${CXX:-g++}"
BASE_FLAGS="-std=c++17 -O2 -fPIC -Wall -Wextra -fstack-protector-strong -D_FORTIFY_SOURCE=2 \
-DQT_NO_DEBUG -DQT_NO_DEBUG_OUTPUT"
# shellcheck disable=SC2086
QT_CFLAGS="$(pkg-config --cflags Qt6Widgets)"
QT_LIBS="$(pkg-config --libs Qt6Widgets)"

echo "== Building with $CXX, Qt $QT_VERSION (moc: $MOC)"
mkdir -p "$BUILD_DIR"
# shellcheck disable=SC2086
"$MOC" $(pkg-config --cflags-only-I Qt6Core) main.cpp -o "$BUILD_DIR/main.moc"
"$RCC" -name resources resources.qrc -o "$BUILD_DIR/qrc_resources.cpp"
# shellcheck disable=SC2086
$CXX $BASE_FLAGS ${CXXFLAGS:-} $QT_CFLAGS -I"$BUILD_DIR" -c main.cpp -o "$BUILD_DIR/main.o"
# shellcheck disable=SC2086
$CXX $BASE_FLAGS ${CXXFLAGS:-} $QT_CFLAGS -c "$BUILD_DIR/qrc_resources.cpp" -o "$BUILD_DIR/qrc_resources.o"
# shellcheck disable=SC2086
$CXX -o Astrex "$BUILD_DIR/main.o" "$BUILD_DIR/qrc_resources.o" ${LDFLAGS:-} $QT_LIBS \
    -Wl,-O1 -Wl,--as-needed -Wl,-z,relro -Wl,-z,now
chmod +x Astrex
echo "== Done: $(pwd)/Astrex"
