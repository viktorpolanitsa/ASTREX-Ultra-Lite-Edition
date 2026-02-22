#!/usr/bin/env python3
"""Тесты графа"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


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
        g = self._make_graph()
        self.assertEqual(g.get_neighbors('UNKNOWN'), [])

    def test_to_dict(self):
        g = self._make_graph()
        d = g.to_dict()
        self.assertIn('nodes', d)
        self.assertIn('edges', d)
        self.assertEqual(len(d['nodes']), 3)
        self.assertEqual(len(d['edges']), 3)

    def test_merge(self):
        from core.graph import Graph
        g1 = Graph()
        g1.add_node('A', 'Alice', 'PERSON')
        g2 = Graph()
        g2.add_node('B', 'Bob', 'PERSON')
        g2.add_edge('A', 'B', relation='knows')
        g1.merge(g2)
        self.assertIn('B', g1.nodes)
        self.assertEqual(len(g1.edges), 1)
        self.assertIn('B', g1.get_neighbors('A'))


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
        # D is isolated
        return GraphAnalyzer(g)

    def test_get_central_nodes(self):
        analyzer = self._make_analyzer()
        central = analyzer.get_central_nodes(limit=2)
        self.assertEqual(len(central), 2)
        # A and B/C should be most central
        node_ids = [n[0].id for n in central]
        self.assertIn('A', node_ids)

    def test_get_communities(self):
        analyzer = self._make_analyzer()
        communities = analyzer.get_communities()
        # Should have 2 communities: {A,B,C} and {D}
        self.assertEqual(len(communities), 2)

    def test_shortest_path(self):
        analyzer = self._make_analyzer()
        path = analyzer.get_shortest_path('A', 'C')
        self.assertEqual(path[0], 'A')
        self.assertEqual(path[-1], 'C')

    def test_shortest_path_no_connection(self):
        analyzer = self._make_analyzer()
        path = analyzer.get_shortest_path('A', 'D')
        self.assertEqual(path, [])

    def test_get_subgraph(self):
        analyzer = self._make_analyzer()
        sub = analyzer.get_subgraph('A', depth=1)
        self.assertIn('A', sub.nodes)
        self.assertIn('B', sub.nodes)
        self.assertNotIn('D', sub.nodes)


if __name__ == '__main__':
    unittest.main()
