import dataclasses
import enum
import typing
import ber_tlv.tlv
import re
from . import util, org_id

NAME_TYPE_1_RE = re.compile(r"(?P<start>\w?)(?P<len>\d+)(?P<end>\w?)")


@dataclasses.dataclass
class Context:
    account_forename: typing.Optional[str]
    account_surname: typing.Optional[str]


@dataclasses.dataclass
class VDVTicket:
    version: str
    ticket_id: int
    ticket_org_id: int
    product_number: int
    product_org_id: int
    validity_start: util.DateTime
    validity_end: util.DateTime
    kvp_org_id: int
    terminal_type: int
    terminal_number: int
    terminal_owner_id: int
    transaction_time: util.DateTime
    location_type: int
    location_number: int
    location_org_id: int
    sam_sequence_number_1: int
    sam_sequence_number_2: int
    sam_version: int
    sam_id: int
    product_data: typing.List
    product_transaction_data: typing.List

    def __str__(self):
        out = "VDVTicket:\n" \
              f"""  Version: {self.version}\n""" \
              f"""  Ticket:\n""" \
              f"""    ID: {self.ticket_id}\n""" \
              f"""    Organization ID: {self.ticket_org_id}\n""" \
              f"""  Product:\n""" \
              f"""    Number: {self.product_number}\n""" \
              f"""    Organization ID: {self.product_org_id}\n""" \
              f"""  Validity:\n""" \
              f"""   Start: {self.validity_start}\n""" \
              f"""   End: {self.validity_end}\n""" \
              f"""  Transaction:\n""" \
              f"""    Time: {self.transaction_time}\n""" \
              f"""    KVP Organization ID: {self.kvp_org_id}\n""" \
              f"""    Terminal:\n""" \
              f"""      Type: {self.terminal_type}\n""" \
              f"""      Number: {self.terminal_number}\n""" \
              f"""      Owner ID: {self.terminal_owner_id}\n""" \
              f"""    Location:\n""" \
              f"""      Type: {self.location_type}\n""" \
              f"""      Number: {self.location_number}\n""" \
              f"""      Organization ID: {self.location_org_id}\n""" \
              f"""  SAM:\n""" \
              f"""    Sequence Number 1: {self.sam_sequence_number_1}\n""" \
              f"""    Sequence Number 2: {self.sam_sequence_number_2}\n""" \
              f"""    Version: {self.sam_version}\n""" \
              f"""    ID: {self.sam_id}\n""" \
              f"""  Product Data:"""
        if not self.product_data:
            out += " N/A"
        else:
            for d in self.product_data:
                out += f"""\n    {d}"""
        out += "\n  Product Transaction Data:"
        if not self.product_transaction_data:
            out += " N/A"
        else:
            for d in self.product_transaction_data:
                out += f"""\n    {d}"""
        return out

    @classmethod
    def parse(cls, data: bytes, context: Context) -> "VDVTicket":
        if len(data) < 111:
            raise util.VDVException("Invalid VDV ticket length")

        header, data = data[0:18], data[18:]

        try:
            parser = ber_tlv.tlv.Tlv.Parser(data, [], 0)
            product_data = parser.next()
        except Exception as e:
            raise util.VDVException("Invalid VDV ticket") from e

        if product_data[0] != 0x85:
            raise util.VDVException("Not a VDV ticket")

        product_data = ber_tlv.tlv.Tlv.parse(product_data[1], False, False)

        offset_1 = parser.get_offset()
        common_transaction_data, data = data[offset_1:offset_1 + 17], data[offset_1 + 17:]

        try:
            parser = ber_tlv.tlv.Tlv.Parser(data, [], 0)
            product_transaction_data = parser.next()
        except Exception as e:
            raise util.VDVException("Invalid VDV ticket") from e

        if product_transaction_data[0] != 0x8A:
            raise util.VDVException("Not a VDV ticket")

        product_transaction_data = ber_tlv.tlv.Tlv.parse(product_transaction_data[1], False, False)

        offset_2 = parser.get_offset()
        ticket_issue_data, data = data[offset_2:offset_2 + 12], data[offset_2 + 12:]

        trailer = data[-5:]

        if trailer[0:3] != b'VDV':
            raise util.VDVException("Not a VDV ticket")

        version = f"{trailer[3] >> 4}.{trailer[3] & 0x0F}.{trailer[4]:02d}"

        return cls(
            version=version,
            ticket_id=int.from_bytes(header[0:4], 'big'),
            ticket_org_id=int.from_bytes(header[4:6], 'big'),
            product_number=int.from_bytes(header[6:8], 'big'),
            product_org_id=int.from_bytes(header[8:10], 'big'),
            validity_start=util.DateTime.from_bytes(header[10:14]),
            validity_end=util.DateTime.from_bytes(header[14:18]),
            kvp_org_id=int.from_bytes(common_transaction_data[0:2], 'big'),
            terminal_type=common_transaction_data[2],
            terminal_number=int.from_bytes(common_transaction_data[3:5], 'big'),
            terminal_owner_id=int.from_bytes(common_transaction_data[5:7], 'big'),
            transaction_time=util.DateTime.from_bytes(common_transaction_data[7:11]),
            location_type=common_transaction_data[11],
            location_number=int.from_bytes(common_transaction_data[12:15], 'big'),
            location_org_id=int.from_bytes(common_transaction_data[15:17], 'big'),
            sam_sequence_number_1=int.from_bytes(ticket_issue_data[0:4], 'big'),
            sam_version=ticket_issue_data[4],
            sam_sequence_number_2=int.from_bytes(ticket_issue_data[5:9], 'big'),
            sam_id=int.from_bytes(ticket_issue_data[9:12], 'big'),
            product_data=list(map(lambda e: cls.parse_product_data_element(e, context), product_data)),
            product_transaction_data=product_transaction_data
        )

    @staticmethod
    def parse_product_data_element(elm, context: Context) -> typing.Any:
        if elm[0] == 0xDB:
            return PassengerData.parse(elm[1], context)
        elif elm[0] == 0xDC:
            return SpacialValidity.parse(elm[1])
        else:
            return UnknownElement(elm[0], elm[1])

    def product_name(self, opt=False):
        if self.product_number == 9999:
            return "Deutschlandticket"
        elif self.product_number == 9998:
            return "Deutschlandjobticket"
        elif self.product_number == 9997:
            return "Startkarte Deutschlandticket"
        elif self.product_number == 9996:
            return "Semesterticket Deutschlandticket Upgrade"
        elif self.product_number == 9995:
            return "Deutschlandsemesterticket"
        else:
            if opt:
                return None
            return f"{self.product_org_name()}:{self.product_number}"

    def product_name_opt(self):
        return self.product_name(True)

    def product_org_name(self):
        return map_org_id(self.product_org_id)

    def product_org_name_opt(self):
        return map_org_id(self.product_org_id, True)

    def ticket_org_name(self):
        return map_org_id(self.ticket_org_id)

    def ticket_org_name_opt(self):
        return map_org_id(self.ticket_org_id, True)

    def kvp_org_name(self):
        return map_org_id(self.kvp_org_id)

    def kvp_org_name_opt(self):
        return map_org_id(self.kvp_org_id, True)

    def terminal_owner_name(self):
        return map_org_id(self.terminal_owner_id)

    def terminal_owner_name_opt(self):
        return map_org_id(self.terminal_owner_id, True)

    def location_org_name(self):
        return map_org_id(self.location_org_id)

    def location_org_name_opt(self):
        return map_org_id(self.location_org_id, True)


