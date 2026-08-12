# Fade-below-threshold slider

## Problem

Tall pillars are hard to pick out of a field of short ones. There is no way
to suppress the low end, so a handful of genuinely extreme units are lost in
national noise — and the effect is worst exactly where it matters, at the
county layer with 3,135 polygons on screen.

## What it does

A slider that dims every pillar below a chosen height. The threshold is
expressed in whatever measure currently drives height — crime rate or
population density, following the existing *Height shows* toggle.

## Control

A new group in the left panel, directly under *Height exaggeration*. Both
shape height, so they belong together.

```
Fade below                    412 /100k
[————————●————————————————————————————]
```

- Range `0 → 0.9`, step `0.01`, default `0`, which reads "off".
- `0.9` rather than `1.0` because the top of a fixed domain is empty in
  every year but the peak one; the last stretch of travel would do nothing
  and read as a broken slider.

## Representation

`S.fade` holds a **fraction of the fixed height domain** — the same
normalised `tHeight` that already drives elevation in `render()`.

This is the load-bearing decision. It is what lets one slider serve both
measures: in rate mode `0.12` is 12% of the all-years peak rate; in density
mode it is 12% of the way up the log-density domain. The handle does not
move when *Height shows* is toggled, only the readout units change.

Storing the threshold in real units instead would need two sliders with two
ranges, and a linear density slider would compress every useful value into
the first 2% of travel — nearly every county sits below 500/sq mi against a
maximum near 70,000.

The readout converts back to real units for display: `S.fade * heightRef`
for rate, `10^(logMin + S.fade * logSpan)` for density, inverting the log
mapping rather than interpolating between the domain ends. Both
`heightRef` and `logMin`/`logSpan` already exist as locals in `render()`.

## Two properties that must hold

**Independent of exaggeration.** The threshold is compared against
`tHeight` *before* `S.exag` is applied, so dragging exaggeration changes how
tall the pillars are but never which ones are faded.

**Fixed across years,** like every other domain in this viewer. Scrubbing the
year slider shows units genuinely crossing the threshold rather than the
threshold moving underneath them. A percentile-based cutoff was rejected for
exactly this reason: it would recompute per year and mean something different
in every frame, which is the same class of mistake as the per-year
normalisation bug recorded in CLAUDE.md.

## Rendering

One predicate in the existing per-row loop:

```js
const faded = S.fade > 0 && !(isFinite(tHeight) && tHeight >= S.fade);
```

The `S.fade > 0` guard matters: without it, units with no data (`tHeight` is
`NaN`) would fade at rest, and they already have their own alpha-70
treatment.

Faded units keep height, shape, and picking — they stay hoverable, so a
dimmed unit can still be interrogated. They get colour mixed hard toward a
dark blue-grey and alpha dropped to 48. `getLineColor` becomes an accessor so
the wireframe dims with them; left constant it would draw a bright cage
around every ghost and defeat the effect. `S.fade` joins `updateTriggers` for
both colour accessors.

Fade composes on top of the low-coverage grey rather than replacing it, so a
faded low-coverage unit stays distinguishable from a faded well-reported one.
Neither signal is lost.

## Out of scope

- **The ranking list** — it shows the top 12 by rate. Everything in it is
  above any useful threshold, so filtering would only shorten it.
- **The legend histogram** — it bins *rate* regardless of which channel
  carries it, so a threshold tick would be plain wrong in density mode.
- **Hiding, or flattening to zero height.** Removing sub-threshold pillars
  makes an empty area read as "no data" rather than "low value", which this
  dataset can least afford.

## Scope of change

Display-only, inside `render()` plus one control block in
`viewer/index.html`. No scripts, no `data/*.json`, no `scripts/common.py`, so
none of the pipeline invariants are in play.

## Verification

No JS test harness exists; `tests/test_pipeline.py` is pipeline-only. Checks
are manual via `make serve`:

1. At `0` the map is identical to before.
2. Dragging exaggeration does not change which pillars are faded.
3. Toggling *Height shows* leaves the handle in place and switches the
   readout to `/sq mi`.
4. A faded pillar still hovers and still shows its coverage warning.
5. The county layer, with the largest polygon count, is where the feature
   has to actually pay off.
