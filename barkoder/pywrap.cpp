#include <pybind11/pybind11.h>
#include <pybind11/numpy.h>
#include <pybind11/stl.h>
#include "Barkoder.hpp"

namespace py = pybind11;

PYBIND11_MODULE(Barkoder, m) {
    py::class_<Barkoder>(m, "Barkoder")
    .def_static("DecodeImageMemory", [](NSBarkoder::Config* config, py::array_t<unsigned char> img, int width, int height) {
        py::buffer_info info = img.request();
        auto ptr = static_cast<unsigned char*>(info.ptr);
        return Barkoder::DecodeImageMemory(config, ptr, width, height);
    });

    py::class_<BaseResult>(m, "BaseResult")
    .def_readonly("binaryData", &BaseResult::binaryData);

    py::class_<NSBarkoder::Config>(m, "Config")
    .def_static("InitializeWithLicenseKey", &NSBarkoder::Config::InitializeWithLicenseKey)
    .def_readwrite("decodingSpeed", &NSBarkoder::Config::decodingSpeed)
    .def("set_enabled_decoders", &NSBarkoder::Config::SetEnabledDecoders);

    py::class_<NSBarkoder::ConfigResponse>(m, "ConfigResponse")
    .def("get_config", &NSBarkoder::ConfigResponse::GetConfig)
    .def("get_result", &NSBarkoder::ConfigResponse::GetResult)
    .def("message", &NSBarkoder::ConfigResponse::Message);

    py::enum_<NSBarkoder::ConfigResponse::Result>(m, "ConfigResult")
    .value("OK", NSBarkoder::ConfigResponse::Result::OK)
    .value("Error", NSBarkoder::ConfigResponse::Result::Error)
    .value("Warning", NSBarkoder::ConfigResponse::Result::Warning);

    py::enum_<NSBarkoder::DecoderType>(m, "DecoderType")
    .value("Aztec", NSBarkoder::DecoderType::Aztec)
    .value("AztecCompact", NSBarkoder::DecoderType::AztecCompact)
    .value("QR", NSBarkoder::DecoderType::QR)
    .value("QRMicro", NSBarkoder::DecoderType::QRMicro)
    .value("Code128", NSBarkoder::DecoderType::Code128)
    .value("Code93", NSBarkoder::DecoderType::Code93)
    .value("Code39", NSBarkoder::DecoderType::Code39)
    .value("Codabar", NSBarkoder::DecoderType::Codabar)
    .value("Code11", NSBarkoder::DecoderType::Code11)
    .value("Msi", NSBarkoder::DecoderType::Msi)
    .value("UpcA", NSBarkoder::DecoderType::UpcA)
    .value("UpcE", NSBarkoder::DecoderType::UpcE)
    .value("UpcE1", NSBarkoder::DecoderType::UpcE1)
    .value("Ean13", NSBarkoder::DecoderType::Ean13)
    .value("Ean8", NSBarkoder::DecoderType::Ean8)
    .value("PDF417", NSBarkoder::DecoderType::PDF417)
    .value("PDF417Micro", NSBarkoder::DecoderType::PDF417Micro)
    .value("Datamatrix", NSBarkoder::DecoderType::Datamatrix)
    .value("Code25", NSBarkoder::DecoderType::Code25)
    .value("Interleaved25", NSBarkoder::DecoderType::Interleaved25)
    .value("ITF14", NSBarkoder::DecoderType::ITF14)
    .value("IATA25", NSBarkoder::DecoderType::IATA25)
    .value("Matrix25", NSBarkoder::DecoderType::Matrix25)
    .value("Datalogic25", NSBarkoder::DecoderType::Datalogic25)
    .value("COOP25", NSBarkoder::DecoderType::COOP25)
    .value("Code32", NSBarkoder::DecoderType::Code32)
    .value("Telepen", NSBarkoder::DecoderType::Telepen)
    .value("Dotcode", NSBarkoder::DecoderType::Dotcode);

    py::enum_<NSBarkoder::DecodingSpeed>(m, "DecodingSpeed")
    .value("Fast", NSBarkoder::DecodingSpeed::Fast)
    .value("Normal", NSBarkoder::DecodingSpeed::Normal)
    .value("Slow", NSBarkoder::DecodingSpeed::Slow);
}