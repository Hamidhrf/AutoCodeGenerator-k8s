#!/usr/bin/env python3
"""Load generator for AutoCodeGenerator /api/process-request."""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import json
import math
import ssl
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Dict, List, Optional


@dataclass
class RequestResult:
    request_no: int
    request_id: int
    ok: bool
    status_code: Optional[int]
    latency_ms: float
    error: str = ""
    response_preview: str = ""


def parse_start_at(raw_value: Optional[str]) -> Optional[float]:
    if not raw_value:
        return None

    try:
        return float(raw_value)
    except ValueError:
        pass

    normalized = raw_value
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"

    start_dt = dt.datetime.fromisoformat(normalized)
    if start_dt.tzinfo is None:
        raise ValueError("start-at must include timezone information (for example: 2026-03-13T15:30:00Z)")
    return start_dt.timestamp()


def parse_headers(header_items: List[str]) -> Dict[str, str]:
    headers: Dict[str, str] = {}
    for raw in header_items:
        if ":" not in raw:
            raise ValueError(f"Invalid header format: '{raw}'. Expected: 'Key: Value'")
        key, value = raw.split(":", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            raise ValueError(f"Invalid header key in: '{raw}'")
        headers[key] = value
    return headers


def percentile(sorted_values: List[float], p: float) -> Optional[float]:
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return sorted_values[0]

    k = (len(sorted_values) - 1) * (p / 100.0)
    floor_idx = math.floor(k)
    ceil_idx = math.ceil(k)
    if floor_idx == ceil_idx:
        return sorted_values[int(k)]

    lower = sorted_values[floor_idx]
    upper = sorted_values[ceil_idx]
    return lower + (upper - lower) * (k - floor_idx)


def make_prompt(template: str, request_id: int, request_no: int) -> str:
    # Supported placeholders in prompt template:
    # {id} or {n} -> request ID, {seq} -> sequence number (1-based).
    return template.format(id=request_id, n=request_id, seq=request_no)


def send_request(
    url: str,
    request_no: int,
    request_id: int,
    prompt_template: str,
    timeout_seconds: float,
    headers: Dict[str, str],
    max_response_bytes: int,
    ssl_context: Optional[ssl.SSLContext],
) -> RequestResult:
    start = time.perf_counter()
    try:
        prompt = make_prompt(prompt_template, request_id, request_no)
        payload = {"id": request_id, "prompt": prompt}
        body = json.dumps(payload).encode("utf-8")

        req = urllib.request.Request(url=url, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        for key, value in headers.items():
            req.add_header(key, value)

        with urllib.request.urlopen(req, timeout=timeout_seconds, context=ssl_context) as response:
            raw = response.read(max_response_bytes)
            text = raw.decode("utf-8", errors="replace")
            status_code = getattr(response, "status", response.getcode())
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            return RequestResult(
                request_no=request_no,
                request_id=request_id,
                ok=200 <= status_code < 300,
                status_code=status_code,
                latency_ms=elapsed_ms,
                response_preview=text,
            )
    except urllib.error.HTTPError as exc:
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        body = exc.read(max_response_bytes).decode("utf-8", errors="replace")
        return RequestResult(
            request_no=request_no,
            request_id=request_id,
            ok=False,
            status_code=exc.code,
            latency_ms=elapsed_ms,
            error=f"HTTPError: {exc}",
            response_preview=body,
        )
    except Exception as exc:  # noqa: BLE001
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        return RequestResult(
            request_no=request_no,
            request_id=request_id,
            ok=False,
            status_code=None,
            latency_ms=elapsed_ms,
            error=f"{type(exc).__name__}: {exc}",
        )


def run_load(args: argparse.Namespace) -> int:
    if args.requests is None and args.duration_seconds is None:
        print("Error: you must set --requests and/or --duration-seconds.", file=sys.stderr)
        return 2

    if args.rps <= 0:
        print("Error: --rps must be > 0.", file=sys.stderr)
        return 2

    if args.concurrency <= 0:
        print("Error: --concurrency must be > 0.", file=sys.stderr)
        return 2

    try:
        extra_headers = parse_headers(args.header)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    try:
        scheduled_start = parse_start_at(args.start_at)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    ssl_context = None
    if args.insecure:
        ssl_context = ssl._create_unverified_context()  # noqa: SLF001

    if scheduled_start is not None:
        wait_seconds = scheduled_start - time.time()
        if wait_seconds > 0:
            print(f"Waiting {wait_seconds:.1f}s until start time...")
            time.sleep(wait_seconds)

    print("Starting load run...")
    print(f"Target URL: {args.url}")
    print(f"Configured rps={args.rps}, concurrency={args.concurrency}, requests={args.requests}, duration={args.duration_seconds}")

    results: List[RequestResult] = []
    in_flight: Dict[concurrent.futures.Future[RequestResult], int] = {}
    max_in_flight = max(args.concurrency * 4, 16)
    send_interval = 1.0 / args.rps
    submitted = 0
    completed = 0
    last_progress_ts = 0.0

    run_start_wall = dt.datetime.now(dt.timezone.utc)
    run_start_mono = time.monotonic()
    run_deadline = run_start_mono + args.duration_seconds if args.duration_seconds is not None else None
    next_dispatch = run_start_mono

    def collect_done(done_futures: List[concurrent.futures.Future[RequestResult]]) -> None:
        nonlocal completed
        for future in done_futures:
            in_flight.pop(future, None)
            result = future.result()
            results.append(result)
            completed += 1

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        while True:
            now = time.monotonic()
            reached_count_limit = args.requests is not None and submitted >= args.requests
            reached_time_limit = run_deadline is not None and now >= run_deadline
            if reached_count_limit or reached_time_limit:
                break

            if in_flight and len(in_flight) >= max_in_flight:
                done, _ = concurrent.futures.wait(
                    in_flight.keys(),
                    timeout=0.1,
                    return_when=concurrent.futures.FIRST_COMPLETED,
                )
                if done:
                    collect_done(list(done))
                continue

            delay = next_dispatch - now
            if delay > 0:
                time.sleep(min(delay, 0.05))
                if in_flight:
                    done, _ = concurrent.futures.wait(
                        in_flight.keys(),
                        timeout=0,
                        return_when=concurrent.futures.FIRST_COMPLETED,
                    )
                    if done:
                        collect_done(list(done))
                continue

            request_no = submitted + 1
            request_id = args.start_id + submitted
            future = pool.submit(
                send_request,
                args.url,
                request_no,
                request_id,
                args.prompt_template,
                args.request_timeout_seconds,
                extra_headers,
                args.max_response_bytes,
                ssl_context,
            )
            in_flight[future] = request_no
            submitted += 1
            next_dispatch += send_interval

            progress_now = time.monotonic()
            if progress_now - last_progress_ts >= 2:
                print(f"Progress: submitted={submitted} completed={completed} inflight={len(in_flight)}")
                last_progress_ts = progress_now

            if in_flight:
                done, _ = concurrent.futures.wait(
                    in_flight.keys(),
                    timeout=0,
                    return_when=concurrent.futures.FIRST_COMPLETED,
                )
                if done:
                    collect_done(list(done))

        while in_flight:
            done, _ = concurrent.futures.wait(
                in_flight.keys(),
                timeout=1.0,
                return_when=concurrent.futures.FIRST_COMPLETED,
            )
            if done:
                collect_done(list(done))
            progress_now = time.monotonic()
            if progress_now - last_progress_ts >= 2:
                print(f"Draining: submitted={submitted} completed={completed} inflight={len(in_flight)}")
                last_progress_ts = progress_now

    run_end_mono = time.monotonic()
    elapsed = max(run_end_mono - run_start_mono, 1e-9)

    status_counter = Counter("EXC" if r.status_code is None else str(r.status_code) for r in results)
    success_count = sum(1 for r in results if r.ok)
    error_count = len(results) - success_count
    latencies = sorted(r.latency_ms for r in results)

    p50 = percentile(latencies, 50)
    p95 = percentile(latencies, 95)
    p99 = percentile(latencies, 99)
    avg_latency = (sum(latencies) / len(latencies)) if latencies else None

    summary = {
        "started_at_utc": run_start_wall.isoformat(),
        "finished_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "target_url": args.url,
        "duration_seconds": elapsed,
        "submitted_requests": submitted,
        "completed_requests": len(results),
        "success_count": success_count,
        "error_count": error_count,
        "success_rate_percent": (success_count / len(results) * 100.0) if results else 0.0,
        "achieved_submit_rps": submitted / elapsed,
        "achieved_complete_rps": len(results) / elapsed,
        "latency_ms_avg": avg_latency,
        "latency_ms_p50": p50,
        "latency_ms_p95": p95,
        "latency_ms_p99": p99,
        "status_counts": dict(status_counter),
    }

    print("\nRun summary")
    print(f"- elapsed_s: {summary['duration_seconds']:.2f}")
    print(f"- submitted: {summary['submitted_requests']}")
    print(f"- completed: {summary['completed_requests']}")
    print(f"- success:   {summary['success_count']}")
    print(f"- errors:    {summary['error_count']}")
    print(f"- success_rate_pct: {summary['success_rate_percent']:.2f}")
    print(f"- submit_rps:   {summary['achieved_submit_rps']:.2f}")
    print(f"- complete_rps: {summary['achieved_complete_rps']:.2f}")
    if avg_latency is not None:
        print(f"- latency_ms avg/p50/p95/p99: {avg_latency:.2f}/{p50:.2f}/{p95:.2f}/{p99:.2f}")
    print(f"- status_counts: {dict(status_counter)}")

    first_errors = [r for r in results if not r.ok][:5]
    if first_errors:
        print("\nFirst errors")
        for err in first_errors:
            preview = err.response_preview.replace("\n", " ")[:220]
            print(
                f"- req#{err.request_no} id={err.request_id} status={err.status_code} "
                f"latency_ms={err.latency_ms:.2f} error={err.error} body='{preview}'"
            )

    if args.report_file:
        report = {
            "config": {
                "url": args.url,
                "rps": args.rps,
                "concurrency": args.concurrency,
                "requests": args.requests,
                "duration_seconds": args.duration_seconds,
                "start_id": args.start_id,
                "prompt_template": args.prompt_template,
            },
            "summary": summary,
            "error_samples": [asdict(r) for r in first_errors],
        }
        with open(args.report_file, "w", encoding="utf-8") as file_obj:
            json.dump(report, file_obj, indent=2)
        print(f"\nSaved report: {args.report_file}")

    return 0 if error_count == 0 else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run a configurable load against AutoCodeGenerator /api/process-request.\n"
            "Stop condition is whichever happens first: --requests or --duration-seconds."
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("--url", default="http://127.0.0.1:4010/api/process-request", help="Target API endpoint URL")
    parser.add_argument("--requests", type=int, default=None, help="Total number of requests to submit")
    parser.add_argument("--duration-seconds", type=float, default=None, help="Maximum run duration in seconds")
    parser.add_argument("--rps", type=float, default=1.0, help="Send rate (requests per second)")
    parser.add_argument("--concurrency", type=int, default=8, help="Number of worker threads")
    parser.add_argument("--request-timeout-seconds", type=float, default=120.0, help="HTTP timeout per request")
    parser.add_argument("--start-id", type=int, default=1000, help="Starting value for request JSON field 'id'")
    parser.add_argument(
        "--prompt-template",
        default="Write a valid Java class with main that prints number {id}",
        help="Prompt template. Placeholders: {id}, {n}, {seq}",
    )
    parser.add_argument("--header", action="append", default=[], help="Extra HTTP header in format 'Key: Value'")
    parser.add_argument(
        "--start-at",
        default=None,
        help="Optional start time: epoch seconds or ISO8601 with timezone, e.g. 2026-03-13T16:00:00Z",
    )
    parser.add_argument("--report-file", default=None, help="Optional path to save summary JSON report")
    parser.add_argument("--max-response-bytes", type=int, default=2048, help="Max bytes to keep from each response body")
    parser.add_argument("--insecure", action="store_true", help="Disable TLS certificate verification (for HTTPS only)")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return run_load(args)
    except KeyError as exc:
        print(
            f"Error: prompt template placeholder {exc} is invalid. Use only: {{id}}, {{n}}, {{seq}}.",
            file=sys.stderr,
        )
        return 2
    except KeyboardInterrupt:
        print("\nInterrupted by user.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
