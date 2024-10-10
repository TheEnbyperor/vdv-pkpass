import subprocess
import enum
from django.conf import settings


class AztecError(Exception):
    pass


class Table(enum.Enum):
    UPPER = 0
    LOWER = 1
    MIXED = 2
    DIGIT = 3
    PUNCT = 4
    BINARY = 5

UPPER_TABLE = [
    'CTRL_PS', ' ', 'A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'J', 'K', 'L', 'M', 'N', 'O', 'P',
    'Q', 'R', 'S', 'T', 'U', 'V', 'W', 'X', 'Y', 'Z', 'CTRL_LL', 'CTRL_ML', 'CTRL_DL', 'CTRL_BS'
]

LOWER_TABLE = [
    'CTRL_PS', ' ', 'a', 'b', 'c', 'd', 'e', 'f', 'g', 'h', 'i', 'j', 'k', 'l', 'm', 'n', 'o', 'p',
    'q', 'r', 's', 't', 'u', 'v', 'w', 'x', 'y', 'z', 'CTRL_US', 'CTRL_ML', 'CTRL_DL', 'CTRL_BS'
]

MIXED_TABLE = [
    'CTRL_PS', ' ', '\x01', '\x02', '\x03', '\x04', '\x05', '\x06', '\x07', '\b', '\t', '\n',
    '\x0b', '\f', '\r', '\x1b', '\x1c', '\x1d', '\x1e', '\x1f', '@', '\\', '^', '_',
    '`', '|', '~', '\x7f', 'CTRL_LL', 'CTRL_UL', 'CTRL_PL', 'CTRL_BS'
]

PUNCT_TABLE = [
    '', '\r', '\r\n', '. ', ', ', ': ', '!', '"', '#', '$', '%', '&', '\'', '(', ')',
    '*', '+', ',', '-', '.', '/', ':', ';', '<', '=', '>', '?', '[', ']', '{', '}', 'CTRL_UL'
]

DIGIT_TABLE = [
    'CTRL_PS', ' ', '0', '1', '2', '3', '4', '5', '6', '7', '8', '9', ',', '.', 'CTRL_UL', 'CTRL_US'
]

def decode(data: bytes) -> bytes:
    try:
        p = subprocess.Popen(
            ["java", "-jar", settings.AZTEC_JAR_PATH],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            universal_newlines=False, text=False
        )
    except OSError as e:
        raise AztecError("Could not execute Java binary") from e

    stdout, _ = p.communicate(data)
    if p.returncode == 255:
        raise AztecError("Invalid image data")
    elif p.returncode == 254:
        raise AztecError("Failed to decode Aztec")
    elif p.returncode != 0:
        raise AztecError(f"Java process exited with code {p.returncode}")
    elif len(stdout) == 0:
        raise AztecError("No barcode was found in the image")

    out_lines = stdout.splitlines()
    num_bits = int(out_lines[0], 10)
    bits_str = out_lines[1]
    bits = []
    for bit in bits_str:
        if bit == 0x31:
            bits.append(True)
        elif bit == 0x30:
            bits.append(False)

    return get_encoded_data_from_bits(bits, num_bits)


def get_encoded_data_from_bits(corrected_bits, end_index):
    latch_table = Table.UPPER
    shift_table = Table.UPPER
    output = bytearray()
    index = 0

    while index < end_index:
        if shift_table == Table.BINARY:
            if end_index - index < 5:
                break
            length = read_code(corrected_bits, index, 5)
            index += 5
            if length == 0:
                if end_index - index < 11:
                    break
                length = read_code(corrected_bits, index, 11) + 31
                index += 11
            for _ in range(length):
                if end_index - index < 8:
                    index = end_index
                    break
                code = read_code(corrected_bits, index, 8)
                output.append(code)
                index += 8
            shift_table = latch_table
        else:
            size = 4 if shift_table == Table.DIGIT else 5
            if end_index - index < size:
                break
            code = read_code(corrected_bits, index, size)
            index += size
            c = get_character(shift_table, code)
            if c.startswith("CTRL_"):
                latch_table = shift_table
                shift_table = get_table(c[5])
                if c[6] == "L":
                    latch_table = shift_table
            else:
                output.append(ord(c[0]))
                shift_table = latch_table

    return output

def read_code(bits, offset, length):
    res = 0
    for i in range(length):
        res <<= 1
        if bits[offset + i]:
            res |= 1
    return res

def get_table(code):
    if code == "L":
        return Table.LOWER
    elif code == "P":
        return Table.PUNCT
    elif code == "M":
        return Table.MIXED
    elif code == "D":
        return Table.DIGIT
    elif code == "B":
        return Table.BINARY
    else:
        return Table.UPPER

def get_character(table, code):
    if table == Table.UPPER:
        return UPPER_TABLE[code]
    elif table == Table.LOWER:
        return LOWER_TABLE[code]
    elif table == Table.MIXED:
        return MIXED_TABLE[code]
    elif table == Table.DIGIT:
        return DIGIT_TABLE[code]
    elif table == Table.PUNCT:
        return PUNCT_TABLE[code]