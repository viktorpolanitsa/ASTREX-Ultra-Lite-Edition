/*
 * ═══════════════════════════════════════════════════════════════════════════════
 * ASTREX v3.0 — Intelligence System
 * Dual Mode Interface: TOP SECRET / SAP
 * GRADX — Supported by PRAXIS
 *
 * BUGFIXES v2:
 * - FIXED: Path resolution now properly finds astrex.py in parent directory
 * - FIXED: Uses QDir::absolutePath() correctly
 * - FIXED: Multiple fallback paths for different installation scenarios
 * - FIXED: Better error messages with actual paths tried
 * - FIXED: Process error handling improved
 *
 * UI v3:
 * - Search history with QComboBox
 * - Context menu on results (copy path, open folder, view snippet)
 * - Double-click shows snippet in DOSSIER tab (no blocking dialog)
 * - Sortable results tree with filter input
 * - Tab badges with live counts
 * - GRAPH tab for entity overview + export
 * - Grouped entity panel with color-coded types
 * - ETA on progress bar
 * - Improved status bar with permanent widgets
 * - Keyboard shortcuts (Ctrl+F filter, Ctrl+L query)
 * - Resizable columns, saved splitter state
 * - Tooltips on all controls
 * ═══════════════════════════════════════════════════════════════════════════════
 */

#include <QApplication>
#include <QMainWindow>
#include <QWidget>
#include <QVBoxLayout>
#include <QHBoxLayout>
#include <QGridLayout>
#include <QLineEdit>
#include <QPushButton>
#include <QTextEdit>
#include <QLabel>
#include <QTabWidget>
#include <QProgressBar>
#include <QTimer>
#include <QProcess>
#include <QFileDialog>
#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonArray>
#include <QDateTime>
#include <QComboBox>
#include <QCheckBox>
#include <QSpinBox>
#include <QDoubleSpinBox>
#include <QSplitter>
#include <QTreeWidget>
#include <QTreeWidgetItem>
#include <QHeaderView>
#include <QMenuBar>
#include <QMenu>
#include <QAction>
#include <QStatusBar>
#include <QMessageBox>
#include <QSettings>
#include <QDir>
#include <QFont>
#include <QFontDatabase>
#include <QPainter>
#include <QPaintEvent>
#include <QPropertyAnimation>
#include <QGraphicsOpacityEffect>
#include <QPixmap>
#include <QThread>
#include <QFileInfo>
#include <QStandardPaths>
#include <QDebug>
#include <QToolBar>
#include <QStyle>
#include <QClipboard>
#include <QDesktopServices>
#include <QUrl>
#include <QShortcut>
#include <QKeySequence>
#include <cmath>
#include <QFile>
#include <QTextStream>
#include <QRegularExpression>
#include <QMutex>
#include <QMutexLocker>
#include <atomic>

// ═══════════════════════════════════════════════════════════════════════════════
// SECURITY MODES
// ═══════════════════════════════════════════════════════════════════════════════

enum SecurityMode {
    MODE_TOP_SECRET,  // Green theme - standard classified
    MODE_SAP          // Red theme - Special Access Programs
};

// ═══════════════════════════════════════════════════════════════════════════════
// HARDWARE MONITOR THREAD
// Runs in background, reads CPU from /proc/stat every 300ms,
// reads GPU/iGPU from external tools every ~900ms (3 ticks) and caches the result.
// ═══════════════════════════════════════════════════════════════════════════════

struct HwSnapshot {
    // CPU
    int cpuPercent = -1;
    // GPU
    QString gpuText;
    int gpuPercent = -1; // -1 = unknown
};
Q_DECLARE_METATYPE(HwSnapshot)

class HwMonitorThread : public QThread {
    Q_OBJECT
public:
    explicit HwMonitorThread(QObject *parent = nullptr) : QThread(parent) {}

    void stop() { m_stop.store(true); }

signals:
    void updated(HwSnapshot snap);

protected:
    void run() override {
        qulonglong prevTotal = 0, prevIdle = 0;
        int gpuPollCounter = 0;
        QString cachedGpuText = "GPU: ...";
        int cachedGpuPct = -1;
        bool gpuDetected = false;
        QString gpuMethod; // "nvidia", "rocm", "intel_sysfs", "amd_sysfs", "intel_top", ""

        while (!m_stop.load()) {
            HwSnapshot snap;

            // ── CPU (fast: read /proc/stat) ──────────────────────────
            snap.cpuPercent = readCpuUsage(prevTotal, prevIdle);

            // ── GPU/iGPU (expensive: run every ~900ms = 3 ticks of 300ms) ───
            if (gpuPollCounter <= 0) {
                readGpuStatus(cachedGpuText, cachedGpuPct, gpuDetected, gpuMethod);
                gpuPollCounter = 3; // next GPU poll in 3*300ms = 900ms
            }
            gpuPollCounter--;

            snap.gpuText = cachedGpuText;
            snap.gpuPercent = cachedGpuPct;

            emit updated(snap);
            QThread::msleep(300);
        }
    }

private:
    std::atomic<bool> m_stop{false};

    int readCpuUsage(qulonglong &prevTotal, qulonglong &prevIdle) {
        QFile file("/proc/stat");
        if (!file.open(QIODevice::ReadOnly | QIODevice::Text)) return -1;

        QByteArray line = file.readLine();
        file.close();

        if (!line.startsWith("cpu ")) return -1;

        QList<QByteArray> parts = line.simplified().split(' ');
        if (parts.size() < 5) return -1;

        qulonglong user    = parts[1].toULongLong();
        qulonglong nice    = parts[2].toULongLong();
        qulonglong system  = parts[3].toULongLong();
        qulonglong idle    = parts[4].toULongLong();
        qulonglong iowait  = parts.size() > 5 ? parts[5].toULongLong() : 0;
        qulonglong irq     = parts.size() > 6 ? parts[6].toULongLong() : 0;
        qulonglong softirq = parts.size() > 7 ? parts[7].toULongLong() : 0;

        qulonglong totalIdle = idle + iowait;
        qulonglong total = user + nice + system + idle + iowait + irq + softirq;

        int result = -1;
        if (prevTotal > 0) {
            qulonglong dTotal = total - prevTotal;
            qulonglong dIdle  = totalIdle - prevIdle;
            if (dTotal > 0)
                result = qBound(0, (int)qRound(100.0 * (1.0 - (double)dIdle / (double)dTotal)), 100);
        }

        prevTotal = total;
        prevIdle  = totalIdle;
        return result;
    }

    void readGpuStatus(QString &text, int &pct, bool &detected, QString &method) {
        // Once we know the method, skip re-detection
        if (detected) {
            if (method == "nvidia")       { readNvidia(text, pct); return; }
            if (method == "rocm")         { readRocm(text, pct); return; }
            if (method == "intel_sysfs")  { readIntelSysfs(text, pct); return; }
            if (method == "amd_sysfs")    { readAmdSysfs(text, pct); return; }
            if (method == "intel_top")    { readIntelTop(text, pct); return; }
        }

        // Auto-detect: try each method
        if (readNvidia(text, pct))      { detected = true; method = "nvidia"; return; }
        if (readRocm(text, pct))        { detected = true; method = "rocm"; return; }
        if (readIntelSysfs(text, pct))  { detected = true; method = "intel_sysfs"; return; }
        if (readAmdSysfs(text, pct))    { detected = true; method = "amd_sysfs"; return; }
        if (readIntelTop(text, pct))    { detected = true; method = "intel_top"; return; }

        detected = true;
        method = "";
        text = "GPU: N/A";
        pct = -1;
    }

    bool readNvidia(QString &text, int &pct) {
        QProcess proc;
        proc.start("nvidia-smi",
            QStringList() << "--query-gpu=utilization.gpu,memory.used,memory.total"
                          << "--format=csv,noheader,nounits");
        if (!proc.waitForFinished(2000) || proc.exitCode() != 0) return false;

        QString out = QString::fromUtf8(proc.readAllStandardOutput()).trimmed();
        if (out.isEmpty()) return false;

        QStringList vals = out.split('\n').first().trimmed().split(',');
        if (vals.size() < 3) return false;

        int util = vals[0].trimmed().toInt();
        int memUsed = vals[1].trimmed().toInt();
        int memTotal = vals[2].trimmed().toInt();
        text = QString("GPU: %1% | %2/%3 MB").arg(util).arg(memUsed).arg(memTotal);
        pct = util;
        return true;
    }

    bool readRocm(QString &text, int &pct) {
        QProcess proc;
        proc.start("rocm-smi", QStringList() << "--showuse" << "--showmemuse" << "--csv");
        if (!proc.waitForFinished(2000) || proc.exitCode() != 0) return false;

        QString out = QString::fromUtf8(proc.readAllStandardOutput()).trimmed();
        for (const QString &line : out.split('\n')) {
            if (line.contains("GPU") && !line.startsWith("=")) {
                QStringList cols = line.split(',');
                if (cols.size() >= 2) {
                    bool ok;
                    int util = cols[1].trimmed().remove('%').trimmed().toInt(&ok);
                    if (ok) {
                        text = QString("AMD GPU: %1%").arg(util);
                        pct = util;
                        return true;
                    }
                }
            }
        }
        return false;
    }

    // Find first iGPU card path in /sys/class/drm
    QString findCardDevice(const QString &vendorId) {
        QDir drm("/sys/class/drm");
        for (const QString &card : drm.entryList(QStringList() << "card[0-9]*", QDir::Dirs)) {
            QString devPath = "/sys/class/drm/" + card + "/device";
            QFile vf(devPath + "/vendor");
            if (vf.open(QIODevice::ReadOnly)) {
                QString v = QString::fromUtf8(vf.readAll()).trimmed();
                vf.close();
                if (v == vendorId) return devPath;
            }
        }
        return QString();
    }

    QString findCardDir(const QString &vendorId) {
        QDir drm("/sys/class/drm");
        for (const QString &card : drm.entryList(QStringList() << "card[0-9]*", QDir::Dirs)) {
            QString cardPath = "/sys/class/drm/" + card;
            QFile vf(cardPath + "/device/vendor");
            if (vf.open(QIODevice::ReadOnly)) {
                QString v = QString::fromUtf8(vf.readAll()).trimmed();
                vf.close();
                if (v == vendorId) return cardPath;
            }
        }
        return QString();
    }

    static int readSysfsInt(const QString &path) {
        QFile f(path);
        if (!f.open(QIODevice::ReadOnly)) return -1;
        bool ok;
        int val = QString::fromUtf8(f.readAll()).trimmed().toInt(&ok);
        f.close();
        return ok ? val : -1;
    }

    static qint64 readSysfsLong(const QString &path) {
        QFile f(path);
        if (!f.open(QIODevice::ReadOnly)) return -1;
        bool ok;
        qint64 val = QString::fromUtf8(f.readAll()).trimmed().toLongLong(&ok);
        f.close();
        return ok ? val : -1;
    }

    bool readIntelSysfs(QString &text, int &pct) {
        QString card = findCardDir("0x8086");
        if (card.isEmpty()) return false;

        QStringList curPaths = {
            card + "/gt_cur_freq_mhz",
            card + "/gt/gt0/cur_freq_mhz",
            card + "/device/gt/gt0/cur_freq_mhz"
        };
        QStringList maxPaths = {
            card + "/gt_max_freq_mhz",
            card + "/gt/gt0/rpe_freq_mhz",
            card + "/device/gt/gt0/rpe_freq_mhz"
        };

        int cur = -1, max = -1;
        for (const QString &p : curPaths) { cur = readSysfsInt(p); if (cur >= 0) break; }
        for (const QString &p : maxPaths) { max = readSysfsInt(p); if (max >= 0) break; }

        if (cur < 0) return false;

        if (max > 0) {
            pct = qBound(0, (int)qRound(100.0 * cur / max), 100);
            text = QString("Intel iGPU: %1% %2/%3 MHz").arg(pct).arg(cur).arg(max);
        } else {
            pct = -1;
            text = QString("Intel iGPU: %1 MHz").arg(cur);
        }
        return true;
    }

    bool readAmdSysfs(QString &text, int &pct) {
        QString dev = findCardDevice("0x1002");
        if (dev.isEmpty()) return false;

        qint64 used = readSysfsLong(dev + "/mem_info_vram_used");
        qint64 total = readSysfsLong(dev + "/mem_info_vram_total");

        // Also try gpu_busy_percent (amdgpu driver)
        int busy = readSysfsInt(dev + "/gpu_busy_percent");

        if (busy >= 0) {
            pct = busy;
            text = QString("AMD iGPU: %1%").arg(busy);
            if (used >= 0 && total > 0) {
                text += QString(" | %1/%2 MB").arg(used / (1024*1024)).arg(total / (1024*1024));
            }
            return true;
        }

        if (used >= 0 && total > 0) {
            pct = qBound(0, (int)(100 * used / total), 100);
            text = QString("AMD iGPU: VRAM %1/%2 MB (%3%)")
                .arg(used / (1024*1024)).arg(total / (1024*1024)).arg(pct);
            return true;
        }
        return false;
    }

    bool readIntelTop(QString &text, int &pct) {
        QProcess proc;
        proc.start("intel_gpu_top", QStringList() << "-s" << "100" << "-l" << "-o" << "-");
        if (!proc.waitForStarted(500)) return false;
        proc.waitForReadyRead(600);
        proc.kill();
        proc.waitForFinished(500);

        QString out = QString::fromUtf8(proc.readAllStandardOutput()).trimmed();
        if (out.isEmpty()) return false;

        QStringList lines = out.split('\n');
        for (int i = lines.size() - 1; i >= 0; --i) {
            QString line = lines[i].trimmed();
            if (!line.startsWith('{')) continue;
            QJsonParseError err;
            QJsonDocument doc = QJsonDocument::fromJson(line.toUtf8(), &err);
            if (err.error != QJsonParseError::NoError || !doc.isObject()) continue;

            double busy = -1;
            QJsonObject engines = doc.object().value("engines").toObject();
            for (auto it = engines.begin(); it != engines.end(); ++it) {
                double b = it.value().toObject().value("busy").toDouble(-1);
                if (b > busy) busy = b;
            }
            if (busy >= 0) {
                pct = qBound(0, (int)qRound(busy), 100);
                text = QString("Intel iGPU: %1%").arg(pct);
                return true;
            }
        }
        return false;
    }
};

// ═══════════════════════════════════════════════════════════════════════════════
// WATERMARK WIDGET (Background overlay for SAP mode)
// ═══════════════════════════════════════════════════════════════════════════════

class WatermarkWidget : public QWidget {
    Q_OBJECT
public:
    WatermarkWidget(QWidget *parent = nullptr) : QWidget(parent) {
        setAttribute(Qt::WA_TransparentForMouseEvents);
        setAttribute(Qt::WA_NoSystemBackground);
        m_enabled = false;
    }

    void setEnabled(bool enabled) {
        m_enabled = enabled;
        update();
    }

    void setLogo(const QPixmap &logo) {
        m_logoPixmap = logo;
        update();
    }

protected:
    void paintEvent(QPaintEvent *event) override {
        Q_UNUSED(event);
        if (!m_enabled) return;

        QPainter painter(this);
        painter.setRenderHint(QPainter::Antialiasing);

        // Diagonal warning text grid
        painter.save();
        painter.setOpacity(0.03);
        QFont font("Courier New", 14, QFont::Bold);
        painter.setFont(font);
        painter.setPen(QColor("#FF0000"));

        QString text = "UNAUTHORIZED ACCESS PROHIBITED";
        int textWidth = painter.fontMetrics().horizontalAdvance(text);
        int textHeight = painter.fontMetrics().height();

        painter.translate(0, height() / 2);
        painter.rotate(-30);

        for (int y = -height(); y < height() * 2; y += textHeight * 3) {
            for (int x = -width(); x < width() * 2; x += textWidth + 50) {
                painter.drawText(x, y, text);
            }
        }
        painter.restore();

        // Center logo
        if (!m_logoPixmap.isNull()) {
            painter.setOpacity(0.05);
            int logoSize = qMin(width(), height()) / 2;
            QPixmap scaled = m_logoPixmap.scaled(logoSize, logoSize,
                Qt::KeepAspectRatio, Qt::SmoothTransformation);
            int x = (width() - scaled.width()) / 2;
            int y = (height() - scaled.height()) / 2;
            painter.drawPixmap(x, y, scaled);
        }
    }

private:
    bool m_enabled;
    QPixmap m_logoPixmap;
};

