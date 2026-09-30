import json
import os
import time
from collections.abc import Callable

from noteezer.links import TLS_CONTEXT
from noteezer.sorting import FILE_AT, FolderOption, NewFolder, Note, RulesSorter, Suggestion

DEFAULT_MODEL = "gemini-3.5-flash-lite"
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

A wrong folder is worse than no folder: the user checks Unsorted and moves things by hand.
Do not pick a folder only because it is the closest one. When the note is about one specific
project or topic and only a broad folder like Projects fits, leave folder_id null and propose
that topic as new_folder inside the broad folder.

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
    },
    "required": ["folder_id", "confidence", "new_folder"],
}

Generate = Callable[[str, str], str]


class GeminiSorter:
    """Sorts with Gemini when the rules are not sure.

    `generate(system, prompt)` returns the model's JSON text. The real one is
    built by `from_env`; tests pass a fake so they never call the API.
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
        return cls(_client_call(model, os.environ["GEMINI_API_KEY"].strip()))

    def suggest(self, note: Note, folders: list[FolderOption]) -> Suggestion:
        rules = self._rules.suggest(note, folders)
        if rules.confidence >= FILE_AT:
            return rules  # a clear #tag or folder name needs no API call
        self._wait_turn()
        return parse_answer(self._generate(SYSTEM, build_prompt(note, folders)), folders)

    def _wait_turn(self) -> None:
        if self._last_call is not None:
            wait = self._last_call + self._min_interval - self._clock()
            if wait > 0:
                self._sleep(wait)
        self._last_call = self._clock()


def build_prompt(note: Note, folders: list[FolderOption]) -> str:
    lines = ["Folders:"]
    lines += [f"{folder.id}: {folder.path}" for folder in folders]
    lines += ["", "Note:", note.body]
    for link in note.links:
        about = f"{link.title} - {link.description}" if link.description else link.title
        lines.append(f"Link ({link.site}): {about}")
    return "\n".join(lines)


def parse_answer(raw: str, folders: list[FolderOption]) -> Suggestion:
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("the model did not answer with an object")
    known = {folder.id: folder for folder in folders}
    folder_id = data.get("folder_id")
    if not isinstance(folder_id, int) or folder_id not in known:
        folder_id = None
    try:
        confidence = min(max(float(data.get("confidence") or 0), 0.0), 1.0)
    except (TypeError, ValueError):
        confidence = 0.0
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
    return Suggestion(folder_id, confidence, proposal, engine="gemini")


def _client_call(model: str, api_key: str) -> Generate:
    from google import genai
    from google.genai import types

    client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(timeout=TIMEOUT_MS, client_args={"verify": TLS_CONTEXT}),
    )

    def generate(system: str, prompt: str) -> str:
        response = client.models.generate_content(
            model=model,
            contents=prompt,
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
