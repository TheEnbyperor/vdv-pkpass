#ifndef BarkoderClasses_h
#define BarkoderClasses_h

#ifdef _MSC_VER
#ifdef DLL_EXPORTS
#define DLL_API __declspec(dllexport)
#else
#define DLL_API __declspec(dllimport)
#endif
#else
#define DLL_API __attribute__ ((visibility ("default")))
#endif

#include <stdio.h>
#include <stdint.h>
#include <vector>
#include <string>
#include <map>
#include <iterator>
#include "SpecificConfigs.hpp"



/**
 * @brief Enumeration for barcode symbologies.
 */
typedef enum {
    BKAztec = 0,           /**< Aztec barcode. */
    BKAztecCompact,        /**< Compact Aztec barcode. */
    BKQR,                  /**< QR code. */
    BKQRMicro,             /**< Micro QR code. */
    BKCode128,             /**< Code 128 barcode. */
    BKCode93,              /**< Code 93 barcode. */
    BKCode39,              /**< Code 39 barcode. */
    BKCodabar,             /**< Codabar barcode. */
    BKCode11,              /**< Code 11 barcode. */
    BKMsi,                 /**< MSI barcode. */
    BKUpcA,                /**< UPC-A barcode. */
    BKUpcE,                /**< UPC-E barcode. */
    BKUpcE1,               /**< UPC-E1 barcode. */
    BKEan13,               /**< EAN-13 barcode. */
    BKEan8,                /**< EAN-8 barcode. */
    BKPDF417,              /**< PDF417 barcode. */
    BKPDF417Micro,         /**< Micro PDF417 barcode. */
    BKDatamatrix,          /**< Datamatrix barcode. */
    BKCode25,              /**< Code 25 barcode. */
    BKInterleaved25,       /**< Interleaved 25 barcode. */
    BKITF14,               /**< ITF-14 barcode. */
    BKIATA25,              /**< IATA 25 barcode. */
    BKMatrix25,            /**< Matrix 25 barcode. */
    BKDatalogic25,         /**< Datalogic 25 barcode. */
    BKCOOP25,              /**< COOP 25 barcode. */
    BKCode32,              /**< Code 32 barcode. */
    BKTelepen,             /**< Telepen barcode. */
    BKDotcode              /**< Dotcode barcode. */
} BKSymbology;

inline std::string SymbologyToString(BKSymbology symbology)
{
    switch (symbology)
    {
        case BKAztec: return "BKAztec";
        case BKAztecCompact: return "BKAztecCompact";
        case BKQR: return "BKQR";
        case BKQRMicro: return "BKQRMicro";
        case BKCode128: return "BKCode128";
        case BKCode93: return "BKCode93";
        case BKCode39: return "BKCode39";
        case BKTelepen: return "BKTelepen";
        case BKDotcode: return "BKDotcode";
        case BKCode32: return "BKCode32";
        case BKCodabar: return "BKCodabar";
        case BKCode11: return "BKCode11";
        case BKMsi: return "BKMsi";
        case BKUpcA: return "BKUpcA";
        case BKUpcE: return "BKUpcE";
        case BKUpcE1: return "BKUpcE1";
        case BKEan13: return "BKEan13";
        case BKEan8: return "BKEan8";
        case BKPDF417: return "BKPDF417";
        case BKPDF417Micro: return "BKPDF417Micro";
        case BKDatamatrix: return "BKDatamatrix";
        case BKCode25: return "BKCode25";
        case BKInterleaved25: return "BKInterleaved25";
        case BKITF14: return "BKITF14";
        case BKIATA25: return "BKIATA25";
        case BKMatrix25: return "BKMatrix25";
        case BKDatalogic25: return "BKDatalogic25";
        case BKCOOP25: return "BKCOOP25";
    }
    
    return "Unknown";
}


/**
 * @brief Enumeration for barcode checksum types.
 */
typedef enum {
    BKCHK_None,           /**< No checksum. */
    BKCHK_Code39,         /**< Code 39 checksum. */
    BKCHK_Code11Single,   /**< Single Code 11 checksum. */
    BKCHK_Code11Double,   /**< Double Code 11 checksum. */
    BKCHK_MsiMod10,       /**< MSI Mod10 checksum. */
    BKCHK_MsiMod11,       /**< MSI Mod11 checksum. */
    BKCHK_MsiMod1010,     /**< MSI Mod1010 checksum. */
    BKCHK_MsiMod1110,     /**< MSI Mod1110 checksum. */
    BKCHK_MsiMod11IBM,    /**< IBM-compatible MSI Mod11 checksum. */
    BKCHK_MsiMod1110IBM,  /**< IBM-compatible MSI Mod1110 checksum. */
    BKCHK_Code25          /**< Code 25 checksum. */
} BKChecksum;

typedef struct {
    float x, y;
    float weight;
    int valid;
} BKPoint;

typedef struct {
    float x, y;
    BKPoint delta1, delta2;
    int black;
    float size;
    float quality;
    int threshold;
} BKDot;

/**
 * @brief The BaseResult class represents the result of barcode decoding.
 */
class DLL_API BaseResult {
private:
public:
    
    /**
     * @brief Default constructor.
     */
    BaseResult();

    NSBarkoder::BarcodeType barcodeType; /**< The type of barcode. */
    NSBarkoder::DecoderType decoderType; /**< The type of decoder used. */
    std::string barcodeTypeName; /**< The name of the barcode type. */
    std::vector<uint8_t> binaryData; /**< The binary data of the barcode. */
    std::string textualData; /**< The textual data of the barcode. */
    std::string characterSet; /**< The character set used for encoding. */
    BKPoint location[4]; /**< The location of the barcode corners. */
    
    std::vector<BKPoint> polygonLocation; /**< The location of the barcode as a polygon. */
    int gs1; /**< GS1 compliance value. */
    
    std::map<std::string, std::string> extra; /**< Extra data associated with the barcode result. */
    
    /**
     * @brief Gets the string value associated with the given key from extra result data.
     * @param key The key to search for.
     * @return The value associated with the key, or an empty string if not found.
     */
    std::string GetStringForKey(std::string key){
            std::map<std::string, std::string>::iterator it = extra.find(key);
            if (it != extra.end()){
                return it->second;
            } else {
                return "";
            }
            
        }

    /**
     * @brief Gets all keys stored in the extra data.
     * @return A vector containing all keys stored in the extra data.
     */
    std::vector<std::string> GetAllKeys(){
            std::map<std::string, std::string>::iterator it = extra.begin();
            std::vector<std::string> keys;
            while(it != extra.end())
            {
                keys.push_back(it->first);
                it++;
            }
            return keys;
            
        }
    
    uint64_t timestampMs; /**< Timestamp when barcode is scanned in milliseconds. */
    BKPoint locationCenter; /**< The center point of the barcode location. */
    int isCached; /**< Flag indicating whether the result is cached. */
    int templateMatching = 0; /**< Flag indicating whether template matching was used. */
};


#endif /* BarkoderClasses_h */
