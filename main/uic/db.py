import dataclasses
import typing

class DBException(Exception):
    pass

@dataclasses.dataclass
class DBRecordBL:
    unknown: str
    certs: typing.List["DBCertBlock"]
    from_station_uic: typing.Optional[str]
    to_station_uic: typing.Optional[str]
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
        from_station_uic = None
        to_station_uic = None
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

            if block_id == "S035":
                try:
                    station_id = int(block_data, 10)
                except ValueError as e:
                    raise DBException(f"Invalid station ID") from e
                from_station_uic = f"80{station_id:05d}"
            elif block_id == "S036":
                try:
                    station_id = int(block_data, 10)
                except ValueError as e:
                    raise DBException(f"Invalid station ID") from e
                to_station_uic = f"80{station_id:05d}"
            else:
                blocks[block_id] = block_data


        return cls(
            unknown=unknown_data,
            certs=certs,
            from_station_uic=from_station_uic,
            to_station_uic=to_station_uic,
            other_blocks=blocks,
        )


@dataclasses.dataclass
class DBCertBlock:
    data: bytes