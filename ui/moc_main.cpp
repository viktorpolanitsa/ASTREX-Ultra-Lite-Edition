/****************************************************************************
** Meta object code from reading C++ file 'main.cpp'
**
** Created by: The Qt Meta Object Compiler version 67 (Qt 5.15.18)
**
** WARNING! All changes made in this file will be lost!
*****************************************************************************/

#include <memory>
#include <QtCore/qbytearray.h>
#include <QtCore/qmetatype.h>
#if !defined(Q_MOC_OUTPUT_REVISION)
#error "The header file 'main.cpp' doesn't include <QObject>."
#elif Q_MOC_OUTPUT_REVISION != 67
#error "This file was generated using the moc from 5.15.18. It"
#error "cannot be used with the include files from this version of Qt."
#error "(The moc has changed too much.)"
#endif

QT_BEGIN_MOC_NAMESPACE
QT_WARNING_PUSH
QT_WARNING_DISABLE_DEPRECATED
struct qt_meta_stringdata_HwMonitorThread_t {
    QByteArrayData data[5];
    char stringdata0[41];
};
#define QT_MOC_LITERAL(idx, ofs, len) \
    Q_STATIC_BYTE_ARRAY_DATA_HEADER_INITIALIZER_WITH_OFFSET(len, \
    qptrdiff(offsetof(qt_meta_stringdata_HwMonitorThread_t, stringdata0) + ofs \
        - idx * sizeof(QByteArrayData)) \
    )
static const qt_meta_stringdata_HwMonitorThread_t qt_meta_stringdata_HwMonitorThread = {
    {
QT_MOC_LITERAL(0, 0, 15), // "HwMonitorThread"
QT_MOC_LITERAL(1, 16, 7), // "updated"
QT_MOC_LITERAL(2, 24, 0), // ""
QT_MOC_LITERAL(3, 25, 10), // "HwSnapshot"
QT_MOC_LITERAL(4, 36, 4) // "snap"

    },
    "HwMonitorThread\0updated\0\0HwSnapshot\0"
    "snap"
};
#undef QT_MOC_LITERAL

static const uint qt_meta_data_HwMonitorThread[] = {

 // content:
       8,       // revision
       0,       // classname
       0,    0, // classinfo
       1,   14, // methods
       0,    0, // properties
       0,    0, // enums/sets
       0,    0, // constructors
       0,       // flags
       1,       // signalCount

 // signals: name, argc, parameters, tag, flags
       1,    1,   19,    2, 0x06 /* Public */,

 // signals: parameters
    QMetaType::Void, 0x80000000 | 3,    4,

       0        // eod
};

void HwMonitorThread::qt_static_metacall(QObject *_o, QMetaObject::Call _c, int _id, void **_a)
{
    if (_c == QMetaObject::InvokeMetaMethod) {
        auto *_t = static_cast<HwMonitorThread *>(_o);
        (void)_t;
        switch (_id) {
        case 0: _t->updated((*reinterpret_cast< HwSnapshot(*)>(_a[1]))); break;
        default: ;
        }
    } else if (_c == QMetaObject::RegisterMethodArgumentMetaType) {
        switch (_id) {
        default: *reinterpret_cast<int*>(_a[0]) = -1; break;
        case 0:
            switch (*reinterpret_cast<int*>(_a[1])) {
            default: *reinterpret_cast<int*>(_a[0]) = -1; break;
            case 0:
                *reinterpret_cast<int*>(_a[0]) = qRegisterMetaType< HwSnapshot >(); break;
            }
            break;
        }
    } else if (_c == QMetaObject::IndexOfMethod) {
        int *result = reinterpret_cast<int *>(_a[0]);
        {
            using _t = void (HwMonitorThread::*)(HwSnapshot );
            if (*reinterpret_cast<_t *>(_a[1]) == static_cast<_t>(&HwMonitorThread::updated)) {
                *result = 0;
                return;
            }
        }
    }
}

