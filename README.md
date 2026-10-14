# Freight Cost Anomaly Detection Engine

Autonomous weekly freight rate surveillance system designed to isolate genuine operational disruptions from unjustified tariff inflation across Indian freight corridors.

---

## 1. System Overview

Road freight transportation costs fluctuate based on fuel dynamics, highway infrastructure disruptions, seasonal demand spikes, and regulatory mandates. This system ingests multi-year shipment transaction records and structured corridor context notes to identify statistically anomalous route-week rates and evaluate whether the escalation is legitimately justified.

```
+------------------------+      +-----------------------+
|  shipment_records.csv  |      |   context_notes.csv   |
+-----------+------------+      +-----------+-----------+
            |                               |
            v                               v
+------------------------+      +-----------------------+
| Weekly Aggregation     |      | Disruption Boundary   |
| Metric: CPTK           |      | & Impact Extraction   |
+-----------+------------+      +-----------+-----------+
            |                               |
            v                               |
+------------------------+                  |
| Vectorized Baselines   |                  |
| - Trailing 8-Week Avg  |                  |
| - Peer Corridor Avg    |                  |
+-----------+------------+                  |
            |                               |
            v                               |
+------------------------+                  |
| Anomaly Filter         |                  |
| Thresholds: 20% / 15%  |                  |
+-----------+------------+                  |
            |                               |
            +---------------+---------------+
                            |
                            v
            +-------------------------------+
            | Grounded Verification Engine  |
            | - Route Scope Validation      |
            | - Temporal Overlap Check      |
            | - Anti-Hallucination Filter   |
            +---------------+---------------+
                            |
                            v
            +-------------------------------+
            |          output.csv           |
            +-------------------------------+
```

---

## 2. Core Methodology

### 2.1 Metric Formulation
Shipments are aggregated by corridor route (`origin-destination`) into ISO calendar weeks (Monday through Sunday, identified by the Monday date `week_of`). 

To eliminate bias from fluctuating consignment weight and trip distance, cost is evaluated as **Cost per Tonne-Kilometre (CPTK)**:

$$\text{CPTK} = \frac{\sum \text{freight\_cost\_inr}}{\sum (\text{quantity\_tonnes} \times \text{distance\_km})}$$

### 2.2 Dual-Baseline Comparison Engine
Each route-week is benchmarked across two independent dimensions:

1. **Own History Benchmark (`vs_own_history`)**:
   - Trailing 8-week rolling mean strictly prior to the evaluated week (`shift(1)`).
   - Zero look-ahead bias. For route-weeks with fewer than 8 preceding observations, all available historical weeks are utilized without synthetic padding.
   
2. **Peer Corridor Benchmark (`vs_similar_routes`)**:
   - Same-week average across all other corridors sharing the identical distance classification (`Short`, `Medium`, `Long`).
   - Strict self-exclusion:

$$\mu_{\text{peer}} = \frac{\sum_{i \in \text{Type}} \text{CPTK}_i - \text{CPTK}_{\text{self}}}{N_{\text{Type}} - 1}$$

### 2.3 Escalation Thresholds
A corridor week is marked as an anomaly if:
- `pct_own >= 20.0%` (Escalation vs corridor baseline), OR
- `pct_peer >= 15.0%` (Divergence vs peer cohort).

### 2.4 Anti-Hallucination Verification Engine
Flagged anomalies are evaluated against `context_notes.csv`. A justification is accepted **only** if all three criteria hold:
1. **Corridor Alignment**: Note targets the specific route or `All Routes`.
2. **Temporal Overlap**: Disruption active dates intersect the Monday-to-Sunday shipment week.
3. **Causal Validity**: Notes citing non-impact signals (*e.g., "remained stable"*, *"costs were not significantly affected"*, *"compliance costs absorbed without a rate change"*) are explicitly rejected.

Unjustified spikes remain marked `flagged = "Yes"` with `matched_note_id` left empty, preventing fabricated explanations.

---

## 3. Benchmark Verification & Test Suite

