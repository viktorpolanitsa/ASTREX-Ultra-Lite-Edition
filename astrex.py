#!/usr/bin/env python3
"""
ASTREX v3.0 — Intelligence System
Форензическая система глубокого анализа данных

Usage:
    astrex.py scan <query> <folder> [options]
    astrex.py search <query> [options]
    astrex.py index <folder> [options]
    astrex.py status
    astrex.py export <output> [options]
"""

import sys
import json
import argparse
from pathlib import Path
from datetime import datetime

# Ensure UTF-8 output
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

# ═══════════════════════════════════════════════════════════════════════════════
# LOGGING
# ═══════════════════════════════════════════════════════════════════════════════

from core.config import VERSION
from core.logging_setup import get_logger

_logger = get_logger("astrex.cli")


def emit(event_type: str, data: dict) -> None:
    """Emit JSON event to stdout (main process only)"""
    try:
        output = {"type": event_type, "timestamp": datetime.now().isoformat(), **data}
        line = json.dumps(output, ensure_ascii=False, default=str)
        sys.stdout.write(line + '\n')
        sys.stdout.flush()
    except (BrokenPipeError, OSError):
        pass
    except Exception:
        pass


def log_info(msg: str) -> None:
    """Log info message"""
    _logger.info(msg)


def log_error(msg: str) -> None:
    """Log error message"""
    _logger.error(msg)


# ═══════════════════════════════════════════════════════════════════════════════
# COMMANDS
# ═══════════════════════════════════════════════════════════════════════════════

def cmd_scan(args):
    """Full scan with indexing and NLP"""
    from core import ScanEngine, ENGINE_CONFIG
    
    log_info(f"ASTREX v{VERSION} — Scan")
    log_info(f"Query: {args.query}")
    log_info(f"Folder: {args.folder}")
    
    # Validate folder
    folder = Path(args.folder).resolve()
    if not folder.exists():
        log_error(f"Folder not found: {folder}")
        return 1
    
    # Create scanner
    engine = ScanEngine(
        callback=emit,
        use_cache=not args.no_index,
        use_nlp=not args.no_nlp,
        fuzzy_search=not args.no_fuzzy
    )
    
    # Run scan
    try:
        results = engine.scan(
            folder=str(folder),
            query=args.query,
            max_workers=args.workers
        )
        
        # Filter by min_score
        if args.min_score > 0:
            results = [r for r in results if r.score >= args.min_score]

        # Output results
        if args.format == 'json':
            output = {
                "query": args.query,
                "folder": str(folder),
                "total_matches": len(results),
                "stats": {
                    "total_files": engine.stats.total_files,
                    "processed": engine.stats.processed_files,
                    "matched": engine.stats.matched_files,
                    "errors": engine.stats.errors,
                    "duration": engine.stats.duration_seconds,
                    "speed": engine.stats.files_per_second
                },
                "results": [r.to_dict() for r in results[:args.limit]]
            }
            print(json.dumps(output, ensure_ascii=False, indent=2))
        else:
            # Text format
            print(f"\n{'='*70}")
            print(f"РЕЗУЛЬТАТЫ ПОИСКА: {args.query}")
            print(f"{'='*70}")
            print(f"Найдено: {len(results)} совпадений из {engine.stats.total_files} файлов")
            print(f"Время: {engine.stats.duration_seconds:.1f}с ({engine.stats.files_per_second:.0f} файлов/сек)")
            print(f"{'='*70}\n")
            
            for i, r in enumerate(results[:args.limit], 1):
                print(f"[{i}] {r.filename} (score: {r.score:.2f})")
                print(f"    Path: {r.path}")
                if r.entities:
                    for ent_type, ents in r.entities.items():
                        if ents:
                            print(f"    {ent_type}: {', '.join(str(e) for e in ents[:5])}")
                print(f"    ---")
                print(f"    {(r.snippet or '')[:300]}...")
                print()
        
        return 0
        
    except KeyboardInterrupt:
        engine.stop()
        log_info("Scan interrupted")
        return 130
    except Exception as e:
        log_error(f"Scan failed: {e}")
        return 1


