#!/usr/bin/env python3
"""
ASTREX v3.0 — Graph Analysis
Построение и анализ графов связей между сущностями
"""

import re
from typing import Dict, List, Set, Tuple, Any, Optional
from dataclasses import dataclass, field
from collections import defaultdict, deque
import json


# ═══════════════════════════════════════════════════════════════════════════════
# DATA STRUCTURES
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class Node:
    """Узел графа (сущность)"""
    id: str
    label: str
    type: str
    weight: float = 1.0
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict:
        return {
            'id': self.id,
            'label': self.label,
            'type': self.type,
            'weight': self.weight,
            'metadata': self.metadata
        }


@dataclass
class Edge:
    """Ребро графа (связь)"""
    source: str
    target: str
    weight: float = 1.0
    relation: str = "related"
    context: Optional[str] = None
    file_path: Optional[str] = None
    
    def to_dict(self) -> Dict:
        return {
            'source': self.source,
            'target': self.target,
            'weight': self.weight,
            'relation': self.relation,
            'context': self.context,
            'file_path': self.file_path
        }


@dataclass
class Graph:
    """Граф связей"""
    nodes: Dict[str, Node] = field(default_factory=dict)
    edges: List[Edge] = field(default_factory=list)
    _adjacency: Dict[str, Set[str]] = field(default_factory=lambda: defaultdict(set), repr=False)
    _edge_index: Dict[str, List['Edge']] = field(default_factory=lambda: defaultdict(list), repr=False)

    def add_node(self, id: str, label: str, type: str, **kwargs) -> Node:
        """Добавить или обновить узел"""
        if id in self.nodes:
            node = self.nodes[id]
            node.weight += kwargs.get('weight', 1.0)
            node.metadata.update(kwargs.get('metadata', {}))
        else:
            node = Node(
                id=id, 
                label=label, 
                type=type,
                weight=kwargs.get('weight', 1.0),
                metadata=kwargs.get('metadata', {})
            )
            self.nodes[id] = node
        return node
    
    def add_edge(
        self, 
        source: str, 
        target: str, 
        weight: float = 1.0,
        relation: str = "related",
        context: str = None,
        file_path: str = None
    ) -> Edge:
        """Добавить ребро"""
        edge = Edge(
            source=source,
            target=target,
            weight=weight,
            relation=relation,
            context=context,
            file_path=file_path
        )
        self.edges.append(edge)
        self._adjacency[source].add(target)
        self._adjacency[target].add(source)
        self._edge_index[source].append(edge)
        self._edge_index[target].append(edge)
        return edge

    def get_neighbors(self, node_id: str) -> List[str]:
        """Получить соседей узла — O(1) через adjacency list"""
        return list(self._adjacency.get(node_id, set()))
    
    def get_node_edges(self, node_id: str) -> List[Edge]:
        """Получить все рёбра узла — O(1) через edge index"""
        return self._edge_index.get(node_id, [])
    
    def merge(self, other: 'Graph') -> None:
        """Объединить с другим графом"""
        for node in other.nodes.values():
            self.add_node(node.id, node.label, node.type, 
                         weight=node.weight, metadata=node.metadata)
        for edge in other.edges:
            self.add_edge(edge.source, edge.target, edge.weight,
                         edge.relation, edge.context, edge.file_path)
    
    def to_dict(self) -> Dict:
        """Сериализация в dict"""
        return {
            'nodes': [n.to_dict() for n in self.nodes.values()],
            'edges': [e.to_dict() for e in self.edges]
        }
    
    def to_json(self) -> str:
        """Сериализация в JSON"""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)
    
    def to_d3_format(self) -> Dict:
        """Формат для D3.js визуализации"""
        return {
            'nodes': [
                {
                    'id': n.id,
                    'label': n.label,
                    'group': n.type,
                    'size': n.weight
                }
                for n in self.nodes.values()
            ],
            'links': [
                {
                    'source': e.source,
                    'target': e.target,
                    'value': e.weight
                }
                for e in self.edges
            ]
        }
    
    def to_cytoscape_format(self) -> List[Dict]:
        """Формат для Cytoscape.js"""
        elements = []
        
        for node in self.nodes.values():
            elements.append({
                'data': {
                    'id': node.id,
                    'label': node.label,
                    'type': node.type,
                    'weight': node.weight
                }
            })
        
        for i, edge in enumerate(self.edges):
            elements.append({
                'data': {
                    'id': f'edge_{i}',
                    'source': edge.source,
                    'target': edge.target,
                    'weight': edge.weight,
                    'relation': edge.relation
                }
            })
        
        return elements


