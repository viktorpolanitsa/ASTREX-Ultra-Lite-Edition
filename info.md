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

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

</div>

---

## 🔍 Overview

ASTREX is a comprehensive forensic analysis system designed for processing large volumes of unstructured data. It extracts, indexes, and correlates information from 60+ file formats with advanced NLP capabilities optimized for Russian text analysis.

### Key Features

- **📁 Multi-Format Support** — PDF, Office (DOCX/XLSX/PPTX), Email (EML/MSG/PST), Archives (ZIP/RAR/7z), Images (OCR), Databases (SQLite/Access)
- **⚡ SQLite Indexing** — Incremental updates, FTS5 full-text search, extracted text caching
- **🧠 Advanced NLP** — Russian morphology (pymorphy2), fuzzy matching (rapidfuzz), entity extraction (spaCy + regex)
- **📊 Semantic Search** — SBERT embeddings, ChromaDB vector store, relevance scoring
- **🔗 Graph Analysis** — Entity relationship mapping, community detection, path finding
- **🤖 LLM Integration** — Local Ollama support for summarization and analysis
- **🖥️ Multiple Interfaces** — CLI, REST API (FastAPI), Qt6 GUI

---

## 🚀 Quick Start

### Installation

```bash
# Clone
git clone https://github.com/yourusername/astrex.git
cd astrex

# Run installer (auto-detects hardware)
chmod +x install.sh
./install.sh

# Activate environment
source venv/bin/activate
```

### Basic Usage

```bash
# CLI Search
python astrex.py scan "Иванов" /path/to/documents

# Quick index search (no file scanning)
python astrex.py search "договор"

# System status
python astrex.py status

# Build index
python astrex.py index /path/to/archive
```

### Web API

```bash
# Start server
python -m web.api

# API is available at http://localhost:8000
# Docs: http://localhost:8000/docs
```

### GUI

```bash
cd ui
qmake6 && make
./Astrex
```

---

## 📖 Architecture

```
astrex_v3/
├── core/                 # Core functionality
│   ├── config.py        # Centralized configuration
│   ├── index.py         # SQLite file index + FTS5
│   ├── nlp.py           # NLP engine (morphology, NER, relevance)
│   ├── graph.py         # Entity relationship graph
│   └── engine.py        # Main scan engine
├── extractors/          # File format extractors
│   ├── documents.py     # PDF, Office, RTF
│   ├── email.py         # EML, MSG, PST, MBOX
│   ├── archives.py      # ZIP, RAR, 7z, TAR
│   ├── images.py        # OCR (Tesseract)
│   ├── databases.py     # SQLite, Access
│   └── text.py          # TXT, JSON, XML, HTML, CSV
├── ml/                   # Machine learning
│   ├── vectors.py       # ChromaDB vector store
│   └── llm.py           # Ollama LLM integration
├── web/                  # Web interface
│   └── api.py           # FastAPI REST API
├── ui/                   # Desktop GUI
│   ├── main.cpp         # Qt6 application
│   └── Astrex.pro       # Qt project file
├── astrex.py            # CLI entry point
├── install.sh           # Installation script
└── requirements.txt     # Python dependencies
```

---

## 📄 Supported Formats

### Documents (Tier 1)
| Format | Library | Features |
|--------|---------|----------|
| PDF | pypdf | Text extraction, metadata |
| DOCX | zipfile (OOXML) | Document, headers, comments |
| XLSX | zipfile (OOXML) | Shared strings, worksheets |
| PPTX | zipfile (OOXML) | Slides, notes |
| DOC/XLS/PPT | olefile | Legacy Office |
| ODT/ODS/ODP | zipfile (ODF) | OpenDocument |
| RTF | striprtf | Rich Text Format |

### Email (Tier 2)
| Format | Library | Features |
|--------|---------|----------|
| EML | email | Multipart, attachments |
| MSG | extract-msg | Outlook messages |
| PST/OST | pypff | Outlook archives |
| MBOX | mailbox | Unix mailbox |

### Archives (Tier 3)
| Format | Library | Recursive |
|--------|---------|-----------|
| ZIP | zipfile | ✓ |
| RAR | rarfile | ✓ |
| 7z | py7zr | ✓ |
| TAR.* | tarfile | ✓ |
| GZ/BZ2/XZ | gzip/bz2/lzma | ✓ |

### Images (Tier 4)
| Format | Library | Features |
|--------|---------|----------|
| PNG/JPG/TIFF/BMP | Tesseract | OCR (rus+eng) |
| Screenshots | Tesseract | Optimized OCR |

### Databases (Tier 5)
| Format | Library | Features |
|--------|---------|----------|
| SQLite | sqlite3 | Text columns |
| MDB/ACCDB | pyodbc/mdbtools | Access databases |

### Text (Fallback)
| Format | Library | Features |
|--------|---------|----------|
| TXT/MD | chardet | Encoding detection |
| JSON | json | Recursive extraction |
| XML | ElementTree | Tag stripping |
| HTML | BeautifulSoup | Script/style removal |
| CSV/TSV | csv | Delimiter detection |

---

## 🧠 NLP Features

