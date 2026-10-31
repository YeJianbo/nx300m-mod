#ifndef NX300M_PP_MEMORY_PIXELS_H
#define NX300M_PP_MEMORY_PIXELS_H

/* A numeric input stage only. This does not execute DPC, CS, or PP DMA,
 * write any hardware register, or signal a completion. The register bank is
 * the 0xb000-byte PP mapping, not the processing page alone. */
int nx300m_pp_expand12_to14(const volatile unsigned int *registers,
                          const unsigned char *packed, unsigned int bytes,
                          unsigned short *expanded, unsigned int pixels);

/* Four strided SECC planes in parity order 00, 01, 10, 11. The destination
 * consists of four contiguous planes of width*rows u16 samples each. All
 * source bounds and overlaps are checked before the first destination write.
 * Padding is excluded; reserved rows are read as supplied, never synthesized.
 * Source mappings and metadata must remain stable throughout the call, and
 * destination backing must not physically alias the source via another map.
 * This produces the input stage only, before DPC/CS/DAMP and WDMA packing. */
int nx300m_pp_expand12_planes14(const volatile unsigned int *registers,
                              const unsigned char *const packed[4],
                              const unsigned int bytes[4],
                              const unsigned int stride[4],
                              unsigned int width, unsigned int rows,
                              unsigned short *expanded, unsigned int samples);

#endif
