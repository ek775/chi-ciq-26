"""Build and query an alderman speech and action dataset."""

import argparse
import json
import os
from pathlib import Path

from pipelines.analytics.evidence import EvidenceIndex
from pipelines.tracker.actions import read_actions
from pipelines.tracker.statements import (
    atomic_write,
    build_statements,
    read_segments,
    write_statements,
)


def _reviewed_views(manifest: dict, statements: dict) -> dict:
    ids = {key: row["statement_id"] for key, row in statements.items()}

    def bind(item):
        return {**item, "evidence": [ids[key] for key in item["evidence"]]}

    follow_up = manifest.get("follow_up")
    if follow_up:
        follow_up = {**bind(follow_up), "commitment": ids[follow_up["commitment"]]}
    digest = manifest.get("digest")
    if digest:
        digest = {**digest, "bullets": [bind(item) for item in digest["bullets"]]}
    return {"follow_up": follow_up, "digest": digest}


def make_dataset(manifest, segments, evidence, actions):
    statements, rejected = build_statements(manifest, segments, evidence)
    try:
        views = _reviewed_views(manifest, statements)
    except KeyError as exc:
        raise ValueError(
            f"Reviewed view references a missing or rejected candidate: {exc}"
        ) from exc
    unique = {row["statement_id"]: row for row in statements.values()}
    dataset = {
        "coverage": manifest.get("coverage", "candidate spans only"),
        "source_keys": sorted({c["published_source_key"] for c in manifest["candidates"].values()}),
        "statements": list(unique.values()),
        "actions": actions,
        "rejected_candidates": rejected,
        **views,
    }
    return dataset, statements


def retrieve(dataset, person_id, start, end):
    def matches(row):
        return (
            (row.get("person_id") or "").casefold() == person_id.casefold()
            and (start is None or row["date"] >= start)
            and (end is None or row["date"] <= end)
        )

    return {
        "coverage": dataset["coverage"],
        "source_keys": dataset["source_keys"],
        "statements": sorted(filter(matches, dataset["statements"]), key=lambda r: r["date"]),
        "actions": sorted(filter(matches, dataset["actions"]), key=lambda r: r["date"]),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    build.add_argument("--candidates", type=Path, required=True)
    build.add_argument("--evidence-dir", type=Path, required=True)
    build.add_argument("--legislation", type=Path, action="append", default=[])
    build.add_argument("--output-dir", type=Path, required=True)
    query = commands.add_parser("query")
    query.add_argument("dataset", type=Path)
    query.add_argument("--person-id", required=True)
    query.add_argument("--from-date")
    query.add_argument("--through-date")
    args = parser.parse_args()

    try:
        if args.command == "query":
            dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
            print(json.dumps(
                retrieve(dataset, args.person_id, args.from_date, args.through_date),
                indent=2, ensure_ascii=False,
            ))
            return

        database_url = os.environ.get("MEETINGS_DATABASE_URL") or os.environ.get("DATABASE_URL")
        if not database_url:
            parser.error("Set MEETINGS_DATABASE_URL to a meetings database reader URL")
        manifest = json.loads(args.candidates.read_text(encoding="utf-8"))
        keys = sorted({c["published_source_key"] for c in manifest["candidates"].values()})
        segments = read_segments(database_url, keys)
        missing = set(keys) - {key for key, _ in segments}
        if missing:
            raise ValueError(f"No published transcript segments for: {', '.join(sorted(missing))}")
        dataset, statements = make_dataset(
            manifest, segments, EvidenceIndex.load(args.evidence_dir),
            read_actions(args.legislation),
        )
        write_statements(args.output_dir, segments, statements)
        path = args.output_dir / "tracker.json"
        atomic_write(path, json.dumps(dataset, indent=2, ensure_ascii=False) + "\n")
        print(
            f"{path}: {len(dataset['statements'])} statements, "
            f"{len(dataset['actions'])} actions, "
            f"{len(dataset['rejected_candidates'])} rejected candidates"
        )
    except (ValueError, KeyError, OSError) as exc:
        parser.exit(1, f"Tracker: {exc}\n")


if __name__ == "__main__":
    main()