def cmd_search(args):
    """Quick search in index only"""
    from core import file_index

    log_info(f"ASTREX v{VERSION} — Index Search")
    log_info(f"Query: {args.query}")

    try:
        results = file_index.search_fts(
            query=args.query,
            limit=args.limit
        )
        
        # Filter by min_score
        if args.min_score > 0:
            results = [r for r in results if r.get('score', 0) >= args.min_score]

        if args.format == 'json':
            print(json.dumps(results, ensure_ascii=False, indent=2))
        else:
            print(f"\nНайдено: {len(results)} результатов\n")
            for i, r in enumerate(results, 1):
                print(f"[{i}] {r['filename']} (score: {r['score']:.2f})")
                print(f"    {r['path']}")
                print()
        
        return 0
        
    except Exception as e:
        log_error(f"Search failed: {e}")
        return 1


def cmd_index(args):
    """Build or update index"""
    from core import file_index, FILE_TYPES
    from extractors import registry
    
    log_info(f"ASTREX v{VERSION} — Index Builder")
    log_info(f"Folder: {args.folder}")
    
    folder = Path(args.folder).resolve()
    if not folder.exists():
        log_error(f"Folder not found: {folder}")
        return 1
    
    try:
        # Collect files
        files = []
        for item in folder.rglob('*'):
            if item.is_file() and item.suffix.lower() in FILE_TYPES.all_supported:
                files.append(item)

        log_info(f"Found {len(files)} files to index")

        # Cleanup stale entries if requested
        if args.cleanup:
            existing_paths = {str(fp.resolve()) for fp in files}
            deleted = file_index.delete_missing_files(existing_paths)
            if deleted:
                log_info(f"Cleaned up {deleted} stale index entries")
            else:
                log_info("No stale entries found")
        
        indexed = 0
        errors = 0
        
        for i, fp in enumerate(files):
            if i % 100 == 0:
                log_info(f"Progress: {i}/{len(files)}")
            
            try:
                stat = fp.stat()
                
                # Check if needs update
                if not file_index.file_needs_update(str(fp), stat.st_mtime, stat.st_size):
                    continue
                
                # Extract text
                result = registry.extract(fp)
                text = result.full_text if result.success else None
                
                # Extract entities
                entities = {}
                if text:
                    from core import extract_entities
                    entities = extract_entities(text[:50000])
                
                # Update index
                file_index.upsert_file(
                    path=str(fp),
                    filename=fp.name,
                    extension=fp.suffix.lower(),
                    size=stat.st_size,
                    mtime=stat.st_mtime,
                    extracted_text=text[:100000] if text else None,
                    entities=entities
                )
                
                indexed += 1
                
            except Exception as e:
                errors += 1
        
        # Vacuum
        file_index.vacuum()
        
        log_info(f"Indexed: {indexed}, Errors: {errors}")
        
        # Stats
        stats = file_index.get_stats()
        print(json.dumps(stats, ensure_ascii=False, indent=2))
        
        return 0
        
    except Exception as e:
        log_error(f"Indexing failed: {e}")
        return 1


def cmd_status(args):
    """Show system status"""
    from core import file_index, ASTREX_HOME
    
    print(f"\nASTREX v{VERSION} — System Status\n")
    print(f"{'='*50}")
    
    # Home directory
    print(f"\nHome: {ASTREX_HOME}")
    
    # Index stats
    stats = file_index.get_stats()
    print(f"\nIndex:")
    print(f"  Files:      {stats['total_files']}")
    print(f"  Size:       {stats['total_size_human']}")
    print(f"  DB size:    {stats['db_size'] / 1024 / 1024:.1f} MB")
    print(f"  Links:      {stats['total_entity_links']}")
    
    # NLP status
    print(f"\nNLP:")
    try:
        from core.nlp import morph_analyzer, entity_extractor, relevance_calculator
        print(f"  Morphology: {'✓' if morph_analyzer.available else '✗'}")
        print(f"  spaCy:      {'✓' if entity_extractor.nlp else '✗'}")
        print(f"  SBERT:      {'✓' if relevance_calculator.sbert_model else '✗'}")
    except Exception as e:
        print(f"  Error: {e}")
    
    # Vector store
    print(f"\nVector Store:")
    try:
        from ml import vector_store
        vs_stats = vector_store.get_stats()
        print(f"  Available:  {'✓' if vs_stats.get('available') else '✗'}")
        print(f"  Documents:  {vs_stats.get('document_count', 0)}")
    except Exception as e:
        print(f"  Error: {e}")
    
    # LLM
    print(f"\nLLM:")
    try:
        from ml import get_llm_status
        llm_stats = get_llm_status()
        print(f"  Available:  {'✓' if llm_stats.get('available') else '✗'}")
        print(f"  Host:       {llm_stats.get('host')}")
        if llm_stats.get('models'):
            print(f"  Models:     {', '.join(llm_stats['models'][:5])}")
    except Exception as e:
        print(f"  Error: {e}")
    
    # Extractors
    print(f"\nExtractors:")
    try:
        from extractors import registry
        extractors = registry.list_extractors()
        available = sum(1 for e in extractors if e['available'])
        print(f"  Registered: {len(extractors)}")
        print(f"  Available:  {available}")
    except Exception as e:
        print(f"  Error: {e}")
    
    print(f"\n{'='*50}\n")
    
    return 0


