# Professional terminal and quantitative-platform benchmarks

**Research date:** 2026-10-03  
**Scope:** Bloomberg Terminal, LSEG Workspace, FactSet, S&P Capital IQ Pro, TradingView professional offerings, and QuantConnect, benchmarked against this repository's Vietnamese-market investment platform.  
**Source policy:** First-party product pages, product documentation, developer documentation, and vendor brochures only. No pricing claims are made. A blank or limited assessment means that the public sources reviewed did not establish the capability; it does not prove the vendor lacks it.

## Executive summary

The current product already covers much of the **individual analyst/trader surface**: VN-market live and historical data, charting and indicators, scanning, sector and fundamental views, price/signal alerts, portfolios and P&L, backtests, ML/regime analysis, research reports/RAG, and multi-agent research. Those capabilities are documented in the repository's [architecture](../architecture.md), [component inventory](../components.md), and [data/integration map](../data-and-integrations.md).

The largest gap is not another chart or indicator. It is the **controlled lifecycle from idea to position to order to execution to reconciliation**. Bloomberg, LSEG, and FactSet document integrated portfolio/risk, OMS/EMS, compliance, straight-through processing, execution monitoring, and post-trade workflows; QuantConnect documents one algorithm definition moving from research and realistic backtesting into live brokerage execution and operational control. The repository documents portfolio records and DNSE market-data clients, but not an OMS/EMS, pre-trade compliance, approvals, execution-quality analysis, broker reconciliation, or a production algorithm deployment plane.

Three decision-relevant findings:

