"""Client-protocol presenters for the shared Responses transport."""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Protocol

from free_claude_code.core.failures import ExecutionFailure
from free_claude_code.core.history_replay import (
    ReplayOrigin,
    ReplayRecord,
    encode_replay,
    is_replay,
)
from free_claude_code.core.json_types import JsonObject
from free_claude_code.core.openai_responses import (
    NativeResponsesRelay,
    ResponsesProviderStream,
    ResponsesStreamFailure,
    ResponsesToolEventAdapter,
    format_response_sse_event,
)


class ResponsesStreamPresenter(Protocol):
    """One client-protocol view over an upstream Responses attempt."""

    @property
    def completed(self) -> bool: ...

    @property
    def terminal_failure_completes_wire(self) -> bool: ...

    def start(self) -> Iterable[str]: ...

    def feed(self, event_type: str, payload: JsonObject) -> Iterable[str]: ...

    def terminal_failure(
        self,
        raw_error: Exception,
        failure: ExecutionFailure,
    ) -> Iterable[str]: ...

    def failure_payload(
        self, raw_error: Exception, failure: ExecutionFailure
    ) -> JsonObject | None: ...


class MessagesResponsesPresenter:
    """Translate one Responses attempt into Anthropic Messages SSE."""

    def __init__(self, stream: ResponsesProviderStream) -> None:
        self._stream = stream
        self._replay_origin: ReplayOrigin | None = None

    def set_replay_origin(self, origin: ReplayOrigin) -> None:
        self._replay_origin = origin

    @property
    def completed(self) -> bool:
        return self._stream.completed

    @property
    def terminal_failure_completes_wire(self) -> bool:
        return False

    def start(self) -> Iterable[str]:
        return self._stream.start()

    def feed(
        self,
        event_type: str,
        payload: JsonObject,
        *,
        native_payload: JsonObject | None = None,
    ) -> Iterable[str]:
        item = payload.get("item")
        native_item = (native_payload if native_payload is not None else payload).get(
            "item"
        )
        if (
            event_type == "response.output_item.done"
            and self._replay_origin is not None
            and isinstance(item, dict)
            and isinstance(native_item, dict)
            and native_item.get("type") == "reasoning"
            and isinstance(native_item.get("id"), str)
            and native_item["id"]
            and isinstance(native_item.get("encrypted_content"), str)
            and native_item["encrypted_content"]
            and not is_replay(native_item["encrypted_content"])
        ):
            payload = {
                **payload,
                "item": {
                    **item,
                    "encrypted_content": encode_replay(
                        ReplayRecord(self._replay_origin, native_item)
                    ),
                },
            }
        return self._stream.feed(event_type, payload)

    def terminal_failure(
        self,
        raw_error: Exception,
        failure: ExecutionFailure,
    ) -> Iterable[str]:
        del raw_error, failure
        return self._stream.ledger.close_unclosed_blocks()

    def failure_payload(self, raw_error: Exception, failure: ExecutionFailure) -> None:
        return None


class NativeResponsesPresenter:
    """Relay one Responses attempt as native Responses SSE."""

    def __init__(
        self, *, public_model: str, tool_events: ResponsesToolEventAdapter | None = None
    ) -> None:
        self._relay = NativeResponsesRelay(public_model=public_model)
        self._tool_events = tool_events

    @property
    def completed(self) -> bool:
        return self._relay.completed

    @property
    def terminal_failure_completes_wire(self) -> bool:
        return True

    def start(self) -> Iterable[str]:
        return ()

    def feed(self, event_type: str, payload: JsonObject) -> Iterable[str]:
        if self._tool_events is not None:
            return tuple(
                self._relay.feed(kind, value)
                for kind, value in self._tool_events.feed(event_type, payload)
            )
        return (self._relay.feed(event_type, payload),)

    def terminal_failure(
        self,
        raw_error: Exception,
        failure: ExecutionFailure,
    ) -> Iterable[str]:
        return (
            format_response_sse_event(
                "response.failed", self.failure_payload(raw_error, failure)
            ),
        )

    def failure_payload(
        self, raw_error: Exception, failure: ExecutionFailure
    ) -> JsonObject:
        if (
            isinstance(raw_error, ResponsesStreamFailure)
            and raw_error.event_type == "response.failed"
            and raw_error.payload is not None
        ):
            payload = raw_error.payload
            if self._tool_events is not None:
                ((_, payload),) = self._tool_events.feed("response.failed", payload)
            return self._relay.project("response.failed", payload)
        return self._relay.failure_payload(failure)


@dataclass(slots=True)
class ResponsesExecutionOutcome:
    """Provider outcome retained when terminal failure is consumed on-wire."""

    failure: Exception | None = None


type ResponsesPresenterFactory = Callable[[bool], ResponsesStreamPresenter]
