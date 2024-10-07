import dataclasses
import traceback
import Crypto.Hash.TupleHash128

from . import models, vdv


class TicketError(Exception):
    def __init__(self, title, message, exception=None):
        self.title = title
        self.message = message
        self.exception = exception


@dataclasses.dataclass
class Ticket:
    root_ca: vdv.CertificateData
    issuing_ca: vdv.CertificateData
    envelope_certificate: vdv.CertificateData
    raw_ticket: bytes
    ticket: vdv.VDVTicket

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
                return hd.hexdigest()

        hd.update(b"unknown")
        hd.update(self.ticket.ticket_id.to_bytes(8, "big"))
        hd.update(self.ticket.ticket_org_id.to_bytes(8, "big"))
        return hd.hexdigest()


def parse_ticket(ticket_bytes: bytes) -> Ticket:
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
            message="You may have scanned something that is not a VDV ticket, the ticket is corrupted, or there"
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

    return Ticket(
        root_ca=root_ca_data,
        issuing_ca=issuing_ca_data,
        envelope_certificate=envelope_certificate_data,
        raw_ticket=ticket_data,
        ticket=ticket
    )