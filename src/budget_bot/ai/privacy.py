"""Conservative redaction of explicit identifiers, not arbitrary financial numbers."""
import re

_REPLACEMENT = '[redacted]'
_PATTERNS = [
    r'(?i)\bmy name is\s+[^\d,;.!?\n]+',
    # Explicit labels cover short secrets and numeric identifiers without treating
    # every large monetary amount as an account number.
    r'(?i)\b(?:api[_ -]?key|password|passwd|secret|token|authorization|telegram[_ -]?id|'
    r'chat[_ -]?id|user[_ -]?id|account[_ -]?(?:number|no))'
    r'\s*[:=]?\s*(?:Bearer\s+)?(?:«[^»]*»|"[^"]*"|\'[^\']*\'|\+?\d[\d ()-]{7,}\d|[^\s,;]+)',
    r'(?i)\b(?:phone|mobile)\s*[:=]?\s*(?!\d{4}-\d{2}-\d{2}\b)'
    r'\+?\d[\d ()-]{7,}\d(?![\d.,])',
    r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b',
    r'(?<!\w)@[A-Za-z][A-Za-z0-9_]{2,}\b',
    r'(?i)\b(?:sk|sess)-[A-Za-z0-9_-]+',
    r'\b\d{6,12}:[A-Za-z0-9_-]{20,}\b',
    r'(?i)\bBearer\s+[^\s,;]+',
    r'(?i)\b(?:https?://|www\.)[^\s]+',
    r'(?<![\w.])\+\d[\d ()-]{7,}\d\b',
    r'(?<![\w.,])\d{3}[- ]\d{3}[- ]\d{4}(?![\w.,])',
    r'(?<![\w.,])\d{5}[- ]\d{5}(?![\w.,])',
    r'(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b',
]


def redact(text: str, secrets: tuple[str, ...] = ()) -> str:
    """Redact known credentials first, then explicitly identifiable contact data.

    Unlabelled names and digit-only numbers cannot reliably be distinguished from
    bucket descriptions and money. Never send identity metadata to this function.
    """
    for secret in secrets:
        if secret:
            text = text.replace(secret, _REPLACEMENT)
    for pattern in _PATTERNS:
        text = re.sub(pattern, _REPLACEMENT, text)
    return text
