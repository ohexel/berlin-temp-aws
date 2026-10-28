# 3. Publication through a pointer file

Status: accepted, with a known limitation

## Context

The dashboard is a static site on S3 and CloudFront. A run consists of 543
JSON files (one summary and one file per planning area). Visitors must never
see a half-written run, and CloudFront invalidations should not be needed.

## Decision

- `finalize` writes each run to its own folder, `data/runs/<run>/`, cached for
  a year because a run folder never changes.
- After all files of the run are written, it replaces the pointer
  `data/current.json` (cached 60 s). The frontend reads the pointer first and
  then loads the run it names.
- `finalize` does not publish a run that is older than the run in the pointer.

## Consequences

- A run becomes visible all at once, within about a minute of publication.
- No CloudFront invalidations are needed for new runs.
- Old run folders stay in the bucket. They are small and harmless.
- **Accepted limitation:** the "newer run already published?" check and the
  pointer write are separate steps, and up to 2 finalize invocations can run at
  the same time. If two different runs finish within seconds of each other,
  the pointer can end on the older run until the next publication. This was
  seen once in testing (runs 09:00 and 10:00 on 2026-10-20). It did not occur
  in the 48 h run, where each run was finalized on its own.

## Possible fix (not implemented)

Write the pointer conditionally: remember the pointer's ETag when reading it,
and write with `If-Match` (or `If-None-Match: *` if there was no pointer). If
S3 refuses because the pointer changed, read it again and repeat the check, a
few times at most, then fail so SQS retries the message. Details are in the
[orchestration plan](../orchestration-plan.md#known-limitations-accepted).
