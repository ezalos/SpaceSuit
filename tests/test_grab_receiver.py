# ABOUTME: Tests the grab-receiver daemon's tar-stream extraction.
# ABOUTME: Covers safe extraction, path-traversal rejection, and e2e socket roundtrip.

import io
import socket
import tarfile
import threading
import time
from pathlib import Path

import pytest

import importlib.util
import sys

# The receiver lives outside the importable package layout; load it by path.
_RECV_PATH = Path(__file__).resolve().parent.parent / "scripts" / "grab-receiver.py"
_spec = importlib.util.spec_from_file_location("grab_receiver", _RECV_PATH)
grab_receiver = importlib.util.module_from_spec(_spec)
sys.modules["grab_receiver"] = grab_receiver
_spec.loader.exec_module(grab_receiver)


def _make_tar_bytes(entries: dict[str, bytes]) -> bytes:
    """Build an in-memory tar archive from {relative_path: content} entries."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w|") as tf:
        for name, content in entries.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(content)
            tf.addfile(info, io.BytesIO(content))
    return buf.getvalue()


def test_extract_stream_writes_single_file(tmp_path):
    data = _make_tar_bytes({"notes.md": b"hello grab"})
    grab_receiver.extract_stream(io.BytesIO(data), tmp_path)
    assert (tmp_path / "notes.md").read_bytes() == b"hello grab"


def test_extract_stream_writes_nested_directory(tmp_path):
    data = _make_tar_bytes({
        "proj/README.md": b"readme",
        "proj/src/main.py": b"print('hi')",
    })
    grab_receiver.extract_stream(io.BytesIO(data), tmp_path)
    assert (tmp_path / "proj" / "README.md").read_bytes() == b"readme"
    assert (tmp_path / "proj" / "src" / "main.py").read_bytes() == b"print('hi')"


def test_extract_stream_rejects_path_traversal(tmp_path):
    # A malicious tar trying to write ../escaped.txt
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w|") as tf:
        info = tarfile.TarInfo(name="../escaped.txt")
        info.size = 4
        tf.addfile(info, io.BytesIO(b"evil"))
    buf.seek(0)

    with pytest.raises(Exception):
        grab_receiver.extract_stream(buf, tmp_path)
    # And the parent directory must not contain escaped.txt
    assert not (tmp_path.parent / "escaped.txt").exists()


def test_extract_stream_rejects_absolute_path(tmp_path):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w|") as tf:
        info = tarfile.TarInfo(name="/tmp/pwned")
        info.size = 4
        tf.addfile(info, io.BytesIO(b"evil"))
    buf.seek(0)

    with pytest.raises(Exception):
        grab_receiver.extract_stream(buf, tmp_path)


def test_serve_accepts_tcp_and_extracts(tmp_path):
    """E2E: start server thread, connect, send tar bytes, verify files extracted."""
    host, port = "127.0.0.1", 0  # let the OS pick a free port
    server = grab_receiver.build_server(host, port, dest_dir=tmp_path)
    actual_port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        data = _make_tar_bytes({"roundtrip.txt": b"ok"})
        with socket.create_connection(("127.0.0.1", actual_port)) as s:
            s.sendall(data)
        # Give the handler a moment to finish extracting
        deadline = time.time() + 2.0
        target = tmp_path / "roundtrip.txt"
        while time.time() < deadline and not target.exists():
            time.sleep(0.01)
        assert target.read_bytes() == b"ok"
    finally:
        server.shutdown()
        server.server_close()
