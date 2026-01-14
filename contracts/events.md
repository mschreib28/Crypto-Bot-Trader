# Event Contracts (Authoritative)

This document defines logical events emitted between system components.
These are **semantic events**, not transport-specific payloads.

Transport (Redis Streams, Pub/Sub, etc.) is an implementation detail.
Event ordering and idempotency requirements will be defined per event during implementation.

---

## MarketDataEvent

**Emitted by:** Data Ingestor  
**Consumed by:** Strategy Modules

**Description:**  
Normalized market data update (tick or bar). Published to Redis Streams for consumption by strategy modules.

**Schema (v1):**

| Field | Type | Description |
|-------|------|-------------|
| `symbol` | `string` | Trading pair symbol (e.g., "BTC/USD", "ETH/USD"). Must match Kraken symbol format. |
| `interval` | `string` | Time interval for the bar. Valid values: "4h" (4-hour), "1d" (1-day). For tick data, this may be "tick" or omitted. |
| `open` | `number` | Opening price of the bar. First price observed in the interval. |
| `high` | `number` | Highest price observed during the interval. Must be >= open and >= low. |
| `low` | `number` | Lowest price observed during the interval. Must be <= open and <= high. |
| `close` | `number` | Closing price of the bar. Last price observed in the interval. |
| `volume` | `number` | Total volume traded during the interval, denominated in the base asset. Must be non-negative. |
| `timestamp` | `string` (ISO8601) | UTC timestamp aligned to the interval boundary. For 4H bars: 00:00, 04:00, 08:00, etc. For 1D bars: 00:00 UTC. Format: `YYYY-MM-DDTHH:mm:ssZ` (e.g., "2024-01-01T00:00:00Z"). |

**Notes:**
- Timestamps are aligned to interval boundaries (not wall-clock time).
- Volume is aggregated from all ticks within the interval window.
- OHLC values must satisfy: `high >= max(open, close)` and `low <= min(open, close)`.

---

## TradeIntentEvent

**Emitted by:** Strategy Modules  
**Consumed by:** Risk Manager

**Description:**  
Wraps a TradeIntent for evaluation by the Risk Manager. This event signals that a strategy has generated a trading signal.

**Schema (v1):**

| Field | Type | Description |
|-------|------|-------------|
| `event_id` | `string` | Unique identifier for this event instance. Used for deduplication and traceability. |
| `intent` | `object` (TradeIntent) | The TradeIntent object being submitted for evaluation. Must conform to the TradeIntent schema defined in `types.md`. |
| `timestamp` | `string` (ISO8601) | UTC timestamp when the strategy generated this intent. Format: `YYYY-MM-DDTHH:mm:ssZ` (e.g., "2024-01-01T12:00:00Z"). |

**Notes:**
- The `intent` field contains a complete TradeIntent object with all required fields.
- Event ordering is not guaranteed; Risk Manager must handle out-of-order events if required.
- Duplicate `event_id` values should be ignored (idempotent processing).

---

## RiskDecisionEvent

**Emitted by:** Risk Manager  
**Consumed by:** Execution Engine

**Description:**  
Indicates whether a TradeIntent is approved or rejected. This event triggers execution for approved intents.

**Schema (v1):**

| Field | Type | Description |
|-------|------|-------------|
| `event_id` | `string` | Unique identifier for this event instance. Links back to the original TradeIntentEvent. |
| `decision` | `object` (RiskDecision) | The RiskDecision object containing the evaluation result. Must conform to the RiskDecision schema defined in `types.md`. |
| `timestamp` | `string` (ISO8601) | UTC timestamp when the risk evaluation was completed. Format: `YYYY-MM-DDTHH:mm:ssZ` (e.g., "2024-01-01T12:00:00Z"). |

**Notes:**
- The `decision` field contains a complete RiskDecision object with all required fields.
- Only approved intents (decision.approved == true) should trigger execution.
- Rejected intents are logged but do not proceed to execution.

---

## OrderExecutedEvent

**Emitted by:** Execution Engine  
**Consumed by:** Persistence, Frontend

**Description:**  
Signals that an order has been successfully executed or failed. This event triggers persistence and frontend updates.

**Schema (v1):**

| Field | Type | Description |
|-------|------|-------------|
| `event_id` | `string` | Unique identifier for this event instance. Used for deduplication and traceability. |
| `fill` | `object` (Fill) | The Fill object containing execution details. Must conform to the Fill schema defined in `types.md`. Only present if execution succeeded. |
| `order_id` | `string` | Internal order identifier. Links back to the original TradeIntent. Always present, even if execution failed. |
| `status` | `enum: "executed" \| "failed" \| "cancelled"` | Execution status. "executed" means the order was filled. "failed" means execution could not be completed. "cancelled" means the order was cancelled before execution. |
| `error_message` | `string \| null` | If status is "failed", this field contains the error message. Otherwise, this field is null. |
| `timestamp` | `string` (ISO8601) | UTC timestamp when the execution completed or failed. Format: `YYYY-MM-DDTHH:mm:ssZ` (e.g., "2024-01-01T12:00:00Z"). |

**Notes:**
- If `status` is "executed", the `fill` field must be present and contain valid Fill data.
- If `status` is "failed" or "cancelled", the `fill` field may be null or omitted.
- Partial fills may result in multiple OrderExecutedEvent instances with the same `order_id` but different `fill` objects.

---

## Event Transport Notes

- Events are transported via Redis Streams in the implementation.
- Stream keys follow the pattern: `{component}:{event_type}` (e.g., `strategy:trade_intent`, `risk:decision`).
- Consumer groups ensure at-least-once delivery semantics.
- Event ordering within a stream is preserved, but cross-stream ordering is not guaranteed.
