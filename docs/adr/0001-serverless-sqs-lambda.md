# 1. Serverless with SQS and Lambda

Status: accepted (2026-10-13)

## Context

The workload is small and bursty: once per hour, 25 forecast files per model
run are downloaded, converted and aggregated, then the dashboard is updated.
Between runs there is nothing to do. The project runs on a fixed credit budget,
and cost overruns must be avoided. It is also a portfolio project, so the
design should show how failures are handled and monitored.

## Decision

- Lambda functions (container images) for all processing.
- SQS queues between the steps, with one message per unit of work (one per
  run and forecast hour, one per run for publication).
- DynamoDB on demand for state and deduplication.
- S3 with bronze/silver/gold prefixes instead of a database.
- EventBridge Scheduler for the two periodic functions.
- No Step Functions. Retries, dead-letter queues and alarms are built from SQS
  and CloudWatch directly.

## Consequences

- No cost while idle, apart from storage. Costs scale with the number of runs.
- One retry mechanism (SQS redelivery, then a dead-letter queue) covers every
  step.
- Completeness of a run has to be tracked explicitly (the `checker` function
  and DynamoDB items), because there is no workflow engine that knows when all
  25 forecast hours are done.
- Every step must be idempotent, because SQS delivers at least once (see
  ADR 2).

## Alternatives considered

- Managed Airflow (MWAA): a continuously running environment, billed while
  idle, for a job that runs a few minutes per hour.
- Containers on ECS/Fargate: workable, but more moving parts (cluster, task
  definitions, networking) for the same short jobs.
- Step Functions: would model the run as a workflow. Not chosen, so that the
  project shows SQS dead-letter queues and CloudWatch monitoring directly.
- A relational database (RDS) for the layers: dropped in favour of Parquet on
  S3, which has no running cost.
