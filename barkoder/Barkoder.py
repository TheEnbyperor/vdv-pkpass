#!/usr/bin/env python3

import BarkoderSDK

#---------------Constants--------------
BarkoderSDK.constants = {
    "DecodingSpeed": None,
    "Decoders": None,
    "Code11ChecksumType": None,
    "Code39ChecksumType": None,
    "MsiChecksumType": None,
    "UpcEanDeblur": None,
    "MulticodeCachingEnabled": None,
    "EnableMisshaped1D": None,
    "EnableVINRestrictions": None,
    "Formatting": None
};

BarkoderSDK.constants["DecodingSpeed"] = {
    "Fast": 0,
    "Normal": 1,
    "Slow": 2
};

BarkoderSDK.constants["Decoders"] = {
    "Aztec": 0,
    "AztecCompact": 1,
    "QR": 2,
    "QRMicro": 3,
    "Code128": 4,
    "Code93": 5,
    "Code39": 6,
    "Codabar": 7,
    "Code11": 8,
    "Msi": 9,
    "UpcA": 10,
    "UpcE": 11,
    "UpcE1": 12,
    "Ean13": 13,
    "Ean8": 14,
    "PDF417": 15,
    "PDF417Micro": 16,
    "Datamatrix": 17,
    "Code25": 18,
    "Interleaved25": 19,
    "ITF14": 20,
    "IATA25": 21,
    "Matrix25": 22,
    "Datalogic25": 23,
    "COOP25": 24,
    "Code32": 25,
    "Telepen": 26,
    "Dotcode": 27
};

BarkoderSDK.constants["Code11ChecksumType"] = {
    "disabled": 0,
    "single": 1,
    "double": 2
};

BarkoderSDK.constants["Code39ChecksumType"] = {
    "disabled": 0,
    "enabled": 1
};

BarkoderSDK.constants["MsiChecksumType"] = {
    "disabled": 0,
    "mod10": 1,
    "mod11": 2,
    "mod1010": 3,
    "mod1110": 4,
    "mod11IBM": 5,
    "mod1110IBM": 6
};

BarkoderSDK.constants["UpcEanDeblur"] = {
    "Disable": 0,
    "Enable": 1
};

BarkoderSDK.constants["MulticodeCachingEnabled"] = {
    "Disable": 0,
    "Enable": 1
};

BarkoderSDK.constants["EnableMisshaped1D"] = {
    "Disable": 0,
    "Enable": 1
};

BarkoderSDK.constants["EnableVINRestrictions"] = {
    "Disable": 0,
    "Enable": 1
};

BarkoderSDK.constants["Formatting"] = {
    "Disabled": 0,
    "Automatic": 1,
    "GS1": 2,
    "AAMVA": 3
};

#BarkoderSDK.constants.EncodingCharacterSet = {
#    "None": 0
#};
#---------------Constants--------------


#---------------Methods--------------
def setCode11ChecksumType(code11ChecksumType):
    BarkoderSDK.setSpecificConfig(BarkoderSDK.constants["Decoders"]["Code11"], code11ChecksumType);

BarkoderSDK.setCode11ChecksumType = setCode11ChecksumType;

def setCode39ChecksumType(code39ChecksumType):
    BarkoderSDK.setSpecificConfig(BarkoderSDK.constants["Decoders"]["Code39"], code39ChecksumType);

BarkoderSDK.setCode39ChecksumType = setCode39ChecksumType;

def setMsiChecksumType(msiChecksumType):
    BarkoderSDK.setSpecificConfig(BarkoderSDK.constants["Decoders"]["Msi"], msiChecksumType);

BarkoderSDK.setMsiChecksumType = setMsiChecksumType;

def setDatamatrixDpmModeEnabled(dpmModeEnabled):
    BarkoderSDK.setSpecificConfig(BarkoderSDK.constants["Decoders"]["Datamatrix"], dpmModeEnabled);

BarkoderSDK.setDatamatrixDpmModeEnabled = setDatamatrixDpmModeEnabled;
#---------------Methods--------------