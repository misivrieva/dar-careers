"""Human-readable HTML rendering of eval JSON reports.  `python -m evals.report` re-renders the latest reports."""
from __future__ import annotations

import html
import json
import sys
from pathlib import Path

REPORTS = Path(__file__).resolve().parent / "reports"


def _e(x) -> str:
    return html.escape(x if isinstance(x, str) else json.dumps(x, indent=1, default=str))


def render_html(r: dict) -> str:
    t = r["totals"]
    rows = "".join(
        f"<tr class='{c['status']}'><td>{_e(c['id'])}</td><td>{_e(c['category'])}</td><td>{_e(c['description'])}</td>"
        f"<td>{_e(c['severity'])}</td><td><b>{_e(c['status'])}</b></td><td>{_e(c.get('failure') or '')}</td>"
        f"<td><details><summary>details</summary><pre>expected: {_e(c['expected'])}\n\nactual: {_e(c['actual'])}</pre></details></td></tr>"
        for c in r["cases"])
    gates = "".join(f"<tr><td>{_e(g['metric'])}</td><td>{_e(str(g['observed']))}</td><td>{g['n']}</td><td>{_e(g['target'])}</td>"
                    f"<td>{'met' if g['met'] else ('not measured' if g['met'] is None else '<b>not met</b>')}</td></tr>" for g in r["quality_gates"])
    crit = "".join(f"<li>{k}: {v['passed']}/{v['cases']} passed{', <b>' + str(v['critical_failures']) + ' critical</b>' if v['critical_failures'] else ''}</li>"
                   for k, v in r["critical_scenarios"].items())
    prev = f"<p>Previous {r['mode']} run {_e(r['previous']['run_id'])}: {r['previous']['totals']['passed']}/{r['previous']['totals']['executed']} passed.</p>" if r.get("previous") else ""
    u = r["usage"]
    return f"""<!doctype html><html lang="en-GB"><head><meta charset="utf-8"><title>DAR eval {r['run_id']}</title>
<style>body{{font-family:Inter,system-ui,sans-serif;background:#F8F6EF;color:#1f2a23;margin:2rem auto;max-width:1100px;padding:0 1rem}}
h1,h2{{font-family:'Playfair Display',Georgia,serif;color:#304839}}table{{border-collapse:collapse;width:100%;background:#fff;font-size:13px}}
td,th{{border-bottom:1px solid #DCE5D4;padding:6px;text-align:left;vertical-align:top}}tr.failed td{{background:#fbefec}}tr.uncertain td{{background:#f8f1e2}}
pre{{white-space:pre-wrap;max-height:300px;overflow:auto;font-size:11px}}.meta{{color:#555;font-size:13px}}</style></head><body>
<h1>DAR Career Agent - evaluation report</h1>
<p class="meta">Run <b>{_e(r['run_id'])}</b> · {_e(r['mode'])} · {_e(r['timestamp'])} · commit {_e(r['git_commit'])}<br>
Generation model: {_e(r['model'])} · Judge model: {_e(r['judge_model'])} · Dataset {_e(r['dataset_version'])} · Repetitions {r['repetitions']}<br>
Prompt versions: {_e(r['prompt_versions'])}</p>
<p><b>Executed {t['executed']}</b> · passed {t['passed']} · failed {t['failed']} · skipped {t['skipped']} · uncertain {t['uncertain']} · <b>critical failures {t['critical_failures']}</b></p>
{prev}<h2>Five critical scenarios</h2><ul>{crit}</ul>
<h2>Quality gates</h2><table><tr><th>Metric</th><th>Observed</th><th>n</th><th>Target</th><th></th></tr>{gates}</table>
<p class="meta">Model calls {u['llm_calls']} · tokens {u['input_tokens'] + u['output_tokens']} · avg latency {u['latency_ms_avg']} ms · cost {u['cost_usd']} · wall {u['wall_clock_s']} s</p>
<h2>Cases</h2><table><tr><th>ID</th><th>Category</th><th>Description</th><th>Severity</th><th>Status</th><th>Failure</th><th></th></tr>{rows}</table>
<p class="meta">{_e(r['disclaimer'])}</p></body></html>"""


def main() -> int:
    files = sorted(REPORTS.glob("eval_*.json"))
    if not files:
        print("No reports yet. Run `make eval` or `make eval-live`.")
        return 1
    for f in files:
        r = json.loads(f.read_text())
        f.with_suffix(".html").write_text(render_html(r))
        print(f"rendered {f.with_suffix('.html').name}: {r['totals']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
