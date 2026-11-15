"""Host-side per-recording job preparation and explicit Fuzzball submission."""

import argparse
import hashlib
import json
import logging
import os
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote, urlsplit
from uuid import uuid4

from dotenv import load_dotenv

from shared.logging import configure_logging

from .inputs import (
    content_digest,
    discover_recordings,
    load_metadata_file,
    meeting_filename,
)


def recording_jobs(
    directory: Path,
    *,
    image: str,
    namespace: str,
    worker_input_dir: str,
    input_base_uri: str | None = None,
    force: bool = False,
    metadata: dict[str, str] | None = None,
    audio_meeting_map: str | None = None,
) -> list[dict[str, Any]]:
    if not image.strip() or not namespace.strip():
        raise ValueError("Worker image and source namespace must not be empty")
    mount = PurePosixPath(worker_input_dir)
    if not mount.is_absolute() or ".." in mount.parts:
        raise ValueError("Worker input directory must be an absolute container path")
    if audio_meeting_map is not None:
        map_path = PurePosixPath(audio_meeting_map)
        if not map_path.is_absolute() or ".." in map_path.parts:
            raise ValueError(
                "Worker audio meeting map must be an absolute container path"
            )
    if input_base_uri is not None:
        uri = urlsplit(input_base_uri)
        if uri.scheme not in {"s3", "https"} or not uri.netloc:
            raise ValueError("Input base URI must be an s3:// or https:// directory")
        if uri.query or uri.fragment or uri.username or uri.password:
            raise ValueError(
                "Input base URI must not contain credentials/query/fragment"
            )
    jobs = []
    for recording in discover_recordings(directory, metadata):
        digest = content_digest(recording)
        key = f"{namespace}/{recording.name}"
        filename = meeting_filename(recording, metadata or {})
        identity = f"{key}\0{digest}"
        if filename != recording.name:
            identity += f"\0{filename}"
        job_id = hashlib.sha256(identity.encode()).hexdigest()[:24]
        worker_path = str(mount / recording.name)
        command = [
            "chi-ciq-ingest",
            "--input-file",
            worker_path,
            "--namespace",
            namespace,
            "--expected-sha256",
            digest,
        ]
        if filename != recording.name:
            command.extend(["--meeting-filename", filename])
        if audio_meeting_map is not None:
            command.extend(["--audio-meeting-map", audio_meeting_map])
        if force:
            command.append("--force")
        jobs.append(
            {
                "job.name": f"transcribe-{job_id}",
                "worker.image": image,
                "worker.command": command,
                "input.path": worker_path,
                "input.filename": recording.name,
                "input.sha256": digest,
                "input.uri": (
                    input_base_uri.rstrip("/") + "/" + quote(recording.name, safe="")
                    if input_base_uri is not None
                    else None
                ),
                "source.key": key,
            }
        )
    return jobs


def render_template(value: Any, job: dict[str, Any]) -> Any:
    """Replace whole JSON values, preserving argv lists without shell interpolation."""
    if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
        key = value[2:-1]
        if key not in job or job[key] is None:
            raise ValueError(f"Missing template value: {key}")
        return job[key]
    if isinstance(value, str) and "${" in value:
        raise ValueError("Template placeholders must occupy an entire JSON value")
    if isinstance(value, list):
        return [render_template(item, job) for item in value]
    if isinstance(value, dict):
        return {key: render_template(item, job) for key, item in value.items()}
    return value


def write_json(path: Path, value: Any) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def prepare_run(
    output: Path, jobs: list[dict[str, Any]], template: dict[str, Any] | None
) -> tuple[Path, list[Path]]:
    # Render every job before writing anything; malformed templates fail preflight.
    rendered = []
    if template is not None:
        if "${worker.image}" not in json.dumps(template):
            raise ValueError("Fuzzfile template must use ${worker.image}")
        if "${worker.command}" not in json.dumps(template):
            raise ValueError("Fuzzfile template must use ${worker.command}")
        rendered = [render_template(template, job) for job in jobs]
    output.mkdir(parents=True, exist_ok=True)
    run = output / f"dispatch-{uuid4().hex}"
    run.mkdir(mode=0o700)
    write_json(
        run / "plan.json",
        {"jobs": jobs, "submission_status": "See submissions.jsonl and CLI receipts"},
    )
    paths = []
    for job, value in zip(jobs, rendered, strict=False):
        path = run / f"{job['job.name']}.json"
        write_json(path, value)
        paths.append(path)
    return run, paths


