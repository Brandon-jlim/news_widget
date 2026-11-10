"""Conservative mixed-script checks, shared with the browser through runtime config."""
import json
from pathlib import Path
import re
import unicodedata

POLICY = json.loads((Path(__file__).resolve().parents[1] / "translation-policy.json").read_text(encoding="utf-8"))
HAN_RUN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\U00020000-\U0002fa1f\U00030000-\U000323af]+")
MIXED_BASE = re.compile(r"[A-Za-z0-9\uac00-\ud7a3]")


def inspect_translation(source: str, translated: str, target: str = "KO") -> tuple[str, str]:
    """Return a usable title or an issue code. Never guess unknown company names."""
    if target.upper() != "KO":
        return translated, ""
    source = unicodedata.normalize("NFC", source)
    text = unicodedata.normalize("NFC", translated)
    for repair in POLICY["nameRepairs"]:
        if re.search(r"(?<!\w)" + re.escape(repair["source"]) + r"(?!\w)", source, re.IGNORECASE):
            text = text.replace(repair["invalid"], repair["replacement"])
    allowed = set(POLICY["allowedHan"])
    for token in re.findall(r"[^\W_]+", text):
        if not MIXED_BASE.search(token):
            continue
        for run in HAN_RUN.findall(token):
            if not all(char in allowed for char in run) and run not in source:
                return "", "mixed_script"
    return text, ""


def review_record() -> dict:
    # Remember rejection without storing the bad title or repeatedly billing retries.
    return {"status": "review", "reason": "mixed_script", "validationVersion": POLICY["version"]}
