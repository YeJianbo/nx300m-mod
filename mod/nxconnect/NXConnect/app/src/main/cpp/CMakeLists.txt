cmake_minimum_required(VERSION 3.22.1)

project(nxconnectraw LANGUAGES C CXX)

set(LIBRAW_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/third_party/libraw")

file(GLOB_RECURSE LIBRAW_SOURCES CONFIGURE_DEPENDS "${LIBRAW_ROOT}/src/*.cpp")
list(FILTER LIBRAW_SOURCES EXCLUDE REGEX ".*_ph\\.cpp$")

add_library(libraw STATIC ${LIBRAW_SOURCES})
target_include_directories(libraw PUBLIC "${LIBRAW_ROOT}")
target_compile_definitions(libraw PUBLIC LIBRAW_NODLL LIBRAW_NOTHREADS)
target_compile_options(libraw PRIVATE -Wall -Wextra -Wno-unused-parameter -Wno-sign-compare)
set_target_properties(libraw PROPERTIES POSITION_INDEPENDENT_CODE ON)

add_library(androidraw SHARED
    androidlibraw/AndroidLibRaw.cpp
    androidlibraw/LibRaw_fd_datastream.cpp
    androidlibraw/anrdroidraw.cpp
)
target_include_directories(androidraw PRIVATE
    "${CMAKE_CURRENT_SOURCE_DIR}/androidlibraw"
    "${LIBRAW_ROOT}"
)
target_compile_features(androidraw PRIVATE cxx_std_17)

find_library(log-lib log)
find_library(jnigraphics-lib jnigraphics)
find_library(android-lib android)

target_link_libraries(androidraw PRIVATE libraw ${log-lib} ${jnigraphics-lib} ${android-lib} m)
