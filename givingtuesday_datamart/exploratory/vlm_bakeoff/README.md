# Vision-model bake-off for 990-PF attachment pages

Does the recovery pipeline need Unstructured, or can attachment pages go
straight to a vision-language model? Fourteen pages with known answers:

| case | pages | why |
|---|---|---|
| Siegel Family Endowment 2023 (`202443189349103114`) | 31–39 | clean list; 110 rows reconcile to the declared $26,907,603 to the dollar |
| Wells Fargo Foundation 2020 (`202133169349103203`) | 100, 233 | dense, rotated landscape; p233 ends with a printed section total of $21,344,804.43 over 20 rows (verified by hand) |
| Klarman Family Foundation 2020 (`202123139349101877`) | 60 | program subtotals inline |
| Bezos Family Foundation 2022 (`202303199349103605`) | 38, 41 | non-cash: fair market value beside book value |

```bash
# render the pages (PDFs come from irs_source; cached under ~/.cache/irs_index/pdfs)
pdftoppm -r 200 -gray -f 31 -l 39 -png ~/.cache/irs_index/pdfs/202443189349103114.pdf pages/siegel23_p
python run.py --provider gateway --models alibaba/qwen3-vl-instruct   # VERCEL_AI_GATEWAY_API_KEY
python run.py --provider openai  --models gpt-5-mini                   # OPENAI_API_KEY
python compare.py                                                      # one table across out/*/
```

`reference.json` holds the Unstructured selector's rows for the same pages
(the reference, not ground truth, except for Siegel's declared total and
the Wells Fargo p233 section total). Results and the decision are in
`docs/placeholder_recovery_pipeline.md`.
