import json
import numpy as np
import cv2
from django.conf import settings


class AztecError(Exception):
    pass


def decode(img_data: bytes):
    try:
        import Barkoder
    except ImportError:
        raise AztecError("Barkoder SDK not available")

    cfg_response = Barkoder.Config.InitializeWithLicenseKey(settings.BARKODER_LICENSE)
    assert cfg_response.get_result() == Barkoder.ConfigResult.OK
    config = cfg_response.get_config()

    config.decodingSpeed = Barkoder.DecodingSpeed.Normal
    assert config.set_enabled_decoders([
        Barkoder.DecoderType.Aztec,
        Barkoder.DecoderType.AztecCompact,
        Barkoder.DecoderType.QR,
        Barkoder.DecoderType.QRMicro,
        Barkoder.DecoderType.PDF417,
        Barkoder.DecoderType.PDF417Micro,
    ]).get_result() == Barkoder.ConfigResult.OK

    img = cv2.imdecode(np.asarray(bytearray(img_data), dtype="uint8"), cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise AztecError("Unable to read image")

    height, width = img.shape[:2]
    results = Barkoder.Barkoder.DecodeImageMemory(config, img, height, width)

    if len(results) > 0:
        return bytes(results[0].binaryData)
    else:
        raise AztecError("No barcodes found")