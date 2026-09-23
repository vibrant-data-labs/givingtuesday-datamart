"""Transcribe 990-PF attachment pages with a vision model.

One page in, one JSON document out: the page's kind, its heading, one row
per recipient line, and the totals printed on it. The bake-off in
``exploratory/vlm_bakeoff`` is why this replaces Unstructured's OCR job — on
14 known pages the two candidates below are exact on the clean and the
dense rotated lists at a fraction of the price, and they hand the selector
two things OCR never did: a label for every page, and the page's own stated
total.

Pages are rendered at 200 DPI: 300 costs more tokens for nothing on a
bitonal scan, and 130 lost rows on the dense pages. Calls go through the
Vercel AI Gateway (OpenAI-compatible), keyed by ``VERCEL_AI_GATEWAY_API_KEY``.

The prompt is versioned, and every stored page carries the version that
produced it. v2 (the bake-off's) made rows independent of the page kind —
v1 let a model that labelled a page "other" return no rows. v3 came out of
the 100-filing sample: it says what a heading is (Gemini put the filer
masthead there and Qwen a recipient line) and keeps a contact person
inside their organisation's row (Gemini listed Claude Moore's contacts as
grantees, doubling the page). v3 also said a name wrapping onto a second
line is still one row — and Gemini then read two adjacent recipients as
one wrapped name on Wyss's p033, after which every amount on the page sat
one row too high and the page sum barely moved. v4 anchors rows on the
amount column instead: every printed amount is a row, and a text line
with no amount belongs to the row above it. On the six pages tried it
read Wyss p033 exactly under both models and kept v3's contact handling.

The sample also showed that Qwen's empty answers were not the prompt's
doing: under ``response_format=json_object`` it returned no rows for 401
dense list pages, in five seconds each, and read every one of them without
JSON mode. ``transcribe`` retries an empty or truncated list page that way.
"""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import time
from pathlib import Path

GATEWAY_URL = "https://ai-gateway.vercel.sh/v1"
DPI = 200
# $ per 1M tokens (in, out): Vercel AI Gateway list prices, 2026-09-21.
PRICES: dict[str, tuple[float, float]] = {
    "alibaba/qwen3-vl-instruct": (0.20, 0.88),
    "google/gemini-3.5-flash-lite": (0.30, 2.50),
    # third-reader candidates, gateway list prices 2026-09-22
    "google/gemini-3.8-flash": (0.75, 3.75),
    "openai/gpt-5.6-terra": (2.00, 12.00),
    "openai/gpt-5.6-luna": (0.20, 1.20),
    "anthropic/claude-sonnet-5": (2.00, 10.00),
}
# Request fields a model needs beyond the shared ones. The GPT-5 line
# reasons by default, and in the bake-off gpt-5-mini answered the same
# page with 13 rows, then none, then 13; transcription wants the least
# reasoning the model allows.
REQUEST_EXTRAS: dict[str, dict] = {
    "openai/gpt-5.6-terra": {"reasoning_effort": "none"},      # terra rejects "minimal"; luna accepts it
    "openai/gpt-5.6-luna": {"reasoning_effort": "minimal"},
    # Sonnet 5 otherwise spends the whole output budget thinking on a dense
    # page and returns no text at all (three of 83 ground-truth pages, at
    # 8K, 16K and 32K tokens); with thinking off it read all three.
    "anthropic/claude-sonnet-5": {"reasoning_effort": "none"},
    # Gemini 3.8 Flash thinks by default and the gateway bills it as output:
    # 5,524 output tokens a page on the 83 ground-truth pages for 7,270
    # characters of answer. At "low" (Zein, 2026-09-23) it reads the same
    # pages at 2,126 tokens a page — $0.0093 against $0.0220, 12 s median
    # against 26 — and no worse: 62 pages exact against 59, 96.5% / 96.3%
    # precision and recall against 94.2% / 94.0%, 17 of 46 disputes
    # resolved against 16, one call a page. Never "none" or "minimal" for
    # a Google model: they map to an unbounded thinking budget — "none" on
    # the same pages gave 12,835 tokens a page, 7 pages at the 32K limit,
    # 56 s median and no gain in accuracy; one page at "minimal" 7,993
    # tokens and three attempts. POLICY_V1's verdicts were decided at the
    # default and pin it; POLICY_V2 reads at "low".
    "google/gemini-3.8-flash": {"reasoning_effort": "low"},
}
LIST_KINDS = ("grants_paid_list", "grants_future_list")
MAX_TOKENS = 8000            # a dense page is 2–3K tokens of JSON; doubled on truncation
ATTEMPTS = 3
# Whether to ask for response_format=json_object first. Qwen answers a
# dense page with an empty list under it (83% of the sample's pages under
# prompt v3 needed the fallback), so for Qwen the first call goes without.
JSON_MODE = {"alibaba/qwen3-vl-instruct": False}

