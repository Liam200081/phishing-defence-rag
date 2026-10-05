"""Run paired M5 experiments or rescore a saved run without model calls."""
import argparse
import json
from pathlib import Path

from .config import load_config
from .evaluation import (MODES, failure_analysis, knowledge_cases, load_cases,
                         run_experiment, score_run, validate_current_version, _usage)


def main(argv=None):
    parser = argparse.ArgumentParser(description="M5 paired evaluation")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--suite", choices=("event", "knowledge"), default="event")
    run.add_argument("--split", choices=("development", "held_out"), default="development")
    run.add_argument("--modes", nargs="+", choices=list(MODES), default=["B1", "W0", "A1"])
    run.add_argument("--repeat", type=int, default=1)
    run.add_argument("--config", default="evaluation/m5_config.json")
    run.add_argument("--cases", default="evaluation/cases/m2_cases.json")
    run.add_argument("--case-id", action="append", dest="case_ids",
                     help="Select development cases for a smoke run; omit for the full split")
    run.add_argument("--queries", default="evaluation/queries.json")
    run.add_argument("--pdf-dir", default="data")
    run.add_argument("--output-dir", default="experiments/m5")
    run.add_argument("--root", default=".")
    run.add_argument("--min-interval-seconds", type=float, default=0,
                     help="Minimum spacing between model requests across all Cases")
    run.add_argument("--retry-429", type=int, default=0,
                     help="Bounded retries for provider rate limits; each attempt is retained")
    run.add_argument("--allow-dirty", action="store_true", help="Development smoke runs only")
    score = sub.add_parser("score")
    score.add_argument("run_json")
    score.add_argument("--reviews", required=True)
    score.add_argument("--cases", default="evaluation/cases/m2_cases.json")
    score.add_argument("--queries", default="evaluation/queries.json")
    score.add_argument("--root", default=".")
    score.add_argument("--allow-historical", action="store_true",
                       help="Score an archived run from another commit explicitly")
    args = parser.parse_args(argv)
    if args.command == "run":
        from .llm import groq_completion
        from .rag.ingest import ingest_pdfs
        from .rag.retrieve import ChromaRetriever
        config = load_config(args.config)
        split = args.split if args.suite == "event" else "knowledge_regression"
        cases = (load_cases(args.cases, args.split, case_ids=args.case_ids) if args.suite == "event"
                 else knowledge_cases(args.queries))
        if args.suite == "knowledge" and args.case_ids:
            cases = [c for c in cases if c["case_id"] in args.case_ids]
            if not cases or {c["case_id"] for c in cases} != set(args.case_ids):
                raise ValueError("unknown knowledge case ID")
        completion = groq_completion(config)
        from .trace import code_provenance
        provenance = code_provenance(args.root)
        if provenance["git_dirty"] is not False and not args.allow_dirty:
            raise ValueError("formal run requires a clean Git worktree")
        generation = config["generation"]
        probe = completion(model=config["model"],
                           messages=[{"role": "user", "content": "Reply READY."}],
                           max_tokens=64, temperature=generation["temperature"],
                           top_p=generation["top_p"])
        preflight = {"status": "ok", "model": config["model"], "usage": _usage(probe)}
        chunks, _ = ingest_pdfs(args.pdf_dir, config)
        retriever = ChromaRetriever(chunks, config)
        try:
            result, path = run_experiment(cases, args.modes, config, retriever, completion,
                                          args.root, args.output_dir, split=split,
                                          repeat=args.repeat, provenance=provenance,
                                          min_interval_seconds=args.min_interval_seconds,
                                          retry_429=args.retry_429, preflight=preflight)
        finally:
            retriever.close()
        print(json.dumps({"run_id": result["run_id"], "path": str(path),
                          "cases": len(cases), "results": len(result["results"])}))
    else:
        saved = json.loads(Path(args.run_json).read_text(encoding="utf-8"))
        if not args.allow_historical:
            validate_current_version(saved, args.root)
        cases = (knowledge_cases(args.queries) if saved["split"] == "knowledge_regression"
                 else load_cases(args.cases, saved["split"], case_ids=saved["case_ids"]))
        if saved["split"] == "knowledge_regression":
            cases = [c for c in cases if c["case_id"] in saved["case_ids"]]
        reviews = json.loads(Path(args.reviews).read_text(encoding="utf-8"))
        if not isinstance(reviews, list):
            raise ValueError("reviews must be a JSON list")
        report = score_run(saved, cases, reviews)
        report["failures"] = failure_analysis(saved, report)
        path = Path(args.run_json).with_name("score-" + report["review_sha256"][:16] + ".json")
        payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        if path.exists():
            if path.read_text(encoding="utf-8") != payload:
                raise ValueError("review hash collision or modified score file")
        else:
            with path.open("x", encoding="utf-8") as out:
                out.write(payload)
        print(json.dumps({"path": str(path), "modes": report["modes"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
