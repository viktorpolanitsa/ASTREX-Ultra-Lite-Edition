#!/usr/bin/env python3
"""
ASTREX v3.0 — Network Extractors
PCAP, PCAPNG, Netflow parsers for network traffic analysis
"""

import struct
import string
from pathlib import Path
from typing import Optional, Dict, List, Set, Tuple
from collections import Counter, defaultdict

from .base import BaseExtractor, ExtractionResult, registry
from core.logging_setup import get_logger

logger = get_logger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# PCAP EXTRACTOR
# ═══════════════════════════════════════════════════════════════════════════════

@registry.register
class PcapExtractor(BaseExtractor):
    """Извлечение данных из PCAP/PCAPNG файлов"""

    extensions = ['.pcap', '.pcapng', '.cap']
    priority = 10

    _available: Optional[bool] = None

    @classmethod
    def is_available(cls) -> bool:
        if cls._available is None:
            try:
                from scapy.all import rdpcap, IP, TCP, UDP, DNS, Raw
                cls._available = True
            except ImportError:
                cls._available = False
                logger.warning("scapy not available - PCAP extraction disabled")
        return cls._available

    @classmethod
    def extract(cls, path: Path) -> ExtractionResult:
        try:
            from scapy.all import rdpcap, IP, TCP, UDP, DNS, Raw, DNSQR

            logger.info(f"Reading PCAP file: {path}")

            # Read packets (limit to first 10000)
            try:
                packets = rdpcap(str(path), count=10000)
            except Exception as e:
                return ExtractionResult(error=f"Failed to read PCAP: {e}")

            if not packets:
                return ExtractionResult(error="No packets found in PCAP file")

            # Data structures for analysis
            ip_conversations: Dict[Tuple[str, str], int] = Counter()
            dns_queries: Set[str] = set()
            http_requests: List[str] = []
            port_stats: Counter = Counter()
            unique_ips: Set[str] = set()
            timestamps = []

            # Process packets
            for pkt in packets:
                # Extract timestamp
                if hasattr(pkt, 'time'):
                    timestamps.append(pkt.time)

                # IP layer analysis
                if IP in pkt:
                    src = pkt[IP].src
                    dst = pkt[IP].dst
                    unique_ips.add(src)
                    unique_ips.add(dst)
                    ip_conversations[(src, dst)] += 1

                    # TCP/UDP port statistics
                    if TCP in pkt:
                        port_stats[pkt[TCP].sport] += 1
                        port_stats[pkt[TCP].dport] += 1
                    elif UDP in pkt:
                        port_stats[pkt[UDP].sport] += 1
                        port_stats[pkt[UDP].dport] += 1

                # DNS query extraction
                if DNS in pkt and pkt.haslayer(DNSQR):
                    try:
                        query_name = pkt[DNSQR].qname.decode('utf-8', errors='ignore').rstrip('.')
                        if query_name:
                            dns_queries.add(query_name)
                    except Exception:
                        pass

                # HTTP request extraction from Raw layer
                if Raw in pkt:
                    try:
                        raw_data = pkt[Raw].load.decode('utf-8', errors='ignore')
                        # Look for HTTP requests
                        if raw_data.startswith('GET ') or raw_data.startswith('POST ') or \
                           raw_data.startswith('PUT ') or raw_data.startswith('HEAD '):
                            lines = raw_data.split('\r\n')
                            request_line = lines[0] if lines else ''
                            host = ''
                            # Extract Host header
                            for line in lines[1:]:
                                if line.lower().startswith('host:'):
                                    host = line.split(':', 1)[1].strip()
                                    break

                            if request_line:
                                if host:
                                    http_requests.append(f"{host} - {request_line}")
                                else:
                                    http_requests.append(request_line)
                    except Exception:
                        pass

            # Build structured text output
            text_parts = []

            # IP Conversations
            text_parts.append("=== IP Conversations ===")
            sorted_conversations = sorted(
                ip_conversations.items(),
                key=lambda x: x[1],
                reverse=True
            )
            for (src, dst), count in sorted_conversations[:50]:  # Top 50
                text_parts.append(f"{src} -> {dst}: {count} packets")

            # DNS Queries
            if dns_queries:
                text_parts.append("\n=== DNS Queries ===")
                for query in sorted(dns_queries)[:100]:  # Top 100
                    text_parts.append(query)

            # HTTP Requests
            if http_requests:
                text_parts.append("\n=== HTTP Requests ===")
                for req in http_requests[:50]:  # Top 50
                    text_parts.append(req)

            # Port Statistics
            text_parts.append("\n=== Port Statistics ===")
            top_ports = port_stats.most_common(20)
            for port, count in top_ports:
                port_name = cls._get_port_name(port)
                text_parts.append(f"Port {port} ({port_name}): {count} occurrences")

            # Summary
            text_parts.append("\n=== Summary ===")
            text_parts.append(f"Total packets: {len(packets)}")
            text_parts.append(f"Unique IP addresses: {len(unique_ips)}")
            text_parts.append(f"Unique ports: {len(port_stats)}")
            text_parts.append(f"IP conversations: {len(ip_conversations)}")
            text_parts.append(f"DNS queries found: {len(dns_queries)}")
            text_parts.append(f"HTTP requests found: {len(http_requests)}")

            # Time range — cast to float first: scapy pkt.time is Decimal on some platforms
            if timestamps:
                from datetime import datetime
                ts_min = float(min(timestamps))
                ts_max = float(max(timestamps))
                start_time = datetime.fromtimestamp(ts_min)
                end_time = datetime.fromtimestamp(ts_max)
                duration = ts_max - ts_min
                text_parts.append(f"Capture start: {start_time}")
                text_parts.append(f"Capture end: {end_time}")
                text_parts.append(f"Duration: {duration:.2f} seconds")

            text = '\n'.join(text_parts)

            # Build metadata
            metadata = {
                'packet_count': len(packets),
                'unique_ips': len(unique_ips),
                'unique_ports': len(port_stats),
                'dns_queries': list(dns_queries)[:100],  # Limit for metadata
                'ip_conversations': [
                    {'src': src, 'dst': dst, 'count': count}
                    for (src, dst), count in sorted_conversations[:50]
                ],
                'http_requests': http_requests[:50],
                'top_ports': [
                    {'port': port, 'count': count, 'name': cls._get_port_name(port)}
                    for port, count in top_ports
                ]
            }

            if timestamps:
                metadata['capture_start'] = ts_min
                metadata['capture_end'] = ts_max
                metadata['duration_seconds'] = ts_max - ts_min

            logger.info(f"PCAP extraction complete: {len(packets)} packets analyzed")
            return ExtractionResult(text=text, metadata=metadata)

        except Exception as e:
            logger.error(f"PCAP extraction failed: {e}", exc_info=True)
            return ExtractionResult(error=f"PCAP extraction failed: {e}")

    @staticmethod
    def _get_port_name(port: int) -> str:
        """Get common port name"""
        common_ports = {
            20: 'FTP-DATA', 21: 'FTP', 22: 'SSH', 23: 'Telnet',
            25: 'SMTP', 53: 'DNS', 67: 'DHCP', 68: 'DHCP',
            69: 'TFTP', 80: 'HTTP', 110: 'POP3', 119: 'NNTP',
            123: 'NTP', 143: 'IMAP', 161: 'SNMP', 162: 'SNMP',
            389: 'LDAP', 443: 'HTTPS', 445: 'SMB', 465: 'SMTPS',
            514: 'Syslog', 587: 'SMTP', 636: 'LDAPS', 993: 'IMAPS',
            995: 'POP3S', 1433: 'MSSQL', 1521: 'Oracle', 3306: 'MySQL',
            3389: 'RDP', 5432: 'PostgreSQL', 5900: 'VNC', 6379: 'Redis',
            8080: 'HTTP-Alt', 8443: 'HTTPS-Alt', 27017: 'MongoDB'
        }
        return common_ports.get(port, 'Unknown')


