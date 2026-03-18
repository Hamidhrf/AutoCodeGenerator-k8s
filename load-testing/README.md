# AutoCodeGenerator Load Script

This folder contains a reusable load script for:

- `POST /api/process-request`
- body format: `{"id": <int>, "prompt": "<text>"}`

Script file: `load-testing/autocodegen_load.py`

## Requirements

- Python 3.9+
- Reachable API endpoint for code-generator service

If your service is only inside Kubernetes, you can port-forward first:

```bash
kubectl port-forward -n autocodegens svc/code-generator-service 4010:4010
```

Default script target URL is:

```text
http://127.0.0.1:4010/api/process-request
```

## Quick Start

```bash
python3 load-testing/autocodegen_load.py --requests 50 --rps 5 --concurrency 10
```

## Main Parameters

- `--requests`: total request count (for example, `--requests 500`)
- `--duration-seconds`: run time limit in seconds (for example, `--duration-seconds 300`)
- `--rps`: load level as requests/sec (for example, `--rps 20`)
- `--concurrency`: worker threads (for example, `--concurrency 32`)
- `--start-at`: schedule start time (epoch seconds or ISO time with timezone)
- `--prompt-template`: prompt text template (`{id}`, `{n}`, `{seq}` placeholders)
- `--start-id`: starting integer for request `id`
- `--report-file`: save run summary JSON

Stop rule:

- If both `--requests` and `--duration-seconds` are set, run stops when the first limit is reached.

## Examples

### 1) Fixed number of requests

```bash
python3 load-testing/autocodegen_load.py \
  --requests 200 \
  --rps 10 \
  --concurrency 20
```

### 2) Fixed runtime

```bash
python3 load-testing/autocodegen_load.py \
  --duration-seconds 600 \
  --rps 15 \
  --concurrency 30
```

### 3) Both runtime + request cap

```bash
python3 load-testing/autocodegen_load.py \
  --duration-seconds 300 \
  --requests 1000 \
  --rps 25 \
  --concurrency 40
```

### 4) Scheduled start time

```bash
python3 load-testing/autocodegen_load.py \
  --start-at 2026-03-13T18:30:00Z \
  --duration-seconds 180 \
  --rps 8 \
  --concurrency 16
```

### 5) Custom prompt pattern and ID range

```bash
python3 load-testing/autocodegen_load.py \
  --requests 100 \
  --start-id 5000 \
  --prompt-template "Write Java class for id={id}, sequence={seq}" \
  --rps 12 \
  --concurrency 24
```

### 6) Save JSON report

```bash
python3 load-testing/autocodegen_load.py \
  --requests 300 \
  --rps 20 \
  --concurrency 40 \
  --report-file load-testing/report.json
```

## Output

The script prints:

- progress (`submitted`, `completed`, `inflight`)
- summary (`success/error`, throughput, latency percentiles, status counts)
- first error samples (if any)

Exit code:

- `0` => all completed requests were successful
- `1` => one or more requests failed
- `2` => argument/config error
- `130` => interrupted by user
