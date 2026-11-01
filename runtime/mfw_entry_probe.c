/* Exercise the real MFW export on an ARM kernel, without camera hardware. */
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

int main(int argc, char **argv)
{
    if (argc != 3) return 2;
    void *library = dlopen(argv[1], RTLD_NOW | RTLD_LOCAL);
    if (!library) { fprintf(stderr, "%s\n", dlerror()); return 3; }
    int (*start_script)(void) = dlsym(library, "start_script");
    if (!start_script) return 4;
    unlink("/tmp/mfw-autoexec-proof");
    int result = start_script();
    printf("start_script_result=%d\n", result);
    if (result != 0) return 5;
    int expect = atoi(argv[2]);
    for (int i = 0; i < (expect ? 100 : 10); ++i) {
        char line[128] = {0};
        FILE *marker = fopen("/tmp/mfw-autoexec-proof", "r");
        int present = marker != NULL;
        if (marker) {
            if (!fgets(line, sizeof(line), marker)) line[0] = '\0';
            fclose(marker);
        }
        if ((!expect && present) ||
            (expect && strcmp(line, "MFW_AUTOEXEC_FROM_REAL_LIBRARY\n") == 0)) {
            puts("autoexec_observed=yes");
            return expect ? 0 : 6;
        }
        usleep(100000);
    }
    puts("autoexec_observed=no");
    return expect ? 7 : 0;
}
