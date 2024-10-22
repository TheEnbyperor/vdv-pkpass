#ifndef Config_hpp
#define Config_hpp

#ifdef _MSC_VER
#ifdef DLL_EXPORTS
#define DLL_API __declspec(dllexport)
#else
#define DLL_API __declspec(dllimport)
#endif
#else
#define DLL_API __attribute__ ((visibility ("default")))
#endif

#include <string>
#include <vector>
#include <map>
#include "SpecificConfigs.hpp"

namespace NSBarkoder {

/**
 * @brief Enumeration for global configuration options.
 */
typedef enum {
    BKGlobalOption_SetMaximumThreads = 0, /**< Set maximum number of threads for processing. */
    BKGlobalOption_UseGPU,                /**< Use GPU for image processing. */
    BKGlobalOption_MulticodeCachingEnabled, /**< Enable multicode caching. */
    BKGlobalOption_MulticodeCachingDuration /**< Set multicode caching timeout. */
    
} BKGlobalOption;


class Config;

/**
 * @brief Response class for Config creation.
 */
class DLL_API ConfigResponse {
public:
    /**
     * @brief Enumeration for possible results of Config operations.
     */
    enum class Result {
        OK,         /**< Creation succeeded. */
        Error,      /**< Creation encountered an error. */
        Warning     /**< Creation completed with a warning. */
    };
    

    ConfigResponse(Config *config);
    

    ConfigResponse(Result result, const char *msg, Config *config);
    

    ConfigResponse(const ConfigResponse &other);

    ~ConfigResponse();
    

    Result GetResult();
    

    const std::string &Message();
    

    Config *GetConfig();
    
private:
    Result result;
    std::string message;
    Config *config;
};




/**
 * @brief Decoder configuration class.
 */
class DLL_API Config {
public:
    /**
     * @brief Initializes the configuration with a license key.
     * @param licenceKey The license key string.
     * @return A ConfigResponse object indicating the result of the initialization.
     */
    static ConfigResponse InitializeWithLicenseKey(const std::string &licenceKey);
    

    static Config* GetByHandle(int handle);

    static ConfigResponse DefaultConfig();
    
    /**
     * @brief Checks if the given barcode type is licensed.
     * @param barcodeType The barcode type to check.
     * @return True if the barcode type is licensed, false otherwise.
     */
    static bool IsLicensed(DecoderType barcodeType);
    
    /**
     * @brief Retrieves the error message for license issues.
     * @return The error message for license issues.
     */
    static std::string GetLicenseErrorMessage();
    
    /**
     * @brief Retrieves the device ID.
     * @return The device ID.
     */
    static std::string GetDeviceId();
    
    /**
     * @brief Sets a global option.
     * @param option The global option to set.
     * @param value The value to set for the option.
     * @return The result of setting the option.
     */
    static int SetGlobalOption(BKGlobalOption option, int value);
    
    /**
     * @brief Retrieves the value of a global option.
     * @param option The global option to retrieve.
     * @return The value of the global option.
     */
    static int GetGlobalOption(BKGlobalOption option);
    
    /**
     * @brief Sets a custom option.
     * @param option The custom option to set.
     * @param value The value to set for the option.
     * @return The result of setting the option.
     */
    int SetCustomOption(std::string option, int value);
    
    /**
     * @brief Gets the value of a custom option.
     * @param option The custom option to retrieve.
     * @return The value of the custom option.
     */
    int GetCustomOption(std::string option);

    Config();
    

    Config(const Config& other);
    
    
    
    DecodingSpeed decodingSpeed{DecodingSpeed::Normal}; /**< The decoding speed - the slower the speed, decoder is more robust and better detecting */
    std::string encodingCharacterSet; /**< Force encoding character set. */
    int maximumResultsCount = 1; /**< The maximum results count to be returned. */
    int duplicatesDelayMs = 0; /**< The duplicates delay in milliseconds. */
    int upcEanDeblur = 0; /**< The UPC/EAN deblur value. */
    int enableMisshaped1D = 0; /**< Enable misshaped 1D barcodes. */
    int enableVINRestrictions = 0; /**< Enable VIN mode. */
    
    std::map<std::string, int> customParams; /**< Custom parameters. */
    
