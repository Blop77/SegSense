/* Copies a name into a buffer that is one byte too small (forgot the '\0'). */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

char *duplicate(const char *s) {
    char *copy = malloc(strlen(s));
    strcpy(copy, s);
    return copy;
}

int main(void) {
    char *name = duplicate("SegSense");
    printf("hello, %s\n", name);
    free(name);
    return 0;
}
