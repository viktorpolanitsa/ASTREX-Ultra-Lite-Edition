#!/usr/bin/env python3
"""Тесты графа"""

import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import tests.helpers  # noqa: F401,E402


class TestGraph(unittest.TestCase):

    def _make_graph(self):
        from core.graph import Graph
        g = Graph()
        g.add_node('A', 'Alice', 'PERSON')
        g.add_node('B', 'Bob', 'PERSON')
        g.add_node('C', 'Corp', 'ORG')
        g.add_edge('A', 'B', relation='knows')
        g.add_edge('A', 'C', relation='works_for')
        g.add_edge('B', 'C', relation='works_for')
        return g

    def test_add_node(self):
        from core.graph import Graph
        g = Graph()
        node = g.add_node('n1', 'Test', 'PERSON')
        self.assertEqual(node.id, 'n1')
        self.assertEqual(node.label, 'Test')
        self.assertIn('n1', g.nodes)

    def test_add_edge_updates_adjacency(self):
        g = self._make_graph()
        neighbors = g.get_neighbors('A')
        self.assertIn('B', neighbors)
        self.assertIn('C', neighbors)
        self.assertEqual(len(neighbors), 2)

    def test_get_neighbors_symmetric(self):
        g = self._make_graph()
        self.assertIn('A', g.get_neighbors('B'))
        self.assertIn('B', g.get_neighbors('A'))

    def test_get_neighbors_unknown_node(self):
        self.assertEqual(self._make_graph().get_neighbors('UNKNOWN'), [])

    def test_to_dict(self):
        d = self._make_graph().to_dict()
        self.assertEqual(len(d['nodes']), 3)
        self.assertEqual(len(d['edges']), 3)

    def test_duplicate_edges_are_merged(self):
        """Регрессия: каждое повторное наблюдение создавало новое ребро."""
        from core.graph import Graph
        g = Graph()
        for _ in range(5):
            g.add_edge('A', 'B', relation='proximity', file_path='/f1')
        g.add_edge('B', 'A', relation='proximity', file_path='/f2')
        self.assertEqual(len(g.edges), 1)
        self.assertEqual(g.edges[0].count, 6)
        self.assertEqual(g.edges[0].weight, 6.0)
        self.assertEqual(g.edges[0].files, ['/f1', '/f2'])
        self.assertIsNone(g.add_edge('A', 'A'))  # петли не добавляются

    def test_merge(self):
        from core.graph import Graph
        g1 = Graph()
        g1.add_node('A', 'Alice', 'PERSON')
        g2 = Graph()
        g2.add_node('B', 'Bob', 'PERSON', metadata={'k': 1})
        g2.add_edge('A', 'B', relation='knows')
        g1.merge(g2)
        self.assertIn('B', g1.nodes)
        self.assertEqual(len(g1.edges), 1)
        self.assertIn('B', g1.get_neighbors('A'))
        g1.nodes['B'].metadata['k'] = 2   # метаданные скопированы, а не разделены
        self.assertEqual(g2.nodes['B'].metadata['k'], 1)


class TestGraphBuilder(unittest.TestCase):

    def test_nodes_are_entities_not_sentences(self):
        """Регрессия: шаблоны связей превращали целые строки текста в узлы."""
        from core.graph import GraphBuilder
        text = ('Сегодня утром в длинном отчёте говорилось, что Иванов работает в ООО Вектор, '
                'а также много другого текста без точек и переводов строк ' * 20)
        b = GraphBuilder()
        g = b.build_from_entities({'PERSONS': ['Иванов'], 'ORGANIZATIONS': ['ООО Вектор']}, text, '/f')
        self.assertEqual(len(g.nodes), 2)
        relations = {e.relation for e in g.edges}
        self.assertIn('works_for', relations)
        self.assertTrue(all(len(n.label) < 50 for n in g.nodes.values()))

    def test_guess_entity_type_whole_words(self):
        from core.graph import GraphBuilder
        self.assertEqual(GraphBuilder._guess_entity_type('ООО Ромашка'), 'ORGANIZATIONS')
        self.assertEqual(GraphBuilder._guess_entity_type('Липецк'), 'UNKNOWN')   # "ип" внутри слова
        self.assertEqual(GraphBuilder._guess_entity_type('хаос'), 'UNKNOWN')     # "ао" внутри слова


