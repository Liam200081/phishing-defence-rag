"""Local B1 CLI; nonzero exit when a query is incomplete."""
import argparse
import json
import sys
from .pipeline import run_b1
from .trace import read_run


def main(argv=None):
    parser = argparse.ArgumentParser(description="ADR-002 M1: B1 single-round RAG")
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--queries", default="evaluation/queries.json")
    parser.add_argument("--pdf-dir", default="data")
    parser.add_argument("--output-dir", default="experiments/b1")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--read-run", help="Validate and summarize an existing B1 run without API calls")
    args = parser.parse_args(argv)
    try:
        if args.read_run:
            run = read_run(args.read_run)
            path = args.read_run
        else:
            run, path = run_b1(args.config, args.queries, args.pdf_dir, args.output_dir, args.project_root)
        print(json.dumps({"run_id": run["run_id"], "path": str(path), **run["summary"]}, ensure_ascii=False))
        return 0 if run["summary"]["incomplete"] == 0 and run["summary"]["evaluation_errors"] == 0 else 2
    except (OSError, ValueError, ImportError) as exc:
        print(f"B1 setup/read error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
