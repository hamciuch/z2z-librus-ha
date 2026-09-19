"""Replying to (and sending) Librus messages from Home Assistant.

Everything here is opt-in (option "replies_enabled") because it creates real
messages addressed to teachers. Safety rules:

* the recipient of a reply is resolved from the sender's name against Librus'
  own recipient list and the message is NOT sent when that is not unique,
* the POST is never retried automatically (see LibrusClient.send_message),
* identical messages are refused for a while, and sends are rate limited,
* the message text is never logged.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
import unicodedata
from typing import Any, Callable

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from .api import LibrusAuthError
from .const import CONF_REPLIES_ENABLED, DEFAULT_REPLIES_ENABLED, DOMAIN

_LOGGER = logging.getLogger(__name__)

PLACEHOLDER = "— wybierz odbiorcę —"
REPLY_TARGETS = 5  # newest messages offered as "reply" targets
MAX_TITLE = 100
MAX_CONTENT = 4000
DUPLICATE_WINDOW_SECONDS = 120
MIN_INTERVAL_SECONDS = 10

STATUS_IDLE = "brak"
STATUS_SENDING = "wysyłanie"
STATUS_SENT = "wysłano"
STATUS_FAILED = "błąd"
STATUS_UNKNOWN = "niepewne"
_STATUS_BY_RESULT = {"sent": STATUS_SENT, "failed": STATUS_FAILED, "unknown": STATUS_UNKNOWN}

SERVICE_REPLY = "reply_message"
SERVICE_SEND = "send_message"


class ReplyError(Exception):
    """A problem with the request itself (nothing was sent)."""


# --------------------------------------------------------------------------- draft
class ReplyDraft:
    """What the dashboard entities edit + the result of the last send."""

    def __init__(self) -> None:
        self.target: str = PLACEHOLDER
        self.title: str = ""
        self.content: str = ""
        self.status: str = STATUS_IDLE
        self.detail: dict[str, Any] = {}
        self.last_key: tuple[str, str, str] | None = None
        self.last_sent_at: float = 0.0
        self._listeners: list[Callable[[], None]] = []

    def add_listener(self, callback: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(callback)

        def remove() -> None:
            if callback in self._listeners:
                self._listeners.remove(callback)

        return remove

    def notify(self) -> None:
        for callback in list(self._listeners):
            try:
                callback()
            except Exception:  # never let a broken entity block the others
                _LOGGER.exception("Reply draft listener failed")

    def set_status(self, status: str, **detail: Any) -> None:
        self.status = status
        self.detail = detail
        self.notify()

    def reset_input(self) -> None:
        self.target = PLACEHOLDER
        self.title = ""
        self.content = ""
        self.notify()


# ------------------------------------------------------------------ name matching
def _tokens(text: Any) -> tuple[frozenset[str], frozenset[str]]:
    """(name words, role words) - 'Nowak Jan (Nauczyciel)' -> ({nowak, jan}, {nauczyciel})."""
    text = unicodedata.normalize("NFKC", str(text or "")).casefold().replace("\xa0", " ")
    roles = re.findall(r"[(\[]([^)\]]*)[)\]]", text)
    core = re.sub(r"[(\[][^)\]]*[)\]]", " ", text)
    name = frozenset(re.findall(r"[^\W\d_]+", core))
    role = frozenset(w for part in roles for w in re.findall(r"[^\W\d_]+", part))
    return name, role


def match_recipients(name: str, recipients: list[dict[str, str]]) -> list[dict[str, str]]:
    """Recipients matching a person's name (word order and role suffix ignored).

    Returns every candidate; the caller must refuse to send unless exactly one.
    """
    wanted, wanted_role = _tokens(name)
    if not wanted:
        return []

    parsed = [(r, *_tokens(r.get("name"))) for r in recipients]
    found = [r for r, words, _ in parsed if words == wanted]
    if not found and len(wanted) >= 2:
        # e.g. Librus omits a second given name on one side
        found = [
            r for r, words, _ in parsed
            if len(words) >= 2 and (wanted <= words or words <= wanted)
        ]
    if len(found) > 1 and wanted_role:
        narrowed = [r for r in found if wanted_role & _tokens(r.get("name"))[1]]
        if narrowed:
            found = narrowed
    return found


def _describe(candidates: list[dict[str, str]]) -> str:
    return "; ".join(c.get("name", "?") for c in candidates[:5])


# ---------------------------------------------------------------------- targets
def _short(text: Any, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def build_targets(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Option label -> target, in display order (placeholder first)."""
    targets: dict[str, dict[str, Any]] = {PLACEHOLDER: {"kind": "none"}}

    def add(label: str, target: dict[str, Any]) -> None:
        unique, n = label, 2
        while unique in targets:
            unique = f"{label} #{n}"
            n += 1
        targets[unique] = target

    for msg in (data.get("messages") or [])[:REPLY_TARGETS]:
        if not msg.get("href"):
            continue
        date_part = _short(msg.get("date"), 10)
        label = f"↩ Odpowiedz: {_short(msg.get('author'), 40)} – {_short(msg.get('title'), 50)}"
        if date_part:
            label += f" ({date_part})"
        add(label, {"kind": "reply", "message": msg})

    recipients = sorted(
        data.get("recipients") or [], key=lambda r: str(r.get("name", "")).casefold()
    )
    for rec in recipients:
        add(f"✉ {_short(rec.get('name'), 80)}", {"kind": "recipient", "recipient": rec})
    return targets


