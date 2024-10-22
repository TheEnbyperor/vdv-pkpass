cmake_minimum_required(VERSION 3.10)

project(Barkoder)
set(CMAKE_CXX_STANDARD 20)
set(PYBIND11_PYTHON_VERSION 3.12)
set(CMAKE_CXX_FLAGS "-Wall -Wextra -fPIC")

find_package(pybind11 REQUIRED)
find_package(CURL REQUIRED)

pybind11_add_module(${PROJECT_NAME} pywrap.cpp)

include_directories(${CURL_INCLUDE_DIR})

target_compile_definitions(${PROJECT_NAME} PRIVATE VERSION_INFO=${EXAMPLE_VERSION_INFO})
target_include_directories(${PROJECT_NAME} PRIVATE ${PYBIND11_INCLUDE_DIRS})
target_link_libraries(${PROJECT_NAME} PRIVATE ${CMAKE_SOURCE_DIR}/libs/x86_64/libbarkoder.a)
target_link_libraries(${PROJECT_NAME} PRIVATE curl)