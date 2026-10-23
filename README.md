# berlin-temp-aws

24-hour apparent-temperature forecast for Berlin's 542 planning areas
(Planungsraeume), built as a serverless, event-driven, medallion-style data
pipeline on AWS (eu-central-1). Portfolio project: built to be deployed,
run for a documented period, costed, and torn down. Not intended to run
indefinitely.

Live dashboard (static snapshot of published runs):
https://d14k2nin89y0qt.cloudfront.net

## What it does

- Input: DWD open data, ICON-D2-RUC model. Hourly runs, 25 forecast hours,
  four variables, 542,040-point grid (GRIB2).
- Static inputs: planning-area boundaries (Berlin WFS), area names,
  population including residents aged 65+.
- Method: apparent temperature per grid cell, then area-weighted to planning
  areas through a cell-by-area intersection table.
- Output: static dashboard (map plus detail chart per area) on S3 and
  CloudFront. Each run is published atomically through a pointer file.

## Architecture

SQS + Lambda, no Step Functions. Five Lambda functions from two container
images:

| Function | Trigger | Job |
|---|---|---|
| `discover` | Scheduler (:34-:59 UTC, every 5 min) | Find new run/lead files at DWD, create state items, queue work |
| `ingest_lead` | SQS (one message per run and lead) | GRIB to Parquet (bronze, silver) |
| `checker` | Scheduler (every 5 min) | Queue finalize once a run has all 25 leads |
| `finalize` | SQS (one message per run) | Silver to gold, dashboard JSON, pointer |
| `reference_build` | Manual, once | Static reference data and map geometry |

Supporting services: DynamoDB (state, deduplication), S3 (data bucket and
dashboard bucket), CloudFront with Origin Access Control, ECR, SNS e-mail
alerts, CloudWatch alarms and logs, one least-privilege IAM role per function.

Design, flow logic, settings and accepted limitations:
[docs/orchestration-plan.md](docs/orchestration-plan.md).
Diagrams: [docs/diagrams/](docs/diagrams/).
Decisions: [docs/adr/](docs/adr/).

Key properties:

- One retry mechanism: SQS redelivery, 5 attempts, then dead-letter queue and
  alarm. No automatic redrive.
- Idempotent steps (conditional DynamoDB writes, fixed S3 keys, finalize stops
  on duplicate or stale runs), so the account limit of 10 concurrent Lambdas
  needs no reserved concurrency.
- Gentle retries for the upstream server: connection-level failures only,
  bounded timeouts, limited parallelism.

## Repository layout

| Path | Content |
|---|---|
| `src/berlin_temp/` | Pipeline code |
| `tests/` | pytest suite |
| `Dockerfile` | Two build targets: `pipeline`, `reference` |
| `infra/terraform/pipeline/` | Processing resources |
| `infra/terraform/dashboard/` | Dashboard bucket and CloudFront |
| `infra/terraform/ci/` | GitHub OIDC provider and image-push role |
| `web/` | Dashboard frontend |
| `.github/workflows/` | `ci.yml` (checks), `images.yml` (image build and push) |

The three Terraform root modules have separate state, so the dashboard can
outlive the pipeline teardown.

## Development

```bash
uv sync
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

## Deployment

Account-specific values (account ID, bucket names, alert e-mail) are not in
the repository. They live in git-ignored files; `*.example` files show the
keys.

1. Create the Terraform state bucket once with the AWS CLI. Copy
   `infra/terraform/backend.hcl.example` to `infra/terraform/backend.hcl` and
   fill in the bucket name.
2. In each module, copy `terraform.tfvars.example` to `terraform.tfvars` and
   fill in the values.
3. `ci` module: set `github_subject_repository` (owner and repository IDs, as
   in the OIDC `sub` claim), then apply. Store the output `images_role_arn` as
   the GitHub secret `CI_IMAGES_ROLE_ARN`.
4. Push to `main`. The `images` workflow builds both images and pushes them
   to ECR, tagged with the short git SHA.
5. Apply the `pipeline` module with the image tags:

```bash
cd infra/terraform/pipeline
terraform init -backend-config=../backend.hcl
terraform apply -var 'pipeline_image_tag=<sha>' \
  -var 'reference_image_tag=<sha>'
```

6. Apply the `dashboard` module, upload `web/` and the map geometry.
7. Invoke `reference_build` once.

Deployment (`terraform apply`) is deliberately manual. CI runs ruff, pytest,
and `terraform fmt -check` and `validate` for all modules.

Since this is a single-user demo project, there are no pull requests. If we
anticipated any (if this became a team project or big enough to earn outside
PRs), we would probably want `terraform plan` to run oon pull requests too,
with a read-only role (separate OIDC role, trust limited to pull request
events).

## Running and stopping

Schedules and the no-publication alarm are off by default. Turn them on:

```bash
terraform apply -var 'schedules_enabled=true' \
  -var 'no_publication_alarm_enabled=true'
```

A plain `terraform apply` switches both off again. Always confirm that both
schedules show DISABLED after a run.

## Tests

Unit tests: pytest suite (botocore Stubber, fakes, synthetic GRIB files).

Operational tests on AWS, run once with the CLI-built resources and again
after the Terraform rebuild:

1. Hand-written message: silver output and item status correct.
2. Same message twice: no duplicates.
3. Broken message: 5 failures, DLQ, alarm e-mail, manual redrive.
4. Checker with 25 DONE leads: exactly one finalize message.
5. Two finalize messages for one run: one publication.
6. Both schedules enabled for one hour: one complete run end to end.

Continuous run (24 h) and cost report: pending.

## Known limitations

- **Finalize pointer race.** If two different runs are finalized within
  seconds of each other, the pointer can end on the older run. Effect: the
  dashboard shows an older, valid run until the next publication. A fix with
  conditional S3 writes (ETag) is designed in the orchestration plan, not
  implemented.
- **discover crash between item creation and queue send** leaves a lead
  QUEUED without a message. The "no publication" alarm is the only signal.
  Recovery is documented in the plan.
- Lambda timeouts and memory sizes are provisional until measured on the
  continuous run.

## Repository history

Development started in a separate, project-specific GitHub account. This was
my first substantial LLM-assisted project, with direct push access and a link
to a live AWS account. I used a separate account and private repo to limit
the fallout from any mistakes or leaks along the way. After the build, the
repository was moved to this account. History was rewritten to replace
account-specific identifiers with placeholders, and the repository was
recreated. Commit authorship therefore shows the `ohexel` identity and, where
applicable, a Claude co-author trailer.
