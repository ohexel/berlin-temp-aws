# 2. Idempotency instead of reserved concurrency

Status: accepted

## Context

The AWS account has a limit of 10 concurrent Lambda executions. That limit does
not allow reserved concurrency for individual functions. Without reservations,
two invocations of the same function can overlap, for example a slow
`discover` tick and the next one, or two deliveries of the same SQS message.

## Decision

Make every step safe to run twice instead of preventing overlap:

- `discover` creates a lead item only if it does not exist yet (conditional
  put), and sends a message only after that put succeeded.
- `ingest_lead` writes to fixed S3 keys (a repeat overwrites with identical
  content) and sets a lead to DONE only if it is not DONE already.
- `checker` records that it requested publication, and `finalize` stops if the
  run is already published or a newer run is already published.
- The SQS triggers limit concurrency (ingest 5, finalize 2) so the pipeline
  cannot use the whole account limit.

## Consequences

- Duplicate messages and overlapping invocations are harmless. This was tested
  on AWS (same message twice, two finalize messages for one run).
- One race remains: two different runs finalized at the same time can leave
  the pointer on the older run (see ADR 3).

## Alternatives considered

- Reserved concurrency of 1 for `discover` and `finalize`: not possible with
  the account limit.
- A maximum concurrency of 1 on the finalize trigger: the minimum for an SQS
  trigger is 2.
