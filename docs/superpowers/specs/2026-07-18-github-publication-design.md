# OpenEnterprise Twin — GitHub publication design

## Goal

Publish OpenEnterprise Twin as a public, recruiter- and executive-ready open-source repository. The repository must immediately communicate a serious decision-simulation product, show a credible path to evaluation and provide clear next actions without generic startup marketing.

## Repository placement and publication

- Canonical local path: `/Users/miguelfidalgo/Documents/OpenEnterprise-Twin`.
- Public GitHub repository: `mfidalgomartins/OpenEnterprise-Twin`.
- The existing `main` history is preserved and becomes the remote default branch.
- No pull request is created: the user explicitly requested direct publication of the complete project.

## README information architecture

The README remains evidence-led and uses its existing Decision Room visual. Its opening gains a compact product header with status and technology badges, followed by four functional CTAs:

1. **Run the flagship demo** — anchors to the five-minute local journey.
2. **Explore the architecture** — opens the modular-monolith and persistence design.
3. **Read the model** — opens equations, assumptions and invariants.
4. **Contribute** — opens contribution contracts and quality gates.

The CTA row is followed by a one-sentence executive value proposition and a compact “what a decision becomes” flow: policy → paired simulation → evidence-linked recommendation → immutable executive brief. It makes the product differentiated before the deeper thesis and model details.

## Tone and visual system

- Editorial, precise and calm; no claims of being “AI-powered”, “revolutionary” or “best-in-class”.
- Use the Decision Room screenshot as proof of the product rather than decorative imagery.
- Keep badges restricted to useful repository signals: Python, React, FastAPI, PostgreSQL, license and CI.
- Use restrained separators, direct labels and real documentation links.
- Do not introduce metrics that the repository cannot substantiate, hosted-demo links or empty community promises.

## GitHub surface

- Public repository description: “Evidence-linked Monte Carlo decision twin for commercial, operational and liquidity policies.”
- Homepage: omitted until a deployed product URL exists.
- Topics: `digital-twin`, `monte-carlo`, `decision-intelligence`, `operations-research`, `revenue-analytics`, `supply-chain`, `fastapi`, `react`.
- The existing Apache-2.0 license, CI workflow, Dockerfiles, documentation and contribution guide remain first-class evidence of maintainability.

## Validation

- Verify markdown links point to tracked local documents.
- Verify `make lint`, `make test`, `make build` and `make e2e` after README changes where applicable.
- Confirm clean worktree, public repository visibility, remote URL, description and topics after publication.
