"""Read complete SSE data events across transport chunk boundaries."""

from collections.abc import Iterator

from httpx2 import Response


def sse_data(response: Response) -> Iterator[str]:
    lines: list[str] = []
    for line in response.iter_lines():
        if line.startswith("data:"):
            lines.append(line[5:].removeprefix(" "))
        elif not line and lines:
            yield "\n".join(lines)
            lines.clear()
    if lines:
        raise ValueError("Truncated upstream event")
