"""Vision-model bake-off on 14 known 990-PF attachment pages.

    python run.py --provider openai  --models gpt-5-mini
    python run.py --provider gateway --models alibaba/qwen3-vl-... google/gemini-2.5-flash

Scores each model per page against reference.json (rows the Unstructured
selector produced; Siegel's pages also carry a hard truth: the declared
total). Raw responses are kept under out/<model>/ for inspection.
"""
import argparse, base64, json, os, re, sys, time, collections
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from openai import OpenAI

HERE = Path(__file__).parent
PROMPT = """This is one scanned page from an attachment to an IRS Form 990-PF. Transcribe it completely.

Return ONLY a JSON object with this shape:
{"page_kind": "grants_paid_list" | "grants_future_list" | "expenditure_responsibility" | "other",
 "heading": "<heading or title text printed on the page, or empty>",
 "rows": [{"name": "<recipient name exactly as printed>", "address": "<address if printed>",
           "status": "<foundation status code such as PC, if printed>", "purpose": "<purpose text if printed>",
           "amount": <number>}],
 "totals": [{"label": "<label as printed>", "amount": <number>}]}

Rules:
- rows: one entry for EVERY printed line that names a recipient and shows a dollar amount, in page order.
  Transcribe the rows whatever the page kind is; page_kind is a separate label and must never cause rows
  to be left out. Do not summarise, do not stop early, do not invent rows.
- totals: lines labelled total, subtotal, grand total or carried forward go here, never in rows.
- amount: a number without $ or commas. When a line shows several money columns, use the grant amount
  (for non-cash grants the fair market value, not book value or cost basis).
- If the page is rotated, read it rotated. Empty rows list only if the page truly has no recipient lines."""


def client_for(provider):
    if provider == "gateway":
        key = os.environ.get("VERCEL_AI_GATEWAY_API_KEY") or os.environ["AI_GATEWAY_API_KEY"]
        return OpenAI(base_url="https://ai-gateway.vercel.sh/v1", api_key=key)
    return OpenAI()


def ask(client, model, image_path, json_mode=True):
    b64 = base64.b64encode(image_path.read_bytes()).decode()
    kwargs = dict(model=model, messages=[{"role": "user", "content": [
        {"type": "text", "text": PROMPT},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}", "detail": "high"}}]}])
    kwargs["max_tokens"] = 8000          # a dense page is ~2-3K tokens of JSON; some models default lower
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    t0 = time.time()
    try:
        r = client.chat.completions.create(**kwargs)
    except Exception as exc:  # some gateway models reject response_format
        if json_mode:
            return ask(client, model, image_path, json_mode=False)
        return {"error": str(exc)[:300], "seconds": time.time() - t0}
    text = r.choices[0].message.content or ""
    finish = r.choices[0].finish_reason
    m = re.search(r"\{.*\}", text, re.S)
    try:
        data = json.loads(m.group(0)) if m else {}
    except json.JSONDecodeError:
        data = {"parse_error": text[:300]}
    u = r.usage
    data["_usage"] = {"in": getattr(u, "prompt_tokens", None), "out": getattr(u, "completion_tokens", None)}
    data["_seconds"] = round(time.time() - t0, 1)
    data["_finish"] = finish
    return data


def score(model, results, ref):
    print(f"\n=== {model}")
    print(f"{'page':<18}{'kind':<26}{'rows':>9}{'amt match':>10}{'sum model':>15}{'sum ref':>15}{'tokens in/out':>15}{'s':>5}")
    tot = collections.Counter(); case_sums = collections.defaultdict(float)
    for key, data in results.items():
        tag, page = key
        r = ref[tag]["pages"][str(page)]
        if "error" in data or "parse_error" in data:
            print(f"{key[0]+' p'+str(page):<18}ERROR {str(data.get('error') or data.get('parse_error'))[:80]}"); continue
        rows = [x for x in data.get("rows", []) if isinstance(x, dict)]
        amts = []
        for x in rows:
            try: amts.append(round(float(str(x.get("amount", "")).replace(",", "").replace("$", "")), 2))
            except ValueError: pass
        ref_amts = collections.Counter(round(a, 2) for a in r["amounts"]); got = collections.Counter(amts)
        matched = sum((ref_amts & got).values())
        s = sum(amts); case_sums[tag] += s
        u = data.get("_usage", {})
        tot["in"] += u.get("in") or 0; tot["out"] += u.get("out") or 0; tot["rows"] += len(rows); tot["ref_rows"] += r["n_rows"]; tot["matched"] += matched
        print(f"{tag+' p'+str(page):<18}{str(data.get('page_kind'))[:25]:<26}{len(rows):>4}/{r['n_rows']:<4}{matched/max(1,r['n_rows']):>9.0%}{s:>15,.0f}{r['sum']:>15,.0f}{str(u.get('in'))+'/'+str(u.get('out')):>15}{data.get('_seconds',0):>5.0f}")
    for tag, s in case_sums.items():
        ref_sum = sum(p["sum"] for p in ref[tag]["pages"].values())
        note = f"  declared paid target ${ref[tag]['paid_target']:,.0f}" if tag == "siegel23" else ""
        print(f"  {tag}: model sum ${s:,.0f} vs reference ${ref_sum:,.0f} ({s/ref_sum:.1%}){note}")
    print(f"  rows {tot['rows']} vs ref {tot['ref_rows']}; amounts matched {tot['matched']}/{tot['ref_rows']} = {tot['matched']/max(1,tot['ref_rows']):.0%}; tokens in {tot['in']:,} out {tot['out']:,}")


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--provider", default="openai"); ap.add_argument("--models", nargs="+", required=True)
    ap.add_argument("--workers", type=int, default=4); ap.add_argument("--only", default=None, help="tag prefix filter, e.g. siegel23")
    ap.add_argument("--pages", default="pages", help="directory of page PNGs (e.g. pages200 for a 200 DPI render)")
    ap.add_argument("--suffix", default="", help="appended to the output dir name, e.g. @200dpi")
    a = ap.parse_args()
    ref = json.load(open(HERE / "reference.json")); client = client_for(a.provider)
    pages = sorted((HERE / a.pages).glob("*.png"))
    if a.only: pages = [p for p in pages if p.name.startswith(a.only)]
    keys = [(p.name.split("_p")[0], int(re.search(r"_p(\d+)", p.name).group(1))) for p in pages]
    for model in a.models:
        out = HERE / "out" / (model.replace("/", "__") + a.suffix); out.mkdir(parents=True, exist_ok=True)
        with ThreadPoolExecutor(a.workers) as ex:
            datas = list(ex.map(lambda p: ask(client, model, p), pages))
        results = {}
        for key, p, data in zip(keys, pages, datas):
            (out / (p.stem + ".json")).write_text(json.dumps(data, indent=1)); results[key] = data
        score(model + a.suffix, results, ref)


if __name__ == "__main__":
    main()
