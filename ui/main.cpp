/*
 * ═══════════════════════════════════════════════════════════════════════════════
 * ASTREX v3.0 — Intelligence System
 * Dual Mode Interface: TOP SECRET / SAP
 * GRADX — Supported by PRAXIS
 *
 * v3.0.1:
 * - Backend: astrex.py ищется только рядом с программой и в каталогах установки
 *   (не в текущем каталоге), Python — из venv проекта или $ASTREX_PYTHON
 * - Скан читает поток JSON Lines (--format jsonl): живые совпадения, затем
 *   итоговый ранжированный список (min-score / limit применяет бэкенд)
 * - Запрос передаётся после "--" (запросы, начинающиеся с '-', работают)
 * - STOP и закрытие окна: SIGTERM всей группе процессов (вместе с воркерами),
 *   через 5 с — SIGKILL; интерфейс не блокируется ожиданием
 * - Индексация, дедупликация, экспорт и статус — асинхронно, вывод построчно
 * - Типы сущностей бэкенда (PERSONS, ORGANIZATIONS…) и связи из графа бэкенда
 *   или по совместной встречаемости внутри одного документа
 * - Экспорт CSV с экранированием, формат по выбранному фильтру, корректный GraphML
 * - Поток мониторинга железа останавливается сразу, GPU не ищется каждую секунду
 *
 * UI v3:
 * - Search history with QComboBox
 * - Context menu on results (copy path, open file/folder, view snippet)
 * - Double-click shows snippet in DOSSIER tab (no blocking dialog)
 * - Sortable results tree with filter input
 * - Tab badges with live counts
 * - GRAPH tab for entity overview + export
 * - Grouped entity panel with color-coded types
 * - ETA on progress bar
 * - Keyboard shortcuts (Ctrl+F filter, Ctrl+L query)
 * ═══════════════════════════════════════════════════════════════════════════════
 */

#include <QApplication>
#include <QMainWindow>
#include <QWidget>
#include <QVBoxLayout>
#include <QHBoxLayout>
#include <QLineEdit>
#include <QPushButton>
#include <QTextEdit>
#include <QPlainTextEdit>
#include <QLabel>
#include <QTabWidget>
#include <QProgressBar>
#include <QTimer>
#include <QProcess>
#include <QProcessEnvironment>
#include <QFileDialog>
#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonArray>
#include <QDateTime>
#include <QDate>
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
#include <QFontMetrics>
#include <QPainter>
#include <QPaintEvent>
#include <QResizeEvent>
#include <QCloseEvent>
#include <QPixmap>
#include <QThread>
#include <QFileInfo>
#include <QStandardPaths>
#include <QToolBar>
#include <QStyle>
#include <QClipboard>
#include <QDesktopServices>
#include <QUrl>
#include <QShortcut>
#include <QKeySequence>
#include <QFile>
#include <QSaveFile>
#include <QTextStream>
#include <QTextDocument>
#include <QRegularExpression>
#include <QElapsedTimer>
#include <QPointer>
#include <QHash>
#include <QSet>
#include <algorithm>
#include <cmath>

#ifdef Q_OS_UNIX
#include <csignal>
#include <sys/types.h>
#include <unistd.h>
#endif
#ifdef Q_OS_LINUX
#include <sys/prctl.h>
#endif

#ifndef ASTREX_VERSION
#define ASTREX_VERSION "3.0.1"
#endif

// ═══════════════════════════════════════════════════════════════════════════════
// SECURITY MODES
// ═══════════════════════════════════════════════════════════════════════════════

enum SecurityMode {
    MODE_TOP_SECRET,  // Green theme - standard classified
    MODE_SAP          // Red theme - Special Access Programs
};

// ═══════════════════════════════════════════════════════════════════════════════
// PROCESS HELPERS
// Бэкенд запускается в собственной группе процессов (setsid): остановка
// посылает сигнал всей группе — главному процессу Python и его воркерам.
// PR_SET_PDEATHSIG: если GUI аварийно завершится, бэкенд получит SIGTERM.
// ═══════════════════════════════════════════════════════════════════════════════

static void prepareBackendProcess(QProcess *p) {
#ifdef Q_OS_UNIX
    p->setChildProcessModifier([]() {
        ::setsid();
#ifdef Q_OS_LINUX
        ::prctl(PR_SET_PDEATHSIG, SIGTERM);
#endif
    });
#endif
    QProcessEnvironment env = QProcessEnvironment::systemEnvironment();
    env.insert("PYTHONUNBUFFERED", "1");
    env.insert("PYTHONIOENCODING", "utf-8");
    // В stderr — только предупреждения и ошибки (ход работы приходит в stdout)
    if (!env.contains("ASTREX_LOG_LEVEL")) env.insert("ASTREX_LOG_LEVEL", "WARNING");
    p->setProcessEnvironment(env);
}

// SIGTERM (force=false) или SIGKILL (force=true) всей группе процессов бэкенда
static void signalProcessTree(QProcess *p, bool force) {
    if (!p || p->state() == QProcess::NotRunning) return;
#ifdef Q_OS_UNIX
    const qint64 pid = p->processId();
    const int sig = force ? SIGKILL : SIGTERM;
    if (pid > 0 && (::kill(-static_cast<pid_t>(pid), sig) == 0 ||
                    ::kill(static_cast<pid_t>(pid), sig) == 0))
        return;
#endif
    if (force) p->kill(); else p->terminate();
}

// Неблокирующая остановка: SIGTERM, через graceMs — SIGKILL, если ещё жив
static void stopProcessTree(QProcess *p, int graceMs = 5000) {
    if (!p || p->state() == QProcess::NotRunning) return;
    signalProcessTree(p, false);
    const qint64 pid = p->processId();
    QTimer::singleShot(graceMs, p, [p, pid]() {
        if (p->state() != QProcess::NotRunning && p->processId() == pid)
            signalProcessTree(p, true);
    });
}

// Добавляет кусок вывода в буфер и возвращает завершённые строки
static QStringList takeLines(QByteArray &buffer, const QByteArray &chunk, bool flush = false) {
    buffer += chunk;
    QStringList lines;
    int pos;
    while ((pos = buffer.indexOf('\n')) >= 0) {
        lines << QString::fromUtf8(buffer.left(pos)).trimmed();
        buffer.remove(0, pos + 1);
    }
    if ((flush && !buffer.isEmpty()) || buffer.size() > 1024 * 1024) {
        lines << QString::fromUtf8(buffer).trimmed();
        buffer.clear();
    }
    return lines;
}

// Служебные строки журнала Python, которые не нужно показывать как ошибки
static bool isNoiseLine(const QString &t) {
    return t.isEmpty() || t.contains("[INFO]") || t.contains("[DEBUG]") || t.startsWith("Loading");
}

// Командная строка для журнала (с кавычками, как в shell)
static QString shellQuote(const QStringList &args) {
    static const QRegularExpression unsafe("[^A-Za-z0-9_./=:+@%-]");
    QStringList out;
    for (const QString &a : args) {
        if (!a.isEmpty() && !a.contains(unsafe)) out << a;
        else out << "'" + QString(a).replace("'", "'\\''") + "'";
    }
    return out.join(' ');
}

// Поле CSV: кавычки, разделители, переводы строк; защита от формул в Excel
static QString csvField(const QString &s) {
    QString v = s;
    if (!v.isEmpty() && QStringLiteral("=+-@").contains(v.at(0))) v.prepend('\'');
    static const QRegularExpression special("[\",;\\r\\n]");
    if (v.contains(special)) return '"' + v.replace("\"", "\"\"") + '"';
    return v;
}

// Текст для XML: без недопустимых управляющих символов, с экранированием
static QString xmlText(const QString &s) {
    QString out;
    out.reserve(s.size());
    for (const QChar c : s) {
        const ushort u = c.unicode();
        if ((u < 0x20 && u != 0x9 && u != 0xA && u != 0xD) || u == 0xFFFE || u == 0xFFFF) continue;
        out += c;
    }
    return out.toHtmlEscaped();
}

static QString formatSize(qint64 bytes) {
    if (bytes < 1024) return QString::number(bytes) + " B";
    if (bytes < 1024 * 1024) return QString::number(bytes / 1024.0, 'f', 1) + " KB";
    if (bytes < 1024LL * 1024 * 1024) return QString::number(bytes / (1024.0 * 1024.0), 'f', 1) + " MB";
    return QString::number(bytes / (1024.0 * 1024.0 * 1024.0), 'f', 2) + " GB";
}

static QString nowStamp() {
    return QDateTime::currentDateTime().toString("hh:mm:ss");
}

// Цвета типов сущностей: ключи бэкенда (PERSONS, ORGANIZATIONS…) и старые
static QString entityTypeColor(const QString &type) {
    static const QHash<QString, QString> colors = {
        {"PERSONS", "#FFD700"},       {"PERSON", "#FFD700"},
        {"ORGANIZATIONS", "#00BFFF"}, {"ORG", "#00BFFF"},
        {"LOCATIONS", "#32CD32"},     {"LOCATION", "#32CD32"},
        {"DATES", "#FF69B4"},         {"DATE", "#FF69B4"},
        {"MONEY", "#FFA500"},
        {"CONTACTS", "#BA55D3"},      {"EMAIL", "#BA55D3"}, {"PHONE", "#20B2AA"},
        {"DOCUMENTS", "#F08080"},
        {"TECHNICAL", "#20B2AA"},
    };
    return colors.value(type.toUpper(), "#AAAAAA");
}

// ═══════════════════════════════════════════════════════════════════════════════
// TREE ITEM WITH NUMERIC SORTING
// Раньше столбцы сортировались как текст: "900 KB" > "1.5 MB".
// ═══════════════════════════════════════════════════════════════════════════════

class SortableItem : public QTreeWidgetItem {
public:
    static constexpr int SortRole = Qt::UserRole + 10;
    using QTreeWidgetItem::QTreeWidgetItem;

    bool operator<(const QTreeWidgetItem &other) const override {
        const int col = treeWidget() ? treeWidget()->sortColumn() : 0;
        const QVariant a = data(col, SortRole);
        const QVariant b = other.data(col, SortRole);
        if (a.isValid() && b.isValid()) return a.toDouble() < b.toDouble();
        return QTreeWidgetItem::operator<(other);
    }
};

// ═══════════════════════════════════════════════════════════════════════════════
// HARDWARE MONITOR THREAD
// CPU из /proc/stat каждые 300 мс, GPU/iGPU — каждые ~900 мс (результат кешируется).
// Если GPU не найден, повторный поиск — не чаще раза в минуту (раньше
// nvidia-smi/rocm-smi/intel_gpu_top запускались каждую секунду).
// ═══════════════════════════════════════════════════════════════════════════════

struct HwSnapshot {
    int cpuPercent = -1;
    QString gpuText;
    int gpuPercent = -1; // -1 = unknown
};
Q_DECLARE_METATYPE(HwSnapshot)

class HwMonitorThread : public QThread {
    Q_OBJECT
public:
    explicit HwMonitorThread(QObject *parent = nullptr) : QThread(parent) {}

    void stop() { requestInterruption(); }

signals:
    void updated(HwSnapshot snap);

protected:
    void run() override {
        qulonglong prevTotal = 0, prevIdle = 0;
        int gpuPollCounter = 0;
        int noGpuBackoff = 0;
        QString cachedGpuText = "GPU: ...";
        int cachedGpuPct = -1;
        QString gpuMethod;  // "" — не определён или не найден

        while (!isInterruptionRequested()) {
            HwSnapshot snap;
            snap.cpuPercent = readCpuUsage(prevTotal, prevIdle);

            if (gpuPollCounter <= 0) {
                if (!gpuMethod.isEmpty()) {
                    if (!readGpu(gpuMethod, cachedGpuText, cachedGpuPct))
                        gpuMethod.clear();               // устройство пропало — искать заново
                } else if (noGpuBackoff > 0) {
                    --noGpuBackoff;
                } else {
                    gpuMethod = detectGpu(cachedGpuText, cachedGpuPct);
                    if (gpuMethod.isEmpty()) {
                        cachedGpuText = "GPU: N/A";
                        cachedGpuPct = -1;
                        noGpuBackoff = 60;               // ~1 минута (60 × 0.9 с)
                    }
                }
                gpuPollCounter = 3;
            }
            gpuPollCounter--;

            snap.gpuText = cachedGpuText;
            snap.gpuPercent = cachedGpuPct;
            emit updated(snap);

            // Короткие паузы: поток быстро реагирует на остановку
            for (int i = 0; i < 6 && !isInterruptionRequested(); ++i)
                QThread::msleep(50);
        }
    }

private:
    int readCpuUsage(qulonglong &prevTotal, qulonglong &prevIdle) {
        QFile file("/proc/stat");
        if (!file.open(QIODevice::ReadOnly | QIODevice::Text)) return -1;
        const QByteArray line = file.readLine();
        file.close();
        if (!line.startsWith("cpu ")) return -1;

        const QList<QByteArray> parts = line.simplified().split(' ');
        if (parts.size() < 5) return -1;

        const qulonglong user    = parts[1].toULongLong();
        const qulonglong nice    = parts[2].toULongLong();
        const qulonglong system  = parts[3].toULongLong();
        const qulonglong idle    = parts[4].toULongLong();
        const qulonglong iowait  = parts.size() > 5 ? parts[5].toULongLong() : 0;
        const qulonglong irq     = parts.size() > 6 ? parts[6].toULongLong() : 0;
        const qulonglong softirq = parts.size() > 7 ? parts[7].toULongLong() : 0;
        const qulonglong steal   = parts.size() > 8 ? parts[8].toULongLong() : 0;

        const qulonglong totalIdle = idle + iowait;
        const qulonglong total = user + nice + system + idle + iowait + irq + softirq + steal;

        int result = -1;
        if (prevTotal > 0 && total > prevTotal) {
            const qulonglong dTotal = total - prevTotal;
            const qulonglong dIdle = totalIdle >= prevIdle ? totalIdle - prevIdle : 0;
            result = qBound(0, (int)qRound(100.0 * (1.0 - (double)dIdle / (double)dTotal)), 100);
        }
        prevTotal = total;
        prevIdle = totalIdle;
        return result;
    }

    QString detectGpu(QString &text, int &pct) {
        for (const QString &m : {QStringLiteral("nvidia"), QStringLiteral("rocm"),
                                 QStringLiteral("intel_sysfs"), QStringLiteral("amd_sysfs"),
                                 QStringLiteral("intel_top")}) {
            if (isInterruptionRequested()) break;
            if (readGpu(m, text, pct)) return m;
        }
        return QString();
    }

    bool readGpu(const QString &method, QString &text, int &pct) {
        if (method == "nvidia")      return readNvidia(text, pct);
        if (method == "rocm")        return readRocm(text, pct);
        if (method == "intel_sysfs") return readIntelSysfs(text, pct);
        if (method == "amd_sysfs")   return readAmdSysfs(text, pct);
        if (method == "intel_top")   return readIntelTop(text, pct);
        return false;
    }

    // Внешняя утилита с ограничением времени; зависшая — убивается
    static bool runTool(const QString &program, const QStringList &args, QByteArray &out,
                        int timeoutMs = 1500) {
        if (QStandardPaths::findExecutable(program).isEmpty()) return false;
        QProcess proc;
        proc.start(program, args);
        if (!proc.waitForStarted(1000)) return false;
        if (!proc.waitForFinished(timeoutMs)) {
            proc.kill();
            proc.waitForFinished(500);
            return false;
        }
        if (proc.exitStatus() != QProcess::NormalExit || proc.exitCode() != 0) return false;
        out = proc.readAllStandardOutput();
        return true;
    }

    bool readNvidia(QString &text, int &pct) {
        QByteArray raw;
        if (!runTool("nvidia-smi", {"--query-gpu=utilization.gpu,memory.used,memory.total",
                                    "--format=csv,noheader,nounits"}, raw))
            return false;
        const QString out = QString::fromUtf8(raw).trimmed();
        if (out.isEmpty()) return false;
        const QStringList vals = out.split('\n').first().trimmed().split(',');
        if (vals.size() < 3) return false;
        bool ok = false;
        const int util = vals[0].trimmed().toInt(&ok);
        if (!ok) return false;
        text = QString("GPU: %1% | %2/%3 MB").arg(util)
                   .arg(vals[1].trimmed().toInt()).arg(vals[2].trimmed().toInt());
        pct = util;
        return true;
    }

    bool readRocm(QString &text, int &pct) {
        QByteArray raw;
        if (!runTool("rocm-smi", {"--showuse", "--csv"}, raw)) return false;
        for (const QString &line : QString::fromUtf8(raw).split('\n')) {
            if (!line.startsWith("card", Qt::CaseInsensitive)) continue;
            const QStringList cols = line.split(',');
            if (cols.size() < 2) continue;
            bool ok = false;
            const int util = cols[1].trimmed().remove('%').trimmed().toInt(&ok);
            if (ok) {
                text = QString("AMD GPU: %1%").arg(util);
                pct = util;
                return true;
            }
        }
        return false;
    }

    // Каталог /sys/class/drm/cardN с заданным производителем (без cardN-DP-1 и т.п.)
    static QString findCardDir(const QString &vendorId) {
        static const QRegularExpression cardRe("^card\\d+$");
        QDir drm("/sys/class/drm");
        for (const QString &card : drm.entryList(QDir::Dirs | QDir::NoDotAndDotDot, QDir::Name)) {
            if (!cardRe.match(card).hasMatch()) continue;
            const QString cardPath = "/sys/class/drm/" + card;
            QFile vf(cardPath + "/device/vendor");
            if (vf.open(QIODevice::ReadOnly) && QString::fromUtf8(vf.readAll()).trimmed() == vendorId)
                return cardPath;
        }
        return QString();
    }