PROMPT_VERSION = "v4"
PROMPT = """This is one scanned page from an attachment to an IRS Form 990-PF. Transcribe it completely.

Return ONLY a JSON object with this shape:
{"page_kind": "grants_paid_list" | "grants_future_list" | "expenditure_responsibility" | "other",
 "heading": "<the title of the list printed above the rows on this page, or empty>",
 "rows": [{"name": "<recipient name exactly as printed>", "address": "<address if printed>",
           "status": "<foundation status code such as PC, if printed>", "purpose": "<purpose text if printed>",
           "amount": <number>}],
 "totals": [{"label": "<label as printed>", "amount": <number>}]}

Rules:
- rows: one entry for EVERY printed line that names a recipient and shows a dollar amount, in page order.
  Transcribe the rows whatever the page kind is; page_kind is a separate label and must never cause rows
  to be left out. Do not summarise, do not stop early, do not invent rows, do not repeat a row.
- The amount column defines the rows: every printed amount is one row, paired with the recipient name
  printed on its line. A text line with no amount of its own is never a row: a name or address that wraps
  onto a second line, or a person's name printed with an organisation (a contact, attention or c/o line),
  belongs to the row above it (the person goes in address). Two recipients with two amounts are two rows,
  however similar their names.
- heading: the list title printed above the rows, such as "Part XV line 3a - Grants and contributions
  paid during the year". A filer's name, EIN, date or page number is not the heading, nor is a recipient
  line. Empty if the page prints no title.
- page_kind: grants_future_list when the title says approved for future payment, grants payable or
  line 3b; expenditure_responsibility for a Part VII-B line 5c statement; grants_paid_list for grants
  or contributions paid; other only for a page that is not a list of recipients. A page that continues
  a list from an earlier page, with no title of its own, is still a list, not other.
- totals: lines labelled total, subtotal, grand total or carried forward go here, never in rows.
- amount: a number without $ or commas, keeping the decimal point where it is printed: $838,000.00 is
  838000, never 8383000. When a line shows several money columns, use the grant amount (for non-cash
  grants the fair market value, not book value or cost basis).
- Strings never contain a raw line break; use a space instead.
- If the page is rotated, read it rotated. Empty rows list only if the page truly has no recipient lines."""

_JSON = re.compile(r"\{.*\}", re.S)
# An amount written as it was printed — "$151,000.00", "1,500000.0" — is not
# JSON; quoting it lets the page parse and leaves the value to _amount().
_BARE_AMOUNT = re.compile(r'("amount"\s*:\s*)\$?\s?([\d][\d,]*(?:\.\d+)?)"?(?=\s*[,}\]])')


def client(timeout: float = 240.0):
    """An OpenAI-compatible client on the gateway. The SDK retries 429/5xx itself."""
    from openai import OpenAI

    key = os.environ.get("VERCEL_AI_GATEWAY_API_KEY") or os.environ.get("AI_GATEWAY_API_KEY")
    if not key:
        raise RuntimeError("VERCEL_AI_GATEWAY_API_KEY is not set")
    return OpenAI(base_url=GATEWAY_URL, api_key=key, timeout=timeout, max_retries=3)


def render(pdf: Path, first: int, last: int, out_dir: Path, dpi: int = DPI) -> list[Path]:
    """Grayscale PNGs ``p<NNN>.png`` for pages ``first``..``last``; skips pages already rendered."""
    out_dir.mkdir(parents=True, exist_ok=True)
    wanted = {page: out_dir / f"p{page:03d}.png" for page in range(first, last + 1)}
    missing = [page for page, path in wanted.items() if not path.exists()]
    if missing:
        # pdftoppm names files <prefix>-<page>.png, zero-padded to the document's
        # width. The prefix carries the pid so two runs sharing a cache never
        # rename each other's half-written files; the final rename is atomic.
        prefix = out_dir / f"tmp{os.getpid()}"
        subprocess.run(["pdftoppm", "-r", str(dpi), "-gray", "-png", "-f", str(min(missing)),
                        "-l", str(max(missing)), str(pdf), str(prefix)], check=True, capture_output=True)
        for produced in out_dir.glob(f"{prefix.name}-*.png"):
            page = int(produced.stem.split("-")[-1])
            produced.replace(wanted[page]) if page in wanted else produced.unlink()
    return [wanted[page] for page in sorted(wanted)]


def _parse(text: str) -> dict:
    """The JSON object in a response. Long pages come back with raw newlines
    inside address and purpose strings, which strict JSON rejects, so the
    lenient parse is tried second. A response that still fails keeps its
    full text under ``parse_error`` so it can be repaired without a recall."""
    match = _JSON.search(text)
    if not match:
        return {"parse_error": text}
    body = match.group(0)
    for candidate, strict in ((body, True), (body, False), (_BARE_AMOUNT.sub(r'\1"\2"', body), False)):
        try:
            data = json.loads(candidate, strict=strict)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    # Some models stop mid-row on a dense page and report a normal finish
    # (Qwen on Johnson & Johnson's matching-gift pages). Keep the rows that
    # were complete and say so: a partial page can never reconcile a filing
    # by itself, but its rows are real and the flag keeps the lineage honest.
    body = _BARE_AMOUNT.sub(r'\1"\2"', body)
    for cut in reversed([m.end() for m in re.finditer(r"\}", body)][-60:]):
        try:
            data = json.loads(body[:cut] + "]}", strict=False)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and isinstance(data.get("rows"), list):
            data["_partial"] = True
            return data
    return {"parse_error": text}


