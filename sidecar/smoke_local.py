r"""Opt-in real-model smoke on synthetic files, never the application's database.

Run from sidecar/: .venv\Scripts\python.exe smoke_local.py ../artifacts
Add --http http://127.0.0.1:9547 to exercise a running app-owned sidecar.
Downloads model weights on the first run. No cloud parsing or account needed.
"""

import argparse
import base64
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import urllib.parse
import urllib.request

import fitz
import numpy as np


def main():
    arguments = argparse.ArgumentParser(description=__doc__)
    arguments.add_argument("output", type=Path)
    arguments.add_argument("--http")
    args = arguments.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="oculus-smoke-", dir=args.output.resolve()))
    print(json.dumps({"fixture_root": str(root)}), flush=True)
    pdf_path = root / "lecture 学生 (windows).pdf"
    with fitz.open() as pdf:
        for title, body in (
            ("Vectors and matrices", "The dot product compares the directions of two vectors."),
            ("Calculus and integrals", "An integral measures the area under a curve."),
        ):
            page = pdf.new_page()
            page.insert_text((60, 80), title, fontsize=24)
            page.insert_text((60, 140), body, fontsize=14)
            page.draw_rect(fitz.Rect(80, 200, 400, 380), color=(0.2, 0.3, 0.8), fill=(0.8, 0.9, 1))
        pdf.save(pdf_path)

    started = time.monotonic()
    shutdown = lambda: None

    def http(path, payload=None):
        request = urllib.request.Request(
            args.http.rstrip("/") + path,
            data=json.dumps(payload).encode("utf-8") if payload is not None else None,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=1200) as response:
            return json.load(response)

    try:
        if args.http:
            initial = http("/parse-pdf", {"pdf_path": str(pdf_path), "subject_code": "TEST00001", "backend": "local"})
            print("fast:", initial, flush=True)
            deadline = time.monotonic() + 1200
            while time.monotonic() < deadline:
                status = http("/parse-status?" + urllib.parse.urlencode({"pdf_path": str(pdf_path)}))
                if status["quality_status"] == "done":
                    break
                if status["quality_status"].startswith("error"):
                    raise RuntimeError(status["quality_status"])
                time.sleep(0.5)
            else:
                raise TimeoutError("quality parse did not complete")
            embed = http("/embed-pdf", {"pdf_path": str(pdf_path)})
            query = lambda text: http("/embed-query", {"text": text})
            health = lambda: http("/health")["memory"]
        else:
            import main as service
            from model_workers import MEMORY_GOVERNOR, shutdown_workers
            from parser import parse_fast_isolated, parse_quality

            shutdown = shutdown_workers
            print("fast:", parse_fast_isolated(str(pdf_path)), flush=True)
            print("quality:", parse_quality(str(pdf_path)), flush=True)
            embed = service.embed_pdf_endpoint(service.EmbedRequest(pdf_path=str(pdf_path)))
            query = lambda text: service.embed_query_endpoint(service.QueryRequest(text=text))
            health = MEMORY_GOVERNOR.health
        print("embed:", embed, flush=True)
        metadata = json.loads(pdf_path.with_suffix(".pages.json").read_text(encoding="utf-8"))
        embeddings = json.loads(pdf_path.with_suffix(".emb.json").read_text(encoding="utf-8"))
        assert metadata["mode"] == "quality"
        assert [page["page_no"] for page in metadata["pages"]] == [1, 2]
        assert embeddings["dim"] == 512 and embeddings["dtype"] == "float16"
        vectors = {page["page_no"]: np.frombuffer(base64.b64decode(page["vector"]), dtype="<f2").astype(np.float32) for page in embeddings["pages"]}
        for vector in vectors.values():
            assert vector.shape == (512,) and abs(np.linalg.norm(vector) - 1) < 0.005

        results = []
        for text, expected_page in (
            ("What does the dot product of two vectors compare?", 1),
            ("How do integrals measure the area under a curve?", 2),
        ):
            response = query(text)
            vector = np.frombuffer(base64.b64decode(response["vector"]), dtype="<f2").astype(np.float32)
            assert response["dim"] == 512 and vector.shape == (512,)
            scores = sorted(((page, float(value @ vector)) for page, value in vectors.items()), key=lambda item: item[1], reverse=True)
            assert scores[0][0] == expected_page, (text, scores)
            results.append({"query": text, "expected_page": expected_page, "scores": scores})

        db_path = root / "retrieval.db"
        with sqlite3.connect(db_path) as database:
            database.executescript("""
                CREATE TABLE subjects(id INTEGER PRIMARY KEY, code TEXT NOT NULL, name TEXT NOT NULL);
                CREATE TABLE files(id INTEGER PRIMARY KEY, subject_id INTEGER NOT NULL REFERENCES subjects(id), filename TEXT NOT NULL, relative_path TEXT NOT NULL, file_type TEXT NOT NULL, embed_status TEXT, embedded_at TEXT);
                CREATE TABLE pages(id INTEGER PRIMARY KEY, file_id INTEGER NOT NULL REFERENCES files(id), page_no INTEGER NOT NULL, markdown TEXT NOT NULL DEFAULT '', embedding BLOB, embed_model TEXT, embed_dim INTEGER, embedded_at TEXT, UNIQUE(file_id,page_no));
                INSERT INTO subjects VALUES(1, 'TEST00001_2026_SM2', 'Synthetic Windows smoke');
            """)
            database.execute("INSERT INTO files VALUES(1,1,?,?, 'pdf','done',datetime('now'))", (pdf_path.name, "courses/TEST00001_2026_SM2/" + pdf_path.name))
            markdown = {page["page_no"]: page["markdown"] for page in metadata["pages"]}
            for page in embeddings["pages"]:
                database.execute("INSERT INTO pages(file_id,page_no,markdown,embedding,embed_model,embed_dim,embedded_at) VALUES(1,?,?,?,?,512,datetime('now'))", (page["page_no"], markdown[page["page_no"]], base64.b64decode(page["vector"]), embeddings["model"]))
        memory = health()
        assert memory["cap_mb"] == 8192, memory
        assert memory["measurement_complete"] and memory["kills"] == 0, memory
        report = {"seconds": round(time.monotonic() - started, 1), "pdf": str(pdf_path), "db": str(db_path), "memory": memory, "queries": results}
        (root / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print("SMOKE PASS", json.dumps(report, ensure_ascii=False), flush=True)
    finally:
        shutdown()


if __name__ == "__main__":
    main()