def submit_run(run: Path, paths: list[Path], *, fuzzball: str, max_jobs: int) -> None:
    if not paths:
        raise ValueError("Submission requires a cluster-specific Fuzzfile template")
    if max_jobs < 1 or len(paths) > max_jobs:
        raise ValueError(f"Submission has {len(paths)} jobs; --max-jobs is {max_jobs}")
    for path in paths:
        subprocess.run([fuzzball, "workflow", "validate", str(path)], check=True)
    # Keep every CLI receipt, even when a later submission fails. No silent retry:
    # submission is not proof of completed inference or successful publication.
    with (run / "submissions.jsonl").open("x", encoding="utf-8") as journal:
        for path in paths:
            with (run / f"{path.stem}.receipt").open("x", encoding="utf-8") as receipt:
                result = subprocess.run(
                    [
                        fuzzball,
                        "workflow",
                        "start",
                        str(path),
                        "--name",
                        f"{path.stem}-{run.name[-8:]}",
                        "--output",
                        "json",
                    ],
                    stdout=receipt,
                    check=False,
                )
                receipt.flush()
                os.fsync(receipt.fileno())
            journal.write(
                json.dumps({"fuzzfile": path.name, "returncode": result.returncode})
                + "\n"
            )
            journal.flush()
            os.fsync(journal.fileno())
            if result.returncode:
                raise subprocess.CalledProcessError(result.returncode, result.args)


def main(argv: list[str] | None = None) -> None:
    load_dotenv()
    configure_logging("dispatch")
    parser = argparse.ArgumentParser(
        description="Prepare one Fuzzball transcription/diarization job per MP4"
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=Path(os.environ.get("TRANSCRIPTION_INPUT_DIR", "data/mp4")),
    )
    parser.add_argument("--image", default=os.environ.get("TRANSCRIPTION_WORKER_IMAGE"))
    parser.add_argument("--namespace", default="recordings")
    parser.add_argument(
        "--metadata-file",
        type=Path,
        help="JSON mapping Vimeo IDs to dated MP4 filenames",
    )
    parser.add_argument(
        "--audio-meeting-map",
        help="Worker-visible path to the archive metadata map",
    )
    parser.add_argument("--worker-input-dir", default="/inputs")
    parser.add_argument(
        "--input-base-uri",
        default=os.environ.get("TRANSCRIPTION_INPUT_BASE_URI") or None,
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--template",
        type=Path,
        help="Cluster-specific Fuzzfile JSON template (JSON is also valid YAML)",
    )
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--submit", action="store_true")
    parser.add_argument(
        "--fuzzball", default=os.environ.get("FUZZBALL_BIN", "fuzzball")
    )
    parser.add_argument(
        "--max-jobs", type=int, default=32, help="Maximum jobs allowed per submission"
    )
    args = parser.parse_args(argv)
    if not args.image:
        parser.error("--image or TRANSCRIPTION_WORKER_IMAGE is required")
    if args.submit and args.template is None:
        parser.error("--submit requires a cluster-specific --template")
    if args.max_jobs < 1:
        parser.error("--max-jobs must be positive")
    jobs = recording_jobs(
        args.input_dir,
        image=args.image,
        namespace=args.namespace,
        worker_input_dir=args.worker_input_dir,
        input_base_uri=args.input_base_uri,
        force=args.force,
        metadata=load_metadata_file(args.metadata_file),
        audio_meeting_map=args.audio_meeting_map,
    )
    if args.submit and len(jobs) > args.max_jobs:
        parser.error(f"Found {len(jobs)} recordings; increase --max-jobs explicitly")
    template = None
    if args.template is not None:
        template = json.loads(args.template.read_text(encoding="utf-8"))
        if not isinstance(template, dict):
            parser.error("Fuzzfile template must be a JSON object")
    run, paths = prepare_run(args.output_dir, jobs, template)
    logging.getLogger(__name__).info("Prepared %s worker jobs in %s", len(jobs), run)
    print(f"Prepared {len(jobs)} independent worker jobs in {run}", flush=True)
    if args.submit:
        submit_run(run, paths, fuzzball=args.fuzzball, max_jobs=args.max_jobs)
        logging.getLogger(__name__).info("Submitted worker jobs")
        print("Submitted jobs; inspect CLI receipts and workflow status", flush=True)
    else:
        print("Not submitted (offline preparation only)", flush=True)


if __name__ == "__main__":
    main()