// ═══════════════════════════════════════════════════════════════════════════════
// PULSATING LABEL (For SAP header - animated warning)
// ═══════════════════════════════════════════════════════════════════════════════

class PulsatingLabel : public QLabel {
    Q_OBJECT
    Q_PROPERTY(QColor backgroundColor READ backgroundColor WRITE setBackgroundColor)

public:
    PulsatingLabel(const QString &text, QWidget *parent = nullptr)
        : QLabel(text, parent), m_pulsing(false), m_pulsePhase(0) {
        m_timer = new QTimer(this);
        connect(m_timer, &QTimer::timeout, this, &PulsatingLabel::updatePulse);
        m_bgColor = QColor("#8B0000");
    }

    void startPulsing() {
        m_pulsing = true;
        m_timer->start(50);
    }

    void stopPulsing() {
        m_pulsing = false;
        m_timer->stop();
        m_bgColor = QColor("#FF0000");
        updateStyleSheet();
    }

    QColor backgroundColor() const { return m_bgColor; }
    void setBackgroundColor(const QColor &color) {
        m_bgColor = color;
        updateStyleSheet();
    }

private slots:
    void updatePulse() {
        m_pulsePhase += 0.08;
        if (m_pulsePhase > 2 * 3.14159) m_pulsePhase = 0;

        int intensity = 139 + static_cast<int>(116 * std::sin(m_pulsePhase));
        m_bgColor = QColor(intensity, 0, 0);
        updateStyleSheet();
    }

    void updateStyleSheet() {
        setStyleSheet(QString(
            "color: #FFFFFF; "
            "background-color: %1; "
            "font-weight: bold; "
            "padding: 8px 15px; "
            "letter-spacing: 2px;"
            "font-size: 12px;"
        ).arg(m_bgColor.name()));
    }

private:
    QTimer *m_timer;
    bool m_pulsing;
    double m_pulsePhase;
    QColor m_bgColor;
};

// ═══════════════════════════════════════════════════════════════════════════════
// MAIN WINDOW
// ═══════════════════════════════════════════════════════════════════════════════

class Astrex : public QMainWindow {
    Q_OBJECT

public:
    Astrex(QWidget *parent = nullptr) : QMainWindow(parent) {
        qRegisterMetaType<HwSnapshot>("HwSnapshot");
        setWindowTitle("ASTREX v3.0 — Intelligence System");
        setMinimumSize(1400, 900);

        m_securityMode = MODE_TOP_SECRET;

        initializePaths();
        loadSettings();
        setupUI();
        setupMenus();
        setupToolBar();
        setupConnections();
        setupShortcuts();
        applySecurityMode();

        QTimer::singleShot(100, this, &Astrex::checkPythonBackend);
    }

    ~Astrex() {
        if (m_hwThread) {
            m_hwThread->stop();
            m_hwThread->wait(1000);
        }
        saveSettings();
    }

private:
    SecurityMode m_securityMode;

    // PATHS
    QString m_projectDir;
    QString m_astrexPy;
    bool m_backendFound = false;

    // UI Elements
    QWidget *m_centralWidget;
    WatermarkWidget *m_watermark;
    QToolBar *m_toolBar;

    QComboBox *m_queryCombo;
    QLineEdit *m_pathInput;
    QPushButton *m_btnBrowse;
    QPushButton *m_btnScan;
    QPushButton *m_btnStop;
    QPushButton *m_btnClear;
    QPushButton *m_btnIndex;      // runs "astrex index <folder>" in-UI
    QPushButton *m_btnModeToggle;
    QComboBox   *m_extFilterCombo; // post-scan file-type filter

    QProgressBar *m_progressBar;
    QLabel *m_statusLabel;
    QLabel *m_statsLabel;
    QLabel *m_nlpLabel;
    QLabel *m_traceLevelLabel;
    QLabel *m_gpuLabel;
    QLabel *m_cpuLabel;
    PulsatingLabel *m_classificationLabel;
    QLabel *m_logoLabel;
    QLabel *m_titleLabel;

    // Status bar permanent widgets
    QLabel *m_sbFiles;
    QLabel *m_sbMatches;
    QLabel *m_sbElapsed;
    QLabel *m_sbIndex;    // index file count + DB size
    QLabel *m_sbBackend;

    QTabWidget *m_tabs;
    QTextEdit *m_logView;
    QTextEdit *m_dossierView;
    QTextEdit *m_errorsView;
    QTreeWidget *m_resultsTree;
    QTreeWidget *m_entitiesTree;
    QTreeWidget *m_graphTree;
    QTreeWidget *m_timelineTree;  // TIMELINE tab — chronology from scan snippets
    QTreeWidget *m_dupesView;     // DUPES tab — fingerprint deduplication report
    QLineEdit *m_filterInput;

    QSplitter *m_splitter;

    QCheckBox *m_chkIndex;
    QCheckBox *m_chkNLP;
    QCheckBox *m_chkFuzzy;
    QSpinBox *m_spinWorkers;
    QDoubleSpinBox *m_spinMinScore;
    QSpinBox *m_spinLimit;

    QProcess *m_process;
    QProcess *m_indexProcess;   // separate process for "index" command
    QProcess *m_dedupProcess;   // separate process for "dedup" command
    QTimer *m_timer;
    QTimer *m_statusFlashTimer;
    QTimer *m_elapsedTimer;
    HwMonitorThread *m_hwThread = nullptr;

    QString m_selectedPath;
    int m_totalFiles = 0;
    int m_processedFiles = 0;
    int m_matchedFiles = 0;
    int m_errors = 0;
    int m_traceLevel = 0;
    QDateTime m_startTime;
    bool m_statusFlashState = false;
    bool m_userStopped = false;

    // Entity tracking for graph tab
    QMap<QString, QMap<QString, int>> m_entityMap; // type -> {name -> count}

    // Entity co-occurrence tracking: "NameA|NameB" -> count (sorted pair)
    QMap<QString, int> m_coocMap;

    // ═══════════════════════════════════════════════════════════════════════════
    // PATH INITIALIZATION
    // ═══════════════════════════════════════════════════════════════════════════

    void initializePaths() {
        QString exeDir = QCoreApplication::applicationDirPath();

        qDebug() << "=== ASTREX Path Resolution ===";
        qDebug() << "Executable directory:" << exeDir;
        qDebug() << "Current working directory:" << QDir::currentPath();

        QStringList searchPaths;

        QDir parentDir(exeDir);
        if (parentDir.cdUp()) {
            searchPaths << parentDir.absolutePath() + "/astrex.py";
        }
        searchPaths << exeDir + "/astrex.py";
        searchPaths << QDir::currentPath() + "/astrex.py";

        QDir cwdParent(QDir::currentPath());
        if (cwdParent.cdUp()) {
            searchPaths << cwdParent.absolutePath() + "/astrex.py";
        }

        if (exeDir.endsWith("/ui") || exeDir.endsWith("\\ui")) {
            QString stripped = exeDir;
            stripped.chop(3);
            searchPaths << stripped + "/astrex.py";
        }

        searchPaths << "../astrex.py";
        searchPaths << "./astrex.py";

        m_backendFound = false;
        for (const QString &path : searchPaths) {
            QFileInfo fi(path);
            QString absolutePath = fi.absoluteFilePath();
            qDebug() << "Checking:" << absolutePath;

            if (fi.exists() && fi.isFile()) {
                m_astrexPy = absolutePath;
                m_projectDir = fi.absolutePath();
                m_backendFound = true;
                qDebug() << "FOUND astrex.py at:" << m_astrexPy;
                qDebug() << "Project directory:" << m_projectDir;
                break;
            }
        }

        if (!m_backendFound) {
            QDir defaultDir(exeDir);
            defaultDir.cdUp();
            m_projectDir = defaultDir.absolutePath();
            m_astrexPy = m_projectDir + "/astrex.py";

            qDebug() << "WARNING: astrex.py NOT FOUND!";
            qDebug() << "Searched paths:" << searchPaths;
        }
    }

    void checkPythonBackend() {
        if (m_backendFound) {
            m_nlpLabel->setText("Backend: OK");
            m_nlpLabel->setStyleSheet("color: #00FF41;");
            m_sbBackend->setText(" Backend: OK ");
            m_sbBackend->setStyleSheet("color: #00FF41; font-weight: bold;");
        } else {
            m_nlpLabel->setText("Backend: NOT FOUND");
            m_nlpLabel->setStyleSheet("color: #FF0000;");
            m_sbBackend->setText(" Backend: MISSING ");
            m_sbBackend->setStyleSheet("color: #FF0000; font-weight: bold;");

            QMessageBox::warning(this, "Backend Not Found",
                QString("Python backend (astrex.py) not found!\n\n"
                        "Expected location: %1\n\n"
                        "Please ensure the directory structure is:\n"
                        "astrex_v3/\n"
                        "├── astrex.py       <- Python backend HERE\n"
                        "├── core/\n"
                        "├── extractors/\n"
                        "└── ui/\n"
                        "    └── Astrex      <- This executable\n\n"
                        "The executable should be in the 'ui' subdirectory.")
                .arg(m_astrexPy));
        }
    }

    void onHwSnapshot(HwSnapshot snap) {
        // CPU label
        if (snap.cpuPercent >= 0) {
            m_cpuLabel->setText(QString("CPU: %1%").arg(snap.cpuPercent));
            if (snap.cpuPercent < 50)
                m_cpuLabel->setStyleSheet("color: #00FF41;");
            else if (snap.cpuPercent < 80)
                m_cpuLabel->setStyleSheet("color: #FFAA00;");
            else
                m_cpuLabel->setStyleSheet("color: #FF4444;");
        }

        // GPU label
        if (!snap.gpuText.isEmpty()) {
            m_gpuLabel->setText(snap.gpuText);
            if (snap.gpuPercent < 0)
                m_gpuLabel->setStyleSheet("color: #00BFFF;");
            else if (snap.gpuPercent < 50)
                m_gpuLabel->setStyleSheet("color: #00FF41;");
            else if (snap.gpuPercent < 80)
                m_gpuLabel->setStyleSheet("color: #FFAA00;");
            else
                m_gpuLabel->setStyleSheet("color: #FF4444;");
        }
    }

    // ── Logo finder ─────────────────────────────────────────────────────────

    QString findLogo() {
        QStringList logoPaths = {
            m_projectDir + "/ui/resources/gradx_logo.png",
            QCoreApplication::applicationDirPath() + "/resources/gradx_logo.png",
            QDir::currentPath() + "/resources/gradx_logo.png",
            ":/resources/gradx_logo.png",
            "./resources/gradx_logo.png",
            "../ui/resources/gradx_logo.png"
        };

        for (const QString &path : logoPaths) {
            if (QFileInfo::exists(path)) {
                return path;
            }
        }
        return "";
    }

    // ═══════════════════════════════════════════════════════════════════════════
    // STYLE SHEETS (UNCHANGED THEMES)
    // ═══════════════════════════════════════════════════════════════════════════

