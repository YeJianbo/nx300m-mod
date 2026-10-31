/* Pair lifecycle for the final RGB-to-YCC and chroma selectors.
 * The caller serializes access and provides the actual stock setter. Neither
 * recipe modes nor an assumed physical update transaction are created here. */
#ifndef NX_CHROMA_PAIR_H
#define NX_CHROMA_PAIR_H
#include <stdint.h>

typedef struct {
    uint32_t sel, chm_sup_sub, chm_sup_gain, chm_sup_th_dark;
    uint32_t chm_sup_gain_dark, chm_sup_gain_dark_sat;
    uint32_t chm_mat[4], ycc_mat[9];
} nx_chroma_parameter;
typedef int (*nx_chroma_setter)(nx_chroma_parameter *);
typedef struct {
    nx_chroma_parameter chm, ycc;
    int have_chm, have_ycc;
    int modified, state_known, last_result, restore_result;
} nx_chroma_pair;

/* Call only for parameters supplied by the stock node, never styled output. */
static inline void nx_chroma_remember(nx_chroma_pair *pair,
                                       const nx_chroma_parameter *parameter) {
    if (parameter->sel == 1u) { pair->chm = *parameter; pair->have_chm = 1; }
    if (parameter->sel == 3u) { pair->ycc = *parameter; pair->have_ycc = 1; }
}

static inline int nx_chroma_restore(nx_chroma_pair *pair, nx_chroma_setter setter) {
    int chm_result = 0, ycc_result = 0;
    if (pair->have_chm) chm_result = setter(&pair->chm);
    if (pair->have_ycc) ycc_result = setter(&pair->ycc);
    pair->restore_result = chm_result ? chm_result : ycc_result;
    pair->state_known = pair->restore_result == 0;
    if (!pair->restore_result) pair->modified = 0;
    return pair->restore_result;
}

/* An already-computed source RGB-to-YCC result is followed by unit CHM,
 * preventing the stock chroma operator from being applied a second time.
 * On failure both newest stock bases are attempted; original error survives.
 * This is software recovery, not an atomic sensor-frame update guarantee. */
static inline int nx_chroma_apply_complete(nx_chroma_pair *pair,
        nx_chroma_setter setter, const int16_t coefficients[9]) {
    if (!pair->have_chm || !pair->have_ycc) return pair->last_result = -110;
    for (int i = 0; i < 9; ++i)
        if (coefficients[i] < -1024 || coefficients[i] > 1023)
            return pair->last_result = -111;
    nx_chroma_parameter chm = pair->chm, ycc = pair->ycc;
    chm.chm_mat[0] = chm.chm_mat[3] = 128;
    chm.chm_mat[1] = chm.chm_mat[2] = 0;
    for (int i = 0; i < 9; ++i) ycc.ycc_mat[i] = (uint32_t)(int32_t)coefficients[i];
    pair->restore_result = 0;
    pair->state_known = 0;
    int result = setter(&chm);
    if (!result) result = setter(&ycc);
    if (result) nx_chroma_restore(pair, setter);
    else { pair->modified = 1; pair->state_known = 1; }
    pair->last_result = result;
    return result;
}
#endif
