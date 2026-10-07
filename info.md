# ASTREX v3.0 — Intelligence System

<div align="center">

```
    █████╗ ███████╗████████╗██████╗ ███████╗██╗  ██╗
   ██╔══██╗██╔════╝╚══██╔══╝██╔══██╗██╔════╝╚██╗██╔╝
   ███████║███████╗   ██║   ██████╔╝█████╗   ╚███╔╝ 
   ██╔══██║╚════██║   ██║   ██╔══██╗██╔══╝   ██╔██╗ 
   ██║  ██║███████║   ██║   ██║  ██║███████╗██╔╝ ██╗
   ╚═╝  ╚═╝╚══════╝   ╚═╝   ╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝
```

**Forensic Analysis System for Deep Data Investigation**

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

</div>

---

## 🔍 Overview

ASTREX is a comprehensive forensic analysis system designed for processing large volumes of unstructured data. It extracts, indexes, and correlates information from 60+ file formats with advanced NLP capabilities optimized for Russian text analysis.

### Key Features

- **📁 Multi-Format Support** — PDF, Office (DOCX/XLSX/PPTX, DOC/XLS/PPT, ODF), e-books (EPUB/FB2/MOBI), Email (EML/MSG/MBOX/PST), Archives (ZIP/RAR/7z/TAR/GZ/BZ2/XZ/ZST/LZ4, nested), Images (OCR), Databases (SQLite/Access/SQL dumps), audio/video, network captures
- **⚡ SQLite Indexing** — Incremental updates, FTS5 full-text search (BM25 ranking), extracted text and entity caching
- **🧠 Advanced NLP** — Russian morphology (pymorphy3, built-in Snowball stemmer as fallback), fuzzy matching (rapidfuzz), entity extraction (regex + optional spaCy)
- **📊 Semantic Search** — SBERT relevance scoring, ChromaDB vector store (RAG)
- **🔗 Graph Analysis** — Entity relationship mapping, centrality, communities, path finding
- **🧪 Forensics** — encryption / crypto artifacts detection, duplicates (exact + near), timeline, classification
- **🤖 LLM Integration** — Local Ollama support for summarization and analysis
- **🖥️ Multiple Interfaces** — CLI, REST API (FastAPI, token auth), Qt6 GUI

---

## 🚀 Quick Start

### Installation

```bash
# Clone
git clone https://github.com/viktorpolanitsa/ASTREX-Ultra-Lite-Edition.git
cd ASTREX-Ultra-Lite-Edition

# Run installer (auto-detects hardware; --yes --mode full for unattended install)
bash install.sh

# Activate environment
source venv/bin/activate
```

### Basic Usage

```bash
# Full scan: extraction + morphology + fuzzy + entities
python astrex.py scan "Иванов" /path/to/documents
python astrex.py scan "договор поставки" ./files --format json --limit 50
python astrex.py scan -- "-5%" ./files            # query starting with '-'

# Build / update the index (only changed files are processed)
python astrex.py index /path/to/archive --cleanup  # --cleanup: drop deleted files of this folder

# Quick index search (no file scanning; all words must match, --any for OR)
python astrex.py search "договор"

# System status
python astrex.py status --json
```

Other commands: `export` (graph to GraphML/GEXF/JSON), `graph`, `report` (HTML/CSV/XLSX/PDF),
`dedup`, `timeline`, `classify`, `crypto`, `ingest` / `ask` (RAG), `plugins`, `web`.
See `python astrex.py <command> --help`.

### Web API

```bash
# Start server (127.0.0.1:8080). The API token is printed at startup:
# ~/.astrex/.secret_key or the ASTREX_API_TOKEN environment variable
python astrex.py web

# Interactive docs: http://127.0.0.1:8080/docs
```

### GUI

```bash
./ui/Astrex          # finds ../astrex.py and venv/ automatically
./ui/build.sh        # rebuild for your Qt 6 (needs build-essential qt6-base-dev)
```

---

## 📖 Architecture