    QString getStyleSheet(SecurityMode mode) {
        if (mode == MODE_TOP_SECRET) {
            return R"(
                QMainWindow { background-color: #050505; }
                QWidget { background-color: #050505; color: #00FF41; font-family: 'JetBrains Mono', 'Consolas', 'Courier New', monospace; font-size: 13px; }
                QLineEdit { background-color: #0F0F0F; border: 1px solid #00FF41; border-radius: 3px; padding: 8px; color: #00FF41; }
                QLineEdit:focus { border: 2px solid #00FF41; }
                QPushButton { background-color: #1A1A1A; border: 1px solid #00FF41; border-radius: 3px; padding: 8px 16px; color: #00FF41; font-weight: bold; }
                QPushButton:hover { background-color: #00FF41; color: #000000; }
                QPushButton:pressed { background-color: #00CC33; }
                QPushButton:disabled { background-color: #1A1A1A; border-color: #333333; color: #333333; }
                QTextEdit { background-color: #080808; border: 1px solid #1A3A3A; border-radius: 3px; color: #00F0FF; padding: 5px; }
                QTreeWidget { background-color: #080808; border: 1px solid #1A3A3A; border-radius: 3px; color: #00F0FF; }
                QTreeWidget::item:selected { background-color: #003333; }
                QTreeWidget::item:hover { background-color: #0A1F1F; }
                QHeaderView::section { background-color: #111111; color: #00FF41; padding: 5px; border: 1px solid #1A3A3A; }
                QProgressBar { border: 1px solid #1A3A3A; border-radius: 3px; background-color: #0A0A0A; text-align: center; color: #00FF41; font-weight: bold; }
                QProgressBar::chunk { background-color: #006622; }
                QTabWidget::pane { border: 1px solid #1A3A3A; background-color: #080808; }
                QTabBar::tab { background-color: #111111; color: #666666; padding: 10px 20px; border: 1px solid #222222; border-bottom: none; }
                QTabBar::tab:selected { color: #00FF41; border-bottom: 2px solid #00FF41; background-color: #151515; }
                QTabBar::tab:hover { color: #00FF41; background-color: #1A1A1A; }
                QLabel { color: #00FF41; }
                QCheckBox { color: #00FF41; }
                QCheckBox::indicator { border: 1px solid #00FF41; background-color: #0A0A0A; width: 15px; height: 15px; }
                QCheckBox::indicator:checked { background-color: #00FF41; }
                QSpinBox, QDoubleSpinBox { background-color: #0F0F0F; border: 1px solid #00FF41; border-radius: 3px; padding: 5px; color: #00FF41; }
                QComboBox { background-color: #0F0F0F; border: 1px solid #00FF41; border-radius: 3px; padding: 8px; color: #00FF41; }
                QComboBox:focus { border: 2px solid #00FF41; }
                QComboBox::drop-down { border: none; background-color: #1A1A1A; width: 30px; }
                QComboBox::down-arrow { image: none; border-left: 5px solid transparent; border-right: 5px solid transparent; border-top: 6px solid #00FF41; margin-right: 10px; }
                QComboBox QAbstractItemView { background-color: #0F0F0F; border: 1px solid #00FF41; color: #00FF41; selection-background-color: #003300; }
                QMenuBar { background-color: #0A0A0A; color: #00FF41; }
                QMenuBar::item:selected { background-color: #1A1A1A; }
                QMenu { background-color: #0A0A0A; border: 1px solid #00FF41; }
                QMenu::item:selected { background-color: #00FF41; color: #000000; }
                QStatusBar { background-color: #0A0A0A; color: #00FF41; }
                QToolBar { background-color: #0A0A0A; border-bottom: 1px solid #1A3A3A; spacing: 5px; padding: 3px; }
                QToolBar QToolButton { background-color: #1A1A1A; border: 1px solid #00FF41; border-radius: 3px; padding: 6px 12px; color: #00FF41; font-weight: bold; }
                QToolBar QToolButton:hover { background-color: #00FF41; color: #000000; }
                QToolBar QToolButton:disabled { border-color: #333333; color: #333333; }
                QSplitter::handle { background-color: #1A3A3A; }
                QToolTip { background-color: #111111; color: #00FF41; border: 1px solid #00FF41; padding: 4px; font-size: 12px; }
            )";
        } else {
            return R"(
                QMainWindow { background-color: #0A0000; }
                QWidget { background-color: #0A0000; color: #FF0000; font-family: 'JetBrains Mono', 'Consolas', 'Courier New', monospace; font-size: 13px; }
                QLineEdit { background-color: #1A0000; border: 1px solid #FF0000; border-radius: 3px; padding: 8px; color: #FF3333; }
                QLineEdit:focus { border: 2px solid #FF0000; background-color: #200000; }
                QPushButton { background-color: #1A0000; border: 1px solid #FF0000; border-radius: 3px; padding: 8px 16px; color: #FF0000; font-weight: bold; }
                QPushButton:hover { background-color: #FF0000; color: #000000; }
                QPushButton:pressed { background-color: #CC0000; }
                QPushButton:disabled { background-color: #1A0000; border-color: #330000; color: #330000; }
                QTextEdit { background-color: #0D0000; border: 1px solid #3A1A1A; border-radius: 3px; color: #FF6666; padding: 5px; }
                QTreeWidget { background-color: #0D0000; border: 1px solid #3A1A1A; border-radius: 3px; color: #FF6666; }
                QTreeWidget::item:selected { background-color: #330000; }
                QTreeWidget::item:hover { background-color: #1F0A0A; }
                QHeaderView::section { background-color: #150000; color: #FF0000; padding: 5px; border: 1px solid #3A1A1A; }
                QProgressBar { border: 1px solid #3A1A1A; border-radius: 3px; background-color: #100000; text-align: center; color: #FF0000; font-weight: bold; }
                QProgressBar::chunk { background-color: #660000; }
                QTabWidget::pane { border: 1px solid #3A1A1A; background-color: #0D0000; }
                QTabBar::tab { background-color: #150000; color: #663333; padding: 10px 20px; border: 1px solid #2A0000; border-bottom: none; }
                QTabBar::tab:selected { color: #000000; background-color: #FF0000; border-bottom: 2px solid #FF0000; font-weight: bold; }
                QTabBar::tab:hover { color: #FF0000; background-color: #1A0000; }
                QLabel { color: #FF0000; }
                QCheckBox { color: #FF0000; }
                QCheckBox::indicator { border: 1px solid #FF0000; background-color: #100000; width: 15px; height: 15px; }
                QCheckBox::indicator:checked { background-color: #FF0000; }
                QSpinBox, QDoubleSpinBox { background-color: #1A0000; border: 1px solid #FF0000; border-radius: 3px; padding: 5px; color: #FF0000; }
                QComboBox { background-color: #1A0000; border: 1px solid #FF0000; border-radius: 3px; padding: 8px; color: #FF3333; }
                QComboBox:focus { border: 2px solid #FF0000; background-color: #200000; }
                QComboBox::drop-down { border: none; background-color: #1A0000; width: 30px; }
                QComboBox::down-arrow { image: none; border-left: 5px solid transparent; border-right: 5px solid transparent; border-top: 6px solid #FF0000; margin-right: 10px; }
                QComboBox QAbstractItemView { background-color: #1A0000; border: 1px solid #FF0000; color: #FF3333; selection-background-color: #330000; }
                QMenuBar { background-color: #0A0000; color: #FF0000; }
                QMenuBar::item:selected { background-color: #1A0000; }
                QMenu { background-color: #0A0000; border: 1px solid #FF0000; }
                QMenu::item:selected { background-color: #FF0000; color: #000000; }
                QStatusBar { background-color: #0A0000; color: #FF0000; }
                QToolBar { background-color: #0A0000; border-bottom: 1px solid #3A1A1A; spacing: 5px; padding: 3px; }
                QToolBar QToolButton { background-color: #1A0000; border: 1px solid #FF0000; border-radius: 3px; padding: 6px 12px; color: #FF0000; font-weight: bold; }
                QToolBar QToolButton:hover { background-color: #FF0000; color: #000000; }
                QToolBar QToolButton:disabled { border-color: #330000; color: #330000; }
                QSplitter::handle { background-color: #3A1A1A; }
                QToolTip { background-color: #150000; color: #FF3333; border: 1px solid #FF0000; padding: 4px; font-size: 12px; }
            )";
        }
    }

    // ═══════════════════════════════════════════════════════════════════════════
    // UI SETUP
    // ═══════════════════════════════════════════════════════════════════════════

    void setupUI() {
        m_centralWidget = new QWidget();
        QVBoxLayout *mainLayout = new QVBoxLayout(m_centralWidget);
        mainLayout->setSpacing(8);
        mainLayout->setContentsMargins(15, 10, 15, 10);

        // ── HEADER ──
        QHBoxLayout *headerLayout = new QHBoxLayout();
        headerLayout->setSpacing(12);

        m_logoLabel = new QLabel();
        QString logoPath = findLogo();
        if (!logoPath.isEmpty()) {
            QPixmap logo(logoPath);
            if (!logo.isNull()) {
                m_logoLabel->setPixmap(logo.scaled(50, 50, Qt::KeepAspectRatio, Qt::SmoothTransformation));
            }
        }
        headerLayout->addWidget(m_logoLabel);

        m_titleLabel = new QLabel("SYSTEM: ASTREX v3.0");
        m_titleLabel->setStyleSheet("font-size: 20px; font-weight: bold; letter-spacing: 5px;");
        headerLayout->addWidget(m_titleLabel);

        headerLayout->addStretch();

        m_cpuLabel = new QLabel("CPU: ---");
        m_cpuLabel->setStyleSheet("color: #00BFFF;");
        m_cpuLabel->setToolTip("CPU usage and load");
        headerLayout->addWidget(m_cpuLabel);

        m_gpuLabel = new QLabel("GPU: ---");
        m_gpuLabel->setStyleSheet("color: #FFAA00;");
        m_gpuLabel->setToolTip("GPU usage and memory");
        headerLayout->addWidget(m_gpuLabel);

        m_nlpLabel = new QLabel("Backend: checking...");
        m_nlpLabel->setToolTip("Python backend connection status");
        headerLayout->addWidget(m_nlpLabel);

        m_btnModeToggle = new QPushButton("MODE: TOP SECRET");
        m_btnModeToggle->setMinimumWidth(180);
        m_btnModeToggle->setToolTip("Toggle between TOP SECRET and SAP security modes");
        headerLayout->addWidget(m_btnModeToggle);

        m_classificationLabel = new PulsatingLabel(" TOP SECRET // SI // NOFORN ");
        headerLayout->addWidget(m_classificationLabel);

        mainLayout->addLayout(headerLayout);

        // ── PATH SELECTION ──
        QHBoxLayout *pathLayout = new QHBoxLayout();
        QLabel *targetLabel = new QLabel("TARGET:");
        targetLabel->setToolTip("Directory to scan for files");
        pathLayout->addWidget(targetLabel);
        m_pathInput = new QLineEdit(m_selectedPath);
        m_pathInput->setPlaceholderText("Select target directory...");
        m_pathInput->setToolTip("Path to target directory (Ctrl+O to browse)");
        m_btnBrowse = new QPushButton("BROWSE");
        m_btnBrowse->setToolTip("Browse for target directory (Ctrl+O)");
        m_btnIndex = new QPushButton("INDEX");
        m_btnIndex->setToolTip("Pre-index this directory: extract text from all files into SQLite cache.\n"
                               "Makes repeated scans much faster. Progress shown in LIVE_LOG.");
        m_btnIndex->setMinimumWidth(80);
        pathLayout->addWidget(m_pathInput, 1);
        pathLayout->addWidget(m_btnBrowse);
        pathLayout->addWidget(m_btnIndex);
        mainLayout->addLayout(pathLayout);

        // ── QUERY INPUT (ComboBox with history) ──
        QHBoxLayout *queryLayout = new QHBoxLayout();
        QLabel *queryLabel = new QLabel("QUERY:");
        queryLabel->setToolTip("Search query with morphological expansion");
        queryLayout->addWidget(queryLabel);

        m_queryCombo = new QComboBox();
        m_queryCombo->setEditable(true);
        m_queryCombo->setInsertPolicy(QComboBox::NoInsert);
        m_queryCombo->setSizePolicy(QSizePolicy::Expanding, QSizePolicy::Fixed);
        m_queryCombo->lineEdit()->setPlaceholderText("Enter search query... (Ctrl+L to focus)");
        m_queryCombo->setToolTip("Search query — press Enter or click SCAN to start. History available in dropdown.");
        m_queryCombo->setMaxCount(20);
        queryLayout->addWidget(m_queryCombo, 1);

        m_btnScan = new QPushButton("SCAN");
        m_btnScan->setMinimumWidth(100);
        m_btnScan->setToolTip("Start scanning (Enter)");
        m_btnStop = new QPushButton("STOP");
        m_btnStop->setEnabled(false);
        m_btnStop->setToolTip("Stop current scan (Escape)");
        m_btnClear = new QPushButton("CLEAR");
        m_btnClear->setToolTip("Clear all results and logs");

        queryLayout->addWidget(m_btnScan);
        queryLayout->addWidget(m_btnStop);
        queryLayout->addWidget(m_btnClear);
        mainLayout->addLayout(queryLayout);

        // ── OPTIONS ──
        QHBoxLayout *optionsLayout = new QHBoxLayout();

        m_chkIndex = new QCheckBox("Index Cache");
        m_chkIndex->setChecked(true);
        m_chkIndex->setToolTip("Use SQLite index cache for faster repeated scans");
        m_chkNLP = new QCheckBox("NLP Analysis");
        m_chkNLP->setChecked(true);
        m_chkNLP->setToolTip("Enable NLP: entity extraction, relevance scoring, morphology");
        m_chkFuzzy = new QCheckBox("Fuzzy Search");
        m_chkFuzzy->setChecked(true);
        m_chkFuzzy->setToolTip("Enable fuzzy string matching (rapidfuzz, threshold 80)");

        optionsLayout->addWidget(m_chkIndex);
        optionsLayout->addWidget(m_chkNLP);
        optionsLayout->addWidget(m_chkFuzzy);
        optionsLayout->addSpacing(20);

        QLabel *lblWorkers = new QLabel("Workers:");
        lblWorkers->setToolTip("Number of parallel threads for file processing");
        optionsLayout->addWidget(lblWorkers);
        m_spinWorkers = new QSpinBox();
        m_spinWorkers->setRange(1, 128);
        m_spinWorkers->setValue(QThread::idealThreadCount() * 2);
        m_spinWorkers->setToolTip("Thread count (default: 2x CPU cores)");
        optionsLayout->addWidget(m_spinWorkers);

        QLabel *lblScore = new QLabel("Min Score:");
        lblScore->setToolTip("Minimum relevance score to include in results (0.0 - 1.0)");
        optionsLayout->addWidget(lblScore);
        m_spinMinScore = new QDoubleSpinBox();
        m_spinMinScore->setRange(0.0, 1.0);
        m_spinMinScore->setSingleStep(0.1);
        m_spinMinScore->setValue(0.1);
        m_spinMinScore->setToolTip("SBERT relevance threshold (0.0 = all, 1.0 = exact)");
        optionsLayout->addWidget(m_spinMinScore);

        QLabel *lblLimit = new QLabel("Limit:");
        lblLimit->setToolTip("Maximum number of results to return");
        optionsLayout->addWidget(lblLimit);
        m_spinLimit = new QSpinBox();
        m_spinLimit->setRange(10, 10000);
        m_spinLimit->setValue(500);
        m_spinLimit->setToolTip("Max results count");
        optionsLayout->addWidget(m_spinLimit);

        optionsLayout->addSpacing(20);
        QLabel *lblType = new QLabel("Show:");
        lblType->setToolTip("Filter displayed results by file type");
        optionsLayout->addWidget(lblType);
        m_extFilterCombo = new QComboBox();
        m_extFilterCombo->addItems({
            "All Types",
            "Documents",   // pdf, docx, doc, odt, rtf, txt, md
            "Email",       // eml, msg, pst, mbox
            "Archives",    // zip, rar, 7z, tar, gz
            "Images",      // png, jpg, jpeg, tiff (OCR)
            "Code",        // py, js, cpp, java, go, sh ...
            "Database",    // db, sqlite, sql, mdb
            "Spreadsheets" // xlsx, xls, csv, ods
        });
        m_extFilterCombo->setToolTip("Show only results matching the selected file type");
        m_extFilterCombo->setMinimumWidth(120);
        optionsLayout->addWidget(m_extFilterCombo);

        optionsLayout->addStretch();
        mainLayout->addLayout(optionsLayout);

        // ── PROGRESS & STATUS ──
        QHBoxLayout *progressLayout = new QHBoxLayout();
        m_statusLabel = new QLabel("STATUS: IDLE");
        m_traceLevelLabel = new QLabel("");
        m_traceLevelLabel->setVisible(false);
        m_statsLabel = new QLabel("Files: 0/0 | Matches: 0 | Errors: 0 | Speed: 0/s");

        progressLayout->addWidget(m_statusLabel);
        progressLayout->addWidget(m_traceLevelLabel);
        progressLayout->addStretch();
        progressLayout->addWidget(m_statsLabel);
        mainLayout->addLayout(progressLayout);

        m_progressBar = new QProgressBar();
        m_progressBar->setFormat("%p% COMPLETE");
        m_progressBar->setMinimumHeight(22);
        mainLayout->addWidget(m_progressBar);

        // ── FILTER INPUT (above results) ──
        QHBoxLayout *filterLayout = new QHBoxLayout();
        QLabel *filterLabel = new QLabel("FILTER:");
        filterLabel->setToolTip("Filter results by filename or path (Ctrl+F)");
        filterLayout->addWidget(filterLabel);
        m_filterInput = new QLineEdit();
        m_filterInput->setPlaceholderText("Type to filter results... (Ctrl+F)");
        m_filterInput->setToolTip("Real-time filter — matches against filename and path columns");
        m_filterInput->setClearButtonEnabled(true);
        filterLayout->addWidget(m_filterInput, 1);
        mainLayout->addLayout(filterLayout);

        // ── MAIN CONTENT ──
        m_splitter = new QSplitter(Qt::Horizontal);

        m_tabs = new QTabWidget();

        // Tab: LIVE_LOG
        m_logView = new QTextEdit();
        m_logView->setReadOnly(true);
        m_tabs->addTab(m_logView, "LIVE_LOG");

        // Tab: RESULTS (sortable tree)
        // Columns: 0=File  1=Score  2=Type  3=Path  4=Size
        m_resultsTree = new QTreeWidget();
        m_resultsTree->setHeaderLabels({"File", "Score", "Type", "Path", "Size"});
        m_resultsTree->setSortingEnabled(true);
        m_resultsTree->sortByColumn(1, Qt::DescendingOrder);
        m_resultsTree->setAlternatingRowColors(true);
        m_resultsTree->setRootIsDecorated(false);
        m_resultsTree->setContextMenuPolicy(Qt::CustomContextMenu);
        m_resultsTree->header()->setSectionResizeMode(0, QHeaderView::Interactive);
        m_resultsTree->header()->setSectionResizeMode(1, QHeaderView::ResizeToContents);
        m_resultsTree->header()->setSectionResizeMode(2, QHeaderView::ResizeToContents);
        m_resultsTree->header()->setSectionResizeMode(3, QHeaderView::Stretch);
        m_resultsTree->header()->setSectionResizeMode(4, QHeaderView::ResizeToContents);
        m_resultsTree->setColumnWidth(0, 230);
        m_resultsTree->setUniformRowHeights(true);
        m_tabs->addTab(m_resultsTree, "RESULTS");

        // Tab: DOSSIER
        m_dossierView = new QTextEdit();
        m_dossierView->setReadOnly(true);
        m_tabs->addTab(m_dossierView, "DOSSIER");

        // Tab: GRAPH (entity overview)
        QWidget *graphWidget = new QWidget();
        QVBoxLayout *graphLayout = new QVBoxLayout(graphWidget);
        graphLayout->setContentsMargins(0, 0, 0, 0);

        m_graphTree = new QTreeWidget();
        m_graphTree->setHeaderLabels({"Entity / Type", "Mentions", "Files"});
        m_graphTree->setRootIsDecorated(true);
        m_graphTree->setAlternatingRowColors(true);
        m_graphTree->header()->setSectionResizeMode(0, QHeaderView::Stretch);
        m_graphTree->header()->setSectionResizeMode(1, QHeaderView::ResizeToContents);
        m_graphTree->header()->setSectionResizeMode(2, QHeaderView::ResizeToContents);
        graphLayout->addWidget(m_graphTree);

        QHBoxLayout *graphBtnLayout = new QHBoxLayout();
        QPushButton *btnExportGraphML = new QPushButton("Export GraphML");
        btnExportGraphML->setToolTip("Export entity graph in GraphML format (for Gephi)");
        QPushButton *btnExportGEXF = new QPushButton("Export GEXF");
        btnExportGEXF->setToolTip("Export entity graph in GEXF format");
        QPushButton *btnExportJSON = new QPushButton("Export JSON");
        btnExportJSON->setToolTip("Export entity graph in JSON format");
        graphBtnLayout->addWidget(btnExportGraphML);
        graphBtnLayout->addWidget(btnExportGEXF);
        graphBtnLayout->addWidget(btnExportJSON);
        graphBtnLayout->addStretch();
        graphLayout->addLayout(graphBtnLayout);

        connect(btnExportGraphML, &QPushButton::clicked, [this]() { exportGraph("graphml"); });
        connect(btnExportGEXF, &QPushButton::clicked, [this]() { exportGraph("gexf"); });
        connect(btnExportJSON, &QPushButton::clicked, [this]() { exportGraph("json"); });

        m_tabs->addTab(graphWidget, "GRAPH");

        // Tab: TIMELINE — chronological events extracted from match snippets
        m_timelineTree = new QTreeWidget();
        m_timelineTree->setHeaderLabels({"Date", "Type", "Source", "Context"});
        m_timelineTree->setSortingEnabled(true);
        m_timelineTree->sortByColumn(0, Qt::AscendingOrder);
        m_timelineTree->setRootIsDecorated(false);
        m_timelineTree->setAlternatingRowColors(true);
        m_timelineTree->header()->setSectionResizeMode(0, QHeaderView::ResizeToContents);
        m_timelineTree->header()->setSectionResizeMode(1, QHeaderView::ResizeToContents);
        m_timelineTree->header()->setSectionResizeMode(2, QHeaderView::Interactive);
        m_timelineTree->header()->setSectionResizeMode(3, QHeaderView::Stretch);
        m_timelineTree->setColumnWidth(2, 180);
        m_tabs->addTab(m_timelineTree, "TIMELINE");

        // Tab: DUPES — fingerprint-based deduplication report
        m_dupesView = new QTreeWidget();
        m_dupesView->setHeaderLabels({"Group / File", "Size", "Similarity", "Path"});
        m_dupesView->setRootIsDecorated(true);
        m_dupesView->setAlternatingRowColors(true);
        m_dupesView->setSortingEnabled(false);
        m_dupesView->header()->setSectionResizeMode(0, QHeaderView::ResizeToContents);
        m_dupesView->header()->setSectionResizeMode(1, QHeaderView::ResizeToContents);
        m_dupesView->header()->setSectionResizeMode(2, QHeaderView::ResizeToContents);
        m_dupesView->header()->setSectionResizeMode(3, QHeaderView::Stretch);
        {
            QWidget *dupesWidget = new QWidget();
            QVBoxLayout *dupesLayout = new QVBoxLayout(dupesWidget);
            dupesLayout->setContentsMargins(2, 2, 2, 2);
            QPushButton *btnDedup = new QPushButton("RUN DEDUP");
            btnDedup->setToolTip("Fingerprint all files in the selected folder and report duplicates (astrex dedup)");
            connect(btnDedup, &QPushButton::clicked, this, &Astrex::startDedup);
            dupesLayout->addWidget(btnDedup);
            dupesLayout->addWidget(m_dupesView);
            m_tabs->addTab(dupesWidget, "DUPES");
        }

        // Tab: ERRORS
        m_errorsView = new QTextEdit();
        m_errorsView->setReadOnly(true);
        m_tabs->addTab(m_errorsView, "ERRORS");

        m_splitter->addWidget(m_tabs);

        // ── ENTITIES PANEL (grouped by type) ──
        QWidget *entitiesPanel = new QWidget();
        QVBoxLayout *entitiesLayout = new QVBoxLayout(entitiesPanel);
        entitiesLayout->setContentsMargins(0, 0, 0, 0);

        QLabel *entTitle = new QLabel("ENTITIES");
        entTitle->setStyleSheet("font-size: 14px; font-weight: bold; padding: 5px;");
        entitiesLayout->addWidget(entTitle);

        m_entitiesTree = new QTreeWidget();
        m_entitiesTree->setHeaderLabels({"Entity", "Type", "Count"});
        m_entitiesTree->setRootIsDecorated(true);
        m_entitiesTree->setAlternatingRowColors(true);
        m_entitiesTree->header()->setSectionResizeMode(0, QHeaderView::Stretch);
        m_entitiesTree->header()->setSectionResizeMode(1, QHeaderView::ResizeToContents);
        m_entitiesTree->header()->setSectionResizeMode(2, QHeaderView::ResizeToContents);
        m_entitiesTree->setMinimumWidth(350);
        entitiesLayout->addWidget(m_entitiesTree);

        m_splitter->addWidget(entitiesPanel);
        m_splitter->setSizes({900, 350});
        mainLayout->addWidget(m_splitter, 1);

        setCentralWidget(m_centralWidget);

        // ── Watermark ──
        m_watermark = new WatermarkWidget(m_centralWidget);
        m_watermark->setGeometry(m_centralWidget->rect());
        m_watermark->raise();
        m_watermark->setEnabled(false);

        QString logo2 = findLogo();
        if (!logo2.isEmpty()) {
            m_watermark->setLogo(QPixmap(logo2));
        }

        m_process = new QProcess(this);
        m_indexProcess = new QProcess(this);
        m_dedupProcess = new QProcess(this);
        m_timer = new QTimer(this);
        m_statusFlashTimer = new QTimer(this);
        m_elapsedTimer = new QTimer(this);

        // Hardware monitor thread — CPU every 300ms, GPU/iGPU every ~900ms (cached)
        m_hwThread = new HwMonitorThread(this);
        connect(m_hwThread, &HwMonitorThread::updated, this, &Astrex::onHwSnapshot, Qt::QueuedConnection);
        m_hwThread->start();

        // ── Status bar with permanent widgets ──
        m_sbFiles = new QLabel(" Files: 0 ");
        m_sbMatches = new QLabel(" Matches: 0 ");
        m_sbElapsed = new QLabel(" Elapsed: 00:00 ");
        m_sbIndex = new QLabel(" Index: — ");
        m_sbIndex->setToolTip("Number of files in the SQLite index and DB size");
        m_sbBackend = new QLabel(" Backend: ... ");

        statusBar()->addPermanentWidget(m_sbFiles);
        statusBar()->addPermanentWidget(m_sbMatches);
        statusBar()->addPermanentWidget(m_sbElapsed);
        statusBar()->addPermanentWidget(m_sbIndex);
        statusBar()->addPermanentWidget(m_sbBackend);
        statusBar()->showMessage("Ready | GRADX — Supported by PRAXIS");
    }

    void setupToolBar() {
        m_toolBar = addToolBar("Actions");
        m_toolBar->setMovable(false);
        m_toolBar->setIconSize(QSize(18, 18));

        QAction *actScan = m_toolBar->addAction(
            style()->standardIcon(QStyle::SP_MediaPlay), "SCAN");
        actScan->setToolTip("Start scan (Enter)");
        connect(actScan, &QAction::triggered, this, &Astrex::startScan);

        QAction *actStop = m_toolBar->addAction(
            style()->standardIcon(QStyle::SP_MediaStop), "STOP");
        actStop->setToolTip("Stop scan (Escape)");
        actStop->setEnabled(false);
        connect(actStop, &QAction::triggered, this, &Astrex::stopScan);

        m_toolBar->addSeparator();

        QAction *actClear = m_toolBar->addAction(
            style()->standardIcon(QStyle::SP_DialogResetButton), "CLEAR");
        actClear->setToolTip("Clear all results and logs");
        connect(actClear, &QAction::triggered, this, &Astrex::clearAll);

        QAction *actExport = m_toolBar->addAction(
            style()->standardIcon(QStyle::SP_DialogSaveButton), "EXPORT");
        actExport->setToolTip("Export results to JSON or CSV (Ctrl+S)");
        connect(actExport, &QAction::triggered, this, &Astrex::exportResults);

        m_toolBar->addSeparator();

        QAction *actOpen = m_toolBar->addAction(
            style()->standardIcon(QStyle::SP_DirOpenIcon), "BROWSE");
        actOpen->setToolTip("Open target directory (Ctrl+O)");
        connect(actOpen, &QAction::triggered, this, &Astrex::selectDirectory);

        QAction *actStatus = m_toolBar->addAction(
            style()->standardIcon(QStyle::SP_FileDialogInfoView), "STATUS");
        actStatus->setToolTip("Show system status");
        connect(actStatus, &QAction::triggered, this, &Astrex::showStatus);

        // Store toolbar actions for enable/disable during scan
        m_toolBar->setProperty("actStop", QVariant::fromValue(actStop));
        m_toolBar->setProperty("actScan", QVariant::fromValue(actScan));
    }

    void setupMenus() {
        QMenu *fileMenu = menuBar()->addMenu("&File");

        QAction *openAction = new QAction("&Open Directory...", this);
        openAction->setShortcut(QKeySequence::Open);
        connect(openAction, &QAction::triggered, this, &Astrex::selectDirectory);
        fileMenu->addAction(openAction);

        fileMenu->addSeparator();

        QAction *exportAction = new QAction("&Export Results...", this);
        exportAction->setShortcut(QKeySequence::Save);
        connect(exportAction, &QAction::triggered, this, &Astrex::exportResults);
        fileMenu->addAction(exportAction);

        fileMenu->addSeparator();

        QAction *clearHistoryAction = new QAction("Clear Search &History", this);
        connect(clearHistoryAction, &QAction::triggered, [this]() {
            m_queryCombo->clear();
            QSettings settings("GRADX", "ASTREX3");
            settings.remove("searchHistory");
            statusBar()->showMessage("Search history cleared");
        });
        fileMenu->addAction(clearHistoryAction);

        fileMenu->addSeparator();

        QAction *exitAction = new QAction("E&xit", this);
        exitAction->setShortcut(QKeySequence::Quit);
        connect(exitAction, &QAction::triggered, this, &QWidget::close);
        fileMenu->addAction(exitAction);

        QMenu *modeMenu = menuBar()->addMenu("&Mode");

        QAction *topSecretAction = new QAction("TOP SECRET (Green)", this);
        connect(topSecretAction, &QAction::triggered, [this]() {
            m_securityMode = MODE_TOP_SECRET;
            applySecurityMode();
        });
        modeMenu->addAction(topSecretAction);

        QAction *sapAction = new QAction("SAP — Special Access (Red)", this);
        connect(sapAction, &QAction::triggered, [this]() {
            m_securityMode = MODE_SAP;
            applySecurityMode();
        });
        modeMenu->addAction(sapAction);

        QMenu *toolsMenu = menuBar()->addMenu("&Tools");

        QAction *indexAction = new QAction("&Rebuild Index", this);
        connect(indexAction, &QAction::triggered, this, &Astrex::rebuildIndex);
        toolsMenu->addAction(indexAction);

        QAction *statusAction = new QAction("System &Status", this);
        connect(statusAction, &QAction::triggered, this, &Astrex::showStatus);
        toolsMenu->addAction(statusAction);

        toolsMenu->addSeparator();

        QAction *pathsAction = new QAction("Show &Paths", this);
        connect(pathsAction, &QAction::triggered, this, &Astrex::showPaths);
        toolsMenu->addAction(pathsAction);

        QMenu *helpMenu = menuBar()->addMenu("&Help");

        QAction *shortcutsAction = new QAction("&Keyboard Shortcuts", this);
        connect(shortcutsAction, &QAction::triggered, this, &Astrex::showShortcuts);
        helpMenu->addAction(shortcutsAction);

        helpMenu->addSeparator();

        QAction *aboutAction = new QAction("&About ASTREX", this);
        connect(aboutAction, &QAction::triggered, this, &Astrex::showAbout);
        helpMenu->addAction(aboutAction);
    }

    void setupConnections() {
        connect(m_btnBrowse, &QPushButton::clicked, this, &Astrex::selectDirectory);
        connect(m_btnScan, &QPushButton::clicked, this, &Astrex::startScan);
        connect(m_btnStop, &QPushButton::clicked, this, &Astrex::stopScan);
        connect(m_btnClear, &QPushButton::clicked, this, &Astrex::clearAll);
        connect(m_btnIndex, &QPushButton::clicked, this, &Astrex::startIndex);
        connect(m_btnModeToggle, &QPushButton::clicked, this, &Astrex::toggleMode);
        connect(m_queryCombo->lineEdit(), &QLineEdit::returnPressed, this, &Astrex::startScan);

        connect(m_process, &QProcess::readyReadStandardOutput, this, &Astrex::onProcessOutput);
        connect(m_process, &QProcess::readyReadStandardError, this, &Astrex::onProcessError);
        connect(m_process, QOverload<int, QProcess::ExitStatus>::of(&QProcess::finished),
                this, &Astrex::onProcessFinished);
        connect(m_process, &QProcess::errorOccurred, this, &Astrex::onProcessErrorOccurred);

        // Index process — output goes to LIVE_LOG, stderr to ERRORS
        connect(m_indexProcess, &QProcess::readyReadStandardOutput, this, &Astrex::onIndexOutput);
        connect(m_indexProcess, &QProcess::readyReadStandardError,  this, &Astrex::onIndexError);
        connect(m_indexProcess, QOverload<int, QProcess::ExitStatus>::of(&QProcess::finished),
                this, &Astrex::onIndexFinished);

        // Dedup process — results parsed into m_dupesView
        connect(m_dedupProcess, &QProcess::readyReadStandardOutput, this, &Astrex::onDedupOutput);
        connect(m_dedupProcess, &QProcess::readyReadStandardError,  this, &Astrex::onDedupError);
        connect(m_dedupProcess, QOverload<int, QProcess::ExitStatus>::of(&QProcess::finished),
                this, &Astrex::onDedupFinished);

        connect(m_timer, &QTimer::timeout, this, &Astrex::updateStats);
        connect(m_statusFlashTimer, &QTimer::timeout, this, &Astrex::flashStatus);
        connect(m_elapsedTimer, &QTimer::timeout, this, &Astrex::updateElapsed);

        // Single-click: preview snippet without switching tab
        connect(m_resultsTree, &QTreeWidget::currentItemChanged,
                this, [this](QTreeWidgetItem *item, QTreeWidgetItem *) {
                    if (item) onResultSingleClicked(item);
                });

        // Double-click shows full dossier and switches to DOSSIER tab
        connect(m_resultsTree, &QTreeWidget::itemDoubleClicked,
                this, &Astrex::onResultDoubleClicked);

        // Context menu on results
        connect(m_resultsTree, &QTreeWidget::customContextMenuRequested,
                this, &Astrex::onResultsContextMenu);

        // Real-time filter (text + type combo)
        connect(m_filterInput, &QLineEdit::textChanged, this, &Astrex::filterResults);
        connect(m_extFilterCombo, QOverload<int>::of(&QComboBox::currentIndexChanged),
                this, [this](int) { filterResults(m_filterInput->text()); });
    }

    void setupShortcuts() {
        // Ctrl+F: focus filter
        QShortcut *scFilter = new QShortcut(QKeySequence("Ctrl+F"), this);
        connect(scFilter, &QShortcut::activated, [this]() {
            m_filterInput->setFocus();
            m_filterInput->selectAll();
        });

        // Ctrl+L: focus query
        QShortcut *scQuery = new QShortcut(QKeySequence("Ctrl+L"), this);
        connect(scQuery, &QShortcut::activated, [this]() {
            m_queryCombo->setFocus();
            m_queryCombo->lineEdit()->selectAll();
        });

        // Escape: stop scan
        QShortcut *scStop = new QShortcut(QKeySequence(Qt::Key_Escape), this);
        connect(scStop, &QShortcut::activated, this, &Astrex::stopScan);
    }

    // ═══════════════════════════════════════════════════════════════════════════
    // SECURITY MODE
    // ═══════════════════════════════════════════════════════════════════════════

    void applySecurityMode() {
        setStyleSheet(getStyleSheet(m_securityMode));

        if (m_securityMode == MODE_TOP_SECRET) {
            m_classificationLabel->setText(" TOP SECRET // SI // NOFORN ");
            m_classificationLabel->setStyleSheet(
                "color: #000000; background-color: #FF0000; font-weight: bold; padding: 8px 15px;");
            m_classificationLabel->stopPulsing();

            m_btnModeToggle->setText("MODE: TOP SECRET");
            m_btnModeToggle->setStyleSheet(
                "background-color: #004400; border-color: #00FF41; color: #00FF41;");

            m_watermark->setEnabled(false);
            m_traceLevelLabel->setVisible(false);

            m_statusLabel->setText("STATUS: IDLE");
            m_statusLabel->setStyleSheet("color: #00FF41;");

            setWindowTitle("ASTREX v3.0 — Intelligence System [TOP SECRET]");
            statusBar()->showMessage("Mode: TOP SECRET | GRADX — Supported by PRAXIS");

        } else {
            m_classificationLabel->setText(" ⚠ SAP // LETHAL COUNTERMEASURES ACTIVE // EYES ONLY ⚠ ");
            m_classificationLabel->startPulsing();

            m_btnModeToggle->setText("MODE: SAP");
            m_btnModeToggle->setStyleSheet(
                "background-color: #440000; border-color: #FF0000; color: #FF0000;");

            m_watermark->setEnabled(true);
            m_watermark->raise();

            m_traceLevelLabel->setVisible(true);
            m_traceLevelLabel->setText("TRACE LEVEL: 0");
            m_traceLevelLabel->setStyleSheet("color: #FFAA00; font-weight: bold;");

            m_statusLabel->setText("STATUS: STANDBY");
            m_statusLabel->setStyleSheet("color: #FF0000;");

            setWindowTitle("ASTREX v3.0 — Special Access Programs [SAP]");
            statusBar()->showMessage("Mode: SAP — SPECIAL ACCESS REQUIRED | GRADX — Supported by PRAXIS");
        }

        m_watermark->setGeometry(m_centralWidget->rect());
        m_watermark->update();
    }

    void toggleMode() {
        if (m_securityMode == MODE_TOP_SECRET) {
            QMessageBox msgBox(this);
            msgBox.setWindowTitle("⚠ ACCESS VERIFICATION REQUIRED ⚠");
            msgBox.setText(
                "SAP ACCESS VERIFICATION REQUIRED\n\n"
                "You are attempting to access SAP-level systems.\n"
                "Unauthorized access is a federal offense.\n\n"
                "Do you have proper SAP authorization?"
            );
            msgBox.setStandardButtons(QMessageBox::Yes | QMessageBox::No);
            msgBox.setDefaultButton(QMessageBox::No);

            if (msgBox.exec() == QMessageBox::Yes) {
                m_securityMode = MODE_SAP;
            }
        } else {
            m_securityMode = MODE_TOP_SECRET;
        }

        applySecurityMode();
    }

    // ═══════════════════════════════════════════════════════════════════════════
    // SETTINGS (with history + splitter)
    // ═══════════════════════════════════════════════════════════════════════════

    void loadSettings() {
        QSettings settings("GRADX", "ASTREX3");
        m_selectedPath = settings.value("lastPath", QDir::homePath()).toString();
        restoreGeometry(settings.value("geometry").toByteArray());

        // Load search history
        QStringList history = settings.value("searchHistory").toStringList();
        // Will be applied after UI is created (in constructor order)
        QTimer::singleShot(0, [this, history]() {
            for (const QString &q : history) {
                m_queryCombo->addItem(q);
            }
            m_queryCombo->setCurrentText("");
            // Restore splitter
            QSettings s("GRADX", "ASTREX3");
            if (s.contains("splitterState")) {
                m_splitter->restoreState(s.value("splitterState").toByteArray());
            }
        });
    }

    void saveSettings() {
        QSettings settings("GRADX", "ASTREX3");
        settings.setValue("lastPath", m_selectedPath);
        settings.setValue("geometry", saveGeometry());
        settings.setValue("splitterState", m_splitter->saveState());

        // Save search history
        QStringList history;
        for (int i = 0; i < m_queryCombo->count(); ++i) {
            history << m_queryCombo->itemText(i);
        }
        settings.setValue("searchHistory", history);
    }

    void addToHistory(const QString &query) {
        // Remove duplicate if exists
        int idx = m_queryCombo->findText(query);
        if (idx >= 0) m_queryCombo->removeItem(idx);
        // Insert at top
        m_queryCombo->insertItem(0, query);
        m_queryCombo->setCurrentIndex(0);
        // Cap at 20
        while (m_queryCombo->count() > 20) {
            m_queryCombo->removeItem(m_queryCombo->count() - 1);
        }
    }

protected:
    void resizeEvent(QResizeEvent *event) override {
        QMainWindow::resizeEvent(event);
        if (m_watermark) {
            m_watermark->setGeometry(m_centralWidget->rect());
        }
    }

private slots:
    void selectDirectory() {
        QString dir = QFileDialog::getExistingDirectory(
            this, "Select Target Directory", m_selectedPath);
        if (!dir.isEmpty()) {
            m_selectedPath = dir;
            m_pathInput->setText(dir);
        }
    }

    void startScan() {
        QString query = m_queryCombo->currentText().trimmed();
        QString folder = m_pathInput->text().trimmed();

        if (query.isEmpty()) {
            QMessageBox::warning(this, "Error", "Please enter a search query");
            m_queryCombo->setFocus();
            return;
        }
        if (folder.isEmpty() || !QDir(folder).exists()) {
            QMessageBox::warning(this, "Error", "Please select a valid directory");
            return;
        }

        if (!m_backendFound) {
            QMessageBox::critical(this, "Error",
                QString("Python backend not found!\n\nExpected: %1\n\n"
                        "Use Tools -> Show Paths to see configuration.")
                .arg(m_astrexPy));
            return;
        }

        // Prevent concurrent INDEX + SCAN (both write to SQLite)
        if (m_indexProcess->state() != QProcess::NotRunning) {
            QMessageBox::warning(this, "Busy",
                "Indexing is in progress. Stop it before starting a scan.");
            return;
        }

        // Add to history
        addToHistory(query);

        // Reset
        m_logView->clear();
        m_resultsTree->clear();
        m_entitiesTree->clear();
        m_graphTree->clear();
        m_timelineTree->clear();
        m_dupesView->clear();
        m_dossierView->clear();
        m_errorsView->clear();
        m_progressBar->setValue(0);
        m_totalFiles = 0;
        m_processedFiles = 0;
        m_matchedFiles = 0;
        m_errors = 0;
        m_traceLevel = 0;
        m_userStopped = false;
        m_entityMap.clear();
        m_coocMap.clear();
        m_startTime = QDateTime::currentDateTime();

        // Update tab titles
        updateTabBadges();

        // Build command
        QStringList args;
        args << m_astrexPy << "scan" << query << folder;
        args << "--format" << "json";
        args << "--limit" << QString::number(m_spinLimit->value());
        args << "--min-score" << QString::number(m_spinMinScore->value());
        args << "--workers" << QString::number(m_spinWorkers->value());

        if (!m_chkIndex->isChecked()) args << "--no-index";
        if (!m_chkNLP->isChecked()) args << "--no-nlp";
        if (!m_chkFuzzy->isChecked()) args << "--no-fuzzy";

        m_process->setWorkingDirectory(m_projectDir);

        m_logView->append(QString("[%1] Starting scan...")
            .arg(QDateTime::currentDateTime().toString("hh:mm:ss")));
        m_logView->append(QString("[%1] Command: python3 %2")
            .arg(QDateTime::currentDateTime().toString("hh:mm:ss"))
            .arg(args.join(" ")));

        m_process->start("python3", args);

        m_btnScan->setEnabled(false);
        m_btnStop->setEnabled(true);

        // Toggle toolbar actions
        QAction *actScan = m_toolBar->property("actScan").value<QAction*>();
        QAction *actStop = m_toolBar->property("actStop").value<QAction*>();
        if (actScan) actScan->setEnabled(false);
        if (actStop) actStop->setEnabled(true);

        if (m_securityMode == MODE_SAP) {
            m_statusLabel->setText("STATUS: INTERCEPTING...");
            m_statusLabel->setStyleSheet("color: #FFAA00; font-weight: bold;");
            m_statusFlashTimer->start(500);
        } else {
            m_statusLabel->setText("STATUS: SCANNING...");
            m_statusLabel->setStyleSheet("color: #FFAA00;");
        }

        m_timer->start(500);
        m_elapsedTimer->start(1000);
        statusBar()->showMessage("Scanning...");
    }

    void stopScan() {
        if (m_process->state() == QProcess::Running) {
            m_userStopped = true;
            m_process->terminate();
            if (!m_process->waitForFinished(3000)) {
                m_process->kill();
            }
        }
    }

    void clearAll() {
        m_logView->clear();
        m_resultsTree->clear();
        m_entitiesTree->clear();
        m_graphTree->clear();
        m_timelineTree->clear();
        m_dupesView->clear();
        m_dossierView->clear();
        m_errorsView->clear();
        m_filterInput->clear();
        m_progressBar->setValue(0);
        m_traceLevel = 0;
        m_matchedFiles = 0;
        m_errors = 0;
        m_totalFiles = 0;
        m_processedFiles = 0;
        m_entityMap.clear();
        m_coocMap.clear();

        updateTabBadges();

        if (m_securityMode == MODE_SAP) {
            m_statusLabel->setText("STATUS: STANDBY");
            m_traceLevelLabel->setText("TRACE LEVEL: 0");
        } else {
            m_statusLabel->setText("STATUS: IDLE");
        }

        m_statsLabel->setText("Files: 0/0 | Matches: 0 | Errors: 0 | Speed: 0/s");
        m_sbFiles->setText(" Files: 0 ");
        m_sbMatches->setText(" Matches: 0 ");
        m_sbElapsed->setText(" Elapsed: 00:00 ");
        statusBar()->showMessage("Cleared");
    }

    void flashStatus() {
        if (m_securityMode != MODE_SAP) {
            m_statusFlashTimer->stop();
            return;
        }

        m_statusFlashState = !m_statusFlashState;
        m_statusLabel->setStyleSheet(m_statusFlashState ?
            "color: #FF0000; font-weight: bold;" :
            "color: #FFAA00; font-weight: bold;");
    }

    void updateElapsed() {
        if (m_process->state() != QProcess::Running) {
            m_elapsedTimer->stop();
            return;
        }
        int secs = m_startTime.secsTo(QDateTime::currentDateTime());
        int mm = secs / 60;
        int ss = secs % 60;
        m_sbElapsed->setText(QString(" Elapsed: %1:%2 ")
            .arg(mm, 2, 10, QChar('0'))
            .arg(ss, 2, 10, QChar('0')));
    }

    void onProcessErrorOccurred(QProcess::ProcessError error) {
        QString errorMsg;
        switch (error) {
            case QProcess::FailedToStart:
                errorMsg = QString("Failed to start process.\n\n"
                                   "Command: python3 %1\n"
                                   "Working dir: %2\n\n"
                                   "Is Python3 installed?")
                           .arg(m_astrexPy).arg(m_projectDir);
                break;
            case QProcess::Crashed: errorMsg = "Process crashed"; break;
            case QProcess::Timedout: errorMsg = "Process timed out"; break;
            default: errorMsg = "Unknown error"; break;
        }

        m_errorsView->append(QString("[%1] %2")
            .arg(QDateTime::currentDateTime().toString("hh:mm:ss"))
            .arg(errorMsg));
        m_errors++;
        updateTabBadges();

        m_statusLabel->setText("STATUS: ERROR");
        m_statusLabel->setStyleSheet("color: #FF0000;");
    }

    void onProcessOutput() {
        while (m_process->canReadLine()) {
            QByteArray line = m_process->readLine();
            QJsonDocument doc = QJsonDocument::fromJson(line);

            if (doc.isNull()) {
                QString text = QString::fromUtf8(line).trimmed();
                if (!text.isEmpty()) {
                    m_logView->append(QString("[%1] %2")
                        .arg(QDateTime::currentDateTime().toString("hh:mm:ss"))
                        .arg(text));
                }
                continue;
            }

            QJsonObject obj = doc.object();
            QString type = obj["type"].toString();

            if (type == "status") {
                m_logView->append(QString("[%1] %2")
                    .arg(QDateTime::currentDateTime().toString("hh:mm:ss"))
                    .arg(obj["msg"].toString()));

            } else if (type == "progress") {
                m_processedFiles = obj["current"].toInt();
                m_totalFiles = obj["total"].toInt();
                m_progressBar->setMaximum(m_totalFiles > 0 ? m_totalFiles : 100);
                m_progressBar->setValue(m_processedFiles);

                // ETA calculation
                if (m_processedFiles > 0 && m_totalFiles > 0) {
                    double elapsed = m_startTime.msecsTo(QDateTime::currentDateTime()) / 1000.0;
                    double rate = m_processedFiles / elapsed;
                    int remaining = (rate > 0) ? static_cast<int>((m_totalFiles - m_processedFiles) / rate) : 0;
                    int pct = (m_totalFiles > 0) ? (m_processedFiles * 100 / m_totalFiles) : 0;
                    if (remaining > 0) {
                        m_progressBar->setFormat(QString("%1% — ~%2s remaining").arg(pct).arg(remaining));
                    } else {
                        m_progressBar->setFormat(QString("%1% COMPLETE").arg(pct));
                    }
                }

                m_sbFiles->setText(QString(" Files: %1/%2 ").arg(m_processedFiles).arg(m_totalFiles));

                if (m_securityMode == MODE_SAP) {
                    m_traceLevel = qMin(10, m_matchedFiles / 3);
                    m_traceLevelLabel->setText(QString("TRACE LEVEL: %1").arg(m_traceLevel));
                    m_traceLevelLabel->setStyleSheet(
                        m_traceLevel >= 8 ? "color: #FF0000; font-weight: bold;" :
                        m_traceLevel >= 5 ? "color: #FF6600; font-weight: bold;" :
                        "color: #FFAA00; font-weight: bold;");
                }

            } else if (type == "match") {
                m_matchedFiles++;
                QString source = obj["filename"].toString();
                if (source.isEmpty()) source = obj["source"].toString();
                QString path = obj["path"].toString();
                if (path.isEmpty()) path = obj["file_path"].toString();
                double score = obj["score"].toDouble();
                QString snippet = obj["snippet"].toString();
                qint64 fileSize = obj["size"].toVariant().toLongLong();

                // Derive file extension for the Type column
                QString ext = QFileInfo(path).suffix().toUpper();
                if (ext.isEmpty()) ext = "?";

                // Color-code by file type
                static const QMap<QString,QString> extColors = {
                    {"PDF","#FF6B6B"},  {"DOCX","#4ECDC4"}, {"DOC","#4ECDC4"},
                    {"ODT","#45B7D1"}, {"RTF","#45B7D1"},
                    {"TXT","#95E1D3"}, {"MD","#95E1D3"},
                    {"EML","#F7B731"}, {"MSG","#F7B731"}, {"PST","#F0A500"},
                    {"MBOX","#F0A500"},
                    {"ZIP","#A55EEA"}, {"RAR","#A55EEA"}, {"7Z","#A55EEA"},
                    {"TAR","#8E44AD"}, {"GZ","#8E44AD"},
                    {"PNG","#00BFFF"}, {"JPG","#00BFFF"}, {"JPEG","#00BFFF"},
                    {"TIFF","#00BFFF"}, {"TIF","#00BFFF"}, {"BMP","#00BFFF"},
                    {"PY","#3DC75D"},  {"JS","#F0DB4F"},  {"TS","#007ACC"},
                    {"CPP","#9B4DCA"}, {"C","#9B4DCA"},   {"H","#9B4DCA"},
                    {"JAVA","#ED8B00"},{"GO","#00ADD8"},   {"RS","#DEA584"},
                    {"SH","#89E051"},  {"BASH","#89E051"},
                    {"XLSX","#21A366"},{"XLS","#21A366"},  {"ODS","#21A366"},
                    {"CSV","#98D8AA"},
                    {"DB","#FF8C00"},  {"SQLITE","#FF8C00"},{"SQL","#FFA500"},
                    {"MDB","#FF8C00"},
                    {"JSON","#FFCC00"},{"XML","#E07B39"},   {"YAML","#CC3333"},
                    {"HTML","#E34C26"},{"HTM","#E34C26"},
                };
                QString extColor = extColors.value(ext, "#888888");

                // Results tree — columns: 0=File 1=Score 2=Type 3=Path 4=Size
                QTreeWidgetItem *item = new QTreeWidgetItem();
                item->setText(0, source);
                item->setText(1, QString::number(score, 'f', 3));
                item->setData(1, Qt::UserRole, score); // for proper numeric sorting
                item->setText(2, ext);
                item->setForeground(2, QColor(extColor));
                item->setText(3, path);
                item->setText(4, fileSize > 0 ? formatSize(fileSize) : "");
                item->setData(0, Qt::UserRole, snippet);
                item->setData(0, Qt::UserRole + 1, path); // full path for context menu

                // Tooltip shows snippet so user can see content without clicking
                if (!snippet.isEmpty()) {
                    QString tip = snippet.left(500);
                    item->setToolTip(0, tip);
                    item->setToolTip(3, tip);
                }

                m_resultsTree->addTopLevelItem(item);

                m_sbMatches->setText(QString(" Matches: %1 ").arg(m_matchedFiles));

                // Process entities — Python sends dict {"TYPE": ["val1", ...]}
                QJsonValue entVal = obj["entities"];
                if (entVal.isObject()) {
                    QJsonObject entObj = entVal.toObject();
                    for (auto it = entObj.begin(); it != entObj.end(); ++it) {
                        QString eType = it.key().toUpper();
                        QJsonArray arr = it.value().toArray();
                        for (const QJsonValue &v : arr) {
                            QString eName = v.toString().trimmed();
                            if (!eName.isEmpty()) {
                                m_entityMap[eType][eName]++;
                            }
                        }
                    }
                } else if (entVal.isArray()) {
                    // Legacy format: [{"text": "...", "type": "..."}]
                    for (const QJsonValue &ev : entVal.toArray()) {
                        QJsonObject ent = ev.toObject();
                        QString eName = ent["text"].toString();
                        if (eName.isEmpty()) eName = ent["name"].toString();
                        QString eType = ent["type"].toString().toUpper();
                        if (eName.isEmpty() || eType.isEmpty()) continue;
                        m_entityMap[eType][eName]++;
                    }
                }

                // ── TIMELINE: extract dates from snippet ──────────────────
                if (!snippet.isEmpty()) {
                    static const QRegularExpression reISO(
                        R"(\b(\d{4})-(\d{2})-(\d{2})\b)");
                    static const QRegularExpression reDMY(
                        R"(\b(\d{1,2})[./](\d{1,2})[./](\d{4})\b)");

                    auto addTimelineEvent = [&](const QString &dateStr,
                                                const QString &ctx) {
                        QTreeWidgetItem *tl = new QTreeWidgetItem();
                        tl->setText(0, dateStr);
                        tl->setText(1, "mention");
                        tl->setText(2, source);
                        tl->setText(3, ctx.left(120).replace('\n', ' '));
                        tl->setToolTip(3, ctx.left(500));
                        m_timelineTree->addTopLevelItem(tl);
                    };

                    QRegularExpressionMatchIterator itISO = reISO.globalMatch(snippet);
                    while (itISO.hasNext()) {
                        QRegularExpressionMatch m = itISO.next();
                        // Validate: year 1900-2100, month 1-12, day 1-31
                        int y = m.captured(1).toInt(), mo = m.captured(2).toInt(),
                            d = m.captured(3).toInt();
                        if (y >= 1900 && y <= 2100 && mo >= 1 && mo <= 12 && d >= 1 && d <= 31) {
                            int start = qMax(0, m.capturedStart() - 80);
                            addTimelineEvent(m.captured(0), snippet.mid(start, 200));
                        }
                    }

                    QRegularExpressionMatchIterator itDMY = reDMY.globalMatch(snippet);
                    while (itDMY.hasNext()) {
                        QRegularExpressionMatch m = itDMY.next();
                        int day = m.captured(1).toInt(), mo = m.captured(2).toInt(),
                            y = m.captured(3).toInt();
                        if (y >= 1900 && y <= 2100 && mo >= 1 && mo <= 12 && day >= 1 && day <= 31) {
                            // Normalize to YYYY-MM-DD for sorting
                            QString iso = QString("%1-%2-%3")
                                .arg(y).arg(mo, 2, 10, QChar('0')).arg(day, 2, 10, QChar('0'));
                            int start = qMax(0, m.capturedStart() - 80);
                            addTimelineEvent(iso, snippet.mid(start, 200));
                        }
                    }
                }

                // ── CO-OCCURRENCE: collect entity pairs from this match ───
                {
                    QStringList allNames;
                    for (auto it = m_entityMap.constBegin(); it != m_entityMap.constEnd(); ++it) {
                        int taken = 0;
                        for (auto jt = it.value().constBegin();
                             jt != it.value().constEnd() && taken < 3; ++jt, ++taken) {
                            allNames << jt.key();
                        }
                    }
                    // Build all pairs (sorted lexicographically to avoid duplicates)
                    for (int ai = 0; ai < allNames.size(); ++ai) {
                        for (int bi = ai + 1; bi < allNames.size(); ++bi) {
                            QString a = allNames[ai], b = allNames[bi];
                            if (a > b) std::swap(a, b);
                            m_coocMap[a + "|" + b]++;
                        }
                    }
                }

                m_logView->append(QString("[%1] %2: %3 (score: %4)")
                    .arg(QDateTime::currentDateTime().toString("hh:mm:ss"))
                    .arg(m_securityMode == MODE_SAP ? "TARGET" : "MATCH")
                    .arg(source)
                    .arg(score, 0, 'f', 3));

                updateTabBadges();

            } else if (type == "complete" || type == "stats") {
                m_totalFiles = obj["total_files"].toInt();
                if (m_totalFiles == 0) m_totalFiles = obj["total"].toInt();
                m_matchedFiles = obj["matched_files"].toInt();
                if (m_matchedFiles == 0) m_matchedFiles = obj["matched"].toInt();

                m_logView->append(m_securityMode == MODE_SAP ?
                    "\n▓▓▓ ACQUISITION COMPLETE ▓▓▓" : "\n═══ SCAN COMPLETE ═══");
                m_logView->append(QString("Total: %1 files, Matches: %2")
                    .arg(m_totalFiles).arg(m_matchedFiles));

                updateTabBadges();
                rebuildEntityTree();
                rebuildGraphTree();

            } else if (type == "error") {
                m_errors++;
                m_errorsView->append(QString("[%1] %2")
                    .arg(QDateTime::currentDateTime().toString("hh:mm:ss"))
                    .arg(obj["msg"].toString()));
                updateTabBadges();
            }
        }
    }

    void onProcessError() {
        QString error = m_process->readAllStandardError();
        if (!error.isEmpty()) {
            for (const QString &line : error.split('\n')) {
                QString t = line.trimmed();
                if (!t.isEmpty() && !t.contains("[INFO]") && !t.startsWith("Loading")) {
                    m_errorsView->append(t);
                }
            }
        }
    }

    void onProcessFinished(int exitCode, QProcess::ExitStatus status) {
        m_timer->stop();
        m_statusFlashTimer->stop();
        m_elapsedTimer->stop();
        m_btnScan->setEnabled(true);
        m_btnStop->setEnabled(false);

        QAction *actScan = m_toolBar->property("actScan").value<QAction*>();
        QAction *actStop = m_toolBar->property("actStop").value<QAction*>();
        if (actScan) actScan->setEnabled(true);
        if (actStop) actStop->setEnabled(false);

        if (m_userStopped) {
            // User pressed Stop — not a crash
            m_statusLabel->setText(m_securityMode == MODE_SAP ?
                "STATUS: ABORTED" : "STATUS: STOPPED");
            m_statusLabel->setStyleSheet("color: #FFAA00;");
            m_progressBar->setFormat("STOPPED");
            m_logView->append(QString("[%1] Scan stopped by user")
                .arg(QDateTime::currentDateTime().toString("hh:mm:ss")));
        } else if (status == QProcess::CrashExit) {
            // Real crash — Python process killed by signal
            QString lastErr = QString::fromUtf8(m_process->readAllStandardError()).trimmed();
            if (!lastErr.isEmpty()) {
                m_errorsView->append(lastErr);
            }

            // Check if it was OOM (signal 9 = SIGKILL from OOM killer)
            bool isOOM = lastErr.contains("MemoryError") || lastErr.contains("out of memory")
                         || exitCode == 137 || exitCode == -9;

            if (isOOM) {
                m_statusLabel->setText("STATUS: OUT OF MEMORY");
                m_errorsView->append(QString("[%1] Process killed: out of memory. "
                    "Try: disable NLP, reduce workers, or scan a smaller folder.")
                    .arg(QDateTime::currentDateTime().toString("hh:mm:ss")));
            } else {
                m_statusLabel->setText("STATUS: CRASHED");
                m_errorsView->append(QString("[%1] Python process crashed (signal %2). "
                    "Check ERRORS tab for details. Possible causes: corrupted file or NLP module error.")
                    .arg(QDateTime::currentDateTime().toString("hh:mm:ss"))
                    .arg(exitCode));
            }

            m_statusLabel->setStyleSheet("color: #FF0000;");
            m_progressBar->setFormat("CRASHED");
            m_errors++;

            m_logView->append(QString("[%1] ERROR: Backend process crashed unexpectedly")
                .arg(QDateTime::currentDateTime().toString("hh:mm:ss")));
        } else if (exitCode != 0) {
            // Python exited with error code
            m_statusLabel->setText("STATUS: ERROR");
            m_statusLabel->setStyleSheet("color: #FF0000;");
            m_progressBar->setFormat("ERROR (exit %1)");

            QString lastErr = QString::fromUtf8(m_process->readAllStandardError()).trimmed();
            if (!lastErr.isEmpty()) {
                m_errorsView->append(lastErr);
            }
            m_errorsView->append(QString("[%1] Process exited with code %2")
                .arg(QDateTime::currentDateTime().toString("hh:mm:ss"))
                .arg(exitCode));
            m_errors++;
        } else {
            // Normal completion
            m_statusLabel->setText(m_securityMode == MODE_SAP ?
                "STATUS: ACQUISITION COMPLETE" : "STATUS: COMPLETE");
            m_statusLabel->setStyleSheet(m_securityMode == MODE_SAP ?
                "color: #FF6666;" : "color: #00FF41;");
            m_progressBar->setValue(m_progressBar->maximum());
            m_progressBar->setFormat("%p% COMPLETE");
        }

        // Final entity/graph rebuild
        rebuildEntityTree();
        rebuildGraphTree();
        updateTabBadges();

        // Auto-generate DOSSIER summary
        buildDossierSummary();

        int secs = m_startTime.secsTo(QDateTime::currentDateTime());
        statusBar()->showMessage(QString("Complete: %1 matches in %2s")
            .arg(m_matchedFiles).arg(secs));
    }

    void updateStats() {
        double elapsed = m_startTime.msecsTo(QDateTime::currentDateTime()) / 1000.0;
        double speed = elapsed > 0 ? m_processedFiles / elapsed : 0;

        m_statsLabel->setText(QString("Files: %1/%2 | Matches: %3 | Errors: %4 | Speed: %5/s")
            .arg(m_processedFiles).arg(m_totalFiles)
            .arg(m_matchedFiles).arg(m_errors)
            .arg(speed, 0, 'f', 0));
    }

    // ═══════════════════════════════════════════════════════════════════════════
    // NEW: Double-click shows in DOSSIER tab
    // ═══════════════════════════════════════════════════════════════════════════

    void onResultDoubleClicked(QTreeWidgetItem *item, int) {
        QString filename = item->text(0);
        QString path = item->text(3);
        QString score = item->text(1);
        QString snippet = item->data(0, Qt::UserRole).toString();

        QString html = QString(
            "<h2 style='color: %1;'>%2</h2>"
            "<p><b>Path:</b> %3</p>"
            "<p><b>Score:</b> %4</p>"
            "<hr>"
            "<h3>Snippet</h3>"
            "<pre style='white-space: pre-wrap; color: %5;'>%6</pre>"
        )
        .arg(m_securityMode == MODE_TOP_SECRET ? "#00FF41" : "#FF3333")
        .arg(filename.toHtmlEscaped())
        .arg(path.toHtmlEscaped())
        .arg(score)
        .arg(m_securityMode == MODE_TOP_SECRET ? "#00F0FF" : "#FF6666")
        .arg(snippet.left(5000).toHtmlEscaped());

        m_dossierView->setHtml(html);
        m_tabs->setCurrentIndex(2); // Switch to DOSSIER tab
    }

    // ═══════════════════════════════════════════════════════════════════════════
    // AUTO: Build DOSSIER summary after scan completes
    // ═══════════════════════════════════════════════════════════════════════════

    void buildDossierSummary() {
        if (m_matchedFiles == 0 && m_resultsTree->topLevelItemCount() == 0) {
            m_dossierView->setHtml(
                "<h2 style='color: #FFAA00;'>No matches found</h2>"
                "<p>Try broadening your search query or scanning a different folder.</p>"
            );
            return;
        }

        int secs = m_startTime.secsTo(QDateTime::currentDateTime());
        QString query = m_queryCombo->currentText().toHtmlEscaped();
        QString folder = m_selectedPath.toHtmlEscaped();
        bool isSAP = (m_securityMode == MODE_SAP);
        QString accent = isSAP ? "#FF3333" : "#00FF41";
        QString accent2 = isSAP ? "#FF6666" : "#00F0FF";

        // --- Header ---
        QString html = QString(
            "<div style='font-family: monospace;'>"
            "<h1 style='color: %1;'>%2</h1>"
            "<table style='color: #CCCCCC; font-size: 14px;'>"
            "<tr><td><b>Query:</b></td><td style='color: %3;'> %4</td></tr>"
            "<tr><td><b>Folder:</b></td><td> %5</td></tr>"
            "<tr><td><b>Files scanned:</b></td><td> %6</td></tr>"
            "<tr><td><b>Matches:</b></td><td style='color: %3;'> %7</td></tr>"
            "<tr><td><b>Errors:</b></td><td> %8</td></tr>"
            "<tr><td><b>Duration:</b></td><td> %9s</td></tr>"
            "</table>"
            "<hr style='border-color: #444;'>"
        )
        .arg(accent)
        .arg(isSAP ? "ACQUISITION DOSSIER" : "SCAN DOSSIER")
        .arg(accent2)
        .arg(query)
        .arg(folder)
        .arg(m_totalFiles)
        .arg(m_matchedFiles)
        .arg(m_errors)
        .arg(secs);

        // --- Top results ---
        int resultCount = m_resultsTree->topLevelItemCount();
        int showMax = qMin(resultCount, 20);

        html += QString("<h2 style='color: %1;'>Top Results (%2)</h2>")
            .arg(accent2).arg(resultCount);
        html += "<table style='width:100%; color: #CCCCCC; font-size: 13px; "
                "border-collapse: collapse;'>"
                "<tr style='color: #888; border-bottom: 1px solid #444;'>"
                "<th align='left'>File</th>"
                "<th align='left'>Score</th>"
                "<th align='left'>Path</th></tr>";

        for (int i = 0; i < showMax; ++i) {
            QTreeWidgetItem *item = m_resultsTree->topLevelItem(i);
            QString fname = item->text(0).toHtmlEscaped();
            QString score = item->text(1);
            QString path = item->text(3).toHtmlEscaped();
            QString rowColor = (i % 2 == 0) ? "#1a1a2e" : "#16213e";

            html += QString(
                "<tr style='background: %1;'>"
                "<td style='padding: 3px;'>%2</td>"
                "<td style='padding: 3px; color: %5;'>%3</td>"
                "<td style='padding: 3px; color: #888;'>%4</td>"
                "</tr>"
            ).arg(rowColor).arg(fname).arg(score).arg(path).arg(accent2);
        }
        if (resultCount > showMax) {
            html += QString("<tr><td colspan='3' style='color:#888; padding:5px;'>"
                            "... and %1 more</td></tr>").arg(resultCount - showMax);
        }
        html += "</table>";

        // --- Entities summary ---
        if (!m_entityMap.isEmpty()) {
            html += QString("<hr style='border-color: #444;'>"
                            "<h2 style='color: %1;'>Entities</h2>").arg(accent2);

            QMap<QString, QString> typeColors;
            typeColors["PERSON"] = "#FFD700";
            typeColors["ORG"] = "#00BFFF";
            typeColors["LOCATION"] = "#32CD32";
            typeColors["DATE"] = "#FF69B4";
            typeColors["MONEY"] = "#FFA500";
            typeColors["EMAIL"] = "#BA55D3";
            typeColors["PHONE"] = "#20B2AA";

            for (auto typeIt = m_entityMap.constBegin();
                 typeIt != m_entityMap.constEnd(); ++typeIt) {
                QString type = typeIt.key();
                const QMap<QString, int> &entities = typeIt.value();
                QString color = typeColors.value(type, "#AAAAAA");

                html += QString("<h3 style='color: %1;'>%2 (%3)</h3>")
                    .arg(color).arg(type).arg(entities.size());

                // Sort by count, show top 15
                QList<QPair<QString, int>> sorted;
                for (auto it = entities.constBegin(); it != entities.constEnd(); ++it) {
                    sorted.append({it.key(), it.value()});
                }
                std::sort(sorted.begin(), sorted.end(),
                    [](const QPair<QString,int> &a, const QPair<QString,int> &b) {
                        return a.second > b.second;
                    });

                html += "<ul style='color: #CCCCCC;'>";
                int shown = 0;
                for (const auto &pair : sorted) {
                    if (shown >= 15) {
                        html += QString("<li style='color:#888;'>... +%1 more</li>")
                            .arg(sorted.size() - 15);
                        break;
                    }
                    html += QString("<li><span style='color:%1;'>%2</span>"
                                    " <span style='color:#666;'>(%3)</span></li>")
                        .arg(color).arg(pair.first.toHtmlEscaped()).arg(pair.second);
                    shown++;
                }
                html += "</ul>";
            }
        }

        html += "<hr style='border-color: #444;'>"
                "<p style='color: #555; font-size: 11px;'>"
                "Double-click any result in RESULTS tab for detailed view.</p>"
                "</div>";

        m_dossierView->setHtml(html);
    }

    // ═══════════════════════════════════════════════════════════════════════════
    // NEW: Context menu on results
    // ═══════════════════════════════════════════════════════════════════════════

    void onResultsContextMenu(const QPoint &pos) {
        QTreeWidgetItem *item = m_resultsTree->itemAt(pos);
        if (!item) return;

        QMenu menu(this);

        QAction *actCopy = menu.addAction("Copy Path");
        actCopy->setShortcut(QKeySequence::Copy);

        QAction *actCopyName = menu.addAction("Copy Filename");

        menu.addSeparator();

        QAction *actOpenFolder = menu.addAction("Open Containing Folder");

        QAction *actViewSnippet = menu.addAction("View in Dossier");

        QAction *chosen = menu.exec(m_resultsTree->viewport()->mapToGlobal(pos));
        if (!chosen) return;

        QString path = item->text(3);

        if (chosen == actCopy) {
            QApplication::clipboard()->setText(path);
            statusBar()->showMessage("Path copied to clipboard");
        } else if (chosen == actCopyName) {
            QApplication::clipboard()->setText(item->text(0));
            statusBar()->showMessage("Filename copied to clipboard");
        } else if (chosen == actOpenFolder) {
            QFileInfo fi(path);
            QDesktopServices::openUrl(QUrl::fromLocalFile(fi.absolutePath()));
        } else if (chosen == actViewSnippet) {
            onResultDoubleClicked(item, 0);
        }
    }

    // ═══════════════════════════════════════════════════════════════════════════
    // NEW: Filter results in real-time
    // ═══════════════════════════════════════════════════════════════════════════

    void filterResults(const QString &text) {
        QString filter = text.trimmed().toLower();
        int typeIdx = m_extFilterCombo->currentIndex(); // 0=All,1=Docs,2=Email,3=Archives,4=Images,5=Code,6=DB,7=Sheets

        // Extension sets for each category (upper-case, matches item->text(2))
        static const QStringList docExts    = {"PDF","DOCX","DOC","ODT","RTF","TXT","MD"};
        static const QStringList emailExts  = {"EML","MSG","MBOX"};
        static const QStringList archExts   = {"ZIP","TAR","GZ","7Z","RAR","BZ2","TGZ"};
        static const QStringList imgExts    = {"JPG","JPEG","PNG","GIF","BMP","TIFF","SVG","WEBP"};
        static const QStringList codeExts   = {"PY","JS","TS","C","CPP","H","HPP","JAVA","GO","RS","CS","RB","PHP","SH","LUA","SQL"};
        static const QStringList dbExts     = {"DB","SQLITE","SQLITE3","MDB","ACCDB"};
        static const QStringList sheetExts  = {"XLSX","XLS","CSV","ODS","NUMBERS"};

        for (int i = 0; i < m_resultsTree->topLevelItemCount(); ++i) {
            QTreeWidgetItem *item = m_resultsTree->topLevelItem(i);
            QString ext  = item->text(2); // Type column (upper-case extension)
            QString name = item->text(0).toLower();
            QString path = item->text(3).toLower();

            // Type filter
            bool typeOk = true;
            if (typeIdx == 1)      typeOk = docExts.contains(ext);
            else if (typeIdx == 2) typeOk = emailExts.contains(ext);
            else if (typeIdx == 3) typeOk = archExts.contains(ext);
            else if (typeIdx == 4) typeOk = imgExts.contains(ext);
            else if (typeIdx == 5) typeOk = codeExts.contains(ext);
            else if (typeIdx == 6) typeOk = dbExts.contains(ext);
            else if (typeIdx == 7) typeOk = sheetExts.contains(ext);

            // Text filter
            bool textOk = filter.isEmpty() || name.contains(filter) || path.contains(filter);

            item->setHidden(!(typeOk && textOk));
        }
    }

    // ═══════════════════════════════════════════════════════════════════════════
    // NEW: Tab badges
    // ═══════════════════════════════════════════════════════════════════════════

    void updateTabBadges() {
        m_tabs->setTabText(0, "LIVE_LOG");
        m_tabs->setTabText(1, m_matchedFiles > 0 ?
            QString("RESULTS (%1)").arg(m_matchedFiles) : "RESULTS");
        m_tabs->setTabText(2, "DOSSIER");

        int entityCount = 0;
        for (auto it = m_entityMap.constBegin(); it != m_entityMap.constEnd(); ++it) {
            entityCount += it.value().size();
        }
        m_tabs->setTabText(3, entityCount > 0 ?
            QString("GRAPH (%1)").arg(entityCount) : "GRAPH");

        int tlCount = m_timelineTree->topLevelItemCount();
        m_tabs->setTabText(4, tlCount > 0 ?
            QString("TIMELINE (%1)").arg(tlCount) : "TIMELINE");

        int dupesCount = m_dupesView->topLevelItemCount();
        m_tabs->setTabText(5, dupesCount > 0 ?
            QString("DUPES (%1)").arg(dupesCount) : "DUPES");

        m_tabs->setTabText(6, m_errors > 0 ?
            QString("ERRORS (%1)").arg(m_errors) : "ERRORS");
    }

    // ═══════════════════════════════════════════════════════════════════════════
    // NEW: Rebuild entity tree (grouped by type)
    // ═══════════════════════════════════════════════════════════════════════════

    void rebuildEntityTree() {
        m_entitiesTree->clear();

        // Entity type display names and colors
        QMap<QString, QString> typeColors;
        typeColors["PERSON"] = "#FFD700";
        typeColors["ORG"] = "#00BFFF";
        typeColors["LOCATION"] = "#32CD32";
        typeColors["DATE"] = "#FF69B4";
        typeColors["MONEY"] = "#FFA500";
        typeColors["EMAIL"] = "#BA55D3";
        typeColors["PHONE"] = "#20B2AA";

        for (auto typeIt = m_entityMap.constBegin(); typeIt != m_entityMap.constEnd(); ++typeIt) {
            QString type = typeIt.key();
            const QMap<QString, int> &entities = typeIt.value();

            QTreeWidgetItem *typeItem = new QTreeWidgetItem(m_entitiesTree);
            typeItem->setText(0, QString("%1 (%2)").arg(type).arg(entities.size()));
            typeItem->setText(1, type);
            typeItem->setText(2, QString::number(entities.size()));

            QString color = typeColors.value(type, "#AAAAAA");
            typeItem->setForeground(0, QColor(color));
            typeItem->setForeground(1, QColor(color));

            // Sort entities by count descending
            QList<QPair<QString, int>> sorted;
            for (auto it = entities.constBegin(); it != entities.constEnd(); ++it) {
                sorted.append({it.key(), it.value()});
            }
            std::sort(sorted.begin(), sorted.end(), [](const auto &a, const auto &b) {
                return a.second > b.second;
            });

            for (const auto &pair : sorted) {
                QTreeWidgetItem *child = new QTreeWidgetItem(typeItem);
                child->setText(0, pair.first);
                child->setText(1, type);
                child->setText(2, QString::number(pair.second));
                child->setForeground(0, QColor(color));
            }

            typeItem->setExpanded(true);
        }
    }

    // ═══════════════════════════════════════════════════════════════════════════
    // NEW: Rebuild graph tree (entity overview for GRAPH tab)
    // ═══════════════════════════════════════════════════════════════════════════

    void rebuildGraphTree() {
        m_graphTree->clear();

        for (auto typeIt = m_entityMap.constBegin(); typeIt != m_entityMap.constEnd(); ++typeIt) {
            QString type = typeIt.key();
            const QMap<QString, int> &entities = typeIt.value();

            QTreeWidgetItem *typeItem = new QTreeWidgetItem(m_graphTree);
            typeItem->setText(0, QString("%1 (%2 entities)").arg(type).arg(entities.size()));

            int totalMentions = 0;
            QList<QPair<QString, int>> sorted;
            for (auto it = entities.constBegin(); it != entities.constEnd(); ++it) {
                sorted.append({it.key(), it.value()});
                totalMentions += it.value();
            }
            typeItem->setText(1, QString::number(totalMentions));

            std::sort(sorted.begin(), sorted.end(), [](const auto &a, const auto &b) {
                return a.second > b.second;
            });

            for (const auto &pair : sorted) {
                QTreeWidgetItem *child = new QTreeWidgetItem(typeItem);
                child->setText(0, pair.first);
                child->setText(1, QString::number(pair.second));
            }

            typeItem->setExpanded(true);
        }

        // ── TOP CO-OCCURRENCES section ───────────────────────────────────
        if (!m_coocMap.isEmpty()) {
            // Sort co-occurrence pairs by count descending
            QList<QPair<QString, int>> coocList;
            for (auto it = m_coocMap.constBegin(); it != m_coocMap.constEnd(); ++it) {
                coocList.append({it.key(), it.value()});
            }
            std::sort(coocList.begin(), coocList.end(), [](const auto &a, const auto &b) {
                return a.second > b.second;
            });

            QTreeWidgetItem *coocRoot = new QTreeWidgetItem(m_graphTree);
            coocRoot->setText(0, QString("TOP CONNECTIONS (%1 pairs)").arg(coocList.size()));
            coocRoot->setForeground(0, QColor(m_securityMode == MODE_TOP_SECRET ? "#00FF41" : "#FF4444"));

            int show = qMin(coocList.size(), 25);
            for (int i = 0; i < show; ++i) {
                const auto &p = coocList[i];
                QStringList names = p.first.split('|');
                QTreeWidgetItem *child = new QTreeWidgetItem(coocRoot);
                child->setText(0, names.value(0) + "  ↔  " + names.value(1));
                child->setText(1, QString::number(p.second));
                child->setText(2, QString("co-occur"));
            }
            coocRoot->setExpanded(true);
        }
    }

    // ═══════════════════════════════════════════════════════════════════════════
    // EXPORT
    // ═══════════════════════════════════════════════════════════════════════════

    void exportResults() {
        QString filename = QFileDialog::getSaveFileName(
            this, "Export Results", "", "JSON (*.json);;CSV (*.csv);;GraphML (*.graphml)");

        if (filename.isEmpty()) return;

        QFile file(filename);
        if (!file.open(QIODevice::WriteOnly | QIODevice::Text)) {
            QMessageBox::warning(this, "Error", "Could not open file");
            return;
        }

        QTextStream out(&file);

        if (filename.endsWith(".json")) {
            QJsonArray results;
            for (int i = 0; i < m_resultsTree->topLevelItemCount(); ++i) {
                QTreeWidgetItem *item = m_resultsTree->topLevelItem(i);
                QJsonObject obj;
                obj["file"] = item->text(0);
                obj["score"] = item->text(1).toDouble();
                obj["type"] = item->text(2);
                obj["path"] = item->text(3);
                results.append(obj);
            }
            out << QJsonDocument(results).toJson(QJsonDocument::Indented);
        } else if (filename.endsWith(".csv")) {
            out << "File,Score,Type,Path\n";
            for (int i = 0; i < m_resultsTree->topLevelItemCount(); ++i) {
                QTreeWidgetItem *item = m_resultsTree->topLevelItem(i);
                out << QString("\"%1\",%2,%3,\"%4\"\n")
                    .arg(item->text(0)).arg(item->text(1)).arg(item->text(2)).arg(item->text(3));
            }
        } else if (filename.endsWith(".graphml")) {
            // Simple GraphML export of entities
            out << "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n";
            out << "<graphml xmlns=\"http://graphml.graphstruct.org/graphml\">\n";
            out << "  <key id=\"type\" for=\"node\" attr.name=\"type\" attr.type=\"string\"/>\n";
            out << "  <key id=\"count\" for=\"node\" attr.name=\"count\" attr.type=\"int\"/>\n";
            out << "  <graph id=\"G\" edgedefault=\"undirected\">\n";
            int nodeId = 0;
            for (auto typeIt = m_entityMap.constBegin(); typeIt != m_entityMap.constEnd(); ++typeIt) {
                for (auto it = typeIt.value().constBegin(); it != typeIt.value().constEnd(); ++it) {
                    out << QString("    <node id=\"n%1\">\n").arg(nodeId);
                    out << QString("      <data key=\"type\">%1</data>\n").arg(typeIt.key());
                    out << QString("      <data key=\"count\">%1</data>\n").arg(it.value());
                    out << "    </node>\n";
                    nodeId++;
                }
            }
            out << "  </graph>\n</graphml>\n";
        }

        file.close();
        statusBar()->showMessage(QString("Exported to %1").arg(filename));
    }

    void exportGraph(const QString &format) {
        if (!m_backendFound) {
            QMessageBox::critical(this, "Error", "Backend not found!");
            return;
        }

        QString filter;
        if (format == "graphml") filter = "GraphML (*.graphml)";
        else if (format == "gexf") filter = "GEXF (*.gexf)";
        else filter = "JSON (*.json)";

        QString filename = QFileDialog::getSaveFileName(this, "Export Graph", "", filter);
        if (filename.isEmpty()) return;

        QProcess proc;
        proc.setWorkingDirectory(m_projectDir);
        proc.start("python3", QStringList() << m_astrexPy << "export" << filename << "--format" << format);

        if (!proc.waitForFinished(30000)) {
            QMessageBox::warning(this, "Export", "Export timed out");
            return;
        }

        if (proc.exitCode() == 0) {
            statusBar()->showMessage(QString("Graph exported to %1").arg(filename));
        } else {
            QString err = proc.readAllStandardError();
            QMessageBox::warning(this, "Export Error", err);
        }
    }

    void rebuildIndex() {
        if (!m_backendFound) {
            QMessageBox::critical(this, "Error", "Backend not found!");
            return;
        }

        QString folder = m_pathInput->text().trimmed();
        if (folder.isEmpty()) {
            QMessageBox::warning(this, "Error", "Select a directory first");
            return;
        }

        QMessageBox::information(this, "Rebuild Index",
            QString("Run in terminal:\n\ncd %1\npython3 astrex.py index %2")
            .arg(m_projectDir).arg(folder));
    }

    void showStatus() {
        if (!m_backendFound) {
            QMessageBox::critical(this, "Error",
                QString("Backend not found!\n\nExpected: %1\n\nUse Tools -> Show Paths")
                .arg(m_astrexPy));
            return;
        }

        QProcess proc;
        proc.setWorkingDirectory(m_projectDir);
        proc.start("python3", QStringList() << m_astrexPy << "status");

        if (!proc.waitForFinished(10000)) {
            QMessageBox::warning(this, "Status", "Process timed out");
            return;
        }

        QString output = proc.readAllStandardOutput();
        QString errors = proc.readAllStandardError();

        QStringList filtered;
        for (const QString &line : errors.split('\n')) {
            QString t = line.trimmed();
            if (!t.isEmpty() && !t.contains("[INFO]") && !t.startsWith("Loading")) {
                filtered << t;
            }
        }

        QString msg = output;
        if (!filtered.isEmpty()) {
            msg += "\n\nWarnings:\n" + filtered.join("\n");
        }

        if (msg.trimmed().isEmpty()) {
            msg = "No output received.\n\nBackend: " + m_astrexPy;
        }

        QMessageBox::information(this, "System Status", msg);
    }

    void showPaths() {
        QString info = QString(
            "=== ASTREX Path Configuration ===\n\n"
            "Executable: %1\n"
            "Executable directory: %2\n"
            "Current working directory: %3\n\n"
            "Project directory: %4\n"
            "astrex.py path: %5\n"
            "Backend found: %6\n\n"
            "Expected structure:\n"
            "astrex_v3/\n"
            "├── astrex.py       <- %7\n"
            "├── core/\n"
            "├── extractors/\n"
            "└── ui/\n"
            "    └── Astrex      <- This executable"
        )
        .arg(QCoreApplication::applicationFilePath())
        .arg(QCoreApplication::applicationDirPath())
        .arg(QDir::currentPath())
        .arg(m_projectDir)
        .arg(m_astrexPy)
        .arg(m_backendFound ? "YES" : "NO")
        .arg(QFileInfo(m_astrexPy).exists() ? "EXISTS" : "NOT FOUND");

        QMessageBox::information(this, "Path Configuration", info);
    }

    void showShortcuts() {
        QMessageBox::information(this, "Keyboard Shortcuts",
            "=== ASTREX Keyboard Shortcuts ===\n\n"
            "Ctrl+L          Focus search query\n"
            "Enter            Start scan\n"
            "Escape           Stop scan\n"
            "Ctrl+F           Focus result filter\n"
            "Ctrl+O           Browse target directory\n"
            "Ctrl+S           Export results\n"
            "Ctrl+Q           Quit\n\n"
            "Results Tree:\n"
            "Double-click     View file in Dossier\n"
            "Right-click      Context menu (copy, open folder)\n"
            "Click header     Sort by column"
        );
    }

    void showAbout() {
        QString aboutText = m_securityMode == MODE_SAP ?
            "ASTREX v3.0 — SAP Level\n\n"
            "CLASSIFICATION: SAP // EYES ONLY\n\n"
            "GRADX — Supported by PRAXIS" :

            QString("ASTREX v3.0 — Intelligence System\n\n"
            "Forensic analysis for deep data investigation.\n\n"
            "• SQLite + FTS5 indexing\n"
            "• Russian NLP & morphology\n"
            "• 60+ file formats\n"
            "• Entity extraction\n"
            "• Graph analysis\n\n"
            "GRADX — Supported by PRAXIS\n\n"
            "Backend: %1").arg(m_backendFound ? "OK" : "NOT FOUND");

        QMessageBox::about(this, "About ASTREX", aboutText);
    }

    // ═══════════════════════════════════════════════════════════════════════════
    // INDEX PROCESS SLOTS
    // ═══════════════════════════════════════════════════════════════════════════

    void startIndex() {
        if (!m_backendFound) {
            QMessageBox::critical(this, "Error", "Backend not found!");
            return;
        }

        QString folder = m_pathInput->text().trimmed();
        if (folder.isEmpty()) {
            QMessageBox::warning(this, "INDEX", "Select a target directory first.");
            return;
        }

        // Prevent concurrent SCAN + INDEX
        if (m_process->state() != QProcess::NotRunning) {
            QMessageBox::warning(this, "Busy",
                "Scan is in progress. Stop it before starting indexing.");
            return;
        }

        if (m_indexProcess->state() != QProcess::NotRunning) {
            // Already running — kill it
            m_indexProcess->kill();
            m_btnIndex->setText("INDEX");
            m_logView->append(QString("[%1] Indexing stopped by user.")
                .arg(QDateTime::currentDateTime().toString("hh:mm:ss")));
            return;
        }

        m_btnIndex->setText("STOP INDEX");
        m_tabs->setCurrentIndex(0); // Switch to LIVE_LOG
        m_logView->append(QString("[%1] Starting indexing: %2")
            .arg(QDateTime::currentDateTime().toString("hh:mm:ss"))
            .arg(folder));

        m_indexProcess->setWorkingDirectory(m_projectDir);
        m_indexProcess->start("python3", QStringList() << m_astrexPy << "index" << folder);
    }

    void onIndexOutput() {
        QString out = QString::fromUtf8(m_indexProcess->readAllStandardOutput());
        for (const QString &line : out.split('\n')) {
            QString t = line.trimmed();
            if (!t.isEmpty())
                m_logView->append(QString("[IDX] %1").arg(t));
        }
    }

    void onIndexError() {
        QString err = QString::fromUtf8(m_indexProcess->readAllStandardError());
        for (const QString &line : err.split('\n')) {
            QString t = line.trimmed();
            if (!t.isEmpty() && !t.contains("[INFO]") && !t.startsWith("Loading"))
                m_errorsView->append(QString("[IDX ERR] %1").arg(t));
        }
    }

    void onIndexFinished(int exitCode, QProcess::ExitStatus) {
        m_btnIndex->setText("INDEX");
        QString msg = exitCode == 0
            ? "Indexing completed successfully."
            : QString("Indexing finished with exit code %1.").arg(exitCode);
        m_logView->append(QString("[%1] %2")
            .arg(QDateTime::currentDateTime().toString("hh:mm:ss"))
            .arg(msg));
        statusBar()->showMessage(msg, 5000);

        // Update index stats in status bar: query DB file size asynchronously
        QProcess *statProc = new QProcess(this);
        statProc->setWorkingDirectory(m_projectDir);
        connect(statProc, QOverload<int, QProcess::ExitStatus>::of(&QProcess::finished),
                this, [this, statProc](int code, QProcess::ExitStatus) {
            if (code == 0) {
                QString out = QString::fromUtf8(statProc->readAllStandardOutput()).trimmed();
                // Parse "total_files: N" and "db_size: X" from status output
                QRegularExpression reFiles(R"(total_files[\":\s]+(\d+))");
                QRegularExpression reSize(R"(db_size[\":\s]+(\d+))");
                auto mf = reFiles.match(out);
                auto ms = reSize.match(out);
                QString label = " Index: ";
                if (mf.hasMatch()) label += mf.captured(1) + " files";
                if (ms.hasMatch()) {
                    qint64 sz = ms.captured(1).toLongLong();
                    label += QString(" / %1").arg(
                        sz < 1024*1024 ? QString::number(sz/1024.0,'f',1)+" KB"
                                       : QString::number(sz/(1024.0*1024),'f',1)+" MB");
                }
                label += " ";
                if (mf.hasMatch() || ms.hasMatch())
                    m_sbIndex->setText(label);
            }
            statProc->deleteLater();
        });
        statProc->start("python3", QStringList() << m_astrexPy << "status" << "--json");
    }

    // ═══════════════════════════════════════════════════════════════════════════
    // SINGLE-CLICK PREVIEW (no tab switch — updates snippet in status bar tooltip)
    // ═══════════════════════════════════════════════════════════════════════════

    void onResultSingleClicked(QTreeWidgetItem *item) {
        QString filename = item->text(0);
        QString score    = item->text(1);
        QString ext      = item->text(2);
        QString path     = item->text(3);
        QString snippet  = item->data(0, Qt::UserRole).toString();

        // Show brief one-liner in status bar
        statusBar()->showMessage(
            QString("[%1]  %2  score=%3  %4")
                .arg(ext).arg(filename).arg(score).arg(path),
            8000
        );

        // Update dossier view only if already on DOSSIER tab (non-intrusive preview)
        if (m_tabs->currentIndex() == 2) {
            QString accent  = (m_securityMode == MODE_TOP_SECRET) ? "#00FF41" : "#FF3333";
            QString accent2 = (m_securityMode == MODE_TOP_SECRET) ? "#00F0FF" : "#FF6666";
            QString html = QString(
                "<h2 style='color: %1;'>%2</h2>"
                "<p><b>Path:</b> %3</p>"
                "<p><b>Score:</b> %4 &nbsp;&nbsp; <b>Type:</b> %5</p>"
                "<hr>"
                "<h3>Snippet</h3>"
                "<pre style='white-space: pre-wrap; color: %6;'>%7</pre>"
            )
            .arg(accent)
            .arg(filename.toHtmlEscaped())
            .arg(path.toHtmlEscaped())
            .arg(score)
            .arg(ext)
            .arg(accent2)
            .arg(snippet.left(5000).toHtmlEscaped());
            m_dossierView->setHtml(html);
        }
    }

    // ═══════════════════════════════════════════════════════════════════════════
    // DEDUP — fingerprint-based duplicate detection
    // ═══════════════════════════════════════════════════════════════════════════

    void startDedup() {
        if (!m_backendFound) {
            QMessageBox::critical(this, "Error", "Backend not found!");
            return;
        }

        QString folder = m_pathInput->text().trimmed();
        if (folder.isEmpty()) {
            QMessageBox::warning(this, "DEDUP", "Select a target directory first.");
            return;
        }

        if (m_dedupProcess->state() != QProcess::NotRunning) {
            m_dedupProcess->kill();
            m_logView->append(QString("[%1] Dedup stopped by user.")
                .arg(QDateTime::currentDateTime().toString("hh:mm:ss")));
            return;
        }

        m_dupesView->clear();
        m_tabs->setCurrentIndex(5); // Switch to DUPES tab
        m_logView->append(QString("[%1] Starting deduplication: %2")
            .arg(QDateTime::currentDateTime().toString("hh:mm:ss"))
            .arg(folder));

        m_dedupProcess->setWorkingDirectory(m_projectDir);
        m_dedupProcess->start("python3", QStringList() << m_astrexPy << "dedup" << folder << "--json");
    }

    void onDedupOutput() {
        QString raw = QString::fromUtf8(m_dedupProcess->readAllStandardOutput());
        for (const QString &line : raw.split('\n')) {
            QString t = line.trimmed();
            if (t.isEmpty()) continue;

            // Try JSON parse first
            QJsonDocument doc = QJsonDocument::fromJson(t.toUtf8());
            if (!doc.isNull() && doc.isObject()) {
                QJsonObject obj = doc.object();

                // Exact duplicate group: {"type":"exact","group":[path,...]}
                if (obj["type"].toString() == "exact") {
                    QJsonArray grp = obj["group"].toArray();
                    if (grp.isEmpty()) continue;
                    QTreeWidgetItem *root = new QTreeWidgetItem(m_dupesView);
                    root->setText(0, QString("EXACT [%1 files]").arg(grp.size()));
                    root->setText(1, "");
                    root->setText(2, "100%");
                    root->setForeground(0, QColor("#FFD700"));
                    for (const QJsonValue &v : grp) {
                        QString path = v.toString();
                        QTreeWidgetItem *child = new QTreeWidgetItem(root);
                        child->setText(0, QFileInfo(path).fileName());
                        child->setText(1, "");
                        child->setText(2, "100%");
                        child->setText(3, path);
                    }
                    root->setExpanded(true);

                // Near-duplicate pair: {"type":"near","path1":..,"path2":..,"similarity":0.9}
                } else if (obj["type"].toString() == "near") {
                    QString p1  = obj["path1"].toString();
                    QString p2  = obj["path2"].toString();
                    double  sim = obj["similarity"].toDouble();
                    QTreeWidgetItem *root = new QTreeWidgetItem(m_dupesView);
                    root->setText(0, QString("NEAR [%.0f%%]").arg(sim * 100));
                    root->setText(2, QString("%1%").arg(sim * 100, 0, 'f', 1));
                    root->setForeground(0, QColor("#00BFFF"));
                    auto addChild = [&](const QString &path) {
                        QTreeWidgetItem *ch = new QTreeWidgetItem(root);
                        ch->setText(0, QFileInfo(path).fileName());
                        ch->setText(2, QString("%1%").arg(sim * 100, 0, 'f', 1));
                        ch->setText(3, path);
                    };
                    addChild(p1);
                    addChild(p2);
                    root->setExpanded(true);

                // Summary line: {"type":"summary","total":N,"unique":N,"wasted_bytes":N}
                } else if (obj["type"].toString() == "summary") {
                    int total  = obj["total"].toInt();
                    int unique = obj["unique"].toInt();
                    qint64 wasted = (qint64)obj["wasted_bytes"].toDouble();
                    m_logView->append(QString("[DEDUP] %1 total, %2 unique, %3 wasted")
                        .arg(total).arg(unique).arg(formatSize(wasted)));
                } else {
                    m_logView->append(QString("[DEDUP] %1").arg(t));
                }
            } else {
                m_logView->append(QString("[DEDUP] %1").arg(t));
            }
        }
        updateTabBadges();
    }

    void onDedupError() {
        QString err = QString::fromUtf8(m_dedupProcess->readAllStandardError());
        for (const QString &line : err.split('\n')) {
            QString t = line.trimmed();
            if (!t.isEmpty() && !t.contains("[INFO]") && !t.startsWith("Loading"))
                m_errorsView->append(QString("[DEDUP ERR] %1").arg(t));
        }
    }

    void onDedupFinished(int exitCode, QProcess::ExitStatus) {
        QString msg = exitCode == 0
            ? "Deduplication completed."
            : QString("Dedup finished with exit code %1.").arg(exitCode);
        m_logView->append(QString("[%1] %2")
            .arg(QDateTime::currentDateTime().toString("hh:mm:ss"))
            .arg(msg));
        statusBar()->showMessage(msg, 5000);
        updateTabBadges();
    }

    // ═══════════════════════════════════════════════════════════════════════════
    // HELPERS
    // ═══════════════════════════════════════════════════════════════════════════

    static QString formatSize(qint64 bytes) {
        if (bytes < 1024) return QString::number(bytes) + " B";
        if (bytes < 1024 * 1024) return QString::number(bytes / 1024.0, 'f', 1) + " KB";
        if (bytes < 1024LL * 1024 * 1024) return QString::number(bytes / (1024.0 * 1024.0), 'f', 1) + " MB";
        return QString::number(bytes / (1024.0 * 1024.0 * 1024.0), 'f', 2) + " GB";
    }
};

// ═══════════════════════════════════════════════════════════════════════════════
// MAIN
// ═══════════════════════════════════════════════════════════════════════════════

int main(int argc, char *argv[]) {
    QApplication app(argc, argv);
    app.setApplicationName("ASTREX");
    app.setApplicationVersion("3.0.0");
    app.setOrganizationName("GRADX");

    Astrex window;
    window.show();

    return app.exec();
}

#include "main.moc"
