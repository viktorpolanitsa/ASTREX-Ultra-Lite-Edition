#!/usr/bin/env python3
"""
ASTREX v3.0 — Graph Analysis
Построение и анализ графов связей между сущностями
"""

import copy
import json
import re
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Dict, List, Set, Tuple, Any, Optional

from .encoding import sanitize_xml_text


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
    """Ребро графа (связь). Повторные наблюдения увеличивают weight/count."""
    source: str
    target: str
    weight: float = 1.0
    relation: str = "related"
    context: Optional[str] = None
    file_path: Optional[str] = None
    count: int = 1
    files: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            'source': self.source,
            'target': self.target,
            'weight': self.weight,
            'relation': self.relation,
            'context': self.context,
            'file_path': self.file_path,
            'count': self.count,
            'files': self.files,
        }


class Graph:
    """Неориентированный граф связей без параллельных рёбер и петель.

    Повторное добавление ребра (u, v, relation) не создаёт дубликат,
    а увеличивает его вес и счётчик наблюдений.
    """

    MAX_FILES_PER_EDGE = 20

    def __init__(self, nodes: Optional[Dict[str, Node]] = None, edges: Optional[List[Edge]] = None):
        self.nodes: Dict[str, Node] = {}
        self.edges: List[Edge] = []
        self._adjacency: Dict[str, Set[str]] = defaultdict(set)
        self._edge_index: Dict[str, List[Edge]] = defaultdict(list)
        self._edge_map: Dict[Tuple[str, str, str], Edge] = {}
        for node in (nodes or {}).values():
            self.add_node(node.id, node.label, node.type, weight=node.weight, metadata=node.metadata)
        for e in edges or []:
            self.add_edge(e.source, e.target, e.weight, e.relation, e.context, e.file_path)

    def add_node(self, id: str, label: str, type: str, **kwargs) -> Node:
        """Добавить или обновить узел"""
        metadata = copy.deepcopy(kwargs.get('metadata') or {})
        if id in self.nodes:
            node = self.nodes[id]
            node.weight += kwargs.get('weight', 1.0)
            node.metadata.update(metadata)
        else:
            node = Node(
                id=id,
                label=label,
                type=type,
                weight=kwargs.get('weight', 1.0),
                metadata=metadata
            )
            self.nodes[id] = node
        return node

    @staticmethod
    def _key(source: str, target: str, relation: str) -> Tuple[str, str, str]:
        a, b = (source, target) if source <= target else (target, source)
        return (a, b, relation)

    def add_edge(
        self,
        source: str,
        target: str,
        weight: float = 1.0,
        relation: str = "related",
        context: str = None,
        file_path: str = None
    ) -> Optional[Edge]:
        """Добавить ребро (или усилить существующее). Петли игнорируются."""
        if source == target:
            return None
        key = self._key(source, target, relation)
        edge = self._edge_map.get(key)
        if edge is not None:
            edge.weight += weight
            edge.count += 1
            if file_path and file_path not in edge.files and len(edge.files) < self.MAX_FILES_PER_EDGE:
                edge.files.append(file_path)
            return edge

        edge = Edge(source=source, target=target, weight=weight, relation=relation,
                    context=context, file_path=file_path,
                    files=[file_path] if file_path else [])
        self._edge_map[key] = edge
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
        return list(self._edge_index.get(node_id, []))

    def all_node_ids(self) -> Set[str]:
        """Узлы, включая встречающиеся только в рёбрах."""
        ids = set(self.nodes)
        for e in self.edges:
            ids.add(e.source)
            ids.add(e.target)
        return ids

    def merge(self, other: 'Graph') -> None:
        """Объединить с другим графом"""
        for node in other.nodes.values():
            self.add_node(node.id, node.label, node.type,
                          weight=node.weight, metadata=node.metadata)
        for edge in other.edges:
            merged = self.add_edge(edge.source, edge.target, edge.weight,
                                   edge.relation, edge.context, edge.file_path)
            if merged is not None and edge.count > 1:
                merged.count += edge.count - 1

    def to_dict(self, max_edges: Optional[int] = None) -> Dict:
        """Сериализация в dict (опционально — только самые весомые рёбра)."""
        edges = self.edges
        if max_edges is not None and len(edges) > max_edges:
            edges = sorted(edges, key=lambda e: -e.weight)[:max_edges]
        return {
            'nodes': [n.to_dict() for n in self.nodes.values()],
            'edges': [e.to_dict() for e in edges]
        }

    def to_json(self) -> str:
        """Сериализация в JSON"""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    def to_d3_format(self) -> Dict:
        """Формат для D3.js визуализации"""
        return {
            'nodes': [
                {'id': n.id, 'label': n.label, 'group': n.type, 'size': n.weight}
                for n in self.nodes.values()
            ],
            'links': [
                {'source': e.source, 'target': e.target, 'value': e.weight}
                for e in self.edges
            ]
        }

    def to_cytoscape_format(self) -> List[Dict]:
        """Формат для Cytoscape.js"""
        elements = []
        for node in self.nodes.values():
            elements.append({'data': {
                'id': node.id, 'label': node.label, 'type': node.type, 'weight': node.weight
            }})
        for i, edge in enumerate(self.edges):
            elements.append({'data': {
                'id': f'edge_{i}', 'source': edge.source, 'target': edge.target,
                'weight': edge.weight, 'relation': edge.relation
            }})
        return elements


