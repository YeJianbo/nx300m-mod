/* Minimal startup-only NX300M 1.15 no-lens Wi-Fi admission candidate.
 * No exposure, key, Ecore, service, or background worker hooks.
 * SPDX-License-Identifier: AGPL-3.0-or-later
 */
#include "manual_lens_wifi_patch.h"

/* Read-only in-process installation outcome; enum is in the shared header.
 * INSTALLED means both connection and Kmount admission sites were patched.
 * This reports the constructor result, not actual wireless interoperability.
 * No file, HTTP request, camera call, or patch attempt occurs in this query. */
unsigned int nx_manual_lens_wifi_status(void) {
    return manual_lens_wifi_result;
}

__attribute__((constructor)) static void install(void) {
    patch_manual_lens_wifi();
}