def cmd_export(args):
    """Export graph or results"""
    from core import file_index, Graph, GraphBuilder, export_to_graphml, export_to_gexf
    
    log_info(f"ASTREX v{VERSION} — Export")
    
    output_path = Path(args.output)
    
    try:
        # Build graph from index
        builder = GraphBuilder()

        # Get all indexed files and build graph from their entities
        stats = file_index.get_stats()
        log_info(f"Building graph from {stats['total_files']} indexed files")

        all_files = file_index.search_fts('*', limit=10000)
        for row in all_files:
            entities = {}
            if row.get('entities_json'):
                try:
                    entities = json.loads(row['entities_json'])
                except Exception:
                    continue
            if entities:
                text = row.get('extracted_text', '') or ''
                builder.build_from_entities(entities, text[:5000], file_path=row.get('path', ''))

        graph = builder.get_graph()
        log_info(f"Graph: {len(graph.nodes)} nodes, {len(graph.edges)} edges")

        # Export
        if output_path.suffix == '.graphml':
            export_to_graphml(graph, str(output_path))
        elif output_path.suffix == '.gexf':
            export_to_gexf(graph, str(output_path))
        elif output_path.suffix == '.json':
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(graph.to_dict(), f, ensure_ascii=False, indent=2)
        else:
            log_error(f"Unknown format: {output_path.suffix}")
            return 1
        
        log_info(f"Exported to {output_path}")
        return 0
        
    except Exception as e:
        log_error(f"Export failed: {e}")
        return 1


def cmd_ingest(args):
    """Ingest documents into RAG vector store"""
    log_info(f"ASTREX v{VERSION} — RAG Ingest")
    try:
        from ml.rag import RAGPipeline
        from extractors import registry

        pipeline = RAGPipeline()
        folder = Path(args.folder).resolve()
        if not folder.exists():
            log_error(f"Folder not found: {folder}")
            return 1

        ingested = 0
        errors = 0
        for fp in folder.rglob('*'):
            if not fp.is_file():
                continue
            if ingested >= args.limit:
                break
            try:
                result = registry.extract(fp)
                if result.success and result.full_text:
                    chunks = pipeline.ingest(
                        doc_id=str(fp),
                        text=result.full_text[:100000],
                        metadata={'filename': fp.name, 'path': str(fp)}
                    )
                    ingested += 1
                    if ingested % 50 == 0:
                        log_info(f"Progress: {ingested} documents ingested")
            except Exception:
                errors += 1

        print(f"\nIngested: {ingested} documents, Errors: {errors}")
        return 0
    except Exception as e:
        log_error(f"Ingest failed: {e}")
        return 1


def cmd_ask(args):
    """Ask a question using RAG pipeline"""
    log_info(f"ASTREX v{VERSION} — RAG Query")
    try:
        from ml.rag import RAGPipeline
        pipeline = RAGPipeline(top_k=args.top_k)
        response = pipeline.query(args.question)

        if response.error and not response.answer:
            log_error(f"RAG error: {response.error}")
            return 1

        print(f"\nОтвет ({response.model}):\n")
        print(response.answer)

        if response.sources:
            print(f"\n--- Источники ({len(response.sources)}) ---")
            for i, src in enumerate(response.sources[:5], 1):
                meta = src.get('metadata', {})
                print(f"[{i}] score={src['score']:.2f} {meta.get('doc_id', '?')}")
        return 0
    except Exception as e:
        log_error(f"RAG failed: {e}")
        return 1


