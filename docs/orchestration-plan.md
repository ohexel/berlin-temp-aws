# Orchestration plan — Berlin temperature pipeline (AWS)

Status: draft for review (revision 3) · Scope: version 1 (25-point apparent-temperature forecast per PLR, population 65+, PLR names)

## Decisions this plan implements

| Topic | Decision |
|---|---|
| Orchestration | SQS + Lambda (no Step Functions) |
| Unit of work | One message per (run, lead); one finalize message per run |
| Retries | One mechanism only: SQS redelivery, `maxReceiveCount` = 5, then dead-letter queue (DLQ) |
| Failed messages | DLQ → alarm e-mail → manual investigation and redrive; no automatic redrive |
| State | DynamoDB: deduplication, lead status, finalize bookkeeping |
| Completeness | A separate **checker** Lambda on its own schedule (every 5 minutes, all hour, created disabled) decides when a run has all 25 leads DONE and queues finalize. Ingest workers only process leads; they do not count or hand off. |
| Duplicate finalize | Finalize is idempotent: it stops if the run is already published, so a duplicate finalize message is harmless |
| Stale runs | Finalize stops if a newer run is already published |
| Gold | Written once per run, by finalize, after the full horizon passes its checks |
| Static reference build | Separate Lambda, invoked manually, run once |
| Container images | Two images from one Dockerfile with two build targets: `reference` (for `reference_build`, includes the geospatial libraries) and `pipeline` (all other Lambdas) |
| Image registry | One ECR repository per image: `berlin-temp-pipeline` and `berlin-temp-reference` |
| Lambda CPU architecture | x86_64 (native builds and local tests; arm64 saving is negligible at this scale) |
| GRIB decoder | `eccodes` |
| `reference` image libraries | shapely, pyproj, openpyxl, netCDF4 (on top of the `pipeline` set: boto3, eccodes, numpy, pandas, pyarrow) |
| discover | Runs newest first, leads ascending; retention window = current UTC hour and the two before; existing lead items are read with one query per run before any HEAD request to DWD |
| Tick times | discover fires at :34, :39 … :59 UTC (six ticks, 5 new leads each, 30 slots for 25 leads). DWD publishes a run's first lead at about :30 and the last at about :40, so the first tick is likely to find leads |
| Connection retries | Calls to DWD (directory listing, HEAD, GRIB download) and the other source downloads retry only connection-level failures (reset, timeout, TLS handshake failure; not HTTP answers, not certificate errors): two retries after about 2 s and 5 s, each randomised by +/-50 %. Every retry is logged. HEAD checks use 4 parallel workers (was 8). The directory listing waits at most 15 s per attempt (3 attempts plus pauses stay within the 60 s Lambda limit); other downloads keep 120 s. No time budget per tick and no change of the 5-lead cap |
| Gold | `gold/forecast_plr/run=<run>/` (542 × 25 rows: area-weighted temperature and apparent temperature) and `gold/forecast_plr_summary/run=<run>/` (542 rows: highest temperature and the largest apparent-minus-air difference with their earliest hour, population, status; the difference is the hour with the largest magnitude in either direction, reported with its sign, ties to the earliest hour). No historical comparison as in capstone Dagster version |
| Publication | Dashboard bucket: frontend and `data/geometry.json` uploaded by hand; per run `data/runs/<run>/summary.json` and `areas/<plr_id>.json`; pointer `data/current.json` written last; long cache for run folders, 60 s for the pointer; no CloudFront invalidation |
| Map boundaries | Simplified once (20 m, topology preserved) by `reference_build` to `gold/static/plr_geometry.json` |
| Lead label | DWD-native `PTxxxH00M` (e.g. `PT005H00M`) everywhere: messages, DynamoDB sort key, S3 keys, DWD URLs |
| Data bucket | One private bucket for bronze/silver/gold; lifecycle: raw GRIB 2 days, silver `forecast_cell` 7 days; static bronze and gold kept until final teardown; unfinished multipart uploads aborted after 1 day |
| Alerts | SNS e-mail: messages in a DLQ, no publication for 3 h, discover/finalize errors |

## System overview