1. **Prioritize trusted portfolio state and controls before expanding analytics.** Institutional products unify positions, benchmark-relative risk/performance, orders, executions, compliance, and reconciliation. Bloomberg PORT combines positions, risk, performance, scenarios, optimization, and scheduled reporting; its OMS adds multi-asset order and operational workflows. FactSet similarly couples intraday portfolio analytics with rules, permissions, approvals, routing, and execution status. ([Bloomberg PORT](https://professional.bloomberg.com/products/bloomberg-terminal/portfolio-analytics/), [Bloomberg OMS](https://professional.bloomberg.com/products/trading/order-management-system/), [FactSet OMS brochure](https://go.factset.com/hubfs/Resources%20Section/Brochures/oms-brochure.pdf))
2. **Make research reproducible and promote the same strategy artifact into realistic simulation and live operation.** QuantConnect exposes notebooks, an open-source backtest engine, parameter optimization, configurable fill/slippage/fee/buying-power/settlement models, live brokerage deployment, notifications, algorithm control, and reconciliation. Bloomberg BQuant demonstrates the terminal-side equivalent: programmatic research, scalable compute, permissioned app publishing, version control, and scheduled jobs. ([QuantConnect Research](https://www.quantconnect.com/docs/v2/cloud-platform/research), [Backtesting](https://www.quantconnect.com/docs/v2/cloud-platform/backtesting), [Reality Modeling](https://www.quantconnect.com/docs/v2/writing-algorithms/reality-modeling/key-concepts), [Live Trading](https://www.quantconnect.com/docs/v2/cloud-platform/live-trading), [BQuant](https://professional.bloomberg.com/products/bloomberg-terminal/research/bquant/))
3. **Treat source provenance, entitlements, auditability, and operating permissions as product features.** LSEG explicitly describes governed data and traceable AI citations; Bloomberg and FactSet document permissioned workflows and auditable/compliant execution; QuantConnect documents private-by-default code, project/live-control permissions, encryption, and organization-level IP ownership. These controls are largely absent from the repository's documented product surface. ([LSEG Workspace](https://www.lseg.com/en/data-analytics/products/workspace), [Bloomberg EMS](https://professional.bloomberg.com/products/trading/execution-management-system/), [FactSet OMS brochure](https://go.factset.com/hubfs/Resources%20Section/Brochures/oms-brochure.pdf), [QuantConnect Security and IP](https://www.quantconnect.com/docs/v2/cloud-platform/security-and-ip), [QuantConnect Collaboration](https://www.quantconnect.com/docs/v2/cloud-platform/projects/collaboration))

## Capability tiers

- **Table stakes (T):** expected in a serious analyst/trader terminal: broad discovery, linked chart/news/fundamental context, reusable screens/watchlists, alerts, portfolio views, export/API interoperability, and reliable multi-device monitoring.
- **Institutional-only (I):** usually justified by team, fiduciary, regulatory, or operational complexity: enterprise data entitlements, multi-asset factor risk and stress testing, benchmark attribution, OMS/EMS, pre-trade compliance, approvals, FIX/routing, straight-through processing, reconciliation, audit history, and permissioned collaboration.
- **Quant-specific (Q):** required for systematic research and execution: code/notebook research, point-in-time datasets, configurable market-reality models, parameter optimization with overfit controls, one code path for backtest and live, broker adapters, deployment control, and run lineage.

The tiers describe workflow complexity, not vendor prestige. TradingView is a useful benchmark for polished table-stakes charting and alerts; Bloomberg/LSEG/FactSet set institutional workflow benchmarks; QuantConnect provides the clearest public quant lifecycle benchmark.

## Concise benchmark matrix

| Product | Data / discovery | Research | Portfolio / risk | Backtesting | Execution / OMS / EMS | Monitoring / collaboration | Governance / operations | Public-source assessment |
|---|---|---|---|---|---|---|---|---|
| **Bloomberg Terminal** | T/I | T + Q via BQuant | I | Q via BQuant strategy testing; public page does not establish a full execution simulator | I | T/I | I | Broadest documented integrated terminal workflow: multi-asset data/research, Launchpad alerts, Instant Bloomberg, PORT, AIM/EMS, APIs and enterprise data delivery. ([Terminal](https://professional.bloomberg.com/products/bloomberg-terminal/), [PORT](https://professional.bloomberg.com/products/bloomberg-terminal/portfolio-analytics/), [BQuant](https://professional.bloomberg.com/products/bloomberg-terminal/research/bquant/), [OMS](https://professional.bloomberg.com/products/trading/order-management-system/), [EMS](https://professional.bloomberg.com/products/trading/execution-management-system/), [Data License](https://professional.bloomberg.com/products/data/data-license/)) |
| **LSEG Workspace** | T/I | T; extensible with data APIs | I with adjacent LSEG portfolio stack | Not established as a core Workspace capability in reviewed public docs | I through REDI/TORA/AlphaDesk | T/I | I in the broader LSEG stack | Workspace documents governed data, Reuters news, analytics, search, portfolios, collaboration and APIs; LSEG's adjacent stack connects research, risk/attribution, OEMS, FIX, compliance, IBOR and post-trade oversight. ([Workspace](https://www.lseg.com/en/data-analytics/products/workspace), [Equities](https://www.lseg.com/en/data-analytics/products/workspace/equities), [Multi-Asset Trading](https://www.lseg.com/en/data-analytics/trading-solutions/multi-asset-trading)) |
| **FactSet** | T/I | T/Q | I | Q strategy simulation is asserted on FactSet's quant page; implementation detail is limited publicly | I | T/I | I | Strong public evidence for portfolio analytics, intraday attribution/risk, OMS/compliance, permissions, approvals, routing, and published APIs. ([Portfolio Analytics](https://www.factset.com/solutions/portfolio-analytics), [Quantitative Research](https://www.factset.com/solutions/quantitative-research), [OMS brochure](https://go.factset.com/hubfs/Resources%20Section/Brochures/oms-brochure.pdf), [Developer Portal](https://developer.factset.com/)) |
| **S&P Capital IQ Pro** | T/I | T | Limited public evidence for investment-portfolio risk compared with Bloomberg/FactSet | Not established as a Capital IQ Pro core capability | Not established | T | T/I for data delivery and Office workflows; detailed controls not public | Best-evidenced strengths are company/private-market data, documents/transcripts, sentiment/document intelligence, screening, Excel/Office modeling and delivery. Public material reviewed does not establish a native OMS/EMS or systematic live-trading lifecycle. ([Capital IQ Pro](https://www.spglobal.com/market-intelligence/en/solutions/products/sp-capital-iq-pro), [Capital IQ Pro Office](https://www.spglobal.com/market-intelligence/en/solutions/products/resources/sp-capital-iq-pro-office), [2026 product update](https://press.spglobal.com/2026-03-12-S-P-Global-Enhances-Capital-IQ-Pro-with-Expanded-Fixed-Income,-Biopharma-and-Private-Markets-Data-Content-and-AI-Capabilities)) |
| **TradingView professional offerings** | T | T/Q-lite through Pine | T tracking; not institutional factor risk | Q-lite strategy testing / Bar Replay | T broker and paper-trading interfaces; not a documented institutional OMS | T | Limited | Benchmark for responsive charts, custom indicators, screeners, alerts/webhooks, paper trading, DOM/chart trading and broker handoff. It is not evidenced as an institutional portfolio-risk or compliance platform. ([Features](https://www.tradingview.com/features/), [Screeners](https://www.tradingview.com/support/solutions/43000718885-tradingview-screeners-walkthrough/), [Paper Trading](https://www.tradingview.com/support/solutions/43000516466-paper-trading-main-functionality/), [Webhooks](https://www.tradingview.com/support/solutions/43000529348-how-to-configure-webhook-alerts/), [Plans/features](https://www.tradingview.com/pricing/)) |
| **QuantConnect** | Q | Q | Q strategy-level portfolio/risk | Q | Q broker adapters and live algorithms, not a discretionary institutional OMS | Q | Q | Clearest public end-to-end systematic lifecycle: notebooks, backtests, optimization, reality models, live deployment/control/reconciliation, collaboration and security/IP controls. ([Research](https://www.quantconnect.com/docs/v2/cloud-platform/research), [Backtesting](https://www.quantconnect.com/docs/v2/cloud-platform/backtesting), [Optimization](https://www.quantconnect.com/docs/v2/cloud-platform/optimization), [Reality Modeling](https://www.quantconnect.com/docs/v2/writing-algorithms/reality-modeling/key-concepts), [Live Trading](https://www.quantconnect.com/docs/v2/cloud-platform/live-trading), [Collaboration](https://www.quantconnect.com/docs/v2/cloud-platform/projects/collaboration)) |

## Workflow benchmark

### 1. Data and discovery

**Table stakes**

- Federated instrument/company search; reusable multi-criteria screens; saved watchlists; technical and fundamental columns; chart/table views; CSV/export; linked news, filings, estimates and research. TradingView documents asset-specific screeners, custom filters and columns, saved templates, CSV export, chart views, watchlist transfer, fundamentals, news and broker handoff. ([TradingView screeners](https://www.tradingview.com/support/solutions/43000718885-tradingview-screeners-walkthrough/))
- Multi-asset market context and depth. LSEG documents cross-asset instruments, long history, technical analysis, Reuters and broker research, blended order books, options analytics and real-time portfolio monitors. ([LSEG Workspace for equities](https://www.lseg.com/en/data-analytics/products/workspace/equities))
- Alerts tied to price, indicator, drawing, watchlist or custom-script conditions, with mobile/email/webhook delivery. TradingView publicly documents script/drawing alerts and webhook POST delivery, delivery status, endpoint restrictions and a 2FA prerequisite. ([TradingView features](https://www.tradingview.com/features/), [webhooks](https://www.tradingview.com/support/solutions/43000529348-how-to-configure-webhook-alerts/))

**Institutional-only**

- Enterprise-scale licensed datasets, field/security selection, scheduled delivery, cloud/API/SFTP delivery, entitlements, and a data catalog. Bloomberg Data License documents these controls and delivery paths. ([Bloomberg Data License](https://professional.bloomberg.com/products/data/data-license/))
- Governed and traceable AI/data answers. LSEG describes governed data and citations back to sources for auditable and repeatable answers. ([LSEG Workspace](https://www.lseg.com/en/data-analytics/products/workspace))
- Full-tick/direct feeds and normalized reference, pricing, corporate-actions, estimates and alternative data are distinct products and licenses, not simply a UI feature. LSEG's operating model explicitly separates direct feeds from the Workspace decision layer. ([LSEG Multi-Asset Trading](https://www.lseg.com/en/data-analytics/trading-solutions/multi-asset-trading))

**Current-platform gap**

The repository has strong VN-specific ingestion (DNSE REST/MQTT, ClickHouse ticks and bars, Delta historical data, Wichart reports, fundamentals and derived signals), but its own design notes say some TradingAgents inputs rely on scraped/third-party sources and one insider-data tool is unavailable. ([Data integrations](../data-and-integrations.md), [TCBS MCP design](../superpowers/specs/2026-09-01-tcbs-mcp-integration-design.md)) The priority gap is an explicit data catalog showing source, entitlement/license, freshness, adjustment policy, point-in-time status, field lineage and quality incidents—not merely more feeds.

### 2. Research

**Table stakes**

- A linked workflow from screen/watchlist to chart, fundamentals, news, filings/transcripts, estimates, research notes and export. Bloomberg describes integrated data, news, research, analytics and a global collaboration network; Capital IQ Pro documents news, filings, research, transcripts, presentations, sentiment and key-phrase search. ([Bloomberg Terminal](https://professional.bloomberg.com/products/bloomberg-terminal/), [S&P Capital IQ Pro](https://www.spglobal.com/market-intelligence/en/solutions/products/sp-capital-iq-pro))
- Spreadsheet/Office interoperability remains table stakes for professional fundamental research. S&P documents Capital IQ Pro Office, and its 2026 release describes natural-language financial-model building and querying planned for the Excel plug-in. ([Capital IQ Pro Office](https://www.spglobal.com/market-intelligence/en/solutions/products/resources/sp-capital-iq-pro-office), [S&P 2026 update](https://press.spglobal.com/2026-03-12-S-P-Global-Enhances-Capital-IQ-Pro-with-Expanded-Fixed-Income,-Biopharma-and-Private-Markets-Data-Content-and-AI-Capabilities))

**Quant-specific**

- A notebook/code environment next to entitled data, reusable project code and visualization. QuantConnect provides Jupyter notebooks with Python/C# and project-code imports; Bloomberg BQuant provides programmatic Bloomberg data/analytics, open-source libraries, templates, scalable compute and interactive visualizations. ([QuantConnect Research](https://www.quantconnect.com/docs/v2/cloud-platform/research), [Bloomberg BQuant](https://professional.bloomberg.com/products/bloomberg-terminal/research/bquant/))
- Permissioned publishing, version control, scheduled/long-running jobs and firm data integration. Bloomberg documents interactive BQuant apps published to Launchpad, version control, firm-data integration, dedicated infrastructure, distributed/GPU compute and scheduled jobs. ([Bloomberg BQuant](https://professional.bloomberg.com/products/bloomberg-terminal/research/bquant/))

**Current-platform gap**

The product has report/RAG chat and multi-agent research with persisted agent reports, but no documented first-class research notebook, dataset snapshot, dependency/environment lock, experiment-to-strategy promotion, peer review, or permissioned research publication. The existing experiment analytics and agent-run persistence are useful starting points, but they do not yet form reproducible research lineage. ([Components](../components.md), [TradingAgents integration](../../backend/app/services/tradingagents/README.md))

### 3. Portfolio and risk

**Table stakes**

- Holdings, transactions, cash, realized/unrealized P&L, allocation, benchmark comparison and performance history.
- Portfolio monitoring linked to alerts and research context. LSEG documents real-time monitoring of instruments and multiple portfolios across asset classes. ([LSEG Workspace for equities](https://www.lseg.com/en/data-analytics/products/workspace/equities))

**Institutional-only**

- Multi-asset exposure, factor/forecast risk, benchmark-relative attribution, optimization and scenario testing. Bloomberg PORT documents positions/risk/performance unification, attribution, risk-controlled optimization, and factor, full-valuation, macroeconomic and climate scenarios. ([Bloomberg PORT](https://professional.bloomberg.com/products/bloomberg-terminal/portfolio-analytics/))
- Intraday performance, attribution and risk connected to the order workflow. FactSet documents real-time intraday analytics and exposure monitoring within its OMS/Portfolio Analysis workflow. ([FactSet OMS brochure](https://go.factset.com/hubfs/Resources%20Section/Brochures/oms-brochure.pdf))
- Automated portfolio reporting, third-party inputs, validation and scheduled delivery. Bloomberg PORT documents validation, templates, batching, scheduling and a dependency-aware orchestration engine. ([Bloomberg PORT](https://professional.bloomberg.com/products/bloomberg-terminal/portfolio-analytics/))

**Current-platform gap**

The current product documents holdings, transactions, realized/unrealized P&L, optimization, and an auditable/reversible corporate-action application ledger. It still lacks benchmark-relative attribution, factor exposure, ex-ante risk, scenario/stress testing, liquidity/capacity risk, multi-account and multi-currency cash, broker position/cash reconciliation, and scheduled client-grade reporting. ([Components](../components.md), [portfolio service](../../backend/app/services/portfolio_service.py), [corporate-action model](../../backend/app/db/models/corporate_action.py)) A professional next step is a versioned, user/account-scoped portfolio and cash ledger plus a benchmark/risk service with explainable calculations, before adding more prediction models.

### 4. Backtesting

**Quant-specific table stakes**

- The same strategy code and data contract should support research, historical simulation and live deployment. QuantConnect presents research, backtesting, optimization and live trading as one project lifecycle. ([QuantConnect Research](https://www.quantconnect.com/docs/v2/cloud-platform/research), [Backtesting](https://www.quantconnect.com/docs/v2/cloud-platform/backtesting), [Live Trading](https://www.quantconnect.com/docs/v2/cloud-platform/live-trading))
- Market-reality models must cover fills, slippage, fees, brokerage behavior, buying power/margin, settlement, short availability, options behavior, capacity and portfolio state; illiquid/high-volume strategies require custom models. ([QuantConnect Reality Modeling](https://www.quantconnect.com/docs/v2/writing-algorithms/reality-modeling/key-concepts))
- Parameter optimization needs declared parameters/objectives/strategy, retained results and explicit overfitting warnings or out-of-sample practice. QuantConnect documents this workflow and warns that past-fit parameters may not be robust out of sample. ([QuantConnect Optimization](https://www.quantconnect.com/docs/v2/cloud-platform/optimization))
- Results need immutable inputs, run lineage, diagnostics, orders/fills, equity/drawdown, risk statistics and shareable reports—not only a summary return.

**Current-platform gap**

The repository has a strategy backtest service and visualization, but public internal documentation does not establish point-in-time universes/fundamentals, configurable VN fee/tax/slippage/lot/price-limit/settlement models, corporate actions, reproducible environment/version identifiers, walk-forward or out-of-sample controls, or a single strategy artifact promoted to live. ([Components](../components.md)) These are higher-value additions than expanding the strategy list.

### 5. Execution / OMS / EMS

**Table stakes for an execution-enabled product**

- Paper trading; explicit market/limit/stop orders; stop-loss/take-profit; positions/orders in real time; chart and depth-of-market entry. TradingView documents these interfaces and distinguishes simulated trading from integrated partner-broker live trading. ([TradingView Paper Trading](https://www.tradingview.com/support/solutions/43000516466-paper-trading-main-functionality/))
- Broker connectivity with observable order lifecycle, rejects, partial fills and cancellations.

**Institutional-only**

- Multi-asset OMS/EMS, liquidity discovery, routing, straight-through processing, front/middle/back-office status, pre-trade compliance, permissions and approvals. Bloomberg documents real-time liquidity, direct buy-/sell-side connectivity, risk/compliance, AIM/TOMS, and EMS interoperability across pre-trade, OMS and post-trade tools. ([Bloomberg OMS](https://professional.bloomberg.com/products/trading/order-management-system/), [Bloomberg EMS](https://professional.bloomberg.com/products/trading/execution-management-system/))
- A rules engine supporting concentration, look-through, compound and limit checks, with breach causes, approvals and permissioned workflows. FactSet documents these controls and published APIs for orders, executions, compliance and prices. ([FactSet OMS brochure](https://go.factset.com/hubfs/Resources%20Section/Brochures/oms-brochure.pdf))
- Integrated research-to-execution plus FIX/IOI connectivity, IBOR, cash/exposure oversight, reconciliation and post-trade reporting. LSEG documents REDI in Workspace, TORA automation/APIs, Autex FIX/IOI, and AlphaDesk portfolio/order management, IBOR, risk/compliance and operational oversight. ([LSEG Multi-Asset Trading](https://www.lseg.com/en/data-analytics/trading-solutions/multi-asset-trading))

**Quant-specific**

- Live algorithm deployment to broker adapters, with notifications, human intervention, algorithm controls, reconciliation and risk documentation. ([QuantConnect Live Trading](https://www.quantconnect.com/docs/v2/cloud-platform/live-trading))

**Current-platform gap**

The repository documents DNSE market-data/trading SDK infrastructure, portfolio transactions and server-side broker credentials, but it does not document a user-facing order blotter, order state machine, broker reconciliation, paper/live parity, pre-trade risk limits, approval gates, execution-quality/TCA, kill switch, or production algorithm runner. ([Data integrations](../data-and-integrations.md), [DNSE SDK README](../../worker/dnse_sdk/README.md)) This is the most material capability gap.

### 6. Monitoring and collaboration

**Table stakes**

- Saved workspaces/watchlists, persistent alerts, mobile access and clear delivery status. Bloomberg documents Launchpad security monitors, alerts, charting/news and mobile Terminal access; TradingView documents web/desktop/mobile use and webhook status. ([Bloomberg Terminal](https://professional.bloomberg.com/products/bloomberg-terminal/), [TradingView webhooks](https://www.tradingview.com/support/solutions/43000529348-how-to-configure-webhook-alerts/))
- Shareable research, screens and dashboards with controlled access.

**Institutional-only**

- Secure internal/external messaging embedded in data and workflow. Bloomberg documents Instant Bloomberg and its network; LSEG documents Open Directory, Teams and LSEG Messenger in the same environment as data and transactions. ([Bloomberg Terminal](https://professional.bloomberg.com/products/bloomberg-terminal/), [LSEG Workspace](https://www.lseg.com/en/data-analytics/products/workspace))
- Permissioned team views, live-control rights, approval ownership and an audit trail. FactSet documents shared views and a permission-based operating model. ([FactSet OMS brochure](https://go.factset.com/hubfs/Resources%20Section/Brochures/oms-brochure.pdf))

**Quant-specific**

- Real-time collaborative code editing, explicit live-deployment control, edit locking, organization ownership, cloning and controlled sharing. QuantConnect documents each of these controls. ([QuantConnect Collaboration](https://www.quantconnect.com/docs/v2/cloud-platform/projects/collaboration))

**Current-platform gap**

The product has Telegram notifications, persisted TradingAgents runs and workflow orchestration, but the documented surface lacks shared workspaces, comments/mentions, review/approval states, incident/run dashboards, delivery acknowledgements, team permissions and escalation policies. ([Data integrations](../data-and-integrations.md), [TradingAgents integration](../../backend/app/services/tradingagents/README.md))

### 7. Governance and operations

**Institutional-only**

- Data/source entitlements, auditable AI answers, permissioned research, pre-trade compliance, approvals, immutable order/execution history, operational reconciliation and scheduled reporting. These controls appear across LSEG Workspace, Bloomberg OMS/PORT and FactSet OMS. ([LSEG Workspace](https://www.lseg.com/en/data-analytics/products/workspace), [Bloomberg OMS](https://professional.bloomberg.com/products/trading/order-management-system/), [Bloomberg PORT](https://professional.bloomberg.com/products/bloomberg-terminal/portfolio-analytics/), [FactSet OMS brochure](https://go.factset.com/hubfs/Resources%20Section/Brochures/oms-brochure.pdf))
- Firm-level IP ownership, private-by-default code, restricted live control, encryption and optionally on-premises operation. QuantConnect documents private code, AES-256 project encryption using a locally hosted key, organization IP rules and a fully offline local platform. ([QuantConnect Security and IP](https://www.quantconnect.com/docs/v2/cloud-platform/security-and-ip), [QuantConnect Collaboration](https://www.quantconnect.com/docs/v2/cloud-platform/projects/collaboration))
- Separation of research permission from production/live control. QuantConnect's collaborator model explicitly grants live control separately. ([QuantConnect Collaboration](https://www.quantconnect.com/docs/v2/cloud-platform/projects/collaboration))

**Current-platform gap**

The application now enforces bearer authentication globally, but the portfolio, transaction and alert records are not user- or account-scoped, so authenticated users share one portfolio domain. The repository does not establish role-based authorization, per-dataset entitlements, maker-checker approvals, immutable application-wide audit events, secrets rotation, retention policy, disaster recovery objectives, model approval, or production change controls. ([authentication guard](../../backend/app/api/deps.py), [portfolio models](../../backend/app/db/models/portfolio.py), [Data integrations](../data-and-integrations.md)) Governance should be designed into portfolio, order and strategy records rather than added as UI later.

## Recommended capability sequence

1. **Trust layer:** instrument master; provider/source/freshness/adjustment metadata; point-in-time snapshots; data-quality incidents; versioned portfolio and cash ledger; benchmark definitions.
2. **Risk layer:** performance history and benchmark attribution; exposures; concentration and liquidity limits; scenario/stress tests; explainable calculation lineage; scheduled reports.
3. **Execution safety layer:** paper account; canonical order state machine; broker adapter boundary; order blotter; pre-trade limits; explicit approvals; idempotent submit/cancel; broker reconciliation; kill switch; immutable audit events.
4. **Quant lifecycle:** notebooks or equivalent research workspace; immutable run manifests; realistic VN fees/taxes/lot sizes/price bands/settlement/corporate actions/slippage; walk-forward/out-of-sample evaluation; strategy registry; controlled promotion from research to paper to live.
5. **Team operations:** RBAC; separate research/edit/live-control permissions; shared views; comments/review; alert acknowledgement and escalation; job/incident dashboards; retention/export.

This ordering preserves the current product's VN-market differentiation while adding the controls that distinguish a useful analytics application from a professional research and execution system.

## What public documentation could not establish

- **Vietnam coverage and local execution:** The reviewed global-vendor pages do not reliably establish current HOSE/HNX/UPCoM instrument coverage, VN corporate-action quality, local exchange depth, broker connectivity, odd-lot/price-band behavior, taxes/fees or settlement handling. Those require entitlement-specific vendor checks and official Vietnamese exchange/broker specifications; no equivalence is claimed here.
- **Commercial packaging:** Features can depend on product modules, entitlements, region and contract. Public pages do not establish what is included in a base subscription, so this note makes no pricing or bundling comparison.
- **Bloomberg backtesting:** BQuant publicly documents building and testing strategies, but the reviewed page does not establish a full event-driven execution simulator with the explicit fill/slippage/fee/brokerage models documented by QuantConnect.
- **LSEG Workspace backtesting:** Public Workspace pages reviewed establish analytics, APIs, collaboration and adjacent execution/portfolio systems, but not a core systematic backtest engine.
- **S&P Capital IQ Pro OMS/live execution:** Public sources reviewed establish discovery, documents, AI analysis, Office workflows and expanded datasets, but not native OMS/EMS, pre-trade compliance or live algorithm deployment.
- **TradingView institutional controls:** Public docs establish charts, Pine, screeners, alerts/webhooks, paper trading and broker integrations; they do not establish factor-risk infrastructure, institutional OMS/IBOR, compliance rules, approvals or reconciliation.
- **FactSet and Capital IQ Pro fine-grained feature parity:** Some detailed documentation is gated or rendered client-side. Claims above are deliberately limited to the vendor pages and brochures accessible publicly.

## Primary-source index

- Bloomberg: [Terminal](https://professional.bloomberg.com/products/bloomberg-terminal/), [PORT](https://professional.bloomberg.com/products/bloomberg-terminal/portfolio-analytics/), [BQuant](https://professional.bloomberg.com/products/bloomberg-terminal/research/bquant/), [OMS](https://professional.bloomberg.com/products/trading/order-management-system/), [EMS](https://professional.bloomberg.com/products/trading/execution-management-system/), [Data License](https://professional.bloomberg.com/products/data/data-license/)
- LSEG: [Workspace](https://www.lseg.com/en/data-analytics/products/workspace), [Workspace for equities](https://www.lseg.com/en/data-analytics/products/workspace/equities), [Multi-Asset Trading](https://www.lseg.com/en/data-analytics/trading-solutions/multi-asset-trading)
- FactSet: [Portfolio Analytics](https://www.factset.com/solutions/portfolio-analytics), [Quantitative Research](https://www.factset.com/solutions/quantitative-research), [Order Management and Compliance brochure](https://go.factset.com/hubfs/Resources%20Section/Brochures/oms-brochure.pdf), [Developer Portal](https://developer.factset.com/)
- S&P Global: [Capital IQ Pro](https://www.spglobal.com/market-intelligence/en/solutions/products/sp-capital-iq-pro), [Capital IQ Pro Office](https://www.spglobal.com/market-intelligence/en/solutions/products/resources/sp-capital-iq-pro-office), [March 2026 product update](https://press.spglobal.com/2026-03-12-S-P-Global-Enhances-Capital-IQ-Pro-with-Expanded-Fixed-Income,-Biopharma-and-Private-Markets-Data-Content-and-AI-Capabilities)
- TradingView: [Features](https://www.tradingview.com/features/), [Screeners](https://www.tradingview.com/support/solutions/43000718885-tradingview-screeners-walkthrough/), [Paper Trading](https://www.tradingview.com/support/solutions/43000516466-paper-trading-main-functionality/), [Webhook alerts](https://www.tradingview.com/support/solutions/43000529348-how-to-configure-webhook-alerts/), [Plans and feature matrix](https://www.tradingview.com/pricing/)
- QuantConnect: [Research](https://www.quantconnect.com/docs/v2/cloud-platform/research), [Backtesting](https://www.quantconnect.com/docs/v2/cloud-platform/backtesting), [Optimization](https://www.quantconnect.com/docs/v2/cloud-platform/optimization), [Reality Modeling](https://www.quantconnect.com/docs/v2/writing-algorithms/reality-modeling/key-concepts), [Live Trading](https://www.quantconnect.com/docs/v2/cloud-platform/live-trading), [Collaboration](https://www.quantconnect.com/docs/v2/cloud-platform/projects/collaboration), [Security and IP](https://www.quantconnect.com/docs/v2/cloud-platform/security-and-ip)
