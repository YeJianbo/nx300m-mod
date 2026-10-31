/* Compile old-camera binaries with the isolated modern toolchain headers. */
#ifndef NX_REMOTE_COMPAT_H
#define NX_REMOTE_COMPAT_H
#include <features.h>
#undef __GLIBC_USE_C23_STRTOL
#define __GLIBC_USE_C23_STRTOL 0
#endif