```mermaid
flowchart LR
    classDef store fill:#eef1f7,stroke:#5b6b8c,color:#2d3a55
    classDef fail fill:#fde2e2,stroke:#c0392b,color:#7b1d1d
    classDef ext fill:#fff7e0,stroke:#b8860b,color:#5c4300

    DWD["DWD open data<br/>ICON-D2-RUC GRIB2"]:::ext
    SCHED1(["Scheduler<br/>discover: :34–:59 every 5 min"])
    SCHED2(["Scheduler<br/>checker: every 5 min"])
    DISC["discover<br/>Lambda"]
    CHK["checker<br/>Lambda"]
    IQ[["ingest-queue"]]:::store
    ING["ingest_lead<br/>Lambda"]
    FQ[["finalize-queue"]]:::store
    FIN["finalize<br/>Lambda"]
    DDB[("DynamoDB<br/>berlin-temp-state")]:::store
    S3[("S3 data bucket<br/>bronze / silver / gold")]:::store
    WEB[("S3 dashboard bucket<br/>+ CloudFront")]:::store
    IDLQ[["ingest-dlq"]]:::fail
    FDLQ[["finalize-dlq"]]:::fail
    CW["CloudWatch alarms"]
    SNS["SNS → e-mail"]

    SCHED1 --> DISC
    DISC -- "list / check files" --> DWD
    DISC -- "create lead item if absent" --> DDB
    DISC -- "one message per new (run, lead)" --> IQ
    IQ --> ING
    ING -- "download GRIB" --> DWD
    ING -- "bronze GRIB, silver forecast_cell" --> S3
    ING -- "mark lead DONE" --> DDB
    SCHED2 --> CHK
    CHK -- "count DONE leads per run" --> DDB
    CHK -- "25 DONE, not yet requested → one message per run" --> FQ
    FQ --> FIN
    FIN -- "read silver, write gold" --> S3
    FIN -- "published_at" --> DDB
    FIN -- "dashboard JSON, then pointer" --> WEB
    IQ -. "after 5 failed attempts" .-> IDLQ
    FQ -. "after 5 failed attempts" .-> FDLQ
    IDLQ --> CW
    FDLQ --> CW
    CW --> SNS
```

## Flow logic: conditionals and blockers

Legend: **green** = success path ends, **amber** = intentional skip (normal, message is consumed), **red** = failure (retry, then DLQ and alarm).

discover evaluates each candidate (run, lead) in turn; an amber skip moves on to the next candidate, except "cap reached", which ends the tick. The checker evaluates each recent run in turn; every amber skip moves on to the next run.

### discover and ingest

```mermaid
flowchart TD
    classDef ok fill:#e3f4e8,stroke:#2e7d4f,color:#1b4d2e
    classDef skip fill:#fff4d6,stroke:#b8860b,color:#5c4300
    classDef fail fill:#fde2e2,stroke:#c0392b,color:#7b1d1d
    classDef store fill:#eef1f7,stroke:#5b6b8c,color:#2d3a55

    SCHED(["discover tick<br/>:34, :39 … :59 UTC<br/>only while enabled"])

    subgraph DISCOVER["discover — every 5 minutes"]
        D1["List the 3 most recent DWD runs"]
        D2{"Run inside the<br/>retention window?"}
        D3{"All 4 indicator files<br/>present for this lead?"}
        D4{"Fewer than 5 pairs<br/>sent this tick?"}
        D5{"Lead item created?<br/>(only if absent)"}
        D6["Send ingest message"]
        D7{"Send succeeded?"}
        D8["Delete lead item<br/>so the next tick retries"]:::skip
        DS1["Skip run"]:::skip
        DS2["Skip lead: not ready yet"]:::skip
        DS3["Stop: cap reached,<br/>rest on the next tick"]:::skip
        DS4["Skip: already queued,<br/>done or dead-lettered"]:::skip
        DOK["Lead queued"]:::ok
    end

    SCHED --> D1 --> D2
    D2 -- no --> DS1
    D2 -- yes --> D3
    D3 -- no --> DS2
    D3 -- yes --> D4
    D4 -- no --> DS3
    D4 -- yes --> D5
    D5 -- "no: item exists" --> DS4
    D5 -- yes --> D6 --> D7
    D7 -- no --> D8
    D7 -- yes --> DOK

    IQ[["ingest-queue<br/>batch size 1 · max. 5 in parallel"]]:::store
    DOK --> IQ

    subgraph INGEST["ingest_lead — one message at a time"]
        I1["Download 4 GRIB files to bronze"]
        I2{"Grid size 542,040 and<br/>units K, %, m/s?"}
        I3["Extract 465 mask cells; compute<br/>°C, wind speed, apparent temperature"]
        I4{"All 465 values finite?"}
        I5["Write silver forecast_cell<br/>fixed key: duplicates overwrite"]
        I6{"Lead already DONE?"}
        I7["Mark lead DONE"]
        IOK1["Message consumed:<br/>lead complete"]:::ok
        IOK2["Message consumed:<br/>duplicate delivery"]:::skip
        IERR["Record last_error and<br/>receive_count; raise error"]:::fail
        I10{"5th attempt?"}
        IRET["Message reappears after<br/>visibility timeout, 720 s"]:::fail
        IDLQ[["ingest-dlq · lead DEAD_LETTERED<br/>→ alarm e-mail → manual redrive"]]:::fail
    end

    IQ --> I1 --> I2
    I1 -- "download error" --> IERR
    I2 -- no --> IERR
    I2 -- yes --> I3 --> I4
    I4 -- no --> IERR
    I4 -- yes --> I5 --> I6
    I6 -- no --> I7 --> IOK1
    I6 -- yes --> IOK2
    IERR --> I10
    I10 -- no --> IRET --> IQ
    I10 -- yes --> IDLQ
```