class TestGraphAnalyzer(unittest.TestCase):

    def _make_analyzer(self):
        from core.graph import Graph, GraphAnalyzer
        g = Graph()
        g.add_node('A', 'Alice', 'PERSON')
        g.add_node('B', 'Bob', 'PERSON')
        g.add_node('C', 'Corp', 'ORG')
        g.add_node('D', 'Dave', 'PERSON')
        g.add_edge('A', 'B')
        g.add_edge('A', 'C')
        g.add_edge('B', 'C')
        return GraphAnalyzer(g)

    def test_get_central_nodes(self):
        central = self._make_analyzer().get_central_nodes(limit=2)
        self.assertEqual(len(central), 2)
        self.assertIn('A', [n[0].id for n in central])

    def test_get_communities(self):
        self.assertEqual(len(self._make_analyzer().get_communities()), 2)

    def test_shortest_path(self):
        path = self._make_analyzer().get_shortest_path('A', 'C')
        self.assertEqual(path, ['A', 'C'])

    def test_shortest_path_no_connection(self):
        self.assertEqual(self._make_analyzer().get_shortest_path('A', 'D'), [])

    def test_get_subgraph(self):
        sub = self._make_analyzer().get_subgraph('A', depth=1)
        self.assertIn('A', sub.nodes)
        self.assertIn('B', sub.nodes)
        self.assertNotIn('D', sub.nodes)

    def test_betweenness_matches_networkx_normalization(self):
        """Путь A-B-C: центральность B = 1.0 (раньше удваивалась до 2.0)."""
        from core.graph import Graph, GraphAnalyzer
        g = Graph()
        for n in 'ABC':
            g.add_node(n, n, 'X')
        g.add_edge('A', 'B')
        g.add_edge('B', 'C')
        bc = GraphAnalyzer(g).betweenness_centrality()
        self.assertAlmostEqual(bc['B'], 1.0)
        self.assertAlmostEqual(bc['A'], 0.0)

    def test_pagerank_sums_to_one_with_isolated_nodes(self):
        pr = self._make_analyzer().pagerank()
        self.assertAlmostEqual(sum(pr.values()), 1.0, places=4)

    def test_bridges_ignore_repeated_observations(self):
        """Повторные наблюдения связи не создают "мостов" из параллельных рёбер."""
        from core.graph import Graph, GraphAnalyzer
        g = Graph()
        g.add_edge('A', 'B')
        g.add_edge('A', 'B')  # объединяется с первым
        g.add_edge('B', 'C')
        g.add_edge('C', 'A')
        self.assertEqual(GraphAnalyzer(g).get_bridges(), [])
        g.add_edge('C', 'D')
        bridges = GraphAnalyzer(g).get_bridges()
        self.assertEqual(len(bridges), 1)
        self.assertEqual({bridges[0].source, bridges[0].target}, {'C', 'D'})

    def test_statistics(self):
        stats = self._make_analyzer().get_statistics()
        self.assertEqual(stats['node_count'], 4)
        self.assertAlmostEqual(stats['avg_degree'], 1.5)   # изолированный узел учитывается
        self.assertAlmostEqual(stats['density'], 0.5)


class TestExport(unittest.TestCase):

    def _graph(self):
        from core.graph import GraphBuilder
        b = GraphBuilder()
        return b.build_from_entities({'PERSONS': ['Иванов \x01'], 'ORGANIZATIONS': ['ООО "Ромашка" & Co']},
                                     'Иванов \x01 работает в ООО "Ромашка" & Co', '/f')

    def test_graphml_is_valid(self):
        """Регрессия: атрибуты for_/attr_name вместо for/attr.name."""
        from core.graph import export_to_graphml
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'g.graphml'
            export_to_graphml(self._graph(), str(path))
            root = ET.parse(path).getroot()
            ns = '{http://graphml.graphdrawing.org/xmlns}'
            keys = root.findall(f'{ns}key')
            self.assertTrue(keys)
            for key in keys:
                self.assertIn('for', key.attrib)
                self.assertIn('attr.name', key.attrib)
                self.assertIn('attr.type', key.attrib)
            self.assertEqual(len(root.findall(f'.//{ns}node')), 2)

    def test_gexf_is_valid(self):
        from core.graph import export_to_gexf
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'g.gexf'
            export_to_gexf(self._graph(), str(path))
            root = ET.parse(path).getroot()
            self.assertTrue(root.tag.endswith('gexf'))


if __name__ == '__main__':
    unittest.main()
