import base64
import dataclasses
import traceback
import typing
import datetime
import Crypto.Hash.TupleHash128

from . import models, vdv, uic


class TicketError(Exception):
    def __init__(self, title, message, exception=None):
        self.title = title
        self.message = message
        self.exception = exception


@dataclasses.dataclass
class VDVTicket:
    root_ca: vdv.CertificateData
    issuing_ca: vdv.CertificateData
    envelope_certificate: vdv.CertificateData
    raw_ticket: bytes
    ticket: vdv.VDVTicket

    @property
    def ticket_type(self) -> str:
        return "VDV"

    def type(self) -> str:
        if self.ticket.product_number in (
                9999, # Deutschlandticket subscription
                9998, # Deutschlandjobticket subscription
                9997, # Startkarte Deutschlandticket
                9996, # Semesterticket Deutschlandticket Upgrade subscription
                9995, # Semesterdeutschlandticket subscription
        ):
            return models.Ticket.TYPE_DEUTCHLANDTICKET
        else:
            return models.Ticket.TYPE_UNKNOWN


    def pk(self) -> str:
        hd = Crypto.Hash.TupleHash128.new(digest_bytes=16)

        ticket_type = self.type()
        if ticket_type == models.Ticket.TYPE_DEUTCHLANDTICKET:
            passenger_data = next(filter(lambda d: isinstance(d, vdv.ticket.PassengerData), self.ticket.product_data), None)
            if passenger_data:
                hd.update(b"deutschlandticket")
                hd.update(self.ticket.product_org_id.to_bytes(8, "big"))
                hd.update(passenger_data.forename.encode("utf-8"))
                hd.update(passenger_data.surname.encode("utf-8"))
                hd.update(str(passenger_data.date_of_birth).encode("utf-8"))
                return base64.b32hexencode(hd.digest()).decode("utf-8")

        hd.update(b"unknown-vdv")
        hd.update(self.ticket.ticket_id.to_bytes(8, "big"))
        hd.update(self.ticket.ticket_org_id.to_bytes(8, "big"))
        return base64.b32encode(hd.digest()).decode("utf-8")


@dataclasses.dataclass
class UICTicket:
    raw_bytes: bytes
    envelope: uic.Envelope
    head: uic.HeadV1
    layout: typing.Optional[uic.LayoutV1]
    flex: typing.Optional[uic.Flex]
    other_records: typing.List[uic.envelope.Record]

    @property
    def ticket_type(self) -> str:
        return "UIC"

    def type(self) -> str:
        if self.flex and self.flex.version == 3 and \
                self.flex.data["issuingDetail"].get("issuerNum") == 1080 and \
                len(self.flex.data.get("transportDocument", [])) >= 1 and \
                len(self.flex.data.get("travelerDetail", {}).get("traveler", [])) >= 1:
            ticket = self.flex.data["transportDocument"][0]["ticket"]
            if ticket[0] == "openTicket" and ticket[1]["productIdNum"] in (
                    9999, # Deutschlandticket subscription
                    9998, # Deutschlandjobticket subscription
                    9997, # Startkarte Deutschlandticket
                    9996, # Semesterticket Deutschlandticket Upgrade subscription
                    9995, # Semesterdeutschlandticket subscription
            ):
                return models.Ticket.TYPE_DEUTCHLANDTICKET
        return models.Ticket.TYPE_UNKNOWN

    def pk(self) -> str:
        hd = Crypto.Hash.TupleHash128.new(digest_bytes=16)

        if self.type() == models.Ticket.TYPE_DEUTCHLANDTICKET:
            passenger = self.flex.data.get("travelerDetail", {}).get("traveler", [{}])[0]
            dob_year = passenger.get("yearOfBirth", 0)
            dob_month = passenger.get("monthOfBirth", 0)
            dob_day = passenger.get("dayOfBirthInMonth", 0)
            hd.update(b"deutschlandticket")
            hd.update(self.flex.data["issuingDetail"]["issuerNum"].to_bytes(8, "big"))
            hd.update(passenger.get("firstName").encode("utf-8"))
            hd.update(passenger.get("lastName").encode("utf-8"))
            hd.update(f"{dob_year:04d}-{dob_month:02d}-{dob_day:02d}".encode("utf-8"))
            return base64.b32hexencode(hd.digest()).decode("utf-8")

        hd.update(b"unknown-uic")
        hd.update(self.issuing_rics().to_bytes(4, "big"))
        hd.update(self.ticket_id().encode("utf-8"))
        return base64.b32encode(hd.digest()).decode("utf-8")

    def issuing_rics(self) -> int:
        if self.head:
            return self.head.distributing_rics
        elif self.flex:
            return self.flex.issuing_rics()
        else:
            return 0

    def distributor(self):
        return uic.rics.get_rics(self.issuing_rics())

    def ticket_id(self) -> str:
        if self.head:
            return self.head.ticket_id
        elif self.flex:
            return self.flex.ticket_id()
        else:
            return ""

    def issuing_time(self) -> typing.Optional[datetime.datetime]:
        if self.head:
            return self.head.issuing_time.as_datetime()
        elif self.flex:
            return self.flex.issuing_time()
        else:
            return None

    def specimen(self) -> bool:
        if self.head:
            return self.head.flags.specimen
        elif self.flex:
            return self.flex.specimen()
        else:
            return False