### checker and finalize

```mermaid
flowchart TD
    classDef ok fill:#e3f4e8,stroke:#2e7d4f,color:#1b4d2e
    classDef skip fill:#fff4d6,stroke:#b8860b,color:#5c4300
    classDef fail fill:#fde2e2,stroke:#c0392b,color:#7b1d1d
    classDef store fill:#eef1f7,stroke:#5b6b8c,color:#2d3a55

    CSCHED(["checker tick<br/>every 5 min, all hour<br/>only while enabled"])

    subgraph CHECKER["checker — every 5 minutes"]
        C1["Take the run times of the current<br/>UTC hour and the two before"]
        C2{"Run already<br/>published?"}
        C3{"Finalize already<br/>requested?"}
        C4{"All 25 leads<br/>DONE?"}
        C5["Send finalize message"]
        C6{"Send succeeded?"}
        C7["Record finalize_requested_at"]:::ok
        CS1["Skip run: published"]:::skip
        CS2["Skip run: finalize<br/>already requested"]:::skip
        CS3["Skip run: not complete yet"]:::skip
        CS4["Skip run: retried<br/>on the next tick"]:::skip
    end

    CSCHED --> C1 --> C2
    C2 -- yes --> CS1
    C2 -- no --> C3
    C3 -- yes --> CS2
    C3 -- no --> C4
    C4 -- no --> CS3
    C4 -- yes --> C5 --> C6
    C6 -- no --> CS4
    C6 -- yes --> C7

    FQ[["finalize-queue<br/>batch size 1 · max. 2 in parallel"]]:::store
    C5 --> FQ

    subgraph FINALIZE["finalize — one message at a time"]
        F1{"This run already<br/>published?"}
        F2{"A newer run already<br/>published?"}
        F3{"25 leads × 465 cells,<br/>all from one run?"}
        F4["Area-weight with bridge; join population<br/>and names → gold forecast_plr + summary"]
        F5{"542 PLRs × 25 leads?"}
        F6["Write run folder to dashboard bucket,<br/>then switch data/current.json"]
        F7["Set published_at"]:::ok
        FS1["Stop: duplicate message"]:::skip
        FS2["Stop: stale run would<br/>replace a newer forecast"]:::skip
        FERR["Raise error → retry after 1,800 s<br/>→ after 5: finalize-dlq → alarm e-mail"]:::fail
    end

    FQ --> F1
    F1 -- yes --> FS1
    F1 -- no --> F2
    F2 -- yes --> FS2
    F2 -- no --> F3
    F3 -- no --> FERR
    F3 -- yes --> F4 --> F5
    F5 -- no --> FERR
    F5 -- yes --> F6 --> F7
    F6 -- "publish error" --> FERR
```

