"""The environment's own login (T-053, ADR-043): mind-N holds the password.

On a server with a login plugin (mc.example.net in the T-052 run) the body cannot
act before `/login`. The password is the stand's secret — compose injects one
slot's value (`MC_LOGIN_PASSWORD_N` → `MC_LOGIN_PASSWORD`) into that mind alone:
the agent never sees it, and every chat line that could echo it is masked before
it reaches a log, an event, `observe()` or `chat.line` (`SessionLogin.mask`,
wired into `Executor.chat_entry`).

The commands ride the body's own chat lever directly — MessageSender auto-detects
the leading `/` and sends them as commands, never as chat (unionclef
MessageSender.java:104-116, verified live on the stand: the `/me` effect
broadcast while no raw slash line appeared in the server log). The MCP deny list
does not apply here: it guards the agent's tool, and the login is the
environment's move.
"""

from __future__ import annotations

import re
import time
from typing import Callable

PASSWORD_MASK = "***"
_LOGIN_PROMPT = re.compile(r"/login", re.IGNORECASE)
_REGISTER_PROMPT = re.compile(r"/register", re.IGNORECASE)


class SessionLogin:
    """Sends `/login` on world entry and answers a server that asks to register;
    re-logins on a repeated prompt behind a cooldown. `send` is the body's chat
    lever; nothing here logs — the password exists only inside the sent commands.

    `skin_command` (T-061) is the environment's own move on servers that manage
    skins by command (the external stand server): one configured command sent
    right after each world-entry `/login`, by the same lever and under the same
    password masking. Empty (the default) sends nothing. It is not a secret and
    holds no password — `mask` would cut any echo that ever contained one."""

    def __init__(
        self,
        password: str,
        send: Callable[[str], None],
        *,
        clock: Callable[[], float] = time.monotonic,
        retry_s: float = 30.0,
        skin_command: str = "",
    ):
        self._password = str(password or "")
        self._send = send
        self._clock = clock
        self._retry_s = retry_s
        self._skin_command = str(skin_command or "").strip()
        self._was_in_game = False
        self._last_login_at = -1e9
        self._registered = False

    @property
    def armed(self) -> bool:
        return bool(self._password)

    def on_tick(self, in_game: bool, chat_lines: list[str]) -> None:
        """One C1 tick: feed the world state and the fresh server lines."""
        if not self._password:
            return
        if in_game and not self._was_in_game:  # a join or a rejoin after a kick
            self._send(f"/login {self._password}")
            if self._skin_command:
                self._send(self._skin_command)
            self._last_login_at = self._clock()
        self._was_in_game = in_game
        if not in_game:
            return
        now = self._clock()
        for line in chat_lines:
            if not self._registered and _REGISTER_PROMPT.search(line):
                # authme-style: the server asks to register first
                self._send(f"/register {self._password} {self._password}")
                self._registered = True
                self._last_login_at = now
                break
            if _LOGIN_PROMPT.search(line) and now - self._last_login_at >= self._retry_s:
                self._send(f"/login {self._password}")
                self._last_login_at = now
                break

    def mask(self, text: str) -> str:
        """The password can never survive into a log, an event or observe(): every
        line that might echo it (the server's own echo of what we sent) is cut."""
        if self._password and self._password in text:
            return text.replace(self._password, PASSWORD_MASK)
        return text
