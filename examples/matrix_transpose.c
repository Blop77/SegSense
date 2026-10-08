/* Transposes a 3x5 matrix stored row-major in one heap block, but uses the wrong row stride for the result. */
#include <stdio.h>
#include <stdlib.h>

static int *transpose(const int *m, int rows, int cols) {
    int *t = malloc(sizeof(int) * rows * cols);
    for (int r = 0; r < rows; r++)
        for (int c = 0; c < cols; c++)
            t[c * cols + r] = m[r * cols + c];   /* t is cols x rows */
    return t;
}

int main(void) {
    enum { ROWS = 3, COLS = 5 };
    int m[ROWS * COLS];
    for (int i = 0; i < ROWS * COLS; i++)
        m[i] = i + 1;

    int *t = transpose(m, ROWS, COLS);
    for (int r = 0; r < COLS; r++) {
        for (int c = 0; c < ROWS; c++)
            printf("%3d", t[r * ROWS + c]);
        printf("\n");
    }
    free(t);
    return 0;
}