    static qint64 readSysfsLong(const QString &path) {
        QFile f(path);
        if (!f.open(QIODevice::ReadOnly)) return -1;
        bool ok = false;
        const qint64 val = QString::fromUtf8(f.readAll()).trimmed().toLongLong(&ok);
        return ok ? val : -1;
    }

    bool readIntelSysfs(QString &text, int &pct) {
        const QString card = findCardDir("0x8086");
        if (card.isEmpty()) return false;
        qint64 cur = -1, max = -1;
        for (const QString &p : {card + "/gt_cur_freq_mhz", card + "/gt/gt0/cur_freq_mhz",
                                 card + "/device/gt/gt0/cur_freq_mhz"}) {
            cur = readSysfsLong(p);
            if (cur >= 0) break;
        }
        for (const QString &p : {card + "/gt_max_freq_mhz", card + "/gt/gt0/rpe_freq_mhz",
                                 card + "/device/gt/gt0/rpe_freq_mhz"}) {
            max = readSysfsLong(p);
            if (max >= 0) break;
        }
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
        const QString card = findCardDir("0x1002");
        if (card.isEmpty()) return false;
        const QString dev = card + "/device";
        const qint64 used = readSysfsLong(dev + "/mem_info_vram_used");
        const qint64 total = readSysfsLong(dev + "/mem_info_vram_total");
        const qint64 busy = readSysfsLong(dev + "/gpu_busy_percent");
        if (busy >= 0) {
            pct = (int)qBound(qint64(0), busy, qint64(100));
            text = QString("AMD GPU: %1%").arg(pct);
            if (used >= 0 && total > 0)
                text += QString(" | %1/%2 MB").arg(used / (1024 * 1024)).arg(total / (1024 * 1024));
            return true;
        }
        if (used >= 0 && total > 0) {
            pct = qBound(0, (int)(100 * used / total), 100);
            text = QString("AMD GPU: VRAM %1/%2 MB (%3%)")
                       .arg(used / (1024 * 1024)).arg(total / (1024 * 1024)).arg(pct);
            return true;
        }
        return false;
    }

