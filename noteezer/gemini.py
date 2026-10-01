import json
import os
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timezone, tzinfo

from noteezer.images import Image
from noteezer.links import TLS_CONTEXT
from noteezer.sorting import FILE_AT, FolderOption, NewFolder, Note, Part, RulesSorter, Suggestion

DEFAULT_MODEL = "gemini-3.5-flash-lite"
# Flash-Lite missed dates in screenshots about half the time in testing;
# Flash read them every time, so images go to the larger model.
DEFAULT_IMAGE_MODEL = "gemini-3.8-flash"
MIN_INTERVAL_SECONDS = 4.0  # the free tier allows about 15 requests a minute
TIMEOUT_MS = 20_000

SYSTEM = """You file one saved note into the user's folders.
The note may be English, Spanish, or both. It may be a short line, a pasted link, or a longer list.
Folders are given as `id: path`, where a path like `Projects / Game` is a folder inside another.

Answer with:
- folder_id: the id of the single best folder, or null when none fits.
- confidence: from 0 to 1, how sure you are that folder_id is right. Use 0.8 or more only when
  the note clearly belongs there. Use less when two folders could fit or you are guessing.
- new_folder: only when folder_id is null and the note starts a clear topic the folders do not
  cover. Give a short name (1 to 3 words, Title Case, in the language of the existing folder
  names) and parent_id, the id of the folder it belongs inside, or null for the top level.
  Otherwise null.
- kind: "task" for something to do, "idea" for something to try, build, or think about later,
  otherwise "note".
- remind_at: when the note asks to be reminded, or names an appointment, event, or deadline on
  a day, the local date and time as YYYY-MM-DDTHH:MM, worked out from Now. Use 09:00 when only
  a day is given. Otherwise null.
- parts: split the note when it holds two or more separate points, each on its own line or
  bullet: bugs to fix, tasks, ideas, or different topics. Each point becomes one part, so the
  user can file, finish, or delete it on its own. Keep the user's own words. When the points sit
  under a heading like "Cannon Circle:", start each part with it, for example
  "Cannon Circle: bigger cannon balls". Give each part its own kind, folder_id, and confidence.
  Use an empty list for one thought, a single sentence, or lines that only make sense together,
  like a shopping list or an address.

A wrong folder is worse than no folder: the user checks Unsorted and moves things by hand.
Do not pick a folder only because it is the closest one. When the note is about one specific
project or topic and only a broad folder like Projects fits, leave folder_id null and propose
that topic as new_folder inside the broad folder.

When an image is attached, it is the main content of the note, so read it and use it for
every answer, including remind_at for an event or deadline shown in it. Also give:
- image_title: one line under 80 characters saying what the image is, in the language of the
  note or of the image, for example "Climbing gym opening hours". Null without an image.
- image_text: the useful text in the image, like names, dates, times, prices, and links, up
  to about 500 characters. Null without an image or when it has no useful text.

Use only ids from the list. Link titles tell you what a link is about."""

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "folder_id": {"type": ["integer", "null"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "new_folder": {
            "type": ["object", "null"],
            "properties": {
                "name": {"type": "string"},
                "parent_id": {"type": ["integer", "null"]},
            },
            "required": ["name", "parent_id"],
        },
        "kind": {"type": "string", "enum": ["note", "idea", "task"]},
        "remind_at": {"type": ["string", "null"]},
        "image_title": {"type": ["string", "null"]},
        "image_text": {"type": ["string", "null"]},
        "parts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "kind": {"type": "string", "enum": ["note", "idea", "task"]},
                    "folder_id": {"type": ["integer", "null"]},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "required": ["text", "kind", "folder_id", "confidence"],
            },
        },
    },
    "required": [
        "folder_id",
        "confidence",
        "new_folder",
        "kind",
        "remind_at",
        "parts",
        "image_title",
        "image_text",
    ],
}

KINDS = ("note", "idea", "task")

Generate = Callable[[str, str, Image | None], str]


