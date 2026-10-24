"""Run the app's chat agent (same graph, primer and tools as /chat) on hackathon benchmark questions
and score the replies with the official scorer.

  uv run python navgraph_checks/bench_agent.py <hackathon repo> <split> [--per-kind N] [--workers 8]

Writes <split>_results.jsonl (and submission_<split>.csv) to --out."""
import argparse, asyncio, collections, json, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from langchain_core.messages import HumanMessage
from app.agent import AgentContext, build_graph
from app.config import get_settings
from app.domain.building import BuildingFile
from app.main import create_chat_model
from app.navigation.navgraph_engine import NavgraphNavigator
from navgraph.model import Building

TIMEOUT_S = 600


async def ask(graph, bdir: Path, q: dict, usage: collections.Counter) -> str:
    nav = NavgraphNavigator(BuildingFile.model_validate(Building.load(bdir).to_dict()))
    state = await asyncio.wait_for(graph.ainvoke(
        {"messages": [HumanMessage(q["question"])]},
        {"configurable": {"thread_id": q["id"]}, "recursion_limit": 60},
        context=AgentContext(navigator=nav)), TIMEOUT_S)
    for m in state["messages"]:
        u = getattr(m, "usage_metadata", None) or {}
        d = u.get("input_token_details") or {}
        # langchain's input_tokens includes cache reads/writes; split them out for pricing
        usage["cache_read_input_tokens"] += d.get("cache_read", 0)
        usage["cache_creation_input_tokens"] += d.get("cache_creation", 0)
        usage["input_tokens"] += u.get("input_tokens", 0) - d.get("cache_read", 0) - d.get("cache_creation", 0)
        usage["output_tokens"] += u.get("output_tokens", 0)
    c = state["messages"][-1].content
    return c if isinstance(c, str) else "".join(p.get("text", "") for p in c if isinstance(p, dict))


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("repo", type=Path); p.add_argument("split")
    p.add_argument("--per-kind", type=int); p.add_argument("--workers", type=int, default=8)
    p.add_argument("--out", type=Path, default=Path("bench_out"))
    a = p.parse_args()
    sys.path.insert(0, str(a.repo))
    from benchmark.evaluate import cost_dollars, load_questions, report, sample_per_kind, write_submission_csv
    from benchmark.score import score_reply

    settings = get_settings()
    model = create_chat_model(settings)
    assert model is not None, "ANTHROPIC_API_KEY missing"
    graph = build_graph(model)
    qs = sample_per_kind(load_questions(a.repo / "data" / a.split), a.per_kind)
    usage, results, sem, rows = collections.Counter(), collections.Counter(), asyncio.Semaphore(a.workers), {}
    print(f"{a.split}: {len(qs)} questions, model {settings.anthropic_model}, {a.workers} workers", flush=True)
    t0 = time.time()

    async def one(bdir, q):
        async with sem:
            try:
                reply = await ask(graph, bdir, q, usage)
            except Exception as e:  # one failure must not sink the run; it scores as wrong
                reply = f"ERROR {e!r}"
            ok = score_reply(q["answer"], reply) if "answer" in q else None
            if ok is not None: results[(q["kind"], ok)] += 1
            rows[q["id"]] = {"id": q["id"], "kind": q["kind"], "correct": ok, "question": q["question"],
                             "answer": q.get("answer"), "reply": reply}
            print(f"[{len(rows):3d}/{len(qs)}] {q['id']:34s} {q['kind']:26s} "
                  f"{ {True: 'OK', False: 'BAD', None: 'saved'}[ok]}  ${cost_dollars(usage):.2f}", flush=True)

    await asyncio.gather(*(one(b, q) for b, q in qs))
    a.out.mkdir(parents=True, exist_ok=True)
    ordered = [rows[q["id"]] for _, q in qs]
    (a.out / f"{a.split}_results.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in ordered))
    write_submission_csv(a.out / f"submission_{a.split}.csv", [(r["id"], r["reply"]) for r in ordered])
    if results: report(results)
    print(f"tokens: {dict(usage)}  cost: ${cost_dollars(usage):.2f}  time: {time.time() - t0:.0f}s")


if __name__ == "__main__":
    asyncio.run(main())