    bool readIntelTop(QString &text, int &pct) {
        if (QStandardPaths::findExecutable("intel_gpu_top").isEmpty()) return false;
        QProcess proc;
        proc.start("intel_gpu_top", {"-s", "100", "-l", "-o", "-"});   // поток — читаем первые данные
        if (!proc.waitForStarted(500)) return false;
        proc.waitForReadyRead(600);
        proc.kill();
        proc.waitForFinished(500);

        const QStringList lines = QString::fromUtf8(proc.readAllStandardOutput()).trimmed().split('\n');
        for (int i = lines.size() - 1; i >= 0; --i) {
            const QString line = lines[i].trimmed();
            if (!line.startsWith('{')) continue;
            QJsonParseError err;
            const QJsonDocument doc = QJsonDocument::fromJson(line.toUtf8(), &err);
            if (err.error != QJsonParseError::NoError || !doc.isObject()) continue;
            double busy = -1;
            const QJsonObject engines = doc.object().value("engines").toObject();
            for (auto it = engines.begin(); it != engines.end(); ++it)
                busy = qMax(busy, it.value().toObject().value("busy").toDouble(-1));
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
    explicit WatermarkWidget(QWidget *parent = nullptr) : QWidget(parent) {
        setAttribute(Qt::WA_TransparentForMouseEvents);
        setAttribute(Qt::WA_NoSystemBackground);
        hide();
    }

    // Раньше метод назывался setEnabled и скрывал QWidget::setEnabled()
    void setActive(bool active) {
        m_active = active;
        setVisible(active);
        update();
    }

    void setLogo(const QPixmap &logo) {
        m_logo = logo;
        m_scaledLogo = QPixmap();
        update();
    }

protected:
    void resizeEvent(QResizeEvent *event) override {
        QWidget::resizeEvent(event);
        m_scaledLogo = QPixmap();   // масштабируется заново при следующей отрисовке
    }

    void paintEvent(QPaintEvent *) override {
        if (!m_active) return;

        QPainter painter(this);
        painter.setRenderHint(QPainter::Antialiasing);

        // Diagonal warning text grid
        painter.save();
        painter.setOpacity(0.03);
        painter.setFont(QFont("Courier New", 14, QFont::Bold));
        painter.setPen(QColor("#FF0000"));
        const QString text = "UNAUTHORIZED ACCESS PROHIBITED";
        const int textWidth = painter.fontMetrics().horizontalAdvance(text);
        const int textHeight = painter.fontMetrics().height();
        painter.translate(0, height() / 2);
        painter.rotate(-30);
        for (int y = -height(); y < height() * 2; y += textHeight * 3)
            for (int x = -width(); x < width() * 2; x += textWidth + 50)
                painter.drawText(x, y, text);
        painter.restore();

        // Center logo (масштабированная копия кешируется — раньше пересчитывалась при каждой отрисовке)
        if (!m_logo.isNull()) {
            const int logoSize = qMin(width(), height()) / 2;
            if (logoSize <= 0) return;
            if (m_scaledLogo.isNull())
                m_scaledLogo = m_logo.scaled(logoSize, logoSize, Qt::KeepAspectRatio, Qt::SmoothTransformation);
            painter.setOpacity(0.05);
            painter.drawPixmap((width() - m_scaledLogo.width()) / 2,
                               (height() - m_scaledLogo.height()) / 2, m_scaledLogo);
        }
    }

private:
    bool m_active = false;
    QPixmap m_logo;
    QPixmap m_scaledLogo;
};

// ═══════════════════════════════════════════════════════════════════════════════
// PULSATING LABEL (For SAP header - animated warning)
// Рисуется сама: раньше каждые 50 мс заново применялась таблица стилей,
// что заставляло Qt пересчитывать стиль виджета (заметная нагрузка на CPU).
// ═══════════════════════════════════════════════════════════════════════════════

class PulsatingLabel : public QLabel {
    Q_OBJECT
public:
    explicit PulsatingLabel(const QString &text, QWidget *parent = nullptr) : QLabel(text, parent) {
        m_timer = new QTimer(this);
        m_timer->setInterval(80);
        connect(m_timer, &QTimer::timeout, this, &PulsatingLabel::updatePulse);
    }

    void setColors(const QColor &background, const QColor &foreground) {
        m_bg = background;
        m_fg = foreground;
        update();
    }

    void startPulsing() {
        m_phase = 0;
        m_timer->start();
    }

    void stopPulsing() {
        m_timer->stop();
        update();
    }

    QSize sizeHint() const override {
        const QFontMetrics fm(labelFont());
        return QSize(fm.horizontalAdvance(text()) + 30, fm.height() + 16);
    }
    QSize minimumSizeHint() const override { return sizeHint(); }

protected:
    void paintEvent(QPaintEvent *) override {
        QPainter p(this);
        p.fillRect(rect(), m_bg);
        p.setPen(m_fg);
        p.setFont(labelFont());
        p.drawText(rect(), Qt::AlignCenter, text());
    }

private slots:
    void updatePulse() {
        static constexpr double kTwoPi = 6.283185307179586;
        m_phase += 0.13;
        if (m_phase > kTwoPi) m_phase -= kTwoPi;
        m_bg = QColor(139 + static_cast<int>(116 * std::sin(m_phase)), 0, 0);
        update();
    }

private:
    QFont labelFont() const {
        QFont f = font();
        f.setBold(true);
        f.setPixelSize(12);
        f.setLetterSpacing(QFont::AbsoluteSpacing, 2);
        return f;
    }

    QTimer *m_timer;
    double m_phase = 0;
    QColor m_bg = QColor("#FF0000");
    QColor m_fg = QColor("#000000");
};

// ═══════════════════════════════════════════════════════════════════════════════
// MAIN WINDOW
// ═══════════════════════════════════════════════════════════════════════════════

class Astrex : public QMainWindow {
    Q_OBJECT

public:
    explicit Astrex(QWidget *parent = nullptr) : QMainWindow(parent) {
        qRegisterMetaType<HwSnapshot>("HwSnapshot");
        setWindowTitle(QString("ASTREX v%1 — Intelligence System").arg(ASTREX_VERSION));
        setMinimumSize(1000, 680);   // раньше 1400×900 — окно не помещалось на ноутбуках
        resize(1400, 900);

        m_securityMode = MODE_TOP_SECRET;

        initializePaths();
        setupUI();
        loadSettings();
        setupMenus();
        setupToolBar();
        setupConnections();
        setupShortcuts();
        applySecurityMode();

        QTimer::singleShot(100, this, &Astrex::checkPythonBackend);
    }

    ~Astrex() override {
        shutdownChildren(1000);
    }

private:
    struct GraphEdge {
        QString a, b, relation;
        double weight = 0;
        int count = 0;
        int files = 0;
    };

    SecurityMode m_securityMode;

    // PATHS
    QString m_projectDir;
    QString m_astrexPy;
    QString m_python;
    QStringList m_searchedPaths;
    bool m_backendFound = false;

    // UI Elements
    QWidget *m_centralWidget = nullptr;
    WatermarkWidget *m_watermark = nullptr;
    QToolBar *m_toolBar = nullptr;
    QAction *m_actScan = nullptr;
    QAction *m_actStop = nullptr;

    QComboBox *m_queryCombo;
    QLineEdit *m_pathInput;
    QPushButton *m_btnBrowse;
    QPushButton *m_btnScan;
    QPushButton *m_btnStop;
    QPushButton *m_btnClear;
    QPushButton *m_btnIndex;      // runs "astrex index <folder>" in-UI
    QPushButton *m_btnDedup;
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
    QWidget *m_dupesTab;
    QPlainTextEdit *m_logView;
    QTextEdit *m_dossierView;
    QPlainTextEdit *m_errorsView;
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
    QPointer<QProcess> m_statsProc;    // status --json (index stats / backend check)
    QPointer<QProcess> m_statusProc;   // status (dialog)
    QPointer<QProcess> m_exportProc;   // export graph
    QTimer *m_timer;
    QTimer *m_statusFlashTimer;
    QTimer *m_elapsedTimer;
    HwMonitorThread *m_hwThread = nullptr;

    QByteArray m_scanErrBuf, m_indexErrBuf, m_dedupErrBuf;

    QString m_selectedPath;
    QString m_scanFolder;   // папка и запрос последнего скана (для DOSSIER)
    QString m_scanQuery;
    int m_totalFiles = 0;
    int m_processedFiles = 0;
    int m_matchedFiles = 0;
    int m_errors = 0;
    int m_traceLevel = 0;
    int m_lastIndexDecile = -1;
    QDateTime m_startTime;
    bool m_statusFlashState = false;
    bool m_userStopped = false;
    bool m_indexStopping = false;
    bool m_dedupStopping = false;
    bool m_finalResults = false;   // получено "results_begin": дальше — итоговый список
    bool m_shuttingDown = false;
    QString m_lastCpuColor, m_lastGpuColor;

    // Entity tracking: type -> {name -> documents}
    QMap<QString, QMap<QString, int>> m_entityMap;
    // Co-occurrence внутри одного документа: "A<US>B" -> documents
    QMap<QString, int> m_coocMap;
    // Связи из графа бэкенда (событие "graph")
    QList<GraphEdge> m_graphEdges;
    // Уже добавленные в хронологию пары (файл, дата)
    QSet<QString> m_timelineKeys;

    static QChar pairSeparator() { return QChar(0x1F); }

    // ═══════════════════════════════════════════════════════════════════════════
    // PATH INITIALIZATION
    // ═══════════════════════════════════════════════════════════════════════════

    void initializePaths() {
        const QString exeDir = QCoreApplication::applicationDirPath();
        QStringList candidates;

        // Явное указание: файл astrex.py или каталог проекта
        const QString envBackend = qEnvironmentVariable("ASTREX_BACKEND");
        if (!envBackend.isEmpty()) {
            const QFileInfo fi(envBackend);
            candidates << (fi.isDir() ? QDir(envBackend).filePath("astrex.py") : envBackend);
        }
        // Только каталоги относительно программы и каталоги установки — без
        // текущего каталога: иначе запускался бы чужой astrex.py из папки, где
        // пользователь открыл терминал.
        candidates << QDir(exeDir).filePath("../astrex.py")               // <проект>/ui/Astrex
                   << QDir(exeDir).filePath("astrex.py")                  // рядом с программой
                   << QDir(exeDir).filePath("../share/astrex/astrex.py")  // make install
                   << "/usr/local/share/astrex/astrex.py"
                   << "/usr/share/astrex/astrex.py"
                   << "/opt/astrex/astrex.py";

        m_searchedPaths.clear();
        m_backendFound = false;
        for (const QString &candidate : candidates) {
            const QFileInfo fi(QDir::cleanPath(candidate));
            m_searchedPaths << fi.absoluteFilePath();
            if (fi.isFile()) {
                m_astrexPy = fi.canonicalFilePath();
                m_projectDir = QFileInfo(m_astrexPy).absolutePath();
                m_backendFound = true;
                break;
            }
        }
        if (!m_backendFound) {
            m_projectDir = QDir::cleanPath(QDir(exeDir).filePath(".."));
            m_astrexPy = QDir(m_projectDir).filePath("astrex.py");
        }
        m_python = findPython();
    }

    // Интерпретатор: $ASTREX_PYTHON → venv проекта (его создаёт install.sh) → python3
    QString findPython() const {
        const QString envPython = qEnvironmentVariable("ASTREX_PYTHON");
        if (!envPython.isEmpty()) return envPython;
        const QDir project(m_projectDir);
        for (const char *rel : {"venv/bin/python3", ".venv/bin/python3", "venv/bin/python",
                                ".venv/bin/python", "venv/Scripts/python.exe", ".venv/Scripts/python.exe"}) {
            const QFileInfo fi(project.filePath(QString::fromLatin1(rel)));
            if (fi.isFile() && fi.isExecutable()) return fi.absoluteFilePath();
        }
        const QString system = QStandardPaths::findExecutable("python3");
        return system.isEmpty() ? QStringLiteral("python3") : system;
    }

    QStringList backendArgs(const QStringList &args) const {
        return QStringList() << m_astrexPy << args;
    }

    void startBackend(QProcess *p, const QStringList &args) {
        p->setWorkingDirectory(m_projectDir);
        p->start(m_python, backendArgs(args));
    }

    QProcess *newHelperProcess() {
        QProcess *p = new QProcess(this);
        prepareBackendProcess(p);
        p->setWorkingDirectory(m_projectDir);
        return p;
    }

    void setBackendState(bool ok, const QString &text, const QString &tooltip = QString()) {
        const QString color = ok ? "#00FF41" : "#FF0000";
        m_nlpLabel->setText(text);
        m_nlpLabel->setStyleSheet(QString("color: %1;").arg(color));
        m_nlpLabel->setToolTip(tooltip.isEmpty() ? QString("Python: %1\nBackend: %2").arg(m_python, m_astrexPy)
                                                 : tooltip);
        m_sbBackend->setText(QString(" %1 ").arg(text));
        m_sbBackend->setStyleSheet(QString("color: %1; font-weight: bold;").arg(color));
    }

    void checkPythonBackend() {
        if (!m_backendFound) {
            setBackendState(false, "Backend: NOT FOUND");
            QMessageBox::warning(this, "Backend Not Found",
                QString("Python backend (astrex.py) not found.\n\n"
                        "Searched:\n%1\n\n"
                        "Expected structure:\n"
                        "ASTREX/\n"
                        "├── astrex.py       <- Python backend\n"
                        "├── core/  extractors/  ...\n"
                        "└── ui/\n"
                        "    └── Astrex      <- this program\n\n"
                        "Or set ASTREX_BACKEND=/path/to/ASTREX (and ASTREX_PYTHON if needed).")
                    .arg(m_searchedPaths.join("\n")));
            return;
        }
        setBackendState(true, "Backend: checking...");
        refreshIndexStats(true);
    }

    void onHwSnapshot(HwSnapshot snap) {
        // setStyleSheet заставляет Qt пересчитать стиль — вызываем только при смене цвета
        if (snap.cpuPercent >= 0) {
            m_cpuLabel->setText(QString("CPU: %1%").arg(snap.cpuPercent));
            const QString color = snap.cpuPercent < 50 ? "#00FF41" : snap.cpuPercent < 80 ? "#FFAA00" : "#FF4444";
            if (color != m_lastCpuColor) {
                m_lastCpuColor = color;
                m_cpuLabel->setStyleSheet("color: " + color + ";");
            }
        }
        if (!snap.gpuText.isEmpty()) {
            m_gpuLabel->setText(snap.gpuText);
            const QString color = snap.gpuPercent < 0 ? "#00BFFF" : snap.gpuPercent < 50 ? "#00FF41"
                                : snap.gpuPercent < 80 ? "#FFAA00" : "#FF4444";
            if (color != m_lastGpuColor) {
                m_lastGpuColor = color;
                m_gpuLabel->setStyleSheet("color: " + color + ";");
            }
        }
    }

    // ── Logo finder ─────────────────────────────────────────────────────────

    QString findLogo() const {
        const QStringList logoPaths = {
            ":/resources/gradx_logo.png",
            QCoreApplication::applicationDirPath() + "/resources/gradx_logo.png",
            m_projectDir + "/ui/resources/gradx_logo.png",
            QCoreApplication::applicationDirPath() + "/../share/astrex/resources/gradx_logo.png",
        };
        for (const QString &path : logoPaths)
            if (QFileInfo::exists(path)) return path;
        return QString();
    }

    // ═══════════════════════════════════════════════════════════════════════════
    // STYLE SHEETS (UNCHANGED THEMES)
    // ═══════════════════════════════════════════════════════════════════════════

    QString getStyleSheet(SecurityMode mode) const {
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
                QTextEdit, QPlainTextEdit { background-color: #080808; border: 1px solid #1A3A3A; border-radius: 3px; color: #00F0FF; padding: 5px; }
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
                QTextEdit, QPlainTextEdit { background-color: #0D0000; border: 1px solid #3A1A1A; border-radius: 3px; color: #FF6666; padding: 5px; }
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

    // Журнал — QPlainTextEdit: appendPlainText() не интерпретирует строки как HTML
    // (QTextEdit::append() принимал "<...>" из имён файлов за разметку), а
    // число строк ограничено — при десятках тысяч совпадений журнал рос без предела
    static QPlainTextEdit *makeLogView(int maxBlocks) {
        QPlainTextEdit *view = new QPlainTextEdit();
        view->setReadOnly(true);
        view->setMaximumBlockCount(maxBlocks);
        return view;
    }

    void setupUI() {
        m_centralWidget = new QWidget();
        QVBoxLayout *mainLayout = new QVBoxLayout(m_centralWidget);
        mainLayout->setSpacing(8);
        mainLayout->setContentsMargins(15, 10, 15, 10);

        // ── HEADER ──
        QHBoxLayout *headerLayout = new QHBoxLayout();
        headerLayout->setSpacing(12);

        m_logoLabel = new QLabel();
        const QString logoPath = findLogo();
        QPixmap logo;
        if (!logoPath.isEmpty() && logo.load(logoPath))
            m_logoLabel->setPixmap(logo.scaled(50, 50, Qt::KeepAspectRatio, Qt::SmoothTransformation));
        headerLayout->addWidget(m_logoLabel);

        m_titleLabel = new QLabel(QString("SYSTEM: ASTREX v%1").arg(ASTREX_VERSION));
        m_titleLabel->setStyleSheet("font-size: 20px; font-weight: bold; letter-spacing: 5px;");
        headerLayout->addWidget(m_titleLabel);

        headerLayout->addStretch();

        m_cpuLabel = new QLabel("CPU: ---");
        m_cpuLabel->setStyleSheet("color: #00BFFF;");
        m_cpuLabel->setToolTip("CPU usage");
        headerLayout->addWidget(m_cpuLabel);

        m_gpuLabel = new QLabel("GPU: ---");
        m_gpuLabel->setStyleSheet("color: #FFAA00;");
        m_gpuLabel->setToolTip("GPU usage and memory");
        headerLayout->addWidget(m_gpuLabel);

        m_nlpLabel = new QLabel("Backend: checking...");
        m_nlpLabel->setToolTip("Python backend status");
        headerLayout->addWidget(m_nlpLabel);

        m_btnModeToggle = new QPushButton("MODE: TOP SECRET");
        m_btnModeToggle->setMinimumWidth(180);
        m_btnModeToggle->setToolTip("Toggle between TOP SECRET and SAP display modes");
        headerLayout->addWidget(m_btnModeToggle);

        m_classificationLabel = new PulsatingLabel(" TOP SECRET // SI // NOFORN ");
        headerLayout->addWidget(m_classificationLabel);

        mainLayout->addLayout(headerLayout);

        // ── PATH SELECTION ──
        QHBoxLayout *pathLayout = new QHBoxLayout();
        QLabel *targetLabel = new QLabel("TARGET:");
        targetLabel->setToolTip("Directory to scan for files");
        pathLayout->addWidget(targetLabel);
        m_pathInput = new QLineEdit();
        m_pathInput->setPlaceholderText("Select target directory...");
        m_pathInput->setToolTip("Path to target directory (Ctrl+O to browse)");
        m_btnBrowse = new QPushButton("BROWSE");
        m_btnBrowse->setToolTip("Browse for target directory (Ctrl+O)");
        m_btnIndex = new QPushButton("INDEX");
        m_btnIndex->setToolTip("Pre-index this directory: extract text (and entities, if NLP is on)\n"
                               "from all files into the SQLite index. Repeated scans become much faster.\n"
                               "Click again to stop. Progress is shown in the progress bar and LIVE_LOG.");
        m_btnIndex->setMinimumWidth(110);
        pathLayout->addWidget(m_pathInput, 1);
        pathLayout->addWidget(m_btnBrowse);
        pathLayout->addWidget(m_btnIndex);
        mainLayout->addLayout(pathLayout);

        // ── QUERY INPUT (ComboBox with history) ──
        QHBoxLayout *queryLayout = new QHBoxLayout();
        QLabel *queryLabel = new QLabel("QUERY:");
        queryLabel->setToolTip("Search query: all significant words must be present (word forms included)");
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
        m_btnStop->setToolTip("Stop current scan (Escape). Press again to kill immediately.");
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
        m_chkIndex->setToolTip("Use the SQLite index: unchanged files are not re-extracted");
        m_chkNLP = new QCheckBox("NLP Analysis");
        m_chkNLP->setChecked(true);
        m_chkNLP->setToolTip("Entity extraction, semantic relevance scoring and morphology\n"
                             "(word forms: договор → договора, договору…)");
        m_chkFuzzy = new QCheckBox("Fuzzy Search");
        m_chkFuzzy->setChecked(true);
        m_chkFuzzy->setToolTip("Also find words with typos (rapidfuzz, similarity ≥ 80; ≥ 90 for short words)");

        optionsLayout->addWidget(m_chkIndex);
        optionsLayout->addWidget(m_chkNLP);
        optionsLayout->addWidget(m_chkFuzzy);
        optionsLayout->addSpacing(20);

        QLabel *lblWorkers = new QLabel("Workers:");
        lblWorkers->setToolTip("Number of worker processes");
        optionsLayout->addWidget(lblWorkers);
        m_spinWorkers = new QSpinBox();
        m_spinWorkers->setRange(1, 128);
        m_spinWorkers->setValue(qMax(1, QThread::idealThreadCount()));
        m_spinWorkers->setToolTip("Worker processes (default: number of CPU cores).\n"
                                  "Each process needs RAM; the backend lowers the count if memory is short.");
        optionsLayout->addWidget(m_spinWorkers);

        QLabel *lblScore = new QLabel("Min Score:");
        lblScore->setToolTip("Minimum relevance score to include in results (0.0 - 1.0)");
        optionsLayout->addWidget(lblScore);
        m_spinMinScore = new QDoubleSpinBox();
        m_spinMinScore->setRange(0.0, 1.0);
        m_spinMinScore->setSingleStep(0.1);
        m_spinMinScore->setDecimals(2);
        m_spinMinScore->setValue(0.1);
        m_spinMinScore->setToolTip("Relevance threshold for the final list (0 = everything that matched).\n"
                                   "Score: exact phrase ≈ 0.9+, all words nearby ≈ 0.6–0.9, fuzzy ≈ 0.3–0.7.");
        optionsLayout->addWidget(m_spinMinScore);

        QLabel *lblLimit = new QLabel("Limit:");
        lblLimit->setToolTip("Maximum number of results in the final list");
        optionsLayout->addWidget(lblLimit);
        m_spinLimit = new QSpinBox();
        m_spinLimit->setRange(10, 100000);
        m_spinLimit->setValue(500);
        m_spinLimit->setToolTip("Max results count (best first)");
        optionsLayout->addWidget(m_spinLimit);

        optionsLayout->addSpacing(20);
        QLabel *lblType = new QLabel("Show:");
        lblType->setToolTip("Filter displayed results by file type");
        optionsLayout->addWidget(lblType);
        m_extFilterCombo = new QComboBox();
        m_extFilterCombo->addItems({
            "All Types",
            "Documents",   // pdf, docx, doc, odt, rtf, txt, md, html, epub…
            "Email",       // eml, msg, pst, mbox
            "Archives",    // zip, rar, 7z, tar, gz…
            "Images",      // png, jpg, jpeg, tiff (OCR)
            "Code",        // py, js, cpp, java, go, sh…
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
        m_progressBar->setValue(0);
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

        // Tab 0: LIVE_LOG
        m_logView = makeLogView(20000);
        m_tabs->addTab(m_logView, "LIVE_LOG");

        // Tab 1: RESULTS — columns: 0=File 1=Score 2=Type 3=Path 4=Size
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

        // Tab 2: DOSSIER
        m_dossierView = new QTextEdit();
        m_dossierView->setReadOnly(true);
        m_tabs->addTab(m_dossierView, "DOSSIER");

        // Tab 3: GRAPH (entity overview)
        QWidget *graphWidget = new QWidget();
        QVBoxLayout *graphLayout = new QVBoxLayout(graphWidget);
        graphLayout->setContentsMargins(0, 0, 0, 0);

        m_graphTree = new QTreeWidget();
        m_graphTree->setHeaderLabels({"Entity / Connection", "Count", "Details"});
        m_graphTree->setRootIsDecorated(true);
        m_graphTree->setAlternatingRowColors(true);
        m_graphTree->header()->setSectionResizeMode(0, QHeaderView::Stretch);
        m_graphTree->header()->setSectionResizeMode(1, QHeaderView::ResizeToContents);
        m_graphTree->header()->setSectionResizeMode(2, QHeaderView::ResizeToContents);
        graphLayout->addWidget(m_graphTree);

        QHBoxLayout *graphBtnLayout = new QHBoxLayout();
        QPushButton *btnExportGraphML = new QPushButton("Export GraphML");
        btnExportGraphML->setToolTip("Export the entity graph of the whole INDEX in GraphML (Gephi, yEd)");
        QPushButton *btnExportGEXF = new QPushButton("Export GEXF");
        btnExportGEXF->setToolTip("Export the entity graph of the whole INDEX in GEXF (Gephi)");
        QPushButton *btnExportJSON = new QPushButton("Export JSON");
        btnExportJSON->setToolTip("Export the entity graph of the whole INDEX in JSON");
        graphBtnLayout->addWidget(btnExportGraphML);
        graphBtnLayout->addWidget(btnExportGEXF);
        graphBtnLayout->addWidget(btnExportJSON);
        graphBtnLayout->addStretch();
        graphLayout->addLayout(graphBtnLayout);

        connect(btnExportGraphML, &QPushButton::clicked, this, [this]() { exportGraph("graphml"); });
        connect(btnExportGEXF, &QPushButton::clicked, this, [this]() { exportGraph("gexf"); });
        connect(btnExportJSON, &QPushButton::clicked, this, [this]() { exportGraph("json"); });

        m_tabs->addTab(graphWidget, "GRAPH");

        // Tab 4: TIMELINE — chronological events extracted from match snippets
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

        // Tab 5: DUPES — fingerprint-based deduplication report
        m_dupesView = new QTreeWidget();
        m_dupesView->setHeaderLabels({"Group / File", "Size", "Similarity", "Path"});
        m_dupesView->setRootIsDecorated(true);
        m_dupesView->setAlternatingRowColors(true);
        m_dupesView->setSortingEnabled(false);
        m_dupesView->header()->setSectionResizeMode(0, QHeaderView::ResizeToContents);
        m_dupesView->header()->setSectionResizeMode(1, QHeaderView::ResizeToContents);
        m_dupesView->header()->setSectionResizeMode(2, QHeaderView::ResizeToContents);
        m_dupesView->header()->setSectionResizeMode(3, QHeaderView::Stretch);
        m_dupesTab = new QWidget();
        {
            QVBoxLayout *dupesLayout = new QVBoxLayout(m_dupesTab);
            dupesLayout->setContentsMargins(2, 2, 2, 2);
            m_btnDedup = new QPushButton("RUN DEDUP");
            m_btnDedup->setToolTip("Fingerprint all files in the target folder and report exact and near\n"
                                   "duplicates (astrex dedup). Click again to stop.");
            dupesLayout->addWidget(m_btnDedup);
            dupesLayout->addWidget(m_dupesView);
        }
        m_tabs->addTab(m_dupesTab, "DUPES");

        // Tab 6: ERRORS
        m_errorsView = makeLogView(10000);
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
        m_entitiesTree->setHeaderLabels({"Entity", "Type", "Docs"});
        m_entitiesTree->setRootIsDecorated(true);
        m_entitiesTree->setAlternatingRowColors(true);
        m_entitiesTree->header()->setSectionResizeMode(0, QHeaderView::Stretch);
        m_entitiesTree->header()->setSectionResizeMode(1, QHeaderView::ResizeToContents);
        m_entitiesTree->header()->setSectionResizeMode(2, QHeaderView::ResizeToContents);
        m_entitiesTree->setMinimumWidth(300);
        entitiesLayout->addWidget(m_entitiesTree);

        m_splitter->addWidget(entitiesPanel);
        m_splitter->setSizes({900, 350});
        mainLayout->addWidget(m_splitter, 1);

        setCentralWidget(m_centralWidget);

        // ── Watermark ──
        m_watermark = new WatermarkWidget(m_centralWidget);
        m_watermark->setGeometry(m_centralWidget->rect());
        if (!logo.isNull()) m_watermark->setLogo(logo);

        m_process = new QProcess(this);
        m_indexProcess = new QProcess(this);
        m_dedupProcess = new QProcess(this);
        prepareBackendProcess(m_process);
        prepareBackendProcess(m_indexProcess);
        prepareBackendProcess(m_dedupProcess);
        m_timer = new QTimer(this);
        m_statusFlashTimer = new QTimer(this);
        m_elapsedTimer = new QTimer(this);

        // Hardware monitor thread — CPU every 300ms, GPU/iGPU every ~900ms (cached)
        m_hwThread = new HwMonitorThread(this);
        connect(m_hwThread, &HwMonitorThread::updated, this, &Astrex::onHwSnapshot, Qt::QueuedConnection);
        m_hwThread->start(QThread::LowPriority);

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
        m_toolBar->setObjectName("actionsToolBar");
        m_toolBar->setMovable(false);
        m_toolBar->setIconSize(QSize(18, 18));

        m_actScan = m_toolBar->addAction(style()->standardIcon(QStyle::SP_MediaPlay), "SCAN");
        m_actScan->setToolTip("Start scan (Enter)");
        connect(m_actScan, &QAction::triggered, this, &Astrex::startScan);

        m_actStop = m_toolBar->addAction(style()->standardIcon(QStyle::SP_MediaStop), "STOP");
        m_actStop->setToolTip("Stop scan (Escape)");
        m_actStop->setEnabled(false);
        connect(m_actStop, &QAction::triggered, this, &Astrex::stopScan);

        m_toolBar->addSeparator();

        QAction *actClear = m_toolBar->addAction(style()->standardIcon(QStyle::SP_DialogResetButton), "CLEAR");
        actClear->setToolTip("Clear all results and logs");
        connect(actClear, &QAction::triggered, this, &Astrex::clearAll);

        QAction *actExport = m_toolBar->addAction(style()->standardIcon(QStyle::SP_DialogSaveButton), "EXPORT");
        actExport->setToolTip("Export results to JSON, CSV or GraphML (Ctrl+S)");
        connect(actExport, &QAction::triggered, this, &Astrex::exportResults);

        m_toolBar->addSeparator();

        QAction *actOpen = m_toolBar->addAction(style()->standardIcon(QStyle::SP_DirOpenIcon), "BROWSE");
        actOpen->setToolTip("Open target directory (Ctrl+O)");
        connect(actOpen, &QAction::triggered, this, &Astrex::selectDirectory);

        QAction *actStatus = m_toolBar->addAction(style()->standardIcon(QStyle::SP_FileDialogInfoView), "STATUS");
        actStatus->setToolTip("Show system status");
        connect(actStatus, &QAction::triggered, this, &Astrex::showStatus);
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
        connect(clearHistoryAction, &QAction::triggered, this, [this]() {
            m_queryCombo->clear();
            QSettings settings("GRADX", "ASTREX3");
            settings.remove("searchHistory");
            statusBar()->showMessage("Search history cleared", 5000);
        });
        fileMenu->addAction(clearHistoryAction);

        fileMenu->addSeparator();

        QAction *exitAction = new QAction("E&xit", this);
        exitAction->setShortcut(QKeySequence::Quit);
        connect(exitAction, &QAction::triggered, this, &QWidget::close);
        fileMenu->addAction(exitAction);

        QMenu *modeMenu = menuBar()->addMenu("&Mode");

        QAction *topSecretAction = new QAction("TOP SECRET (Green)", this);
        connect(topSecretAction, &QAction::triggered, this, [this]() {
            m_securityMode = MODE_TOP_SECRET;
            applySecurityMode();
        });
        modeMenu->addAction(topSecretAction);

        QAction *sapAction = new QAction("SAP — Special Access (Red)", this);
        connect(sapAction, &QAction::triggered, this, [this]() {
            m_securityMode = MODE_SAP;
            applySecurityMode();
        });
        modeMenu->addAction(sapAction);

        QMenu *toolsMenu = menuBar()->addMenu("&Tools");

        QAction *indexAction = new QAction("&Rebuild Index (remove deleted files)", this);
        indexAction->setToolTip("Index the target folder and drop index entries of files that no longer exist");
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
        connect(m_btnDedup, &QPushButton::clicked, this, &Astrex::startDedup);
        connect(m_btnModeToggle, &QPushButton::clicked, this, &Astrex::toggleMode);
        connect(m_queryCombo->lineEdit(), &QLineEdit::returnPressed, this, &Astrex::startScan);
        connect(m_pathInput, &QLineEdit::editingFinished, this, [this]() {
            const QString p = m_pathInput->text().trimmed();
            if (!p.isEmpty() && QFileInfo(p).isDir()) m_selectedPath = p;
        });

        connect(m_process, &QProcess::readyReadStandardOutput, this, &Astrex::onProcessOutput);
        connect(m_process, &QProcess::readyReadStandardError, this, &Astrex::onProcessError);
        connect(m_process, &QProcess::finished, this, &Astrex::onProcessFinished);
        connect(m_process, &QProcess::errorOccurred, this, &Astrex::onProcessErrorOccurred);

        // Index process — progress to the progress bar, other output to LIVE_LOG, stderr to ERRORS
        connect(m_indexProcess, &QProcess::readyReadStandardOutput, this, &Astrex::onIndexOutput);
        connect(m_indexProcess, &QProcess::readyReadStandardError, this, &Astrex::onIndexError);
        connect(m_indexProcess, &QProcess::finished, this, &Astrex::onIndexFinished);
        connect(m_indexProcess, &QProcess::errorOccurred, this, [this](QProcess::ProcessError e) {
            if (e != QProcess::FailedToStart) return;
            reportFailedToStart(m_indexProcess, "index");
            m_btnIndex->setText("INDEX");
            m_btnIndex->setEnabled(true);
        });

        // Dedup process — results parsed into m_dupesView
        connect(m_dedupProcess, &QProcess::readyReadStandardOutput, this, &Astrex::onDedupOutput);
        connect(m_dedupProcess, &QProcess::readyReadStandardError, this, &Astrex::onDedupError);
        connect(m_dedupProcess, &QProcess::finished, this, &Astrex::onDedupFinished);
        connect(m_dedupProcess, &QProcess::errorOccurred, this, [this](QProcess::ProcessError e) {
            if (e != QProcess::FailedToStart) return;
            reportFailedToStart(m_dedupProcess, "dedup");
            m_btnDedup->setText("RUN DEDUP");
            m_btnDedup->setEnabled(true);
        });

        connect(m_timer, &QTimer::timeout, this, &Astrex::updateStats);
        connect(m_statusFlashTimer, &QTimer::timeout, this, &Astrex::flashStatus);
        connect(m_elapsedTimer, &QTimer::timeout, this, &Astrex::updateElapsed);

        // Single-click: preview snippet without switching tab
        connect(m_resultsTree, &QTreeWidget::currentItemChanged,
                this, [this](QTreeWidgetItem *item, QTreeWidgetItem *) {
                    if (item) onResultSingleClicked(item);
                });

        // Double-click shows full dossier and switches to DOSSIER tab
        connect(m_resultsTree, &QTreeWidget::itemDoubleClicked, this, &Astrex::onResultDoubleClicked);

        // Context menu on results
        connect(m_resultsTree, &QTreeWidget::customContextMenuRequested, this, &Astrex::onResultsContextMenu);

        // Real-time filter (text + type combo)
        connect(m_filterInput, &QLineEdit::textChanged, this, &Astrex::filterResults);
        connect(m_extFilterCombo, &QComboBox::currentIndexChanged,
                this, [this](int) { filterResults(m_filterInput->text()); });
    }

    void setupShortcuts() {
        // Ctrl+F: focus filter
        QShortcut *scFilter = new QShortcut(QKeySequence("Ctrl+F"), this);
        connect(scFilter, &QShortcut::activated, this, [this]() {
            m_filterInput->setFocus();
            m_filterInput->selectAll();
        });

        // Ctrl+L: focus query
        QShortcut *scQuery = new QShortcut(QKeySequence("Ctrl+L"), this);
        connect(scQuery, &QShortcut::activated, this, [this]() {
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

    bool isScanRunning() const { return m_process->state() != QProcess::NotRunning; }

    void applySecurityMode() {
        setStyleSheet(getStyleSheet(m_securityMode));
        const bool running = isScanRunning();

        if (m_securityMode == MODE_TOP_SECRET) {
            m_classificationLabel->setText(" TOP SECRET // SI // NOFORN ");
            m_classificationLabel->setColors(QColor("#FF0000"), QColor("#000000"));
            m_classificationLabel->stopPulsing();

            m_btnModeToggle->setText("MODE: TOP SECRET");
            m_btnModeToggle->setStyleSheet("background-color: #004400; border-color: #00FF41; color: #00FF41;");

            m_watermark->setActive(false);
            m_traceLevelLabel->setVisible(false);
            m_statusFlashTimer->stop();

            if (!running) {
                m_statusLabel->setText("STATUS: IDLE");
                m_statusLabel->setStyleSheet("color: #00FF41;");
            }
            setWindowTitle(QString("ASTREX v%1 — Intelligence System [TOP SECRET]").arg(ASTREX_VERSION));
            statusBar()->showMessage("Mode: TOP SECRET | GRADX — Supported by PRAXIS");
        } else {
            m_classificationLabel->setText(" ⚠ SAP // LETHAL COUNTERMEASURES ACTIVE // EYES ONLY ⚠ ");
            m_classificationLabel->setColors(QColor("#8B0000"), QColor("#FFFFFF"));
            m_classificationLabel->startPulsing();

            m_btnModeToggle->setText("MODE: SAP");
            m_btnModeToggle->setStyleSheet("background-color: #440000; border-color: #FF0000; color: #FF0000;");

            m_watermark->setActive(true);
            m_watermark->raise();

            m_traceLevelLabel->setVisible(true);
            m_traceLevelLabel->setText(QString("TRACE LEVEL: %1").arg(m_traceLevel));
            m_traceLevelLabel->setStyleSheet("color: #FFAA00; font-weight: bold;");

            if (running) {
                m_statusFlashTimer->start(500);
            } else {
                m_statusLabel->setText("STATUS: STANDBY");
                m_statusLabel->setStyleSheet("color: #FF0000;");
            }
            setWindowTitle(QString("ASTREX v%1 — Special Access Programs [SAP]").arg(ASTREX_VERSION));
            statusBar()->showMessage("Mode: SAP — SPECIAL ACCESS REQUIRED | GRADX — Supported by PRAXIS");
        }

        m_watermark->setGeometry(m_centralWidget->rect());
        m_watermark->update();
    }

    void toggleMode() {
        if (m_securityMode == MODE_TOP_SECRET) {
            QMessageBox msgBox(this);
            msgBox.setWindowTitle("⚠ ACCESS VERIFICATION REQUIRED ⚠");
            msgBox.setText("SAP ACCESS VERIFICATION REQUIRED\n\n"
                           "You are attempting to access SAP-level systems.\n"
                           "Unauthorized access is a federal offense.\n\n"
                           "Do you have proper SAP authorization?");
            msgBox.setStandardButtons(QMessageBox::Yes | QMessageBox::No);
            msgBox.setDefaultButton(QMessageBox::No);
            if (msgBox.exec() == QMessageBox::Yes) m_securityMode = MODE_SAP;
        } else {
            m_securityMode = MODE_TOP_SECRET;
        }
        applySecurityMode();
    }

    // ═══════════════════════════════════════════════════════════════════════════
    // SETTINGS (history, splitter, options)
    // ═══════════════════════════════════════════════════════════════════════════

    void loadSettings() {
        QSettings settings("GRADX", "ASTREX3");
        m_selectedPath = settings.value("lastPath", QDir::homePath()).toString();
        m_pathInput->setText(m_selectedPath);
        restoreGeometry(settings.value("geometry").toByteArray());
        if (settings.contains("splitterState"))
            m_splitter->restoreState(settings.value("splitterState").toByteArray());

        const QStringList history = settings.value("searchHistory").toStringList();
        for (const QString &q : history)
            if (!q.trimmed().isEmpty()) m_queryCombo->addItem(q);
        m_queryCombo->setCurrentText("");

        m_chkIndex->setChecked(settings.value("options/indexCache", true).toBool());
        m_chkNLP->setChecked(settings.value("options/nlp", true).toBool());
        m_chkFuzzy->setChecked(settings.value("options/fuzzy", true).toBool());
        m_spinWorkers->setValue(settings.value("options/workers", qMax(1, QThread::idealThreadCount())).toInt());
        m_spinMinScore->setValue(settings.value("options/minScore", 0.1).toDouble());
        m_spinLimit->setValue(settings.value("options/limit", 500).toInt());
    }

    void saveSettings() {
        QSettings settings("GRADX", "ASTREX3");
        settings.setValue("lastPath", m_selectedPath);
        settings.setValue("geometry", saveGeometry());
        settings.setValue("splitterState", m_splitter->saveState());

        QStringList history;
        for (int i = 0; i < m_queryCombo->count(); ++i) history << m_queryCombo->itemText(i);
        settings.setValue("searchHistory", history);

        settings.setValue("options/indexCache", m_chkIndex->isChecked());
        settings.setValue("options/nlp", m_chkNLP->isChecked());
        settings.setValue("options/fuzzy", m_chkFuzzy->isChecked());
        settings.setValue("options/workers", m_spinWorkers->value());
        settings.setValue("options/minScore", m_spinMinScore->value());
        settings.setValue("options/limit", m_spinLimit->value());
    }

    void addToHistory(const QString &query) {
        const int idx = m_queryCombo->findText(query);
        if (idx >= 0) m_queryCombo->removeItem(idx);
        m_queryCombo->insertItem(0, query);
        m_queryCombo->setCurrentIndex(0);
        while (m_queryCombo->count() > 20) m_queryCombo->removeItem(m_queryCombo->count() - 1);
    }

    // Остановить все дочерние процессы и поток мониторинга (при закрытии окна)
    void shutdownChildren(int graceMs) {
        m_shuttingDown = true;
        if (m_hwThread) m_hwThread->stop();

        const QList<QProcess *> procs = findChildren<QProcess *>();
        for (QProcess *p : procs) {
            QObject::disconnect(p, nullptr, this, nullptr);   // слоты не должны срабатывать при выходе
            signalProcessTree(p, false);
        }
        QElapsedTimer timer;
        timer.start();
        for (QProcess *p : procs) {
            if (p->state() == QProcess::NotRunning) continue;
            const int left = qMax(100, graceMs - static_cast<int>(timer.elapsed()));
            if (!p->waitForFinished(left)) {
                signalProcessTree(p, true);
                p->waitForFinished(1000);
            }
        }
        if (m_hwThread) {
            m_hwThread->wait();   // поток прерывается за ≤ 1.5 с (таймаут внешних утилит)
            m_hwThread = nullptr;
        }
    }

protected:
    void resizeEvent(QResizeEvent *event) override {
        QMainWindow::resizeEvent(event);
        if (m_watermark) m_watermark->setGeometry(m_centralWidget->rect());
    }

    void closeEvent(QCloseEvent *event) override {
        QStringList busy;
        if (isScanRunning()) busy << "scan";
        if (m_indexProcess->state() != QProcess::NotRunning) busy << "indexing";
        if (m_dedupProcess->state() != QProcess::NotRunning) busy << "deduplication";
        if (!busy.isEmpty() &&
            QMessageBox::question(this, "Exit ASTREX",
                                  QString("A %1 is still running. Stop it and exit?").arg(busy.join(", ")),
                                  QMessageBox::Yes | QMessageBox::No, QMessageBox::No) != QMessageBox::Yes) {
            event->ignore();
            return;
        }
        saveSettings();
        shutdownChildren(3000);
        QMainWindow::closeEvent(event);
    }

    // ═══════════════════════════════════════════════════════════════════════════
    // LOG HELPERS
    // ═══════════════════════════════════════════════════════════════════════════

    void appendLog(const QString &msg) {
        m_logView->appendPlainText(QString("[%1] %2").arg(nowStamp(), msg));
    }

    void appendError(const QString &msg) {
        m_errorsView->appendPlainText(QString("[%1] %2").arg(nowStamp(), msg));
    }

    // Сообщение без вложенного цикла событий (open(), а не exec()). Из
    // обработчиков сигналов QProcess нельзя вызывать модальный exec(): во
    // вложенном цикле процесс может быть удалён (deleteLater), а QProcess
    // после возврата из обработчика обращается к себе — аварийное завершение.
    void popup(QMessageBox::Icon icon, const QString &title, const QString &text) {
        if (m_shuttingDown) return;
        auto *box = new QMessageBox(icon, title, text, QMessageBox::Ok, this);
        box->setAttribute(Qt::WA_DeleteOnClose);
        box->open();
    }

    void reportFailedToStart(QProcess *p, const QString &what) {
        const QString msg = QString("Failed to start %1: %2\n\nInterpreter: %3\nBackend: %4\n\n"
                                    "Install Python 3.10+ and the dependencies (./install.sh), or set "
                                    "ASTREX_PYTHON to the interpreter of the virtual environment.")
                                .arg(what, p->errorString(), m_python, m_astrexPy);
        appendError(msg);
        popup(QMessageBox::Critical, "ASTREX", msg);
    }

    static QString formatDuration(qint64 secs) {
        if (secs >= 3600) return QString("%1h %2m").arg(secs / 3600).arg((secs % 3600) / 60, 2, 10, QChar('0'));
        if (secs >= 60) return QString("%1m %2s").arg(secs / 60).arg(secs % 60, 2, 10, QChar('0'));
        return QString("%1s").arg(secs);
    }

    static QString extensionColor(const QString &ext) {
        static const QHash<QString, QString> colors = {
            {"PDF", "#FF6B6B"},  {"DOCX", "#4ECDC4"}, {"DOC", "#4ECDC4"},
            {"ODT", "#45B7D1"},  {"RTF", "#45B7D1"},
            {"TXT", "#95E1D3"},  {"MD", "#95E1D3"},   {"LOG", "#95E1D3"},
            {"EML", "#F7B731"},  {"MSG", "#F7B731"},  {"PST", "#F0A500"}, {"MBOX", "#F0A500"},
            {"ZIP", "#A55EEA"},  {"RAR", "#A55EEA"},  {"7Z", "#A55EEA"},
            {"TAR", "#8E44AD"},  {"GZ", "#8E44AD"},   {"BZ2", "#8E44AD"}, {"XZ", "#8E44AD"},
            {"PNG", "#00BFFF"},  {"JPG", "#00BFFF"},  {"JPEG", "#00BFFF"},
            {"TIFF", "#00BFFF"}, {"TIF", "#00BFFF"},  {"BMP", "#00BFFF"},
            {"PY", "#3DC75D"},   {"JS", "#F0DB4F"},   {"TS", "#007ACC"},
            {"CPP", "#9B4DCA"},  {"C", "#9B4DCA"},    {"H", "#9B4DCA"},
            {"JAVA", "#ED8B00"}, {"GO", "#00ADD8"},   {"RS", "#DEA584"},
            {"SH", "#89E051"},   {"BASH", "#89E051"},
            {"XLSX", "#21A366"}, {"XLS", "#21A366"},  {"ODS", "#21A366"}, {"CSV", "#98D8AA"},
            {"DB", "#FF8C00"},   {"SQLITE", "#FF8C00"}, {"SQL", "#FFA500"}, {"MDB", "#FF8C00"},
            {"JSON", "#FFCC00"}, {"XML", "#E07B39"},  {"YAML", "#CC3333"},
            {"HTML", "#E34C26"}, {"HTM", "#E34C26"},
            {"EPUB", "#C39BD3"}, {"FB2", "#C39BD3"},  {"MOBI", "#C39BD3"},
        };
        return colors.value(ext, "#888888");
    }

    static QList<QPair<QString, int>> sortedByCount(const QMap<QString, int> &map) {
        QList<QPair<QString, int>> v;
        v.reserve(map.size());
        for (auto it = map.constBegin(); it != map.constEnd(); ++it) v.append({it.key(), it.value()});
        std::stable_sort(v.begin(), v.end(), [](const auto &a, const auto &b) { return a.second > b.second; });
        return v;
    }

private slots:
    void selectDirectory() {
        const QString current = m_pathInput->text().trimmed();
        const QString start = QFileInfo(current).isDir() ? current : m_selectedPath;
        const QString dir = QFileDialog::getExistingDirectory(this, "Select Target Directory", start);
        if (!dir.isEmpty()) {
            m_selectedPath = dir;
            m_pathInput->setText(dir);
        }
    }

    // ═══════════════════════════════════════════════════════════════════════════
    // SCAN
    // ═══════════════════════════════════════════════════════════════════════════

    void startScan() {
        // Enter в поле запроса во время скана раньше очищал результаты и пытался
        // запустить второй процесс поверх работающего
        if (isScanRunning()) {
            statusBar()->showMessage("A scan is already running — stop it first", 4000);
            return;
        }

        const QString query = m_queryCombo->currentText().trimmed();
        QString folder = m_pathInput->text().trimmed();

        if (query.isEmpty()) {
            QMessageBox::warning(this, "Error", "Please enter a search query");
            m_queryCombo->setFocus();
            return;
        }
        const QFileInfo folderInfo(folder);
        if (folder.isEmpty() || !folderInfo.isDir()) {
            QMessageBox::warning(this, "Error", "Please select a valid directory");
            return;
        }
        folder = folderInfo.absoluteFilePath();

        if (!m_backendFound) {
            QMessageBox::critical(this, "Error",
                QString("Python backend not found!\n\nExpected: %1\n\n"
                        "Use Tools -> Show Paths to see configuration.").arg(m_astrexPy));
            return;
        }

        // Индексация и скан одновременно — оба пишут в SQLite
        if (m_indexProcess->state() != QProcess::NotRunning) {
            QMessageBox::warning(this, "Busy", "Indexing is in progress. Stop it before starting a scan.");
            return;
        }

        addToHistory(query);
        m_selectedPath = folder;
        m_scanFolder = folder;
        m_scanQuery = query;

        // Reset (результаты дедупликации не зависят от запроса — остаются)
        m_logView->clear();
        m_errorsView->clear();
        m_dossierView->clear();
        resetScanViews();
        m_graphEdges.clear();
        m_scanErrBuf.clear();
        m_progressBar->setMaximum(100);
        m_progressBar->setValue(0);
        m_progressBar->setFormat("%p% COMPLETE");
        m_totalFiles = 0;
        m_processedFiles = 0;
        m_matchedFiles = 0;
        m_errors = 0;
        m_traceLevel = 0;
        m_userStopped = false;
        m_finalResults = false;
        m_startTime = QDateTime::currentDateTime();
        updateTabBadges();
        updateStats();

        // Параметры — до "--", запрос и папка — после: запрос может начинаться с '-'
        QStringList args;
        args << "scan" << "--format" << "jsonl"
             << "--limit" << QString::number(m_spinLimit->value())
             << "--min-score" << QString::number(m_spinMinScore->value(), 'f', 2)
             << "--workers" << QString::number(m_spinWorkers->value());
        if (!m_chkIndex->isChecked()) args << "--no-index";
        if (!m_chkNLP->isChecked()) args << "--no-nlp" << "--no-morph";
        if (!m_chkFuzzy->isChecked()) args << "--no-fuzzy";
        args << "--" << query << folder;

        appendLog("Starting scan...");
        appendLog("Command: " + shellQuote(QStringList() << m_python << backendArgs(args)));

        m_resultsTree->setSortingEnabled(false);   // сортировка — после загрузки (быстрее)
        startBackend(m_process, args);
        setScanControls(true);

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
        if (!isScanRunning()) return;
        if (m_userStopped) {
            // Повторное нажатие — немедленно
            signalProcessTree(m_process, true);
            return;
        }
        // Раньше: terminate() + waitForFinished(3000) — интерфейс замирал,
        // а сигнал получал только главный процесс Python
        m_userStopped = true;
        stopProcessTree(m_process, 5000);
        m_statusLabel->setText("STATUS: STOPPING...");
        m_statusLabel->setStyleSheet("color: #FFAA00;");
        statusBar()->showMessage("Stopping scan... (press STOP again to kill immediately)");
    }

    void clearAll() {
        m_logView->clear();
        m_errorsView->clear();
        m_dossierView->clear();
        m_dupesView->clear();
        resetScanViews();
        m_graphEdges.clear();
        m_filterInput->clear();
        m_progressBar->setMaximum(100);
        m_progressBar->setValue(0);
        m_progressBar->setFormat("%p% COMPLETE");
        m_traceLevel = 0;
        m_matchedFiles = 0;
        m_errors = 0;
        if (!isScanRunning()) {
            m_totalFiles = 0;
            m_processedFiles = 0;
        }
        updateTabBadges();

        if (!isScanRunning()) {
            if (m_securityMode == MODE_SAP) {
                m_statusLabel->setText("STATUS: STANDBY");
                m_traceLevelLabel->setText("TRACE LEVEL: 0");
            } else {
                m_statusLabel->setText("STATUS: IDLE");
            }
        }
        m_statsLabel->setText("Files: 0/0 | Matches: 0 | Errors: 0 | Speed: 0/s");
        m_sbFiles->setText(" Files: 0 ");
        m_sbMatches->setText(" Matches: 0 ");
        m_sbElapsed->setText(" Elapsed: 00:00 ");
        statusBar()->showMessage("Cleared", 3000);
    }

    void flashStatus() {
        if (m_securityMode != MODE_SAP) {
            m_statusFlashTimer->stop();
            return;
        }
        m_statusFlashState = !m_statusFlashState;
        m_statusLabel->setStyleSheet(m_statusFlashState ? "color: #FF0000; font-weight: bold;"
                                                        : "color: #FFAA00; font-weight: bold;");
    }

    void updateElapsed() {
        const qint64 secs = qMax<qint64>(0, m_startTime.secsTo(QDateTime::currentDateTime()));
        m_sbElapsed->setText(QString(" Elapsed: %1:%2 ")
                                 .arg(secs / 60, 2, 10, QChar('0'))
                                 .arg(secs % 60, 2, 10, QChar('0')));
        if (!isScanRunning()) m_elapsedTimer->stop();
    }

    void onProcessErrorOccurred(QProcess::ProcessError error) {
        if (error == QProcess::FailedToStart) {
            // finished() в этом случае не приходит — восстанавливаем интерфейс здесь
            reportFailedToStart(m_process, "the scan");
            m_errors++;
            finishScanUi();
            m_statusLabel->setText("STATUS: ERROR");
            m_statusLabel->setStyleSheet("color: #FF0000;");
            m_progressBar->setFormat("FAILED TO START");
            updateTabBadges();
            return;
        }
        // Crashed — подробности в onProcessFinished (остановка пользователем — не ошибка)
        if (error == QProcess::Crashed || m_userStopped) return;
        appendError(QString("Backend process error: %1").arg(m_process->errorString()));
    }

    void onProcessOutput() {
        while (m_process->canReadLine()) {
            const QByteArray line = m_process->readLine().trimmed();
            if (!line.isEmpty()) handleScanLine(line);
        }
    }

    void onProcessError() {
        for (const QString &line : takeLines(m_scanErrBuf, m_process->readAllStandardError()))
            if (!isNoiseLine(line)) appendError(line);
    }

    void onProcessFinished(int exitCode, QProcess::ExitStatus status) {
        // Дочитать остаток вывода
        onProcessOutput();
        const QByteArray rest = m_process->readAllStandardOutput().trimmed();
        if (!rest.isEmpty()) handleScanLine(rest);
        for (const QString &line : takeLines(m_scanErrBuf, m_process->readAllStandardError(), true))
            if (!isNoiseLine(line)) appendError(line);

        finishScanUi();
        updateElapsed();

        const bool crashed = status == QProcess::CrashExit;
        // Для CrashExit exitCode — номер сигнала; 137 — выход бэкенда по MemoryError
        const bool oom = !m_userStopped && ((crashed && exitCode == 9) || (!crashed && exitCode == 137));
        const bool terminated = m_userStopped || (!crashed && (exitCode == 143 || exitCode == 130))
                                || (crashed && exitCode == 15);

        if (terminated) {
            m_statusLabel->setText(m_securityMode == MODE_SAP ? "STATUS: ABORTED" : "STATUS: STOPPED");
            m_statusLabel->setStyleSheet("color: #FFAA00;");
            m_progressBar->setFormat("STOPPED");
            appendLog("Scan stopped by user (partial results are shown)");
        } else if (oom) {
            m_statusLabel->setText("STATUS: OUT OF MEMORY");
            m_statusLabel->setStyleSheet("color: #FF0000;");
            m_progressBar->setFormat("OUT OF MEMORY");
            appendError("Backend killed: out of memory. Try: fewer workers, disable NLP, or a smaller folder.");
            m_errors++;
        } else if (crashed) {
            m_statusLabel->setText("STATUS: CRASHED");
            m_statusLabel->setStyleSheet("color: #FF0000;");
            m_progressBar->setFormat("CRASHED");
            appendError(QString("Backend process crashed (signal %1). See the traceback above.").arg(exitCode));
            appendLog("ERROR: Backend process crashed unexpectedly");
            m_errors++;
        } else if (exitCode != 0) {
            m_statusLabel->setText("STATUS: ERROR");
            m_statusLabel->setStyleSheet("color: #FF0000;");
            m_progressBar->setFormat(QString("ERROR (exit %1)").arg(exitCode));   // раньше "%1" выводилось буквально
            appendError(QString("Backend exited with code %1").arg(exitCode));
            m_errors++;
        } else {
            m_statusLabel->setText(m_securityMode == MODE_SAP ? "STATUS: ACQUISITION COMPLETE" : "STATUS: COMPLETE");
            m_statusLabel->setStyleSheet(m_securityMode == MODE_SAP ? "color: #FF6666;" : "color: #00FF41;");
            m_progressBar->setMaximum(qMax(1, m_progressBar->maximum()));
            m_progressBar->setValue(m_progressBar->maximum());
            m_progressBar->setFormat("%p% COMPLETE");
        }

        rebuildEntityTree();
        rebuildGraphTree();
        updateTabBadges();
        updateStats();
        buildDossierSummary();

        const qint64 secs = m_startTime.secsTo(QDateTime::currentDateTime());
        statusBar()->showMessage(QString("Finished: %1 match(es), %2 shown, %3")
                                     .arg(m_matchedFiles).arg(m_resultsTree->topLevelItemCount())
                                     .arg(formatDuration(secs)));
        if (!m_shuttingDown && m_chkIndex->isChecked()) refreshIndexStats();
    }

    void updateStats() {
        const double elapsed = m_startTime.isValid() ? m_startTime.msecsTo(QDateTime::currentDateTime()) / 1000.0 : 0;
        const double speed = elapsed > 0 ? m_processedFiles / elapsed : 0;
        m_statsLabel->setText(QString("Files: %1/%2 | Matches: %3 | Errors: %4 | Speed: %5/s")
                                  .arg(m_processedFiles).arg(m_totalFiles)
                                  .arg(m_matchedFiles).arg(m_errors)
                                  .arg(speed, 0, 'f', 0));
    }

    // ═══════════════════════════════════════════════════════════════════════════
    // RESULTS VIEW
    // ═══════════════════════════════════════════════════════════════════════════

    void onResultDoubleClicked(QTreeWidgetItem *item, int) {
        showResultDossier(item);
        m_tabs->setCurrentWidget(m_dossierView);
    }

    void onResultSingleClicked(QTreeWidgetItem *item) {
        statusBar()->showMessage(QString("[%1]  %2  score=%3  %4")
                                     .arg(item->text(2), item->text(0), item->text(1), item->text(3)), 8000);
        // DOSSIER обновляется только если он уже открыт (без переключения вкладок)
        if (m_tabs->currentWidget() == m_dossierView) showResultDossier(item);
    }

    void onResultsContextMenu(const QPoint &pos) {
        QTreeWidgetItem *item = m_resultsTree->itemAt(pos);
        if (!item) return;

        QMenu menu(this);
        QAction *actOpenFile = menu.addAction("Open File");
        QAction *actOpenFolder = menu.addAction("Open Containing Folder");
        menu.addSeparator();
        QAction *actCopy = menu.addAction("Copy Path");
        QAction *actCopyName = menu.addAction("Copy Filename");
        QAction *actCopySnippet = menu.addAction("Copy Snippet");
        menu.addSeparator();
        QAction *actViewSnippet = menu.addAction("View in Dossier");

        QAction *chosen = menu.exec(m_resultsTree->viewport()->mapToGlobal(pos));
        if (!chosen) return;

        const QString path = item->data(0, Qt::UserRole + 1).toString();
        if (chosen == actOpenFile) {
            if (!QDesktopServices::openUrl(QUrl::fromLocalFile(path)))
                statusBar()->showMessage("No application to open this file", 5000);
        } else if (chosen == actOpenFolder) {
            QDesktopServices::openUrl(QUrl::fromLocalFile(QFileInfo(path).absolutePath()));
        } else if (chosen == actCopy) {
            QApplication::clipboard()->setText(path);
            statusBar()->showMessage("Path copied to clipboard", 3000);
        } else if (chosen == actCopyName) {
            QApplication::clipboard()->setText(item->text(0));
            statusBar()->showMessage("Filename copied to clipboard", 3000);
        } else if (chosen == actCopySnippet) {
            QApplication::clipboard()->setText(item->data(0, Qt::UserRole).toString());
            statusBar()->showMessage("Snippet copied to clipboard", 3000);
        } else if (chosen == actViewSnippet) {
            onResultDoubleClicked(item, 0);
        }
    }

    void filterResults(const QString &) {
        for (int i = 0; i < m_resultsTree->topLevelItemCount(); ++i)
            applyFilterToItem(m_resultsTree->topLevelItem(i));
    }

private:
    void setScanControls(bool running) {
        m_btnScan->setEnabled(!running);
        m_btnStop->setEnabled(running);
        if (m_actScan) m_actScan->setEnabled(!running);
        if (m_actStop) m_actStop->setEnabled(running);
    }

    void finishScanUi() {
        m_timer->stop();
        m_statusFlashTimer->stop();
        m_elapsedTimer->stop();
        setScanControls(false);
        m_resultsTree->setSortingEnabled(true);   // заодно сортирует по выбранному столбцу
    }

    void resetScanViews() {
        m_resultsTree->clear();
        m_entitiesTree->clear();
        m_graphTree->clear();
        m_timelineTree->clear();
        m_entityMap.clear();
        m_coocMap.clear();
        m_timelineKeys.clear();
    }

    void handleScanLine(const QByteArray &line) {
        QJsonParseError err;
        const QJsonDocument doc = QJsonDocument::fromJson(line, &err);
        if (err.error != QJsonParseError::NoError || !doc.isObject()) {
            appendLog(QString::fromUtf8(line));
            return;
        }
        handleScanEvent(doc.object());
    }

    // События бэкенда (astrex.py scan --format jsonl):
    //   status, gpu_status, progress, match*, error*, stats, graph,
    //   results_begin, result*, complete
    void handleScanEvent(const QJsonObject &obj) {
        const QString type = obj.value("type").toString();

        if (type == "status") {
            appendLog(obj.value("msg").toString());

        } else if (type == "progress") {
            onScanProgress(obj);

        } else if (type == "match") {
            // Живая выдача с предварительной оценкой; после results_begin
            // показывается только итоговый список
            if (m_finalResults) return;
            m_matchedFiles++;
            addResultItem(obj);
            appendLog(QString("%1: %2 (score: %3)")
                          .arg(m_securityMode == MODE_SAP ? QString("TARGET") : QString("MATCH"),
                               obj.value("filename").toString(),
                               QString::number(obj.value("score").toDouble(), 'f', 3)));
            m_sbMatches->setText(QString(" Matches: %1 ").arg(m_matchedFiles));
            updateTabBadges();

        } else if (type == "error") {
            m_errors++;
            QString msg = obj.value("msg").toString();
            const QString file = obj.value("file").toString();
            if (!file.isEmpty()) msg += QString("  [%1]").arg(file);
            appendError(msg);
            updateTabBadges();

        } else if (type == "stats") {
            // Промежуточная статистика (не завершение скана — раньше здесь
            // преждевременно выводилось "SCAN COMPLETE")
            applyStats(obj);

        } else if (type == "graph") {
            loadBackendGraph(obj);

        } else if (type == "results_begin") {
            m_finalResults = true;
            m_resultsTree->clear();
            m_entityMap.clear();
            m_coocMap.clear();
            m_timelineTree->clear();
            m_timelineKeys.clear();
            appendLog(QString("Final ranking: %1 of %2 match(es) (min-score %3, limit %4)")
                          .arg(obj.value("count").toInt())
                          .arg(obj.value("total_matches").toInt())
                          .arg(obj.value("min_score").toDouble(), 0, 'f', 2)
                          .arg(obj.value("limit").toInt()));

        } else if (type == "result") {
            addResultItem(obj);

        } else if (type == "complete") {
            applyStats(obj);
            m_sbMatches->setText(QString(" Matches: %1 ").arg(m_matchedFiles));
            appendLog(m_securityMode == MODE_SAP ? "▓▓▓ ACQUISITION COMPLETE ▓▓▓" : "═══ SCAN COMPLETE ═══");
            appendLog(QString("Total: %1 files, matches: %2, shown: %3, errors: %4, time: %5")
                          .arg(m_totalFiles).arg(m_matchedFiles)
                          .arg(obj.value("returned").toInt()).arg(m_errors)
                          .arg(formatDuration(qRound64(obj.value("duration_seconds").toDouble()))));
            updateTabBadges();
        }
    }

    void onScanProgress(const QJsonObject &obj) {
        m_processedFiles = obj.value("current").toInt();
        m_totalFiles = obj.value("total").toInt();
        m_errors = qMax(m_errors, obj.value("errors").toInt());

        m_progressBar->setMaximum(m_totalFiles > 0 ? m_totalFiles : 100);
        m_progressBar->setValue(qMin(m_processedFiles, m_progressBar->maximum()));
        if (m_processedFiles > 0 && m_totalFiles > 0) {
            const double elapsed = qMax<qint64>(1, m_startTime.msecsTo(QDateTime::currentDateTime())) / 1000.0;
            const double rate = m_processedFiles / elapsed;
            const qint64 remaining = rate > 0 ? qint64((m_totalFiles - m_processedFiles) / rate) : 0;
            const int pct = int(qint64(m_processedFiles) * 100 / m_totalFiles);
            m_progressBar->setFormat(remaining > 0
                ? QString("%1% — ~%2 remaining").arg(pct).arg(formatDuration(remaining))
                : QString("%1%").arg(pct));
        }
        m_sbFiles->setText(QString(" Files: %1/%2 ").arg(m_processedFiles).arg(m_totalFiles));
        updateTraceLevel();
    }

    void applyStats(const QJsonObject &s) {
        if (s.contains("total_files")) m_totalFiles = s.value("total_files").toInt();
        if (s.contains("processed_files")) m_processedFiles = s.value("processed_files").toInt();
        if (s.contains("matched_files")) m_matchedFiles = s.value("matched_files").toInt();
        if (s.contains("errors")) m_errors = qMax(m_errors, s.value("errors").toInt());
        m_sbFiles->setText(QString(" Files: %1/%2 ").arg(m_processedFiles).arg(m_totalFiles));
        updateStats();
        updateTraceLevel();
    }

    void updateTraceLevel() {
        if (m_securityMode != MODE_SAP) return;
        const int level = qMin(10, m_matchedFiles / 3);
        if (level == m_traceLevel && !m_traceLevelLabel->text().isEmpty()) return;
        m_traceLevel = level;
        m_traceLevelLabel->setText(QString("TRACE LEVEL: %1").arg(level));
        m_traceLevelLabel->setStyleSheet(level >= 8 ? "color: #FF0000; font-weight: bold;"
                                         : level >= 5 ? "color: #FF6600; font-weight: bold;"
                                                      : "color: #FFAA00; font-weight: bold;");
    }

    void addResultItem(const QJsonObject &obj) {
        const QString path = obj.value("path").toString();
        QString name = obj.value("filename").toString();
        if (name.isEmpty()) name = QFileInfo(path).fileName();
        const double score = obj.value("score").toDouble();
        const QString snippet = obj.value("snippet").toString();
        const QJsonObject meta = obj.value("metadata").toObject();
        const qint64 size = meta.value("size").toVariant().toLongLong();   // раньше читалось несуществующее поле "size"
        QString ext = QFileInfo(path).suffix().toUpper();
        if (ext.isEmpty()) ext = "?";

        auto *item = new SortableItem();
        item->setText(0, name);
        item->setText(1, QString::number(score, 'f', 3));
        item->setData(1, SortableItem::SortRole, score);
        item->setText(2, ext);
        item->setForeground(2, QColor(extensionColor(ext)));
        item->setText(3, path);
        item->setText(4, size > 0 ? formatSize(size) : QString());
        item->setData(4, SortableItem::SortRole, static_cast<double>(size));
        item->setData(0, Qt::UserRole, snippet);
        item->setData(0, Qt::UserRole + 1, path);
        item->setData(0, Qt::UserRole + 2, obj.value("entities").toVariant());
        item->setData(0, Qt::UserRole + 3, meta.value("match_type").toString());
        if (!snippet.isEmpty()) {
            const QString tip = snippet.left(500);
            item->setToolTip(0, tip);
            item->setToolTip(3, tip);
        }
        m_resultsTree->addTopLevelItem(item);
        applyFilterToItem(item);   // фильтр действует и на новые строки

        collectEntities(obj.value("entities"));
        collectTimeline(snippet, name, path);
    }

    // Сущности документа → общий список; связи — только между сущностями
    // ОДНОГО документа (раньше пары строились из первых сущностей всего скана)
    void collectEntities(const QJsonValue &entVal) {
        QStringList docNames;
        QHash<QString, int> perType;
        auto add = [&](const QString &rawType, const QString &rawName) {
            const QString type = rawType.toUpper();
            const QString name = rawName.trimmed();
            if (type.isEmpty() || name.isEmpty()) return;
            m_entityMap[type][name]++;
            if (type == "DATES" || type == "DATE") return;   // даты не связываем — их слишком много
            int &taken = perType[type];
            if (taken < 4 && docNames.size() < 16 && !docNames.contains(name)) {
                docNames << name;
                ++taken;
            }
        };

        if (entVal.isObject()) {
            const QJsonObject entObj = entVal.toObject();
            for (auto it = entObj.begin(); it != entObj.end(); ++it)
                for (const QJsonValue &v : it.value().toArray()) add(it.key(), v.toString());
        } else if (entVal.isArray()) {
            // Старый формат: [{"text": "...", "type": "..."}]
            for (const QJsonValue &ev : entVal.toArray()) {
                const QJsonObject ent = ev.toObject();
                QString name = ent.value("text").toString();
                if (name.isEmpty()) name = ent.value("name").toString();
                add(ent.value("type").toString(), name);
            }
        }

        for (int i = 0; i < docNames.size(); ++i) {
            for (int j = i + 1; j < docNames.size(); ++j) {
                QString a = docNames[i], b = docNames[j];
                if (b < a) std::swap(a, b);
                m_coocMap[a + pairSeparator() + b]++;
            }
        }
    }

    static QString classifyEvent(const QString &context) {
        const QString c = context.toLower().replace(QChar(0x0451), QChar(0x0435));   // ё → е
        auto has = [&c](std::initializer_list<const char *> words) {
            for (const char *w : words)
                if (c.contains(QString::fromUtf8(w))) return true;
            return false;
        };
        if (has({"договор", "контракт", "соглашени", "подписан"})) return "agreement";
        if (has({"оплат", "перевод", "сумм", "рубл", "доллар", "платеж"})) return "transaction";
        if (has({"встреч", "совещани", "заседани", "собрани"})) return "meeting";
        if (has({"создан", "зарегистрирован", "основан", "учрежден"})) return "created";
        if (has({"родил", "дата рождения"})) return "birth";
        return "mention";
    }

    // Даты из фрагмента текста: 2024-03-15, 15.03.2024, 15 марта 2024.
    // Даты проверяются по календарю (раньше принималось 31.02), повторы
    // одной даты в одном файле не дублируются.
    void collectTimeline(const QString &snippet, const QString &source, const QString &path) {
        if (snippet.isEmpty()) return;
        static const QStringList months = {
            "января", "февраля", "марта", "апреля", "мая", "июня",
            "июля", "августа", "сентября", "октября", "ноября", "декабря"};
        static const QRegularExpression reISO("(?<!\\d)(\\d{4})-(\\d{1,2})-(\\d{1,2})(?!\\d)");
        static const QRegularExpression reDMY("(?<!\\d)(\\d{1,2})[./](\\d{1,2})[./](\\d{4})(?!\\d)");
        static const QRegularExpression reText(
            "(?<!\\d)(\\d{1,2})\\s+(" + months.join('|') + ")\\s+(\\d{4})(?!\\d)",
            QRegularExpression::CaseInsensitiveOption | QRegularExpression::UseUnicodePropertiesOption);

        auto add = [&](const QDate &date, qsizetype pos, qsizetype len) {
            if (!date.isValid() || date.year() < 1900 || date.year() > 2100) return;
            const QString iso = date.toString(Qt::ISODate);
            const QString key = path + pairSeparator() + iso;
            if (m_timelineKeys.contains(key)) return;
            m_timelineKeys.insert(key);
            const QString ctx = snippet.mid(qMax<qsizetype>(0, pos - 80), len + 160).simplified();
            auto *tl = new QTreeWidgetItem();
            tl->setText(0, iso);
            tl->setText(1, classifyEvent(ctx));
            tl->setText(2, source);
            tl->setText(3, ctx.left(160));
            tl->setToolTip(3, ctx);
            tl->setToolTip(2, path);
            m_timelineTree->addTopLevelItem(tl);
        };

        for (auto it = reISO.globalMatch(snippet); it.hasNext();) {
            const QRegularExpressionMatch m = it.next();
            add(QDate(m.captured(1).toInt(), m.captured(2).toInt(), m.captured(3).toInt()),
                m.capturedStart(), m.capturedLength());
        }
        for (auto it = reDMY.globalMatch(snippet); it.hasNext();) {
            const QRegularExpressionMatch m = it.next();
            add(QDate(m.captured(3).toInt(), m.captured(2).toInt(), m.captured(1).toInt()),
                m.capturedStart(), m.capturedLength());
        }
        for (auto it = reText.globalMatch(snippet); it.hasNext();) {
            const QRegularExpressionMatch m = it.next();
            const int month = months.indexOf(m.captured(2).toLower()) + 1;
            add(QDate(m.captured(3).toInt(), month, m.captured(1).toInt()), m.capturedStart(), m.capturedLength());
        }
    }

    // Граф связей бэкенда: узлы id → подпись, рёбра с типом связи и весом
    void loadBackendGraph(const QJsonObject &graph) {
        QHash<QString, QString> labels;
        for (const QJsonValue &n : graph.value("nodes").toArray()) {
            const QJsonObject o = n.toObject();
            labels.insert(o.value("id").toString(), o.value("label").toString());
        }
        m_graphEdges.clear();
        for (const QJsonValue &e : graph.value("edges").toArray()) {
            const QJsonObject o = e.toObject();
            GraphEdge edge;
            const QString src = o.value("source").toString(), dst = o.value("target").toString();
            edge.a = labels.value(src, src);
            edge.b = labels.value(dst, dst);
            edge.relation = o.value("relation").toString();
            edge.weight = o.value("weight").toDouble();
            edge.count = o.value("count").toInt(1);
            edge.files = o.value("files").toArray().size();
            if (!edge.a.isEmpty() && !edge.b.isEmpty()) m_graphEdges << edge;
        }
        std::stable_sort(m_graphEdges.begin(), m_graphEdges.end(),
                         [](const GraphEdge &x, const GraphEdge &y) { return x.weight > y.weight; });
        appendLog(QString("Entity graph: %1 node(s), %2 connection(s)").arg(labels.size()).arg(m_graphEdges.size()));
    }

    // Подсветка слов запроса во фрагменте (по началу слова: "договор" → "договору")
    QString highlightSnippet(const QString &text) const {
        static const QRegularExpression wordRe("[\\p{L}\\p{N}]+", QRegularExpression::UseUnicodePropertiesOption);
        QStringList stems;
        for (auto it = wordRe.globalMatch(m_scanQuery); it.hasNext();) {
            const QString w = it.next().captured(0);
            if (w.size() < 3) continue;
            // Основа: без двух последних букв у длинных слов; е/ё взаимозаменяемы.
            // (QRegularExpression::escape() экранирует и кириллицу — "\\е", поэтому
            // шаблон собирается посимвольно: в слове только буквы и цифры)
            QString stem;
            for (const QChar ch : (w.size() > 5 ? w.left(w.size() - 2) : w)) {
                const ushort lower = ch.toLower().unicode();
                if (lower == 0x0435 || lower == 0x0451) stem += "[\\x{0435}\\x{0451}]";
                else stem += ch;
            }
            stems << stem;
        }
        if (stems.isEmpty()) return text.toHtmlEscaped();

        const QRegularExpression re("(?<![\\p{L}\\p{N}])(?:" + stems.join('|') + ")[\\p{L}\\p{N}]*",
                                    QRegularExpression::CaseInsensitiveOption |
                                    QRegularExpression::UseUnicodePropertiesOption);
        if (!re.isValid()) return text.toHtmlEscaped();
        const QString mark = m_securityMode == MODE_TOP_SECRET ? "#005522" : "#661111";
        QString out;
        qsizetype last = 0;
        for (auto it = re.globalMatch(text); it.hasNext();) {
            const QRegularExpressionMatch m = it.next();
            out += text.mid(last, m.capturedStart() - last).toHtmlEscaped();
            out += QString("<span style='background-color:%1; color:#FFFFFF;'>%2</span>")
                       .arg(mark, m.captured(0).toHtmlEscaped());
            last = m.capturedEnd();
        }
        out += text.mid(last).toHtmlEscaped();
        return out;
    }

    void showResultDossier(QTreeWidgetItem *item) {
        const bool ts = m_securityMode == MODE_TOP_SECRET;
        const QString accent = ts ? "#00FF41" : "#FF3333";
        const QString accent2 = ts ? "#00F0FF" : "#FF6666";
        const QString snippet = item->data(0, Qt::UserRole).toString();
        const QString matchType = item->data(0, Qt::UserRole + 3).toString();
        const QVariantMap entities = item->data(0, Qt::UserRole + 2).toMap();

        // Многоаргументный arg(): "%2" внутри имени файла или фрагмента больше
        // не подменяется следующими аргументами
        QString html = QString("<h2 style='color: %1;'>%2</h2>"
                               "<p><b>Path:</b> %3</p>"
                               "<p><b>Score:</b> %4 &nbsp;&nbsp; <b>Type:</b> %5 &nbsp;&nbsp; "
                               "<b>Match:</b> %6 &nbsp;&nbsp; <b>Size:</b> %7</p>"
                               "<hr><h3>Snippet</h3>"
                               "<pre style='white-space: pre-wrap; color: %8;'>%9</pre>")
                           .arg(accent, item->text(0).toHtmlEscaped(), item->text(3).toHtmlEscaped(),
                                item->text(1), item->text(2).toHtmlEscaped(),
                                matchType.isEmpty() ? QString("—") : matchType.toHtmlEscaped(),
                                item->text(4).isEmpty() ? QString("—") : item->text(4),
                                accent2, highlightSnippet(snippet.left(5000)));

        if (!entities.isEmpty()) {
            html += "<hr><h3>Entities</h3><ul>";
            for (auto it = entities.constBegin(); it != entities.constEnd(); ++it) {
                const QStringList values = it.value().toStringList();
                if (values.isEmpty()) continue;
                QStringList escaped;
                for (const QString &v : values) escaped << v.toHtmlEscaped();
                html += QString("<li><span style='color:%1;'><b>%2</b></span>: %3</li>")
                            .arg(entityTypeColor(it.key()), it.key().toHtmlEscaped(), escaped.join(", "));
            }
            html += "</ul>";
        }
        m_dossierView->setHtml(html);
    }

    // ═══════════════════════════════════════════════════════════════════════════
    // DOSSIER SUMMARY (after scan)
    // ═══════════════════════════════════════════════════════════════════════════

    void buildDossierSummary() {
        const int resultCount = m_resultsTree->topLevelItemCount();
        const QString query = m_scanQuery.toHtmlEscaped();
        const QString folder = m_scanFolder.toHtmlEscaped();   // раньше — последний выбранный в диалоге путь

        if (resultCount == 0) {
            m_dossierView->setHtml(QString(
                "<h2 style='color: #FFAA00;'>No matches found</h2>"
                "<p>Query: %1<br>Folder: %2<br>Files scanned: %3</p>"
                "<p>Try broadening your search query, lowering Min Score, or scanning a different folder.</p>")
                .arg(query, folder, QString::number(m_totalFiles)));
            return;
        }

        const qint64 secs = m_startTime.secsTo(QDateTime::currentDateTime());
        const bool isSAP = (m_securityMode == MODE_SAP);
        const QString accent = isSAP ? "#FF3333" : "#00FF41";
        const QString accent2 = isSAP ? "#FF6666" : "#00F0FF";

        QString html = QString(
            "<div style='font-family: monospace;'>"
            "<h1 style='color: %1;'>%2</h1>"
            "<table style='color: #CCCCCC; font-size: 14px;'>"
            "<tr><td><b>Query:</b></td><td style='color: %3;'> %4</td></tr>"
            "<tr><td><b>Folder:</b></td><td> %5</td></tr>"
            "<tr><td><b>Files scanned:</b></td><td> %6</td></tr>"
            "<tr><td><b>Matches:</b></td><td style='color: %3;'> %7 (shown: %8)</td></tr>"
            "<tr><td><b>Errors:</b></td><td> %9</td></tr>")
            .arg(accent, isSAP ? QString("ACQUISITION DOSSIER") : QString("SCAN DOSSIER"), accent2, query, folder,
                 QString::number(m_totalFiles), QString::number(m_matchedFiles),
                 QString::number(resultCount), QString::number(m_errors));
        html += QString("<tr><td><b>Duration:</b></td><td> %1</td></tr></table>"
                        "<hr style='border-color: #444;'>").arg(formatDuration(secs));

        // --- Top results (в текущем порядке сортировки) ---
        const int showMax = qMin(resultCount, 20);
        html += QString("<h2 style='color: %1;'>Top Results (%2)</h2>").arg(accent2).arg(resultCount);
        html += "<table style='width:100%; color: #CCCCCC; font-size: 13px; border-collapse: collapse;'>"
                "<tr style='color: #888; border-bottom: 1px solid #444;'>"
                "<th align='left'>File</th><th align='left'>Score</th><th align='left'>Path</th></tr>";
        for (int i = 0; i < showMax; ++i) {
            const QTreeWidgetItem *item = m_resultsTree->topLevelItem(i);
            html += QString("<tr style='background: %1;'>"
                            "<td style='padding: 3px;'>%2</td>"
                            "<td style='padding: 3px; color: %3;'>%4</td>"
                            "<td style='padding: 3px; color: #888;'>%5</td></tr>")
                        .arg(i % 2 == 0 ? QString("#1a1a2e") : QString("#16213e"),
                             item->text(0).toHtmlEscaped(), accent2, item->text(1),
                             item->text(3).toHtmlEscaped());
        }
        if (resultCount > showMax)
            html += QString("<tr><td colspan='3' style='color:#888; padding:5px;'>... and %1 more</td></tr>")
                        .arg(resultCount - showMax);
        html += "</table>";

        // --- Entities summary ---
        if (!m_entityMap.isEmpty()) {
            html += QString("<hr style='border-color: #444;'><h2 style='color: %1;'>Entities</h2>").arg(accent2);
            for (auto typeIt = m_entityMap.constBegin(); typeIt != m_entityMap.constEnd(); ++typeIt) {
                const QString color = entityTypeColor(typeIt.key());
                const auto sorted = sortedByCount(typeIt.value());
                html += QString("<h3 style='color: %1;'>%2 (%3)</h3><ul style='color: #CCCCCC;'>")
                            .arg(color, typeIt.key().toHtmlEscaped(), QString::number(sorted.size()));
                const int shown = qMin<int>(sorted.size(), 15);
                for (int i = 0; i < shown; ++i)
                    html += QString("<li><span style='color:%1;'>%2</span> <span style='color:#666;'>(%3)</span></li>")
                                .arg(color, sorted[i].first.toHtmlEscaped(), QString::number(sorted[i].second));
                if (sorted.size() > shown)
                    html += QString("<li style='color:#888;'>... +%1 more</li>").arg(sorted.size() - shown);
                html += "</ul>";
            }
        }

        // --- Connections ---
        if (!m_graphEdges.isEmpty()) {
            html += QString("<hr style='border-color: #444;'><h2 style='color: %1;'>Top Connections</h2><ul>").arg(accent2);
            const int shown = qMin<int>(m_graphEdges.size(), 10);
            for (int i = 0; i < shown; ++i) {
                const GraphEdge &e = m_graphEdges[i];
                html += QString("<li>%1 &harr; %2 <span style='color:#666;'>(%3, %4)</span></li>")
                            .arg(e.a.toHtmlEscaped(), e.b.toHtmlEscaped(), e.relation.toHtmlEscaped(),
                                 QString::number(e.count));
            }
            html += "</ul>";
        }

        html += "<hr style='border-color: #444;'>"
                "<p style='color: #555; font-size: 11px;'>"
                "Double-click any result in RESULTS tab for detailed view.</p></div>";
        m_dossierView->setHtml(html);
    }

    // ═══════════════════════════════════════════════════════════════════════════
    // FILTER / BADGES / TREES
    // ═══════════════════════════════════════════════════════════════════════════

    bool itemPassesFilter(const QTreeWidgetItem *item) const {
        static const QHash<int, QSet<QString>> categories = {
            {1, {"PDF", "DOCX", "DOCM", "DOTX", "DOC", "DOT", "ODT", "OTT", "RTF", "TXT", "MD", "RST", "LOG",
                 "HTML", "HTM", "XHTML", "EPUB", "FB2", "MOBI", "AZW", "AZW3", "PPTX", "PPTM", "PPSX",
                 "PPT", "PPS", "ODP", "ODG", "XML", "JSON", "YAML", "YML", "INI", "CFG", "CONF"}},
            {2, {"EML", "MSG", "MBOX", "PST", "OST"}},
            {3, {"ZIP", "RAR", "7Z", "TAR", "GZ", "TGZ", "BZ2", "TBZ2", "XZ", "TXZ", "LZ4", "ZST",
                 "JAR", "APK", "CBZ", "CBR"}},
            {4, {"JPG", "JPEG", "PNG", "GIF", "BMP", "TIFF", "TIF", "WEBP", "SVG", "HEIC"}},
            {5, {"PY", "JS", "TS", "C", "CPP", "CC", "H", "HPP", "JAVA", "GO", "RS", "CS", "RB", "PHP",
                 "SH", "BASH", "LUA", "PL", "KT", "SWIFT", "SQL"}},
            {6, {"DB", "DB3", "SQLITE", "SQLITE3", "SQL", "MDB", "ACCDB", "MYD", "DUMP"}},
            {7, {"XLSX", "XLSM", "XLS", "XLT", "ODS", "CSV", "TSV", "NUMBERS"}},
        };
        const int typeIdx = m_extFilterCombo->currentIndex();
        if (typeIdx > 0 && !categories.value(typeIdx).contains(item->text(2))) return false;
        const QString filter = m_filterInput->text().trimmed();
        return filter.isEmpty() || item->text(0).contains(filter, Qt::CaseInsensitive)
               || item->text(3).contains(filter, Qt::CaseInsensitive);
    }

    void applyFilterToItem(QTreeWidgetItem *item) {
        item->setHidden(!itemPassesFilter(item));
    }

    void setTabBadge(QWidget *tab, const QString &name, int count) {
        const int idx = m_tabs->indexOf(tab);
        if (idx >= 0) m_tabs->setTabText(idx, count > 0 ? QString("%1 (%2)").arg(name).arg(count) : name);
    }

    void updateTabBadges() {
        setTabBadge(m_resultsTree, "RESULTS", m_resultsTree->topLevelItemCount());
        int entityCount = 0;
        for (auto it = m_entityMap.constBegin(); it != m_entityMap.constEnd(); ++it) entityCount += it.value().size();
        setTabBadge(m_graphTree->parentWidget(), "GRAPH", entityCount);
        setTabBadge(m_timelineTree, "TIMELINE", m_timelineTree->topLevelItemCount());
        setTabBadge(m_dupesTab, "DUPES", m_dupesView->topLevelItemCount());
        setTabBadge(m_errorsView, "ERRORS", m_errors);
    }

    void rebuildEntityTree() {
        m_entitiesTree->setUpdatesEnabled(false);
        m_entitiesTree->clear();
        for (auto typeIt = m_entityMap.constBegin(); typeIt != m_entityMap.constEnd(); ++typeIt) {
            const QString type = typeIt.key();
            const QString color = entityTypeColor(type);
            const auto sorted = sortedByCount(typeIt.value());

            auto *typeItem = new QTreeWidgetItem(m_entitiesTree);
            typeItem->setText(0, QString("%1 (%2)").arg(type).arg(sorted.size()));
            typeItem->setText(1, type);
            typeItem->setText(2, QString::number(sorted.size()));
            typeItem->setForeground(0, QColor(color));
            typeItem->setForeground(1, QColor(color));

            const int shown = qMin<int>(sorted.size(), 500);   // огромные списки не тормозят интерфейс
            for (int i = 0; i < shown; ++i) {
                auto *child = new QTreeWidgetItem(typeItem);
                child->setText(0, sorted[i].first);
                child->setText(1, type);
                child->setText(2, QString::number(sorted[i].second));
                child->setForeground(0, QColor(color));
            }
            if (sorted.size() > shown) {
                auto *more = new QTreeWidgetItem(typeItem);
                more->setText(0, QString("... +%1 more").arg(sorted.size() - shown));
            }
            typeItem->setExpanded(true);
        }
        m_entitiesTree->setUpdatesEnabled(true);
    }

    void rebuildGraphTree() {
        m_graphTree->setUpdatesEnabled(false);
        m_graphTree->clear();

        for (auto typeIt = m_entityMap.constBegin(); typeIt != m_entityMap.constEnd(); ++typeIt) {
            const QString color = entityTypeColor(typeIt.key());
            const auto sorted = sortedByCount(typeIt.value());
            int totalDocs = 0;
            for (const auto &p : sorted) totalDocs += p.second;

            auto *typeItem = new QTreeWidgetItem(m_graphTree);
            typeItem->setText(0, QString("%1 (%2 entities)").arg(typeIt.key()).arg(sorted.size()));
            typeItem->setText(1, QString::number(totalDocs));
            typeItem->setForeground(0, QColor(color));
            const int shown = qMin<int>(sorted.size(), 500);
            for (int i = 0; i < shown; ++i) {
                auto *child = new QTreeWidgetItem(typeItem);
                child->setText(0, sorted[i].first);
                child->setText(1, QString::number(sorted[i].second));
            }
            typeItem->setExpanded(false);
        }

        const QColor accent(m_securityMode == MODE_TOP_SECRET ? "#00FF41" : "#FF4444");
        if (!m_graphEdges.isEmpty()) {
            // Граф бэкенда: связи по близости в тексте и типизированные (works_for и т.п.)
            auto *root = new QTreeWidgetItem(m_graphTree);
            root->setText(0, QString("CONNECTIONS (%1)").arg(m_graphEdges.size()));
            root->setText(2, "relation · files");
            root->setForeground(0, accent);
            const int show = qMin<int>(m_graphEdges.size(), 100);
            for (int i = 0; i < show; ++i) {
                const GraphEdge &e = m_graphEdges[i];
                auto *child = new QTreeWidgetItem(root);
                child->setText(0, e.a + "  ↔  " + e.b);
                child->setText(1, QString::number(e.count));
                child->setText(2, QString("%1 · %2").arg(e.relation).arg(e.files));
            }
            root->setExpanded(true);
        } else if (!m_coocMap.isEmpty()) {
            const auto coocList = sortedByCount(m_coocMap);
            auto *root = new QTreeWidgetItem(m_graphTree);
            root->setText(0, QString("CO-OCCURRENCE IN DOCUMENTS (%1 pairs)").arg(coocList.size()));
            root->setForeground(0, accent);
            const int show = qMin<int>(coocList.size(), 50);
            for (int i = 0; i < show; ++i) {
                const QStringList names = coocList[i].first.split(pairSeparator());
                auto *child = new QTreeWidgetItem(root);
                child->setText(0, names.value(0) + "  ↔  " + names.value(1));
                child->setText(1, QString::number(coocList[i].second));
                child->setText(2, "same document");
            }
            root->setExpanded(true);
        }
        m_graphTree->setUpdatesEnabled(true);
    }

    // ═══════════════════════════════════════════════════════════════════════════
    // EXPORT
    // ═══════════════════════════════════════════════════════════════════════════

    // Запись результатов в файл (json / csv / graphml)
    bool exportResultsTo(const QString &filename, const QString &format) {
        QByteArray data;
        if (format == "json") {
            QJsonArray results;
            for (int i = 0; i < m_resultsTree->topLevelItemCount(); ++i) {
                const QTreeWidgetItem *item = m_resultsTree->topLevelItem(i);
                QJsonObject obj;
                obj["file"] = item->text(0);
                obj["path"] = item->data(0, Qt::UserRole + 1).toString();
                obj["score"] = item->data(1, SortableItem::SortRole).toDouble();
                obj["type"] = item->text(2);
                obj["size"] = item->data(4, SortableItem::SortRole).toDouble();
                obj["match_type"] = item->data(0, Qt::UserRole + 3).toString();
                obj["snippet"] = item->data(0, Qt::UserRole).toString();
                obj["entities"] = QJsonObject::fromVariantMap(item->data(0, Qt::UserRole + 2).toMap());
                results.append(obj);
            }
            QJsonObject doc;
            doc["query"] = m_scanQuery;
            doc["folder"] = m_scanFolder;
            doc["exported_at"] = QDateTime::currentDateTime().toString(Qt::ISODate);
            doc["results"] = results;
            data = QJsonDocument(doc).toJson(QJsonDocument::Indented);
        } else if (format == "csv") {
            // UTF-8 BOM — чтобы Excel правильно показал кириллицу
            QString csv = QString(QChar(0xFEFF)) + "File,Score,Type,Size,Path\r\n";
            for (int i = 0; i < m_resultsTree->topLevelItemCount(); ++i) {
                const QTreeWidgetItem *item = m_resultsTree->topLevelItem(i);
                csv += QStringList{csvField(item->text(0)), item->text(1), csvField(item->text(2)),
                                   QString::number(item->data(4, SortableItem::SortRole).toLongLong()),
                                   csvField(item->data(0, Qt::UserRole + 1).toString())}.join(',') + "\r\n";
            }
            data = csv.toUtf8();
        } else {
            data = buildGraphML();
        }

        QSaveFile file(filename);
        if (!file.open(QIODevice::WriteOnly) || file.write(data) != data.size() || !file.commit()) {
            popup(QMessageBox::Warning, "Export Error", QString("Could not write %1:\n%2").arg(filename, file.errorString()));
            return false;
        }
        return true;
    }

    QByteArray buildGraphML() const {
        QString out;
        QTextStream ts(&out);
        ts << "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
           << "<graphml xmlns=\"http://graphml.graphdrawing.org/xmlns\" "
              "xmlns:xsi=\"http://www.w3.org/2001/XMLSchema-instance\" "
              "xsi:schemaLocation=\"http://graphml.graphdrawing.org/xmlns "
              "http://graphml.graphdrawing.org/xmlns/1.0/graphml.xsd\">\n"
           << "  <key id=\"label\" for=\"node\" attr.name=\"label\" attr.type=\"string\"/>\n"
           << "  <key id=\"type\" for=\"node\" attr.name=\"type\" attr.type=\"string\"/>\n"
           << "  <key id=\"count\" for=\"node\" attr.name=\"count\" attr.type=\"int\"/>\n"
           << "  <key id=\"relation\" for=\"edge\" attr.name=\"relation\" attr.type=\"string\"/>\n"
           << "  <key id=\"weight\" for=\"edge\" attr.name=\"weight\" attr.type=\"double\"/>\n"
           << "  <graph id=\"G\" edgedefault=\"undirected\">\n";

        QHash<QString, QString> ids;   // подпись → id узла
        int nodeNo = 0;
        auto addNode = [&](const QString &label, const QString &type, int count) {
            if (ids.contains(label)) return;
            const QString id = QString("n%1").arg(nodeNo++);
            ids.insert(label, id);
            ts << "    <node id=\"" << id << "\">\n"
               << "      <data key=\"label\">" << xmlText(label) << "</data>\n"
               << "      <data key=\"type\">" << xmlText(type) << "</data>\n"
               << "      <data key=\"count\">" << count << "</data>\n"
               << "    </node>\n";
        };
        for (auto typeIt = m_entityMap.constBegin(); typeIt != m_entityMap.constEnd(); ++typeIt)
            for (auto it = typeIt.value().constBegin(); it != typeIt.value().constEnd(); ++it)
                addNode(it.key(), typeIt.key(), it.value());

        int edgeNo = 0;
        auto addEdge = [&](const QString &a, const QString &b, const QString &relation, double weight) {
            addNode(a, "UNKNOWN", 0);
            addNode(b, "UNKNOWN", 0);
            ts << "    <edge id=\"e" << edgeNo++ << "\" source=\"" << ids.value(a)
               << "\" target=\"" << ids.value(b) << "\">\n"
               << "      <data key=\"relation\">" << xmlText(relation) << "</data>\n"
               << "      <data key=\"weight\">" << weight << "</data>\n"
               << "    </edge>\n";
        };
        if (!m_graphEdges.isEmpty()) {
            for (const GraphEdge &e : m_graphEdges) addEdge(e.a, e.b, e.relation, e.weight);
        } else {
            for (auto it = m_coocMap.constBegin(); it != m_coocMap.constEnd(); ++it) {
                const QStringList names = it.key().split(pairSeparator());
                if (names.size() == 2) addEdge(names[0], names[1], "co-occurrence", it.value());
            }
        }
        ts << "  </graph>\n</graphml>\n";
        ts.flush();
        return out.toUtf8();
    }

private slots:
    void exportResults() {
        if (m_resultsTree->topLevelItemCount() == 0 && m_entityMap.isEmpty()) {
            QMessageBox::information(this, "Export", "Nothing to export yet — run a scan first.");
            return;
        }
        QString selectedFilter;
        QString filename = QFileDialog::getSaveFileName(
            this, "Export Results", QDir(m_selectedPath).filePath("astrex_results.json"),
            "JSON (*.json);;CSV (*.csv);;GraphML (*.graphml)", &selectedFilter);
        if (filename.isEmpty()) return;

        // Формат — по расширению, а без него — по выбранному фильтру
        // (раньше файл без расширения создавался пустым)
        QString format = QFileInfo(filename).suffix().toLower();
        if (format != "json" && format != "csv" && format != "graphml") {
            format = selectedFilter.startsWith("CSV") ? "csv" : selectedFilter.startsWith("GraphML") ? "graphml" : "json";
            filename += "." + format;
        }

        if (exportResultsTo(filename, format)) {
            statusBar()->showMessage(QString("Exported to %1").arg(filename), 8000);
            appendLog(QString("Exported %1 to %2").arg(format.toUpper(), filename));
        }
    }

    void exportGraph(const QString &format) {
        if (!m_backendFound) {
            QMessageBox::critical(this, "Error", "Backend not found!");
            return;
        }
        if (m_exportProc) {
            statusBar()->showMessage("An export is already running", 4000);
            return;
        }
        const QString filter = format == "graphml" ? "GraphML (*.graphml)"
                             : format == "gexf" ? "GEXF (*.gexf)" : "JSON (*.json)";
        QString filename = QFileDialog::getSaveFileName(this, "Export Graph (from index)",
                                                        QDir(m_selectedPath).filePath("astrex_graph." + format), filter);
        if (filename.isEmpty()) return;
        if (QFileInfo(filename).suffix().compare(format, Qt::CaseInsensitive) != 0) filename += "." + format;

        // Асинхронно: раньше интерфейс замирал до 30 с в waitForFinished()
        QProcess *proc = newHelperProcess();
        m_exportProc = proc;
        connect(proc, &QProcess::finished, this, [this, proc, filename](int code, QProcess::ExitStatus st) {
            m_exportProc = nullptr;
            proc->deleteLater();
            QStringList warnings;
            for (const QString &line : QString::fromUtf8(proc->readAllStandardError()).split('\n'))
                if (!isNoiseLine(line.trimmed())) warnings << line.trimmed();
            if (st == QProcess::NormalExit && code == 0) {
                statusBar()->showMessage(QString("Graph exported to %1").arg(filename), 8000);
                appendLog("Graph exported to " + filename);
                if (!warnings.isEmpty())
                    popup(QMessageBox::Information, "Export Graph",
                          QString("Exported to %1, but:\n\n%2").arg(filename, warnings.join('\n').right(2000)));
            } else {
                popup(QMessageBox::Warning, "Export Error",
                      warnings.isEmpty() ? QString("Export failed (exit code %1)").arg(code)
                                         : warnings.join('\n').right(2000));
            }
        });
        connect(proc, &QProcess::errorOccurred, this, [this, proc](QProcess::ProcessError e) {
            if (e != QProcess::FailedToStart) return;
            m_exportProc = nullptr;
            proc->deleteLater();
            reportFailedToStart(proc, "the export");
        });
        statusBar()->showMessage("Exporting entity graph from the index...");
        proc->start(m_python, backendArgs({"export", filename, "--format", format}));
    }

    // ═══════════════════════════════════════════════════════════════════════════
    // INDEX
    // ═══════════════════════════════════════════════════════════════════════════

    void startIndex() { startIndexJob(false); }

    // Раньше пункт меню только показывал команду для терминала
    void rebuildIndex() { startIndexJob(true); }

    void onIndexOutput() {
        static const QRegularExpression reProgress("^Progress:\\s*(\\d+)/(\\d+)");
        while (m_indexProcess->canReadLine()) {
            const QString t = QString::fromUtf8(m_indexProcess->readLine()).trimmed();
            if (t.isEmpty()) continue;
            const QRegularExpressionMatch m = reProgress.match(t);
            if (m.hasMatch()) {
                const int cur = m.captured(1).toInt();
                const int total = m.captured(2).toInt();
                if (!isScanRunning()) {
                    m_progressBar->setMaximum(qMax(1, total));
                    m_progressBar->setValue(qMin(cur, qMax(1, total)));
                    m_progressBar->setFormat(QString("INDEX %1/%2").arg(cur).arg(total));
                }
                statusBar()->showMessage(t, 3000);
                // В журнал — не каждую строку, а каждые 10%
                const int decile = total > 0 ? int(qint64(cur) * 10 / total) : 0;
                if (decile != m_lastIndexDecile) {
                    m_lastIndexDecile = decile;
                    appendLog("[IDX] " + t);
                }
                continue;
            }
            appendLog("[IDX] " + t);
        }
    }

    void onIndexError() {
        for (const QString &line : takeLines(m_indexErrBuf, m_indexProcess->readAllStandardError()))
            if (!isNoiseLine(line)) appendError("[IDX] " + line);
    }

    void onIndexFinished(int exitCode, QProcess::ExitStatus status) {
        onIndexOutput();
        const QString rest = QString::fromUtf8(m_indexProcess->readAllStandardOutput()).trimmed();
        if (!rest.isEmpty()) appendLog("[IDX] " + rest);
        for (const QString &line : takeLines(m_indexErrBuf, m_indexProcess->readAllStandardError(), true))
            if (!isNoiseLine(line)) appendError("[IDX] " + line);

        const bool crashed = status == QProcess::CrashExit;
        bool ok = false, stopped = false;
        QString msg;
        if (m_indexStopping || (!crashed && (exitCode == 143 || exitCode == 130))) {
            stopped = true;
            msg = "Indexing stopped by user (files processed so far stay in the index).";
        } else if (crashed) {
            msg = QString("Indexing process crashed (signal %1).").arg(exitCode);
        } else if (exitCode == 137) {
            msg = "Indexing stopped: out of memory. Try fewer workers.";
        } else if (exitCode != 0) {
            msg = QString("Indexing failed (exit code %1). See ERRORS.").arg(exitCode);
        } else {
            ok = true;
            msg = "Indexing completed successfully.";
        }
        m_indexStopping = false;
        m_btnIndex->setText("INDEX");
        m_btnIndex->setEnabled(true);
        if (!isScanRunning()) {
            if (ok) m_progressBar->setValue(m_progressBar->maximum());
            m_progressBar->setFormat(ok ? "INDEX COMPLETE" : stopped ? "INDEX STOPPED" : "INDEX FAILED");
        }
        appendLog(msg);
        if (!ok && !stopped) {
            appendError(msg);
            m_errors++;
            updateTabBadges();
        }
        statusBar()->showMessage(msg, 8000);
        refreshIndexStats();
    }

    // ═══════════════════════════════════════════════════════════════════════════
    // STATUS / INFO
    // ═══════════════════════════════════════════════════════════════════════════

    void showStatus() {
        if (!m_backendFound) {
            QMessageBox::critical(this, "Error",
                QString("Backend not found!\n\nExpected: %1\n\nUse Tools -> Show Paths").arg(m_astrexPy));
            return;
        }
        if (m_statusProc) {
            statusBar()->showMessage("Status request is already running", 3000);
            return;
        }
        // Асинхронно: раньше интерфейс замирал до 10 с
        QProcess *proc = newHelperProcess();
        m_statusProc = proc;
        connect(proc, &QProcess::finished, this, [this, proc](int code, QProcess::ExitStatus) {
            m_statusProc = nullptr;
            proc->deleteLater();
            QString text = QString::fromUtf8(proc->readAllStandardOutput()).trimmed();
            QStringList warnings;
            for (const QString &line : QString::fromUtf8(proc->readAllStandardError()).split('\n'))
                if (!isNoiseLine(line.trimmed())) warnings << line.trimmed();
            if (text.isEmpty()) text = QString("No output (exit code %1).").arg(code);
            if (!warnings.isEmpty()) text += "\n\nWarnings:\n" + warnings.join('\n').right(3000);
            text += QString("\n\nGUI:\n  Python:  %1\n  Backend: %2").arg(m_python, m_astrexPy);

            if (m_shuttingDown) return;
            auto *box = new QMessageBox(QMessageBox::Information, "System Status", QString(), QMessageBox::Ok, this);
            box->setAttribute(Qt::WA_DeleteOnClose);
            box->setTextFormat(Qt::RichText);
            box->setText("<pre style='font-family: monospace;'>" + text.toHtmlEscaped() + "</pre>");
            box->open();
        });
        connect(proc, &QProcess::errorOccurred, this, [this, proc](QProcess::ProcessError e) {
            if (e != QProcess::FailedToStart) return;
            m_statusProc = nullptr;
            proc->deleteLater();
            reportFailedToStart(proc, "the status command");
        });
        statusBar()->showMessage("Collecting system status...", 5000);
        proc->start(m_python, backendArgs({"status"}));
    }

    void showPaths() {
        const QString info = QString(
            "=== ASTREX Path Configuration ===\n\n"
            "Executable: %1\n"
            "Current working directory: %2\n\n"
            "Backend found: %3\n"
            "astrex.py: %4\n"
            "Project directory: %5\n"
            "Python interpreter: %6\n\n"
            "Searched (in order):\n%7\n\n"
            "Overrides:\n"
            "  ASTREX_BACKEND=<project dir or astrex.py>\n"
            "  ASTREX_PYTHON=<python interpreter>")
            .arg(QCoreApplication::applicationFilePath(), QDir::currentPath(),
                 m_backendFound ? QString("YES") : QString("NO"), m_astrexPy, m_projectDir, m_python,
                 m_searchedPaths.join('\n'));
        QMessageBox::information(this, "Path Configuration", info);
    }

    void showShortcuts() {
        QMessageBox::information(this, "Keyboard Shortcuts",
            "=== ASTREX Keyboard Shortcuts ===\n\n"
            "Ctrl+L           Focus search query\n"
            "Enter            Start scan\n"
            "Escape           Stop scan (twice: kill immediately)\n"
            "Ctrl+F           Focus result filter\n"
            "Ctrl+O           Browse target directory\n"
            "Ctrl+S           Export results\n"
            "Ctrl+Q           Quit\n\n"
            "Results Tree:\n"
            "Double-click     View file in Dossier\n"
            "Right-click      Context menu (open, copy, folder)\n"
            "Click header     Sort by column");
    }

    void showAbout() {
        const QString aboutText = m_securityMode == MODE_SAP
            ? QString("ASTREX v%1 — SAP Level\n\nCLASSIFICATION: SAP // EYES ONLY\n\n"
                      "GRADX — Supported by PRAXIS").arg(ASTREX_VERSION)
            : QString("ASTREX v%1 — Intelligence System\n\n"
                      "Forensic analysis for deep data investigation.\n\n"
                      "• SQLite + FTS5 indexing\n"
                      "• Russian NLP & morphology\n"
                      "• 60+ file formats\n"
                      "• Entity extraction\n"
                      "• Graph analysis\n\n"
                      "GRADX — Supported by PRAXIS\n\n"
                      "Backend: %2\nPython: %3")
                  .arg(QString(ASTREX_VERSION), m_backendFound ? QString("OK") : QString("NOT FOUND"), m_python);
        QMessageBox::about(this, "About ASTREX", aboutText);
    }

    // ═══════════════════════════════════════════════════════════════════════════
    // DEDUP — fingerprint-based duplicate detection
    // ═══════════════════════════════════════════════════════════════════════════

    void startDedup() {
        if (!m_backendFound) {
            QMessageBox::critical(this, "Error", "Backend not found!");
            return;
        }
        if (m_dedupProcess->state() != QProcess::NotRunning) {
            if (m_dedupStopping) {
                signalProcessTree(m_dedupProcess, true);
                return;
            }
            m_dedupStopping = true;
            stopProcessTree(m_dedupProcess);
            m_btnDedup->setText("STOPPING...");
            appendLog("Stopping deduplication...");
            return;
        }

        QString folder = m_pathInput->text().trimmed();
        const QFileInfo fi(folder);
        if (folder.isEmpty() || !fi.isDir()) {
            QMessageBox::warning(this, "DEDUP", "Select a valid target directory first.");
            return;
        }
        folder = fi.absoluteFilePath();

        m_dedupStopping = false;
        m_dedupErrBuf.clear();
        m_dupesView->clear();
        m_tabs->setCurrentWidget(m_dupesTab);
        updateTabBadges();

        // Лимит CLI по умолчанию — 1000 файлов; для GUI берём папку целиком
        const QStringList args{"dedup", folder, "--json", "--limit", "100000"};
        appendLog("Starting deduplication: " + folder);
        appendLog("Command: " + shellQuote(QStringList() << m_python << backendArgs(args)));
        m_btnDedup->setText("STOP DEDUP");
        startBackend(m_dedupProcess, args);
    }

    // Построчное чтение: раньше readAll() разрезал JSON-строки на границе блоков
    void onDedupOutput() {
        while (m_dedupProcess->canReadLine()) {
            const QByteArray raw = m_dedupProcess->readLine().trimmed();
            if (!raw.isEmpty()) handleDedupLine(raw);
        }
        updateTabBadges();
    }

    void onDedupError() {
        for (const QString &line : takeLines(m_dedupErrBuf, m_dedupProcess->readAllStandardError()))
            if (!isNoiseLine(line)) appendError("[DEDUP] " + line);
    }

    void onDedupFinished(int exitCode, QProcess::ExitStatus status) {
        onDedupOutput();
        const QByteArray rest = m_dedupProcess->readAllStandardOutput().trimmed();
        if (!rest.isEmpty()) handleDedupLine(rest);
        for (const QString &line : takeLines(m_dedupErrBuf, m_dedupProcess->readAllStandardError(), true))
            if (!isNoiseLine(line)) appendError("[DEDUP] " + line);

        const bool crashed = status == QProcess::CrashExit;
        QString msg;
        if (m_dedupStopping || (!crashed && (exitCode == 143 || exitCode == 130)))
            msg = "Deduplication stopped by user.";
        else if (crashed)
            msg = QString("Dedup process crashed (signal %1).").arg(exitCode);
        else if (exitCode != 0)
            msg = QString("Dedup failed (exit code %1). See ERRORS.").arg(exitCode);
        else
            msg = QString("Deduplication completed: %1 group(s) of duplicates.").arg(m_dupesView->topLevelItemCount());

        m_dedupStopping = false;
        m_btnDedup->setText("RUN DEDUP");
        appendLog(msg);
        statusBar()->showMessage(msg, 8000);
        updateTabBadges();
    }

private:
    void startIndexJob(bool cleanup) {
        if (!m_backendFound) {
            QMessageBox::critical(this, "Error", "Backend not found!");
            return;
        }
        if (m_indexProcess->state() != QProcess::NotRunning) {
            if (m_indexStopping) {
                signalProcessTree(m_indexProcess, true);   // повторное нажатие — немедленно
                return;
            }
            m_indexStopping = true;
            stopProcessTree(m_indexProcess);
            m_btnIndex->setText("STOPPING...");
            appendLog("Stopping indexing...");
            return;
        }

        QString folder = m_pathInput->text().trimmed();
        const QFileInfo fi(folder);
        if (folder.isEmpty() || !fi.isDir()) {
            QMessageBox::warning(this, "INDEX", "Select a valid target directory first.");
            return;
        }
        folder = fi.absoluteFilePath();

        if (isScanRunning()) {
            QMessageBox::warning(this, "Busy", "Scan is in progress. Stop it before starting indexing.");
            return;
        }

        m_indexStopping = false;
        m_indexErrBuf.clear();
        m_lastIndexDecile = -1;
        m_selectedPath = folder;

        QStringList args{"index", folder, "--workers", QString::number(m_spinWorkers->value())};
        if (cleanup) args << "--cleanup";
        if (!m_chkNLP->isChecked()) args << "--no-entities";

        m_btnIndex->setText("STOP INDEX");
        m_tabs->setCurrentWidget(m_logView);
        m_progressBar->setMaximum(100);
        m_progressBar->setValue(0);
        m_progressBar->setFormat("INDEXING...");
        appendLog(QString("Starting indexing%1: %2").arg(cleanup ? QString(" (cleanup of deleted files)") : QString(), folder));
        appendLog("Command: " + shellQuote(QStringList() << m_python << backendArgs(args)));
        startBackend(m_indexProcess, args);
    }

    // Статистика индекса (status --json); при запуске — ещё и проверка, что
    // бэкенд вообще стартует (интерпретатор найден, зависимости установлены)
    void refreshIndexStats(bool startupCheck = false) {
        if (!m_backendFound || m_shuttingDown || m_statsProc) return;
        QProcess *proc = newHelperProcess();
        m_statsProc = proc;
        connect(proc, &QProcess::finished, this, [this, proc, startupCheck](int code, QProcess::ExitStatus st) {
            m_statsProc = nullptr;
            proc->deleteLater();
            const QByteArray out = proc->readAllStandardOutput();
            const QString err = QString::fromUtf8(proc->readAllStandardError()).trimmed();
            QJsonParseError perr;
            const QJsonDocument doc = QJsonDocument::fromJson(out, &perr);

            if (st == QProcess::NormalExit && code == 0 && doc.isObject()) {
                const QJsonObject o = doc.object();
                const qint64 files = o.value("total_files").toVariant().toLongLong();
                const qint64 dbSize = o.value("db_size").toVariant().toLongLong();
                m_sbIndex->setText(QString(" Index: %1 files / %2 ").arg(files).arg(formatSize(dbSize)));
                if (startupCheck) {
                    const QJsonObject nlp = o.value("nlp").toObject();
                    const QJsonObject llm = o.value("llm").toObject();
                    const QString morph = nlp.value("morphology").toBool()
                        ? nlp.value("morphology_backend").toString() : QString("snowball stemmer");
                    setBackendState(true, "Backend: OK",
                        QString("ASTREX %1\nPython: %2\nBackend: %3\nData: %4\nMorphology: %5\nLLM (Ollama): %6")
                            .arg(o.value("version").toString(), m_python, m_astrexPy, o.value("home").toString(),
                                 morph, llm.value("available").toBool() ? QString("available")
                                                                        : QString("not available")));
                }
            } else if (startupCheck) {
                const QStringList lines = err.split('\n');
                const QString tail = lines.mid(qMax(0, lines.size() - 15)).join('\n');
                setBackendState(false, "Backend: ERROR");
                appendError("Backend self-check failed (astrex.py status --json):\n" + tail);
                m_errors++;
                updateTabBadges();
                popup(QMessageBox::Warning, "Backend Error",
                      QString("The Python backend does not start:\n\n%1\n\nInterpreter: %2\n\n"
                              "Run ./install.sh (creates venv/ with all dependencies) "
                              "or set ASTREX_PYTHON.").arg(tail.right(1500), m_python));
            }
        });
        connect(proc, &QProcess::errorOccurred, this, [this, proc, startupCheck](QProcess::ProcessError e) {
            if (e != QProcess::FailedToStart) return;
            m_statsProc = nullptr;
            proc->deleteLater();
            if (startupCheck) {
                setBackendState(false, "Backend: NO PYTHON");
                reportFailedToStart(proc, "the Python backend");
            }
        });
        proc->start(m_python, backendArgs({"status", "--json"}));
    }

    void handleDedupLine(const QByteArray &raw) {
        QJsonParseError err;
        const QJsonDocument doc = QJsonDocument::fromJson(raw, &err);
        if (err.error != QJsonParseError::NoError || !doc.isObject()) {
            appendLog("[DEDUP] " + QString::fromUtf8(raw));
            return;
        }
        const QJsonObject obj = doc.object();
        const QString type = obj.value("type").toString();

        if (type == "exact") {
            // {"type":"exact","group":[path,...],"size":N}
            const QJsonArray grp = obj.value("group").toArray();
            if (grp.isEmpty()) return;
            const qint64 size = obj.value("size").toVariant().toLongLong();
            auto *root = new QTreeWidgetItem(m_dupesView);
            root->setText(0, QString("EXACT [%1 files]").arg(grp.size()));
            root->setText(1, size > 0 ? formatSize(size) : QString());
            root->setText(2, "100%");
            if (size > 0) root->setText(3, QString("wasted: %1").arg(formatSize(size * (grp.size() - 1))));
            root->setForeground(0, QColor("#FFD700"));
            for (const QJsonValue &v : grp) {
                const QString path = v.toString();
                auto *child = new QTreeWidgetItem(root);
                child->setText(0, QFileInfo(path).fileName());
                child->setText(1, size > 0 ? formatSize(size) : QString());
                child->setText(2, "100%");
                child->setText(3, path);
            }
            root->setExpanded(true);

        } else if (type == "near") {
            // {"type":"near","path1":..,"path2":..,"similarity":0.9}
            const double sim = obj.value("similarity").toDouble();
            auto *root = new QTreeWidgetItem(m_dupesView);
            root->setText(0, QString("NEAR [%1%]").arg(qRound(sim * 100)));   // раньше "%.0f%%" выводилось буквально
            root->setText(2, QString("%1%").arg(sim * 100, 0, 'f', 1));
            root->setForeground(0, QColor("#00BFFF"));
            for (const QString &path : {obj.value("path1").toString(), obj.value("path2").toString()}) {
                auto *ch = new QTreeWidgetItem(root);
                ch->setText(0, QFileInfo(path).fileName());
                const qint64 size = QFileInfo(path).size();
                ch->setText(1, size > 0 ? formatSize(size) : QString());
                ch->setText(2, QString("%1%").arg(sim * 100, 0, 'f', 1));
                ch->setText(3, path);
            }
            root->setExpanded(true);

        } else if (type == "summary") {
            appendLog(QString("[DEDUP] %1 files, %2 unique, %3 exact group(s), %4 near pair(s), "
                              "%5 wasted, %6 unreadable")
                          .arg(obj.value("total").toInt()).arg(obj.value("unique").toInt())
                          .arg(obj.value("exact_groups").toInt()).arg(obj.value("near_pairs").toInt())
                          .arg(formatSize(obj.value("wasted_bytes").toVariant().toLongLong()))
                          .arg(obj.value("failed").toInt()));

        } else if (type == "progress") {
            statusBar()->showMessage(QString("Dedup: %1 files fingerprinted...").arg(obj.value("current").toInt()), 3000);

        } else if (type == "error") {
            appendError("[DEDUP] " + obj.value("msg").toString());

        } else {
            appendLog("[DEDUP] " + QString::fromUtf8(raw));
        }
    }
};

// ═══════════════════════════════════════════════════════════════════════════════
// MAIN
// ═══════════════════════════════════════════════════════════════════════════════

int main(int argc, char *argv[]) {
    QApplication app(argc, argv);
    app.setApplicationName("ASTREX");
    app.setApplicationVersion(ASTREX_VERSION);
    app.setOrganizationName("GRADX");

    Astrex window;
    window.show();

    return app.exec();
}

#include "main.moc"