    Code25Config code25;
    IATA25Config iata25;
    Matrix25Config matrix25;
    Datalogic25Config datalogic25;
    COOP25Config coop25;
    Interleaved25Config interleaved25;
    ITF14Config itf14;
    Code39Config code39;
    TelepenConfig telepen;
    DotcodeConfig dotcode;
    Code32Config code32;
    Code11Config code11;
    MsiConfig msi;
    AztecConfig aztec;
    AztecCompactConfig aztecCompact;
    QRConfig qr;
    QRMicroConfig qrMicro;
    Code128Config code128;
    Code93Config code93;
    CodabarConfig codabar;
    UpcAConfig upcA;
    UpcEConfig upcE;
    UpcE1Config upcE1;
    Ean13Config ean13;
    Ean8Config ean8;
    PDF417Config pdf417;
    PDF417MicroConfig pdf417Micro;
    DatamatrixConfig datamatrix;
    Formatting formatting {Formatting::Disabled}; /**< The formatting option. */
    
    
    /**
     * @brief Sets the enabled decoders.
     * @param types The types of decoders to enable.
     * @return A ConfigResponse object indicating the result of the operation.
     */
    ConfigResponse SetEnabledDecoders(const std::vector<DecoderType> &types);
    
    /**
     * @brief Gets the enabled decoders.
     * @return The enabled decoders.
     */
    std::vector<DecoderType> GetEnabledDecoders();
    
    /**
     * @brief Gets the available decoders.
     * @return The available decoders.
     */
    std::vector<DecoderType> GetAvailableDecoders();
    
    /**
     * @brief Gets the configuration for a specific decoder type.
     * @param decoderType The decoder type.
     * @return The configuration for the specified decoder type.
     */
    SpecificConfig* GetConfigForDecoder(DecoderType decoderType);
    
   
    int Handle() { return handle; }
    
    /**
     * @brief Checks if a decoder type is enabled.
     * @param decoderType The decoder type to check.
     * @return True if the decoder type is enabled, false otherwise.
     */
    bool IsEnabled(DecoderType decoderType);
    
    /**
     * @brief Enables a decoder type.
     * @param decoderType The decoder type to enable.
     */
    void Enable(DecoderType decoderType);
    
    /**
     * @brief Disables a decoder type.
     * @param decoderType The decoder type to disable.
     */
    void Disable(DecoderType decoderType);
    
    /**
     * @brief Sets the length range for a decoder type.
     * @param decoderType The decoder type.
     * @param minimumLength The minimum length.
     * @param maximumLength The maximum length.
     * @return The result of the operation.
     */
    int SetLengthRange(DecoderType decoderType, int minimumLength, int maximumLength);
    
    /**
     * @brief Gets the minimum length for a decoder type.
     * @param decoderType The decoder type.
     * @return The minimum length.
     */
    int GetMinimumLength(DecoderType decoderType);
    
    /**
     * @brief Gets the maximum length for a decoder type.
     * @param decoderType The decoder type.
     * @return The maximum length.
     */
    int GetMaximumLength(DecoderType decoderType);
    
    /**
     * @brief Gets the expected count for a decoder type.
     * @param decoderType The decoder type.
     * @return The expected count.
     */
    int GetExpectedCount(DecoderType decoderType);
    
    /**
     * @brief Sets the expected count for a decoder type.
     * @param decoderType The decoder type.
     * @param expectedCount The expected count.
     * @return The result of the operation.
     */
    int SetExpectedCount(DecoderType decoderType, int expectedCount);
    
    /**
     * @brief Gets the name of a decoder type.
     * @param decoderType The decoder type.
     * @return The name of the decoder type.
     */
    std::string GetTypeName(DecoderType decoderType);
    
    /**
     * @brief Gets the region of interest.
     * @return The region of interest.
     */
    const Rect& RegionOfInterest();
    
    /**
     * @brief Sets the region of interest in range 0% - 100%.
     * @param left The left coordinate.
     * @param top The top coordinate.
     * @param width The width.
     * @param height The height.
     */
    void SetRegionOfInterest(float left, float top, float width, float height);
    
    /**
     * @brief Gets the region of interest in range 0% - 100%.
     * @param left The left coordinate.
     * @param top The top coordinate.
     * @param width The width.
     * @param height The height.
     */
    void GetRegionOfInterest(float &left, float &top, float &width, float &height);
    
    virtual ~Config() {}
    
private:
    int lineSharpening = 0;
    int handle;
    Rect regionOfInterest;
    std::map<DecoderType, SpecificConfig*> typeToConfig;
};

}

#endif /* Config_hpp */
