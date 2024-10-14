import dataclasses
import typing
import datetime

class DBException(Exception):
    pass

@dataclasses.dataclass
class DBRecordBL:
    unknown: str
    certs: typing.List["DBCertBlock"]
    product: typing.Optional[str]
    from_station_name: typing.Optional[str]
    from_station_uic: typing.Optional[int]
    to_station_name: typing.Optional[str]
    to_station_uic: typing.Optional[int]
    route: typing.Optional[str]
    validity_start: typing.Optional[datetime.date]
    validity_end: typing.Optional[datetime.date]
    traveller_forename: typing.Optional[str]
    traveller_surname: typing.Optional[str]
    other_blocks: typing.Dict[str, str]

    @classmethod
    def parse(cls, data: bytes, version: int):
        if version != 3:
            raise DBException(f"Unsupported record version {version}")

        try:
            unknown_data = data[0:2].decode("utf-8")
        except UnicodeDecodeError as e:
            raise DBException(f"Invalid DB BL record") from e
        try:
            num_cert_blocks = int(data[2:3].decode("utf-8"), 10)
        except (ValueError, UnicodeDecodeError) as e:
            raise DBException(f"Invalid DB BL record") from e

        offset = 3
        certs = []
        for _ in range(num_cert_blocks):
            certs.append(DBCertBlock(data[offset:offset+26]))
            offset += 26

        try:
            num_sub_blocks = int(data[offset:offset+2].decode("utf-8"), 10)
        except (ValueError, UnicodeDecodeError) as e:
            raise DBException(f"Invalid DB BL record") from e
        offset += 2

        blocks = {}
        product = None
        from_station_name = None
        from_station_uic = None
        to_station_name = None
        to_station_uic = None
        route = None
        validity_start = None
        validity_end = None
        traveller_forename = None
        traveller_surname = None
        for _ in range(num_sub_blocks):
            try:
                block_id = data[offset:offset+4].decode("utf-8")
            except UnicodeDecodeError as e:
                raise DBException(f"Invalid DB BL record") from e
            try:
                block_len = int(data[offset+4:offset+8].decode("utf-8"), 10)
            except (ValueError, UnicodeDecodeError) as e:
                raise DBException(f"Invalid DB BL record") from e
            try:
                block_data = data[offset+8:offset+8+block_len].decode("utf-8")
            except UnicodeDecodeError as e:
                raise DBException(f"Invalid DB BL record") from e
            offset += block_len + 8

            if block_id == "S001":
                product = block_data
            elif block_id == "S015":
                from_station_name = block_data
            elif block_id == "S035":
                try:
                    station_id = int(block_data, 10)
                except ValueError as e:
                    raise DBException(f"Invalid station ID") from e
                from_station_uic = 8000000 + station_id
            elif block_id == "S016":
                to_station_name = block_data
            elif block_id == "S036":
                try:
                    station_id = int(block_data, 10)
                except ValueError as e:
                    raise DBException(f"Invalid station ID") from e
                to_station_uic = 8000000 + station_id
            elif block_id == "S021":
                route = block_data
            elif block_id == "S028":
                traveller_forename, traveller_surname = block_data.split("#", 1)
            elif block_id == "S031":
                try:
                    validity_start = datetime.datetime.strptime(block_data, "%d.%m.%Y").date()
                except ValueError as e:
                    raise DBException(f"Invalid validity start date") from e
            elif block_id == "S032":
                try:
                    validity_end = datetime.datetime.strptime(block_data, "%d.%m.%Y").date()
                except ValueError as e:
                    raise DBException(f"Invalid validity end date") from e
            else:
                blocks[block_id] = block_data


        return cls(
            unknown=unknown_data,
            certs=certs,
            product=product,
            from_station_name=from_station_name,
            from_station_uic=from_station_uic,
            to_station_name=to_station_name,
            to_station_uic=to_station_uic,
            route=route,
            traveller_forename=traveller_forename,
            traveller_surname=traveller_surname,
            validity_start=validity_start,
            validity_end=validity_end,
            other_blocks=blocks,
        )


@dataclasses.dataclass
class DBCertBlock:
    data: bytes