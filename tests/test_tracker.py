import json
import unittest
from pathlib import Path

from pipelines.analytics.evidence import Evidence, EvidenceIndex
from pipelines.tracker.__main__ import make_dataset, retrieve
from pipelines.tracker.statements import build_statements

ROOT = Path(__file__).resolve().parents[1]


def demo_inputs():
    manifest = json.loads(
        (ROOT / "tests/fixtures/tracker/candidates.json").read_text()
    )
    segments = {}
    for candidate in manifest["candidates"].values():
        source = candidate["published_source_key"]
        sequence = candidate["segment_sequence"]
        council = source.endswith("1156547113.mp4")
        segments[source, sequence] = {
            "meeting_id": f"7de00000-0000-4000-8000-00000000000{2 if council else 1}",
            "meeting_date": "2026-01-21" if council else "2026-01-14",
            "meeting_type": "City Council" if council else "Committee on Finance",
            "sequence": sequence,
            "text": candidate["quote"],
            "start_seconds": candidate["start_s"],
            "end_seconds": candidate["end_s"],
            "transcription_model": "reference-autocaptions",
            "content_sha256": "0" * 64,
        }
    return manifest, segments


class TrackerTests(unittest.TestCase):
    def test_demo_links_verified_quotes_and_keeps_unknown_speaker(self):
        manifest, segments = demo_inputs()
        evidence = EvidenceIndex.load(ROOT / "data-reference/evidence")
        dataset, statements = make_dataset(manifest, segments, evidence, [])
        promise = statements["report-out"]
        self.assertIsNone(promise["speaker"])
        self.assertEqual(promise["role"], "chair")
        self.assertEqual(promise["matter_id"], "SO2025-0021894")
        self.assertTrue(promise["video_link"].endswith("#t=3h21m33s"))
        self.assertEqual(dataset["follow_up"]["commitment"], promise["statement_id"])
        self.assertIn(promise["statement_id"], dataset["digest"]["bullets"][-1]["evidence"])

    def test_rejects_invented_quote_and_broken_citation(self):
        manifest, segments = demo_inputs()
        manifest["candidates"]["report-out"]["quote"] = "This was never said."
        evidence = EvidenceIndex([])
        statements, rejected = build_statements(manifest, segments, evidence)
        self.assertNotIn("report-out", statements)
        self.assertIn("report-out", rejected)
        with self.assertRaisesRegex(ValueError, "report-out"):
            make_dataset(manifest, segments, evidence, [])

    def test_person_query_uses_evidence_identity(self):
        manifest, segments = demo_inputs()
        candidate = manifest["candidates"]["report-out"]
        candidate["speaker"] = "Suggested by extractor"
        evidence = EvidenceIndex([
            Evidence(
                "test-name", candidate["recording_id"],
                candidate["start_s"], candidate["end_s"],
                "reviewed_recording", "test recording",
                speaker="Confirmed name", person_id="PERSON-A",
            )
        ])
        dataset, _ = make_dataset(manifest, segments, evidence, [])
        result = retrieve(dataset, "person-a", "2026-01-14", "2026-01-14")
        self.assertEqual(len(result["statements"]), 1)
        self.assertEqual(result["statements"][0]["speaker"], "Confirmed name")
        self.assertEqual(result["statements"][0]["evidence_ids"], ["test-name"])
        self.assertEqual(retrieve(dataset, "person-a", "2026-01-15", None)["statements"], [])

    def test_unknown_meeting_date_remains_null_and_is_excluded_from_date_ranges(self):
        manifest, segments = demo_inputs()
        candidate = manifest["candidates"]["report-out"]
        segment = segments[
            candidate["published_source_key"], candidate["segment_sequence"]
        ]
        segment["meeting_date"] = None
        segment["meeting_type"] = None
        evidence = EvidenceIndex([
            Evidence(
                "test-name", candidate["recording_id"],
                candidate["start_s"], candidate["end_s"],
                "reviewed_recording", "test recording",
                speaker="Confirmed name", person_id="PERSON-A",
            )
        ])

        dataset, statements = make_dataset(manifest, segments, evidence, [])
        statement = statements["report-out"]
        self.assertIsNone(statement["date"])
        self.assertIsNone(statement["body"])
        self.assertEqual(
            len(retrieve(dataset, "person-a", None, None)["statements"]), 1
        )
        self.assertEqual(
            retrieve(dataset, "person-a", "2026-01-01", None)["statements"], []
        )
