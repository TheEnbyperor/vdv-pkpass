import dataclasses
import typing
import datetime

import pytz


class CDException(Exception):
    pass

@dataclasses.dataclass
class CDRecordUT:
    name: typing.Optional[str]
    validity_start: typing.Optional[datetime.datetime]
    validity_end: typing.Optional[datetime.datetime]
    other_blocks: typing.Dict[str, str]

    @classmethod
    def parse(cls, data: bytes, version: int):
        if version != 1:
            raise CDException(f"Unsupported record version {version}")

        tz = pytz.timezone("Europe/Prague")

        name = None
        validity_start = None
        validity_end = None
        blocks = {}

        offset = 0
        while data[offset:]:
            try:
                block_id = data[offset:offset + 2].decode("utf-8")
            except UnicodeDecodeError as e:
                raise CDException(f"Invalid CD UT record") from e
            try:
                block_len = int(data[offset + 2:offset + 5].decode("utf-8"), 10)
            except (ValueError, UnicodeDecodeError) as e:
                raise CDException(f"Invalid CD UT record") from e
            try:
                block_data = data[offset + 5:offset + 5 + block_len].decode("utf-8")
            except UnicodeDecodeError as e:
                raise CDException(f"Invalid CD UT record") from e
            offset += 5 + block_len

            if block_id == "KJ":
                name = block_data
            elif block_id == "OD":
                try:
                    validity_start = tz.localize(
                        datetime.datetime.strptime(block_data, "%d.%m.%Y %H:%M")
                    ).astimezone(pytz.utc)
                except ValueError as e:
                    raise CDException(f"Invalid validity start date") from e
            elif block_id == "DO":
                try:
                    validity_end = tz.localize(
                        datetime.datetime.strptime(block_data, "%d.%m.%Y %H:%M")
                    ).astimezone(pytz.utc)
                except ValueError as e:
                    raise CDException(f"Invalid validity end date") from e
            else:
                blocks[block_id] = block_data

        return cls(
            name=name,
            validity_start=validity_start,
            validity_end=validity_end,
            other_blocks=blocks,
        )