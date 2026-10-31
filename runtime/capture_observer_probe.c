/* Isolated observation probe. No singleton construction or camera startup. */
extern int printf(const char *, ...), puts(const char *), *__errno_location(void);
extern void *dlopen(const char *, int), *dlsym(void *, const char *);
extern void *fopen(const char *, const char *);
extern int fclose(void *), fprintf(void *, const char *, ...), snprintf(char *, unsigned int, const char *, ...);
extern int mkdir(const char *, unsigned int);
extern int strcmp(const char *, const char *);
extern unsigned int fread(void *, unsigned int, unsigned int, void *), fwrite(const void *, unsigned int, unsigned int, void *);
#define CHECK(x) do { if (!(x)) { printf("FAIL line=%d errno=%d\n", __LINE__, *__errno_location()); return 1; } } while (0)
static unsigned int object[0x78/4], image[0x30/4], descriptor[0x44/4];
static int copy_file(const char *source, const char *target) {
    void *input = fopen(source, "rb");
    if (!input) return 0;
    void *output = fopen(target, "wb");
    if (!output) { fclose(input); return 0; }
    char bytes[1024]; unsigned int count;
    while ((count = fread(bytes, 1, sizeof bytes, input)))
        if (fwrite(bytes, 1, count, output) != count) { fclose(input); fclose(output); return 0; }
    fclose(input);
    return fclose(output) == 0;
}
int main(int argc, char **argv) {
    CHECK(argc == 3);
    int stock = !strcmp(argv[1], "stock");
    void *provider = stock ? dlopen("libsif.so", 2|0x100) : dlopen("capture-observer-fixture.so", 2|0x100);
    CHECK(provider);
    unsigned int (*hook)(void *) = dlsym((void *)0, "_ZN14DscFileHandler11WriteChunksEv");
    unsigned int (*original)(void *) = dlsym(provider, "_ZN14DscFileHandler11WriteChunksEv");
    const char *(*directory)(void) = dlsym((void *)0, "nx_capture_observer_directory");
    CHECK(hook && original && hook != original && directory);
    object[0x64/4] = (unsigned int)image; image[0x2c/4] = (unsigned int)descriptor;
    if (stock) {
        /* Original WriteChunks calls ChangeSpeed(false) then returns true on
         * an empty inline path. This allocated layout has no live camera. */
        original(object); /* Let original one-time logging initialize first. */
        *__errno_location() = 63;
        unsigned int expected = original(object);
        int expected_errno = *__errno_location();
        *__errno_location() = 63;
        unsigned int result = hook(object);
        CHECK(result == expected && result == 1 && *__errno_location() == expected_errno);
        puts("STOCK_EMPTY_PATH original_branch=1 forwarding=1 no_photo=1");
    } else {
        unsigned int (*calls)(unsigned int) = dlsym(provider, "capture_fixture_calls");
        int (*backend)(void *, int, int) = dlsym((void *)0, "_ZN10CDriveMode17BackEndCompletionEii");
        CHECK(calls && backend);
        char *path = (char *)descriptor+12;
        snprintf(path, 52, "/var/run/probe-quote\"-slash\\-line\n-%c.dat", 128);
        CHECK(backend((void *)1, 3, -4) == 47 && *__errno_location() == 71 && calls(0) == 1);
        CHECK(hook(object) == 0 && *__errno_location() == 72 && calls(1) == 1);
        CHECK(hook((void *)1) == 0 && *__errno_location() == 72 && calls(1) == 2);
        /* Runtime output failure must also leave the original call intact. */
        char target[256]; snprintf(target, sizeof target, "%s/event-4.json.tmp", directory());
        CHECK(mkdir(target, 0700) == 0);
        CHECK(backend((void *)1, 1, -2) == 15 && *__errno_location() == 71 && calls(0) == 2);
        snprintf(target, sizeof target, "%s/event-4.json.tmp", directory());
        extern int rmdir(const char *); CHECK(rmdir(target) == 0);
        for (unsigned int i = 0; i < 130; ++i) CHECK(backend((void *)1, 0, (int)i) == (int)i && *__errno_location() == 71);
        CHECK(calls(0) == 132);
        puts("FIXTURE forwarding=1 errno=preserved unreadable_object=forwarded log_failure=forwarded event_limit=128 no_photo=1");
    }
    char output[512]; snprintf(output, sizeof output, "%s/%s-directory.txt", argv[2], argv[1]);
    void *file = fopen(output, "w"); CHECK(file);
    CHECK(fprintf(file, "%s\n", directory()) > 0 && fclose(file) == 0);
    char input[256];
    snprintf(input, sizeof input, "%s/ready.json", directory());
    snprintf(output, sizeof output, "%s/%s-ready.json", argv[2], argv[1]);
    CHECK(copy_file(input, output));
    for (unsigned int i = 1; i <= 128; ++i) {
        snprintf(input, sizeof input, "%s/event-%u.json", directory(), i);
        snprintf(output, sizeof output, "%s/%s-event-%u.json", argv[2], argv[1], i);
        copy_file(input, output);
    }
    return 0;
}
