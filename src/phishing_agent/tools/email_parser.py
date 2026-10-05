"""Parse untrusted RFC 822/MIME bytes without fetching or executing content."""
import hashlib
import re
from email import policy
from email.parser import BytesParser
from html.parser import HTMLParser
from pathlib import Path

URL = re.compile(r"(?i)\b(?:https?://|hxxps?://)[^\s<>\"']+")


class _Links(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []
        self.text = []

    def handle_starttag(self, tag, attrs):
        for key, value in attrs:
            if key in {"href", "src"} and value:
                self.links.append((tag, key, value))

    def handle_data(self, data):
        self.text.append(data)


def parse_email(*, text=None, path=None):
    """Provide exactly one of text (str/bytes) or a local .eml path."""
    if (text is None) == (path is None):
        raise ValueError("provide exactly one of text or path")
    if path is not None:
        source = Path(path)
        if source.suffix.lower() != ".eml":
            raise ValueError("path must name a .eml file")
        raw = source.read_bytes()
        source_name = str(source)
    else:
        raw = text.encode("utf-8") if isinstance(text, str) else text
        if not isinstance(raw, bytes):
            raise TypeError("text must be str or bytes")
        source_name = "inline"
    message = BytesParser(policy=policy.default).parsebytes(raw)
    warnings = [str(d) for d in message.defects]
    headers = {name.lower().replace("-", "_"): str(message.get(name, ""))
               for name in ("From", "Reply-To", "Subject")}
    for name in ("From", "Subject"):
        if not headers[name.lower()]:
            warnings.append(f"missing_{name.lower()}")
    bodies, urls, attachments = [], [], []
    for index, part in enumerate(message.walk()):
        if part.is_multipart():
            continue
        warnings.extend(f"part_{index}: {d}" for d in part.defects)
        content_type = part.get_content_type()
        disposition = part.get_content_disposition()
        if disposition == "attachment" or part.get_filename():
            payload = part.get_payload(decode=True)
            attachments.append({"part": index, "filename": part.get_filename(),
                                "content_type": content_type,
                                "size_bytes": len(payload) if payload is not None else None})
            continue
        if content_type not in ("text/plain", "text/html"):
            continue
        try:
            value = part.get_content()
        except (LookupError, UnicodeError, ValueError, TypeError) as exc:
            warnings.append(f"part_{index}_decode_error:{type(exc).__name__}")
            value = (part.get_payload(decode=True) or b"").decode("utf-8", errors="replace")
        bodies.append({"part": index, "content_type": content_type, "text": value})
        for match in URL.finditer(value):
            urls.append({"raw": match.group().rstrip(".,;!?)"), "location":
                         {"part": index, "field": "body", "start": match.start(),
                          "end": match.start() + len(match.group().rstrip(".,;!?)"))}})
        if content_type == "text/html":
            parser = _Links()
            try:
                parser.feed(value)
                for tag, attribute, target in parser.links:
                    start = value.find(target)
                    urls.append({"raw": target, "location": {"part": index,
                                 "field": f"html_{tag}_{attribute}", "start": start if start >= 0 else None,
                                 "end": start + len(target) if start >= 0 else None}})
            except (ValueError, TypeError) as exc:
                warnings.append(f"part_{index}_html_error:{type(exc).__name__}")
    return {"source": source_name, "sha256": hashlib.sha256(raw).hexdigest(),
            "from": headers["from"], "reply_to": headers["reply_to"],
            "subject": headers["subject"], "body": "\n".join(b["text"] for b in bodies),
            "body_parts": bodies, "urls": urls, "attachments": attachments,
            "warnings": warnings}
