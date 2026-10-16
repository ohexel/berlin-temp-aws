# syntax=docker/dockerfile:1
# One Dockerfile, two images (Lambda container images, x86_64):
#   docker build --target pipeline  -t berlin-temp-pipeline  .
#   docker build --target reference -t berlin-temp-reference .
# The handler is not set here; each Lambda function sets it with its
# image config (command override).

FROM public.ecr.aws/lambda/python:3.14 AS base
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv
WORKDIR ${LAMBDA_TASK_ROOT}
COPY pyproject.toml uv.lock ./

FROM base AS pipeline
RUN uv export --frozen --no-hashes --no-emit-project --no-default-groups \
        --group pipeline -o /tmp/requirements.txt \
    && uv pip install --python /var/lang/bin/python --no-cache \
        -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt
COPY src/berlin_temp ./berlin_temp

FROM base AS reference
RUN uv export --frozen --no-hashes --no-emit-project --no-default-groups \
        --group pipeline --group reference -o /tmp/requirements.txt \
    && uv pip install --python /var/lang/bin/python --no-cache \
        -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt
COPY src/berlin_temp ./berlin_temp
