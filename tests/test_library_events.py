"""Contrato de invalidación de biblioteca y evento SSE sin datos de obras."""

from api import library_events
from api import runtime


def test_mark_changed_avanza_revision_y_no_filtra_la_biblioteca(monkeypatch):
    events = []
    monkeypatch.setattr(runtime, "push_sse_event", lambda event, **payload: events.append((event, payload)))

    before = library_events.current_revision()
    after = library_events.mark_changed()

    assert after == before + 1
    assert library_events.current_revision() == after
    assert events == [("library_changed", {"revision": after})]
    assert "title" not in events[0][1]
    assert "path" not in events[0][1]


def test_la_revision_forma_parte_de_la_clave_del_snapshot(monkeypatch):
    from api import library

    monkeypatch.setattr("api.roots.roots", lambda: [])
    before = library._library_snapshot_key("normal")
    library_events.mark_changed()
    after = library._library_snapshot_key("normal")

    assert before[0] == after[0] == "normal"
    assert before[1] != after[1]