### Morphological Analysis (Russian)
```python
from core.nlp import expand_query

# Returns all word forms: "договор", "договора", "договору", "договором"...
forms = expand_query("договор")
```

### Fuzzy Search
```python
from core.nlp import fuzzy_search

# Find similar strings with threshold
matches = fuzzy_search("Газпром", candidates, threshold=80)
```

### Entity Extraction (NER)
```python
from core.nlp import extract_entities

entities = extract_entities(text)
# {
#   "PERSON": ["Иванов Иван Иванович"],
#   "ORG": ["ООО Газпром"],
#   "LOCATION": ["Москва"],
#   "DATE": ["15.03.2024"],
#   "MONEY": ["1 500 000 рублей"]
# }
```

### Semantic Relevance
```python
from core.nlp import calculate_relevance

score = calculate_relevance(snippet, query)  # 0.0 - 1.0
```

---

## 📊 Graph Analysis

ASTREX builds entity relationship graphs from documents.

### Node Types
- **PERSON** — People mentioned in documents
- **ORG** — Organizations and companies
- **LOCATION** — Geographic locations
- **DATE** — Dates and time periods
- **MONEY** — Financial amounts

### Relation Types
- `WORKS_FOR` — Person → Organization
- `LOCATED_IN` — Entity → Location
- `TRANSACTION` — Money → Entity
- `MEETING` — Person ↔ Person
- `AGREEMENT` — Org ↔ Org

### Export Formats
```python
from core.graph import export_to_graphml, export_to_gexf

export_to_graphml(graph, "network.graphml")  # For Gephi
export_to_gexf(graph, "network.gexf")        # For Cytoscape
```

---

## 🔧 Configuration

All settings in `core/config.py`:

```python
# Engine
ENGINE_CONFIG.max_workers_multiplier = 2  # CPU count × multiplier
ENGINE_CONFIG.max_file_size = 500 * 1024 * 1024  # 500 MB
ENGINE_CONFIG.context_window = 600  # Characters around match
ENGINE_CONFIG.fuzzy_threshold = 80  # Fuzzy match threshold (0-100)

# NLP
NLP_CONFIG.max_text_length = 50000  # Max text for NLP processing
NLP_CONFIG.spacy_model = "ru_core_news_sm"

# Vector Store
VECTOR_CONFIG.embedding_model = "paraphrase-multilingual-MiniLM-L12-v2"
VECTOR_CONFIG.chunk_size = 512
VECTOR_CONFIG.top_k = 20

# LLM
LLM_CONFIG.ollama_host = "http://localhost:11434"
LLM_CONFIG.default_model = "llama3.2"
```

---

## 🌐 REST API

### Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/scan` | Start scan task |
| GET | `/api/scan/{id}` | Get task status |
| GET | `/api/scan/{id}/results` | Get results |
| POST | `/api/search` | Search index |
| GET | `/api/search/entity/{entity}` | Search by entity |
| POST | `/api/nlp/entities` | Extract entities |
| GET | `/api/graph` | Get entity graph |
| GET | `/api/index/stats` | Index statistics |
| GET | `/api/status` | System status |
| GET | `/api/health` | Health check |
| WS | `/ws` | Real-time updates |

### Example

```bash
# Start scan
curl -X POST http://localhost:8000/api/scan \
  -H "Content-Type: application/json" \
  -d '{"query": "Иванов", "folder": "/data/documents"}'

# Response
{
  "task_id": "scan_20250128_123456_789",
  "status": "running",
  "message": "Scan started"
}
```

---

## 💻 Python API

```python
from core import ScanEngine, SearchOptions

# Create engine
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

- **File browser** with recursive scanning
- **Real-time progress** with speed metrics
- **Results tree** with score sorting
- **Entity panel** with type filtering
- **Dossier view** with highlighted matches
- **Graph visualization** (planned)
- **Export** to JSON/CSV/GRAPHML

---

## ⚡ Performance

### Benchmarks (100,000 files)

| Mode | Time | Speed |
|------|------|-------|
| Raw scan (no cache) | ~5 min | ~350 files/sec |
| Indexed scan (cached) | ~30 sec | ~3,500 files/sec |
| Index search only | <1 sec | instant |

### Optimization Tips

1. **Build index first** — Run `astrex.py index /folder` before searching
2. **Use fuzzy threshold** — Lower threshold = more matches but slower
3. **Limit NLP scope** — Disable NER for simple keyword search
4. **Adjust workers** — Match to CPU cores for I/O-bound workloads

---

## 🔐 Security Notes

- All processing is **local** — no data leaves your machine
- Index stored in `~/.astrex/` by default
- **No telemetry** or external API calls
- LLM integration uses local Ollama only

---

## 📋 Requirements

### Minimum
- Python 3.11 or 3.12 (3.14+ has limited spaCy support)
- 4 GB RAM
- 10 GB disk space for index

### Recommended
- Python 3.12
- 16 GB RAM
- SSD for index storage
- GPU for faster embeddings (NVIDIA/Intel/AMD)

### Optional System Dependencies
- **Tesseract** — for OCR
- **libpst** — for PST files
- **Qt6** — for GUI

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