```
ASTREX/
├── core/                    # Core functionality
│   ├── config.py           # Configuration (+ ~/.astrex/config.yaml overrides)
│   ├── engine.py           # Scan engine, incremental indexer
│   ├── workerpool.py       # Process pool: per-file timeout, RSS limit, crash isolation
│   ├── index.py            # SQLite file index + FTS5
│   ├── nlp.py              # Morphology, query matching, NER, relevance
│   ├── encoding.py         # Encoding detection (UTF-8/16, cp1251, KOI8-R, cp866…)
│   ├── graph.py            # Entity relationship graph, GraphML/GEXF export
│   ├── timeline.py         # Event timeline
│   ├── classifier.py       # Document classification
│   ├── crypto.py           # Encryption / crypto artifacts detection
│   ├── fingerprint.py      # Duplicates (SHA-256, MinHash/SimHash)
│   ├── plugins.py          # Extractor plugins (~/.astrex/plugins)
│   ├── gpu.py, resource_guard.py, logging_setup.py
├── extractors/             # File format extractors
│   ├── documents.py        # PDF, Office, ODF, RTF, EPUB, FB2, MOBI
│   ├── email.py            # EML, MSG, MBOX, PST
│   ├── archives.py         # ZIP, RAR, 7z, TAR, GZ/BZ2/XZ/ZST/LZ4
│   ├── images.py           # OCR (Tesseract), EXIF/GPS
│   ├── databases.py        # SQLite, Access, SQL dumps, MySQL data files
│   ├── media.py            # Audio/video metadata, Whisper transcription
│   ├── network.py          # PCAP/PCAPNG, NetFlow
│   └── text.py             # TXT, JSON, XML, HTML, CSV
├── ml/                     # Machine learning
│   ├── vectors.py          # ChromaDB / built-in vector store
│   ├── rag.py              # Retrieval-augmented answers
│   └── llm.py              # Ollama LLM integration
├── reporting/reports.py    # HTML / CSV / XLSX / PDF reports
├── web/api.py              # FastAPI REST API
├── ui/                     # Desktop GUI (Qt6): main.cpp, Astrex.pro, build.sh
├── tests/                  # unittest suite
├── astrex.py               # CLI entry point
├── selftest.py             # Installation self-test
├── install.sh              # Installation script
└── requirements.txt        # Python dependencies
```

---

## 📄 Supported Formats

### Documents
| Format | Library | Features |
|--------|---------|----------|
| PDF | pypdf | Text, metadata, empty-password decryption; no text layer → flagged |
| DOCX/DOCM/DOTX | zipfile (OOXML) | Body, headers/footers, footnotes, comments, properties |
| XLSX/XLSM | zipfile (OOXML) | All sheets, shared strings, numbers |
| PPTX/PPSX/PPTM | zipfile (OOXML) | Slides in order, notes |
| DOC/XLS/PPT | olefile, xlrd | Word 97 piece table, BIFF8 (built-in if xlrd is missing), PPT text atoms |
| ODT/ODS/ODP/ODG | zipfile (ODF) | Text, tables, headers/footers |
| RTF | striprtf / built-in | Code pages (\ansicpg), Unicode escapes |
| EPUB / FB2 / MOBI | zipfile / XML / PalmDOC | Reading order, encodings |

### Email
| Format | Library | Features |
|--------|---------|----------|
| EML | email | Multipart, encoded headers, attachments (extracted recursively) |
| MSG | extract-msg | Outlook messages, embedded messages |
| PST/OST | pypff or readpst | Outlook archives |
| MBOX | mailbox | All messages, broken charsets tolerated |

### Archives (recursive, path-traversal entries flagged)
| Format | Library |
|--------|---------|
| ZIP/JAR/APK/CBZ | zipfile (cp866 names supported) |
| RAR/CBR | rarfile (needs unrar / unar / bsdtar) |
| 7z | py7zr |
| TAR/TGZ/TBZ2/TXZ | tarfile (streaming) |
| GZ/BZ2/XZ/ZST/LZ4 | gzip/bz2/lzma/zstandard/lz4 |

### Images
| Format | Library | Features |
|--------|---------|----------|
| PNG/JPG/TIFF/BMP… | Tesseract | OCR (rus+eng), EXIF, GPS |
| Screenshots | Tesseract | Optimized OCR |

### Databases
| Format | Library | Features |
|--------|---------|----------|
| SQLite | sqlite3 | All tables and rows, read-only (incl. WAL) |
| MDB/ACCDB | pyodbc / mdbtools | Access databases |
| SQL dumps | built-in, pg_restore | INSERT (multi-line), COPY, PostgreSQL custom format |
| MySQL MYD | built-in | Text runs (UTF-8 / cp1251) |

### Text
| Format | Library | Features |
|--------|---------|----------|
| TXT/MD/LOG/code | built-in | Encoding detection: UTF-8/16, cp1251, KOI8-R, cp866, ISO-8859-5 |
| JSON/JSONL | json | All keys and values |
| XML | ElementTree | Document order, declared encoding |
| HTML | BeautifulSoup | Script/style removal, title |
| CSV/TSV | csv | Delimiter detection (`,` `;` tab `|`), all rows |