def default_reply_title(title: Any) -> str:
    base = re.sub(r"^\s*(?:re\s*:\s*)+", "", str(title or ""), flags=re.IGNORECASE).strip()
    return _short(f"RE: {base}" if base else "RE:", MAX_TITLE)


def resolve_sender(data: dict[str, Any], message: dict[str, Any]) -> dict[str, str]:
    """The recipient entry that corresponds to the sender of a received message."""
    recipients = data.get("recipients") or []
    if not recipients:
        raise ReplyError(
            "Lista odbiorców z Librusa jest niedostępna - nie mogę ustalić adresata "
            "odpowiedzi. Spróbuj za chwilę lub wybierz odbiorcę z listy."
        )
    author = message.get("author") or ""
    candidates = match_recipients(author, recipients)
    if not candidates:
        raise ReplyError(
            "Nie znalazłem nadawcy wiadomości na liście odbiorców Librusa, więc nic "
            "nie wysłałem. Wybierz odbiorcę ręcznie."
        )
    if len(candidates) > 1:
        raise ReplyError(
            "Nie mogę jednoznacznie ustalić adresata odpowiedzi (pasuje: "
            f"{_describe(candidates)}), więc nic nie wysłałem. Wybierz odbiorcę ręcznie."
        )
    return candidates[0]