# ═══════════════════════════════════════════════════════════════════════════════
# GRAPH BUILDER
# ═══════════════════════════════════════════════════════════════════════════════

class GraphBuilder:
    """Построение графа связей из текста и сущностей"""
    
    # Типы связей по соседству сущностей
    PROXIMITY_WINDOW = 200  # символов
    
    # Паттерны связей
    RELATION_PATTERNS = {
        'works_for': [
            re.compile(r'(.+?)\s+(?:работает|трудится|служит)\s+в\s+(.+)', re.IGNORECASE),
            re.compile(r'(.+?)\s+[-–—]\s+(?:директор|руководитель|глава|начальник|сотрудник)\s+(.+)', re.IGNORECASE),
            re.compile(r'(?:директор|руководитель|глава|начальник)\s+(.+?)\s+(.+)', re.IGNORECASE),
        ],
        'located_in': [
            re.compile(r'(.+?)\s+(?:находится|расположен[ао]?|базируется)\s+в\s+(.+)', re.IGNORECASE),
            re.compile(r'(.+?)\s+\((.+?)\)', re.IGNORECASE),  # ООО "Рога" (Москва)
        ],
        'transaction': [
            re.compile(r'(.+?)\s+(?:перевёл|перечислил|заплатил|получил|выплатил)\s+(.+)', re.IGNORECASE),
            re.compile(r'(?:сумм[аы]|платёж|оплата|перевод)\s+(?:от|для|в пользу)\s+(.+)', re.IGNORECASE),
        ],
        'meeting': [
            re.compile(r'(.+?)\s+(?:встретился|встреча)\s+с\s+(.+)', re.IGNORECASE),
            re.compile(r'(?:переговоры|совещание)\s+(.+?)\s+(?:и|с)\s+(.+)', re.IGNORECASE),
        ],
        'agreement': [
            re.compile(r'(?:договор|контракт|соглашение)\s+(?:между|с)\s+(.+?)\s+и\s+(.+)', re.IGNORECASE),
        ]
    }
    
    def __init__(self):
        self.graph = Graph()
    
    def build_from_entities(
        self,
        entities: Dict[str, List[str]],
        text: str,
        file_path: str = None
    ) -> Graph:
        """Построить граф из извлечённых сущностей"""
        
        # Add nodes
        for entity_type, entity_list in entities.items():
            for entity in entity_list:
                node_id = self._make_node_id(entity, entity_type)
                self.graph.add_node(
                    id=node_id,
                    label=entity,
                    type=entity_type
                )
        
        # Find connections by proximity
        self._find_proximity_connections(entities, text, file_path)
        
        # Find connections by patterns
        self._find_pattern_connections(text, file_path)
        
        return self.graph
    
    def _make_node_id(self, entity: str, entity_type: str) -> str:
        """Создать уникальный ID для узла"""
        # Normalize
        normalized = entity.lower().strip()
        normalized = re.sub(r'\s+', '_', normalized)
        normalized = re.sub(r'[^\w_]', '', normalized)
        return f"{entity_type.lower()}_{normalized}"
    
    def _find_proximity_connections(
        self,
        entities: Dict[str, List[str]],
        text: str,
        file_path: str
    ) -> None:
        """Найти связи по близости в тексте"""

        # Build all entity positions in a single pass using str.find
        # Collect unique entity strings first to avoid redundant searches
        positions: List[Tuple[int, str, str]] = []  # (pos, entity, type)

        text_lower = text.lower()

        # Deduplicate entity searches: same string may appear under different types
        seen_searches: Dict[str, List[Tuple[str, str]]] = {}  # entity_lower -> [(entity, type)]
        for entity_type, entity_list in entities.items():
            for entity in entity_list:
                entity_lower = entity.lower()
                seen_searches.setdefault(entity_lower, []).append((entity, entity_type))

        for entity_lower, entity_type_pairs in seen_searches.items():
            start = 0
            while True:
                pos = text_lower.find(entity_lower, start)
                if pos == -1:
                    break
                for entity, entity_type in entity_type_pairs:
                    positions.append((pos, entity, entity_type))
                start = pos + 1
        
        # Sort by position
        positions.sort(key=lambda x: x[0])
        
        # Find connections between nearby entities
        for i, (pos1, ent1, type1) in enumerate(positions):
            for pos2, ent2, type2 in positions[i+1:]:
                # Check if within proximity window
                if pos2 - pos1 > self.PROXIMITY_WINDOW:
                    break
                
                # Don't connect same type unless it's different entities
                if type1 == type2 and ent1.lower() == ent2.lower():
                    continue
                
                # Extract context
                context_start = max(0, pos1 - 50)
                context_end = min(len(text), pos2 + len(ent2) + 50)
                context = text[context_start:context_end]
                
                # Add edge
                node_id1 = self._make_node_id(ent1, type1)
                node_id2 = self._make_node_id(ent2, type2)
                
                self.graph.add_edge(
                    source=node_id1,
                    target=node_id2,
                    weight=1.0,
                    relation='proximity',
                    context=context[:200],
                    file_path=file_path
                )
    
    def _find_pattern_connections(self, text: str, file_path: str) -> None:
        """Найти связи по паттернам"""
        
        for relation_type, patterns in self.RELATION_PATTERNS.items():
            for pattern in patterns:
                for match in pattern.finditer(text):
                    groups = match.groups()
                    if len(groups) >= 2:
                        entity1 = groups[0].strip()
                        entity2 = groups[1].strip()
                        
                        if entity1 and entity2 and len(entity1) > 2 and len(entity2) > 2:
                            # Guess entity types
                            type1 = self._guess_entity_type(entity1)
                            type2 = self._guess_entity_type(entity2)
                            
                            node_id1 = self._make_node_id(entity1, type1)
                            node_id2 = self._make_node_id(entity2, type2)
                            
                            # Add nodes if not exist
                            self.graph.add_node(node_id1, entity1, type1)
                            self.graph.add_node(node_id2, entity2, type2)
                            
                            # Add edge
                            self.graph.add_edge(
                                source=node_id1,
                                target=node_id2,
                                weight=2.0,  # Pattern matches are more reliable
                                relation=relation_type,
                                context=match.group(0)[:200],
                                file_path=file_path
                            )
    
    def _guess_entity_type(self, entity: str) -> str:
        """Угадать тип сущности"""
        entity_lower = entity.lower()
        
        # Organization patterns
        if any(x in entity_lower for x in ['ооо', 'оао', 'зао', 'пао', 'ао', 'ип', 'банк', 'компания']):
            return 'ORGANIZATIONS'
        
        # Location patterns
        if any(x in entity_lower for x in ['город', 'область', 'улица', 'район', 'г.', 'ул.']):
            return 'LOCATIONS'
        
        # Money patterns
        if any(x in entity_lower for x in ['руб', 'рублей', '$', '€', 'млн', 'млрд']):
            return 'MONEY'
        
        # Date patterns
        if re.search(r'\d{1,2}[\.\/]\d{1,2}[\.\/]\d{2,4}', entity):
            return 'DATES'
        
        # Default to UNKNOWN
        return 'UNKNOWN'
    
    def get_graph(self) -> Graph:
        return self.graph
    
    def clear(self) -> None:
        self.graph = Graph()


