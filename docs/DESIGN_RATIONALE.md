# Design rationale: what the agent-harness research says, and why the field got it wrong

This system is a *harness*, not a model. The recurring finding across recent agentic-coding
research is that capability at the task level comes as much from the **scaffold** -- the tools,
the verification, the control flow around a stochastic agent -- as from the base model (the
agent-computer-interface argument in SWE-agent, Yang et al. 2024; the OpenHands/OpenDevin line).
So the right question for this challenge is not "can Devin write the fix" (it can) but "what
harness makes Devin's output *trustable at scale*." Every design choice below is an answer to a
specific, documented failure mode -- and each is a failure the seven prior submissions walked
straight into because they treated "CI green + the agent's summary" as ground truth.

---

## 1. Cheat-fixes are reward hacking. So the verifier is adversarial, not cooperative.

**Research.** Specification gaming / reward hacking is one of the oldest documented failure modes
in the field (Amodei et al. 2016, *Concrete Problems in AI Safety*; Krakovna et al.'s running
catalogue). In code agents it shows up concretely: a model rewarded for "make the test pass" will
special-case the test, hardcode the expected output, weaken the assertion, or `skip`/`@flaky` it.
The optimizer satisfies the *measure*, not the *goal*.

**What the field did.** Treated CI-green as the target. The strongest field verifier (thompalex)
returns `merged_ready` when `all(check == success)`. But CI-green is exactly the measure a
cheat-fix is optimizing -- a CI-based check is **structurally blind** to gaming, because it is
measuring the gamed quantity.

