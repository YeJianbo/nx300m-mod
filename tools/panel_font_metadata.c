/* Build with the stock ARM Fontconfig: save only the immutable face coverage.
 * Glyphs and display-dependent rendering settings are still loaded at runtime. */
#include <fontconfig/fontconfig.h>
#include <fontconfig/fcfreetype.h>
#include <stdio.h>

int main(int argc, char **argv) {
    int faces = 0, result = 1;
    FcCharSet *charset;
    if (argc != 3) return 2;
    FILE *source = fopen(argv[1], "rb");
    if (!source) return 2;
    long source_bytes = fseek(source, 0, SEEK_END) ? -1 : ftell(source);
    fclose(source);
    if (source_bytes <= 0) return 2;
    FcPattern *face = FcFreeTypeQuery((const FcChar8 *)argv[1], 0, NULL, &faces);
    if (!face || FcPatternGetCharSet(face, FC_CHARSET, 0, &charset) != FcResultMatch) return 3;
    FcPattern *coverage = FcPatternCreate();
    if (!coverage || !FcPatternAddCharSet(coverage, FC_CHARSET, charset)) return 4;
    FcChar8 *text = FcNameUnparse(coverage);
    FILE *output = text ? fopen(argv[2], "w") : NULL;
    if (output) {
        int written = fprintf(output, "NX_FONT_CHARSET_V1 %lu %u\n%s\n",
            (unsigned long)source_bytes, FcCharSetCount(charset), text);
        int closed = fclose(output);
        if (written > 0 && !closed) result = 0;
    }
    if (text) FcStrFree(text);
    FcPatternDestroy(coverage); FcPatternDestroy(face);
    return result;
}