## Components

| Name | Type | Purpose |
|---|---|---|
| `berlin-temp-discover` | EventBridge Scheduler | Fires at :34, :39 … :59 each hour (UTC). Created **disabled**; enabled only for tests and the 24–48 h run. |
| `discover` | Lambda | Finds ready (run, lead) pairs and queues new ones |
| `ingest-queue` / `ingest-dlq` | SQS | One message per (run, lead); after 5 failed attempts to the DLQ |
| `ingest_lead` | Lambda (triggered by `ingest-queue`) | bronze GRIB → silver `forecast_cell`; marks the lead DONE |
| `berlin-temp-checker` | EventBridge Scheduler | Fires every 5 minutes, all hour. Created **disabled**; enabled only for tests and the 24–48 h run. |
| `checker` | Lambda | Finds runs with all 25 leads DONE and queues finalize once per run |
| `finalize-queue` / `finalize-dlq` | SQS | One message per run |
| `finalize` | Lambda (triggered by `finalize-queue`) | silver → gold → dashboard publication |
| `berlin-temp-state` | DynamoDB, billed per request | Bookkeeping per run and lead |
| `berlin-temp-alerts` | SNS topic | E-mail to the budget-alert address |
| `reference_build` | Lambda, invoked manually | Static bronze → silver reference data, run once |

`reference_build` uses the `reference` image; all other Lambdas share the `pipeline` image with different handlers. Both images are built from one Dockerfile with two build targets.

## Messages

```json
ingest:   {"run_time": "2026-10-13T12:00Z", "lead": "PT005H00M"}
finalize: {"run_time": "2026-10-13T12:00Z"}
```

## DynamoDB items

| Key | Attributes |
|---|---|
| `PK=run#<run_time>`, `SK=lead#<lead>` | `status`: QUEUED / DONE / DEAD_LETTERED · `queued_at`, `done_at` · `receive_count`, `last_error` · `ttl` (7 days) |
| `PK=run#<run_time>`, `SK=meta` | `finalize_requested_at` · `published_at` · `ttl` |

## Step-by-step behavior

### discover (every 5 minutes, :34–:59)

1. List the runs from the `T_2M` directory listing, newest first. Keep runs from the current UTC hour and the two hours before it (the retention window, the same window the checker uses), at most 3.
2. Per run, newest first: read the run's existing lead items with one DynamoDB query. Leads that already have an item are skipped without any request to DWD.
3. HEAD-check the 4 indicator files for the remaining leads (4 in parallel); leads with a missing file are skipped. A connection reset or timeout is retried first (see the decision "Connection retries"); if it still fails, the file counts as not ready and a warning is logged.
4. Take the ready leads in ascending order, at most 5 new pairs per tick across all runs: create the lead item only if it does not exist (status QUEUED, `ttl` 7 days); on success, send the ingest message. If sending fails, delete the item (so the next tick retries) and fail the tick.

### ingest_lead (one message at a time)

1. Download the 4 GRIB files to `bronze/forecast/run=…/lead=…/`.
2. Decode; check grid size (542,040) and units (K, %, m/s).
3. Extract the 465 mask cells (mask loaded from silver once, reused while the Lambda stays warm).
4. Compute °C, wind speed and apparent temperature per cell; check all values are finite.
5. Write `silver/forecast_cell/run=…/lead=…/part.parquet` (fixed key: a duplicate run overwrites with identical content).
6. Set the lead to DONE only if it is not already DONE. A duplicate delivery finds it DONE and simply ends.
7. On error: record `last_error` and `receive_count`; on the 5th attempt set DEAD_LETTERED; re-raise so SQS retries or dead-letters the message.

### checker (every 5 minutes, all hour)

1. Take the run times of the current UTC hour and the two before it (the discover window), newest first.
2. For each run: if `published_at` is set, skip; if `finalize_requested_at` is set, skip.
3. Count the run's DONE leads with a strongly consistent read; fewer than 25 → skip.
4. Send the finalize message; if sending fails, skip (the next tick tries again).
5. After a successful send, record `finalize_requested_at`. If the checker crashes between steps 4 and 5, the next tick sends a second message, which finalize ignores.

