#!/usr/bin/env python3
"""
ASTREX v3.0 — Self-Test Module
Автоматическая проверка всех компонентов системы
"""

import sys
import os
import json
import tempfile
import shutil
from pathlib import Path
from datetime import datetime


# Colors
class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    BOLD = '\033[1m'
    END = '\033[0m'


def ok(msg: str):
    print(f"  {Colors.GREEN}✓{Colors.END} {msg}")

def fail(msg: str):
    print(f"  {Colors.RED}✗{Colors.END} {msg}")

def warn(msg: str):
    print(f"  {Colors.YELLOW}○{Colors.END} {msg}")

def section(title: str):
    print(f"\n{Colors.CYAN}{Colors.BOLD}{'═'*60}{Colors.END}")
    print(f"{Colors.CYAN}{Colors.BOLD}{title}{Colors.END}")
    print(f"{Colors.CYAN}{Colors.BOLD}{'═'*60}{Colors.END}")


def run_tests():
    """Run all tests"""
    
    print(f"""
{Colors.CYAN}
    █████╗ ███████╗████████╗██████╗ ███████╗██╗  ██╗
   ██╔══██╗██╔════╝╚══██╔══╝██╔══██╗██╔════╝╚██╗██╔╝
   ███████║███████╗   ██║   ██████╔╝█████╗   ╚███╔╝ 
   ██╔══██║╚════██║   ██║   ██╔══██╗██╔══╝   ██╔██╗ 
   ██║  ██║███████║   ██║   ██║  ██║███████╗██╔╝ ██╗
   ╚═╝  ╚═╝╚══════╝   ╚═╝   ╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝
{Colors.END}
                  SELF-TEST v3.0
    """)
    
    results = {
        "timestamp": datetime.now().isoformat(),
        "python_version": sys.version,
        "tests": {}
    }
    
    # ═══════════════════════════════════════════════════════════════════════════
    # CORE TESTS
    # ═══════════════════════════════════════════════════════════════════════════
    
    section("CORE MODULES")
    
    # Config
    try:
        from core.config import ASTREX_HOME, FILE_TYPES, ENGINE_CONFIG
        ok(f"config — Home: {ASTREX_HOME}")
        ok(f"config — File types: {len(FILE_TYPES.all_supported)} supported")
        results["tests"]["config"] = True
    except Exception as e:
        fail(f"config — {e}")
        results["tests"]["config"] = False
    
    # Index
    try:
        from core.index import file_index
        stats = file_index.get_stats()
        ok(f"index — {stats['total_files']} files, {stats['total_size_human']}")
        results["tests"]["index"] = True
    except Exception as e:
        fail(f"index — {e}")
        results["tests"]["index"] = False
    
    # NLP - Morphology
    try:
        from core.nlp import morph_analyzer
        if morph_analyzer.available:
            forms = morph_analyzer.get_all_forms("договор")
            ok(f"morphology — pymorphy2 ({len(forms)} forms)")
        else:
            warn("morphology — pymorphy2 not available")
        results["tests"]["morphology"] = morph_analyzer.available
    except Exception as e:
        fail(f"morphology — {e}")
        results["tests"]["morphology"] = False
    
    # NLP - Fuzzy
    try:
        from core.nlp import FuzzyMatcher
        if FuzzyMatcher.is_available():
            score = FuzzyMatcher.ratio("Газпром", "Газпрм")
            ok(f"fuzzy — rapidfuzz (score: {score})")
        else:
            warn("fuzzy — rapidfuzz not available")
        results["tests"]["fuzzy"] = FuzzyMatcher.is_available()
    except Exception as e:
        fail(f"fuzzy — {e}")
        results["tests"]["fuzzy"] = False
    
    # NLP - spaCy
    try:
        from core.nlp import entity_extractor
        if entity_extractor.nlp:
            ok(f"spacy — {entity_extractor.nlp.meta['name']}")
        else:
            warn("spacy — not available (regex fallback)")
        results["tests"]["spacy"] = entity_extractor.nlp is not None
    except Exception as e:
        fail(f"spacy — {e}")
        results["tests"]["spacy"] = False
    
    # NLP - SBERT
    try:
        from core.nlp import relevance_calculator
        if relevance_calculator.sbert_model:
            ok("sbert — sentence-transformers loaded")
        else:
            warn("sbert — not available (keyword fallback)")
        results["tests"]["sbert"] = relevance_calculator.sbert_model is not None
    except Exception as e:
        fail(f"sbert — {e}")
        results["tests"]["sbert"] = False
    
    # NLP - Entity Extraction Test
    try:
        from core.nlp import extract_entities
        test_text = """
        Иванов Иван Иванович работает в ООО "Газпром" в городе Москва.
        Сумма: 1 500 000 рублей. Дата: 15.03.2024.
        """
        entities = extract_entities(test_text)
        entity_count = sum(len(v) for v in entities.values())
        ok(f"ner — Extracted {entity_count} entities")
        results["tests"]["ner"] = True
    except Exception as e:
        fail(f"ner — {e}")
        results["tests"]["ner"] = False
    
    # Graph
    try:
        from core.graph import GraphBuilder, GraphAnalyzer
        builder = GraphBuilder()
        builder.build_from_entities(
            {"PERSON": ["Иванов", "Петров"], "ORG": ["Газпром"]},
            "Иванов и Петров работают в Газпром",
            "/test/file.txt"
        )
        graph = builder.get_graph()
        ok(f"graph — {len(graph.nodes)} nodes, {len(graph.edges)} edges")
        results["tests"]["graph"] = True
    except Exception as e:
        fail(f"graph — {e}")
        results["tests"]["graph"] = False
    
    # ═══════════════════════════════════════════════════════════════════════════
    # EXTRACTORS
    # ═══════════════════════════════════════════════════════════════════════════
    
    section("EXTRACTORS")
    
    try:
        from extractors import registry
        extractors = registry.list_extractors()
        
        available = 0
        for ext in extractors:
            if ext['available']:
                available += 1
                ok(f"{ext['name']} — {', '.join(ext['extensions'][:5])}")
            else:
                warn(f"{ext['name']} — dependencies missing")
        
        print(f"\n  {Colors.BOLD}Total: {available}/{len(extractors)} available{Colors.END}")
        results["tests"]["extractors"] = available
    except Exception as e:
        fail(f"extractors — {e}")
        results["tests"]["extractors"] = 0
    
    # ═══════════════════════════════════════════════════════════════════════════
    # ML MODULES
    # ═══════════════════════════════════════════════════════════════════════════
    
    section("ML MODULES")
    
    # Vector Store
    try:
        from ml import vector_store
        vs_stats = vector_store.get_stats()
        if vs_stats.get('available'):
            ok(f"vectors — ChromaDB ({vs_stats.get('document_count', 0)} docs)")
        else:
            warn("vectors — ChromaDB not available")
        results["tests"]["vectors"] = vs_stats.get('available', False)
    except Exception as e:
        fail(f"vectors — {e}")
        results["tests"]["vectors"] = False
    
    # LLM
    try:
        from ml import get_llm_status
        llm = get_llm_status()
        if llm.get('available'):
            models = llm.get('models', [])
            ok(f"llm — Ollama ({len(models)} models)")
        else:
            warn("llm — Ollama not available")
        results["tests"]["llm"] = llm.get('available', False)
    except Exception as e:
        fail(f"llm — {e}")
        results["tests"]["llm"] = False
    
    # ═══════════════════════════════════════════════════════════════════════════
    # GPU
    # ═══════════════════════════════════════════════════════════════════════════
    
    section("GPU ACCELERATION")
    
    try:
        import torch
        
        if torch.cuda.is_available():
            gpu_name = torch.cuda.get_device_name(0)
            ok(f"CUDA — {gpu_name}")
            results["tests"]["gpu"] = "cuda"
        elif hasattr(torch, 'xpu') and torch.xpu.is_available():
            ok("Intel XPU — available")
            results["tests"]["gpu"] = "intel"
        elif hasattr(torch.backends, 'mps') and torch.backends.mps.is_available():
            ok("Apple MPS — available")
            results["tests"]["gpu"] = "mps"
        else:
            warn("GPU — CPU only mode")
            results["tests"]["gpu"] = "cpu"
    except ImportError:
        warn("PyTorch — not installed")
        results["tests"]["gpu"] = None
    except Exception as e:
        fail(f"GPU — {e}")
        results["tests"]["gpu"] = None
    
    # ═══════════════════════════════════════════════════════════════════════════
    # WEB API
    # ═══════════════════════════════════════════════════════════════════════════
    
    section("WEB API")
    
    try:
        from web import FASTAPI_AVAILABLE
        if FASTAPI_AVAILABLE:
            from fastapi import __version__ as fastapi_version
            ok(f"fastapi — v{fastapi_version}")
        else:
            warn("fastapi — not installed")
        results["tests"]["web"] = FASTAPI_AVAILABLE
    except Exception as e:
        fail(f"web — {e}")
        results["tests"]["web"] = False
    
    # ═══════════════════════════════════════════════════════════════════════════
    # SYSTEM DEPENDENCIES
    # ═══════════════════════════════════════════════════════════════════════════
    
    section("SYSTEM DEPENDENCIES")
    
    # Tesseract
    try:
        import subprocess
        result = subprocess.run(['tesseract', '--version'], capture_output=True, text=True)
        if result.returncode == 0:
            version = result.stdout.split('\n')[0]
            ok(f"tesseract — {version}")
            results["tests"]["tesseract"] = True
        else:
            warn("tesseract — not found")
            results["tests"]["tesseract"] = False
    except FileNotFoundError:
        warn("tesseract — not installed")
        results["tests"]["tesseract"] = False
    except Exception as e:
        fail(f"tesseract — {e}")
        results["tests"]["tesseract"] = False
    
    # libpst
    try:
        import subprocess
        result = subprocess.run(['readpst', '-V'], capture_output=True, text=True)
        if result.returncode == 0:
            ok("libpst — available")
            results["tests"]["libpst"] = True
        else:
            warn("libpst — not found")
            results["tests"]["libpst"] = False
    except FileNotFoundError:
        warn("libpst — not installed")
        results["tests"]["libpst"] = False
    except Exception as e:
        results["tests"]["libpst"] = False
    
    # ═══════════════════════════════════════════════════════════════════════════
    # FUNCTIONAL TESTS
    # ═══════════════════════════════════════════════════════════════════════════
    
    section("FUNCTIONAL TESTS")
    
    # Test file extraction
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create test file
            test_file = Path(tmpdir) / "test.txt"
            test_file.write_text("Тестовый документ для проверки ASTREX", encoding='utf-8')
            
            from extractors import registry
            result = registry.extract(test_file)
            
            if result.success and "Тестовый" in result.full_text:
                ok("extraction — Text extraction works")
                results["tests"]["extraction"] = True
            else:
                fail("extraction — Text extraction failed")
                results["tests"]["extraction"] = False
    except Exception as e:
        fail(f"extraction — {e}")
        results["tests"]["extraction"] = False
    
    # Test search engine
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create test files
            for i in range(5):
                test_file = Path(tmpdir) / f"doc{i}.txt"
                test_file.write_text(f"Документ номер {i} содержит информацию о Газпроме", encoding='utf-8')
            
            from core.engine import ScanEngine
            
            engine = ScanEngine()
            matches = list(engine.search("Газпром", tmpdir))
            
            if len(matches) == 5:
                ok(f"search — Found {len(matches)} matches")
                results["tests"]["search"] = True
            else:
                warn(f"search — Found {len(matches)}/5 matches")
                results["tests"]["search"] = True
    except Exception as e:
        fail(f"search — {e}")
        results["tests"]["search"] = False
    
    # Test index operations
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            from core.index import FileIndex
            
            test_index = FileIndex(Path(tmpdir) / "test.db")
            
            test_index.upsert_file(
                path="/test/file.txt",
                filename="file.txt",
                extension=".txt",
                size=1000,
                mtime=12345678.0,
                extracted_text="Тестовый текст с ключевым словом",
                entities={"PERSON": ["Тест"]}
            )
            
            results_list = test_index.search_fts("ключевым", 10)
            
            if results_list and len(results_list) > 0:
                ok("indexing — FTS search works")
                results["tests"]["indexing"] = True
            else:
                fail("indexing — FTS search failed")
                results["tests"]["indexing"] = False
    except Exception as e:
        fail(f"indexing — {e}")
        results["tests"]["indexing"] = False
    
    # ═══════════════════════════════════════════════════════════════════════════
    # SUMMARY
    # ═══════════════════════════════════════════════════════════════════════════
    
    section("SUMMARY")
    
    passed = sum(1 for v in results["tests"].values() if v)
    total = len(results["tests"])
    
    print(f"\n  Tests passed: {Colors.GREEN}{passed}{Colors.END}/{total}")
    
    # Determine overall status
    critical_tests = ["config", "index", "extraction", "search"]
    critical_passed = all(results["tests"].get(t) for t in critical_tests)
    
    if critical_passed:
        print(f"\n  {Colors.GREEN}{Colors.BOLD}✓ ASTREX v3.0 is ready!{Colors.END}")
        status = "ready"
    else:
        print(f"\n  {Colors.RED}{Colors.BOLD}✗ Some critical components are missing{Colors.END}")
        status = "incomplete"
    
    results["status"] = status
    results["passed"] = passed
    results["total"] = total
    
    print()
    
    return results


def main():
    """Main entry point"""
    import argparse
    
    parser = argparse.ArgumentParser(description='ASTREX v3.0 Self-Test')
    parser.add_argument('--json', action='store_true', help='Output JSON')
    parser.add_argument('--quiet', '-q', action='store_true', help='Quiet mode')
    
    args = parser.parse_args()
    
    if args.quiet:
        import io
        sys.stdout = io.StringIO()
    
    results = run_tests()
    
    if args.quiet:
        sys.stdout = sys.__stdout__
    
    if args.json:
        print(json.dumps(results, indent=2, ensure_ascii=False))
    
    return 0 if results["status"] == "ready" else 1


if __name__ == "__main__":
    sys.exit(main())
