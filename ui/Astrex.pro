# ═══════════════════════════════════════════════════════════════════════════════
# ASTREX v3.0 — Qt6 Project File
# GRADX — Supported by PRAXIS
# ═══════════════════════════════════════════════════════════════════════════════

TEMPLATE = app
TARGET = Astrex
QT += core gui widgets

CONFIG += c++17
QMAKE_CXXFLAGS += -Wall -Wextra

# Release optimizations
CONFIG(release, debug|release) {
    QMAKE_CXXFLAGS += -O2 -march=native
    DEFINES += QT_NO_DEBUG_OUTPUT
}

# Debug
CONFIG(debug, debug|release) {
    QMAKE_CXXFLAGS += -g
}

# Source
SOURCES += main.cpp

# Resources (GRADX logo)
RESOURCES += resources.qrc

# Copy resources to build directory
copydata.commands = $(MKDIR) $$OUT_PWD/resources && $(COPY_FILE) $$PWD/resources/gradx_logo.png $$OUT_PWD/resources/
first.depends = $(first) copydata
export(first.depends)
export(copydata.commands)
QMAKE_EXTRA_TARGETS += first copydata

# Version
DEFINES += ASTREX_VERSION=\\\"3.0.0\\\"
DEFINES += QT_DISABLE_DEPRECATED_BEFORE=0x060000

# Installation
unix:!macx {
    target.path = /usr/local/bin
    resources.path = /usr/local/share/astrex/resources
    resources.files = resources/gradx_logo.png
    INSTALLS += target resources
}

macx {
    QMAKE_MACOSX_DEPLOYMENT_TARGET = 11.0
    ICON = resources/gradx_logo.icns
}

win32 {
    RC_ICONS = resources/gradx_logo.ico
}
