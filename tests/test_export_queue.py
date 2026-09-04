"""Contratos pequeños de la cola durable de exportación."""

import base64
import io

from PIL import Image

from api import export_queue as queue


def _jpeg():
    buf = io.BytesIO()
    Image.new("RGB", (8, 12), (220, 40, 40)).save(buf, "JPEG")
    return buf.getvalue()


def test_cover_is_externalized_and_roundtrips(monkeypatch, tmp_path):
    payload_dir = tmp_path / "payloads"
    state_path = tmp_path / "export_tasks.json"
    monkeypatch.setattr(queue, "_PAYLOAD_DIR", payload_dir)
    monkeypatch.setattr(queue, "_STATE_PATH", state_path)
    raw = _jpeg()

    spec = queue.prepare_request("task-1", {
        "title": "Obra", "chapters": ["1"], "cover_data": "data:image/jpeg;base64," + base64.b64encode(raw).decode(),
        "cover_path": "/no-se-persisten-rutas-del-cliente",
    })
    assert "cover_data" not in spec
    assert spec["_cover_file"] == "task-1.cover"
    assert (payload_dir / "task-1.cover").read_bytes() == raw

    queue.save_state({"task-1": {"task_id": "task-1", "status": "queued"}}, {"task-1": spec})
    tasks, requests = queue.load_state()
    assert tasks["task-1"]["status"] == "queued"
    restored = queue.restore_request(requests["task-1"])
    assert base64.b64decode(restored["cover_data"]) == raw
    assert "cover_path" not in restored


def test_remove_payload_does_not_escape_payload_directory(monkeypatch, tmp_path):
    monkeypatch.setattr(queue, "_PAYLOAD_DIR", tmp_path / "payloads")
    queue._PAYLOAD_DIR.mkdir()
    (queue._PAYLOAD_DIR / "task-1.cover").write_bytes(b"x")
    queue.remove_payload("task-1")
    assert not (queue._PAYLOAD_DIR / "task-1.cover").exists()