def cmd_classify(args):
    """Classify documents in folder"""
    log_info(f"ASTREX v{VERSION} — Classifier")
    try:
        from core.classifier import DocumentClassifier
        from extractors import registry
        classifier = DocumentClassifier()
        folder = Path(args.folder).resolve()
        if not folder.exists():
            log_error(f"Folder not found: {folder}")
            return 1

        results = {}
        for fp in [p for p in folder.rglob('*') if p.is_file()][:args.limit]:
            try:
                result = registry.extract(fp)
                if result.success and result.full_text:
                    cr = classifier.classify(result.full_text[:50000])
                    results[str(fp)] = cr
                    topics_str = ', '.join(cr.topics[:3]) if cr.topics else '-'
                    print(f"{fp.name}: {cr.category} ({cr.confidence:.0%}) lang={cr.language} sent={cr.sentiment} topics=[{topics_str}]")
            except Exception:
                continue

        print(f"\nОбработано: {len(results)} файлов")
        return 0
    except Exception as e:
        log_error(f"Classification failed: {e}")
        return 1


def cmd_crypto(args):
    """Scan for encrypted files and crypto artifacts"""
    log_info(f"ASTREX v{VERSION} — Crypto Detection")
    try:
        from core.crypto import scan_directory_crypto
        folder = Path(args.folder).resolve()
        if not folder.exists():
            log_error(f"Folder not found: {folder}")
            return 1

        results = scan_directory_crypto(str(folder), sample_size=args.limit)
        encrypted = [r for r in results if r.is_encrypted]

        print(f"\nСканировано: {len(results)} файлов")
        print(f"Зашифрованных: {len(encrypted)}\n")

        for r in encrypted:
            print(f"  {r.file_path}")
            print(f"    Тип: {r.encryption_type}, Энтропия: {r.entropy:.2f}, Confidence: {r.confidence:.0%}")
            if r.crypto_artifacts:
                print(f"    Артефакты: {', '.join(r.crypto_artifacts[:5])}")
        return 0
    except Exception as e:
        log_error(f"Crypto scan failed: {e}")
        return 1


def cmd_dedup(args):
    """Find duplicate files"""
    log_info(f"ASTREX v{VERSION} — Deduplication")
    try:
        from core.fingerprint import FingerprintEngine, deduplicate
        engine = FingerprintEngine()
        folder = Path(args.folder).resolve()
        if not folder.exists():
            log_error(f"Folder not found: {folder}")
            return 1

        fingerprints = []
        for fp in [p for p in folder.rglob('*') if p.is_file()][:args.limit]:
            try:
                fingerprints.append(engine.fingerprint_file(str(fp)))
            except Exception:
                continue

        report = deduplicate(fingerprints, near_threshold=args.threshold)

        if args.json:
            output = {
                "total_files": report.total_files,
                "unique_files": report.unique_files,
                "wasted_bytes": report.wasted_bytes,
                "exact_duplicate_groups": report.exact_duplicate_groups,
                "near_duplicate_pairs": [
                    {"file1": p1, "file2": p2, "similarity": sim}
                    for p1, p2, sim in report.near_duplicate_pairs
                ],
            }
            print(json.dumps(output, ensure_ascii=False, indent=2))
        else:
            print(f"\nФайлов: {report.total_files}, Уникальных: {report.unique_files}")
            print(f"Потеряно на дубликаты: {report.wasted_bytes / 1024 / 1024:.1f} MB")

            if report.exact_duplicate_groups:
                print(f"\nТочные дубликаты ({len(report.exact_duplicate_groups)} групп):")
                for group in report.exact_duplicate_groups[:10]:
                    print(f"  {', '.join(Path(p).name for p in group)}")

            if report.near_duplicate_pairs:
                print(f"\nПохожие файлы ({len(report.near_duplicate_pairs)} пар):")
                for p1, p2, sim in report.near_duplicate_pairs[:10]:
                    print(f"  {Path(p1).name} <-> {Path(p2).name} ({sim:.0%})")
        return 0
    except Exception as e:
        log_error(f"Dedup failed: {e}")
        return 1


