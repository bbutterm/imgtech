"""Worker процесса для очереди imgtech dev."""

from __future__ import annotations

import argparse
import time

import fitz

from api.index import MAX_BATCH, _open_pdfs, build_plan, compare_pairs
from api.job_queue import (
    claim_next,
    fail_job,
    finish_job,
    recover_stale_jobs,
    update_progress,
)


def process_one() -> bool:
    job = claim_next()
    if not job:
        return False
    job_id = job["id"]
    doc1 = doc2 = None
    try:
        update_progress(job_id, stage="opening", done=0, total=0)
        data1 = open(job["file1"], "rb").read()
        data2 = open(job["file2"], "rb").read()
        doc1, doc2 = _open_pdfs(data1, data2)
        if doc1 is None:
            raise ValueError("Не удалось открыть один из файлов как PDF")

        update_progress(job_id, stage="mapping", done=0, total=0)
        started = time.time()
        plan = build_plan(doc1, doc2)
        pairs = plan["pairs"]
        update_progress(job_id, stage="comparing", done=0, total=len(pairs))

        pages = []
        for offset in range(0, len(pairs), MAX_BATCH):
            batch = pairs[offset:offset + MAX_BATCH]
            pages.extend(compare_pairs(doc1, doc2, batch))
            update_progress(
                job_id,
                stage="comparing",
                done=min(offset + len(batch), len(pairs)),
                total=len(pairs),
            )

        result = {
            "pages": pages,
            "removed": plan["removed"],
            "added": plan["added"],
            "numPages1": plan["numPages1"],
            "numPages2": plan["numPages2"],
            "totalPairs": len(pairs),
            "elapsed": round(time.time() - started, 2),
        }
        finish_job(job_id, result)
    except Exception as exc:
        fail_job(job_id, str(exc))
    finally:
        if doc1 is not None:
            doc1.close()
        if doc2 is not None:
            doc2.close()
    return True


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll", type=float, default=1.0)
    args = parser.parse_args()
    recover_stale_jobs()
    if args.once:
        process_one()
        return
    while True:
        if not process_one():
            time.sleep(max(0.1, args.poll))


if __name__ == "__main__":
    main()