### Other
| Format | Library | Features |
|--------|---------|----------|
| MP3/FLAC/OGG/WAV…, MP4/MKV/AVI… | mutagen, ffprobe, Whisper | Metadata, transcription |
| PCAP/PCAPNG | scapy | Hosts, payload strings |
| NetFlow (nfcapd) | nfdump | Flows |

---

## 🧠 NLP Features

### Query semantics
All significant words of the query must occur in the document (stop words are ignored),
as whole words, in any grammatical form (`договор` → `договора`, `договору`…), with `е`/`ё`
treated as equal. Phrases score higher than scattered words.

### Morphological Analysis (Russian)
```python
from core.nlp import expand_query

# Returns word forms: "договор", "договора", "договору", "договором"...
forms = expand_query("договор")
```

### Fuzzy Search
```python
from core.nlp import fuzzy_search

# Find similar strings (rapidfuzz; threshold 0-100)
matches = fuzzy_search("Газпром", candidates, threshold=80)
```

### Entity Extraction (NER)
```python
from core.nlp import extract_entities

entities = extract_entities(text)
# {
#   "PERSONS": ["Иванов Иван Иванович", "Петров И.И."],
#   "ORGANIZATIONS": ["ООО Газпром"],
#   "LOCATIONS": ["г. Москва"],
#   "DATES": ["15.03.2024"],
#   "MONEY": ["1 500 000 рублей"],
#   "CONTACTS": ["+7 (999) 123-45-67", "info@company.ru"],
#   "DOCUMENTS": ["договор № 17/2023"],
#   "TECHNICAL": ["192.168.0.1"]
# }
```

### Relevance
```python
from core.nlp import calculate_relevance

score = calculate_relevance(snippet, query)  # 0.0 - 1.0 (keywords + SBERT when installed)
```

---

## 📊 Graph Analysis

ASTREX builds entity relationship graphs from documents.

### Node Types
`PERSONS`, `ORGANIZATIONS`, `LOCATIONS`, `DATES`, `MONEY`, `CONTACTS`, `DOCUMENTS`, `TECHNICAL`

### Relation Types
- `proximity` — entities mentioned close to each other
- `works_for` — Person → Organization
- `located_in` — Organization/Person → Location
- `transaction` — Money ↔ Person/Organization
- `meeting` — Person ↔ Person
- `agreement` — Organization/Person ↔ Organization/Person

Repeated observations of a relation increase the edge weight (no duplicate edges).

### Export Formats
```bash
python astrex.py export network.graphml   # Gephi, yEd, Cytoscape
python astrex.py export network.gexf      # Gephi
python astrex.py export network.json
```
```python
from core.graph import export_to_graphml, export_to_gexf

export_to_graphml(graph, "network.graphml")
export_to_gexf(graph, "network.gexf")
```

---

## 🔧 Configuration

Defaults are in `core/config.py`; override them in `~/.astrex/config.yaml`
(unknown keys and invalid values are reported at startup):

```yaml
engine:
  max_file_size: 2147483648     # 2 GB
  file_timeout: 600             # seconds per file (0 = no limit)
  max_ram_per_worker_mb: 2048   # worker restarted above this RSS
  context_window: 600           # characters around a match in snippets
  fuzzy_threshold: 80           # 0-100
  ocr_languages: rus+eng
  skip_hidden_dirs: true
nlp:
  max_text_length: 50000        # characters analysed for entities
  sbert_model: paraphrase-multilingual-MiniLM-L12-v2
vector:
  chunk_size: 80                # words per chunk
  top_k: 20
llm:
  ollama_host: http://localhost:11434
  default_model: llama3.2
web:
  host: 127.0.0.1
  port: 8080
  enable_auth: true
```

Environment: `ASTREX_HOME` (data directory, default `~/.astrex`), `ASTREX_LOG_LEVEL`,
`ASTREX_API_TOKEN`; GUI: `ASTREX_BACKEND`, `ASTREX_PYTHON`.

---

## 🌐 REST API

All endpoints except `/api/health` require the token:
`Authorization: Bearer <token>` (or `X-API-Key: <token>`, or `?token=` for WebSocket).
Folders inside blocked paths (`~/.ssh`, `~/.gnupg`, the ASTREX data directory…) are refused.

### Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/scan` | Start scan task (max 2 concurrent) |
| GET | `/api/scan/{id}` | Get task status |
| DELETE | `/api/scan/{id}` | Cancel task |
| GET | `/api/scan/{id}/results` | Get results (paginated) |
| POST | `/api/search` | Search index |
| GET | `/api/search/entity/{entity}` | Search by entity |
| POST | `/api/nlp/entities` | Extract entities |
| POST | `/api/nlp/relevance` | Relevance score |
| GET | `/api/graph` | Get entity graph |
| GET | `/api/graph/entity/{entity}` | Connections of an entity |
| GET | `/api/index/stats` | Index statistics |
| POST | `/api/index/vacuum` | Optimize index |
| POST | `/api/llm/summarize`, `/api/llm/analyze` | Local LLM |
| GET | `/api/status` | System status |
| GET | `/api/health` | Health check (no token) |
| WS | `/ws` | Real-time updates |