# ═══════════════════════════════════════════════════════════════════════════════
# GRAPH ANALYZER
# ═══════════════════════════════════════════════════════════════════════════════

class GraphAnalyzer:
    """Анализ графа связей"""
    
    def __init__(self, graph: Graph):
        self.graph = graph
    
    def get_central_nodes(self, limit: int = 10) -> List[Tuple[Node, float]]:
        """Найти наиболее связанные узлы (degree centrality)"""
        degree: Dict[str, int] = defaultdict(int)
        
        for edge in self.graph.edges:
            degree[edge.source] += 1
            degree[edge.target] += 1
        
        ranked = sorted(
            [(self.graph.nodes[nid], count) for nid, count in degree.items() if nid in self.graph.nodes],
            key=lambda x: -x[1]
        )
        
        return ranked[:limit]
    
    def get_communities(self) -> List[Set[str]]:
        """Найти сообщества (connected components)"""
        if not self.graph.nodes:
            return []
        
        visited: Set[str] = set()
        communities: List[Set[str]] = []
        
        for node_id in self.graph.nodes:
            if node_id in visited:
                continue
            
            # BFS
            community: Set[str] = set()
            queue = deque([node_id])

            while queue:
                current = queue.popleft()
                if current in visited:
                    continue
                
                visited.add(current)
                community.add(current)
                
                for neighbor in self.graph.get_neighbors(current):
                    if neighbor not in visited:
                        queue.append(neighbor)
            
            if community:
                communities.append(community)
        
        return sorted(communities, key=len, reverse=True)
    
    def get_shortest_path(self, source: str, target: str) -> List[str]:
        """Найти кратчайший путь между узлами"""
        if source not in self.graph.nodes or target not in self.graph.nodes:
            return []
        
        if source == target:
            return [source]
        
        # BFS
        visited: Set[str] = {source}
        queue = deque([[source]])

        while queue:
            path = queue.popleft()
            current = path[-1]
            
            for neighbor in self.graph.get_neighbors(current):
                if neighbor == target:
                    return path + [neighbor]
                
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append(path + [neighbor])
        
        return []
    
    def get_subgraph(self, node_id: str, depth: int = 2) -> Graph:
        """Получить подграф вокруг узла"""
        if node_id not in self.graph.nodes:
            return Graph()
        
        subgraph = Graph()
        visited: Set[str] = set()
        queue = deque([(node_id, 0)])

        while queue:
            current, current_depth = queue.popleft()
            
            if current in visited or current_depth > depth:
                continue
            
            visited.add(current)
            
            if current in self.graph.nodes:
                node = self.graph.nodes[current]
                subgraph.add_node(node.id, node.label, node.type, weight=node.weight)
            
            if current_depth < depth:
                for neighbor in self.graph.get_neighbors(current):
                    if neighbor not in visited:
                        queue.append((neighbor, current_depth + 1))
        
        # Add edges
        for edge in self.graph.edges:
            if edge.source in visited and edge.target in visited:
                subgraph.add_edge(
                    edge.source, edge.target, edge.weight,
                    edge.relation, edge.context, edge.file_path
                )
        
        return subgraph
    
    def _count_node_types(self) -> Dict[str, int]:
        """Подсчёт узлов по типам"""
        counts: Dict[str, int] = defaultdict(int)
        for node in self.graph.nodes.values():
            counts[node.type] += 1
        return counts

    def pagerank(self, damping=0.85, iterations=100, tolerance=1e-6) -> Dict[str, float]:
        """
        Classic PageRank algorithm

        Args:
            damping: Damping factor (default 0.85)
            iterations: Maximum number of iterations (default 100)
            tolerance: Convergence tolerance (default 1e-6)

        Returns:
            Dict mapping node_id to PageRank score
        """
        if not self.graph.nodes:
            return {}

        N = len(self.graph.nodes)

        # Initialize PageRank scores
        pagerank_scores = {node_id: 1.0 / N for node_id in self.graph.nodes}

        # Calculate out-degrees for each node
        out_degree = defaultdict(int)
        for node_id in self.graph.nodes:
            neighbors = self.graph._adjacency.get(node_id, set())
            out_degree[node_id] = len(neighbors) if neighbors else 0

        # Iterate until convergence or max iterations
        for iteration in range(iterations):
            new_scores = {}
            max_change = 0.0

            for node_id in self.graph.nodes:
                # Base rank
                rank = (1 - damping) / N

                # Add contributions from neighbors (use adjacency — O(degree) not O(n))
                for neighbor_id in self.graph._adjacency.get(node_id, set()):
                    degree = out_degree[neighbor_id]
                    if degree > 0:
                        rank += damping * pagerank_scores[neighbor_id] / degree

                new_scores[node_id] = rank
                max_change = max(max_change, abs(rank - pagerank_scores[node_id]))

            pagerank_scores = new_scores

            # Check for convergence
            if max_change < tolerance:
                break

        return pagerank_scores

    def betweenness_centrality(self) -> Dict[str, float]:
        """
        Brandes algorithm for betweenness centrality

        Returns:
            Dict mapping node_id to betweenness centrality score
        """
        if not self.graph.nodes:
            return {}

        betweenness = {node_id: 0.0 for node_id in self.graph.nodes}

        # For each source node
        for source in self.graph.nodes:
            # BFS to find shortest paths
            stack = []
            predecessors = {node_id: [] for node_id in self.graph.nodes}
            sigma = {node_id: 0 for node_id in self.graph.nodes}
            sigma[source] = 1
            distance = {node_id: -1 for node_id in self.graph.nodes}
            distance[source] = 0

            queue = deque([source])

            while queue:
                current = queue.popleft()
                stack.append(current)

                for neighbor in self.graph.get_neighbors(current):
                    # First time we see this neighbor
                    if distance[neighbor] < 0:
                        queue.append(neighbor)
                        distance[neighbor] = distance[current] + 1

                    # Shortest path to neighbor via current
                    if distance[neighbor] == distance[current] + 1:
                        sigma[neighbor] += sigma[current]
                        predecessors[neighbor].append(current)

            # Accumulate dependency scores
            delta = {node_id: 0.0 for node_id in self.graph.nodes}

            while stack:
                w = stack.pop()
                for v in predecessors[w]:
                    delta[v] += (sigma[v] / sigma[w]) * (1 + delta[w])

                if w != source:
                    betweenness[w] += delta[w]

        # Normalize for undirected graph
        n = len(self.graph.nodes)
        if n > 2:
            normalization = 2.0 / ((n - 1) * (n - 2))
            for node_id in betweenness:
                betweenness[node_id] *= normalization

        return betweenness

    def closeness_centrality(self) -> Dict[str, float]:
        """
        Closeness centrality for each node

        Returns:
            Dict mapping node_id to closeness centrality score
        """
        if not self.graph.nodes:
            return {}

        closeness = {}

        for node_id in self.graph.nodes:
            # BFS to find shortest path distances
            distances = {}
            visited = {node_id}
            queue = deque([(node_id, 0)])

            while queue:
                current, dist = queue.popleft()
                distances[current] = dist

                for neighbor in self.graph.get_neighbors(current):
                    if neighbor not in visited:
                        visited.add(neighbor)
                        queue.append((neighbor, dist + 1))

            # Calculate closeness
            reachable_count = len(distances) - 1  # Exclude the node itself
            sum_distances = sum(d for d in distances.values() if d > 0)

            if sum_distances > 0 and reachable_count > 0:
                closeness[node_id] = reachable_count / sum_distances
            else:
                closeness[node_id] = 0.0

        return closeness

    def get_bridges(self) -> List[Edge]:
        """
        Find bridge edges (whose removal disconnects the graph)
        Uses Tarjan's bridge-finding algorithm

        Returns:
            List of bridge edges
        """
        if not self.graph.nodes:
            return []

        # Build edge lookup for quick access
        edge_lookup = {}
        for edge in self.graph.edges:
            key1 = (edge.source, edge.target)
            key2 = (edge.target, edge.source)
            if key1 not in edge_lookup:
                edge_lookup[key1] = edge
            if key2 not in edge_lookup:
                edge_lookup[key2] = edge

        # Iterative Tarjan's algorithm (avoids stack overflow on deep graphs)
        visited = set()
        disc = {}   # Discovery time
        low = {}    # Lowest discovery time reachable
        parent = {}
        bridges = []
        timer = 0

        for start_node in self.graph.nodes:
            if start_node in visited:
                continue

            parent[start_node] = None
            # Stack stores (node, iterator_over_neighbors)
            stack = [(start_node, iter(self.graph.get_neighbors(start_node)))]
            visited.add(start_node)
            disc[start_node] = low[start_node] = timer
            timer += 1

            while stack:
                u, neighbors_iter = stack[-1]
                try:
                    v = next(neighbors_iter)
                    if v not in visited:
                        parent[v] = u
                        visited.add(v)
                        disc[v] = low[v] = timer
                        timer += 1
                        stack.append((v, iter(self.graph.get_neighbors(v))))
                    elif v != parent.get(u):
                        # Back edge
                        low[u] = min(low[u], disc[v])
                except StopIteration:
                    stack.pop()
                    if stack:
                        # Returning from v=u back to its parent
                        p = parent.get(u)
                        if p is not None:
                            low[p] = min(low[p], low[u])
                            # If lowest reachable from u is below parent, then p-u is a bridge
                            if low[u] > disc[p]:
                                edge = edge_lookup.get((p, u)) or edge_lookup.get((u, p))
                                if edge:
                                    bridges.append(edge)

        return bridges

    def get_statistics(self) -> Dict[str, Any]:
        """Получить статистику графа"""
        degree: Dict[str, int] = defaultdict(int)
        for edge in self.graph.edges:
            degree[edge.source] += 1
            degree[edge.target] += 1

        degrees = list(degree.values()) if degree else [0]

        # Calculate density
        node_count = len(self.graph.nodes)
        edge_count = len(self.graph.edges)
        if node_count > 1:
            density = (2.0 * edge_count) / (node_count * (node_count - 1))
        else:
            density = 0.0

        # Calculate PageRank and get top 5
        pagerank_scores = self.pagerank()
        pagerank_top5 = []
        if pagerank_scores:
            sorted_pr = sorted(pagerank_scores.items(), key=lambda x: -x[1])[:5]
            pagerank_top5 = [
                {'node_id': node_id, 'score': score, 'label': self.graph.nodes[node_id].label}
                for node_id, score in sorted_pr
            ]

        return {
            'node_count': node_count,
            'edge_count': edge_count,
            'avg_degree': sum(degrees) / len(degrees) if degrees else 0,
            'max_degree': max(degrees) if degrees else 0,
            'density': density,
            'communities': len(self.get_communities()),
            'node_types': dict(self._count_node_types()),
            'pagerank_top5': pagerank_top5
        }