The test suite in `evaluate.py` validates schema compliance, numerical precision against ground-truth sample benchmarks, guardrail integrity, and deterministic reproducibility.

```
============================================================
FREIGHT TIGER PIPELINE TEST SUITE
============================================================
  PASS schema: columns and ordering match sample_output_format exactly
  PASS Delhi-Jaipur [2024-11-11]: cptk=4.17, own=+35.5%, peer=+21.0%, flag='Yes'
  PASS Ahmedabad-Mumbai [2025-01-20]: cptk=3.29, own=+29.5%, peer=+22.5%, flag='No (justified)'
  PASS Mumbai-Pune [2025-09-15]: cptk=3.98, own=+9.2%, peer=+23.6%, flag='Yes'
  PASS guardrails: all unexplained flagged rows leave matched_note_id blank
  PASS guardrails: all 3 justified rows cite valid context notes in table and reason
  PASS guardrails: zero non-impact notes cited (rejection filter active)
  PASS reproducibility: 3 identical runs (SHA-256: 4a70fa75c3f71637...)
============================================================
TEST SUMMARY
============================================================
  Schema & Contract                   : PASSED
  Sample Rows Numerical Accuracy      : PASSED
  Hallucination & Guardrails          : PASSED
  Reproducibility (3 Runs)            : PASSED
============================================================
```

---

## 4. Reproducibility & Audit Log

Three consecutive, independent runs on raw inputs produce identical SHA-256 digests:

| Run Index | SHA-256 Digest of `output.csv` | Status |
| :---: | :--- | :---: |
| 1 | `4a70fa75c3f71637b0e86d1c552ad19248ef826cad068ae14ec9b934538d2142` | Reference |
| 2 | `4a70fa75c3f71637b0e86d1c552ad19248ef826cad068ae14ec9b934538d2142` | Bit-Identical |
| 3 | `4a70fa75c3f71637b0e86d1c552ad19248ef826cad068ae14ec9b934538d2142` | Bit-Identical |

### Operational Resource Footprint
- **LLM Calls**: 0
- **Token Ingestion / Generation**: 0 / 0
- **Total Compute Cost**: $0.00
- **Runtime Latency**: ~0.35s on standard CPU

---

## 5. Output Specification Contract

The generated `output.csv` matches the schema structure of `sample_output_format_v2.csv`:

| Column | Type | Description |
| :--- | :--- | :--- |
| `route` | String | Origin-Destination corridor identifier |
| `week_of` | ISO Date | Monday date of evaluated week (`YYYY-MM-DD`) |
| `cost_per_tonne_km` | Float | Route-week CPTK rounded to two decimal places |
| `vs_own_history` | String | Percentage deviation against trailing 8-week rolling mean |
| `vs_similar_routes` | String | Percentage deviation against same-week peer corridor mean |
| `flagged` | Enum | `Yes` (Unexplained escalation) or `No (justified)` |
| `matched_note_id` | String | Cited context note identifier (blank if unexplained) |
| `reason` | String | Fact-grounded prose detailing causal verification |

---

## 6. Execution Instructions

### Environment Setup
```bash
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### Pipeline Execution
```bash
# Execute end-to-end anomaly detection pipeline
python freight_tiger.py

# Execute test suite and regression verification
python evaluate.py
```

---

## 7. Architecture & Scalability Roadmap

1. **High-Cardinality Note Retrieval**: For enterprise deployments with thousands of unstructured logistics advisories, replace rule-based temporal matching with an embedded vector store (`sentence-transformers` + FAISS/ChromaDB), preserving the current anti-hallucination validation filter.
2. **Adaptive Statistical Bounds**: Complement static percentage thresholds with adaptive rolling Z-score / median absolute deviation (MAD) filters to account for corridor-specific volatility profiles.
3. **Interactive Conversational Audit**: Integrate an agentic tool-calling interface (e.g. LangChain / LlamaIndex) allowing operations managers to query corridor cost anomalies via natural language grounded strictly in pipeline artifacts.