QT_INIT_METAOBJECT const QMetaObject HwMonitorThread::staticMetaObject = { {
    QMetaObject::SuperData::link<QThread::staticMetaObject>(),
    qt_meta_stringdata_HwMonitorThread.data,
    qt_meta_data_HwMonitorThread,
    qt_static_metacall,
    nullptr,
    nullptr
} };


const QMetaObject *HwMonitorThread::metaObject() const
{
    return QObject::d_ptr->metaObject ? QObject::d_ptr->dynamicMetaObject() : &staticMetaObject;
}

void *HwMonitorThread::qt_metacast(const char *_clname)
{
    if (!_clname) return nullptr;
    if (!strcmp(_clname, qt_meta_stringdata_HwMonitorThread.stringdata0))
        return static_cast<void*>(this);
    return QThread::qt_metacast(_clname);
}

int HwMonitorThread::qt_metacall(QMetaObject::Call _c, int _id, void **_a)
{
    _id = QThread::qt_metacall(_c, _id, _a);
    if (_id < 0)
        return _id;
    if (_c == QMetaObject::InvokeMetaMethod) {
        if (_id < 1)
            qt_static_metacall(this, _c, _id, _a);
        _id -= 1;
    } else if (_c == QMetaObject::RegisterMethodArgumentMetaType) {
        if (_id < 1)
            qt_static_metacall(this, _c, _id, _a);
        _id -= 1;
    }
    return _id;
}

// SIGNAL 0
void HwMonitorThread::updated(HwSnapshot _t1)
{
    void *_a[] = { nullptr, const_cast<void*>(reinterpret_cast<const void*>(std::addressof(_t1))) };
    QMetaObject::activate(this, &staticMetaObject, 0, _a);
}
struct qt_meta_stringdata_WatermarkWidget_t {
    QByteArrayData data[1];
    char stringdata0[16];
};
#define QT_MOC_LITERAL(idx, ofs, len) \
    Q_STATIC_BYTE_ARRAY_DATA_HEADER_INITIALIZER_WITH_OFFSET(len, \
    qptrdiff(offsetof(qt_meta_stringdata_WatermarkWidget_t, stringdata0) + ofs \
        - idx * sizeof(QByteArrayData)) \
    )
static const qt_meta_stringdata_WatermarkWidget_t qt_meta_stringdata_WatermarkWidget = {
    {
QT_MOC_LITERAL(0, 0, 15) // "WatermarkWidget"

    },
    "WatermarkWidget"
};
#undef QT_MOC_LITERAL

static const uint qt_meta_data_WatermarkWidget[] = {

 // content:
       8,       // revision
       0,       // classname
       0,    0, // classinfo
       0,    0, // methods
       0,    0, // properties
       0,    0, // enums/sets
       0,    0, // constructors
       0,       // flags
       0,       // signalCount

       0        // eod
};

void WatermarkWidget::qt_static_metacall(QObject *_o, QMetaObject::Call _c, int _id, void **_a)
{
    (void)_o;
    (void)_id;
    (void)_c;
    (void)_a;
}

QT_INIT_METAOBJECT const QMetaObject WatermarkWidget::staticMetaObject = { {
    QMetaObject::SuperData::link<QWidget::staticMetaObject>(),
    qt_meta_stringdata_WatermarkWidget.data,
    qt_meta_data_WatermarkWidget,
    qt_static_metacall,
    nullptr,
    nullptr
} };


const QMetaObject *WatermarkWidget::metaObject() const
{
    return QObject::d_ptr->metaObject ? QObject::d_ptr->dynamicMetaObject() : &staticMetaObject;
}

void *WatermarkWidget::qt_metacast(const char *_clname)
{
    if (!_clname) return nullptr;
    if (!strcmp(_clname, qt_meta_stringdata_WatermarkWidget.stringdata0))
        return static_cast<void*>(this);
    return QWidget::qt_metacast(_clname);
}

int WatermarkWidget::qt_metacall(QMetaObject::Call _c, int _id, void **_a)
{
    _id = QWidget::qt_metacall(_c, _id, _a);
    return _id;
}
struct qt_meta_stringdata_PulsatingLabel_t {
    QByteArrayData data[5];
    char stringdata0[61];
};
#define QT_MOC_LITERAL(idx, ofs, len) \
    Q_STATIC_BYTE_ARRAY_DATA_HEADER_INITIALIZER_WITH_OFFSET(len, \
    qptrdiff(offsetof(qt_meta_stringdata_PulsatingLabel_t, stringdata0) + ofs \
        - idx * sizeof(QByteArrayData)) \
    )
