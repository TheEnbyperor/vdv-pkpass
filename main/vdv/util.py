import dataclasses
import datetime
import pytz

TAG_OCTET_STRING = 0x04
TAG_NULL = 0x05
TAG_OID = 0x06
TAG_SEQUENCE = 0x30
TAG_SIGNATURE = 0x9E
REMAINING_DATA = 0x9A
TAG_CA_REFERENCE = 0x42
TAG_TICKET_PRODUCT_DATA = 0x85
TAG_TICKET_PRODUCT_TRANSACTION_DATA = 0x8A
TAG_CERTIFICATE = 0x7F21
TAG_CERTIFICATE_SIGNATURE = 0x5F37
TAG_CERTIFICATE_SIGNATURE_REMAINDER = 0x5F38
TAG_CERTIFICATE_CONTENT = 0x5F4E

VDV_TZ = pytz.timezone("Europe/Berlin")


class VDVException(Exception):
    pass


@dataclasses.dataclass
class Date:
    year: int
    month: int
    day: int

    def __str__(self):
        return f"{self.year:04d}-{self.month:02d}-{self.day:02d}"

    def as_date(self):
        return datetime.date(self.year, self.month, self.day)

    @classmethod
    def from_bytes(cls, data: bytes) -> "Date":
        if len(data) != 4:
            raise ValueError("Invalid date length")

        return cls(
            year=un_bcd(data[0:2]),
            month=un_bcd(data[2:3]),
            day=un_bcd(data[3:4])
        )


@dataclasses.dataclass
class DateTime:
    year: int
    month: int
    day: int
    hour: int
    minute: int
    second: int

    def __str__(self):
        return f"{self.year:04d}-{self.month:02d}-{self.day:02d} {self.hour:02d}:{self.minute:02d}:{self.second:02d}"

    def as_datetime(self):
        return VDV_TZ.localize(datetime.datetime(self.year, self.month, self.day, self.hour, self.minute, self.second))

    @classmethod
    def from_bytes(cls, data: bytes) -> "DateTime":
        if len(data) != 4:
            raise ValueError("Invalid date time length")

        year = data[0] >> 1
        month = ((data[0] & 0x01) << 3) | ((data[1] & 0xE0) >> 5)
        day = data[1] & 0x1F
        hour = (data[2] & 0xF8) >> 3
        minute = ((data[2] & 0x07) << 3) | ((data[3] & 0xE0) >> 5)
        second = data[3] & 0x1F

        return cls(
            year=year + 1990,
            month=month,
            day=day,
            hour=hour,
            minute=minute,
            second=second
        )

    def to_bytes(self) -> bytes:
        return bytes([
            ((self.year - 1990) << 1) | ((self.month >> 3) & 0x01),
            ((self.month << 5) & 0xE0) | self.day & 0x1F,
            ((self.hour & 0xF8) << 3)| ((self.minute >> 3) & 0x07),
            ((self.minute << 5) & 0xE0) | self.second & 0x1F
        ])

def un_bcd(data: bytes) -> int:
    v = 0
    for i in range(len(data)):
        v *= 100
        v += ((data[i] & 0xF0) >> 4) * 10 + (data[i] & 0x0F)
    return v