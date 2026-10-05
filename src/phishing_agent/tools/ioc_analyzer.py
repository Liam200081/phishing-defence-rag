"""Offline URL/domain normalization and structural observations only."""
import ipaddress
import re
from urllib.parse import urlsplit, urlunsplit


def _normalize(raw):
    value = raw.strip().replace("[.]", ".")
    value = re.sub(r"^hxxp", "http", value, flags=re.I)
    if "://" not in value:
        value = "//" + value
    parsed = urlsplit(value)
    host = parsed.hostname
    if not host:
        raise ValueError("no_host")
    host = host.rstrip(".").encode("idna").decode("ascii").lower()
    port = parsed.port
    scheme = parsed.scheme.lower()
    if parsed.scheme:
        netloc = host if port is None or (scheme, port) in (("http", 80), ("https", 443)) else f"{host}:{port}"
        normalized = urlunsplit((scheme, netloc, parsed.path or "/", parsed.query, ""))
        kind = "url"
    else:
        normalized, kind = host, "domain"
    observations = []
    try:
        ipaddress.ip_address(host)
        observations.append("ip_literal_host")
    except ValueError:
        pass
    if parsed.username or parsed.password:
        observations.append("userinfo_in_url")
    if "xn--" in host:
        observations.append("punycode_label")
    if scheme == "http":
        observations.append("http_scheme")
    return kind, normalized, host, observations


def analyze_iocs(items):
    """Each item is {'raw': value, 'location': source location}; repeats retain all locations."""
    if not isinstance(items, (list, tuple)):
        raise TypeError("items must be a list")
    grouped, warnings = {}, []
    for index, item in enumerate(items):
        raw = item.get("raw") if isinstance(item, dict) else None
        if not isinstance(raw, str) or not raw.strip():
            warnings.append({"index": index, "reason": "empty_or_invalid"})
            continue
        try:
            kind, normalized, host, observations = _normalize(raw)
        except (ValueError, UnicodeError) as exc:
            warnings.append({"index": index, "raw": raw, "reason": str(exc)})
            continue
        key = (kind, normalized)
        if key not in grouped:
            grouped[key] = {"kind": kind, "normalized": normalized, "host": host,
                            "observations": observations, "occurrences": []}
        grouped[key]["occurrences"].append({"raw": raw, "location": item.get("location"),
                                             "input_index": index})
    return {"iocs": list(grouped.values()), "warnings": warnings,
            "analysis": "offline_static_only; observations are not verdicts"}
