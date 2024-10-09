import dataclasses
import typing

from . import util, rics

class Flags:
    def __init__(self, flags: int):
        self.flags = flags

    @property
    def international_ticket(self) -> bool:
        return bool(self.flags & 1)

    @property
    def edited_by_agent(self) -> bool:
        return bool(self.flags & 2)

    @property
    def specimen(self) -> bool:
        return bool(self.flags & 4)

    def __str__(self):
        return f"Flags(international_ticket={self.international_ticket}, edited_by_agent={self.edited_by_agent}, specimen={self.specimen})"

    def __repr__(self):
        return str(self)

@dataclasses.dataclass
class HeadV1:
    distributing_rics: int
    ticket_id: str
    issuing_time: util.Timestamp
    flags: Flags
    language: str
    second_language: typing.Optional[str]

    def distributor(self):
        return rics.get_rics(self.distributing_rics)

    @classmethod
    def parse(cls, data: bytes):
        if len(data) != 41:
            raise util.UICException("UIC ticket head has the wrong length")

        try:
            distributing_rics_str = data[0:4].decode("ascii")
            distributing_rics = int(distributing_rics_str, 10)
        except (UnicodeDecodeError, ValueError) as e:
            raise util.UICException("Invalid UIC ticket distributing RICS") from e

        try:
            ticket_id = data[4:24].rstrip(b"\x00").decode("ascii")
        except UnicodeDecodeError as e:
            raise util.UICException("Invalid UIC ticket ID") from e

        issuing_time = util.Timestamp.from_bytes(data[24:36])

        try:
            flags_str = data[36:37].decode("ascii")
            flags = Flags(int(flags_str, 10))
        except (UnicodeDecodeError, ValueError) as e:
            raise util.UICException("Invalid UIC ticket flags") from e

        try:
            language = data[37:39].decode("ascii")
            if data[39:41] == b"\x00\x00":
                second_language = None
            else:
                second_language = data[39:41].decode("ascii").strip()
        except UnicodeDecodeError as e:
            raise util.UICException("Invalid UIC ticket language") from e

        return cls(
            distributing_rics=distributing_rics,
            ticket_id=ticket_id,
            issuing_time=issuing_time,
            flags=flags,
            language=language,
            second_language=second_language
       )