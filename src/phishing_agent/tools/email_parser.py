"""Parse untrusted RFC 822/MIME bytes without fetching or executing content."""
import hashlib
import html
import re
from email import policy
from email.parser import BytesParser
from html.parser import HTMLParser
from pathlib import Path

URL = re.compile(r"(?i)\b(?:https?://|hxxps?://)[^\s<>\"']+")
HTML_URL_ATTRIBUTE = re.compile(r'''(?i)(?<![\w:-])(href|src)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))''')


class _Links(HTMLParser):
    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.links = []
        self.attribute_spans = []
        self.line_starts = [0]
        for match in re.finditer("\n", source):
            self.line_starts.append(match.end())

    def handle_starttag(self, tag, attrs):
        line, column = self.getpos()
        tag_start = self.line_starts[line - 1] + column
        for match in HTML_URL_ATTRIBUTE.finditer(self.get_starttag_text() or ""):
            key = match.group(1).lower()
            group = next(i for i in (2, 3, 4) if match.group(i) is not None)
            raw_value = match.group(group)
            start = tag_start + match.start(group)
            end = tag_start + match.end(group)
            self.attribute_spans.append((start, end))
            if raw_value:
                self.links.append((tag, key, html.unescape(raw_value), raw_value, start, end))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)


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
        parser = None
        if content_type == "text/html":
            parser = _Links(value)
            try:
                parser.feed(value)
            except (ValueError, TypeError) as exc:
                warnings.append(f"part_{index}_html_error:{type(exc).__name__}")
        for match in URL.finditer(value):
            if parser and any(start <= match.start() < end for start, end in parser.attribute_spans):
                continue
            source_text = match.group().rstrip(".,;!?)")
            urls.append({"raw": html.unescape(source_text) if parser else source_text,
                         "source_text": source_text, "location":
                         {"part": index, "field": "body", "start": match.start(),
                          "end": match.start() + len(source_text)}})
        if parser:
            for tag, attribute, target, source_text, start, end in parser.links:
                urls.append({"raw": target, "source_text": source_text,
                             "location": {"part": index, "field": f"html_{tag}_{attribute}",
                                          "start": start, "end": end}})
    return {"source": source_name, "sha256": hashlib.sha256(raw).hexdigest(),
            "from": headers["from"], "reply_to": headers["reply_to"],
            "subject": headers["subject"], "body": "\n".join(b["text"] for b in bodies),
            "body_parts": bodies, "urls": urls, "attachments": attachments,
            "warnings": warnings}
