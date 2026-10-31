/* Explicit probe provider. Its files are text fixtures, never camera photos. */
extern int *__errno_location(void), open(const char *, int, ...), close(int);
extern int write(int, const void *, unsigned int);
static unsigned int backend_calls, write_calls;
unsigned int capture_fixture_calls(unsigned int kind) { return kind ? write_calls : backend_calls; }
int fixture_backend(void *, int, int) __asm__("_ZN10CDriveMode17BackEndCompletionEii");
int fixture_backend(void *self, int drive, int argument) {
    (void)self; ++backend_calls;
    *__errno_location() = 71;
    return drive*17+argument;
}
unsigned int fixture_write(void *) __asm__("_ZN14DscFileHandler11WriteChunksEv");
unsigned int fixture_write(void *self) {
    ++write_calls;
    if ((unsigned int)self != 1) {
        unsigned char *image = *(unsigned char **)((char *)self+0x64);
        char *descriptor = *(char **)(image+0x2c);
        int fd = open(descriptor+12, 1|0100|01000, 0600);
        if (fd >= 0) { write(fd, "fixture-bytes", 13); close(fd); }
        *(unsigned int *)((char *)self+0x70) = 2;
    }
    *__errno_location() = 72;
    return 0; /* A changed file and a completion are deliberately not success. */
}
