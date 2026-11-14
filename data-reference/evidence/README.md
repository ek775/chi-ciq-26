# Evidence for speaker attribution and agenda links

Keep reviewed evidence in this directory as one JSONL file per recording.
Check small reviewed files into Git so changes have a review history. For
cluster runs, mount a copy of this directory as an analytics input. Keep
captions, recordings, agendas, and minutes in their source archives.

Each row has a stable `id`, a namespaced `recording_id` such as
`vimeo:1154082574`, `start_s` and `end_s` on the original recording
timeline, a `kind`, and a `source` locator. Optional values are `speaker`,
`person_id`, `role`, `matter_id` (the legislative record number), and
`agenda_item`. A source locator should identify the document and page or
the transcript and line. When using a clipped recording, add its known
offset before looking up evidence.

Speaker names need a time-aligned caption label, self-introduction, clear
recognition followed by the person's turn, explicit document attribution
for the action, or a reviewed recording. Use attendance, committee rosters,
and sponsors to check spelling and find candidates. Record a role by itself
if that's all the source establishes. Use separate agenda evidence to link
a span to a legislative record. Keep evidence ranges as narrow as the source
supports.

## Using it in analytics

The resolver is available as a Python library and CLI. The statement
extraction stage still needs to call it. The intended flow is:

1. Read the published transcript and its timestamped, diarized segments.
   Carry the original recording ID alongside the segments. Diarization
   groups speech under anonymous labels.
2. Ask the LLM to propose statements with quotes and source segment
   references. Treat its output as candidates.
3. Verify each quote against the referenced transcript text. Drop quotes
   that fail verification. Derive the verified span from the transcript
   timestamps and add the original recording offset for clipped inputs.
4. Load the evidence index once per run. For each verified span, call
   `resolve` with the recording ID and original timestamp range. This
   deterministic Python step controls the speaker name. The pipeline
   takes the name from the resolver, even when the result is null.
5. Save the resolved fields and evidence IDs in the verified statement.
   The commitment tracker and digest use those saved fields and cite the
   original recording at the verified start time.

For example, the Finance Committee quote resolves to the role `chair`
and ordinance `SO2025-0021894`, with an unconfirmed speaker name:

```python
from pathlib import Path
from pipelines.analytics.evidence import EvidenceIndex

index = EvidenceIndex.load(Path("data-reference/evidence"))
fields = index.resolve("vimeo:1154082574", 12093.415, 12100.485)
```

The result contains the speaker, confidence, person ID, role, legislative
record number, agenda item, and evidence IDs. Use the result when writing
verified statements and preserve the evidence IDs for audit. The tracker
and digest can read those enriched statements without looking up evidence
themselves. Keep this resolver on the statement path, separate from legacy
voiceprint-based analytics. The tracker candidate manifest carries the
explicit recording IDs needed for the lookup.

An evidence row must cover the quote span to supply a value. Conflicting
overlapping values leave that field unresolved. No match means an
unconfirmed speaker, and no name is inferred from an agenda or role.
The resolver does not check the quote text, extend an attribution past
its supported span, or validate a source locator against the archive.

Run a lookup without a database or transcription models:

```sh
PYTHONPATH=src python -m pipelines.analytics.evidence \
  data-reference/evidence vimeo:1154082574 12093.415 12100.485
```

The checked-in files cover selected spans in three recordings. The first
two link the Congress Theater item to `SO2025-0021894` and identify the
Finance Committee chair's role. The third shows a caption-based
self-introduction from a separate meeting. This is a starting collection,
with caption-derived examples still to be checked against the recordings.
