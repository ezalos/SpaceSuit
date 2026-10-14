# Authority Map — concrete publisher roster

<!-- ABOUTME: Global baseline roster of named publishers mapped to slide-creation-standards.md §6.2 tiers. -->
<!-- ABOUTME: Extended per-run by /cite-scan and promoted globally via /cite-apply promotion gates. -->

This file lists **which publishers count at which tier** for citation purposes.
Tier *definitions* live in `slide-creation-standards.md` §6.2; this file is the
populated *roster*. The `/cite` skill family reads this to assign
`authority_tier` to discovered sources and flag low-reputation ones.

Grow this file by promoting per-run overlays (see `/cite-apply` promotion gate).

---

## Tier 1 — Primary sources (company IR, SEC filings, government)

- **SEC.gov** (`sec.gov`) — US Securities and Exchange Commission (filings, enforcement actions, joint reports with CFTC)
- **CFTC.gov** (`cftc.gov`) — US Commodity Futures Trading Commission
- **EUR-Lex** (`eur-lex.europa.eu`) — EU legal texts, including the AI Act
- **European Parliament** (`europarl.europa.eu`) — press releases, committee reports
- **European Commission** (`ec.europa.eu`, `digital-strategy.ec.europa.eu`) — official communications
- **Company investor relations** — any URL matching `investor.*`, `ir.*`, or `<domain>/investors`
- **Company official news pages** — `anthropic.com/news`, `openai.com/index/*`, `mistral.ai/news`, etc.
- **Company pricing pages** — `openai.com/pricing`, `anthropic.com/pricing`, `aws.amazon.com/*/pricing`
- **Anthropic** (`anthropic.com`) — AI safety company
- **OpenAI** (`openai.com`) — AI research and products
- **Mistral AI** (`mistral.ai`) — European AI company
- **Generalist AI** (`generalistai.com`) — robot foundation models (GEN-0, GEN-1, GEN-1.5); tier 1 for claims about its own models only
- **Skild AI** (`skild.ai`) — robot foundation models (S1); tier 1 for claims about its own models only
- **Dyna Robotics** (`dyna.co`) — robot foundation models (DYNA-1, DYNA-2); tier 1 for claims about its own models only
- **Government statistics offices** — INSEE (`insee.fr`), Eurostat (`eurostat.ec.europa.eu`), BLS (`bls.gov`), ONS (`ons.gov.uk`)
- **Klarna** (`klarna.com`) — company press releases
- **L'Oréal Finance** (`loreal-finance.com`) — investor relations / annual report
- **Fin AI** (`fin.ai`) — Intercom product / pricing page
- **Google Developers** (`developers.google.com`) — official Google documentation (Rules of ML, etc.)
- **EU AI Act Service Desk** (`ai-act-service-desk.ec.europa.eu`) — official EU AI Act support
- **Meta AI** (`ai.meta.com`) — Meta's official AI blog (Llama announcements)
- **Google corporate blog** (`blog.google`) — official Google/Alphabet blog
- **Google DeepMind** (`deepmind.google`) — Google DeepMind official site
- **Anthropic Claude product** (`claude.com`) — Claude product/feature pages
- **AWS** (`aws.amazon.com`) — AWS official announcements and documentation
- **Linux Foundation** (`linuxfoundation.org`) — foundation press releases and announcements
- **EU AI Act explainer** (`artificialintelligenceact.eu`) — official EU AI Act portal maintained by the Future of Life Institute / EC contributors
- **Coursera** (`coursera.org`) — course platform, authoritative for official course pages (e.g., Andrew Ng's Generative AI for Everyone)

## Tier 2 — Peer-reviewed academic

- **arXiv** (`arxiv.org`) — preprints (note acceptance venue in quote when available)
- **NeurIPS / ICML / ICLR / EMNLP / ACL** — ML conference proceedings
- **Nature** (`nature.com`), **Science** (`science.org`) — journals
- **IEEE Xplore** (`ieeexplore.ieee.org`), **ACM Digital Library** (`dl.acm.org`) — engineering/CS journals
- **The Lancet** (`thelancet.com`), **NEJM** (`nejm.org`) — medical journals (if ever relevant)
- **SWE-bench** (`swebench.com`) — academic coding-agent benchmark (paper: Jimenez et al.)
- **Terminal-Bench** (`tbench.ai`) — Stanford / Laude Institute terminal-agent benchmark
- **OSWorld** (`os-world.github.io`) — academic benchmark for computer-use agents

## Tier 3 — Tier-1 research firms and trackers

- **Gartner** (`gartner.com`) — market forecasts, Magic Quadrants
- **McKinsey** (`mckinsey.com`, McKinsey Global Institute)
- **Deloitte Insights** (`deloitte.com`)
- **IDC** (`idc.com`)
- **Forrester** (`forrester.com`)
- **Stanford HAI AI Index** (`hai.stanford.edu`, `aiindex.stanford.edu`)
- **OECD.AI** (`oecd.ai`)
- **Epoch AI** (`epochai.org`, `epoch.ai`) — compute / training / model trends
- **Our World in Data** (`ourworldindata.org`)
- **CB Insights** (`cbinsights.com`)
- **Statista** (`statista.com`)
- **Stack Overflow Developer Survey** (`survey.stackoverflow.co`) — large-N developer methodology survey
- **Microsoft Research** (`microsoft.com`) — corporate research publications
- **Databricks** (`databricks.com`) — engineering / research blog
- **Hugging Face** (`huggingface.co`) — official docs and courses
- **MIT Sloan Management Review** (`sloanreview.mit.edu`)
- **Andreessen Horowitz** (`a16z.com`) — VC research and market commentary
- **Bessemer Venture Partners** (`bvp.com`) — State of AI / State of the Cloud reports
- **METR** (`metr.org`) — model evaluation and safety research organisation (long-task benchmarks)
- **Vals AI** (`vals.ai`) — independent benchmark verification / leaderboards
- **Center for Data Innovation** (`datainnovation.org`, `www2.datainnovation.org`) — EU-policy think tank (EU AI Act compliance cost studies)
- **Cisco (corporate research/security blog)** (`blogs.cisco.com`) — corporate engineering/security publications
- Domain-specific:
  - **SemiAnalysis** (`semianalysis.com`) — AI hardware / datacenter
  - **CEPS** (`ceps.eu`) — EU policy
  - **Chinchilla / DeepMind research blog** — LLM scaling laws

## Tier 4 — Tier-1 press

- **Bloomberg** (`bloomberg.com`)
- **Reuters** (`reuters.com`)
- **Financial Times** (`ft.com`)
- **CNBC** (`cnbc.com`)
- **The New York Times** (`nytimes.com`)
- **The Wall Street Journal** (`wsj.com`)
- **The Economist** (`economist.com`)
- **The Information** (`theinformation.com`)
- **Les Échos** (`lesechos.fr`) — FR business press
- **Le Monde** (`lemonde.fr`) — FR general press (business sections)

## Tier 5 — Tier-2 press (flagged, needs human review)

- **TechCrunch** (`techcrunch.com`)
- **The Verge** (`theverge.com`)
- **Ars Technica** (`arstechnica.com`)
- **Wired** (`wired.com`)
- **VentureBeat** (`venturebeat.com`)
- **MIT Technology Review** (`technologyreview.com`)
- **Sifted** (`sifted.eu`) — EU startup press
- **Entrepreneur** (`entrepreneur.com`) — business / startup press
- **Latent Space** (`latent.space`) — AI industry editorial (swyx newsletter)
- **Ahead of AI — Sebastian Raschka** (`magazine.sebastianraschka.com`) — recognized ML practitioner blog
- **Chip Huyen** (`huyenchip.com`) — recognized ML practitioner blog
- **Jason Liu** (`jxnl.co`) — AI practitioner blog
- **VintageData** (`vintagedata.org`) — practitioner blog on synthetic data
- **PYMNTS** (`pymnts.com`) — payments / retail trade press
- **36Kr** (`36kr.com`, `eu.36kr.com`) — Chinese tech press
- **The New Stack** (`thenewstack.io`) — developer-focused tech press
- **Peter Steinberger blog** (`steipete.me`) — recognized practitioner blog (OpenClaw, iOS/AI tooling)
- **Revenue Wizards** (`revenuewizards.com`) — SaaS pricing practitioner blog
- **Jarvislabs docs** (`docs.jarvislabs.ai`) — GPU-infra practitioner docs / blog
- **Spheron Network** (`spheron.network`) — GPU / ML infra practitioner blog
- **Richard Sutton — Incomplete Ideas** (`incompleteideas.net`) — Sutton's personal essay site (The Bitter Lesson)

## Tier 6 — Startup databases and aggregators (flagged, needs human review)

- **Crunchbase** (`crunchbase.com`)
- **Sacra** (`sacra.com`)
- **PitchBook** (`pitchbook.com`)
- **Dealroom** (`dealroom.co`)
- **Wikipedia** (`en.wikipedia.org`, `fr.wikipedia.org`) — acceptable only as a pointer to primary sources
- **GitHub** (`github.com`) — code/README host; use only as pointer to code or official repo documentation
- **YouTube** (`youtube.com`) — video aggregator; use only as pointer to official channel content
- **X / Twitter** (`x.com`, `twitter.com`) — social media; use only for public statements by primary authors
- **Tracxn** (`tracxn.com`) — startup data aggregator

---

## How `/cite` uses this file

- `/cite-scan` reads this file and proposes a per-run overlay with domain-specific sources it surfaced during pre-research.
- `/cite-research` resolves `publisher_org` → `authority_tier` by matching against entries here (and the per-run overlay). A publisher not matched to any tier defaults to `tier: unknown` and flags `flagged-low-reputation`.
- `/cite-apply` offers a promotion gate: per-run overlay entries that Louis approves get appended here.

When promoting a new entry, include the domain-specific context as a comment (e.g., `- **Jane Street Tech Blog** (trading-tech domain only)`).