# ═══════════════════════════════════════════════════════════════════════════════
# NETFLOW EXTRACTOR
# ═══════════════════════════════════════════════════════════════════════════════

@registry.register
class NetflowExtractor(BaseExtractor):
    """Извлечение данных из Netflow файлов (.nfcapd, .flow)"""

    extensions = ['.nfcapd', '.flow']
    priority = 15

    @classmethod
    def is_available(cls) -> bool:
        return True  # Uses struct for basic parsing

    @classmethod
    def extract(cls, path: Path) -> ExtractionResult:
        try:
            file_size = path.stat().st_size

            # Try to read as text first
            text_parts = []
            metadata = {
                'file_size': file_size,
                'format': 'netflow'
            }

            try:
                # Try reading first few KB as text
                with open(path, 'rb') as f:
                    data = f.read(min(file_size, 65536))  # First 64KB

                # Check for magic bytes or known headers
                if data[:4] == b'NFCA' or data[:4] == b'\xa5\xca\x1d\x00':
                    text_parts.append("=== Netflow Capture File ===")
                    text_parts.append(f"File size: {file_size} bytes")

                    # Extract header info if possible
                    try:
                        # Basic nfcapd header structure (simplified)
                        magic = struct.unpack('<I', data[0:4])[0]
                        version = struct.unpack('<H', data[4:6])[0]
                        text_parts.append(f"Magic: 0x{magic:08x}")
                        text_parts.append(f"Version: {version}")
                    except Exception:
                        pass

                # Extract printable strings (like strings command)
                printable_strings = cls._extract_printable_strings(data, min_length=4)

                if printable_strings:
                    text_parts.append("\n=== Extracted Strings ===")
                    # Look for IP-like patterns, domains, etc.
                    for s in printable_strings[:100]:  # Limit to first 100
                        text_parts.append(s)

                    metadata['extracted_strings_count'] = len(printable_strings)

                # Try to identify any text-based flow data
                try:
                    text_data = data.decode('utf-8', errors='ignore')
                    if any(keyword in text_data.lower() for keyword in
                           ['flow', 'source', 'destination', 'port', 'protocol']):
                        text_parts.append("\n=== Flow Data (partial) ===")
                        # Extract lines that look like flow records
                        lines = text_data.split('\n')
                        for line in lines[:50]:
                            if line.strip() and any(c.isalnum() for c in line):
                                text_parts.append(line.strip())
                except Exception:
                    pass

                if not text_parts:
                    text_parts.append("=== Binary Netflow File ===")
                    text_parts.append(f"File size: {file_size} bytes")
                    text_parts.append("Binary format detected - specialized tools required for full analysis")
                    text_parts.append("Suggested tools: nfdump, flow-tools, Wireshark")

                text = '\n'.join(text_parts)
                return ExtractionResult(text=text, metadata=metadata)

            except Exception as e:
                # Fallback: just report metadata
                text = f"=== Netflow File ===\nFile size: {file_size} bytes\nBinary format - use nfdump or flow-tools for analysis"
                metadata['parse_error'] = str(e)
                return ExtractionResult(text=text, metadata=metadata)

        except Exception as e:
            logger.error(f"Netflow extraction failed: {e}", exc_info=True)
            return ExtractionResult(error=f"Netflow extraction failed: {e}")

    @staticmethod
    def _extract_printable_strings(data: bytes, min_length: int = 4) -> List[str]:
        """Extract printable strings from binary data (like strings command).

        Uses re.findall on bytes (C-level) instead of a pure-Python byte loop,
        which is ~100x faster for 64KB inputs.
        """
        import re as _re
        pattern = _re.compile(rb'[ -~]{' + str(min_length).encode() + rb',}')
        return [s.decode('ascii') for s in pattern.findall(data)]


__all__ = ['PcapExtractor', 'NetflowExtractor']
