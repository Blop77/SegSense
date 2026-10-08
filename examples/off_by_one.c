/* Averages an array but walks one element past the end. */
#include <stdio.h>

double average(const int *xs, int n) {
    long total = 0;
    for (int i = 0; i <= n; i++)
        total += xs[i];
    return (double)total / n;
}

int main(void) {
    int scores[5] = {90, 72, 85, 64, 99};
    printf("average = %.2f\n", average(scores, 5));
    return 0;
}