class Gender(enum.Enum):
    Unspecified = 0
    Male = 1
    Female = 2
    Diverse = 3


@dataclasses.dataclass
class PassengerData:
    TYPE = "passenger-data"

    gender: Gender
    date_of_birth: util.Date
    forename: str
    original_forename: typing.Optional[str]
    surname: str
    original_surname: typing.Optional[str]

    def __str__(self):
        return f"Passenger: forename={self.forename}, surname={self.surname}, date_of_birth={self.date_of_birth}, gender={self.gender}"

    @classmethod
    def parse(cls, data: bytes, context: Context) -> "PassengerData":
        if len(data) < 5:
            raise util.VDVException("Invalid passenger data element")

        name = data[5:].decode("iso-8859-1", "replace")
        forename = ""
        original_forename = None
        original_surname = None
        if "#" in name:
            forename, surname = name.split("#", 1)
            if context.account_forename and context.account_forename.startswith(forename):
                forename = context.account_forename
            if context.account_surname and context.account_surname.startswith(surname):
                surname = context.account_surname
        elif "@" in name:
            forename, surname = name.split("@", 1)
            new_forename = []
            new_surname = []
            while forename_match := NAME_TYPE_1_RE.match(forename):
                forename = forename[forename_match.end():]
                forename_start = forename_match.group("start")
                forename_end = forename_match.group("end")
                forename_len = int(forename_match.group("len"))
                new_forename.append(f"{forename_start}{'_' * forename_len}{forename_end}")

            while surname_match := NAME_TYPE_1_RE.match(surname):
                surname = surname[surname_match.end():]
                surname_start = surname_match.group("start")
                surname_end = surname_match.group("end")
                surname_len = int(surname_match.group("len"))
                new_surname.append(f"{surname_start}{'_' * surname_len}{surname_end}")

            if new_forename:
                forename = " ".join(new_forename)
                if context.account_forename and len(context.account_forename) == len(forename):
                    if (
                            context.account_forename.startswith(forename[0]) and
                            context.account_forename.endswith(forename[-1])
                    ) or (
                            (context.account_forename.startswith(forename[0]) or
                             context.account_forename.endswith(forename[-1])) and
                            len(forename) == 2
                    ) or (
                            len(forename) == 1
                    ):
                        original_forename = forename
                        forename = context.account_forename

            if new_surname:
                surname = " ".join(new_surname)
                if context.account_surname and len(context.account_surname) == len(surname):
                    if (
                            context.account_surname.startswith(surname[0]) and
                            context.account_surname.endswith(surname[-1])
                    ) or (
                            (context.account_surname.startswith(surname[0]) or
                             context.account_surname.endswith(surname[-1])) and
                            len(surname) == 2
                    ) or (
                            len(surname) == 1
                    ):
                        original_surname = surname
                        surname = context.account_surname
        else:
            surname = name

        return cls(
            gender=Gender(data[0]),
            date_of_birth=util.Date.from_bytes(data[1:5]),
            forename=forename,
            surname=surname,
            original_forename=original_forename,
            original_surname=original_surname
        )


