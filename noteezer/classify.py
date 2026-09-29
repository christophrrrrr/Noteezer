import re
from dataclasses import dataclass

TAG_PATTERN = re.compile(r"#([A-Za-z0-9_]+)")
URL_PATTERN = re.compile(r"https?://\S+", re.IGNORECASE)
REMIND_PREFIX = re.compile(r"^remind\b", re.IGNORECASE)
TASK_PREFIX = re.compile(r"^(todo|buy)\b", re.IGNORECASE)
IDEA_PREFIX = re.compile(r"^idea\b", re.IGNORECASE)
KINDS = ("note", "idea", "task", "link", "reminder")


class ClassificationError(Exception):
    pass


@dataclass(frozen=True)
class ClassifiedLine:
    body: str
    kind: str
    tags: tuple[str, ...]


def classify(body: str, forced_kind: str | None = None) -> ClassifiedLine:
    text = body.strip()
    if not text:
        raise ClassificationError("Write something before saving.")
    if forced_kind not in (None, *KINDS):
        raise ClassificationError("Choose a valid kind.")

    tags = tuple(dict.fromkeys(match.group(1).lower() for match in TAG_PATTERN.finditer(text)))
    if forced_kind == "reminder" or (forced_kind is None and REMIND_PREFIX.search(text)):
        kind = "reminder"
    elif forced_kind in ("note", "idea", "task", "link"):
        kind = forced_kind
    elif TASK_PREFIX.search(text):
        kind = "task"
    elif IDEA_PREFIX.search(text):
        kind = "idea"
    elif URL_PATTERN.search(text):
        kind = "link"
    else:
        kind = "note"
    return ClassifiedLine(body=text, kind=kind, tags=tags)