### finalize (one message at a time)

1. If `published_at` is set for this run, stop (duplicate message).
2. If `data/current.json` names a newer run, stop (a late older run must not replace a newer forecast). The pointer is the source of truth for what is published.
3. Read the 25 silver files; check 25 × 465 complete values from one run.
4. Load bridge, population and PLR names; aggregate to `gold/forecast_plr/run=…` (13,550 rows) and `gold/forecast_plr_summary/run=…`; check 542 × 25.
5. Write `data/runs/<run>/areas/<plr_id>.json` (542) and `data/runs/<run>/summary.json` to the dashboard bucket (cached for a year: run folders never change), then replace `data/current.json` (cached 60 s). The pointer is written last, so visitors never see a half-written run. No CloudFront invalidation.
6. Set `published_at`.

### reference_build (invoked manually, run once)

1. Download the four static sources from their public URLs and write them unchanged to `bronze/static/<source>/`, each with a `.meta.json` (source URL, SHA-256, size, retrieval time; for the LOR layer also layer name and output format).
2. Parse and build in memory: population (542 rows, rejections classified), PLR names, planning-area geometries (EPSG:25833), ICON cell triangles (all 542,040, EPSG:25833), Berlin mask (cells touching Berlin widened by 5,000 m), PLR/cell area bridge.
3. Check before writing silver: 542 PLRs; population 540 accepted / 2 rejected; names and population ids match the LOR ids; mask 465 cells; bridge 1,678 rows; every PLR's weights sum to 1; every bridge cell is in the mask. All failed checks are reported together; on failure bronze stays written and silver is not touched.
4. Write `silver/{plr,population,icon_cell,mask,bridge}/part.parquet` (fixed keys; a re-run overwrites with identical content). Geometries are WKB in EPSG:25833 with a `crs` column.
5. Write the simplified map boundaries to `gold/static/plr_geometry.json` (20 m, topology preserved, coordinates rounded to 0.1 m).

Event option: `{"plr_layer": "<WFS layer name>"}` skips layer discovery.

## Settings

| Setting | Value |
|---|---|
| Lambda timeouts | discover 60 s · checker 60 s · ingest 120 s · finalize 300 s (not tuned; measured durations in the [run report](run-report.md)) |
| `reference_build` | timeout 900 s · memory 3,008 MB · temporary disk 2,048 MB (placeholders until measured) |
| Visibility timeout (≥ 6 × Lambda timeout) | ingest-queue 720 s · finalize-queue 1,800 s |
| `maxReceiveCount` | 5 (both queues) |
| DLQ message retention | 14 days |
| Batch size | 1 |
| Maximum concurrency of queue triggers | ingest 5 · finalize 2 (2 is the minimum allowed; verified in the Lambda API reference, `ScalingConfig`) |
| Reserved concurrency | none (the account limit of 10 concurrent executions does not allow reservations; overlapping ticks are harmless: conditional puts in discover, idempotent finalize) |

## Monitoring

| Alarm | Condition |
|---|---|
| Ingest DLQ not empty | messages visible > 0 |
| Finalize DLQ not empty | messages visible > 0 |
| No publication | finalize invocations < 1 over 3 h; missing data counts as breaching |
| discover or finalize failing | Lambda errors > 0 |

- All alarms → SNS → e-mail.
- Structured JSON logs with `run_time`, `lead`, `receive_count` and duration, so CloudWatch Logs Insights can trace a lead end to end.
- Log retention 14 days on every log group (default is never expire, and stored logs are billed).

## Permissions (least privilege per function)

| Role | Allowed |
|---|---|
| schedulers | invoke `discover` / invoke `checker` (one role each) |
| discover | DynamoDB query/put/delete on the table · send to `ingest-queue` |
| ingest_lead | S3 write `bronze/forecast/` and `silver/forecast_cell/` · read `silver/mask/` · DynamoDB update · consume `ingest-queue` |
| checker | DynamoDB query/get/update on the table · send to `finalize-queue` |
| finalize | S3 read `silver/`, write `gold/forecast_plr*/` · dashboard bucket: read/write `data/`, list (so a missing pointer reads as "not found") · DynamoDB get/update · consume `finalize-queue` |
| reference_build | S3 read/write `bronze/static/` · write silver reference data and `gold/static/` |