def parse_ticket_vdv(ticket_bytes: bytes) -> VDVTicket:
    pki_store = vdv.CertificateStore()
    try:
        pki_store.load_certificates()
    except vdv.util.VDVException:
        raise TicketError(
            title="Internal error",
            message="The PKI certificates could not be loaded. This is almost certainly a bug.",
            exception=traceback.format_exc()
        )

    raw_root_ca = pki_store.find_certificate(vdv.CAReference.root())
    if not raw_root_ca:
        raise TicketError(
            title="Internal error",
            message="The root CA couldn't be found. This is almost certainly a bug.",
        )

    try:
        root_ca = vdv.Certificate.parse(raw_root_ca)
    except vdv.util.VDVException:
        raise TicketError(
            title="Internal error",
            message="The root CA certificate is invalid. This is almost certainly a bug.",
            exception=traceback.format_exc()
        )

    if root_ca.needs_ca_key():
        raise TicketError(
            title="Internal error",
            message="The root CA certificate is encrypted and requires a CA key. This is almost certainly a bug."
        )

    try:
        root_ca_data = vdv.CertificateData.parse(root_ca)
    except vdv.util.VDVException:
        raise TicketError(
            title="Internal error",
            message="The root CA certificate data is invalid. This is almost certainly a bug.",
            exception=traceback.format_exc()
        )

    if root_ca_data.ca_reference != vdv.CAReference.root() or \
            root_ca_data.certificate_holder_reference != vdv.CAReference.root():
        raise TicketError(
            title="Internal error",
            message="The root CA appears to not be a root. This is almost certainly a bug."
        )

    try:
        root_ca.verify_signature(root_ca_data)
    except vdv.util.VDVException:
        raise TicketError(
            title="Internal error",
            message="The root CA certificate signature is invalid. This is almost certainly a bug.",
            exception=traceback.format_exc()
        )

    try:
        envelope = vdv.EnvelopeV2.parse(ticket_bytes)
    except vdv.util.VDVException:
        raise TicketError(
            title="This doesn't look like a valid VDV ticket",
            message="You may have scanned something that is not a VDV ticket, the ticket is corrupted, or there "
                    "is a bug in this program.",
            exception=traceback.format_exc()
        )

    raw_issuing_ca = pki_store.find_certificate(envelope.ca_reference)
    if not raw_issuing_ca:
        raise TicketError(
            title="Unknown issuing certificate",
            message="The certificate that issued this ticket is not known - the ticket is likely invalid."
        )

    try:
        issuing_ca = vdv.Certificate.parse(raw_issuing_ca)
    except vdv.util.VDVException:
        raise TicketError(
            title="Invalid issuing certificate",
            message="The issuing CA can't be decoded - this is likely a bug.",
            exception=traceback.format_exc()
        )

    if issuing_ca.needs_ca_key():
        try:
            issuing_ca.decrypt_with_ca_key(root_ca_data)
        except vdv.util.VDVException:
            raise TicketError(
                title="Unable to decrypt issuing certificate",
                message="The issuing CA is encrypted and can't be decrypted - the ticket is likely invalid.",
                exception=traceback.format_exc()
            )
    else:
        try:
            issuing_ca.verify_signature(root_ca_data)
        except vdv.util.VDVException:
            raise TicketError(
                title="Invalid issuing certificate signature",
                message="The issuing CA has an invalid signature - the ticket is likely invalid.",
                exception=traceback.format_exc()
            )

    try:
        issuing_ca_data = vdv.CertificateData.parse(issuing_ca)
    except vdv.util.VDVException:
        raise TicketError(
            title="Invalid issuing certificate data",
            message="The issuing CA couldn't be decoded - this is likely a bug.",
            exception=traceback.format_exc()
        )

    if issuing_ca_data.ca_reference != root_ca_data.certificate_holder_reference:
        raise TicketError(
            title="Broken certificate chain",
            message="The issuing CA isn't issued by the root CA - the ticket is likely invalid."
        )

    if envelope.ca_reference != issuing_ca_data.certificate_holder_reference:
        raise TicketError(
            title="Broken certificate chain",
            message="The ticket certificate isn't issued by the issuing CA - the ticket is likely invalid."
        )

    if envelope.certificate.needs_ca_key():
        try:
            envelope.certificate.decrypt_with_ca_key(issuing_ca_data)
        except vdv.util.VDVException:
            raise TicketError(
                title="Unable to decrypt ticket certificate",
                message="The ticket certificate is encrypted and can't be decrypted - the ticket is likely invalid.",
                exception=traceback.format_exc()
            )
    else:
        try:
            envelope.certificate.verify_signature(issuing_ca_data)
        except vdv.util.VDVException:
            raise TicketError(
                title="Invalid ticket certificate signature",
                message="The ticket certificate has an invalid signature - the ticket is likely invalid.",
                exception=traceback.format_exc()
            )

    try:
        envelope_certificate_data = vdv.CertificateData.parse(envelope.certificate)
    except vdv.util.VDVException:
        raise TicketError(
            title="Invalid ticket certificate data",
            message="The ticket certificate couldn't be decoded - the ticket is likely invalid.",
            exception=traceback.format_exc()
        )

    try:
        ticket_data = envelope.decrypt_with_cert(envelope_certificate_data)
    except vdv.util.VDVException:
        raise TicketError(
            title="Unable to decrypt ticket",
            message="The ticket data couldn't be decrypted - the ticket is likely invalid.",
            exception=traceback.format_exc()
        )

    try:
        ticket = vdv.VDVTicket.parse(ticket_data)
    except vdv.util.VDVException:
        raise TicketError(
            title="Unable to parse ticket",
            message="The ticket data is invalid - this is likely a bug.",
            exception=traceback.format_exc()
        )

    return VDVTicket(
        root_ca=root_ca_data,
        issuing_ca=issuing_ca_data,
        envelope_certificate=envelope_certificate_data,
        raw_ticket=ticket_data,
        ticket=ticket
    )


