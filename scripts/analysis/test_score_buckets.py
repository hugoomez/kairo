"""Tests for score_buckets.py (invented predictions only, written under tmp_path)."""
import csv
import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import score_buckets as sb  # noqa: E402


def run(capsys, *args):
    code = sb.main(list(args))
    return code, json.loads(capsys.readouterr().out)


def test_labels_and_bucket_edges():
    cuts = sb.parse_cuts("0.2,0.5,0.9")
    names = sb.labels(cuts)
    assert names == ["<0.2", "0.2-0.5", "0.5-0.9", ">=0.9"]
    assert sb.bucket(0.19, cuts, names) == "<0.2"
    assert sb.bucket(0.2, cuts, names) == "0.2-0.5"       # closed below
    assert sb.bucket("0.9", cuts, names) == ">=0.9"
    assert sb.bucket(None, cuts, names) == sb.NO_SCORE
    assert sb.bucket("n/a", cuts, names) == sb.NO_SCORE
    assert sb.bucket(True, cuts, names) == sb.NO_SCORE
    assert sb.labels([1.0, 2.0]) == ["<1", "1-2", ">=2"]


def test_jsonl_copy_keeps_rows_and_prints_only_counts(tmp_path, capsys):
    src = tmp_path / "pred.jsonl"
    rows = [{"id": f"u{i}", "texto": f"inventado {i}", "puntuacion": i / 10} for i in range(10)]
    rows.append({"id": "u10", "texto": "sin puntuar"})
    src.write_bytes("".join(json.dumps(r) + "\n" for r in rows).encode("utf-8"))
    out = tmp_path / "salida" / "pred-buckets.jsonl"
    code, res = run(capsys, "--in", str(src), "--score", "puntuacion", "--cuts", "0.5,0.8", "--out", str(out))
    assert code == 0
    assert res["buckets"] == {"<0.5": 5, "0.5-0.8": 3, ">=0.8": 2, "sin_puntuacion": 1}
    assert res["rows"] == 11 and res["out_sha256"] == hashlib.sha256(out.read_bytes()).hexdigest()
    assert "inventado" not in json.dumps(res)
    got = [json.loads(x) for x in out.read_text(encoding="utf-8").splitlines()]
    assert [g["id"] for g in got] == [r["id"] for r in rows]
    assert got[0] == {**rows[0], "score_bucket": "<0.5"} and got[10]["score_bucket"] == "sin_puntuacion"


def test_csv_and_refusals(tmp_path, capsys):
    src = tmp_path / "pred.csv"
    src.write_text("id,score\na,0.1\nb,0.95\n", encoding="utf-8", newline="")
    out = tmp_path / "o.csv"
    code, res = run(capsys, "--in", str(src), "--score", "score", "--cuts", "0.9", "--out", str(out),
                    "--column", "cubo")
    assert code == 0 and res["buckets"] == {"<0.9": 1, ">=0.9": 1}
    with open(out, encoding="utf-8", newline="") as fh:
        assert [r["cubo"] for r in csv.DictReader(fh)] == ["<0.9", ">=0.9"]
    # existing --out, wrong column, bad cuts, relative out, colliding column
    assert run(capsys, "--in", str(src), "--score", "score", "--cuts", "0.9", "--out", str(out))[0] == 3
    assert run(capsys, "--in", str(src), "--score", "nada", "--cuts", "0.9",
               "--out", str(tmp_path / "x.csv"))[0] == 3
    assert run(capsys, "--in", str(src), "--score", "score", "--cuts", "0.9,0.5",
               "--out", str(tmp_path / "y.csv"))[0] == 3
    assert run(capsys, "--in", str(src), "--score", "score", "--cuts", "0.9", "--out", "rel.csv")[0] == 3
    assert run(capsys, "--in", str(src), "--score", "score", "--cuts", "0.9", "--column", "id",
               "--out", str(tmp_path / "z.csv"))[0] == 3
    assert not (tmp_path / "x.csv").exists() and not (tmp_path / "z.csv").exists()
