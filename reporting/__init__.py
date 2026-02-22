#!/usr/bin/env python3
"""
ASTREX v3.0 — Reporting Package
Генерация отчётов: PDF, Excel, HTML
"""

from .reports import (
    generate_html_report, generate_csv_report,
    generate_excel_report, generate_pdf_report,
    ReportData, ReportSection
)

__all__ = [
    'generate_html_report', 'generate_csv_report',
    'generate_excel_report', 'generate_pdf_report',
    'ReportData', 'ReportSection'
]
