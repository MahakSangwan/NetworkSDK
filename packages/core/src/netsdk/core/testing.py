"""Test helpers for netsdk packages: a scripted fake CLI for SSH transports.

Shipped in core so every device package can unit-test its transport and
getters without hardware.
"""

from collections import deque
from collections.abc import Callable, Iterable
from types import SimpleNamespace


class FakeConnection:
    """Records every write and replies with scripted chunks.

    `responder(data)` returns the chunks the "device" sends back after
    `data` is written; `initial` is what arrives right after login.
    """

    def __init__(
        self,
        responder: Callable[[str], Iterable[str]] | None = None,
        initial: Iterable[str] = (),
    ) -> None:
        self.writes: list[str] = []
        self.disconnected = False
        self.remote_conn = SimpleNamespace(closed=False)
        self._responder = responder or (lambda _data: ())
        self._pending: deque[str] = deque(initial)

    def write_channel(self, out_data: str) -> None:
        if self.remote_conn.closed:
            raise OSError("Socket is closed")  # what paramiko raises
        self.writes.append(out_data)
        self._pending.extend(self._responder(out_data))

    def read_channel(self) -> str:
        return self._pending.popleft() if self._pending else ""

    def disconnect(self) -> None:
        self.disconnected = True
        self.remote_conn.closed = True


class FakeCLI:
    """Minimal CLI: answers commands from a table, pages long outputs.

    `outputs` maps a command to a list of pages. Multi-page outputs end each
    page but the last with `pager`; the next page starts with `erase`.
    """

    def __init__(
        self,
        outputs: dict[str, list[str]],
        *,
        hostname: str = "R1",
        terminator: str = ">",
        pager: str = " --More-- ",
        erase: str = "\b" * 10 + " " * 10 + "\b" * 10,
    ) -> None:
        self.outputs = outputs
        self.prompt = f"{hostname}{terminator}"
        self.pager = pager
        self.erase = erase
        self._pages: deque[str] = deque()
        self.conn: FakeConnection | None = None

    def __call__(self, data: str) -> list[str]:
        if data == " " and self._pages:
            return [self.erase, *self._next_page()]
        if data == "\n":
            return ["\r\n" + self.prompt]
        command = data.rstrip("\n")
        if command == "exit":
            if self.conn is not None:
                self.conn.remote_conn.closed = True
            return ["exit\r\n"]
        pages = self.outputs.get(command)
        if pages is None:
            return [f"{command}\r\n", "% Invalid input detected at '^' marker.\r\n", self.prompt]
        self._pages = deque(pages)
        return [f"{command}\r\n", *self._next_page()]

    def _next_page(self) -> list[str]:
        page = self._pages.popleft()
        if self._pages:
            return [page, self.pager]
        return [page, self.prompt]

    def connection(self, initial: Iterable[str] | None = None) -> FakeConnection:
        conn = FakeConnection(
            self, ["Banner text\r\n", self.prompt] if initial is None else initial
        )
        self.conn = conn
        return conn
