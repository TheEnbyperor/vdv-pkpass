import json
import numpy as np
import cv2
from django.conf import settings


class AztecError(Exception):
    pass


def decode(img_data: bytes):
    try:
        from Barkoder import BarkoderSDK
    except ImportError:
        raise AztecError("Barkoder SDK not available")

    BarkoderSDK.initialize(settings.BARKODER_LICENSE)

    decoders = [
        BarkoderSDK.constants["Decoders"]["Aztec"],
        BarkoderSDK.constants["Decoders"]["AztecCompact"],
        BarkoderSDK.constants["Decoders"]["QR"],
        BarkoderSDK.constants["Decoders"]["QRMicro"],
        BarkoderSDK.constants["Decoders"]["PDF417"],
        BarkoderSDK.constants["Decoders"]["PDF417Micro"],
    ]
    BarkoderSDK.setEnabledDecoders(decoders, len(decoders))
    BarkoderSDK.setDecodingSpeed(BarkoderSDK.constants["DecodingSpeed"]["Normal"])

    img = cv2.imdecode(np.asarray(bytearray(img_data), dtype="uint8"), cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise AztecError("Unable to read image")

    height, width = img.shape[:2]
    result_json = BarkoderSDK.decodeImage(img, width, height)
    result = json.loads(result_json)

    if result["resultsCount"] > 0:
        return bytes(result["binaryData"])
    else:
        raise AztecError("No barcodes found")