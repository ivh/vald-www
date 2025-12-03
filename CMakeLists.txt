cmake_minimum_required(VERSION 3.15...3.26)
project(vald_www)

# Find Python
find_package(Python 3.11 COMPONENTS Interpreter Development.Module REQUIRED)

# Find nanobind
find_package(nanobind CONFIG REQUIRED)

# C++ standard
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Enable C for unkompress3.c
enable_language(C)
set(CMAKE_C_STANDARD 99)

# Include source directory
include_directories(${CMAKE_CURRENT_SOURCE_DIR}/vald/lib/vald3/src)

# Create the nanobind module
nanobind_add_module(
    vald3_decompress
    STABLE_ABI
    vald/lib/vald3/src/vald3_decompress.cpp
    vald/lib/vald3/src/unkompress3.c
)

# Compiler options
if(NOT MSVC)
    target_compile_options(vald3_decompress PRIVATE -Wall -Wextra)
    target_compile_options(vald3_decompress PRIVATE
        $<$<COMPILE_LANGUAGE:C>:-Wno-unused-parameter -Wno-unused-variable>
    )
endif()

# Link math library on Linux
if(UNIX AND NOT APPLE)
    target_link_libraries(vald3_decompress PRIVATE m)
endif()

# Install to vald package
install(TARGETS vald3_decompress LIBRARY DESTINATION vald)
