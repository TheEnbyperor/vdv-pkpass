import dataclasses
import typing

from . import util

class LayoutV1FieldFormatting:
    def __init__(self, formatting: int):
        self.formatting = formatting

    @property
    def bold(self) -> bool:
        return bool(self.formatting & 1)

    @property
    def italic(self) -> bool:
        return bool(self.formatting & 2)

    @property
    def small_font(self) -> bool:
        return bool(self.formatting & 4)

    def __str__(self):
        return f"LayoutV1FieldFormatting(bold={self.bold}, italic={self.italic}, small_font={self.small_font})"

    def __repr__(self):
        return str(self)


@dataclasses.dataclass
class LayoutV1Field:
    line: int
    column: int
    height: int
    width: int
    formatting: LayoutV1FieldFormatting
    text: str

@dataclasses.dataclass
class LayoutV1:
    standard: str
    fields: typing.List[LayoutV1Field]

    @classmethod
    def parse(cls, data: bytes) -> "LayoutV1":
        if len(data) < 8:
            raise util.UICException("UIC ticket layout too short")

        try:
            standard = data[0:4].decode("ascii")
        except UnicodeDecodeError as e:
            raise util.UICException("Invalid UIC ticket layout standard") from e

        try:
            field_count_str = data[4:8].decode("ascii")
            field_count = int(field_count_str, 10)
        except (UnicodeDecodeError, ValueError) as e:
            raise util.UICException("Invalid UIC ticket layout field count") from e

        fields = []
        offset = 8
        for _ in range(field_count):
            if len(data) < offset + 13:
                raise util.UICException("UIC ticket layout field too short")

            try:
                field_line_str = data[offset:offset + 2].decode("ascii")
                field_line = int(field_line_str, 10)
                field_column_str = data[offset + 2:offset + 4].decode("ascii")
                field_column = int(field_column_str, 10)
                field_height_str = data[offset + 4:offset + 6].decode("ascii")
                field_height = int(field_height_str, 10)
                field_width_str = data[offset + 6:offset + 8].decode("ascii")
                field_width = int(field_width_str, 10)
            except (UnicodeDecodeError, ValueError) as e:
                raise util.UICException("Invalid UIC ticket layout field position") from e

            if field_line > 14:
                raise util.UICException("UIC ticket layout field line out of bounds")
            if field_column > 71:
                raise util.UICException("UIC ticket layout field column out of bounds")
            if field_height + field_line > 14:
                raise util.UICException("UIC ticket layout field height out of bounds")
            if field_width + field_column > 71:
                raise util.UICException("UIC ticket layout field width out of bounds")

            try:
                field_formatting_str = data[offset + 8:offset + 9].decode("ascii")
                field_formatting = LayoutV1FieldFormatting(int(field_formatting_str, 10))
            except (UnicodeDecodeError, ValueError) as e:
                raise util.UICException("Invalid UIC ticket layout field formatting") from e

            try:
                field_text_length_str = data[offset + 9:offset + 13].decode("ascii")
                field_text_length = int(field_text_length_str, 10)
            except (UnicodeDecodeError, ValueError) as e:
                raise util.UICException("Invalid UIC ticket layout field text length") from e

            if len(data) < offset + 13 + field_text_length:
                raise util.UICException("UIC ticket layout field text too short")

            try:
                field_text = data[offset + 13:offset + 13 + field_text_length].decode("utf-8")
            except UnicodeDecodeError as e:
                raise util.UICException("Invalid UIC ticket layout field text") from e

            offset += 13 + field_text_length

            fields.append(LayoutV1Field(
                line=field_line,
                column=field_column,
                height=field_height,
                width=field_width,
                formatting=field_formatting,
                text=field_text
            ))

        return cls(
            standard=standard,
            fields=fields
        )