static const qt_meta_stringdata_PulsatingLabel_t qt_meta_stringdata_PulsatingLabel = {
    {
QT_MOC_LITERAL(0, 0, 14), // "PulsatingLabel"
QT_MOC_LITERAL(1, 15, 11), // "updatePulse"
QT_MOC_LITERAL(2, 27, 0), // ""
QT_MOC_LITERAL(3, 28, 16), // "updateStyleSheet"
QT_MOC_LITERAL(4, 45, 15) // "backgroundColor"

    },
    "PulsatingLabel\0updatePulse\0\0"
    "updateStyleSheet\0backgroundColor"
};
#undef QT_MOC_LITERAL

static const uint qt_meta_data_PulsatingLabel[] = {

 // content:
       8,       // revision
       0,       // classname
       0,    0, // classinfo
       2,   14, // methods
       1,   26, // properties
       0,    0, // enums/sets
       0,    0, // constructors
       0,       // flags
       0,       // signalCount

 // slots: name, argc, parameters, tag, flags
       1,    0,   24,    2, 0x08 /* Private */,
       3,    0,   25,    2, 0x08 /* Private */,

 // slots: parameters
    QMetaType::Void,
    QMetaType::Void,

 // properties: name, type, flags
       4, QMetaType::QColor, 0x00095103,

       0        // eod
};

void PulsatingLabel::qt_static_metacall(QObject *_o, QMetaObject::Call _c, int _id, void **_a)
{
    if (_c == QMetaObject::InvokeMetaMethod) {
        auto *_t = static_cast<PulsatingLabel *>(_o);
        (void)_t;
        switch (_id) {
        case 0: _t->updatePulse(); break;
        case 1: _t->updateStyleSheet(); break;
        default: ;
        }
    }
#ifndef QT_NO_PROPERTIES
    else if (_c == QMetaObject::ReadProperty) {
        auto *_t = static_cast<PulsatingLabel *>(_o);
        (void)_t;
        void *_v = _a[0];
        switch (_id) {
        case 0: *reinterpret_cast< QColor*>(_v) = _t->backgroundColor(); break;
        default: break;
        }
    } else if (_c == QMetaObject::WriteProperty) {
        auto *_t = static_cast<PulsatingLabel *>(_o);
        (void)_t;
        void *_v = _a[0];
        switch (_id) {
        case 0: _t->setBackgroundColor(*reinterpret_cast< QColor*>(_v)); break;
        default: break;
        }
    } else if (_c == QMetaObject::ResetProperty) {
    }
#endif // QT_NO_PROPERTIES
    (void)_a;
}

QT_INIT_METAOBJECT const QMetaObject PulsatingLabel::staticMetaObject = { {
    QMetaObject::SuperData::link<QLabel::staticMetaObject>(),
    qt_meta_stringdata_PulsatingLabel.data,
    qt_meta_data_PulsatingLabel,
    qt_static_metacall,
    nullptr,
    nullptr
} };


const QMetaObject *PulsatingLabel::metaObject() const
{
    return QObject::d_ptr->metaObject ? QObject::d_ptr->dynamicMetaObject() : &staticMetaObject;
}

void *PulsatingLabel::qt_metacast(const char *_clname)
{
    if (!_clname) return nullptr;
    if (!strcmp(_clname, qt_meta_stringdata_PulsatingLabel.stringdata0))
        return static_cast<void*>(this);
    return QLabel::qt_metacast(_clname);
}

