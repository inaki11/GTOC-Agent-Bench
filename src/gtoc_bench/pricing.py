"""Dated, offline token pricing; no provider calls or subscription billing claims."""

import hashlib
import math
from datetime import date
from pathlib import Path

import yaml


def load_prices(path):
    raw = Path(path).read_bytes()
    data = yaml.safe_load(raw)
    if (
        not isinstance(data, dict)
        or data.get("schema_version") != 1
        or data.get("currency") != "USD"
    ):
        raise ValueError("Prices require schema_version: 1 and currency: USD")
    if not isinstance(data.get("as_of"), str) or not isinstance(data.get("models"), dict):
        raise ValueError("Prices require an as_of date string and a models mapping")
    as_of = date.fromisoformat(data["as_of"])
    order = data.get("model_order", [])
    if (
        not isinstance(order, list)
        or any(not isinstance(m, str) for m in order)
        or len(set(order)) != len(order)
    ):
        raise ValueError("model_order must be a list of distinct model identifiers")
    for model, rates in data["models"].items():
        if not isinstance(rates, dict):
            raise ValueError(f"Invalid price mapping for {model}")
        for field in ("input", "cached_input", "output", "cache_write", "cache_write_long"):
            if field not in rates and field not in {"input", "output", "cached_input"}:
                continue
            value = rates.get(field)
            if type(value) not in {int, float} or not math.isfinite(value) or value < 0:
                raise ValueError(f"Invalid {field} price for {model}")
        if not isinstance(rates.get("source"), str) or not rates["source"].startswith("https://"):
            raise ValueError(f"A price source is required for {model}")
        if rates.get("valid_through") and as_of > date.fromisoformat(rates["valid_through"]):
            raise ValueError(f"Price template date exceeds the declared price validity for {model}")
        long = rates.get("long_context")
        if long and any(
            type(long.get(k)) not in {int, float} or not math.isfinite(long[k]) or long[k] <= 0
            for k in ("threshold", "input_multiplier", "output_multiplier")
        ):
            raise ValueError(f"Invalid long-context pricing for {model}")
    for alias, target in data.get("aliases", {}).items():
        if target not in data["models"]:
            raise ValueError(f"Unknown price alias target: {alias}")
    return data | {"sha256": hashlib.sha256(raw).hexdigest()}


def counts(usage):
    """Input includes cache reads/writes; reasoning is already in output."""
    incoming, cached, outgoing = (
        usage.get(k) or 0 for k in ("input_tokens", "cached_input_tokens", "output_tokens")
    )
    if any(type(v) is not int or v < 0 for v in (incoming, cached, outgoing)):
        raise ValueError("Invalid token counts")
    raw = usage.get("raw") or {}
    anthropic = usage.get("source") == "official_claude_cli" or any(
        key in raw for key in ("cache_read_input_tokens", "cache_creation_input_tokens")
    )
    if anthropic:
        ttl = raw.get("cache_creation") or {}
        writes = raw.get("cache_creation_input_tokens", 0) or 0
        long_write = ttl.get("ephemeral_1h_input_tokens", 0) or 0
        vector = {
            "input": raw.get("input_tokens", 0) or 0,
            "cached_input": raw.get("cache_read_input_tokens", 0) or 0,
            "cache_write": writes - long_write,
            "cache_write_long": long_write,
            "output": raw.get("output_tokens", 0) or 0,
        }
    else:
        details = raw.get("input_tokens_details") or {}
        writes = details.get("cache_write_tokens", 0) or 0
        vector = {
            "input": incoming - cached - writes,
            "cached_input": cached,
            "cache_write": writes,
            "cache_write_long": 0,
            "output": outgoing,
        }
    if any(type(v) is not int or v < 0 for v in vector.values()):
        raise ValueError("Inconsistent token/cache counts")
    return vector


class PriceLedger:
    def __init__(self, catalog):
        self.catalog = catalog
        self.requests = {}
        self.native_totals = {}
        self.notes = set()

    def charge(self, model, request_id, usage, metadata=None):
        """Cost of this usage delta, avoiding repeated native message observations."""
        metadata = metadata or {}
        if usage.get("quality") == "not_billed":
            return 0.0
        canonical = self.catalog.get("aliases", {}).get(model, model)
        if canonical != model:
            self.notes.add(f"Alias de precio explícito: {model} → {canonical}")
        rate = self.catalog["models"].get(canonical)
        if rate is None:
            self.notes.add(f"Sin tarifa para {model}")
            return None
        if usage.get("upstream_verified") is False:
            self.notes.add("Consumo reportado por el puente; sin verificación upstream")
        incoming = usage.get("input_tokens") or 0
        outgoing = usage.get("output_tokens") or 0
        if not incoming and not outgoing:
            return None if usage.get("quality") in {"unknown", "partial"} else 0.0
        vector = counts(usage)
        context_input = sum(value for key, value in vector.items() if key != "output")
        native = usage.get("source") == "official_claude_cli"
        if native:
            totals = self.native_totals.setdefault(model, dict.fromkeys(vector, 0))
            if metadata.get("cumulative_reconciliation"):
                # Final CLI totals omit TTL details. Reconcile aggregate writes,
                # rather than billing every previous write again as a default TTL.
                old = totals.copy()
                old["cache_write"] += old.pop("cache_write_long")
                old["cache_write_long"] = 0
            else:
                old = self.requests.get((model, request_id), dict.fromkeys(vector, 0))
            current = vector
            vector = {k: max(0, v - old[k]) for k, v in current.items()}
            # NativeUsage input/output are already deduplicated deltas. In a
            # final reconciliation one category can be absent without new input.
            if incoming == 0:
                vector = {k: v if k == "output" else 0 for k, v in vector.items()}
            if outgoing == 0:
                vector["output"] = 0
            if (
                sum(v for k, v in vector.items() if k != "output") != incoming
                or vector["output"] != outgoing
            ):
                self.notes.add("Reconciliación de categorías de caché incompleta; coste parcial")
                return None
            self.requests[model, request_id] = current
            for k, v in vector.items():
                totals[k] += v
        if vector["cache_write"] and not (usage.get("raw") or {}).get("cache_creation"):
            self.notes.add("Escrituras sin TTL explícito: tarifa predeterminada de la plantilla")
        long = rate.get("long_context", {})
        if (
            native
            and metadata.get("cumulative_reconciliation")
            and long
            and context_input > long.get("threshold", float("inf"))
            and any(vector.values())
        ):
            self.notes.add(
                "Uso acumulado de Claude sin contexto por respuesta para aplicar la tarifa de contexto largo; coste parcial"
            )
            return None
        long_request = (context_input if native else incoming) > long.get("threshold", float("inf"))
        total = 0.0
        for field, value in vector.items():
            if not value:
                continue
            price = rate.get(field)
            if price is None:
                self.notes.add(f"Sin tarifa {field} para {model}")
                return None
            multiplier = (
                long.get("output_multiplier" if field == "output" else "input_multiplier", 1)
                if long_request
                else 1
            )
            total += value * price * multiplier / 1e6
        return total
