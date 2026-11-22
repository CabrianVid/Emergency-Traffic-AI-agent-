# Matched grid and Ljubljana evaluation

The final study ran 680 synthetic SUMO simulations. Of these, 667 passed the audit. The average response and five-minute charts use scenarios valid under all three displayed policies: 109 on the grid and 24 in Ljubljana. Each paired comparison uses all scenarios valid for that pair.

| Policy | Dispatch | Signals |
| --- | --- | --- |
| **B1** | Rule-based central priority queue | Normal |
| **A1Q** | Feasible, queue-aware plans ranked by AI | Normal |
| **A2Q** | Same planning and ranking as A1Q | Green-wave priority |

## Charts and data

| Measure | Figure | CSV |
| --- | --- | --- |
| Average response time | [Chart](average-response-time.png) | [Policy summary](policy-summary.csv) |
| Calls reached within five minutes | [Chart](five-minute-target.png) | [Policy summary](policy-summary.csv) |
| Paired response time saved | [Chart](response-time-saved.png) | [Paired comparisons](paired-comparisons.csv) |
| Focused signal priority tests | | [Corridor results](priority-stress.csv) |

Positive savings mean faster response. The chart shows 95% paired bootstrap intervals. Average response gives each scenario equal weight, caps response at 15 minutes, and gives missed calls the full 15-minute score. P90 in the policy summary is calculated among reached calls. In the mixed-call scenarios, the effect of adding signal priority to A1Q was within 0.4% of zero on both networks, with both intervals spanning zero.

The separate corridor tests kept the urgent call, responding unit, traffic and dispatch delay fixed across 24 valid pairs. Signal priority reduced mean response by 13.7% eastbound and 14.0% northbound under those conditions.

Ljubljana uses OpenStreetMap road geometry with modeled signals and synthetic traffic and calls. See the [methods and reproduction commands](../../../docs/EXPERIMENTS.md#final-matched-grid-and-ljubljana-evaluation).
