FROM ghcr.io/astral-sh/uv@sha256:e49fde5daf002023f0a2e2643861ce9ca8a8da5b73d0e6db83ef82ff99969baf AS uv
FROM backenddevelopersltd/bittensor-pylon@sha256:6d6dd6a79bb313f0bdd6b2833f193ac16b59738eee6138652d4418a8da0dd352

# Install the same SDK used by the validator and miner, without unrelated dependency upgrades.
COPY --from=uv /uv /usr/local/bin/uv
COPY sdk-requirements.txt /tmp/sdk-requirements.txt
RUN uv pip install --python /app/pylon_service/.venv/bin/python \
    --no-deps --require-hashes --only-binary :all: --no-cache \
    -r /tmp/sdk-requirements.txt

# Rebuild the two affected distributions so their dependency metadata matches their source.
COPY build-requirements.txt /tmp/build-requirements.txt
RUN uv pip install --python /app/pylon_service/.venv/bin/python \
    --no-deps --require-hashes --only-binary :all: --no-cache -r /tmp/build-requirements.txt
ADD https://files.pythonhosted.org/packages/8f/8b/ff46d3760a396ac135cf6adccfdab07cbcf50d94f8dcae224580cf158d33/turbobt-1.3.1.tar.gz /tmp/turbobt.tar.gz
RUN echo '695048e86bb0fd464b3dc9313d89b29711dbe256f4b2028316682a6a8499c46a  /tmp/turbobt.tar.gz' | sha256sum --check - \
    && tar -xzf /tmp/turbobt.tar.gz -C /tmp

# Explicit source migration against this pinned Pylon/TurboBT release; no runtime monkeypatches.
COPY bittensor-sdk.patch /tmp/bittensor-sdk.patch
COPY turbobt-sdk.patch /tmp/turbobt-sdk.patch
RUN patch --batch --forward --fuzz=0 -d /app -p1 < /tmp/bittensor-sdk.patch \
    && patch --batch --forward --fuzz=0 -d /tmp/turbobt-1.3.1 -p1 < /tmp/turbobt-sdk.patch \
    && uv pip install --python /app/pylon_service/.venv/bin/python --no-deps --no-build-isolation --no-sources \
        --editable /app/pylon_service /tmp/turbobt-1.3.1 \
    && uv pip uninstall --python /app/pylon_service/.venv/bin/python bittensor-wallet \
    && uv pip check --python /app/pylon_service/.venv/bin/python
COPY tests /opt/validity-pylon-tests
RUN PYLON_OPEN_ACCESS_TOKEN=offline-test-open-token \
    PYLON_METRICS_TOKEN=offline-test-metrics-token PYLON_IDENTITIES='[]' \
    /app/pylon_service/.venv/bin/python -m unittest discover -s /opt/validity-pylon-tests -v
