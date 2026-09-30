from typing import Any

import httpx


class TossApiError(Exception):
    """토스 API 에러 응답 {"error": {requestId, code, message, data}} 를 담는다."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        request_id: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.request_id = request_id
        self.data = data
        super().__init__(f"[{status_code} {code}] {message} (requestId={request_id})")

    @classmethod
    def from_response(cls, response: httpx.Response) -> "TossApiError":
        header_request_id = response.headers.get("X-Request-Id")
        try:
            error = response.json()["error"]
            return cls(
                status_code=response.status_code,
                code=str(error.get("code", "unknown")),
                message=str(error.get("message", "")),
                request_id=error.get("requestId") or header_request_id,
                data=error.get("data"),
            )
        except (ValueError, KeyError, TypeError, AttributeError):
            return cls(
                status_code=response.status_code,
                code="unknown",
                message=response.reason_phrase,
                request_id=header_request_id,
            )


class TossAuthError(Exception):
    """POST /oauth2/token 실패. 응답은 OAuth2 표준 형식 {error, error_description} 이다."""

    def __init__(self, status_code: int, error: str, description: str | None) -> None:
        self.status_code = status_code
        self.error = error
        self.description = description
        super().__init__(f"[{status_code} {error}] {description}")

    @classmethod
    def from_response(cls, response: httpx.Response) -> "TossAuthError":
        try:
            body = response.json()
            return cls(response.status_code, str(body["error"]), body.get("error_description"))
        except (ValueError, KeyError, TypeError):
            return cls(response.status_code, "unknown", response.reason_phrase)