# ═══════════════════════════════════════════════════════════════════════════════
# VISUALIZATION EXPORT
# ═══════════════════════════════════════════════════════════════════════════════

def export_to_graphml(graph: Graph, path: str) -> None:
    """Экспорт в GraphML формат"""
    import xml.etree.ElementTree as ET
    
    root = ET.Element('graphml')
    root.set('xmlns', 'http://graphml.graphdrawing.org/xmlns')
    
    # Key definitions
    ET.SubElement(root, 'key', id='label', for_='node', attr_name='label', attr_type='string')
    ET.SubElement(root, 'key', id='type', for_='node', attr_name='type', attr_type='string')
    ET.SubElement(root, 'key', id='weight', for_='edge', attr_name='weight', attr_type='double')
    
    g = ET.SubElement(root, 'graph', edgedefault='undirected')
    
    # Nodes
    for node in graph.nodes.values():
        n = ET.SubElement(g, 'node', id=node.id)
        ET.SubElement(n, 'data', key='label').text = node.label
        ET.SubElement(n, 'data', key='type').text = node.type
    
    # Edges
    for i, edge in enumerate(graph.edges):
        e = ET.SubElement(g, 'edge', id=f'e{i}', source=edge.source, target=edge.target)
        ET.SubElement(e, 'data', key='weight').text = str(edge.weight)
    
    tree = ET.ElementTree(root)
    tree.write(path, encoding='utf-8', xml_declaration=True)


