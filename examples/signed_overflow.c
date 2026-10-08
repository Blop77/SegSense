/* Factorial overflows a signed int: undefined behaviour caught by UBSan. */
#include <stdio.h>

int factorial(int n) {
    int result = 1;
    for (int i = 2; i <= n; i++)
        result *= i;
    return result;
}

int main(void) {
    for (int n = 10; n <= 15; n++)
        printf("%d! = %d\n", n, factorial(n));
    return 0;
}