int PulsatingLabel::qt_metacall(QMetaObject::Call _c, int _id, void **_a)
{
    _id = QLabel::qt_metacall(_c, _id, _a);
    if (_id < 0)
        return _id;
    if (_c == QMetaObject::InvokeMetaMethod) {
        if (_id < 2)
            qt_static_metacall(this, _c, _id, _a);
        _id -= 2;
    } else if (_c == QMetaObject::RegisterMethodArgumentMetaType) {
        if (_id < 2)
            *reinterpret_cast<int*>(_a[0]) = -1;
        _id -= 2;
    }
#ifndef QT_NO_PROPERTIES
    else if (_c == QMetaObject::ReadProperty || _c == QMetaObject::WriteProperty
            || _c == QMetaObject::ResetProperty || _c == QMetaObject::RegisterPropertyMetaType) {
        qt_static_metacall(this, _c, _id, _a);
        _id -= 1;
    } else if (_c == QMetaObject::QueryPropertyDesignable) {
        _id -= 1;
    } else if (_c == QMetaObject::QueryPropertyScriptable) {
        _id -= 1;
    } else if (_c == QMetaObject::QueryPropertyStored) {
        _id -= 1;
    } else if (_c == QMetaObject::QueryPropertyEditable) {
        _id -= 1;
    } else if (_c == QMetaObject::QueryPropertyUser) {
        _id -= 1;
    }
#endif // QT_NO_PROPERTIES
    return _id;
}
struct qt_meta_stringdata_Astrex_t {
    QByteArrayData data[39];
    char stringdata0[495];
};
#define QT_MOC_LITERAL(idx, ofs, len) \
    Q_STATIC_BYTE_ARRAY_DATA_HEADER_INITIALIZER_WITH_OFFSET(len, \
    qptrdiff(offsetof(qt_meta_stringdata_Astrex_t, stringdata0) + ofs \
        - idx * sizeof(QByteArrayData)) \
    )
static const qt_meta_stringdata_Astrex_t qt_meta_stringdata_Astrex = {
    {
QT_MOC_LITERAL(0, 0, 6), // "Astrex"
QT_MOC_LITERAL(1, 7, 15), // "selectDirectory"
QT_MOC_LITERAL(2, 23, 0), // ""
QT_MOC_LITERAL(3, 24, 9), // "startScan"
QT_MOC_LITERAL(4, 34, 8), // "stopScan"
QT_MOC_LITERAL(5, 43, 8), // "clearAll"
QT_MOC_LITERAL(6, 52, 11), // "flashStatus"
QT_MOC_LITERAL(7, 64, 13), // "updateElapsed"
QT_MOC_LITERAL(8, 78, 22), // "onProcessErrorOccurred"
QT_MOC_LITERAL(9, 101, 22), // "QProcess::ProcessError"
QT_MOC_LITERAL(10, 124, 5), // "error"
QT_MOC_LITERAL(11, 130, 15), // "onProcessOutput"
QT_MOC_LITERAL(12, 146, 14), // "onProcessError"
QT_MOC_LITERAL(13, 161, 17), // "onProcessFinished"
QT_MOC_LITERAL(14, 179, 8), // "exitCode"
QT_MOC_LITERAL(15, 188, 20), // "QProcess::ExitStatus"
QT_MOC_LITERAL(16, 209, 6), // "status"
QT_MOC_LITERAL(17, 216, 11), // "updateStats"
QT_MOC_LITERAL(18, 228, 21), // "onResultDoubleClicked"
QT_MOC_LITERAL(19, 250, 16), // "QTreeWidgetItem*"
QT_MOC_LITERAL(20, 267, 4), // "item"
QT_MOC_LITERAL(21, 272, 19), // "buildDossierSummary"
QT_MOC_LITERAL(22, 292, 20), // "onResultsContextMenu"
QT_MOC_LITERAL(23, 313, 3), // "pos"
QT_MOC_LITERAL(24, 317, 13), // "filterResults"
QT_MOC_LITERAL(25, 331, 4), // "text"
QT_MOC_LITERAL(26, 336, 15), // "updateTabBadges"
QT_MOC_LITERAL(27, 352, 17), // "rebuildEntityTree"
QT_MOC_LITERAL(28, 370, 16), // "rebuildGraphTree"
QT_MOC_LITERAL(29, 387, 13), // "exportResults"
QT_MOC_LITERAL(30, 401, 11), // "exportGraph"
QT_MOC_LITERAL(31, 413, 6), // "format"
QT_MOC_LITERAL(32, 420, 12), // "rebuildIndex"
QT_MOC_LITERAL(33, 433, 10), // "showStatus"
QT_MOC_LITERAL(34, 444, 9), // "showPaths"
QT_MOC_LITERAL(35, 454, 13), // "showShortcuts"
QT_MOC_LITERAL(36, 468, 9), // "showAbout"
QT_MOC_LITERAL(37, 478, 10), // "formatSize"
QT_MOC_LITERAL(38, 489, 5) // "bytes"

    },
    "Astrex\0selectDirectory\0\0startScan\0"
    "stopScan\0clearAll\0flashStatus\0"
    "updateElapsed\0onProcessErrorOccurred\0"
    "QProcess::ProcessError\0error\0"
    "onProcessOutput\0onProcessError\0"
    "onProcessFinished\0exitCode\0"
    "QProcess::ExitStatus\0status\0updateStats\0"
    "onResultDoubleClicked\0QTreeWidgetItem*\0"
    "item\0buildDossierSummary\0onResultsContextMenu\0"
    "pos\0filterResults\0text\0updateTabBadges\0"
    "rebuildEntityTree\0rebuildGraphTree\0"
    "exportResults\0exportGraph\0format\0"
    "rebuildIndex\0showStatus\0showPaths\0"
    "showShortcuts\0showAbout\0formatSize\0"
    "bytes"
};
#undef QT_MOC_LITERAL