def cmd_timeline(args):
    """Build timeline from documents"""
    log_info(f"ASTREX v{VERSION} — Timeline")
    try:
        from core.timeline import TimelineBuilder
        from extractors import registry
        builder = TimelineBuilder()
        folder = Path(args.folder).resolve()
        if not folder.exists():
            log_error(f"Folder not found: {folder}")
            return 1

        for fp in [p for p in folder.rglob('*') if p.is_file()][:args.limit]:
            try:
                result = registry.extract(fp)
                if result.success and result.full_text:
                    builder.add_document(result.full_text, source=str(fp), file_mtime=fp.stat().st_mtime)
            except Exception:
                continue

        summary = builder.get_summary()

        if args.format == 'json':
            print(json.dumps({"summary": summary, "events": builder.to_list()}, ensure_ascii=False, indent=2))
        else:
            print(f"\nСобытий: {summary.get('total_events', 0)}")
            if summary.get('date_range'):
                print(f"Период: {summary['date_range']}")
            if summary.get('by_type'):
                print(f"По типам: {summary['by_type']}")
            events = builder.build()
            for e in events[:args.show]:
                print(f"  {e.date} [{e.event_type}] {e.text[:100]}")
        return 0
    except Exception as e:
        log_error(f"Timeline failed: {e}")
        return 1


def cmd_report(args):
    """Generate report from index data"""
    log_info(f"ASTREX v{VERSION} — Report Generator")
    try:
        from reporting import ReportData, ReportSection
        from reporting import generate_html_report, generate_csv_report
        from core import file_index

        output = Path(args.output)

        # Gather real data from the index
        index_stats = file_index.get_stats()

        # Collect results and entities from index
        all_files = file_index.search_fts('*', limit=args.limit or 500)
        results = []
        all_entities = {}
        for row in all_files:
            results.append({
                'filename': row.get('filename', ''),
                'path': row.get('path', ''),
                'score': row.get('score', 0),
                'snippet': (row.get('snippet') or row.get('extracted_text', '') or '')[:300],
                'entities': {},
            })
            if row.get('entities_json'):
                try:
                    ents = json.loads(row['entities_json'])
                    results[-1]['entities'] = ents
                    for k, v in ents.items():
                        if isinstance(v, list):
                            all_entities.setdefault(k, []).extend(v)
                except Exception:
                    pass

        # Deduplicate entity lists
        for k in all_entities:
            all_entities[k] = list(dict.fromkeys(all_entities[k]))[:100]

        # Build report data
        data = ReportData(
            title=args.title or "ASTREX — Отчёт по индексу",
            subtitle=f"Файлов в индексе: {index_stats['total_files']}, "
                     f"Размер: {index_stats['total_size_human']}",
            stats={
                'total_files': index_stats['total_files'],
                'total_size': index_stats['total_size_human'],
                'db_size_mb': f"{index_stats['db_size'] / 1024 / 1024:.1f}",
                'entity_links': index_stats['total_entity_links'],
            },
            results=results,
            entities=all_entities,
            sections=[
                ReportSection(
                    title="Расширения",
                    data=index_stats.get('by_extension', [])
                ),
            ],
        )

        if output.suffix == '.html':
            generate_html_report(data, str(output))
        elif output.suffix == '.csv':
            generate_csv_report(data, str(output))
        elif output.suffix == '.xlsx':
            from reporting import generate_excel_report
            generate_excel_report(data, str(output))
        elif output.suffix == '.pdf':
            from reporting import generate_pdf_report
            generate_pdf_report(data, str(output))
        else:
            log_error(f"Unsupported format: {output.suffix}. Use .html, .csv, .xlsx, or .pdf")
            return 1

        log_info(f"Report saved: {output}")
        return 0
    except Exception as e:
        log_error(f"Report failed: {e}")
        return 1


def cmd_plugins(args):
    """Manage plugins"""
    log_info(f"ASTREX v{VERSION} — Plugins")
    try:
        from core.plugins import plugin_manager
        plugins = plugin_manager.discover()

        if args.action == 'list':
            if not plugins:
                print("Нет плагинов")
            for p in plugins:
                status = "✓" if p.loaded else "✗"
                print(f"  [{status}] {p.name} v{p.version} — {p.description}")
        elif args.action == 'load':
            count = plugin_manager.load_all()
            print(f"Загружено: {count} плагинов")
        return 0
    except Exception as e:
        log_error(f"Plugins failed: {e}")
        return 1


