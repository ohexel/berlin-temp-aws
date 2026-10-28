# 4. Gentle retries for an unreliable upstream

Status: accepted

## Context

All forecast data comes from the DWD open-data server. In test runs, the
CloudWatch logs showed connection resets and timeouts from that server. SQS
redelivery retries a whole Lambda invocation, which is slow for a single
dropped connection. Aggressive retries, on the other hand, put more load on a
free public service.

## Decision

- Retry only connection-level failures (reset, timeout, TLS handshake
  failure), not HTTP answers and not certificate errors.
- Two retries, after about 2 s and 5 s, each randomised by +/-50 % so that
  parallel workers do not retry at the same moment. Every retry is logged.
- Bounded socket timeouts that fit the Lambda limits: 15 s per attempt for the
  directory listing (3 attempts plus pauses stay within `discover`'s 60 s), 10 s
  for file checks, 120 s for downloads.
- Less parallelism: 4 parallel file checks per `discover` tick (was 8), at most
  5 new forecast hours per tick, ingest concurrency 5.
- Anything that still fails is left to SQS redelivery and, after 5 attempts,
  the dead-letter queue.

## Consequences

- A single dropped connection costs seconds, not a full redelivery.
- The load on DWD stays low and predictable.
- In the 48 h run, no retries were needed (0 warnings in the logs).