static const uint qt_meta_data_Astrex[] = {

 // content:
       8,       // revision
       0,       // classname
       0,    0, // classinfo
      26,   14, // methods
       0,    0, // properties
       0,    0, // enums/sets
       0,    0, // constructors
       0,       // flags
       0,       // signalCount

 // slots: name, argc, parameters, tag, flags
       1,    0,  144,    2, 0x08 /* Private */,
       3,    0,  145,    2, 0x08 /* Private */,
       4,    0,  146,    2, 0x08 /* Private */,
       5,    0,  147,    2, 0x08 /* Private */,
       6,    0,  148,    2, 0x08 /* Private */,
       7,    0,  149,    2, 0x08 /* Private */,
       8,    1,  150,    2, 0x08 /* Private */,
      11,    0,  153,    2, 0x08 /* Private */,
      12,    0,  154,    2, 0x08 /* Private */,
      13,    2,  155,    2, 0x08 /* Private */,
      17,    0,  160,    2, 0x08 /* Private */,
      18,    2,  161,    2, 0x08 /* Private */,
      21,    0,  166,    2, 0x08 /* Private */,
      22,    1,  167,    2, 0x08 /* Private */,
      24,    1,  170,    2, 0x08 /* Private */,
      26,    0,  173,    2, 0x08 /* Private */,
      27,    0,  174,    2, 0x08 /* Private */,
      28,    0,  175,    2, 0x08 /* Private */,
      29,    0,  176,    2, 0x08 /* Private */,
      30,    1,  177,    2, 0x08 /* Private */,
      32,    0,  180,    2, 0x08 /* Private */,
      33,    0,  181,    2, 0x08 /* Private */,
      34,    0,  182,    2, 0x08 /* Private */,
      35,    0,  183,    2, 0x08 /* Private */,
      36,    0,  184,    2, 0x08 /* Private */,
      37,    1,  185,    2, 0x08 /* Private */,

 // slots: parameters
    QMetaType::Void,
    QMetaType::Void,
    QMetaType::Void,
    QMetaType::Void,
    QMetaType::Void,
    QMetaType::Void,
    QMetaType::Void, 0x80000000 | 9,   10,
    QMetaType::Void,
    QMetaType::Void,
    QMetaType::Void, QMetaType::Int, 0x80000000 | 15,   14,   16,
    QMetaType::Void,
    QMetaType::Void, 0x80000000 | 19, QMetaType::Int,   20,    2,
    QMetaType::Void,
    QMetaType::Void, QMetaType::QPoint,   23,
    QMetaType::Void, QMetaType::QString,   25,
    QMetaType::Void,
    QMetaType::Void,
    QMetaType::Void,
    QMetaType::Void,
    QMetaType::Void, QMetaType::QString,   31,
    QMetaType::Void,
    QMetaType::Void,
    QMetaType::Void,
    QMetaType::Void,
    QMetaType::Void,
    QMetaType::QString, QMetaType::LongLong,   38,

       0        // eod
};