# ═══════════════════════════════════════════════════════════════════════════════
# GRAPH BUILDER
# ═══════════════════════════════════════════════════════════════════════════════

_SENTENCE_SPLIT = re.compile(r'(?<=[.!?…])\s+|\n+')


class GraphBuilder:
    """Построение графа связей из извлечённых сущностей.

    Узлы — только сущности, найденные NER (а не произвольные куски текста).
    Связи:
    - proximity: сущности встречаются рядом (в пределах PROXIMITY_WINDOW);
    - типизированные (works_for, located_in, ...): сущности подходящих типов
      в одном предложении, содержащем слово-триггер.
    """

    PROXIMITY_WINDOW = 200  # символов
    MAX_OCCURRENCES = 50    # вхождений одной сущности в тексте

    # relation -> (триггеры, допустимые пары типов)
    RELATION_RULES = {
        'works_for': (
            re.compile(r'\b(?:работа\w*|труди\w*|служи\w*|сотрудник\w*|директор\w*|руководител\w*|'
                       r'начальник\w*|глав[аы]\s+(?:компании|организации|отдела)|учредител\w*|'
                       r'владел\w*|бухгалтер\w*|менеджер\w*)', re.IGNORECASE),
            {('PERSONS', 'ORGANIZATIONS')},
        ),
        'located_in': (
            re.compile(r'\b(?:находи\w*|располож\w*|базиру\w*|зарегистрирован\w*|адрес\w*|'
                       r'прожив\w*)', re.IGNORECASE),
            {('ORGANIZATIONS', 'LOCATIONS'), ('PERSONS', 'LOCATIONS')},
        ),
        'transaction': (
            re.compile(r'\b(?:перев[её]л\w*|перечисл\w*|заплати\w*|получи\w*|выплати\w*|оплат\w*|'
                       r'плат[её]ж\w*|перевод\w*|сумм\w*)', re.IGNORECASE),
            {('MONEY', 'PERSONS'), ('MONEY', 'ORGANIZATIONS')},
        ),
        'meeting': (
            re.compile(r'\b(?:встрет\w*|встреч\w*|переговор\w*|совещани\w*|созвон\w*)', re.IGNORECASE),
            {('PERSONS', 'PERSONS')},
        ),
        'agreement': (
            re.compile(r'\b(?:договор\w*|контракт\w*|соглашени\w*|сделк\w*)', re.IGNORECASE),
            {('ORGANIZATIONS', 'ORGANIZATIONS'), ('PERSONS', 'ORGANIZATIONS'), ('PERSONS', 'PERSONS')},
        ),
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
        entities = {k: [str(v) for v in vals if str(v).strip()]
                    for k, vals in (entities or {}).items() if isinstance(vals, list)}

        for entity_type, entity_list in entities.items():
            for entity in entity_list:
                self.graph.add_node(
                    id=self._make_node_id(entity, entity_type),
                    label=entity,
                    type=entity_type
                )

        positions = self._entity_positions(entities, text or '')
        self._find_proximity_connections(positions, text or '', file_path)
        self._find_pattern_connections(positions, text or '', file_path)

        return self.graph

    def _make_node_id(self, entity: str, entity_type: str) -> str:
        """Создать уникальный ID для узла"""
        normalized = entity.lower().strip().replace('ё', 'е')
        normalized = re.sub(r'\s+', '_', normalized)
        normalized = re.sub(r'[^\w]', '', normalized)
        return f"{entity_type.lower()}_{normalized}"

    def _entity_positions(self, entities: Dict[str, List[str]], text: str) -> List[Tuple[int, int, str, str]]:
        """Позиции вхождений сущностей (целыми словами, без учёта регистра).

        Поиск идёт по исходному тексту, поэтому позиции совпадают с ним
        (lower() может менять длину строки для некоторых символов).
        """
        positions: List[Tuple[int, int, str, str]] = []
        for entity_type, entity_list in entities.items():
            for entity in entity_list:
                pattern = re.compile(r'(?<!\w)' + re.escape(entity) + r'(?!\w)', re.IGNORECASE)
                for i, m in enumerate(pattern.finditer(text)):
                    if i >= self.MAX_OCCURRENCES:
                        break
                    positions.append((m.start(), m.end(), entity, entity_type))
        positions.sort()
        return positions

    def _find_proximity_connections(
        self,
        positions: List[Tuple[int, int, str, str]],
        text: str,
        file_path: str
    ) -> None:
        """Найти связи по близости в тексте"""
        for i, (start1, end1, ent1, type1) in enumerate(positions):
            id1 = self._make_node_id(ent1, type1)
            for start2, end2, ent2, type2 in positions[i + 1:]:
                if start2 - start1 > self.PROXIMITY_WINDOW:
                    break
                if start2 < end1:  # перекрывающиеся сущности ("Иван Петров" / "Петров")
                    continue
                id2 = self._make_node_id(ent2, type2)
                if id1 == id2:
                    continue
                context = text[max(0, start1 - 50):min(len(text), end2 + 50)]
                self.graph.add_edge(
                    source=id1, target=id2, weight=1.0, relation='proximity',
                    context=context[:200], file_path=file_path
                )

    def _find_pattern_connections(
        self,
        positions: List[Tuple[int, int, str, str]],
        text: str,
        file_path: str = None
    ) -> None:
        """Типизированные связи: сущности подходящих типов в предложении с триггером."""
        if not positions:
            return
        # Границы предложений
        bounds = []
        start = 0
        for m in _SENTENCE_SPLIT.finditer(text):
            bounds.append((start, m.start()))
            start = m.end()
        bounds.append((start, len(text)))

        idx = 0
        for s_start, s_end in bounds:
            in_sentence = []
            while idx < len(positions) and positions[idx][0] < s_end:
                if positions[idx][0] >= s_start:
                    in_sentence.append(positions[idx])
                idx += 1
            if len(in_sentence) < 2:
                continue
            sentence = text[s_start:s_end]
            for relation, (trigger, type_pairs) in self.RELATION_RULES.items():
                if not trigger.search(sentence):
                    continue
                for a in range(len(in_sentence)):
                    for b in range(a + 1, len(in_sentence)):
                        _, _, e1, t1 = in_sentence[a]
                        _, _, e2, t2 = in_sentence[b]
                        if (t1, t2) in type_pairs:
                            src, dst = (e1, t1), (e2, t2)
                        elif (t2, t1) in type_pairs:
                            src, dst = (e2, t2), (e1, t1)
                        else:
                            continue
                        id1 = self._make_node_id(*src)
                        id2 = self._make_node_id(*dst)
                        if id1 == id2:
                            continue
                        self.graph.add_edge(
                            source=id1, target=id2, weight=2.0, relation=relation,
                            context=sentence.strip()[:200], file_path=file_path
                        )

    @staticmethod
    def _guess_entity_type(entity: str) -> str:
        """Угадать тип сущности (по целым словам, а не по подстрокам)"""
        low = entity.lower()
        if re.search(r'\b(?:ооо|оао|зао|пао|ао|ип|банк\w*|компани\w*)\b', low):
            return 'ORGANIZATIONS'
        if re.search(r'\b(?:город\w*|област\w*|улиц\w*|район\w*|г\.|ул\.)', low):
            return 'LOCATIONS'
        if re.search(r'\b(?:руб\w*|долл\w*|млн|млрд)\b|[$€₽]', low):
            return 'MONEY'
        if re.search(r'\d{1,2}[./]\d{1,2}[./]\d{2,4}', entity):
            return 'DATES'
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

    def _node_ids(self) -> List[str]:
        return sorted(self.graph.all_node_ids())

    def get_central_nodes(self, limit: int = 10) -> List[Tuple[Node, float]]:
        """Найти наиболее связанные узлы (degree centrality)"""
        ranked = sorted(
            [(node, len(self.graph.get_neighbors(nid))) for nid, node in self.graph.nodes.items()],
            key=lambda x: -x[1]
        )
        return [r for r in ranked if r[1] > 0][:limit]

    def get_communities(self) -> List[Set[str]]:
        """Найти сообщества (компоненты связности)"""
        node_ids = self._node_ids()
        if not node_ids:
            return []

        visited: Set[str] = set()
        communities: List[Set[str]] = []

        for node_id in node_ids:
            if node_id in visited:
                continue
            community: Set[str] = set()
            queue = deque([node_id])
            visited.add(node_id)
            while queue:
                current = queue.popleft()
                community.add(current)
                for neighbor in self.graph.get_neighbors(current):
                    if neighbor not in visited:
                        visited.add(neighbor)
                        queue.append(neighbor)
            communities.append(community)

        return sorted(communities, key=len, reverse=True)

    def get_shortest_path(self, source: str, target: str) -> List[str]:
        """Найти кратчайший путь между узлами"""
        ids = self.graph.all_node_ids()
        if source not in ids or target not in ids:
            return []
        if source == target:
            return [source]

        parents: Dict[str, Optional[str]] = {source: None}
        queue = deque([source])
        while queue:
            current = queue.popleft()
            for neighbor in self.graph.get_neighbors(current):
                if neighbor in parents:
                    continue
                parents[neighbor] = current
                if neighbor == target:
                    path = [target]
                    while parents[path[-1]] is not None:
                        path.append(parents[path[-1]])
                    return list(reversed(path))
                queue.append(neighbor)
        return []

    def get_subgraph(self, node_id: str, depth: int = 2) -> Graph:
        """Получить подграф вокруг узла"""
        if node_id not in self.graph.all_node_ids():
            return Graph()

        subgraph = Graph()
        visited: Set[str] = {node_id}
        queue = deque([(node_id, 0)])

        while queue:
            current, current_depth = queue.popleft()
            node = self.graph.nodes.get(current)
            if node is not None:
                subgraph.add_node(node.id, node.label, node.type, weight=node.weight)
            if current_depth < depth:
                for neighbor in self.graph.get_neighbors(current):
                    if neighbor not in visited:
                        visited.add(neighbor)
                        queue.append((neighbor, current_depth + 1))

        for edge in self.graph.edges:
            if edge.source in visited and edge.target in visited:
                subgraph.add_edge(edge.source, edge.target, edge.weight,
                                  edge.relation, edge.context, edge.file_path)
        return subgraph

    def _count_node_types(self) -> Dict[str, int]:
        """Подсчёт узлов по типам"""
        counts: Dict[str, int] = defaultdict(int)
        for node in self.graph.nodes.values():
            counts[node.type] += 1
        return counts

    def pagerank(self, damping=0.85, iterations=100, tolerance=1e-6) -> Dict[str, float]:
        """PageRank для неориентированного графа (с учётом изолированных узлов)."""
        node_ids = self._node_ids()
        if not node_ids:
            return {}

        n = len(node_ids)
        scores = {nid: 1.0 / n for nid in node_ids}
        degree = {nid: len(self.graph.get_neighbors(nid)) for nid in node_ids}

        for _ in range(iterations):
            # Масса "висячих" узлов распределяется равномерно (иначе она теряется)
            dangling = sum(scores[nid] for nid in node_ids if degree[nid] == 0)
            base = (1 - damping) / n + damping * dangling / n
            new_scores = {}
            max_change = 0.0
            for nid in node_ids:
                rank = base
                for neighbor in self.graph.get_neighbors(nid):
                    if degree[neighbor] > 0:
                        rank += damping * scores[neighbor] / degree[neighbor]
                new_scores[nid] = rank
                max_change = max(max_change, abs(rank - scores[nid]))
            scores = new_scores
            if max_change < tolerance:
                break

        return scores

    def betweenness_centrality(self) -> Dict[str, float]:
        """Brandes algorithm (неориентированный граф, нормировка как в networkx)."""
        node_ids = self._node_ids()
        if not node_ids:
            return {}

        betweenness = {nid: 0.0 for nid in node_ids}

        for source in node_ids:
            stack = []
            predecessors: Dict[str, List[str]] = {nid: [] for nid in node_ids}
            sigma = dict.fromkeys(node_ids, 0)
            sigma[source] = 1
            distance = dict.fromkeys(node_ids, -1)
            distance[source] = 0
            queue = deque([source])

            while queue:
                current = queue.popleft()
                stack.append(current)
                for neighbor in self.graph.get_neighbors(current):
                    if distance[neighbor] < 0:
                        queue.append(neighbor)
                        distance[neighbor] = distance[current] + 1
                    if distance[neighbor] == distance[current] + 1:
                        sigma[neighbor] += sigma[current]
                        predecessors[neighbor].append(current)

            delta = dict.fromkeys(node_ids, 0.0)
            while stack:
                w = stack.pop()
                for v in predecessors[w]:
                    delta[v] += (sigma[v] / sigma[w]) * (1 + delta[w])
                if w != source:
                    betweenness[w] += delta[w]

        # Каждая пара (s, t) учтена дважды → делим на 2; затем нормировка
        n = len(node_ids)
        scale = 0.5
        if n > 2:
            scale *= 2.0 / ((n - 1) * (n - 2))
        return {nid: value * scale for nid, value in betweenness.items()}

    def closeness_centrality(self) -> Dict[str, float]:
        """Closeness centrality (для несвязного графа — в пределах компоненты)."""
        node_ids = self._node_ids()
        closeness = {}
        for node_id in node_ids:
            distances = {node_id: 0}
            queue = deque([node_id])
            while queue:
                current = queue.popleft()
                for neighbor in self.graph.get_neighbors(current):
                    if neighbor not in distances:
                        distances[neighbor] = distances[current] + 1
                        queue.append(neighbor)
            reachable = len(distances) - 1
            total = sum(distances.values())
            closeness[node_id] = reachable / total if total > 0 else 0.0
        return closeness

    def get_bridges(self) -> List[Edge]:
        """Рёбра-мосты (удаление связи разъединяет граф). Итеративный Тарьян.

        Повторные наблюдения одной связи не являются альтернативным путём,
        поэтому они объединены в одно ребро ещё при построении графа.
        """
        node_ids = self._node_ids()
        if not node_ids:
            return []

        # Связь между парой узлов может быть представлена рёбрами разных
        # типов (proximity/works_for) — для мостов важна сама пара.
        pair_edges: Dict[Tuple[str, str], Edge] = {}
        for edge in self.graph.edges:
            key = (edge.source, edge.target) if edge.source <= edge.target else (edge.target, edge.source)
            if key not in pair_edges or edge.weight > pair_edges[key].weight:
                pair_edges[key] = edge

        visited: Set[str] = set()
        disc: Dict[str, int] = {}
        low: Dict[str, int] = {}
        parent: Dict[str, Optional[str]] = {}
        bridges: List[Edge] = []
        timer = 0

        for start_node in node_ids:
            if start_node in visited:
                continue
            parent[start_node] = None
            stack = [(start_node, iter(self.graph.get_neighbors(start_node)))]
            visited.add(start_node)
            disc[start_node] = low[start_node] = timer
            timer += 1

            while stack:
                u, neighbors_iter = stack[-1]
                advanced = False
                for v in neighbors_iter:
                    if v not in visited:
                        parent[v] = u
                        visited.add(v)
                        disc[v] = low[v] = timer
                        timer += 1
                        stack.append((v, iter(self.graph.get_neighbors(v))))
                        advanced = True
                        break
                    elif v != parent.get(u):
                        low[u] = min(low[u], disc[v])
                if advanced:
                    continue
                stack.pop()
                p = parent.get(u)
                if p is not None:
                    low[p] = min(low[p], low[u])
                    if low[u] > disc[p]:
                        key = (p, u) if p <= u else (u, p)
                        edge = pair_edges.get(key)
                        if edge:
                            bridges.append(edge)

        return bridges

    def get_statistics(self) -> Dict[str, Any]:
        """Получить статистику графа"""
        node_ids = self._node_ids()
        node_count = len(node_ids)
        pairs = {(e.source, e.target) if e.source <= e.target else (e.target, e.source)
                 for e in self.graph.edges}
        degrees = [len(self.graph.get_neighbors(nid)) for nid in node_ids] or [0]

        density = (2.0 * len(pairs)) / (node_count * (node_count - 1)) if node_count > 1 else 0.0

        pagerank_scores = self.pagerank()
        pagerank_top5 = []
        for node_id, score in sorted(pagerank_scores.items(), key=lambda x: -x[1])[:5]:
            node = self.graph.nodes.get(node_id)
            pagerank_top5.append({'node_id': node_id, 'score': score,
                                  'label': node.label if node else node_id})

        return {
            'node_count': node_count,
            'edge_count': len(self.graph.edges),
            'connected_pairs': len(pairs),
            'avg_degree': sum(degrees) / len(degrees),
            'max_degree': max(degrees),
            'density': density,
            'communities': len(self.get_communities()),
            'node_types': dict(self._count_node_types()),
            'pagerank_top5': pagerank_top5
        }


# ═══════════════════════════════════════════════════════════════════════════════
# VISUALIZATION EXPORT
# ═══════════════════════════════════════════════════════════════════════════════

def export_to_graphml(graph: Graph, path: str) -> None:
    """Экспорт в GraphML (Gephi, yEd, Cytoscape, networkx)."""
    import xml.etree.ElementTree as ET

    ns = 'http://graphml.graphdrawing.org/xmlns'
    root = ET.Element('graphml', {'xmlns': ns})

    keys = [
        ('label', 'node', 'label', 'string'),
        ('type', 'node', 'type', 'string'),
        ('nweight', 'node', 'weight', 'double'),
        ('weight', 'edge', 'weight', 'double'),
        ('relation', 'edge', 'relation', 'string'),
        ('count', 'edge', 'count', 'int'),
    ]
    for key_id, domain, name, attr_type in keys:
        ET.SubElement(root, 'key', {'id': key_id, 'for': domain,
                                    'attr.name': name, 'attr.type': attr_type})

    g = ET.SubElement(root, 'graph', {'id': 'G', 'edgedefault': 'undirected'})

    for node_id in sorted(graph.all_node_ids()):
        node = graph.nodes.get(node_id)
        n = ET.SubElement(g, 'node', {'id': sanitize_xml_text(node_id)})
        ET.SubElement(n, 'data', {'key': 'label'}).text = sanitize_xml_text(node.label if node else node_id)
        ET.SubElement(n, 'data', {'key': 'type'}).text = sanitize_xml_text(node.type if node else 'UNKNOWN')
        ET.SubElement(n, 'data', {'key': 'nweight'}).text = str(node.weight if node else 1.0)

    for i, edge in enumerate(graph.edges):
        e = ET.SubElement(g, 'edge', {'id': f'e{i}', 'source': sanitize_xml_text(edge.source),
                                      'target': sanitize_xml_text(edge.target)})
        ET.SubElement(e, 'data', {'key': 'weight'}).text = str(edge.weight)
        ET.SubElement(e, 'data', {'key': 'relation'}).text = sanitize_xml_text(edge.relation)
        ET.SubElement(e, 'data', {'key': 'count'}).text = str(edge.count)

    ET.ElementTree(root).write(path, encoding='utf-8', xml_declaration=True)


def export_to_gexf(graph: Graph, path: str) -> None:
    """Экспорт в GEXF 1.2 (Gephi)."""
    import xml.etree.ElementTree as ET

    root = ET.Element('gexf', {'xmlns': 'http://www.gexf.net/1.2draft', 'version': '1.2'})
    meta = ET.SubElement(root, 'meta')
    ET.SubElement(meta, 'creator').text = 'ASTREX'

    g = ET.SubElement(root, 'graph', {'mode': 'static', 'defaultedgetype': 'undirected'})

    node_attrs = ET.SubElement(g, 'attributes', {'class': 'node'})
    ET.SubElement(node_attrs, 'attribute', {'id': 'type', 'title': 'type', 'type': 'string'})
    edge_attrs = ET.SubElement(g, 'attributes', {'class': 'edge'})
    ET.SubElement(edge_attrs, 'attribute', {'id': 'relation', 'title': 'relation', 'type': 'string'})

    nodes = ET.SubElement(g, 'nodes')
    for node_id in sorted(graph.all_node_ids()):
        node = graph.nodes.get(node_id)
        n = ET.SubElement(nodes, 'node', {'id': sanitize_xml_text(node_id),
                                          'label': sanitize_xml_text(node.label if node else node_id)})
        attvalues = ET.SubElement(n, 'attvalues')
        ET.SubElement(attvalues, 'attvalue', {'for': 'type',
                                              'value': sanitize_xml_text(node.type if node else 'UNKNOWN')})

    edges = ET.SubElement(g, 'edges')
    for i, edge in enumerate(graph.edges):
        e = ET.SubElement(edges, 'edge', {
            'id': str(i), 'source': sanitize_xml_text(edge.source),
            'target': sanitize_xml_text(edge.target), 'weight': str(edge.weight)})
        attvalues = ET.SubElement(e, 'attvalues')
        ET.SubElement(attvalues, 'attvalue', {'for': 'relation', 'value': sanitize_xml_text(edge.relation)})

    ET.ElementTree(root).write(path, encoding='utf-8', xml_declaration=True)


__all__ = [
    'Node', 'Edge', 'Graph', 'GraphBuilder', 'GraphAnalyzer',
    'export_to_graphml', 'export_to_gexf'
]
