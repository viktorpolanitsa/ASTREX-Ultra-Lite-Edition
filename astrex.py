#!/usr/bin/env python3
"""
ASTREX v3.0 — Intelligence System
Форензическая система глубокого анализа данных

Usage:
    astrex.py scan <query> <folder> [--format text|json|jsonl] [options]
    astrex.py search <query> [options]
    astrex.py index <folder> [--cleanup] [--vacuum] [options]
    astrex.py status [--json]
    astrex.py export <output.graphml|gexf|json> [--format ...]
    astrex.py graph | report | ingest | ask | classify | crypto | dedup | timeline | plugins | web

Вывод:
    text  — человекочитаемый отчёт в stdout, ход работы — в stderr;
    json  — один JSON-документ в stdout (ход работы — в stderr);
    jsonl — поток событий JSON Lines в stdout (используется GUI).
"""

import argparse
import json
import os
import signal
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterator, List, Optional


def _configure_stdio() -> None:
    """UTF-8 для stdout/stderr (в C/POSIX-локали кириллица превращалась бы в \\uXXXX)."""
    for stream_name in ('stdout', 'stderr'):
        stream = getattr(sys, stream_name, None)
        if stream is None or not hasattr(stream, 'reconfigure'):
            continue
        try:
            if (stream.encoding or '').lower().replace('_', '-') not in ('utf-8', 'utf8'):
                stream.reconfigure(encoding='utf-8', errors='replace')
        except (ValueError, OSError):
            pass


_configure_stdio()

from core.config import VERSION, ENGINE_CONFIG  # noqa: E402
from core.logging_setup import get_logger  # noqa: E402

_logger = get_logger("astrex.cli")


# ═══════════════════════════════════════════════════════════════════════════════
# OUTPUT
# ═══════════════════════════════════════════════════════════════════════════════

class Output:
    """Куда отправлять события: JSON Lines в stdout (GUI) или журнал в stderr."""
    jsonl = False


def _write_json_line(obj: Dict) -> None:
    try:
        sys.stdout.write(json.dumps(obj, ensure_ascii=False, default=str) + '\n')
        sys.stdout.flush()
    except (BrokenPipeError, OSError, ValueError):
        pass


def emit(event_type: str, data: Dict) -> None:
    """Событие сканирования/индексации."""
    if Output.jsonl:
        _write_json_line({"type": event_type, "timestamp": datetime.now().isoformat(), **data})
        return
    if event_type == 'error':
        target = f" [{data['file']}]" if data.get('file') else ''
        _logger.warning(f"{data.get('msg', '')}{target}")
    elif event_type == 'status':
        _logger.info(data.get('msg', ''))
    elif event_type == 'progress':
        total = data.get('total') or 0
        current = data.get('current') or 0
        if total and (current == total or current % 100 == 0):
            _logger.info(f"Progress: {current}/{total}")


def log_info(msg: str) -> None:
    _logger.info(msg)


def log_error(msg: str) -> None:
    _logger.error(msg)


