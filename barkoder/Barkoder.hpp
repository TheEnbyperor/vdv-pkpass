
#ifndef Barkoder_h
#define Barkoder_h


#ifdef _MSC_VER
#ifdef DLL_EXPORTS
#define DLL_API __declspec(dllexport)
#else
#define DLL_API __declspec(dllimport)
#endif
#else
#define DLL_API __attribute__ ((visibility ("default")))
#endif


#include "stdint.h"
#include "BarkoderClasses.hpp"
#include "Config.hpp"
#include <vector>


/**
 * @brief The Barkoder class provides methods for image decoding.
 */
class DLL_API Barkoder {

public:
    /**
     * @brief Decodes an image stored in memory synchronously.
     * @param config The configuration to use for decoding.
     * @param pixels Pointer to the grayscale image pixels.
     * @param width Width of the image.
     * @param height Height of the image.
     * @return A vector of BaseResult objects containing decoded results.
     */
    static std::vector<BaseResult> DecodeImageMemory(NSBarkoder::Config *config, uint8_t *pixels, int width, int height);

    /**
     * @brief Decodes an image stored in memory asynchronously.
     * @param config The configuration to use for decoding.
     * @param pixels Pointer to the grayscale image pixels.
     * @param width Width of the image.
     * @param height Height of the image.
     * @param decodeMemoryCallback Callback function to be called with the decoded results.
     * @param callbackId An optional ID for the callback.
     * @param taskAlreadyAdded Flag indicating whether the decoding task has already been added.
     * @return An ID representing the decoding task.
     */
    static int DecodeImageMemoryAsync(NSBarkoder::Config *config, uint8_t *pixels, int width, int height, void (*decodeMemoryCallback)(std::vector<BaseResult>, int), int callbackId = 0, bool taskAlreadyAdded = 0);

    /**
     * @brief Decodes an image using GPU asynchronously (Beta - works only on Apple platforms).
     * @param config The configuration to use for decoding.
     * @param GPUImageRef Reference to the grayscale image in GPU memory.
     * @param width Width of the image.
     * @param height Height of the image.
     * @param decodeMemoryCallback Callback function to be called with the decoded results.
     * @param callbackId An ID for the callback.
     * @return An ID representing the decoding task.
     */
    static int DecodeImageInGPUAsync(NSBarkoder::Config *config, void *GPUImageRef, int width, int height, void (*decodeMemoryCallback)(std::vector<BaseResult>, int), int callbackId);

    /**
     * @brief Retrieves the version of the Barkoder library.
     * @return The version of the Barkoder library.
     */
    static std::string GetLibVersion();

    /**
     * @brief Checks if the decoder is currently busy.
     * @return 1 if the decoder is busy, 0 otherwise.
     */
    static int IsDecoderBusy();
};

#endif /* Barkoder_h */
