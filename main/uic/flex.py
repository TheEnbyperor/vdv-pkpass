import typing
import dataclasses
import pathlib
import datetime
import asn1tools
import pytz
from . import util

ROOT = pathlib.Path(__file__).parent
ASN1_SPEC_V1_3 = asn1tools.compile_files([ROOT / "asn1" / "uicRailTicketData_v1.3.4.asn"], codec="uper")
ASN1_SPEC_V2 = asn1tools.compile_files([ROOT / "asn1" / "uicRailTicketData_v2.0.2.asn"], codec="uper")
ASN1_SPEC_V3 = asn1tools.compile_files([ROOT / "asn1" / "uicRailTicketData_v3.0.3.asn"], codec="uper")

@dataclasses.dataclass
class Flex:
    version: int
    data: typing.Dict[str, typing.Any]

    @classmethod
    def parse(cls, version: int, data: bytes) -> "Flex":
        try:
            if version == 13:
                return cls(
                    version=version,
                    data=ASN1_SPEC_V1_3.decode("UicRailTicketData", data)
                )
            elif version == 2:
                return cls(
                    version=version,
                    data=ASN1_SPEC_V2.decode("UicRailTicketData", data)
                )
            elif version == 3:
                return cls(
                    version=version,
                    data=ASN1_SPEC_V3.decode("UicRailTicketData", data)
                )
            else:
                raise util.UICException("Unsupported UIC rail ticket flexible data version")
        except asn1tools.DecodeError as e:
            raise util.UICException("Failed to decode UIC rail ticket flexible data") from e

    def issuing_rics(self) -> int:
        if self.version in (13, 2, 3):
            rics = self.data["issuingDetail"].get("issuerNum", 0)
            if rics:
                return rics
            else:
                return self.data["issuingDetail"].get("securityProviderNum", 0)

    def ticket_id(self) -> str:
        if self.version in (13, 2, 3):
            return self.data["issuingDetail"].get("issuerPNR", "")

    def issuing_time(self) -> typing.Optional[datetime.datetime]:
        if self.version in (13, 2, 3):
            date = datetime.datetime(self.data["issuingDetail"]["issuingYear"], 1, 1)
            date += datetime.timedelta(days=self.data["issuingDetail"]["issuingDay"] - 1)
            if "issuingTime" in self.data["issuingDetail"]:
                date += datetime.timedelta(minutes=self.data["issuingDetail"]["issuingTime"])
            return pytz.utc.localize(date)

    def specimen(self) -> bool:
        if self.version in (13, 2, 3):
            return self.data["issuingDetail"]["specimen"]