void Astrex::qt_static_metacall(QObject *_o, QMetaObject::Call _c, int _id, void **_a)
{
    if (_c == QMetaObject::InvokeMetaMethod) {
        auto *_t = static_cast<Astrex *>(_o);
        (void)_t;
        switch (_id) {
        case 0: _t->selectDirectory(); break;
        case 1: _t->startScan(); break;
        case 2: _t->stopScan(); break;
        case 3: _t->clearAll(); break;
        case 4: _t->flashStatus(); break;
        case 5: _t->updateElapsed(); break;
        case 6: _t->onProcessErrorOccurred((*reinterpret_cast< QProcess::ProcessError(*)>(_a[1]))); break;
        case 7: _t->onProcessOutput(); break;
        case 8: _t->onProcessError(); break;
        case 9: _t->onProcessFinished((*reinterpret_cast< int(*)>(_a[1])),(*reinterpret_cast< QProcess::ExitStatus(*)>(_a[2]))); break;
        case 10: _t->updateStats(); break;
        case 11: _t->onResultDoubleClicked((*reinterpret_cast< QTreeWidgetItem*(*)>(_a[1])),(*reinterpret_cast< int(*)>(_a[2]))); break;
        case 12: _t->buildDossierSummary(); break;
        case 13: _t->onResultsContextMenu((*reinterpret_cast< const QPoint(*)>(_a[1]))); break;
        case 14: _t->filterResults((*reinterpret_cast< const QString(*)>(_a[1]))); break;
        case 15: _t->updateTabBadges(); break;
        case 16: _t->rebuildEntityTree(); break;
        case 17: _t->rebuildGraphTree(); break;
        case 18: _t->exportResults(); break;
        case 19: _t->exportGraph((*reinterpret_cast< const QString(*)>(_a[1]))); break;
        case 20: _t->rebuildIndex(); break;
        case 21: _t->showStatus(); break;
        case 22: _t->showPaths(); break;
        case 23: _t->showShortcuts(); break;
        case 24: _t->showAbout(); break;
        case 25: { QString _r = _t->formatSize((*reinterpret_cast< qint64(*)>(_a[1])));
            if (_a[0]) *reinterpret_cast< QString*>(_a[0]) = std::move(_r); }  break;
        default: ;
        }
    }
}

QT_INIT_METAOBJECT const QMetaObject Astrex::staticMetaObject = { {
    QMetaObject::SuperData::link<QMainWindow::staticMetaObject>(),
    qt_meta_stringdata_Astrex.data,
    qt_meta_data_Astrex,
    qt_static_metacall,
    nullptr,
    nullptr
} };


const QMetaObject *Astrex::metaObject() const
{
    return QObject::d_ptr->metaObject ? QObject::d_ptr->dynamicMetaObject() : &staticMetaObject;
}

void *Astrex::qt_metacast(const char *_clname)
{
    if (!_clname) return nullptr;
    if (!strcmp(_clname, qt_meta_stringdata_Astrex.stringdata0))
        return static_cast<void*>(this);
    return QMainWindow::qt_metacast(_clname);
}

int Astrex::qt_metacall(QMetaObject::Call _c, int _id, void **_a)
{
    _id = QMainWindow::qt_metacall(_c, _id, _a);
    if (_id < 0)
        return _id;
    if (_c == QMetaObject::InvokeMetaMethod) {
        if (_id < 26)
            qt_static_metacall(this, _c, _id, _a);
        _id -= 26;
    } else if (_c == QMetaObject::RegisterMethodArgumentMetaType) {
        if (_id < 26)
            *reinterpret_cast<int*>(_a[0]) = -1;
        _id -= 26;
    }
    return _id;
}
QT_WARNING_POP
QT_END_MOC_NAMESPACE