def _print_json(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def _folder_arg(value: str) -> Optional[Path]:
    folder = Path(value).expanduser().resolve()
    if not folder.exists():
        log_error(f"Folder not found: {folder}")
        return None
    if not folder.is_dir():
        log_error(f"Not a directory: {folder}")
        return None
    return folder


def iter_input_files(folder: Path, limit: Optional[int] = None,
                     supported_only: bool = True) -> Iterator[Path]:
    """Файлы папки (рекурсивно, в стабильном порядке, без скрытых и системных каталогов)."""
    from core.engine import default_extensions
    extensions = default_extensions() if supported_only else None
    excludes = [os.path.realpath(p) for p in ENGINE_CONFIG.exclude_paths]
    count = 0
    for root, dirs, files in os.walk(folder):
        dirs[:] = sorted(d for d in dirs
                         if not (ENGINE_CONFIG.skip_hidden_dirs and d.startswith('.'))
                         and os.path.realpath(os.path.join(root, d)) not in excludes)
        for name in sorted(files):
            if extensions is not None and os.path.splitext(name)[1].lower() not in extensions:
                continue
            path = Path(root) / name
            if not path.is_file():
                continue
            yield path
            count += 1
            if limit and count >= limit:
                return


def _autoload_plugins() -> None:
    from core.engine import ensure_plugins_loaded
    ensure_plugins_loaded()


def _highlight_plain(snippet: str) -> str:
    return (snippet or '').replace('<mark>', '[').replace('</mark>', ']')


# ═══════════════════════════════════════════════════════════════════════════════
# COMMANDS
# ═══════════════════════════════════════════════════════════════════════════════

def cmd_scan(args) -> int:
    """Full scan with indexing and NLP"""
    from core import ScanEngine

    if args.timeout is not None:
        ENGINE_CONFIG.file_timeout = max(0, args.timeout)

    Output.jsonl = args.format == 'jsonl'
    log_info(f"ASTREX v{VERSION} — Scan: {args.query!r} in {args.folder}")

    folder = _folder_arg(args.folder)
    if folder is None:
        if Output.jsonl:
            emit("error", {"msg": f"Folder not found: {args.folder}"})
        return 1

    _autoload_plugins()
    engine = ScanEngine(
        callback=emit,
        use_cache=not args.no_index,
        use_nlp=not args.no_nlp,
        fuzzy_search=not args.no_fuzzy,
        use_morphology=not args.no_morph,
    )

    try:
        all_results = engine.scan(folder=str(folder), query=args.query, max_workers=args.workers)
    except KeyboardInterrupt:
        engine.stop()
        log_info("Scan interrupted")
        return 130

    results = [r for r in all_results if r.score >= args.min_score][:args.limit]
    stats = engine.stats

    if args.format == 'jsonl':
        _write_json_line({"type": "results_begin", "count": len(results),
                          "total_matches": len(all_results),
                          "min_score": args.min_score, "limit": args.limit})
        for r in results:
            _write_json_line({"type": "result", **r.to_dict()})
        _write_json_line({"type": "complete", **stats.to_dict(), "returned": len(results)})
    elif args.format == 'json':
        _print_json({
            "query": args.query,
            "folder": str(folder),
            "total_matches": len(all_results),
            "returned": len(results),
            "stats": stats.to_dict(),
            "results": [r.to_dict() for r in results],
        })
    else:
        print(f"\n{'=' * 70}")
        print(f"РЕЗУЛЬТАТЫ ПОИСКА: {args.query}")
        print(f"{'=' * 70}")
        print(f"Найдено: {len(all_results)} совпадений из {stats.total_files} файлов"
              f" (показано: {len(results)}, min-score {args.min_score})")
        print(f"Время: {stats.duration_seconds:.1f}с ({stats.files_per_second:.0f} файлов/сек),"
              f" ошибок: {stats.errors}")
        print(f"{'=' * 70}\n")
        for i, r in enumerate(results, 1):
            print(f"[{i}] {r.filename} (score: {r.score:.2f}, {r.metadata.get('match_type', '')})")
            print(f"    Path: {r.path}")
            for ent_type, ents in (r.entities or {}).items():
                if ents:
                    print(f"    {ent_type}: {', '.join(str(e) for e in ents[:5])}")
            print("    ---")
            snippet = ' '.join((r.snippet or '').split())
            print(f"    {snippet[:400]}{'…' if len(snippet) > 400 else ''}")
            print()
    return 0


def cmd_search(args) -> int:
    """Quick search in index only"""
    from core import file_index

    log_info(f"ASTREX v{VERSION} — Index Search: {args.query!r}")
    results = file_index.search_fts(query=args.query, limit=args.limit, use_or=args.any)
    results = [r for r in results if r.get('score', 0) >= args.min_score]

    if args.format == 'json':
        _print_json(results)
    else:
        print(f"\nНайдено: {len(results)} результатов\n")
        for i, r in enumerate(results, 1):
            print(f"[{i}] {r['filename']} (score: {r['score']:.2f})")
            print(f"    {r['path']}")
            snippet = ' '.join(_highlight_plain(r.get('snippet', '')).split())
            if snippet:
                print(f"    {snippet[:300]}")
            print()
    return 0


def cmd_index(args) -> int:
    """Build or update index (параллельно, только изменённые файлы)"""
    from core import file_index
    from core.engine import IncrementalIndexer

    folder = _folder_arg(args.folder)
    if folder is None:
        return 1
    log_info(f"ASTREX v{VERSION} — Index Builder: {folder}")
    _autoload_plugins()

    # Ход индексации — в stdout (его показывает GUI); с --json stdout занят
    # итоговым JSON-документом, поэтому прогресс уходит в stderr.
    progress_stream = sys.stderr if args.json else sys.stdout

    def progress(event_type: str, data: Dict) -> None:
        if event_type == 'progress':
            print(f"Progress: {data.get('current', 0)}/{data.get('total', 0)} "
                  f"(indexed {data.get('indexed', 0)}, unchanged {data.get('cached', 0)}, "
                  f"no text {data.get('skipped', 0)}, errors {data.get('errors', 0)})",
                  file=progress_stream, flush=True)
        elif event_type == 'status':
            print(data.get('msg', ''), file=progress_stream, flush=True)
        elif event_type == 'error':
            _logger.warning(f"{data.get('msg', '')} [{data.get('file', '')}]")

    indexer = IncrementalIndexer(callback=progress)
    try:
        stats = indexer.index_folder(str(folder), extract_entities=not args.no_entities,
                                     max_workers=args.workers, cleanup=args.cleanup)
    except KeyboardInterrupt:
        indexer.stop()
        log_info("Indexing interrupted")
        return 130

    if args.vacuum:
        try:
            print("Optimizing database (VACUUM)...", file=progress_stream, flush=True)
            file_index.vacuum()
        except Exception as e:
            log_error(f"VACUUM failed (indexing itself succeeded): {e}")

    summary = {"indexing": stats.to_dict(), "index": file_index.get_stats()}
    if args.json:
        _print_json(summary)
    else:
        s = stats
        print(f"\nIndexed: {s.matched_files}, unchanged: {s.cached}, no text: {s.skipped}, "
              f"errors: {s.errors}, total files: {s.total_files}, time: {s.duration_seconds:.1f}s")
        idx = summary['index']
        print(f"Index: {idx['total_files']} files, {idx['total_size_human']}, "
              f"DB {idx['db_size'] / 1024 / 1024:.1f} MB")
    return 0


def _module_available(name: str) -> bool:
    import importlib.util
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def collect_status(deep: bool = False) -> Dict:
    """Состояние системы (без загрузки тяжёлых моделей, если deep=False)."""
    from core import file_index, ASTREX_HOME

    status: Dict = {"version": VERSION, "home": str(ASTREX_HOME)}
    stats = file_index.get_stats()
    status["index"] = stats
    status["total_files"] = stats["total_files"]
    status["db_size"] = stats["db_size"]

    nlp: Dict = {}
    try:
        from core.nlp import morph_analyzer, entity_extractor, relevance_calculator
        nlp["morphology"] = morph_analyzer.available
        nlp["morphology_backend"] = morph_analyzer.backend
        if deep:
            nlp["spacy"] = entity_extractor.nlp is not None
            nlp["sbert"] = relevance_calculator.sbert_model is not None
        else:
            nlp["spacy_installed"] = _module_available("spacy")
            nlp["sbert_installed"] = _module_available("sentence_transformers")
        nlp["fuzzy"] = _module_available("rapidfuzz")
    except Exception as e:
        nlp["error"] = str(e)
    status["nlp"] = nlp

    try:
        from ml import vector_store
        status["vectors"] = vector_store.get_stats()
    except Exception as e:
        status["vectors"] = {"available": False, "error": str(e)}

    try:
        from ml import get_llm_status
        status["llm"] = get_llm_status()
    except Exception as e:
        status["llm"] = {"available": False, "error": str(e)}

    try:
        from extractors import registry
        extractors = registry.list_extractors()
        status["extractors"] = {
            "registered": len(extractors),
            "available": sum(1 for e in extractors if e['available']),
            "unavailable": [e['name'] for e in extractors if not e['available']],
        }
    except Exception as e:
        status["extractors"] = {"error": str(e)}
    return status


def cmd_status(args) -> int:
    """Show system status"""
    status = collect_status(deep=args.deep)
    if args.json:
        _print_json(status)
        return 0

    idx = status["index"]
    yes = lambda v: '✓' if v else '✗'  # noqa: E731
    print(f"\nASTREX v{VERSION} — System Status\n")
    print('=' * 50)
    print(f"\nHome: {status['home']}")
    print("\nIndex:")
    print(f"  Files:      {idx['total_files']}")
    print(f"  Size:       {idx['total_size_human']}")
    print(f"  DB size:    {idx['db_size'] / 1024 / 1024:.1f} MB")
    print(f"  Links:      {idx['total_entity_links']}")
    print(f"  FTS5:       {yes(idx.get('fts_available', True))}")

    nlp = status["nlp"]
    print("\nNLP:")
    print(f"  Morphology: {yes(nlp.get('morphology'))} {nlp.get('morphology_backend') or 'snowball stemmer'}")
    if args.deep:
        print(f"  spaCy:      {yes(nlp.get('spacy'))}")
        print(f"  SBERT:      {yes(nlp.get('sbert'))}")
    else:
        print(f"  spaCy:      {yes(nlp.get('spacy_installed'))} (installed)")
        print(f"  SBERT:      {yes(nlp.get('sbert_installed'))} (installed)")
    print(f"  Fuzzy:      {yes(nlp.get('fuzzy'))}")

    vs = status["vectors"]
    print("\nVector Store:")
    print(f"  Available:  {yes(vs.get('available'))} {vs.get('type', '')}")
    print(f"  Documents:  {vs.get('document_count', 0)} ({vs.get('chunk_count', 0)} chunks)")

    llm = status["llm"]
    print("\nLLM:")
    print(f"  Available:  {yes(llm.get('available'))}")
    print(f"  Host:       {llm.get('host')}")
    if llm.get('models'):
        print(f"  Models:     {', '.join(llm['models'][:5])}")

    ex = status["extractors"]
    print("\nExtractors:")
    print(f"  Registered: {ex.get('registered')}")
    print(f"  Available:  {ex.get('available')}")
    print(f"\n{'=' * 50}\n")
    return 0


def _graph_from_index(limit: Optional[int] = None):
    from core import file_index, GraphBuilder
    builder = GraphBuilder()
    count = 0
    for row in file_index.iter_files(include_text=True, with_entities_only=True, limit=limit):
        try:
            entities = json.loads(row.get('entities_json') or '{}')
        except ValueError:
            continue
        if entities:
            builder.build_from_entities(entities, (row.get('extracted_text') or '')[:20000],
                                        file_path=row.get('path', ''))
            count += 1
    return builder.get_graph(), count


def cmd_export(args) -> int:
    """Export entity graph built from the index"""
    from core import export_to_graphml, export_to_gexf

    output_path = Path(args.output).expanduser()
    fmt = args.format
    if fmt == 'auto':
        fmt = output_path.suffix.lower().lstrip('.')
    if fmt not in ('graphml', 'gexf', 'json'):
        log_error(f"Unknown format: {fmt!r}. Use .graphml, .gexf or .json (or --format)")
        return 1

    graph, files = _graph_from_index(args.limit)
    log_info(f"Graph: {len(graph.nodes)} nodes, {len(graph.edges)} edges (from {files} indexed files)")
    if not graph.nodes:
        log_error("Index contains no entities. Run 'astrex.py index <folder>' first.")

    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if fmt == 'graphml':
            export_to_graphml(graph, str(output_path))
        elif fmt == 'gexf':
            export_to_gexf(graph, str(output_path))
        else:
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(graph.to_dict(), f, ensure_ascii=False, indent=2)
    except OSError as e:
        log_error(f"Export failed: {e}")
        return 1

    log_info(f"Exported to {output_path}")
    return 0


def cmd_ingest(args) -> int:
    """Ingest documents into RAG vector store"""
    from ml.rag import RAGPipeline
    from extractors import registry

    folder = _folder_arg(args.folder)
    if folder is None:
        return 1
    pipeline = RAGPipeline()
    if not pipeline.vector_store.available:
        log_error("Vector store unavailable: install sentence-transformers (and chromadb)")
        return 1

    ingested = errors = empty = 0
    for i, fp in enumerate(iter_input_files(folder, limit=args.limit), 1):
        try:
            result = registry.extract(fp)
            if result.success and result.full_text.strip():
                chunks = pipeline.ingest(doc_id=str(fp), text=result.full_text[:ENGINE_CONFIG.max_extracted_chars],
                                         metadata={'filename': fp.name, 'path': str(fp)})
                if chunks:
                    ingested += 1
                else:
                    errors += 1
            else:
                empty += 1
        except Exception as e:
            errors += 1
            _logger.debug(f"Ingest failed for {fp}: {e}")
        if i % 50 == 0:
            log_info(f"Progress: {i} files processed, {ingested} ingested")

    print(f"\nIngested: {ingested} documents, without text: {empty}, errors: {errors}")
    return 0


def cmd_ask(args) -> int:
    """Ask a question using RAG pipeline"""
    from ml.rag import RAGPipeline

    pipeline = RAGPipeline(top_k=args.top_k)
    response = pipeline.query(args.question)

    if args.format == 'json':
        _print_json({"answer": response.answer, "model": response.model, "error": response.error,
                     "sources": response.sources, "tokens_used": response.tokens_used})
        return 1 if response.error and not response.answer else 0

    if response.error and not response.answer:
        log_error(f"RAG error: {response.error}")
        return 1

    print(f"\nОтвет ({response.model}):\n")
    print(response.answer)
    if response.sources:
        print(f"\n--- Источники ({len(response.sources)}) ---")
        for i, src in enumerate(response.sources[:10], 1):
            meta = src.get('metadata', {})
            print(f"[{i}] score={src['score']:.2f} {meta.get('path') or meta.get('doc_id', '?')}")
    return 0


def cmd_classify(args) -> int:
    """Classify documents in folder"""
    from core.classifier import DocumentClassifier
    from extractors import registry

    folder = _folder_arg(args.folder)
    if folder is None:
        return 1
    classifier = DocumentClassifier()
    results = []
    for fp in iter_input_files(folder, limit=args.limit):
        try:
            result = registry.extract(fp)
            if not (result.success and result.full_text.strip()):
                continue
            cr = classifier.classify(result.full_text[:50000])
            results.append((fp, cr))
            if args.format == 'text':
                topics_str = ', '.join(cr.topics[:3]) if cr.topics else '-'
                print(f"{fp.name}: {cr.category} ({cr.confidence:.0%}) lang={cr.language} "
                      f"sent={cr.sentiment} topics=[{topics_str}]")
        except Exception as e:
            _logger.debug(f"Classification failed for {fp}: {e}")

    if args.format == 'json':
        _print_json([{"path": str(fp), **cr.__dict__} for fp, cr in results])
    else:
        print(f"\nОбработано: {len(results)} файлов")
    return 0


def cmd_crypto(args) -> int:
    """Scan for encrypted files and crypto artifacts"""
    from core.crypto import scan_directory_crypto

    folder = _folder_arg(args.folder)
    if folder is None:
        return 1
    results = scan_directory_crypto(str(folder), sample_size=args.limit, deep=args.deep)
    encrypted = [r for r in results if r.is_encrypted]

    if args.format == 'json':
        _print_json([r.__dict__ for r in results if r.is_encrypted or r.crypto_artifacts])
        return 0

    print(f"\nСканировано: {len(results)} файлов")
    print(f"Зашифрованных (вероятно): {len(encrypted)}\n")
    for r in encrypted:
        print(f"  {r.file_path}")
        print(f"    Тип: {r.encryption_type}, формат: {r.file_format}, "
              f"энтропия: {r.entropy:.2f}, уверенность: {r.confidence:.0%}")
        if r.crypto_artifacts:
            print(f"    Артефакты: {', '.join(r.crypto_artifacts[:5])}")
    if args.deep:
        others = [r for r in results if not r.is_encrypted and r.crypto_artifacts]
        if others:
            print("\nКриптоартефакты в файлах:")
            for r in others:
                print(f"  {r.file_path}: {', '.join(r.crypto_artifacts[:5])}")
    return 0


def cmd_dedup(args) -> int:
    """Find duplicate files"""
    from core.fingerprint import FingerprintEngine, deduplicate

    folder = _folder_arg(args.folder)
    if folder is None:
        if args.json:
            _write_json_line({"type": "error", "msg": f"Folder not found: {args.folder}"})
        return 1

    engine = FingerprintEngine()
    fingerprints = []
    failed = 0
    for i, fp in enumerate(iter_input_files(folder, limit=args.limit, supported_only=False), 1):
        try:
            fingerprints.append(engine.fingerprint_file(str(fp)))
        except Exception as e:
            failed += 1
            _logger.debug(f"Fingerprint failed for {fp}: {e}")
        if args.json and i % 100 == 0:
            _write_json_line({"type": "progress", "current": i})

    report = deduplicate(fingerprints, near_threshold=args.threshold)
    sizes = {f.file_path: f.size for f in fingerprints}

    if args.json:
        # JSON Lines — формат, который читает вкладка DUPES в GUI
        for group in report.exact_duplicate_groups:
            _write_json_line({"type": "exact", "group": group, "size": sizes.get(group[0], 0)})
        for p1, p2, sim in report.near_duplicate_pairs:
            _write_json_line({"type": "near", "path1": p1, "path2": p2, "similarity": sim})
        _write_json_line({"type": "summary", "total": report.total_files, "unique": report.unique_files,
                          "wasted_bytes": report.wasted_bytes, "failed": failed,
                          "exact_groups": len(report.exact_duplicate_groups),
                          "near_pairs": len(report.near_duplicate_pairs)})
        return 0

    print(f"\nФайлов: {report.total_files}, уникальных: {report.unique_files}, ошибок чтения: {failed}")
    print(f"Занято дубликатами: {report.wasted_bytes / 1024 / 1024:.1f} MB")
    if report.exact_duplicate_groups:
        print(f"\nТочные дубликаты ({len(report.exact_duplicate_groups)} групп):")
        for group in report.exact_duplicate_groups[:20]:
            print(f"  {', '.join(group)}")
    if report.near_duplicate_pairs:
        print(f"\nПохожие файлы ({len(report.near_duplicate_pairs)} пар):")
        for p1, p2, sim in report.near_duplicate_pairs[:20]:
            print(f"  {p1} <-> {p2} ({sim:.0%})")
    return 0


def cmd_timeline(args) -> int:
    """Build timeline from documents"""
    from core.timeline import TimelineBuilder
    from extractors import registry

    folder = _folder_arg(args.folder)
    if folder is None:
        return 1
    builder = TimelineBuilder()
    for fp in iter_input_files(folder, limit=args.limit):
        try:
            result = registry.extract(fp)
            if result.success and result.full_text:
                builder.add_document(result.full_text, source=str(fp), file_mtime=fp.stat().st_mtime)
        except Exception as e:
            _logger.debug(f"Timeline failed for {fp}: {e}")

    summary = builder.get_summary()
    if args.format == 'json':
        _print_json({"summary": summary, "events": builder.to_list()})
        return 0

    print(f"\nСобытий: {summary.get('total_events', 0)}")
    if summary.get('date_range'):
        print(f"Период: {summary['date_range']}")
    if summary.get('by_type'):
        print(f"По типам: {summary['by_type']}")
    for e in builder.build()[:args.show]:
        print(f"  {e.date} [{e.event_type}] {Path(e.source).name}: {e.text[:100]}")
    return 0


def cmd_report(args) -> int:
    """Generate report from index data"""
    from reporting import ReportData, ReportSection
    from reporting import generate_html_report, generate_csv_report, generate_excel_report, generate_pdf_report
    from core import file_index

    output = Path(args.output).expanduser()
    generators = {'.html': generate_html_report, '.htm': generate_html_report, '.csv': generate_csv_report,
                  '.xlsx': generate_excel_report, '.pdf': generate_pdf_report}
    generator = generators.get(output.suffix.lower())
    if generator is None:
        log_error(f"Unsupported format: {output.suffix}. Use .html, .csv, .xlsx, or .pdf")
        return 1

    index_stats = file_index.get_stats()
    results = []
    all_entities: Dict[str, List[str]] = {}
    for row in file_index.iter_files(include_text=True, limit=args.limit):
        entities = {}
        if row.get('entities_json'):
            try:
                entities = json.loads(row['entities_json'])
            except ValueError:
                entities = {}
        text = ' '.join((row.get('extracted_text') or '').split())
        results.append({
            'filename': row.get('filename', ''),
            'path': row.get('path', ''),
            'score': 1.0,
            'snippet': text[:300],
            'entities': entities,
        })
        for k, v in entities.items():
            if isinstance(v, list):
                all_entities.setdefault(k, []).extend(str(x) for x in v)

    for k in all_entities:
        all_entities[k] = list(dict.fromkeys(all_entities[k]))[:100]

    data = ReportData(
        title=args.title or "ASTREX — Отчёт по индексу",
        subtitle=f"Файлов в индексе: {index_stats['total_files']}, размер: {index_stats['total_size_human']}",
        stats={
            'total_files': index_stats['total_files'],
            'total_size': index_stats['total_size_human'],
            'db_size_mb': f"{index_stats['db_size'] / 1024 / 1024:.1f}",
            'entity_links': index_stats['total_entity_links'],
        },
        results=results,
        entities=all_entities,
        sections=[ReportSection(title="Расширения", data=index_stats.get('by_extension', []))],
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    if not generator(data, str(output)):
        log_error(f"Report generation failed: {output} (see log above; .xlsx needs openpyxl, "
                  f".pdf needs weasyprint)")
        return 1
    log_info(f"Report saved: {output}")
    return 0


def cmd_plugins(args) -> int:
    """Manage plugins"""
    from core.plugins import plugin_manager

    if args.action == 'load':
        loaded = plugin_manager.load_all()
        for p in plugin_manager.list_plugins():
            state = "loaded" if p.loaded else f"FAILED: {p.error}"
            print(f"  {p.name} v{p.version}: {state} {', '.join(p.extractors)}")
        print(f"Загружено: {loaded} плагинов")
        return 0 if loaded == len(plugin_manager.list_plugins()) else 1

    plugins = plugin_manager.discover()
    if not plugins:
        print(f"Нет плагинов в {plugin_manager.PLUGIN_DIR}")
    for p in plugins:
        autoload = "автозагрузка" if ENGINE_CONFIG.plugins_autoload else "вручную"
        print(f"  {p.name} v{p.version} — {p.description} ({autoload})")
    return 0


def cmd_graph(args) -> int:
    """Analyze entity graph built from the index"""
    from core import GraphAnalyzer

    graph, files = _graph_from_index(args.limit)
    analyzer = GraphAnalyzer(graph)
    stats = analyzer.get_statistics()
    stats['source_files'] = files

    heavy_ok = stats['node_count'] <= 3000
    if args.format == 'json':
        if heavy_ok:
            bc = analyzer.betweenness_centrality()
            stats['betweenness_top5'] = [
                {'node_id': n, 'score': s, 'label': graph.nodes[n].label if n in graph.nodes else n}
                for n, s in sorted(bc.items(), key=lambda x: -x[1])[:5]]
            stats['bridges'] = len(analyzer.get_bridges())
        _print_json(stats)
        return 0

    print(f"\nГраф: {stats['node_count']} узлов, {stats['edge_count']} рёбер (из {files} файлов)")
    print(f"Плотность: {stats['density']:.4f}")
    print(f"Компонент связности: {stats['communities']}")
    print(f"Типы узлов: {stats['node_types']}")

    if stats.get('pagerank_top5'):
        print("\nPageRank Top-5:")
        for item in stats['pagerank_top5']:
            print(f"  {item['label']}: {item['score']:.4f}")

    if not heavy_ok:
        print("\n(betweenness и мосты не вычисляются для графов больше 3000 узлов)")
        return 0

    bc = analyzer.betweenness_centrality()
    if bc:
        print("\nBetweenness Top-5:")
        for nid, score in sorted(bc.items(), key=lambda x: -x[1])[:5]:
            node = graph.nodes.get(nid)
            print(f"  {node.label if node else nid}: {score:.4f}")

    bridges = analyzer.get_bridges()
    if bridges:
        print(f"\nМосты (критические связи): {len(bridges)}")
        for edge in bridges[:5]:
            src, tgt = graph.nodes.get(edge.source), graph.nodes.get(edge.target)
            print(f"  {src.label if src else edge.source} <-> {tgt.label if tgt else edge.target}")
    return 0


def cmd_web(args) -> int:
    """Start web server"""
    from web.api import run_server
    return run_server(host=args.host, port=args.port, debug=args.debug)


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='astrex',
        description=f'ASTREX v{VERSION} — Intelligence System',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  astrex scan "Иванов" /data/documents
  astrex scan "договор" ./files --format json --limit 50
  astrex scan -- "-5%" ./files          (запрос, начинающийся с '-')
  astrex search "Газпром" --limit 20
  astrex index /data/archive --cleanup
  astrex status --json
  astrex export graph.gexf
        """
    )

    parser.add_argument('--version', action='version', version=f'ASTREX v{VERSION}')
    subparsers = parser.add_subparsers(dest='command', help='Commands')

    # Scan
    p = subparsers.add_parser('scan', help='Full scan with indexing and NLP')
    p.add_argument('query', help='Search query')
    p.add_argument('folder', help='Folder to scan')
    p.add_argument('--format', choices=['text', 'json', 'jsonl'], default='text',
                   help='Output: text report, single JSON document, or JSON Lines event stream (GUI)')
    p.add_argument('--limit', type=int, default=100, help='Max results')
    p.add_argument('--min-score', type=float, default=0.1, help='Minimum relevance score (0..1)')
    p.add_argument('--workers', type=int, help='Number of worker processes')
    p.add_argument('--timeout', type=int, help='Per-file processing time limit, seconds (0 = none)')
    p.add_argument('--no-index', action='store_true', help='Disable index caching')
    p.add_argument('--no-nlp', action='store_true', help='Disable NLP (entities, semantic scoring)')
    p.add_argument('--no-fuzzy', action='store_true', help='Disable fuzzy search')
    p.add_argument('--no-morph', action='store_true', help='Disable morphological expansion')

    # Search
    p = subparsers.add_parser('search', help='Quick search in index')
    p.add_argument('query', help='Search query')
    p.add_argument('--format', choices=['text', 'json'], default='text', help='Output format')
    p.add_argument('--limit', type=int, default=50, help='Max results')
    p.add_argument('--min-score', type=float, default=0.0, help='Minimum relevance score (0..1)')
    p.add_argument('--any', action='store_true', help='Match ANY word (OR) instead of all words')

    # Index
    p = subparsers.add_parser('index', help='Build or update index')
    p.add_argument('folder', help='Folder to index')
    p.add_argument('--cleanup', action='store_true',
                   help='Remove index entries for files of THIS folder that no longer exist')
    p.add_argument('--vacuum', action='store_true', help='Compact the database afterwards')
    p.add_argument('--workers', type=int, help='Number of worker processes')
    p.add_argument('--no-entities', action='store_true', help='Do not extract entities')
    p.add_argument('--json', action='store_true', help='Print final statistics as JSON')

    # Status
    p = subparsers.add_parser('status', help='Show system status')
    p.add_argument('--json', action='store_true', help='Output JSON')
    p.add_argument('--deep', action='store_true', help='Load NLP models to check them (slow)')

    # Export
    p = subparsers.add_parser('export', help='Export entity graph from the index')
    p.add_argument('output', help='Output file (.graphml, .gexf, .json)')
    p.add_argument('--format', choices=['auto', 'graphml', 'gexf', 'json'], default='auto',
                   help='Output format (default: by file extension)')
    p.add_argument('--limit', type=int, help='Max indexed files to use')

    # Ingest (RAG)
    p = subparsers.add_parser('ingest', help='Ingest documents into RAG vector store')
    p.add_argument('folder', help='Folder to ingest')
    p.add_argument('--limit', type=int, default=5000, help='Max files to process')

    # Graph
    p = subparsers.add_parser('graph', help='Analyze entity graph')
    p.add_argument('--format', choices=['text', 'json'], default='text')
    p.add_argument('--limit', type=int, help='Max indexed files to use')

    # Web
    p = subparsers.add_parser('web', help='Start web server')
    p.add_argument('--host', default=None, help='Server host (default: 127.0.0.1)')
    p.add_argument('--port', type=int, default=None, help='Server port (default: 8080)')
    p.add_argument('--debug', action='store_true', default=None, help='Enable auto-reload')

    # Ask (RAG)
    p = subparsers.add_parser('ask', help='Ask a question (RAG pipeline)')
    p.add_argument('question', help='Question to ask')
    p.add_argument('--top-k', type=int, default=10, help='Number of chunks to retrieve')
    p.add_argument('--format', choices=['text', 'json'], default='text')

    # Classify
    p = subparsers.add_parser('classify', help='Classify documents')
    p.add_argument('folder', help='Folder to classify')
    p.add_argument('--limit', type=int, default=500, help='Max files')
    p.add_argument('--format', choices=['text', 'json'], default='text')

    # Crypto
    p = subparsers.add_parser('crypto', help='Detect encrypted files and crypto artifacts')
    p.add_argument('folder', help='Folder to scan')
    p.add_argument('--limit', type=int, default=100, help='Max files')
    p.add_argument('--deep', action='store_true', help='Also search text for keys, wallets, hashes')
    p.add_argument('--format', choices=['text', 'json'], default='text')

    # Dedup
    p = subparsers.add_parser('dedup', help='Find duplicate files')
    p.add_argument('folder', help='Folder to scan')
    p.add_argument('--limit', type=int, default=1000, help='Max files')
    p.add_argument('--threshold', type=float, default=0.8, help='Near-duplicate threshold (0..1)')
    p.add_argument('--json', action='store_true', help='JSON Lines output (exact/near/summary)')

    # Timeline
    p = subparsers.add_parser('timeline', help='Build event timeline')
    p.add_argument('folder', help='Folder to analyze')
    p.add_argument('--format', choices=['text', 'json'], default='text')
    p.add_argument('--limit', type=int, default=500, help='Max files')
    p.add_argument('--show', type=int, default=50, help='Max events to display')

    # Report
    p = subparsers.add_parser('report', help='Generate report from the index')
    p.add_argument('output', help='Output file (.html, .csv, .xlsx, .pdf)')
    p.add_argument('--title', help='Report title')
    p.add_argument('--limit', type=int, default=500, help='Max files to include')

    # Plugins
    p = subparsers.add_parser('plugins', help='Manage plugins')
    p.add_argument('action', choices=['list', 'load'], help='Action')

    return parser


COMMANDS = {
    'scan': cmd_scan,
    'search': cmd_search,
    'index': cmd_index,
    'status': cmd_status,
    'export': cmd_export,
    'ingest': cmd_ingest,
    'graph': cmd_graph,
    'web': cmd_web,
    'ask': cmd_ask,
    'classify': cmd_classify,
    'crypto': cmd_crypto,
    'dedup': cmd_dedup,
    'timeline': cmd_timeline,
    'report': cmd_report,
    'plugins': cmd_plugins,
}


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 0

    try:
        return COMMANDS[args.command](args)
    except KeyboardInterrupt:
        log_info("Interrupted")
        return 130


def _sigterm_handler(signum, frame):
    # QProcess::terminate() / kill -TERM: штатная остановка (не ошибка)
    if Output.jsonl:
        _write_json_line({"type": "status", "msg": "Stopped by user (SIGTERM)"})
    raise SystemExit(143)


if __name__ == '__main__':
    import faulthandler

    # Traceback при SIGSEGV/SIGABRT в stderr (GUI показывает его во вкладке ERRORS)
    faulthandler.enable()
    signal.signal(signal.SIGTERM, _sigterm_handler)

    try:
        sys.exit(main())
    except MemoryError:
        msg = "Fatal: out of memory. Try fewer workers (--workers 1) or --no-nlp."
        if Output.jsonl:
            _write_json_line({"type": "error", "msg": msg})
        log_error(msg)
        sys.exit(137)
    except Exception as e:
        msg = f"Fatal error: {type(e).__name__}: {e}"
        if Output.jsonl:
            _write_json_line({"type": "error", "msg": msg})
        _logger.exception(msg)
        sys.exit(1)