class GeminiSorter:
    """Sorts with Gemini: folder, kind, reminder time, and splitting.

    An explicit #tag or folder name found by the rules still decides the
    folder, because that is the user's own choice. `generate(system, prompt, image)`
    returns the model's JSON text. The real one is built by `from_env`; tests
    pass a fake so they never call the API.
    """

    name = "gemini"

    def __init__(
        self,
        generate: Generate,
        min_interval: float = MIN_INTERVAL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._generate = generate
        self._rules = RulesSorter()
        self._min_interval = min_interval
        self._clock = clock
        self._sleep = sleep
        self._last_call: float | None = None

    @classmethod
    def from_env(cls) -> "GeminiSorter":
        model = os.environ.get("GEMINI_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
        image_model = os.environ.get("GEMINI_IMAGE_MODEL", DEFAULT_IMAGE_MODEL).strip() or DEFAULT_IMAGE_MODEL
        return cls(_client_call(model, image_model, os.environ["GEMINI_API_KEY"].strip()))

    def suggest(self, note: Note, folders: list[FolderOption]) -> Suggestion:
        rules = self._rules.suggest(note, folders)
        self._wait_turn()
        zone = note.now.tzinfo if note.now is not None else None
        answer = parse_answer(self._generate(SYSTEM, build_prompt(note, folders), note.image), folders, zone)
        if rules.confidence >= FILE_AT:
            answer = replace(answer, folder_id=rules.folder_id, confidence=rules.confidence, new_folder=None)
        return answer

    def _wait_turn(self) -> None:
        if self._last_call is not None:
            wait = self._last_call + self._min_interval - self._clock()
            if wait > 0:
                self._sleep(wait)
        self._last_call = self._clock()


def build_prompt(note: Note, folders: list[FolderOption]) -> str:
    now = note.now or datetime.now(timezone.utc)
    lines = [f"Now: {now:%A %Y-%m-%d %H:%M} ({now.tzinfo})", "", "Folders:"]
    lines += [f"{folder.id}: {folder.path}" for folder in folders]
    lines += ["", f"Saved as: {note.kind}", "Note:", note.body]
    if note.image is not None:
        lines.append("Image: attached")
    for link in note.links:
        about = f"{link.title} - {link.description}" if link.description else link.title
        lines.append(f"Link ({link.site}): {about}")
    return "\n".join(lines)


def parse_answer(raw: str, folders: list[FolderOption], zone: tzinfo | None = None) -> Suggestion:
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("the model did not answer with an object")
    known = {folder.id: folder for folder in folders}
    folder_id = data.get("folder_id")
    if not isinstance(folder_id, int) or folder_id not in known:
        folder_id = None
    confidence = _confidence(data.get("confidence"))
    proposal = None
    new_folder = data.get("new_folder")
    if folder_id is None and isinstance(new_folder, dict):
        name = " ".join(str(new_folder.get("name") or "").split())
        parent_id = new_folder.get("parent_id")
        if not isinstance(parent_id, int) or parent_id not in known:
            parent_id = None
        if name and len(name) <= 60 and "/" not in name:
            path = f"{known[parent_id].path} / {name}" if parent_id is not None else name
            existing = next((folder for folder in folders if folder.path.casefold() == path.casefold()), None)
            if existing is not None:
                folder_id = existing.id
            else:
                proposal = NewFolder(name, parent_id)
    return Suggestion(
        folder_id,
        confidence,
        proposal,
        engine="gemini",
        kind=data.get("kind") if data.get("kind") in KINDS else None,
        remind_at=_local_time(data.get("remind_at"), zone),
        parts=_parts(data.get("parts"), known),
        image_title=_short(data.get("image_title"), 120),
        image_text=_short(data.get("image_text"), 1000),
    )


def _short(raw: object, limit: int) -> str | None:
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    return text[:limit] if text else None


def _parts(raw: object, known: dict[int, FolderOption]) -> tuple[Part, ...]:
    if not isinstance(raw, list):
        return ()
    parts = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        text = str(entry.get("text") or "").strip()
        if not text:
            continue
        folder_id = entry.get("folder_id")
        parts.append(
            Part(
                text=text,
                kind=entry.get("kind") if entry.get("kind") in KINDS else None,
                folder_id=folder_id if isinstance(folder_id, int) and folder_id in known else None,
                confidence=_confidence(entry.get("confidence")),
            )
        )
    return tuple(parts) if len(parts) >= 2 else ()


def _local_time(raw: object, zone: tzinfo | None) -> datetime | None:
    # The model answers in the user's local time without an offset, which is
    # easier for it than UTC. It is turned into UTC here.
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = datetime.fromisoformat(raw.strip())
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=zone or timezone.utc)
    return parsed.astimezone(timezone.utc)


def _confidence(raw: object) -> float:
    try:
        return min(max(float(raw or 0), 0.0), 1.0)
    except (TypeError, ValueError):
        return 0.0


def _client_call(model: str, image_model: str, api_key: str) -> Generate:
    from google import genai
    from google.genai import types

    client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(timeout=TIMEOUT_MS, client_args={"verify": TLS_CONTEXT}),
    )

    def generate(system: str, prompt: str, image: Image | None) -> str:
        contents: list = [prompt]
        if image is not None:
            contents.append(types.Part.from_bytes(data=image.data, mime_type=image.mime))
        response = client.models.generate_content(
            model=image_model if image is not None else model,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=system,
                response_mime_type="application/json",
                response_json_schema=RESPONSE_SCHEMA,
                temperature=0,
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            ),
        )
        return response.text or ""

    return generate
