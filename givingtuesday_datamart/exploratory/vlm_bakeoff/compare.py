"""One table across every model that has finished: reads out/<model>/*.json."""
import json, collections, re
from pathlib import Path
HERE = Path(__file__).parent
ref = json.load(open(HERE / "reference.json"))
PRICE = {  # $ per 1M tokens (in, out): Vercel AI Gateway page, 2026-09-21; OpenAI list prices not fetched -> None
    "alibaba/qwen3-vl-instruct": (0.20, 0.88), "alibaba/qwen3-vl-235b-a22b-instruct": (0.20, 0.88),
    "nvidia/nemotron-nano-12b-v2-vl": (0.20, 0.60), "inclusionai/ling-3.0-flash-vl": (0, 0), "inclusionai/ling-3.0-flash-vl-free": (0, 0),
    "zai/glm-5v-turbo": (1.20, 4.00), "zai/glm-4.5v": (0.60, 1.80), "meta/llama-4-maverick": (0.24, 0.97),
    "google/gemma-4-31b-it": (0.14, 0.40), "mistral/mistral-small": (0.15, 0.60), "amazon/nova-2-lite": (0.30, 2.50),
    "google/gemini-3.5-flash-lite": (0.30, 2.50), "google/gemini-3.8-flash": (0.75, 3.75), "anthropic/claude-haiku-4.5": (1.00, 5.00),
    "deepseek/deepseek-v4-flash-vision-exp": (0.22, 0.65),
}
UNSTRUCTURED_PER_PAGE = 0.015
WF233_TRUTH = 21_344_804.43   # the section total printed on the page; 20 rows, verified by hand
rows_out = []
for d in sorted((HERE / "out").glob("*")):
    model = d.name.replace("__", "/")
    files = sorted(d.glob("*.json"))
    if len(files) < 14: continue
    tot = collections.Counter(); case_sum = collections.defaultdict(float); errors = 0; kinds = collections.Counter()
    for f in files:
        tag, page = f.stem.split("_p")[0], int(re.search(r"_p(\d+)", f.stem).group(1))
        data = json.load(open(f)); r = ref[tag]["pages"][str(page)]
        if "error" in data or "parse_error" in data: errors += 1; continue
        rows = [x for x in data.get("rows", []) if isinstance(x, dict)]
        amts = []
        for x in rows:
            try: amts.append(round(float(str(x.get("amount", "")).replace(",", "").replace("$", "")), 2))
            except ValueError: pass
        matched = sum((collections.Counter(round(a, 2) for a in r["amounts"]) & collections.Counter(amts)).values())
        tot["rows"] += len(rows); tot["ref"] += r["n_rows"]; tot["matched"] += matched
        u = data.get("_usage") or {}; tot["in"] += u.get("in") or 0; tot["out"] += u.get("out") or 0
        case_sum[tag] += sum(amts); kinds[(tag, data.get("page_kind"))] += 1
        if tag == "wf20" and page == 233: tot["wf233"] = sum(amts); tot["wf233_rows"] = len(rows)
        if tag == "siegel23": tot["siegel_pages_ok"] += int(matched == r["n_rows"] == len(rows))
    siegel = case_sum["siegel23"] / ref["siegel23"]["paid_target"]
    bezos = case_sum["bezos22"] / sum(p["sum"] for p in ref["bezos22"]["pages"].values())
    per_page_tokens = (tot["in"] + tot["out"]) / 14
    price = PRICE.get(model.split("@")[0]); cost = (tot["in"] * price[0] + tot["out"] * price[1]) / 1e6 / 14 if price else None
    rows_out.append((model, tot["siegel_pages_ok"], siegel, tot["wf233_rows"], tot["wf233"] / WF233_TRUTH, bezos, tot["matched"] / max(1, tot["ref"]), errors, per_page_tokens, cost))
print(f"{'model':<40}{'siegel pages exact':>19}{'siegel sum':>11}{'wf p233 rows':>13}{'wf p233 sum':>12}{'bezos sum':>10}{'amt match':>10}{'err':>4}{'tok/page':>9}{'$/page':>9}{'x cheaper':>10}")
for m, sp, ss, wr, ws, bz, am, er, tp, c in sorted(rows_out, key=lambda r: (-r[1], -r[6])):
    print(f"{m:<40}{sp:>13}/9{ss:>11.0%}{wr:>10}/20{ws:>12.1%}{bz:>10.0%}{am:>10.0%}{er:>4}{tp:>9,.0f}{('$'+format(c,'.4f')) if c is not None else 'n/a':>9}{(format(UNSTRUCTURED_PER_PAGE/c,'.0f')+'x') if c else '':>10}")
print("\nsiegel sum = 9 pages against the declared line 3a total ($26,907,603); wf p233 against the printed section total ($21,344,804.43);")
print("bezos sum = FMV rows on p38+p41 against the selector's reference; amt match = reference amounts found, all 14 pages; x cheaper = vs Unstructured at $0.015/page")
