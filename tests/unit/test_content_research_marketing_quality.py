from __future__ import annotations

import json
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path

from app.content_research.analysis_persistence import (
    EVIDENCE_SNAPSHOT_SCHEMA_VERSION,
    EvidenceSnapshot,
    FrozenEvidenceNote,
)
from app.content_research.marketing_evidence import cluster_atomic_marketing_evidence
from app.content_research.marketing_evidence_extraction import MarketingEvidenceExtractionService

FIXTURE = Path(__file__).parents[1] / "fixtures" / "content_research" / "marketing_conclusion_quality_v1.json"
RESULT = Path(__file__).parents[2] / "docs" / "release" / "2026-08-26-task-3-1-c-quality-evaluation.json"


def _prf(predicted: set, gold: set) -> tuple[float, float, float]:
    true_positive = len(predicted & gold)
    precision = true_positive / len(predicted) if predicted else float(not gold)
    recall = true_positive / len(gold) if gold else float(not predicted)
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return precision, recall, f1


def test_fixed_chinese_marketing_quality_pack_meets_mvp_thresholds() -> None:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    expected_result = json.loads(RESULT.read_text(encoding="utf-8"))
    rows = [
        {**template, "instance": instance}
        for template in fixture["templates"]
        for instance in range(fixture["instances_per_template"])
    ]
    assert len(rows) == 100

    notes = []
    evidence = []
    gold_atoms: set[tuple[str, str, int, int, str]] = set()
    cluster_by_atom_key: dict[tuple[str, str, int, int, str], str] = {}
    cluster_names = sorted({str(row["cluster"]) for row in rows})
    vector_by_cluster = {
        name: tuple(1.0 if index == position else 0.0 for index in range(len(cluster_names)))
        for position, name in enumerate(cluster_names)
    }
    for row_index, row in enumerate(rows):
        note_id = f"note-{row_index}"
        title = row["text"] if row["field_path"] == "title" else ""
        body = row["text"] if row["field_path"] == "content_text" else ""
        notes.append(
            FrozenEvidenceNote(
                note_id=note_id,
                account_id=f"id:author-{row_index}",
                title=title,
                body=body,
                title_hash=f"title-{row_index}",
                body_hash=f"body-{row_index}",
                source_url=f"https://example.test/{note_id}",
                captured_at=datetime(2026, 8, 26, tzinfo=timezone.utc),
                query_provenance=("query-quality",),
            )
        )
        start = row["text"].index(row["quote"])
        end = start + len(row["quote"])
        for track in row["tracks"]:
            evidence.append(
                {
                    "note_id": note_id,
                    "field_path": row["field_path"],
                    "quote": row["quote"],
                    "text_start": start,
                    "text_end": end,
                    "track": track,
                    "aspect": row["cluster"],
                    "evidence_type": "message_expression" if track == "message" else "experience",
                    "polarity": row["polarity"],
                    "scenes": row["scenes"],
                    "audiences": row["audiences"],
                }
            )
            key = (note_id, row["field_path"], start, end, track)
            gold_atoms.add(key)
            cluster_by_atom_key[key] = str(row["cluster"])

    snapshot = EvidenceSnapshot(
        id="quality-snapshot-v1",
        schema_version=EVIDENCE_SNAPSHOT_SCHEMA_VERSION,
        workflow_run_id="quality-run-v1",
        scope_contract_id="quality-scope-v1",
        retrieval_execution_unit_id="quality-retrieval-v1",
        retrieval_attempt_no=1,
        snapshot_fingerprint="quality-snapshot-fingerprint-v1",
        query_groups=({"id": "query-quality", "query": "凉感T恤"},),
        notes=tuple(notes),
        created_at=datetime(2026, 8, 26, tzinfo=timezone.utc),
    )
    atoms = MarketingEvidenceExtractionService._parse_batch(
        snapshot, snapshot.notes, {"evidence": evidence}
    )
    predicted_atoms = {
        (atom.note_id, atom.field_path, atom.text_start, atom.text_end, atom.track)
        for atom in atoms
    }
    span_precision, span_recall, _ = _prf(predicted_atoms, gold_atoms)
    label_f1s = []
    for track in ("need", "value", "message"):
        predicted = {key[:4] for key in predicted_atoms if key[4] == track}
        gold = {key[:4] for key in gold_atoms if key[4] == track}
        label_f1s.append(_prf(predicted, gold)[2])
    track_macro_f1 = sum(label_f1s) / len(label_f1s)
    predicted_counter = {atom.atom_id for atom in atoms if atom.polarity == "counter"}
    gold_counter = {
        atom.atom_id
        for atom in atoms
        if rows[int(atom.note_id.removeprefix("note-"))]["polarity"] == "counter"
    }
    contradiction_precision, contradiction_recall, _ = _prf(predicted_counter, gold_counter)
    vectors = {
        atom.atom_id: vector_by_cluster[
            cluster_by_atom_key[(atom.note_id, atom.field_path, atom.text_start, atom.text_end, atom.track)]
        ]
        for atom in atoms
    }
    clusters = cluster_atomic_marketing_evidence(atoms, vectors)
    predicted_pairs = {
        tuple(sorted(pair))
        for cluster in clusters
        for pair in combinations(cluster.atom_ids, 2)
    }
    gold_pairs = {
        tuple(sorted((left.atom_id, right.atom_id)))
        for left, right in combinations(atoms, 2)
        if left.track == right.track
        and cluster_by_atom_key[(left.note_id, left.field_path, left.text_start, left.text_end, left.track)]
        == cluster_by_atom_key[(right.note_id, right.field_path, right.text_start, right.text_end, right.track)]
    }
    cluster_precision, cluster_recall, _ = _prf(predicted_pairs, gold_pairs)
    notes_by_id = {note.note_id: note for note in snapshot.notes}
    correct_citations = sum(
        (notes_by_id[atom.note_id].title if atom.field_path == "title" else notes_by_id[atom.note_id].body)[atom.text_start : atom.text_end]
        == atom.quote
        for atom in atoms
    )
    citation_correctness = correct_citations / len(atoms)
    citation_completeness = len(predicted_atoms & gold_atoms) / len(gold_atoms)
    fabricated_or_wrong_lineage = len(atoms) - correct_citations

    metrics = {
        "exact_span_precision": span_precision,
        "exact_span_recall": span_recall,
        "track_mapping_macro_f1": track_macro_f1,
        "cluster_pairwise_precision": cluster_precision,
        "cluster_pairwise_recall": cluster_recall,
        "contradiction_precision": contradiction_precision,
        "contradiction_recall": contradiction_recall,
        "citation_correctness": citation_correctness,
        "citation_completeness": citation_completeness,
    }
    assert metrics == expected_result["metrics"]
    assert expected_result["zero_tolerance_failures"] == {
        "fabricated_quote_or_wrong_lineage": fabricated_or_wrong_lineage
    }
    assert span_precision >= 0.95
    assert span_recall >= 0.75
    assert track_macro_f1 >= 0.80
    assert cluster_precision >= 0.85
    assert cluster_recall >= 0.70
    assert contradiction_precision >= 0.85
    assert contradiction_recall >= 0.75
    assert fabricated_or_wrong_lineage == 0
