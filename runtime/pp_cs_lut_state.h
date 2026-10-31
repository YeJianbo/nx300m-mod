#ifndef NX300M_PP_CS_LUT_STATE_H
#define NX300M_PP_CS_LUT_STATE_H

struct nx300m_pp_cs_lut_state {
    unsigned int source, bytes, generation, valid;
    int error;
};

/* Copy the loaded single-source bytes, never a live DMA-source pointer.
 * A null destination with zero capacity reads metadata only. Returns bytes
 * copied, -61 when no supported load is current, or -28 for short capacity. */
int nx300m_pp_cs_lut_read(struct nx300m_pp_cs_lut_state *, void *, unsigned int);
#endif