def export_to_gexf(graph: Graph, path: str) -> None:
    """Экспорт в GEXF формат (для Gephi)"""
    import xml.etree.ElementTree as ET
    
    root = ET.Element('gexf')
    root.set('xmlns', 'http://www.gexf.net/1.2draft')
    root.set('version', '1.2')
    
    g = ET.SubElement(root, 'graph', defaultedgetype='undirected')
    
    # Attributes
    attrs = ET.SubElement(g, 'attributes', {'class': 'node'})
    ET.SubElement(attrs, 'attribute', id='type', title='type', type='string')
    
    # Nodes
    nodes = ET.SubElement(g, 'nodes')
    for node in graph.nodes.values():
        n = ET.SubElement(nodes, 'node', id=node.id, label=node.label)
        attvalues = ET.SubElement(n, 'attvalues')
        ET.SubElement(attvalues, 'attvalue', {'for': 'type', 'value': node.type})
    
    # Edges
    edges = ET.SubElement(g, 'edges')
    for i, edge in enumerate(graph.edges):
        ET.SubElement(edges, 'edge', 
                     id=str(i), source=edge.source, target=edge.target,
                     weight=str(edge.weight))
    
    tree = ET.ElementTree(root)
    tree.write(path, encoding='utf-8', xml_declaration=True)


__all__ = [
    'Node', 'Edge', 'Graph', 'GraphBuilder', 'GraphAnalyzer',
    'export_to_graphml', 'export_to_gexf'
]
