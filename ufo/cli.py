"""Command line interface: ``python -m ufo <command>``."""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

DEFAULT_SEED = Path(__file__).resolve().parent.parent / "data" / "seed" / "ufo-seed.json.gz"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ufo", description="UFO files pipeline")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("run", help="check sources, download new files, extract and classify")
    p.add_argument("--limit", type=int, help="process at most N pending records")
    p.add_argument("--source", action="append", help="only this source (repeatable)")

    sub.add_parser("check", help="only list what the sources publish and record new releases")

    p = sub.add_parser("process", help="process pending records without re-checking sources")
    p.add_argument("--limit", type=int)
    p.add_argument("ids", nargs="*", help="record ids")

    p = sub.add_parser("reprocess", help="redo extraction or classification")
    p.add_argument("stage", choices=["extract", "classify"])
    p.add_argument("ids", nargs="*", help="record ids (default: all)")

    p = sub.add_parser("extract-file", help="extract text from a local PDF (debugging)")
    p.add_argument("path")
    p.add_argument("--force-ocr", action="store_true")

    p = sub.add_parser("export-seed", help="write a data snapshot")
    p.add_argument("path", nargs="?", default=str(DEFAULT_SEED))
    p = sub.add_parser("import-seed", help="load a data snapshot into an empty database")
    p.add_argument("path", nargs="?", default=str(DEFAULT_SEED))

    sub.add_parser("analyze", help="recompute cross-record patterns (waves, clusters, links)")

    sub.add_parser("status", help="summarise the database")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    from . import pipeline
    from .db import init_db

    init_db()
    if args.cmd == "run":
        run_id = pipeline.run_pipeline(trigger="cli", sources=args.source, limit=args.limit)
        print(f"run id: {run_id}")
    elif args.cmd == "check":
        from .sources import get_sources
        rlog = pipeline.RunLog()
        for s in get_sources():
            pipeline.sync_source(s, rlog)
    elif args.cmd == "process":
        pipeline.process_pending(pipeline.RunLog(), limit=args.limit, record_ids=args.ids or None)
    elif args.cmd == "reprocess":
        n = pipeline.reset_for_reprocess(args.stage, args.ids or None)
        print(f"{n} documents queued")
        pipeline.process_pending(pipeline.RunLog(), record_ids=args.ids or None)
    elif args.cmd == "extract-file":
        from .extract import extract_pdf
        res = extract_pdf(args.path, force_ocr=args.force_ocr)
        for p in res.pages:
            print(f"--- page {p.page_no} [{p.method}{'' if p.confidence is None else f' {p.confidence}%'}]")
            print(p.text)
    elif args.cmd == "export-seed":
        from .seed import export_seed
        print(f"exported {export_seed(Path(args.path))} documents to {args.path}")
    elif args.cmd == "import-seed":
        from .seed import import_seed
        print(f"imported {import_seed(Path(args.path))} documents")
    elif args.cmd == "analyze":
        pipeline.run_analysis_step(pipeline.RunLog())
    elif args.cmd == "status":
        from sqlalchemy import func, select
        from .db import Document, PipelineRun, session_scope
        with session_scope() as db:
            for status, n in db.execute(select(Document.status, func.count()).group_by(Document.status)):
                print(f"{status:12} {n}")
            last = db.scalar(select(PipelineRun).order_by(PipelineRun.id.desc()))
            if last:
                print(f"last run #{last.id} {last.status} {last.started_at:%Y-%m-%d %H:%M} new={last.new_records}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
