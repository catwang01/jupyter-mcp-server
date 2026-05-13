"""Unit tests for resolve_cell_index utility."""

import pytest
from jupyter_mcp_server.utils import resolve_cell_index


class TestResolveCellIndex:

    def test_resolve_by_cell_id_dict(self):
        cells = [{"id": "abc123"}, {"id": "def456"}, {"id": "ghi789"}]
        assert resolve_cell_index(cells, cell_id="def456") == 1

    def test_resolve_by_cell_id_object(self):
        from types import SimpleNamespace
        cells = [SimpleNamespace(id="abc"), SimpleNamespace(id="xyz")]
        assert resolve_cell_index(cells, cell_id="xyz") == 1

    def test_resolve_by_cell_index(self):
        cells = [{"id": "abc123"}, {"id": "def456"}]
        assert resolve_cell_index(cells, cell_index=0) == 0
        assert resolve_cell_index(cells, cell_index=1) == 1

    def test_cell_id_priority_over_index(self):
        cells = [{"id": "abc123"}, {"id": "def456"}]
        # cell_id="def456" is at index 1, but cell_index=0 is given — cell_id wins
        assert resolve_cell_index(cells, cell_id="def456", cell_index=0) == 1

    def test_cell_id_not_found_raises(self):
        cells = [{"id": "abc123"}]
        with pytest.raises(ValueError, match="not found"):
            resolve_cell_index(cells, cell_id="zzz999")

    def test_neither_provided_raises(self):
        cells = [{"id": "abc123"}]
        with pytest.raises(ValueError, match="Either"):
            resolve_cell_index(cells)

    def test_both_none_raises(self):
        cells = [{"id": "abc123"}]
        with pytest.raises(ValueError, match="Either"):
            resolve_cell_index(cells, cell_id=None, cell_index=None)

    def test_empty_cells_with_cell_id_raises(self):
        with pytest.raises(ValueError, match="not found"):
            resolve_cell_index([], cell_id="abc")

    def test_first_match_returned_on_duplicate_ids(self):
        cells = [{"id": "dup"}, {"id": "dup"}]
        assert resolve_cell_index(cells, cell_id="dup") == 0

    def test_get_method_used_for_dict_cells(self):
        # Cells without "id" key — should not match
        cells = [{"source": "print()"}, {"id": "real-id"}]
        assert resolve_cell_index(cells, cell_id="real-id") == 1