def cmd_graph(args):
    """Analyze entity graph"""
    log_info(f"ASTREX v{VERSION} — Graph Analysis")
    try:
        from core import file_index, Graph, GraphBuilder, GraphAnalyzer

        builder = GraphBuilder()

        # Build graph from indexed entities
        all_files = file_index.search_fts('*', limit=10000)
        for row in all_files:
            entities = {}
            if row.get('entities_json'):
                try:
                    entities = json.loads(row['entities_json'])
                except Exception:
                    continue
            if entities:
                text = row.get('extracted_text', '') or ''
                builder.build_from_entities(entities, text[:5000], file_path=row.get('path', ''))

        graph = builder.get_graph()
        analyzer = GraphAnalyzer(graph)

        if args.format == 'json':
            stats = analyzer.get_statistics()
            print(json.dumps(stats, ensure_ascii=False, indent=2))
        else:
            stats = analyzer.get_statistics()
            print(f"\nГраф: {stats['node_count']} узлов, {stats['edge_count']} рёбер")
            print(f"Плотность: {stats['density']:.4f}")
            print(f"Сообществ: {stats['communities']}")
            print(f"Типы узлов: {stats['node_types']}")

            if stats.get('pagerank_top5'):
                print(f"\nPageRank Top-5:")
                for item in stats['pagerank_top5']:
                    print(f"  {item['label']}: {item['score']:.4f}")

            # Betweenness top-5
            bc = analyzer.betweenness_centrality()
            if bc:
                top_bc = sorted(bc.items(), key=lambda x: -x[1])[:5]
                print(f"\nBetweenness Top-5:")
                for nid, score in top_bc:
                    label = graph.nodes[nid].label if graph.nodes.get(nid) else nid
                    print(f"  {label}: {score:.4f}")

            # Bridges
            bridges = analyzer.get_bridges()
            if bridges:
                print(f"\nМосты (критические связи): {len(bridges)}")
                for edge in bridges[:5]:
                    src = graph.nodes.get(edge.source)
                    tgt = graph.nodes.get(edge.target)
                    print(f"  {src.label if src else edge.source} <-> {tgt.label if tgt else edge.target}")

        return 0
    except Exception as e:
        log_error(f"Graph analysis failed: {e}")
        return 1


