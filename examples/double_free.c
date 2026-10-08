/* Two owners free the same buffer. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

struct message {
    char *text;
};

int main(void) {
    char *buf = malloc(32);
    strcpy(buf, "goal!");

    struct message a = { buf };
    struct message b = { buf };   /* b should own its own copy */

    printf("%s %s\n", a.text, b.text);
    free(a.text);
    free(b.text);
    return 0;
}