def repair(result: Path) -> bool:
    """Re-parse a stored response that failed before; True if it parses now,
    complete or partial (a recall of a page the model keeps stopping on is
    not worth another attempt)."""
    data = json.loads(result.read_text())
    if "parse_error" not in data:
        return True
    fixed = _parse(data["parse_error"])
    if "parse_error" in fixed:
        return False
    fixed.update({k: v for k, v in data.items() if k.startswith("_")})
    result.write_text(json.dumps(fixed, indent=1))
    return True


def _ask(client, model: str, png: Path, max_tokens: int, json_mode: bool = True) -> dict:
    image = base64.b64encode(png.read_bytes()).decode()
    kwargs = dict(
        model=model, max_tokens=max_tokens,
        messages=[{"role": "user", "content": [
            {"type": "text", "text": PROMPT},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image}", "detail": "high"}},
        ]}],
    )
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    if model in REQUEST_EXTRAS:
        kwargs["extra_body"] = REQUEST_EXTRAS[model]
    started = time.time()
    try:
        response = client.chat.completions.create(**kwargs)
    except Exception as exc:                                   # noqa: BLE001
        if json_mode and "response_format" in str(exc):
            return _ask(client, model, png, max_tokens, json_mode=False)
        return {"error": f"{type(exc).__name__}: {str(exc)[:300]}", "_seconds": round(time.time() - started, 1)}
    choice = response.choices[0]
    text = choice.message.content or ""
    data = _parse(text)
    usage = response.usage
    data["_usage"] = {"in": getattr(usage, "prompt_tokens", None), "out": getattr(usage, "completion_tokens", None)}
    data["_seconds"] = round(time.time() - started, 1)
    data["_finish"] = choice.finish_reason
    data["_max_tokens"] = max_tokens
    data["_json_mode"] = json_mode
    data["_prompt"] = PROMPT_VERSION
    return data


def _rows(data: dict) -> int:
    return len(data.get("rows") or []) if "error" not in data and "parse_error" not in data else -1


def transcribe(client, model: str, png: Path) -> dict:
    """One page, with the retries the bake-off and the sample showed are needed.

    A transport error is retried after a pause (on top of the SDK's own).
    Output cut off at the token limit is asked for again with twice the
    room. A page the model labelled a grant list but gave no rows, or
    stopped mid-row on, is asked for again, without JSON mode from then
    on — that mode, not the page, is what made Qwen answer 401 dense pages
    with nothing. The fullest parsed answer is returned, with
    ``_attempts`` set to the number of calls made.
    """
    max_tokens, json_mode = MAX_TOKENS, JSON_MODE.get(model, True)
    best: dict = {}
    attempt = 0
    for attempt in range(1, ATTEMPTS + 1):
        data = _ask(client, model, png, max_tokens, json_mode)
        if (not best or _rows(data) > _rows(best)
                or (_rows(data) == _rows(best) and best.get("_partial") and not data.get("_partial"))):
            best = data
        if "error" in data:
            time.sleep(3 * attempt)
            continue
        if data.get("_finish") == "length":
            max_tokens *= 2
            continue
        if "parse_error" in data:
            continue
        short = (not data.get("rows") and data.get("page_kind") in LIST_KINDS) or data.get("_partial")
        if short and attempt < ATTEMPTS:
            json_mode = False
            continue
        break
    best["_attempts"] = attempt
    return best


def rows_sum(data: dict) -> float:
    total = 0.0
    for item in data.get("rows") or []:
        if isinstance(item, dict):
            try:
                total += float(str(item.get("amount", "")).replace(",", "").replace("$", ""))
            except ValueError:
                pass
    return total


def total_check(data: dict, tolerance: float = 0.005) -> str:
    """Do the rows sum to a total printed on the page?

    ``matched`` / ``mismatch`` / ``no_total``. A mismatch is not by itself a
    transcription error — a page's printed total may be cumulative — but
    matched pages need no further checking at all.
    """
    totals = []
    for item in data.get("totals") or []:
        if isinstance(item, dict):
            try:
                totals.append(float(str(item.get("amount", "")).replace(",", "").replace("$", "")))
            except ValueError:
                pass
    totals = [t for t in totals if t > 0]
    if not totals:
        return "no_total"
    total = rows_sum(data)
    return "matched" if any(abs(total - t) <= t * tolerance for t in totals) else "mismatch"


def cost(model: str, tokens_in: int, tokens_out: int) -> float | None:
    price = PRICES.get(model)
    if price is None:
        return None
    return (tokens_in * price[0] + tokens_out * price[1]) / 1e6
