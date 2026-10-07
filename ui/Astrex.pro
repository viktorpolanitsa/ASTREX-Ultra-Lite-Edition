# ═══════════════════════════════════════════════════════════════════════════════
# ASTREX v3.0 — Qt6 Project File
# GRADX — Supported by PRAXIS
#
# Сборка:  ./build.sh          (или: mkdir build && cd build && qmake6 ../Astrex.pro && make)
# Установка: sudo make install  — программа в /usr/local/bin, бэкенд в
#            /usr/local/share/astrex (затем: sudo /usr/local/share/astrex/install.sh)
# ═══════════════════════════════════════════════════════════════════════════════

TEMPLATE = app
TARGET = Astrex
QT += core gui widgets

lessThan(QT_MAJOR_VERSION, 6): error("ASTREX GUI requires Qt 6")

CONFIG += c++17
QMAKE_CXXFLAGS += -Wall -Wextra

# Бинарник кладётся рядом с проектом (ui/Astrex): так он находит ../astrex.py
DESTDIR = $$PWD

# Release: без -march=native — иначе программа падала (SIGILL) на других процессорах
CONFIG(release, debug|release) {
    QMAKE_CXXFLAGS += -O2 -fstack-protector-strong -D_FORTIFY_SOURCE=2
    QMAKE_LFLAGS += -Wl,-z,relro -Wl,-z,now
    DEFINES += QT_NO_DEBUG_OUTPUT
}

CONFIG(debug, debug|release) {
    QMAKE_CXXFLAGS += -g
}

SOURCES += main.cpp

# Логотип встраивается в программу (resources.qrc)
RESOURCES += resources.qrc

DEFINES += QT_DISABLE_DEPRECATED_BEFORE=0x060000

# Installation
unix:!macx {
    target.path = /usr/local/bin

    # Бэкенд: GUI ищет его в <prefix>/share/astrex относительно программы
    backend.path = /usr/local/share/astrex
    backend.files = ../astrex.py ../__init__.py ../selftest.py ../install.sh \
                    ../requirements.txt ../README.md ../info.md \
                    ../core ../extractors ../ml ../reporting ../web

    INSTALLS += target backend
}

macx {
    QMAKE_MACOSX_DEPLOYMENT_TARGET = 11.0
    exists(resources/gradx_logo.icns): ICON = resources/gradx_logo.icns
}

win32 {
    exists(resources/gradx_logo.ico): RC_ICONS = resources/gradx_logo.ico
}