@dataclasses.dataclass
class SpacialValidity:
    TYPE = "spacial-validity"

    definition_type: int
    organization_id: int
    area_ids: typing.List[int]

    def __str__(self):
        return f"Spacial validity: org_id={self.organization_id}, area_ids={','.join(map(str, self.area_ids))}"

    @classmethod
    def parse(cls, data: bytes):
        if data[0] == 0x0F:
            return cls(
                definition_type=data[0],
                organization_id=int.from_bytes(data[1:3], 'big'),
                area_ids=[int.from_bytes(data[i:i + 2], 'big') for i in range(3, len(data), 2)]
            )
        else:
            return UnknownSpacialValidity(
                definition_type=data[0],
                value=data[1:]
            )

    def organization_name(self):
        return map_org_id(self.organization_id)

    def organization_name_opt(self):
        return map_org_id(self.organization_id, True)


@dataclasses.dataclass
class UnknownSpacialValidity:
    TYPE = "unknown-spacial-validity"

    definition_type: int
    value: bytes

    def __str__(self):
        return f"Unknown spacial validity: type=0x{self.definition_type:02X}, value={self.value.hex()}"

    def type_hex(self):
        return f"0x{self.definition_type:02X}"

    def data_hex(self):
        return ":".join(f"{self.value[i]:02x}" for i in range(len(self.value)))


@dataclasses.dataclass
class UnknownElement:
    TYPE = "unknown"

    tag: int
    value: bytes

    def __str__(self):
        return f"Unknown element 0x{self.tag:02X}: {self.value.hex()}"

    def tag_hex(self):
        return f"0x{self.tag:02X}"

    def data_hex(self):
        return ":".join(f"{self.value[i]:02x}" for i in range(len(self.value)))


def map_org_id(code: int, opt=False):
    org, is_test = org_id.get_org(code)
    if org:
        if is_test:
            return f"{org['name']} (Test)"
        else:
            return org['name']
    if opt:
        return ""
    else:
        return str(org_id)