def cmd_web(args):
    """Start web server"""
    log_info(f"ASTREX v{VERSION} — Web Server")
    try:
        from web.api import run_server
        run_server(
            host=args.host,
            port=args.port,
            debug=args.debug
        )
        return 0
    except ImportError:
        log_error("FastAPI not installed. Run: pip install fastapi uvicorn")
        return 1
    except Exception as e:
        log_error(f"Web server failed: {e}")
        return 1


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        prog='astrex',
        description=f'ASTREX v{VERSION} — Intelligence System',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  astrex scan "Иванов" /data/documents
  astrex scan "договор" ./files --format json --limit 50
  astrex search "Газпром" --limit 20
  astrex index /data/archive
  astrex status
  astrex export graph.gexf
        """
    )
    
    parser.add_argument('--version', action='version', version=f'ASTREX v{VERSION}')
    
    subparsers = parser.add_subparsers(dest='command', help='Commands')
    
    # Scan command
    scan_parser = subparsers.add_parser('scan', help='Full scan with indexing and NLP')
    scan_parser.add_argument('query', help='Search query')
    scan_parser.add_argument('folder', help='Folder to scan')
    scan_parser.add_argument('--format', choices=['text', 'json'], default='text', help='Output format')
    scan_parser.add_argument('--limit', type=int, default=100, help='Max results')
    scan_parser.add_argument('--min-score', type=float, default=0.1, help='Minimum relevance score')
    scan_parser.add_argument('--workers', type=int, help='Number of worker threads')
    scan_parser.add_argument('--no-index', action='store_true', help='Disable index caching')
    scan_parser.add_argument('--no-nlp', action='store_true', help='Disable NLP analysis')
    scan_parser.add_argument('--no-fuzzy', action='store_true', help='Disable fuzzy search')
    
    # Search command
    search_parser = subparsers.add_parser('search', help='Quick search in index')
    search_parser.add_argument('query', help='Search query')
    search_parser.add_argument('--format', choices=['text', 'json'], default='text', help='Output format')
    search_parser.add_argument('--limit', type=int, default=50, help='Max results')
    search_parser.add_argument('--min-score', type=float, default=0.1, help='Minimum relevance score')
    
    # Index command
    index_parser = subparsers.add_parser('index', help='Build or update index')
    index_parser.add_argument('folder', help='Folder to index')
    index_parser.add_argument('--cleanup', action='store_true', help='Remove stale entries for deleted files')
    
    # Status command
    subparsers.add_parser('status', help='Show system status')
    
    # Export command
    export_parser = subparsers.add_parser('export', help='Export graph or results')
    export_parser.add_argument('output', help='Output file (.json, .graphml, .gexf)')

    # Ingest command (RAG)
    ingest_parser = subparsers.add_parser('ingest', help='Ingest documents into RAG vector store')
    ingest_parser.add_argument('folder', help='Folder to ingest')
    ingest_parser.add_argument('--limit', type=int, default=5000, help='Max files')

    # Graph command
    graph_parser = subparsers.add_parser('graph', help='Analyze entity graph')
    graph_parser.add_argument('--format', choices=['text', 'json'], default='text')

    # Web command
    web_parser = subparsers.add_parser('web', help='Start web server')
    web_parser.add_argument('--host', default=None, help='Server host (default: 127.0.0.1)')
    web_parser.add_argument('--port', type=int, default=None, help='Server port (default: 8080)')
    web_parser.add_argument('--debug', action='store_true', default=None, help='Enable debug mode')

    # Ask command (RAG)
    ask_parser = subparsers.add_parser('ask', help='Ask a question (RAG pipeline)')
    ask_parser.add_argument('question', help='Question to ask')
    ask_parser.add_argument('--top-k', type=int, default=10, help='Number of chunks to retrieve')

    # Classify command
    classify_parser = subparsers.add_parser('classify', help='Classify documents')
    classify_parser.add_argument('folder', help='Folder to classify')
    classify_parser.add_argument('--limit', type=int, default=500, help='Max files')

    # Crypto command
    crypto_parser = subparsers.add_parser('crypto', help='Detect encrypted files')
    crypto_parser.add_argument('folder', help='Folder to scan')
    crypto_parser.add_argument('--limit', type=int, default=100, help='Max files')

    # Dedup command
    dedup_parser = subparsers.add_parser('dedup', help='Find duplicate files')
    dedup_parser.add_argument('folder', help='Folder to scan')
    dedup_parser.add_argument('--limit', type=int, default=1000, help='Max files')
    dedup_parser.add_argument('--threshold', type=float, default=0.8, help='Near-duplicate threshold')

    # Timeline command
    timeline_parser = subparsers.add_parser('timeline', help='Build event timeline')
    timeline_parser.add_argument('folder', help='Folder to analyze')
    timeline_parser.add_argument('--format', choices=['text', 'json'], default='text')
    timeline_parser.add_argument('--limit', type=int, default=500, help='Max files')
    timeline_parser.add_argument('--show', type=int, default=50, help='Max events to display')

    # Report command
    report_parser = subparsers.add_parser('report', help='Generate report')
    report_parser.add_argument('output', help='Output file (.html, .csv, .xlsx, .pdf)')
    report_parser.add_argument('--title', help='Report title')
    report_parser.add_argument('--limit', type=int, default=500, help='Max files to include')

    # Plugins command
    plugins_parser = subparsers.add_parser('plugins', help='Manage plugins')
    plugins_parser.add_argument('action', choices=['list', 'load'], help='Action')

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 0

    commands = {
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
    
    return commands[args.command](args)


if __name__ == '__main__':
    import signal
    import faulthandler

    # Print traceback on SIGSEGV/SIGABRT to stderr so UI can display it
    faulthandler.enable()

    # Handle SIGTERM gracefully (sent by QProcess::terminate)
    def _sigterm_handler(signum, frame):
        emit("error", {"msg": "Scan terminated by user (SIGTERM)"})
        sys.exit(130)

    signal.signal(signal.SIGTERM, _sigterm_handler)

    try:
        sys.exit(main())
    except MemoryError:
        emit("error", {"msg": "Fatal: out of memory. Try scanning fewer files or disable NLP (--no-nlp)."})
        sys.exit(137)
    except Exception as e:
        emit("error", {"msg": f"Fatal error: {type(e).__name__}: {e}"})
        sys.exit(1)
