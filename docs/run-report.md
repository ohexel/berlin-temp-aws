# Continuous run report

Status: preliminary (2026-10-28). Cost figures are estimates from Cost
Explorer, which lags by up to a day; they will be updated once final.

## Setup

- Window: 2026-10-25 06:32 UTC (schedules enabled) to 2026-10-27, before
  the 08:34 UTC tick (schedules disabled), about 50 h.
- Deployment: Terraform `pipeline` module, both container images at commit
  `2882ddf`, settings as in the [orchestration plan](orchestration-plan.md).
- Data sources: DynamoDB state items, CloudWatch Logs Insights over the four
  scheduled and queue-triggered functions, alarm history, SQS attributes,
  Cost Explorer, the dashboard bucket listing.

## Results

| Measure | Result |
|---|---|
| Runs published | 50 of 50 (06:00 UTC on 25 Oct to 07:00 UTC on 27 Oct) |
| Forecast hours ingested | 1,250 of 1,250, all on the first attempt |
| Errors, timeouts, redeliveries | 0 |
| Connection retries to DWD | 0 |
| Messages in dead-letter queues | 0 (both queues) |
| Alarms | No alarm fired during the run (see below) |
| Latency, model run time to publication | 57.6 min for every run |

- **Latency.** The first `discover` tick at :34 found the new run's files
  every hour. With at most 5 new forecast hours per tick, the 25 hours need
  five ticks (:34 to :54); the last one was done at about :54:45. The
  `checker` then requested publication at :57:33, and `finalize` published
  about 5 s later.
- **Alarms.** The "no publication" alarm went from ALARM to OK at 06:58 UTC
  on 25 Oct (first publication). It went back to ALARM at 12:58 UTC on
  27 Oct, about 5 h after the last publication, as expected; its
  notifications were already switched off.
- **Extra work.** The 06:59 and 07:59 ticks on 25 Oct used their spare
  slots for 5 forecast hours each of the 05:00 UTC run, which was still
  inside the discover window. That run never became complete and was not
  published. This explains 1,260 ingest invocations for 1,250 published
  forecast hours.
- **Not observed.** The finalize pointer race (ADR 3) did not occur: every
  run was finalized on its own.

## Lambda measurements

| Function | Invocations | Avg / p95 / max duration | Peak memory / configured | Cold starts (max init) |
|---|---|---|---|---|
| `discover` | 300 | 1.6 / 4.7 / 10.3 s | 100 / 512 MB | 106 (1.1 s) |
| `ingest_lead` | 1,260 | 2.0 / 5.3 / 16.0 s | 308 / 1,024 MB | 269 (5.8 s) |
| `checker` | 596 | 0.15 / 0.21 / 0.67 s | 101 / 512 MB | 25 (1.2 s) |
| `finalize` | 50 | 6.6 / 7.2 / 7.4 s | 287 / 1,024 MB | 50 (4.0 s) |

- All durations are well within the timeouts (60, 120, 60 and 300 s).
- Peak memory stayed below a third of the configured memory for every
  function. The settings were left unchanged for this run; Lambda CPU scales
  with memory, so lower memory may also mean longer durations.
- All cold-start init phases stayed below the 10 s init limit.

## Cost (preliminary)

Usage cost before credits, from Cost Explorer (all days still marked as
estimated).

| Period | Total | Main items |
|---|---|---|
| Run days, 25 to 27 Oct | USD 0.28 | S3 0.22, Cost Explorer queries 0.04, ECR 0.03, DynamoDB < 0.01 |
| Other days, 20 to 24 Oct and 28 Oct | USD 0.07 | S3 0.04, ECR 0.03, DynamoDB < 0.01 |

By `component` tag for the run days: dashboard USD 0.15, data USD 0.07,
ECR USD 0.03, state (DynamoDB) < 0.01, untagged USD 0.04 (Cost Explorer).

- Most of the run cost is S3 requests, mainly the 543 files written to the
  dashboard bucket per published run.
- Lambda, SQS, EventBridge Scheduler, CloudWatch, SNS and CloudFront show
  USD 0.00 for the whole period.
- Days before 20 Oct were not included in this query.

## Teardown (2026-10-28)

- Removed: the `pipeline` module (49 resources, data bucket emptied first),
  the `ci` module (OIDC provider and role), the image-build workflow and its
  secret, the artifacts bucket.
- Kept: the `dashboard` module with the last published run (static snapshot),
  and the Terraform state bucket.