**Our choice.** The validator's anti-cheat diff scan *assumes the agent may have gamed the proxy*
and tries to catch it: it rejects `skip`/`flaky`/`xfail`/`reruns`/`sleep`/assertion-weakening/
randomization-pinning in the added lines. The verifier's job is to **try to reject the PR**, not
to confirm it. (Goodhart's law, Strathern's formulation: "when a measure becomes a target, it
ceases to be a good measure." The fix is a measure the agent is *not* optimizing against.)

## 2. Don't verify an LLM with another LLM. Ground verification in execution.

**Research.** LLM-as-judge is convenient but gameable and biased: judges exhibit self-preference
(Panickssery et al. 2024, *LLM Evaluators Recognize and Favor Their Own Generations*), position
and verbosity biases (Zheng et al. 2023, MT-Bench/Chatbot Arena), and can be talked out of correct
verdicts. A judge drawn from the same model family as the generator shares its blind spots, so
errors are **correlated** -- the worst property for a verifier.

**What the field did.** The weakest field verifier (emillaurence) literally trusts Devin's own
`validation_summary` -- an agent grading its own homework. Others lean on CI.

**Our choice.** The validator never asks a model whether the fix is good. Its ground truth is the
**test runner** (re-execute the targets across seeds) plus **deterministic static analysis** (the
diff scan, the provenance check). Execution and `grep` cannot be sweet-talked, and they do not
share the generator's failure modes.

## 3. "Tests pass" is a weak signal. Re-derive it independently and statistically.

**Research.** Even curated agent benchmarks discovered their pass signal was weak: SWE-bench's
own maintainers shipped *SWE-bench Verified* (2024) after finding underspecified/insufficient
tests let wrong patches "pass." Separately, the generation-verification literature
(Lightman et al. 2023, *Let's Verify Step by Step*) distinguishes **outcome** checks from
**process** checks and shows combining them is more robust than either alone.

**What the field did.** "PR opened + CI green once" = done. One run, one ordering, agent's word.

**Our choice.** We re-derive the verdict from a **fresh clone of the PR branch** (never Devin's
workspace), and we check **both**: an *outcome* gate (re-run the targets across the known-bad seeds
*and* K fresh seeds -- regression guard + generalization) and a *process* gate (the diff scan +
provenance). Flaky tests are the sharpest instance of why one run lies: order-dependence only
manifests under the right predecessor ordering, the exact thing a single CI run doesn't exercise.

## 4. The flake methodology is established -- we verify with the same instrument that detects.

**Research.** Order-dependent (shared-state) flakiness and its detection-by-reordering are
well-studied: Luo et al. 2014 (empirical study of flaky tests), Lam et al. 2019 (*iDFlakies* --
randomized run-order to surface order-dependence), and the `pytest-randomly` tooling that
operationalizes it.

**Our choice.** Detection and verification are the **same statistical engine** run for two
purposes -- prove a flake is real (discovery) and prove it is dead (verification). Using the exact
instrument that found the problem to confirm the fix is what makes the verdict honest, and it is
why a per-file "green" is meaningless here (the leaking predecessor must run first).

## 5. Self-correction only works with external, executable feedback -- and must be bounded.

**Research.** Intrinsic self-correction (a model critiquing itself with no external signal) is
unreliable and can *degrade* performance (Huang et al. 2023, *Large Language Models Cannot
Self-Correct Reasoning Yet*). Self-correction that works is the kind grounded in an **external
signal** -- execution results, a tool, a test (Reflexion, Shinn et al. 2023; Self-Refine,
Madaan et al. 2023). Unbounded correction loops can also oscillate.

**Our choice.** The feedback we send back is not "are you sure?" -- it is the validator's
**concrete external evidence**: the specific forbidden pattern caught, or the exact seed under
which a target still fails. And the loop is **bounded** (`MAX_CORRECTION_ROUNDS`), after which we
escalate rather than spin. This is precisely the regime the literature says self-correction helps.

## 6. Knowing when *not* to answer: principled abstention over forced verdicts.

**Research.** Selective prediction / learning-to-defer (El-Yaniv & Wiener; Madras et al.) shows a
system is more trustworthy when it can **abstain or defer** under low confidence instead of forcing
an output. A "helpful" agent that always produces a fix will, when the bug is in product code,
*edit the test to pass* -- masking a real defect (trust-failure #4).

**Our choice.** Two principled non-answers are first-class verdicts: `needs_human_review` (the
provenance gate -- the fix touched product code, or the session escalated a suspected product bug
-> route to a human, never silently stabilize) and `inconclusive` (env/build/collection failure ->
do not fabricate a verdict). Escalation is a feature, not a fallback.

## 7. Cost is a first-class control, not an afterthought.

**Observed firsthand.** Five parallel full-suite verifications exhausted a $20 ACU budget in an
afternoon -- statistical verification has real compute cost. (riankawahara's submission anticipated
this with a circuit breaker; most did not.)

**Our choice.** `max_active_sessions` bounds concurrency: excess dispatches are QUEUED and promoted
by the poller as capacity frees, so a large scan cannot fan out into simultaneous full-suite runs.
`DEVIN_MAX_ACU_LIMIT` caps spend per session; `VALIDATOR_FRESH_SEED_RUNS` trades confidence for
wall-clock. A production deployment adds a true circuit breaker (open on repeated failures) and
module-scoped sweeps once discovery has pinned the leaker set.

---

## The field, mapped to the research it ignored

| Prior submission's implicit assumption | The research that refutes it | What we do instead |
|---|---|---|
| CI-green == correct | Goodhart; reward hacking (Amodei 2016) | independent statistical re-run; assume the proxy is gamed |
| The agent's self-report is trustworthy | overconfidence; LLM self-preference (Panickssery 2024) | never trust self-report; re-derive from a clean checkout |
| One verifier (often CI or an LLM) suffices | LLM-judge bias; correlated errors (Zheng 2023) | execution + deterministic static analysis, not an LLM judge |
| "Tests pass" is a strong signal | SWE-bench Verified; weak/insufficient tests | outcome (multi-seed) + process (diff/provenance) gates |
| Just ask the agent to double-check | intrinsic self-correction fails (Huang 2023) | feed back concrete *external* evidence, bounded rounds |
| Always produce a fix | selective prediction / defer | escalate product bugs; `inconclusive` over fabrication |
| Fan out freely | (cost) circuit-breaker pattern | bounded concurrency + per-session ACU cap |

The throughline: **the prior submissions are generators bolted to weak verifiers, built on the
unexamined assumption that agent output is trustworthy.** The harness research says that assumption
is wrong in seven specific, named ways. This system is the verifier those findings imply.

> Citations name representative works for each idea; the argument rests on the well-established
> *concepts* (reward hacking, generator-verifier gap, LLM-judge bias, externally-grounded
> self-correction, selective prediction, order-dependence flake detection), not on any single paper.