# ------------------------------------------------------------------- delivery
def _one_line(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _body(text: Any) -> str:
    return str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()


def replies_enabled(coordinator) -> bool:
    return bool(coordinator.entry.options.get(CONF_REPLIES_ENABLED, DEFAULT_REPLIES_ENABLED))


async def async_deliver(
    coordinator, recipient: dict[str, str], title: str, content: str
) -> dict[str, Any]:
    """Validate, guard against duplicates and send. Returns the result dict."""
    title = _one_line(title)
    content = _body(content)
    if not title:
        raise ReplyError("Podaj temat wiadomości.")
    if len(title) > MAX_TITLE:
        raise ReplyError(f"Temat jest za długi (max {MAX_TITLE} znaków).")
    if not content:
        raise ReplyError("Treść wiadomości jest pusta.")
    if len(content) > MAX_CONTENT:
        raise ReplyError(f"Treść jest za długa (max {MAX_CONTENT} znaków).")

    draft: ReplyDraft = coordinator.draft
    async with coordinator.send_lock:
        now = time.monotonic()
        since = now - draft.last_sent_at
        if draft.last_sent_at and since < MIN_INTERVAL_SECONDS:
            raise ReplyError("Wiadomość została wysłana przed chwilą - odczekaj kilka sekund.")
        key = (str(recipient["id"]), title, content)
        if key == draft.last_key and since < DUPLICATE_WINDOW_SECONDS:
            raise ReplyError(
                "Taka sama wiadomość została wysłana do tej osoby przed chwilą - "
                "pomijam, żeby nie wysłać jej dwa razy."
            )

        draft.set_status(STATUS_SENDING, recipient=recipient.get("name"), title=title)
        try:
            result = await coordinator.client.send_message(str(recipient["id"]), title, content)
        except LibrusAuthError as err:
            result = {"status": "failed", "message": str(err), "verified": None}
        except Exception as err:  # noqa: BLE001
            _LOGGER.exception("Unexpected error while sending a Librus message")
            result = {
                "status": "unknown",
                "message": f"Nieoczekiwany błąd ({err}). Sprawdź folder Wysłane w Librusie.",
                "verified": None,
            }

        status = result.get("status", "unknown")
        if status in ("sent", "unknown"):
            draft.last_key = key
            draft.last_sent_at = time.monotonic()

        _LOGGER.info(
            "Librus message to recipient %s: %s", recipient["id"], status
        )  # never log the text
        draft.set_status(
            _STATUS_BY_RESULT.get(status, STATUS_UNKNOWN),
            recipient=recipient.get("name"),
            title=title,
            message=result.get("message"),
            verified=result.get("verified"),
            time=time.strftime("%Y-%m-%d %H:%M:%S"),
        )

    return {
        "status": status,
        "message": result.get("message"),
        "verified": result.get("verified"),
        "recipient": recipient.get("name"),
        "title": title,
    }


async def async_send_to_target(
    coordinator, target: dict[str, Any], title: str, content: str
) -> dict[str, Any]:
    kind = target.get("kind")
    if kind == "reply":
        message = target["message"]
        recipient = resolve_sender(coordinator.data, message)
        title = _one_line(title) or default_reply_title(message.get("title"))
    elif kind == "recipient":
        recipient = target["recipient"]
    else:
        raise ReplyError("Wybierz odbiorcę (lub wiadomość, na którą odpowiadasz).")
    return await async_deliver(coordinator, recipient, title, content)


async def async_send_from_draft(coordinator) -> dict[str, Any]:
    """Button handler: send what the dashboard entities currently hold."""
    draft: ReplyDraft = coordinator.draft
    if not replies_enabled(coordinator):
        raise ReplyError("Odpowiadanie jest wyłączone w ustawieniach integracji.")
    target = build_targets(coordinator.data).get(draft.target)
    if target is None:
        target = {"kind": "none"}
    result = await async_send_to_target(coordinator, target, draft.title, draft.content)
    if result["status"] in ("sent", "unknown"):
        draft.reset_input()
    return result


# -------------------------------------------------------------------- services
_ENTRY_KEY = "config_entry"
REPLY_SCHEMA = vol.Schema(
    {
        vol.Optional("message_id"): vol.All(vol.Coerce(str), vol.Length(min=1)),
        vol.Required("content"): vol.All(vol.Coerce(str), vol.Length(min=1)),
        vol.Optional("title"): vol.Coerce(str),
        vol.Optional(_ENTRY_KEY): vol.Coerce(str),
    }
)
SEND_SCHEMA = vol.Schema(
    {
        vol.Required("recipient"): vol.All(vol.Coerce(str), vol.Length(min=1)),
        vol.Required("title"): vol.All(vol.Coerce(str), vol.Length(min=1)),
        vol.Required("content"): vol.All(vol.Coerce(str), vol.Length(min=1)),
        vol.Optional(_ENTRY_KEY): vol.Coerce(str),
    }
)


def _coordinator(hass: HomeAssistant, entry_id: str | None):
    coordinators = hass.data.get(DOMAIN, {})
    if entry_id:
        if entry_id not in coordinators:
            raise ServiceValidationError("Nie znaleziono wskazanej integracji Z2Z Librus.")
        coordinator = coordinators[entry_id]
    elif len(coordinators) == 1:
        coordinator = next(iter(coordinators.values()))
    else:
        raise ServiceValidationError(
            "Skonfigurowano więcej niż jedno konto Librus - podaj pole config_entry."
        )
    if not replies_enabled(coordinator):
        raise ServiceValidationError(
            "Odpowiadanie jest wyłączone. Włącz je w ustawieniach integracji Z2Z Librus."
        )
    return coordinator


def _raise_if_failed(result: dict[str, Any]) -> dict[str, Any]:
    if result["status"] == "failed":
        raise HomeAssistantError(f"Librus: {result.get('message') or 'nie udało się wysłać wiadomości'}")
    return result


async def _handle_reply(hass: HomeAssistant, call: ServiceCall) -> dict[str, Any]:
    coordinator = _coordinator(hass, call.data.get(_ENTRY_KEY))
    messages = coordinator.data.get("messages") or []
    wanted = call.data.get("message_id")
    message = None
    if wanted:
        message = next((m for m in messages if str(m.get("href")) == str(wanted)), None)
        if message is None:
            raise ServiceValidationError(
                "Nie ma takiej wiadomości wśród ostatnio pobranych - podaj message_id "
                "z sensora „Wiadomość 1/2/3” albo pomiń pole (odpowiedź na najnowszą)."
            )
    elif messages:
        message = messages[0]
    if message is None:
        raise ServiceValidationError("Brak wiadomości, na którą można odpowiedzieć.")
    try:
        result = await async_send_to_target(
            coordinator, {"kind": "reply", "message": message},
            call.data.get("title", ""), call.data["content"],
        )
    except ReplyError as err:
        coordinator.draft.set_status(STATUS_FAILED, message=str(err))
        raise ServiceValidationError(str(err)) from err
    return _raise_if_failed(result)


async def _handle_send(hass: HomeAssistant, call: ServiceCall) -> dict[str, Any]:
    coordinator = _coordinator(hass, call.data.get(_ENTRY_KEY))
    candidates = match_recipients(call.data["recipient"], coordinator.data.get("recipients") or [])
    try:
        if not candidates:
            raise ReplyError(
                "Nie znalazłem takiego odbiorcy na liście Librusa (podaj imię i nazwisko)."
            )
        if len(candidates) > 1:
            raise ReplyError(
                f"Pasuje kilka osób: {_describe(candidates)}. Podaj pełne imię i nazwisko."
            )
        result = await async_send_to_target(
            coordinator, {"kind": "recipient", "recipient": candidates[0]},
            call.data["title"], call.data["content"],
        )
    except ReplyError as err:
        coordinator.draft.set_status(STATUS_FAILED, message=str(err))
        raise ServiceValidationError(str(err)) from err
    return _raise_if_failed(result)


def async_register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_REPLY):
        return

    async def reply(call: ServiceCall):
        return await _handle_reply(hass, call)

    async def send(call: ServiceCall):
        return await _handle_send(hass, call)

    hass.services.async_register(
        DOMAIN, SERVICE_REPLY, reply, schema=REPLY_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SEND, send, schema=SEND_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )


def async_unregister_services(hass: HomeAssistant) -> None:
    for name in (SERVICE_REPLY, SERVICE_SEND):
        if hass.services.has_service(DOMAIN, name):
            hass.services.async_remove(DOMAIN, name)
