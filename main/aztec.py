import subprocess
from django.conf import settings

class AztecError(Exception):
    pass

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

    return stdout