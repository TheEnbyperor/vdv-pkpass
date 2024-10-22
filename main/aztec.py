import json
import numpy as np
import cv2


class AztecError(Exception):
    pass


def decode(img_data: bytes):
    try:
        from Barkoder import BarkoderSDK
    except ImportError:
        raise AztecError("Barkoder SDK not available")

    decoders = [
        BarkoderSDK.constants["Decoders"]["Aztec"],
        BarkoderSDK.constants["Decoders"]["AztecCompact"],
    ]
    BarkoderSDK.setEnabledDecoders(decoders, len(decoders))
    BarkoderSDK.setDecodingSpeed(BarkoderSDK.constants["DecodingSpeed"]["Normal"])

    img = cv2.imdecode(np.asarray(bytearray(img_data), dtype="uint8"), cv2.IMREAD_GRAYSCALE)
    if not img:
        raise AztecError("Unable to read image")

    height, width = img.shape[:2]
    result_json = BarkoderSDK.decodeImage(img, width, height)
    result = json.loads(result_json)

    if result["resultsCount"] > 0:
        return bytes(result["binaryData"])
    else:
        raise AztecError("No barcodes found")