def parse_ticket_uic_head(ticket_envelope: uic.Envelope) -> typing.Optional[uic.HeadV1]:
    head_record = next(filter(lambda r: r.id == "U_HEAD", ticket_envelope.records), None)
    if not head_record:
        return None

    if head_record.version != 1:
        raise TicketError(
            title="Unsupported header record version",
            message=f"The header record version {head_record.version} is not supported."
        )

    try:
        return uic.HeadV1.parse(head_record.data)
    except uic.util.UICException:
        raise TicketError(
            title="Invalid header record",
            message="The header record is invalid - the ticket is likely invalid.",
            exception=traceback.format_exc()
        )


def parse_ticket_uic_layout(ticket_envelope: uic.Envelope) -> typing.Optional[uic.LayoutV1]:
    layout_record = next(filter(lambda r: r.id == "U_TLAY", ticket_envelope.records), None)
    if not layout_record:
        return None

    if layout_record.version != 1:
        raise TicketError(
            title="Unsupported layout record version",
            message=f"The layout record version {layout_record.version} is not supported."
        )

    try:
        return uic.LayoutV1.parse(layout_record.data)
    except uic.util.UICException:
        raise TicketError(
            title="Invalid layout record",
            message="The layout record is invalid - the ticket is likely invalid.",
            exception=traceback.format_exc()
        )


def parse_ticket_uic_flex(ticket_envelope: uic.Envelope) -> typing.Optional[uic.Flex]:
    flex_record = next(filter(lambda r: r.id == "U_FLEX", ticket_envelope.records), None)
    if not flex_record:
        return None

    try:
        return uic.Flex.parse(flex_record.version, flex_record.data)
    except uic.util.UICException:
        raise TicketError(
            title="Invalid flexible data record",
            message="The flexible record is invalid - the ticket is likely invalid.",
            exception=traceback.format_exc()
        )


def parse_ticket_uic(ticket_bytes: bytes) -> UICTicket:
    try:
        ticket_envelope = uic.Envelope.parse(ticket_bytes)
    except uic.util.UICException:
        raise TicketError(
            title="This doesn't look like a valid UIC ticket",
            message="You may have scanned something that is not a UIC ticket, the ticket is corrupted, or there "
                    "is a bug in this program.",
            exception=traceback.format_exc()
        )

    return UICTicket(
        raw_bytes=ticket_bytes,
        envelope=ticket_envelope,
        head=parse_ticket_uic_head(ticket_envelope),
        layout=parse_ticket_uic_layout(ticket_envelope),
        flex=parse_ticket_uic_flex(ticket_envelope),
        other_records=[r for r in ticket_envelope.records if not r.id.startswith("U_")]
    )

def parse_ticket(ticket_bytes: bytes) -> typing.Union[VDVTicket, UICTicket]:
    if ticket_bytes[:3] == b"#UT":
        return parse_ticket_uic(ticket_bytes)
    else:
        return parse_ticket_vdv(ticket_bytes)