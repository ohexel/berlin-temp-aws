# berlin-temp-aws

Apparent-temperature forecast for Berlin's 542 planning areas (PLR), built as a serverless,
medallion-style data pipeline on AWS. Portfolio project; not intended to run indefinitely.

**Status:** skeleton only. Infrastructure is built step by step (AWS CLI first, then Terraform).

- Design: [docs/orchestration-plan.md](docs/orchestration-plan.md)
- Decisions: [docs/adr/](docs/adr/)

## Development

```bash
uv sync
uv run ruff check .
uv run ruff format --check .
uv run pytest
```