## Cost guards

- Both schedules disabled outside tests and the continuous run.
- discover sends at most 5 pairs per tick.
- Maximum concurrency on both queue triggers.
- No S3-event triggers anywhere, so no Lambda can trigger itself through its own output.
- DLQs end retries; no automatic redrive.
- Log retention set on all log groups.
- Existing AWS Budgets alerts remain as the backstop.
- Checker every 5 minutes: ≈ 8,600 invocations/month, within the Lambda and Scheduler free allowances (estimate from N. Virginia list prices; Frankfurt slightly higher).

## Tests before the continuous run

1. Hand-written message to `ingest-queue` → check silver output and item status.
2. Same message twice → no duplicates, status correct.
3. Broken message (e.g. nonexistent lead) → 5 failures → DLQ → alarm e-mail → manual redrive works.
4. Checker with 25 DONE leads → exactly one finalize message; next tick sends none.
5. Two finalize messages for one run → exactly one publication.
6. Both schedules enabled for one hour → one full run end to end.

## Build order
During development, each step was done once via the CLI, then deleted, and
finally rebuilt with Terraform.

1. SNS topic + e-mail subscription
2. DynamoDB table
3. Private data bucket with lifecycle rules
4. ECR repository with lifecycle policy; Dockerfile with two build targets
5. `reference` image + `reference_build` invoked manually -> static bronze and silver reference data
6. `pipeline` image + `ingest_lead` invoked manually
7. `ingest-queue` + DLQ + queue trigger
8. `discover` + schedule (created disabled)
9. `finalize-queue` + DLQ + `finalize`
10. `checker` + schedule (created disabled)
11. Alarms + log retention

## Details on the checker

- The checker examines the run times of the **current UTC hour and the two before it** (the same window as discover), derived from the clock.
- It **sends the finalize message first, then records** `finalize_requested_at`. A crash in between causes at most a duplicate message, which finalize ignores.
- Once `finalize_requested_at` is set, the checker never requests that run again, even if it is not published. A failed finalize is handled by its DLQ and manual redrive.
- Checker settings: timeout 60 s, no reserved concurrency. It is **not** included in the error alarm (only discover and finalize are).

## Known limitations (accepted)
These are known limitations that I accepted for the current state of the project.

- **discover crash between creating the lead item and sending the ingest message.** The item says QUEUED but no message exists, so the lead is never processed and its run never reaches 25 DONE leads. The undo step covers a failed send, not a crash. The only signal is the "no publication for 3 h" alarm. Recovery: delete the stuck lead item by hand so the next discover tick queues it again. 
- **Finalize can leave the pointer on an older run.** The "newer run already published" check (reading `data/current.json`) and the pointer write are separate steps about 7 s apart, and the finalize trigger allows 2 concurrent invocations. If two different runs are finalized at the same time, both can pass the check, and whichever writes last wins. This was observed once during a manual checker test when two recent runs were finalized together and the pointer ended on earlier one. Effect: the dashboard shows an older, valid run until the next run is published. It needs two different runs to finish within seconds of each other (for example a late older run completing together with a newer one). Recovery: rewrite `data/current.json` by hand to the newest published run (its `published_at` is on the run's `meta` item in DynamoDB), or wait for the next run.
  - **Possible solution (not implemented): conditional pointer write.** S3 can refuse a write if the object changed since it was read. Finalize would (1) read the pointer and remember its ETag, or "no pointer"; (2) stop with `stale` if the pointer names a newer run; (3) write the pointer with `If-Match: <ETag>` (or `If-None-Match: *` if there was none); (4) if S3 refuses (HTTP 412, or 409 while another conditional write is in progress), go back to (1), at most about 5 times, then raise so SQS retries the message. Whichever order two finalize invocations run in, the pointer ends on the newest run. A run that ends as `stale` after writing its run folder leaves an unlinked folder (harmless) and does not set `published_at`. boto3 1.43 in the image supports `IfMatch` and `IfNoneMatch`. A maximum concurrency of 1 is not possible (the minimum for an SQS trigger is 2; reserved concurrency is ruled out by the account limit).
