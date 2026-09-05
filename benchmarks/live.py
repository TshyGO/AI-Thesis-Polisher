"""Explicit paid synthetic-only smoke run. Credential read from stdin, never saved.

python -m benchmarks.live --model MODEL --output cache/live-run
Reports lexical/reference metrics, NOT human acceptance or real Word success.
"""
import argparse
import hashlib
import json
import logging
import sys
import time
from pathlib import Path

from engine.llm_client import LLMClient
from engine.pipelines import PolishingPipeline
from benchmarks.evaluate import evaluate, read_rows


class TextDocument:
    """In-memory patch adapter; deliberately not a Word acceptance test."""
    def __init__(self, samples):
        self.original = {n: s["original"] for n, s in enumerate(samples, 1)}
        self.current = self.original.copy()

    def open_document(self, *args, **kwargs):
        pass

    def get_total_paragraphs(self):
        return len(self.original)

    def parse_chapters(self):
        return [{"name": f"independent-{n}", "start": n, "end": n} for n in self.original]

    def get_paragraph_text(self, index):
        return self.original[index]

    def get_neighbor_text(self, index, direction="prev", limit=1):
        return ""  # Independent benchmark examples are not contiguous paper prose.

    def apply_tracked_revision(self, index, old, new):
        if self.current[index].count(old) != 1:
            return False
        self.current[index] = self.current[index].replace(old, new, 1)
        return True

    def save(self):
        pass


class BoundedClient(LLMClient):
    def __init__(self, key, model):
        super().__init__(key, "https://api.siliconflow.cn/v1", model)
        self.calls = 0
        self.responses = []

    def call_api(self, messages, temperature=0.3, timeout=60, max_retries=3):
        if self.calls >= 80:
            raise RuntimeError("Live request budget exceeded")
        self.calls += 1
        print(f"request {self.calls}/80", file=sys.stderr, flush=True)
        response = super().call_api(messages, temperature, timeout=min(timeout, 90), max_retries=1)
        self.responses.append({"request": self.calls, "response": response})
        return response


def main():
    args_parser = argparse.ArgumentParser(description=__doc__)
    args_parser.add_argument("--model", required=True)
    args_parser.add_argument("--output", required=True)
    args_parser.add_argument("--corpus", choices=("synthetic", "public_derived"), default="synthetic")
    args_parser.add_argument("--resume", action="store_true", help="Replay saved suggestions; retry only missing model results")
    args = args_parser.parse_args()
    # Fixed public synthetic corpus only. Do not add an arbitrary private input flag.
    corpus = Path(__file__).with_name(args.corpus + ".jsonl")
    samples = read_rows(corpus)
    key = sys.stdin.read().strip()
    if not key:
        raise ValueError("Missing credential on stdin")
    client = BoundedClient(key, args.model)
    output = Path(args.output)
    if args.resume:
        prior = json.loads((output / "report.json").read_text(encoding="utf-8"))
        if (prior["model"] != args.model or prior["prompt_version"] != PolishingPipeline.PROMPT_VERSION
                or prior["corpus_sha256"] != hashlib.sha256(corpus.read_bytes()).hexdigest()):
            raise ValueError("Resume configuration does not match the saved run")
    else:
        output.mkdir(parents=True, exist_ok=False)
    document = TextDocument(samples)
    pipeline = PolishingPipeline(client, document, {
        "language": "mixed", "intensity": "light", "min_chars": 1,
        "original_filename": corpus.name, "output_dir": str(output),
        "protected_terms": sorted({term for s in samples for term in s["protected_terms"]}),
        "use_cross_review": True,
    })
    pipeline.cache_dir = output
    pipeline.cache_file = output / "suggestions.json"
    pipeline.chapter_notes_dir = output / "chapter_notes"
    pipeline.chapter_notes_dir.mkdir(exist_ok=args.resume)
    started = time.monotonic()
    pipeline.process_document(str(corpus))
    failed = {r["paragraph_idx"] for r in pipeline.last_records
              if r["status"].endswith(("ERROR", "FAILED", "TIMEOUT"))}
    predictions = [{"id": s["id"], "revised": document.current[n],
                    "status": "ERROR" if n in failed else "OK"}
                   for n, s in enumerate(samples, 1)]
    (output / "predictions.jsonl").write_text(
        "\n".join(json.dumps(p, ensure_ascii=False) for p in predictions) + "\n", encoding="utf-8")
    report = evaluate(samples, predictions)
    report.update(model=args.model, prompt_version=pipeline.PROMPT_VERSION,
                  corpus_sha256=hashlib.sha256(corpus.read_bytes()).hexdigest(),
                  calls=client.calls, elapsed_seconds=round(time.monotonic()-started, 2),
                  mode=f"{args.corpus}-text-adapter-not-Word", intensity="light")
    report["records"] = pipeline.last_records
    (output / "responses.json").write_text(json.dumps(client.responses, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k not in ("records", "details")}, ensure_ascii=False))


if __name__ == "__main__":
    logging.basicConfig(level=logging.CRITICAL)  # Never print SDK error response bodies.
    try:
        main()
    except Exception as error:
        print(f"Live evaluation failed ({type(error).__name__}); details suppressed.", file=sys.stderr)
        sys.exit(1)