### Example

```bash
TOKEN=$(cat ~/.astrex/.secret_key)

# Start scan
curl -X POST http://127.0.0.1:8080/api/scan \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query": "Иванов", "folder": "/data/documents"}'

# Response
{
  "task_id": "scan_20250128_123456_789",
  "status": "running",
  "message": "Scan started for query: Иванов"
}
```

---

## 💻 Python API

```python
from core import ScanEngine, SearchOptions

# Create engine (events go to the log; pass callback=... to receive them)
engine = ScanEngine()

# Configure options
options = SearchOptions(
    use_index=True,
    use_fuzzy=True,
    use_morphology=True,
    extract_entities=True,
    build_graph=True,
    min_score=0.1,
    max_results=500
)

# Run search
for match in engine.search("договор", "/data/documents", options):
    print(f"{match.filename}: {match.score:.2f}")
    print(f"  Entities: {match.entities}")
    print(f"  Snippet: {match.snippet[:200]}...")

# Get graph
graph = engine.get_graph()
```

---

## 🖥️ GUI Features

- **Scan** with live matches and the final ranked list (Min Score / Limit)
- **Real-time progress** with speed and ETA; STOP stops all worker processes
- **Results tree** with score / size sorting, type filter, context menu
- **Entity panel** grouped by type; **GRAPH** tab with connections
- **Dossier view** with highlighted matches and per-file entities
- **Timeline**, **duplicates** (DEDUP), **indexing** with progress
- **Export** results to JSON/CSV/GraphML, entity graph of the index to GraphML/GEXF/JSON

---

## ⚡ Performance

Measured on 12 CPU cores, 20,000 small text files (4 KB), default settings:

| Mode | Time | Speed |
|------|------|-------|
| First scan (extraction + indexing) | ~20 s | ~1,000 files/sec |
| First scan, entity extraction for every file | ~28 s | ~700 files/sec |
| Repeated scan (index cache) | ~7 s | ~3,000 files/sec |
| Index search only | <1 sec | instant |

Real throughput depends on formats: OCR, PDF, archives and audio transcription are much slower
than plain text.

### Optimization Tips

1. **Build index first** — Run `astrex.py index /folder` before searching
2. **Fuzzy search** — disable it (`--no-fuzzy`) for exact keyword search
3. **Limit NLP scope** — `--no-nlp` skips entity extraction and semantic scoring
4. **Adjust workers** — `--workers N`; each worker is a process and needs RAM

---

## 🔐 Security Notes

- Documents are processed **locally**; nothing is uploaded
- Network access happens only to download models on first use (sentence-transformers / Hugging Face,
  spaCy, Whisper) and for the local Ollama server
- Index (with extracted text) is stored in `~/.astrex/` — protect it like the source data
- Web API listens on 127.0.0.1 with token authentication; it refuses non-loopback addresses
  when authentication is disabled
- Plugins in `~/.astrex/plugins` are executed as Python code — install only trusted ones

---

## 📋 Requirements

### Minimum
- Linux, Python 3.10+ (tested on 3.13)
- 4 GB RAM
- Disk space for the index (roughly the size of the extracted text)

### Recommended
- 16 GB RAM
- SSD for index storage
- GPU for faster embeddings (NVIDIA/Intel/AMD)

### Optional System Dependencies
- **Tesseract** (+ `tesseract-ocr-rus`) — OCR
- **unrar / unar** — RAR archives
- **pst-utils** (readpst) — PST files
- **mdbtools** — Access databases
- **ffmpeg** — audio/video
- **postgresql-client** (pg_restore) — PostgreSQL dumps
- **nfdump** — NetFlow
- **Qt 6** — GUI

---

## 🧪 Tests

```bash
python -m unittest discover -s tests -t .   # unit and integration tests
python selftest.py                          # installation self-test
```

---

## 📜 License

MIT License — see [LICENSE](LICENSE)

---

## 🤝 Contributing

1. Fork the repository
2. Create feature branch (`git checkout -b feature/amazing`)
3. Commit changes (`git commit -m 'Add amazing feature'`)
4. Push to branch (`git push origin feature/amazing`)
5. Open Pull Request

---

<div align="center">

**ASTREX v3.0** — *Deep Data Analysis for Intelligence Operations*

</div>
